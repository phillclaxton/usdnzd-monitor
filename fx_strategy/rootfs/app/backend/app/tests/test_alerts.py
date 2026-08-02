"""Target state machine, cooldown, hysteresis and quiet-hours tests.

These are the rules that stop the app either missing a target or turning into a
stream of duplicate alerts, so they are pinned in detail.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.home_assistant.client import HomeAssistantError
from app.models.alert import AlertRuleType, NotificationLog, Severity, TargetState
from app.models.strategy import Strategy, TrancheStatus
from app.schemas.settings import Settings
from app.schemas.strategy import StrategyIn, TrancheIn
from app.services import alert_service, notifications, settings_service
from app.services import strategy_service as strategies
from app.services.notifications import Notification, in_quiet_hours


class FakeHomeAssistant:
    """Records notify calls, and can be made to fail on demand."""

    def __init__(self, *, fail: str | None = None, retryable: bool = True) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail = fail
        self.retryable = retryable
        self.configured = True

    async def notify(self, service: str, *, title: str, message: str, data: Any = None) -> None:
        if self.fail:
            raise HomeAssistantError(self.fail, retryable=self.retryable)
        self.calls.append({"service": service, "title": title, "message": message, "data": data})

    async def aclose(self) -> None:
        return None


@pytest.fixture
async def settings(session: AsyncSession) -> Settings:
    loaded = await settings_service.load_settings(session)
    loaded.notifications.services = ["notify.test"]
    return loaded


@pytest.fixture
async def strategy(session: AsyncSession) -> Strategy:
    payload = StrategyIn(
        name="Test",
        initial_source_amount=Decimal("800000"),
        funds_available_amount=Decimal("800000"),
        walk_away_rate=Decimal("1.7800"),
        tranches=[
            TrancheIn(
                sequence=1,
                allocation_type="percentage",
                allocation_value=Decimal("50"),
                target_rate=Decimal("1.7600"),
            ),
            TrancheIn(
                sequence=2,
                allocation_type="percentage",
                allocation_value=Decimal("50"),
                target_rate=Decimal("1.8000"),
            ),
        ],
    )
    created = await strategies.create_strategy(session, payload)
    await strategies.activate(session, created)
    await session.flush()
    return created


async def evaluate(
    session: AsyncSession,
    strategy: Strategy,
    settings: Settings,
    rate: str,
    *,
    at: datetime | None = None,
    stale: bool = False,
    disagreement: bool = False,
) -> alert_service.AlertOutcome:
    return await alert_service.evaluate_targets(
        session,
        strategy,
        settings,
        rate=Decimal(rate),
        rate_is_stale=stale,
        sample_at=at or utcnow(),
        provider_disagreement=disagreement,
    )


def state_of(outcome: alert_service.AlertOutcome, tranche_id: int) -> TargetState:
    return next(e.state for e in outcome.evaluations if e.tranche_id == tranche_id)


def reason_for(outcome: alert_service.AlertOutcome, tranche_id: int) -> str:
    return next(e.reason for e in outcome.evaluations if e.tranche_id == tranche_id)


# ---------------------------------------------------------------------------
# The confirmation sequence
# ---------------------------------------------------------------------------


async def test_one_sample_at_the_target_is_not_yet_confirmed(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    first = strategy.tranches[0]
    outcome = await evaluate(session, strategy, settings, "1.7601")

    assert state_of(outcome, first.id) is TargetState.REACHED_UNCONFIRMED
    assert outcome.notifications == []
    assert "awaiting confirmation" in reason_for(outcome, first.id)
    # The tranche records that its target was seen, but is not completed.
    assert first.target_first_reached_at is not None
    assert first.status == str(TrancheStatus.TARGET_REACHED)
    assert first.completed_at is None


async def test_two_qualifying_samples_confirm_and_notify(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    first = strategy.tranches[0]
    start = utcnow()
    await evaluate(session, strategy, settings, "1.7601", at=start)
    outcome = await evaluate(
        session, strategy, settings, "1.7604", at=start + timedelta(seconds=31)
    )

    assert state_of(outcome, first.id) is TargetState.NOTIFIED
    assert len(outcome.notifications) == 1
    notification = outcome.notifications[0]
    assert notification.rule_type is AlertRuleType.TARGET_REACHED
    assert "1.7600" in notification.title
    assert "has not converted anything" in notification.message


async def test_samples_too_close_together_do_not_confirm(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    first = strategy.tranches[0]
    start = utcnow()
    await evaluate(session, strategy, settings, "1.7601", at=start)
    outcome = await evaluate(session, strategy, settings, "1.7602", at=start + timedelta(seconds=5))
    assert state_of(outcome, first.id) is TargetState.REACHED_UNCONFIRMED
    assert outcome.notifications == []


async def test_a_stale_rate_never_confirms_a_target(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    first = strategy.tranches[0]
    start = utcnow()
    await evaluate(session, strategy, settings, "1.7601", at=start, stale=True)
    outcome = await evaluate(
        session, strategy, settings, "1.7605", at=start + timedelta(minutes=1), stale=True
    )
    assert outcome.notifications == []
    assert reason_for(outcome, first.id) == "rate is stale"


async def test_provider_disagreement_withholds_confirmation(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    first = strategy.tranches[0]
    start = utcnow()
    await evaluate(session, strategy, settings, "1.7601", at=start, disagreement=True)
    outcome = await evaluate(
        session,
        strategy,
        settings,
        "1.7605",
        at=start + timedelta(minutes=1),
        disagreement=True,
    )
    assert outcome.notifications == []
    assert state_of(outcome, first.id) is TargetState.REACHED_UNCONFIRMED
    assert "disagree" in reason_for(outcome, first.id)


async def test_a_single_sample_confirms_when_configured_to(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    settings.notifications.confirmation_samples = 1
    outcome = await evaluate(session, strategy, settings, "1.7601")
    assert len(outcome.notifications) == 1


# ---------------------------------------------------------------------------
# Duplicate suppression, hysteresis and reset
# ---------------------------------------------------------------------------


async def test_a_target_does_not_notify_twice_while_it_stays_reached(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    start = utcnow()
    await evaluate(session, strategy, settings, "1.7601", at=start)
    await evaluate(session, strategy, settings, "1.7604", at=start + timedelta(seconds=31))

    for minute in range(2, 8):
        outcome = await evaluate(
            session, strategy, settings, "1.7620", at=start + timedelta(minutes=minute)
        )
        assert outcome.notifications == []
        assert "awaiting reset" in reason_for(outcome, strategy.tranches[0].id)


async def test_a_small_dip_does_not_reset_the_target(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    first = strategy.tranches[0]
    start = utcnow()
    await evaluate(session, strategy, settings, "1.7601", at=start)
    await evaluate(session, strategy, settings, "1.7604", at=start + timedelta(seconds=31))

    # 1.7570 is only 0.0030 below the target: inside the 0.0050 hysteresis.
    outcome = await evaluate(session, strategy, settings, "1.7570", at=start + timedelta(minutes=2))
    assert state_of(outcome, first.id) is TargetState.NOTIFIED
    assert outcome.notifications == []


async def test_falling_past_the_hysteresis_resets_and_allows_a_second_alert(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    first = strategy.tranches[0]
    start = utcnow()
    await evaluate(session, strategy, settings, "1.7601", at=start)
    await evaluate(session, strategy, settings, "1.7604", at=start + timedelta(seconds=31))

    # 1.7540 is more than 0.0050 below 1.7600, so the target resets.
    reset = await evaluate(session, strategy, settings, "1.7540", at=start + timedelta(minutes=5))
    assert state_of(reset, first.id) is TargetState.BELOW

    await evaluate(session, strategy, settings, "1.7610", at=start + timedelta(minutes=10))
    again = await evaluate(session, strategy, settings, "1.7615", at=start + timedelta(minutes=11))
    assert len(again.notifications) == 1


async def test_an_acknowledged_target_does_not_notify_again(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    first = strategy.tranches[0]
    first.acknowledged_at = utcnow()
    await session.flush()

    start = utcnow()
    await evaluate(session, strategy, settings, "1.7601", at=start)
    outcome = await evaluate(
        session, strategy, settings, "1.7604", at=start + timedelta(seconds=31)
    )
    assert outcome.notifications == []
    assert state_of(outcome, first.id) is TargetState.ACKNOWLEDGED


async def test_a_tranche_with_notifications_disabled_is_silent(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    first = strategy.tranches[0]
    first.notifications_enabled = False
    await session.flush()

    start = utcnow()
    await evaluate(session, strategy, settings, "1.7601", at=start)
    outcome = await evaluate(
        session, strategy, settings, "1.7604", at=start + timedelta(seconds=31)
    )
    assert outcome.notifications == []
    assert state_of(outcome, first.id) is TargetState.REACHED_CONFIRMED


async def test_a_completed_tranche_is_terminal(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    first = strategy.tranches[0]
    first.status = str(TrancheStatus.COMPLETED)
    await session.flush()
    outcome = await evaluate(session, strategy, settings, "1.9000")
    assert state_of(outcome, first.id) is TargetState.COMPLETED
    assert outcome.notifications == []


async def test_a_skipped_tranche_is_ignored(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    first = strategy.tranches[0]
    first.status = str(TrancheStatus.SKIPPED)
    await session.flush()
    outcome = await evaluate(session, strategy, settings, "1.9000")
    assert outcome.notifications == []
    assert reason_for(outcome, first.id) == "tranche is skipped"


# ---------------------------------------------------------------------------
# Approaching alerts
# ---------------------------------------------------------------------------


async def test_approaching_a_target_notifies_once(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    first = strategy.tranches[0]
    # 0.0048 below the 1.7600 target, inside the 0.0050 near threshold.
    outcome = await evaluate(session, strategy, settings, "1.7552")
    assert state_of(outcome, first.id) is TargetState.NEAR
    assert len(outcome.notifications) == 1
    notification = outcome.notifications[0]
    assert notification.rule_type is AlertRuleType.TARGET_NEAR
    assert "0.00480000" in notification.message

    # Staying near does not repeat it.
    again = await evaluate(session, strategy, settings, "1.7555")
    assert again.notifications == []


async def test_no_approaching_alert_from_a_stale_rate(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    outcome = await evaluate(session, strategy, settings, "1.7552", stale=True)
    assert outcome.notifications == []


async def test_a_rate_far_below_produces_nothing(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    first = strategy.tranches[0]
    outcome = await evaluate(session, strategy, settings, "1.6500")
    assert state_of(outcome, first.id) is TargetState.BELOW
    assert outcome.notifications == []


async def test_no_rate_at_all_produces_nothing(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    outcome = await alert_service.evaluate_targets(
        session, strategy, settings, rate=None, rate_is_stale=True
    )
    assert outcome.notifications == []
    assert all(e.reason == "no rate available" for e in outcome.evaluations)


async def test_a_rate_above_every_target_notifies_each_one(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> None:
    start = utcnow()
    await evaluate(session, strategy, settings, "1.8100", at=start)
    outcome = await evaluate(
        session, strategy, settings, "1.8100", at=start + timedelta(seconds=31)
    )
    assert len(outcome.notifications) == 2


# ---------------------------------------------------------------------------
# Delivery: cooldown, quiet hours, failure handling
# ---------------------------------------------------------------------------


def notification() -> Notification:
    return Notification(
        rule_type=AlertRuleType.TARGET_REACHED,
        title="Target reached",
        message="body",
        entity_type="tranche",
        entity_id="1",
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
        rule_type=AlertRuleType.TARGET_REACHED,
        title="Other target",
        message="body",
        entity_type="tranche",
        entity_id="2",
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
        rule_type=AlertRuleType.DEADLINE_MISSED,
        title="Deadline passed",
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
                rule_type=AlertRuleType.RATE_BELOW,
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
