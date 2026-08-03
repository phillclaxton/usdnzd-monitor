"""Entity definitions published to Home Assistant.

Every entity is declared once here, with how to derive its state and attributes
from the dashboard summary.  Discovery payloads, MQTT state publication and the
REST fallback all read from this one table, so they cannot drift apart.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.money import decimal_to_str
from app.schemas.obligation import ObligationOut, PortfolioOut
from app.schemas.rates import CurrentRateOut
from app.schemas.strategy import StrategySummaryOut

#: The node ID and entity-ID prefix. Matches the specification's entity names.
NODE_ID = "fx_strategy"

StateFn = Callable[["EntityContext"], Any]
AttrFn = Callable[["EntityContext"], dict[str, Any]]


@dataclass(slots=True)
class EntityContext:
    """Everything the entity state functions can read."""

    rate: CurrentRateOut
    summary: StrategySummaryOut | None
    provider_healthy: bool
    provider_message: str
    mqtt_connected: bool
    wise_connected: bool
    simulation: bool
    #: The obligations book. Absent when the feature is unused.
    portfolio: PortfolioOut | None = None
    obligations: list[ObligationOut] = field(default_factory=list)

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
    #: Overrides the app-level device, so an obligation can be its own device in
    #: Home Assistant rather than another row on one enormous one.
    device: dict[str, Any] | None = None

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


def _summary_value(context: EntityContext, getter: Callable[[StrategySummaryOut], Any]) -> Any:
    return getter(context.summary) if context.summary is not None else None


def build_definitions(context: EntityContext) -> list[EntityDefinition]:
    """The full entity set, named exactly as the specification lists them."""
    source = context.source.lower()
    target = context.target.lower()
    pair = f"{source}_{target}"
    money_unit = context.target

    def summary(getter: Callable[[StrategySummaryOut], Any]) -> StateFn:
        return lambda ctx: _summary_value(ctx, getter)

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
                "next_target": _summary_value(ctx, lambda s: _d(s.next_target_rate)),
                "distance_to_target": _summary_value(
                    ctx,
                    lambda s: (
                        _d(s.next_target_rate - ctx.rate.rate)
                        if s.next_target_rate is not None and ctx.rate.rate is not None
                        else None
                    ),
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
            state=summary(lambda s: s.rate_zone.label if s.rate_zone else None),
            attributes=lambda ctx: {
                "guidance": _summary_value(
                    ctx, lambda s: s.rate_zone.guidance if s.rate_zone else None
                ),
                "note": "Zone labels are your own configuration, not a forecast.",
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_{source}_initial",
            name=f"{context.source} initial",
            component="sensor",
            icon="mdi:cash",
            unit=context.source,
            state=summary(lambda s: _money(s.initial_source_amount)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_{source}_available",
            name=f"{context.source} available",
            component="sensor",
            icon="mdi:cash",
            unit=context.source,
            state=summary(lambda s: _money(s.available_source_amount)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_{source}_converted",
            name=f"{context.source} converted",
            component="sensor",
            icon="mdi:cash-check",
            unit=context.source,
            state=summary(lambda s: _money(s.converted_source_amount)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_{source}_remaining",
            name=f"{context.source} remaining",
            component="sensor",
            icon="mdi:cash-clock",
            unit=context.source,
            state=summary(lambda s: _money(s.remaining_source_amount)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_percent_converted",
            name="Percent converted",
            component="sensor",
            icon="mdi:percent",
            unit="%",
            state_class="measurement",
            state=summary(lambda s: _money(s.percent_converted)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_{target}_received_gross",
            name=f"{context.target} received (gross)",
            component="sensor",
            icon="mdi:cash-plus",
            unit=money_unit,
            state=summary(lambda s: _money(s.gross_target_received)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_{target}_received_net",
            name=f"{context.target} received (net)",
            component="sensor",
            icon="mdi:cash-plus",
            unit=money_unit,
            state=summary(lambda s: _money(s.net_target_received)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_total_fees_{target}",
            name=f"Total fees ({context.target})",
            component="sensor",
            icon="mdi:cash-minus",
            unit=money_unit,
            state=summary(lambda s: _money(s.total_fees)),
            attributes=lambda ctx: {
                "recorded": _summary_value(ctx, lambda s: s.total_fees is not None),
                "note": "Blank when no conversion recorded a fee. Not the same as a zero fee.",
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_blended_rate_gross",
            name="Blended rate (gross)",
            component="sensor",
            icon="mdi:chart-line",
            state=summary(lambda s: _d(s.blended_gross_rate)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_blended_rate_effective",
            name="Blended rate (effective)",
            component="sensor",
            icon="mdi:chart-line",
            state=summary(lambda s: _d(s.blended_effective_rate)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_next_target_rate",
            name="Next target rate",
            component="sensor",
            icon="mdi:target",
            state=summary(lambda s: _d(s.next_target_rate)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_next_target_{source}",
            name=f"Next target {context.source}",
            component="sensor",
            icon="mdi:target",
            unit=context.source,
            state=summary(lambda s: _money(s.next_target_source_amount)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_next_target_upside_{target}",
            name=f"Next target upside ({context.target})",
            component="sensor",
            icon="mdi:trending-up",
            unit=money_unit,
            state=summary(lambda s: _money(s.next_target_upside)),
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_one_cent_exposure_{target}",
            name=f"One cent exposure ({context.target})",
            component="sensor",
            icon="mdi:swap-vertical",
            unit=money_unit,
            state=summary(lambda s: _money(s.one_cent_exposure)),
            attributes=lambda ctx: {
                "basis": "A 0.0100 move on the amount still unconverted.",
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_convert_all_now_{target}",
            name=f"Convert all now ({context.target})",
            component="sensor",
            icon="mdi:cash-fast",
            unit=money_unit,
            state=summary(
                lambda s: _money(
                    s.convert_all_now.gross_target_amount if s.convert_all_now else None
                )
            ),
            attributes=lambda ctx: {
                "quality": "gross",
                "estimated_net": _summary_value(
                    ctx,
                    lambda s: _money(
                        s.convert_all_now.net_target_amount if s.convert_all_now else None
                    ),
                ),
                "fee_label": _summary_value(
                    ctx, lambda s: s.convert_all_now.fee.label if s.convert_all_now else None
                ),
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_estimated_wise_fee_{target}",
            name=f"Estimated fee ({context.target})",
            component="sensor",
            icon="mdi:cash-minus",
            unit=money_unit,
            state=summary(
                lambda s: _money(
                    s.convert_all_now.fee.amount_target_currency if s.convert_all_now else None
                )
            ),
            attributes=lambda ctx: {
                "basis": _summary_value(
                    ctx, lambda s: s.convert_all_now.fee.basis if s.convert_all_now else None
                ),
                "is_estimate": True,
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_days_to_deadline",
            name="Days to deadline",
            component="sensor",
            icon="mdi:calendar-clock",
            unit="d",
            state=summary(lambda s: s.days_to_deadline),
            attributes=lambda ctx: {
                "severity": _summary_value(ctx, lambda s: s.deadline_severity),
                "message": _summary_value(ctx, lambda s: s.deadline_message),
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
            object_id=f"{NODE_ID}_strategy_status",
            name="Strategy status",
            component="sensor",
            icon="mdi:ladder",
            state=summary(lambda s: s.strategy.status),
            attributes=lambda ctx: {
                "strategy_id": _summary_value(ctx, lambda s: s.strategy.id),
                "strategy_name": _summary_value(ctx, lambda s: s.strategy.name),
                "tranche_count": _summary_value(ctx, lambda s: len(s.strategy.tranches)),
                "completed_tranches": _summary_value(
                    ctx,
                    lambda s: sum(1 for t in s.strategy.tranches if t.status == "completed"),
                ),
                "remaining_tranches": _summary_value(
                    ctx,
                    lambda s: sum(
                        1
                        for t in s.strategy.tranches
                        if t.status not in ("completed", "skipped", "cancelled")
                    ),
                ),
                "final_deadline": _summary_value(
                    ctx,
                    lambda s: (
                        s.strategy.final_deadline.isoformat() if s.strategy.final_deadline else None
                    ),
                ),
                "walk_away_rate": _summary_value(ctx, lambda s: _d(s.strategy.walk_away_rate)),
            },
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
            object_id=f"{NODE_ID}_target_reached",
            name="Target reached",
            component="binary_sensor",
            icon="mdi:target",
            state=summary(lambda s: any(row.target_reached_now for row in s.tranche_progress)),
            attributes=lambda ctx: {
                "note": (
                    "A reached target has not converted anything. Record the conversion "
                    "once your provider has performed it."
                ),
                "reached_tranches": _summary_value(
                    ctx,
                    lambda s: [
                        row.tranche.sequence for row in s.tranche_progress if row.target_reached_now
                    ],
                ),
            },
        ),
        EntityDefinition(
            object_id=f"{NODE_ID}_deadline_warning",
            name="Deadline warning",
            component="binary_sensor",
            device_class="problem",
            state=summary(lambda s: s.deadline_severity in ("warning", "critical", "overdue")),
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
            state=lambda ctx: bool(
                ctx.rate.status in ("stale", "unavailable")
                or not ctx.provider_healthy
                or (
                    ctx.summary is not None
                    and ctx.summary.deadline_severity in ("warning", "critical", "overdue")
                )
                or (
                    ctx.summary is not None
                    and any(row.target_reached_now for row in ctx.summary.tranche_progress)
                )
            ),
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
            object_id=f"{NODE_ID}_recalculate",
            name="Recalculate",
            component="button",
            icon="mdi:calculator",
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
    reasons: list[str] = []
    if context.rate.status in ("stale", "unavailable"):
        reasons.append("The rate is stale or unavailable.")
    if not context.provider_healthy:
        reasons.append(context.provider_message or "A rate provider is failing.")
    if context.summary is not None:
        if context.summary.deadline_severity in ("warning", "critical", "overdue"):
            reasons.append(context.summary.deadline_message)
        reached = [
            row.tranche.sequence
            for row in context.summary.tranche_progress
            if row.target_reached_now
        ]
        if reached:
            reasons.append(
                f"Target reached for tranche(s) {', '.join(str(item) for item in reached)}. "
                "Nothing has been converted."
            )
    return reasons


def writable_definitions(context: EntityContext) -> list[EntityDefinition]:
    """Optional writable controls.

    Target rates are deliberately absent: changing one has to go through the
    validating, audited API, not through a number entity.
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
        EntityDefinition(
            object_id=f"{NODE_ID}_available_{context.source.lower()}",
            name=f"Available {context.source}",
            component="number",
            icon="mdi:cash",
            entity_category="config",
            unit=context.source,
            state=lambda ctx: _summary_value(ctx, lambda s: _money(s.available_source_amount)),
            extra={"min": 0, "max": 1_000_000_000, "step": 0.01, "mode": "box"},
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
