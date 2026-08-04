"""Provider adapter tests, driven by recorded response payloads.

No test makes a network call. Each provider is exercised against the JSON shape
its vendor documents, so a change in our parsing is caught without depending on
someone else's uptime.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest

from app.providers.base import (
    ProviderConfigurationError,
    ProviderResponseError,
    ProviderUnavailableError,
    QuoteType,
    normalize_rate,
)
from app.providers.generic import GenericProvider, apply_preset, parse_provider_timestamp
from app.providers.http import json_path
from app.providers.manual import ManualProvider
from app.providers.wise import WiseProvider
from app.schemas.settings import GenericProviderSettings, WiseProviderSettings


def mock_transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def install(provider: Any, handler: Any) -> None:
    """Point a provider's HTTP client at a mock transport."""
    provider._client = httpx.AsyncClient(
        base_url=provider._base_url,
        transport=mock_transport(handler),
    )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def test_normalize_inverts_when_the_provider_quotes_the_other_way() -> None:
    # A provider quoting USD per NZD gives 0.5714...; we store NZD per USD.
    assert normalize_rate("0.57142857", inverted=True, provider="x") == Decimal("1.75000000")
    assert normalize_rate("1.75", inverted=False, provider="x") == Decimal("1.75000000")


def test_normalize_rejects_a_non_positive_rate() -> None:
    for bad in ("0", "-1.75"):
        with pytest.raises(ProviderResponseError):
            normalize_rate(bad, inverted=False, provider="x")


def test_normalize_rejects_unreadable_values() -> None:
    with pytest.raises(ProviderResponseError):
        normalize_rate("not-a-rate", inverted=False, provider="x")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (1767225600, datetime(2026, 1, 1, tzinfo=UTC)),
        (1767225600000, datetime(2026, 1, 1, tzinfo=UTC)),
        ("2026-01-01T00:00:00Z", datetime(2026, 1, 1, tzinfo=UTC)),
        ("2026-01-01T00:00:00+0000", datetime(2026, 1, 1, tzinfo=UTC)),
        ("2026-01-01", datetime(2026, 1, 1, tzinfo=UTC)),
    ],
)
def test_provider_timestamp_formats(raw: Any, expected: datetime) -> None:
    assert parse_provider_timestamp(raw) == expected


def test_unreadable_timestamp_is_none_not_a_guess() -> None:
    assert parse_provider_timestamp("last Tuesday") is None
    assert parse_provider_timestamp("") is None


def test_json_path_reads_nested_and_indexed_values() -> None:
    document = {"rates": {"NZD": 1.75}, "data": [{"rate": 1.76}]}
    assert json_path(document, "rates.{target}", provider="x", target="NZD") == 1.75
    assert json_path(document, "data.0.rate", provider="x") == 1.76


def test_json_path_names_the_missing_field() -> None:
    with pytest.raises(ProviderResponseError, match=r"rates\.AUD"):
        json_path({"rates": {"NZD": 1.75}}, "rates.{target}", provider="x", target="AUD")


# ---------------------------------------------------------------------------
# Manual provider
# ---------------------------------------------------------------------------


async def test_manual_provider_returns_the_entered_rate() -> None:
    provider = ManualProvider(rate=Decimal("1.75"), entered_at=datetime(2026, 8, 1, tzinfo=UTC))
    quote = await provider.get_spot_rate("USD", "NZD")
    assert quote.rate == Decimal("1.75")
    assert quote.quote_type is QuoteType.MANUAL
    assert quote.is_executable is False


async def test_manual_provider_reports_nothing_entered_as_a_configuration_state() -> None:
    """Not an outage: there is simply nothing to serve yet.

    Raising an "unavailable" here made an unused fallback record a failure, and
    since the chain stops at the first success it could never record a success
    to clear it — so it showed as failing for ever.
    """
    provider = ManualProvider()
    assert provider.configured is False
    with pytest.raises(ProviderConfigurationError, match="No manual rate"):
        await provider.get_spot_rate("USD", "NZD")


