"""Publishing entity state to Home Assistant, and handling commands from it.

MQTT discovery is used when a broker is configured.  Without one the app falls
back to writing a smaller set of states over the REST API, and says so — those
states do not survive a Home Assistant restart, which is why MQTT is preferred.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_config
from app.database import get_sessionmaker
from app.home_assistant.client import HomeAssistantError, get_home_assistant
from app.home_assistant.entities import (
    EntityContext,
    EntityDefinition,
    state_payload,
)
from app.home_assistant.mqtt import MqttPublisher, all_definitions, get_publisher
from app.logging_setup import get_logger
from app.money import MoneyError, quantize_money, quantize_rate
from app.scheduler.jobs import build_registry
from app.schemas.rates import CurrentRateOut, RateChanges
from app.schemas.settings import Settings
from app.schemas.strategy import StrategySummaryOut
from app.services import rate_service, settings_service, summary_service
from app.services import strategy_service as strategies
from app.services.audit import new_correlation_id

log = get_logger(__name__)

#: The subset published over REST when there is no broker. Keeping it small
#: keeps the fallback honest: it is a convenience, not a replacement.
REST_FALLBACK_OBJECT_IDS = (
    "fx_strategy_usd_nzd_rate",
    "fx_strategy_usd_remaining",
    "fx_strategy_one_cent_exposure_nzd",
    "fx_strategy_next_target_rate",
    "fx_strategy_strategy_status",
)


@dataclass(slots=True)
class PublishResult:
    transport: str
    entities: int = 0
    discovery: int = 0
    errors: list[str] = field(default_factory=list)
    message: str = ""


async def build_context(session: AsyncSession, settings: Settings) -> EntityContext:
    """Gather everything the entity definitions read."""
    current = await rate_service.current_rate(session, settings)
    sample = current.sample
    from app.providers.base import QUOTE_TYPE_LABEL, QuoteType

    quote_type = QuoteType(sample.quote_type) if sample else None
    rate_out = CurrentRateOut(
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
    )

    strategy = await strategies.active_strategy(session, settings)
    summary: StrategySummaryOut | None = None
    if strategy is not None:
        summary = await summary_service.build_summary(session, strategy, settings)

    statuses = await rate_service.provider_statuses(session)
    # A provider that is merely not set up is not a problem to report. The
    # manual fallback with no rate entered is the usual case.
    registry = await build_registry(session, settings)
    try:
        unconfigured = {d.name for d in registry.describe() if not d.configured}
    finally:
        await registry.aclose()
    unhealthy = [
        status for status in statuses if not status.healthy and status.provider not in unconfigured
    ]
    publisher = get_publisher()

    # The obligations book, if the feature is in use. A failure here must not
    # take down publication of the rate and strategy entities.
    portfolio = None
    obligation_rows: list[Any] = []
    try:
        from app.api.v1.obligations import _to_out
        from app.schemas.obligation import PortfolioOut
        from app.services import obligation_service

        ranked = await obligation_service.analyse_all(session, settings)
        if ranked:
            rate_context = await obligation_service.rate_context(session, settings)
            portfolio = PortfolioOut.model_validate(
                obligation_service.build_portfolio(ranked, rate_context),
                from_attributes=True,
            )
            obligation_rows = [_to_out(item) for item in ranked]
    except Exception:  # pragma: no cover - defensive
        log.warning("obligation_entities_unavailable", exc_info=True)

    return EntityContext(
        rate=rate_out,
        summary=summary,
        provider_healthy=not unhealthy,
        provider_message=(
            f"{unhealthy[0].provider}: {unhealthy[0].last_error}"
            if unhealthy
            else "All providers healthy."
        ),
        mqtt_connected=publisher.connected,
        wise_connected=settings.providers.wise.enabled,
        simulation=settings.simulation.enabled,
        portfolio=portfolio,
        obligations=obligation_rows,
    )


async def publish(
    session: AsyncSession,
    settings: Settings,
    *,
    publisher: MqttPublisher | None = None,
    force_discovery: bool = False,
) -> PublishResult:
    """Publish the current entity state through whichever transport is available."""
    if not settings.home_assistant.publish_entities:
        return PublishResult(transport="none", message="Entity publication is switched off.")

    context = await build_context(session, settings)
    definitions = all_definitions(context, settings)
    mqtt = publisher or get_publisher()

    if mqtt.connected:
        discovery = 0
        if force_discovery or not mqtt.state.discovery_sent:
            discovery = await mqtt.publish_discovery(definitions, settings)
        published = await mqtt.publish_states(definitions, context, settings)
        return PublishResult(transport="mqtt", entities=published, discovery=discovery)

    return await _publish_over_rest(context, definitions)


async def _publish_over_rest(
    context: EntityContext, definitions: list[EntityDefinition]
) -> PublishResult:
    """Fallback publication for installations without a broker."""
    client = get_home_assistant()
    if not client.configured:
        return PublishResult(
            transport="none",
            message=("No MQTT broker and no Supervisor token, so no entities were published."),
        )

    errors: list[str] = []
    published = 0
    wanted = {
        definition.object_id: definition
        for definition in definitions
        if definition.object_id in REST_FALLBACK_OBJECT_IDS
    }
    for definition in wanted.values():
        value = state_payload(definition, context)
        attributes = definition.attributes(context) if definition.attributes else {}
        attributes["friendly_name"] = definition.name
        attributes["published_via"] = "rest_fallback"
        try:
            await client.set_state(definition.entity_id, value or "unknown", attributes)
            published += 1
        except HomeAssistantError as exc:
            errors.append(f"{definition.entity_id}: {exc.message}")

    return PublishResult(
        transport="rest",
        entities=published,
        errors=errors,
        message=(
            "Published over the REST API. These states do not survive a Home Assistant "
            "restart; configure an MQTT broker for proper entities."
        ),
    )


# ---------------------------------------------------------------------------
# Commands from Home Assistant
# ---------------------------------------------------------------------------


async def handle_command(object_id: str, payload: str) -> None:
    """Execute a button press or writable-entity change.

    Every command is validated exactly as the equivalent API call would be;
    nothing arriving over MQTT bypasses a rule.
    """
    new_correlation_id()
    log.info("mqtt_command", object_id=object_id)

    async with get_sessionmaker()() as session:
        try:
            settings = await settings_service.load_settings(session)
            await _dispatch(session, settings, object_id, payload)
            await session.commit()
        except Exception:
            await session.rollback()
            log.exception("command_failed", object_id=object_id)
            raise


async def _dispatch(
    session: AsyncSession, settings: Settings, object_id: str, payload: str
) -> None:
    from app.scheduler.jobs import build_registry
    from app.services import monitor

    if object_id == "fx_strategy_refresh_rate":
        registry = await build_registry(session, settings)
        try:
            await rate_service.refresh_rate(
                session, settings, registry, respect_backoff=False, actor="home_assistant"
            )
        finally:
            await registry.aclose()
        return

    if object_id == "fx_strategy_test_notification":
        await monitor.send_test_notification(session, settings)
        return

    if object_id in ("fx_strategy_recalculate", "fx_strategy_export_backup"):
        strategy = await strategies.active_strategy(session, settings)
        if strategy is not None:
            strategies.recalculate_allocations(strategy)
        await publish(session, settings, force_discovery=True)
        return

    if object_id == "fx_strategy_reconcile_wise":
        log.info("reconcile_requested_via_mqtt")
        return

    if object_id == "fx_strategy_manual_rate":
        rate = _parse_decimal(payload, "rate")
        await rate_service.record_manual_rate(
            session,
            source_currency=settings.general.source_currency,
            target_currency=settings.general.target_currency,
            rate=quantize_rate(rate),
            note="Set from Home Assistant",
            simulated=settings.simulation.enabled,
            actor="home_assistant",
        )
        return

    if object_id.startswith("fx_strategy_available_"):
        amount = quantize_money(_parse_decimal(payload, "amount"))
        strategy = await strategies.active_strategy(session, settings)
        if strategy is None:
            raise ValueError("There is no active strategy to update.")
        if amount < 0 or amount > strategy.initial_source_amount:
            raise ValueError(
                f"Available funds must be between 0 and {strategy.initial_source_amount}."
            )
        from app.models.audit import AuditEventType
        from app.services import audit

        before = strategy.funds_available_amount
        strategy.funds_available_amount = amount
        await session.flush()
        await audit.record(
            session,
            event_type=AuditEventType.UPDATED,
            entity_type="strategy",
            entity_id=strategy.id,
            message=f"Available funds set to {amount} from Home Assistant",
            before={"funds_available_amount": before},
            after={"funds_available_amount": amount},
            actor="home_assistant",
        )
        return

    log.warning("unknown_command", object_id=object_id)


def _parse_decimal(payload: str, field: str) -> Decimal:
    from app.money import to_decimal

    try:
        value = to_decimal(payload.strip(), field=field)
    except MoneyError as exc:
        raise ValueError(str(exc)) from exc
    if value <= 0:
        raise ValueError(f"{field} must be greater than zero.")
    return value


# ---------------------------------------------------------------------------
# Lifecycle helpers
# ---------------------------------------------------------------------------


async def start_publisher(session: AsyncSession, settings: Settings) -> None:
    publisher = get_publisher()
    publisher.on_command(handle_command)
    await publisher.start(settings)


async def stop_publisher(settings: Settings) -> None:
    publisher = get_publisher()
    try:
        await publisher.publish_offline(settings)
    except Exception:
        log.debug("offline_publish_failed")
    await publisher.stop()


async def after_refresh(session: AsyncSession, settings: Settings, _outcome: Any) -> None:
    """Scheduler callback: keep the entities in step with the latest sample."""
    try:
        await publish(session, settings)
    except Exception:
        log.exception("entity_publish_failed")


def diagnostics() -> dict[str, Any]:
    publisher = get_publisher()
    config = get_config()
    return {
        "mqtt_configured": config.mqtt_configured,
        "mqtt_connected": publisher.connected,
        "mqtt_last_error": publisher.state.last_error,
        "mqtt_last_publish_at": publisher.state.last_publish_at,
        "mqtt_entities_published": publisher.state.published_entities,
        "mqtt_discovery_sent": publisher.state.discovery_sent,
        "mqtt_commands_received": publisher.state.commands_received,
    }
