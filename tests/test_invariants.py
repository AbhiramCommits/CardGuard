import threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select, text

from app.ledger import (
    account_balance,
    authorization_net,
    post_capture,
    post_hold,
    post_hold_release,
    post_reversal,
)
from app.models import (
    Account,
    AccountType,
    Authorization,
    AuthorizationStatus,
    Base,
    Card,
    Company,
    Direction,
    Employee,
    EntryType,
    LedgerEntry,
    LedgerPosting,
    SpendPolicy,
)
from tests.test_authorization_api import _payload, _post

CREDIT_LIMIT_CENTS = 1_000_000


def _truncate_all(session):
    tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
    session.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    session.commit()
    session.expire_all()


def _fresh_company(session):
    company = Company(name=f"prop-{uuid4().hex}")
    session.add(company)
    session.flush()
    accounts = {}
    for account_type in AccountType:
        account = Account(company_id=company.id, account_type=account_type)
        session.add(account)
        session.flush()
        accounts[account_type] = account
    employee = Employee(
        company_id=company.id, name="Prop", email=f"prop-{uuid4().hex}@example.com"
    )
    session.add(employee)
    session.flush()
    card = Card(employee_id=employee.id, last_four="4242")
    session.add(card)
    session.commit()
    return company, card, accounts


def _assert_ledger_invariants(session, accounts, authorizations):
    entries = session.scalars(select(LedgerEntry)).all()
    debits = sum(e.amount_cents for e in entries if e.direction is Direction.debit)
    credits = sum(e.amount_cents for e in entries if e.direction is Direction.credit)
    assert debits == credits, "total debits must equal total credits"

    holds = account_balance(session, accounts[AccountType.holds].id)
    settled = account_balance(session, accounts[AccountType.settled].id)
    available = account_balance(session, accounts[AccountType.available_credit].id)
    assert holds >= 0, f"holds account went negative: {holds}"
    assert settled >= 0, f"settled account went negative: {settled}"
    assert available <= 0, f"available credit account went positive: {available}"

    expected_holds = sum(
        a["amount"] - a["captured"] - a["released"] for a in authorizations
    )
    expected_settled = sum(a["captured"] for a in authorizations)
    assert holds == expected_holds, f"holds {holds} != mirror {expected_holds}"
    assert settled == expected_settled, (
        f"settled {settled} != mirror {expected_settled}"
    )

    committed = holds + settled
    assert CREDIT_LIMIT_CENTS - committed >= 0, "derived available credit went negative"
    assert committed + available == 0

    for state in authorizations:
        assert state["captured"] <= state["amount"], "captured must never exceed held"
        assert authorization_net(session, state["id"]) == 0


