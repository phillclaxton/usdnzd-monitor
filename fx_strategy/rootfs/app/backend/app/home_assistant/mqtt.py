"""MQTT discovery publisher.

Preferred over the REST fallback because discovery gives Home Assistant real
entities with attributes and availability, which survive a restart of either
side.  MQTT is never required: without a broker the app runs normally and
publishes a reduced set of entities over REST.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.config import AppConfig, get_config
from app.database import utcnow
from app.home_assistant.entities import (
    NODE_ID,
    EntityContext,
    EntityDefinition,
    build_definitions,
    state_payload,
    writable_definitions,
)
from app.logging_setup import get_logger
from app.schemas.settings import Settings

log = get_logger(__name__)

ONLINE = "online"
OFFLINE = "offline"

CommandHandler = Callable[[str, str], Awaitable[None]]


@dataclass(slots=True)
class MqttState:
    connected: bool = False
    last_error: str = ""
    last_publish_at: str = ""
    published_entities: int = 0
    discovery_sent: bool = False
    commands_received: int = 0
    topics: list[str] = field(default_factory=list)


def availability_topic(node_id: str = NODE_ID) -> str:
    return f"{node_id}/status"


def state_topic(definition: EntityDefinition, node_id: str = NODE_ID) -> str:
    return f"{node_id}/{definition.object_id}/state"


def attributes_topic(definition: EntityDefinition, node_id: str = NODE_ID) -> str:
    return f"{node_id}/{definition.object_id}/attributes"


def command_topic(definition: EntityDefinition, node_id: str = NODE_ID) -> str:
    return f"{node_id}/{definition.object_id}/set"


def discovery_topic(definition: EntityDefinition, prefix: str, node_id: str = NODE_ID) -> str:
    return f"{prefix}/{definition.component}/{node_id}/{definition.object_id}/config"


def device_payload(settings: Settings, config: AppConfig) -> dict[str, Any]:
    return {
        "identifiers": [NODE_ID],
        "name": settings.home_assistant.device_name,
        "manufacturer": "FX Strategy Manager",
        "model": "Home Assistant app",
        "sw_version": config.app_version,
    }


def discovery_payload(
    definition: EntityDefinition, settings: Settings, config: AppConfig
) -> dict[str, Any]:
    """Build one MQTT discovery config message."""
    node_id = settings.home_assistant.node_id or NODE_ID
    payload: dict[str, Any] = {
        "name": definition.name,
        "unique_id": definition.object_id,
        "object_id": definition.object_id,
        "device": device_payload(settings, config),
        "availability_topic": availability_topic(node_id),
        "payload_available": ONLINE,
        "payload_not_available": OFFLINE,
    }
    if definition.component == "button":
        payload["command_topic"] = command_topic(definition, node_id)
        payload["payload_press"] = "PRESS"
    else:
        payload["state_topic"] = state_topic(definition, node_id)
        payload["json_attributes_topic"] = attributes_topic(definition, node_id)
    if definition.component in ("number", "select"):
        payload["command_topic"] = command_topic(definition, node_id)

    for key, value in (
        ("icon", definition.icon),
        ("device_class", definition.device_class),
        ("state_class", definition.state_class),
        ("unit_of_measurement", definition.unit),
        ("entity_category", definition.entity_category),
    ):
        if value:
            payload[key] = value
    payload.update(definition.extra)
    return payload


class MqttPublisher:
    """Owns the broker connection, discovery and state publication."""

    def __init__(self, config: AppConfig | None = None) -> None:
        self._config = config or get_config()
        self.state = MqttState()
        self._client: Any = None
        self._task: asyncio.Task[None] | None = None
        self._handler: CommandHandler | None = None
        self._stop = asyncio.Event()

    @property
    def configured(self) -> bool:
        return self._config.mqtt_configured

    @property
    def connected(self) -> bool:
        return self.state.connected

    def on_command(self, handler: CommandHandler) -> None:
        self._handler = handler

    # -- connection --------------------------------------------------------

    def _build_client(self, settings: Settings) -> Any:
        import aiomqtt

        node_id = settings.home_assistant.node_id or NODE_ID
        return aiomqtt.Client(
            hostname=self._config.mqtt_host,
            port=self._config.mqtt_port,
            username=self._config.mqtt_username or None,
            password=self._config.mqtt_password or None,
            identifier=f"{NODE_ID}-{self._config.app_version}",
            # The broker announces the app offline if the connection drops, so
            # Home Assistant marks the entities unavailable rather than showing
            # a frozen last value as if it were current.
            will=aiomqtt.Will(
                topic=availability_topic(node_id), payload=OFFLINE, qos=1, retain=True
            ),
        )

    async def start(self, settings: Settings) -> None:
        """Begin the connection loop. Safe to call when MQTT is not configured."""
        if not self.configured:
            log.info("mqtt_not_configured")
            return
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(settings), name="mqtt-publisher")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self.state.connected = False

    async def _run(self, settings: Settings) -> None:
        """Reconnect with backoff for as long as the app is running."""
        delay = 5
        while not self._stop.is_set():
            try:
                async with self._build_client(settings) as client:
                    self._client = client
                    self.state.connected = True
                    self.state.last_error = ""
                    delay = 5
                    log.info("mqtt_connected", host=self._config.mqtt_host)
                    await self._announce(settings)
                    await self._listen(client, settings)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.state.connected = False
                self.state.last_error = str(exc)
                log.warning("mqtt_disconnected", error=str(exc), retry_in=delay)
            finally:
                self._client = None
                self.state.connected = False
            if self._stop.is_set():
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)

    async def _announce(self, settings: Settings) -> None:
        node_id = settings.home_assistant.node_id or NODE_ID
        if self._client is None:
            return
        await self._client.publish(availability_topic(node_id), ONLINE, qos=1, retain=True)

    async def _listen(self, client: Any, settings: Settings) -> None:
        node_id = settings.home_assistant.node_id or NODE_ID
        await client.subscribe(f"{node_id}/+/set", qos=1)
        async for message in client.messages:
            topic = str(message.topic)
            payload = (
                message.payload.decode("utf-8", "replace")
                if isinstance(message.payload, bytes | bytearray)
                else str(message.payload)
            )
            self.state.commands_received += 1
            object_id = topic.split("/")[1] if "/" in topic else ""
            if self._handler is None:
                log.warning("mqtt_command_ignored", topic=topic)
                continue
            try:
                await self._handler(object_id, payload)
            except Exception:
                # A bad command must not drop the broker connection.
                log.exception("mqtt_command_failed", object_id=object_id)

    # -- publication -------------------------------------------------------

    async def publish_discovery(
        self, definitions: list[EntityDefinition], settings: Settings
    ) -> int:
        if self._client is None:
            return 0
        prefix = settings.home_assistant.mqtt_discovery_prefix or "homeassistant"
        for definition in definitions:
            await self._client.publish(
                discovery_topic(definition, prefix, settings.home_assistant.node_id or NODE_ID),
                json.dumps(discovery_payload(definition, settings, self._config)),
                qos=1,
                retain=True,
            )
        self.state.discovery_sent = True
        log.info("mqtt_discovery_published", entities=len(definitions))
        return len(definitions)

    async def publish_states(
        self,
        definitions: list[EntityDefinition],
        context: EntityContext,
        settings: Settings,
    ) -> int:
        if self._client is None:
            return 0
        node_id = settings.home_assistant.node_id or NODE_ID
        published = 0
        for definition in definitions:
            if definition.component == "button":
                continue
            await self._client.publish(
                state_topic(definition, node_id),
                state_payload(definition, context),
                qos=0,
                retain=True,
            )
            if definition.attributes is not None:
                await self._client.publish(
                    attributes_topic(definition, node_id),
                    json.dumps(definition.attributes(context), default=str),
                    qos=0,
                    retain=True,
                )
            published += 1
        self.state.published_entities = published
        self.state.last_publish_at = utcnow().isoformat()
        return published

    async def remove_entities(self, definitions: list[EntityDefinition], settings: Settings) -> int:
        """Clear retained discovery configs so entities disappear cleanly.

        Called when entity publication is switched off or the app is removed,
        so Home Assistant is not left with orphaned entities.
        """
        if self._client is None:
            return 0
        prefix = settings.home_assistant.mqtt_discovery_prefix or "homeassistant"
        node_id = settings.home_assistant.node_id or NODE_ID
        for definition in definitions:
            await self._client.publish(
                discovery_topic(definition, prefix, node_id), "", qos=1, retain=True
            )
        self.state.discovery_sent = False
        log.info("mqtt_entities_removed", entities=len(definitions))
        return len(definitions)

    async def publish_offline(self, settings: Settings) -> None:
        if self._client is None:
            return
        node_id = settings.home_assistant.node_id or NODE_ID
        await self._client.publish(availability_topic(node_id), OFFLINE, qos=1, retain=True)


_publisher: MqttPublisher | None = None


def get_publisher() -> MqttPublisher:
    global _publisher
    if _publisher is None:
        _publisher = MqttPublisher()
    return _publisher


def set_publisher(publisher: MqttPublisher | None) -> None:
    """Replace the publisher. Used by tests."""
    global _publisher
    _publisher = publisher


def all_definitions(context: EntityContext, settings: Settings) -> list[EntityDefinition]:
    definitions = build_definitions(context)
    if settings.home_assistant.expose_writable_controls:
        definitions.extend(writable_definitions(context))
    return definitions