async def test_a_manual_provider_with_a_rate_is_configured() -> None:
    assert ManualProvider(rate=Decimal("1.75")).configured is True


async def test_manual_provider_marks_simulated_rates() -> None:
    provider = ManualProvider(rate=Decimal("1.80"), simulated=True)
    quote = await provider.get_spot_rate("USD", "NZD")
    assert quote.quote_type is QuoteType.SIMULATED
    assert quote.provider == "simulation"


async def test_unsupported_currency_is_refused() -> None:
    provider = ManualProvider(rate=Decimal("1.75"))
    with pytest.raises(ProviderConfigurationError):
        await provider.get_spot_rate("USD", "XYZ")


# ---------------------------------------------------------------------------
# Generic provider
# ---------------------------------------------------------------------------


def generic_settings(**overrides: Any) -> GenericProviderSettings:
    base = GenericProviderSettings(
        enabled=True,
        display_name="Test provider",
        base_url="https://rates.example",
        rate_path="/latest",
        auth_style="header",
        auth_name="apikey",
        source_param="base",
        target_param="symbols",
        rate_json_path="rates.{target}",
        timestamp_json_path="timestamp",
    )
    return base.model_copy(update=overrides)


async def test_generic_provider_reads_a_documented_response() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200, json={"rates": {"NZD": 1.7604}, "timestamp": 1767225600, "base": "USD"}
        )

    provider = GenericProvider(generic_settings(), api_key="secret-key")
    install(provider, handler)
    quote = await provider.get_spot_rate("USD", "NZD")

    assert quote.rate == Decimal("1.76040000")
    assert quote.quote_type is QuoteType.MID_MARKET
    assert quote.provider_timestamp == datetime(2026, 1, 1, tzinfo=UTC)
    assert seen["headers"]["apikey"] == "secret-key"
    assert seen["params"] == {"base": "USD", "symbols": "NZD"}
    await provider.aclose()


async def test_generic_provider_inverts_when_configured_to() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"rates": {"NZD": 0.5714}, "timestamp": 1767225600})

    provider = GenericProvider(
        generic_settings(convention="source_per_target"), api_key="secret-key"
    )
    install(provider, handler)
    quote = await provider.get_spot_rate("USD", "NZD")
    # 1 / 0.5714 = 1.75008...
    assert quote.rate == Decimal("1.75008750")
    await provider.aclose()


async def test_generic_provider_sends_the_key_as_a_query_parameter() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"rates": {"NZD": 1.75}})

    provider = GenericProvider(
        generic_settings(auth_style="query", auth_name="access_key"), api_key="k"
    )
    install(provider, handler)
    await provider.get_spot_rate("USD", "NZD")
    assert seen["params"]["access_key"] == "k"
    await provider.aclose()


async def test_generic_provider_handles_single_symbol_providers() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"rate": "1.7550", "timestamp": 1767225600})

    provider = GenericProvider(
        generic_settings(
            source_param="symbol", target_param="", rate_json_path="rate", auth_style="none"
        ),
        api_key=None,
    )
    install(provider, handler)
    quote = await provider.get_spot_rate("USD", "NZD")
    assert seen["params"]["symbol"] == "USD/NZD"
    assert quote.rate == Decimal("1.75500000")
    await provider.aclose()


async def test_generic_provider_surfaces_an_http_error_rather_than_a_rate() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad key"})

    provider = GenericProvider(generic_settings(), api_key="wrong")
    install(provider, handler)
    with pytest.raises(ProviderUnavailableError, match="credential was rejected"):
        await provider.get_spot_rate("USD", "NZD")
    await provider.aclose()


async def test_generic_provider_reports_a_missing_field_usefully() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"conversion_rates": {"NZD": 1.75}})

    provider = GenericProvider(generic_settings(), api_key="k")
    install(provider, handler)
    with pytest.raises(ProviderResponseError, match="conversion_rates"):
        await provider.get_spot_rate("USD", "NZD")
    await provider.aclose()


