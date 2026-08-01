"""Strategies, tranches, dated requirements and conversions."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base, MoneyText, RateText, UTCDateTime, utcnow


class StrategyStatus(StrEnum):
    DRAFT = "draft"
    WAITING_FOR_FUNDS = "waiting_for_funds"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


class TrancheStatus(StrEnum):
    PENDING = "pending"
    ARMED = "armed"
    TARGET_REACHED = "target_reached"
    PARTIALLY_COMPLETED = "partially_completed"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class RecordSource(StrEnum):
    MANUAL = "manual"
    WISE_API = "wise_api"
    CSV_IMPORT = "csv_import"
    SIMULATION = "simulation"


#: Statuses in which the app watches rates against this strategy's targets.
MONITORED_STATUSES = frozenset({StrategyStatus.ACTIVE, StrategyStatus.WAITING_FOR_FUNDS})


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=str(StrategyStatus.DRAFT), index=True
    )
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    target_currency: Mapped[str] = mapped_column(String(3), nullable=False)

    #: The full amount the strategy plans for.
    initial_source_amount: Mapped[Decimal] = mapped_column(MoneyText(), nullable=False)
    #: How much has actually arrived and can be converted today.
    funds_available_amount: Mapped[Decimal] = mapped_column(
        MoneyText(), nullable=False, default=Decimal(0)
    )

    funds_arrival_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    strategy_start_date: Mapped[datetime | None] = mapped_column(UTCDateTime)
    final_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime)

    minimum_acceptable_rate: Mapped[Decimal | None] = mapped_column(RateText())
    walk_away_rate: Mapped[Decimal | None] = mapped_column(RateText())
    #: When set, targets must be reached in sequence order.
    require_targets_in_order: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    fee_model_id: Mapped[int | None] = mapped_column(ForeignKey("fee_models.id"))
    rate_provider_id: Mapped[str | None] = mapped_column(String(32))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Pacific/Auckland")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    tranches: Mapped[list[Tranche]] = relationship(
        back_populates="strategy",
        cascade="all, delete-orphan",
        order_by="Tranche.sequence",
        lazy="selectin",
    )
    requirements: Mapped[list[DeadlineRequirement]] = relationship(
        back_populates="strategy",
        cascade="all, delete-orphan",
        order_by="DeadlineRequirement.due_date",
        lazy="selectin",
    )
    conversions: Mapped[list[Conversion]] = relationship(
        back_populates="strategy",
        cascade="all, delete-orphan",
        order_by="Conversion.executed_at",
        lazy="selectin",
    )

    @property
    def is_monitored(self) -> bool:
        return self.status in MONITORED_STATUSES


class Tranche(Base):
    __tablename__ = "tranches"
    __table_args__ = (Index("ix_tranches_strategy_sequence", "strategy_id", "sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False, default="")

    #: percentage | fixed_amount | remainder
    allocation_type: Mapped[str] = mapped_column(String(16), nullable=False)
    allocation_value: Mapped[Decimal] = mapped_column(
        MoneyText(), nullable=False, default=Decimal(0)
    )
    #: Derived from the allocation rule; recalculated whenever either changes.
    calculated_source_amount: Mapped[Decimal] = mapped_column(
        MoneyText(), nullable=False, default=Decimal(0)
    )

    target_rate: Mapped[Decimal] = mapped_column(RateText(), nullable=False)
    minimum_rate: Mapped[Decimal | None] = mapped_column(RateText())
    deadline: Mapped[datetime | None] = mapped_column(UTCDateTime)

    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default=str(TrancheStatus.PENDING)
    )
    #: False when the target is informational rather than intended for a Wise
    #: Auto Conversion; purely descriptive, since nothing is executed either way.
    intended_for_auto_conversion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    wise_auto_conversion_reference: Mapped[str | None] = mapped_column(String(128))

    target_first_reached_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notification_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    strategy: Mapped[Strategy] = relationship(back_populates="tranches")

    @property
    def is_open(self) -> bool:
        """Whether this tranche still has money waiting behind its target."""
        return self.status not in (
            str(TrancheStatus.COMPLETED),
            str(TrancheStatus.SKIPPED),
            str(TrancheStatus.CANCELLED),
        )


class DeadlineRequirement(Base):
    """An amount that must be converted by a given date."""

    __tablename__ = "deadline_requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    due_date: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    #: Either an amount or a percentage of the strategy total.
    required_source_amount: Mapped[Decimal | None] = mapped_column(MoneyText())
    required_percentage: Mapped[Decimal | None] = mapped_column(MoneyText())
    description: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)

    strategy: Mapped[Strategy] = relationship(back_populates="requirements")


class Conversion(Base):
    """A conversion that actually happened.

    Records are corrected by superseding them, never by silently rewriting
    history: every change writes an audit event carrying the previous values.
    """

    __tablename__ = "conversions"
    __table_args__ = (
        Index("ix_conversions_strategy_executed", "strategy_id", "executed_at"),
        Index("ix_conversions_provider_txn", "provider", "provider_transaction_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(
        ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    tranche_id: Mapped[int | None] = mapped_column(ForeignKey("tranches.id", ondelete="SET NULL"))

    source_amount: Mapped[Decimal] = mapped_column(MoneyText(), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(MoneyText(), nullable=False)
    gross_rate: Mapped[Decimal] = mapped_column(RateText(), nullable=False)
    effective_rate: Mapped[Decimal] = mapped_column(RateText(), nullable=False)

    fee_source_currency: Mapped[Decimal | None] = mapped_column(MoneyText())
    fee_target_currency: Mapped[Decimal | None] = mapped_column(MoneyText())
    fee_total_target_equivalent: Mapped[Decimal | None] = mapped_column(MoneyText())

    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="wise")
    provider_transaction_id: Mapped[str | None] = mapped_column(String(128))
    executed_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    #: manual | wise_api | csv_import | simulation
    record_source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=str(RecordSource.MANUAL)
    )
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    receipt_filename: Mapped[str | None] = mapped_column(String(200))

    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    strategy: Mapped[Strategy] = relationship(back_populates="conversions")
