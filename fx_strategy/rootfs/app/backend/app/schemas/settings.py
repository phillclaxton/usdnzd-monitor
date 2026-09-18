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
    #: Refuse a quote that jumps further than this from the last good rate,
    #: as a proportion (0.02 = 2%). A provider glitch looks exactly like this
    #: and there is no way to tell one from a real move on a single sample.
    implausible_move_enabled: bool = True
    implausible_move_threshold: DecimalStr = Decimal("0.0200")
    #: How many refusals in a row, agreeing with each other, before the new
    #: level is believed. Without this a genuine jump would be refused for ever.
    implausible_move_accept_after: int = Field(default=3, ge=2, le=20)
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
    """How an alert is delivered, once something has decided there is one.

    What *earns* an alert is :class:`FxAlertSettings`. The split is the same one
    the UI shows: this section cannot make the app say more, only decide where a
    message goes and how often.

    ``near_threshold``, ``repeat_interval_minutes``, ``reversal_threshold`` and
    ``deadline_warning_days`` were read only by the conversion ladder and went
    with it in 2.0.0. A stored settings row that still carries them is fine —
    ``Section`` ignores unknown keys, deliberately.
    """

    enabled: bool = True
    services: list[str] = Field(default_factory=lambda: ["notify.persistent_notification"])
    default_cooldown_minutes: int = Field(default=60, ge=0)
    #: How far the rate must fall back through a level before it speaks again.
    reset_hysteresis: RateStr = Decimal("0.0050")
    confirmation_samples: int = Field(default=2, ge=1, le=10)
    confirmation_min_seconds: int = Field(default=30, ge=0)
    quiet_hours: QuietHours = Field(default_factory=QuietHours)


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


class RateZoneSetting(Section):
    """One band of the rate, with a label and guidance.

    These are editable labels for orientation. They are not forecasts, and the
    application never acts on them.
    """

    label: str
    guidance: str = ""
    #: Inclusive lower bound. ``None`` marks the bottom band.
    lower_bound: RateStr | None = None


def _default_zones() -> list[RateZoneSetting]:
    """The example zones from the product specification."""
    return [
        RateZoneSetting(
            label="Unfavourable", guidance="Avoid discretionary conversion.", lower_bound=None
        ),
        RateZoneSetting(
            label="Weak",
            guidance="Convert only amounts required soon.",
            lower_bound=Decimal("1.6800"),
        ),
        RateZoneSetting(
            label="Acceptable",
            guidance="Begin smaller staged conversions.",
            lower_bound=Decimal("1.7000"),
        ),
        RateZoneSetting(
            label="Good",
            guidance="Convert meaningful tranches.",
            lower_bound=Decimal("1.7300"),
        ),
        RateZoneSetting(
            label="Very good",
            guidance="Convert more aggressively.",
            lower_bound=Decimal("1.7600"),
        ),
        RateZoneSetting(
            label="Excellent",
            guidance="Strongly consider completing most or all of the remaining conversion.",
            lower_bound=Decimal("1.7800"),
        ),
    ]


class ZoneSettings(Section):
    enabled: bool = True
    zones: list[RateZoneSetting] = Field(default_factory=_default_zones)


def _default_watch_levels() -> list[Decimal]:
    """The levels from the product specification.

    Round numbers people actually talk about, not targets. Crossing one is worth
    knowing; it is not an instruction to do anything.
    """
    return [
        Decimal("1.7000"),
        Decimal("1.7200"),
        Decimal("1.7300"),
        Decimal("1.7400"),
        Decimal("1.7500"),
        Decimal("1.7600"),
        Decimal("1.7700"),
        Decimal("1.7800"),
        Decimal("1.8000"),
    ]


class FxAlertSettings(Section):
    """When the app should say something about the position.

    These are *preferences about behaviour*, which is why they are settings and
    not columns on ``fx_position``: the measured offset shortfall is a fact, but
    "tell me when the daily carrying cost drops below five dollars" is a choice.

    Every threshold here exists so that alert volume can be turned down without
    a code change. The one most likely to need it on first contact with a real
    market is ``minimum_change_since_last_alert``.
    """

    enabled: bool = True

    #: Alert once the rate has moved this far since the last time anything was
    #: said about it.
    absolute_rate_move: RateStr = Decimal("0.0050")
    #: As a percentage of the day's opening rate, so 0.50 means half a percent.
    intraday_percent_move: DecimalStr = Decimal("0.50")

    alert_new_7d_high: bool = True
    alert_new_30d_high: bool = True
    alert_new_90d_high: bool = True
    #: How long a new high stays quiet before another can be reported, unless
    #: the rate has also moved another ``minimum_change_since_last_alert``.
    new_high_cooldown_minutes: int = Field(default=240, ge=0)

    alert_round_number_breaks: bool = True
    watch_levels: list[RateStr] = Field(default_factory=_default_watch_levels)

    #: The remaining balance's target-currency value changing by this much.
    material_nzd_value_change: DecimalStr = Decimal("5000")
    #: A second, louder threshold for a move worth interrupting someone for.
    secondary_nzd_value_change: DecimalStr = Decimal("10000")

    #: Offset shortfall levels worth remarking on, in target currency.
    offset_shortfall_thresholds: list[DecimalStr] = Field(
        default_factory=lambda: [Decimal("100000"), Decimal("50000"), Decimal("0")]
    )
    #: Daily carrying cost levels, in target currency.
    daily_cost_thresholds: list[DecimalStr] = Field(
        default_factory=lambda: [Decimal("10"), Decimal("5")]
    )

    #: **The gate that does not exist for tranche alerts.** Having spoken at
    #: 1.7502, say nothing again until the rate has moved at least this far —
    #: which is what stops a rate oscillating a pip around a level producing one
    #: alert per cooldown window for ever.
    minimum_change_since_last_alert: RateStr = Decimal("0.0050")


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
    zones: ZoneSettings = Field(default_factory=ZoneSettings)
    simulation: SimulationSettings = Field(default_factory=SimulationSettings)
    fx_alerts: FxAlertSettings = Field(default_factory=FxAlertSettings)


class SettingsUpdate(BaseModel):
    """Partial settings update: only the supplied sections are replaced."""

    model_config = ConfigDict(extra="forbid")

    general: GeneralSettings | None = None
    formatting: FormattingSettings | None = None
    providers: ProviderSettings | None = None
    notifications: NotificationSettings | None = None
    home_assistant: HomeAssistantSettings | None = None
    retention: RetentionSettings | None = None
    zones: ZoneSettings | None = None
    simulation: SimulationSettings | None = None
    fx_alerts: FxAlertSettings | None = None
