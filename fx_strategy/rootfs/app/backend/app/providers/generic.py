"""A vendor-neutral HTTP rate provider.

Everything about the request and the response shape is configuration, so the
application is never tied to one commercial vendor.  Presets for several known
providers are included; each preset is just a set of defaults for the same
configuration fields, and can be edited afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.database import utcnow
from app.logging_setup import get_logger
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
from app.providers.http import HttpProviderMixin, json_path
from app.schemas.settings import GenericProviderSettings

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    """Defaults for a known vendor, applied to the generic configuration."""

    key: str
    display_name: str
    base_url: str
    rate_path: str
    history_path: str
    auth_style: str
    auth_name: str
    source_param: str
    target_param: str
    rate_json_path: str
    timestamp_json_path: str
    notes: str
    requires_key: bool = True
    min_seconds_between_calls: int = 60


#: Response shapes taken from each vendor's published API reference. They are
#: covered by tests using recorded payloads; CI makes no live calls.
PRESETS: dict[str, ProviderPreset] = {
    "frankfurter": ProviderPreset(
        key="frankfurter",
        display_name="Frankfurter (ECB reference rates)",
        base_url="https://api.frankfurter.app",
        rate_path="/latest",
        history_path="/{start}..{end}",
        auth_style="none",
        auth_name="",
        source_param="from",
        target_param="to",
        rate_json_path="rates.{target}",
        timestamp_json_path="date",
        requires_key=False,
        notes=(
            "Free, no key, but publishes one European Central Bank reference rate "
            "per working day. Good for a long historical backfill, too coarse to "
            "drive target alerts on its own."
        ),
        min_seconds_between_calls=300,
    ),
    "exchangerate_host": ProviderPreset(
        key="exchangerate_host",
        display_name="exchangerate.host",
        base_url="https://api.exchangerate.host",
        rate_path="/live",
        history_path="/timeframe",
        auth_style="query",
        auth_name="access_key",
        source_param="source",
        target_param="currencies",
        rate_json_path="quotes.{source}{target}",
        timestamp_json_path="timestamp",
        notes="Key required. Quotes are keyed by the concatenated pair, e.g. USDNZD.",
    ),
    "openexchangerates": ProviderPreset(
        key="openexchangerates",
        display_name="Open Exchange Rates",
        base_url="https://openexchangerates.org/api",
        rate_path="/latest.json",
        history_path="/historical/{date}.json",
        auth_style="query",
        auth_name="app_id",
        source_param="base",
        target_param="symbols",
        rate_json_path="rates.{target}",
        timestamp_json_path="timestamp",
        notes=(
            "The free plan only supports a USD base. For USD to NZD that is "
            "exactly what is needed; other pairs need a paid plan."
        ),
    ),
    "apilayer_exchangerates": ProviderPreset(
        key="apilayer_exchangerates",
        display_name="apilayer Exchange Rates Data",
        base_url="https://api.apilayer.com/exchangerates_data",
        rate_path="/latest",
        history_path="/timeseries",
        auth_style="header",
        auth_name="apikey",
        source_param="base",
        target_param="symbols",
        rate_json_path="rates.{target}",
        timestamp_json_path="timestamp",
        notes="Key is sent in an `apikey` header rather than a query parameter.",
    ),
    "twelvedata": ProviderPreset(
        key="twelvedata",
        display_name="Twelve Data",
        base_url="https://api.twelvedata.com",
        rate_path="/exchange_rate",
        history_path="/time_series",
        auth_style="query",
        auth_name="apikey",
        source_param="symbol",
        target_param="",
        rate_json_path="rate",
        timestamp_json_path="timestamp",
        notes="Uses a single `symbol` parameter in the form USD/NZD.",
    ),
}


def apply_preset(settings: GenericProviderSettings, key: str) -> GenericProviderSettings:
    """Return a copy of ``settings`` with a preset's defaults applied."""
    preset = PRESETS.get(key)
    if preset is None:
        raise ProviderConfigurationError("generic", f"unknown provider preset {key!r}")
    return settings.model_copy(
        update={
            "preset": preset.key,
            "display_name": preset.display_name,
            "base_url": preset.base_url,
            "rate_path": preset.rate_path,
            "history_path": preset.history_path,
            "auth_style": preset.auth_style,
            "auth_name": preset.auth_name,
            "source_param": preset.source_param,
            "target_param": preset.target_param,
            "rate_json_path": preset.rate_json_path,
            "timestamp_json_path": preset.timestamp_json_path,
            "min_seconds_between_calls": preset.min_seconds_between_calls,
        }
    )


