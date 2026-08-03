"""Obligations: storage, ranking, portfolio view and conversion planning.

This is the layer between the pure engine and the rest of the application. It
owns nothing about the arithmetic — every figure comes from
:mod:`app.services.obligation_engine` — and nothing about presentation.

Nothing in here can move money. An allocation is a suggestion the user acts on
in Wise themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.logging_setup import get_logger
from app.models.audit import AuditEventType
from app.models.obligation import Obligation, ObligationFunding
from app.money import quantize_money, quantize_rate, safe_divide
from app.schemas.settings import Settings
from app.services import audit, rate_service
from app.services.obligation_engine import (
    InterestBasis,
    ObligationAnalysis,
    ObligationInput,
    ObligationType,
    Priority,
    RateContext,
    RecommendedAction,
    Relationship,
    analyse,
)

log = get_logger(__name__)

#: Actions that mean "this needs USD converted for it now".
NEEDS_CONVERSION = frozenset(
    {
        RecommendedAction.CONVERT_NOW,
        RecommendedAction.CONVERT_PARTIAL,
        RecommendedAction.OVERDUE,
    }
)


class ObligationError(ValueError):
    """The requested obligation change cannot be made."""


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def to_input(row: Obligation) -> ObligationInput:
    """Turn a stored row into the engine's value object."""
    return ObligationInput(
        name=row.name,
        obligation_type=ObligationType(row.obligation_type),
        total_nzd=row.total_nzd,
        amount_funded_nzd=row.amount_funded_nzd,
        remaining_override_nzd=row.remaining_override_nzd,
        annual_rate=row.annual_rate,
        interest_basis=InterestBasis(row.interest_basis),
        daily_rate=row.daily_rate,
        due_date=row.due_date,
        earliest_payment_date=row.earliest_payment_date,
        priority=Priority(row.priority),
        relationship=Relationship(row.relationship_importance),
        minimum_payment_nzd=row.minimum_payment_nzd,
        partial_allowed=row.partial_allowed,
        target_rate=row.target_rate,
        max_wait_days=row.max_wait_days,
        notes=row.notes,
        active=row.active,
        completed=row.completed,
    )


async def rate_context(session: AsyncSession, settings: Settings) -> RateContext:
    """The FX side of every calculation, with its staleness carried honestly.

    The quality is always ``market`` here: this is an indicative rate, never a
    Wise quote, and the two must not be presented as the same thing.
    """
    current = await rate_service.current_rate(session, settings)
    return RateContext(
        rate=current.rate,
        stale=current.is_stale,
        as_of=current.sample.retrieved_at.isoformat() if current.sample else "",
        quality="market",
    )


async def list_rows(session: AsyncSession, *, include_inactive: bool = False) -> list[Obligation]:
    statement = select(Obligation).order_by(Obligation.id)
    if not include_inactive:
        statement = statement.where(Obligation.active.is_(True))
    return list((await session.execute(statement)).scalars().all())


async def get_row(session: AsyncSession, obligation_id: int) -> Obligation:
    row = await session.get(Obligation, obligation_id)
    if row is None:
        raise ObligationError(f"No obligation with id {obligation_id}.")
    return row


@dataclass(frozen=True, slots=True)
class RankedAnalysis:
    """An analysis with its place in both orderings."""

    obligation_id: int
    row: Obligation
    analysis: ObligationAnalysis
    financial_rank: int
    overall_rank: int


