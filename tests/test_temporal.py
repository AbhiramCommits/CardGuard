import asyncio
import os
import uuid as uuid_module
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest_asyncio
from sqlalchemy import select
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from app.config import TestingConfig
from app.ledger import post_capture, post_hold
from app.models import (
    Authorization,
    AuthorizationStatus,
    EntryType,
    LedgerEntry,
    SpendPolicy,
)
from tests.test_authorization_api import _payload, _post, _postings
from workflows import HoldExpiryWorkflow, ReviewWorkflow
from workflows import activities as activities_module
from workflows.activities import (
    finalize_decline,
    post_review_hold,
    release_expired_hold,
)

TASK_QUEUE = "cardguard-risk"

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", TestingConfig.DATABASE_URL)


def _ephemeral_target(env):
    server = vars(env).get("_server")
    if server is None:
        raise RuntimeError("ephemeral server not found")
    return server.target


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def temporal():
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    env = await WorkflowEnvironment.start_time_skipping()
    os.environ["TEMPORAL_HOST"] = _ephemeral_target(env)
    async with Worker(
        env.client,
        task_queue=TASK_QUEUE,
        workflows=[ReviewWorkflow, HoldExpiryWorkflow],
        activities=[post_review_hold, finalize_decline, release_expired_hold],
    ):
        yield env
    await env.shutdown()


def _review_authorization(session, card, amount_cents=10_000):
    authorization = Authorization(
        idempotency_key=f"rev-{uuid4().hex}",
        card_id=card.id,
        merchant_name="REVIEW MERCHANT",
        mcc="5411",
        amount_cents=amount_cents,
        status=AuthorizationStatus.pending_review,
        decision_reason="NEAR_MONTHLY_LIMIT",
    )
    session.add(authorization)
    session.commit()
    return authorization


def _entries(session, authorization_id):
    return session.scalars(
        select(LedgerEntry).where(LedgerEntry.authorization_id == authorization_id)
    ).all()


async def test_signal_approve_posts_hold(temporal, session, card):
    authorization = _review_authorization(session, card)

    handle = await temporal.client.start_workflow(
        ReviewWorkflow.run,
        args=[authorization.id, 60],
        id=f"review-{authorization.public_id}",
        task_queue=TASK_QUEUE,
    )
    state = await handle.query(ReviewWorkflow.get_state)
    assert state.status == "pending_review"

    await handle.signal(
        ReviewWorkflow.reviewer_decision, args=[True, "alice", "looks fine"]
    )
    assert await handle.result() == "APPROVED"

    session.expire_all()
    authorization = session.get(Authorization, authorization.id)
    assert authorization.status == AuthorizationStatus.approved
    postings = _postings(session, authorization.id)
    assert [p.entry_type for p in postings] == [EntryType.hold]
    assert len(_entries(session, authorization.id)) == 2


async def test_signal_decline_finalizes(temporal, session, card):
    authorization = _review_authorization(session, card)

    handle = await temporal.client.start_workflow(
        ReviewWorkflow.run,
        args=[authorization.id, 60],
        id=f"review-{authorization.public_id}",
        task_queue=TASK_QUEUE,
    )
    await handle.signal(
        ReviewWorkflow.reviewer_decision, args=[False, "bob", "suspicious"]
    )
    assert await handle.result() == "DECLINED"

    state = await handle.query(ReviewWorkflow.get_state)
    assert state.status == "declined"
    assert state.reviewer_id == "bob"

    session.expire_all()
    authorization = session.get(Authorization, authorization.id)
    assert authorization.status == AuthorizationStatus.declined
    assert authorization.decision_reason == "REVIEW_DECLINED"
    assert _postings(session, authorization.id) == []


async def test_timeout_auto_declines(temporal, session, card):
    authorization = _review_authorization(session, card)

    handle = await temporal.client.start_workflow(
        ReviewWorkflow.run,
        args=[authorization.id, 2],
        id=f"review-{authorization.public_id}",
        task_queue=TASK_QUEUE,
    )
    assert await handle.result() == "REVIEW_TIMEOUT"

    state = await handle.query(ReviewWorkflow.get_state)
    assert state.status == "auto_declined"
    assert state.decision == "timeout"

    session.expire_all()
    authorization = session.get(Authorization, authorization.id)
    assert authorization.status == AuthorizationStatus.declined
    assert authorization.decision_reason == "REVIEW_TIMEOUT"
    assert _postings(session, authorization.id) == []


async def test_hold_expiry_releases(temporal, session, card):
    authorization = Authorization(
        idempotency_key=f"exp-{uuid4().hex}",
        card_id=card.id,
        merchant_name="EXPIRY MERCHANT",
        mcc="5411",
        amount_cents=8_000,
        status=AuthorizationStatus.approved,
    )
    session.add(authorization)
    session.flush()
    post_hold(
        session, authorization.id, 8_000, idempotency_key=f"hold-{authorization.id}"
    )
    expires_at = datetime.now(UTC).timestamp() + 2

    handle = await temporal.client.start_workflow(
        HoldExpiryWorkflow.run,
        args=[authorization.id, expires_at],
        id=f"hold-expiry-{authorization.public_id}",
        task_queue=TASK_QUEUE,
    )
    assert await handle.result() == "released"

    session.expire_all()
    authorization = session.get(Authorization, authorization.id)
    assert authorization.status == AuthorizationStatus.expired
    assert authorization.decision_reason == "EXPIRED"
    entries = _entries(session, authorization.id)
    assert [e.entry_type for e in entries].count(EntryType.hold_release) == 2


