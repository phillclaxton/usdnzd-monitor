"""Home Assistant entity publication tests.

The entity names and the meaning of each state are a public contract: someone's
automations depend on them. These tests pin both.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_config
from app.home_assistant.entities import state_payload
from app.home_assistant.mqtt import (
    MqttPublisher,
    all_definitions,
    availability_topic,
    discovery_payload,
    discovery_topic,
    set_publisher,
    state_topic,
)
from app.schemas.settings import Settings
from app.schemas.strategy import StrategyIn, TrancheIn
from app.services import publisher as publisher_service
from app.services import rate_service, settings_service
from app.services import strategy_service as strategies

#: The entity IDs the product specification lists.
EXPECTED_ENTITY_IDS = {
    "sensor.fx_strategy_usd_nzd_rate",
    "sensor.fx_strategy_rate_age",
    "sensor.fx_strategy_rate_provider",
    "sensor.fx_strategy_rate_zone",
    "sensor.fx_strategy_usd_initial",
    "sensor.fx_strategy_usd_available",
    "sensor.fx_strategy_usd_converted",
    "sensor.fx_strategy_usd_remaining",
    "sensor.fx_strategy_percent_converted",
    "sensor.fx_strategy_nzd_received_gross",
    "sensor.fx_strategy_nzd_received_net",
    "sensor.fx_strategy_total_fees_nzd",
    "sensor.fx_strategy_blended_rate_gross",
    "sensor.fx_strategy_blended_rate_effective",
    "sensor.fx_strategy_next_target_rate",
    "sensor.fx_strategy_next_target_usd",
    "sensor.fx_strategy_next_target_upside_nzd",
    "sensor.fx_strategy_one_cent_exposure_nzd",
    "sensor.fx_strategy_convert_all_now_nzd",
    "sensor.fx_strategy_estimated_wise_fee_nzd",
    "sensor.fx_strategy_days_to_deadline",
    "sensor.fx_strategy_six_month_high",
    "sensor.fx_strategy_six_month_low",
    "sensor.fx_strategy_strategy_status",
    "sensor.fx_strategy_provider_status",
    "binary_sensor.fx_strategy_rate_stale",
    "binary_sensor.fx_strategy_target_reached",
    "binary_sensor.fx_strategy_deadline_warning",
    "binary_sensor.fx_strategy_provider_error",
    "binary_sensor.fx_strategy_wise_connected",
    "binary_sensor.fx_strategy_mqtt_connected",
    "binary_sensor.fx_strategy_attention_required",
    "button.fx_strategy_refresh_rate",
    "button.fx_strategy_test_notification",
    "button.fx_strategy_recalculate",
    "button.fx_strategy_export_backup",
    "button.fx_strategy_reconcile_wise",
    "number.fx_strategy_manual_rate",
    "number.fx_strategy_available_usd",
}


class FakeMqtt(MqttPublisher):
    """A publisher that records what it would have sent."""

    def __init__(self) -> None:
        super().__init__(get_config())
        self.published: list[tuple[str, str, bool]] = []
        self.state.connected = True

    @property
    def configured(self) -> bool:
        return True

    @property
    def connected(self) -> bool:
        return self.state.connected

    async def publish_discovery(self, definitions: Any, settings: Settings) -> int:
        prefix = settings.home_assistant.mqtt_discovery_prefix
        for definition in definitions:
            self.published.append(
                (
                    discovery_topic(definition, prefix),
                    json.dumps(discovery_payload(definition, settings, get_config())),
                    True,
                )
            )
        self.state.discovery_sent = True
        return len(definitions)

    async def publish_states(self, definitions: Any, context: Any, settings: Settings) -> int:
        count = 0
        for definition in definitions:
            if definition.component == "button":
                continue
            self.published.append(
                (state_topic(definition), state_payload(definition, context), True)
            )
            count += 1
        self.state.published_entities = count
        return count

    async def remove_entities(self, definitions: Any, settings: Settings) -> int:
        prefix = settings.home_assistant.mqtt_discovery_prefix
        for definition in definitions:
            self.published.append((discovery_topic(definition, prefix), "", True))
        self.state.discovery_sent = False
        return len(definitions)

    def topic_payload(self, topic_suffix: str) -> str | None:
        for topic, payload, _retain in reversed(self.published):
            if topic.endswith(topic_suffix):
                return payload
        return None


@pytest.fixture
def mqtt() -> FakeMqtt:
    fake = FakeMqtt()
    set_publisher(fake)
    yield fake
    set_publisher(None)


@pytest.fixture
async def settings(session: AsyncSession) -> Settings:
    return await settings_service.load_settings(session)


@pytest.fixture
async def strategy(session: AsyncSession) -> Any:
    created = await strategies.create_strategy(
        session,
        StrategyIn(
            name="Entities",
            initial_source_amount=Decimal("800000"),
            funds_available_amount=Decimal("800000"),
            final_deadline=None,
            walk_away_rate=Decimal("1.7800"),
            tranches=[
                TrancheIn(
                    sequence=1,
                    allocation_type="percentage",
                    allocation_value=Decimal("100"),
                    target_rate=Decimal("1.7600"),
                )
            ],
        ),
    )
    await strategies.activate(session, created)
    await rate_service.record_manual_rate(
        session,
        source_currency="USD",
        target_currency="NZD",
        rate=Decimal("1.7550"),
    )
    # Committed because command handling opens its own session, exactly as it
    # does when a message arrives from the broker.
    await session.commit()
    return created


# ---------------------------------------------------------------------------
# The entity set
# ---------------------------------------------------------------------------


async def test_every_specified_entity_is_published(
    session: AsyncSession, settings: Settings, strategy: Any, mqtt: FakeMqtt
) -> None:
    context = await publisher_service.build_context(session, settings)
    definitions = all_definitions(context, settings)
    produced = {definition.entity_id for definition in definitions}
    missing = EXPECTED_ENTITY_IDS - produced
    assert not missing, f"missing entities: {sorted(missing)}"


async def test_writable_controls_can_be_switched_off(
    session: AsyncSession, settings: Settings, strategy: Any
) -> None:
    settings.home_assistant.expose_writable_controls = False
    context = await publisher_service.build_context(session, settings)
    components = {definition.component for definition in all_definitions(context, settings)}
    assert "number" not in components


async def test_no_writable_entity_exposes_a_target_rate(
    session: AsyncSession, settings: Settings, strategy: Any
) -> None:
    """Targets must change only through the validating, audited API."""
    context = await publisher_service.build_context(session, settings)
    writable = [
        definition
        for definition in all_definitions(context, settings)
        if definition.component in ("number", "select")
    ]
    assert all("target" not in definition.object_id for definition in writable)


# ---------------------------------------------------------------------------
# State values
# ---------------------------------------------------------------------------


async def test_state_values_match_the_dashboard(
    session: AsyncSession, settings: Settings, strategy: Any, mqtt: FakeMqtt
) -> None:
    result = await publisher_service.publish(session, settings, publisher=mqtt)
    assert result.transport == "mqtt"
    assert result.entities > 0

    assert mqtt.topic_payload("fx_strategy_usd_nzd_rate/state") == "1.75500000"
    assert mqtt.topic_payload("fx_strategy_usd_remaining/state") == "800000.00"
    assert mqtt.topic_payload("fx_strategy_one_cent_exposure_nzd/state") == "8000.00"
    assert mqtt.topic_payload("fx_strategy_next_target_rate/state") == "1.76000000"
    assert mqtt.topic_payload("fx_strategy_strategy_status/state") == "active"


async def test_an_uncalculable_figure_is_blank_not_zero(
    session: AsyncSession, settings: Settings, strategy: Any, mqtt: FakeMqtt
) -> None:
    await publisher_service.publish(session, settings, publisher=mqtt)
    # No fee model is configured, so the fee is unknown rather than zero.
    assert mqtt.topic_payload("fx_strategy_estimated_wise_fee_nzd/state") == ""
    assert mqtt.topic_payload("fx_strategy_total_fees_nzd/state") == ""
    assert mqtt.topic_payload("fx_strategy_blended_rate_effective/state") == ""


async def test_binary_sensors_use_on_and_off(
    session: AsyncSession, settings: Settings, strategy: Any, mqtt: FakeMqtt
) -> None:
    await publisher_service.publish(session, settings, publisher=mqtt)
    assert mqtt.topic_payload("fx_strategy_rate_stale/state") == "OFF"
    assert mqtt.topic_payload("fx_strategy_target_reached/state") == "OFF"


async def test_target_reached_turns_on_and_says_nothing_was_converted(
    session: AsyncSession, settings: Settings, strategy: Any, mqtt: FakeMqtt
) -> None:
    await rate_service.record_manual_rate(
        session, source_currency="USD", target_currency="NZD", rate=Decimal("1.7700")
    )
    await session.flush()

    context = await publisher_service.build_context(session, settings)
    definitions = all_definitions(context, settings)
    reached = next(
        definition
        for definition in definitions
        if definition.object_id == "fx_strategy_target_reached"
    )
    assert state_payload(reached, context) == "ON"
    assert reached.attributes is not None
    attributes = reached.attributes(context)
    assert "has not converted anything" in attributes["note"]
    assert attributes["reached_tranches"] == [1]


async def test_the_rate_sensor_carries_the_specified_attributes(
    session: AsyncSession, settings: Settings, strategy: Any
) -> None:
    context = await publisher_service.build_context(session, settings)
    definition = next(
        item
        for item in all_definitions(context, settings)
        if item.object_id == "fx_strategy_usd_nzd_rate"
    )
    assert definition.attributes is not None
    attributes = definition.attributes(context)
    for key in (
        "provider",
        "source_timestamp",
        "retrieved_at",
        "stale",
        "high_24h",
        "low_24h",
        "high_6m",
        "low_6m",
        "next_target",
        "distance_to_target",
    ):
        assert key in attributes
    assert attributes["next_target"] == "1.76000000"
    assert attributes["distance_to_target"] == "0.00500000"


async def test_the_strategy_sensor_carries_the_specified_attributes(
    session: AsyncSession, settings: Settings, strategy: Any
) -> None:
    context = await publisher_service.build_context(session, settings)
    definition = next(
        item
        for item in all_definitions(context, settings)
        if item.object_id == "fx_strategy_strategy_status"
    )
    assert definition.attributes is not None
    attributes = definition.attributes(context)
    assert attributes["strategy_name"] == "Entities"
    assert attributes["tranche_count"] == 1
    assert attributes["completed_tranches"] == 0
    assert attributes["remaining_tranches"] == 1
    assert attributes["walk_away_rate"] == "1.78000000"


async def test_attention_required_explains_itself(
    session: AsyncSession, settings: Settings, strategy: Any
) -> None:
    await rate_service.record_manual_rate(
        session, source_currency="USD", target_currency="NZD", rate=Decimal("1.7700")
    )
    await session.flush()
    context = await publisher_service.build_context(session, settings)
    definition = next(
        item
        for item in all_definitions(context, settings)
        if item.object_id == "fx_strategy_attention_required"
    )
    assert state_payload(definition, context) == "ON"
    assert definition.attributes is not None
    reasons = definition.attributes(context)["reasons"]
    assert any("Nothing has been converted" in reason for reason in reasons)


# ---------------------------------------------------------------------------
# Discovery payloads
# ---------------------------------------------------------------------------


async def test_discovery_payloads_are_well_formed(
    session: AsyncSession, settings: Settings, strategy: Any, mqtt: FakeMqtt
) -> None:
    await publisher_service.publish(session, settings, publisher=mqtt, force_discovery=True)
    configs = [
        (topic, json.loads(payload))
        for topic, payload, _retain in mqtt.published
        if topic.startswith("homeassistant/") and payload
    ]
    assert configs

    topic, payload = next(
        item for item in configs if item[0].endswith("fx_strategy_usd_nzd_rate/config")
    )
    assert topic == "homeassistant/sensor/fx_strategy/fx_strategy_usd_nzd_rate/config"
    assert payload["unique_id"] == "fx_strategy_usd_nzd_rate"
    assert payload["object_id"] == "fx_strategy_usd_nzd_rate"
    assert payload["state_topic"] == "fx_strategy/fx_strategy_usd_nzd_rate/state"
    assert payload["availability_topic"] == availability_topic()
    assert payload["device"]["identifiers"] == ["fx_strategy"]
    assert payload["device"]["name"] == "FX Strategy Manager"


async def test_buttons_declare_a_command_topic_not_a_state_topic(
    session: AsyncSession, settings: Settings, strategy: Any, mqtt: FakeMqtt
) -> None:
    await publisher_service.publish(session, settings, publisher=mqtt, force_discovery=True)
    payload = json.loads(
        mqtt.topic_payload("button/fx_strategy/fx_strategy_refresh_rate/config") or "{}"
    )
    assert payload["command_topic"] == "fx_strategy/fx_strategy_refresh_rate/set"
    assert payload["payload_press"] == "PRESS"
    assert "state_topic" not in payload


async def test_removing_entities_clears_the_retained_configs(
    session: AsyncSession, settings: Settings, strategy: Any, mqtt: FakeMqtt
) -> None:
    context = await publisher_service.build_context(session, settings)
    definitions = all_definitions(context, settings)
    await mqtt.publish_discovery(definitions, settings)
    removed = await mqtt.remove_entities(definitions, settings)
    assert removed == len(definitions)
    # An empty retained payload is what makes Home Assistant drop the entity.
    assert mqtt.topic_payload("fx_strategy_usd_nzd_rate/config") == ""


# ---------------------------------------------------------------------------
# Working without MQTT
# ---------------------------------------------------------------------------


async def test_without_a_broker_or_token_nothing_is_published_but_the_app_works(
    session: AsyncSession, settings: Settings, strategy: Any
) -> None:
    result = await publisher_service.publish(session, settings)
    assert result.transport == "none"
    assert "no entities were published" in result.message


async def test_entity_publication_can_be_switched_off(
    session: AsyncSession, settings: Settings, strategy: Any, mqtt: FakeMqtt
) -> None:
    settings.home_assistant.publish_entities = False
    result = await publisher_service.publish(session, settings, publisher=mqtt)
    assert result.transport == "none"
    assert mqtt.published == []


async def test_the_entity_preview_endpoint_works_without_a_broker(
    client: AsyncClient,
) -> None:
    body = (await client.get("/api/v1/home-assistant/entities")).json()
    ids = {row["entity_id"] for row in body}
    assert "sensor.fx_strategy_usd_nzd_rate" in ids
    assert "binary_sensor.fx_strategy_attention_required" in ids


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def test_a_manual_rate_command_is_validated_like_the_api(
    settings: Settings, strategy: Any
) -> None:
    from app.database import get_sessionmaker

    await publisher_service.handle_command("fx_strategy_manual_rate", "1.7899")

    async with get_sessionmaker()() as check:
        latest = await rate_service.latest_manual_rate(check, "USD", "NZD")
        assert latest is not None
        assert latest.rate == Decimal("1.78990000")


async def test_a_nonsense_command_value_is_rejected(settings: Settings, strategy: Any) -> None:
    with pytest.raises(ValueError, match="not a valid decimal"):
        await publisher_service.handle_command("fx_strategy_manual_rate", "not a rate")


async def test_a_negative_available_amount_is_rejected(settings: Settings, strategy: Any) -> None:
    with pytest.raises(ValueError):
        await publisher_service.handle_command("fx_strategy_available_usd", "-1")


async def test_an_available_amount_above_the_total_is_rejected(
    settings: Settings, strategy: Any
) -> None:
    with pytest.raises(ValueError, match="between 0 and"):
        await publisher_service.handle_command("fx_strategy_available_usd", "900000")


async def test_an_unknown_command_is_ignored_without_raising(
    settings: Settings, strategy: Any
) -> None:
    await publisher_service.handle_command("fx_strategy_not_a_button", "PRESS")
