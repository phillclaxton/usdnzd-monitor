"""Post-refresh pipeline tests: which rules fire, and which correctly do not.

What is left in the pipeline after the conversion ladder went is provider
health and the movement alerts. The movement alerts have their own file; this
one is about the pipeline around them — the outage rule, the retry queue, and
the test notification.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.home_assistant.client import set_home_assistant
from app.models.rate import ProviderStatus, RateSample
from app.providers.base import QuoteType
from app.schemas.settings import Settings
from app.services import monitor, notifications, rate_service, settings_service
from app.services.rate_service import RefreshOutcome
from app.tests.helpers import FakeHomeAssistant


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
    # The message says what the outage costs: nothing is alerted from a rate
    # the app cannot trust.
    assert "does not trust" in outage[0]["message"]


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
    from app.models.alert import AlertRuleType
    from app.services.notifications import Notification

    failing = FakeHomeAssistant(fail="down")
    await notifications.send(
        session,
        Notification(rule_type=AlertRuleType.FX_ABSOLUTE_MOVE, title="t", message="m"),
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
