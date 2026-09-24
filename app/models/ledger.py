import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, enum_values

if TYPE_CHECKING:
    from app.models.account import Account
    from app.models.authorization import Authorization


class Direction(enum.Enum):
    debit = "debit"
    credit = "credit"


class EntryType(enum.Enum):
    hold = "hold"
    capture = "capture"
    reversal = "reversal"
    hold_release = "hold_release"


class LedgerPosting(Base):
    __tablename__ = "ledger_posting"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    authorization_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("authorization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    entry_type: Mapped[EntryType] = mapped_column(
        Enum(EntryType, name="entry_type", values_callable=enum_values), nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    authorization: Mapped["Authorization"] = relationship(back_populates="postings")
    entries: Mapped[list["LedgerEntry"]] = relationship(back_populates="posting")


class LedgerEntry(Base):
    __tablename__ = "ledger_entry"
    __table_args__ = (
        UniqueConstraint(
            "authorization_id",
            "entry_type",
            "account_id",
            "direction",
            name="uq_ledger_entry_posting_group",
        ),
        CheckConstraint("amount_cents > 0", name="ck_ledger_entry_amount_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    authorization_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("authorization.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("account.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    direction: Mapped[Direction] = mapped_column(
        Enum(Direction, name="direction", values_callable=enum_values), nullable=False
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    entry_type: Mapped[EntryType] = mapped_column(
        Enum(EntryType, name="entry_type", values_callable=enum_values), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    posting_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("ledger_posting.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    authorization: Mapped["Authorization"] = relationship(back_populates="ledger_entries")
    account: Mapped["Account"] = relationship(back_populates="ledger_entries")
    posting: Mapped["LedgerPosting"] = relationship(back_populates="entries")