async def analyse_all(
    session: AsyncSession,
    settings: Settings,
    *,
    include_inactive: bool = False,
    today: date | None = None,
    rate: RateContext | None = None,
    nzd_available: Decimal = Decimal(0),
) -> list[RankedAnalysis]:
    """Analyse every obligation and rank it twice.

    Two orderings, because they genuinely differ: an interest-free family loan
    is financially unhurried and may still be the first thing to fund.
    """
    rows = await list_rows(session, include_inactive=include_inactive)
    context = rate if rate is not None else await rate_context(session, settings)
    when = today or utcnow().date()

    inputs = [to_input(row) for row in rows]
    portfolio_total = sum(
        (analyse(item, context, today=when).remaining_nzd for item in inputs), Decimal(0)
    )

    analyses = [
        analyse(
            item,
            context,
            today=when,
            portfolio_total_nzd=portfolio_total,
            nzd_available=nzd_available,
        )
        for item in inputs
    ]

    financial_order = sorted(
        range(len(analyses)), key=lambda i: analyses[i].financial_score, reverse=True
    )
    overall_order = sorted(
        range(len(analyses)), key=lambda i: analyses[i].overall_score, reverse=True
    )
    financial_rank = {index: rank for rank, index in enumerate(financial_order, start=1)}
    overall_rank = {index: rank for rank, index in enumerate(overall_order, start=1)}

    return [
        RankedAnalysis(
            obligation_id=rows[index].id,
            row=rows[index],
            analysis=analyses[index],
            financial_rank=financial_rank[index],
            overall_rank=overall_rank[index],
        )
        for index in range(len(analyses))
    ]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


async def create(
    session: AsyncSession, values: dict[str, Any], *, actor: str = "user"
) -> Obligation:
    row = Obligation(**values)
    session.add(row)
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.CREATED,
        entity_type="obligation",
        entity_id=str(row.id),
        message=f"Obligation created: {row.name} for NZ${row.total_nzd:,.2f}",
        after={"name": row.name, "total_nzd": format(row.total_nzd, "f")},
        actor=actor,
    )
    return row


async def update(
    session: AsyncSession, obligation_id: int, values: dict[str, Any], *, actor: str = "user"
) -> Obligation:
    """Apply a partial edit, keeping the previous values in the audit trail."""
    row = await get_row(session, obligation_id)
    before = {key: _render(getattr(row, key)) for key in values}

    for key, value in values.items():
        setattr(row, key, value)
    if values.get("completed") and row.completed_at is None:
        row.completed_at = utcnow()
    if values.get("active") is False and row.archived_at is None:
        row.archived_at = utcnow()
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type="obligation",
        entity_id=str(row.id),
        message=f"Obligation updated: {row.name}",
        before=before,
        after={key: _render(getattr(row, key)) for key in values},
        actor=actor,
    )
    return row


def _render(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    return value


async def record_funding(
    session: AsyncSession,
    obligation_id: int,
    amount_nzd: Decimal,
    *,
    conversion_id: int | None = None,
    note: str = "",
    actor: str = "user",
) -> ObligationFunding:
    """Apply NZD to an obligation, keeping the history as its own record.

    The running total is updated too, but the entries are what survive an edit
    to the obligation itself.
    """
    row = await get_row(session, obligation_id)

    funding = ObligationFunding(
        obligation_id=row.id,
        amount_nzd=quantize_money(amount_nzd),
        conversion_id=conversion_id,
        note=note,
    )
    session.add(funding)

    row.amount_funded_nzd = quantize_money(row.amount_funded_nzd + amount_nzd)
    if row.amount_funded_nzd >= row.total_nzd and not row.completed:
        row.completed = True
        row.completed_at = utcnow()
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type="obligation",
        entity_id=str(row.id),
        message=(
            f"Funding recorded on {row.name}: NZ${amount_nzd:,.2f}. "
            f"Funded to date NZ${row.amount_funded_nzd:,.2f} of NZ${row.total_nzd:,.2f}."
        ),
        after={"amount_funded_nzd": format(row.amount_funded_nzd, "f")},
        actor=actor,
    )
    return funding


async def fundings(session: AsyncSession, obligation_id: int) -> list[ObligationFunding]:
    statement = (
        select(ObligationFunding)
        .where(ObligationFunding.obligation_id == obligation_id)
        .order_by(ObligationFunding.funded_at)
    )
    return list((await session.execute(statement)).scalars().all())


