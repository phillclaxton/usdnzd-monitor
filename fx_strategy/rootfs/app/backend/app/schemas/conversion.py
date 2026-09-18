"""Conversion schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field, model_validator

from app.schemas.common import MoneyStr, RateStr, Schema, StrictSchema

RecordSourceName = Literal["manual", "wise_api", "csv_import", "simulation"]


class ConversionIn(StrictSchema):
    executed_at: datetime
    source_amount: MoneyStr
    target_amount: MoneyStr
    fee_source_currency: MoneyStr | None = None
    fee_target_currency: MoneyStr | None = None
    #: The rate Wise displayed. When omitted it is derived from the amounts.
    gross_rate: RateStr | None = None
    provider: str = Field(default="wise", max_length=32)
    provider_transaction_id: str | None = Field(default=None, max_length=128)
    notes: str = Field(default="", max_length=2000)
    record_source: RecordSourceName = "manual"
    simulated: bool = False
    #: At least one amount is reconstructed rather than taken from a receipt.
    amounts_estimated: bool = False
    receipt_filename: str | None = Field(default=None, max_length=200)
    #: Set when fixing an earlier mis-entry, which may exceed the remaining balance.
    correcting_earlier_record: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> ConversionIn:
        if self.source_amount <= 0:
            raise ValueError("The converted amount must be greater than zero.")
        if self.target_amount <= 0:
            raise ValueError("The amount received must be greater than zero.")
        for fee in (self.fee_source_currency, self.fee_target_currency):
            if fee is not None and fee < 0:
                raise ValueError("A fee cannot be negative.")
        return self


class ConversionUpdate(ConversionIn):
    """Correcting an existing record. The previous values are kept in the audit trail."""

    correction_reason: str = Field(default="", max_length=500)


class ConversionOut(Schema):
    id: int
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
    amounts_estimated: bool
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
