from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import Authorization, Card

bp = Blueprint("authorizations", __name__)


def _to_dict(authorization):
    return {
        "id": authorization.id,
        "idempotency_key": authorization.idempotency_key,
        "card_id": authorization.card_id,
        "merchant_name": authorization.merchant_name,
        "mcc": authorization.mcc,
        "amount_cents": authorization.amount_cents,
        "status": authorization.status.value,
        "decision_reason": authorization.decision_reason,
        "risk_score": authorization.risk_score,
        "created_at": authorization.created_at.isoformat(),
        "expires_at": authorization.expires_at.isoformat() if authorization.expires_at else None,
    }


@bp.get("")
def list_authorizations():
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    session_factory = current_app.extensions["session_factory"]
    with session_factory() as session:
        rows = session.scalars(
            select(Authorization).order_by(Authorization.id.desc()).limit(limit)
        ).all()
        return jsonify([_to_dict(row) for row in rows])


@bp.post("")
def create_authorization():
    payload = request.get_json(silent=True) or {}
    required = ("idempotency_key", "card_id", "merchant_name", "mcc", "amount_cents")
    missing = [field for field in required if field not in payload]
    if missing:
        return jsonify({"error": "missing fields", "fields": missing}), 400
    amount_cents = payload["amount_cents"]
    if not isinstance(amount_cents, int) or amount_cents <= 0:
        return jsonify({"error": "amount_cents must be a positive integer"}), 400

    session_factory = current_app.extensions["session_factory"]
    with session_factory() as session:
        existing = session.scalar(
            select(Authorization).where(
                Authorization.idempotency_key == payload["idempotency_key"]
            )
        )
        if existing is not None:
            return jsonify(_to_dict(existing)), 200
        card = session.get(Card, payload["card_id"])
        if card is None:
            return jsonify({"error": "card not found"}), 404
        authorization = Authorization(
            idempotency_key=payload["idempotency_key"],
            card_id=card.id,
            merchant_name=payload["merchant_name"],
            mcc=payload["mcc"],
            amount_cents=amount_cents,
        )
        session.add(authorization)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            existing = session.scalar(
                select(Authorization).where(
                    Authorization.idempotency_key == payload["idempotency_key"]
                )
            )
            if existing is None:
                raise
            return jsonify(_to_dict(existing)), 200
        return jsonify(_to_dict(authorization)), 201