@given(st.data())
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_ledger_invariants_under_random_operations(session, data):
    _truncate_all(session)
    _company, card, accounts = _fresh_company(session)
    authorizations = []
    exposure = 0

    steps = data.draw(st.integers(5, 60))
    for _ in range(steps):
        choices = []
        if len(authorizations) < 3 and exposure < CREDIT_LIMIT_CENTS - 100:
            choices.append("create")
        for state in authorizations:
            if state["amount"] - state["captured"] - state["released"] > 0:
                choices.append(("capture", state))
                choices.append(("expire", state))
            if (
                state["captured"] > 0
                or state["amount"] - state["captured"] - state["released"] > 0
            ):
                choices.append(("reverse", state))

        if not choices:
            break
        op = data.draw(st.sampled_from(choices))
        if op == "create":
            amount = data.draw(
                st.integers(100, min(50_000, CREDIT_LIMIT_CENTS - exposure))
            )
            authorization = Authorization(
                idempotency_key=f"prop-{uuid4().hex}",
                card_id=card.id,
                merchant_name="PROP MERCHANT",
                mcc="5411",
                amount_cents=amount,
                status=AuthorizationStatus.approved,
            )
            session.add(authorization)
            session.flush()
            post_hold(
                session,
                authorization.id,
                amount,
                idempotency_key=f"hold-{authorization.id}",
            )
            authorizations.append(
                {"id": authorization.id, "amount": amount, "captured": 0, "released": 0}
            )
            exposure += amount
        else:
            kind, state = op
            held = state["amount"] - state["captured"] - state["released"]
            authorization_id = state["id"]
            if kind == "capture":
                amount = data.draw(st.integers(100, held))
                post_capture(
                    session,
                    authorization_id,
                    amount,
                    idempotency_key=f"capture-{authorization_id}",
                )
                state["captured"] += amount
                remainder = held - amount
                if remainder > 0:
                    post_hold_release(
                        session,
                        authorization_id,
                        remainder,
                        idempotency_key=f"hold-release-{authorization_id}",
                    )
                    state["released"] += remainder
            elif kind == "expire":
                post_hold_release(
                    session,
                    authorization_id,
                    held,
                    idempotency_key=f"hold-release-{authorization_id}",
                )
                state["released"] += held
            else:
                if state["captured"] > 0:
                    post_reversal(
                        session,
                        authorization_id,
                        state["captured"],
                        idempotency_key=f"reversal-{authorization_id}",
                    )
                    state["released"] += state["captured"]
                    state["captured"] = 0
                if held > 0:
                    post_hold_release(
                        session,
                        authorization_id,
                        held,
                        idempotency_key=f"hold-release-{authorization_id}",
                    )
                    state["released"] += held

        _assert_ledger_invariants(session, accounts, authorizations)

    assert len(authorizations) >= 1


def test_5000_request_replay_storm_zero_duplicate_postings(app, session, card):
    policy = session.scalar(
        select(SpendPolicy).where(SpendPolicy.employee_id == card.employee_id)
    )
    policy.monthly_limit_cents = 10_000_000_000
    policy.per_transaction_limit_cents = 100_000
    policy.blocked_mccs = []
    policy.velocity_max_auths = 1_000_000
    session.commit()

    unique_count = 3500
    replay_count = 1500
    payloads = [
        _payload(card, amount_cents=1_000 + i % 4_000) for i in range(unique_count)
    ]
    for payload in payloads:
        payload["idempotency_key"] = f"storm-{uuid4().hex}"

    index_lock = threading.Lock()
    sent_index = 0
    sent_payloads = []

    def worker():
        nonlocal sent_index
        for _ in range((unique_count + replay_count) // 24 + 1):
            with index_lock:
                if sent_index >= unique_count + replay_count:
                    return
                sent_index += 1
                total = sent_index
            if total <= unique_count or not sent_payloads:
                payload = payloads[(total - 1) % len(payloads)]
                with index_lock:
                    sent_payloads.append(payload)
            else:
                payload = sent_payloads[total % len(sent_payloads)]
            response = _post(app.test_client(), payload)
            assert response.status_code == 200, response.get_data(as_text=True)

    with ThreadPoolExecutor(max_workers=24) as pool:
        list(pool.map(lambda _: worker(), range(24)))

    approved = session.scalar(
        select(func.count())
        .select_from(Authorization)
        .where(
            Authorization.status == AuthorizationStatus.approved,
            Authorization.idempotency_key.like("storm-%"),
        )
    )
    assert approved == unique_count

    hold_postings = session.scalar(
        select(func.count())
        .select_from(LedgerPosting)
        .where(LedgerPosting.entry_type == EntryType.hold)
    )
    hold_entries = session.scalar(
        select(func.count())
        .select_from(LedgerEntry)
        .where(LedgerEntry.entry_type == EntryType.hold)
    )
    total_postings = session.scalar(select(func.count()).select_from(LedgerPosting))
    assert hold_postings == approved, (
        "each approved authorization must have exactly one hold"
    )
    assert hold_entries == 2 * approved, (
        "each hold must post exactly two ledger entries"
    )
    assert total_postings == approved, "zero duplicate postings"

    duplicates = session.execute(
        select(LedgerPosting.authorization_id, func.count())
        .group_by(LedgerPosting.authorization_id)
        .having(func.count() > 1)
    ).all()
    assert duplicates == []
