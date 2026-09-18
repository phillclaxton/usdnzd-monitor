"""Recording conversions that actually happened.

A conversion is a fact about money that moved. It belongs to no plan: there is
nothing to allocate it against here and no remaining balance to check it
against, so what is left are the rules that protect the record itself.

* Amounts must be positive.
* A repeated provider transaction ID is refused, so a reconciliation run or a
  re-imported CSV cannot double-count a conversion.
* Corrections keep the previous values in the audit trail rather than
  overwriting them silently.

Whether more was converted than is actually held is the *position's* question,
and ``POST /fx/conversions`` asks it there, where the balance lives.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.logging_setup import get_logger
from app.models.audit import AuditEventType
from app.models.strategy import Conversion, RecordSource
from app.money import ZERO, MoneyError, quantize_money, quantize_rate, safe_divide
from app.schemas.conversion import ConversionIn
from app.services import audit
from app.services import calculations as calc

log = get_logger(__name__)


class ConversionError(ValueError):
    """A conversion record was rejected."""


class DuplicateConversionError(ConversionError):
    """This provider transaction has already been recorded."""


def derive_gross_rate(source_amount: Decimal, target_amount: Decimal) -> Decimal:
    """The rate implied by the amounts, when the user did not supply one."""
    rate = safe_divide(target_amount, source_amount)
    if rate is None:
        raise ConversionError("The converted amount must be greater than zero.")
    return quantize_rate(rate)


def total_fee_in_target(
    fee_source: Decimal | None, fee_target: Decimal | None, rate: Decimal
) -> Decimal | None:
    """Combine source-side and target-side fees into one target-currency figure.

    Returns ``None`` when no fee was recorded at all, so the UI can say the fee
    is unknown rather than showing zero.
    """
    if fee_source is None and fee_target is None:
        return None
    total = ZERO
    if fee_source is not None:
        total += fee_source * rate
    if fee_target is not None:
        total += fee_target
    return quantize_money(total, field="fee")


async def find_duplicate(
    session: AsyncSession,
    provider: str,
    transaction_id: str | None,
    *,
    exclude_id: int | None = None,
) -> Conversion | None:
    if not transaction_id:
        return None
    stmt = select(Conversion).where(
        Conversion.provider == provider,
        Conversion.provider_transaction_id == transaction_id,
    )
    if exclude_id is not None:
        stmt = stmt.where(Conversion.id != exclude_id)
    return (await session.execute(stmt)).scalars().first()


async def create_conversion(
    session: AsyncSession,
    payload: ConversionIn,
    *,
    currencies: tuple[str, str] | None = None,
    actor: str = "user",
) -> Conversion:
    """Record a conversion that happened.

    ``currencies`` names the pair in the audit message. Without it the trail
    would read "Recorded 30000 converted to 52500", which looks like a bug six
    months later.
    """
    duplicate = await find_duplicate(session, payload.provider, payload.provider_transaction_id)
    if duplicate is not None:
        raise DuplicateConversionError(
            f"Transaction {payload.provider_transaction_id} is already recorded "
            f"(conversion {duplicate.id}, {duplicate.executed_at.date().isoformat()})."
        )

    try:
        # There is no recorded total to measure against, so only the amounts
        # themselves are checked. Whether more was converted than is held is the
        # position's question, and POST /fx/conversions asks it there.
        calc.validate_conversion_amounts(
            payload.source_amount,
            payload.target_amount,
            ZERO,
            allow_exceeding_remaining=True,
        )
    except MoneyError as exc:
        raise ConversionError(str(exc)) from exc

    gross_rate = payload.gross_rate or derive_gross_rate(
        payload.source_amount, payload.target_amount
    )
    fee_total = total_fee_in_target(
        payload.fee_source_currency, payload.fee_target_currency, gross_rate
    )

    conversion = Conversion(
        source_amount=payload.source_amount,
        target_amount=payload.target_amount,
        gross_rate=gross_rate,
        effective_rate=derive_gross_rate(payload.source_amount, payload.target_amount),
        fee_source_currency=payload.fee_source_currency,
        fee_target_currency=payload.fee_target_currency,
        fee_total_target_equivalent=fee_total,
        provider=payload.provider,
        provider_transaction_id=payload.provider_transaction_id,
        executed_at=payload.executed_at,
        record_source=str(RecordSource(payload.record_source)),
        simulated=payload.simulated,
        amounts_estimated=payload.amounts_estimated,
        notes=payload.notes,
        receipt_filename=payload.receipt_filename,
    )
    session.add(conversion)
    await session.flush()

    source_code, target_code = currencies if currencies is not None else ("?", "?")
    await audit.record(
        session,
        event_type=AuditEventType.CREATED,
        entity_type="conversion",
        entity_id=conversion.id,
        message=(
            f"Recorded {payload.source_amount} {source_code} converted to "
            f"{payload.target_amount} {target_code} at {gross_rate}"
            + (" (simulated)" if payload.simulated else "")
            + (" (amounts estimated)" if payload.amounts_estimated else "")
        ),
        after={
            "source_amount": payload.source_amount,
            "target_amount": payload.target_amount,
            "gross_rate": gross_rate,
            "executed_at": payload.executed_at,
            "provider_transaction_id": payload.provider_transaction_id,
            "correcting_earlier_record": payload.correcting_earlier_record,
            "amounts_estimated": payload.amounts_estimated,
        },
        actor=actor,
    )
    return conversion


async def update_conversion(
    session: AsyncSession,
    conversion: Conversion,
    payload: ConversionIn,
    *,
    reason: str = "",
    actor: str = "user",
) -> Conversion:
    """Correct a record, keeping the previous values in the audit trail."""
    duplicate = await find_duplicate(
        session,
        payload.provider,
        payload.provider_transaction_id,
        exclude_id=conversion.id,
    )
    if duplicate is not None:
        raise DuplicateConversionError(
            f"Transaction {payload.provider_transaction_id} is already recorded "
            f"on conversion {duplicate.id}."
        )

    before = {
        "source_amount": conversion.source_amount,
        "target_amount": conversion.target_amount,
        "gross_rate": conversion.gross_rate,
        "executed_at": conversion.executed_at,
        "fee_total_target_equivalent": conversion.fee_total_target_equivalent,
        "amounts_estimated": conversion.amounts_estimated,
        "notes": conversion.notes,
    }

    gross_rate = payload.gross_rate or derive_gross_rate(
        payload.source_amount, payload.target_amount
    )
    conversion.source_amount = payload.source_amount
    conversion.target_amount = payload.target_amount
    conversion.gross_rate = gross_rate
    conversion.effective_rate = derive_gross_rate(payload.source_amount, payload.target_amount)
    conversion.fee_source_currency = payload.fee_source_currency
    conversion.fee_target_currency = payload.fee_target_currency
    conversion.fee_total_target_equivalent = total_fee_in_target(
        payload.fee_source_currency, payload.fee_target_currency, gross_rate
    )
    conversion.provider = payload.provider
    conversion.provider_transaction_id = payload.provider_transaction_id
    conversion.executed_at = payload.executed_at
    conversion.notes = payload.notes
    conversion.simulated = payload.simulated
    conversion.amounts_estimated = payload.amounts_estimated
    conversion.receipt_filename = payload.receipt_filename
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type="conversion",
        entity_id=conversion.id,
        message=(
            f"Conversion {conversion.id} corrected"
            + (f": {reason}" if reason else ". No reason given.")
        ),
        before=before,
        after={
            "source_amount": conversion.source_amount,
            "target_amount": conversion.target_amount,
            "gross_rate": conversion.gross_rate,
            "executed_at": conversion.executed_at,
            "amounts_estimated": conversion.amounts_estimated,
            "notes": conversion.notes,
        },
        actor=actor,
    )
    return conversion


async def delete_conversion(
    session: AsyncSession,
    conversion: Conversion,
    *,
    reason: str = "",
    actor: str = "user",
) -> None:
    """Remove a record. The audit event keeps every value it held."""
    snapshot = {
        "source_amount": conversion.source_amount,
        "target_amount": conversion.target_amount,
        "gross_rate": conversion.gross_rate,
        "effective_rate": conversion.effective_rate,
        "fee_total_target_equivalent": conversion.fee_total_target_equivalent,
        "executed_at": conversion.executed_at,
        "provider": conversion.provider,
        "provider_transaction_id": conversion.provider_transaction_id,
        "amounts_estimated": conversion.amounts_estimated,
        "notes": conversion.notes,
        "record_source": conversion.record_source,
    }
    conversion_id = conversion.id
    await session.delete(conversion)
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.DELETED,
        entity_type="conversion",
        entity_id=conversion_id,
        message=(
            f"Conversion {conversion_id} deleted"
            + (f": {reason}" if reason else ". No reason given.")
        ),
        before=snapshot,
        actor=actor,
    )


async def list_conversions(
    session: AsyncSession,
    *,
    include_simulated: bool = True,
    limit: int = 500,
    offset: int = 0,
) -> list[Conversion]:
    stmt = select(Conversion).order_by(Conversion.executed_at.desc(), Conversion.id.desc())
    if not include_simulated:
        stmt = stmt.where(Conversion.simulated.is_(False))
    stmt = stmt.limit(min(limit, 5000)).offset(max(offset, 0))
    return list((await session.execute(stmt)).scalars().all())


def totals(conversions: Sequence[Conversion]) -> dict[str, Decimal | None]:
    """Aggregate a list of conversions for the list view."""
    real = [c for c in conversions if not c.simulated]
    completed = [
        calc.CompletedConversion(
            source_amount=c.source_amount,
            target_amount=c.target_amount,
            gross_rate=c.gross_rate,
            fee_target_equivalent=c.fee_total_target_equivalent,
        )
        for c in real
    ]
    return {
        "total_source_amount": quantize_money(sum((c.source_amount for c in real), ZERO)),
        "total_target_amount": quantize_money(sum((c.target_amount for c in real), ZERO)),
        "blended_gross_rate": calc.blended_gross_rate(completed),
        "blended_effective_rate": calc.blended_effective_rate(completed),
        "total_fees": calc.total_fees(completed),
    }