async def test_generic_provider_parses_history() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "rates": {
                    "2026-07-01": {"NZD": 1.75},
                    "2026-07-02": {"NZD": 1.76},
                    "not-a-date": {"NZD": 1.77},
                }
            },
        )

    provider = GenericProvider(generic_settings(history_path="/timeseries"), api_key="k")
    install(provider, handler)
    points = await provider.get_historical_rates(
        "USD", "NZD", datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 3, tzinfo=UTC), "day"
    )
    assert [point.rate for point in points] == [Decimal("1.75000000"), Decimal("1.76000000")]
    await provider.aclose()


def test_generic_provider_refuses_to_start_without_configuration() -> None:
    with pytest.raises(ProviderConfigurationError, match="base URL"):
        GenericProvider(generic_settings(base_url=""), api_key="k")
    with pytest.raises(ProviderConfigurationError, match="API key"):
        GenericProvider(generic_settings(), api_key=None)


def test_presets_fill_in_a_working_configuration() -> None:
    configured = apply_preset(GenericProviderSettings(), "openexchangerates")
    assert configured.base_url == "https://openexchangerates.org/api"
    assert configured.rate_json_path == "rates.{target}"
    assert configured.auth_style == "query"
    with pytest.raises(ProviderConfigurationError):
        apply_preset(GenericProviderSettings(), "not-a-provider")


# ---------------------------------------------------------------------------
# Wise provider
# ---------------------------------------------------------------------------


def wise_settings(**overrides: Any) -> WiseProviderSettings:
    return WiseProviderSettings(enabled=True, profile_id="12345").model_copy(update=overrides)


async def test_wise_reads_the_documented_rate_payload() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(
            200,
            json=[
                {
                    "rate": 1.76043,
                    "source": "USD",
                    "target": "NZD",
                    "time": "2026-08-01T09:00:00+0000",
                }
            ],
        )

    provider = WiseProvider(wise_settings(), api_token="wise-token")
    install(provider, handler)
    quote = await provider.get_spot_rate("USD", "NZD")

    assert quote.rate == Decimal("1.76043000")
    # /v1/rates is the mid-market reference, not an executable rate.
    assert quote.quote_type is QuoteType.MID_MARKET
    assert quote.is_executable is False
    assert seen["auth"] == "Bearer wise-token"
    assert "source=USD" in seen["url"] and "target=NZD" in seen["url"]
    await provider.aclose()


async def test_wise_history_is_sorted_and_skips_unusable_entries() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"rate": 1.76, "time": "2026-07-02T00:00:00+0000"},
                {"rate": 1.75, "time": "2026-07-01T00:00:00+0000"},
                {"time": "2026-07-03T00:00:00+0000"},
                {"rate": 1.77, "time": "nonsense"},
            ],
        )

    provider = WiseProvider(wise_settings(), api_token="t")
    install(provider, handler)
    points = await provider.get_historical_rates(
        "USD", "NZD", datetime(2026, 7, 1, tzinfo=UTC), datetime(2026, 7, 3, tzinfo=UTC), "day"
    )
    assert [format(point.rate, "f") for point in points] == ["1.75000000", "1.76000000"]
    await provider.aclose()


async def test_wise_without_a_token_refuses_rather_than_calling() -> None:
    provider = WiseProvider(wise_settings(), api_token=None)
    with pytest.raises(ProviderConfigurationError, match="No Wise API token"):
        await provider.get_spot_rate("USD", "NZD")
    health = await provider.health_check()
    assert health.healthy is False
    assert "token" in health.message


