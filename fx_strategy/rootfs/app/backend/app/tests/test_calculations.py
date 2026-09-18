"""Financial calculation tests.

What is left in ``calculations`` once the conversion ladder has gone is the
arithmetic of a conversion itself: what an amount is worth at a rate, what a
fee does to it, what a list of completed conversions blends to, and what a rate
movement is worth on what is still held. Every figure is asserted exactly,
because each one is money someone reads off a screen.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.money import MoneyError
from app.services.calculations import (
    AmountQuality,
    CompletedConversion,
    FeeAssumption,
    FeeType,
    RateZone,
    blended_effective_rate,
    blended_gross_rate,
    classify_rate,
    effective_rate,
    estimate_fee,
    gross_proceeds,
    net_proceeds,
    project_conversion,
    total_fees,
    validate_conversion_amounts,
)

TOTAL = Decimal("800000")

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
# Blended rates
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
