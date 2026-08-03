"""Request and response models for obligations.

Every money and rate figure crosses the API as a string, so the browser never
receives a float for something that has to be exact.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import Field, field_validator, model_validator

from app.schemas.common import DecimalStr, MoneyStr, RateStr, Schema, StrictSchema, TargetStr
from app.services.obligation_engine import (
    InterestBasis,
    ObligationType,
    Priority,
    RecommendedAction,
    Relationship,
)

#: Shown wherever a figure is presented. This is decision support, not advice.
DISCLAIMER = (
    "These figures are estimates calculated from the values you entered and an "
    "indicative market rate. This is decision support, not financial advice, and "
    "this application never moves money."
)


class ObligationIn(StrictSchema):
    """Creating or replacing an obligation."""

    name: str = Field(min_length=1, max_length=120)
    obligation_type: ObligationType = ObligationType.OTHER
    total_nzd: MoneyStr
    amount_funded_nzd: MoneyStr = Decimal(0)
    remaining_override_nzd: MoneyStr | None = None
    #: A fraction, not a percentage: enter 6.04% as 0.0604.
    annual_rate: RateStr = Decimal(0)
    interest_basis: InterestBasis = InterestBasis.SIMPLE_ANNUAL
    daily_rate: RateStr | None = None
    due_date: date | None = None
    earliest_payment_date: date | None = None
    priority: Priority = Priority.NORMAL
    relationship_importance: Relationship = Relationship.NONE
    minimum_payment_nzd: MoneyStr | None = None
    partial_allowed: bool = True
    target_rate: TargetStr | None = None
    max_wait_days: int | None = Field(default=None, ge=0, le=3650)
    notes: str = ""
    active: bool = True
    completed: bool = False

    @field_validator("total_nzd", "amount_funded_nzd")
    @classmethod
    def _not_negative(cls, value: Decimal) -> Decimal:
        if value < 0:
            raise ValueError("Amounts cannot be negative.")
        return value

    @field_validator("annual_rate")
    @classmethod
    def _plausible_rate(cls, value: Decimal) -> Decimal:
        # A fraction, so 1.0 would be 100% a year. Anything past that is almost
        # certainly a percentage entered by mistake.
        if value < 0 or value > 1:
            raise ValueError(
                "Enter the annual rate as a fraction, so 6.04% is 0.0604. "
                "Values above 1 are rejected as a likely percentage."
            )
        return value

    @model_validator(mode="after")
    def _daily_rate_present_when_needed(self) -> ObligationIn:
        if self.interest_basis == InterestBasis.DAILY_MANUAL and self.daily_rate is None:
            raise ValueError("A daily rate is required when the interest basis is 'daily_manual'.")
        return self


class ObligationPatch(StrictSchema):
    """Editing one or more fields. Omitted fields are left alone."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    obligation_type: ObligationType | None = None
    total_nzd: MoneyStr | None = None
    amount_funded_nzd: MoneyStr | None = None
    remaining_override_nzd: MoneyStr | None = None
    annual_rate: RateStr | None = None
    interest_basis: InterestBasis | None = None
    daily_rate: RateStr | None = None
    due_date: date | None = None
    earliest_payment_date: date | None = None
    priority: Priority | None = None
    relationship_importance: Relationship | None = None
    minimum_payment_nzd: MoneyStr | None = None
    partial_allowed: bool | None = None
    target_rate: TargetStr | None = None
    max_wait_days: int | None = Field(default=None, ge=0, le=3650)
    notes: str | None = None
    active: bool | None = None
    completed: bool | None = None


