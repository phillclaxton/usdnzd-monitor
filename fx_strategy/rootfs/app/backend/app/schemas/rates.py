"""Rate request and response models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, field_validator

from app.money import ALLOWED_CURRENCIES
from app.schemas.common import RateStr, Schema, StrictSchema


class RateSampleOut(Schema):
    id: int
    provider: str
    source_currency: str
    target_currency: str
    rate: RateStr
    quote_type: str
    provider_timestamp: datetime | None
    retrieved_at: datetime
    expires_at: datetime | None
    is_stale: bool
    latency_ms: int | None


class RateChanges(Schema):
    one_hour: RateStr | None = None
    twenty_four_hours: RateStr | None = None
    seven_days: RateStr | None = None
    thirty_days: RateStr | None = None


class CurrentRateOut(Schema):
    """The dashboard header payload."""

    source_currency: str
    target_currency: str
    rate: RateStr | None
    #: `live`, `delayed`, `stale` or `unavailable` — never conflated.
    status: Literal["live", "delayed", "stale", "unavailable"]
    provider: str
    quote_type: str | None
    quote_label: str | None
    provider_timestamp: datetime | None
    retrieved_at: datetime | None
    age_seconds: int | None
    stale_after_seconds: int
    changes: RateChanges
    high_24h: RateStr | None
    low_24h: RateStr | None
    high_6m: RateStr | None
    low_6m: RateStr | None
    disagreement_warning: str | None = None
    #: Present when the app has no rate at all yet.
    message: str | None = None


class RatePointOut(Schema):
    timestamp: datetime
    rate: RateStr
    provider: str


class RateHistoryOut(Schema):
    source_currency: str
    target_currency: str
    start: datetime
    end: datetime
    resolution: Literal["sample", "hour", "day"]
    points: list[RatePointOut]
    high: RateStr | None
    low: RateStr | None
    average: RateStr | None
    truncated: bool = False


class ManualRateIn(StrictSchema):
    rate: RateStr
    note: str = Field(default="", max_length=500)
    simulated: bool = False
    source_currency: str | None = None
    target_currency: str | None = None

    @field_validator("source_currency", "target_currency")
    @classmethod
    def _known_currency(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.strip().upper()
        if code not in ALLOWED_CURRENCIES:
            raise ValueError(f"{value!r} is not a supported currency code")
        return code

    @field_validator("rate")
    @classmethod
    def _positive(cls, value: Any) -> Any:
        if value is not None and value <= 0:
            raise ValueError("rate must be greater than zero")
        return value


class RefreshOut(Schema):
    succeeded: bool
    provider: str
    attempted: list[str]
    errors: dict[str, str]
    rate: RateStr | None
    disagreement: RateStr | None = None
    disagreement_exceeded: bool = False
    comparison: dict[str, str] = Field(default_factory=dict)


class ProviderStatusOut(Schema):
    provider: str
    display_name: str = ""
    configured: bool = True
    healthy: bool
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    last_latency_ms: int | None = None
    retry_after: datetime | None = None
    reason: str = ""


class RateImportPreview(Schema):
    total_rows: int
    accepted: int
    rejected: int
    errors: list[dict[str, Any]]
    sample: list[RatePointOut]
    imported: int = 0
    committed: bool = False
