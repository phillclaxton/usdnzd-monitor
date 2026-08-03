"""Home Assistant entities and notifications for obligations.

Two things matter most: an uncalculable figure is published as unknown rather
than as zero, and the messages say enough to act on without opening the app.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from app.home_assistant.entities import EntityContext, state_payload
from app.home_assistant.obligation_entities import (
    definitions_for,
    obligation_definitions,
    portfolio_definitions,
    slugify,
    unique_slugs,
)
from app.schemas.obligation import ObligationOut, PortfolioOut

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
    "priority": "normal",
    "partial_allowed": True,
}


@pytest.fixture
async def rate(client: AsyncClient) -> None:
    await client.post("/api/v1/rates/manual", json={"rate": "1.7200"})


async def add(client: AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post("/api/v1/obligations", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def context_for(rows: list[dict[str, Any]], portfolio: dict[str, Any] | None = None) -> Any:
    """A minimal context carrying only what the obligation entities read."""
    from app.schemas.rates import CurrentRateOut, RateChanges

    return EntityContext(
        rate=CurrentRateOut(
            source_currency="USD",
            target_currency="NZD",
            rate=Decimal("1.72"),
            status="live",
            provider="manual",
            quote_type=None,
            quote_label=None,
            provider_timestamp=None,
            retrieved_at=None,
            age_seconds=None,
            stale_after_seconds=900,
            changes=RateChanges(),
            high_24h=None,
            low_24h=None,
            high_6m=None,
            low_6m=None,
        ),
        summary=None,
        provider_healthy=True,
        provider_message="",
        mqtt_connected=False,
        wise_connected=False,
        simulation=False,
        portfolio=PortfolioOut.model_validate(portfolio) if portfolio else None,
        obligations=[ObligationOut.model_validate(row) for row in rows],
    )


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def test_the_entity_id_follows_the_obligation_name() -> None:
    """The specification's example: sensor.meika_repayment_remaining."""
    assert slugify("Meika repayment") == "meika_repayment"
    assert slugify("Mortgage offset (ANZ)") == "mortgage_offset_anz"


def test_an_unnameable_obligation_still_gets_an_id() -> None:
    assert slugify("!!!") == "obligation"


def test_duplicate_names_do_not_share_entities() -> None:
    """Sharing entity IDs would silently merge two different debts."""
    rows = [
        {"id": 1, "name": "Loan"},
        {"id": 2, "name": "Loan"},
        {"id": 3, "name": "Other"},
    ]
    slugs = unique_slugs([_minimal(row) for row in rows])

    assert slugs[1] == "loan_1"
    assert slugs[2] == "loan_2"
    # A unique name is left clean.
    assert slugs[3] == "other"


def _minimal(row: dict[str, Any]) -> Any:
    """An ObligationOut with only the fields the slug logic reads."""
    from types import SimpleNamespace

    return SimpleNamespace(id=row["id"], name=row["name"])


# ---------------------------------------------------------------------------
# Per-obligation entities
# ---------------------------------------------------------------------------


async def test_an_obligation_becomes_its_own_device(client: AsyncClient, rate: None) -> None:
    created = await add(client, MORTGAGE)
    item = ObligationOut.model_validate(created)
    definitions = definitions_for(item, "mortgage_offset", "FX Strategy Manager")

    devices = {tuple(d.device["identifiers"]) for d in definitions if d.device}
    assert devices == {(f"fx_strategy_obligation_{item.id}",)}
    # Linked to the app's own device rather than floating free.
    assert definitions[0].device is not None
    assert definitions[0].device["via_device"] == "fx_strategy"


async def test_the_specified_sensors_exist(client: AsyncClient, rate: None) -> None:
    created = await add(client, {**MEIKA, "name": "Meika repayment"})
    item = ObligationOut.model_validate(created)
    definitions = definitions_for(item, "meika_repayment", "FX")

    ids = {d.entity_id for d in definitions}
    assert "sensor.meika_repayment_remaining" in ids
    assert "sensor.meika_repayment_usd_required" in ids
    assert "sensor.meika_repayment_daily_waiting_cost" in ids
    assert "sensor.meika_repayment_break_even_days" in ids
    assert "sensor.meika_repayment_break_even_rate_30_days" in ids
    assert "sensor.meika_repayment_recommendation" in ids
    assert "sensor.meika_repayment_priority_rank" in ids


