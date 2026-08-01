"""User-editable settings.

Each section is persisted as one row in ``app_settings``.  Defaults here are the
defaults described in the product specification, so a fresh install behaves
sensibly before the user opens the settings page.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.money import ALLOWED_CURRENCIES
from app.schemas.common import DecimalStr, RateStr

QuoteConvention = Literal["target_per_source", "source_per_target"]


class Section(BaseModel):
    """Base settings section: unknown keys are dropped rather than fatal.

    Dropping unknown keys keeps a downgrade from wedging the app on a settings
    row written by a newer version.  Defaults are validated so that a Decimal
    default is quantized exactly like a user-supplied one.
    """

    model_config = ConfigDict(extra="ignore", validate_assignment=True, validate_default=True)


class GeneralSettings(Section):
    timezone: str = "Pacific/Auckland"
    source_currency: str = "USD"
    target_currency: str = "NZD"
    rate_convention: QuoteConvention = "target_per_source"
    setup_complete: bool = False
    active_strategy_id: int | None = None

    @field_validator("source_currency", "target_currency")
    @classmethod
    def _known_currency(cls, value: str) -> str:
        code = value.strip().upper()
        if code not in ALLOWED_CURRENCIES:
            raise ValueError(f"{value!r} is not a supported currency code")
        return code


class FormattingSettings(Section):
    currency_decimal_places: int = Field(default=2, ge=0, le=4)
    rate_decimal_places: int = Field(default=4, ge=2, le=8)
    thousands_separator: bool = True
    locale: str = "en-NZ"


class GenericProviderSettings(Section):
    """Configuration for the vendor-neutral HTTP rate provider."""

    enabled: bool = False
    display_name: str = "Generic API provider"
    base_url: str = ""
    rate_path: str = "/latest"
    history_path: str = ""
    auth_style: Literal["header", "query", "bearer", "none"] = "header"
    auth_name: str = "apikey"
    source_param: str = "base"
    target_param: str = "symbols"
    #: Dotted path into the JSON response, ``{target}`` expands to the currency.
    rate_json_path: str = "rates.{target}"
    timestamp_json_path: str = "timestamp"
    #: Set when the provider quotes source-per-target instead.
    convention: QuoteConvention = "target_per_source"
    provider_timezone: str = "UTC"
    min_seconds_between_calls: int = Field(default=60, ge=1)
    timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    preset: str = ""


class WiseProviderSettings(Section):
    """Non-secret half of the Wise configuration. Tokens live in the secret store."""

    enabled: bool = False
    environment: Literal["live", "sandbox"] = "live"
    profile_id: str = ""
    source_balance_id: str = ""
    target_balance_id: str = ""
    read_only: bool = True
    timeout_seconds: float = Field(default=20.0, gt=0, le=120)


class ProviderSettings(Section):
    primary: str = "manual"
    secondary: str | None = None
    manual_fallback: bool = True
    poll_seconds_active: int = Field(default=300, ge=60)
    poll_seconds_idle: int = Field(default=900, ge=60)
    #: The floor the UI enforces; the spec allows a one-minute minimum.
    poll_seconds_minimum: int = Field(default=60, ge=60)
    jitter_seconds: int = Field(default=20, ge=0, le=300)
    stale_after_seconds: int = Field(default=900, ge=60)
    max_backoff_seconds: int = Field(default=3600, ge=60)
    error_notify_after_seconds: int = Field(default=1800, ge=60)
    disagreement_threshold: DecimalStr = Decimal("0.0030")
    disagreement_is_relative: bool = True
    market_active_weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    store_raw_payloads: bool = False
    generic: GenericProviderSettings = Field(default_factory=GenericProviderSettings)
    wise: WiseProviderSettings = Field(default_factory=WiseProviderSettings)


class QuietHours(Section):
    enabled: bool = False
    start: str = "22:00"
    end: str = "07:00"
    #: Critical alerts (deadline missed, provider down) ignore quiet hours.
    allow_critical: bool = True


class NotificationSettings(Section):
    enabled: bool = True
    services: list[str] = Field(default_factory=lambda: ["notify.persistent_notification"])
    default_cooldown_minutes: int = Field(default=60, ge=0)
    near_threshold: RateStr = Decimal("0.0050")
    reset_hysteresis: RateStr = Decimal("0.0050")
    confirmation_samples: int = Field(default=2, ge=1, le=10)
    confirmation_min_seconds: int = Field(default=30, ge=0)
    repeat_interval_minutes: int = Field(default=0, ge=0)
    quiet_hours: QuietHours = Field(default_factory=QuietHours)
    reversal_threshold: RateStr = Decimal("0.0200")
    deadline_warning_days: list[int] = Field(default_factory=lambda: [30, 14, 7, 3, 1])


class HomeAssistantSettings(Section):
    publish_entities: bool = True
    mqtt_discovery_prefix: str = "homeassistant"
    device_name: str = "FX Strategy Manager"
    node_id: str = "fx_strategy"
    expose_writable_controls: bool = True
    publish_interval_seconds: int = Field(default=60, ge=10)


class RetentionSettings(Section):
    fine_rate_days: int = Field(default=365, ge=7)
    hourly_aggregate_days: int = Field(default=1826, ge=30)
    keep_daily_aggregates_forever: bool = True
    log_days: int = Field(default=30, ge=1)
    store_raw_payloads: bool = False


class SimulationSettings(Section):
    enabled: bool = False
    simulated_rate: RateStr | None = None
    #: Multiplier applied to simulated clock advancement during replay.
    time_acceleration: int = Field(default=1, ge=1, le=10000)
    force_provider_error: bool = False
    force_disagreement: bool = False
    replay_cursor: int = 0


class Settings(BaseModel):
    """The complete settings document."""

    model_config = ConfigDict(validate_assignment=True)

    general: GeneralSettings = Field(default_factory=GeneralSettings)
    formatting: FormattingSettings = Field(default_factory=FormattingSettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)
    home_assistant: HomeAssistantSettings = Field(default_factory=HomeAssistantSettings)
    retention: RetentionSettings = Field(default_factory=RetentionSettings)
    simulation: SimulationSettings = Field(default_factory=SimulationSettings)


class SettingsUpdate(BaseModel):
    """Partial settings update: only the supplied sections are replaced."""

    model_config = ConfigDict(extra="forbid")

    general: GeneralSettings | None = None
    formatting: FormattingSettings | None = None
    providers: ProviderSettings | None = None
    notifications: NotificationSettings | None = None
    home_assistant: HomeAssistantSettings | None = None
    retention: RetentionSettings | None = None
    simulation: SimulationSettings | None = None
