"""Post-refresh pipeline tests: which rules fire, and which correctly do not."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.home_assistant.client import set_home_assistant
from app.models.rate import ProviderStatus, RateSample
from app.models.strategy import Strategy
from app.providers.base import QuoteType
from app.schemas.settings import Settings
from app.schemas.strategy import StrategyIn, TrancheIn
from app.services import monitor, notifications, rate_service, settings_service
from app.services import strategy_service as strategies
from app.services.rate_service import RefreshOutcome
from app.tests.test_alerts import FakeHomeAssistant


@pytest.fixture
async def settings(session: AsyncSession) -> Settings:
    loaded = await settings_service.load_settings(session)
    loaded.notifications.services = ["notify.test"]
    loaded.notifications.confirmation_samples = 1
    await settings_service.save_settings(session, loaded)
    return loaded


@pytest.fixture
def fake_home_assistant() -> FakeHomeAssistant:
    fake = FakeHomeAssistant()
    set_home_assistant(fake)  # type: ignore[arg-type]
    yield fake
    set_home_assistant(None)


async def make_strategy(session: AsyncSession, **overrides: object) -> Strategy:
    payload = StrategyIn(
        name="Monitored",
        initial_source_amount=Decimal("800000"),
        funds_available_amount=Decimal("800000"),
        walk_away_rate=Decimal("1.7800"),
        tranches=[
            TrancheIn(
                sequence=1,
                allocation_type="percentage",
                allocation_value=Decimal("100"),
                target_rate=Decimal("1.7600"),
            )
        ],
        **overrides,  # type: ignore[arg-type]
    )
    strategy = await strategies.create_strategy(session, payload)
    await strategies.activate(session, strategy)
    await session.flush()
    return strategy


async def set_rate(session: AsyncSession, rate: str, *, minutes_ago: int = 0) -> None:
    session.add(
        RateSample(
            provider="test",
            source_currency="USD",
            target_currency="NZD",
            rate=Decimal(rate),
            rate_numeric=float(rate),
            quote_type=str(QuoteType.MID_MARKET),
            retrieved_at=utcnow() - timedelta(minutes=minutes_ago),
        )
    )
    await session.flush()


def outcome(**kwargs: object) -> RefreshOutcome:
    return RefreshOutcome(**kwargs)  # type: ignore[arg-type]


async def test_a_reached_target_notifies_through_the_pipeline(
    session: AsyncSession,
    settings: Settings,
    fake_home_assistant: FakeHomeAssistant,
) -> None:
    await make_strategy(session)
    await set_rate(session, "1.7650")

    result = await monitor.run_after_refresh(session, settings, outcome())

    assert result.evaluated_strategies == 1
    assert result.notifications_delivered >= 1
    titles = [call["title"] for call in fake_home_assistant.calls]
    assert any("target reached" in title for title in titles)


async def test_a_stale_rate_produces_no_notification(
    session: AsyncSession,
    settings: Settings,
    fake_home_assistant: FakeHomeAssistant,
) -> None:
    await make_strategy(session)
    # Older than the 900-second staleness threshold.
    await set_rate(session, "1.7650", minutes_ago=30)

    result = await monitor.run_after_refresh(session, settings, outcome())
    assert result.notifications_delivered == 0
    assert fake_home_assistant.calls == []


async def test_a_paused_strategy_is_not_monitored(
    session: AsyncSession,
    settings: Settings,
    fake_home_assistant: FakeHomeAssistant,
) -> None:
    from app.models.strategy import StrategyStatus

    strategy = await make_strategy(session)
    await strategies.set_status(session, strategy, StrategyStatus.PAUSED)
    await set_rate(session, "1.7650")

    result = await monitor.run_after_refresh(session, settings, outcome())
    assert result.evaluated_strategies == 0
    assert fake_home_assistant.calls == []


async def test_the_walk_away_rate_produces_its_own_alert(
    session: AsyncSession,
    settings: Settings,
    fake_home_assistant: FakeHomeAssistant,
) -> None:
    await make_strategy(session)
    await set_rate(session, "1.7850")

    await monitor.run_after_refresh(session, settings, outcome())
    titles = [call["title"] for call in fake_home_assistant.calls]
    assert any("walk-away" in title for title in titles)
    walk_away = next(call for call in fake_home_assistant.calls if "walk-away" in call["title"])
    # The message states both what waiting adds and what stays exposed.
    assert "exposed" in walk_away["message"]


async def test_a_deadline_warning_says_nothing_was_changed(
    session: AsyncSession,
    settings: Settings,
    fake_home_assistant: FakeHomeAssistant,
) -> None:
    await make_strategy(session, final_deadline=utcnow() + timedelta(days=7))
    await set_rate(session, "1.7000")

    await monitor.run_after_refresh(session, settings, outcome())
    deadline = [call for call in fake_home_assistant.calls if "deadline" in call["title"].lower()]
    assert deadline
    assert "No target has been changed and nothing has been converted." in deadline[0]["message"]


async def test_no_deadline_alert_far_from_the_date(
    session: AsyncSession,
    settings: Settings,
    fake_home_assistant: FakeHomeAssistant,
) -> None:
    await make_strategy(session, final_deadline=utcnow() + timedelta(days=200))
    await set_rate(session, "1.7000")
    await monitor.run_after_refresh(session, settings, outcome())
    assert not [c for c in fake_home_assistant.calls if "deadline" in c["title"].lower()]


async def test_a_reversal_from_the_daily_high_is_reported(
    session: AsyncSession,
    settings: Settings,
    fake_home_assistant: FakeHomeAssistant,
) -> None:
    await make_strategy(session)
    await set_rate(session, "1.7800", minutes_ago=120)
    await set_rate(session, "1.7550")

    await monitor.run_after_refresh(session, settings, outcome())
    reversal = [c for c in fake_home_assistant.calls if "fallen" in c["title"]]
    assert reversal
    # 800,000 x 0.025 = NZD 20,000 of value.
    assert "20,000.00" in reversal[0]["message"]


async def test_a_provider_down_briefly_does_not_notify(
    session: AsyncSession,
    settings: Settings,
    fake_home_assistant: FakeHomeAssistant,
) -> None:
    session.add(
        ProviderStatus(
            provider="generic",
            healthy=False,
            failing_since=utcnow() - timedelta(minutes=2),
            consecutive_failures=1,
            last_error="timeout",
        )
    )
    await session.flush()
    await monitor.run_after_refresh(session, settings, outcome(polled={"generic"}))
    assert not [c for c in fake_home_assistant.calls if "provider" in c["title"].lower()]


async def test_a_long_provider_outage_notifies(
    session: AsyncSession,
    settings: Settings,
    fake_home_assistant: FakeHomeAssistant,
) -> None:
    session.add(
        ProviderStatus(
            provider="generic",
            healthy=False,
            failing_since=utcnow() - timedelta(hours=2),
            consecutive_failures=12,
            last_error="connection refused",
        )
    )
    await session.flush()
    await monitor.run_after_refresh(session, settings, outcome(polled={"generic"}))
    outage = [c for c in fake_home_assistant.calls if "failing" in c["title"]]
    assert outage
    assert "connection refused" in outage[0]["message"]
    assert "stale rate" in outage[0]["message"]


async def test_a_provider_with_nothing_entered_is_never_alerted_on(
    session: AsyncSession,
    settings: Settings,
    fake_home_assistant: FakeHomeAssistant,
) -> None:
    """The manual fallback, days "down", with a healthy primary above it.

    This is the alert a working installation used to get at three in the
    morning: "Rate provider manual has been failing for 5119 minutes — no
    manual rate has been entered yet." Nothing was wrong. Nothing was chosen.
    """
    session.add(
        ProviderStatus(
            provider="manual",
            healthy=False,
            failing_since=utcnow() - timedelta(days=3, hours=13),
            consecutive_failures=1,
            last_error="No manual rate has been entered yet.",
        )
    )
    await session.flush()

    # Polled or not, the answer is the same: nothing entered is not an outage.
    await monitor.run_after_refresh(
        session, settings, outcome(polled={"wise", "manual"}, unconfigured={"manual"})
    )

    assert not [c for c in fake_home_assistant.calls if "failing" in c["title"]]


async def test_a_configured_provider_that_fails_is_still_alerted_on(
    session: AsyncSession,
    settings: Settings,
    fake_home_assistant: FakeHomeAssistant,
) -> None:
    """The suppression is about being unconfigured, not about being quiet."""
    session.add(
        ProviderStatus(
            provider="wise",
            healthy=False,
            failing_since=utcnow() - timedelta(hours=2),
            consecutive_failures=9,
            last_error="connection refused",
        )
    )
    await session.flush()

    await monitor.run_after_refresh(
        session, settings, outcome(polled={"wise"}, unconfigured={"manual"})
    )

    outage = [c for c in fake_home_assistant.calls if "failing" in c["title"]]
    assert outage and "connection refused" in outage[0]["message"]


async def test_queued_notifications_are_retried_on_the_next_cycle(
    session: AsyncSession, settings: Settings
) -> None:
    from app.services.alert_service import AlertRuleType
    from app.services.notifications import Notification

    failing = FakeHomeAssistant(fail="down")
    await notifications.send(
        session,
        Notification(rule_type=AlertRuleType.TARGET_REACHED, title="t", message="m"),
        settings,
        client=failing,  # type: ignore[arg-type]
    )

    working = FakeHomeAssistant()
    set_home_assistant(working)  # type: ignore[arg-type]
    try:
        await monitor.run_after_refresh(session, settings, outcome())
    finally:
        set_home_assistant(None)
    assert len(working.calls) == 1


async def test_a_test_notification_is_unmistakably_a_test(
    session: AsyncSession,
    settings: Settings,
    fake_home_assistant: FakeHomeAssistant,
) -> None:
    result = await monitor.send_test_notification(session, settings)
    assert result.delivered
    call = fake_home_assistant.calls[0]
    assert "test" in call["title"].lower()
    assert "No target has been reached and nothing has been converted." in call["message"]


async def test_completion_follows_conversions_not_targets(
    session: AsyncSession, settings: Settings
) -> None:
    strategy = await make_strategy(session)
    await set_rate(session, "1.9000")
    await monitor.run_after_refresh(session, settings, outcome())

    # The target is far exceeded, but nothing was converted, so the strategy
    # stays active.
    assert strategy.status == "active"
    assert await monitor.check_strategy_completion(session, strategy) is False


async def test_home_assistant_status_endpoint_reports_absence_honestly(
    client: AsyncClient,
) -> None:
    body = (await client.get("/api/v1/home-assistant/status")).json()
    assert body["available"] is False
    assert "Supervisor token" in body["message"]
    assert body["mqtt_configured"] is False


async def test_notification_history_endpoint(client: AsyncClient) -> None:
    await client.post("/api/v1/home-assistant/test-notification", json={})
    history = (await client.get("/api/v1/home-assistant/notifications")).json()
    assert history
    assert history[0]["rule_type"]


async def test_services_endpoint_is_empty_without_home_assistant(
    client: AsyncClient,
) -> None:
    assert (await client.get("/api/v1/home-assistant/services")).json() == []


async def test_current_rate_helper_marks_staleness_for_the_pipeline(
    session: AsyncSession, settings: Settings
) -> None:
    await set_rate(session, "1.76", minutes_ago=60)
    current = await rate_service.current_rate(session, settings)
    assert current.is_stale is True
