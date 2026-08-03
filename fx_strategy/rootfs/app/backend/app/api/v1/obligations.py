"""Obligations: debts and conversion priorities.

Decision support only. There is no endpoint here that pays, converts or
transfers anything, and an allocation is a suggestion the user carries out
themselves.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Query

from app.api.deps import ActorDep, SessionDep, SettingsDep
from app.api.errors import NotFoundError, ValidationError
from app.schemas.common import Message
from app.schemas.obligation import (
    AllocationLineOut,
    AllocationOut,
    AllocationRequest,
    FundingIn,
    FundingOut,
    ObligationIn,
    ObligationOut,
    ObligationPatch,
    PortfolioOut,
    WaitingOut,
)
from app.services import obligation_service as obligations
from app.services.obligation_engine import RateContext

router = APIRouter(prefix="/obligations", tags=["obligations"])


def _to_out(item: obligations.RankedAnalysis) -> ObligationOut:
    """Flatten a ranked analysis into the response shape."""
    analysis = item.analysis
    row = item.row
    return ObligationOut(
        id=item.obligation_id,
        name=row.name,
        obligation_type=analysis.obligation_type,
        priority=row.priority,
        relationship_importance=row.relationship_importance,
        interest_basis=row.interest_basis,
        partial_allowed=row.partial_allowed,
        active=row.active,
        completed=row.completed,
        notes=row.notes,
        total_nzd=row.total_nzd,
        amount_funded_nzd=row.amount_funded_nzd,
        remaining_nzd=analysis.remaining_nzd,
        annual_rate=row.annual_rate,
        minimum_payment_nzd=row.minimum_payment_nzd,
        due_date=row.due_date,
        earliest_payment_date=row.earliest_payment_date,
        target_rate=row.target_rate,
        max_wait_days=row.max_wait_days,
        daily_cost_nzd=analysis.daily_cost_nzd,
        weekly_cost_nzd=analysis.weekly_cost_nzd,
        monthly_cost_nzd=analysis.monthly_cost_nzd,
        annual_cost_nzd=analysis.annual_cost_nzd,
        has_interest_cost=analysis.has_interest_cost,
        usd_required_now=analysis.usd_required_now,
        rate_used=analysis.rate_used,
        rate_stale=analysis.rate_stale,
        rate_quality=analysis.rate_quality,
        gain_at_improvement=analysis.gain_at_improvement,
        gain_at_target_nzd=analysis.gain_at_target_nzd,
        waiting=[
            WaitingOut(
                days=outcome.days,
                waiting_cost_nzd=outcome.waiting_cost_nzd,
                fx_gain_nzd=outcome.fx_gain_nzd,
                net_benefit_nzd=outcome.net_benefit_nzd,
            )
            for outcome in sorted(analysis.waiting.values(), key=lambda o: o.days)
        ],
        break_even_days_at_improvement=analysis.break_even_days_at_improvement,
        break_even_days_at_target=analysis.break_even_days_at_target,
        break_even_rate_after={
            str(days): value for days, value in analysis.break_even_rate_after.items()
        },
        days_until_due=analysis.days_until_due,
        overdue=analysis.overdue,
        priority_components=analysis.priority.components(),
        financial_score=analysis.financial_score,
        overall_score=analysis.overall_score,
        financial_rank=item.financial_rank,
        overall_rank=item.overall_rank,
        action=analysis.action,
        reason=analysis.reason,
        warnings=analysis.warnings,
    )


def _allocation_out(allocation: obligations.Allocation) -> AllocationOut:
    return AllocationOut(
        label=allocation.label,
        description=allocation.description,
        usd_to_convert=allocation.usd_to_convert,
        nzd_obtained=allocation.nzd_obtained,
        lines=[
            AllocationLineOut(
                obligation_id=line.obligation_id,
                name=line.name,
                nzd_funded=line.nzd_funded,
                usd_required=line.usd_required,
                fully_funded=line.fully_funded,
                action=line.action,
            )
            for line in allocation.lines
        ],
        unfunded_obligation_ids=allocation.unfunded_obligation_ids,
        unfunded_nzd=allocation.unfunded_nzd,
        rate_used=allocation.rate_used,
        rate_stale=allocation.rate_stale,
    )


@router.get("", response_model=list[ObligationOut], summary="List obligations")
async def list_obligations(
    session: SessionDep,
    settings: SettingsDep,
    include_inactive: bool = Query(default=False),
    nzd_available: Decimal = Query(default=Decimal(0), ge=0),
) -> list[ObligationOut]:
    """Every obligation, analysed and ranked twice.

    ``nzd_available`` lets the caller say how much NZD is already in hand, which
    is what distinguishes "pay it" from "convert for it".
    """
    ranked = await obligations.analyse_all(
        session, settings, include_inactive=include_inactive, nzd_available=nzd_available
    )
    return [_to_out(item) for item in ranked]


def _column_values(payload: ObligationIn | ObligationPatch, *, only_set: bool = False) -> dict:
    """Read the model's attributes rather than dumping it.

    ``model_dump`` applies the string serializer these schemas use to keep
    Decimals exact across JSON, so it would hand the database a ``str`` where a
    ``Decimal`` belongs. Reading the attributes keeps the real values, and the
    enums are stringified explicitly for the columns that store them as text.
    """
    names = payload.model_fields_set if only_set else type(payload).model_fields
    values: dict = {name: getattr(payload, name) for name in names}
    for key in ("obligation_type", "interest_basis", "priority", "relationship_importance"):
        if key in values and values[key] is not None:
            values[key] = str(values[key])
    return values


@router.post("", response_model=ObligationOut, status_code=201, summary="Add an obligation")
async def create_obligation(
    payload: ObligationIn, session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> ObligationOut:
    row = await obligations.create(session, _column_values(payload), actor=actor)
    return await get_obligation(row.id, session, settings)


@router.get("/portfolio", response_model=PortfolioOut, summary="Portfolio summary")
async def portfolio(
    session: SessionDep,
    settings: SettingsDep,
    usd_on_hand: Decimal | None = Query(default=None, ge=0),
) -> PortfolioOut:
    """The whole book: totals, costs, the next thing to fund, and the limits.

    ``usd_on_hand`` enables the "USD remaining after funding the critical
    obligations" figures; without it they are omitted rather than guessed.
    """
    ranked = await obligations.analyse_all(session, settings)
    rate = await obligations.rate_context(session, settings)
    result = obligations.build_portfolio(ranked, rate, usd_on_hand=usd_on_hand)
    return PortfolioOut.model_validate(result, from_attributes=True)


@router.get("/allocations", response_model=list[AllocationOut], summary="Standard conversion plans")
async def standard_allocations(session: SessionDep, settings: SettingsDep) -> list[AllocationOut]:
    """The three plans side by side: critical only, critical plus a fortnight, everything."""
    ranked = await obligations.analyse_all(session, settings)
    rate = await obligations.rate_context(session, settings)
    return [_allocation_out(plan) for plan in obligations.standard_scenarios(ranked, rate)]


@router.post("/allocations", response_model=AllocationOut, summary="Plan a conversion")
async def plan_allocation(
    payload: AllocationRequest, session: SessionDep, settings: SettingsDep
) -> AllocationOut:
    """Ask what a given amount of USD would settle, optionally at a hypothetical rate.

    A scenario, not an instruction: nothing is converted and nothing is recorded.
    """
    ranked = await obligations.analyse_all(session, settings)
    rate = await obligations.rate_context(session, settings)

    if payload.rate is not None:
        # A hypothetical rate is never stale — it is a question, not an observation.
        rate = RateContext(rate=payload.rate, stale=False, quality="hypothetical")
        ranked = await obligations.analyse_all(session, settings, rate=rate)

    allocation = obligations.plan_allocation(
        ranked,
        rate,
        label="Custom plan",
        description=(
            f"Scenario at {payload.rate}" if payload.rate else "Scenario at the current rate"
        ),
        selected_ids=payload.obligation_ids or None,
        usd_available=payload.usd_available,
    )
    return _allocation_out(allocation)


@router.get("/{obligation_id}", response_model=ObligationOut, summary="One obligation")
async def get_obligation(
    obligation_id: int, session: SessionDep, settings: SettingsDep
) -> ObligationOut:
    ranked = await obligations.analyse_all(session, settings, include_inactive=True)
    for item in ranked:
        if item.obligation_id == obligation_id:
            return _to_out(item)
    raise NotFoundError(f"No obligation with id {obligation_id}.")


@router.patch("/{obligation_id}", response_model=ObligationOut, summary="Edit an obligation")
async def patch_obligation(
    obligation_id: int,
    payload: ObligationPatch,
    session: SessionDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> ObligationOut:
    try:
        await obligations.update(
            session, obligation_id, _column_values(payload, only_set=True), actor=actor
        )
    except obligations.ObligationError as exc:
        raise NotFoundError(str(exc)) from exc
    return await get_obligation(obligation_id, session, settings)


@router.delete("/{obligation_id}", response_model=Message, summary="Delete an obligation")
async def delete_obligation(obligation_id: int, session: SessionDep, actor: ActorDep) -> Message:
    try:
        await obligations.delete(session, obligation_id, actor=actor)
    except obligations.ObligationError as exc:
        raise NotFoundError(str(exc)) from exc
    return Message(message="Obligation deleted. The audit record of it remains.")


@router.post(
    "/{obligation_id}/funding",
    response_model=ObligationOut,
    status_code=201,
    summary="Record funding",
)
async def add_funding(
    obligation_id: int,
    payload: FundingIn,
    session: SessionDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> ObligationOut:
    """Record NZD applied to this obligation.

    A record of something that already happened, not an instruction to pay.
    """
    try:
        await obligations.record_funding(
            session,
            obligation_id,
            payload.amount_nzd,
            conversion_id=payload.conversion_id,
            note=payload.note,
            actor=actor,
        )
    except obligations.ObligationError as exc:
        raise NotFoundError(str(exc)) from exc
    return await get_obligation(obligation_id, session, settings)


@router.get("/{obligation_id}/funding", response_model=list[FundingOut], summary="Funding history")
async def funding_history(obligation_id: int, session: SessionDep) -> list[FundingOut]:
    try:
        await obligations.get_row(session, obligation_id)
    except obligations.ObligationError as exc:
        raise NotFoundError(str(exc)) from exc
    rows = await obligations.fundings(session, obligation_id)
    return [FundingOut.model_validate(row) for row in rows]


@router.post("/{obligation_id}/complete", response_model=ObligationOut, summary="Mark funded")
async def mark_complete(
    obligation_id: int, session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> ObligationOut:
    try:
        await obligations.update(session, obligation_id, {"completed": True}, actor=actor)
    except obligations.ObligationError as exc:
        raise NotFoundError(str(exc)) from exc
    return await get_obligation(obligation_id, session, settings)


@router.post("/{obligation_id}/archive", response_model=ObligationOut, summary="Archive")
async def archive(
    obligation_id: int, session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> ObligationOut:
    """Take it out of the active book without deleting the record."""
    try:
        await obligations.update(session, obligation_id, {"active": False}, actor=actor)
    except obligations.ObligationError as exc:
        raise NotFoundError(str(exc)) from exc
    return await get_obligation(obligation_id, session, settings)


@router.post("/pay", include_in_schema=False)
async def refuse_payment() -> None:
    """There is no payment endpoint, and this says so rather than 404ing.

    A 404 could read as "not built yet". This is a deliberate position.
    """
    raise ValidationError(
        "This application does not pay, convert or transfer money. It records "
        "obligations and suggests what to fund; you carry out the conversion in "
        "Wise yourself."
    )