async def test_hold_expiry_noop_when_captured(temporal, session, card):
    authorization = Authorization(
        idempotency_key=f"exp-{uuid4().hex}",
        card_id=card.id,
        merchant_name="EXPIRY MERCHANT",
        mcc="5411",
        amount_cents=8_000,
        status=AuthorizationStatus.approved,
    )
    session.add(authorization)
    session.flush()
    post_hold(
        session, authorization.id, 8_000, idempotency_key=f"hold-{authorization.id}"
    )
    post_capture(
        session, authorization.id, 8_000, idempotency_key=f"capture-{authorization.id}"
    )
    session.expire_all()
    authorization = session.get(Authorization, authorization.id)
    authorization.status = AuthorizationStatus.captured
    session.commit()

    handle = await temporal.client.start_workflow(
        HoldExpiryWorkflow.run,
        args=[authorization.id, datetime.now(UTC).timestamp() + 2],
        id=f"hold-expiry-{authorization.public_id}",
        task_queue=TASK_QUEUE,
    )
    assert await handle.result() == "noop"

    session.expire_all()
    authorization = session.get(Authorization, authorization.id)
    assert authorization.status == AuthorizationStatus.captured
    entry_types = [e.entry_type for e in _entries(session, authorization.id)]
    assert entry_types.count(EntryType.hold_release) == 0


async def test_activity_retry_no_double_post(temporal, session, card):
    authorization = _review_authorization(session, card)
    activities_module.fail_next_calls = 1

    handle = await temporal.client.start_workflow(
        ReviewWorkflow.run,
        args=[authorization.id, 60],
        id=f"review-{authorization.public_id}",
        task_queue=TASK_QUEUE,
    )
    await handle.signal(
        ReviewWorkflow.reviewer_decision, args=[True, "carol", "retry me"]
    )
    assert await handle.result() == "APPROVED"

    postings = _postings(session, authorization.id)
    assert len(postings) == 1
    assert len(_entries(session, authorization.id)) == 2


async def test_replayed_activity_does_not_double_post(temporal, session, card):
    authorization = _review_authorization(session, card)

    assert await post_review_hold(authorization.id) == "held"
    assert await post_review_hold(authorization.id) == "held"

    postings = _postings(session, authorization.id)
    assert len(postings) == 1
    assert len(_entries(session, authorization.id)) == 2


async def test_review_decision_endpoints(temporal, app, session, card):
    policy = session.scalar(
        select(SpendPolicy).where(SpendPolicy.employee_id == card.employee_id)
    )
    policy.monthly_limit_cents = 100_000
    session.commit()

    payload = _payload(card, amount_cents=95_000)
    body = (await asyncio.to_thread(_post, app.test_client(), payload)).get_json()
    assert body["decision"] == "review"
    authorization_id = body["authorization_id"]

    status = await asyncio.to_thread(
        lambda: app.test_client().get(
            f"/v1/authorizations/{authorization_id}/review-status"
        )
    )
    assert status.status_code == 200
    assert status.get_json()["status"] == "pending_review"

    response = await asyncio.to_thread(
        lambda: app.test_client().post(
            f"/v1/authorizations/{authorization_id}/review-decision",
            json={"decision": "approve", "reviewer_id": "bob", "note": "verified"},
        )
    )
    assert response.status_code == 202

    state = None
    for _ in range(100):
        state = (
            await asyncio.to_thread(
                lambda: app.test_client().get(
                    f"/v1/authorizations/{authorization_id}/review-status"
                )
            )
        ).get_json()
        if state["status"] == "approved":
            break
        await asyncio.sleep(0.2)
    assert state["status"] == "approved"
    assert state["reviewer_id"] == "bob"

    session.expire_all()
    authorization = session.scalar(
        select(Authorization).where(Authorization.public_id == UUID(authorization_id))
    )
    assert authorization.status == AuthorizationStatus.approved
    assert len(_postings(session, authorization.id)) == 1


async def test_review_status_unknown_workflow_404(temporal, app):
    response = await asyncio.to_thread(
        lambda: app.test_client().get(
            f"/v1/authorizations/{uuid_module.uuid4()}/review-status"
        )
    )
    assert response.status_code == 404


async def test_review_decision_invalid_400(temporal, app):
    response = await asyncio.to_thread(
        lambda: app.test_client().post(
            f"/v1/authorizations/{uuid_module.uuid4()}/review-decision",
            json={"decision": "maybe"},
        )
    )
    assert response.status_code == 400
