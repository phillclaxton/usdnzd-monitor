"""Financial calculations.

Pure functions over :class:`~decimal.Decimal` values.  Nothing in this module
touches the database, the network or the clock beyond what is passed in, so
every rule here is directly testable.

Two conventions run through the whole module:

* A figure that cannot be calculated is ``None``, never zero.  The UI renders
  ``None`` as a dash and an explicit "Fee not included", because a zero fee
  shown as a fact is a lie about money.
* Every amount is labelled as gross, estimated or actual by the type that
  carries it, so no caller can accidentally present an estimate as a result.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.money import (
    ONE_CENT,
    STANDARD_MOVEMENTS,
    ZERO,
    MoneyError,
    quantize_money,
    quantize_rate,
    safe_divide,
)


class FeeType(StrEnum):
    PERCENTAGE = "percentage"
    FIXED_PLUS_PERCENTAGE = "fixed_plus_percentage"
    QUOTE_ONLY = "quote_only"
    MANUAL = "manual"


class AmountQuality(StrEnum):
    """How much confidence an amount carries. Always shown beside the figure."""

    GROSS = "gross"
    ESTIMATE = "estimate"
    ACTUAL = "actual"


# ---------------------------------------------------------------------------
# Fees
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FeeAssumption:
    """A fee model, as configured by the user."""

    fee_type: FeeType
    fixed_fee: Decimal = ZERO
    percentage_fee: Decimal = ZERO
    minimum_fee: Decimal | None = None
    maximum_fee: Decimal | None = None
    #: The currency the fee is charged in — Wise charges in the source currency.
    currency: str = "USD"
    name: str = ""


@dataclass(frozen=True, slots=True)
class FeeEstimate:
    """An estimated fee, or an explicit statement that none is available."""

    amount_source_currency: Decimal | None
    amount_target_currency: Decimal | None
    quality: AmountQuality
    basis: str

    @property
    def available(self) -> bool:
        return self.amount_source_currency is not None or self.amount_target_currency is not None

    @property
    def label(self) -> str:
        return self.basis if self.available else "Fee not included"


#: Returned when no fee model is configured. Deliberately not a zero fee.
NO_FEE_ESTIMATE = FeeEstimate(
    amount_source_currency=None,
    amount_target_currency=None,
    quality=AmountQuality.ESTIMATE,
    basis="No fee model configured",
)


def estimate_fee(
    assumption: FeeAssumption | None,
    source_amount: Decimal,
    rate: Decimal | None,
) -> FeeEstimate:
    """Estimate the fee on converting ``source_amount``.

    ``quote_only`` models return no estimate: their figure comes from a live
    provider quote, and inventing one here would be a fabrication.
    """
    if assumption is None:
        return NO_FEE_ESTIMATE
    if assumption.fee_type in (FeeType.QUOTE_ONLY, FeeType.MANUAL):
        return FeeEstimate(
            amount_source_currency=None,
            amount_target_currency=None,
            quality=AmountQuality.ESTIMATE,
            basis=(
                "Fee comes from a live quote"
                if assumption.fee_type is FeeType.QUOTE_ONLY
                else "Fee entered manually per conversion"
            ),
        )
    if source_amount <= ZERO:
        return NO_FEE_ESTIMATE

    percentage_part = source_amount * (assumption.percentage_fee / Decimal(100))
    fee = percentage_part
    if assumption.fee_type is FeeType.FIXED_PLUS_PERCENTAGE:
        fee += assumption.fixed_fee

    if assumption.minimum_fee is not None:
        fee = max(fee, assumption.minimum_fee)
    if assumption.maximum_fee is not None:
        fee = min(fee, assumption.maximum_fee)
    fee = quantize_money(fee, field="fee")

    basis = (
        f"{assumption.percentage_fee}% of the converted amount"
        if assumption.fee_type is FeeType.PERCENTAGE
        else f"{assumption.fixed_fee} + {assumption.percentage_fee}%"
    )

    # Wise charges its fee on the source side, so the target-currency figure is
    # derived from the rate when one is available.
    in_target = quantize_money(fee * rate, field="fee") if rate is not None else None
    return FeeEstimate(
        amount_source_currency=fee,
        amount_target_currency=in_target,
        quality=AmountQuality.ESTIMATE,
        basis=basis,
    )


# ---------------------------------------------------------------------------
# Proceeds
# ---------------------------------------------------------------------------


def gross_proceeds(source_amount: Decimal, rate: Decimal) -> Decimal:
    """``gross = source x rate``."""
    return quantize_money(source_amount * rate, field="gross proceeds")


def net_proceeds(gross: Decimal, fee_in_target_currency: Decimal | None) -> Decimal | None:
    """``net = gross - fee``, or ``None`` when the fee is unknown."""
    if fee_in_target_currency is None:
        return None
    return quantize_money(gross - fee_in_target_currency, field="net proceeds")


def effective_rate(net_target_amount: Decimal | None, source_amount: Decimal) -> Decimal | None:
    """``effective = net / source`` — what the conversion actually achieved."""
    if net_target_amount is None:
        return None
    result = safe_divide(net_target_amount, source_amount)
    return quantize_rate(result) if result is not None else None


@dataclass(frozen=True, slots=True)
class ConversionOutcome:
    """Gross, fee and net for one conversion, each labelled."""

    source_amount: Decimal
    rate: Decimal
    gross_target_amount: Decimal
    fee: FeeEstimate
    net_target_amount: Decimal | None
    effective_rate: Decimal | None
    quality: AmountQuality


def project_conversion(
    source_amount: Decimal,
    rate: Decimal,
    assumption: FeeAssumption | None,
) -> ConversionOutcome:
    """Project what converting ``source_amount`` at ``rate`` would produce."""
    gross = gross_proceeds(source_amount, rate)
    fee = estimate_fee(assumption, source_amount, rate)
    net = net_proceeds(gross, fee.amount_target_currency)
    return ConversionOutcome(
        source_amount=source_amount,
        rate=rate,
        gross_target_amount=gross,
        fee=fee,
        net_target_amount=net,
        effective_rate=effective_rate(net, source_amount),
        quality=AmountQuality.ESTIMATE,
    )


# ---------------------------------------------------------------------------
# Blended rates
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompletedConversion:
    """A conversion that actually happened, as the blended maths sees it."""

    source_amount: Decimal
    target_amount: Decimal
    gross_rate: Decimal
    fee_target_equivalent: Decimal | None = None

    @property
    def net_target_amount(self) -> Decimal:
        """What reached the account.

        ``target_amount`` is the figure the user recorded from their statement,
        which is already net of fees deducted on the target side.
        """
        return self.target_amount


def blended_gross_rate(conversions: Sequence[CompletedConversion]) -> Decimal | None:
    """Source-weighted average of the gross rates achieved."""
    total_source = sum((c.source_amount for c in conversions), ZERO)
    if total_source == ZERO:
        return None
    weighted = sum((c.source_amount * c.gross_rate for c in conversions), ZERO)
    result = safe_divide(weighted, total_source)
    return quantize_rate(result) if result is not None else None


def blended_effective_rate(conversions: Sequence[CompletedConversion]) -> Decimal | None:
    """Total target received divided by total source converted."""
    total_source = sum((c.source_amount for c in conversions), ZERO)
    if total_source == ZERO:
        return None
    total_target = sum((c.net_target_amount for c in conversions), ZERO)
    result = safe_divide(total_target, total_source)
    return quantize_rate(result) if result is not None else None


def total_fees(conversions: Sequence[CompletedConversion]) -> Decimal | None:
    """Sum of recorded fees, or ``None`` when no conversion recorded one."""
    recorded = [c.fee_target_equivalent for c in conversions if c.fee_target_equivalent is not None]
    if not recorded:
        return None
    return quantize_money(sum(recorded, ZERO), field="total fees")


# ---------------------------------------------------------------------------
# Exposure
# ---------------------------------------------------------------------------


def value_of_one_cent(remaining_source: Decimal) -> Decimal:
    """The headline exposure figure: what a one-cent move is worth.

    At USD 800,000 this is NZD 8,000, which is the number that makes the
    position understandable at a glance.
    """
    return quantize_money(remaining_source * ONE_CENT, field="one cent exposure")


def movement_value(remaining_source: Decimal, movement: Decimal) -> Decimal:
    """Target-currency value of a rate movement of ``movement``."""
    return quantize_money(remaining_source * movement, field="movement value")


@dataclass(frozen=True, slots=True)
class SensitivityRow:
    movement: Decimal
    downside: Decimal
    upside: Decimal


def sensitivity_table(
    remaining_source: Decimal, movements: Iterable[Decimal] = STANDARD_MOVEMENTS
) -> list[SensitivityRow]:
    """What each standard rate movement is worth, up and down."""
    return [
        SensitivityRow(
            movement=movement,
            downside=movement_value(remaining_source, -movement),
            upside=movement_value(remaining_source, movement),
        )
        for movement in movements
    ]


# ---------------------------------------------------------------------------
# Rate zones
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RateZone:
    """A named band of the rate, with guidance. Not a forecast."""

    label: str
    guidance: str
    #: Inclusive lower bound; ``None`` means "no lower bound".
    lower_bound: Decimal | None = None


def classify_rate(rate: Decimal | None, zones: Sequence[RateZone]) -> RateZone | None:
    """Find the zone a rate falls in.

    Zones are matched from the highest lower bound downwards, so the order they
    were configured in does not matter.
    """
    if rate is None or not zones:
        return None
    ordered = sorted(
        zones,
        key=lambda zone: zone.lower_bound if zone.lower_bound is not None else Decimal("-1e30"),
        reverse=True,
    )
    for zone in ordered:
        if zone.lower_bound is None or rate >= zone.lower_bound:
            return zone
    return ordered[-1]


def validate_conversion_amounts(
    source_amount: Decimal,
    target_amount: Decimal,
    remaining_source: Decimal,
    *,
    allow_exceeding_remaining: bool = False,
) -> None:
    """Reject financially impossible conversion records.

    ``allow_exceeding_remaining`` exists for correcting a mis-entered history,
    where the running total is temporarily inconsistent by design.
    """
    if source_amount <= ZERO:
        raise MoneyError("The converted amount must be greater than zero.")
    if target_amount <= ZERO:
        raise MoneyError("The amount received must be greater than zero.")
    if not allow_exceeding_remaining and source_amount > remaining_source:
        raise MoneyError(
            f"This conversion is {source_amount}, but only {remaining_source} is unconverted. "
            "Tick 'correcting an earlier record' if you are fixing the history."
        )
