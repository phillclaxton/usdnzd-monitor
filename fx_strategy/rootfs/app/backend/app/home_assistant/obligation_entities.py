"""Home Assistant entities for obligations.

Two layers:

* Portfolio sensors, which belong to the app's own device.
* A device per obligation, so an obligation appears in Home Assistant as a
  thing with its own sensors rather than as seven more rows on one enormous
  device. Automations can then target "the Meika repayment" directly.

A figure that cannot be calculated is published as an empty string, which Home
Assistant shows as unknown. It is never published as zero.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

from app.home_assistant.entities import NODE_ID, EntityContext, EntityDefinition
from app.money import decimal_to_str
from app.schemas.obligation import ObligationOut, PortfolioOut

#: Prefix for every obligation entity, so they group in the entity list.
OBLIGATION_PREFIX = "fx_obligation"


def slugify(name: str) -> str:
    """A stable, readable object-ID fragment: 'Meika repayment' -> 'meika_repayment'."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "obligation"


def unique_slugs(obligations: list[ObligationOut]) -> dict[int, str]:
    """One slug per obligation, disambiguated by id only where names collide.

    Renaming an obligation changes its entity IDs, which is the same behaviour
    as renaming anything else in Home Assistant; two obligations sharing a name
    would otherwise silently share entities, which is worse.
    """
    counts: dict[str, int] = {}
    for item in obligations:
        counts[slugify(item.name)] = counts.get(slugify(item.name), 0) + 1

    slugs: dict[int, str] = {}
    for item in obligations:
        base = slugify(item.name)
        slugs[item.id] = base if counts[base] == 1 else f"{base}_{item.id}"
    return slugs


def _d(value: Decimal | None) -> str | None:
    return decimal_to_str(value) if value is not None else None


def device_for(item: ObligationOut, slug: str, device_name: str) -> dict[str, Any]:
    """One Home Assistant device per obligation, linked to the app's device."""
    return {
        "identifiers": [f"{NODE_ID}_obligation_{item.id}"],
        "name": f"{device_name}: {item.name}",
        "manufacturer": "FX Strategy Manager",
        "model": f"Obligation ({item.obligation_type.replace('_', ' ')})",
        "via_device": NODE_ID,
    }


def _attributes(item: ObligationOut) -> dict[str, Any]:
    """The context a state alone cannot carry.

    The priority score is always accompanied by its components: a bare number
    would be impossible to argue with.
    """
    return {
        "obligation_id": item.id,
        "obligation_type": str(item.obligation_type),
        "priority": str(item.priority),
        "relationship_importance": str(item.relationship_importance),
        "total_nzd": _d(item.total_nzd),
        "amount_funded_nzd": _d(item.amount_funded_nzd),
        "annual_rate": _d(item.annual_rate),
        "has_interest_cost": item.has_interest_cost,
        "due_date": item.due_date.isoformat() if item.due_date else None,
        "days_until_due": item.days_until_due,
        "overdue": item.overdue,
        "target_rate": _d(item.target_rate),
        "max_wait_days": item.max_wait_days,
        "partial_allowed": item.partial_allowed,
        "monthly_waiting_cost_nzd": _d(item.monthly_cost_nzd),
        "annual_waiting_cost_nzd": _d(item.annual_cost_nzd),
        "gain_at_0_005_nzd": _d(item.gain_at_improvement.get("0.005")),
        "gain_at_0_01_nzd": _d(item.gain_at_improvement.get("0.01")),
        "gain_at_target_nzd": _d(item.gain_at_target_nzd),
        "net_benefit_7_days_nzd": _net(item, 7),
        "net_benefit_30_days_nzd": _net(item, 30),
        "net_benefit_60_days_nzd": _net(item, 60),
        "break_even_days_at_0_01": _d(item.break_even_days_at_improvement.get("0.01")),
        "financial_rank": item.financial_rank,
        "overall_rank": item.overall_rank,
        "financial_score": _d(item.financial_score),
        "overall_score": _d(item.overall_score),
        # Read the attributes rather than model_dump: these schemas serialize
        # Decimals to strings, and dumping would hand _d a str.
        "priority_components": {
            key: _d(getattr(item.priority_components, key))
            for key in type(item.priority_components).model_fields
        },
        "recommendation_reason": item.reason,
        "warnings": item.warnings,
        "rate_quality": item.rate_quality,
        "rate_stale": item.rate_stale,
        "disclaimer": item.disclaimer,
    }


def _net(item: ObligationOut, days: int) -> str | None:
    for outcome in item.waiting:
        if outcome.days == days:
            return _d(outcome.net_benefit_nzd)
    return None


