"""Notification composition and delivery.

Delivery rules, all of which exist to stop the app becoming noise:

* A cooldown per rule type and entity.
* Quiet hours, which critical alerts may override.
* Home Assistant being down queues the message rather than dropping it, with a
  bounded queue so an outage cannot grow without limit.
* Every attempt is logged, successful or not.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.home_assistant.client import (
    HomeAssistantClient,
    HomeAssistantError,
    get_home_assistant,
)
from app.logging_setup import get_logger
from app.models.alert import AlertRuleType, NotificationLog, Severity
from app.models.audit import AuditEventType
from app.schemas.settings import Settings
from app.services import audit
from app.services.audit import current_correlation_id

log = get_logger(__name__)

#: The queue is bounded: an outage must not grow memory or storage without end.
MAX_QUEUED = 50
MAX_ATTEMPTS = 6

#: Alerts that ignore quiet hours when the user has allowed critical overrides.
CRITICAL_RULES = frozenset(
    {
        AlertRuleType.DEADLINE_MISSED,
        AlertRuleType.PROVIDER_ERROR,
        AlertRuleType.STRATEGY_COMPLETED,
    }
)


@dataclass(frozen=True, slots=True)
class Notification:
    """A composed message, before any delivery decision is made."""

    rule_type: AlertRuleType
    title: str
    message: str
    severity: Severity = Severity.INFO
    entity_type: str | None = None
    entity_id: str | None = None
    #: Overrides the configured services when set.
    services: list[str] | None = None
    data: dict[str, Any] | None = None

    @property
    def is_critical(self) -> bool:
        return self.severity is Severity.CRITICAL or self.rule_type in CRITICAL_RULES


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    delivered: bool
    queued: bool
    suppressed_reason: str | None
    services: list[str]
    errors: dict[str, str]

    @property
    def sent(self) -> bool:
        return self.delivered


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        log.warning("unknown_timezone", timezone=name)
        return ZoneInfo("UTC")


def _parse_hhmm(value: str, fallback: time) -> time:
    try:
        hour, _, minute = value.partition(":")
        return time(int(hour), int(minute or 0))
    except (ValueError, TypeError):
        return fallback


def in_quiet_hours(settings: Settings, moment: datetime | None = None) -> bool:
    """Whether ``moment`` falls inside the configured quiet window.

    Evaluated in the user's timezone, and handles a window that crosses
    midnight (22:00 to 07:00 is the default shape).
    """
    quiet = settings.notifications.quiet_hours
    if not quiet.enabled:
        return False
    local = (moment or utcnow()).astimezone(_zone(settings.general.timezone)).time()
    start = _parse_hhmm(quiet.start, time(22, 0))
    end = _parse_hhmm(quiet.end, time(7, 0))
    if start == end:
        return False
    if start < end:
        return start <= local < end
    return local >= start or local < end


async def _recent_notification(
    session: AsyncSession,
    rule_type: str,
    entity_type: str | None,
    entity_id: str | None,
    since: datetime,
) -> NotificationLog | None:
    stmt = (
        select(NotificationLog)
        .where(
            NotificationLog.rule_type == rule_type,
            NotificationLog.created_at >= since,
            NotificationLog.delivered.is_(True),
        )
        .order_by(NotificationLog.created_at.desc())
        .limit(1)
    )
    if entity_type is not None:
        stmt = stmt.where(NotificationLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(NotificationLog.entity_id == entity_id)
    return (await session.execute(stmt)).scalars().first()


async def in_cooldown(
    session: AsyncSession,
    notification: Notification,
    settings: Settings,
    *,
    cooldown_minutes: int | None = None,
) -> bool:
    minutes = (
        cooldown_minutes
        if cooldown_minutes is not None
        else settings.notifications.default_cooldown_minutes
    )
    if minutes <= 0:
        return False
    recent = await _recent_notification(
        session,
        str(notification.rule_type),
        notification.entity_type,
        notification.entity_id,
        utcnow() - timedelta(minutes=minutes),
    )
    return recent is not None


async def _log(
    session: AsyncSession,
    notification: Notification,
    services: list[str],
    *,
    delivered: bool,
    queued: bool = False,
    attempts: int = 0,
    error: str | None = None,
    suppressed_reason: str | None = None,
) -> NotificationLog:
    entry = NotificationLog(
        rule_type=str(notification.rule_type),
        severity=str(notification.severity),
        title=notification.title,
        message=notification.message,
        services=json.dumps(services),
        entity_type=notification.entity_type,
        entity_id=notification.entity_id,
        delivered=delivered,
        queued=queued,
        attempts=attempts,
        last_error=error,
        suppressed_reason=suppressed_reason,
        correlation_id=current_correlation_id(),
        delivered_at=utcnow() if delivered else None,
    )
    session.add(entry)
    await session.flush()
    return entry


async def send(
    session: AsyncSession,
    notification: Notification,
    settings: Settings,
    *,
    client: HomeAssistantClient | None = None,
    cooldown_minutes: int | None = None,
    bypass_cooldown: bool = False,
) -> DeliveryResult:
    """Deliver a notification, or record precisely why it was not delivered."""
    services = notification.services or settings.notifications.services

    if not settings.notifications.enabled:
        await _log(session, notification, services, delivered=False, suppressed_reason="disabled")
        return DeliveryResult(False, False, "Notifications are switched off.", services, {})

    if not services:
        await _log(
            session, notification, services, delivered=False, suppressed_reason="no_services"
        )
        return DeliveryResult(
            False, False, "No Home Assistant notify service is configured.", services, {}
        )

    quiet = in_quiet_hours(settings)
    if quiet and not (
        notification.is_critical and settings.notifications.quiet_hours.allow_critical
    ):
        await _log(
            session, notification, services, delivered=False, suppressed_reason="quiet_hours"
        )
        return DeliveryResult(False, False, "Suppressed by quiet hours.", services, {})

    if not bypass_cooldown and await in_cooldown(
        session, notification, settings, cooldown_minutes=cooldown_minutes
    ):
        await _log(session, notification, services, delivered=False, suppressed_reason="cooldown")
        return DeliveryResult(False, False, "Suppressed by cooldown.", services, {})

    home_assistant = client or get_home_assistant()
    errors: dict[str, str] = {}
    delivered_any = False
    retryable = False

    for service in services:
        try:
            await home_assistant.notify(
                service,
                title=notification.title,
                message=notification.message,
                data=notification.data,
            )
            delivered_any = True
        except HomeAssistantError as exc:
            errors[service] = exc.message
            retryable = retryable or exc.retryable
            log.warning("notification_failed", service=service, error=exc.message)

    if delivered_any:
        await _log(
            session,
            notification,
            services,
            delivered=True,
            attempts=1,
            error=json.dumps(errors) if errors else None,
        )
        await audit.record(
            session,
            event_type=AuditEventType.NOTIFIED,
            entity_type=notification.entity_type or "notification",
            entity_id=notification.entity_id,
            message=f"Notification sent: {notification.title}",
            after={"services": services, "rule_type": str(notification.rule_type)},
        )
        return DeliveryResult(True, False, None, services, errors)

    # Nothing got through. Queue it if the failure looks temporary.
    queued = retryable and await _queue_depth(session) < MAX_QUEUED
    await _log(
        session,
        notification,
        services,
        delivered=False,
        queued=queued,
        attempts=1,
        error=json.dumps(errors),
        suppressed_reason=None if queued else "delivery_failed",
    )
    return DeliveryResult(
        False,
        queued,
        "Queued for retry." if queued else "Delivery failed.",
        services,
        errors,
    )


async def _queue_depth(session: AsyncSession) -> int:
    from sqlalchemy import func

    stmt = select(func.count()).select_from(NotificationLog).where(NotificationLog.queued.is_(True))
    return int((await session.execute(stmt)).scalar_one())


async def retry_queued(
    session: AsyncSession,
    settings: Settings,
    *,
    client: HomeAssistantClient | None = None,
) -> int:
    """Re-attempt queued notifications. Returns how many were delivered.

    A message that has failed ``MAX_ATTEMPTS`` times is dequeued and left in the
    log as an undelivered record rather than retried for ever.
    """
    stmt = (
        select(NotificationLog)
        .where(NotificationLog.queued.is_(True))
        .order_by(NotificationLog.created_at.asc())
        .limit(MAX_QUEUED)
    )
    pending = list((await session.execute(stmt)).scalars().all())
    if not pending:
        return 0

    home_assistant = client or get_home_assistant()
    delivered = 0
    for entry in pending:
        services = json.loads(entry.services or "[]")
        entry.attempts += 1
        succeeded = False
        errors: dict[str, str] = {}
        for service in services:
            try:
                await home_assistant.notify(service, title=entry.title, message=entry.message)
                succeeded = True
            except HomeAssistantError as exc:
                errors[service] = exc.message
        if succeeded:
            entry.delivered = True
            entry.queued = False
            entry.delivered_at = utcnow()
            entry.last_error = None
            delivered += 1
        else:
            entry.last_error = json.dumps(errors)
            if entry.attempts >= MAX_ATTEMPTS:
                entry.queued = False
                entry.suppressed_reason = "gave_up_after_retries"
                log.error(
                    "notification_abandoned", rule_type=entry.rule_type, attempts=entry.attempts
                )
    await session.flush()
    if delivered:
        log.info("queued_notifications_delivered", count=delivered)
    return delivered


async def recent_log(session: AsyncSession, limit: int = 50) -> list[NotificationLog]:
    stmt = (
        select(NotificationLog)
        .order_by(NotificationLog.created_at.desc(), NotificationLog.id.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())
