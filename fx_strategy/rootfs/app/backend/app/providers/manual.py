"""Manual and simulated rate sources.

The manual provider is mandatory: it makes the whole application testable with
no internet access, and it is the fallback when every configured API is down.
It reads what the user last entered — it never invents a number.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.database import utcnow
from app.logging_setup import get_logger
from app.providers.base import (
    ProviderHealth,
    ProviderUnavailableError,
    QuoteType,
    RatePoint,
    RateQuote,
    validate_interval,
    validate_pair,
)

log = get_logger(__name__)


class ManualProvider:
    """Serves a rate the user typed in, or a simulated one.

    History comes from whatever was imported from CSV, which the rate service
    reads directly from the database; this provider does not fabricate a series.
    """

    name = "manual"
    display_name = "Manual entry"
    supports_history = False

    def __init__(
        self,
        *,
        rate: Decimal | None = None,
        entered_at: datetime | None = None,
        simulated: bool = False,
    ) -> None:
        self._rate = rate
        self._entered_at = entered_at
        self._simulated = simulated
        if simulated:
            self.name = "simulation"
            self.display_name = "Simulation"

    async def get_spot_rate(self, source_currency: str, target_currency: str) -> RateQuote:
        source, target = validate_pair(source_currency, target_currency, provider=self.name)
        if self._rate is None:
            raise ProviderUnavailableError(
                self.name,
                "No manual rate has been entered yet.",
                retryable=False,
            )
        return RateQuote(
            provider=self.name,
            source_currency=source,
            target_currency=target,
            rate=self._rate,
            quote_type=QuoteType.SIMULATED if self._simulated else QuoteType.MANUAL,
            provider_timestamp=self._entered_at,
            retrieved_at=utcnow(),
            raw_reference=None,
            latency_ms=0,
            metadata={"entered_by": "user", "simulated": self._simulated},
        )

    async def get_historical_rates(
        self,
        source_currency: str,
        target_currency: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> list[RatePoint]:
        validate_pair(source_currency, target_currency, provider=self.name)
        validate_interval(interval, provider=self.name)
        # Historical data for this provider is whatever was imported from CSV.
        # It lives in the database, so there is nothing to fetch here.
        return []

    async def health_check(self) -> ProviderHealth:
        return ProviderHealth(
            name=self.name,
            healthy=self._rate is not None,
            message=(
                "A manual rate is available."
                if self._rate is not None
                else "No manual rate has been entered."
            ),
            latency_ms=0,
            details={"simulated": self._simulated},
        )

    async def aclose(self) -> None:
        return None
