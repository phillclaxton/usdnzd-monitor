"""Shared schema building blocks.

Decimals cross the API boundary as JSON *strings*.  Emitting them as JSON
numbers would hand the browser a float and silently reintroduce the binary
rounding this application exists to avoid.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, PlainSerializer

from app.money import (
    MONEY_QUANT,
    RATE_QUANT,
    TARGET_QUANT,
    MoneyError,
    to_decimal,
)


def _parse_decimal(value: Any) -> Any:
    if value is None or isinstance(value, Decimal):
        return value
    try:
        return to_decimal(value)
    except MoneyError as exc:
        raise ValueError(str(exc)) from exc


def _quantizer(quant: Decimal) -> Any:
    def _apply(value: Any) -> Any:
        parsed = _parse_decimal(value)
        return parsed.quantize(quant) if isinstance(parsed, Decimal) else parsed

    return _apply


def _render(value: Any) -> str | None:
    """Render a Decimal as a plain string.

    Tolerates a value that is already a string so a serialization error can
    never take down a settings response.
    """
    if value is None:
        return None
    return format(value, "f") if isinstance(value, Decimal) else str(value)


_serialize = PlainSerializer(_render, return_type=str)

#: A decimal with no enforced scale.
DecimalStr = Annotated[Decimal, BeforeValidator(_parse_decimal), _serialize]
#: Money at 4-place calculation precision.
MoneyStr = Annotated[Decimal, BeforeValidator(_quantizer(MONEY_QUANT)), _serialize]
#: An exchange rate at 8-place storage precision.
RateStr = Annotated[Decimal, BeforeValidator(_quantizer(RATE_QUANT)), _serialize]
#: A user-entered target rate at 4-place precision.
TargetStr = Annotated[Decimal, BeforeValidator(_quantizer(TARGET_QUANT)), _serialize]


class Schema(BaseModel):
    """Base for response models read from ORM objects."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class StrictSchema(BaseModel):
    """Base for request models: unknown fields are an error, not a shrug."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Message(Schema):
    """Generic acknowledgement payload."""

    ok: bool = True
    message: str = ""
