"""The conversion-execution interface — deliberately not implemented.

This module exists so that a future, separately enabled module has a shape to
fill in, and so that the guard rails around it are written down and tested
*before* any such module exists.

There is no implementation here that can move money.  The only concrete class is
:class:`DisabledExecutor`, which refuses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable

from app.database import utcnow
from app.logging_setup import get_logger

log = get_logger(__name__)

#: Every condition a future execution module must satisfy before it may act.
#: Written here so the requirement is version-controlled, reviewable and
#: testable rather than living only in a specification document.
EXECUTION_REQUIREMENTS: tuple[str, ...] = (
    "An explicit feature flag, disabled by default.",
    "A separate acknowledgement from the user, distinct from enabling the flag.",
    "A preview showing the rate, the fee and the resulting target amount.",
    "Human confirmation of that specific preview.",
    "A configured maximum amount per conversion.",
    "An idempotency key, so a retry cannot convert twice.",
    "A complete audit log entry before and after the attempt.",
    "No unattended retry after an ambiguous result.",
    "No execution from a single stale quote.",
    "No execution after the quote's expiry time.",
    "An emergency disable switch that takes effect immediately.",
)


class ExecutionDisabledError(RuntimeError):
    """Raised whenever execution is attempted. There is no path past this."""


@dataclass(frozen=True, slots=True)
class ConversionPreview:
    """What a conversion would produce, if a future module could perform one."""

    preview_id: str
    source_currency: str
    target_currency: str
    source_amount: Decimal
    target_amount: Decimal
    rate: Decimal
    fee: Decimal
    fee_currency: str
    expires_at: datetime | None
    created_at: datetime = field(default_factory=utcnow)

    @property
    def expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= utcnow()


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """The outcome of an execution attempt."""

    executed: bool
    provider_transaction_id: str | None
    source_amount: Decimal
    target_amount: Decimal
    rate: Decimal
    executed_at: datetime | None
    message: str


@runtime_checkable
class ConversionExecutor(Protocol):
    """The interface a future execution module would implement."""

    async def preview_conversion(
        self,
        source_currency: str,
        target_currency: str,
        source_amount: Decimal,
    ) -> ConversionPreview: ...

    async def execute_conversion(
        self,
        preview_id: str,
        idempotency_key: str,
        confirmation_token: str,
    ) -> ConversionResult: ...


class DisabledExecutor:
    """The only executor this application ships.

    Both methods refuse. This is not a stub waiting to be filled in: enabling
    execution would mean adding a new module that satisfies every item in
    :data:`EXECUTION_REQUIREMENTS`, and this class stays as the default.
    """

    enabled = False

    async def preview_conversion(
        self,
        source_currency: str,
        target_currency: str,
        source_amount: Decimal,
    ) -> ConversionPreview:
        raise ExecutionDisabledError(
            "This application does not execute conversions. "
            "Use the Wise quote endpoint for a fee estimate instead."
        )

    async def execute_conversion(
        self,
        preview_id: str,
        idempotency_key: str,
        confirmation_token: str,
    ) -> ConversionResult:
        # Logged at error level: an attempt to reach this code means something
        # is calling an interface this application intentionally does not offer.
        log.error(
            "execution_attempt_refused",
            preview_id=preview_id,
            has_idempotency_key=bool(idempotency_key),
            has_confirmation_token=bool(confirmation_token),
        )
        raise ExecutionDisabledError(
            "Automatic conversion is not implemented and is not enabled. "
            "This application never moves money."
        )


def get_executor() -> DisabledExecutor:
    """The active executor. Always the disabled one."""
    return DisabledExecutor()
