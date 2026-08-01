"""Audit event recording.

The audit trail is append-only.  ``record`` never overwrites an existing row and
there is deliberately no update or delete helper in this module.
"""

from __future__ import annotations

import json
import uuid
from contextvars import ContextVar
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_setup import REDACTED, SENSITIVE_KEYS
from app.models.audit import AuditEvent, AuditEventType

#: Correlates every event produced while handling one request or job run.
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")


def new_correlation_id() -> str:
    value = uuid.uuid4().hex[:16]
    correlation_id.set(value)
    return value


def current_correlation_id() -> str:
    value = correlation_id.get()
    return value or new_correlation_id()


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def sanitize(payload: Any) -> Any:
    """Strip secret-looking keys before a payload reaches the audit trail."""
    if isinstance(payload, dict):
        return {
            key: (REDACTED if key.lower() in SENSITIVE_KEYS else sanitize(value))
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [sanitize(item) for item in payload]
    return payload


def _dump(payload: Any) -> str | None:
    if payload is None:
        return None
    return json.dumps(sanitize(payload), default=_json_default, sort_keys=True)


async def record(
    session: AsyncSession,
    *,
    event_type: AuditEventType | str,
    entity_type: str,
    entity_id: str | int | None = None,
    message: str = "",
    before: Any = None,
    after: Any = None,
    actor: str = "system",
) -> AuditEvent:
    """Append an audit event to the current transaction."""
    event = AuditEvent(
        event_type=str(event_type),
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        actor=actor,
        message=message,
        before_json=_dump(before),
        after_json=_dump(after),
        correlation_id=current_correlation_id(),
    )
    session.add(event)
    # Sessions run with autoflush off, so the event is flushed explicitly to
    # make it visible to reads later in the same transaction.
    await session.flush()
    return event


async def list_events(
    session: AsyncSession,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditEvent]:
    stmt = select(AuditEvent).order_by(AuditEvent.timestamp.desc(), AuditEvent.id.desc())
    if entity_type:
        stmt = stmt.where(AuditEvent.entity_type == entity_type)
    if entity_id:
        stmt = stmt.where(AuditEvent.entity_id == str(entity_id))
    if event_type:
        stmt = stmt.where(AuditEvent.event_type == event_type)
    stmt = stmt.limit(min(limit, 1000)).offset(max(offset, 0))
    return list((await session.execute(stmt)).scalars().all())
