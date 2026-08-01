"""Conversion recording tests.

The behaviours pinned here are the ones that protect the financial record:
duplicate refusal, impossible amounts, corrections that keep history, and the
rule that only a recorded conversion completes a tranche.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from httpx import AsyncClient

from app.database import utcnow

LADDER = [
    {
        "sequence": 1,
        "allocation_type": "percentage",
        "allocation_value": "15",
        "target_rate": "1.7200",
    },
    {
        "sequence": 2,
        "allocation_type": "percentage",
        "allocation_value": "20",
        "target_rate": "1.7400",
    },
    {
        "sequence": 3,
        "allocation_type": "percentage",
        "allocation_value": "25",
        "target_rate": "1.7600",
    },
    {
        "sequence": 4,
        "allocation_type": "percentage",
        "allocation_value": "20",
        "target_rate": "1.7800",
    },
    {
        "sequence": 5,
        "allocation_type": "percentage",
        "allocation_value": "20",
        "target_rate": "1.8000",
    },
]


async def make_strategy(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "USD to NZD",
        "initial_source_amount": "800000",
        "funds_available_amount": "800000",
        "tranches": LADDER,
    }
    payload.update(overrides)
    response = await client.post("/api/v1/strategies", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def conversion_payload(strategy_id: int, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "strategy_id": strategy_id,
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
    strategy = await make_strategy(client)
    response = await client.post("/api/v1/conversions", json=conversion_payload(strategy["id"]))
    assert response.status_code == 201
    row = response.json()[0]
    # 206,400 / 120,000 = 1.7200 exactly.
    assert row["gross_rate"] == "1.72000000"
    assert row["effective_rate"] == "1.72000000"
    assert row["record_source"] == "manual"


async def test_a_supplied_rate_is_kept_alongside_the_derived_effective_rate(
    client: AsyncClient,
) -> None:
    strategy = await make_strategy(client)
    row = (
        await client.post(
            "/api/v1/conversions",
            json=conversion_payload(
                strategy["id"],
                source_amount="120000",
                target_amount="205880",
                gross_rate="1.7200",
                fee_target_currency="520",
            ),
        )
    ).json()[0]
    assert row["gross_rate"] == "1.72000000"
    # What actually arrived, divided by what was sent.
    assert row["effective_rate"] == "1.71566667"
    assert row["fee_total_target_equivalent"] == "520.0000"


async def test_the_remaining_balance_and_blended_rate_update(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    await client.post("/api/v1/conversions", json=conversion_payload(strategy["id"]))
    await client.post(
        "/api/v1/conversions",
        json=conversion_payload(strategy["id"], source_amount="160000", target_amount="278400"),
    )

    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()
    assert summary["converted_source_amount"] == "280000.0000"
    assert summary["remaining_source_amount"] == "520000.0000"
    # (206,400 + 278,400) / 280,000 = 1.7314285714...
    assert summary["blended_effective_rate"] == "1.73142857"
    assert summary["gross_target_received"] == "484800.0000"
    # Exposure now uses only what is left: 520,000 x 0.01.
    assert summary["one_cent_exposure"] == "5200.0000"


async def test_a_zero_or_negative_amount_is_refused(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    for bad in ({"source_amount": "0"}, {"target_amount": "-5"}):
        response = await client.post(
            "/api/v1/conversions", json=conversion_payload(strategy["id"], **bad)
        )
        assert response.status_code == 422


async def test_converting_more_than_remains_is_refused(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    response = await client.post(
        "/api/v1/conversions",
        json=conversion_payload(strategy["id"], source_amount="900000", target_amount="1500000"),
    )
    assert response.status_code == 422
    assert "only" in response.json()["error"]["message"]


async def test_a_correction_may_exceed_the_remaining_balance(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    response = await client.post(
        "/api/v1/conversions",
        json=conversion_payload(
            strategy["id"],
            source_amount="900000",
            target_amount="1500000",
            correcting_earlier_record=True,
        ),
    )
    assert response.status_code == 201


async def test_a_repeated_transaction_id_is_refused(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    payload = conversion_payload(strategy["id"], provider_transaction_id="WISE-123")
    assert (await client.post("/api/v1/conversions", json=payload)).status_code == 201

    duplicate = await client.post("/api/v1/conversions", json=payload)
    assert duplicate.status_code == 409
    assert "already recorded" in duplicate.json()["error"]["message"]


async def test_conversions_without_a_transaction_id_are_not_treated_as_duplicates(
    client: AsyncClient,
) -> None:
    strategy = await make_strategy(client)
    payload = conversion_payload(strategy["id"], source_amount="1000", target_amount="1750")
    assert (await client.post("/api/v1/conversions", json=payload)).status_code == 201
    assert (await client.post("/api/v1/conversions", json=payload)).status_code == 201


# ---------------------------------------------------------------------------
# Tranche assignment
# ---------------------------------------------------------------------------


async def test_a_conversion_completes_its_tranche_only_when_fully_converted(
    client: AsyncClient,
) -> None:
    strategy = await make_strategy(client)
    tranche = strategy["tranches"][0]  # 120,000

    await client.post(
        "/api/v1/conversions",
        json=conversion_payload(
            strategy["id"],
            source_amount="60000",
            target_amount="103200",
            tranche_id=tranche["id"],
        ),
    )
    tranches = (await client.get(f"/api/v1/strategies/{strategy['id']}/tranches")).json()
    assert tranches[0]["status"] == "partially_completed"
    assert tranches[0]["completed_at"] is None

    await client.post(
        "/api/v1/conversions",
        json=conversion_payload(
            strategy["id"],
            source_amount="60000",
            target_amount="103200",
            tranche_id=tranche["id"],
        ),
    )
    tranches = (await client.get(f"/api/v1/strategies/{strategy['id']}/tranches")).json()
    assert tranches[0]["status"] == "completed"
    assert tranches[0]["completed_at"] is not None


async def test_one_conversion_can_be_split_across_tranches(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    first, second = strategy["tranches"][0], strategy["tranches"][1]

    rows = (
        await client.post(
            "/api/v1/conversions",
            json=conversion_payload(
                strategy["id"],
                source_amount="280000",
                target_amount="484800",
                provider_transaction_id="WISE-SPLIT",
                allocations=[
                    {"tranche_id": first["id"], "source_amount": "120000"},
                    {"tranche_id": second["id"], "source_amount": "160000"},
                ],
            ),
        )
    ).json()

    assert len(rows) == 2
    assert [row["source_amount"] for row in rows] == ["120000.0000", "160000.0000"]
    # The parts sum exactly to what the user entered.
    total = sum(float(row["target_amount"]) for row in rows)
    assert total == 484800.0

    tranches = (await client.get(f"/api/v1/strategies/{strategy['id']}/tranches")).json()
    assert tranches[0]["status"] == "completed"
    assert tranches[1]["status"] == "completed"


async def test_allocations_must_sum_to_the_conversion_amount(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    response = await client.post(
        "/api/v1/conversions",
        json=conversion_payload(
            strategy["id"],
            source_amount="280000",
            target_amount="484800",
            allocations=[{"tranche_id": strategy["tranches"][0]["id"], "source_amount": "1000"}],
        ),
    )
    assert response.status_code == 422


async def test_a_tranche_from_another_strategy_is_refused(client: AsyncClient) -> None:
    first = await make_strategy(client)
    second = await make_strategy(client, name="Other")
    response = await client.post(
        "/api/v1/conversions",
        json=conversion_payload(first["id"], tranche_id=second["tranches"][0]["id"]),
    )
    assert response.status_code == 422
    assert "does not belong" in response.json()["error"]["message"]


async def test_a_tranche_with_conversions_cannot_be_deleted(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    tranche = strategy["tranches"][0]
    await client.post(
        "/api/v1/conversions",
        json=conversion_payload(strategy["id"], tranche_id=tranche["id"]),
    )
    response = await client.delete(f"/api/v1/tranches/{tranche['id']}")
    assert response.status_code == 409
    assert "recorded conversion" in response.json()["error"]["message"]


# ---------------------------------------------------------------------------
# Corrections and deletion
# ---------------------------------------------------------------------------


async def test_a_correction_keeps_the_previous_values_in_the_audit_trail(
    client: AsyncClient,
) -> None:
    strategy = await make_strategy(client)
    created = (
        await client.post("/api/v1/conversions", json=conversion_payload(strategy["id"]))
    ).json()[0]

    await client.put(
        f"/api/v1/conversions/{created['id']}",
        json=conversion_payload(
            strategy["id"],
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
    strategy = await make_strategy(client)
    created = (
        await client.post(
            "/api/v1/conversions",
            json=conversion_payload(strategy["id"], provider_transaction_id="WISE-DEL"),
        )
    ).json()[0]

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


async def test_deleting_a_conversion_reopens_its_tranche(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    tranche = strategy["tranches"][0]
    created = (
        await client.post(
            "/api/v1/conversions",
            json=conversion_payload(strategy["id"], tranche_id=tranche["id"]),
        )
    ).json()[0]

    tranches = (await client.get(f"/api/v1/strategies/{strategy['id']}/tranches")).json()
    assert tranches[0]["status"] == "completed"

    await client.delete(f"/api/v1/conversions/{created['id']}")
    tranches = (await client.get(f"/api/v1/strategies/{strategy['id']}/tranches")).json()
    assert tranches[0]["status"] == "pending"
    assert tranches[0]["completed_at"] is None


# ---------------------------------------------------------------------------
# Simulated records
# ---------------------------------------------------------------------------


async def test_simulated_conversions_do_not_change_the_real_position(
    client: AsyncClient,
) -> None:
    strategy = await make_strategy(client)
    await client.post(
        "/api/v1/conversions",
        json=conversion_payload(strategy["id"], simulated=True),
    )
    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()
    assert summary["converted_source_amount"] == "0.0000"
    assert summary["remaining_source_amount"] == "800000.0000"
    assert summary["blended_effective_rate"] is None


# ---------------------------------------------------------------------------
# List, import and export
# ---------------------------------------------------------------------------


async def test_the_list_view_aggregates_correctly(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    await client.post("/api/v1/conversions", json=conversion_payload(strategy["id"]))
    await client.post(
        "/api/v1/conversions",
        json=conversion_payload(
            strategy["id"],
            source_amount="160000",
            target_amount="277900",
            fee_target_currency="500",
        ),
    )

    body = (await client.get(f"/api/v1/conversions?strategy_id={strategy['id']}")).json()
    assert body["total_source_amount"] == "280000.0000"
    assert body["total_target_amount"] == "484300.0000"
    assert body["blended_effective_rate"] == "1.72964286"
    assert body["total_fees"] == "500.0000"


async def test_csv_import_previews_then_commits(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    csv_text = (
        "executed_at,source_amount,target_amount,transaction_id,tranche,notes\n"
        "2026-09-15T10:30:00Z,120000,207840,WISE-1,1,Auto conversion\n"
        "2026-09-20T10:30:00Z,160000,278400,WISE-2,2,\n"
        "not-a-date,1000,1750,WISE-3,,\n"
    )
    files = {"file": ("conversions.csv", csv_text, "text/csv")}

    preview = (
        await client.post(f"/api/v1/conversions/import?strategy_id={strategy['id']}", files=files)
    ).json()
    assert preview["accepted"] == 2
    assert preview["rejected"] == 1
    assert preview["imported"] == 0

    committed = (
        await client.post(
            f"/api/v1/conversions/import?strategy_id={strategy['id']}&commit=true", files=files
        )
    ).json()
    assert committed["imported"] == 2

    body = (await client.get(f"/api/v1/conversions?strategy_id={strategy['id']}")).json()
    assert body["total_source_amount"] == "280000.0000"
    # The tranche column resolved to the matching sequence numbers.
    assert {row["tranche_id"] for row in body["conversions"]} == {
        strategy["tranches"][0]["id"],
        strategy["tranches"][1]["id"],
    }


async def test_reimporting_the_same_file_skips_duplicates(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    csv_text = (
        "executed_at,source_amount,target_amount,transaction_id\n"
        "2026-09-15T10:30:00Z,120000,207840,WISE-1\n"
    )
    files = {"file": ("conversions.csv", csv_text, "text/csv")}
    await client.post(
        f"/api/v1/conversions/import?strategy_id={strategy['id']}&commit=true", files=files
    )

    again = (
        await client.post(
            f"/api/v1/conversions/import?strategy_id={strategy['id']}&commit=true", files=files
        )
    ).json()
    assert again["duplicates"] == 1
    assert again["imported"] == 0


async def test_csv_export_round_trips_through_the_importer(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    await client.post(
        "/api/v1/conversions",
        json=conversion_payload(strategy["id"], provider_transaction_id="WISE-RT"),
    )

    response = await client.get(f"/api/v1/conversions/export?strategy_id={strategy['id']}")
    assert response.status_code == 200
    assert response.text.splitlines()[0].startswith("executed_at,source_amount,target_amount")

    other = await make_strategy(client, name="Round trip")
    files = {"file": ("conversions.csv", response.text, "text/csv")}
    preview = (
        await client.post(f"/api/v1/conversions/import?strategy_id={other['id']}", files=files)
    ).json()
    # The transaction ID already exists, so a re-import into another strategy is
    # still recognised as a duplicate rather than silently double-counted.
    assert preview["duplicates"] == 1


async def test_import_requires_the_mandatory_columns(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    files = {"file": ("bad.csv", "date,amount\n2026-01-01,100\n", "text/csv")}
    response = await client.post(
        f"/api/v1/conversions/import?strategy_id={strategy['id']}", files=files
    )
    assert response.status_code == 422
    assert "Missing required column" in response.json()["error"]["message"]


async def test_conversions_are_listed_newest_first(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    await client.post(
        "/api/v1/conversions",
        json=conversion_payload(
            strategy["id"], executed_at=(utcnow() - timedelta(days=5)).isoformat()
        ),
    )
    await client.post(
        "/api/v1/conversions",
        json=conversion_payload(
            strategy["id"],
            source_amount="1000",
            target_amount="1750",
            executed_at=utcnow().isoformat(),
        ),
    )
    body = (await client.get(f"/api/v1/conversions?strategy_id={strategy['id']}")).json()
    assert body["conversions"][0]["source_amount"] == "1000.0000"
