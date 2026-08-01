"""Home Assistant integration endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter

from app.api.deps import ActorDep, SessionDep, SettingsDep
from app.home_assistant.client import get_home_assistant
from app.schemas.common import Schema, StrictSchema
from app.services import monitor, notifications

router = APIRouter(prefix="/home-assistant", tags=["home assistant"])


class HomeAssistantStatusOut(Schema):
    available: bool
    message: str
    notify_services: list[str]
    latency_ms: int | None
    configured_services: list[str]
    mqtt_configured: bool


class TestNotificationIn(StrictSchema):
    #: When omitted, the services configured in settings are used.
    services: list[str] | None = None


class DeliveryOut(Schema):
    delivered: bool
    queued: bool
    suppressed_reason: str | None
    services: list[str]
    errors: dict[str, str]


class NotificationLogOut(Schema):
    id: int
    rule_type: str
    severity: str
    title: str
    message: str
    entity_type: str | None
    entity_id: str | None
    delivered: bool
    queued: bool
    attempts: int
    last_error: str | None
    suppressed_reason: str | None
    created_at: datetime
    delivered_at: datetime | None


@router.get("/status", response_model=HomeAssistantStatusOut, summary="Connection status")
async def status(settings: SettingsDep) -> HomeAssistantStatusOut:
    from app.config import get_config

    result = await get_home_assistant().status()
    return HomeAssistantStatusOut(
        available=result.available,
        message=result.message,
        notify_services=result.notify_services,
        latency_ms=result.latency_ms,
        configured_services=settings.notifications.services,
        mqtt_configured=get_config().mqtt_configured,
    )


@router.get("/services", response_model=list[str], summary="Discover notify services")
async def services() -> list[str]:
    """List the installation's notify services.

    No device name is ever hard-coded; the list comes from Home Assistant.
    """
    client = get_home_assistant()
    if not client.configured:
        return []
    try:
        return await client.notify_services()
    except Exception:
        return []


@router.post("/test-notification", response_model=DeliveryOut, summary="Send a test notification")
async def test_notification(
    payload: TestNotificationIn, session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> DeliveryOut:
    _ = actor
    result = await monitor.send_test_notification(session, settings, services=payload.services)
    return DeliveryOut(
        delivered=result.delivered,
        queued=result.queued,
        suppressed_reason=result.suppressed_reason,
        services=result.services,
        errors=result.errors,
    )


class PublishOut(Schema):
    transport: str
    entities: int
    discovery: int
    errors: list[str]
    message: str


@router.post("/publish", response_model=PublishOut, summary="Publish entities now")
async def publish_entities(
    session: SessionDep, settings: SettingsDep, force_discovery: bool = False
) -> PublishOut:
    """Re-publish every entity, optionally resending the discovery configs."""
    from app.services import publisher

    result = await publisher.publish(session, settings, force_discovery=force_discovery)
    return PublishOut(
        transport=result.transport,
        entities=result.entities,
        discovery=result.discovery,
        errors=result.errors,
        message=result.message,
    )


class EntityPreviewOut(Schema):
    entity_id: str
    name: str
    component: str
    state: str
    attributes: dict[str, Any]


@router.get(
    "/entities", response_model=list[EntityPreviewOut], summary="Preview published entities"
)
async def preview_entities(session: SessionDep, settings: SettingsDep) -> list[EntityPreviewOut]:
    """Show exactly what would be published, without needing a broker."""
    from app.home_assistant.entities import state_payload
    from app.home_assistant.mqtt import all_definitions
    from app.services import publisher

    context = await publisher.build_context(session, settings)
    return [
        EntityPreviewOut(
            entity_id=definition.entity_id,
            name=definition.name,
            component=definition.component,
            state=state_payload(definition, context),
            attributes=definition.attributes(context) if definition.attributes else {},
        )
        for definition in all_definitions(context, settings)
    ]


@router.get(
    "/notifications", response_model=list[NotificationLogOut], summary="Notification history"
)
async def notification_history(session: SessionDep, limit: int = 50) -> list[NotificationLogOut]:
    rows = await notifications.recent_log(session, limit=min(limit, 200))
    return [NotificationLogOut.model_validate(row) for row in rows]