async def delete(session: AsyncSession, obligation_id: int, *, actor: str = "user") -> None:
    """Remove an obligation. The audit record of it remains."""
    row = await get_row(session, obligation_id)
    name, total = row.name, row.total_nzd
    await session.delete(row)
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.DELETED,
        entity_type="obligation",
        entity_id=str(obligation_id),
        message=f"Obligation deleted: {name} for NZ${total:,.2f}",
        actor=actor,
    )


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Portfolio:
    """The whole book at a glance."""

    total_obligations: int
    total_nzd: Decimal
    total_usd_required: Decimal | None
    total_daily_cost_nzd: Decimal
    total_monthly_cost_nzd: Decimal
    due_within_7_days_nzd: Decimal
    due_within_30_days_nzd: Decimal

    highest_priority_obligation_id: int | None
    highest_priority_obligation_name: str
    next_obligation_id: int | None
    next_obligation_name: str
    next_conversion_usd: Decimal | None
    next_conversion_nzd: Decimal

    usd_after_critical: Decimal | None
    usd_after_high_priority: Decimal | None
    weighted_break_even_rate: Decimal | None
    max_rational_wait_days: int | None

    strategy_status: str
    rate_used: Decimal | None
    rate_stale: bool
    rate_quality: str
    warnings: list[str]


def _sum_usd(items: list[RankedAnalysis]) -> Decimal | None:
    """Total USD needed, or None if any item could not be priced.

    Returning a partial total would understate the requirement, which is the
    one direction that matters here.
    """
    total = Decimal(0)
    for item in items:
        if item.analysis.usd_required_now is None:
            return None
        total += item.analysis.usd_required_now
    return quantize_money(total)


def build_portfolio(
    ranked: list[RankedAnalysis], rate: RateContext, *, usd_on_hand: Decimal | None = None
) -> Portfolio:
    """Aggregate the analyses into the portfolio view.

    Every figure here is a sum of per-obligation figures already computed by the
    engine, so the summary can never disagree with the detail.
    """
    live = [item for item in ranked if not item.row.completed and item.analysis.remaining_nzd > 0]

    total_nzd = quantize_money(sum((i.analysis.remaining_nzd for i in live), Decimal(0)))
    total_daily = quantize_money(sum((i.analysis.daily_cost_nzd for i in live), Decimal(0)))
    total_monthly = quantize_money(sum((i.analysis.monthly_cost_nzd for i in live), Decimal(0)))

    due_7 = quantize_money(
        sum(
            (
                i.analysis.remaining_nzd
                for i in live
                if i.analysis.days_until_due is not None and i.analysis.days_until_due <= 7
            ),
            Decimal(0),
        )
    )
    due_30 = quantize_money(
        sum(
            (
                i.analysis.remaining_nzd
                for i in live
                if i.analysis.days_until_due is not None and i.analysis.days_until_due <= 30
            ),
            Decimal(0),
        )
    )

    by_overall = sorted(live, key=lambda i: i.overall_rank)
    highest = by_overall[0] if by_overall else None

    # The next thing to fund is the highest-ranked obligation that actually
    # calls for conversion, not simply the highest-ranked one.
    needing = [i for i in by_overall if i.analysis.action in NEEDS_CONVERSION]
    next_item = needing[0] if needing else highest

    critical = [i for i in live if i.row.priority == str(Priority.CRITICAL)]
    high = [i for i in live if i.row.priority in {str(Priority.CRITICAL), str(Priority.HIGH)}]

    usd_after_critical: Decimal | None = None
    usd_after_high: Decimal | None = None
    if usd_on_hand is not None:
        critical_usd = _sum_usd(critical)
        high_usd = _sum_usd(high)
        if critical_usd is not None:
            usd_after_critical = quantize_money(usd_on_hand - critical_usd)
        if high_usd is not None:
            usd_after_high = quantize_money(usd_on_hand - high_usd)

    warnings: list[str] = []
    if rate.stale:
        warnings.append(
            "The exchange rate is stale. USD figures are calculated from it but no "
            "waiting recommendation is made."
        )
    if rate.rate is None:
        warnings.append("No exchange rate is available, so USD requirements cannot be shown.")

    return Portfolio(
        total_obligations=len(live),
        total_nzd=total_nzd,
        total_usd_required=_sum_usd(live),
        total_daily_cost_nzd=total_daily,
        total_monthly_cost_nzd=total_monthly,
        due_within_7_days_nzd=due_7,
        due_within_30_days_nzd=due_30,
        highest_priority_obligation_id=highest.obligation_id if highest else None,
        highest_priority_obligation_name=highest.row.name if highest else "",
        next_obligation_id=next_item.obligation_id if next_item else None,
        next_obligation_name=next_item.row.name if next_item else "",
        next_conversion_usd=next_item.analysis.usd_required_now if next_item else None,
        next_conversion_nzd=next_item.analysis.remaining_nzd if next_item else Decimal("0.0000"),
        usd_after_critical=usd_after_critical,
        usd_after_high_priority=usd_after_high,
        weighted_break_even_rate=weighted_break_even(live, rate),
        max_rational_wait_days=max_rational_wait(live),
        strategy_status=strategy_status(live, rate),
        rate_used=rate.rate,
        rate_stale=rate.stale,
        rate_quality=rate.quality,
        warnings=warnings,
    )


