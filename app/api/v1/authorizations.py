import json
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import BigInteger, case, cast, func, select
from sqlalchemy.exc import IntegrityError

from app.ledger import post_capture, post_hold, post_hold_release, post_reversal
from app.models import (
    Account,
    AccountType,
    Authorization,
    AuthorizationStatus,
    Card,
    Direction,
    Employee,
    EntryType,
    IdempotencyRecord,
    LedgerEntry,
    LedgerPosting,
    SpendPolicy,
)
from app.observability import StageTimer
from app.risk.policy import PolicyContext, evaluate_policy

bp = Blueprint("authorizations_v1", __name__)

HOLD_TTL_DAYS = 7
REQUIRED_FIELDS = ("idempotency_key", "card_token", "merchant_name", "mcc", "amount_cents", "timestamp")

DECISION_STATUS = {
    "approve": AuthorizationStatus.approved,
    "decline": AuthorizationStatus.declined,
    "review": AuthorizationStatus.pending_review,
}


def _validate_payload(payload):
    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        return f"missing fields: {', '.join(missing)}"
    amount = payload["amount_cents"]
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        return "amount_cents must be a positive integer"
    try:
        datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "timestamp must be ISO 8601"
    return None


def _fingerprint(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _monthly_used_cents(session, employee_id, now):
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total = session.scalar(
        select(
            func.coalesce(
                cast(
                    func.sum(
                        case(
                            (LedgerEntry.direction == Direction.debit, LedgerEntry.amount_cents),
                            else_=-LedgerEntry.amount_cents,
                        )
                    ),
                    BigInteger,
                ),
                0,
            )
        )
        .join(Account, Account.id == LedgerEntry.account_id)
        .join(Authorization, Authorization.id == LedgerEntry.authorization_id)
        .join(Card, Card.id == Authorization.card_id)
        .where(
            Card.employee_id == employee_id,
            Account.account_type.in_([AccountType.holds, AccountType.settled]),
            LedgerEntry.created_at >= month_start,
        )
    )
    return int(total)


def _velocity_count(session, employee_id, window_minutes, now):
    window_start = now - timedelta(minutes=window_minutes)
    return session.scalar(
        select(func.count(Authorization.id))
        .join(Card, Card.id == Authorization.card_id)
        .where(
            Card.employee_id == employee_id,
            Authorization.created_at > window_start,
        )
    )


def _replay_response(record):
    response = jsonify(record.response_body)
    response.status_code = record.status_code
    return response


def _log_and_return(timer, response, status_code=None, replay=False):
    if replay:
        response.headers["Idempotent-Replay"] = "true"
    timer.finish()
    if status_code is None:
        return response
    return response, status_code


@bp.post("")
def create_authorization():
    timer = StageTimer("authorization_decision")
    with timer.stage("request_parse"):
        payload = request.get_json(silent=True) or {}
        validation_error = _validate_payload(payload)
    if validation_error:
        return _log_and_return(timer, jsonify({"error": validation_error}), 400)

    idempotency_key = payload["idempotency_key"]
    fingerprint = _fingerprint(payload)
    session_factory = current_app.extensions["session_factory"]

    with session_factory() as session:
        with timer.stage("idempotency"):
            record = session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == idempotency_key
                )
            )
        if record is not None:
            if record.request_fingerprint != fingerprint:
                return _log_and_return(
                    timer,
                    jsonify({"error": "idempotency key already used with a different request body"}),
                    409,
                )
            return _log_and_return(timer, _replay_response(record), replay=True)

        with timer.stage("policy"):
            card = session.scalar(select(Card).where(Card.token == payload["card_token"]))
            if card is None:
                return _log_and_return(timer, jsonify({"error": "card not found"}), 404)
            employee = session.get(Employee, card.employee_id)
            policy = session.scalar(
                select(SpendPolicy).where(SpendPolicy.employee_id == employee.id)
            )
            if policy is None:
                return _log_and_return(
                    timer, jsonify({"error": "no spend policy for card"}), 422
                )
            now = datetime.now(timezone.utc)
            monthly_used = _monthly_used_cents(session, employee.id, now)
            velocity_count = _velocity_count(
                session, employee.id, policy.velocity_window_minutes, now
            )
            decision = evaluate_policy(
                PolicyContext(
                    mcc=payload["mcc"],
                    amount_cents=payload["amount_cents"],
                    blocked_mccs=tuple(policy.blocked_mccs),
                    per_transaction_limit_cents=policy.per_transaction_limit_cents,
                    monthly_limit_cents=policy.monthly_limit_cents,
                    monthly_used_cents=monthly_used,
                    velocity_count=velocity_count,
                    velocity_max_auths=policy.velocity_max_auths,
                )
            )

        with timer.stage("model"):
            pass

        try:
            authorization = Authorization(
                idempotency_key=idempotency_key,
                card_id=card.id,
                merchant_name=payload["merchant_name"],
                mcc=payload["mcc"],
                amount_cents=payload["amount_cents"],
                status=DECISION_STATUS[decision.decision],
                decision_reason=decision.reason_code.value,
                risk_score=decision.risk_score,
                expires_at=now + timedelta(days=HOLD_TTL_DAYS)
                if decision.decision == "approve"
                else None,
            )
            session.add(authorization)
            session.flush()

            with timer.stage("ledger"):
                if decision.decision == "approve":
                    post_hold(
                        session,
                        authorization.id,
                        authorization.amount_cents,
                        idempotency_key=f"hold-{authorization.id}",
                        commit=False,
                    )

            response_body = {
                "authorization_id": str(authorization.public_id),
                "decision": decision.decision,
                "decision_reason": decision.reason_code.value,
                "risk_score": decision.risk_score,
                "latency_ms": round(timer.elapsed_ms(), 3),
            }
            record = IdempotencyRecord(
                idempotency_key=idempotency_key,
                request_fingerprint=fingerprint,
                status_code=200,
                response_body=response_body,
            )
            session.add(record)
            session.commit()
        except IntegrityError:
            session.rollback()
            record = session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.idempotency_key == idempotency_key
                )
            )
            if record is None:
                raise
            if record.request_fingerprint != fingerprint:
                return _log_and_return(
                    timer,
                    jsonify({"error": "idempotency key already used with a different request body"}),
                    409,
                )
            return _log_and_return(timer, _replay_response(record), replay=True)
        return _log_and_return(timer, jsonify(response_body), 200)


