"""Wise provider.

Covers the read-only surface: rates, historical rates, quotes for fee
estimation, balances and completed conversions.  There is deliberately no
method here that moves money — see :mod:`app.services.execution` for the
interface a future, separately enabled module would have to implement.

Endpoints and authentication were checked against the current Wise API
reference; see ``docs/upstream-notes.md`` for what differed from the original
specification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from app.database import utcnow
from app.logging_setup import get_logger
from app.money import quantize_money, quantize_rate
from app.providers.base import (
    ProviderConfigurationError,
    ProviderHealth,
    ProviderResponseError,
    QuoteType,
    RatePoint,
    RateQuote,
    normalize_rate,
    validate_interval,
    validate_pair,
)
from app.providers.generic import parse_provider_timestamp
from app.providers.http import HttpProviderMixin
from app.schemas.settings import WiseProviderSettings

log = get_logger(__name__)

LIVE_BASE_URL = "https://api.transferwise.com"
SANDBOX_BASE_URL = "https://api.wise-sandbox.com"

#: Maps this application's interval names onto Wise's `group` parameter.
GROUP_FOR_INTERVAL = {"minute": "minute", "hour": "hour", "day": "day"}


@dataclass(frozen=True, slots=True)
class WiseBalance:
    balance_id: str
    currency: str
    amount: Decimal
    reserved: Decimal
    type: str


@dataclass(frozen=True, slots=True)
class WiseConversion:
    """A completed currency conversion read back from Wise."""

    reference: str
    source_currency: str
    target_currency: str
    source_amount: Decimal
    target_amount: Decimal
    rate: Decimal | None
    fee: Decimal | None
    fee_currency: str
    executed_at: datetime | None
    description: str


@dataclass(frozen=True, slots=True)
class WiseQuote:
    """A fee/rate estimate. Never described as executable by this application."""

    rate: Decimal
    source_amount: Decimal
    target_amount: Decimal
    fee: Decimal
    fee_currency: str
    expires_at: datetime | None
    authenticated: bool
    raw_reference: str | None


class WiseProvider(HttpProviderMixin):
    """Read-only Wise client."""

    name = "wise"
    display_name = "Wise"
    supports_history = True

    def __init__(self, settings: WiseProviderSettings, api_token: str | None) -> None:
        self._settings = settings
        self._token = api_token or ""
        base = SANDBOX_BASE_URL if settings.environment == "sandbox" else LIVE_BASE_URL
        super().__init__(timeout=settings.timeout_seconds, base_url=base)

    @property
    def has_credentials(self) -> bool:
        return bool(self._token)

    @property
    def profile_id(self) -> str:
        return self._settings.profile_id

    def _headers(self) -> dict[str, str]:
        """Bearer authentication, as used by Wise personal API tokens.

        Affiliate integrations authenticate with Basic client credentials
        instead; when that is the account type, the connection test reports the
        rejection rather than silently returning nothing.
        """
        if not self._token:
            raise ProviderConfigurationError(
                self.name,
                "No Wise API token is configured. Add one under Settings → Wise.",
            )
        return {"Authorization": f"Bearer {self._token}"}

    # -- rates -------------------------------------------------------------

    async def get_spot_rate(self, source_currency: str, target_currency: str) -> RateQuote:
        source, target = validate_pair(source_currency, target_currency, provider=self.name)
        document, latency_ms = await self.request_json(
            "GET",
            "/v1/rates",
            params={"source": source, "target": target},
            headers=self._headers(),
        )
        entry = self._first_rate_entry(document)
        rate = normalize_rate(entry["rate"], inverted=False, provider=self.name)
        return RateQuote(
            provider=self.name,
            source_currency=source,
            target_currency=target,
            rate=rate,
            # Wise's /v1/rates is the mid-market reference rate, not the rate a
            # transfer settles at. Labelling it correctly is the whole point of
            # the quote_type field.
            quote_type=QuoteType.MID_MARKET,
            provider_timestamp=parse_provider_timestamp(entry.get("time")),
            retrieved_at=utcnow(),
            latency_ms=latency_ms,
            metadata={"environment": self._settings.environment},
        )

    def _first_rate_entry(self, document: Any) -> dict[str, Any]:
        if isinstance(document, list):
            if not document:
                raise ProviderResponseError(self.name, "Wise returned an empty rate list.")
            entry = document[0]
        elif isinstance(document, dict):
            entry = document
        else:
            raise ProviderResponseError(self.name, "Wise returned an unexpected rate payload.")
        if not isinstance(entry, dict) or "rate" not in entry:
            raise ProviderResponseError(self.name, "Wise rate payload has no 'rate' field.")
        return entry

    async def get_historical_rates(
        self,
        source_currency: str,
        target_currency: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> list[RatePoint]:
        source, target = validate_pair(source_currency, target_currency, provider=self.name)
        group = GROUP_FOR_INTERVAL[validate_interval(interval, provider=self.name)]
        document, _ = await self.request_json(
            "GET",
            "/v1/rates",
            params={
                "source": source,
                "target": target,
                "from": start.isoformat(timespec="seconds"),
                "to": end.isoformat(timespec="seconds"),
                "group": group,
            },
            headers=self._headers(),
        )
        if not isinstance(document, list):
            raise ProviderResponseError(self.name, "Wise history payload is not a list.")
        points: list[RatePoint] = []
        for entry in document:
            if not isinstance(entry, dict) or "rate" not in entry:
                continue
            timestamp = parse_provider_timestamp(entry.get("time"))
            if timestamp is None:
                continue
            points.append(
                RatePoint(
                    timestamp=timestamp,
                    rate=normalize_rate(entry["rate"], inverted=False, provider=self.name),
                    provider=self.name,
                )
            )
        points.sort(key=lambda point: point.timestamp)
        return points

    # -- read-only account access -----------------------------------------

    async def get_profiles(self) -> list[dict[str, Any]]:
        document, _ = await self.request_json("GET", "/v2/profiles", headers=self._headers())
        return document if isinstance(document, list) else []

    async def get_balances(self, profile_id: str | None = None) -> list[WiseBalance]:
        profile = profile_id or self._settings.profile_id
        if not profile:
            raise ProviderConfigurationError(
                self.name, "A Wise profile ID is required to read balances."
            )
        document, _ = await self.request_json(
            "GET",
            f"/v4/profiles/{profile}/balances",
            params={"types": "STANDARD"},
            headers=self._headers(),
        )
        balances: list[WiseBalance] = []
        for entry in document if isinstance(document, list) else []:
            amount = entry.get("amount") or {}
            reserved = entry.get("reservedAmount") or {}
            balances.append(
                WiseBalance(
                    balance_id=str(entry.get("id", "")),
                    currency=str(entry.get("currency", "")),
                    amount=quantize_money(amount.get("value", 0), field="balance"),
                    reserved=quantize_money(reserved.get("value", 0), field="reserved"),
                    type=str(entry.get("type", "STANDARD")),
                )
            )
        return balances

    async def get_conversions(
        self, balance_id: str, start: datetime, end: datetime, profile_id: str | None = None
    ) -> list[WiseConversion]:
        """Read completed conversions from a balance statement.

        Wise reports a conversion as a pair of statement entries; the ones with
        a `CONVERSION` type carry both sides of the exchange.
        """
        profile = profile_id or self._settings.profile_id
        if not profile:
            raise ProviderConfigurationError(
                self.name, "A Wise profile ID is required to read conversions."
            )
        document, _ = await self.request_json(
            "GET",
            f"/v1/profiles/{profile}/balance-statements/{balance_id}/statement.json",
            params={
                "intervalStart": start.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "intervalEnd": end.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                "type": "COMPACT",
            },
            headers=self._headers(),
        )
        transactions = document.get("transactions", []) if isinstance(document, dict) else []
        return [
            conversion
            for conversion in (self._parse_transaction(entry) for entry in transactions)
            if conversion is not None
        ]

    def _parse_transaction(self, entry: Any) -> WiseConversion | None:
        if not isinstance(entry, dict):
            return None
        details = entry.get("details") or {}
        if str(details.get("type", "")).upper() != "CONVERSION":
            return None
        exchange = entry.get("exchangeDetails") or {}
        from_amount = exchange.get("fromAmount") or {}
        to_amount = exchange.get("toAmount") or {}
        fee = entry.get("totalFees") or {}
        rate_value = exchange.get("rate")
        return WiseConversion(
            reference=str(entry.get("referenceNumber") or entry.get("id") or ""),
            source_currency=str(from_amount.get("currency", "")),
            target_currency=str(to_amount.get("currency", "")),
            source_amount=quantize_money(from_amount.get("value", 0), field="source_amount"),
            target_amount=quantize_money(to_amount.get("value", 0), field="target_amount"),
            rate=quantize_rate(rate_value, field="rate") if rate_value is not None else None,
            fee=quantize_money(fee.get("value", 0), field="fee") if fee else None,
            fee_currency=str(fee.get("currency", "")),
            executed_at=parse_provider_timestamp(entry.get("date")),
            description=str(details.get("description", "")),
        )

    async def create_quote(
        self,
        source_currency: str,
        target_currency: str,
        source_amount: Decimal,
        profile_id: str | None = None,
    ) -> WiseQuote:
        """Create a quote so fees can be estimated from real numbers.

        An unauthenticated quote is a marketing estimate, not something that can
        be transacted; the returned object records which kind it was and the UI
        labels it accordingly. This application never acts on either.
        """
        profile = profile_id or self._settings.profile_id
        payload: dict[str, Any] = {
            "sourceCurrency": source_currency,
            "targetCurrency": target_currency,
            "sourceAmount": float(source_amount),
        }
        authenticated = bool(profile and self._token)
        if authenticated:
            path = f"/v3/profiles/{profile}/quotes"
            payload["payOut"] = "BALANCE"
        else:
            path = "/v3/quotes"

        document, _ = await self.request_json(
            "POST", path, json_body=payload, headers=self._headers()
        )
        if not isinstance(document, dict):
            raise ProviderResponseError(self.name, "Wise returned an unexpected quote payload.")

        option = self._preferred_payment_option(document)
        fee_value = option.get("fee", {}).get("total", 0) if option else 0
        target_value = option.get("targetAmount") if option else document.get("targetAmount")
        rate_value = document.get("rate")
        if rate_value is None:
            raise ProviderResponseError(self.name, "Wise quote payload has no rate.")

        return WiseQuote(
            rate=quantize_rate(rate_value, field="quote rate"),
            source_amount=quantize_money(
                document.get("sourceAmount", source_amount), field="source_amount"
            ),
            target_amount=quantize_money(target_value or 0, field="target_amount"),
            fee=quantize_money(fee_value, field="fee"),
            fee_currency=str(document.get("sourceCurrency", source_currency)),
            expires_at=parse_provider_timestamp(document.get("expirationTime")),
            authenticated=authenticated,
            raw_reference=str(document.get("id", "")) or None,
        )

    @staticmethod
    def _preferred_payment_option(document: dict[str, Any]) -> dict[str, Any] | None:
        """Pick the balance-funded option, which is what a conversion uses."""
        options = document.get("paymentOptions")
        if not isinstance(options, list) or not options:
            return None
        for option in options:
            if not isinstance(option, dict) or option.get("disabled"):
                continue
            if option.get("payIn") == "BALANCE":
                return option
        first = options[0]
        return first if isinstance(first, dict) else None

    # -- health -----------------------------------------------------------

    async def health_check(self) -> ProviderHealth:
        if not self._token:
            return ProviderHealth(
                name=self.name,
                healthy=False,
                message="No Wise API token is configured.",
                details={"environment": self._settings.environment, "read_only": True},
            )
        try:
            quote = await self.get_spot_rate("USD", "NZD")
        except Exception as exc:
            return ProviderHealth(
                name=self.name,
                healthy=False,
                message=getattr(exc, "message", str(exc)),
                details={"environment": self._settings.environment},
            )
        return ProviderHealth(
            name=self.name,
            healthy=True,
            message=f"Returned a mid-market rate of {quote.rate}.",
            latency_ms=quote.latency_ms,
            details={
                "environment": self._settings.environment,
                "profile_configured": bool(self._settings.profile_id),
                "read_only": True,
            },
        )
