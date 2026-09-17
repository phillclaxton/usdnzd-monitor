"""The position arithmetic, on its own.

The figures are the ones in ``docs/examples/fx-state-example.json``: made up,
and chosen so every expected answer divides exactly. A wrong answer here is
meant to be obvious at a glance rather than something you have to reach for a
calculator to disbelieve.

Against a 1.6000 baseline the four example conversions give confirmed
10,732.50, estimated 6,000.00, total 16,732.50 — and the offset costs exactly
NZ$6.00 a day.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import position_math as pm
from app.services.position_math import ConversionFact

BASELINE = Decimal("1.6000")

#: Full data, fee recorded. 49,900 net x 0.10 improvement = 4,990.00
P1 = ConversionFact(
    source_amount=Decimal("50000.00"),
    target_amount=Decimal("84830.00"),
    fee_source_currency=Decimal("100.00"),
)
#: Full data, fee recorded. 24,950 net x 0.15 improvement = 3,742.50
P2 = ConversionFact(
    source_amount=Decimal("25000.00"),
    target_amount=Decimal("43662.50"),
    fee_source_currency=Decimal("50.00"),
)
#: Amount converted implied by algebra (34,000 / 1.7); the fee is genuinely
#: unrecorded. 20,000 x 0.10 = 2,000.00
P3 = ConversionFact(
    source_amount=Decimal("20000.00"),
    target_amount=Decimal("34000.00"),
    fee_source_currency=None,
)
#: The amount received is a reconstruction. 30,000 x 0.20 = 6,000.00
P4 = ConversionFact(
    source_amount=Decimal("30000.00"),
    target_amount=Decimal("54000.00"),
    fee_source_currency=None,
    amounts_estimated=True,
)
EXAMPLE = [P1, P2, P3, P4]


# ---------------------------------------------------------------------------
# A fee that was not recorded is not a fee of zero
# ---------------------------------------------------------------------------


def test_the_fee_comes_off_the_amount_that_actually_crossed() -> None:
    assert pm.net_converted(Decimal("50000.00"), Decimal("100.00")) == Decimal("49900.0000")


def test_an_unrecorded_fee_falls_back_to_the_gross_amount() -> None:
    """Not to a fee of zero — the difference is flagged, not assumed away."""
    assert pm.net_converted(Decimal("20000.00"), None) == Decimal("20000.0000")


@pytest.mark.parametrize(
    ("fact", "expected"),
    [
        (P1, Decimal("4990.0000")),
        (P2, Decimal("3742.5000")),
        (P3, Decimal("2000.0000")),
        (P4, Decimal("6000.0000")),
    ],
)
def test_each_example_conversion_gains_what_it_should(
    fact: ConversionFact, expected: Decimal
) -> None:
    assert pm.realised_improvement(fact, BASELINE) == expected


# ---------------------------------------------------------------------------
# None is not zero
# ---------------------------------------------------------------------------


def test_without_a_baseline_there_is_no_improvement_to_report() -> None:
    """``None`` so the UI can say "set a baseline rate" instead of "0.00"."""
    assert pm.realised_improvement(P1, None) is None
    assert pm.unrealised_improvement(Decimal("500000"), Decimal("1.7"), None) is None
    assert pm.current_value(Decimal("500000"), None) is None

    total = pm.cumulative_realised(EXAMPLE, None)
    assert (total.confirmed, total.estimated, total.total) == (None, None, None)
    assert total.includes_estimates is False


def test_a_total_is_never_a_partial_sum() -> None:
    """Half a total reads as though both halves were counted. It must not."""
    assert pm.total_improvement(Decimal("100"), None) is None
    assert pm.total_improvement(None, Decimal("100")) is None
    assert pm.total_improvement(Decimal("100"), Decimal("25")) == Decimal("125.0000")


@pytest.mark.parametrize(
    ("shortfall", "rate"),
    [(None, Decimal("0.06")), (Decimal("36500"), None), (None, None)],
)
def test_carrying_cost_is_unknown_rather_than_zero(
    shortfall: Decimal | None, rate: Decimal | None
) -> None:
    assert pm.daily_carrying_cost(shortfall, rate) is None
    assert pm.monthly_carrying_cost(shortfall, rate) is None


# ---------------------------------------------------------------------------
# Confirmed and estimated never merge silently
# ---------------------------------------------------------------------------


def test_the_example_reconciles_as_a_split_not_a_bare_total() -> None:
    total = pm.cumulative_realised(EXAMPLE, BASELINE)
    assert total.confirmed == Decimal("10732.5000")
    assert total.estimated == Decimal("6000.0000")
    assert total.total == Decimal("16732.5000")
    assert total.includes_estimates is True


def test_no_estimates_means_a_real_zero_not_an_unknown() -> None:
    """The absence of estimates is itself a fact, so it is stated as 0.00."""
    total = pm.cumulative_realised([P1, P2, P3], BASELINE)
    assert total.confirmed == Decimal("10732.5000")
    assert total.estimated == Decimal("0.0000")
    assert total.total == Decimal("10732.5000")
    assert total.includes_estimates is False


def test_confirming_an_estimate_collapses_the_split() -> None:
    """Entering the real receipt makes the distinction disappear on its own."""
    confirmed_p4 = ConversionFact(
        source_amount=P4.source_amount,
        target_amount=P4.target_amount,
        fee_source_currency=None,
        amounts_estimated=False,
    )
    total = pm.cumulative_realised([P1, P2, P3, confirmed_p4], BASELINE)
    assert total.includes_estimates is False
    assert total.estimated == Decimal("0.0000")
    assert total.confirmed == total.total == Decimal("16732.5000")


# ---------------------------------------------------------------------------
# Per-row improvement and the running total
# ---------------------------------------------------------------------------


def test_the_cumulative_column_counts_up_in_the_order_given() -> None:
    rows = pm.improvements(EXAMPLE, BASELINE)
    assert [row.improvement for row in rows] == [
        Decimal("4990.0000"),
        Decimal("3742.5000"),
        Decimal("2000.0000"),
        Decimal("6000.0000"),
    ]
    assert [row.cumulative for row in rows] == [
        Decimal("4990.0000"),
        Decimal("8732.5000"),
        Decimal("10732.5000"),
        Decimal("16732.5000"),
    ]


def test_a_row_says_when_its_fee_was_never_recorded() -> None:
    """Its improvement is measured on the gross amount, so it overstates."""
    rows = pm.improvements(EXAMPLE, BASELINE)
    assert [row.fee_unrecorded for row in rows] == [False, False, True, True]
    assert [row.estimated for row in rows] == [False, False, False, True]


def test_without_a_baseline_every_row_is_unknown() -> None:
    rows = pm.improvements(EXAMPLE, None)
    assert all(row.improvement is None and row.cumulative is None for row in rows)
    # The flags do not depend on the baseline and are still reported.
    assert [row.estimated for row in rows] == [False, False, False, True]


# ---------------------------------------------------------------------------
# Exposure and carrying cost
# ---------------------------------------------------------------------------


def test_the_remaining_balance_is_valued_at_the_current_rate() -> None:
    assert pm.current_value(Decimal("500000"), Decimal("1.7000")) == Decimal("850000.0000")


def test_the_unconverted_balance_gains_on_paper_against_the_baseline() -> None:
    assert pm.unrealised_improvement(Decimal("500000"), Decimal("1.7000"), BASELINE) == Decimal(
        "50000.0000"
    )


def test_a_rate_below_the_baseline_is_a_loss_not_an_error() -> None:
    assert pm.unrealised_improvement(Decimal("500000"), Decimal("1.5000"), BASELINE) == Decimal(
        "-50000.0000"
    )


def test_the_offset_costs_six_dollars_a_day() -> None:
    assert pm.daily_carrying_cost(Decimal("36500"), Decimal("0.0600")) == Decimal("6.0000")


def test_a_month_is_the_year_over_twelve_not_thirty_days() -> None:
    """Twelve months and one year have to agree, as obligation_engine already
    insists — a 30-day slice makes them differ by five days a year."""
    assert pm.monthly_carrying_cost(Decimal("36500"), Decimal("0.0600")) == Decimal("182.5000")
    annual = Decimal("36500") * Decimal("0.0600")
    assert pm.monthly_carrying_cost(Decimal("36500"), Decimal("0.0600")) * 12 == annual


def test_the_cost_is_charged_on_the_shortfall_not_the_whole_balance() -> None:
    """Money that was never going to the mortgage is not costing mortgage
    interest, so the whole position must not appear in this figure."""
    on_shortfall = pm.daily_carrying_cost(Decimal("36500"), Decimal("0.0600"))
    on_everything = pm.daily_carrying_cost(Decimal("850000"), Decimal("0.0600"))
    assert on_shortfall is not None and on_everything is not None
    assert on_shortfall < on_everything


def test_months_of_burn_divides_the_value_by_the_spend() -> None:
    assert pm.months_of_burn(Decimal("850000"), Decimal("4000")) == Decimal("212.5000")


@pytest.mark.parametrize("burn", [None, Decimal("0")])
def test_nothing_spent_is_not_infinite_months(burn: Decimal | None) -> None:
    assert pm.months_of_burn(Decimal("850000"), burn) is None


# ---------------------------------------------------------------------------
# Round-number crossings
# ---------------------------------------------------------------------------


def test_rising_through_levels_lists_them_in_travel_order() -> None:
    assert pm.round_number_levels(Decimal("1.6950"), Decimal("1.7250"), Decimal("0.01")) == [
        Decimal("1.70000000"),
        Decimal("1.71000000"),
        Decimal("1.72000000"),
    ]


def test_falling_through_levels_lists_them_highest_first() -> None:
    """That is the order they were crossed in, which is what a message says."""
    assert pm.round_number_levels(Decimal("1.7250"), Decimal("1.6950"), Decimal("0.01")) == [
        Decimal("1.72000000"),
        Decimal("1.71000000"),
        Decimal("1.70000000"),
    ]


def test_sitting_above_a_level_is_not_crossing_it_again() -> None:
    """Otherwise every poll re-reports the same level for ever."""
    assert pm.round_number_levels(Decimal("1.7510"), Decimal("1.7520"), Decimal("0.01")) == []


def test_landing_exactly_on_a_level_has_reached_it_not_crossed_it() -> None:
    """The crossing is reported by the next sample that moves off it."""
    assert pm.round_number_levels(Decimal("1.7450"), Decimal("1.7500"), Decimal("0.01")) == []
    assert pm.round_number_levels(Decimal("1.7500"), Decimal("1.7550"), Decimal("0.01")) == []


@pytest.mark.parametrize(
    ("previous", "current", "step"),
    [
        (None, Decimal("1.75"), Decimal("0.01")),
        (Decimal("1.75"), None, Decimal("0.01")),
        (Decimal("1.70"), Decimal("1.75"), Decimal("0")),
    ],
)
def test_no_levels_without_two_rates_and_a_step(
    previous: Decimal | None, current: Decimal | None, step: Decimal
) -> None:
    assert pm.round_number_levels(previous, current, step) == []