@bp.post("/<uuid:authorization_id>/capture")
def capture_authorization(authorization_id):
    payload = request.get_json(silent=True) or {}
    amount = payload.get("amount_cents")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        return jsonify({"error": "amount_cents must be a positive integer"}), 400

    session_factory = current_app.extensions["session_factory"]
    with session_factory() as session:
        authorization = session.scalar(
            select(Authorization).where(Authorization.public_id == authorization_id)
        )
        if authorization is None:
            return jsonify({"error": "authorization not found"}), 404
        if authorization.status not in (
            AuthorizationStatus.approved,
            AuthorizationStatus.captured,
        ):
            return jsonify(
                {"error": f"cannot capture authorization in status {authorization.status.value}"}
            ), 409

        hold_posting = session.scalar(
            select(LedgerPosting).where(
                LedgerPosting.authorization_id == authorization.id,
                LedgerPosting.entry_type == EntryType.hold,
            )
        )
        if hold_posting is None:
            return jsonify({"error": "no hold found for authorization"}), 409

        capture_posting = session.scalar(
            select(LedgerPosting).where(
                LedgerPosting.authorization_id == authorization.id,
                LedgerPosting.entry_type == EntryType.capture,
            )
        )
        released = session.scalar(
            select(func.coalesce(func.sum(LedgerPosting.amount_cents), 0)).where(
                LedgerPosting.authorization_id == authorization.id,
                LedgerPosting.entry_type == EntryType.hold_release,
            )
        )

        if capture_posting is not None:
            if capture_posting.amount_cents != amount:
                return jsonify(
                    {
                        "error": "authorization already captured for a different amount",
                        "captured_amount_cents": capture_posting.amount_cents,
                    }
                ), 409
            return jsonify(
                {
                    "authorization_id": str(authorization.public_id),
                    "status": authorization.status.value,
                    "captured_amount_cents": capture_posting.amount_cents,
                    "released_amount_cents": 0,
                }
            ), 200

        remaining_held = hold_posting.amount_cents - int(released)
        if amount > remaining_held:
            return jsonify(
                {
                    "error": "capture amount exceeds held amount",
                    "held_amount_cents": remaining_held,
                }
            ), 409

        post_capture(
            session,
            authorization.id,
            amount,
            idempotency_key=f"capture-{authorization.id}",
            commit=False,
        )
        remainder = remaining_held - amount
        if remainder > 0:
            post_hold_release(
                session,
                authorization.id,
                remainder,
                idempotency_key=f"hold-release-{authorization.id}",
                commit=False,
            )
        authorization.status = AuthorizationStatus.captured
        session.commit()
        return jsonify(
            {
                "authorization_id": str(authorization.public_id),
                "status": authorization.status.value,
                "captured_amount_cents": amount,
                "released_amount_cents": remainder,
            }
        ), 200