async def test_a_zero_interest_break_even_is_unknown_not_zero(
    client: AsyncClient, rate: None
) -> None:
    """Publishing 0 would say 'the improvement pays for no days', which is wrong."""
    created = await add(client, MEIKA)
    item = ObligationOut.model_validate(created)
    definitions = {d.object_id: d for d in definitions_for(item, "meika", "FX")}
    context = context_for([created])

    assert state_payload(definitions["meika_break_even_days"], context) == ""
    # The daily cost genuinely is zero, and says so.
    assert state_payload(definitions["meika_daily_waiting_cost"], context) == "0.0000"


async def test_the_usd_sensor_is_unknown_without_a_rate(client: AsyncClient) -> None:
    created = await add(client, MORTGAGE)
    item = ObligationOut.model_validate(created)
    definitions = {d.object_id: d for d in definitions_for(item, "m", "FX")}
    context = context_for([created])

    assert state_payload(definitions["m_usd_required"], context) == ""
    # But the NZD side is still published.
    assert state_payload(definitions["m_remaining"], context) == "256000.0000"


async def test_the_attributes_carry_the_score_components(client: AsyncClient, rate: None) -> None:
    """A rank with no explanation is not something to act on."""
    created = await add(client, MORTGAGE)
    item = ObligationOut.model_validate(created)
    definition = definitions_for(item, "m", "FX")[0]
    assert definition.attributes is not None

    attributes = definition.attributes(context_for([created]))
    assert "priority_components" in attributes
    assert set(attributes["priority_components"]) == {
        "due_urgency",
        "user_priority",
        "relationship",
        "interest_cost",
        "size",
        "max_wait",
        "partial_flexibility",
    }
    assert attributes["recommendation_reason"]
    assert "not financial advice" in attributes["disclaimer"]


async def test_the_rate_quality_is_published_and_not_called_a_quote(
    client: AsyncClient, rate: None
) -> None:
    created = await add(client, MORTGAGE)
    item = ObligationOut.model_validate(created)
    definition = definitions_for(item, "m", "FX")[0]
    assert definition.attributes is not None

    assert definition.attributes(context_for([created]))["rate_quality"] == "market"


# ---------------------------------------------------------------------------
# Portfolio entities
# ---------------------------------------------------------------------------


def test_the_portfolio_sensors_are_the_specified_ones() -> None:
    ids = {d.entity_id for d in portfolio_definitions()}

    assert ids == {
        "sensor.fx_total_active_obligations_nzd",
        "sensor.fx_total_usd_required",
        "sensor.fx_total_daily_waiting_cost",
        "sensor.fx_total_monthly_waiting_cost",
        "sensor.fx_next_obligation",
        "sensor.fx_next_conversion_amount_usd",
        "sensor.fx_next_conversion_amount_nzd",
        "sensor.fx_debt_strategy_status",
        "sensor.fx_weighted_break_even_rate",
        "sensor.fx_max_rational_wait_days",
    }


async def test_the_portfolio_sensors_are_unknown_with_no_book(client: AsyncClient) -> None:
    """No obligations means unknown, not a column of confident zeroes."""
    context = context_for([])
    for definition in portfolio_definitions():
        assert state_payload(definition, context) == ""


async def test_the_portfolio_sensors_read_the_summary(client: AsyncClient, rate: None) -> None:
    await add(client, MORTGAGE)
    await add(client, MEIKA)
    rows = (await client.get("/api/v1/obligations")).json()
    portfolio = (await client.get("/api/v1/obligations/portfolio")).json()

    context = context_for(rows, portfolio)
    definitions = {d.object_id: d for d in portfolio_definitions()}

    assert state_payload(definitions["fx_total_active_obligations_nzd"], context) == "326000.0000"
    assert (
        state_payload(definitions["fx_debt_strategy_status"], context)
        == portfolio["strategy_status"]
    )
    assert (
        state_payload(definitions["fx_next_obligation"], context)
        == portfolio["next_obligation_name"]
    )


async def test_every_obligation_contributes_its_own_entities(
    client: AsyncClient, rate: None
) -> None:
    await add(client, MORTGAGE)
    await add(client, MEIKA)
    rows = (await client.get("/api/v1/obligations")).json()

    definitions = obligation_definitions(context_for(rows), "FX")
    # Ten portfolio sensors plus eight per obligation.
    assert len(definitions) == 10 + 8 * 2
    # And every object ID is unique, or Home Assistant would collapse them.
    ids = [d.object_id for d in definitions]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