def parse_provider_timestamp(value: Any) -> datetime | None:
    """Interpret whatever a provider calls a timestamp.

    Handles epoch seconds and milliseconds, ISO-8601 with or without a zone, and
    plain dates. Returns ``None`` rather than guessing when the value makes no
    sense — an absent provider timestamp is honest; a fabricated one is not.
    """
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        seconds = float(value)
        # Values past ~2001 in milliseconds are far beyond any plausible epoch
        # second, so this discriminates the two unambiguously.
        if seconds > 1e11:
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip()
    if text.isdigit():
        return parse_provider_timestamp(int(text))
    normalized = text.replace("Z", "+00:00")
    # Wise returns offsets without a colon, e.g. +0000.
    if len(normalized) > 5 and (normalized[-5] in "+-") and normalized[-5:].isascii():
        tail = normalized[-5:]
        if tail[1:].isdigit():
            normalized = f"{normalized[:-5]}{tail[:3]}:{tail[3:]}"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class GenericProvider(HttpProviderMixin):
    """Configurable JSON rate provider."""

    supports_history = True
    #: Built only when usable; the registry refuses otherwise.
    configured = True

    def __init__(self, settings: GenericProviderSettings, api_key: str | None) -> None:
        self.name = "generic"
        self.display_name = settings.display_name or "Generic API provider"
        self._settings = settings
        self._api_key = api_key or ""
        if not settings.base_url:
            raise ProviderConfigurationError(
                self.name, "The generic provider needs a base URL before it can be used."
            )
        if settings.auth_style != "none" and not self._api_key:
            raise ProviderConfigurationError(
                self.name,
                "The generic provider needs an API key. Add it under Settings → Rate providers.",
            )
        self.supports_history = bool(settings.history_path)
        super().__init__(timeout=settings.timeout_seconds, base_url=settings.base_url)

    # -- request assembly -------------------------------------------------

    def _auth(self) -> tuple[dict[str, str], dict[str, str]]:
        """Return ``(headers, params)`` carrying the credential."""
        style = self._settings.auth_style
        if style == "none" or not self._api_key:
            return {}, {}
        if style == "header":
            return {self._settings.auth_name or "apikey": self._api_key}, {}
        if style == "bearer":
            return {"Authorization": f"Bearer {self._api_key}"}, {}
        return {}, {self._settings.auth_name or "apikey": self._api_key}

    def _pair_params(self, source: str, target: str) -> dict[str, str]:
        settings = self._settings
        if not settings.target_param:
            # Single-parameter providers such as Twelve Data use `symbol=USD/NZD`.
            return {settings.source_param: f"{source}/{target}"}
        return {settings.source_param: source, settings.target_param: target}

    # -- provider interface -----------------------------------------------

    async def get_spot_rate(self, source_currency: str, target_currency: str) -> RateQuote:
        source, target = validate_pair(source_currency, target_currency, provider=self.name)
        headers, auth_params = self._auth()
        params = {**self._pair_params(source, target), **auth_params}

        document, latency_ms = await self.request_json(
            "GET", self._settings.rate_path, params=params, headers=headers
        )
        raw_rate = json_path(
            document,
            self._settings.rate_json_path.replace("{source}", source),
            provider=self.display_name,
            target=target,
        )
        inverted = self._settings.convention == "source_per_target"
        rate = normalize_rate(raw_rate, inverted=inverted, provider=self.display_name)

        provider_timestamp = None
        if self._settings.timestamp_json_path:
            try:
                provider_timestamp = parse_provider_timestamp(
                    json_path(
                        document,
                        self._settings.timestamp_json_path,
                        provider=self.display_name,
                        target=target,
                    )
                )
            except ProviderResponseError:
                # A missing timestamp is not fatal; it is recorded as absent so
                # the UI can say the provider did not supply one.
                log.debug("provider_timestamp_missing", provider=self.display_name)

        return RateQuote(
            provider=self.name,
            source_currency=source,
            target_currency=target,
            rate=rate,
            quote_type=QuoteType.MID_MARKET,
            provider_timestamp=provider_timestamp,
            retrieved_at=utcnow(),
            latency_ms=latency_ms,
            metadata={
                "display_name": self.display_name,
                "preset": self._settings.preset,
                "inverted": inverted,
            },
        )

    async def get_historical_rates(
        self,
        source_currency: str,
        target_currency: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> list[RatePoint]:
        source, target = validate_pair(source_currency, target_currency, provider=self.name)
        validate_interval(interval, provider=self.name)
        if not self._settings.history_path:
            return []

        headers, auth_params = self._auth()
        path = self._settings.history_path.format(
            start=start.date().isoformat(),
            end=end.date().isoformat(),
            date=start.date().isoformat(),
        )
        params = {
            **self._pair_params(source, target),
            **auth_params,
            "start_date": start.date().isoformat(),
            "end_date": end.date().isoformat(),
        }
        document, _ = await self.request_json("GET", path, params=params, headers=headers)
        return self._parse_history(document, source, target)

    def _parse_history(self, document: Any, source: str, target: str) -> list[RatePoint]:
        """Read a date-keyed history document.

        Providers differ, but the shape is almost always
        ``{"rates": {"2026-07-01": {"NZD": 1.75}, ...}}``. Anything that cannot
        be read is skipped rather than guessed at, and the count of skipped
        entries is logged.
        """
        container = document.get("rates") if isinstance(document, dict) else None
        if not isinstance(container, dict):
            raise ProviderResponseError(
                self.display_name, "the history response has no date-keyed 'rates' object"
            )
        inverted = self._settings.convention == "source_per_target"
        points: list[RatePoint] = []
        skipped = 0
        for key, value in sorted(container.items()):
            timestamp = parse_provider_timestamp(key)
            if timestamp is None:
                skipped += 1
                continue
            raw = value.get(target) if isinstance(value, dict) else value
            if raw is None and isinstance(value, dict):
                raw = value.get(f"{source}{target}")
            if raw is None:
                skipped += 1
                continue
            try:
                points.append(
                    RatePoint(
                        timestamp=timestamp,
                        rate=normalize_rate(raw, inverted=inverted, provider=self.display_name),
                        provider=self.name,
                    )
                )
            except ProviderResponseError:
                skipped += 1
        if skipped:
            log.warning("history_entries_skipped", provider=self.display_name, skipped=skipped)
        return points

    async def health_check(self) -> ProviderHealth:
        try:
            quote = await self.get_spot_rate("USD", "NZD")
        except Exception as exc:
            message = getattr(exc, "message", str(exc))
            return ProviderHealth(
                name=self.name,
                healthy=False,
                message=message,
                details={"display_name": self.display_name},
            )
        return ProviderHealth(
            name=self.name,
            healthy=True,
            message=f"Returned a rate of {quote.rate}.",
            latency_ms=quote.latency_ms,
            details={"display_name": self.display_name, "preset": self._settings.preset},
        )
