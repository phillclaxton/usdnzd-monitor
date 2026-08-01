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
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from app.money import (
    ONE_CENT,
    STANDARD_MOVEMENTS,
    ZERO,
    MoneyError,
    quantize_money,
    quantize_percent,
    quantize_rate,
    safe_divide,
)


class AllocationType(StrEnum):
    PERCENTAGE = "percentage"
    FIXED_AMOUNT = "fixed_amount"
    REMAINDER = "remainder"


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


def remaining_source_amount(available: Decimal, converted: Decimal) -> Decimal:
    """Never negative: over-recording cannot produce a negative exposure."""
    return quantize_money(max(available - converted, ZERO), field="remaining")


def percent_converted(converted: Decimal, available: Decimal) -> Decimal | None:
    if available <= ZERO:
        return None
    ratio = safe_divide(converted * Decimal(100), available)
    return quantize_percent(ratio) if ratio is not None else None


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


def target_upside(
    remaining_source: Decimal, target_rate: Decimal, current_rate: Decimal | None
) -> Decimal | None:
    """Extra target currency if the rate reaches ``target_rate``.

    Negative when the target is already below the current rate, which is
    informative rather than an error.
    """
    if current_rate is None:
        return None
    return quantize_money(remaining_source * (target_rate - current_rate), field="upside")


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
# Tranche allocation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllocationRequest:
    sequence: int
    allocation_type: AllocationType
    allocation_value: Decimal


@dataclass(frozen=True, slots=True)
class AllocationResult:
    sequence: int
    source_amount: Decimal
    allocation_type: AllocationType
    allocation_value: Decimal


@dataclass(frozen=True, slots=True)
class AllocationReport:
    allocations: list[AllocationResult]
    total_allocated: Decimal
    unallocated: Decimal
    percentage_total: Decimal
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def fully_allocated(self) -> bool:
        return self.unallocated == ZERO


def allocate(total_source: Decimal, requests: Sequence[AllocationRequest]) -> AllocationReport:
    """Turn tranche allocation rules into amounts.

    Percentages and fixed amounts are applied first; any ``remainder`` tranches
    then split what is left equally.  Rounding residue from percentage tranches
    is pushed onto the last percentage tranche so the parts always sum exactly
    to the whole — a cent lost to rounding would show up as a permanent
    unallocated balance otherwise.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if total_source <= ZERO:
        errors.append("The total amount must be greater than zero.")
        return AllocationReport([], ZERO, ZERO, ZERO, errors, warnings)

    ordered = sorted(requests, key=lambda item: item.sequence)
    percentage_total = sum(
        (r.allocation_value for r in ordered if r.allocation_type is AllocationType.PERCENTAGE),
        ZERO,
    )
    fixed_total = sum(
        (r.allocation_value for r in ordered if r.allocation_type is AllocationType.FIXED_AMOUNT),
        ZERO,
    )
    remainder_count = sum(1 for r in ordered if r.allocation_type is AllocationType.REMAINDER)

    for request in ordered:
        if request.allocation_type is not AllocationType.REMAINDER and (
            request.allocation_value <= ZERO
        ):
            errors.append(f"Tranche {request.sequence} must allocate more than zero.")

    if percentage_total > Decimal(100):
        errors.append(f"Tranche percentages total {percentage_total}%, which is more than 100%.")
    if fixed_total > total_source:
        errors.append(
            f"Fixed tranche amounts total {fixed_total}, which is more than the "
            f"strategy total of {total_source}."
        )
    if errors:
        return AllocationReport([], ZERO, ZERO, percentage_total, errors, warnings)

    results: list[AllocationResult] = []
    percentage_amounts: dict[int, Decimal] = {}
    for request in ordered:
        if request.allocation_type is AllocationType.PERCENTAGE:
            percentage_amounts[request.sequence] = quantize_money(
                total_source * request.allocation_value / Decimal(100), field="tranche amount"
            )

    # Push rounding residue onto the last percentage tranche.
    if percentage_amounts and percentage_total == Decimal(100) and not remainder_count:
        expected = total_source - fixed_total
        residue = expected - sum(percentage_amounts.values(), ZERO)
        if residue != ZERO:
            last = max(percentage_amounts)
            percentage_amounts[last] = quantize_money(percentage_amounts[last] + residue)

    assigned_before_remainder = sum(percentage_amounts.values(), ZERO) + fixed_total
    remainder_pool = quantize_money(
        max(total_source - assigned_before_remainder, ZERO), field="remainder"
    )
    remainder_each = (
        quantize_money(remainder_pool / Decimal(remainder_count)) if remainder_count else ZERO
    )
    remainder_assigned = ZERO
    remainder_seen = 0

    for request in ordered:
        if request.allocation_type is AllocationType.PERCENTAGE:
            amount = percentage_amounts[request.sequence]
        elif request.allocation_type is AllocationType.FIXED_AMOUNT:
            amount = quantize_money(request.allocation_value, field="tranche amount")
        else:
            remainder_seen += 1
            amount = (
                quantize_money(remainder_pool - remainder_assigned)
                if remainder_seen == remainder_count
                else remainder_each
            )
            remainder_assigned += amount
        results.append(
            AllocationResult(
                sequence=request.sequence,
                source_amount=amount,
                allocation_type=request.allocation_type,
                allocation_value=request.allocation_value,
            )
        )

    total_allocated = quantize_money(sum((r.source_amount for r in results), ZERO))
    unallocated = quantize_money(total_source - total_allocated)

    if unallocated > ZERO:
        warnings.append(
            f"{unallocated} of {total_source} is unallocated. "
            "That is fine if you intend to hold a reserve."
        )
    elif unallocated < ZERO:
        errors.append(
            f"Tranches allocate {total_allocated}, which exceeds the total of {total_source}."
        )

    return AllocationReport(
        allocations=results,
        total_allocated=total_allocated,
        unallocated=unallocated,
        percentage_total=quantize_percent(percentage_total),
        errors=errors,
        warnings=warnings,
    )


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


# ---------------------------------------------------------------------------
# Walk-away analysis
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WalkAwayAnalysis:
    """Everything the walk-away panel needs to show the trade-off honestly."""

    remaining_source: Decimal
    current_rate: Decimal | None
    convert_now: ConversionOutcome | None
    existing_blended_rate: Decimal | None
    blended_if_converted_now: Decimal | None
    highest_outstanding_target: Decimal | None
    difference_versus_waiting: Decimal | None
    rate_movement_to_next_target: Decimal | None
    sensitivity: list[SensitivityRow]


def analyse_walk_away(
    *,
    remaining_source: Decimal,
    current_rate: Decimal | None,
    completed: Sequence[CompletedConversion],
    outstanding_targets: Sequence[Decimal],
    next_target: Decimal | None,
    assumption: FeeAssumption | None,
) -> WalkAwayAnalysis:
    """Compare converting the remainder now against holding out for a target.

    The comparison deliberately reports both the extra amount a higher target
    would bring *and* how much stays exposed while waiting. It never recommends
    waiting merely because a higher target exists.
    """
    convert_now = (
        project_conversion(remaining_source, current_rate, assumption)
        if current_rate is not None and remaining_source > ZERO
        else None
    )
    existing_blended = blended_effective_rate(completed)

    blended_if_now: Decimal | None = None
    if convert_now is not None:
        combined = [
            *completed,
            CompletedConversion(
                source_amount=remaining_source,
                target_amount=(
                    convert_now.net_target_amount
                    if convert_now.net_target_amount is not None
                    else convert_now.gross_target_amount
                ),
                gross_rate=current_rate,  # type: ignore[arg-type]
            ),
        ]
        blended_if_now = blended_effective_rate(combined)

    highest = max(outstanding_targets) if outstanding_targets else None
    difference = (
        movement_value(remaining_source, highest - current_rate)
        if highest is not None and current_rate is not None
        else None
    )
    movement_needed = (
        quantize_rate(next_target - current_rate)
        if next_target is not None and current_rate is not None
        else None
    )

    return WalkAwayAnalysis(
        remaining_source=remaining_source,
        current_rate=current_rate,
        convert_now=convert_now,
        existing_blended_rate=existing_blended,
        blended_if_converted_now=blended_if_now,
        highest_outstanding_target=highest,
        difference_versus_waiting=difference,
        rate_movement_to_next_target=movement_needed,
        sensitivity=sensitivity_table(remaining_source),
    )


# ---------------------------------------------------------------------------
# Deadlines
# ---------------------------------------------------------------------------


class DeadlineSeverity(StrEnum):
    NORMAL = "normal"
    NOTICE = "notice"
    REVIEW = "review"
    WARNING = "warning"
    CRITICAL = "critical"
    OVERDUE = "overdue"


#: Thresholds from the product specification, in days remaining.
DEADLINE_BANDS: tuple[tuple[int, DeadlineSeverity, str], ...] = (
    (90, DeadlineSeverity.NORMAL, "Normal target strategy."),
    (30, DeadlineSeverity.NOTICE, "Unconverted exposure is worth watching."),
    (14, DeadlineSeverity.REVIEW, "Review your targets against the time left."),
    (7, DeadlineSeverity.WARNING, "Deadline approaching."),
    (0, DeadlineSeverity.CRITICAL, "Deadline is very close."),
)


def deadline_severity(days_remaining: int | None) -> tuple[DeadlineSeverity, str]:
    """Classify how close a deadline is.

    Returns UI guidance only. Nothing in this application changes a target rate
    or executes anything because a deadline is near.
    """
    if days_remaining is None:
        return DeadlineSeverity.NORMAL, "No deadline set."
    if days_remaining < 0:
        return DeadlineSeverity.OVERDUE, "The deadline has passed."
    for threshold, severity, message in DEADLINE_BANDS:
        if days_remaining > threshold:
            return severity, message
    return DeadlineSeverity.CRITICAL, "Deadline is very close."


def required_conversion_shortfall(
    required_source_amount: Decimal, converted_source_amount: Decimal
) -> Decimal:
    """How much still has to be converted to meet a dated requirement."""
    return quantize_money(
        max(required_source_amount - converted_source_amount, ZERO), field="shortfall"
    )


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScenarioLeg:
    source_amount: Decimal
    rate: Decimal
    label: str


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    key: str
    name: str
    description: str
    total_source: Decimal
    gross_target_amount: Decimal
    fee: FeeEstimate
    net_target_amount: Decimal | None
    blended_rate: Decimal
    effective_rate: Decimal | None
    #: How much is still exposed to future movements under this scenario.
    exposed_source_amount: Decimal
    #: What a one-cent adverse move costs while the plan is unfinished.
    one_cent_exposure: Decimal
    legs: list[ScenarioLeg]
    #: Highest rate the plan needs in order to complete.
    rate_required: Decimal | None
    assumptions: list[str] = field(default_factory=list)


def evaluate_scenario(
    *,
    key: str,
    name: str,
    description: str,
    legs: Sequence[ScenarioLeg],
    assumption: FeeAssumption | None,
    exposed_source_amount: Decimal = ZERO,
    rate_required: Decimal | None = None,
    assumptions: Sequence[str] = (),
) -> ScenarioResult:
    """Evaluate one conversion plan.

    No scenario is labelled "best": the result carries the trade-offs — what is
    received, what stays exposed, and what rate the plan depends on — and the UI
    presents them side by side.
    """
    total_source = quantize_money(sum((leg.source_amount for leg in legs), ZERO))
    gross = quantize_money(sum((gross_proceeds(leg.source_amount, leg.rate) for leg in legs), ZERO))
    fee = estimate_fee(assumption, total_source, None)

    fee_in_target: Decimal | None = None
    if fee.amount_source_currency is not None and total_source > ZERO:
        # Convert the source-side fee using the scenario's own blended rate.
        blended = safe_divide(gross, total_source)
        if blended is not None:
            fee_in_target = quantize_money(fee.amount_source_currency * blended)
            fee = FeeEstimate(
                amount_source_currency=fee.amount_source_currency,
                amount_target_currency=fee_in_target,
                quality=fee.quality,
                basis=fee.basis,
            )

    net = net_proceeds(gross, fee_in_target)
    blended_rate = safe_divide(gross, total_source)

    return ScenarioResult(
        key=key,
        name=name,
        description=description,
        total_source=total_source,
        gross_target_amount=gross,
        fee=fee,
        net_target_amount=net,
        blended_rate=quantize_rate(blended_rate) if blended_rate is not None else ZERO,
        effective_rate=effective_rate(net, total_source) if total_source > ZERO else None,
        exposed_source_amount=exposed_source_amount,
        one_cent_exposure=value_of_one_cent(exposed_source_amount),
        legs=list(legs),
        rate_required=rate_required,
        assumptions=list(assumptions),
    )


def convert_all_now_scenario(
    total_source: Decimal, current_rate: Decimal, assumption: FeeAssumption | None
) -> ScenarioResult:
    return evaluate_scenario(
        key="convert_all_now",
        name="Convert everything now",
        description="One conversion at today's rate.",
        legs=[ScenarioLeg(total_source, current_rate, "Today")],
        assumption=assumption,
        exposed_source_amount=ZERO,
        rate_required=current_rate,
        assumptions=["Assumes the whole amount is available today."],
    )


def ladder_scenario(
    allocations: Sequence[tuple[Decimal, Decimal]],
    assumption: FeeAssumption | None,
    *,
    key: str = "target_ladder",
    name: str = "Target ladder",
    description: str = "Each tranche converts when its target rate is reached.",
) -> ScenarioResult:
    """Evaluate a ladder given ``(source_amount, target_rate)`` pairs."""
    legs = [
        ScenarioLeg(amount, rate, f"At {rate}") for amount, rate in allocations if amount > ZERO
    ]
    highest = max((rate for _amount, rate in allocations), default=None)
    return evaluate_scenario(
        key=key,
        name=name,
        description=description,
        legs=legs,
        assumption=assumption,
        exposed_source_amount=quantize_money(sum((leg.source_amount for leg in legs), ZERO)),
        rate_required=highest,
        assumptions=[
            "Assumes every target is reached before the deadline.",
            "Targets that are never reached leave that tranche unconverted.",
        ],
    )


def equal_schedule_scenario(
    total_source: Decimal,
    rate: Decimal,
    periods: int,
    assumption: FeeAssumption | None,
    *,
    name: str = "Equal monthly conversion",
) -> ScenarioResult:
    """A simple time-based schedule at a single assumed rate.

    Using one rate for every period is an explicit simplification, stated in the
    result, rather than a hidden forecast of where the rate will go.
    """
    if periods < 1:
        periods = 1
    each = quantize_money(total_source / Decimal(periods))
    amounts = [each] * (periods - 1)
    amounts.append(quantize_money(total_source - sum(amounts, ZERO)))
    return evaluate_scenario(
        key="equal_schedule",
        name=name,
        description=f"Convert an equal share in each of {periods} periods.",
        legs=[
            ScenarioLeg(amount, rate, f"Period {index + 1}") for index, amount in enumerate(amounts)
        ],
        assumption=assumption,
        exposed_source_amount=quantize_money(total_source - each),
        rate_required=None,
        assumptions=[
            "Uses today's rate for every period. It is a reference point, not a forecast.",
        ],
    )


def compare_against(
    achieved_net: Decimal | None,
    total_source: Decimal,
    comparison_rate: Decimal | None,
    assumption: FeeAssumption | None,
) -> Decimal | None:
    """Difference between what was achieved and one alternative rate."""
    if achieved_net is None or comparison_rate is None or total_source <= ZERO:
        return None
    alternative = project_conversion(total_source, comparison_rate, assumption)
    baseline = (
        alternative.net_target_amount
        if alternative.net_target_amount is not None
        else alternative.gross_target_amount
    )
    return quantize_money(achieved_net - baseline, field="comparison")


# ---------------------------------------------------------------------------
# Validation used by the API layer
# ---------------------------------------------------------------------------


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
