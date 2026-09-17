"""FX position schemas.

Money and rates cross this boundary as JSON *strings*, as everywhere else in
this application: emitting them as JSON numbers would hand the browser a float
and reintroduce the binary rounding the whole design exists to avoid.

Anything derived from an estimated conversion is reported as a **split**
(`realised_confirmed` / `realised_estimated` / `realised_total`) rather than as
a total with a flag beside it. A consumer that ignores the flag still cannot
read the estimate as a fact, which matters most for the consumer that is not a
person.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import Field, model_validator

from app.schemas.common import MoneyStr, RateStr, Schema, StrictSchema

#: A field that exists only to be ignored, so a hand-edited state document can
#: carry notes to itself. JSON has no comment syntax, and the request schemas
#: here reject unknown keys on purpose — that catches ``baseline_rte``, which is
#: worth keeping, but it would also reject a file someone had annotated.
CommentText = Annotated[str | list[str] | None, Field(default=None, alias="_comment")]


def _check_position_values(
    *,
    balance: Decimal | None,
    baseline_rate: Decimal | None,
    loan_rate: Decimal | None,
    shortfall: Decimal | None,
    burn: Decimal | None,
) -> None:
    """The invariants a position has to hold, whichever route set it.

    Shared by the whole-document and partial forms so that a value refused by
    one cannot be slipped past the other — a PATCH is not a back door.
    """
    if balance is not None and balance < 0:
        raise ValueError("The balance cannot be negative.")
    if baseline_rate is not None and baseline_rate <= 0:
        raise ValueError("The baseline rate must be greater than zero.")
    if loan_rate is not None:
        if loan_rate < 0:
            raise ValueError("The loan rate cannot be negative.")
        if loan_rate > 1:
            # 6.04 instead of 0.0604 would overstate the carrying cost a
            # hundredfold, and look plausible enough to act on.
            raise ValueError(
                "The loan rate is a fraction, not a percentage: enter 6.04% as 0.0604."
            )
    for name, value in (("offset shortfall", shortfall), ("monthly burn", burn)):
        if value is not None and value < 0:
            raise ValueError(f"The {name} cannot be negative.")


class PositionIn(StrictSchema):
    """The whole position, as the manual state form submits it."""

    source_currency: str = Field(default="USD", min_length=3, max_length=3)
    target_currency: str = Field(default="NZD", min_length=3, max_length=3)
    current_source_balance: MoneyStr = Decimal(0)
    baseline_rate: RateStr | None = None
    baseline_date: date | None = None
    #: A fraction, not a percentage: 6.04% is 0.0604.
    floating_loan_rate: RateStr | None = None
    current_offset_shortfall_nzd: MoneyStr | None = None
    monthly_nzd_burn: MoneyStr | None = None
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def _coherent(self) -> PositionIn:
        _check_position_values(
            balance=self.current_source_balance,
            baseline_rate=self.baseline_rate,
            loan_rate=self.floating_loan_rate,
            shortfall=self.current_offset_shortfall_nzd,
            burn=self.monthly_nzd_burn,
        )
        return self


class PositionPatch(StrictSchema):
    """A partial update. Only the fields actually sent are applied.

    Every field is optional *and* nullable, so clearing the baseline rate is
    expressible. ``model_fields_set`` is what distinguishes "set this to null"
    from "leave it alone" — reading the attribute alone cannot.
    """

    source_currency: str | None = Field(default=None, min_length=3, max_length=3)
    target_currency: str | None = Field(default=None, min_length=3, max_length=3)
    current_source_balance: MoneyStr | None = None
    baseline_rate: RateStr | None = None
    baseline_date: date | None = None
    floating_loan_rate: RateStr | None = None
    current_offset_shortfall_nzd: MoneyStr | None = None
    monthly_nzd_burn: MoneyStr | None = None
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _coherent(self) -> PositionPatch:
        # ``None`` means "clear this" here, which each check already passes
        # over; what is being caught is a *value* that a whole-document save
        # would have refused.
        _check_position_values(
            balance=self.current_source_balance,
            baseline_rate=self.baseline_rate,
            loan_rate=self.floating_loan_rate,
            shortfall=self.current_offset_shortfall_nzd,
            burn=self.monthly_nzd_burn,
        )
        return self


class PositionOut(Schema):
    source_currency: str
    target_currency: str
    current_source_balance: MoneyStr
    baseline_rate: RateStr | None
    baseline_date: date | None
    floating_loan_rate: RateStr | None
    current_offset_shortfall_nzd: MoneyStr | None
    monthly_nzd_burn: MoneyStr | None
    notes: str
    updated_at: datetime


class RealisedSplit(Schema):
    """Realised improvement, with the estimated part never folded in silently."""

    confirmed: MoneyStr | None
    estimated: MoneyStr | None
    total: MoneyStr | None
    includes_estimates: bool


class PositionMetrics(Schema):
    """Everything derived, computed on read.

    Nothing here is stored. A stored total can disagree with the figures it was
    built from; a computed one cannot.

    A ``None`` means "not calculable from what has been entered" — almost always
    a missing baseline rate — and never zero.
    """

    current_rate: RateStr | None
    rate_status: str
    current_target_value: MoneyStr | None
    realised: RealisedSplit
    unrealised_improvement: MoneyStr | None
    #: Confirmed realised plus unrealised. The estimated part stays in
    #: ``realised`` so this figure is one a consumer can rely on.
    total_improvement_confirmed: MoneyStr | None
    total_source_converted: MoneyStr
    total_target_received: MoneyStr
    total_fees_target: MoneyStr | None
    daily_carrying_cost: MoneyStr | None
    monthly_carrying_cost: MoneyStr | None
    months_of_burn: MoneyStr | None


class FxStateOut(Schema):
    """``position`` is null on a fresh install, and that is a 200, not a 404.

    An empty state is a normal thing for this endpoint to describe, so the
    frontend should not have to treat an error as the first-run case.
    """

    position: PositionOut | None
    metrics: PositionMetrics | None
    latest_conversion: dict[str, Any] | None
    conversion_count: int


class ConversionHistoryRow(Schema):
    """One conversion with what it gained, for the history table."""

    id: int
    executed_at: datetime
    source_amount: MoneyStr
    target_amount: MoneyStr
    gross_rate: RateStr
    effective_rate: RateStr
    fee_source_currency: MoneyStr | None
    fee_total_target_equivalent: MoneyStr | None
    provider: str
    record_source: str
    notes: str
    amounts_estimated: bool
    simulated: bool
    improvement: MoneyStr | None
    cumulative_improvement: MoneyStr | None
    #: The fee was never recorded, so the improvement is measured on the gross
    #: amount and overstates the gain by whatever the fee was.
    fee_unrecorded: bool


class ConversionHistoryOut(Schema):
    conversions: list[ConversionHistoryRow]
    baseline_rate: RateStr | None
    realised: RealisedSplit


class RecordConversionIn(StrictSchema):
    """The quick action: a conversion that has just happened.

    Recording one here **reduces the held balance**, which is what separates it
    from ``POST /conversions``. It does not touch the offset shortfall: not
    every conversion goes to the mortgage, and guessing that it did would
    quietly corrupt the carrying cost.
    """

    executed_at: datetime | None = None
    source_amount: MoneyStr
    target_amount: MoneyStr
    gross_rate: RateStr | None = None
    fee_source_currency: MoneyStr | None = None
    fee_target_currency: MoneyStr | None = None
    provider: str = Field(default="wise", max_length=32)
    provider_transaction_id: str | None = Field(default=None, max_length=128)
    notes: str = Field(default="", max_length=2000)
    amounts_estimated: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> RecordConversionIn:
        if self.source_amount <= 0:
            raise ValueError("The converted amount must be greater than zero.")
        if self.target_amount <= 0:
            raise ValueError("The amount received must be greater than zero.")
        return self


class AlertHistoryRow(Schema):
    id: int
    rule_type: str
    severity: str
    title: str
    message: str
    entity_id: str | None
    created_at: datetime
    delivered: bool


class StateExport(Schema):
    """§10's export shape, plus the split that keeps an estimate honest."""

    as_of: datetime
    source_currency: str
    target_currency: str
    source_balance: MoneyStr
    current_rate: RateStr | None
    baseline_rate: RateStr | None
    baseline_date: date | None
    offset_shortfall_nzd: MoneyStr | None
    floating_rate: RateStr | None
    monthly_nzd_burn: MoneyStr | None
    realised_confirmed: MoneyStr | None
    realised_estimated: MoneyStr | None
    realised_total: MoneyStr | None
    includes_estimates: bool
    unrealised_gain_nzd: MoneyStr | None
    conversions: list[dict[str, Any]]