def definitions_for(item: ObligationOut, slug: str, device_name: str) -> list[EntityDefinition]:
    """The sensors for one obligation."""
    device = device_for(item, slug, device_name)
    attributes = _attributes(item)

    def fixed(value: Any) -> Any:
        # The values are already computed; the state functions simply return them.
        return lambda _ctx: value

    return [
        EntityDefinition(
            object_id=f"{slug}_remaining",
            name=f"{item.name} remaining",
            component="sensor",
            state=fixed(_d(item.remaining_nzd)),
            icon="mdi:cash-remove",
            unit="NZD",
            state_class="measurement",
            attributes=lambda _ctx: attributes,
            device=device,
        ),
        EntityDefinition(
            object_id=f"{slug}_usd_required",
            name=f"{item.name} USD required",
            component="sensor",
            state=fixed(_d(item.usd_required_now)),
            icon="mdi:currency-usd",
            unit="USD",
            state_class="measurement",
            attributes=lambda _ctx: attributes,
            device=device,
        ),
        EntityDefinition(
            object_id=f"{slug}_daily_waiting_cost",
            name=f"{item.name} daily waiting cost",
            component="sensor",
            state=fixed(_d(item.daily_cost_nzd)),
            icon="mdi:calendar-clock",
            unit="NZD",
            state_class="measurement",
            attributes=lambda _ctx: attributes,
            device=device,
        ),
        EntityDefinition(
            object_id=f"{slug}_break_even_days",
            name=f"{item.name} break-even days",
            component="sensor",
            # None for a zero-interest obligation: there is no break-even period,
            # and Home Assistant shows that as unknown rather than as zero days.
            state=fixed(_d(item.break_even_days_at_improvement.get("0.01"))),
            icon="mdi:scale-balance",
            unit="d",
            attributes=lambda _ctx: attributes,
            device=device,
        ),
        EntityDefinition(
            object_id=f"{slug}_break_even_rate_30_days",
            name=f"{item.name} break-even rate after 30 days",
            component="sensor",
            state=fixed(_d(item.break_even_rate_after.get("30"))),
            icon="mdi:target",
            state_class="measurement",
            attributes=lambda _ctx: attributes,
            device=device,
        ),
        EntityDefinition(
            object_id=f"{slug}_recommendation",
            name=f"{item.name} recommendation",
            component="sensor",
            state=fixed(str(item.action)),
            icon="mdi:lightbulb-on-outline",
            attributes=lambda _ctx: attributes,
            device=device,
        ),
        EntityDefinition(
            object_id=f"{slug}_priority_rank",
            name=f"{item.name} priority rank",
            component="sensor",
            state=fixed(item.overall_rank),
            icon="mdi:sort-numeric-ascending",
            attributes=lambda _ctx: attributes,
            device=device,
        ),
        EntityDefinition(
            object_id=f"{slug}_overdue",
            name=f"{item.name} overdue",
            component="binary_sensor",
            state=fixed(item.overdue),
            device_class="problem",
            attributes=lambda _ctx: attributes,
            device=device,
        ),
    ]


# ---------------------------------------------------------------------------
# Portfolio sensors, on the app's own device
# ---------------------------------------------------------------------------


def _portfolio(context: EntityContext) -> PortfolioOut | None:
    return context.portfolio


def _p(context: EntityContext, reader: Any) -> Any:
    """Read a portfolio field, or None when there is no book at all."""
    portfolio = _portfolio(context)
    return None if portfolio is None else reader(portfolio)


