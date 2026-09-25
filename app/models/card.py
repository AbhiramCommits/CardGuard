import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.authorization import Authorization
    from app.models.employee import Employee


class Card(Base):
    __tablename__ = "card"
    __table_args__ = (Index("ix_card_token", "token", unique=True),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employee.id", ondelete="CASCADE"), nullable=False, index=True
    )
    last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    token: Mapped[str] = mapped_column(
        String(64), nullable=False, default=lambda: f"card_{uuid.uuid4().hex}"
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    employee: Mapped["Employee"] = relationship(back_populates="cards")
    authorizations: Mapped[list["Authorization"]] = relationship(back_populates="card")