class ImportConversion(StrictSchema):
    """One historical conversion in an imported state document.

    ``source_amount`` may be omitted when the receipt and the rate are known:
    the amount converted is then implied by algebra rather than invented, which
    is a different thing from an estimate and is not flagged as one.
    """

    #: JSON has no comments and this document is written by hand, so one field
    #: is set aside to be ignored. Without it the strict schema would reject a
    #: file someone had annotated to remind themselves what a row was.
    comment: CommentText = None
    executed_at: datetime | None = None
    source_amount: MoneyStr | None = None
    target_amount: MoneyStr | None = None
    gross_rate: RateStr | None = None
    fee_source_currency: MoneyStr | None = None
    fee_target_currency: MoneyStr | None = None
    provider: str = Field(default="wise", max_length=32)
    provider_transaction_id: str | None = Field(default=None, max_length=128)
    notes: str = Field(default="", max_length=2000)
    amounts_estimated: bool = False

    @model_validator(mode="after")
    def _derivable(self) -> ImportConversion:
        known = [
            value is not None for value in (self.source_amount, self.target_amount, self.gross_rate)
        ]
        if sum(known) < 2:
            raise ValueError(
                "Each conversion needs at least two of: amount converted, amount "
                "received, and rate. The third is derived from them."
            )
        return self


class StateImport(StrictSchema):
    """A whole position plus its history, written as backfill.

    The balance is written **exactly as given**. It is already the balance after
    these conversions, so recording them through the live path would decrement
    it a second time and leave the position short by the whole converted total.
    """

    comment: CommentText = None
    source_currency: str = Field(default="USD", min_length=3, max_length=3)
    target_currency: str = Field(default="NZD", min_length=3, max_length=3)
    current_source_balance: MoneyStr = Decimal(0)
    baseline_rate: RateStr | None = None
    baseline_date: date | None = None
    floating_loan_rate: RateStr | None = None
    current_offset_shortfall_nzd: MoneyStr | None = None
    monthly_nzd_burn: MoneyStr | None = None
    notes: str = Field(default="", max_length=2000)
    completed_conversions: list[ImportConversion] = Field(default_factory=list)


class ImportPreview(Schema):
    """What an import would do, or did.

    Preview is the default because a state import rewrites the position and
    inserts history in one go, and seeing that before it happens costs nothing.
    """

    committed: bool
    position_written: bool
    conversions_to_write: int
    conversions_written: int
    #: How many are already recorded. Non-zero blocks the write unless the
    #: caller says to replace them — importing the same history twice would
    #: double the realised gain, and nothing downstream could tell.
    existing_conversions: int
    replaced_conversions: int
    resulting_balance: MoneyStr
    realised: RealisedSplit
    warnings: list[str]
