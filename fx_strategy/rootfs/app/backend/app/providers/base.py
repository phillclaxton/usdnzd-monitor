"""The exchange-rate provider interface.

Nothing outside ``app.providers`` may depend on a particular vendor.  The rest
of the application talks to :class:`FxRateProvider` and receives
:class:`RateQuote` objects that are already normalised to *target currency units
per one source currency unit*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.database import utcnow
from app.money import invert_rate, quantize_rate, require_currency


class QuoteType(StrEnum):
    """What a rate actually represents.

    The distinction matters: a mid-market reference rate is not what a user
    receives, and a quote that was never authenticated is not executable.
    """

    MID_MARKET = "mid_market"
    PROVIDER_DISPLAYED = "provider_displayed"
    GUARANTEED_QUOTE = "guaranteed_quote"
    MANUAL = "manual"
    SIMULATED = "simulated"


#: Human-readable descriptions surfaced in the UI next to every rate.
QUOTE_TYPE_LABEL: dict[QuoteType, str] = {
    QuoteType.MID_MARKET: "Mid-market reference rate",
    QuoteType.PROVIDER_DISPLAYED: "Rate displayed by the provider",
    QuoteType.GUARANTEED_QUOTE: "Guaranteed quote (expires)",
    QuoteType.MANUAL: "Manually entered rate",
    QuoteType.SIMULATED: "Simulated rate",
}


class ProviderError(Exception):
    """Base class for provider failures. Never swallowed, always surfaced."""

    def __init__(self, provider: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.provider = provider
        self.message = message
        self.retryable = retryable


class ProviderUnavailableError(ProviderError):
    """The upstream service could not be reached or returned an error."""


class ProviderConfigurationError(ProviderError):
    """The provider is not configured well enough to be used."""

    def __init__(self, provider: str, message: str) -> None:
        super().__init__(provider, message, retryable=False)


class ProviderResponseError(ProviderError):
    """The upstream responded, but not with something we can read as a rate."""


@dataclass(frozen=True, slots=True)
class RateQuote:
    """A single observation of an exchange rate."""

    provider: str
    source_currency: str
    target_currency: str
    #: Always target units per one source unit, e.g. NZD per USD.
    rate: Decimal
    quote_type: QuoteType
    #: The timestamp the provider itself reported, when it supplies one.
    provider_timestamp: datetime | None = None
    retrieved_at: datetime = field(default_factory=utcnow)
    expires_at: datetime | None = None
    raw_reference: str | None = None
    latency_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def quote_label(self) -> str:
        return QUOTE_TYPE_LABEL[self.quote_type]

    @property
    def is_executable(self) -> bool:
        """Only an authenticated, unexpired guaranteed quote is executable.

        Even then this application will not act on it; the flag exists so the UI
        never labels a reference rate as something the user can transact at.
        """
        if self.quote_type is not QuoteType.GUARANTEED_QUOTE:
            return False
        if self.expires_at is None:
            return False
        return self.expires_at > utcnow() and bool(self.metadata.get("authenticated"))


@dataclass(frozen=True, slots=True)
class RatePoint:
    """One point on a historical series."""

    timestamp: datetime
    rate: Decimal
    provider: str


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    name: str
    healthy: bool
    message: str = ""
    latency_ms: int | None = None
    checked_at: datetime = field(default_factory=utcnow)
    #: Extra context for the diagnostics page. Must never contain a credential.
    details: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class FxRateProvider(Protocol):
    """The contract every rate source implements."""

    name: str
    display_name: str
    supports_history: bool
    #: False when the provider exists but has nothing to work with yet — no
    #: credential, no manual rate. That is not a failure, and must not be
    #: reported as one.
    configured: bool

    async def get_spot_rate(
        self,
        source_currency: str,
        target_currency: str,
    ) -> RateQuote: ...

    async def get_historical_rates(
        self,
        source_currency: str,
        target_currency: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> list[RatePoint]: ...

    async def health_check(self) -> ProviderHealth: ...

    async def aclose(self) -> None: ...


def normalize_rate(
    raw_rate: Decimal | str | float | int,
    *,
    inverted: bool,
    provider: str,
) -> Decimal:
    """Bring a provider's number into the canonical convention.

    ``inverted`` means the provider quoted *source per target* (USD per NZD)
    rather than the convention this application stores (NZD per USD).
    """
    try:
        rate = quantize_rate(raw_rate, field=f"{provider} rate")
    except Exception as exc:
        raise ProviderResponseError(provider, f"unreadable rate value {raw_rate!r}") from exc
    if rate <= 0:
        raise ProviderResponseError(provider, f"non-positive rate {rate} from {provider}")
    return invert_rate(rate) if inverted else rate


def validate_pair(source: str, target: str, *, provider: str) -> tuple[str, str]:
    """Validate a currency pair against the allow-list."""
    try:
        return require_currency(source, field="source_currency"), require_currency(
            target, field="target_currency"
        )
    except Exception as exc:
        raise ProviderConfigurationError(provider, str(exc)) from exc


#: Valid ``interval`` values for :meth:`FxRateProvider.get_historical_rates`.
INTERVALS: tuple[str, ...] = ("minute", "hour", "day")


def validate_interval(interval: str, *, provider: str) -> str:
    if interval not in INTERVALS:
        raise ProviderConfigurationError(
            provider, f"interval must be one of {', '.join(INTERVALS)}, got {interval!r}"
        )
    return interval
