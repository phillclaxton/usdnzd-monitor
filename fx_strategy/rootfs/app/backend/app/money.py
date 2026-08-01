"""Decimal helpers.

Every monetary value, exchange rate, fee and percentage in this application is a
:class:`decimal.Decimal`.  Binary floating point is never used for financial
values; the only float in the codebase is an unindexed convenience column used
to accelerate SQL min/max queries, and it never reaches a user-facing figure.

Precision, per the product specification:

======================  ========
Exchange rate storage   8 places
Currency calculation    4 places
Currency display        2 places
Target rate input       4 places
======================  ========
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, localcontext
from typing import Final

RATE_PLACES: Final = 8
MONEY_PLACES: Final = 4
DISPLAY_PLACES: Final = 2
TARGET_PLACES: Final = 4
PERCENT_PLACES: Final = 6

RATE_QUANT: Final = Decimal(1).scaleb(-RATE_PLACES)
MONEY_QUANT: Final = Decimal(1).scaleb(-MONEY_PLACES)
DISPLAY_QUANT: Final = Decimal(1).scaleb(-DISPLAY_PLACES)
TARGET_QUANT: Final = Decimal(1).scaleb(-TARGET_PLACES)
PERCENT_QUANT: Final = Decimal(1).scaleb(-PERCENT_PLACES)

ZERO: Final = Decimal(0)
ONE_CENT: Final = Decimal("0.01")

#: Currency codes the application will accept. Deliberately an allow-list.
ALLOWED_CURRENCIES: Final[frozenset[str]] = frozenset(
    {
        "AUD",
        "CAD",
        "CHF",
        "CNY",
        "EUR",
        "GBP",
        "HKD",
        "JPY",
        "NOK",
        "NZD",
        "SEK",
        "SGD",
        "USD",
        "ZAR",
    }
)

#: Rate movements shown in the downside/sensitivity table.
STANDARD_MOVEMENTS: Final[tuple[Decimal, ...]] = (
    Decimal("0.0050"),
    Decimal("0.0100"),
    Decimal("0.0200"),
    Decimal("0.0300"),
    Decimal("0.0500"),
    Decimal("0.1000"),
)


class MoneyError(ValueError):
    """Raised when a value cannot be used as a financial quantity."""


def to_decimal(value: Decimal | int | str | float, *, field: str = "value") -> Decimal:
    """Coerce ``value`` to a finite :class:`Decimal`.

    Floats are accepted only because JSON payloads and CSV files can produce
    them; they are routed through :func:`repr` so that ``0.1`` becomes
    ``Decimal("0.1")`` rather than the binary expansion.  Rejecting NaN and
    infinity here means the rest of the codebase never has to.
    """
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, float):
        candidate = Decimal(repr(value))
    else:
        try:
            candidate = Decimal(str(value).strip())
        except (InvalidOperation, ArithmeticError) as exc:
            raise MoneyError(f"{field} is not a valid decimal number: {value!r}") from exc

    if not candidate.is_finite():
        raise MoneyError(f"{field} must be a finite number, got {value!r}")
    return candidate


def quantize_rate(value: Decimal | int | str | float, *, field: str = "rate") -> Decimal:
    """Round to the exchange-rate storage precision (8 places)."""
    return to_decimal(value, field=field).quantize(RATE_QUANT)


def quantize_money(value: Decimal | int | str | float, *, field: str = "amount") -> Decimal:
    """Round to the currency calculation precision (4 places)."""
    return to_decimal(value, field=field).quantize(MONEY_QUANT)


def quantize_display(value: Decimal | int | str | float, *, field: str = "amount") -> Decimal:
    """Round to the currency display precision (2 places)."""
    return to_decimal(value, field=field).quantize(DISPLAY_QUANT)


def quantize_target(value: Decimal | int | str | float, *, field: str = "target") -> Decimal:
    """Round to the target-rate input precision (4 places)."""
    return to_decimal(value, field=field).quantize(TARGET_QUANT)


def quantize_percent(value: Decimal | int | str | float, *, field: str = "percent") -> Decimal:
    return to_decimal(value, field=field).quantize(PERCENT_QUANT)


def require_positive(value: Decimal, *, field: str = "amount") -> Decimal:
    """Reject zero and negative amounts."""
    if value <= ZERO:
        raise MoneyError(f"{field} must be greater than zero, got {value}")
    return value


def require_non_negative(value: Decimal, *, field: str = "amount") -> Decimal:
    if value < ZERO:
        raise MoneyError(f"{field} must not be negative, got {value}")
    return value


def require_currency(code: str, *, field: str = "currency") -> str:
    """Validate a currency code against the allow-list."""
    normalized = (code or "").strip().upper()
    if normalized not in ALLOWED_CURRENCIES:
        raise MoneyError(f"{field} {code!r} is not a supported currency code")
    return normalized


def safe_divide(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    """Divide with extra working precision, returning ``None`` for a zero divisor.

    Callers use ``None`` to mean "not calculable", which the UI renders as a
    dash rather than a misleading zero.
    """
    if denominator == ZERO:
        return None
    with localcontext() as ctx:
        ctx.prec = 34
        return numerator / denominator


def invert_rate(rate: Decimal) -> Decimal:
    """Invert a quoted rate, e.g. USD per NZD -> NZD per USD."""
    if rate <= ZERO:
        raise MoneyError(f"cannot invert a non-positive rate: {rate}")
    with localcontext() as ctx:
        ctx.prec = 34
        return (Decimal(1) / rate).quantize(RATE_QUANT)


def decimal_to_str(value: Decimal | None) -> str | None:
    """Render a Decimal for JSON as a plain string, never scientific notation.

    The stored scale is preserved so the frontend can tell ``1.75`` (a target a
    user typed) from ``1.75000000`` (a stored rate) if it ever needs to.
    """
    if value is None:
        return None
    return format(value, "f")
