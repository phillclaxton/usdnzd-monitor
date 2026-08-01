"""Simulation mode.

Simulation exists so the entire workflow — targets crossing, notifications,
deadline warnings, provider failure, conversion entry — can be exercised without
touching a live rate or a real account.

Every record produced in simulation is marked as simulated, and simulated
records are excluded from the real position, the blended rate and every exposure
figure.  The UI shows a permanent banner while it is on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.logging_setup import get_logger
from app.models.alert import NotificationLog, TrancheAlertState
from app.models.audit import AuditEventType
from app.models.rate import ManualRate, RateSample
from app.models.strategy import Conversion, Strategy
from app.providers.base import QuoteType, RatePoint
from app.schemas.settings import Settings
from app.services import audit, rate_service, settings_service

log = get_logger(__name__)

BANNER = "SIMULATION MODE: No live financial decisions should be based on this screen."

#: Providers whose samples belong to simulation rather than the real history.
SIMULATED_PROVIDERS = ("simulation",)


@dataclass(slots=True)
class SimulationStatus:
    enabled: bool
    banner: str
    simulated_rate: Decimal | None
    time_acceleration: int
    force_provider_error: bool
    force_disagreement: bool
    simulated_samples: int = 0
    simulated_conversions: int = 0
    replay_cursor: int = 0


@dataclass(slots=True)
class ReplayResult:
    """What a replay produced."""

    steps: int = 0
    samples_written: int = 0
    notifications: int = 0
    final_rate: Decimal | None = None
    events: list[str] = field(default_factory=list)


async def status(session: AsyncSession, settings: Settings) -> SimulationStatus:
    samples = (
        (
            await session.execute(
                select(RateSample).where(RateSample.provider.in_(SIMULATED_PROVIDERS))
            )
        )
        .scalars()
        .all()
    )
    conversions = (
        (await session.execute(select(Conversion).where(Conversion.simulated.is_(True))))
        .scalars()
        .all()
    )
    return SimulationStatus(
        enabled=settings.simulation.enabled,
        banner=BANNER,
        simulated_rate=settings.simulation.simulated_rate,
        time_acceleration=settings.simulation.time_acceleration,
        force_provider_error=settings.simulation.force_provider_error,
        force_disagreement=settings.simulation.force_disagreement,
        simulated_samples=len(samples),
        simulated_conversions=len(conversions),
        replay_cursor=settings.simulation.replay_cursor,
    )


async def configure(
    session: AsyncSession,
    settings: Settings,
    values: dict[str, object],
    *,
    actor: str = "user",
) -> Settings:
    """Change the simulation settings, auditing the switch."""
    was_enabled = settings.simulation.enabled
    updated = await settings_service.patch_section(session, "simulation", values, actor=actor)
    if updated.simulation.enabled != was_enabled:
        await audit.record(
            session,
            event_type=AuditEventType.SIMULATION,
            entity_type="settings",
            entity_id="simulation",
            message=(
                "Simulation mode enabled"
                if updated.simulation.enabled
                else "Simulation mode disabled"
            ),
            before={"enabled": was_enabled},
            after={"enabled": updated.simulation.enabled},
            actor=actor,
        )
    return updated


async def set_rate(
    session: AsyncSession,
    settings: Settings,
    rate: Decimal,
    *,
    actor: str = "user",
) -> RateSample:
    """Inject a simulated rate, recorded as simulated."""
    if not settings.simulation.enabled:
        raise ValueError("Simulation mode is not enabled.")
    await settings_service.patch_section(
        session, "simulation", {"simulated_rate": rate}, actor=actor
    )
    return await rate_service.record_manual_rate(
        session,
        source_currency=settings.general.source_currency,
        target_currency=settings.general.target_currency,
        rate=rate,
        note="Simulated rate",
        simulated=True,
        actor=actor,
    )


async def replay(
    session: AsyncSession,
    settings: Settings,
    rates: list[Decimal],
    *,
    seconds_between: int = 60,
    actor: str = "user",
) -> ReplayResult:
    """Play a sequence of rates through the whole pipeline.

    Samples are spaced by ``seconds_between`` of *simulated* time so the
    confirmation rules — which require samples a minimum distance apart — are
    exercised exactly as they would be in real running.
    """
    if not settings.simulation.enabled:
        raise ValueError("Simulation mode is not enabled.")

    from app.services import monitor
    from app.services.rate_service import RefreshOutcome

    result = ReplayResult()
    start = utcnow() - timedelta(seconds=seconds_between * len(rates))

    for index, rate in enumerate(rates):
        moment = start + timedelta(seconds=seconds_between * index)
        sample = RateSample(
            provider="simulation",
            source_currency=settings.general.source_currency,
            target_currency=settings.general.target_currency,
            rate=rate,
            rate_numeric=float(rate),
            quote_type=str(QuoteType.SIMULATED),
            provider_timestamp=moment,
            retrieved_at=moment,
        )
        session.add(sample)
        await session.flush()
        result.samples_written += 1
        result.final_rate = rate

        outcome = await monitor.run_after_refresh(
            session,
            settings,
            RefreshOutcome(
                used_provider="simulation",
                attempted=["simulation"],
                disagreement_exceeded=settings.simulation.force_disagreement,
            ),
        )
        result.notifications += outcome.notifications_delivered
        result.events.extend(outcome.suppressed)
        result.steps += 1

    await settings_service.patch_section(
        session,
        "simulation",
        {"replay_cursor": settings.simulation.replay_cursor + result.steps},
        actor=actor,
    )
    await audit.record(
        session,
        event_type=AuditEventType.SIMULATION,
        entity_type="simulation",
        message=(
            f"Replayed {result.steps} simulated rate(s), producing "
            f"{result.notifications} notification(s)."
        ),
        after={"final_rate": result.final_rate},
        actor=actor,
    )
    return result


async def reset(session: AsyncSession, *, actor: str = "user") -> dict[str, int]:
    """Delete every simulated record, leaving real data untouched.

    Alert state is cleared too, so a reset genuinely returns the app to a clean
    starting point rather than leaving targets flagged from a simulated run.
    """
    removed: dict[str, int] = {}

    simulated_samples = (
        (
            await session.execute(
                select(RateSample.id).where(RateSample.provider.in_(SIMULATED_PROVIDERS))
            )
        )
        .scalars()
        .all()
    )
    if simulated_samples:
        await session.execute(delete(RateSample).where(RateSample.id.in_(simulated_samples)))
    removed["rate_samples"] = len(simulated_samples)

    simulated_manual = (
        (await session.execute(select(ManualRate.id).where(ManualRate.simulated.is_(True))))
        .scalars()
        .all()
    )
    if simulated_manual:
        await session.execute(delete(ManualRate).where(ManualRate.id.in_(simulated_manual)))
    removed["manual_rates"] = len(simulated_manual)

    simulated_conversions = (
        (await session.execute(select(Conversion.id).where(Conversion.simulated.is_(True))))
        .scalars()
        .all()
    )
    if simulated_conversions:
        await session.execute(delete(Conversion).where(Conversion.id.in_(simulated_conversions)))
    removed["conversions"] = len(simulated_conversions)

    states = (await session.execute(select(TrancheAlertState))).scalars().all()
    for state in states:
        await session.delete(state)
    removed["alert_states"] = len(states)

    logs = (
        (
            await session.execute(
                select(NotificationLog).where(NotificationLog.entity_type == "test")
            )
        )
        .scalars()
        .all()
    )
    for entry in logs:
        await session.delete(entry)
    removed["test_notifications"] = len(logs)

    await session.flush()
    await audit.record(
        session,
        event_type=AuditEventType.SIMULATION,
        entity_type="simulation",
        message=(
            "Simulation reset: " + ", ".join(f"{count} {name}" for name, count in removed.items())
        ),
        after=removed,
        actor=actor,
    )
    log.info("simulation_reset", **removed)
    return removed


async def import_replay_points(
    session: AsyncSession,
    settings: Settings,
    points: list[RatePoint],
    *,
    actor: str = "user",
) -> int:
    """Load historical points as simulation material."""
    return await rate_service.import_points(
        session,
        [RatePoint(timestamp=p.timestamp, rate=p.rate, provider="simulation") for p in points],
        settings.general.source_currency,
        settings.general.target_currency,
        quote_type=QuoteType.SIMULATED,
        actor=actor,
    )


def simulated_data_present(strategy: Strategy) -> bool:
    return any(conversion.simulated for conversion in strategy.conversions)
