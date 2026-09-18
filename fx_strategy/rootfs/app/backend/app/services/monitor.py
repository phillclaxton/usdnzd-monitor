"""The post-refresh pipeline.

Runs after every rate sample: evaluate the movement alerts against the stored
position, chase provider outages, and deliver whatever that produced.

This module is deliberately the only place that decides *whether* to notify, so
the rules cannot drift apart across call sites.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.logging_setup import get_logger
from app.models.alert import AlertRuleType, Severity
from app.schemas.settings import Settings
from app.services import alert_service, fx_alerts, notifications, rate_service
from app.services.notifications import Notification
from app.services.rate_service import RefreshOutcome

log = get_logger(__name__)


@dataclass(slots=True)
class MonitorResult:
    notifications_attempted: int = 0
    notifications_delivered: int = 0
    suppressed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def run_after_refresh(
    session: AsyncSession, settings: Settings, outcome: RefreshOutcome
) -> MonitorResult:
    """Scheduler callback: react to a new rate sample."""
    result = MonitorResult()

    # Retry anything queued while Home Assistant was unavailable, first.
    try:
        await notifications.retry_queued(session, settings)
    except Exception as exc:
        result.errors.append(f"queued retry failed: {exc}")
        log.warning("queued_retry_failed", error=str(exc))

    await _check_provider_health(session, settings, outcome, result)

    current = await rate_service.current_rate(session, settings)
    await _check_position(session, settings, current, outcome, result)
    return result


async def _check_position(
    session: AsyncSession,
    settings: Settings,
    current: rate_service.CurrentRate,
    outcome: RefreshOutcome,
    result: MonitorResult,
) -> None:
    """The movement alerts.

    Delivered through the same ``_deliver`` funnel as everything else, so quiet
    hours, the retry queue and the notification log apply without fx_alerts
    knowing they exist.

    Level crossings and mortgage milestones pass ``cooldown_minutes=0``: their
    own hysteresis is a stricter rule than a timer, and a timer on top could
    only swallow a genuine second crossing.
    """
    try:
        run = await fx_alerts.evaluate(
            session,
            settings,
            current=current,
            disagreement=outcome.disagreement_exceeded,
        )
    except Exception as exc:  # pragma: no cover - defensive
        result.errors.append(f"movement alerts failed: {exc}")
        log.warning("fx_alerts_failed", error=str(exc))
        return

    for notification in run.notifications:
        cooldown = 0 if _hysteresis_governed(notification) else None
        await _deliver(session, settings, notification, result, cooldown_minutes=cooldown)
    for key, reason in run.suppressed:
        result.suppressed.append(f"{key}: {reason}")


def _hysteresis_governed(notification: Notification) -> bool:
    """Conditions whose dedup is a state change rather than a period of time."""
    return notification.rule_type in {
        AlertRuleType.FX_LEVEL_CROSSED,
        AlertRuleType.FX_MORTGAGE_MILESTONE,
    }


async def _deliver(
    session: AsyncSession,
    settings: Settings,
    notification: Notification,
    result: MonitorResult,
    *,
    cooldown_minutes: int | None = None,
) -> None:
    result.notifications_attempted += 1
    delivery = await notifications.send(
        session, notification, settings, cooldown_minutes=cooldown_minutes
    )
    if delivery.delivered:
        result.notifications_delivered += 1
    elif delivery.suppressed_reason:
        result.suppressed.append(f"{notification.rule_type}: {delivery.suppressed_reason}")


async def _check_provider_health(
    session: AsyncSession,
    settings: Settings,
    outcome: RefreshOutcome,
    result: MonitorResult,
) -> None:
    """Notify once a provider has been failing for longer than the threshold.

    A single failed poll is not worth waking someone for; a provider that has
    been down for half an hour is.

    Only a provider this cycle actually asked can be reported as failing. A
    fallback sitting behind a healthy primary is never contacted, so whatever
    its status row says, there is no outage to report — and a provider with
    nothing entered is a blank setting, not an outage at three in the morning.
    """
    threshold = timedelta(seconds=settings.providers.error_notify_after_seconds)
    now = utcnow()
    for status in await rate_service.provider_statuses(session):
        if status.healthy or status.failing_since is None:
            continue
        if status.provider not in outcome.polled or status.provider in outcome.unconfigured:
            continue
        down_for = now - status.failing_since
        if down_for < threshold:
            continue
        notification = alert_service.provider_error_notification(
            status.provider,
            status.last_error or "The provider returned an error.",
            int(down_for.total_seconds() // 60),
        )
        await _deliver(session, settings, notification, result, cooldown_minutes=6 * 60)


async def send_test_notification(
    session: AsyncSession, settings: Settings, *, services: list[str] | None = None
) -> notifications.DeliveryResult:
    """Send a message that is unmistakably a test."""
    notification = Notification(
        rule_type=AlertRuleType.TEST,
        title="FX Strategy Manager test notification",
        message=(
            "This is a test. No target has been reached and nothing has been converted.\n\n"
            "If you can read this, notifications are configured correctly."
        ),
        severity=Severity.INFO,
        entity_type="test",
        entity_id="test",
        services=services,
    )
    return await notifications.send(session, notification, settings, bypass_cooldown=True)
