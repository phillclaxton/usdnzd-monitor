"""Notification delivery: cooldown, quiet hours, and the retry queue.

These are the rules that decide whether a composed message actually reaches a
phone, and they are the same whichever rule raised it. What a given rule says,
and when it says it, is pinned in ``test_fx_alerts.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import AlertRuleType, NotificationLog, Severity
from app.schemas.settings import Settings
from app.services import notifications, settings_service
from app.services.notifications import Notification, in_quiet_hours
from app.tests.helpers import FakeHomeAssistant


@pytest.fixture
async def settings(session: AsyncSession) -> Settings:
    loaded = await settings_service.load_settings(session)
    loaded.notifications.services = ["notify.test"]
    return loaded


# ---------------------------------------------------------------------------
# Delivery: cooldown, quiet hours, failure handling
# ---------------------------------------------------------------------------


def notification() -> Notification:
    return Notification(
        rule_type=AlertRuleType.FX_ABSOLUTE_MOVE,
        title="USD/NZD is up half a cent",
        message="body",
        entity_type="fx_alert",
        entity_id="absolute_move",
    )


async def test_delivery_calls_every_configured_service(
    session: AsyncSession, settings: Settings
) -> None:
    settings.notifications.services = ["notify.one", "notify.two"]
    fake = FakeHomeAssistant()
    result = await notifications.send(session, notification(), settings, client=fake)  # type: ignore[arg-type]
    assert result.delivered
    assert [call["service"] for call in fake.calls] == ["notify.one", "notify.two"]


async def test_a_second_identical_alert_is_suppressed_by_cooldown(
    session: AsyncSession, settings: Settings
) -> None:
    fake = FakeHomeAssistant()
    await notifications.send(session, notification(), settings, client=fake)  # type: ignore[arg-type]
    second = await notifications.send(session, notification(), settings, client=fake)  # type: ignore[arg-type]
    assert second.delivered is False
    assert second.suppressed_reason == "Suppressed by cooldown."
    assert len(fake.calls) == 1


async def test_a_different_entity_is_not_caught_by_the_cooldown(
    session: AsyncSession, settings: Settings
) -> None:
    fake = FakeHomeAssistant()
    await notifications.send(session, notification(), settings, client=fake)  # type: ignore[arg-type]
    other = Notification(
        rule_type=AlertRuleType.FX_ABSOLUTE_MOVE,
        title="A different condition",
        message="body",
        entity_type="fx_alert",
        entity_id="intraday_percent",
    )
    result = await notifications.send(session, other, settings, client=fake)  # type: ignore[arg-type]
    assert result.delivered is True


async def test_a_test_notification_bypasses_the_cooldown(
    session: AsyncSession, settings: Settings
) -> None:
    fake = FakeHomeAssistant()
    await notifications.send(session, notification(), settings, client=fake)  # type: ignore[arg-type]
    result = await notifications.send(
        session,
        notification(),
        settings,
        client=fake,
        bypass_cooldown=True,  # type: ignore[arg-type]
    )
    assert result.delivered is True


async def test_notifications_can_be_switched_off_entirely(
    session: AsyncSession, settings: Settings
) -> None:
    settings.notifications.enabled = False
    fake = FakeHomeAssistant()
    result = await notifications.send(session, notification(), settings, client=fake)  # type: ignore[arg-type]
    assert result.delivered is False
    assert fake.calls == []


async def test_no_configured_service_is_reported_plainly(
    session: AsyncSession, settings: Settings
) -> None:
    settings.notifications.services = []
    result = await notifications.send(session, notification(), settings, client=FakeHomeAssistant())  # type: ignore[arg-type]
    assert result.delivered is False
    assert "No Home Assistant notify service" in (result.suppressed_reason or "")


@pytest.mark.parametrize(
    ("hour", "expected"),
    [(23, True), (2, True), (6, True), (7, False), (12, False), (21, False), (22, True)],
)
async def test_quiet_hours_window_crosses_midnight(
    session: AsyncSession, settings: Settings, hour: int, expected: bool
) -> None:
    settings.notifications.quiet_hours.enabled = True
    settings.notifications.quiet_hours.start = "22:00"
    settings.notifications.quiet_hours.end = "07:00"
    settings.general.timezone = "UTC"
    moment = datetime(2026, 8, 1, hour, 0, tzinfo=UTC)
    assert in_quiet_hours(settings, moment) is expected


async def test_quiet_hours_suppress_an_ordinary_alert(
    session: AsyncSession, settings: Settings
) -> None:
    settings.notifications.quiet_hours.enabled = True
    settings.notifications.quiet_hours.start = "00:00"
    settings.notifications.quiet_hours.end = "23:59"
    fake = FakeHomeAssistant()
    result = await notifications.send(session, notification(), settings, client=fake)  # type: ignore[arg-type]
    assert result.delivered is False
    assert result.suppressed_reason == "Suppressed by quiet hours."


async def test_a_critical_alert_overrides_quiet_hours(
    session: AsyncSession, settings: Settings
) -> None:
    settings.notifications.quiet_hours.enabled = True
    settings.notifications.quiet_hours.start = "00:00"
    settings.notifications.quiet_hours.end = "23:59"
    settings.notifications.quiet_hours.allow_critical = True
    fake = FakeHomeAssistant()
    critical = Notification(
        rule_type=AlertRuleType.PROVIDER_ERROR,
        title="Rate provider wise has been failing for 45 minutes",
        message="body",
        severity=Severity.CRITICAL,
    )
    result = await notifications.send(session, critical, settings, client=fake)  # type: ignore[arg-type]
    assert result.delivered is True


async def test_a_failed_delivery_is_queued_not_dropped(
    session: AsyncSession, settings: Settings
) -> None:
    failing = FakeHomeAssistant(fail="Home Assistant could not be reached")
    result = await notifications.send(session, notification(), settings, client=failing)  # type: ignore[arg-type]
    assert result.delivered is False
    assert result.queued is True

    working = FakeHomeAssistant()
    delivered = await notifications.retry_queued(session, settings, client=working)  # type: ignore[arg-type]
    assert delivered == 1
    assert len(working.calls) == 1

    # Nothing is left queued after a successful retry.
    assert await notifications.retry_queued(session, settings, client=working) == 0  # type: ignore[arg-type]


async def test_a_permanently_rejected_delivery_is_not_queued(
    session: AsyncSession, settings: Settings
) -> None:
    failing = FakeHomeAssistant(fail="token rejected", retryable=False)
    result = await notifications.send(session, notification(), settings, client=failing)  # type: ignore[arg-type]
    assert result.queued is False
    assert result.suppressed_reason == "Delivery failed."


async def test_the_retry_queue_gives_up_eventually(
    session: AsyncSession, settings: Settings
) -> None:
    failing = FakeHomeAssistant(fail="still down")
    await notifications.send(session, notification(), settings, client=failing)  # type: ignore[arg-type]
    for _ in range(notifications.MAX_ATTEMPTS + 1):
        await notifications.retry_queued(session, settings, client=failing)  # type: ignore[arg-type]

    log = await notifications.recent_log(session)
    entry = log[0]
    assert entry.queued is False
    assert entry.delivered is False
    assert entry.suppressed_reason == "gave_up_after_retries"


async def test_every_attempt_is_logged_including_failures(
    session: AsyncSession, settings: Settings
) -> None:
    await notifications.send(
        session,
        notification(),
        settings,
        client=FakeHomeAssistant(fail="down"),  # type: ignore[arg-type]
    )
    log = await notifications.recent_log(session)
    assert len(log) == 1
    assert log[0].delivered is False
    assert log[0].last_error


async def test_the_queue_is_bounded(session: AsyncSession, settings: Settings) -> None:
    failing = FakeHomeAssistant(fail="down")
    for index in range(notifications.MAX_QUEUED + 5):
        await notifications.send(
            session,
            Notification(
                rule_type=AlertRuleType.FX_NEW_HIGH,
                title=f"alert {index}",
                message="body",
                entity_type="test",
                entity_id=str(index),
            ),
            settings,
            client=failing,  # type: ignore[arg-type]
        )
    from sqlalchemy import func, select

    queued = (
        await session.execute(
            select(func.count())
            .select_from(NotificationLog)
            .where(NotificationLog.queued.is_(True))
        )
    ).scalar_one()
    assert queued <= notifications.MAX_QUEUED
