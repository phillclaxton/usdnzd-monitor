"""Conversion schemas."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import Field, model_validator

from app.schemas.common import MoneyStr, RateStr, Schema, StrictSchema

RecordSourceName = Literal["manual", "wise_api", "csv_import", "simulation"]


class TrancheAllocationIn(StrictSchema):
    """Part of one conversion attributed to a tranche."""

    tranche_id: int
    source_amount: MoneyStr

    @model_validator(mode="after")
    def _positive(self) -> TrancheAllocationIn:
        if self.source_amount <= 0:
            raise ValueError("Each allocation must be greater than zero.")
        return self


class ConversionIn(StrictSchema):
    strategy_id: int
    executed_at: datetime
    source_amount: MoneyStr
    target_amount: MoneyStr
    fee_source_currency: MoneyStr | None = None
    fee_target_currency: MoneyStr | None = None
    #: The rate Wise displayed. When omitted it is derived from the amounts.
    gross_rate: RateStr | None = None
    provider: str = Field(default="wise", max_length=32)
    provider_transaction_id: str | None = Field(default=None, max_length=128)
    tranche_id: int | None = None
    #: Splits one conversion across several tranches. Overrides ``tranche_id``.
    allocations: list[TrancheAllocationIn] = Field(default_factory=list)
    notes: str = Field(default="", max_length=2000)
    record_source: RecordSourceName = "manual"
    simulated: bool = False
    receipt_filename: str | None = Field(default=None, max_length=200)
    #: Set when fixing an earlier mis-entry, which may exceed the remaining balance.
    correcting_earlier_record: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> ConversionIn:
        if self.source_amount <= 0:
            raise ValueError("The converted amount must be greater than zero.")
        if self.target_amount <= 0:
            raise ValueError("The amount received must be greater than zero.")
        if self.allocations:
            total = sum((item.source_amount for item in self.allocations), Decimal(0))
            if total != self.source_amount:
                raise ValueError(
                    f"The tranche allocations total {total}, but the conversion is "
                    f"{self.source_amount}. They must match exactly."
                )
            ids = [item.tranche_id for item in self.allocations]
            if len(ids) != len(set(ids)):
                raise ValueError("Each tranche may appear only once in the allocations.")
        for fee in (self.fee_source_currency, self.fee_target_currency):
            if fee is not None and fee < 0:
                raise ValueError("A fee cannot be negative.")
        return self


class ConversionUpdate(ConversionIn):
    """Correcting an existing record. The previous values are kept in the audit trail."""

    correction_reason: str = Field(default="", max_length=500)


class ConversionOut(Schema):
    id: int
    strategy_id: int
    tranche_id: int | None
    source_amount: MoneyStr
    target_amount: MoneyStr
    gross_rate: RateStr
    effective_rate: RateStr
    fee_source_currency: MoneyStr | None
    fee_target_currency: MoneyStr | None
    fee_total_target_equivalent: MoneyStr | None
    provider: str
    provider_transaction_id: str | None
    executed_at: datetime
    record_source: str
    simulated: bool
    notes: str
    receipt_filename: str | None
    created_at: datetime
    updated_at: datetime


class ConversionListOut(Schema):
    conversions: list[ConversionOut]
    total_source_amount: MoneyStr
    total_target_amount: MoneyStr
    blended_gross_rate: RateStr | None
    blended_effective_rate: RateStr | None
    total_fees: MoneyStr | None


class ConversionImportPreview(Schema):
    total_rows: int
    accepted: int
    rejected: int
    duplicates: int
    errors: list[dict[str, Any]]
    sample: list[dict[str, Any]]
    imported: int = 0
    committed: bool = False
