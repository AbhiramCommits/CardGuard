import asyncio
import os
import threading
from datetime import timedelta

from sqlalchemy import select
from temporalio import activity
from temporalio.common import RetryPolicy

from app.config import Config
from app.db import make_engine, make_session_factory
from app.ledger import post_hold, post_hold_release
from app.models import Authorization, AuthorizationStatus


class ValidationError(Exception):
    pass


ACTIVITY_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_attempts=5,
    non_retryable_error_types=["ValidationError"],
)

ACTIVITY_START_TO_CLOSE_TIMEOUT = timedelta(seconds=30)

_lock = threading.Lock()
_engine = None
_session_factory = None

fail_next_calls = 0


def _database_url():
    return os.environ.get("DATABASE_URL", Config.DATABASE_URL)


def _get_session():
    global _engine, _session_factory
    with _lock:
        if _engine is None:
            _engine = make_engine(_database_url())
            _session_factory = make_session_factory(_engine)
    return _session_factory()


def _maybe_fail():
    global fail_next_calls
    if fail_next_calls > 0:
        fail_next_calls -= 1
        raise RuntimeError("simulated transient database failure")


def _get_authorization(session, authorization_id):
    authorization = session.get(Authorization, authorization_id)
    if authorization is None:
        raise ValidationError(f"authorization {authorization_id} not found")
    return authorization


@activity.defn
async def post_review_hold(authorization_id: int) -> str:
    _maybe_fail()
    session = _get_session()
    try:
        authorization = _get_authorization(session, authorization_id)
        if authorization.status == AuthorizationStatus.approved:
            return "held"
        if authorization.status != AuthorizationStatus.pending_review:
            raise ValidationError(
                f"authorization {authorization_id} is {authorization.status.value}, "
                "not pending_review"
            )
        post_hold(
            session,
            authorization_id,
            authorization.amount_cents,
            idempotency_key=f"hold-{authorization_id}",
        )
        authorization.status = AuthorizationStatus.approved
        authorization.decision_reason = "APPROVED"
        session.commit()
        return "held"
    finally:
        session.close()


@activity.defn
async def finalize_decline(
    authorization_id: int, reason: str, reviewer_id: str | None, note: str | None
) -> str:
    _maybe_fail()
    session = _get_session()
    try:
        authorization = _get_authorization(session, authorization_id)
        authorization.status = AuthorizationStatus.declined
        authorization.decision_reason = reason
        session.commit()
        return "declined"
    finally:
        session.close()


@activity.defn
async def release_expired_hold(authorization_id: int) -> str:
    _maybe_fail()
    session = _get_session()
    try:
        authorization = _get_authorization(session, authorization_id)
        if authorization.status in (
            AuthorizationStatus.captured,
            AuthorizationStatus.reversed,
            AuthorizationStatus.expired,
        ):
            return "noop"
        if authorization.status != AuthorizationStatus.approved:
            raise ValidationError(
                f"authorization {authorization_id} in status {authorization.status.value}"
            )
        post_hold_release(
            session,
            authorization_id,
            authorization.amount_cents,
            idempotency_key=f"hold-release-{authorization_id}",
        )
        authorization.status = AuthorizationStatus.expired
        authorization.decision_reason = "EXPIRED"
        session.commit()
        return "released"
    finally:
        session.close()
