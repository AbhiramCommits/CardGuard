import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, enum_values

if TYPE_CHECKING:
    from app.models.card import Card
    from app.models.ledger import LedgerEntry, LedgerPosting


class AuthorizationStatus(enum.Enum):
    approved = "approved"
    declined = "declined"
    pending_review = "pending_review"
    expired = "expired"
    captured = "captured"
    reversed = "reversed"


class Authorization(Base):
    __tablename__ = "authorization"
    __table_args__ = (
        CheckConstraint("amount_cents > 0", name="ck_authorization_amount_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    card_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("card.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    merchant_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mcc: Mapped[str] = mapped_column(String(4), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[AuthorizationStatus] = mapped_column(
        Enum(AuthorizationStatus, name="authorization_status", values_callable=enum_values),
        nullable=False,
        default=AuthorizationStatus.pending_review,
    )
    decision_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    card: Mapped["Card"] = relationship(back_populates="authorizations")
    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(back_populates="authorization")
    postings: Mapped[list["LedgerPosting"]] = relationship(back_populates="authorization")
