"""Background jobs.

The scheduler owns exactly three responsibilities: poll the rate, keep the
derived state (alerts, published entities) in step with it, and perform
housekeeping.  Every job opens its own session and commits or rolls back on its
own, so one failing job cannot poison another.
"""

from __future__ import annotations

import random
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import checkpoint_wal, get_sessionmaker, utcnow
from app.logging_setup import get_logger
from app.providers.registry import ProviderRegistry
from app.schemas.settings import Settings
from app.services import rate_service, settings_service
from app.services.audit import new_correlation_id

log = get_logger(__name__)

#: A post-refresh consumer. Its return value is ignored; the scheduler only
#: needs to know that it completed.
JobCallback = Callable[[AsyncSession, Settings, rate_service.RefreshOutcome], Awaitable[Any]]


def is_market_active(settings: Settings) -> bool:
    """Whether the FX market is in its active window.

    Deliberately coarse: weekday in UTC. The purpose is only to poll less often
    over the weekend, not to model exchange sessions.
    """
    return utcnow().weekday() in set(settings.providers.market_active_weekdays)


def poll_interval_seconds(settings: Settings) -> int:
    providers = settings.providers
    base = (
        providers.poll_seconds_active if is_market_active(settings) else providers.poll_seconds_idle
    )
    return max(base, providers.poll_seconds_minimum)


async def build_registry(session: AsyncSession, settings: Settings) -> ProviderRegistry:
    """Create a registry primed with the latest manual rate."""
    manual = await rate_service.latest_manual_rate(
        session, settings.general.source_currency, settings.general.target_currency
    )
    return ProviderRegistry(
        settings,
        manual_rate=manual.rate if manual else None,
        manual_entered_at=manual.entered_at if manual else None,
    )


class RateScheduler:
    """Owns the APScheduler instance and the jobs registered on it."""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._callbacks: list[JobCallback] = []
        self._last_outcome: rate_service.RefreshOutcome | None = None
        self._last_run_at: Any = None
        self._last_error: str | None = None
        self._started = False

    # -- lifecycle --------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._started and self._scheduler.running

    def on_refresh(self, callback: JobCallback) -> None:
        """Register work that must happen after each successful poll."""
        self._callbacks.append(callback)

    async def start(self) -> None:
        if self._started:
            return
        async with get_sessionmaker()() as session:
            settings = await settings_service.load_settings(session)

        interval = poll_interval_seconds(settings)
        jitter = settings.providers.jitter_seconds
        self._scheduler.add_job(
            self.run_refresh,
            IntervalTrigger(seconds=interval, jitter=jitter or None),
            id="rate_refresh",
            name="Refresh the exchange rate",
            max_instances=1,
            coalesce=True,
            # Stagger the first run so a Home Assistant restart does not fire
            # every add-on's outbound calls at the same instant.
            next_run_time=utcnow() + timedelta(seconds=random.uniform(2, 10)),  # noqa: S311
        )
        self._scheduler.add_job(
            self.run_housekeeping,
            IntervalTrigger(hours=6, jitter=600),
            id="housekeeping",
            name="Aggregate history and checkpoint the database",
            max_instances=1,
            coalesce=True,
        )
        self._scheduler.start()
        self._started = True
        log.info("scheduler_started", interval_seconds=interval, jitter_seconds=jitter)

    async def shutdown(self) -> None:
        if self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            log.info("scheduler_stopped")

    def reschedule(self, settings: Settings) -> None:
        """Apply a changed polling interval without restarting the process."""
        if not self._started:
            return
        interval = poll_interval_seconds(settings)
        self._scheduler.reschedule_job(
            "rate_refresh",
            trigger=IntervalTrigger(
                seconds=interval, jitter=settings.providers.jitter_seconds or None
            ),
        )
        log.info("scheduler_rescheduled", interval_seconds=interval)

    # -- jobs -------------------------------------------------------------

    async def run_refresh(self) -> rate_service.RefreshOutcome:
        """Poll for a rate and run the post-refresh callbacks."""
        new_correlation_id()
        async with get_sessionmaker()() as session:
            try:
                settings = await settings_service.load_settings(session)
                registry = await build_registry(session, settings)
                try:
                    outcome = await rate_service.refresh_rate(session, settings, registry)
                finally:
                    await registry.aclose()

                for callback in self._callbacks:
                    try:
                        await callback(session, settings, outcome)
                    except Exception:
                        # A failing consumer must not lose the rate sample that
                        # has already been collected.
                        log.exception("refresh_callback_failed", callback=callback.__name__)

                await session.commit()
                self._last_outcome = outcome
                self._last_run_at = utcnow()
                self._last_error = None if outcome.succeeded else str(outcome.errors)
                return outcome
            except Exception as exc:
                await session.rollback()
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.exception("refresh_job_failed")
                raise

    async def run_housekeeping(self) -> None:
        """Aggregate history, purge expired samples, checkpoint the WAL."""
        new_correlation_id()
        async with get_sessionmaker()() as session:
            try:
                settings = await settings_service.load_settings(session)
                source = settings.general.source_currency
                target = settings.general.target_currency
                now = utcnow()

                await rate_service.build_aggregates(
                    session,
                    source,
                    target,
                    bucket="hour",
                    start=now - timedelta(days=7),
                    end=now,
                )
                await rate_service.build_aggregates(
                    session,
                    source,
                    target,
                    bucket="day",
                    start=now - timedelta(days=400),
                    end=now,
                )
                purged = await rate_service.purge_old_samples(
                    session,
                    source,
                    target,
                    older_than=now - timedelta(days=settings.retention.fine_rate_days),
                )
                await session.commit()
                if purged:
                    log.info("retention_purge", samples=purged)
            except Exception:
                await session.rollback()
                log.exception("housekeeping_failed")
                return

        try:
            await checkpoint_wal()
        except Exception:  # pragma: no cover - best effort
            log.warning("wal_checkpoint_failed")

    # -- introspection ----------------------------------------------------

    def status(self) -> dict[str, Any]:
        jobs = []
        if self._started:
            for job in self._scheduler.get_jobs():
                jobs.append(
                    {
                        "id": job.id,
                        "name": job.name,
                        "next_run_at": (
                            job.next_run_time.isoformat() if job.next_run_time else None
                        ),
                    }
                )
        return {
            "running": self.running,
            "jobs": jobs,
            "last_run_at": self._last_run_at.isoformat() if self._last_run_at else None,
            "last_error": self._last_error,
            "last_provider": self._last_outcome.used_provider if self._last_outcome else None,
        }


_scheduler: RateScheduler | None = None


def get_scheduler() -> RateScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = RateScheduler()
    return _scheduler


def reset_scheduler() -> None:
    """Drop the singleton. Used by tests."""
    global _scheduler
    _scheduler = None