@bp.post("/<uuid:authorization_id>/reverse")
def reverse_authorization(authorization_id):
    session_factory = current_app.extensions["session_factory"]
    with session_factory() as session:
        authorization = session.scalar(
            select(Authorization).where(Authorization.public_id == authorization_id)
        )
        if authorization is None:
            return jsonify({"error": "authorization not found"}), 404
        if authorization.status in (
            AuthorizationStatus.declined,
            AuthorizationStatus.pending_review,
            AuthorizationStatus.expired,
        ):
            return jsonify(
                {"error": f"cannot reverse authorization in status {authorization.status.value}"}
            ), 409
        if authorization.status == AuthorizationStatus.reversed:
            return jsonify(
                {
                    "authorization_id": str(authorization.public_id),
                    "status": authorization.status.value,
                    "reversed_amount_cents": 0,
                }
            ), 200

        hold_posting = session.scalar(
            select(LedgerPosting).where(
                LedgerPosting.authorization_id == authorization.id,
                LedgerPosting.entry_type == EntryType.hold,
            )
        )
        if hold_posting is None:
            return jsonify({"error": "no hold found for authorization"}), 409

        capture_posting = session.scalar(
            select(LedgerPosting).where(
                LedgerPosting.authorization_id == authorization.id,
                LedgerPosting.entry_type == EntryType.capture,
            )
        )
        released = session.scalar(
            select(func.coalesce(func.sum(LedgerPosting.amount_cents), 0)).where(
                LedgerPosting.authorization_id == authorization.id,
                LedgerPosting.entry_type == EntryType.hold_release,
            )
        )

        captured = capture_posting.amount_cents if capture_posting is not None else 0
        remaining_held = hold_posting.amount_cents - captured - int(released)
        reversed_amount = 0
        if captured > 0:
            post_reversal(
                session,
                authorization.id,
                captured,
                idempotency_key=f"reversal-{authorization.id}",
                commit=False,
            )
            reversed_amount += captured
        if remaining_held > 0:
            post_hold_release(
                session,
                authorization.id,
                remaining_held,
                idempotency_key=f"hold-release-{authorization.id}",
                commit=False,
            )
            reversed_amount += remaining_held
        authorization.status = AuthorizationStatus.reversed
        session.commit()
        return jsonify(
            {
                "authorization_id": str(authorization.public_id),
                "status": authorization.status.value,
                "reversed_amount_cents": reversed_amount,
            }
        ), 200
