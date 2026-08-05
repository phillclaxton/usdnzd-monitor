"""Strategy, tranche and summary schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from app.money import ALLOWED_CURRENCIES
from app.schemas.common import MoneyStr, RateStr, Schema, StrictSchema, TargetStr

AllocationTypeName = Literal["percentage", "fixed_amount", "remainder"]
StrategyStatusName = Literal[
    "draft", "waiting_for_funds", "active", "paused", "completed", "cancelled", "archived"
]
TrancheStatusName = Literal[
    "pending", "armed", "target_reached", "partially_completed", "completed", "skipped", "cancelled"
]
FeeTypeName = Literal["percentage", "fixed_plus_percentage", "quote_only", "manual"]


# ---------------------------------------------------------------------------
# Fee models
# ---------------------------------------------------------------------------


class FeeModelIn(StrictSchema):
    name: str = Field(min_length=1, max_length=80)
    fee_type: FeeTypeName
    fixed_fee: MoneyStr = Decimal(0)
    percentage_fee: MoneyStr = Decimal(0)
    minimum_fee: MoneyStr | None = None
    maximum_fee: MoneyStr | None = None
    currency: str = "USD"
    provider: str = "wise"

    @field_validator("currency")
    @classmethod
    def _known_currency(cls, value: str) -> str:
        code = value.strip().upper()
        if code not in ALLOWED_CURRENCIES:
            raise ValueError(f"{value!r} is not a supported currency code")
        return code

    @model_validator(mode="after")
    def _coherent(self) -> FeeModelIn:
        if self.fixed_fee < 0 or self.percentage_fee < 0:
            raise ValueError("Fees cannot be negative.")
        if self.percentage_fee > Decimal(100):
            raise ValueError("A percentage fee above 100% is not plausible.")
        if (
            self.minimum_fee is not None
            and self.maximum_fee is not None
            and self.minimum_fee > self.maximum_fee
        ):
            raise ValueError("The minimum fee cannot exceed the maximum fee.")
        return self


class FeeModelOut(Schema):
    id: int
    name: str
    fee_type: str
    fixed_fee: MoneyStr
    percentage_fee: MoneyStr
    minimum_fee: MoneyStr | None
    maximum_fee: MoneyStr | None
    currency: str
    provider: str


# ---------------------------------------------------------------------------
# Tranches
# ---------------------------------------------------------------------------


class TrancheIn(StrictSchema):
    sequence: int = Field(ge=1, le=100)
    name: str = Field(default="", max_length=80)
    allocation_type: AllocationTypeName = "percentage"
    allocation_value: MoneyStr = Decimal(0)
    target_rate: TargetStr
    minimum_rate: TargetStr | None = None
    deadline: datetime | None = None
    intended_for_auto_conversion: bool = True
    notifications_enabled: bool = True
    wise_auto_conversion_reference: str | None = Field(default=None, max_length=128)

    @field_validator("target_rate")
    @classmethod
    def _positive_target(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("A target rate must be greater than zero.")
        return value

    @model_validator(mode="after")
    def _allocation_is_coherent(self) -> TrancheIn:
        if self.allocation_type != "remainder" and self.allocation_value <= 0:
            raise ValueError("A percentage or fixed tranche must allocate more than zero.")
        if self.allocation_type == "percentage" and self.allocation_value > Decimal(100):
            raise ValueError("A tranche cannot allocate more than 100%.")
        return self


class TrancheUpdate(TrancheIn):
    status: TrancheStatusName | None = None


class TrancheOut(Schema):
    id: int
    strategy_id: int
    sequence: int
    name: str
    allocation_type: str
    allocation_value: MoneyStr
    calculated_source_amount: MoneyStr
    target_rate: RateStr
    minimum_rate: RateStr | None
    deadline: datetime | None
    status: str
    intended_for_auto_conversion: bool
    notifications_enabled: bool
    wise_auto_conversion_reference: str | None
    target_first_reached_at: datetime | None
    notification_sent_at: datetime | None
    acknowledged_at: datetime | None
    completed_at: datetime | None


class TrancheReorder(StrictSchema):
    strategy_id: int
    #: Tranche IDs in their new order.
    tranche_ids: list[int] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Deadline requirements
# ---------------------------------------------------------------------------


class RequirementIn(StrictSchema):
    due_date: datetime
    required_source_amount: MoneyStr | None = None
    required_percentage: MoneyStr | None = None
    description: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _one_of(self) -> RequirementIn:
        if (self.required_source_amount is None) == (self.required_percentage is None):
            raise ValueError("Set either a required amount or a required percentage, not both.")
        if self.required_source_amount is not None and self.required_source_amount <= 0:
            raise ValueError("A required amount must be greater than zero.")
        if self.required_percentage is not None and not (
            0 < self.required_percentage <= Decimal(100)
        ):
            raise ValueError("A required percentage must be between 0 and 100.")
        return self


class RequirementOut(Schema):
    id: int
    strategy_id: int
    due_date: datetime
    required_source_amount: MoneyStr | None
    required_percentage: MoneyStr | None
    description: str


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------


class StrategyIn(StrictSchema):
    name: str = Field(min_length=1, max_length=120)
    source_currency: str = "USD"
    target_currency: str = "NZD"
    initial_source_amount: MoneyStr
    funds_available_amount: MoneyStr = Decimal(0)
    funds_arrival_date: datetime | None = None
    strategy_start_date: datetime | None = None
    final_deadline: datetime | None = None
    minimum_acceptable_rate: TargetStr | None = None
    walk_away_rate: TargetStr | None = None
    require_targets_in_order: bool = False
    fee_model_id: int | None = None
    rate_provider_id: str | None = None
    timezone: str = "Pacific/Auckland"
    notes: str = Field(default="", max_length=2000)
    tranches: list[TrancheIn] = Field(default_factory=list)
    requirements: list[RequirementIn] = Field(default_factory=list)

    @field_validator("source_currency", "target_currency")
    @classmethod
    def _known_currency(cls, value: str) -> str:
        code = value.strip().upper()
        if code not in ALLOWED_CURRENCIES:
            raise ValueError(f"{value!r} is not a supported currency code")
        return code

    @model_validator(mode="after")
    def _coherent(self) -> StrategyIn:
        if self.source_currency == self.target_currency:
            raise ValueError("The source and target currencies must differ.")
        if self.initial_source_amount <= 0:
            raise ValueError("The total amount must be greater than zero.")
        if self.funds_available_amount < 0:
            raise ValueError("The available amount cannot be negative.")
        if self.funds_available_amount > self.initial_source_amount:
            raise ValueError("The available amount cannot exceed the strategy total.")
        if (
            self.final_deadline
            and self.funds_arrival_date
            and self.final_deadline < self.funds_arrival_date
        ):
            raise ValueError("The deadline cannot be before the funds arrive.")
        sequences = [tranche.sequence for tranche in self.tranches]
        if len(sequences) != len(set(sequences)):
            raise ValueError("Tranche sequence numbers must be unique.")
        return self


class StrategyUpdate(StrategyIn):
    status: StrategyStatusName | None = None


class StrategyOut(Schema):
    id: int
    name: str
    status: str
    source_currency: str
    target_currency: str
    initial_source_amount: MoneyStr
    funds_available_amount: MoneyStr
    funds_arrival_date: datetime | None
    strategy_start_date: datetime | None
    final_deadline: datetime | None
    minimum_acceptable_rate: RateStr | None
    walk_away_rate: RateStr | None
    require_targets_in_order: bool
    fee_model_id: int | None
    rate_provider_id: str | None
    timezone: str
    notes: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    completed_at: datetime | None
    tranches: list[TrancheOut] = Field(default_factory=list)
    requirements: list[RequirementOut] = Field(default_factory=list)


class AllocationIssue(Schema):
    severity: Literal["error", "warning"]
    message: str


class ValidationOut(Schema):
    valid: bool
    total_allocated: MoneyStr
    unallocated: MoneyStr
    percentage_total: MoneyStr
    issues: list[AllocationIssue] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


class FeeOut(Schema):
    available: bool
    label: str
    amount_source_currency: MoneyStr | None
    amount_target_currency: MoneyStr | None
    basis: str


class OutcomeOut(Schema):
    """A projected conversion, always labelled as an estimate."""

    source_amount: MoneyStr
    rate: RateStr
    gross_target_amount: MoneyStr
    fee: FeeOut
    net_target_amount: MoneyStr | None
    effective_rate: RateStr | None
    quality: str


class TrancheProgressOut(Schema):
    tranche: TrancheOut
    distance_to_target: RateStr | None
    target_reached_now: bool
    estimated_gross: MoneyStr | None
    estimated_fee: FeeOut
    estimated_net: MoneyStr | None
    converted_source_amount: MoneyStr
    converted_target_amount: MoneyStr
    percent_complete: MoneyStr | None
    upside_to_target: MoneyStr | None


class SensitivityOut(Schema):
    movement: RateStr
    downside: MoneyStr
    upside: MoneyStr


class RequirementProgressOut(Schema):
    requirement: RequirementOut
    required_source_amount: MoneyStr
    shortfall: MoneyStr
    days_remaining: int | None
    overdue: bool


class WalkAwayOut(Schema):
    reached: bool
    walk_away_rate: RateStr | None
    remaining_source_amount: MoneyStr
    convert_now: OutcomeOut | None
    existing_blended_rate: RateStr | None
    blended_if_converted_now: RateStr | None
    highest_outstanding_target: RateStr | None
    difference_versus_waiting: MoneyStr | None
    rate_movement_to_next_target: RateStr | None
    sensitivity: list[SensitivityOut]


class ZoneOut(Schema):
    label: str
    guidance: str
    lower_bound: RateStr | None


class ComparisonOut(Schema):
    """How the plan so far compares with simple alternatives."""

    versus_start_rate: MoneyStr | None
    versus_six_month_high: MoneyStr | None
    versus_six_month_low: MoneyStr | None
    versus_today: MoneyStr | None
    versus_equal_schedule: MoneyStr | None


class StrategySummaryOut(Schema):
    """Everything the dashboard needs in one request."""

    strategy: StrategyOut
    current_rate: RateStr | None
    rate_status: str
    rate_zone: ZoneOut | None

    initial_source_amount: MoneyStr
    available_source_amount: MoneyStr
    converted_source_amount: MoneyStr
    remaining_source_amount: MoneyStr
    percent_converted: MoneyStr | None

    gross_target_received: MoneyStr
    net_target_received: MoneyStr
    total_fees: MoneyStr | None
    blended_gross_rate: RateStr | None
    blended_effective_rate: RateStr | None
    best_conversion_rate: RateStr | None
    worst_conversion_rate: RateStr | None
    average_fee_percentage: MoneyStr | None

    convert_all_now: OutcomeOut | None
    next_target_rate: RateStr | None
    next_target_source_amount: MoneyStr | None
    next_target_upside: MoneyStr | None
    one_cent_exposure: MoneyStr
    sensitivity: list[SensitivityOut]

    tranche_progress: list[TrancheProgressOut]
    requirements: list[RequirementProgressOut]
    walk_away: WalkAwayOut
    comparisons: ComparisonOut

    days_to_deadline: int | None
    deadline_severity: str
    deadline_message: str
    fee_model: FeeModelOut | None
    warnings: list[str] = Field(default_factory=list)


class ScenarioLegOut(Schema):
    source_amount: MoneyStr
    rate: RateStr
    label: str


class ScenarioOut(Schema):
    key: str
    name: str
    description: str
    total_source_amount: MoneyStr
    gross_target_amount: MoneyStr
    fee: FeeOut
    net_target_amount: MoneyStr | None
    blended_rate: RateStr
    effective_rate: RateStr | None
    exposed_source_amount: MoneyStr
    one_cent_exposure: MoneyStr
    rate_required: RateStr | None
    legs: list[ScenarioLegOut]
    assumptions: list[str]


class ScenariosOut(Schema):
    scenarios: list[ScenarioOut]
    #: Stated plainly rather than crowning a winner.
    note: str = (
        "These are trade-offs, not a ranking. A plan that produces more assumes "
        "rates it may never reach; a plan that finishes sooner gives up upside "
        "in exchange for certainty."
    )


class TemplateOut(Schema):
    key: str
    name: str
    description: str
    tranches: list[TrancheIn]


# ---------------------------------------------------------------------------
# JSON document editing
# ---------------------------------------------------------------------------


class DocumentIn(StrictSchema):
    """Pasted text, exactly as typed.

    The text is carried as a string rather than as a parsed object so that a
    syntax error can be reported with a line and column. Letting FastAPI parse
    it would collapse every mistake into one unlocatable message.
    """

    text: str = Field(max_length=200_000)


class DocumentOut(Schema):
    strategy_id: int | None = None
    name: str
    #: The document as text, indented and ready for an editor.
    text: str
    #: The same content already parsed, for callers that want the structure.
    document: dict[str, Any]
    #: Field name to the reason it is not part of the document.
    omitted: dict[str, str] = Field(default_factory=dict)


class DocumentProblemOut(Schema):
    path: str
    message: str
    line: int | None = None
    column: int | None = None


class DocumentChangeOut(Schema):
    path: str
    before: str
    after: str


class DocumentPreviewOut(Schema):
    """What saving the pasted document would do, computed without saving it."""

    valid: bool
    problems: list[DocumentProblemOut] = Field(default_factory=list)
    changes: list[DocumentChangeOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    tranches_added: list[int] = Field(default_factory=list)
    tranches_removed: list[int] = Field(default_factory=list)
    tranches_retargeted: list[int] = Field(default_factory=list)
    conversions_preserved: int = 0
