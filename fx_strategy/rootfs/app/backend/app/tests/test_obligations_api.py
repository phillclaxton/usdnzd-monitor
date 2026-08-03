"""Obligations through the API: storage, ranking, portfolio and allocation.

The arithmetic itself is covered in test_obligation_engine.py. What matters here
is that it survives the round trip through SQLite and JSON without turning into
a float, that the two rankings behave differently, and that no endpoint exists
which could move money.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

MEIKA = {
    "name": "Meika repayment",
    "obligation_type": "interest_free_loan",
    "total_nzd": "70000",
    "annual_rate": "0",
    "interest_basis": "none",
    "priority": "high",
    "relationship_importance": "high",
    "partial_allowed": False,
}

MORTGAGE = {
    "name": "Mortgage offset",
    "obligation_type": "offset_loan",
    "total_nzd": "256000",
    "annual_rate": "0.0604",
    "interest_basis": "simple_annual",
    "priority": "normal",
    "relationship_importance": "none",
    "partial_allowed": True,
}


@pytest.fixture
async def rate(client: AsyncClient) -> None:
    """A fresh manual rate, so nothing is stale."""
    response = await client.post("/api/v1/rates/manual", json={"rate": "1.7200"})
    assert response.status_code in (200, 201)


async def add(client: AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post("/api/v1/obligations", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


async def test_an_obligation_round_trips_without_becoming_a_float(
    client: AsyncClient, rate: None
) -> None:
    created = await add(client, MORTGAGE)

    # Money and rates cross the API as strings, at full scale.
    assert created["total_nzd"] == "256000.0000"
    assert created["annual_rate"] == "0.06040000"
    assert created["annual_cost_nzd"] == "15462.4000"
    assert isinstance(created["daily_cost_nzd"], str)


async def test_the_specification_figures_survive_the_database(
    client: AsyncClient, rate: None
) -> None:
    """The same numbers the engine tests assert, but read back through SQLite."""
    created = await add(client, MORTGAGE)

    assert abs(Decimal(created["daily_cost_nzd"]) - Decimal("42.36")) < Decimal("0.01")
    assert abs(Decimal(created["monthly_cost_nzd"]) - Decimal("1288.53")) < Decimal("0.01")
    assert abs(Decimal(created["usd_required_now"]) - Decimal("148837.21")) < Decimal("0.01")


async def test_a_percentage_entered_by_mistake_is_refused(client: AsyncClient) -> None:
    """6.04 instead of 0.0604 would silently inflate every figure a hundredfold."""
    response = await client.post("/api/v1/obligations", json={**MORTGAGE, "annual_rate": "6.04"})
    assert response.status_code == 422
    assert "fraction" in response.text


async def test_a_daily_basis_requires_a_daily_rate(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/obligations",
        json={"name": "Odd", "total_nzd": "1000", "interest_basis": "daily_manual"},
    )
    assert response.status_code == 422
    assert "daily rate is required" in response.text.lower()


async def test_editing_keeps_the_previous_value_in_the_audit_trail(
    client: AsyncClient, rate: None
) -> None:
    created = await add(client, MORTGAGE)
    await client.patch(f"/api/v1/obligations/{created['id']}", json={"priority": "critical"})

    events = (await client.get("/api/v1/audit-events")).json()
    updates = [
        e for e in events if e["entity_type"] == "obligation" and e["event_type"] == "updated"
    ]
    assert updates
    assert json.loads(updates[0]["before_json"]) == {"priority": "normal"}
    assert json.loads(updates[0]["after_json"]) == {"priority": "critical"}


# ---------------------------------------------------------------------------
# Funding
# ---------------------------------------------------------------------------


async def test_partial_funding_reduces_the_remaining_amount(
    client: AsyncClient, rate: None
) -> None:
    created = await add(client, MORTGAGE)
    response = await client.post(
        f"/api/v1/obligations/{created['id']}/funding",
        json={"amount_nzd": "56000", "note": "First tranche"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["amount_funded_nzd"] == "56000.0000"
    assert body["remaining_nzd"] == "200000.0000"
    # And the cost now follows the smaller balance.
    assert body["annual_cost_nzd"] == "12080.0000"


async def test_funding_history_survives_a_later_edit(client: AsyncClient, rate: None) -> None:
    """The entries are the record; the running total is a convenience."""
    created = await add(client, MORTGAGE)
    await client.post(f"/api/v1/obligations/{created['id']}/funding", json={"amount_nzd": "10000"})
    await client.post(f"/api/v1/obligations/{created['id']}/funding", json={"amount_nzd": "5000"})
    await client.patch(f"/api/v1/obligations/{created['id']}", json={"notes": "edited"})

    history = (await client.get(f"/api/v1/obligations/{created['id']}/funding")).json()
    assert [row["amount_nzd"] for row in history] == ["10000.0000", "5000.0000"]


async def test_funding_to_the_total_marks_it_complete(client: AsyncClient, rate: None) -> None:
    created = await add(client, {**MORTGAGE, "total_nzd": "1000"})
    body = (
        await client.post(
            f"/api/v1/obligations/{created['id']}/funding", json={"amount_nzd": "1000"}
        )
    ).json()

    assert body["completed"] is True
    assert body["action"] == "FUNDED"


async def test_a_zero_funding_amount_is_refused(client: AsyncClient, rate: None) -> None:
    created = await add(client, MORTGAGE)
    response = await client.post(
        f"/api/v1/obligations/{created['id']}/funding", json={"amount_nzd": "0"}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


async def test_financial_and_overall_rankings_differ(client: AsyncClient, rate: None) -> None:
    """The heart of the feature: an interest-free loan can still come first."""
    await add(client, MORTGAGE)
    await add(client, MEIKA)

    rows = (await client.get("/api/v1/obligations")).json()
    by_name = {row["name"]: row for row in rows}
    meika = by_name["Meika repayment"]
    mortgage = by_name["Mortgage offset"]

    # Financially the mortgage is the more urgent...
    assert mortgage["financial_rank"] < meika["financial_rank"]
    # ...but overall the family loan outranks it.
    assert meika["overall_rank"] < mortgage["overall_rank"]


async def test_the_priority_score_is_itemised(client: AsyncClient, rate: None) -> None:
    """No opaque number: the components add up to the total shown."""
    created = await add(client, MEIKA)
    components = created["priority_components"]

    assert set(components) == {
        "due_urgency",
        "user_priority",
        "relationship",
        "interest_cost",
        "size",
        "max_wait",
        "partial_flexibility",
    }
    total = sum(Decimal(value) for value in components.values())
    assert total == Decimal(created["overall_score"])


async def test_a_zero_interest_obligation_still_ranks_highly(
    client: AsyncClient, rate: None
) -> None:
    await add(client, MORTGAGE)
    meika = await add(client, MEIKA)

    assert meika["daily_cost_nzd"] == "0.0000"
    assert meika["has_interest_cost"] is False
    assert meika["overall_rank"] == 1


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def in_days(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).date().isoformat()


async def test_a_near_due_date_forces_conversion(client: AsyncClient, rate: None) -> None:
    created = await add(client, {**MORTGAGE, "due_date": in_days(3)})

    assert created["action"] == "CONVERT_NOW"
    assert created["days_until_due"] == 3


async def test_an_overdue_obligation_is_reported_as_overdue(
    client: AsyncClient, rate: None
) -> None:
    created = await add(client, {**MORTGAGE, "due_date": in_days(-5)})

    assert created["action"] == "OVERDUE"
    assert created["overdue"] is True


async def test_available_nzd_turns_convert_into_pay(client: AsyncClient, rate: None) -> None:
    await add(client, {**MORTGAGE, "total_nzd": "5000"})
    rows = (await client.get("/api/v1/obligations?nzd_available=9000")).json()

    assert rows[0]["action"] == "PAY_NOW"


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


async def test_the_portfolio_totals_agree_with_the_detail(client: AsyncClient, rate: None) -> None:
    await add(client, MORTGAGE)
    await add(client, MEIKA)

    rows = (await client.get("/api/v1/obligations")).json()
    portfolio = (await client.get("/api/v1/obligations/portfolio")).json()

    assert portfolio["total_obligations"] == 2
    assert Decimal(portfolio["total_nzd"]) == sum(Decimal(r["remaining_nzd"]) for r in rows)
    assert Decimal(portfolio["total_daily_cost_nzd"]) == sum(
        Decimal(r["daily_cost_nzd"]) for r in rows
    )


async def test_the_portfolio_names_the_next_obligation(client: AsyncClient, rate: None) -> None:
    await add(client, MORTGAGE)
    await add(client, {**MEIKA, "due_date": in_days(4)})

    portfolio = (await client.get("/api/v1/obligations/portfolio")).json()
    assert portfolio["next_obligation_name"] == "Meika repayment"
    assert Decimal(portfolio["next_conversion_nzd"]) == Decimal("70000.0000")


async def test_usd_remaining_needs_a_balance_to_be_supplied(
    client: AsyncClient, rate: None
) -> None:
    """Without knowing the USD on hand, the figure is omitted rather than guessed."""
    await add(client, {**MORTGAGE, "priority": "critical"})

    without = (await client.get("/api/v1/obligations/portfolio")).json()
    assert without["usd_after_critical"] is None

    with_balance = (await client.get("/api/v1/obligations/portfolio?usd_on_hand=800000")).json()
    assert Decimal(with_balance["usd_after_critical"]) < Decimal("800000")


async def test_the_weighted_break_even_rate_exceeds_the_current_rate(
    client: AsyncClient, rate: None
) -> None:
    await add(client, MORTGAGE)
    portfolio = (await client.get("/api/v1/obligations/portfolio")).json()

    assert Decimal(portfolio["weighted_break_even_rate"]) > Decimal("1.72")


async def test_an_empty_book_says_so_rather_than_showing_zeroes(client: AsyncClient) -> None:
    portfolio = (await client.get("/api/v1/obligations/portfolio")).json()

    assert portfolio["total_obligations"] == 0
    assert portfolio["strategy_status"] in {"no_obligations", "rate_unavailable"}
    assert portfolio["next_obligation_id"] is None


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------


async def test_the_standard_scenarios_are_offered(client: AsyncClient, rate: None) -> None:
    await add(client, {**MORTGAGE, "priority": "critical"})
    await add(client, MEIKA)

    plans = (await client.get("/api/v1/obligations/allocations")).json()
    labels = [plan["label"] for plan in plans]
    assert labels == ["Critical only", "Critical and due within 14 days", "Everything"]


async def test_a_limited_tranche_funds_what_it_reaches_and_names_the_rest(
    client: AsyncClient, rate: None
) -> None:
    await add(client, {**MORTGAGE, "priority": "critical"})
    meika = await add(client, MEIKA)

    plan = (
        await client.post("/api/v1/obligations/allocations", json={"usd_available": "40000"})
    ).json()

    # 40,000 USD at 1.72 buys 68,800 NZD — not enough for either in full.
    assert Decimal(plan["usd_to_convert"]) <= Decimal("40000")
    assert meika["id"] in plan["unfunded_obligation_ids"]
    assert Decimal(plan["unfunded_nzd"]) > 0


async def test_an_all_or_nothing_obligation_is_never_part_funded(
    client: AsyncClient, rate: None
) -> None:
    """A half-paid loan that does not accept partial payment satisfies nobody."""
    meika = await add(client, MEIKA)

    plan = (
        await client.post("/api/v1/obligations/allocations", json={"usd_available": "1000"})
    ).json()

    assert plan["lines"] == []
    assert meika["id"] in plan["unfunded_obligation_ids"]


async def test_a_partial_obligation_can_be_part_funded(client: AsyncClient, rate: None) -> None:
    await add(client, MORTGAGE)

    plan = (
        await client.post("/api/v1/obligations/allocations", json={"usd_available": "10000"})
    ).json()

    assert len(plan["lines"]) == 1
    assert plan["lines"][0]["fully_funded"] is False
    assert Decimal(plan["lines"][0]["nzd_funded"]) == Decimal("17200.0000")


async def test_a_hypothetical_rate_answers_a_what_if(client: AsyncClient, rate: None) -> None:
    await add(client, MORTGAGE)

    at_current = (await client.post("/api/v1/obligations/allocations", json={})).json()
    at_target = (
        await client.post("/api/v1/obligations/allocations", json={"rate": "1.8000"})
    ).json()

    # A better rate settles the same NZD for less USD.
    assert Decimal(at_target["usd_to_convert"]) < Decimal(at_current["usd_to_convert"])
    assert at_target["rate_stale"] is False


async def test_the_selection_can_be_narrowed(client: AsyncClient, rate: None) -> None:
    mortgage = await add(client, MORTGAGE)
    await add(client, MEIKA)

    plan = (
        await client.post(
            "/api/v1/obligations/allocations", json={"obligation_ids": [mortgage["id"]]}
        )
    ).json()

    assert [line["obligation_id"] for line in plan["lines"]] == [mortgage["id"]]


# ---------------------------------------------------------------------------
# Rate handling and safety
# ---------------------------------------------------------------------------


async def test_without_a_rate_no_usd_figure_is_invented(client: AsyncClient) -> None:
    created = await add(client, MORTGAGE)

    assert created["usd_required_now"] is None
    assert created["action"] == "REVIEW"
    # The NZD side is still perfectly calculable.
    assert created["annual_cost_nzd"] == "15462.4000"


async def test_the_rate_quality_is_labelled_and_never_called_a_wise_quote(
    client: AsyncClient, rate: None
) -> None:
    created = await add(client, MORTGAGE)
    assert created["rate_quality"] == "market"


async def test_every_response_carries_the_disclaimer(client: AsyncClient, rate: None) -> None:
    created = await add(client, MORTGAGE)
    portfolio = (await client.get("/api/v1/obligations/portfolio")).json()

    for text in (created["disclaimer"], portfolio["disclaimer"]):
        assert "not financial advice" in text
        assert "never moves money" in text


async def test_there_is_no_endpoint_that_pays_anything(client: AsyncClient) -> None:
    """A refusal rather than a 404, which could read as 'not built yet'."""
    response = await client.post("/api/v1/obligations/pay", json={})
    assert response.status_code == 422
    assert "does not pay, convert or transfer money" in response.text


async def test_no_obligation_route_mentions_paying_or_transferring(client: AsyncClient) -> None:
    """Structural: the route table itself contains no such operation."""
    from app.config import get_config
    from app.main import create_app
    from app.tests.helpers import all_route_paths

    paths = all_route_paths(create_app(get_config()))
    obligation_paths = [p for p in paths if "obligation" in p]
    assert obligation_paths, "the obligation routes should be mounted"
    for path in obligation_paths:
        assert "transfer" not in path
        assert "execute" not in path
        assert "withdraw" not in path


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_archiving_removes_it_from_the_active_book(client: AsyncClient, rate: None) -> None:
    created = await add(client, MORTGAGE)
    await client.post(f"/api/v1/obligations/{created['id']}/archive")

    active = (await client.get("/api/v1/obligations")).json()
    assert active == []
    everything = (await client.get("/api/v1/obligations?include_inactive=true")).json()
    assert len(everything) == 1


async def test_a_completed_obligation_leaves_the_portfolio_totals(
    client: AsyncClient, rate: None
) -> None:
    created = await add(client, MORTGAGE)
    await client.post(f"/api/v1/obligations/{created['id']}/complete")

    portfolio = (await client.get("/api/v1/obligations/portfolio")).json()
    assert portfolio["total_obligations"] == 0
    assert portfolio["total_nzd"] == "0.0000"


async def test_deleting_keeps_the_audit_record(client: AsyncClient, rate: None) -> None:
    created = await add(client, MORTGAGE)
    response = await client.delete(f"/api/v1/obligations/{created['id']}")
    assert response.status_code == 200

    events = (await client.get("/api/v1/audit-events")).json()
    deletions = [e for e in events if e["event_type"] == "deleted"]
    assert any("Mortgage offset" in e["message"] for e in deletions)


async def test_a_missing_obligation_is_a_404(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/obligations/999")).status_code == 404
    assert (await client.delete("/api/v1/obligations/999")).status_code == 404
