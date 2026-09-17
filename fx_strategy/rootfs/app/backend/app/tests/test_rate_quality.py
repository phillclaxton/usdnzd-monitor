"""Keeping bad observations out of the figures.

Two halves: refusing an implausible quote on arrival, and removing one that got
in before the guard existed. Both leave the row in the database — what a
provider actually returned is the evidence for why a wrong figure appeared.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.models.rate import RateSample
from app.providers.base import QuoteType, RateQuote
from app.schemas.settings import Settings
from app.services import rate_service, settings_service
from app.tests.test_rate_service import StubProvider, StubRegistry


@pytest.fixture
async def settings(session: AsyncSession) -> Settings:
    return await settings_service.load_settings(session)


def quote(rate: str, provider: str = "primary") -> RateQuote:
    return RateQuote(
        provider=provider,
        source_currency="USD",
        target_currency="NZD",
        rate=Decimal(rate),
        quote_type=QuoteType.MID_MARKET,
        latency_ms=10,
    )


async def seed(session: AsyncSession, rate: str, *, minutes_ago: int = 0) -> RateSample:
    sample = await rate_service.store_sample(session, quote(rate))
    if minutes_ago:
        sample.retrieved_at = utcnow() - timedelta(minutes=minutes_ago)
        await session.flush()
    return sample


# ---------------------------------------------------------------------------
# Refusing on arrival
# ---------------------------------------------------------------------------


async def test_a_spike_is_refused_and_does_not_become_the_current_rate(
    session: AsyncSession, settings: Settings
) -> None:
    await seed(session, "1.7200")

    registry = StubRegistry(settings, {"primary": StubProvider("primary", Decimal("1.7750"))})
    outcome = await rate_service.refresh_rate(session, settings, registry)

    assert outcome.succeeded is False
    assert outcome.refused, "the refused sample is kept, not dropped on the floor"
    assert "Refused on arrival" in outcome.errors["primary"]

    current = await rate_service.latest_sample(session, "USD", "NZD")
    assert current is not None and current.rate == Decimal("1.72000000")


async def test_the_refused_sample_is_kept_with_the_reason(
    session: AsyncSession, settings: Settings
) -> None:
    await seed(session, "1.7200")
    registry = StubRegistry(settings, {"primary": StubProvider("primary", Decimal("1.7750"))})
    outcome = await rate_service.refresh_rate(session, settings, registry)

    refused = await session.get(RateSample, outcome.refused[0])
    assert refused is not None
    assert refused.rate == Decimal("1.77500000")
    assert refused.excluded_at is not None
    # The reason names the figures, not just "rejected".
    assert "1.7750" in (refused.excluded_reason or "")
    assert "1.7200" in (refused.excluded_reason or "")


async def test_the_provider_is_not_marked_failing_for_a_refused_quote(
    session: AsyncSession, settings: Settings
) -> None:
    """The call worked. It is the number that is in doubt, not the provider."""
    await seed(session, "1.7200")
    registry = StubRegistry(settings, {"primary": StubProvider("primary", Decimal("1.7750"))})
    await rate_service.refresh_rate(session, settings, registry)

    statuses = {row.provider: row for row in await rate_service.provider_statuses(session)}
    assert statuses["primary"].healthy is True
    assert statuses["primary"].consecutive_failures == 0


async def test_a_refused_primary_falls_through_to_the_secondary(
    session: AsyncSession, settings: Settings
) -> None:
    await seed(session, "1.7200")
    registry = StubRegistry(
        settings,
        {
            "primary": StubProvider("primary", Decimal("1.7750")),
            "secondary": StubProvider("secondary", Decimal("1.7210")),
        },
    )
    outcome = await rate_service.refresh_rate(session, settings, registry)

    assert outcome.succeeded
    assert outcome.used_provider == "secondary"
    assert outcome.quote is not None and outcome.quote.rate == Decimal("1.7210")


async def test_a_move_within_the_threshold_is_accepted(
    session: AsyncSession, settings: Settings
) -> None:
    await seed(session, "1.7200")
    registry = StubRegistry(settings, {"primary": StubProvider("primary", Decimal("1.7400"))})
    outcome = await rate_service.refresh_rate(session, settings, registry)

    assert outcome.succeeded
    assert outcome.refused == []


async def test_a_sustained_move_is_believed_rather_than_refused_for_ever(
    session: AsyncSession, settings: Settings
) -> None:
    """A real jump must not lock the app out of collecting rates."""
    await seed(session, "1.7200")
    registry = StubRegistry(settings, {"primary": StubProvider("primary", Decimal("1.7750"))})

    first = await rate_service.refresh_rate(session, settings, registry)
    second = await rate_service.refresh_rate(session, settings, registry)
    assert first.succeeded is False and second.succeeded is False

    # Three in a row agreeing is the default, so the third is accepted.
    third = await rate_service.refresh_rate(session, settings, registry)
    assert third.succeeded is True
    assert third.quote is not None and third.quote.rate == Decimal("1.7750")


async def test_disagreeing_refusals_do_not_add_up_to_acceptance(
    session: AsyncSession, settings: Settings
) -> None:
    """Three different wild numbers are three glitches, not a trend."""
    await seed(session, "1.7200")
    for rate in ("1.7750", "1.9000", "2.1000"):
        registry = StubRegistry(settings, {"primary": StubProvider("primary", Decimal(rate))})
        outcome = await rate_service.refresh_rate(session, settings, registry)
        assert outcome.succeeded is False, f"{rate} should not have been accepted"


async def test_the_first_ever_rate_is_accepted(session: AsyncSession, settings: Settings) -> None:
    """With nothing to compare against, refusing would collect nothing at all."""
    registry = StubRegistry(settings, {"primary": StubProvider("primary", Decimal("1.7750"))})
    outcome = await rate_service.refresh_rate(session, settings, registry)
    assert outcome.succeeded is True


async def test_an_old_reference_is_not_used_to_judge_a_new_quote(
    session: AsyncSession, settings: Settings
) -> None:
    """After a long outage the market really could be anywhere."""
    await seed(session, "1.7200", minutes_ago=24 * 60)

    registry = StubRegistry(settings, {"primary": StubProvider("primary", Decimal("1.7750"))})
    outcome = await rate_service.refresh_rate(session, settings, registry)
    assert outcome.succeeded is True


async def test_the_guard_can_be_turned_off(session: AsyncSession, settings: Settings) -> None:
    await seed(session, "1.7200")
    settings.providers.implausible_move_enabled = False

    registry = StubRegistry(settings, {"primary": StubProvider("primary", Decimal("1.9000"))})
    outcome = await rate_service.refresh_rate(session, settings, registry)
    assert outcome.succeeded is True


# ---------------------------------------------------------------------------
# Excluding one that already got in
# ---------------------------------------------------------------------------


async def test_an_excluded_sample_leaves_every_figure(
    session: AsyncSession, settings: Settings
) -> None:
    await seed(session, "1.7200", minutes_ago=30)
    spike = await seed(session, "1.7750", minutes_ago=20)
    await seed(session, "1.7210", minutes_ago=10)

    before = await rate_service.extremes(session, "USD", "NZD", utcnow() - timedelta(hours=1))
    assert before.high == Decimal("1.77500000")

    await rate_service.exclude_sample(session, spike, reason="never happened")

    after = await rate_service.extremes(session, "USD", "NZD", utcnow() - timedelta(hours=1))
    assert after.high == Decimal("1.72100000")
    assert after.low == Decimal("1.72000000")

    points = await rate_service.history(
        session, "USD", "NZD", utcnow() - timedelta(hours=1), utcnow()
    )
    assert [point.rate for point in points] == [Decimal("1.72000000"), Decimal("1.72100000")]


async def test_an_excluded_sample_is_not_the_current_rate(
    session: AsyncSession, settings: Settings
) -> None:
    await seed(session, "1.7200", minutes_ago=10)
    spike = await seed(session, "1.7750")

    assert (await rate_service.latest_sample(session, "USD", "NZD")) is not None
    await rate_service.exclude_sample(session, spike, reason="never happened")

    current = await rate_service.latest_sample(session, "USD", "NZD")
    assert current is not None and current.rate == Decimal("1.72000000")


async def test_excluding_rebuilds_the_aggregates_behind_the_long_range_chart(
    session: AsyncSession, settings: Settings
) -> None:
    """Otherwise the spike vanishes at 7 days and reappears at 3 months."""
    await seed(session, "1.7200", minutes_ago=40)
    spike = await seed(session, "1.7750", minutes_ago=30)
    await seed(session, "1.7210", minutes_ago=20)

    now = utcnow()
    await rate_service.build_aggregates(
        session, "USD", "NZD", bucket="hour", start=now - timedelta(hours=3), end=now
    )
    before = await rate_service.aggregates(
        session, "USD", "NZD", "hour", now - timedelta(hours=3), now
    )
    assert max(row.high_rate for row in before) == Decimal("1.77500000")

    await rate_service.exclude_sample(session, spike, reason="never happened")

    after = await rate_service.aggregates(
        session, "USD", "NZD", "hour", now - timedelta(hours=3), now
    )
    assert max(row.high_rate for row in after) == Decimal("1.72100000")


async def test_a_bucket_with_nothing_left_is_removed_not_left_stale(
    session: AsyncSession, settings: Settings
) -> None:
    spike = await seed(session, "1.7750", minutes_ago=30)
    now = utcnow()
    await rate_service.build_aggregates(
        session, "USD", "NZD", bucket="hour", start=now - timedelta(hours=2), end=now
    )
    assert await rate_service.aggregates(
        session, "USD", "NZD", "hour", now - timedelta(hours=2), now
    )

    await rate_service.exclude_sample(session, spike, reason="never happened")

    remaining = await rate_service.aggregates(
        session, "USD", "NZD", "hour", now - timedelta(hours=2), now
    )
    assert remaining == []


async def test_restoring_brings_the_sample_back(session: AsyncSession, settings: Settings) -> None:
    await seed(session, "1.7200", minutes_ago=20)
    spike = await seed(session, "1.7750", minutes_ago=10)

    await rate_service.exclude_sample(session, spike, reason="mistake")
    await rate_service.restore_sample(session, spike)

    assert spike.excluded_at is None
    assert spike.excluded_reason is None
    window = await rate_service.extremes(session, "USD", "NZD", utcnow() - timedelta(hours=1))
    assert window.high == Decimal("1.77500000")


async def test_excluding_is_recorded_in_the_audit_trail(
    session: AsyncSession, client: AsyncClient
) -> None:
    spike = await rate_service.store_sample(session, quote("1.7750"))
    await rate_service.exclude_sample(session, spike, reason="never happened", actor="user")
    await session.commit()

    events = (await client.get("/api/v1/audit-events?entity_type=rate_sample")).json()
    assert events
    assert "never happened" in events[0]["message"]


# ---------------------------------------------------------------------------
# The review endpoint
# ---------------------------------------------------------------------------


async def test_the_review_endpoint_flags_the_outlier(client: AsyncClient) -> None:
    for _ in range(8):
        await client.post("/api/v1/rates/manual", json={"rate": "1.7200"})
    await client.post("/api/v1/rates/manual", json={"rate": "1.7750"})
    for _ in range(8):
        await client.post("/api/v1/rates/manual", json={"rate": "1.7210"})

    body = (await client.get("/api/v1/rates/samples?range=24h")).json()
    suspicious = [row for row in body["samples"] if row["suspicious"]]
    assert len(suspicious) == 1
    assert suspicious[0]["rate"] == "1.77500000"
    assert body["suspicious_count"] == 1


async def test_a_point_can_be_excluded_and_restored_over_the_api(
    client: AsyncClient,
) -> None:
    await client.post("/api/v1/rates/manual", json={"rate": "1.7200"})
    await client.post("/api/v1/rates/manual", json={"rate": "1.7750"})

    rows = (await client.get("/api/v1/rates/samples?range=24h")).json()["samples"]
    spike = next(row for row in rows if row["rate"] == "1.77500000")

    excluded = await client.post(
        "/api/v1/rates/samples/exclude",
        json={"sample_ids": [spike["id"]], "reason": "never happened"},
    )
    assert excluded.status_code == 200
    assert "can be restored" in excluded.json()["message"]

    history = (await client.get("/api/v1/rates/history?range=24h")).json()
    assert all(point["rate"] != "1.77500000" for point in history["points"])

    after = (await client.get("/api/v1/rates/samples?range=24h")).json()["samples"]
    assert next(row for row in after if row["id"] == spike["id"])["excluded"] is True

    restored = await client.post(
        "/api/v1/rates/samples/restore", json={"sample_ids": [spike["id"]]}
    )
    assert restored.status_code == 200
    back = (await client.get("/api/v1/rates/history?range=24h")).json()
    assert any(point["rate"] == "1.77500000" for point in back["points"])


async def test_excluding_a_sample_that_does_not_exist_is_a_404(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/rates/samples/exclude", json={"sample_ids": [9999], "reason": "x"}
    )
    assert response.status_code == 404


async def test_the_review_endpoint_rejects_an_unknown_range(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/rates/samples?range=decade")).status_code == 422


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def _series(count: int) -> list[RateSample]:
    base = utcnow() - timedelta(minutes=5 * count)
    rows = []
    for index in range(count):
        row = RateSample(
            provider="wise",
            source_currency="USD",
            target_currency="NZD",
            rate=Decimal("1.7200") + Decimal(index % 7) / Decimal(10000),
            rate_numeric=1.72,
            quote_type="mid_market",
            retrieved_at=base + timedelta(minutes=5 * index),
            is_stale=False,
        )
        row.id = index + 1
        row.excluded_at = None
        rows.append(row)
    return rows


def test_reviewing_a_long_history_stays_cheap() -> None:
    """A guard on the shape of the work, not a micro-benchmark.

    Comparing every sample against every other is quadratic. At five-minute
    polling a month is ~8,600 samples and a year is capped at 20,000, so the
    quadratic version took 23 seconds and 110 seconds of CPU for one request —
    on a machine far faster than the one this runs on. The bound below is
    generous enough not to flake on a loaded runner and still fails by a wide
    margin if the neighbour window goes back to scanning the whole series.
    """
    import time

    rows = _series(12_000)
    started = time.perf_counter()
    reviews = rate_service.review_samples(rows)
    elapsed = time.perf_counter() - started

    assert len(reviews) == 12_000
    assert elapsed < 15, f"reviewing 12,000 samples took {elapsed:.1f}s"


def test_every_sample_is_judged_against_its_neighbours_not_the_whole_series() -> None:
    """A slow drift is not an outlier; a local jump is."""
    rows = _series(200)
    # A long, smooth climb: every point is far from the start and near its
    # neighbours, so nothing should stand out.
    for index, row in enumerate(rows):
        row.rate = Decimal("1.7000") + Decimal(index) / Decimal(1000)

    reviews = rate_service.review_samples(rows)
    deviations = [r.deviation for r in reviews if r.deviation is not None]
    assert deviations and max(deviations) < Decimal("0.02")

    # One point put well off the line is caught.
    rows[100].rate = Decimal("2.5000")
    caught = rate_service.review_samples(rows)
    assert next(r for r in caught if r.sample.id == 101).deviation > Decimal("0.02")
