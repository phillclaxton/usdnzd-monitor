"""The debt and obligation decision engine.

Pure calculation. This module knows nothing about where a rate came from, how
it is stored, or how it is presented: it takes plain values and returns plain
results, so the arithmetic can be read and tested on its own.

Every figure is an estimate produced from the numbers the user entered. None of
it is financial advice, and nothing here can move money.

The central question is a trade-off:

- An interest-bearing obligation costs money for every day it goes unpaid.
- A better USD/NZD rate reduces the USD needed to settle it.

Waiting is rational only while the second outweighs the first.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.money import quantize_display, quantize_money, quantize_rate, safe_divide

#: Interest is accrued on a 365-day year unless a daily rate is entered directly.
DAYS_PER_YEAR = Decimal(365)

#: Months are the annual cost divided by twelve rather than a 30-day slice, so a
#: monthly figure and twelve of them agree with the annual one.
MONTHS_PER_YEAR = Decimal(12)

#: The two rate improvements quoted for every obligation.
STANDARD_IMPROVEMENTS: tuple[Decimal, ...] = (Decimal("0.005"), Decimal("0.01"))

#: The waiting horizons quoted for every obligation.
STANDARD_HORIZONS: tuple[int, ...] = (7, 14, 30, 60, 90)


class ObligationType(StrEnum):
    """Affects wording only. Every figure comes from the entered values."""

    MORTGAGE = "mortgage"
    REVOLVING_CREDIT = "revolving_credit"
    OFFSET_LOAN = "offset_loan"
    PERSONAL_LOAN = "personal_loan"
    CREDIT_CARD = "credit_card"
    TAX_PAYMENT = "tax_payment"
    INTEREST_FREE_LOAN = "interest_free_loan"
    PLANNED_PURCHASE = "planned_purchase"
    OTHER = "other"


class InterestBasis(StrEnum):
    SIMPLE_ANNUAL = "simple_annual"
    DAILY_MANUAL = "daily_manual"
    NONE = "none"


class Priority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


class Relationship(StrEnum):
    """Non-financial importance. An interest-free family loan lives here."""

    NONE = "none"
    MODERATE = "moderate"
    HIGH = "high"


class RecommendedAction(StrEnum):
    PAY_NOW = "PAY_NOW"
    CONVERT_NOW = "CONVERT_NOW"
    CONVERT_PARTIAL = "CONVERT_PARTIAL"
    WAIT_FOR_TARGET = "WAIT_FOR_TARGET"
    WAIT_WITH_DEADLINE = "WAIT_WITH_DEADLINE"
    REVIEW = "REVIEW"
    FUNDED = "FUNDED"
    OVERDUE = "OVERDUE"


#: Wording per type, used in explanations. Nothing here changes a number.
TYPE_LABELS: dict[ObligationType, str] = {
    ObligationType.MORTGAGE: "mortgage",
    ObligationType.REVOLVING_CREDIT: "revolving credit facility",
    ObligationType.OFFSET_LOAN: "offset loan",
    ObligationType.PERSONAL_LOAN: "personal loan",
    ObligationType.CREDIT_CARD: "credit card",
    ObligationType.TAX_PAYMENT: "tax payment",
    ObligationType.INTEREST_FREE_LOAN: "interest-free loan",
    ObligationType.PLANNED_PURCHASE: "planned purchase",
    ObligationType.OTHER: "obligation",
}


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ObligationInput:
    """Everything the engine needs about one obligation.

    Deliberately a plain value object: the database row, the API payload and a
    test all build one of these, and the engine never sees anything else.
    """

    name: str
    obligation_type: ObligationType = ObligationType.OTHER
    total_nzd: Decimal = Decimal(0)
    amount_funded_nzd: Decimal = Decimal(0)
    #: Set only when the user overrides ``total - funded``.
    remaining_override_nzd: Decimal | None = None
    annual_rate: Decimal = Decimal(0)
    interest_basis: InterestBasis = InterestBasis.SIMPLE_ANNUAL
    #: Used when the basis is DAILY_MANUAL: a fraction per day, not a percentage.
    daily_rate: Decimal | None = None
    due_date: date | None = None
    earliest_payment_date: date | None = None
    priority: Priority = Priority.NORMAL
    relationship: Relationship = Relationship.NONE
    minimum_payment_nzd: Decimal | None = None
    partial_allowed: bool = True
    target_rate: Decimal | None = None
    max_wait_days: int | None = None
    notes: str = ""
    active: bool = True
    completed: bool = False


@dataclass(frozen=True, slots=True)
class RateContext:
    """The FX side of the decision, supplied by whatever produced the rate.

    ``stale`` is honoured strictly: a stale rate never supports a recommendation
    to wait, because the comparison it would rest on is not current.
    """

    rate: Decimal | None
    stale: bool = False
    as_of: str = ""
    #: "market", "wise_estimate" or "wise_quote" — never conflated.
    quality: str = "market"


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WaitingOutcome:
    """What waiting a given number of days is worth, for one target rate."""

    days: int
    waiting_cost_nzd: Decimal
    fx_gain_nzd: Decimal | None
    net_benefit_nzd: Decimal | None


@dataclass(frozen=True, slots=True)
class PriorityBreakdown:
    """A score with its components, so it can be argued with.

    An opaque number would be worse than useless for a decision like this.
    """

    due_urgency: Decimal = Decimal(0)
    user_priority: Decimal = Decimal(0)
    relationship: Decimal = Decimal(0)
    interest_cost: Decimal = Decimal(0)
    size: Decimal = Decimal(0)
    max_wait: Decimal = Decimal(0)
    partial_flexibility: Decimal = Decimal(0)

    @property
    def financial_total(self) -> Decimal:
        """Money-driven urgency only: cost, size, and the deadline."""
        return self.due_urgency + self.interest_cost + self.size + self.max_wait

    @property
    def overall_total(self) -> Decimal:
        """Everything, including the reasons that are not financial."""
        return (
            self.financial_total
            + self.user_priority
            + self.relationship
            + (self.partial_flexibility)
        )

    def components(self) -> dict[str, Decimal]:
        return {
            "due_urgency": self.due_urgency,
            "user_priority": self.user_priority,
            "relationship": self.relationship,
            "interest_cost": self.interest_cost,
            "size": self.size,
            "max_wait": self.max_wait,
            "partial_flexibility": self.partial_flexibility,
        }


@dataclass(frozen=True, slots=True)
class ObligationAnalysis:
    """Everything computed for a single obligation."""

    name: str
    obligation_type: ObligationType
    remaining_nzd: Decimal

    # Cost of carrying it
    daily_cost_nzd: Decimal
    weekly_cost_nzd: Decimal
    monthly_cost_nzd: Decimal
    annual_cost_nzd: Decimal
    has_interest_cost: bool

    # The FX side. None whenever no usable rate is available.
    usd_required_now: Decimal | None
    rate_used: Decimal | None
    rate_stale: bool
    rate_quality: str

    gain_at_improvement: dict[str, Decimal | None]
    gain_at_target_nzd: Decimal | None
    waiting: dict[int, WaitingOutcome]
    break_even_days_at_improvement: dict[str, Decimal | None]
    break_even_rate_after: dict[int, Decimal | None]
    break_even_days_at_target: Decimal | None

    days_until_due: int | None
    overdue: bool

    priority: PriorityBreakdown
    financial_score: Decimal
    overall_score: Decimal

    action: RecommendedAction
    reason: str
    #: Set when the engine could not reach a rate-dependent conclusion.
    warnings: list[str] = field(default_factory=list)

    @property
    def estimate_notice(self) -> str:
        return (
            "All figures are estimates from the values you entered. "
            "Decision support only — not financial advice."
        )


# ---------------------------------------------------------------------------
# Core arithmetic
# ---------------------------------------------------------------------------


def remaining_amount(obligation: ObligationInput) -> Decimal:
    """``total - funded``, unless the user has overridden it."""
    if obligation.remaining_override_nzd is not None:
        return quantize_money(max(Decimal(0), obligation.remaining_override_nzd))
    remaining = obligation.total_nzd - obligation.amount_funded_nzd
    return quantize_money(max(Decimal(0), remaining))


def effective_daily_rate(obligation: ObligationInput) -> Decimal:
    """The fraction of the balance that accrues each day.

    A manually entered daily rate is used as given; a simple annual rate is
    divided by 365. ``InterestBasis.NONE`` is zero however the rate field is set,
    so an obligation marked as interest-free cannot accrue by accident.
    """
    if obligation.interest_basis == InterestBasis.NONE:
        return Decimal(0)
    if obligation.interest_basis == InterestBasis.DAILY_MANUAL:
        return obligation.daily_rate or Decimal(0)
    return obligation.annual_rate / DAYS_PER_YEAR


def waiting_cost(remaining_nzd: Decimal, daily_rate: Decimal, days: int) -> Decimal:
    """What ``days`` of delay costs in NZD.

    ``A * r * d / 365`` when the rate is annual, expressed here through the
    already-derived daily rate so both interest bases share one path.
    """
    return quantize_money(remaining_nzd * daily_rate * Decimal(days))


def usd_required(remaining_nzd: Decimal, rate: Decimal | None) -> Decimal | None:
    """USD needed to buy the remaining NZD at ``rate``."""
    if rate is None:
        return None
    result = safe_divide(remaining_nzd, rate)
    return None if result is None else quantize_money(result)


def fx_gain(
    usd_amount: Decimal | None, current_rate: Decimal | None, target_rate: Decimal | None
) -> Decimal | None:
    """Extra NZD the same USD buys if the rate improves to ``target_rate``.

    Negative when the target is worse than the current rate, which is the honest
    answer rather than a floor at zero.
    """
    if usd_amount is None or current_rate is None or target_rate is None:
        return None
    return quantize_money(usd_amount * (target_rate - current_rate))


def net_waiting_benefit(gain_nzd: Decimal | None, cost_nzd: Decimal) -> Decimal | None:
    """Positive when the rate improvement beats the cost of carrying the debt."""
    if gain_nzd is None:
        return None
    return quantize_money(gain_nzd - cost_nzd)


def break_even_days(gain_nzd: Decimal | None, daily_cost_nzd: Decimal) -> Decimal | None:
    """How long the improvement pays for.

    Returns ``None`` when nothing accrues: an obligation with no interest has no
    financial break-even period, and reporting a number there — or an infinity —
    would suggest a conclusion the arithmetic does not support.
    """
    if gain_nzd is None or daily_cost_nzd <= 0:
        return None
    result = safe_divide(gain_nzd, daily_cost_nzd)
    return None if result is None else quantize_display(result)


def break_even_rate(
    current_rate: Decimal | None, cost_nzd: Decimal, usd_amount: Decimal | None
) -> Decimal | None:
    """The rate that would exactly repay the cost of waiting.

    ``R + (waiting_cost / usd_required)``.
    """
    if current_rate is None or usd_amount is None or usd_amount <= 0:
        return None
    improvement = safe_divide(cost_nzd, usd_amount)
    if improvement is None:
        return None
    return quantize_rate(current_rate + improvement)


def days_until(due: date | None, today: date) -> int | None:
    return None if due is None else (due - today).days


# ---------------------------------------------------------------------------
# Priority
# ---------------------------------------------------------------------------

#: Weights are constants rather than magic numbers inside the function so the
#: model can be read, argued with and changed in one place.
PRIORITY_WEIGHTS: dict[Priority, Decimal] = {
    Priority.CRITICAL: Decimal(60),
    Priority.HIGH: Decimal(30),
    Priority.NORMAL: Decimal(10),
    Priority.LOW: Decimal(0),
}

RELATIONSHIP_WEIGHTS: dict[Relationship, Decimal] = {
    Relationship.HIGH: Decimal(35),
    Relationship.MODERATE: Decimal(15),
    Relationship.NONE: Decimal(0),
}


def due_urgency_score(days: int | None) -> Decimal:
    """Steeply increasing as the date approaches; highest once overdue."""
    if days is None:
        return Decimal(0)
    if days < 0:
        return Decimal(80)
    if days <= 7:
        return Decimal(60)
    if days <= 30:
        return Decimal(35)
    if days <= 90:
        return Decimal(15)
    return Decimal(5)


def interest_cost_score(daily_cost_nzd: Decimal) -> Decimal:
    """Rises with the daily bleed, capped so a large debt cannot dominate.

    Deliberately not proportional: an obligation costing NZ$100 a day is more
    urgent than one costing NZ$10, but not ten times more urgent than every
    non-financial consideration put together.
    """
    if daily_cost_nzd <= 0:
        return Decimal(0)
    if daily_cost_nzd >= 100:
        return Decimal(40)
    if daily_cost_nzd >= 50:
        return Decimal(30)
    if daily_cost_nzd >= 20:
        return Decimal(22)
    if daily_cost_nzd >= 5:
        return Decimal(14)
    return Decimal(6)


def size_score(remaining_nzd: Decimal, portfolio_total_nzd: Decimal) -> Decimal:
    """Share of the total book, worth at most 10 points."""
    if portfolio_total_nzd <= 0 or remaining_nzd <= 0:
        return Decimal(0)
    share = safe_divide(remaining_nzd, portfolio_total_nzd)
    if share is None:
        return Decimal(0)
    return quantize_display(share * Decimal(10))


def max_wait_score(max_wait_days: int | None) -> Decimal:
    """A short acceptable wait is itself a statement of urgency."""
    if max_wait_days is None:
        return Decimal(0)
    if max_wait_days <= 7:
        return Decimal(25)
    if max_wait_days <= 30:
        return Decimal(12)
    return Decimal(4)


def partial_flexibility_score(partial_allowed: bool) -> Decimal:
    """All-or-nothing obligations are slightly harder to schedule around."""
    return Decimal(0) if partial_allowed else Decimal(8)


def score_obligation(
    obligation: ObligationInput,
    remaining_nzd: Decimal,
    daily_cost_nzd: Decimal,
    days_to_due: int | None,
    portfolio_total_nzd: Decimal,
) -> PriorityBreakdown:
    """Build the score component by component.

    Interest rate alone never decides the order: a zero-interest obligation with
    a near due date or high relationship importance still scores highly.
    """
    return PriorityBreakdown(
        due_urgency=due_urgency_score(days_to_due),
        user_priority=PRIORITY_WEIGHTS[obligation.priority],
        relationship=RELATIONSHIP_WEIGHTS[obligation.relationship],
        interest_cost=interest_cost_score(daily_cost_nzd),
        size=size_score(remaining_nzd, portfolio_total_nzd),
        max_wait=max_wait_score(obligation.max_wait_days),
        partial_flexibility=partial_flexibility_score(obligation.partial_allowed),
    )


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Decision:
    action: RecommendedAction
    reason: str
    warnings: list[str] = field(default_factory=list)


def _money(value: Decimal) -> str:
    return f"NZ${quantize_money(value):,.2f}"


def decide(
    obligation: ObligationInput,
    *,
    remaining_nzd: Decimal,
    daily_cost_nzd: Decimal,
    days_to_due: int | None,
    rate: RateContext,
    net_at_horizon: Decimal | None,
    break_even_at_target: Decimal | None,
    nzd_available: Decimal,
) -> Decision:
    """Choose an action, and say why in a sentence a person can check.

    The order matters: settled and overdue states are facts, staleness blocks
    every rate-dependent conclusion, and only then do the trade-offs apply.
    """
    label = TYPE_LABELS[obligation.obligation_type]
    warnings: list[str] = []

    if obligation.completed or remaining_nzd <= 0:
        return Decision(
            RecommendedAction.FUNDED,
            f"This {label} is fully funded. Nothing further is required.",
        )

    if days_to_due is not None and days_to_due < 0:
        return Decision(
            RecommendedAction.OVERDUE,
            f"This {label} was due {abs(days_to_due)} day(s) ago and "
            f"{_money(remaining_nzd)} remains outstanding.",
        )

    # NZD already in hand needs no conversion decision at all.
    if nzd_available >= remaining_nzd > 0:
        return Decision(
            RecommendedAction.PAY_NOW,
            f"{_money(nzd_available)} of NZD is already available, which covers the "
            f"{_money(remaining_nzd)} outstanding. No conversion is needed.",
        )

    if rate.rate is None:
        return Decision(
            RecommendedAction.REVIEW,
            "No exchange rate is available, so the cost of waiting cannot be "
            "compared against any FX benefit. Review manually.",
            ["No usable rate."],
        )

    if rate.stale:
        # Never advise waiting on a rate that is not current: the whole
        # comparison rests on the rate being what it says it is.
        return Decision(
            RecommendedAction.REVIEW,
            "The exchange rate is stale, so waiting cannot be justified against it. "
            "Refresh the rate before deciding.",
            ["Stale rate: waiting is not recommended on out-of-date data."],
        )

    urgent_soon = days_to_due is not None and days_to_due <= 7
    due_within_month = days_to_due is not None and days_to_due <= 30
    max_wait_reached = obligation.max_wait_days is not None and obligation.max_wait_days <= 0
    critical = obligation.priority == Priority.CRITICAL
    high_relationship = obligation.relationship == Relationship.HIGH

    if urgent_soon:
        return Decision(
            RecommendedAction.CONVERT_NOW,
            f"Due in {days_to_due} day(s). Convert now to fund the "
            f"{_money(remaining_nzd)} outstanding; a better rate cannot be relied on "
            "within that window.",
            warnings,
        )

    if max_wait_reached:
        return Decision(
            RecommendedAction.CONVERT_NOW,
            "The maximum acceptable waiting period for this obligation has been "
            "reached, so convert now regardless of the rate.",
            warnings,
        )

    if critical:
        return Decision(
            RecommendedAction.CONVERT_NOW,
            f"Marked critical. Fund the {_money(remaining_nzd)} outstanding now rather "
            "than exposing it to rate movement.",
            warnings,
        )

    if high_relationship and due_within_month:
        return Decision(
            RecommendedAction.CONVERT_NOW,
            "Relationship importance is high and the date is within a month. Delay is "
            "not justified by the possible FX gain.",
            warnings,
        )

    # From here the decision is genuinely a trade-off.
    if net_at_horizon is not None and net_at_horizon < 0:
        return Decision(
            RecommendedAction.CONVERT_NOW,
            "Waiting costs more than the target rate would return: the net effect is "
            f"{_money(net_at_horizon)}. Converting now is the cheaper option.",
            warnings,
        )

    if obligation.partial_allowed and due_within_month and remaining_nzd > 0:
        return Decision(
            RecommendedAction.CONVERT_PARTIAL,
            "Part of this obligation falls due within the month and partial payments "
            "are allowed. Fund the near-term portion now and leave the balance waiting "
            "for a better rate.",
            warnings,
        )

    if obligation.target_rate is not None and net_at_horizon is not None and net_at_horizon > 0:
        if obligation.max_wait_days is not None:
            return Decision(
                RecommendedAction.WAIT_WITH_DEADLINE,
                f"Waiting for {obligation.target_rate} is worth "
                f"{_money(net_at_horizon)} net, but treat day "
                f"{obligation.max_wait_days} as the conversion deadline.",
                warnings,
            )
        return Decision(
            RecommendedAction.WAIT_FOR_TARGET,
            f"No urgent date, and reaching {obligation.target_rate} would be worth "
            f"{_money(net_at_horizon)} net after the cost of carrying the balance.",
            warnings,
        )

    if daily_cost_nzd <= 0:
        # No interest means no financial clock; the decision rests on the date
        # and on the non-financial importance instead.
        return Decision(
            RecommendedAction.WAIT_WITH_DEADLINE
            if obligation.due_date is not None
            else RecommendedAction.WAIT_FOR_TARGET,
            f"This {label} accrues no interest, so waiting has no financial cost. "
            + (
                f"The date of {obligation.due_date} is the only deadline."
                if obligation.due_date is not None
                else "Fund it when the rate suits, subject to your own priorities."
            ),
            warnings,
        )

    if break_even_at_target is not None:
        return Decision(
            RecommendedAction.WAIT_WITH_DEADLINE,
            f"Waiting stays worthwhile for about {break_even_at_target} day(s) at the "
            "configured target; past that the interest cost overtakes the FX gain.",
            warnings,
        )

    return Decision(
        RecommendedAction.REVIEW,
        "No target rate is set, so there is nothing to weigh the cost of waiting "
        "against. Set a target rate or convert now.",
        ["No target rate configured."],
    )


# ---------------------------------------------------------------------------
# Whole-obligation analysis
# ---------------------------------------------------------------------------


def analyse(
    obligation: ObligationInput,
    rate: RateContext,
    *,
    today: date,
    portfolio_total_nzd: Decimal = Decimal(0),
    nzd_available: Decimal = Decimal(0),
    horizons: tuple[int, ...] = STANDARD_HORIZONS,
    improvements: tuple[Decimal, ...] = STANDARD_IMPROVEMENTS,
) -> ObligationAnalysis:
    """Everything the dashboard, the entities and the notifications need."""
    remaining = remaining_amount(obligation)
    daily_rate = effective_daily_rate(obligation)

    daily_cost = quantize_money(remaining * daily_rate)
    annual_cost = quantize_money(remaining * daily_rate * DAYS_PER_YEAR)
    # Monthly is the annual figure divided by twelve, so twelve months and one
    # year agree. A 30-day slice would not.
    monthly_cost = quantize_money(annual_cost / MONTHS_PER_YEAR)
    weekly_cost = quantize_money(daily_cost * Decimal(7))

    usd_now = usd_required(remaining, rate.rate)

    gain_at_improvement: dict[str, Decimal | None] = {}
    break_even_at_improvement: dict[str, Decimal | None] = {}
    for improvement in improvements:
        key = format(improvement, "f")
        target = None if rate.rate is None else rate.rate + improvement
        gain = fx_gain(usd_now, rate.rate, target)
        gain_at_improvement[key] = gain
        break_even_at_improvement[key] = break_even_days(gain, daily_cost)

    gain_at_target = fx_gain(usd_now, rate.rate, obligation.target_rate)
    break_even_at_target = break_even_days(gain_at_target, daily_cost)

    waiting: dict[int, WaitingOutcome] = {}
    for days in horizons:
        cost = waiting_cost(remaining, daily_rate, days)
        waiting[days] = WaitingOutcome(
            days=days,
            waiting_cost_nzd=cost,
            fx_gain_nzd=gain_at_target,
            net_benefit_nzd=net_waiting_benefit(gain_at_target, cost),
        )

    break_even_rate_after: dict[int, Decimal | None] = {}
    horizon_days = [7, 30]
    if obligation.max_wait_days is not None and obligation.max_wait_days > 0:
        horizon_days.append(obligation.max_wait_days)
    for days in horizon_days:
        cost = waiting_cost(remaining, daily_rate, days)
        break_even_rate_after[days] = break_even_rate(rate.rate, cost, usd_now)

    days_to_due = days_until(obligation.due_date, today)
    breakdown = score_obligation(
        obligation, remaining, daily_cost, days_to_due, portfolio_total_nzd
    )

    # The horizon the recommendation is judged against: the user's own limit
    # when they set one, otherwise 30 days.
    judged_at = obligation.max_wait_days if obligation.max_wait_days else 30
    judged_cost = waiting_cost(remaining, daily_rate, judged_at)
    net_at_horizon = net_waiting_benefit(gain_at_target, judged_cost)

    decision = decide(
        obligation,
        remaining_nzd=remaining,
        daily_cost_nzd=daily_cost,
        days_to_due=days_to_due,
        rate=rate,
        net_at_horizon=net_at_horizon,
        break_even_at_target=break_even_at_target,
        nzd_available=nzd_available,
    )

    warnings = list(decision.warnings)
    if daily_cost <= 0:
        warnings.append("No interest-based cost of waiting: this obligation accrues nothing.")
    if obligation.target_rate is None:
        warnings.append("No target rate set, so FX gain at target cannot be calculated.")

    return ObligationAnalysis(
        name=obligation.name,
        obligation_type=obligation.obligation_type,
        remaining_nzd=remaining,
        daily_cost_nzd=daily_cost,
        weekly_cost_nzd=weekly_cost,
        monthly_cost_nzd=monthly_cost,
        annual_cost_nzd=annual_cost,
        has_interest_cost=daily_cost > 0,
        usd_required_now=usd_now,
        rate_used=rate.rate,
        rate_stale=rate.stale,
        rate_quality=rate.quality,
        gain_at_improvement=gain_at_improvement,
        gain_at_target_nzd=gain_at_target,
        waiting=waiting,
        break_even_days_at_improvement=break_even_at_improvement,
        break_even_rate_after=break_even_rate_after,
        break_even_days_at_target=break_even_at_target,
        days_until_due=days_to_due,
        overdue=days_to_due is not None and days_to_due < 0,
        priority=breakdown,
        financial_score=quantize_display(breakdown.financial_total),
        overall_score=quantize_display(breakdown.overall_total),
        action=decision.action,
        reason=decision.reason,
        warnings=warnings,
    )
