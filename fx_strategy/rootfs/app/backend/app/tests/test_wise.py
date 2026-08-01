"""Wise integration tests.

Two things matter most here: reconciliation is idempotent, and there is no path
through this application that converts money.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.settings import Settings
from app.schemas.strategy import StrategyIn, TrancheIn
from app.security.secrets import get_secret_store, reset_secret_store
from app.services import conversion_service, settings_service, wise_service
from app.services import strategy_service as strategies
from app.services.execution import (
    EXECUTION_REQUIREMENTS,
    DisabledExecutor,
    ExecutionDisabledError,
)

TOKEN = "wise-live-token-abcdef123456"

STATEMENT = {
    "transactions": [
        {
            "referenceNumber": "CONV-A",
            "date": "2026-09-15T10:30:00Z",
            "details": {"type": "CONVERSION", "description": "Auto conversion"},
            "exchangeDetails": {
                "fromAmount": {"value": 120000, "currency": "USD"},
                "toAmount": {"value": 207840, "currency": "NZD"},
                "rate": 1.732,
            },
            "totalFees": {"value": 520, "currency": "NZD"},
        },
        {
            "referenceNumber": "CONV-B",
            "date": "2026-09-20T09:00:00Z",
            "details": {"type": "CONVERSION", "description": ""},
            "exchangeDetails": {
                "fromAmount": {"value": 160000, "currency": "USD"},
                "toAmount": {"value": 278400, "currency": "NZD"},
                "rate": 1.74,
            },
            "totalFees": {"value": 640, "currency": "NZD"},
        },
        {
            "referenceNumber": "CONV-EUR",
            "date": "2026-09-21T09:00:00Z",
            "details": {"type": "CONVERSION", "description": "Different pair"},
            "exchangeDetails": {
                "fromAmount": {"value": 1000, "currency": "EUR"},
                "toAmount": {"value": 1800, "currency": "NZD"},
                "rate": 1.8,
            },
        },
        {"details": {"type": "DEPOSIT"}},
    ]
}


def wise_handler(request: httpx.Request) -> httpx.Response:
    """A stand-in Wise API built from its documented response shapes."""
    path = request.url.path
    if path == "/v1/rates":
        return httpx.Response(
            200,
            json=[
                {
                    "rate": 1.7604,
                    "source": "USD",
                    "target": "NZD",
                    "time": "2026-08-01T09:00:00+0000",
                }
            ],
        )
    if path == "/v2/profiles":
        return httpx.Response(200, json=[{"id": 12345, "type": "personal"}])
    if "balances" in path:
        return httpx.Response(
            200,
            json=[
                {
                    "id": 777,
                    "currency": "USD",
                    "type": "STANDARD",
                    "amount": {"value": 520000.0, "currency": "USD"},
                    "reservedAmount": {"value": 0, "currency": "USD"},
                }
            ],
        )
    if "statement.json" in path:
        return httpx.Response(200, json=STATEMENT)
    if path.endswith("/quotes"):
        return httpx.Response(
            200,
            json={
                "id": "quote-1",
                "rate": 1.7550,
                "sourceAmount": 200000,
                "sourceCurrency": "USD",
                "targetCurrency": "NZD",
                "expirationTime": "2036-08-01T09:30:00Z",
                "paymentOptions": [
                    {"payIn": "BALANCE", "targetAmount": 350_120.0, "fee": {"total": 880.0}}
                ],
            },
        )
    return httpx.Response(404, json={"error": "not found"})


@pytest.fixture
def wise_credentials(app_config: object) -> Any:
    reset_secret_store()
    get_secret_store().set("wise_api_token", TOKEN)
    yield
    reset_secret_store()


@pytest.fixture
def stub_wise(monkeypatch: pytest.MonkeyPatch, wise_credentials: Any) -> None:
    """Point every constructed Wise provider at the stand-in API."""
    from app.providers.wise import WiseProvider

    original = WiseProvider._build_client

    def build(self: WiseProvider) -> httpx.AsyncClient:
        _ = original
        return httpx.AsyncClient(
            base_url=self._base_url, transport=httpx.MockTransport(wise_handler)
        )

    monkeypatch.setattr(WiseProvider, "_build_client", build)


@pytest.fixture
async def settings(session: AsyncSession) -> Settings:
    loaded = await settings_service.load_settings(session)
    loaded.providers.wise.enabled = True
    loaded.providers.wise.profile_id = "12345"
    loaded.providers.wise.source_balance_id = "777"
    await settings_service.save_settings(session, loaded)
    return loaded


@pytest.fixture
async def strategy(session: AsyncSession) -> Any:
    created = await strategies.create_strategy(
        session,
        StrategyIn(
            name="Wise",
            initial_source_amount=Decimal("800000"),
            funds_available_amount=Decimal("800000"),
            tranches=[
                TrancheIn(
                    sequence=1,
                    allocation_type="percentage",
                    allocation_value=Decimal("100"),
                    target_rate=Decimal("1.7600"),
                )
            ],
        ),
    )
    await strategies.activate(session, created)
    await session.flush()
    return created


# ---------------------------------------------------------------------------
# Status and credentials
# ---------------------------------------------------------------------------


async def test_status_without_a_token_says_so(
    session: AsyncSession, settings: Settings, app_config: object
) -> None:
    reset_secret_store()
    result = await wise_service.status(settings)
    assert result.configured is False
    assert result.connected is False
    assert "No Wise API token" in result.message


async def test_status_with_a_working_token(
    session: AsyncSession, settings: Settings, stub_wise: None
) -> None:
    result = await wise_service.status(settings)
    assert result.configured is True
    assert result.connected is True
    assert result.read_only is True
    assert result.profiles == [{"id": "12345", "type": "personal"}]
    # The hint reveals only the last four characters.
    assert result.token_hint.endswith("3456")
    assert TOKEN not in result.token_hint


async def test_the_token_never_appears_in_an_api_response(
    client: AsyncClient, stub_wise: None
) -> None:
    response = await client.get("/api/v1/wise/status")
    assert TOKEN not in response.text
    body = response.json()
    assert body["read_only"] is True
    assert "does not automatically convert" in body["notice"]


async def test_storing_and_removing_a_token_is_audited_without_the_value(
    client: AsyncClient, app_config: object
) -> None:
    reset_secret_store()
    await client.put("/api/v1/wise/credentials", json={"api_token": TOKEN})
    await client.delete("/api/v1/wise/credentials")

    events = (await client.get("/api/v1/audit-events?entity_type=credential")).json()
    assert len(events) == 2
    for event in events:
        assert TOKEN not in (event["after_json"] or "")
        assert TOKEN not in (event["before_json"] or "")
        assert TOKEN not in event["message"]
    assert {event["message"] for event in events} == {
        "Wise API token stored",
        "Wise API token removed",
    }


# ---------------------------------------------------------------------------
# Read-only operations
# ---------------------------------------------------------------------------


async def test_balances_are_read(
    session: AsyncSession, settings: Settings, stub_wise: None
) -> None:
    rows = await wise_service.balances(settings)
    assert rows[0].currency == "USD"
    assert rows[0].amount == Decimal("520000.0000")


async def test_transactions_need_a_balance_id(session: AsyncSession, stub_wise: None) -> None:
    plain = await settings_service.load_settings(session)
    plain.providers.wise.enabled = True
    with pytest.raises(Exception, match="balance ID is required"):
        await wise_service.transactions(plain)


async def test_a_quote_is_labelled_as_an_estimate(
    client: AsyncClient, stub_wise: None, settings: Settings
) -> None:
    response = await client.post("/api/v1/wise/quote?source_amount=200000")
    assert response.status_code == 200
    body = response.json()
    assert body["fee"] == "880.0000"
    assert body["executable_here"] is False
    assert "estimate" in body["note"]


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------


async def test_a_dry_run_changes_nothing(
    session: AsyncSession, settings: Settings, strategy: Any, stub_wise: None
) -> None:
    result = await wise_service.reconcile(session, strategy, settings, commit=False)
    assert result.dry_run is True
    assert result.fetched == 3
    assert result.skipped_other_pair == 1
    assert result.imported == 0
    assert sorted(result.imported_references) == ["CONV-A", "CONV-B"]
    assert await conversion_service.list_conversions(session, strategy_id=strategy.id) == []


async def test_committing_imports_the_unmatched_conversions(
    session: AsyncSession, settings: Settings, strategy: Any, stub_wise: None
) -> None:
    result = await wise_service.reconcile(session, strategy, settings, commit=True)
    assert result.imported == 2

    rows = await conversion_service.list_conversions(session, strategy_id=strategy.id)
    assert len(rows) == 2
    first = next(row for row in rows if row.provider_transaction_id == "CONV-A")
    assert first.source_amount == Decimal("120000.0000")
    assert first.target_amount == Decimal("207840.0000")
    assert first.fee_total_target_equivalent == Decimal("520.0000")
    assert first.record_source == "wise_api"


async def test_reconciliation_is_idempotent(
    session: AsyncSession, settings: Settings, strategy: Any, stub_wise: None
) -> None:
    await wise_service.reconcile(session, strategy, settings, commit=True)
    again = await wise_service.reconcile(session, strategy, settings, commit=True)

    assert again.imported == 0
    assert again.matched == 2
    rows = await conversion_service.list_conversions(session, strategy_id=strategy.id)
    assert len(rows) == 2


async def test_a_conversion_for_another_pair_is_skipped_not_imported(
    session: AsyncSession, settings: Settings, strategy: Any, stub_wise: None
) -> None:
    result = await wise_service.reconcile(session, strategy, settings, commit=True)
    rows = await conversion_service.list_conversions(session, strategy_id=strategy.id)
    assert result.skipped_other_pair == 1
    assert all(row.provider_transaction_id != "CONV-EUR" for row in rows)


async def test_reconciliation_is_audited(
    session: AsyncSession, settings: Settings, strategy: Any, stub_wise: None
) -> None:
    await wise_service.reconcile(session, strategy, settings, commit=True)
    from app.services import audit

    events = await audit.list_events(session, event_type="reconciled")
    assert events
    assert "2 imported" in events[0].message


async def test_the_reconcile_endpoint_defaults_to_a_dry_run(
    client: AsyncClient, stub_wise: None
) -> None:
    await client.post(
        "/api/v1/strategies",
        json={
            "name": "Wise",
            "initial_source_amount": "800000",
            "funds_available_amount": "800000",
            "tranches": [
                {
                    "sequence": 1,
                    "allocation_type": "percentage",
                    "allocation_value": "100",
                    "target_rate": "1.76",
                }
            ],
        },
    )
    await client.put(
        "/api/v1/wise/credentials",
        json={"enabled": True, "profile_id": "12345", "source_balance_id": "777"},
    )

    body = (await client.post("/api/v1/wise/reconcile")).json()
    assert body["dry_run"] is True
    assert body["imported"] == 0
    assert body["fetched"] == 3

    committed = (await client.post("/api/v1/wise/reconcile?commit=true")).json()
    assert committed["imported"] == 2


async def test_reconciling_without_a_strategy_is_a_404(
    client: AsyncClient, stub_wise: None
) -> None:
    assert (await client.post("/api/v1/wise/reconcile")).status_code == 404


# ---------------------------------------------------------------------------
# There is no execution
# ---------------------------------------------------------------------------


async def test_the_only_executor_refuses_to_preview() -> None:
    with pytest.raises(ExecutionDisabledError):
        await DisabledExecutor().preview_conversion("USD", "NZD", Decimal("1000"))


async def test_the_only_executor_refuses_to_execute() -> None:
    with pytest.raises(ExecutionDisabledError, match="never moves money"):
        await DisabledExecutor().execute_conversion("preview", "idempotency", "confirmation")


def test_the_executor_is_disabled_by_default() -> None:
    from app.services.execution import get_executor

    assert get_executor().enabled is False


def test_the_requirements_for_any_future_module_are_recorded() -> None:
    joined = " ".join(EXECUTION_REQUIREMENTS).lower()
    for requirement in (
        "feature flag",
        "acknowledgement",
        "preview",
        "confirmation",
        "maximum amount",
        "idempotency",
        "audit log",
        "unattended retry",
        "stale quote",
        "expiry",
        "disable switch",
    ):
        assert requirement in joined


async def test_the_execution_policy_endpoint_states_the_position(
    client: AsyncClient,
) -> None:
    body = (await client.get("/api/v1/wise/execution-policy")).json()
    assert body["execution_enabled"] is False
    assert "does not execute conversions" in body["message"]
    assert len(body["requirements_for_any_future_module"]) == len(EXECUTION_REQUIREMENTS)


async def test_probing_for_an_execute_endpoint_gets_an_explicit_refusal(
    client: AsyncClient,
) -> None:
    response = await client.post("/api/v1/wise/execute")
    assert response.status_code == 422
    assert "not implemented and will not be performed" in response.json()["error"]["message"]


async def test_no_route_in_the_application_executes_a_conversion(
    client: AsyncClient,
) -> None:
    """A structural check over the whole route table."""
    from app.config import get_config
    from app.main import create_app

    routes = [getattr(route, "path", "") for route in create_app(get_config()).routes]
    banned = ("/transfers", "/execute-conversion", "/convert")
    assert not [path for path in routes if any(token in path for token in banned)]


async def test_an_expired_preview_is_recognised() -> None:
    from app.services.execution import ConversionPreview

    preview = ConversionPreview(
        preview_id="p1",
        source_currency="USD",
        target_currency="NZD",
        source_amount=Decimal("1000"),
        target_amount=Decimal("1750"),
        rate=Decimal("1.75"),
        fee=Decimal("5"),
        fee_currency="USD",
        expires_at=datetime(2020, 1, 1, tzinfo=UTC),
    )
    assert preview.expired is True
