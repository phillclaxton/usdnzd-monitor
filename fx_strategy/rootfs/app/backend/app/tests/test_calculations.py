"""Financial calculation tests.

The worked example from the product specification — USD 800,000 across a
five-step ladder producing NZD 1,409,600 at a blended 1.7620 — is asserted here
figure by figure. If any of these move, a user's expected proceeds have moved
with them.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.money import MoneyError
from app.services.calculations import (
    AllocationRequest,
    AllocationType,
    AmountQuality,
    CompletedConversion,
    DeadlineSeverity,
    FeeAssumption,
    FeeType,
    RateZone,
    ScenarioLeg,
    allocate,
    analyse_walk_away,
    blended_effective_rate,
    blended_gross_rate,
    classify_rate,
    compare_against,
    convert_all_now_scenario,
    deadline_severity,
    effective_rate,
    equal_schedule_scenario,
    estimate_fee,
    evaluate_scenario,
    gross_proceeds,
    ladder_scenario,
    movement_value,
    net_proceeds,
    percent_converted,
    project_conversion,
    remaining_source_amount,
    required_conversion_shortfall,
    sensitivity_table,
    target_upside,
    total_fees,
    validate_conversion_amounts,
    value_of_one_cent,
)

TOTAL = Decimal("800000")

#: The recommended ladder: (percentage, target rate, expected USD, expected NZD).
LADDER = [
    (Decimal("15"), Decimal("1.7200"), Decimal("120000"), Decimal("206400")),
    (Decimal("20"), Decimal("1.7400"), Decimal("160000"), Decimal("278400")),
    (Decimal("25"), Decimal("1.7600"), Decimal("200000"), Decimal("352000")),
    (Decimal("20"), Decimal("1.7800"), Decimal("160000"), Decimal("284800")),
    (Decimal("20"), Decimal("1.8000"), Decimal("160000"), Decimal("288000")),
]


# ---------------------------------------------------------------------------
# The specification's worked example
# ---------------------------------------------------------------------------


def test_recommended_ladder_allocates_the_documented_amounts() -> None:
    report = allocate(
        TOTAL,
        [
            AllocationRequest(index + 1, AllocationType.PERCENTAGE, percentage)
            for index, (percentage, _rate, _usd, _nzd) in enumerate(LADDER)
        ],
    )
    assert report.valid
    assert report.fully_allocated
    assert report.percentage_total == Decimal("100.000000")
    assert [result.source_amount for result in report.allocations] == [
        expected_usd.quantize(Decimal("0.0001")) for _p, _r, expected_usd, _n in LADDER
    ]


def test_recommended_ladder_produces_the_documented_gross_proceeds() -> None:
    proceeds = [gross_proceeds(expected_usd, rate) for _p, rate, expected_usd, _nzd in LADDER]
    assert proceeds == [expected_nzd.quantize(Decimal("0.0001")) for *_x, expected_nzd in LADDER]
    assert sum(proceeds) == Decimal("1409600.0000")


def test_recommended_ladder_blends_to_the_documented_rate() -> None:
    conversions = [
        CompletedConversion(
            source_amount=usd, target_amount=gross_proceeds(usd, rate), gross_rate=rate
        )
        for _p, rate, usd, _nzd in LADDER
    ]
    assert blended_gross_rate(conversions) == Decimal("1.76200000")
    assert blended_effective_rate(conversions) == Decimal("1.76200000")


def test_one_cent_on_the_full_position_is_eight_thousand() -> None:
    assert value_of_one_cent(TOTAL) == Decimal("8000.0000")


@pytest.mark.parametrize(
    ("movement", "expected"),
    [
        ("0.0050", "4000.0000"),
        ("0.0100", "8000.0000"),
        ("0.0200", "16000.0000"),
        ("0.0300", "24000.0000"),
        ("0.0500", "40000.0000"),
        ("0.1000", "80000.0000"),
    ],
)
def test_documented_sensitivity_figures(movement: str, expected: str) -> None:
    assert movement_value(TOTAL, Decimal(movement)) == Decimal(expected)


def test_sensitivity_recalculates_on_the_remaining_amount_only() -> None:
    remaining = remaining_source_amount(TOTAL, Decimal("480000"))
    assert remaining == Decimal("320000.0000")
    assert value_of_one_cent(remaining) == Decimal("3200.0000")
    rows = sensitivity_table(remaining)
    assert rows[1].downside == Decimal("-3200.0000")
    assert rows[1].upside == Decimal("3200.0000")


# ---------------------------------------------------------------------------
# Proceeds and effective rate
# ---------------------------------------------------------------------------


def test_gross_net_and_effective_rate() -> None:
    gross = gross_proceeds(Decimal("200000"), Decimal("1.7604"))
    assert gross == Decimal("352080.0000")
    net = net_proceeds(gross, Decimal("1020"))
    assert net == Decimal("351060.0000")
    assert effective_rate(net, Decimal("200000")) == Decimal("1.75530000")


def test_net_is_none_when_the_fee_is_unknown() -> None:
    assert net_proceeds(Decimal("352080"), None) is None
    assert effective_rate(None, Decimal("200000")) is None


def test_effective_rate_of_a_zero_conversion_is_none_not_zero() -> None:
    assert effective_rate(Decimal("100"), Decimal("0")) is None


# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------


def test_no_fee_model_reports_that_fees_are_excluded() -> None:
    estimate = estimate_fee(None, Decimal("200000"), Decimal("1.76"))
    assert estimate.available is False
    assert estimate.amount_source_currency is None
    assert estimate.label == "Fee not included"


def test_quote_only_model_does_not_invent_a_number() -> None:
    estimate = estimate_fee(
        FeeAssumption(fee_type=FeeType.QUOTE_ONLY), Decimal("200000"), Decimal("1.76")
    )
    assert estimate.available is False
    assert "live quote" in estimate.basis


def test_percentage_fee() -> None:
    estimate = estimate_fee(
        FeeAssumption(fee_type=FeeType.PERCENTAGE, percentage_fee=Decimal("0.41")),
        Decimal("200000"),
        Decimal("1.76"),
    )
    assert estimate.amount_source_currency == Decimal("820.0000")
    assert estimate.amount_target_currency == Decimal("1443.2000")
    assert estimate.quality is AmountQuality.ESTIMATE


def test_fixed_plus_percentage_with_bounds() -> None:
    assumption = FeeAssumption(
        fee_type=FeeType.FIXED_PLUS_PERCENTAGE,
        fixed_fee=Decimal("5"),
        percentage_fee=Decimal("0.5"),
        minimum_fee=Decimal("10"),
        maximum_fee=Decimal("400"),
    )
    small = estimate_fee(assumption, Decimal("100"), Decimal("1.76"))
    assert small.amount_source_currency == Decimal("10.0000")  # minimum applied

    large = estimate_fee(assumption, Decimal("500000"), Decimal("1.76"))
    assert large.amount_source_currency == Decimal("400.0000")  # maximum applied


def test_fee_without_a_rate_has_no_target_currency_figure() -> None:
    estimate = estimate_fee(
        FeeAssumption(fee_type=FeeType.PERCENTAGE, percentage_fee=Decimal("0.5")),
        Decimal("1000"),
        None,
    )
    assert estimate.amount_source_currency == Decimal("5.0000")
    assert estimate.amount_target_currency is None


def test_project_conversion_carries_the_estimate_label() -> None:
    outcome = project_conversion(
        Decimal("200000"),
        Decimal("1.7600"),
        FeeAssumption(fee_type=FeeType.PERCENTAGE, percentage_fee=Decimal("0.41")),
    )
    assert outcome.gross_target_amount == Decimal("352000.0000")
    assert outcome.net_target_amount == Decimal("350556.8000")
    assert outcome.quality is AmountQuality.ESTIMATE


def test_projection_without_a_fee_model_reports_no_net() -> None:
    outcome = project_conversion(Decimal("200000"), Decimal("1.76"), None)
    assert outcome.gross_target_amount == Decimal("352000.0000")
    assert outcome.net_target_amount is None
    assert outcome.fee.label == "Fee not included"


# ---------------------------------------------------------------------------
# Blended rates and remaining balance
# ---------------------------------------------------------------------------


def test_blended_rate_of_nothing_is_none() -> None:
    assert blended_gross_rate([]) is None
    assert blended_effective_rate([]) is None


def test_blended_effective_rate_reflects_fees_taken_on_the_target_side() -> None:
    conversions = [
        CompletedConversion(Decimal("120000"), Decimal("206400"), Decimal("1.7200")),
        # 160,000 at 1.7400 is 278,400 gross; 277,900 actually arrived.
        CompletedConversion(Decimal("160000"), Decimal("277900"), Decimal("1.7400")),
    ]
    assert blended_gross_rate(conversions) == Decimal("1.73142857")
    assert blended_effective_rate(conversions) == Decimal("1.72964286")


def test_total_fees_is_none_when_none_were_recorded() -> None:
    conversions = [CompletedConversion(Decimal("100"), Decimal("175"), Decimal("1.75"))]
    assert total_fees(conversions) is None
    with_fees = [
        CompletedConversion(Decimal("100"), Decimal("175"), Decimal("1.75"), Decimal("2.50"))
    ]
    assert total_fees(with_fees) == Decimal("2.5000")


def test_remaining_never_goes_negative() -> None:
    assert remaining_source_amount(TOTAL, Decimal("900000")) == Decimal("0.0000")


def test_percent_converted() -> None:
    assert percent_converted(Decimal("200000"), TOTAL) == Decimal("25.000000")
    assert percent_converted(Decimal("0"), Decimal("0")) is None


# ---------------------------------------------------------------------------
# Upside and downside
# ---------------------------------------------------------------------------


def test_target_upside() -> None:
    assert target_upside(Decimal("320000"), Decimal("1.8000"), Decimal("1.7800")) == Decimal(
        "6400.0000"
    )


def test_target_upside_is_negative_when_the_target_is_already_behind() -> None:
    assert target_upside(Decimal("100000"), Decimal("1.70"), Decimal("1.76")) == Decimal(
        "-6000.0000"
    )


def test_target_upside_without_a_rate_is_none() -> None:
    assert target_upside(Decimal("100000"), Decimal("1.80"), None) is None


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------


def test_fixed_amounts_and_a_remainder_split_what_is_left() -> None:
    report = allocate(
        Decimal("800000"),
        [
            AllocationRequest(1, AllocationType.FIXED_AMOUNT, Decimal("100000")),
            AllocationRequest(2, AllocationType.PERCENTAGE, Decimal("25")),
            AllocationRequest(3, AllocationType.REMAINDER, Decimal("0")),
            AllocationRequest(4, AllocationType.REMAINDER, Decimal("0")),
        ],
    )
    assert report.valid
    amounts = [result.source_amount for result in report.allocations]
    assert amounts[0] == Decimal("100000.0000")
    assert amounts[1] == Decimal("200000.0000")
    # 500,000 left, split between the two remainder tranches.
    assert amounts[2] == Decimal("250000.0000")
    assert amounts[3] == Decimal("250000.0000")
    assert report.fully_allocated


def test_rounding_residue_is_pushed_onto_the_last_percentage_tranche() -> None:
    # Three equal thirds of 100 cannot be represented exactly.
    report = allocate(
        Decimal("100"),
        [
            AllocationRequest(1, AllocationType.PERCENTAGE, Decimal("33.333333")),
            AllocationRequest(2, AllocationType.PERCENTAGE, Decimal("33.333333")),
            AllocationRequest(3, AllocationType.PERCENTAGE, Decimal("33.333334")),
        ],
    )
    assert report.valid
    assert sum(result.source_amount for result in report.allocations) == Decimal("100.0000")
    assert report.unallocated == Decimal("0.0000")


def test_percentages_over_one_hundred_are_rejected() -> None:
    report = allocate(
        TOTAL,
        [
            AllocationRequest(1, AllocationType.PERCENTAGE, Decimal("60")),
            AllocationRequest(2, AllocationType.PERCENTAGE, Decimal("50")),
        ],
    )
    assert not report.valid
    assert "more than 100%" in report.errors[0]


def test_fixed_amounts_over_the_total_are_rejected() -> None:
    report = allocate(
        Decimal("100000"),
        [AllocationRequest(1, AllocationType.FIXED_AMOUNT, Decimal("200000"))],
    )
    assert not report.valid


def test_a_deliberate_reserve_is_a_warning_not_an_error() -> None:
    report = allocate(
        TOTAL,
        [
            AllocationRequest(1, AllocationType.PERCENTAGE, Decimal("50")),
            AllocationRequest(2, AllocationType.PERCENTAGE, Decimal("30")),
        ],
    )
    assert report.valid
    assert report.unallocated == Decimal("160000.0000")
    assert report.warnings and "unallocated" in report.warnings[0]


def test_zero_and_negative_allocations_are_rejected() -> None:
    report = allocate(TOTAL, [AllocationRequest(1, AllocationType.PERCENTAGE, Decimal("0"))])
    assert not report.valid
    assert "more than zero" in report.errors[0]


def test_a_zero_total_is_rejected() -> None:
    report = allocate(
        Decimal("0"), [AllocationRequest(1, AllocationType.PERCENTAGE, Decimal("100"))]
    )
    assert not report.valid


def test_allocation_order_follows_sequence_not_input_order() -> None:
    report = allocate(
        Decimal("1000"),
        [
            AllocationRequest(2, AllocationType.PERCENTAGE, Decimal("70")),
            AllocationRequest(1, AllocationType.PERCENTAGE, Decimal("30")),
        ],
    )
    assert [result.sequence for result in report.allocations] == [1, 2]
    assert report.allocations[0].source_amount == Decimal("300.0000")


# ---------------------------------------------------------------------------
# Rate zones
# ---------------------------------------------------------------------------


ZONES = [
    RateZone("Unfavourable", "Avoid discretionary conversion", None),
    RateZone("Weak", "Convert only amounts required soon", Decimal("1.6800")),
    RateZone("Acceptable", "Begin smaller staged conversions", Decimal("1.7000")),
    RateZone("Good", "Convert meaningful tranches", Decimal("1.7300")),
    RateZone("Very good", "Convert more aggressively", Decimal("1.7600")),
    RateZone("Excellent", "Strongly consider completing conversion", Decimal("1.7800")),
]


@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        ("1.6700", "Unfavourable"),
        ("1.6800", "Weak"),
        ("1.6999", "Weak"),
        ("1.7000", "Acceptable"),
        ("1.7299", "Acceptable"),
        ("1.7300", "Good"),
        ("1.7599", "Good"),
        ("1.7600", "Very good"),
        ("1.7799", "Very good"),
        ("1.7800", "Excellent"),
        ("2.0000", "Excellent"),
    ],
)
def test_zone_boundaries_match_the_specification(rate: str, expected: str) -> None:
    zone = classify_rate(Decimal(rate), ZONES)
    assert zone is not None
    assert zone.label == expected


def test_zone_classification_without_a_rate_or_zones() -> None:
    assert classify_rate(None, ZONES) is None
    assert classify_rate(Decimal("1.76"), []) is None


# ---------------------------------------------------------------------------
# Walk-away analysis
# ---------------------------------------------------------------------------


def test_walk_away_shows_both_sides_of_the_trade_off() -> None:
    completed = [CompletedConversion(Decimal("480000"), Decimal("835200"), Decimal("1.7400"))]
    analysis = analyse_walk_away(
        remaining_source=Decimal("320000"),
        current_rate=Decimal("1.7800"),
        completed=completed,
        outstanding_targets=[Decimal("1.8000")],
        next_target=Decimal("1.8000"),
        assumption=None,
    )
    assert analysis.convert_now is not None
    assert analysis.convert_now.gross_target_amount == Decimal("569600.0000")
    assert analysis.existing_blended_rate == Decimal("1.74000000")
    # Converting the remainder now lifts the blended rate from 1.74 to 1.7560.
    assert analysis.blended_if_converted_now == Decimal("1.75600000")
    # Waiting for 1.8000 would add 6,400 before fees...
    assert analysis.difference_versus_waiting == Decimal("6400.0000")
    # ...while leaving the whole 320,000 exposed: 3,200 per cent of movement.
    assert analysis.sensitivity[1].downside == Decimal("-3200.0000")
    assert analysis.rate_movement_to_next_target == Decimal("0.02000000")


def test_walk_away_without_a_rate_reports_nothing_rather_than_guessing() -> None:
    analysis = analyse_walk_away(
        remaining_source=Decimal("320000"),
        current_rate=None,
        completed=[],
        outstanding_targets=[],
        next_target=None,
        assumption=None,
    )
    assert analysis.convert_now is None
    assert analysis.difference_versus_waiting is None
    assert analysis.blended_if_converted_now is None


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (365, DeadlineSeverity.NORMAL),
        (91, DeadlineSeverity.NORMAL),
        (90, DeadlineSeverity.NOTICE),
        (31, DeadlineSeverity.NOTICE),
        (30, DeadlineSeverity.REVIEW),
        (15, DeadlineSeverity.REVIEW),
        (14, DeadlineSeverity.WARNING),
        (8, DeadlineSeverity.WARNING),
        (7, DeadlineSeverity.CRITICAL),
        (0, DeadlineSeverity.CRITICAL),
        (-1, DeadlineSeverity.OVERDUE),
    ],
)
def test_deadline_bands(days: int, expected: DeadlineSeverity) -> None:
    severity, _message = deadline_severity(days)
    assert severity is expected


def test_no_deadline_is_normal() -> None:
    severity, message = deadline_severity(None)
    assert severity is DeadlineSeverity.NORMAL
    assert "No deadline" in message


def test_requirement_shortfall_clamps_at_zero() -> None:
    assert required_conversion_shortfall(Decimal("200000"), Decimal("50000")) == Decimal(
        "150000.0000"
    )
    assert required_conversion_shortfall(Decimal("200000"), Decimal("250000")) == Decimal("0.0000")


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def test_convert_all_now_scenario() -> None:
    scenario = convert_all_now_scenario(TOTAL, Decimal("1.7550"), None)
    assert scenario.gross_target_amount == Decimal("1404000.0000")
    assert scenario.blended_rate == Decimal("1.75500000")
    assert scenario.exposed_source_amount == Decimal("0")  # nothing left exposed
    assert scenario.one_cent_exposure == Decimal("0.0000")


def test_ladder_scenario_matches_the_documented_totals() -> None:
    scenario = ladder_scenario([(usd, rate) for _p, rate, usd, _n in LADDER], None)
    assert scenario.gross_target_amount == Decimal("1409600.0000")
    assert scenario.blended_rate == Decimal("1.76200000")
    assert scenario.rate_required == Decimal("1.8000")
    # The ladder's whole position is exposed until every target is reached.
    assert scenario.exposed_source_amount == Decimal("800000.0000")
    assert any("never reached" in note for note in scenario.assumptions)


def test_equal_schedule_states_its_own_simplification() -> None:
    scenario = equal_schedule_scenario(TOTAL, Decimal("1.7550"), 4, None)
    assert len(scenario.legs) == 4
    assert sum(leg.source_amount for leg in scenario.legs) == Decimal("800000.0000")
    assert any("not a forecast" in note for note in scenario.assumptions)


def test_equal_schedule_handles_indivisible_totals() -> None:
    scenario = equal_schedule_scenario(Decimal("100"), Decimal("1.75"), 3, None)
    assert sum(leg.source_amount for leg in scenario.legs) == Decimal("100.0000")


def test_scenario_fee_is_converted_at_the_scenario_blended_rate() -> None:
    scenario = evaluate_scenario(
        key="x",
        name="x",
        description="",
        legs=[ScenarioLeg(Decimal("100000"), Decimal("1.76"), "a")],
        assumption=FeeAssumption(fee_type=FeeType.PERCENTAGE, percentage_fee=Decimal("1")),
    )
    assert scenario.fee.amount_source_currency == Decimal("1000.0000")
    assert scenario.fee.amount_target_currency == Decimal("1760.0000")
    assert scenario.net_target_amount == Decimal("174240.0000")


def test_comparison_against_an_alternative_rate() -> None:
    difference = compare_against(Decimal("1409600"), TOTAL, Decimal("1.7000"), None)
    # 800,000 at 1.70 would have been 1,360,000, so the ladder is 49,600 ahead.
    assert difference == Decimal("49600.0000")


def test_comparison_is_none_without_a_baseline() -> None:
    assert compare_against(Decimal("100"), TOTAL, None, None) is None
    assert compare_against(None, TOTAL, Decimal("1.7"), None) is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_conversion_validation_rejects_impossible_values() -> None:
    with pytest.raises(MoneyError, match="greater than zero"):
        validate_conversion_amounts(Decimal("0"), Decimal("100"), TOTAL)
    with pytest.raises(MoneyError, match="greater than zero"):
        validate_conversion_amounts(Decimal("-10"), Decimal("100"), TOTAL)
    with pytest.raises(MoneyError, match="received must be greater"):
        validate_conversion_amounts(Decimal("100"), Decimal("0"), TOTAL)


def test_conversion_beyond_the_remaining_balance_is_rejected_by_default() -> None:
    with pytest.raises(MoneyError, match="only"):
        validate_conversion_amounts(Decimal("900000"), Decimal("1"), TOTAL)


def test_a_correction_may_exceed_the_remaining_balance() -> None:
    validate_conversion_amounts(
        Decimal("900000"), Decimal("1"), TOTAL, allow_exceeding_remaining=True
    )
