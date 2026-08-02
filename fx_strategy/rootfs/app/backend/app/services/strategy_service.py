"""Strategy and tranche persistence, plus the dashboard summary.

The rules live in :mod:`app.services.calculations`; this module supplies them
with database state and writes the results back.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.logging_setup import get_logger
from app.models.audit import AuditEventType
from app.models.rate import FeeModel
from app.models.strategy import (
    Conversion,
    DeadlineRequirement,
    Strategy,
    StrategyStatus,
    Tranche,
    TrancheStatus,
)
from app.money import ZERO, quantize_money, quantize_percent, quantize_rate, safe_divide
from app.schemas.settings import Settings
from app.schemas.strategy import RequirementIn, StrategyIn, TrancheIn
from app.services import audit
from app.services import calculations as calc
from app.services.calculations import (
    AllocationRequest,
    AllocationType,
    CompletedConversion,
    FeeAssumption,
    FeeType,
    RateZone,
)

log = get_logger(__name__)


class StrategyError(ValueError):
    """A strategy rule was violated."""


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def recommended_ladder() -> list[TrancheIn]:
    """The template from the product specification.

    15% at 1.7200, 20% at 1.7400, 25% at 1.7600, 20% at 1.7800, 20% at 1.8000.
    A starting point to edit, not advice.
    """
    steps = [
        ("15", "1.7200"),
        ("20", "1.7400"),
        ("25", "1.7600"),
        ("20", "1.7800"),
        ("20", "1.8000"),
    ]
    return [
        TrancheIn(
            sequence=index + 1,
            name=f"Tranche {index + 1}",
            allocation_type="percentage",
            allocation_value=Decimal(percentage),
            target_rate=Decimal(rate),
        )
        for index, (percentage, rate) in enumerate(steps)
    ]


def equal_ladder(count: int, start: Decimal, step: Decimal) -> list[TrancheIn]:
    """Equal tranches at evenly spaced targets."""
    if count < 1:
        raise StrategyError("A ladder needs at least one tranche.")
    share = quantize_money(Decimal(100) / Decimal(count))
    tranches = []
    for index in range(count):
        value = (
            share
            if index < count - 1
            else quantize_money(Decimal(100) - share * Decimal(count - 1))
        )
        tranches.append(
            TrancheIn(
                sequence=index + 1,
                name=f"Tranche {index + 1}",
                allocation_type="percentage",
                allocation_value=value,
                target_rate=quantize_rate(start + step * index),
            )
        )
    return tranches


def templates(current_rate: Decimal | None) -> list[dict[str, Any]]:
    """Templates offered by the setup wizard."""
    base = current_rate or Decimal("1.7200")
    return [
        {
            "key": "recommended",
            "name": "Recommended staged ladder",
            "description": "15% at 1.7200, 20% at 1.7400, 25% at 1.7600, 20% at 1.7800, "
            "20% at 1.8000. Edit every figure to suit.",
            "tranches": recommended_ladder(),
        },
        {
            "key": "equal",
            "name": "Equal tranches",
            "description": "Five equal tranches, spaced two cents apart from today's rate.",
            "tranches": equal_ladder(5, quantize_rate(base), Decimal("0.0200")),
        },
        {
            "key": "monitor_only",
            "name": "Monitor only",
            "description": "No tranches. Watch the rate and record conversions as you make them.",
            "tranches": [],
        },
    ]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


async def get_strategy(session: AsyncSession, strategy_id: int) -> Strategy | None:
    return await session.get(Strategy, strategy_id)


async def require_strategy(session: AsyncSession, strategy_id: int) -> Strategy:
    strategy = await get_strategy(session, strategy_id)
    if strategy is None:
        raise StrategyError(f"Strategy {strategy_id} does not exist.")
    return strategy


async def list_strategies(
    session: AsyncSession, *, include_archived: bool = False
) -> list[Strategy]:
    stmt = select(Strategy).order_by(Strategy.created_at.desc())
    if not include_archived:
        stmt = stmt.where(Strategy.status != str(StrategyStatus.ARCHIVED))
    return list((await session.execute(stmt)).scalars().all())


async def active_strategy(session: AsyncSession, settings: Settings) -> Strategy | None:
    """The strategy the dashboard and entities follow.

    Preference order: the one named in settings, then the single active one,
    then the most recently updated.
    """
    chosen_id = settings.general.active_strategy_id
    if chosen_id is not None:
        strategy = await get_strategy(session, chosen_id)
        if strategy is not None and strategy.status != str(StrategyStatus.ARCHIVED):
            return strategy

    stmt = (
        select(Strategy)
        .where(Strategy.status == str(StrategyStatus.ACTIVE))
        .order_by(Strategy.updated_at.desc())
        .limit(1)
    )
    strategy = (await session.execute(stmt)).scalars().first()
    if strategy is not None:
        return strategy

    stmt = (
        select(Strategy)
        .where(Strategy.status != str(StrategyStatus.ARCHIVED))
        .order_by(Strategy.updated_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def monitored_strategies(session: AsyncSession) -> list[Strategy]:
    """Strategies whose targets the scheduler should evaluate."""
    stmt = select(Strategy).where(
        Strategy.status.in_([str(status) for status in (StrategyStatus.ACTIVE,)])
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_fee_model(session: AsyncSession, fee_model_id: int | None) -> FeeModel | None:
    if fee_model_id is None:
        return None
    return await session.get(FeeModel, fee_model_id)


def fee_assumption_from(model: FeeModel | None) -> FeeAssumption | None:
    if model is None:
        return None
    return FeeAssumption(
        fee_type=FeeType(model.fee_type),
        fixed_fee=model.fixed_fee,
        percentage_fee=model.percentage_fee,
        minimum_fee=model.minimum_fee,
        maximum_fee=model.maximum_fee,
        currency=model.currency,
        name=model.name,
    )


def zones_from(settings: Settings) -> list[RateZone]:
    if not settings.zones.enabled:
        return []
    return [
        RateZone(label=zone.label, guidance=zone.guidance, lower_bound=zone.lower_bound)
        for zone in settings.zones.zones
    ]


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------


def allocation_requests(tranches: Sequence[Tranche | TrancheIn]) -> list[AllocationRequest]:
    return [
        AllocationRequest(
            sequence=tranche.sequence,
            allocation_type=AllocationType(tranche.allocation_type),
            allocation_value=tranche.allocation_value,
        )
        for tranche in tranches
    ]


def recalculate_allocations(strategy: Strategy) -> calc.AllocationReport:
    """Recompute every tranche amount from its allocation rule.

    Called after any change to the total or to a tranche, so a stored
    ``calculated_source_amount`` is never left disagreeing with its rule.
    """
    report = calc.allocate(strategy.initial_source_amount, allocation_requests(strategy.tranches))
    by_sequence = {result.sequence: result.source_amount for result in report.allocations}
    for tranche in strategy.tranches:
        tranche.calculated_source_amount = by_sequence.get(tranche.sequence, ZERO)
    return report


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _apply_tranche(tranche: Tranche, payload: TrancheIn) -> None:
    tranche.sequence = payload.sequence
    tranche.name = payload.name or f"Tranche {payload.sequence}"
    tranche.allocation_type = payload.allocation_type
    tranche.allocation_value = payload.allocation_value
    tranche.target_rate = payload.target_rate
    tranche.minimum_rate = payload.minimum_rate
    tranche.deadline = payload.deadline
    tranche.intended_for_auto_conversion = payload.intended_for_auto_conversion
    tranche.notifications_enabled = payload.notifications_enabled
    tranche.wise_auto_conversion_reference = payload.wise_auto_conversion_reference


def _snapshot(strategy: Strategy) -> dict[str, Any]:
    """A comparable view of a strategy, for the audit trail."""
    return {
        "name": strategy.name,
        "status": strategy.status,
        "initial_source_amount": strategy.initial_source_amount,
        "funds_available_amount": strategy.funds_available_amount,
        "final_deadline": strategy.final_deadline,
        "walk_away_rate": strategy.walk_away_rate,
        "minimum_acceptable_rate": strategy.minimum_acceptable_rate,
        "fee_model_id": strategy.fee_model_id,
        "tranches": [
            {
                "sequence": tranche.sequence,
                "allocation_type": tranche.allocation_type,
                "allocation_value": tranche.allocation_value,
                "target_rate": tranche.target_rate,
                "calculated_source_amount": tranche.calculated_source_amount,
                "status": tranche.status,
            }
            for tranche in sorted(strategy.tranches, key=lambda item: item.sequence)
        ],
    }


async def create_strategy(
    session: AsyncSession, payload: StrategyIn, *, actor: str = "user"
) -> Strategy:
    strategy = Strategy(
        # The collections are passed explicitly so they are initialised rather
        # than left to a lazy load, which would need IO at serialisation time.
        tranches=[],
        requirements=[],
        conversions=[],
        name=payload.name,
        status=str(
            StrategyStatus.WAITING_FOR_FUNDS
            if payload.funds_available_amount <= ZERO
            else StrategyStatus.DRAFT
        ),
        source_currency=payload.source_currency,
        target_currency=payload.target_currency,
        initial_source_amount=payload.initial_source_amount,
        funds_available_amount=payload.funds_available_amount,
        funds_arrival_date=payload.funds_arrival_date,
        strategy_start_date=payload.strategy_start_date or utcnow(),
        final_deadline=payload.final_deadline,
        minimum_acceptable_rate=payload.minimum_acceptable_rate,
        walk_away_rate=payload.walk_away_rate,
        require_targets_in_order=payload.require_targets_in_order,
        fee_model_id=payload.fee_model_id,
        rate_provider_id=payload.rate_provider_id,
        timezone=payload.timezone,
        notes=payload.notes,
    )
    for item in payload.tranches:
        # Appending to the collection is what associates the tranche; passing
        # `strategy=` as well would add it twice.
        tranche = Tranche(target_rate=item.target_rate, allocation_type=item.allocation_type)
        _apply_tranche(tranche, item)
        strategy.tranches.append(tranche)
    for requirement in payload.requirements:
        strategy.requirements.append(_requirement_model(requirement))

    session.add(strategy)
    await session.flush()
    recalculate_allocations(strategy)
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.CREATED,
        entity_type="strategy",
        entity_id=strategy.id,
        message=f"Strategy '{strategy.name}' created",
        after=_snapshot(strategy),
        actor=actor,
    )
    return strategy


def _requirement_model(payload: RequirementIn) -> DeadlineRequirement:
    return DeadlineRequirement(
        due_date=payload.due_date,
        required_source_amount=payload.required_source_amount,
        required_percentage=payload.required_percentage,
        description=payload.description,
    )


async def update_strategy(
    session: AsyncSession, strategy: Strategy, payload: StrategyIn, *, actor: str = "user"
) -> Strategy:
    """Replace a strategy's definition.

    Tranche identity is kept by sequence number so per-tranche alert state and
    recorded conversions survive an edit.
    """
    before = _snapshot(strategy)

    strategy.name = payload.name
    strategy.source_currency = payload.source_currency
    strategy.target_currency = payload.target_currency
    strategy.initial_source_amount = payload.initial_source_amount
    strategy.funds_available_amount = payload.funds_available_amount
    strategy.funds_arrival_date = payload.funds_arrival_date
    strategy.strategy_start_date = payload.strategy_start_date or strategy.strategy_start_date
    strategy.final_deadline = payload.final_deadline
    strategy.minimum_acceptable_rate = payload.minimum_acceptable_rate
    strategy.walk_away_rate = payload.walk_away_rate
    strategy.require_targets_in_order = payload.require_targets_in_order
    strategy.fee_model_id = payload.fee_model_id
    strategy.rate_provider_id = payload.rate_provider_id
    strategy.timezone = payload.timezone
    strategy.notes = payload.notes

    existing = {tranche.sequence: tranche for tranche in strategy.tranches}
    wanted = {item.sequence for item in payload.tranches}

    for sequence, tranche in list(existing.items()):
        if sequence not in wanted:
            strategy.tranches.remove(tranche)

    for item in payload.tranches:
        found = existing.get(item.sequence)
        if found is None:
            tranche = Tranche(target_rate=item.target_rate, allocation_type=item.allocation_type)
            strategy.tranches.append(tranche)
        else:
            tranche = found
        if found is not None and found.target_rate != item.target_rate:
            # Moving a target resets its alert state; otherwise a raised target
            # would stay flagged as reached from the old level.
            tranche.target_first_reached_at = None
            tranche.notification_sent_at = None
            tranche.acknowledged_at = None
            if tranche.status in (str(TrancheStatus.TARGET_REACHED), str(TrancheStatus.ARMED)):
                tranche.status = str(TrancheStatus.PENDING)
        _apply_tranche(tranche, item)

    strategy.requirements.clear()
    for requirement in payload.requirements:
        strategy.requirements.append(_requirement_model(requirement))

    await session.flush()
    recalculate_allocations(strategy)
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type="strategy",
        entity_id=strategy.id,
        message=f"Strategy '{strategy.name}' updated",
        before=before,
        after=_snapshot(strategy),
        actor=actor,
    )
    return strategy


async def set_status(
    session: AsyncSession,
    strategy: Strategy,
    status: StrategyStatus,
    *,
    actor: str = "user",
    event: AuditEventType = AuditEventType.UPDATED,
) -> Strategy:
    previous = strategy.status
    strategy.status = str(status)
    if status is StrategyStatus.COMPLETED:
        strategy.completed_at = utcnow()
    if status is StrategyStatus.ARCHIVED:
        strategy.archived_at = utcnow()
    await session.flush()
    await audit.record(
        session,
        event_type=event,
        entity_type="strategy",
        entity_id=strategy.id,
        message=f"Strategy '{strategy.name}' moved from {previous} to {status}",
        before={"status": previous},
        after={"status": str(status)},
        actor=actor,
    )
    return strategy


async def activate(session: AsyncSession, strategy: Strategy, *, actor: str = "user") -> Strategy:
    """Start monitoring, refusing to activate an invalid allocation."""
    report = recalculate_allocations(strategy)
    if not report.valid:
        raise StrategyError("; ".join(report.errors))
    if not strategy.tranches:
        raise StrategyError(
            "A strategy needs at least one tranche before it can be activated. "
            "Use the monitor-only template if you only want to watch the rate."
        )
    return await set_status(
        session, strategy, StrategyStatus.ACTIVE, actor=actor, event=AuditEventType.ACTIVATED
    )


async def duplicate(
    session: AsyncSession, strategy: Strategy, *, name: str | None = None, actor: str = "user"
) -> Strategy:
    """Copy a strategy's definition, without its history.

    Conversions and alert state belong to the original; carrying them over would
    make the copy claim results it never produced.
    """
    copy = Strategy(
        tranches=[],
        requirements=[],
        conversions=[],
        name=name or f"{strategy.name} (copy)",
        status=str(StrategyStatus.DRAFT),
        source_currency=strategy.source_currency,
        target_currency=strategy.target_currency,
        initial_source_amount=strategy.initial_source_amount,
        funds_available_amount=strategy.funds_available_amount,
        funds_arrival_date=strategy.funds_arrival_date,
        strategy_start_date=utcnow(),
        final_deadline=strategy.final_deadline,
        minimum_acceptable_rate=strategy.minimum_acceptable_rate,
        walk_away_rate=strategy.walk_away_rate,
        require_targets_in_order=strategy.require_targets_in_order,
        fee_model_id=strategy.fee_model_id,
        rate_provider_id=strategy.rate_provider_id,
        timezone=strategy.timezone,
        notes=strategy.notes,
    )
    for tranche in strategy.tranches:
        copy.tranches.append(
            Tranche(
                sequence=tranche.sequence,
                name=tranche.name,
                allocation_type=tranche.allocation_type,
                allocation_value=tranche.allocation_value,
                calculated_source_amount=tranche.calculated_source_amount,
                target_rate=tranche.target_rate,
                minimum_rate=tranche.minimum_rate,
                deadline=tranche.deadline,
                intended_for_auto_conversion=tranche.intended_for_auto_conversion,
                notifications_enabled=tranche.notifications_enabled,
            )
        )
    for requirement in strategy.requirements:
        copy.requirements.append(
            DeadlineRequirement(
                due_date=requirement.due_date,
                required_source_amount=requirement.required_source_amount,
                required_percentage=requirement.required_percentage,
                description=requirement.description,
            )
        )
    session.add(copy)
    await session.flush()
    recalculate_allocations(copy)
    await session.flush()
    await audit.record(
        session,
        event_type=AuditEventType.CREATED,
        entity_type="strategy",
        entity_id=copy.id,
        message=f"Strategy '{copy.name}' duplicated from strategy {strategy.id}",
        after=_snapshot(copy),
        actor=actor,
    )
    return copy


async def delete_strategy(
    session: AsyncSession, strategy: Strategy, *, actor: str = "user"
) -> None:
    """Archive rather than delete when the strategy has financial history.

    Recorded conversions are financial records; removing them silently would
    destroy the audit trail this application exists to keep.
    """
    if strategy.conversions:
        await set_status(session, strategy, StrategyStatus.ARCHIVED, actor=actor)
        return
    snapshot = _snapshot(strategy)
    strategy_id = strategy.id
    await session.delete(strategy)
    await session.flush()
    await audit.record(
        session,
        event_type=AuditEventType.DELETED,
        entity_type="strategy",
        entity_id=strategy_id,
        message=f"Strategy '{snapshot['name']}' deleted (it had no recorded conversions)",
        before=snapshot,
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Derived state
# ---------------------------------------------------------------------------


def completed_conversions(strategy: Strategy) -> list[CompletedConversion]:
    """Real conversions, excluding simulated ones."""
    return [
        CompletedConversion(
            source_amount=conversion.source_amount,
            target_amount=conversion.target_amount,
            gross_rate=conversion.gross_rate,
            fee_target_equivalent=conversion.fee_total_target_equivalent,
        )
        for conversion in strategy.conversions
        if not conversion.simulated
    ]


def converted_source_total(strategy: Strategy) -> Decimal:
    return quantize_money(
        sum((c.source_amount for c in strategy.conversions if not c.simulated), ZERO)
    )


def tranche_conversions(strategy: Strategy, tranche_id: int) -> list[Conversion]:
    return [
        conversion
        for conversion in strategy.conversions
        if conversion.tranche_id == tranche_id and not conversion.simulated
    ]


def remaining_amount(strategy: Strategy) -> Decimal:
    """Unconverted funds, based on what has actually arrived."""
    return calc.remaining_source_amount(
        strategy.funds_available_amount, converted_source_total(strategy)
    )


def open_tranches(strategy: Strategy) -> list[Tranche]:
    return sorted(
        (tranche for tranche in strategy.tranches if tranche.is_open),
        key=lambda tranche: tranche.sequence,
    )


def next_target(strategy: Strategy, current_rate: Decimal | None) -> Tranche | None:
    """The next target to watch.

    With ordered targets it is simply the lowest open sequence. Otherwise it is
    the open tranche whose target is nearest above the current rate; if every
    target is already below the rate, the lowest open target is returned, since
    that is the one that would trigger first.
    """
    candidates = open_tranches(strategy)
    if not candidates:
        return None
    if strategy.require_targets_in_order:
        return candidates[0]
    if current_rate is None:
        return min(candidates, key=lambda tranche: tranche.target_rate)
    above = [tranche for tranche in candidates if tranche.target_rate > current_rate]
    if above:
        return min(above, key=lambda tranche: tranche.target_rate)
    return min(candidates, key=lambda tranche: tranche.target_rate)


def days_until(moment: datetime | None) -> int | None:
    if moment is None:
        return None
    delta = moment - utcnow()
    # Ceiling, so "tomorrow" never reads as zero days remaining.
    return -((-delta.days * 86400 - delta.seconds) // 86400)


def average_fee_percentage(strategy: Strategy) -> Decimal | None:
    """Mean fee as a percentage of gross, over conversions that recorded one."""
    with_fees = [
        conversion
        for conversion in strategy.conversions
        if not conversion.simulated and conversion.fee_total_target_equivalent is not None
    ]
    if not with_fees:
        return None
    gross = sum((calc.gross_proceeds(c.source_amount, c.gross_rate) for c in with_fees), ZERO)
    fees = sum((c.fee_total_target_equivalent or ZERO for c in with_fees), ZERO)
    ratio = safe_divide(fees * Decimal(100), gross)
    return quantize_percent(ratio) if ratio is not None else None


def requirement_amount(strategy: Strategy, requirement: DeadlineRequirement) -> Decimal:
    """Resolve a requirement to an absolute amount."""
    if requirement.required_source_amount is not None:
        return requirement.required_source_amount
    if requirement.required_percentage is not None:
        return quantize_money(
            strategy.initial_source_amount * requirement.required_percentage / Decimal(100)
        )
    return ZERO