def in_days(days: int) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).date().isoformat()


async def evaluate_alerts(client: AsyncClient, **kwargs: Any) -> list[Any]:
    from app.database import get_sessionmaker
    from app.services import obligation_alerts, obligation_service, settings_service

    async with get_sessionmaker()() as session:
        settings = await settings_service.load_settings(session)
        ranked = await obligation_service.analyse_all(session, settings)
        return obligation_alerts.evaluate(ranked, settings, **kwargs)


async def test_a_due_obligation_notifies_with_the_amounts(client: AsyncClient, rate: None) -> None:
    await add(client, {**MEIKA, "due_date": in_days(5)})
    alerts = await evaluate_alerts(client)

    due = [a for a in alerts if a.key.startswith("obligation_due_soon")]
    assert due
    message = due[0].message
    # The example from the specification: amount, USD, and why interest is irrelevant.
    assert "NZ$70,000.00" in message
    assert "US$" in message
    assert "interest-free" in message


async def test_an_interest_bearing_obligation_quotes_its_daily_cost(
    client: AsyncClient, rate: None
) -> None:
    await add(client, {**MORTGAGE, "due_date": in_days(3)})
    alerts = await evaluate_alerts(client)

    due = [a for a in alerts if a.key.startswith("obligation_due_soon")]
    assert due
    assert "a day" in due[0].message


async def test_an_overdue_obligation_is_critical(client: AsyncClient, rate: None) -> None:
    from app.models.alert import Severity

    await add(client, {**MORTGAGE, "due_date": in_days(-2)})
    alerts = await evaluate_alerts(client)

    overdue = [a for a in alerts if a.key.startswith("obligation_overdue")]
    assert overdue
    assert overdue[0].severity is Severity.CRITICAL


async def test_a_critical_unfunded_obligation_is_reported(client: AsyncClient, rate: None) -> None:
    await add(client, {**MORTGAGE, "priority": "critical"})
    alerts = await evaluate_alerts(client)

    assert any(a.key.startswith("obligation_critical_unfunded") for a in alerts)


async def test_reaching_the_target_says_it_converts_nothing(
    client: AsyncClient, rate: None
) -> None:
    """A target being reached must never read as though something happened."""
    await add(client, {**MORTGAGE, "target_rate": "1.7000"})
    alerts = await evaluate_alerts(client)

    reached = [a for a in alerts if a.key.startswith("obligation_target_reached")]
    assert reached
    assert "converts nothing on its own" in reached[0].message


async def test_a_material_change_in_the_amount_notifies(client: AsyncClient, rate: None) -> None:
    created = await add(client, MORTGAGE)
    alerts = await evaluate_alerts(client, previous_amounts={created["id"]: Decimal("140000")})

    changed = [a for a in alerts if a.key.startswith("obligation_amount_changed")]
    assert changed
    assert "reflects the rate, not a change to the obligation" in changed[0].message


async def test_a_trivial_change_in_the_amount_stays_quiet(client: AsyncClient, rate: None) -> None:
    """Rate noise must not generate messages the user learns to ignore."""
    created = await add(client, MORTGAGE)
    previous = Decimal(created["usd_required_now"]) - Decimal("10")
    alerts = await evaluate_alerts(client, previous_amounts={created["id"]: previous})

    assert not [a for a in alerts if a.key.startswith("obligation_amount_changed")]


async def test_a_completed_obligation_generates_nothing(client: AsyncClient, rate: None) -> None:
    created = await add(client, {**MORTGAGE, "due_date": in_days(1), "priority": "critical"})
    await client.post(f"/api/v1/obligations/{created['id']}/complete")

    assert await evaluate_alerts(client) == []


def test_moving_between_the_two_waiting_states_is_not_worth_a_message() -> None:
    from app.services.obligation_alerts import recommended_action_changed
    from app.services.obligation_engine import RecommendedAction

    assert not recommended_action_changed(
        RecommendedAction.WAIT_FOR_TARGET, RecommendedAction.WAIT_WITH_DEADLINE
    )
    assert recommended_action_changed(
        RecommendedAction.WAIT_FOR_TARGET, RecommendedAction.CONVERT_NOW
    )
