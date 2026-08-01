"""Decimal helper tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.money import (
    MoneyError,
    invert_rate,
    quantize_display,
    quantize_money,
    quantize_rate,
    require_currency,
    require_positive,
    safe_divide,
    to_decimal,
)


def test_float_input_uses_repr_not_binary_expansion() -> None:
    assert to_decimal(0.1) == Decimal("0.1")
    assert to_decimal(1.75) == Decimal("1.75")


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_values_are_rejected(bad: float) -> None:
    with pytest.raises(MoneyError):
        to_decimal(bad)


def test_non_numeric_string_is_rejected() -> None:
    with pytest.raises(MoneyError):
        to_decimal("seventeen")


def test_quantization_precisions() -> None:
    assert quantize_rate("1.7") == Decimal("1.70000000")
    assert quantize_money("1.23456789") == Decimal("1.2346")
    assert quantize_display("1.005") == Decimal("1.00")  # banker's rounding


def test_invert_rate_round_trips_within_storage_precision() -> None:
    # 0.5714... USD per NZD inverts back to 1.75 NZD per USD.
    usd_per_nzd = invert_rate(Decimal("1.75"))
    assert usd_per_nzd == Decimal("0.57142857")
    assert invert_rate(usd_per_nzd).quantize(Decimal("0.0001")) == Decimal("1.7500")


def test_invert_rejects_non_positive() -> None:
    with pytest.raises(MoneyError):
        invert_rate(Decimal("0"))


def test_safe_divide_returns_none_for_zero_denominator() -> None:
    assert safe_divide(Decimal("10"), Decimal("0")) is None
    assert safe_divide(Decimal("10"), Decimal("4")) == Decimal("2.5")


def test_require_positive_rejects_zero_and_negative() -> None:
    assert require_positive(Decimal("0.0001")) == Decimal("0.0001")
    for bad in (Decimal("0"), Decimal("-1")):
        with pytest.raises(MoneyError):
            require_positive(bad)


def test_currency_allow_list() -> None:
    assert require_currency("usd") == "USD"
    with pytest.raises(MoneyError):
        require_currency("XYZ")


def test_decimal_arithmetic_is_exact_where_float_is_not() -> None:
    # The canonical float failure: 0.1 + 0.2 != 0.3
    assert to_decimal("0.1") + to_decimal("0.2") == to_decimal("0.3")
    # 800,000 USD at 1.7620 must be exactly 1,409,600 NZD.
    assert to_decimal("800000") * to_decimal("1.7620") == Decimal("1409600.0000")
