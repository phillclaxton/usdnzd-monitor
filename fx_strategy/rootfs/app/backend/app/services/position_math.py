"""What the position is worth, and what waiting for it costs.

Pure calculation. Like :mod:`app.services.obligation_engine`, this module knows
nothing about where a rate came from, how it is stored or how it is presented:
plain values in, plain results out, so the arithmetic can be read and tested on
its own.

Every figure is measured against a **baseline rate** the user sets — the rate
they would otherwise have accepted. Improvement is therefore rate improvement on
the amount that actually converted, and fees are reported separately rather than
folded in, because a fee is a cost of transacting and not a worse rate.

Two rules run through everything here:

* **``None`` is not zero.** A figure that cannot be calculated comes back as
  ``None`` so the caller can say "set a baseline rate" instead of showing 0.00,
  the same convention :func:`app.services.calculations.total_fees` follows.
* **An estimate is never silently mixed with a fact.** Anything derived from a
  reconstructed amount is reported beside the confirmed figure, never added into
  it without saying so.

None of it is financial advice, and nothing here can move money.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from app.money import ZERO, quantize_money, quantize_rate, safe_divide

#: Interest accrues on a 365-day year, as ``obligation_engine`` already assumes.
DAYS_PER_YEAR = Decimal(365)

#: A month is the annual cost divided by twelve, never a 30-day slice, so a
#: monthly figure and twelve of them agree with the annual one.
MONTHS_PER_YEAR = Decimal(12)


@dataclass(frozen=True, slots=True)
class ConversionFact:
    """One conversion, reduced to what the arithmetic needs.

    Deliberately not the ORM row: this module never touches the database, and
    the caller decides which conversions count.
    """

    source_amount: Decimal
    target_amount: Decimal
    #: ``None`` means the fee was never recorded — which is not the same as a
    #: fee of zero, and is reported rather than assumed away.
    fee_source_currency: Decimal | None = None
    #: At least one amount was reconstructed rather than read off a receipt.
    amounts_estimated: bool = False


@dataclass(frozen=True, slots=True)
class ConversionImprovement:
    """What one conversion gained against the baseline, and how sure that is."""

    improvement: Decimal | None
    #: Running total including this conversion, oldest first.
    cumulative: Decimal | None
    #: The fee was unrecorded, so the figure is measured on the gross amount and
    #: overstates the gain by whatever the fee actually was.
    fee_unrecorded: bool
    #: This row's amounts were estimated, so this figure is an estimate too.
    estimated: bool


@dataclass(frozen=True, slots=True)
class RealisedTotal:
    """Realised improvement, split so an estimate cannot be read as a fact.

    All three figures are ``None`` together, and only when the baseline is
    unset. With a baseline and no estimated rows, ``estimated`` is a real
    ``0.00`` — the absence of estimates is a fact worth stating.
    """

    confirmed: Decimal | None
    estimated: Decimal | None
    total: Decimal | None
    includes_estimates: bool


def net_converted(source_amount: Decimal, fee_source: Decimal | None) -> Decimal:
    """The amount that actually crossed, after the provider took its fee.

    An unrecorded fee falls back to the gross amount rather than to zero fee.
    That overstates the gain slightly, which is why every figure derived from
    such a row carries ``fee_unrecorded``.
    """
    if fee_source is None:
        return quantize_money(source_amount, field="net converted")
    return quantize_money(source_amount - fee_source, field="net converted")


def realised_improvement(fact: ConversionFact, baseline: Decimal | None) -> Decimal | None:
    """What this conversion gained over converting the same amount at baseline.

    ``None`` while the baseline is unset: there is nothing to have improved on.
    """
    if baseline is None:
        return None
    net = net_converted(fact.source_amount, fact.fee_source_currency)
    return quantize_money(fact.target_amount - net * baseline, field="realised improvement")


def improvements(
    facts: Sequence[ConversionFact], baseline: Decimal | None
) -> list[ConversionImprovement]:
    """Per-conversion improvement with a running total, oldest first.

    The caller orders ``facts``; the cumulative column is only meaningful
    against the order it was given, so this does not quietly re-sort them.
    """
    running = ZERO
    rows: list[ConversionImprovement] = []
    for fact in facts:
        improvement = realised_improvement(fact, baseline)
        if improvement is not None:
            running += improvement
        rows.append(
            ConversionImprovement(
                improvement=improvement,
                cumulative=(
                    quantize_money(running, field="cumulative") if improvement is not None else None
                ),
                fee_unrecorded=fact.fee_source_currency is None,
                estimated=fact.amounts_estimated,
            )
        )
    return rows


def cumulative_realised(facts: Iterable[ConversionFact], baseline: Decimal | None) -> RealisedTotal:
    """Total realised improvement, with the estimated part kept separate.

    The split is the whole point: a headline that silently folds a reconstructed
    amount into a confirmed total is worse than one that admits the uncertainty,
    because there is no way to tell afterwards which it was.
    """
    if baseline is None:
        return RealisedTotal(confirmed=None, estimated=None, total=None, includes_estimates=False)

    confirmed = ZERO
    estimated = ZERO
    has_estimates = False
    for fact in facts:
        improvement = realised_improvement(fact, baseline)
        if improvement is None:  # pragma: no cover - baseline is set above
            continue
        if fact.amounts_estimated:
            estimated += improvement
            has_estimates = True
        else:
            confirmed += improvement

    confirmed_total = quantize_money(confirmed, field="confirmed realised")
    estimated_total = quantize_money(estimated, field="estimated realised")
    return RealisedTotal(
        confirmed=confirmed_total,
        estimated=estimated_total,
        total=quantize_money(confirmed + estimated, field="realised total"),
        includes_estimates=has_estimates,
    )


def current_value(balance: Decimal, rate: Decimal | None) -> Decimal | None:
    """What the remaining balance is worth at ``rate``."""
    if rate is None:
        return None
    return quantize_money(balance * rate, field="current value")


def unrealised_improvement(
    balance: Decimal, rate: Decimal | None, baseline: Decimal | None
) -> Decimal | None:
    """What the *unconverted* balance has gained on paper against the baseline.

    Negative when the rate is below the baseline, which is information rather
    than an error.
    """
    if rate is None or baseline is None:
        return None
    return quantize_money(balance * (rate - baseline), field="unrealised improvement")


def total_improvement(realised: Decimal | None, unrealised: Decimal | None) -> Decimal | None:
    """Realised plus unrealised, or ``None`` if either is unknown.

    Not a partial sum: a total that quietly omits one half would read as though
    both had been counted.
    """
    if realised is None or unrealised is None:
        return None
    return quantize_money(realised + unrealised, field="total improvement")


def daily_carrying_cost(shortfall: Decimal | None, annual_rate: Decimal | None) -> Decimal | None:
    """Interest a day on the part of the offset facility still unfunded.

    This is charged on the shortfall, never on the whole balance held: money
    that was never going to the mortgage is not costing mortgage interest.
    """
    if shortfall is None or annual_rate is None:
        return None
    cost = safe_divide(shortfall * annual_rate, DAYS_PER_YEAR)
    return quantize_money(cost, field="daily carrying cost") if cost is not None else None


def monthly_carrying_cost(shortfall: Decimal | None, annual_rate: Decimal | None) -> Decimal | None:
    """The same cost over a month — the annual figure over twelve."""
    if shortfall is None or annual_rate is None:
        return None
    cost = safe_divide(shortfall * annual_rate, MONTHS_PER_YEAR)
    return quantize_money(cost, field="monthly carrying cost") if cost is not None else None


def months_of_burn(nzd_value: Decimal | None, monthly_burn: Decimal | None) -> Decimal | None:
    """How many months the position covers at the stated spending rate."""
    if nzd_value is None or monthly_burn is None or monthly_burn <= ZERO:
        return None
    months = safe_divide(nzd_value, monthly_burn)
    return quantize_money(months, field="months of burn") if months is not None else None


def round_number_levels(
    previous_rate: Decimal | None, current_rate: Decimal | None, step: Decimal
) -> list[Decimal]:
    """Every multiple of ``step`` strictly between two rates, in travel order.

    Used to notice that a level was passed rather than that the rate happens to
    sit above one, so the same level does not report itself on every poll.
    Falling returns the levels highest-first, because that is the order they
    were crossed in.

    Both bounds are exclusive: a rate that lands exactly on a level has reached
    it, not crossed it, and the crossing is reported by the next sample that
    moves off it.
    """
    if previous_rate is None or current_rate is None or step <= ZERO:
        return []
    low, high = sorted((previous_rate, current_rate))
    # Start at the first multiple strictly above the lower bound.
    first = (low // step) + 1
    levels: list[Decimal] = []
    level = quantize_rate(first * step)
    while level < high:
        levels.append(level)
        first += 1
        level = quantize_rate(first * step)
    if current_rate < previous_rate:
        levels.reverse()
    return levels
