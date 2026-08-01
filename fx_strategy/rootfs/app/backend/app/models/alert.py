"""Alert rules, per-target state and the notification log."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, RateText, UTCDateTime, utcnow


class AlertRuleType(StrEnum):
    TARGET_REACHED = "target_reached"
    TARGET_NEAR = "target_near"
    RATE_ABOVE = "rate_above"
    RATE_BELOW = "rate_below"
    DAILY_CHANGE = "daily_change"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_DISAGREEMENT = "provider_disagreement"
    RATE_STALE = "rate_stale"
    DEADLINE_APPROACHING = "deadline_approaching"
    DEADLINE_MISSED = "deadline_missed"
    FUNDS_ARRIVED = "funds_arrived"
    BALANCE_CHANGED = "balance_changed"
    CONVERSION_DETECTED = "conversion_detected"
    STRATEGY_COMPLETED = "strategy_completed"
    WALK_AWAY_REACHED = "walk_away_reached"
    RATE_REVERSAL = "rate_reversal"


class Severity(StrEnum):
    INFO = "info"
    NOTICE = "notice"
    WARNING = "warning"
    CRITICAL = "critical"


class TargetState(StrEnum):
    """The per-target alert state machine from the product specification."""

    BELOW = "below"
    NEAR = "near"
    REACHED_UNCONFIRMED = "reached_unconfirmed"
    REACHED_CONFIRMED = "reached_confirmed"
    NOTIFIED = "notified"
    ACKNOWLEDGED = "acknowledged"
    COMPLETED = "completed"
    RESET = "reset"


class AlertRule(Base):
    __tablename__ = "alert_rules"
    __table_args__ = (Index("ix_alert_rules_strategy_type", "strategy_id", "rule_type"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int | None] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    threshold: Mapped[Decimal | None] = mapped_column(RateText())
    #: ``above``, ``below``, ``crosses_up``, ``crosses_down``
    comparison: Mapped[str] = mapped_column(String(16), nullable=False, default="above")
    cooldown_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    #: JSON list of notify services; empty means "use the configured default".
    notification_targets: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default=str(Severity.INFO))
    metadata_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class TrancheAlertState(Base):
    """Alert state for one tranche's target.

    Kept separate from :class:`~app.models.strategy.Tranche` so that the alert
    machinery can evolve without touching the financial record, and so a state
    reset never rewrites a tranche's conversion history.
    """

    __tablename__ = "tranche_alert_states"

    tranche_id: Mapped[int] = mapped_column(
        ForeignKey("tranches.id", ondelete="CASCADE"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False, default=str(TargetState.BELOW))
    #: Consecutive samples at or above the target that qualified for confirmation.
    qualifying_samples: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_qualifying_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_sample_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_sample_rate: Mapped[Decimal | None] = mapped_column(RateText())
    last_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    notification_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    near_notified_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    reset_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )


class NotificationLog(Base):
    """Every notification the app produced, delivered or not.

    Failures are recorded as rows with ``delivered`` false and the error text,
    so a silent delivery failure is impossible to miss on the diagnostics page.
    """

    __tablename__ = "notification_log"
    __table_args__ = (Index("ix_notification_log_created", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    rule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default=str(Severity.INFO))
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    services: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    entity_type: Mapped[str | None] = mapped_column(String(32))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    delivered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Held for retry while Home Assistant is unavailable.
    queued: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    suppressed_reason: Mapped[str | None] = mapped_column(String(120))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
