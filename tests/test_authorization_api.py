import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select

from app.ledger import account_balance, post_hold
from app.models import (
    Account,
    AccountType,
    Authorization,
    AuthorizationStatus,
    EntryType,
    LedgerEntry,
    LedgerPosting,
    SpendPolicy,
)


def _payload(card, **overrides):
    payload = {
        "idempotency_key": uuid4().hex,
        "card_token": card.token,
        "merchant_name": "TEST MERCHANT",
        "mcc": "5411",
        "amount_cents": 5_000,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    payload.update(overrides)
    return payload


API_HEADERS = {"X-Api-Key": "test-key"}


def _post(client, payload):
    return client.post("/v1/authorizations", json=payload, headers=API_HEADERS)


def _policy(session, card):
    return session.scalar(
        select(SpendPolicy).where(SpendPolicy.employee_id == card.employee_id)
    )


def _accounts_by_type(session, company_id):
    return {
        account.account_type: account
        for account in session.scalars(
            select(Account).where(Account.company_id == company_id)
        ).all()
    }


def _postings(session, authorization_id):
    return session.scalars(
        select(LedgerPosting).where(LedgerPosting.authorization_id == authorization_id)
    ).all()


def test_approve_posts_hold_and_sets_expiry(app, session, card):
    payload = _payload(card)
    response = _post(app.test_client(), payload)

    assert response.status_code == 200
    assert "Idempotent-Replay" not in response.headers
    body = response.get_json()
    assert body["decision"] == "approve"
    assert body["decision_reason"] == "APPROVED"
    assert body["risk_score"] is None
    assert isinstance(body["latency_ms"], (int, float))
    UUID(body["authorization_id"])

    authorization = session.scalar(
        select(Authorization).where(
            Authorization.idempotency_key == payload["idempotency_key"]
        )
    )
    assert authorization is not None
    assert authorization.status == AuthorizationStatus.approved
    assert str(authorization.public_id) == body["authorization_id"]
    assert authorization.expires_at is not None
    assert (
        abs(
            (
                authorization.expires_at - (datetime.now(UTC) + timedelta(days=7))
            ).total_seconds()
        )
        < 60
    )

    postings = _postings(session, authorization.id)
    assert [p.entry_type for p in postings] == [EntryType.hold]
    entries = session.scalars(
        select(LedgerEntry).where(LedgerEntry.authorization_id == authorization.id)
    ).all()
    assert len(entries) == 2


def test_replay_returns_stored_response_verbatim(app, session, card):
    client = app.test_client()
    payload = _payload(card)
    first = _post(client, payload)
    second = _post(client, payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert "Idempotent-Replay" not in first.headers
    assert second.headers.get("Idempotent-Replay") == "true"
    assert second.get_json() == first.get_json()

    authorizations = session.scalars(
        select(Authorization).where(
            Authorization.idempotency_key == payload["idempotency_key"]
        )
    ).all()
    assert len(authorizations) == 1
    assert len(session.scalars(select(LedgerPosting)).all()) == 1


def test_conflicting_body_returns_409(app, card):
    client = app.test_client()
    payload = _payload(card)
    assert _post(client, payload).status_code == 200

    conflicting = {**payload, "amount_cents": payload["amount_cents"] + 100}
    response = _post(client, conflicting)
    assert response.status_code == 409


def test_mcc_blocked_declines(app, session, card):
    policy = _policy(session, card)
    policy.blocked_mccs = ["5541"]
    session.commit()

    payload = _payload(card, mcc="5541")
    body = _post(app.test_client(), payload).get_json()
    assert body["decision"] == "decline"
    assert body["decision_reason"] == "MCC_BLOCKED"
    assert body["risk_score"] == 100.0

    authorization = session.scalar(
        select(Authorization).where(
            Authorization.idempotency_key == payload["idempotency_key"]
        )
    )
    assert authorization.status == AuthorizationStatus.declined
    assert _postings(session, authorization.id) == []


def test_per_txn_limit_declines(app, session, card):
    policy = _policy(session, card)
    policy.per_transaction_limit_cents = 10_000
    session.commit()

    body = _post(app.test_client(), _payload(card, amount_cents=10_001)).get_json()
    assert body["decision"] == "decline"
    assert body["decision_reason"] == "PER_TXN_LIMIT"
    assert body["risk_score"] == 100.0


def test_monthly_limit_declines(app, session, card):
    policy = _policy(session, card)
    policy.monthly_limit_cents = 200_000
    session.commit()

    pre = Authorization(
        idempotency_key=f"pre-{uuid4().hex}",
        card_id=card.id,
        merchant_name="PRE",
        mcc="5411",
        amount_cents=150_000,
        status=AuthorizationStatus.approved,
    )
    session.add(pre)
    session.flush()
    post_hold(session, pre.id, 150_000, idempotency_key=f"pre-hold-{pre.id}")

    body = _post(app.test_client(), _payload(card, amount_cents=60_000)).get_json()
    assert body["decision"] == "decline"
    assert body["decision_reason"] == "MONTHLY_LIMIT"


def test_velocity_declines(app, session, card):
    policy = _policy(session, card)
    policy.velocity_max_auths = 2
    policy.velocity_window_minutes = 60
    session.commit()

    for _ in range(2):
        session.add(
            Authorization(
                idempotency_key=f"vel-{uuid4().hex}",
                card_id=card.id,
                merchant_name="VEL",
                mcc="5411",
                amount_cents=1_000,
                status=AuthorizationStatus.approved,
            )
        )
    session.commit()

    body = _post(app.test_client(), _payload(card)).get_json()
    assert body["decision"] == "decline"
    assert body["decision_reason"] == "VELOCITY"


def test_near_monthly_limit_reviews(app, session, card):
    policy = _policy(session, card)
    policy.monthly_limit_cents = 100_000
    session.commit()

    payload = _payload(card, amount_cents=95_000)
    body = _post(app.test_client(), payload).get_json()
    assert body["decision"] == "review"
    assert body["decision_reason"] == "NEAR_MONTHLY_LIMIT"

    authorization = session.scalar(
        select(Authorization).where(
            Authorization.idempotency_key == payload["idempotency_key"]
        )
    )
    assert authorization.status == AuthorizationStatus.pending_review
    assert _postings(session, authorization.id) == []


def test_partial_capture(app, session, card, company):
    payload = _payload(card, amount_cents=10_000)
    authorization_id = _post(app.test_client(), payload).get_json()["authorization_id"]

    response = app.test_client().post(
        f"/v1/authorizations/{authorization_id}/capture",
        json={"amount_cents": 6_000},
        headers=API_HEADERS,
    )
    assert response.status_code == 200
    result = response.get_json()
    assert result["captured_amount_cents"] == 6_000
    assert result["released_amount_cents"] == 4_000
    assert result["status"] == "captured"

    accounts = _accounts_by_type(session, company.id)
    assert account_balance(session, accounts[AccountType.holds].id) == 0
    assert account_balance(session, accounts[AccountType.settled].id) == 6_000
    assert account_balance(session, accounts[AccountType.available_credit].id) == -6_000


def test_over_capture_rejected(app, card):
    payload = _payload(card, amount_cents=10_000)
    authorization_id = _post(app.test_client(), payload).get_json()["authorization_id"]
    client = app.test_client()

    response = client.post(
        f"/v1/authorizations/{authorization_id}/capture",
        json={"amount_cents": 12_000},
        headers=API_HEADERS,
    )
    assert response.status_code == 409
    assert response.get_json()["held_amount_cents"] == 10_000

    assert (
        client.post(
            f"/v1/authorizations/{authorization_id}/capture",
            json={"amount_cents": 10_000},
            headers=API_HEADERS,
        ).status_code
        == 200
    )
    second = client.post(
        f"/v1/authorizations/{authorization_id}/capture",
        json={"amount_cents": 5_000},
        headers=API_HEADERS,
    )
    assert second.status_code == 409


def test_reverse_authorization(app, session, card, company):
    payload = _payload(card, amount_cents=10_000)
    authorization_id = _post(app.test_client(), payload).get_json()["authorization_id"]

    response = app.test_client().post(
        f"/v1/authorizations/{authorization_id}/reverse", headers=API_HEADERS
    )
    assert response.status_code == 200
    result = response.get_json()
    assert result["status"] == "reversed"
    assert result["reversed_amount_cents"] == 10_000

    accounts = _accounts_by_type(session, company.id)
    assert account_balance(session, accounts[AccountType.holds].id) == 0
    assert account_balance(session, accounts[AccountType.available_credit].id) == 0


def test_account_balance_endpoint(app, session, card, company):
    _post(app.test_client(), _payload(card, amount_cents=5_000))
    accounts = _accounts_by_type(session, company.id)
    client = app.test_client()

    holds = client.get(
        f"/v1/accounts/{accounts[AccountType.holds].id}/balance", headers=API_HEADERS
    )
    assert holds.status_code == 200
    assert holds.get_json()["balance_cents"] == 5_000

    available = client.get(
        f"/v1/accounts/{accounts[AccountType.available_credit].id}/balance",
        headers=API_HEADERS,
    )
    assert available.get_json()["balance_cents"] == -5_000


def test_readyz(app):
    response = app.test_client().get("/readyz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ready"}


def test_validation_errors(app, card):
    client = app.test_client()
    payload = _payload(card)

    missing = {k: v for k, v in payload.items() if k != "timestamp"}
    assert (
        client.post("/v1/authorizations", json=missing, headers=API_HEADERS).status_code
        == 400
    )

    bad_amount = {**payload, "amount_cents": -5}
    assert (
        client.post(
            "/v1/authorizations", json=bad_amount, headers=API_HEADERS
        ).status_code
        == 400
    )

    bad_timestamp = {**payload, "timestamp": "not-a-date"}
    assert (
        client.post(
            "/v1/authorizations", json=bad_timestamp, headers=API_HEADERS
        ).status_code
        == 400
    )

    unknown_card = {**payload, "card_token": "card_missing"}
    assert (
        client.post(
            "/v1/authorizations", json=unknown_card, headers=API_HEADERS
        ).status_code
        == 404
    )


def test_concurrent_identical_requests_single_hold(app, session, card):
    payload = _payload(card)
    barrier = threading.Barrier(20)

    def worker(_):
        barrier.wait()
        return app.test_client().post(
            "/v1/authorizations", json=payload, headers=API_HEADERS
        )

    with ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(worker, range(20)))

    assert all(response.status_code == 200 for response in responses)
    replays = [
        response
        for response in responses
        if response.headers.get("Idempotent-Replay") == "true"
    ]
    assert len(replays) == 19

    authorizations = session.scalars(
        select(Authorization).where(
            Authorization.idempotency_key == payload["idempotency_key"]
        )
    ).all()
    assert len(authorizations) == 1
    postings = _postings(session, authorizations[0].id)
    assert len(postings) == 1
    assert postings[0].entry_type == EntryType.hold
    entries = session.scalars(
        select(LedgerEntry).where(LedgerEntry.authorization_id == authorizations[0].id)
    ).all()
    assert len(entries) == 2
