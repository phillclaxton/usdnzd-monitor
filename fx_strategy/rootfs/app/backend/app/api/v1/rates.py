"""Rate endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import PlainTextResponse

from app.api.deps import ActorDep, SessionDep, SettingsDep
from app.api.errors import NotFoundError, ValidationError
from app.api.errors import ProviderError as ApiProviderError
from app.database import utcnow
from app.money import ZERO, quantize_rate, safe_divide
from app.providers.base import QUOTE_TYPE_LABEL, QuoteType, RatePoint
from app.scheduler.jobs import build_registry
from app.schemas.common import Message
from app.schemas.rates import (
    CurrentRateOut,
    ExcludeSamplesIn,
    ManualRateIn,
    ProviderStatusOut,
    RateChanges,
    RateHistoryOut,
    RateImportPreview,
    RatePointOut,
    RefreshOut,
    SampleListOut,
    SampleOut,
)
from app.services import csv_io, rate_service

router = APIRouter(prefix="/rates", tags=["rates"])

#: Uploaded CSV files above this size are refused before being read into memory.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024

RANGE_WINDOWS: dict[str, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "3m": timedelta(days=91),
    "6m": timedelta(days=182),
    "1y": timedelta(days=365),
}


@router.get("/current", response_model=CurrentRateOut, summary="The current rate")
async def get_current_rate(session: SessionDep, settings: SettingsDep) -> CurrentRateOut:
    current = await rate_service.current_rate(session, settings)
    sample = current.sample
    quote_type = QuoteType(sample.quote_type) if sample else None
    return CurrentRateOut(
        source_currency=settings.general.source_currency,
        target_currency=settings.general.target_currency,
        rate=current.rate,
        status=current.status,
        provider=current.provider,
        quote_type=str(quote_type) if quote_type else None,
        quote_label=QUOTE_TYPE_LABEL[quote_type] if quote_type else None,
        provider_timestamp=sample.provider_timestamp if sample else None,
        retrieved_at=sample.retrieved_at if sample else None,
        age_seconds=current.age_seconds,
        stale_after_seconds=current.stale_after_seconds,
        changes=RateChanges(
            one_hour=current.changes.get("1h"),
            twenty_four_hours=current.changes.get("24h"),
            seven_days=current.changes.get("7d"),
            thirty_days=current.changes.get("30d"),
        ),
        high_24h=current.high_24h,
        low_24h=current.low_24h,
        high_6m=current.high_6m,
        low_6m=current.low_6m,
        disagreement_warning=current.disagreement_warning,
        message=(
            None if sample else "No rate has been collected yet. Refresh, or enter one manually."
        ),
    )


@router.get("/history", response_model=RateHistoryOut, summary="Historical rates")
async def get_history(
    session: SessionDep,
    settings: SettingsDep,
    range_key: str = Query(default="30d", alias="range"),
    start: datetime | None = None,
    end: datetime | None = None,
    max_points: int = Query(default=2000, ge=10, le=20000),
) -> RateHistoryOut:
    """Return a series, automatically choosing raw, hourly or daily resolution."""
    source = settings.general.source_currency
    target = settings.general.target_currency
    now = utcnow()

    if start is None or end is None:
        window = RANGE_WINDOWS.get(range_key)
        if window is None:
            raise ValidationError(
                f"Unknown range {range_key!r}. Use one of: {', '.join(RANGE_WINDOWS)}, "
                "or supply start and end."
            )
        window_start, window_end = now - window, now
    else:
        window_start = start if start.tzinfo else start.replace(tzinfo=UTC)
        window_end = end if end.tzinfo else end.replace(tzinfo=UTC)
        if window_start >= window_end:
            raise ValidationError("start must be before end.")

    span = window_end - window_start
    # Short ranges use raw samples; longer ones use the aggregates, which
    # survive retention purges.
    if span <= timedelta(days=8):
        resolution = "sample"
        samples = await rate_service.history(session, source, target, window_start, window_end)
        points = [
            RatePointOut(timestamp=row.retrieved_at, rate=row.rate, provider=row.provider)
            for row in samples
            if not row.is_stale
        ]
    else:
        resolution = "hour" if span <= timedelta(days=95) else "day"
        rows = await rate_service.aggregates(
            session, source, target, resolution, window_start, window_end
        )
        points = [
            RatePointOut(timestamp=row.bucket_start, rate=row.close_rate, provider="aggregate")
            for row in rows
        ]
        if not points:
            # Aggregates have not been built yet (a fresh install). Fall back to
            # raw samples rather than showing an empty chart.
            resolution = "sample"
            samples = await rate_service.history(session, source, target, window_start, window_end)
            points = [
                RatePointOut(timestamp=row.retrieved_at, rate=row.rate, provider=row.provider)
                for row in samples
                if not row.is_stale
            ]

    truncated = len(points) > max_points
    if truncated:
        # Even thinning keeps the first and last point so the range is honest.
        step = len(points) // max_points + 1
        points = points[::step] + points[-1:]

    rates = [point.rate for point in points]
    average = safe_divide(sum(rates, ZERO), Decimal(len(rates))) if rates else None

    return RateHistoryOut(
        source_currency=source,
        target_currency=target,
        start=window_start,
        end=window_end,
        resolution=resolution,
        points=points,
        high=max(rates) if rates else None,
        low=min(rates) if rates else None,
        average=quantize_rate(average) if average is not None else None,
        truncated=truncated,
    )


@router.post("/refresh", response_model=RefreshOut, summary="Refresh the rate now")
async def refresh(session: SessionDep, settings: SettingsDep, actor: ActorDep) -> RefreshOut:
    """Poll the provider chain immediately.

    A failure is reported as a failure: this endpoint never returns a stale rate
    dressed up as a fresh one.
    """
    registry = await build_registry(session, settings)
    try:
        outcome = await rate_service.refresh_rate(
            session, settings, registry, respect_backoff=False, actor=actor
        )
    finally:
        await registry.aclose()

    if not outcome.succeeded:
        raise ApiProviderError(
            "No configured rate provider returned a rate.",
            details={"attempted": outcome.attempted, "errors": outcome.errors},
        )

    return RefreshOut(
        succeeded=True,
        provider=outcome.used_provider,
        attempted=outcome.attempted,
        errors=outcome.errors,
        rate=outcome.quote.rate if outcome.quote else None,
        disagreement=outcome.disagreement,
        disagreement_exceeded=outcome.disagreement_exceeded,
        comparison=outcome.comparison,
        refused=outcome.refused,
    )


@router.post("/manual", response_model=RefreshOut, summary="Enter a rate manually")
async def set_manual_rate(
    payload: ManualRateIn, session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> RefreshOut:
    source = payload.source_currency or settings.general.source_currency
    target = payload.target_currency or settings.general.target_currency
    sample = await rate_service.record_manual_rate(
        session,
        source_currency=source,
        target_currency=target,
        rate=payload.rate,
        note=payload.note,
        simulated=payload.simulated or settings.simulation.enabled,
        actor=actor,
    )
    return RefreshOut(
        succeeded=True,
        provider=sample.provider,
        attempted=[sample.provider],
        errors={},
        rate=sample.rate,
    )


@router.post("/import", response_model=RateImportPreview, summary="Import rate history from CSV")
async def import_rates(
    session: SessionDep,
    settings: SettingsDep,
    actor: ActorDep,
    file: UploadFile = File(...),
    commit: bool = Query(default=False, description="Set true to write the rows"),
) -> RateImportPreview:
    """Validate a CSV file, and write it only when ``commit`` is set.

    The default is a dry run so the user sees exactly what would be imported —
    and what would be rejected — before anything touches the database.
    """
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationError(f"The file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("The file is not UTF-8 text.") from exc

    try:
        parsed = csv_io.parse_rate_csv(content)
    except csv_io.CsvFormatError as exc:
        raise ValidationError(str(exc)) from exc

    errors: list[dict[str, Any]] = [
        {"row": error.row_number, "message": error.message} for error in parsed.errors[:100]
    ]
    points = [
        RatePoint(timestamp=row["timestamp"], rate=row["rate"], provider=row["provider"])
        for row in parsed.rows
        if row["source_currency"] == settings.general.source_currency
        and row["target_currency"] == settings.general.target_currency
    ]
    mismatched = len(parsed.rows) - len(points)
    if mismatched:
        errors.append(
            {
                "row": 0,
                "message": (
                    f"{mismatched} row(s) are for a different currency pair and were skipped. "
                    f"This strategy uses {settings.general.source_currency}/"
                    f"{settings.general.target_currency}."
                ),
            }
        )

    imported = 0
    if commit and points:
        imported = await rate_service.import_points(
            session,
            points,
            settings.general.source_currency,
            settings.general.target_currency,
            actor=actor,
        )

    return RateImportPreview(
        total_rows=parsed.total_rows,
        accepted=len(points),
        rejected=len(parsed.errors) + mismatched,
        errors=errors,
        sample=[
            RatePointOut(timestamp=p.timestamp, rate=p.rate, provider=p.provider)
            for p in points[:10]
        ],
        imported=imported,
        committed=commit,
    )


@router.get("/export", summary="Export rate history as CSV")
async def export_rates(
    session: SessionDep,
    settings: SettingsDep,
    range_key: str = Query(default="30d", alias="range"),
) -> PlainTextResponse:
    """Download the visible range in the same format the importer accepts."""
    window = RANGE_WINDOWS.get(range_key)
    if window is None:
        raise ValidationError(
            f"Unknown range {range_key!r}. Use one of: {', '.join(RANGE_WINDOWS)}."
        )
    now = utcnow()
    samples = await rate_service.history(
        session,
        settings.general.source_currency,
        settings.general.target_currency,
        now - window,
        now,
        limit=200_000,
    )
    body = csv_io.write_csv(
        csv_io.RATE_REQUIRED_COLUMNS,
        [
            (
                row.retrieved_at,
                row.source_currency,
                row.target_currency,
                row.rate,
                row.provider,
            )
            for row in samples
        ],
    )
    filename = (
        f"fx-rates-{settings.general.source_currency}{settings.general.target_currency}"
        f"-{range_key}-{now.date().isoformat()}.csv"
    )
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Reviewing and excluding observations
# ---------------------------------------------------------------------------


@router.get("/samples", response_model=SampleListOut, summary="Stored observations")
async def list_samples(
    session: SessionDep,
    settings: SettingsDep,
    range_key: str = Query(default="7d", alias="range"),
    suspicious_only: bool = False,
    limit: int = Query(default=500, ge=1, le=5000),
) -> SampleListOut:
    """Raw samples in a window, each measured against its neighbours.

    Excluded samples are included here on purpose: this is the screen for
    seeing what was thrown out and putting it back if that was a mistake.
    """
    source = settings.general.source_currency
    target = settings.general.target_currency
    window = RANGE_WINDOWS.get(range_key)
    if window is None:
        raise ValidationError(
            f"Unknown range {range_key!r}. Use one of: {', '.join(RANGE_WINDOWS)}."
        )
    now = utcnow()
    samples = await rate_service.history(
        session, source, target, now - window, now, limit=20_000, include_excluded=True
    )
    reviews = rate_service.review_samples(samples)
    threshold = settings.providers.implausible_move_threshold

    rows: list[SampleOut] = []
    for review in reviews:
        sample = review.sample
        suspicious = review.deviation is not None and review.deviation > threshold
        rows.append(
            SampleOut(
                id=sample.id,
                timestamp=sample.retrieved_at,
                rate=sample.rate,
                provider=sample.provider,
                quote_type=sample.quote_type,
                excluded=sample.excluded_at is not None,
                excluded_reason=sample.excluded_reason,
                deviation=review.deviation,
                suspicious=suspicious,
            )
        )

    excluded_count = sum(1 for row in rows if row.excluded)
    suspicious_count = sum(1 for row in rows if row.suspicious and not row.excluded)
    total = len(rows)
    if suspicious_only:
        rows = [row for row in rows if row.suspicious or row.excluded]
    # Newest first: a point you have just noticed on the chart is at the top.
    rows.sort(key=lambda row: row.timestamp, reverse=True)

    return SampleListOut(
        samples=rows[:limit],
        threshold=threshold,
        total=total,
        excluded_count=excluded_count,
        suspicious_count=suspicious_count,
    )


@router.post("/samples/exclude", response_model=Message, summary="Exclude observations")
async def exclude_samples(
    payload: ExcludeSamplesIn, session: SessionDep, actor: ActorDep
) -> Message:
    """Stop these samples contributing to any figure.

    The rows are kept. What a provider actually returned is the evidence for
    why a wrong figure appeared, and it is what makes this reversible.
    """
    reason = payload.reason.strip() or "Excluded by hand from the rate history."
    excluded = 0
    for sample_id in payload.sample_ids:
        sample = await rate_service.get_sample(session, sample_id)
        if sample is None:
            raise NotFoundError(f"Rate sample {sample_id} does not exist.")
        if sample.excluded_at is None:
            await rate_service.exclude_sample(session, sample, reason=reason, actor=actor)
            excluded += 1
    return Message(
        message=(
            f"{excluded} rate sample(s) excluded. They are kept in the database and can be "
            "restored; no figure is calculated from them."
            if excluded
            else "Those samples were already excluded."
        )
    )


@router.post("/samples/restore", response_model=Message, summary="Restore observations")
async def restore_samples(
    payload: ExcludeSamplesIn, session: SessionDep, actor: ActorDep
) -> Message:
    restored = 0
    for sample_id in payload.sample_ids:
        sample = await rate_service.get_sample(session, sample_id)
        if sample is None:
            raise NotFoundError(f"Rate sample {sample_id} does not exist.")
        if sample.excluded_at is not None:
            await rate_service.restore_sample(session, sample, actor=actor)
            restored += 1
    return Message(message=f"{restored} rate sample(s) restored to use.")


@router.get("/providers", response_model=list[ProviderStatusOut], summary="Provider health")
async def provider_health(session: SessionDep, settings: SettingsDep) -> list[ProviderStatusOut]:
    # Primed with the stored manual rate: a registry built without it would
    # describe the manual provider as unconfigured even after one was entered.
    registry = await build_registry(session, settings)
    try:
        descriptions = {item.name: item for item in registry.describe()}
    finally:
        await registry.aclose()

    stored = {status.provider: status for status in await rate_service.provider_statuses(session)}
    result: list[ProviderStatusOut] = []
    for name, description in descriptions.items():
        status = stored.get(name)
        # A provider that is not configured has no failure state worth showing.
        # Its stored counters can only be historical: the chain stops at the
        # first success, so a fallback it never reaches can never record a
        # success to clear them, and a stale red badge would sit there for ever.
        unconfigured = not description.configured
        result.append(
            ProviderStatusOut(
                provider=name,
                display_name=description.display_name,
                configured=description.configured,
                healthy=bool(status.healthy) if status else description.configured,
                last_success_at=status.last_success_at if status else None,
                last_failure_at=None
                if unconfigured
                else (status.last_failure_at if status else None),
                consecutive_failures=0
                if unconfigured
                else (status.consecutive_failures if status else 0),
                last_error=None if unconfigured else (status.last_error if status else None),
                last_latency_ms=status.last_latency_ms if status else None,
                retry_after=None if unconfigured else (status.retry_after if status else None),
                reason=description.reason,
            )
        )
    return result
