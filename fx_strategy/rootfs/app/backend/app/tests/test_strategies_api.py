"""Strategy, tranche and summary endpoint tests.

These walk the initial user scenario: USD 800,000, the recommended ladder, a
rate, and the figures the dashboard puts in front of the user.
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


def strategy_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "USD to NZD",
        "source_currency": "USD",
        "target_currency": "NZD",
        "initial_source_amount": "800000",
        "funds_available_amount": "800000",
        "final_deadline": (utcnow() + timedelta(days=120)).isoformat(),
        "walk_away_rate": "1.7800",
        "minimum_acceptable_rate": "1.7000",
        "tranches": LADDER,
    }
    payload.update(overrides)
    return payload


async def create(client: AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await client.post("/api/v1/strategies", json=strategy_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Creation and validation
# ---------------------------------------------------------------------------


async def test_the_recommended_ladder_allocates_the_documented_amounts(
    client: AsyncClient,
) -> None:
    strategy = await create(client)
    amounts = [tranche["calculated_source_amount"] for tranche in strategy["tranches"]]
    assert amounts == [
        "120000.0000",
        "160000.0000",
        "200000.0000",
        "160000.0000",
        "160000.0000",
    ]


async def test_templates_include_the_recommended_ladder(client: AsyncClient) -> None:
    templates = (await client.get("/api/v1/strategy-templates")).json()
    keys = {template["key"] for template in templates}
    assert {"recommended", "equal", "monitor_only"} == keys
    recommended = next(t for t in templates if t["key"] == "recommended")
    assert [t["target_rate"] for t in recommended["tranches"]] == [
        "1.7200",
        "1.7400",
        "1.7600",
        "1.7800",
        "1.8000",
    ]


async def test_over_allocation_is_rejected_with_a_useful_message(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/strategies",
        json=strategy_payload(
            tranches=[
                {
                    "sequence": 1,
                    "allocation_type": "percentage",
                    "allocation_value": "60",
                    "target_rate": "1.72",
                },
                {
                    "sequence": 2,
                    "allocation_type": "percentage",
                    "allocation_value": "60",
                    "target_rate": "1.75",
                },
            ]
        ),
    )
    # The strategy is created, but validation reports the problem and
    # activation is refused.
    strategy_id = response.json()["id"]
    report = (await client.get(f"/api/v1/strategies/{strategy_id}/validate")).json()
    assert report["valid"] is False
    assert any("more than 100%" in issue["message"] for issue in report["issues"])

    activate = await client.post(f"/api/v1/strategies/{strategy_id}/activate")
    assert activate.status_code == 422


async def test_a_deliberate_reserve_is_allowed_with_a_warning(client: AsyncClient) -> None:
    strategy = await create(
        client,
        tranches=[
            {
                "sequence": 1,
                "allocation_type": "percentage",
                "allocation_value": "50",
                "target_rate": "1.72",
            },
            {
                "sequence": 2,
                "allocation_type": "percentage",
                "allocation_value": "30",
                "target_rate": "1.76",
            },
        ],
    )
    report = (await client.get(f"/api/v1/strategies/{strategy['id']}/validate")).json()
    assert report["valid"] is True
    assert report["unallocated"] == "160000.0000"
    assert any(issue["severity"] == "warning" for issue in report["issues"])
    # A reserve does not block activation.
    assert (await client.post(f"/api/v1/strategies/{strategy['id']}/activate")).status_code == 200


async def test_matching_currencies_are_rejected(client: AsyncClient) -> None:
    response = await client.post("/api/v1/strategies", json=strategy_payload(target_currency="USD"))
    assert response.status_code == 422


async def test_available_above_the_total_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/strategies", json=strategy_payload(funds_available_amount="900000")
    )
    assert response.status_code == 422


async def test_a_deadline_before_funds_arrive_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/strategies",
        json=strategy_payload(
            funds_arrival_date=(utcnow() + timedelta(days=30)).isoformat(),
            final_deadline=(utcnow() + timedelta(days=10)).isoformat(),
        ),
    )
    assert response.status_code == 422


async def test_duplicate_sequences_are_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/strategies",
        json=strategy_payload(
            tranches=[
                {
                    "sequence": 1,
                    "allocation_type": "percentage",
                    "allocation_value": "50",
                    "target_rate": "1.72",
                },
                {
                    "sequence": 1,
                    "allocation_type": "percentage",
                    "allocation_value": "50",
                    "target_rate": "1.76",
                },
            ]
        ),
    )
    assert response.status_code == 422


async def test_a_strategy_with_no_tranches_cannot_be_activated(client: AsyncClient) -> None:
    strategy = await create(client, tranches=[])
    response = await client.post(f"/api/v1/strategies/{strategy['id']}/activate")
    assert response.status_code == 422
    assert "at least one tranche" in response.json()["error"]["message"]


# ---------------------------------------------------------------------------
# The dashboard summary
# ---------------------------------------------------------------------------


async def test_summary_puts_the_dollar_consequence_beside_the_rate(
    client: AsyncClient,
) -> None:
    strategy = await create(client)
    await client.post("/api/v1/rates/manual", json={"rate": "1.7550"})

    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()

    assert summary["current_rate"] == "1.75500000"
    assert summary["remaining_source_amount"] == "800000.0000"
    # The headline exposure figure: one cent on USD 800,000 is NZD 8,000.
    assert summary["one_cent_exposure"] == "8000.0000"
    assert summary["convert_all_now"]["gross_target_amount"] == "1404000.0000"
    # No fee model is configured, so net is absent rather than shown as gross.
    assert summary["convert_all_now"]["net_target_amount"] is None
    assert summary["convert_all_now"]["fee"]["label"] == "Fee not included"
    assert any("No fee model" in warning for warning in summary["warnings"])


async def test_summary_classifies_the_rate_zone(client: AsyncClient) -> None:
    strategy = await create(client)
    await client.post("/api/v1/rates/manual", json={"rate": "1.7650"})
    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()
    assert summary["rate_zone"]["label"] == "Very good"

    await client.post("/api/v1/rates/manual", json={"rate": "1.6500"})
    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()
    assert summary["rate_zone"]["label"] == "Unfavourable"


async def test_summary_identifies_the_next_target_and_its_upside(client: AsyncClient) -> None:
    strategy = await create(client)
    await client.post("/api/v1/rates/manual", json={"rate": "1.7550"})
    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()

    assert summary["next_target_rate"] == "1.76000000"
    assert summary["next_target_source_amount"] == "200000.0000"
    # 200,000 x (1.7600 - 1.7550) = 1,000.
    assert summary["next_target_upside"] == "1000.0000"


async def test_summary_sensitivity_matches_the_specification(client: AsyncClient) -> None:
    strategy = await create(client)
    await client.post("/api/v1/rates/manual", json={"rate": "1.7550"})
    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()
    downside = {row["movement"]: row["downside"] for row in summary["sensitivity"]}
    assert downside["0.00500000"] == "-4000.0000"
    assert downside["0.01000000"] == "-8000.0000"
    assert downside["0.03000000"] == "-24000.0000"
    assert downside["0.10000000"] == "-80000.0000"


async def test_tranche_progress_shows_distance_and_projected_proceeds(
    client: AsyncClient,
) -> None:
    strategy = await create(client)
    await client.post("/api/v1/rates/manual", json={"rate": "1.7550"})
    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()

    third = summary["tranche_progress"][2]
    assert third["tranche"]["target_rate"] == "1.76000000"
    assert third["distance_to_target"] == "0.00500000"
    assert third["target_reached_now"] is False
    assert third["estimated_gross"] == "352000.0000"

    first = summary["tranche_progress"][0]
    assert first["target_reached_now"] is True
    assert first["distance_to_target"] == "-0.03500000"


async def test_walk_away_analysis_shows_both_sides(client: AsyncClient) -> None:
    strategy = await create(client)
    await client.post("/api/v1/rates/manual", json={"rate": "1.7800"})
    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()

    walk_away = summary["walk_away"]
    assert walk_away["reached"] is True
    assert walk_away["convert_now"]["gross_target_amount"] == "1424000.0000"
    # Holding out for 1.8000 adds 16,000 before fees...
    assert walk_away["difference_versus_waiting"] == "16000.0000"
    # ...while leaving the whole position exposed: 8,000 per cent of movement.
    assert walk_away["sensitivity"][1]["downside"] == "-8000.0000"
    assert walk_away["rate_movement_to_next_target"] == "0.02000000"


async def test_deadline_severity_bands(client: AsyncClient) -> None:
    strategy = await create(client, final_deadline=(utcnow() + timedelta(days=5)).isoformat())
    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()
    assert summary["deadline_severity"] == "critical"
    assert summary["days_to_deadline"] == 5


async def test_summary_without_any_rate_reports_it_plainly(client: AsyncClient) -> None:
    strategy = await create(client)
    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()
    assert summary["current_rate"] is None
    assert summary["rate_status"] == "unavailable"
    assert summary["convert_all_now"] is None
    assert summary["rate_zone"] is None


async def test_active_summary_endpoint_follows_the_selected_strategy(
    client: AsyncClient,
) -> None:
    assert (await client.get("/api/v1/summary")).status_code == 404
    strategy = await create(client)
    summary = (await client.get("/api/v1/summary")).json()
    assert summary["strategy"]["id"] == strategy["id"]


# ---------------------------------------------------------------------------
# Fee models
# ---------------------------------------------------------------------------


async def test_a_fee_model_turns_gross_figures_into_net(client: AsyncClient) -> None:
    fee = (
        await client.post(
            "/api/v1/fee-models",
            json={"name": "Wise estimate", "fee_type": "percentage", "percentage_fee": "0.41"},
        )
    ).json()
    strategy = await create(client, fee_model_id=fee["id"])
    await client.post("/api/v1/rates/manual", json={"rate": "1.7550"})

    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()
    convert_now = summary["convert_all_now"]
    assert convert_now["gross_target_amount"] == "1404000.0000"
    assert convert_now["fee"]["available"] is True
    assert convert_now["fee"]["amount_source_currency"] == "3280.0000"
    assert convert_now["fee"]["amount_target_currency"] == "5756.4000"
    assert convert_now["net_target_amount"] == "1398243.6000"
    assert convert_now["effective_rate"] == "1.74780450"
    assert not any("No fee model" in warning for warning in summary["warnings"])


async def test_a_fee_model_in_use_cannot_be_deleted(client: AsyncClient) -> None:
    fee = (
        await client.post(
            "/api/v1/fee-models",
            json={"name": "In use", "fee_type": "percentage", "percentage_fee": "0.5"},
        )
    ).json()
    await create(client, fee_model_id=fee["id"])
    response = await client.delete(f"/api/v1/fee-models/{fee['id']}")
    assert response.status_code == 409


async def test_an_implausible_fee_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/fee-models",
        json={"name": "Absurd", "fee_type": "percentage", "percentage_fee": "150"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_activate_pause_resume_complete(client: AsyncClient) -> None:
    strategy = await create(client)
    strategy_id = strategy["id"]

    assert (await client.post(f"/api/v1/strategies/{strategy_id}/activate")).json()[
        "status"
    ] == "active"
    assert (await client.post(f"/api/v1/strategies/{strategy_id}/pause")).json()[
        "status"
    ] == "paused"
    assert (await client.post(f"/api/v1/strategies/{strategy_id}/resume")).json()[
        "status"
    ] == "active"
    completed = (await client.post(f"/api/v1/strategies/{strategy_id}/complete")).json()
    assert completed["status"] == "completed"
    assert completed["completed_at"] is not None


async def test_resume_only_applies_to_a_paused_strategy(client: AsyncClient) -> None:
    strategy = await create(client)
    response = await client.post(f"/api/v1/strategies/{strategy['id']}/resume")
    assert response.status_code == 409


async def test_duplicate_copies_the_plan_without_the_history(client: AsyncClient) -> None:
    strategy = await create(client)
    copy = (await client.post(f"/api/v1/strategies/{strategy['id']}/duplicate")).json()
    assert copy["id"] != strategy["id"]
    assert copy["status"] == "draft"
    assert copy["name"].endswith("(copy)")
    assert [t["target_rate"] for t in copy["tranches"]] == [
        t["target_rate"] for t in strategy["tranches"]
    ]


async def test_lifecycle_changes_are_audited(client: AsyncClient) -> None:
    strategy = await create(client)
    await client.post(f"/api/v1/strategies/{strategy['id']}/activate")
    events = (
        await client.get(f"/api/v1/audit-events?entity_type=strategy&entity_id={strategy['id']}")
    ).json()
    types = {event["event_type"] for event in events}
    assert "created" in types
    assert "activated" in types


# ---------------------------------------------------------------------------
# Tranche editing
# ---------------------------------------------------------------------------


async def test_adding_a_tranche_recalculates_every_allocation(client: AsyncClient) -> None:
    strategy = await create(
        client,
        tranches=[
            {
                "sequence": 1,
                "allocation_type": "percentage",
                "allocation_value": "50",
                "target_rate": "1.72",
            },
        ],
    )
    await client.post(
        f"/api/v1/strategies/{strategy['id']}/tranches",
        json={
            "sequence": 2,
            "allocation_type": "remainder",
            "allocation_value": "0",
            "target_rate": "1.78",
        },
    )
    tranches = (await client.get(f"/api/v1/strategies/{strategy['id']}/tranches")).json()
    assert [t["calculated_source_amount"] for t in tranches] == ["400000.0000", "400000.0000"]


async def test_a_duplicate_sequence_is_refused(client: AsyncClient) -> None:
    strategy = await create(client)
    response = await client.post(
        f"/api/v1/strategies/{strategy['id']}/tranches",
        json={
            "sequence": 1,
            "allocation_type": "percentage",
            "allocation_value": "5",
            "target_rate": "1.9",
        },
    )
    assert response.status_code == 409


async def test_reordering_renumbers_the_sequence(client: AsyncClient) -> None:
    strategy = await create(client)
    ids = [tranche["id"] for tranche in strategy["tranches"]]
    reordered = (
        await client.post(
            "/api/v1/tranches/reorder",
            json={"strategy_id": strategy["id"], "tranche_ids": list(reversed(ids))},
        )
    ).json()
    assert [tranche["id"] for tranche in reordered] == list(reversed(ids))
    assert [tranche["sequence"] for tranche in reordered] == [1, 2, 3, 4, 5]


async def test_an_incomplete_reorder_is_rejected(client: AsyncClient) -> None:
    strategy = await create(client)
    ids = [tranche["id"] for tranche in strategy["tranches"]]
    response = await client.post(
        "/api/v1/tranches/reorder",
        json={"strategy_id": strategy["id"], "tranche_ids": ids[:2]},
    )
    assert response.status_code == 422


async def test_moving_a_target_resets_its_reached_state(client: AsyncClient) -> None:
    from sqlalchemy import select

    from app.database import get_sessionmaker
    from app.models.strategy import Tranche, TrancheStatus

    strategy = await create(client)
    tranche_id = strategy["tranches"][0]["id"]

    async with get_sessionmaker()() as session:
        tranche = (
            (await session.execute(select(Tranche).where(Tranche.id == tranche_id))).scalars().one()
        )
        tranche.status = str(TrancheStatus.TARGET_REACHED)
        tranche.target_first_reached_at = utcnow()
        tranche.notification_sent_at = utcnow()
        await session.commit()

    updated = (
        await client.put(
            f"/api/v1/tranches/{tranche_id}",
            json={
                "sequence": 1,
                "allocation_type": "percentage",
                "allocation_value": "15",
                "target_rate": "1.8500",
            },
        )
    ).json()
    assert updated["status"] == "pending"
    assert updated["target_first_reached_at"] is None
    assert updated["notification_sent_at"] is None


async def test_acknowledging_does_not_mark_a_tranche_converted(client: AsyncClient) -> None:
    strategy = await create(client)
    tranche_id = strategy["tranches"][0]["id"]
    acknowledged = (await client.post(f"/api/v1/tranches/{tranche_id}/acknowledge")).json()
    assert acknowledged["acknowledged_at"] is not None
    # Crucially, still not completed.
    assert acknowledged["status"] == "pending"
    assert acknowledged["completed_at"] is None


async def test_skipping_a_tranche_reallocates_nothing_but_closes_it(
    client: AsyncClient,
) -> None:
    strategy = await create(client)
    tranche_id = strategy["tranches"][0]["id"]
    skipped = (await client.post(f"/api/v1/tranches/{tranche_id}/skip")).json()
    assert skipped["status"] == "skipped"

    await client.post("/api/v1/rates/manual", json={"rate": "1.7000"})
    summary = (await client.get(f"/api/v1/strategies/{strategy['id']}/summary")).json()
    # The skipped tranche no longer counts as the next target.
    assert summary["next_target_rate"] == "1.74000000"


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def test_scenarios_present_trade_offs_without_ranking_them(client: AsyncClient) -> None:
    strategy = await create(client)
    await client.post("/api/v1/rates/manual", json={"rate": "1.7550"})

    body = (await client.get(f"/api/v1/strategies/{strategy['id']}/scenarios")).json()
    keys = [scenario["key"] for scenario in body["scenarios"]]
    assert keys == ["convert_all_now", "target_ladder", "equal_schedule"]
    assert "not a ranking" in body["note"]

    now = body["scenarios"][0]
    ladder = body["scenarios"][1]
    assert now["gross_target_amount"] == "1404000.0000"
    assert now["exposed_source_amount"] == "0.0000"
    assert ladder["gross_target_amount"] == "1409600.0000"
    assert ladder["blended_rate"] == "1.76200000"
    # The ladder produces more but keeps the whole position exposed.
    assert ladder["exposed_source_amount"] == "800000.0000"
    assert ladder["one_cent_exposure"] == "8000.0000"
    assert any("never reached" in note for note in ladder["assumptions"])


async def test_a_custom_scenario_can_be_added(client: AsyncClient) -> None:
    strategy = await create(client)
    await client.post("/api/v1/rates/manual", json={"rate": "1.7550"})
    body = (
        await client.get(f"/api/v1/strategies/{strategy['id']}/scenarios?custom_rate=1.8500")
    ).json()
    custom = next(s for s in body["scenarios"] if s["key"] == "custom")
    assert custom["gross_target_amount"] == "1480000.0000"
    assert custom["rate_required"] == "1.85000000"


async def test_a_non_positive_custom_rate_is_rejected(client: AsyncClient) -> None:
    strategy = await create(client)
    response = await client.get(f"/api/v1/strategies/{strategy['id']}/scenarios?custom_rate=0")
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


async def test_a_strategy_without_history_is_deleted(client: AsyncClient) -> None:
    strategy = await create(client)
    response = await client.delete(f"/api/v1/strategies/{strategy['id']}")
    assert response.status_code == 200
    assert (await client.get(f"/api/v1/strategies/{strategy['id']}")).status_code == 404


async def test_missing_strategies_return_404(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/strategies/999")).status_code == 404
    assert (await client.post("/api/v1/strategies/999/activate")).status_code == 404
    assert (await client.get("/api/v1/strategies/999/summary")).status_code == 404
