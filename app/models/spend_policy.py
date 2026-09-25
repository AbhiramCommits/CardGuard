from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.employee import Employee


class SpendPolicy(Base):
    __tablename__ = "spend_policy"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("employee.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    monthly_limit_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    per_transaction_limit_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    blocked_mccs: Mapped[list[str]] = mapped_column(
        ARRAY(String(4)), nullable=False, default=list
    )
    velocity_max_auths: Mapped[int] = mapped_column(Integer, nullable=False)
    velocity_window_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    employee: Mapped["Employee"] = relationship(back_populates="spend_policy")
