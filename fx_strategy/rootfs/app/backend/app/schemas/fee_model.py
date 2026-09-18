"""Fee model schemas.

A fee model is what a provider charges, entered once and reused. It outlived
the conversion ladder that used to reference it: the charge is a fact about the
provider, not part of a plan.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import Field, field_validator, model_validator

from app.money import ALLOWED_CURRENCIES
from app.schemas.common import MoneyStr, Schema, StrictSchema

FeeTypeName = Literal["percentage", "fixed_plus_percentage", "quote_only", "manual"]


class FeeModelIn(StrictSchema):
    name: str = Field(min_length=1, max_length=80)
    fee_type: FeeTypeName
    fixed_fee: MoneyStr = Decimal(0)
    percentage_fee: MoneyStr = Decimal(0)
    minimum_fee: MoneyStr | None = None
    maximum_fee: MoneyStr | None = None
    currency: str = "USD"
    provider: str = "wise"

    @field_validator("currency")
    @classmethod
    def _known_currency(cls, value: str) -> str:
        code = value.strip().upper()
        if code not in ALLOWED_CURRENCIES:
            raise ValueError(f"{value!r} is not a supported currency code")
        return code

    @model_validator(mode="after")
    def _coherent(self) -> FeeModelIn:
        if self.fixed_fee < 0 or self.percentage_fee < 0:
            raise ValueError("Fees cannot be negative.")
        if self.percentage_fee > Decimal(100):
            raise ValueError("A percentage fee above 100% is not plausible.")
        if (
            self.minimum_fee is not None
            and self.maximum_fee is not None
            and self.minimum_fee > self.maximum_fee
        ):
            raise ValueError("The minimum fee cannot exceed the maximum fee.")
        return self


class FeeModelOut(Schema):
    id: int
    name: str
    fee_type: str
    fixed_fee: MoneyStr
    percentage_fee: MoneyStr
    minimum_fee: MoneyStr | None
    maximum_fee: MoneyStr | None
    currency: str
    provider: str