class FundingIn(StrictSchema):
    """Recording NZD applied to an obligation."""

    amount_nzd: MoneyStr
    conversion_id: int | None = None
    note: str = ""

    @field_validator("amount_nzd")
    @classmethod
    def _positive(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("A funding amount must be greater than zero.")
        return value


class FundingOut(Schema):
    id: int
    obligation_id: int
    amount_nzd: MoneyStr
    conversion_id: int | None
    funded_at: datetime
    note: str


class WaitingOut(Schema):
    days: int
    waiting_cost_nzd: MoneyStr
    fx_gain_nzd: MoneyStr | None
    net_benefit_nzd: MoneyStr | None


class PriorityComponentsOut(Schema):
    """The score, itemised. Never present the total without these."""

    due_urgency: DecimalStr
    user_priority: DecimalStr
    relationship: DecimalStr
    interest_cost: DecimalStr
    size: DecimalStr
    max_wait: DecimalStr
    partial_flexibility: DecimalStr


class ObligationOut(Schema):
    """An obligation with everything computed for it."""

    id: int
    name: str
    obligation_type: ObligationType
    priority: Priority
    relationship_importance: Relationship
    interest_basis: InterestBasis
    partial_allowed: bool
    active: bool
    completed: bool
    notes: str

    total_nzd: MoneyStr
    amount_funded_nzd: MoneyStr
    remaining_nzd: MoneyStr
    annual_rate: RateStr
    minimum_payment_nzd: MoneyStr | None
    due_date: date | None
    earliest_payment_date: date | None
    target_rate: TargetStr | None
    max_wait_days: int | None

    # Cost of carrying it
    daily_cost_nzd: MoneyStr
    weekly_cost_nzd: MoneyStr
    monthly_cost_nzd: MoneyStr
    annual_cost_nzd: MoneyStr
    has_interest_cost: bool

    # The FX side
    usd_required_now: MoneyStr | None
    rate_used: RateStr | None
    rate_stale: bool
    #: "market", "wise_estimate" or "wise_quote" — never conflated.
    rate_quality: str

    gain_at_improvement: dict[str, MoneyStr | None]
    gain_at_target_nzd: MoneyStr | None
    waiting: list[WaitingOut]
    break_even_days_at_improvement: dict[str, DecimalStr | None]
    break_even_days_at_target: DecimalStr | None
    break_even_rate_after: dict[str, RateStr | None]

    days_until_due: int | None
    overdue: bool

    priority_components: PriorityComponentsOut
    financial_score: DecimalStr
    overall_score: DecimalStr
    financial_rank: int
    overall_rank: int

    action: RecommendedAction
    reason: str
    warnings: list[str]
    disclaimer: str = DISCLAIMER


class AllocationLineOut(Schema):
    """One obligation inside a conversion plan."""

    obligation_id: int
    name: str
    nzd_funded: MoneyStr
    usd_required: MoneyStr | None
    fully_funded: bool
    action: RecommendedAction


class AllocationOut(Schema):
    """A suggested tranche, and exactly what it would settle.

    A recommendation only. Nothing here initiates a conversion.
    """

    label: str
    description: str
    usd_to_convert: MoneyStr | None
    nzd_obtained: MoneyStr
    lines: list[AllocationLineOut]
    unfunded_obligation_ids: list[int]
    unfunded_nzd: MoneyStr
    rate_used: RateStr | None
    rate_stale: bool
    disclaimer: str = DISCLAIMER


class PortfolioOut(Schema):
    """Everything across the active obligations."""

    total_obligations: int
    total_nzd: MoneyStr
    total_usd_required: MoneyStr | None
    total_daily_cost_nzd: MoneyStr
    total_monthly_cost_nzd: MoneyStr
    due_within_7_days_nzd: MoneyStr
    due_within_30_days_nzd: MoneyStr

    highest_priority_obligation_id: int | None
    highest_priority_obligation_name: str
    next_obligation_id: int | None
    next_obligation_name: str
    next_conversion_usd: MoneyStr | None
    next_conversion_nzd: MoneyStr

    usd_after_critical: MoneyStr | None
    usd_after_high_priority: MoneyStr | None
    weighted_break_even_rate: RateStr | None
    max_rational_wait_days: int | None

    strategy_status: str
    rate_used: RateStr | None
    rate_stale: bool
    rate_quality: str
    warnings: list[str]
    disclaimer: str = DISCLAIMER


class AllocationRequest(StrictSchema):
    """Which obligations to include in a scenario, and at what rate."""

    #: Empty means every active obligation.
    obligation_ids: list[int] = Field(default_factory=list)
    #: Overrides the live rate, for asking "what if we reach 1.78?".
    rate: TargetStr | None = None
    #: Caps the tranche. Omitted means fund as much as the selection needs.
    usd_available: MoneyStr | None = None
