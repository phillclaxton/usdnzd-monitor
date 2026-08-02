"""Rate collection, staleness, fallback and disagreement tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.models.rate import ProviderStatus, RateSample
from app.providers.base import (
    ProviderHealth,
    ProviderUnavailableError,
    QuoteType,
    RatePoint,
    RateQuote,
)
from app.providers.registry import ProviderRegistry
from app.schemas.settings import Settings
from app.services import rate_service, settings_service


class StubProvider:
    """A provider that returns, or fails, exactly as a test dictates."""

    supports_history = False

    def __init__(self, name: str, rate: Decimal | None, *, error: str | None = None) -> None:
        self.name = name
        self.display_name = name
        self.rate = rate
        self.error = error
        self.calls = 0

    async def get_spot_rate(self, source_currency: str, target_currency: str) -> RateQuote:
        self.calls += 1
        if self.error is not None:
            raise ProviderUnavailableError(self.name, self.error)
        assert self.rate is not None
        return RateQuote(
            provider=self.name,
            source_currency=source_currency,
            target_currency=target_currency,
            rate=self.rate,
            quote_type=QuoteType.MID_MARKET,
            latency_ms=12,
        )

    async def get_historical_rates(self, *_args: Any, **_kwargs: Any) -> list[RatePoint]:
        return []

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(name=self.name, healthy=self.error is None)

    async def aclose(self) -> None:
        return None


class StubRegistry(ProviderRegistry):
    """A registry whose chain is fixed by the test."""

    def __init__(self, settings: Settings, providers: dict[str, StubProvider]) -> None:
        super().__init__(settings)
        self._stubs = providers

    def create(self, name: str) -> Any:
        if name not in self._stubs:
            raise ProviderUnavailableError(name, f"{name} not configured in this test")
        return self._stubs[name]

    def chain(self) -> list[str]:
        return list(self._stubs)

    def comparison_pair(self) -> list[str]:
        return list(self._stubs)[:2]

    async def aclose(self) -> None:
        return None


@pytest.fixture
async def settings(session: AsyncSession) -> Settings:
    return await settings_service.load_settings(session)


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


async def test_successful_refresh_stores_an_exact_decimal(
    session: AsyncSession, settings: Settings
) -> None:
    registry = StubRegistry(settings, {"primary": StubProvider("primary", Decimal("1.76043210"))})
    outcome = await rate_service.refresh_rate(session, settings, registry)

    assert outcome.succeeded
    assert outcome.used_provider == "primary"
    sample = await rate_service.latest_sample(session, "USD", "NZD")
    assert sample is not None
    assert sample.rate == Decimal("1.76043210")
    assert isinstance(sample.rate, Decimal)


async def test_falls_back_to_the_next_provider_and_records_the_failure(
    session: AsyncSession, settings: Settings
) -> None:
    registry = StubRegistry(
        settings,
        {
            "primary": StubProvider("primary", None, error="upstream exploded"),
            "secondary": StubProvider("secondary", Decimal("1.75")),
        },
    )
    outcome = await rate_service.refresh_rate(session, settings, registry)

    assert outcome.used_provider == "secondary"
    assert outcome.errors["primary"] == "upstream exploded"
    status = await session.get(ProviderStatus, "primary")
    assert status is not None
    assert status.healthy is False
    assert status.consecutive_failures == 1
    assert status.retry_after is not None


async def test_every_provider_failing_is_reported_not_papered_over(
    session: AsyncSession, settings: Settings
) -> None:
    registry = StubRegistry(
        settings,
        {
            "primary": StubProvider("primary", None, error="down"),
            "manual": StubProvider("manual", None, error="nothing entered"),
        },
    )
    outcome = await rate_service.refresh_rate(session, settings, registry)

    assert outcome.succeeded is False
    assert outcome.quote is None
    assert set(outcome.errors) == {"primary", "manual"}
    # No sample was invented to cover the gap.
    assert await rate_service.latest_sample(session, "USD", "NZD") is None


async def test_backoff_skips_a_provider_that_is_still_cooling_off(
    session: AsyncSession, settings: Settings
) -> None:
    failing = StubProvider("primary", None, error="down")
    registry = StubRegistry(
        settings, {"primary": failing, "secondary": StubProvider("secondary", Decimal("1.75"))}
    )
    await rate_service.refresh_rate(session, settings, registry)
    assert failing.calls == 1

    await rate_service.refresh_rate(session, settings, registry)
    # Still inside the backoff window, so the failing provider is not called again.
    assert failing.calls == 1

    await rate_service.refresh_rate(session, settings, registry, respect_backoff=False)
    assert failing.calls == 2


async def test_recovery_clears_the_failure_counter(
    session: AsyncSession, settings: Settings
) -> None:
    provider = StubProvider("primary", None, error="down")
    registry = StubRegistry(settings, {"primary": provider})
    await rate_service.refresh_rate(session, settings, registry)

    provider.error = None
    provider.rate = Decimal("1.75")
    await rate_service.refresh_rate(session, settings, registry, respect_backoff=False)

    status = await session.get(ProviderStatus, "primary")
    assert status is not None
    assert status.healthy is True
    assert status.consecutive_failures == 0
    assert status.failing_since is None


# ---------------------------------------------------------------------------
# Disagreement
# ---------------------------------------------------------------------------


async def test_providers_within_the_threshold_do_not_warn(
    session: AsyncSession, settings: Settings
) -> None:
    registry = StubRegistry(
        settings,
        {
            "primary": StubProvider("primary", Decimal("1.7600")),
            "secondary": StubProvider("secondary", Decimal("1.7620")),
        },
    )
    outcome = await rate_service.refresh_rate(session, settings, registry)
    # 0.002 / 1.76 = 0.00113, under the 0.0030 default.
    assert outcome.disagreement_exceeded is False


async def test_providers_beyond_the_threshold_raise_a_warning(
    session: AsyncSession, settings: Settings
) -> None:
    registry = StubRegistry(
        settings,
        {
            "primary": StubProvider("primary", Decimal("1.7600")),
            "secondary": StubProvider("secondary", Decimal("1.7900")),
        },
    )
    outcome = await rate_service.refresh_rate(session, settings, registry)
    assert outcome.disagreement_exceeded is True
    assert outcome.comparison["secondary"] == "1.7900"


async def test_absolute_disagreement_mode(session: AsyncSession, settings: Settings) -> None:
    settings.providers.disagreement_is_relative = False
    settings.providers.disagreement_threshold = Decimal("0.0100")
    registry = StubRegistry(
        settings,
        {
            "primary": StubProvider("primary", Decimal("1.7600")),
            "secondary": StubProvider("secondary", Decimal("1.7750")),
        },
    )
    outcome = await rate_service.refresh_rate(session, settings, registry)
    assert outcome.disagreement == Decimal("0.0150")
    assert outcome.disagreement_exceeded is True


async def test_a_failing_secondary_does_not_break_the_refresh(
    session: AsyncSession, settings: Settings
) -> None:
    registry = StubRegistry(
        settings,
        {
            "primary": StubProvider("primary", Decimal("1.76")),
            "secondary": StubProvider("secondary", None, error="offline"),
        },
    )
    outcome = await rate_service.refresh_rate(session, settings, registry)
    assert outcome.succeeded
    assert "offline" in outcome.comparison["secondary"]


def test_relative_difference() -> None:
    assert rate_service.relative_difference(Decimal("2"), Decimal("2")) == 0
    assert rate_service.relative_difference(Decimal("0"), Decimal("0")) == 0
    difference = rate_service.relative_difference(Decimal("1.76"), Decimal("1.79"))
    assert Decimal("0.017") < difference < Decimal("0.018")


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------


async def test_a_fresh_sample_reads_as_live(session: AsyncSession, settings: Settings) -> None:
    registry = StubRegistry(settings, {"primary": StubProvider("primary", Decimal("1.76"))})
    await rate_service.refresh_rate(session, settings, registry)

    current = await rate_service.current_rate(session, settings)
    assert current.status == "live"
    assert current.is_stale is False


async def test_an_old_sample_reads_as_stale(session: AsyncSession, settings: Settings) -> None:
    session.add(
        RateSample(
            provider="primary",
            source_currency="USD",
            target_currency="NZD",
            rate=Decimal("1.76"),
            rate_numeric=1.76,
            quote_type=str(QuoteType.MID_MARKET),
            retrieved_at=utcnow() - timedelta(hours=2),
        )
    )
    await session.flush()

    current = await rate_service.current_rate(session, settings)
    assert current.status == "stale"
    assert current.is_stale is True
    assert current.age_seconds is not None and current.age_seconds > 3600


async def test_no_samples_at_all_reads_as_unavailable(
    session: AsyncSession, settings: Settings
) -> None:
    current = await rate_service.current_rate(session, settings)
    assert current.status == "unavailable"
    assert current.rate is None


async def test_between_half_and_full_staleness_reads_as_delayed(
    session: AsyncSession, settings: Settings
) -> None:
    session.add(
        RateSample(
            provider="primary",
            source_currency="USD",
            target_currency="NZD",
            rate=Decimal("1.76"),
            rate_numeric=1.76,
            quote_type=str(QuoteType.MID_MARKET),
            retrieved_at=utcnow() - timedelta(seconds=600),
        )
    )
    await session.flush()
    current = await rate_service.current_rate(session, settings)
    assert current.status == "delayed"


# ---------------------------------------------------------------------------
# History, extremes and changes
# ---------------------------------------------------------------------------


async def seed_history(session: AsyncSession, rates: list[tuple[int, str]]) -> None:
    """Insert samples at ``hours_ago`` offsets."""
    for hours_ago, rate in rates:
        session.add(
            RateSample(
                provider="test",
                source_currency="USD",
                target_currency="NZD",
                rate=Decimal(rate),
                rate_numeric=float(rate),
                quote_type=str(QuoteType.MID_MARKET),
                retrieved_at=utcnow() - timedelta(hours=hours_ago),
            )
        )
    await session.flush()


async def test_extremes_return_exact_decimals(session: AsyncSession, settings: Settings) -> None:
    await seed_history(session, [(1, "1.7512"), (5, "1.8033"), (10, "1.6821")])
    low, high = await rate_service.extremes(session, "USD", "NZD", utcnow() - timedelta(days=1))
    assert low == Decimal("1.68210000")
    assert high == Decimal("1.80330000")
    assert isinstance(low, Decimal)


async def test_change_over_a_window(session: AsyncSession, settings: Settings) -> None:
    await seed_history(session, [(25, "1.7000"), (0, "1.7600")])
    change = await rate_service.change_over(
        session, "USD", "NZD", timedelta(hours=24), Decimal("1.7600")
    )
    assert change == Decimal("0.06000000")


async def test_change_is_none_without_an_earlier_sample(
    session: AsyncSession, settings: Settings
) -> None:
    await seed_history(session, [(0, "1.76")])
    change = await rate_service.change_over(
        session, "USD", "NZD", timedelta(days=30), Decimal("1.76")
    )
    assert change is None


async def test_stale_samples_are_excluded_from_extremes(
    session: AsyncSession, settings: Settings
) -> None:
    await seed_history(session, [(1, "1.75")])
    session.add(
        RateSample(
            provider="test",
            source_currency="USD",
            target_currency="NZD",
            rate=Decimal("9.99"),
            rate_numeric=9.99,
            quote_type=str(QuoteType.MID_MARKET),
            retrieved_at=utcnow() - timedelta(hours=2),
            is_stale=True,
        )
    )
    await session.flush()
    _low, high = await rate_service.extremes(session, "USD", "NZD", utcnow() - timedelta(days=1))
    assert high == Decimal("1.75000000")


# ---------------------------------------------------------------------------
# Manual entry, aggregation and retention
# ---------------------------------------------------------------------------


async def test_manual_rate_is_stored_and_audited(session: AsyncSession) -> None:
    sample = await rate_service.record_manual_rate(
        session,
        source_currency="USD",
        target_currency="NZD",
        rate=Decimal("1.7325"),
        note="from the Wise app",
    )
    assert sample.rate == Decimal("1.73250000")
    assert sample.quote_type == str(QuoteType.MANUAL)

    latest = await rate_service.latest_manual_rate(session, "USD", "NZD")
    assert latest is not None and latest.note == "from the Wise app"

    from app.services import audit

    events = await audit.list_events(session, entity_type="rate")
    assert events and "Manual rate" in events[0].message


async def test_aggregates_are_built_before_purging(
    session: AsyncSession, settings: Settings
) -> None:
    await seed_history(session, [(50, "1.70"), (49, "1.72"), (48, "1.74"), (1, "1.76")])

    purged = await rate_service.purge_old_samples(
        session, "USD", "NZD", older_than=utcnow() - timedelta(hours=24)
    )
    assert purged == 3

    daily = await rate_service.aggregates(
        session, "USD", "NZD", "day", utcnow() - timedelta(days=7), utcnow()
    )
    assert daily, "aggregates must exist before raw samples are removed"
    assert any(row.high_rate == Decimal("1.74000000") for row in daily)

    remaining = await rate_service.history(
        session, "USD", "NZD", utcnow() - timedelta(days=7), utcnow()
    )
    assert len(remaining) == 1


async def test_aggregate_rebuild_is_idempotent(session: AsyncSession) -> None:
    await seed_history(session, [(3, "1.70"), (2, "1.80")])
    window_start = utcnow() - timedelta(days=1)
    first = await rate_service.build_aggregates(
        session, "USD", "NZD", bucket="day", start=window_start, end=utcnow()
    )
    second = await rate_service.build_aggregates(
        session, "USD", "NZD", bucket="day", start=window_start, end=utcnow()
    )
    assert first == second == 1
    rows = await rate_service.aggregates(session, "USD", "NZD", "day", window_start, utcnow())
    assert len(rows) == 1
    assert rows[0].low_rate == Decimal("1.70000000")
    assert rows[0].high_rate == Decimal("1.80000000")


async def test_importing_points_is_idempotent(session: AsyncSession) -> None:
    points = [
        RatePoint(
            timestamp=datetime(2026, 7, day, tzinfo=UTC), rate=Decimal("1.75"), provider="csv"
        )
        for day in (1, 2, 3)
    ]
    assert await rate_service.import_points(session, points, "USD", "NZD") == 3
    # Re-importing the same file adds nothing.
    assert await rate_service.import_points(session, points, "USD", "NZD") == 0
