"""Editing a strategy as a JSON document.

The behaviours pinned here are the ones that make pasting a document safe: what
you copy is what the API accepts, every mistake is reported with a location, and
nothing that was recorded as having happened is altered by an edit.
"""

from __future__ import annotations

import json
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
        "final_deadline": (utcnow() + timedelta(days=120)).isoformat(),
        "walk_away_rate": "1.7800",
        "tranches": LADDER,
    }
    payload.update(overrides)
    response = await client.post("/api/v1/strategies", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def read_document(client: AsyncClient, strategy_id: int) -> dict[str, Any]:
    response = await client.get(f"/api/v1/strategies/{strategy_id}/document")
    assert response.status_code == 200, response.text
    return response.json()


async def preview(client: AsyncClient, strategy_id: int, document: Any) -> dict[str, Any]:
    text = document if isinstance(document, str) else json.dumps(document)
    response = await client.post(
        f"/api/v1/strategies/{strategy_id}/document/preview", json={"text": text}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def save(client: AsyncClient, strategy_id: int, document: Any) -> Any:
    text = document if isinstance(document, str) else json.dumps(document)
    return await client.put(f"/api/v1/strategies/{strategy_id}/document", json={"text": text})


async def conversions_of(client: AsyncClient, strategy_id: int) -> list[dict[str, Any]]:
    response = await client.get(f"/api/v1/conversions?strategy_id={strategy_id}")
    assert response.status_code == 200, response.text
    return list(response.json()["conversions"])


# ---------------------------------------------------------------------------
# The document itself
# ---------------------------------------------------------------------------


async def test_the_document_is_the_shape_the_create_endpoint_accepts(
    client: AsyncClient,
) -> None:
    """Copy from one strategy, paste as a new one. No conversion step."""
    original = await make_strategy(client)
    text = (await read_document(client, original["id"]))["text"]

    response = await client.post("/api/v1/strategies", json=json.loads(text))
    assert response.status_code == 201, response.text
    copy = response.json()

    assert [t["target_rate"] for t in copy["tranches"]] == [
        t["target_rate"] for t in original["tranches"]
    ]
    assert copy["initial_source_amount"] == original["initial_source_amount"]


async def test_the_document_leaves_out_what_is_not_a_setting(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    payload = await read_document(client, strategy["id"])
    document = payload["document"]

    for key in ("id", "status", "conversions", "created_at", "updated_at"):
        assert key not in document
    for tranche in document["tranches"]:
        assert "id" not in tranche
        assert "status" not in tranche
        assert "calculated_source_amount" not in tranche

    # The omissions are explained rather than left to be discovered.
    assert "conversions" in payload["omitted"]
    assert "status" in payload["omitted"]


async def test_money_is_text_in_the_document_never_a_json_number(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    assert isinstance(document["initial_source_amount"], str)
    assert isinstance(document["tranches"][0]["target_rate"], str)


async def test_saving_an_unchanged_document_changes_nothing(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    text = (await read_document(client, strategy["id"]))["text"]

    report = await preview(client, strategy["id"], text)
    assert report["valid"] is True
    assert report["changes"] == []

    response = await save(client, strategy["id"], text)
    assert response.status_code == 200, response.text
    assert response.json()["tranches"] == strategy["tranches"]


async def test_a_reformatted_rate_is_not_reported_as_a_change(client: AsyncClient) -> None:
    """``1.72`` and ``1.7200`` are the same rate, and saying otherwise is noise."""
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    document["tranches"][0]["target_rate"] = "1.72"
    document["initial_source_amount"] = "800000"

    report = await preview(client, strategy["id"], document)
    assert report["valid"] is True
    assert report["changes"] == []


async def test_an_omitted_start_date_is_not_reported_as_a_change(client: AsyncClient) -> None:
    """Leaving it out keeps the existing date, so it is not an edit."""
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    assert document["strategy_start_date"] is not None
    del document["strategy_start_date"]

    report = await preview(client, strategy["id"], document)
    assert all(change["path"] != "strategy_start_date" for change in report["changes"])

    response = await save(client, strategy["id"], document)
    assert response.status_code == 200
    assert response.json()["strategy_start_date"] == strategy["strategy_start_date"]


async def test_dated_requirements_round_trip_through_the_document(client: AsyncClient) -> None:
    due = (utcnow() + timedelta(days=45)).isoformat()
    strategy = await make_strategy(
        client,
        requirements=[
            {
                "due_date": due,
                "required_source_amount": "250000",
                "description": "School fees",
            }
        ],
    )
    document = (await read_document(client, strategy["id"]))["document"]
    assert document["requirements"][0]["description"] == "School fees"
    assert document["requirements"][0]["required_source_amount"] == "250000.0000"
    assert document["requirements"][0]["required_percentage"] is None

    report = await preview(client, strategy["id"], document)
    assert report["changes"] == []

    document["requirements"] = []
    report = await preview(client, strategy["id"], document)
    change = next(c for c in report["changes"] if c["path"] == "requirements")
    assert change["before"] == "1 dated requirement(s)"
    assert change["after"] == "0 dated requirement(s)"


async def test_clearing_an_optional_rate_reads_as_not_set(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    document["walk_away_rate"] = None

    report = await preview(client, strategy["id"], document)
    change = next(c for c in report["changes"] if c["path"] == "walk_away_rate")
    assert change["before"] == "1.78000000"
    assert change["after"] == "not set"

    response = await save(client, strategy["id"], document)
    assert response.status_code == 200, response.text
    assert response.json()["walk_away_rate"] is None


# ---------------------------------------------------------------------------
# Errors, and where they are
# ---------------------------------------------------------------------------


async def test_a_syntax_error_reports_its_line_and_column(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    broken = '{\n  "name": "USD to NZD",\n  "initial_source_amount": 800000,,\n}'

    report = await preview(client, strategy["id"], broken)
    assert report["valid"] is False
    problem = report["problems"][0]
    assert problem["line"] == 3
    assert problem["column"] is not None


async def test_an_unknown_field_is_named_rather_than_ignored(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    document["walkaway_rate"] = "1.78"

    report = await preview(client, strategy["id"], document)
    assert report["valid"] is False
    problem = next(p for p in report["problems"] if p["path"] == "walkaway_rate")
    assert "Unknown field" in problem["message"]


async def test_a_bad_tranche_is_located_by_index(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    document["tranches"][2]["target_rate"] = "-1"

    report = await preview(client, strategy["id"], document)
    assert report["valid"] is False
    paths = [problem["path"] for problem in report["problems"]]
    assert "tranches[2].target_rate" in paths


async def test_a_missing_required_field_says_so_plainly(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    del document["initial_source_amount"]

    report = await preview(client, strategy["id"], document)
    assert report["valid"] is False
    problem = next(p for p in report["problems"] if p["path"] == "initial_source_amount")
    assert problem["message"] == "This field is required."


async def test_an_empty_document_is_refused(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    report = await preview(client, strategy["id"], "   \n  ")
    assert report["valid"] is False
    assert "empty" in report["problems"][0]["message"]


async def test_a_json_list_is_not_a_strategy(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    report = await preview(client, strategy["id"], "[1, 2, 3]")
    assert report["valid"] is False
    assert "must be a JSON object" in report["problems"][0]["message"]


async def test_saving_an_invalid_document_returns_the_located_problems(
    client: AsyncClient,
) -> None:
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    document["tranches"][0]["allocation_value"] = "0"

    response = await save(client, strategy["id"], document)
    assert response.status_code == 422
    details = response.json()["error"]["details"]
    assert any(item["path"] == "tranches[0]" for item in details)

    # And nothing was written.
    unchanged = (await client.get(f"/api/v1/strategies/{strategy['id']}")).json()
    assert unchanged["tranches"][0]["allocation_value"] == "15.0000"


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


async def test_a_preview_writes_nothing(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    document["name"] = "Something else"
    document["initial_source_amount"] = "900000"

    report = await preview(client, strategy["id"], document)
    assert report["valid"] is True

    after = (await client.get(f"/api/v1/strategies/{strategy['id']}")).json()
    assert after["name"] == "USD to NZD"
    assert after["initial_source_amount"] == "800000.0000"


async def test_scalar_changes_are_listed_with_before_and_after(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    document["name"] = "Staged conversion"
    document["require_targets_in_order"] = True

    report = await preview(client, strategy["id"], document)
    changes = {change["path"]: change for change in report["changes"]}
    assert changes["name"]["before"] == "USD to NZD"
    assert changes["name"]["after"] == "Staged conversion"
    # Booleans read as words, because "True" in a diff is easy to misread.
    assert changes["require_targets_in_order"]["before"] == "no"
    assert changes["require_targets_in_order"]["after"] == "yes"


async def test_a_tranche_field_change_names_the_sequence(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    document["tranches"][1]["allocation_value"] = "25"
    document["tranches"][3]["allocation_value"] = "15"

    report = await preview(client, strategy["id"], document)
    paths = [change["path"] for change in report["changes"]]
    assert "tranches[sequence=2].allocation_value" in paths
    assert "tranches[sequence=4].allocation_value" in paths


async def test_a_moved_target_is_reported_before_it_resets_alert_state(
    client: AsyncClient,
) -> None:
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    document["tranches"][0]["target_rate"] = "1.7300"

    report = await preview(client, strategy["id"], document)
    assert report["tranches_retargeted"] == [1]
    assert any("alert again" in warning for warning in report["warnings"])


async def test_added_and_removed_tranches_are_counted(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    document["tranches"] = document["tranches"][:3]
    document["tranches"].append(
        {
            "sequence": 9,
            "allocation_type": "percentage",
            "allocation_value": "40",
            "target_rate": "1.9000",
        }
    )

    report = await preview(client, strategy["id"], document)
    assert report["tranches_added"] == [9]
    assert report["tranches_removed"] == [4, 5]


async def test_removing_a_tranche_that_has_conversions_is_warned_about(
    client: AsyncClient,
) -> None:
    strategy = await make_strategy(client)
    tranche = strategy["tranches"][0]
    recorded = await client.post(
        "/api/v1/conversions",
        json={
            "strategy_id": strategy["id"],
            "tranche_id": tranche["id"],
            "executed_at": utcnow().isoformat(),
            "source_amount": "120000",
            "target_amount": "206400",
            "provider": "wise",
        },
    )
    assert recorded.status_code == 201, recorded.text

    document = (await read_document(client, strategy["id"]))["document"]
    document["tranches"] = document["tranches"][1:]

    report = await preview(client, strategy["id"], document)
    assert report["tranches_removed"] == [1]
    warning = next(w for w in report["warnings"] if "Tranche 1" in w)
    assert "1 recorded conversion(s)" in warning
    assert "stay in the totals" in warning


async def test_a_preview_states_how_many_conversions_are_untouched(
    client: AsyncClient,
) -> None:
    strategy = await make_strategy(client)
    await client.post(
        "/api/v1/conversions",
        json={
            "strategy_id": strategy["id"],
            "executed_at": utcnow().isoformat(),
            "source_amount": "120000",
            "target_amount": "206400",
            "provider": "wise",
        },
    )
    document = (await read_document(client, strategy["id"]))["document"]
    document["name"] = "Renamed"

    report = await preview(client, strategy["id"], document)
    assert report["conversions_preserved"] == 1
    assert any("untouched by this edit" in warning for warning in report["warnings"])


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


async def test_saving_keeps_tranche_identity_and_recorded_conversions(
    client: AsyncClient,
) -> None:
    """A pasted document is an edit, not a replacement of the record."""
    strategy = await make_strategy(client)
    tranche = strategy["tranches"][1]
    await client.post(
        "/api/v1/conversions",
        json={
            "strategy_id": strategy["id"],
            "tranche_id": tranche["id"],
            "executed_at": utcnow().isoformat(),
            "source_amount": "160000",
            "target_amount": "278400",
            "provider": "wise",
        },
    )

    document = (await read_document(client, strategy["id"]))["document"]
    document["name"] = "Staged conversion"
    document["tranches"][4]["target_rate"] = "1.8200"

    response = await save(client, strategy["id"], document)
    assert response.status_code == 200, response.text
    updated = response.json()

    assert updated["name"] == "Staged conversion"
    assert [t["id"] for t in updated["tranches"]] == [t["id"] for t in strategy["tranches"]]

    conversions = await conversions_of(client, strategy["id"])
    assert len(conversions) == 1
    assert conversions[0]["tranche_id"] == tranche["id"]


async def test_removing_a_tranche_keeps_its_conversion_and_only_drops_the_link(
    client: AsyncClient,
) -> None:
    strategy = await make_strategy(client)
    tranche = strategy["tranches"][0]
    await client.post(
        "/api/v1/conversions",
        json={
            "strategy_id": strategy["id"],
            "tranche_id": tranche["id"],
            "executed_at": utcnow().isoformat(),
            "source_amount": "120000",
            "target_amount": "206400",
            "provider": "wise",
        },
    )

    document = (await read_document(client, strategy["id"]))["document"]
    document["tranches"] = document["tranches"][1:]

    response = await save(client, strategy["id"], document)
    assert response.status_code == 200, response.text

    conversions = await conversions_of(client, strategy["id"])
    assert len(conversions) == 1, "the financial record must survive an edit to the plan"
    assert conversions[0]["source_amount"] == "120000.0000"
    assert conversions[0]["tranche_id"] is None


async def test_saving_recalculates_the_allocated_amounts(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    document["initial_source_amount"] = "400000"
    document["funds_available_amount"] = "400000"

    response = await save(client, strategy["id"], document)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert [t["calculated_source_amount"] for t in updated["tranches"]] == [
        "60000.0000",
        "80000.0000",
        "100000.0000",
        "80000.0000",
        "80000.0000",
    ]


async def test_saving_records_the_before_and_after_in_the_audit_trail(
    client: AsyncClient,
) -> None:
    strategy = await make_strategy(client)
    document = (await read_document(client, strategy["id"]))["document"]
    document["name"] = "Staged conversion"

    assert (await save(client, strategy["id"], document)).status_code == 200

    events = (await client.get("/api/v1/audit-events?entity_type=strategy")).json()
    updated = next(event for event in events if event["event_type"] == "updated")
    assert json.loads(updated["before_json"])["name"] == "USD to NZD"
    assert json.loads(updated["after_json"])["name"] == "Staged conversion"


async def test_an_archived_strategy_refuses_a_pasted_document(client: AsyncClient) -> None:
    strategy = await make_strategy(client)
    await client.post(
        "/api/v1/conversions",
        json={
            "strategy_id": strategy["id"],
            "executed_at": utcnow().isoformat(),
            "source_amount": "120000",
            "target_amount": "206400",
            "provider": "wise",
        },
    )
    # A strategy with conversions is archived rather than deleted.
    assert (await client.delete(f"/api/v1/strategies/{strategy['id']}")).status_code == 200

    document = (await read_document(client, strategy["id"]))["document"]
    document["name"] = "Reopened"
    response = await save(client, strategy["id"], document)
    assert response.status_code == 409
    assert "archived" in response.json()["error"]["message"]


async def test_a_document_for_a_missing_strategy_is_a_404(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/strategies/9999/document")).status_code == 404


# ---------------------------------------------------------------------------
# Creating from a pasted document
# ---------------------------------------------------------------------------


async def test_a_strategy_can_be_created_from_pasted_text(client: AsyncClient) -> None:
    text = json.dumps(
        {
            "name": "Pasted plan",
            "initial_source_amount": "800000",
            "funds_available_amount": "800000",
            "tranches": LADDER,
        }
    )
    response = await client.post("/api/v1/strategies/document", json={"text": text})
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["name"] == "Pasted plan"
    assert [t["calculated_source_amount"] for t in created["tranches"]] == [
        "120000.0000",
        "160000.0000",
        "200000.0000",
        "160000.0000",
        "160000.0000",
    ]


async def test_a_new_document_can_be_checked_before_it_is_created(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/strategies/document/preview",
        json={"text": json.dumps({"name": "Draft", "initial_source_amount": "1000"})},
    )
    assert response.status_code == 200
    assert response.json()["valid"] is True
    assert (await client.get("/api/v1/strategies")).json() == []


async def test_creating_from_a_broken_document_creates_nothing(client: AsyncClient) -> None:
    response = await client.post("/api/v1/strategies/document", json={"text": "{oops"})
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["line"] == 1
    assert (await client.get("/api/v1/strategies")).json() == []
