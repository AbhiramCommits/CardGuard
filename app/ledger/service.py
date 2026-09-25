from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AccountType,
    Authorization,
    Card,
    Company,
    Direction,
    Employee,
    EntryType,
    LedgerEntry,
    LedgerPosting,
)


class UnbalancedPostingError(ValueError):
    pass


@dataclass(frozen=True)
class PostingResult:
    posting_id: int
    idempotency_key: str
    authorization_id: int
    entry_type: EntryType
    amount_cents: int
    replayed: bool
    entries: tuple[LedgerEntry, ...]


def _company_for_authorization(session: Session, authorization_id: int) -> Company:
    company = session.execute(
        select(Company)
        .join(Employee, Employee.company_id == Company.id)
        .join(Card, Card.employee_id == Employee.id)
        .join(Authorization, Authorization.card_id == Card.id)
        .where(Authorization.id == authorization_id)
    ).scalar_one_or_none()
    if company is None:
        raise ValueError(f"authorization {authorization_id} not found")
    return company


def _require_accounts(company: Company) -> dict[AccountType, Account]:
    accounts = {account.account_type: account for account in company.accounts}
    missing = [
        account_type for account_type in AccountType if account_type not in accounts
    ]
    if missing:
        raise ValueError(f"company {company.id} missing accounts: {missing}")
    return accounts


def _fetch_posting(session: Session, idempotency_key: str) -> LedgerPosting | None:
    return session.scalar(
        select(LedgerPosting).where(LedgerPosting.idempotency_key == idempotency_key)
    )


def _entries_for(session: Session, posting: LedgerPosting) -> tuple[LedgerEntry, ...]:
    rows = session.scalars(
        select(LedgerEntry)
        .where(LedgerEntry.posting_id == posting.id)
        .order_by(LedgerEntry.id)
    ).all()
    return tuple(rows)


def _replayed(session: Session, posting: LedgerPosting) -> PostingResult:
    return PostingResult(
        posting_id=posting.id,
        idempotency_key=posting.idempotency_key,
        authorization_id=posting.authorization_id,
        entry_type=posting.entry_type,
        amount_cents=posting.amount_cents,
        replayed=True,
        entries=_entries_for(session, posting),
    )


def _post(
    session: Session,
    authorization_id: int,
    entry_type: EntryType,
    amount_cents: int,
    idempotency_key: str,
    legs: list[tuple[Account, Direction, int]],
    commit: bool = True,
) -> PostingResult:
    existing = _fetch_posting(session, idempotency_key)
    if existing is not None:
        return _replayed(session, existing)

    debits = sum(
        amount for _, direction, amount in legs if direction is Direction.debit
    )
    credits = sum(
        amount for _, direction, amount in legs if direction is Direction.credit
    )
    if debits != credits or debits == 0:
        raise UnbalancedPostingError(
            f"unbalanced posting authorization_id={authorization_id} "
            f"entry_type={entry_type.value} debits={debits} credits={credits}"
        )

    posting = LedgerPosting(
        idempotency_key=idempotency_key,
        authorization_id=authorization_id,
        entry_type=entry_type,
        amount_cents=amount_cents,
    )
    session.add(posting)
    session.flush()
    entries = []
    for account, direction, amount in legs:
        entry = LedgerEntry(
            authorization_id=authorization_id,
            account_id=account.id,
            direction=direction,
            amount_cents=amount,
            entry_type=entry_type,
            posting_id=posting.id,
        )
        session.add(entry)
        entries.append(entry)
    if not commit:
        return PostingResult(
            posting_id=posting.id,
            idempotency_key=idempotency_key,
            authorization_id=authorization_id,
            entry_type=entry_type,
            amount_cents=amount_cents,
            replayed=False,
            entries=tuple(entries),
        )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = _fetch_posting(session, idempotency_key)
        if existing is None:
            raise
        return _replayed(session, existing)
    return PostingResult(
        posting_id=posting.id,
        idempotency_key=idempotency_key,
        authorization_id=authorization_id,
        entry_type=entry_type,
        amount_cents=amount_cents,
        replayed=False,
        entries=tuple(entries),
    )


def _posting(
    session: Session,
    authorization_id: int,
    entry_type: EntryType,
    amount_cents: int,
    idempotency_key: str | None,
    debit_account: AccountType,
    credit_account: AccountType,
    commit: bool = True,
) -> PostingResult:
    key = idempotency_key or f"{entry_type.value}-{authorization_id}-{uuid4().hex}"
    company = _company_for_authorization(session, authorization_id)
    accounts = _require_accounts(company)
    legs = [
        (accounts[debit_account], Direction.debit, amount_cents),
        (accounts[credit_account], Direction.credit, amount_cents),
    ]
    return _post(
        session, authorization_id, entry_type, amount_cents, key, legs, commit=commit
    )


def post_hold(
    session: Session,
    authorization_id: int,
    amount_cents: int,
    idempotency_key: str | None = None,
    commit: bool = True,
) -> PostingResult:
    return _posting(
        session,
        authorization_id,
        EntryType.hold,
        amount_cents,
        idempotency_key,
        AccountType.holds,
        AccountType.available_credit,
        commit=commit,
    )


def post_capture(
    session: Session,
    authorization_id: int,
    amount_cents: int,
    idempotency_key: str | None = None,
    commit: bool = True,
) -> PostingResult:
    return _posting(
        session,
        authorization_id,
        EntryType.capture,
        amount_cents,
        idempotency_key,
        AccountType.settled,
        AccountType.holds,
        commit=commit,
    )


def post_reversal(
    session: Session,
    authorization_id: int,
    amount_cents: int,
    idempotency_key: str | None = None,
    commit: bool = True,
) -> PostingResult:
    return _posting(
        session,
        authorization_id,
        EntryType.reversal,
        amount_cents,
        idempotency_key,
        AccountType.available_credit,
        AccountType.settled,
        commit=commit,
    )


def post_hold_release(
    session: Session,
    authorization_id: int,
    amount_cents: int,
    idempotency_key: str | None = None,
    commit: bool = True,
) -> PostingResult:
    return _posting(
        session,
        authorization_id,
        EntryType.hold_release,
        amount_cents,
        idempotency_key,
        AccountType.available_credit,
        AccountType.holds,
        commit=commit,
    )