async def test_wise_quote_is_never_marked_executable_when_unauthenticated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v3/quotes"
        return httpx.Response(
            200,
            json={
                "id": "quote-1",
                "rate": 1.7550,
                "sourceAmount": 120000,
                "sourceCurrency": "USD",
                "targetCurrency": "NZD",
                "expirationTime": "2026-08-01T09:30:00Z",
                "paymentOptions": [
                    {
                        "payIn": "BALANCE",
                        "targetAmount": 210_060.0,
                        "fee": {"total": 540.25},
                        "disabled": False,
                    }
                ],
            },
        )

    provider = WiseProvider(wise_settings(profile_id=""), api_token="t")
    install(provider, handler)
    quote = await provider.create_quote("USD", "NZD", Decimal("120000"))
    assert quote.authenticated is False
    assert quote.fee == Decimal("540.2500")
    assert quote.target_amount == Decimal("210060.0000")
    await provider.aclose()


async def test_wise_authenticated_quote_uses_the_profile_endpoint() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(
            200,
            json={
                "id": "quote-2",
                "rate": 1.76,
                "sourceAmount": 200000,
                "sourceCurrency": "USD",
                "paymentOptions": [
                    {"payIn": "BALANCE", "targetAmount": 351_080.0, "fee": {"total": 920.0}}
                ],
            },
        )

    provider = WiseProvider(wise_settings(), api_token="t")
    install(provider, handler)
    quote = await provider.create_quote("USD", "NZD", Decimal("200000"))
    assert seen["path"] == "/v3/profiles/12345/quotes"
    assert quote.authenticated is True
    await provider.aclose()


async def test_wise_balances_and_conversions_are_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "balances" in request.url.path:
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 777,
                        "currency": "USD",
                        "type": "STANDARD",
                        "amount": {"value": 680000.5, "currency": "USD"},
                        "reservedAmount": {"value": 0, "currency": "USD"},
                    }
                ],
            )
        return httpx.Response(
            200,
            json={
                "transactions": [
                    {
                        "referenceNumber": "CONV-1",
                        "date": "2026-09-15T10:30:00Z",
                        "details": {"type": "CONVERSION", "description": "Auto conversion"},
                        "exchangeDetails": {
                            "fromAmount": {"value": 120000, "currency": "USD"},
                            "toAmount": {"value": 207840, "currency": "NZD"},
                            "rate": 1.732,
                        },
                        "totalFees": {"value": 520, "currency": "NZD"},
                    },
                    {"details": {"type": "DEPOSIT"}},
                ]
            },
        )

    provider = WiseProvider(wise_settings(), api_token="t")
    install(provider, handler)

    balances = await provider.get_balances()
    assert balances[0].amount == Decimal("680000.5000")

    conversions = await provider.get_conversions(
        "777", datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 30, tzinfo=UTC)
    )
    assert len(conversions) == 1
    assert conversions[0].source_amount == Decimal("120000.0000")
    assert conversions[0].target_amount == Decimal("207840.0000")
    assert conversions[0].fee == Decimal("520.0000")
    assert conversions[0].executed_at == datetime(2026, 9, 15, 10, 30, tzinfo=UTC)
    await provider.aclose()


async def test_transport_failure_becomes_a_provider_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    provider = GenericProvider(generic_settings(), api_key="k")
    install(provider, handler)
    with pytest.raises(ProviderUnavailableError, match="could not be reached"):
        await provider.get_spot_rate("USD", "NZD")
    await provider.aclose()


async def test_retryable_status_is_retried_then_reported() -> None:
    calls = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(503, text="upstream down")

    provider = GenericProvider(generic_settings(), api_key="k")
    install(provider, handler)
    with pytest.raises(ProviderUnavailableError):
        await provider.request_json("GET", "/latest", attempts=2)
    assert calls["count"] == 2
    await provider.aclose()


async def test_a_rate_expiry_in_the_past_is_not_executable() -> None:
    from app.providers.base import RateQuote

    quote = RateQuote(
        provider="wise",
        source_currency="USD",
        target_currency="NZD",
        rate=Decimal("1.76"),
        quote_type=QuoteType.GUARANTEED_QUOTE,
        expires_at=datetime.now(UTC) - timedelta(minutes=1),
        metadata={"authenticated": True},
    )
    assert quote.is_executable is False
