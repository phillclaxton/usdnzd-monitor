"""Health, settings and security-header tests."""

from __future__ import annotations

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.settings import SettingsUpdate
from app.services import settings_service


async def test_health_reports_database_ok(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


async def test_liveness_and_readiness(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/health/live")).json() == {"status": "alive"}
    assert (await client.get("/api/v1/health/ready")).json() == {"status": "ready"}


async def test_security_headers_present(client: AsyncClient) -> None:
    response = await client.get("/api/v1/health")
    csp = response.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    # No CDN or external asset host is permitted.
    assert "https://" not in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-correlation-id"]


async def test_defaults_returned_for_a_fresh_install(client: AsyncClient) -> None:
    body = (await client.get("/api/v1/settings")).json()
    assert body["general"]["source_currency"] == "USD"
    assert body["general"]["target_currency"] == "NZD"
    assert body["general"]["timezone"] == "Pacific/Auckland"
    assert body["general"]["setup_complete"] is False
    # Decimals cross the wire as strings, never JSON numbers.
    assert body["providers"]["disagreement_threshold"] == "0.0030"
    assert body["notifications"]["reset_hysteresis"] == "0.00500000"


async def test_partial_update_leaves_other_sections_alone(client: AsyncClient) -> None:
    response = await client.put(
        "/api/v1/settings",
        json={"notifications": {"services": ["notify.mobile_app_test"], "enabled": True}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["notifications"]["services"] == ["notify.mobile_app_test"]
    assert body["general"]["timezone"] == "Pacific/Auckland"


async def test_invalid_currency_is_rejected(client: AsyncClient) -> None:
    response = await client.put("/api/v1/settings", json={"general": {"source_currency": "XYZ"}})
    assert response.status_code == 422


async def test_settings_changes_are_audited(client: AsyncClient) -> None:
    await client.put("/api/v1/settings", json={"formatting": {"currency_decimal_places": 0}})
    events = (await client.get("/api/v1/audit-events?entity_type=settings")).json()
    assert events
    assert events[0]["event_type"] == "updated"
    assert events[0]["correlation_id"]


async def test_patch_section_round_trips_decimals(session: AsyncSession) -> None:
    await settings_service.patch_section(
        session, "providers", {"disagreement_threshold": Decimal("0.0050")}
    )
    loaded = await settings_service.load_settings(session)
    assert loaded.providers.disagreement_threshold == Decimal("0.0050")


async def test_corrupt_section_falls_back_to_defaults(session: AsyncSession) -> None:
    from app.models.setting import AppSetting

    session.add(AppSetting(key="general", value_json="{not json"))
    await session.flush()
    loaded = await settings_service.load_settings(session)
    assert loaded.general.source_currency == "USD"


async def test_update_rejects_unknown_section(client: AsyncClient) -> None:
    response = await client.put("/api/v1/settings", json={"nonsense": {}})
    assert response.status_code == 422


async def test_cross_origin_state_change_is_rejected(client: AsyncClient) -> None:
    response = await client.put(
        "/api/v1/settings",
        json={"formatting": {"currency_decimal_places": 2}},
        headers={"Origin": "https://evil.example", "Host": "testserver"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "cross_origin"


async def test_same_origin_state_change_is_allowed(client: AsyncClient) -> None:
    response = await client.put(
        "/api/v1/settings",
        json={"formatting": {"currency_decimal_places": 2}},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200


async def test_settings_update_model_ignores_none_sections(session: AsyncSession) -> None:
    await settings_service.update_settings(
        session, SettingsUpdate(general=None, formatting=None), actor="test"
    )
    loaded = await settings_service.load_settings(session)
    assert loaded.general.setup_complete is False
