import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.ledger import (
    account_balance,
    authorization_net,
    post_capture,
    post_hold,
    post_hold_release,
    post_reversal,
)
from app.ledger.service import UnbalancedPostingError, _post
from app.models import AccountType, Direction, EntryType, LedgerEntry, LedgerPosting


def _entries(session, authorization_id):
    return session.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.authorization_id == authorization_id)
        .order_by(LedgerEntry.id)
    ).all()


def _group_totals(entries):
    debits = sum(
        entry.amount_cents for entry in entries if entry.direction is Direction.debit
    )
    credits = sum(
        entry.amount_cents for entry in entries if entry.direction is Direction.credit
    )
    return debits, credits


def _accounts_by_type(session, company_id):
    from app.models import Account

    accounts = session.scalars(
        select(Account).where(Account.company_id == company_id)
    ).all()
    return {account.account_type: account for account in accounts}


def test_post_hold_balances_to_zero_net(session, company, authorization):
    result = post_hold(
        session, authorization.id, amount_cents=10_000, idempotency_key="hold-1"
    )

    assert result.replayed is False
    entries = _entries(session, authorization.id)
    assert len(entries) == 2

    debits, credits = _group_totals(entries)
    assert debits == credits == 10_000
    assert authorization_net(session, authorization.id) == 0

    accounts = _accounts_by_type(session, company.id)
    assert account_balance(session, accounts[AccountType.holds].id) == 10_000
    assert (
        account_balance(session, accounts[AccountType.available_credit].id) == -10_000
    )
    assert account_balance(session, accounts[AccountType.settled].id) == 0


def test_idempotent_replay_creates_exactly_one_posting(session, authorization):
    results = [
        post_hold(
            session, authorization.id, amount_cents=10_000, idempotency_key="hold-idem"
        )
        for _ in range(5)
    ]

    assert results[0].replayed is False
    assert all(result.replayed is True for result in results[1:])
    assert {result.posting_id for result in results} == {results[0].posting_id}

    postings = session.scalars(select(LedgerPosting)).all()
    assert len(postings) == 1
    entries = _entries(session, authorization.id)
    assert len(entries) == 2
    debits, credits = _group_totals(entries)
    assert debits == credits


def test_duplicate_posting_with_new_key_fails_loudly(session, authorization):
    post_hold(session, authorization.id, 10_000, idempotency_key="hold-a")

    with pytest.raises(IntegrityError):
        post_hold(session, authorization.id, 10_000, idempotency_key="hold-b")

    assert len(_entries(session, authorization.id)) == 2


def test_unbalanced_posting_rejected(session, company, authorization):
    accounts = _accounts_by_type(session, company.id)
    legs = [
        (accounts[AccountType.holds], Direction.debit, 10_000),
        (accounts[AccountType.available_credit], Direction.credit, 5_000),
    ]

    with pytest.raises(UnbalancedPostingError):
        _post(session, authorization.id, EntryType.hold, 10_000, "unbalanced-1", legs)

    assert _entries(session, authorization.id) == []


def test_hold_capture_reversal_lifecycle(session, company, authorization):
    post_hold(session, authorization.id, 25_000, idempotency_key="lc-hold")
    post_capture(session, authorization.id, 25_000, idempotency_key="lc-capture")
    post_reversal(session, authorization.id, 25_000, idempotency_key="lc-reversal")

    accounts = _accounts_by_type(session, company.id)
    assert account_balance(session, accounts[AccountType.available_credit].id) == 0
    assert account_balance(session, accounts[AccountType.holds].id) == 0
    assert account_balance(session, accounts[AccountType.settled].id) == 0
    assert authorization_net(session, authorization.id) == 0

    for entry_type in (EntryType.hold, EntryType.capture, EntryType.reversal):
        entries = session.scalars(
            select(LedgerEntry).where(
                LedgerEntry.authorization_id == authorization.id,
                LedgerEntry.entry_type == entry_type,
            )
        ).all()
        debits, credits = _group_totals(entries)
        assert debits == credits


def test_hold_release_lifecycle(session, company, authorization):
    post_hold(session, authorization.id, 5_000, idempotency_key="hr-hold")
    post_hold_release(session, authorization.id, 5_000, idempotency_key="hr-release")

    accounts = _accounts_by_type(session, company.id)
    assert account_balance(session, accounts[AccountType.holds].id) == 0
    assert account_balance(session, accounts[AccountType.available_credit].id) == 0
    assert authorization_net(session, authorization.id) == 0
