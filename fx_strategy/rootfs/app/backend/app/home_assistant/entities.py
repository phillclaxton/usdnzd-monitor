"""Entity definitions published to Home Assistant.

Every entity is declared once here, with how to derive its state and attributes
from the rate and the FX position.  Discovery payloads, MQTT state publication
and the REST fallback all read from this one table, so they cannot drift apart.

An entity whose figure cannot be computed publishes an empty state, which Home
Assistant shows as unknown. That is the truthful answer; zero would be a
different and wrong one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.money import decimal_to_str
from app.schemas.position import PositionMetrics, PositionOut
from app.schemas.rates import CurrentRateOut

#: The node ID and entity-ID prefix. Matches the specification's entity names.
NODE_ID = "fx_strategy"

StateFn = Callable[["EntityContext"], Any]
AttrFn = Callable[["EntityContext"], dict[str, Any]]


@dataclass(slots=True)
class EntityContext:
    """Everything the entity state functions can read."""

    rate: CurrentRateOut
    position: PositionOut | None
    metrics: PositionMetrics | None
    zone_label: str | None
    zone_guidance: str | None
    provider_healthy: bool
    provider_message: str
    mqtt_connected: bool
    wise_connected: bool
    simulation: bool

    @property
    def source(self) -> str:
        return self.rate.source_currency

    @property
    def target(self) -> str:
        return self.rate.target_currency


@dataclass(frozen=True, slots=True)
class EntityDefinition:
    """One Home Assistant entity."""

    #: Object ID, which becomes the entity_id suffix.
    object_id: str
    name: str
    component: str  # sensor | binary_sensor | button | number | select
    state: StateFn
    icon: str = ""
    device_class: str = ""
    state_class: str = ""
    unit: str = ""
    entity_category: str = ""
    attributes: AttrFn | None = None
    #: Extra keys merged into the discovery payload (number ranges, select options).
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def entity_id(self) -> str:
        return f"{self.component}.{self.object_id}"


def _d(value: Decimal | None) -> str | None:
    return decimal_to_str(value)


def _money(value: Decimal | None) -> str | None:
    """Money for a Home Assistant sensor: two places, still a string."""
    if value is None:
        return None
    return format(value.quantize(Decimal("0.01")), "f")


def _metric(context: EntityContext, getter: Callable[[PositionMetrics], Any]) -> Any:
    return getter(context.metrics) if context.metrics is not None else None


def build_definitions(context: EntityContext) -> list[EntityDefinition]:
    """The full entity set."""
    source = context.source.lower()
    target = context.target.lower()
    pair = f"{source}_{target}"
    money_unit = context.target

    def metric(getter: Callable[[PositionMetrics], Any]) -> StateFn:
        return lambda ctx: _metric(ctx, getter)

    sensors: list[EntityDefinition] = [
        EntityDefinition(
            object_id=f"{NODE_ID}_{pair}_rate",
            name=f"{context.source}/{context.target} rate",
            component="sensor",
            icon="mdi:currency-usd",
            state_class="measurement",
            state=lambda ctx: _d(ctx.rate.rate),
            attributes=lambda ctx: {
                "provider": ctx.rate.provider,
                "quote_type": ctx.rate.quote_type,
                "quote_label": ctx.rate.quote_label,
                "source_timestamp": (
                    ctx.rate.provider_timestamp.isoformat() if ctx.rate.provider_timestamp else None
                ),
                "retrieved_at": (
                    ctx.rate.retrieved_at.isoformat() if ctx.rate.retrieved_at else None
                ),
                "status": ctx.rate.status,
                "stale": ctx.rate.status == "stale",
                "high_24h": _d(ctx.rate.high_24h),
                "low_24h": _d(ctx.rate.low_24h),
                "change_24h": _d(ctx.rate.changes.twenty_four_hours),
                "high_6m": _d(ctx.rate.high_6m),
                "low_6m": _d(ctx.rate.low_6m),
                "baseline_rate": (
                    _d(ctx.position.baseline_rate) if ctx.position is not None else None
                ),
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_rate_age",
            name="Rate age",
            component="sensor",
            icon="mdi:clock-outline",
            device_class="duration",
            unit="s",
            state_class="measurement",
            state=lambda ctx: ctx.rate.age_seconds,
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_rate_provider",
            name="Rate provider",
            component="sensor",
            icon="mdi:cloud-outline",
            state=lambda ctx: ctx.rate.provider or "none",
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_rate_zone",
            name="Rate zone",
            component="sensor",
            icon="mdi:gauge",
            state=lambda ctx: ctx.zone_label,
            attributes=lambda ctx: {
                "guidance": ctx.zone_guidance,
                "note": "Zone labels are your own configuration, not a forecast.",
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_six_month_high",
            name="Six month high",
            component="sensor",
            icon="mdi:arrow-up-bold",
            state=lambda ctx: _d(ctx.rate.high_6m),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_six_month_low",
            name="Six month low",
            component="sensor",
            icon="mdi:arrow-down-bold",
            state=lambda ctx: _d(ctx.rate.low_6m),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_{source}_remaining",
            name=f"{context.source} still held",
            component="sensor",
            icon="mdi:cash-clock",
            unit=context.source,
            state=lambda ctx: (
                _money(ctx.position.current_source_balance) if ctx.position is not None else None
            ),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_{target}_value",
            name=f"{context.target} value of what is held",
            component="sensor",
            icon="mdi:cash",
            unit=money_unit,
            state=metric(lambda m: _money(m.current_target_value)),
            attributes=lambda ctx: {
                "rate_status": _metric(ctx, lambda m: m.rate_status),
                "note": "Blank when no trusted rate has arrived. Not the same as zero.",
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_{source}_converted",
            name=f"{context.source} converted",
            component="sensor",
            icon="mdi:cash-check",
            unit=context.source,
            state=metric(lambda m: _money(m.total_source_converted)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_{target}_received_gross",
            name=f"{context.target} received (gross)",
            component="sensor",
            icon="mdi:cash-plus",
            unit=money_unit,
            state=metric(lambda m: _money(m.total_target_received)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_total_fees_{target}",
            name=f"Total fees ({context.target})",
            component="sensor",
            icon="mdi:cash-minus",
            unit=money_unit,
            state=metric(lambda m: _money(m.total_fees_target)),
            attributes=lambda ctx: {
                "recorded": _metric(ctx, lambda m: m.total_fees_target is not None),
                "note": "Blank when no conversion recorded a fee. Not the same as a zero fee.",
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_realised_improvement_{target}",
            name=f"Realised improvement ({context.target})",
            component="sensor",
            icon="mdi:cash-plus",
            unit=money_unit,
            #: The *confirmed* half. Anything reconstructed rather than read off
            #: a receipt stays in the attributes, so an automation reading the
            #: state alone cannot pick up an estimate as a fact.
            state=metric(lambda m: _money(m.realised.confirmed)),
            attributes=lambda ctx: {
                "estimated_additional": _metric(ctx, lambda m: _money(m.realised.estimated)),
                "total_including_estimates": _metric(ctx, lambda m: _money(m.realised.total)),
                "includes_estimates": _metric(ctx, lambda m: m.realised.includes_estimates),
                "basis": "Rate improvement against the baseline, on what actually converted.",
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_unrealised_improvement_{target}",
            name=f"Unrealised improvement ({context.target})",
            component="sensor",
            icon="mdi:trending-up",
            unit=money_unit,
            state=metric(lambda m: _money(m.unrealised_improvement)),
            attributes=lambda _ctx: {
                "basis": "On paper, on what is still held, against the baseline rate.",
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_total_improvement_{target}",
            name=f"Total improvement ({context.target})",
            component="sensor",
            icon="mdi:chart-line",
            unit=money_unit,
            state=metric(lambda m: _money(m.total_improvement_confirmed)),
            attributes=lambda _ctx: {
                "basis": "Confirmed realised plus unrealised. Estimated rows are not included.",
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_offset_shortfall_{target}",
            name=f"Offset shortfall ({context.target})",
            component="sensor",
            icon="mdi:home-percent",
            unit=money_unit,
            state=lambda ctx: (
                _money(ctx.position.current_offset_shortfall_nzd)
                if ctx.position is not None
                else None
            ),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_daily_carrying_cost_{target}",
            name=f"Daily carrying cost ({context.target})",
            component="sensor",
            icon="mdi:cash-minus",
            unit=money_unit,
            state=metric(lambda m: _money(m.daily_carrying_cost)),
            attributes=lambda ctx: {
                "monthly": _metric(ctx, lambda m: _money(m.monthly_carrying_cost)),
                "basis": (
                    "The floating loan rate on the unfunded part of the offset, "
                    "never on the whole balance."
                ),
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_months_of_burn",
            name="Months of spending held",
            component="sensor",
            icon="mdi:calendar-month",
            unit="months",
            state=metric(lambda m: _money(m.months_of_burn)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_provider_status",
            name="Provider status",
            component="sensor",
            icon="mdi:server-network",
            entity_category="diagnostic",
            state=lambda ctx: "healthy" if ctx.provider_healthy else "failing",
            attributes=lambda ctx: {"detail": ctx.provider_message},
        ),
    ]

    binary_sensors = [
        EntityDefinition(
            object_id=f"{NODE_ID}_rate_stale",
            name="Rate stale",
            component="binary_sensor",
            device_class="problem",
            entity_category="diagnostic",
            state=lambda ctx: ctx.rate.status in ("stale", "unavailable"),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_position_saved",
            name="Position saved",
            component="binary_sensor",
            icon="mdi:clipboard-check",
            state=lambda ctx: ctx.position is not None,
            attributes=lambda ctx: {
                "note": (
                    "Off until a position has been entered. Every figure derived "
                    "from it is unknown while this is off."
                ),
                "updated_at": (
                    ctx.position.updated_at.isoformat() if ctx.position is not None else None
                ),
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_provider_error",
            name="Provider error",
            component="binary_sensor",
            device_class="problem",
            entity_category="diagnostic",
            state=lambda ctx: not ctx.provider_healthy,
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_wise_connected",
            name="Wise connected",
            component="binary_sensor",
            device_class="connectivity",
            entity_category="diagnostic",
            state=lambda ctx: ctx.wise_connected,
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_mqtt_connected",
            name="MQTT connected",
            component="binary_sensor",
            device_class="connectivity",
            entity_category="diagnostic",
            state=lambda ctx: ctx.mqtt_connected,
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_attention_required",
            name="Attention required",
            component="binary_sensor",
            device_class="problem",
            state=lambda ctx: bool(_attention_reasons(ctx)),
            attributes=lambda ctx: {
                "reasons": _attention_reasons(ctx),
            },
        ),
    ]

    buttons = [
        EntityDefinition(
            object_id=f"{NODE_ID}_refresh_rate",
            name="Refresh rate",
            component="button",
            icon="mdi:refresh",
            state=lambda _ctx: None,
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_test_notification",
            name="Test notification",
            component="button",
            icon="mdi:bell-ring",
            entity_category="config",
            state=lambda _ctx: None,
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_export_backup",
            name="Export backup",
            component="button",
            icon="mdi:database-export",
            entity_category="config",
            state=lambda _ctx: None,
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_reconcile_wise",
            name="Reconcile Wise",
            component="button",
            icon="mdi:sync",
            entity_category="config",
            state=lambda _ctx: None,
        ),
    ]

    return [*sensors, *binary_sensors, *buttons]


def _attention_reasons(context: EntityContext) -> list[str]:
    """Why the app thinks something needs a person.

    Deliberately short: a rate it cannot trust, a provider that is failing, and
    nothing entered to measure against. A rate that has *moved* is not on this
    list — that is what the alerts are for, and it needs no attention.
    """
    reasons: list[str] = []
    if context.rate.status in ("stale", "unavailable"):
        reasons.append("The rate is stale or unavailable.")
    if not context.provider_healthy:
        reasons.append(context.provider_message or "A rate provider is failing.")
    if context.position is None:
        reasons.append("No position has been entered, so nothing can be valued.")
    return reasons


def writable_definitions(_context: EntityContext) -> list[EntityDefinition]:
    """Optional writable controls.

    The balance is deliberately absent: it is the figure every other one is
    derived from, and changing it has to go through the validating, audited API
    rather than a number box with no record of who moved it.
    """
    return [
        EntityDefinition(
            object_id=f"{NODE_ID}_manual_rate",
            name="Manual rate",
            component="number",
            icon="mdi:pencil",
            entity_category="config",
            state=lambda ctx: _d(ctx.rate.rate),
            extra={"min": 0, "max": 1000, "step": 0.0001, "mode": "box"},
        ),
    ]


def state_payload(definition: EntityDefinition, context: EntityContext) -> str:
    """Render an entity's state for MQTT.

    Home Assistant treats an empty string as unknown, which is exactly what an
    uncalculable figure should be — never zero.
    """
    value = definition.state(context)
    if value is None:
        return ""
    if definition.component == "binary_sensor":
        return "ON" if value else "OFF"
    return str(value)