def weighted_break_even(items: list[RankedAnalysis], rate: RateContext) -> Decimal | None:
    """One rate that would repay 30 days of waiting across the whole book.

    Weighted by USD required, because that is what a single conversion would
    actually buy. Obligations that accrue nothing contribute no cost but still
    contribute weight, which is correct: they dilute the required improvement.
    """
    if rate.rate is None:
        return None

    total_cost = Decimal(0)
    total_usd = Decimal(0)
    for item in items:
        usd = item.analysis.usd_required_now
        if usd is None:
            continue
        total_usd += usd
        outcome = item.analysis.waiting.get(30)
        if outcome is not None:
            total_cost += outcome.waiting_cost_nzd

    if total_usd <= 0:
        return None
    improvement = safe_divide(total_cost, total_usd)
    return None if improvement is None else quantize_rate(rate.rate + improvement)


def max_rational_wait(items: list[RankedAnalysis]) -> int | None:
    """The shortest deadline anything in the book imposes.

    The whole portfolio can only wait as long as its most pressing member, so
    this is a minimum across obligations, not an average.
    """
    limits: list[int] = []
    for item in items:
        if item.analysis.days_until_due is not None:
            limits.append(max(item.analysis.days_until_due, 0))
        if item.row.max_wait_days is not None:
            limits.append(item.row.max_wait_days)
        break_even = item.analysis.break_even_days_at_target
        if break_even is not None:
            limits.append(int(break_even))
    return min(limits) if limits else None


def strategy_status(items: list[RankedAnalysis], rate: RateContext) -> str:
    """A single word for the dashboard and the Home Assistant sensor."""
    if not items:
        return "no_obligations"
    if rate.rate is None:
        return "rate_unavailable"
    if any(i.analysis.action == RecommendedAction.OVERDUE for i in items):
        return "overdue"
    if rate.stale:
        return "rate_stale"
    if any(i.analysis.action == RecommendedAction.CONVERT_NOW for i in items):
        return "convert_now"
    if any(i.analysis.action == RecommendedAction.CONVERT_PARTIAL for i in items):
        return "convert_partial"
    if any(
        i.analysis.action
        in {RecommendedAction.WAIT_FOR_TARGET, RecommendedAction.WAIT_WITH_DEADLINE}
        for i in items
    ):
        return "waiting"
    return "review"


# ---------------------------------------------------------------------------
# Allocation planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllocationLine:
    obligation_id: int
    name: str
    nzd_funded: Decimal
    usd_required: Decimal | None
    fully_funded: bool
    action: RecommendedAction


@dataclass(frozen=True, slots=True)
class Allocation:
    """A suggested conversion and exactly what it settles.

    A recommendation only. This application never initiates a conversion.
    """

    label: str
    description: str
    usd_to_convert: Decimal | None
    nzd_obtained: Decimal
    lines: list[AllocationLine]
    unfunded_obligation_ids: list[int]
    unfunded_nzd: Decimal
    rate_used: Decimal | None
    rate_stale: bool


