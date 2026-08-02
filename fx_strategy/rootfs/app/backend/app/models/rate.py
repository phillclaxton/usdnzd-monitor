"""Rate observations, aggregates and provider health."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, MoneyText, RateText, UTCDateTime, utcnow


class RateSample(Base):
    """One observed exchange rate.

    ``rate`` is the authoritative Decimal value.  ``rate_numeric`` holds the same
    number as a float purely so SQL ``MIN``/``MAX``/``AVG`` over a year of
    samples stays fast; it is never read for a displayed figure.
    """

    __tablename__ = "rate_samples"
    __table_args__ = (
        Index("ix_rate_samples_pair_time", "source_currency", "target_currency", "retrieved_at"),
        Index("ix_rate_samples_provider_time", "provider", "retrieved_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    target_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(RateText(), nullable=False)
    rate_numeric: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    quote_type: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_timestamp: Mapped[datetime | None] = mapped_column(UTCDateTime)
    retrieved_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    is_stale: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    raw_reference: Mapped[str | None] = mapped_column(String(128))
    metadata_json: Mapped[str | None] = mapped_column(Text)


class RateAggregate(Base):
    """Hourly and daily rollups, so long-range charts survive data retention."""

    __tablename__ = "rate_aggregates"
    __table_args__ = (
        Index(
            "ix_rate_aggregates_bucket",
            "source_currency",
            "target_currency",
            "bucket",
            "bucket_start",
            unique=True,
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    target_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    #: "hour" or "day".
    bucket: Mapped[str] = mapped_column(String(8), nullable=False)
    bucket_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    open_rate: Mapped[Decimal] = mapped_column(RateText(), nullable=False)
    high_rate: Mapped[Decimal] = mapped_column(RateText(), nullable=False)
    low_rate: Mapped[Decimal] = mapped_column(RateText(), nullable=False)
    close_rate: Mapped[Decimal] = mapped_column(RateText(), nullable=False)
    average_rate: Mapped[Decimal] = mapped_column(RateText(), nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)


class ProviderStatus(Base):
    """Rolling health for each provider, used by diagnostics and backoff."""

    __tablename__ = "provider_status"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    healthy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_success_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_failure_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: First failure of the current outage; drives "notify after N seconds down".
    failing_since: Mapped[datetime | None] = mapped_column(UTCDateTime)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer)
    #: Earliest time the scheduler should try this provider again.
    retry_after: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class ManualRate(Base):
    """The latest manually entered rate for a pair.

    Kept separate from ``rate_samples`` so that re-reading "what did the user
    type" does not require scanning the sample history.
    """

    __tablename__ = "manual_rates"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    target_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(RateText(), nullable=False)
    entered_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class FeeModel(Base):
    """How fees are estimated when no live quote is available."""

    __tablename__ = "fee_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    #: percentage | fixed_plus_percentage | quote_only | manual
    fee_type: Mapped[str] = mapped_column(String(32), nullable=False)
    fixed_fee: Mapped[Decimal] = mapped_column(MoneyText(), nullable=False, default=Decimal(0))
    percentage_fee: Mapped[Decimal] = mapped_column(MoneyText(), nullable=False, default=Decimal(0))
    minimum_fee: Mapped[Decimal | None] = mapped_column(MoneyText())
    maximum_fee: Mapped[Decimal | None] = mapped_column(MoneyText())
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="wise")
    effective_from: Mapped[datetime | None] = mapped_column(UTCDateTime)
    effective_to: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )
