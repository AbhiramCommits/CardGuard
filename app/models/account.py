import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, enum_values

if TYPE_CHECKING:
    from app.models.company import Company
    from app.models.ledger import LedgerEntry


class AccountType(enum.Enum):
    available_credit = "available_credit"
    holds = "holds"
    settled = "settled"


class Account(Base):
    __tablename__ = "account"
    __table_args__ = (
        UniqueConstraint("company_id", "account_type", name="uq_account_company_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("company.id", ondelete="CASCADE"), nullable=False, index=True
    )
    account_type: Mapped[AccountType] = mapped_column(
        Enum(AccountType, name="account_type", values_callable=enum_values), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    company: Mapped["Company"] = relationship(back_populates="accounts")
    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(back_populates="account")
