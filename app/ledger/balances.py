from sqlalchemy import BigInteger, case, cast, func, select

from app.models import Direction, LedgerEntry


def _totals(session, *filters):
    debit_expr = func.coalesce(
        cast(
            func.sum(
                case((LedgerEntry.direction == Direction.debit, LedgerEntry.amount_cents), else_=0)
            ),
            BigInteger,
        ),
        0,
    )
    credit_expr = func.coalesce(
        cast(
            func.sum(
                case((LedgerEntry.direction == Direction.credit, LedgerEntry.amount_cents), else_=0)
            ),
            BigInteger,
        ),
        0,
    )
    row = session.execute(select(debit_expr, credit_expr).where(*filters)).one()
    return int(row[0]), int(row[1])


def account_balance(session, account_id):
    debits, credits = _totals(session, LedgerEntry.account_id == account_id)
    return debits - credits


def authorization_net(session, authorization_id):
    debits, credits = _totals(session, LedgerEntry.authorization_id == authorization_id)
    return debits - credits