def plan_allocation(
    ranked: list[RankedAnalysis],
    rate: RateContext,
    *,
    label: str,
    description: str,
    selected_ids: list[int] | None = None,
    usd_available: Decimal | None = None,
) -> Allocation:
    """Work down the overall ranking, funding what the USD reaches.

    An obligation that does not allow partial payment is skipped rather than
    part-funded, and the planner carries on to the next one: a half-paid
    all-or-nothing debt would satisfy nobody.
    """
    chosen = [
        item
        for item in sorted(ranked, key=lambda i: i.overall_rank)
        if not item.row.completed
        and item.analysis.remaining_nzd > 0
        and (selected_ids is None or item.obligation_id in selected_ids)
    ]

    lines: list[AllocationLine] = []
    unfunded: list[int] = []
    nzd_obtained = Decimal(0)
    usd_spent = Decimal(0)
    usd_left = usd_available

    for item in chosen:
        needed_nzd = item.analysis.remaining_nzd
        needed_usd = item.analysis.usd_required_now

        if rate.rate is None or needed_usd is None:
            unfunded.append(item.obligation_id)
            continue

        if usd_left is None or usd_left >= needed_usd:
            # Funded in full.
            lines.append(
                AllocationLine(
                    obligation_id=item.obligation_id,
                    name=item.row.name,
                    nzd_funded=needed_nzd,
                    usd_required=needed_usd,
                    fully_funded=True,
                    action=item.analysis.action,
                )
            )
            nzd_obtained += needed_nzd
            usd_spent += needed_usd
            if usd_left is not None:
                usd_left -= needed_usd
            continue

        if usd_left <= 0 or not item.row.partial_allowed:
            unfunded.append(item.obligation_id)
            continue

        # Partial: what the remaining USD buys.
        part_nzd = quantize_money(usd_left * rate.rate)
        minimum = item.row.minimum_payment_nzd
        if minimum is not None and part_nzd < minimum:
            # Below the obligation's own minimum payment, so it funds nothing.
            unfunded.append(item.obligation_id)
            continue

        lines.append(
            AllocationLine(
                obligation_id=item.obligation_id,
                name=item.row.name,
                nzd_funded=part_nzd,
                usd_required=quantize_money(usd_left),
                fully_funded=False,
                action=item.analysis.action,
            )
        )
        nzd_obtained += part_nzd
        usd_spent += usd_left
        usd_left = Decimal(0)

    unfunded_nzd = quantize_money(
        sum(
            (item.analysis.remaining_nzd for item in chosen if item.obligation_id in set(unfunded)),
            Decimal(0),
        )
    )

    return Allocation(
        label=label,
        description=description,
        usd_to_convert=quantize_money(usd_spent) if rate.rate is not None else None,
        nzd_obtained=quantize_money(nzd_obtained),
        lines=lines,
        unfunded_obligation_ids=unfunded,
        unfunded_nzd=unfunded_nzd,
        rate_used=rate.rate,
        rate_stale=rate.stale,
    )


def standard_scenarios(ranked: list[RankedAnalysis], rate: RateContext) -> list[Allocation]:
    """The three plans the specification asks for, side by side."""
    critical_ids = [i.obligation_id for i in ranked if i.row.priority == str(Priority.CRITICAL)]
    soon_ids = [
        i.obligation_id
        for i in ranked
        if i.analysis.days_until_due is not None and i.analysis.days_until_due <= 14
    ]
    urgent_ids = sorted(set(critical_ids) | set(soon_ids))

    return [
        plan_allocation(
            ranked,
            rate,
            label="Critical only",
            description="Convert enough to fund every obligation marked critical.",
            selected_ids=critical_ids,
        ),
        plan_allocation(
            ranked,
            rate,
            label="Critical and due within 14 days",
            description=(
                "Convert enough to fund the critical obligations and anything falling "
                "due in the next fortnight. The rest stays in USD, waiting for the "
                "next target rate."
            ),
            selected_ids=urgent_ids,
        ),
        plan_allocation(
            ranked,
            rate,
            label="Everything",
            description="Convert enough to clear the whole book at the current rate.",
        ),
    ]
