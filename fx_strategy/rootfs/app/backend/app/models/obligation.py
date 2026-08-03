"""NZD obligations that may be funded by converting USD.

Deliberately independent of strategies and tranches. An obligation is a debt or
commitment the user holds; the strategy is how they intend to buy the currency.
The two are compared, not merged.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, MoneyText, RateText, UTCDateTime, utcnow


class Obligation(Base):
    __tablename__ = "obligations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    #: One of ObligationType. Affects wording only.
    obligation_type: Mapped[str] = mapped_column(String(32), nullable=False, default="other")

    total_nzd: Mapped[Decimal] = mapped_column(MoneyText(), nullable=False, default=Decimal(0))
    amount_funded_nzd: Mapped[Decimal] = mapped_column(
        MoneyText(), nullable=False, default=Decimal(0)
    )
    #: Set only when the user overrides ``total - funded``.
    remaining_override_nzd: Mapped[Decimal | None] = mapped_column(MoneyText())

    #: A fraction, not a percentage: 6.04% is stored as 0.0604.
    annual_rate: Mapped[Decimal] = mapped_column(RateText(), nullable=False, default=Decimal(0))
    #: simple_annual | daily_manual | none
    interest_basis: Mapped[str] = mapped_column(String(16), nullable=False, default="simple_annual")
    daily_rate: Mapped[Decimal | None] = mapped_column(RateText())

    due_date: Mapped[date | None] = mapped_column(Date)
    earliest_payment_date: Mapped[date | None] = mapped_column(Date)

    #: critical | high | normal | low
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="normal", index=True)
    #: none | moderate | high — non-financial importance.
    relationship_importance: Mapped[str] = mapped_column(String(16), nullable=False, default="none")

    minimum_payment_nzd: Mapped[Decimal | None] = mapped_column(MoneyText())
    partial_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    target_rate: Mapped[Decimal | None] = mapped_column(RateText())
    max_wait_days: Mapped[int | None] = mapped_column(Integer)

    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    completed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class ObligationFunding(Base):
    """A record of NZD applied to an obligation.

    Kept as its own table rather than only mutating ``amount_funded_nzd`` so
    partial funding has a history that cannot be lost to an edit.
    """

    __tablename__ = "obligation_fundings"

    id: Mapped[int] = mapped_column(primary_key=True)
    obligation_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    amount_nzd: Mapped[Decimal] = mapped_column(MoneyText(), nullable=False)
    #: Optional link to the conversion that produced the NZD.
    conversion_id: Mapped[int | None] = mapped_column(Integer)
    funded_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
