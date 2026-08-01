"""Append-only audit trail.

Audit rows are never updated or deleted by application code.  Corrections are
recorded as new events so the history of a financial record stays intact.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base, UTCDateTime, utcnow


class AuditEventType(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"
    ACTIVATED = "activated"
    PAUSED = "paused"
    RESUMED = "resumed"
    COMPLETED = "completed"
    IMPORTED = "imported"
    EXPORTED = "exported"
    NOTIFIED = "notified"
    ACKNOWLEDGED = "acknowledged"
    TARGET_REACHED = "target_reached"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_RECOVERED = "provider_recovered"
    CREDENTIAL_CHANGED = "credential_changed"
    RECONCILED = "reconciled"
    RESTORED = "restored"
    PURGED = "purged"
    SIMULATION = "simulation"


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_entity", "entity_type", "entity_id"),
        Index("ix_audit_events_timestamp", "timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, default=utcnow)
    before_json: Mapped[str | None] = mapped_column(Text)
    after_json: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    correlation_id: Mapped[str | None] = mapped_column(String(64))
