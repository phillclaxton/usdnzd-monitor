"""Simulation, backup, restore and diagnostics tests."""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.home_assistant.client import set_home_assistant
from app.security.secrets import get_secret_store, reset_secret_store
from app.tests.test_alerts import FakeHomeAssistant

LADDER = [
    {
        "sequence": 1,
        "allocation_type": "percentage",
        "allocation_value": "100",
        "target_rate": "1.7600",
    }
]


async def make_strategy(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "Simulated",
        "initial_source_amount": "800000",
        "funds_available_amount": "800000",
        "tranches": LADDER,
    }
    payload.update(overrides)
    response = await client.post("/api/v1/strategies", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def fake_home_assistant() -> FakeHomeAssistant:
    fake = FakeHomeAssistant()
    set_home_assistant(fake)  # type: ignore[arg-type]
    yield fake
    set_home_assistant(None)


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


async def test_simulation_is_off_by_default_and_carries_the_banner(
    client: AsyncClient,
) -> None:
    body = (await client.get("/api/v1/simulation")).json()
    assert body["enabled"] is False
    assert body["banner"] == (
        "SIMULATION MODE: No live financial decisions should be based on this screen."
    )


async def test_a_simulated_rate_needs_simulation_enabled(client: AsyncClient) -> None:
    response = await client.post("/api/v1/simulation/rate", json={"rate": "1.76"})
    assert response.status_code == 422
    assert "not enabled" in response.json()["error"]["message"]


async def test_simulated_data_is_kept_out_of_the_real_position(
    client: AsyncClient,
) -> None:
    strategy = await make_strategy(client)
    await client.put("/api/v1/simulation", json={"enabled": True})
    await client.post("/api/v1/simulation/rate", json={"rate": "1.7900"})
    await client.post(
        "/api/v1/conversions",
        json={
            "strategy_id": strategy["id"],
            "executed_at": "2026-08-01T00:00:00Z",
            "source_amount": "100000",
            "target_amount": "179000",
            "simulated": True,
        },
    )

    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()
    assert summary["converted_source_amount"] == "0.0000"
    assert summary["remaining_source_amount"] == "800000.0000"
    assert summary["blended_effective_rate"] is None


async def test_enabling_simulation_is_audited(client: AsyncClient) -> None:
    await client.put("/api/v1/simulation", json={"enabled": True})
    events = (await client.get("/api/v1/audit-events?event_type=simulation")).json()
    assert any("Simulation mode enabled" in event["message"] for event in events)


async def test_a_replay_drives_the_whole_pipeline(
    client: AsyncClient, fake_home_assistant: FakeHomeAssistant
) -> None:
    strategy = await make_strategy(client)
    await client.post(f"/api/v1/strategies/{strategy['id']}/activate")
    await client.put("/api/v1/settings", json={"notifications": {"services": ["notify.test"]}})
    await client.put("/api/v1/simulation", json={"enabled": True})

    # Below the target, then across it twice — the confirmation rule needs two
    # qualifying samples at least 30 seconds apart.
    body = (
        await client.post(
            "/api/v1/simulation/replay",
            json={"rates": ["1.7400", "1.7500", "1.7610", "1.7620"], "seconds_between": 60},
        )
    ).json()

    assert body["steps"] == 4
    assert body["samples_written"] == 4
    assert body["final_rate"] == "1.76200000"
    assert body["notifications"] >= 1
    titles = [call["title"] for call in fake_home_assistant.calls]
    assert any("target reached" in title for title in titles)


async def test_a_replay_needs_simulation_enabled(client: AsyncClient) -> None:
    response = await client.post("/api/v1/simulation/replay", json={"rates": ["1.76"]})
    assert response.status_code == 422


async def test_a_replay_rejects_an_impossible_rate(client: AsyncClient) -> None:
    await client.put("/api/v1/simulation", json={"enabled": True})
    for bad in (["0"], ["-1.5"], ["not a rate"]):
        response = await client.post("/api/v1/simulation/replay", json={"rates": bad})
        assert response.status_code == 422


async def test_reset_removes_simulated_data_and_leaves_real_data(
    client: AsyncClient,
) -> None:
    strategy = await make_strategy(client)
    # A real conversion and a real rate.
    await client.post("/api/v1/rates/manual", json={"rate": "1.7000"})
    await client.post(
        "/api/v1/conversions",
        json={
            "strategy_id": strategy["id"],
            "executed_at": "2026-08-01T00:00:00Z",
            "source_amount": "100000",
            "target_amount": "170000",
        },
    )
    # Then simulated ones.
    await client.put("/api/v1/simulation", json={"enabled": True})
    await client.post("/api/v1/simulation/rate", json={"rate": "1.9000"})
    await client.post(
        "/api/v1/conversions",
        json={
            "strategy_id": strategy["id"],
            "executed_at": "2026-08-02T00:00:00Z",
            "source_amount": "50000",
            "target_amount": "95000",
            "simulated": True,
        },
    )

    before = (await client.get("/api/v1/simulation")).json()
    assert before["simulated_conversions"] == 1

    reset = (await client.post("/api/v1/simulation/reset")).json()
    assert "Real records were not touched" in reset["message"]

    after = (await client.get("/api/v1/simulation")).json()
    assert after["simulated_conversions"] == 0

    conversions = (await client.get(f"/api/v1/conversions?strategy_id={strategy['id']}")).json()
    assert len(conversions["conversions"]) == 1
    assert conversions["total_source_amount"] == "100000.0000"


# ---------------------------------------------------------------------------
# Backup and restore
# ---------------------------------------------------------------------------


async def test_a_backup_contains_the_data_but_never_a_credential(
    client: AsyncClient, app_config: object
) -> None:
    reset_secret_store()
    get_secret_store().set("wise_api_token", "wise-secret-token-999")

    strategy = await make_strategy(client)
    await client.post("/api/v1/rates/manual", json={"rate": "1.7550"})
    await client.post(
        "/api/v1/conversions",
        json={
            "strategy_id": strategy["id"],
            "executed_at": "2026-08-01T00:00:00Z",
            "source_amount": "100000",
            "target_amount": "175500",
        },
    )

    response = await client.post("/api/v1/backup")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    document = response.json()

    assert document["contains_secrets"] is False
    assert document["counts"]["strategies"] == 1
    assert document["counts"]["conversions"] == 1
    assert "wise-secret-token-999" not in response.text
    reset_secret_store()


async def test_a_backup_round_trips_into_a_fresh_install(
    client: AsyncClient, session: AsyncSession
) -> None:
    strategy = await make_strategy(client)
    await client.post(
        "/api/v1/conversions",
        json={
            "strategy_id": strategy["id"],
            "executed_at": "2026-08-01T00:00:00Z",
            "source_amount": "120000",
            "target_amount": "206400",
            "provider_transaction_id": "WISE-BK",
        },
    )
    document = (await client.post("/api/v1/backup")).json()

    files = {"file": ("backup.json", json.dumps(document), "application/json")}
    restored = await client.post("/api/v1/restore?replace=true", files=files)
    assert restored.status_code == 200
    body = restored.json()
    assert body["restored"]["strategies"] == 1
    assert "re-enter any API tokens" in body["message"]

    conversions = (await client.get("/api/v1/conversions")).json()
    assert conversions["total_source_amount"] == "120000.0000"
    assert conversions["blended_effective_rate"] == "1.72000000"


async def test_restoring_into_a_populated_install_is_refused_by_default(
    client: AsyncClient,
) -> None:
    await make_strategy(client)
    document = (await client.post("/api/v1/backup")).json()
    files = {"file": ("backup.json", json.dumps(document), "application/json")}

    response = await client.post("/api/v1/restore", files=files)
    assert response.status_code == 422
    assert "already has 1 strategy" in response.json()["error"]["message"]


async def test_a_foreign_file_is_refused(client: AsyncClient) -> None:
    files = {"file": ("notes.json", json.dumps({"hello": "world"}), "application/json")}
    response = await client.post("/api/v1/restore", files=files)
    assert response.status_code == 422
    assert "not a FX Strategy Manager backup" in response.json()["error"]["message"]


async def test_a_backup_from_a_future_format_is_refused(client: AsyncClient) -> None:
    document = (await client.post("/api/v1/backup")).json()
    document["format_version"] = 99
    files = {"file": ("backup.json", json.dumps(document), "application/json")}
    response = await client.post("/api/v1/restore", files=files)
    assert response.status_code == 422
    assert "format version 99" in response.json()["error"]["message"]


async def test_non_json_is_refused(client: AsyncClient) -> None:
    files = {"file": ("backup.json", "not json at all", "application/json")}
    response = await client.post("/api/v1/restore", files=files)
    assert response.status_code == 422


async def test_a_restore_is_audited(client: AsyncClient) -> None:
    document = (await client.post("/api/v1/backup")).json()
    files = {"file": ("backup.json", json.dumps(document), "application/json")}
    await client.post("/api/v1/restore?replace=true", files=files)

    events = (await client.get("/api/v1/audit-events?entity_type=backup")).json()
    assert any(event["event_type"] == "restored" for event in events)


async def test_decimals_survive_a_backup_round_trip(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    await client.post(
        "/api/v1/conversions",
        json={
            "strategy_id": strategy["id"],
            "executed_at": "2026-08-01T00:00:00Z",
            "source_amount": "123456.7891",
            "target_amount": "216543.2109",
        },
    )
    document = (await client.post("/api/v1/backup")).json()
    files = {"file": ("backup.json", json.dumps(document), "application/json")}
    await client.post("/api/v1/restore?replace=true", files=files)

    conversions = (await client.get("/api/v1/conversions")).json()
    row = conversions["conversions"][0]
    assert row["source_amount"] == "123456.7891"
    assert row["target_amount"] == "216543.2109"


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


async def test_diagnostics_report_state_without_credentials(
    client: AsyncClient, app_config: object
) -> None:
    reset_secret_store()
    get_secret_store().set("wise_api_token", "wise-secret-token-888")

    response = await client.get("/api/v1/diagnostics")
    assert response.status_code == 200
    assert "wise-secret-token-888" not in response.text

    body = response.json()
    assert body["app"]["version"]
    assert body["database"]["counts"]["strategies"] == 0
    assert body["credentials"]["wise_api_token"]["configured"] is True
    # Only the configured flag, never the value or even the masked hint.
    assert set(body["credentials"]["wise_api_token"]) == {"configured"}
    assert "excludes credentials" in body["note"]
    reset_secret_store()


async def test_diagnostics_mask_account_identifiers(client: AsyncClient) -> None:
    await client.put(
        "/api/v1/wise/credentials",
        json={"profile_id": "12345678", "source_balance_id": "98765432"},
    )
    body = (await client.get("/api/v1/diagnostics")).json()
    assert body["wise"]["profile_id"] == "12****78"
    assert body["wise"]["source_balance_id"] == "98****32"
    assert body["wise"]["read_only"] is True


async def test_diagnostics_include_provider_and_scheduler_state(
    client: AsyncClient,
) -> None:
    await client.post("/api/v1/rates/manual", json={"rate": "1.7550"})
    body = (await client.get("/api/v1/diagnostics")).json()
    assert body["rates"]["last_provider"] == "manual"
    assert "running" in body["scheduler"]
    assert "mqtt_configured" in body["mqtt"]
    assert body["database"]["integrity_problems"] == []


async def test_the_diagnostics_bundle_is_a_download(client: AsyncClient) -> None:
    response = await client.get("/api/v1/diagnostics/bundle")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")


async def test_the_integrity_check_does_not_repair(client: AsyncClient) -> None:
    body = (await client.post("/api/v1/diagnostics/integrity-check")).json()
    assert body["ok"] is True
    assert body["problems"] == []
    assert "does not repair" in body["note"]


async def test_logs_can_be_excluded_from_diagnostics(client: AsyncClient) -> None:
    body = (await client.get("/api/v1/diagnostics?include_logs=false")).json()
    assert body["recent_logs"] == []


def test_identifier_masking() -> None:
    from app.services.backup import mask_identifier

    assert mask_identifier("12345678") == "12****78"
    assert mask_identifier("abc") == "***"
    assert mask_identifier("") == ""
    assert mask_identifier(None) is None


async def test_a_clock_drift_warning_appears_when_timestamps_disagree(
    client: AsyncClient, session: AsyncSession
) -> None:
    from datetime import timedelta

    from app.database import utcnow
    from app.models.rate import RateSample
    from app.providers.base import QuoteType

    session.add(
        RateSample(
            provider="test",
            source_currency="USD",
            target_currency="NZD",
            rate=Decimal("1.76"),
            rate_numeric=1.76,
            quote_type=str(QuoteType.MID_MARKET),
            retrieved_at=utcnow(),
            provider_timestamp=utcnow() - timedelta(hours=5),
        )
    )
    await session.commit()

    body = (await client.get("/api/v1/diagnostics")).json()
    assert body["rates"]["clock_warning"] is not None
    assert "differs from this system's clock" in body["rates"]["clock_warning"]