def portfolio_definitions() -> list[EntityDefinition]:
    """The portfolio-level sensors named in the specification."""
    return [
        EntityDefinition(
            object_id="fx_total_active_obligations_nzd",
            name="Total active obligations",
            component="sensor",
            state=lambda ctx: _p(ctx, lambda p: decimal_to_str(p.total_nzd)),
            icon="mdi:file-document-multiple",
            unit="NZD",
            state_class="measurement",
            attributes=lambda ctx: {
                "count": _p(ctx, lambda p: p.total_obligations),
                "due_within_7_days_nzd": _p(ctx, lambda p: decimal_to_str(p.due_within_7_days_nzd)),
                "due_within_30_days_nzd": _p(
                    ctx, lambda p: decimal_to_str(p.due_within_30_days_nzd)
                ),
                "disclaimer": _p(ctx, lambda p: p.disclaimer),
            },
        ),
        EntityDefinition(
            object_id="fx_total_usd_required",
            name="Total USD required",
            component="sensor",
            state=lambda ctx: _p(
                ctx,
                lambda p: (
                    decimal_to_str(p.total_usd_required)
                    if p.total_usd_required is not None
                    else None
                ),
            ),
            icon="mdi:currency-usd",
            unit="USD",
            state_class="measurement",
            attributes=lambda ctx: {
                "rate_used": _p(
                    ctx, lambda p: decimal_to_str(p.rate_used) if p.rate_used else None
                ),
                "rate_stale": _p(ctx, lambda p: p.rate_stale),
                "rate_quality": _p(ctx, lambda p: p.rate_quality),
                "warnings": _p(ctx, lambda p: p.warnings),
            },
        ),
        EntityDefinition(
            object_id="fx_total_daily_waiting_cost",
            name="Total daily waiting cost",
            component="sensor",
            state=lambda ctx: _p(ctx, lambda p: decimal_to_str(p.total_daily_cost_nzd)),
            icon="mdi:calendar-clock",
            unit="NZD",
            state_class="measurement",
        ),
        EntityDefinition(
            object_id="fx_total_monthly_waiting_cost",
            name="Total monthly waiting cost",
            component="sensor",
            state=lambda ctx: _p(ctx, lambda p: decimal_to_str(p.total_monthly_cost_nzd)),
            icon="mdi:calendar-month",
            unit="NZD",
            state_class="measurement",
        ),
        EntityDefinition(
            object_id="fx_next_obligation",
            name="Next obligation",
            component="sensor",
            state=lambda ctx: _p(ctx, lambda p: p.next_obligation_name or None),
            icon="mdi:arrow-right-bold",
            attributes=lambda ctx: {
                "obligation_id": _p(ctx, lambda p: p.next_obligation_id),
                "highest_priority_obligation": _p(
                    ctx, lambda p: p.highest_priority_obligation_name
                ),
                "highest_priority_obligation_id": _p(
                    ctx, lambda p: p.highest_priority_obligation_id
                ),
            },
        ),
        EntityDefinition(
            object_id="fx_next_conversion_amount_usd",
            name="Next conversion amount",
            component="sensor",
            state=lambda ctx: _p(
                ctx,
                lambda p: (
                    decimal_to_str(p.next_conversion_usd)
                    if p.next_conversion_usd is not None
                    else None
                ),
            ),
            icon="mdi:cash-fast",
            unit="USD",
            state_class="measurement",
        ),
        EntityDefinition(
            object_id="fx_next_conversion_amount_nzd",
            name="Next conversion amount in NZD",
            component="sensor",
            state=lambda ctx: _p(ctx, lambda p: decimal_to_str(p.next_conversion_nzd)),
            icon="mdi:cash",
            unit="NZD",
            state_class="measurement",
        ),
        EntityDefinition(
            object_id="fx_debt_strategy_status",
            name="Debt strategy status",
            component="sensor",
            state=lambda ctx: _p(ctx, lambda p: p.strategy_status),
            icon="mdi:strategy",
            attributes=lambda ctx: {
                "warnings": _p(ctx, lambda p: p.warnings),
                "disclaimer": _p(ctx, lambda p: p.disclaimer),
            },
        ),
        EntityDefinition(
            object_id="fx_weighted_break_even_rate",
            name="Weighted break-even rate",
            component="sensor",
            state=lambda ctx: _p(
                ctx,
                lambda p: (
                    decimal_to_str(p.weighted_break_even_rate)
                    if p.weighted_break_even_rate is not None
                    else None
                ),
            ),
            icon="mdi:scale-balance",
            state_class="measurement",
            attributes=lambda ctx: {
                "basis": (
                    "The rate that would repay 30 days of waiting across the whole "
                    "book, weighted by USD required."
                ),
            },
        ),
        EntityDefinition(
            object_id="fx_max_rational_wait_days",
            name="Maximum rational wait",
            component="sensor",
            state=lambda ctx: _p(ctx, lambda p: p.max_rational_wait_days),
            icon="mdi:timer-sand",
            unit="d",
            attributes=lambda ctx: {
                "basis": (
                    "The shortest limit any obligation imposes: the book can only "
                    "wait as long as its most pressing member."
                ),
            },
        ),
    ]


def obligation_definitions(context: EntityContext, device_name: str) -> list[EntityDefinition]:
    """Every obligation entity: the portfolio sensors plus one device each."""
    definitions = portfolio_definitions()
    slugs = unique_slugs(context.obligations)
    for item in context.obligations:
        definitions.extend(definitions_for(item, slugs[item.id], device_name))
    return definitions
