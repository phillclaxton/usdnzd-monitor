"""Conversion recording tests.

A conversion is a fact about money that moved, and belongs to no plan. What is
pinned here is what protects the record: duplicate refusal, impossible amounts,
corrections that keep the previous values, and an estimate that can never be
read back as a fact.

Whether more was converted than is actually held is the *position's* question,
so it is asked in ``test_position.py`` against ``POST /fx/conversions``, which
is the route that owns the balance.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from app.database import utcnow


def conversion_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "executed_at": utcnow().isoformat(),
        "source_amount": "120000",
        "target_amount": "206400",
        "provider": "wise",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


async def test_recording_a_conversion_derives_the_rate(client: AsyncClient) -> None:
    response = await client.post("/api/v1/conversions", json=conversion_payload())
    assert response.status_code == 201, response.text
    row = response.json()
    # 206,400 / 120,000 = 1.7200 exactly.
    assert row["gross_rate"] == "1.72000000"
    assert row["effective_rate"] == "1.72000000"
    assert row["record_source"] == "manual"


async def test_a_supplied_rate_is_kept_alongside_the_derived_effective_rate(
    client: AsyncClient,
) -> None:
    row = (
        await client.post(
            "/api/v1/conversions",
            json=conversion_payload(
                source_amount="120000",
                target_amount="205880",
                gross_rate="1.7200",
                fee_target_currency="520",
            ),
        )
    ).json()
    assert row["gross_rate"] == "1.72000000"
    # What actually arrived, divided by what was sent.
    assert row["effective_rate"] == "1.71566667"
    assert row["fee_total_target_equivalent"] == "520.0000"


@pytest.mark.parametrize(
    ("field", "value"),
    [("source_amount", "0"), ("target_amount", "0"), ("source_amount", "-1")],
)
async def test_an_amount_has_to_be_positive(client: AsyncClient, field: str, value: str) -> None:
    response = await client.post("/api/v1/conversions", json=conversion_payload(**{field: value}))
    assert response.status_code == 422, response.text


async def test_any_positive_amount_is_recordable_history(client: AsyncClient) -> None:
    """There is no recorded total here to exceed.

    What is held is the position's business, and ``POST /fx/conversions`` is
    where converting more than that is refused.
    """
    response = await client.post(
        "/api/v1/conversions", json=conversion_payload(source_amount="9000000")
    )
    assert response.status_code == 201, response.text


async def test_a_repeated_transaction_id_is_refused(client: AsyncClient) -> None:
    payload = conversion_payload(provider_transaction_id="WISE-123")
    assert (await client.post("/api/v1/conversions", json=payload)).status_code == 201

    duplicate = await client.post("/api/v1/conversions", json=payload)
    assert duplicate.status_code == 409
    assert "already recorded" in duplicate.json()["error"]["message"]


async def test_conversions_without_a_transaction_id_are_not_treated_as_duplicates(
    client: AsyncClient,
) -> None:
    payload = conversion_payload(source_amount="1000", target_amount="1750")
    assert (await client.post("/api/v1/conversions", json=payload)).status_code == 201
    assert (await client.post("/api/v1/conversions", json=payload)).status_code == 201


async def test_the_audit_message_names_the_configured_pair(client: AsyncClient) -> None:
    """Without it the trail reads "Recorded 120000 converted to 206400"."""
    await client.post("/api/v1/conversions", json=conversion_payload())
    events = await client.get("/api/v1/audit-events", params={"entity_type": "conversion"})
    (event,) = events.json()
    assert "120000.0000 USD" in event["message"]
    assert "206400.0000 NZD" in event["message"]


# ---------------------------------------------------------------------------
# Corrections and deletion
# ---------------------------------------------------------------------------


async def test_a_correction_keeps_the_previous_values_in_the_audit_trail(
    client: AsyncClient,
) -> None:
    created = (await client.post("/api/v1/conversions", json=conversion_payload())).json()

    await client.put(
        f"/api/v1/conversions/{created['id']}",
        json=conversion_payload(
            source_amount="120000",
            target_amount="207000",
            correction_reason="Statement showed a different amount",
        ),
    )

    events = (
        await client.get(f"/api/v1/audit-events?entity_type=conversion&entity_id={created['id']}")
    ).json()
    update = next(event for event in events if event["event_type"] == "updated")
    assert "Statement showed a different amount" in update["message"]
    assert "206400" in (update["before_json"] or "")
    assert "207000" in (update["after_json"] or "")


async def test_deleting_a_conversion_records_everything_it_held(
    client: AsyncClient,
) -> None:
    created = (
        await client.post(
            "/api/v1/conversions",
            json=conversion_payload(provider_transaction_id="WISE-DEL"),
        )
    ).json()

    response = await client.delete(f"/api/v1/conversions/{created['id']}?reason=entered%20twice")
    assert response.status_code == 200
    assert (await client.get(f"/api/v1/conversions/{created['id']}")).status_code == 404

    events = (
        await client.get(f"/api/v1/audit-events?entity_type=conversion&entity_id={created['id']}")
    ).json()
    deleted = next(event for event in events if event["event_type"] == "deleted")
    assert "entered twice" in deleted["message"]
    assert "WISE-DEL" in (deleted["before_json"] or "")
    assert "206400" in (deleted["before_json"] or "")


# ---------------------------------------------------------------------------
# Estimated amounts
# ---------------------------------------------------------------------------


async def test_an_estimate_is_recorded_as_an_estimate(client: AsyncClient) -> None:
    created = (
        await client.post("/api/v1/conversions", json=conversion_payload(amounts_estimated=True))
    ).json()
    assert created["amounts_estimated"] is True

    read = await client.get(f"/api/v1/conversions/{created['id']}")
    assert read.json()["amounts_estimated"] is True

    events = await client.get("/api/v1/audit-events", params={"entity_type": "conversion"})
    assert "(amounts estimated)" in events.json()[0]["message"]


async def test_a_conversion_is_confirmed_unless_it_says_otherwise(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/conversions", json=conversion_payload())).json()
    assert created["amounts_estimated"] is False


async def test_entering_the_real_receipt_clears_the_estimate(client: AsyncClient) -> None:
    """The distinction is meant to disappear on its own once the figure is known."""
    created = (
        await client.post("/api/v1/conversions", json=conversion_payload(amounts_estimated=True))
    ).json()
    corrected = await client.put(
        f"/api/v1/conversions/{created['id']}",
        json=conversion_payload(
            target_amount="206387.50",
            amounts_estimated=False,
            correction_reason="Wise receipt entered.",
        ),
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["amounts_estimated"] is False
    assert corrected.json()["target_amount"] == "206387.5000"


async def test_an_estimate_survives_a_backup_and_restore(client: AsyncClient) -> None:
    await client.post("/api/v1/conversions", json=conversion_payload(amounts_estimated=True))
    document = (await client.post("/api/v1/backup")).json()

    restored = await client.post(
        "/api/v1/restore?replace=true",
        files={"file": ("backup.json", json.dumps(document), "application/json")},
    )
    assert restored.status_code == 200, restored.text

    (row,) = (await client.get("/api/v1/conversions")).json()["conversions"]
    assert row["amounts_estimated"] is True


# ---------------------------------------------------------------------------
# Simulated records
# ---------------------------------------------------------------------------


async def test_simulated_conversions_do_not_change_the_real_position(
    client: AsyncClient,
) -> None:
    await client.post("/api/v1/fx/state", json={"current_source_balance": "800000"})
    await client.post("/api/v1/conversions", json=conversion_payload(simulated=True))

    metrics = (await client.get("/api/v1/fx/state")).json()["metrics"]
    assert metrics["total_source_converted"] == "0.0000"
    assert metrics["total_target_received"] == "0.0000"


# ---------------------------------------------------------------------------
# List, import and export
# ---------------------------------------------------------------------------


async def test_the_list_view_aggregates_correctly(client: AsyncClient) -> None:
    await client.post("/api/v1/conversions", json=conversion_payload())
    await client.post(
        "/api/v1/conversions",
        json=conversion_payload(
            source_amount="160000",
            target_amount="277900",
            fee_target_currency="500",
        ),
    )

    body = (await client.get("/api/v1/conversions")).json()
    assert body["total_source_amount"] == "280000.0000"
    assert body["total_target_amount"] == "484300.0000"
    assert body["blended_effective_rate"] == "1.72964286"
    assert body["total_fees"] == "500.0000"


async def test_csv_import_previews_then_commits(client: AsyncClient) -> None:
    csv_text = (
        "executed_at,source_amount,target_amount,transaction_id,notes\n"
        "2026-09-15T10:30:00Z,120000,207840,WISE-1,Auto conversion\n"
        "2026-09-20T10:30:00Z,160000,278400,WISE-2,\n"
        "not-a-date,1000,1750,WISE-3,\n"
    )
    files = {"file": ("conversions.csv", csv_text, "text/csv")}

    preview = (await client.post("/api/v1/conversions/import", files=files)).json()
    assert preview["accepted"] == 2
    assert preview["rejected"] == 1
    assert preview["imported"] == 0

    committed = (await client.post("/api/v1/conversions/import?commit=true", files=files)).json()
    assert committed["imported"] == 2

    body = (await client.get("/api/v1/conversions")).json()
    assert body["total_source_amount"] == "280000.0000"


async def test_a_tranche_column_is_accepted_and_ignored(client: AsyncClient) -> None:
    """Files exported by earlier versions carry one.

    Refusing them over a column that no longer means anything would be a poor
    trade for someone reimporting their own history.
    """
    csv_text = (
        "executed_at,source_amount,target_amount,gross_rate,effective_rate,"
        "fee_source_currency,fee_target_currency,provider,provider_transaction_id,"
        "tranche_reference,notes\n"
        "2026-03-01T00:00:00Z,50000,86000,1.7200,1.7200,,,wise,csv-1,3,\n"
    )
    response = await client.post(
        "/api/v1/conversions/import",
        params={"commit": True},
        files={"file": ("history.csv", csv_text, "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 1

    (row,) = (await client.get("/api/v1/conversions")).json()["conversions"]
    assert row["source_amount"] == "50000.0000"


async def test_reimporting_the_same_file_skips_duplicates(client: AsyncClient) -> None:
    csv_text = (
        "executed_at,source_amount,target_amount,transaction_id\n"
        "2026-09-15T10:30:00Z,120000,207840,WISE-1\n"
    )
    files = {"file": ("conversions.csv", csv_text, "text/csv")}
    await client.post("/api/v1/conversions/import?commit=true", files=files)

    again = (await client.post("/api/v1/conversions/import?commit=true", files=files)).json()
    assert again["duplicates"] == 1
    assert again["imported"] == 0


async def test_csv_export_round_trips_through_the_importer(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/conversions", json=conversion_payload(provider_transaction_id="WISE-RT")
    )

    response = await client.get("/api/v1/conversions/export")
    assert response.status_code == 200
    assert response.text.splitlines()[0].startswith("executed_at,source_amount,target_amount")

    files = {"file": ("conversions.csv", response.text, "text/csv")}
    preview = (await client.post("/api/v1/conversions/import", files=files)).json()
    # The transaction ID already exists, so a re-import is recognised as a
    # duplicate rather than silently double-counted.
    assert preview["duplicates"] == 1


async def test_import_requires_the_mandatory_columns(client: AsyncClient) -> None:
    files = {"file": ("bad.csv", "date,amount\n2026-01-01,100\n", "text/csv")}
    response = await client.post("/api/v1/conversions/import", files=files)
    assert response.status_code == 422
    assert "Missing required column" in response.json()["error"]["message"]


async def test_conversions_are_listed_newest_first(client: AsyncClient) -> None:
    await client.post(
        "/api/v1/conversions",
        json=conversion_payload(executed_at=(utcnow() - timedelta(days=5)).isoformat()),
    )
    await client.post(
        "/api/v1/conversions",
        json=conversion_payload(
            source_amount="1000",
            target_amount="1750",
            executed_at=utcnow().isoformat(),
        ),
    )
    body = (await client.get("/api/v1/conversions")).json()
    assert body["conversions"][0]["source_amount"] == "1000.0000"
