"""Collecting, storing and reading exchange rates.

Rules this module enforces, from the reliability requirements:

* The last valid rate is retained when a provider fails, but it is marked stale
  and never presented as live.
* A stale rate never triggers a target-reached state.
* Providers that disagree by more than the configured threshold produce a
  warning, and target confirmation is withheld until two consecutive samples
  agree.
* Nothing is fabricated: a failed refresh is reported as a failure.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.logging_setup import get_logger
from app.models.audit import AuditEventType
from app.models.rate import ManualRate, ProviderStatus, RateAggregate, RateSample
from app.money import ZERO, quantize_rate, safe_divide
from app.providers.base import ProviderError, QuoteType, RatePoint, RateQuote
from app.providers.registry import MANUAL, ProviderRegistry
from app.schemas.settings import Settings
from app.services import audit

log = get_logger(__name__)

#: Windows offered by the rate chart and the header change indicators.
CHANGE_WINDOWS: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


@dataclass(slots=True)
class RefreshOutcome:
    """What one polling cycle produced."""

    quote: RateQuote | None = None
    sample_id: int | None = None
    used_provider: str = ""
    attempted: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    disagreement: Decimal | None = None
    disagreement_exceeded: bool = False
    comparison: dict[str, str] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.quote is not None


@dataclass(slots=True)
class CurrentRate:
    """The rate as the dashboard should present it."""

    sample: RateSample | None
    is_stale: bool
    age_seconds: int | None
    stale_after_seconds: int
    provider: str
    changes: dict[str, Decimal | None]
    high_24h: Decimal | None
    low_24h: Decimal | None
    high_6m: Decimal | None
    low_6m: Decimal | None
    disagreement_warning: str | None = None

    @property
    def rate(self) -> Decimal | None:
        return self.sample.rate if self.sample else None

    @property
    def status(self) -> str:
        """``live``, ``delayed`` or ``stale`` — never blurred together."""
        if self.sample is None:
            return "unavailable"
        if self.is_stale:
            return "stale"
        if self.age_seconds is not None and self.age_seconds > self.stale_after_seconds // 2:
            return "delayed"
        return "live"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


async def store_sample(
    session: AsyncSession, quote: RateQuote, *, is_stale: bool = False
) -> RateSample:
    """Persist one observation."""
    sample = RateSample(
        provider=quote.provider,
        source_currency=quote.source_currency,
        target_currency=quote.target_currency,
        rate=quote.rate,
        # Float companion for SQL aggregation only; `rate` stays authoritative.
        rate_numeric=float(quote.rate),
        quote_type=str(quote.quote_type),
        provider_timestamp=quote.provider_timestamp,
        retrieved_at=quote.retrieved_at,
        expires_at=quote.expires_at,
        is_stale=is_stale,
        latency_ms=quote.latency_ms,
        raw_reference=quote.raw_reference,
        metadata_json=json.dumps(quote.metadata, sort_keys=True) if quote.metadata else None,
    )
    session.add(sample)
    await session.flush()
    return sample


async def record_manual_rate(
    session: AsyncSession,
    *,
    source_currency: str,
    target_currency: str,
    rate: Decimal,
    note: str = "",
    simulated: bool = False,
    actor: str = "user",
) -> RateSample:
    """Store a hand-entered rate and emit a matching sample."""
    entry = ManualRate(
        source_currency=source_currency,
        target_currency=target_currency,
        rate=quantize_rate(rate),
        entered_at=utcnow(),
        note=note,
        simulated=simulated,
    )
    session.add(entry)
    await session.flush()

    quote = RateQuote(
        provider="simulation" if simulated else MANUAL,
        source_currency=source_currency,
        target_currency=target_currency,
        rate=entry.rate,
        quote_type=QuoteType.SIMULATED if simulated else QuoteType.MANUAL,
        provider_timestamp=entry.entered_at,
        retrieved_at=entry.entered_at,
        latency_ms=0,
        metadata={"note": note, "simulated": simulated},
    )
    sample = await store_sample(session, quote)
    await audit.record(
        session,
        event_type=AuditEventType.CREATED,
        entity_type="rate",
        entity_id=str(sample.id),
        message=f"Manual rate {entry.rate} recorded for {source_currency}/{target_currency}",
        after={"rate": entry.rate, "simulated": simulated, "note": note},
        actor=actor,
    )
    return sample


async def latest_manual_rate(
    session: AsyncSession, source_currency: str, target_currency: str
) -> ManualRate | None:
    stmt = (
        select(ManualRate)
        .where(
            ManualRate.source_currency == source_currency,
            ManualRate.target_currency == target_currency,
        )
        .order_by(ManualRate.entered_at.desc(), ManualRate.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


# ---------------------------------------------------------------------------
# Provider health and backoff
# ---------------------------------------------------------------------------


async def _get_status(session: AsyncSession, provider: str) -> ProviderStatus:
    status = await session.get(ProviderStatus, provider)
    if status is None:
        status = ProviderStatus(provider=provider)
        session.add(status)
        await session.flush()
    return status


def _backoff_seconds(consecutive_failures: int, maximum: int) -> int:
    """Exponential backoff, capped, so a broken provider is not hammered."""
    return min(60 * (2 ** min(consecutive_failures - 1, 12)), maximum)


async def record_provider_success(
    session: AsyncSession, provider: str, latency_ms: int | None
) -> None:
    status = await _get_status(session, provider)
    recovered = status.consecutive_failures > 0
    status.healthy = True
    status.last_success_at = utcnow()
    status.consecutive_failures = 0
    status.failing_since = None
    status.last_error = None
    status.last_latency_ms = latency_ms
    status.retry_after = None
    if recovered:
        await audit.record(
            session,
            event_type=AuditEventType.PROVIDER_RECOVERED,
            entity_type="provider",
            entity_id=provider,
            message=f"Provider {provider} recovered",
        )


async def record_provider_failure(
    session: AsyncSession, provider: str, error: str, *, max_backoff: int
) -> ProviderStatus:
    status = await _get_status(session, provider)
    now = utcnow()
    status.healthy = False
    status.last_failure_at = now
    status.failing_since = status.failing_since or now
    status.consecutive_failures += 1
    status.last_error = error[:500]
    status.retry_after = now + timedelta(
        seconds=_backoff_seconds(status.consecutive_failures, max_backoff)
    )
    if status.consecutive_failures == 1:
        await audit.record(
            session,
            event_type=AuditEventType.PROVIDER_ERROR,
            entity_type="provider",
            entity_id=provider,
            message=f"Provider {provider} failed: {error[:200]}",
        )
    return status


async def provider_statuses(session: AsyncSession) -> list[ProviderStatus]:
    return list((await session.execute(select(ProviderStatus))).scalars().all())


# ---------------------------------------------------------------------------
# Refreshing
# ---------------------------------------------------------------------------


def relative_difference(first: Decimal, second: Decimal) -> Decimal:
    """Absolute difference between two rates as a proportion of the smaller."""
    base = min(abs(first), abs(second))
    if base == ZERO:
        return ZERO
    difference = safe_divide(abs(first - second), base)
    return difference if difference is not None else ZERO


async def refresh_rate(
    session: AsyncSession,
    settings: Settings,
    registry: ProviderRegistry,
    *,
    respect_backoff: bool = True,
    actor: str = "scheduler",
) -> RefreshOutcome:
    """Poll the provider chain and store the first usable answer.

    Every provider tried is recorded, successes and failures alike. When all of
    them fail the outcome says so; no stale value is quietly substituted for a
    fresh one.
    """
    general = settings.general
    source, target = general.source_currency, general.target_currency
    outcome = RefreshOutcome()
    now = utcnow()

    for name in registry.chain():
        outcome.attempted.append(name)
        if respect_backoff:
            status = await session.get(ProviderStatus, name)
            if status and status.retry_after and status.retry_after > now:
                outcome.errors[name] = (
                    f"skipped: backing off until {status.retry_after.isoformat()}"
                )
                continue
        try:
            provider = registry.create(name)
            quote = await provider.get_spot_rate(source, target)
        except ProviderError as exc:
            outcome.errors[name] = exc.message
            log.warning("provider_failed", provider=name, error=exc.message)
            await record_provider_failure(
                session, name, exc.message, max_backoff=settings.providers.max_backoff_seconds
            )
            continue
        except Exception as exc:  # defensive: an adapter bug must not stop polling
            message = f"unexpected {type(exc).__name__}: {exc}"
            outcome.errors[name] = message
            log.exception("provider_crashed", provider=name)
            await record_provider_failure(
                session, name, message, max_backoff=settings.providers.max_backoff_seconds
            )
            continue

        await record_provider_success(session, name, quote.latency_ms)
        sample = await store_sample(session, quote)
        outcome.quote = quote
        outcome.sample_id = sample.id
        outcome.used_provider = name
        break

    if outcome.succeeded:
        await _evaluate_disagreement(session, settings, registry, outcome)
    else:
        log.error("rate_refresh_failed", attempted=outcome.attempted, errors=outcome.errors)
        await audit.record(
            session,
            event_type=AuditEventType.PROVIDER_ERROR,
            entity_type="rate",
            message="Rate refresh failed for every configured provider",
            after={"attempted": outcome.attempted, "errors": outcome.errors},
            actor=actor,
        )
    return outcome


async def _evaluate_disagreement(
    session: AsyncSession,
    settings: Settings,
    registry: ProviderRegistry,
    outcome: RefreshOutcome,
) -> None:
    """Compare the primary and secondary providers when both are configured."""
    if settings.simulation.enabled and settings.simulation.force_disagreement:
        outcome.disagreement = settings.providers.disagreement_threshold * 2
        outcome.disagreement_exceeded = True
        outcome.comparison = {"simulation": "forced disagreement"}
        return

    names = [name for name in registry.comparison_pair() if name != outcome.used_provider]
    if not names or outcome.quote is None:
        return

    general = settings.general
    now = utcnow()
    for name in names:
        status = await session.get(ProviderStatus, name)
        if status and status.retry_after and status.retry_after > now:
            # A provider that is backing off is not woken up merely to be
            # compared against; the comparison is skipped for this cycle.
            outcome.comparison[name] = "skipped: backing off after a recent failure"
            continue
        try:
            provider = registry.create(name)
            other = await provider.get_spot_rate(general.source_currency, general.target_currency)
        except ProviderError as exc:
            outcome.comparison[name] = f"unavailable: {exc.message}"
            continue
        except Exception as exc:
            outcome.comparison[name] = f"unavailable: {type(exc).__name__}"
            continue

        outcome.comparison[name] = format(other.rate, "f")
        difference = (
            relative_difference(outcome.quote.rate, other.rate)
            if settings.providers.disagreement_is_relative
            else abs(outcome.quote.rate - other.rate)
        )
        if outcome.disagreement is None or difference > outcome.disagreement:
            outcome.disagreement = difference
        if difference > settings.providers.disagreement_threshold:
            outcome.disagreement_exceeded = True
            log.warning(
                "provider_disagreement",
                primary=outcome.used_provider,
                secondary=name,
                difference=format(difference, "f"),
                threshold=format(settings.providers.disagreement_threshold, "f"),
            )


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


async def latest_sample(
    session: AsyncSession, source_currency: str, target_currency: str
) -> RateSample | None:
    stmt = (
        select(RateSample)
        .where(
            RateSample.source_currency == source_currency,
            RateSample.target_currency == target_currency,
        )
        .order_by(RateSample.retrieved_at.desc(), RateSample.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def sample_at_or_before(
    session: AsyncSession, source_currency: str, target_currency: str, moment: datetime
) -> RateSample | None:
    stmt = (
        select(RateSample)
        .where(
            RateSample.source_currency == source_currency,
            RateSample.target_currency == target_currency,
            RateSample.retrieved_at <= moment,
        )
        .order_by(RateSample.retrieved_at.desc(), RateSample.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def extremes(
    session: AsyncSession,
    source_currency: str,
    target_currency: str,
    since: datetime,
) -> tuple[Decimal | None, Decimal | None]:
    """High and low over a window.

    The float companion column does the aggregation; the two matching samples
    are then re-read so the returned values are exact Decimals.
    """
    stmt = select(func.min(RateSample.rate_numeric), func.max(RateSample.rate_numeric)).where(
        RateSample.source_currency == source_currency,
        RateSample.target_currency == target_currency,
        RateSample.retrieved_at >= since,
        RateSample.is_stale.is_(False),
    )
    low_float, high_float = (await session.execute(stmt)).one()
    if low_float is None or high_float is None:
        return None, None

    async def exact(ascending: bool) -> Decimal | None:
        order = RateSample.rate_numeric.asc() if ascending else RateSample.rate_numeric.desc()
        row = (
            (
                await session.execute(
                    select(RateSample)
                    .where(
                        RateSample.source_currency == source_currency,
                        RateSample.target_currency == target_currency,
                        RateSample.retrieved_at >= since,
                        RateSample.is_stale.is_(False),
                    )
                    .order_by(order)
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )
        return row.rate if row else None

    return await exact(True), await exact(False)


async def change_over(
    session: AsyncSession,
    source_currency: str,
    target_currency: str,
    window: timedelta,
    current: Decimal | None,
) -> Decimal | None:
    """Rate change over a window, or ``None`` when there is no earlier sample."""
    if current is None:
        return None
    earlier = await sample_at_or_before(
        session, source_currency, target_currency, utcnow() - window
    )
    if earlier is None:
        return None
    return quantize_rate(current - earlier.rate)


async def current_rate(
    session: AsyncSession, settings: Settings, *, disagreement_warning: str | None = None
) -> CurrentRate:
    """Assemble everything the dashboard header needs."""
    source = settings.general.source_currency
    target = settings.general.target_currency
    stale_after = settings.providers.stale_after_seconds

    sample = await latest_sample(session, source, target)
    age_seconds: int | None = None
    is_stale = True
    if sample is not None:
        age_seconds = max(int((utcnow() - sample.retrieved_at).total_seconds()), 0)
        is_stale = sample.is_stale or age_seconds > stale_after

    changes = {
        label: await change_over(session, source, target, window, sample.rate if sample else None)
        for label, window in CHANGE_WINDOWS.items()
    }
    high_24h, low_24h = await extremes(session, source, target, utcnow() - timedelta(hours=24))
    high_6m, low_6m = await extremes(session, source, target, utcnow() - timedelta(days=182))

    return CurrentRate(
        sample=sample,
        is_stale=is_stale,
        age_seconds=age_seconds,
        stale_after_seconds=stale_after,
        provider=sample.provider if sample else "",
        changes=changes,
        high_24h=high_24h,
        low_24h=low_24h,
        high_6m=high_6m,
        low_6m=low_6m,
        disagreement_warning=disagreement_warning,
    )


async def history(
    session: AsyncSession,
    source_currency: str,
    target_currency: str,
    start: datetime,
    end: datetime,
    *,
    limit: int = 5000,
) -> list[RateSample]:
    """Raw samples in a window, oldest first."""
    stmt = (
        select(RateSample)
        .where(
            RateSample.source_currency == source_currency,
            RateSample.target_currency == target_currency,
            RateSample.retrieved_at >= start,
            RateSample.retrieved_at <= end,
        )
        .order_by(RateSample.retrieved_at.asc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars().all())


async def aggregates(
    session: AsyncSession,
    source_currency: str,
    target_currency: str,
    bucket: str,
    start: datetime,
    end: datetime,
) -> list[RateAggregate]:
    stmt = (
        select(RateAggregate)
        .where(
            RateAggregate.source_currency == source_currency,
            RateAggregate.target_currency == target_currency,
            RateAggregate.bucket == bucket,
            RateAggregate.bucket_start >= start,
            RateAggregate.bucket_start <= end,
        )
        .order_by(RateAggregate.bucket_start.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


# ---------------------------------------------------------------------------
# Aggregation and retention
# ---------------------------------------------------------------------------


def _bucket_start(moment: datetime, bucket: str) -> datetime:
    moment = moment.astimezone(UTC)
    if bucket == "hour":
        return moment.replace(minute=0, second=0, microsecond=0)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


async def build_aggregates(
    session: AsyncSession,
    source_currency: str,
    target_currency: str,
    *,
    bucket: str,
    start: datetime,
    end: datetime,
) -> int:
    """Roll raw samples into hourly or daily buckets.

    Always run before deleting raw samples, so long-range charts keep working
    after retention trims the fine-grained history.
    """
    samples = await history(session, source_currency, target_currency, start, end, limit=200_000)
    grouped: dict[datetime, list[RateSample]] = {}
    for sample in samples:
        if sample.is_stale:
            continue
        grouped.setdefault(_bucket_start(sample.retrieved_at, bucket), []).append(sample)

    written = 0
    for bucket_start, rows in sorted(grouped.items()):
        rates = [row.rate for row in rows]
        total = sum(rates, ZERO)
        average = safe_divide(total, Decimal(len(rates))) or ZERO
        existing = (
            (
                await session.execute(
                    select(RateAggregate).where(
                        RateAggregate.source_currency == source_currency,
                        RateAggregate.target_currency == target_currency,
                        RateAggregate.bucket == bucket,
                        RateAggregate.bucket_start == bucket_start,
                    )
                )
            )
            .scalars()
            .first()
        )
        values = {
            "open_rate": rates[0],
            "high_rate": max(rates),
            "low_rate": min(rates),
            "close_rate": rates[-1],
            "average_rate": quantize_rate(average),
            "sample_count": len(rates),
        }
        if existing is None:
            session.add(
                RateAggregate(
                    source_currency=source_currency,
                    target_currency=target_currency,
                    bucket=bucket,
                    bucket_start=bucket_start,
                    **values,
                )
            )
        else:
            for key, value in values.items():
                setattr(existing, key, value)
        written += 1
    await session.flush()
    return written


async def purge_old_samples(
    session: AsyncSession,
    source_currency: str,
    target_currency: str,
    *,
    older_than: datetime,
    actor: str = "scheduler",
) -> int:
    """Delete raw samples after aggregating them.

    Aggregates are built first, unconditionally, so purging can never destroy
    the only copy of a period's history.
    """
    await build_aggregates(
        session,
        source_currency,
        target_currency,
        bucket="hour",
        start=datetime(1970, 1, 1, tzinfo=UTC),
        end=older_than,
    )
    await build_aggregates(
        session,
        source_currency,
        target_currency,
        bucket="day",
        start=datetime(1970, 1, 1, tzinfo=UTC),
        end=older_than,
    )

    doomed = (
        (
            await session.execute(
                select(RateSample.id).where(
                    RateSample.source_currency == source_currency,
                    RateSample.target_currency == target_currency,
                    RateSample.retrieved_at < older_than,
                )
            )
        )
        .scalars()
        .all()
    )
    if not doomed:
        return 0

    from sqlalchemy import delete

    await session.execute(delete(RateSample).where(RateSample.id.in_(doomed)))
    await audit.record(
        session,
        event_type=AuditEventType.PURGED,
        entity_type="rate",
        message=(
            f"Purged {len(doomed)} raw rate samples older than "
            f"{older_than.date().isoformat()} after building aggregates"
        ),
        actor=actor,
    )
    return len(doomed)


async def import_points(
    session: AsyncSession,
    points: Sequence[RatePoint],
    source_currency: str,
    target_currency: str,
    *,
    quote_type: QuoteType = QuoteType.MID_MARKET,
    actor: str = "user",
) -> int:
    """Insert historical points, skipping ones already present.

    De-duplication is on (provider, timestamp) so re-running an import is safe.
    """
    if not points:
        return 0

    earliest = min(point.timestamp for point in points)
    latest = max(point.timestamp for point in points)
    existing = {
        (row.provider, row.retrieved_at)
        for row in await history(
            session, source_currency, target_currency, earliest, latest, limit=200_000
        )
    }

    inserted = 0
    for point in points:
        if (point.provider, point.timestamp) in existing:
            continue
        session.add(
            RateSample(
                provider=point.provider,
                source_currency=source_currency,
                target_currency=target_currency,
                rate=point.rate,
                rate_numeric=float(point.rate),
                quote_type=str(quote_type),
                provider_timestamp=point.timestamp,
                retrieved_at=point.timestamp,
                is_stale=False,
            )
        )
        existing.add((point.provider, point.timestamp))
        inserted += 1

    await session.flush()
    if inserted:
        await audit.record(
            session,
            event_type=AuditEventType.IMPORTED,
            entity_type="rate",
            message=f"Imported {inserted} historical rate points",
            after={"count": inserted, "from": earliest, "to": latest},
            actor=actor,
        )
    return inserted
