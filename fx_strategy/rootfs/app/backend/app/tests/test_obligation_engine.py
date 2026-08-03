"""The obligation engine.

The two worked scenarios from the specification are asserted figure by figure;
the rest covers the edges where a plausible-looking wrong answer would be worse
than no answer — zero interest, stale rates, missing targets, overdue dates.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services.obligation_engine import (
    InterestBasis,
    ObligationInput,
    ObligationType,
    Priority,
    RateContext,
    RecommendedAction,
    Relationship,
    analyse,
    break_even_days,
    break_even_rate,
    effective_daily_rate,
    fx_gain,
    net_waiting_benefit,
    remaining_amount,
    usd_required,
    waiting_cost,
)

TODAY = date(2026, 8, 2)
RATE = Decimal("1.72")

#: Rounding tolerance for the figures quoted in the specification.
CENT = Decimal("0.01")


def live(rate: Decimal = RATE) -> RateContext:
    return RateContext(rate=rate, stale=False, as_of="2026-08-02T00:00:00Z")


# ---------------------------------------------------------------------------
# The worked scenarios
# ---------------------------------------------------------------------------


MORTGAGE_OFFSET = ObligationInput(
    name="Mortgage offset",
    obligation_type=ObligationType.OFFSET_LOAN,
    total_nzd=Decimal("256000"),
    annual_rate=Decimal("0.0604"),
    interest_basis=InterestBasis.SIMPLE_ANNUAL,
    priority=Priority.NORMAL,
    relationship=Relationship.NONE,
    partial_allowed=True,
)

MEIKA = ObligationInput(
    name="Meika repayment",
    obligation_type=ObligationType.INTEREST_FREE_LOAN,
    total_nzd=Decimal("70000"),
    annual_rate=Decimal(0),
    interest_basis=InterestBasis.NONE,
    priority=Priority.HIGH,
    relationship=Relationship.HIGH,
    partial_allowed=False,
)


class TestMortgageOffset:
    """NZ$256,000 at 6.04%, from the specification."""

    def test_the_interest_costs_match_the_specification(self) -> None:
        result = analyse(MORTGAGE_OFFSET, live(), today=TODAY)

        assert result.annual_cost_nzd == Decimal("15462.4000")
        assert abs(result.daily_cost_nzd - Decimal("42.36")) < CENT
        # Monthly is the annual figure over twelve, not a 30-day slice.
        assert abs(result.monthly_cost_nzd - Decimal("1288.53")) < CENT
        assert result.has_interest_cost is True

    def test_a_one_cent_improvement_is_worth_about_1488_nzd(self) -> None:
        result = analyse(MORTGAGE_OFFSET, live(), today=TODAY)

        # 256,000 / 1.72 = 148,837.21 USD required.
        assert abs(result.usd_required_now - Decimal("148837.21")) < CENT  # type: ignore[operator]
        gain = result.gain_at_improvement["0.01"]
        assert gain is not None
        assert abs(gain - Decimal("1488.37")) < Decimal("1")

    def test_a_one_cent_improvement_offsets_about_35_days_of_interest(self) -> None:
        result = analyse(MORTGAGE_OFFSET, live(), today=TODAY)

        days = result.break_even_days_at_improvement["0.01"]
        assert days is not None
        assert abs(days - Decimal(35)) < Decimal(1)

    def test_the_half_cent_improvement_is_half_the_gain(self) -> None:
        result = analyse(MORTGAGE_OFFSET, live(), today=TODAY)

        half = result.gain_at_improvement["0.005"]
        full = result.gain_at_improvement["0.01"]
        assert half is not None and full is not None
        assert abs(full - half * 2) < CENT


class TestMeikaRepayment:
    """NZ$70,000, interest-free, high relationship importance."""

    def test_waiting_costs_nothing_financially(self) -> None:
        result = analyse(MEIKA, live(), today=TODAY)

        assert result.daily_cost_nzd == Decimal("0.0000")
        assert result.annual_cost_nzd == Decimal("0.0000")
        assert result.has_interest_cost is False
        assert all(
            outcome.waiting_cost_nzd == Decimal("0.0000") for outcome in result.waiting.values()
        )

    def test_there_is_no_financial_break_even_period(self) -> None:
        """Not zero, not infinity — the concept does not apply."""
        result = analyse(MEIKA, live(), today=TODAY)

        assert result.break_even_days_at_improvement["0.01"] is None
        assert result.break_even_days_at_target is None
        assert any("no interest-based cost" in warning.lower() for warning in result.warnings)

    def test_the_overall_priority_exceeds_the_financial_priority(self) -> None:
        """The whole point: an interest-free family loan still matters."""
        result = analyse(MEIKA, live(), today=TODAY, portfolio_total_nzd=Decimal("326000"))

        assert result.overall_score > result.financial_score
        # The gap is exactly the non-financial part.
        assert result.priority.user_priority == Decimal(30)
        assert result.priority.relationship == Decimal(35)

    def test_it_outranks_a_larger_interest_bearing_debt_overall(self) -> None:
        total = Decimal("326000")
        meika = analyse(MEIKA, live(), today=TODAY, portfolio_total_nzd=total)
        mortgage = analyse(MORTGAGE_OFFSET, live(), today=TODAY, portfolio_total_nzd=total)

        # The mortgage is financially more urgent...
        assert mortgage.financial_score > meika.financial_score
        # ...but the family loan wins once relationship and priority are counted.
        assert meika.overall_score > mortgage.overall_score

    def test_the_recommendation_does_not_rest_on_interest(self) -> None:
        result = analyse(MEIKA, live(), today=TODAY)

        assert result.action in {
            RecommendedAction.WAIT_FOR_TARGET,
            RecommendedAction.WAIT_WITH_DEADLINE,
        }
        assert "no interest" in result.reason.lower()


# ---------------------------------------------------------------------------
# The arithmetic on its own
# ---------------------------------------------------------------------------


def test_waiting_cost_follows_the_specified_formula() -> None:
    # A x r x d / 365 with A=100000, r=0.05, d=73 -> 1000
    daily = Decimal("0.05") / Decimal(365)
    assert waiting_cost(Decimal("100000"), daily, 73) == Decimal("1000.0000")


def test_usd_required_is_the_amount_over_the_rate() -> None:
    assert usd_required(Decimal("172000"), Decimal("1.72")) == Decimal("100000.0000")


def test_usd_required_is_none_without_a_rate() -> None:
    assert usd_required(Decimal("172000"), None) is None


def test_fx_gain_can_be_negative_when_the_target_is_worse() -> None:
    """A worse target is reported honestly rather than floored at zero."""
    gain = fx_gain(Decimal("100000"), Decimal("1.72"), Decimal("1.70"))
    assert gain == Decimal("-2000.0000")


def test_net_benefit_is_gain_minus_cost() -> None:
    assert net_waiting_benefit(Decimal("1500"), Decimal("1200")) == Decimal("300.0000")


def test_break_even_days_is_none_when_nothing_accrues() -> None:
    assert break_even_days(Decimal("1500"), Decimal(0)) is None


def test_break_even_rate_is_the_rate_that_repays_the_wait() -> None:
    # Waiting costs 1000 NZD on 100000 USD, so a 0.01 improvement repays it.
    result = break_even_rate(Decimal("1.72"), Decimal("1000"), Decimal("100000"))
    assert result == Decimal("1.73000000")


def test_break_even_rate_is_none_without_a_rate() -> None:
    assert break_even_rate(None, Decimal("1000"), Decimal("100000")) is None


class TestInterestBasis:
    def test_a_manual_daily_rate_is_used_as_given(self) -> None:
        obligation = ObligationInput(
            name="Daily",
            interest_basis=InterestBasis.DAILY_MANUAL,
            daily_rate=Decimal("0.0002"),
            annual_rate=Decimal("0.99"),
            total_nzd=Decimal("10000"),
        )
        # The annual field is ignored entirely.
        assert effective_daily_rate(obligation) == Decimal("0.0002")

    def test_none_means_none_even_with_a_rate_entered(self) -> None:
        """An obligation marked interest-free cannot accrue by accident."""
        obligation = ObligationInput(
            name="Free",
            interest_basis=InterestBasis.NONE,
            annual_rate=Decimal("0.10"),
            total_nzd=Decimal("10000"),
        )
        assert effective_daily_rate(obligation) == Decimal(0)
        assert analyse(obligation, live(), today=TODAY).daily_cost_nzd == Decimal("0.0000")


# ---------------------------------------------------------------------------
# Funding, dates and states
# ---------------------------------------------------------------------------


def test_partial_funding_reduces_the_remaining_amount() -> None:
    obligation = ObligationInput(
        name="Half paid",
        total_nzd=Decimal("100000"),
        amount_funded_nzd=Decimal("40000"),
        annual_rate=Decimal("0.06"),
    )
    result = analyse(obligation, live(), today=TODAY)

    assert result.remaining_nzd == Decimal("60000.0000")
    # And the cost follows the remaining balance, not the original.
    assert result.annual_cost_nzd == Decimal("3600.0000")


def test_the_remaining_amount_can_be_overridden() -> None:
    obligation = ObligationInput(
        name="Odd",
        total_nzd=Decimal("100000"),
        amount_funded_nzd=Decimal("40000"),
        remaining_override_nzd=Decimal("55000"),
    )
    assert remaining_amount(obligation) == Decimal("55000.0000")


def test_overfunding_does_not_produce_a_negative_balance() -> None:
    obligation = ObligationInput(
        name="Over",
        total_nzd=Decimal("100"),
        amount_funded_nzd=Decimal("150"),
    )
    assert remaining_amount(obligation) == Decimal("0.0000")


def test_a_fully_funded_obligation_reports_funded() -> None:
    obligation = ObligationInput(
        name="Done", total_nzd=Decimal("1000"), amount_funded_nzd=Decimal("1000")
    )
    result = analyse(obligation, live(), today=TODAY)

    assert result.action == RecommendedAction.FUNDED


def test_a_completed_obligation_reports_funded_whatever_the_balance() -> None:
    obligation = ObligationInput(name="Closed", total_nzd=Decimal("1000"), completed=True)
    assert analyse(obligation, live(), today=TODAY).action == RecommendedAction.FUNDED


def test_an_overdue_obligation_says_how_late_it_is() -> None:
    obligation = ObligationInput(
        name="Late",
        total_nzd=Decimal("5000"),
        annual_rate=Decimal("0.2"),
        due_date=date(2026, 7, 26),
    )
    result = analyse(obligation, live(), today=TODAY)

    assert result.action == RecommendedAction.OVERDUE
    assert result.overdue is True
    assert result.days_until_due == -7
    assert "7 day(s) ago" in result.reason


def test_a_due_date_within_a_week_forces_conversion() -> None:
    obligation = ObligationInput(
        name="Soon",
        total_nzd=Decimal("5000"),
        annual_rate=Decimal("0.05"),
        due_date=date(2026, 8, 6),
        target_rate=Decimal("1.80"),
    )
    result = analyse(obligation, live(), today=TODAY)

    assert result.action == RecommendedAction.CONVERT_NOW
    assert "4 day(s)" in result.reason


def test_available_nzd_means_pay_rather_than_convert() -> None:
    obligation = ObligationInput(name="Payable", total_nzd=Decimal("5000"))
    result = analyse(obligation, live(), today=TODAY, nzd_available=Decimal("6000"))

    assert result.action == RecommendedAction.PAY_NOW
    assert "no conversion is needed" in result.reason.lower()


def test_a_critical_obligation_converts_now() -> None:
    obligation = ObligationInput(
        name="Critical",
        total_nzd=Decimal("5000"),
        annual_rate=Decimal("0.05"),
        priority=Priority.CRITICAL,
        target_rate=Decimal("1.90"),
    )
    assert analyse(obligation, live(), today=TODAY).action == RecommendedAction.CONVERT_NOW


# ---------------------------------------------------------------------------
# Rate availability
# ---------------------------------------------------------------------------


def test_a_stale_rate_never_recommends_waiting() -> None:
    """The comparison rests on the rate being current. If it is not, say so."""
    obligation = ObligationInput(
        name="Patient",
        total_nzd=Decimal("100000"),
        annual_rate=Decimal("0.06"),
        target_rate=Decimal("1.80"),
    )
    stale = RateContext(rate=RATE, stale=True, as_of="2026-07-01T00:00:00Z")
    result = analyse(obligation, stale, today=TODAY)

    assert result.action == RecommendedAction.REVIEW
    assert "stale" in result.reason.lower()
    assert result.rate_stale is True


def test_a_missing_rate_reports_review_rather_than_guessing() -> None:
    obligation = ObligationInput(name="No rate", total_nzd=Decimal("1000"))
    result = analyse(obligation, RateContext(rate=None), today=TODAY)

    assert result.action == RecommendedAction.REVIEW
    assert result.usd_required_now is None
    assert result.gain_at_improvement["0.01"] is None


def test_a_missing_target_rate_is_stated_not_assumed() -> None:
    obligation = ObligationInput(
        name="No target", total_nzd=Decimal("100000"), annual_rate=Decimal("0.06")
    )
    result = analyse(obligation, live(), today=TODAY)

    assert result.gain_at_target_nzd is None
    assert any("no target rate" in warning.lower() for warning in result.warnings)


def test_the_rate_quality_is_carried_through_and_not_conflated() -> None:
    obligation = ObligationInput(name="Quality", total_nzd=Decimal("1000"))
    quoted = RateContext(rate=RATE, quality="wise_quote")
    assert analyse(obligation, quoted, today=TODAY).rate_quality == "wise_quote"


# ---------------------------------------------------------------------------
# Waiting horizons and precision
# ---------------------------------------------------------------------------


def test_every_standard_horizon_is_reported() -> None:
    result = analyse(MORTGAGE_OFFSET, live(), today=TODAY)
    assert sorted(result.waiting) == [7, 14, 30, 60, 90]


def test_a_very_short_wait_still_produces_a_figure() -> None:
    obligation = ObligationInput(
        name="Brief", total_nzd=Decimal("256000"), annual_rate=Decimal("0.0604")
    )
    result = analyse(obligation, live(), today=TODAY, horizons=(1,))

    assert abs(result.waiting[1].waiting_cost_nzd - Decimal("42.36")) < CENT


def test_break_even_rates_are_quoted_for_seven_and_thirty_days() -> None:
    result = analyse(MORTGAGE_OFFSET, live(), today=TODAY)

    seven = result.break_even_rate_after[7]
    thirty = result.break_even_rate_after[30]
    assert seven is not None and thirty is not None
    # Longer waits need a better rate to justify them.
    assert thirty > seven > RATE


def test_the_user_defined_maximum_wait_gets_its_own_break_even_rate() -> None:
    obligation = ObligationInput(
        name="Limited",
        total_nzd=Decimal("256000"),
        annual_rate=Decimal("0.0604"),
        max_wait_days=45,
    )
    result = analyse(obligation, live(), today=TODAY)

    assert 45 in result.break_even_rate_after
    assert result.break_even_rate_after[45] is not None


def test_calculations_stay_in_decimal_throughout() -> None:
    """No float may appear anywhere in a money or rate figure."""
    result = analyse(MORTGAGE_OFFSET, live(), today=TODAY)

    values = [
        result.remaining_nzd,
        result.daily_cost_nzd,
        result.weekly_cost_nzd,
        result.monthly_cost_nzd,
        result.annual_cost_nzd,
        result.usd_required_now,
        *result.gain_at_improvement.values(),
        *(outcome.waiting_cost_nzd for outcome in result.waiting.values()),
        *result.break_even_rate_after.values(),
    ]
    for value in values:
        assert value is None or isinstance(value, Decimal)


def test_precision_survives_a_repeating_division() -> None:
    """256,000 / 1.72 repeats; the result must not drift into binary float."""
    obligation = ObligationInput(name="Precision", total_nzd=Decimal("256000"))
    result = analyse(obligation, live(), today=TODAY)

    assert result.usd_required_now == Decimal("148837.2093")
    # Multiplying back lands within a cent of the original.
    assert abs(result.usd_required_now * RATE - Decimal("256000")) < CENT  # type: ignore[operator]


@pytest.mark.parametrize(
    ("priority", "expected"),
    [
        (Priority.CRITICAL, Decimal(60)),
        (Priority.HIGH, Decimal(30)),
        (Priority.NORMAL, Decimal(10)),
        (Priority.LOW, Decimal(0)),
    ],
)
def test_the_priority_weighting_is_explicit(priority: Priority, expected: Decimal) -> None:
    obligation = ObligationInput(name="P", total_nzd=Decimal("1000"), priority=priority)
    result = analyse(obligation, live(), today=TODAY)

    assert result.priority.user_priority == expected


def test_the_score_components_are_all_shown() -> None:
    """No opaque number: every part of the score is available for inspection."""
    result = analyse(MORTGAGE_OFFSET, live(), today=TODAY)
    components = result.priority.components()

    assert set(components) == {
        "due_urgency",
        "user_priority",
        "relationship",
        "interest_cost",
        "size",
        "max_wait",
        "partial_flexibility",
    }
    assert sum(components.values()) == result.overall_score
