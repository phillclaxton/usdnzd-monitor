"""Assembling the dashboard summary and the scenario comparison.

This is the layer that turns database rows plus a current rate into the exact
payload the dashboard renders.  It contains no financial rules of its own: it
calls :mod:`app.services.calculations` for every figure.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.logging_setup import get_logger
from app.models.strategy import Strategy, Tranche
from app.money import ZERO, quantize_money, quantize_rate, safe_divide
from app.schemas.settings import Settings
from app.schemas.strategy import (
    ComparisonOut,
    FeeModelOut,
    FeeOut,
    OutcomeOut,
    RequirementOut,
    RequirementProgressOut,
    ScenarioLegOut,
    ScenarioOut,
    ScenariosOut,
    SensitivityOut,
    StrategyOut,
    StrategySummaryOut,
    TrancheOut,
    TrancheProgressOut,
    WalkAwayOut,
    ZoneOut,
)
from app.services import calculations as calc
from app.services import rate_service
from app.services import strategy_service as strategies

log = get_logger(__name__)


def _fee_out(estimate: calc.FeeEstimate) -> FeeOut:
    return FeeOut(
        available=estimate.available,
        label=estimate.label,
        amount_source_currency=estimate.amount_source_currency,
        amount_target_currency=estimate.amount_target_currency,
        basis=estimate.basis,
    )


def _outcome_out(outcome: calc.ConversionOutcome) -> OutcomeOut:
    return OutcomeOut(
        source_amount=outcome.source_amount,
        rate=outcome.rate,
        gross_target_amount=outcome.gross_target_amount,
        fee=_fee_out(outcome.fee),
        net_target_amount=outcome.net_target_amount,
        effective_rate=outcome.effective_rate,
        quality=str(outcome.quality),
    )


def _sensitivity_out(rows: list[calc.SensitivityRow]) -> list[SensitivityOut]:
    return [
        SensitivityOut(movement=row.movement, downside=row.downside, upside=row.upside)
        for row in rows
    ]


def _tranche_progress(
    strategy: Strategy,
    tranche: Tranche,
    current_rate: Decimal | None,
    assumption: calc.FeeAssumption | None,
) -> TrancheProgressOut:
    conversions = strategies.tranche_conversions(strategy, tranche.id)
    converted_source = quantize_money(sum((c.source_amount for c in conversions), ZERO))
    converted_target = quantize_money(sum((c.target_amount for c in conversions), ZERO))

    outstanding = quantize_money(max(tranche.calculated_source_amount - converted_source, ZERO))
    projection = (
        calc.project_conversion(outstanding, tranche.target_rate, assumption)
        if outstanding > ZERO
        else None
    )
    percent = calc.percent_converted(converted_source, tranche.calculated_source_amount)

    return TrancheProgressOut(
        tranche=TrancheOut.model_validate(tranche),
        distance_to_target=(
            quantize_rate(tranche.target_rate - current_rate) if current_rate is not None else None
        ),
        target_reached_now=current_rate is not None and current_rate >= tranche.target_rate,
        estimated_gross=projection.gross_target_amount if projection else None,
        estimated_fee=_fee_out(projection.fee if projection else calc.NO_FEE_ESTIMATE),
        estimated_net=projection.net_target_amount if projection else None,
        converted_source_amount=converted_source,
        converted_target_amount=converted_target,
        percent_complete=percent,
        upside_to_target=calc.target_upside(outstanding, tranche.target_rate, current_rate),
    )


async def build_summary(
    session: AsyncSession, strategy: Strategy, settings: Settings
) -> StrategySummaryOut:
    """Everything the dashboard shows, in one payload."""
    current = await rate_service.current_rate(session, settings)
    # A stale rate is still displayed, clearly marked, but it must not be the
    # basis of a "target reached" claim; that decision lives in the alert
    # service, which checks `rate_status` before acting.
    current_rate = current.rate

    fee_model = await strategies.get_fee_model(session, strategy.fee_model_id)
    assumption = strategies.fee_assumption_from(fee_model)

    completed = strategies.completed_conversions(strategy)
    converted_source = strategies.converted_source_total(strategy)
    remaining = strategies.remaining_amount(strategy)

    gross_received = quantize_money(
        sum((calc.gross_proceeds(c.source_amount, c.gross_rate) for c in completed), ZERO)
    )
    net_received = quantize_money(sum((c.net_target_amount for c in completed), ZERO))

    convert_now = (
        calc.project_conversion(remaining, current_rate, assumption)
        if current_rate is not None and remaining > ZERO
        else None
    )

    next_tranche = strategies.next_target(strategy, current_rate)
    next_outstanding = ZERO
    if next_tranche is not None:
        already = quantize_money(
            sum(
                (
                    c.source_amount
                    for c in strategies.tranche_conversions(strategy, next_tranche.id)
                ),
                ZERO,
            )
        )
        next_outstanding = quantize_money(
            max(next_tranche.calculated_source_amount - already, ZERO)
        )

    zone = calc.classify_rate(current_rate, strategies.zones_from(settings))

    outstanding_targets = [tranche.target_rate for tranche in strategies.open_tranches(strategy)]
    walk_away = calc.analyse_walk_away(
        remaining_source=remaining,
        current_rate=current_rate,
        completed=completed,
        outstanding_targets=outstanding_targets,
        next_target=next_tranche.target_rate if next_tranche else None,
        assumption=assumption,
    )
    walk_away_reached = (
        strategy.walk_away_rate is not None
        and current_rate is not None
        and current_rate >= strategy.walk_away_rate
    )

    days = strategies.days_until(strategy.final_deadline)
    severity, deadline_message = calc.deadline_severity(days)

    warnings: list[str] = []
    if fee_model is None:
        warnings.append(
            "No fee model is configured, so net figures cannot be calculated. "
            "Amounts are shown gross and labelled as such."
        )
    if current.status == "stale":
        warnings.append(
            "The rate is stale. Figures below are calculated from it, but no target "
            "will be confirmed until a fresh rate arrives."
        )
    if strategy.funds_available_amount < strategy.initial_source_amount:
        warnings.append(
            f"Only {strategy.funds_available_amount} of {strategy.initial_source_amount} "
            f"{strategy.source_currency} has arrived. Exposure figures use the available amount."
        )

    return StrategySummaryOut(
        strategy=StrategyOut.model_validate(strategy),
        current_rate=current_rate,
        rate_status=current.status,
        rate_zone=(
            ZoneOut(label=zone.label, guidance=zone.guidance, lower_bound=zone.lower_bound)
            if zone
            else None
        ),
        initial_source_amount=strategy.initial_source_amount,
        available_source_amount=strategy.funds_available_amount,
        converted_source_amount=converted_source,
        remaining_source_amount=remaining,
        percent_converted=calc.percent_converted(converted_source, strategy.funds_available_amount),
        gross_target_received=gross_received,
        net_target_received=net_received,
        total_fees=calc.total_fees(completed),
        blended_gross_rate=calc.blended_gross_rate(completed),
        blended_effective_rate=calc.blended_effective_rate(completed),
        best_conversion_rate=max((c.gross_rate for c in completed), default=None),
        worst_conversion_rate=min((c.gross_rate for c in completed), default=None),
        average_fee_percentage=strategies.average_fee_percentage(strategy),
        convert_all_now=_outcome_out(convert_now) if convert_now else None,
        next_target_rate=next_tranche.target_rate if next_tranche else None,
        next_target_source_amount=next_outstanding if next_tranche else None,
        next_target_upside=(
            calc.target_upside(next_outstanding, next_tranche.target_rate, current_rate)
            if next_tranche
            else None
        ),
        one_cent_exposure=calc.value_of_one_cent(remaining),
        sensitivity=_sensitivity_out(calc.sensitivity_table(remaining)),
        tranche_progress=[
            _tranche_progress(strategy, tranche, current_rate, assumption)
            for tranche in sorted(strategy.tranches, key=lambda item: item.sequence)
        ],
        requirements=_requirement_progress(strategy, converted_source),
        walk_away=WalkAwayOut(
            reached=walk_away_reached,
            walk_away_rate=strategy.walk_away_rate,
            remaining_source_amount=walk_away.remaining_source,
            convert_now=_outcome_out(walk_away.convert_now) if walk_away.convert_now else None,
            existing_blended_rate=walk_away.existing_blended_rate,
            blended_if_converted_now=walk_away.blended_if_converted_now,
            highest_outstanding_target=walk_away.highest_outstanding_target,
            difference_versus_waiting=walk_away.difference_versus_waiting,
            rate_movement_to_next_target=walk_away.rate_movement_to_next_target,
            sensitivity=_sensitivity_out(walk_away.sensitivity),
        ),
        comparisons=await _comparisons(
            session, strategy, settings, net_received, converted_source, current_rate, assumption
        ),
        days_to_deadline=days,
        deadline_severity=str(severity),
        deadline_message=deadline_message,
        fee_model=FeeModelOut.model_validate(fee_model) if fee_model else None,
        warnings=warnings,
    )


def _requirement_progress(
    strategy: Strategy, converted_source: Decimal
) -> list[RequirementProgressOut]:
    rows: list[RequirementProgressOut] = []
    for requirement in sorted(strategy.requirements, key=lambda item: item.due_date):
        required = strategies.requirement_amount(strategy, requirement)
        days = strategies.days_until(requirement.due_date)
        rows.append(
            RequirementProgressOut(
                requirement=RequirementOut.model_validate(requirement),
                required_source_amount=required,
                shortfall=calc.required_conversion_shortfall(required, converted_source),
                days_remaining=days,
                overdue=days is not None and days < 0 and converted_source < required,
            )
        )
    return rows


async def _comparisons(
    session: AsyncSession,
    strategy: Strategy,
    settings: Settings,
    net_received: Decimal,
    converted_source: Decimal,
    current_rate: Decimal | None,
    assumption: calc.FeeAssumption | None,
) -> ComparisonOut:
    """Compare what was achieved with simple alternatives.

    Every comparison is against the amount actually converted, so it answers
    "how did this compare with having done something else with the same money".
    """
    if converted_source <= ZERO:
        return ComparisonOut(
            versus_start_rate=None,
            versus_six_month_high=None,
            versus_six_month_low=None,
            versus_today=None,
            versus_equal_schedule=None,
        )

    source = strategy.source_currency
    target = strategy.target_currency

    start_sample = (
        await rate_service.sample_at_or_before(
            session, source, target, strategy.strategy_start_date
        )
        if strategy.strategy_start_date
        else None
    )
    high_6m, low_6m = await rate_service.extremes(
        session, source, target, utcnow() - timedelta(days=182)
    )

    equal_reference: Decimal | None = None
    if strategy.strategy_start_date:
        samples = await rate_service.history(
            session, source, target, strategy.strategy_start_date, utcnow(), limit=20_000
        )
        usable = [sample.rate for sample in samples if not sample.is_stale]
        if usable:
            equal_reference = quantize_rate(
                safe_divide(sum(usable, ZERO), Decimal(len(usable))) or ZERO
            )

    def against(rate: Decimal | None) -> Decimal | None:
        return calc.compare_against(net_received, converted_source, rate, assumption)

    return ComparisonOut(
        versus_start_rate=against(start_sample.rate if start_sample else None),
        versus_six_month_high=against(high_6m),
        versus_six_month_low=against(low_6m),
        versus_today=against(current_rate),
        versus_equal_schedule=against(equal_reference),
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def _scenario_out(result: calc.ScenarioResult) -> ScenarioOut:
    return ScenarioOut(
        key=result.key,
        name=result.name,
        description=result.description,
        total_source_amount=result.total_source,
        gross_target_amount=result.gross_target_amount,
        fee=_fee_out(result.fee),
        net_target_amount=result.net_target_amount,
        blended_rate=result.blended_rate,
        effective_rate=result.effective_rate,
        exposed_source_amount=result.exposed_source_amount,
        one_cent_exposure=result.one_cent_exposure,
        rate_required=result.rate_required,
        legs=[
            ScenarioLegOut(source_amount=leg.source_amount, rate=leg.rate, label=leg.label)
            for leg in result.legs
        ],
        assumptions=result.assumptions,
    )


async def build_scenarios(
    session: AsyncSession,
    strategy: Strategy,
    settings: Settings,
    *,
    periods: int = 4,
    custom_rate: Decimal | None = None,
) -> ScenariosOut:
    """Compare up to four plans for the amount still unconverted."""
    current = await rate_service.current_rate(session, settings)
    fee_model = await strategies.get_fee_model(session, strategy.fee_model_id)
    assumption = strategies.fee_assumption_from(fee_model)
    remaining = strategies.remaining_amount(strategy)

    scenarios: list[ScenarioOut] = []

    if current.rate is not None and remaining > ZERO:
        scenarios.append(
            _scenario_out(calc.convert_all_now_scenario(remaining, current.rate, assumption))
        )

    open_allocations = [
        (
            quantize_money(
                max(
                    tranche.calculated_source_amount
                    - sum(
                        (
                            c.source_amount
                            for c in strategies.tranche_conversions(strategy, tranche.id)
                        ),
                        ZERO,
                    ),
                    ZERO,
                )
            ),
            tranche.target_rate,
        )
        for tranche in strategies.open_tranches(strategy)
    ]
    if any(amount > ZERO for amount, _rate in open_allocations):
        scenarios.append(
            _scenario_out(
                calc.ladder_scenario(
                    open_allocations,
                    assumption,
                    name="Your target ladder",
                    description="Each remaining tranche converts when its target is reached.",
                )
            )
        )

    if current.rate is not None and remaining > ZERO:
        scenarios.append(
            _scenario_out(
                calc.equal_schedule_scenario(remaining, current.rate, periods, assumption)
            )
        )

    if custom_rate is not None and remaining > ZERO:
        scenarios.append(
            _scenario_out(
                calc.evaluate_scenario(
                    key="custom",
                    name="Your alternative",
                    description=f"Convert everything remaining at {custom_rate}.",
                    legs=[calc.ScenarioLeg(remaining, custom_rate, "At your rate")],
                    assumption=assumption,
                    exposed_source_amount=remaining,
                    rate_required=custom_rate,
                    assumptions=["Assumes the rate reaches the level you entered."],
                )
            )
        )

    return ScenariosOut(scenarios=scenarios)
