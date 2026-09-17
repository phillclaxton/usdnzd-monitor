"""Recording conversions that actually happened.

The rules here protect the financial record:

* Amounts must be positive and, unless explicitly correcting history, cannot
  exceed what is still unconverted.
* A repeated provider transaction ID is refused, so a reconciliation run or a
  re-imported CSV cannot double-count a conversion.
* Corrections keep the previous values in the audit trail rather than
  overwriting them silently.
* A tranche is only marked completed by a recorded conversion — never by its
  target being reached.

A conversion does not need a strategy. Passing ``strategy=None`` records the
money that moved and nothing else: there is no ladder to allocate against, no
remaining balance to check, and no tranche status to refresh.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.logging_setup import get_logger
from app.models.audit import AuditEventType
from app.models.strategy import (
    Conversion,
    RecordSource,
    Strategy,
    Tranche,
    TrancheStatus,
)
from app.money import ZERO, MoneyError, quantize_money, quantize_rate, safe_divide
from app.schemas.conversion import ConversionIn, TrancheAllocationIn
from app.services import audit
from app.services import calculations as calc
from app.services import strategy_service as strategies

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


def _currency_pair(
    strategy: Strategy | None, currencies: tuple[str, str] | None
) -> tuple[str, str]:
    """The pair to name in the audit message.

    The strategy is authoritative when there is one; otherwise the caller passes
    the configured pair. Falling back to empty strings would put "Recorded 30000
    converted to 52500" in the audit trail, which reads like a bug six months
    later.
    """
    if strategy is not None:
        return strategy.source_currency, strategy.target_currency
    if currencies is not None:
        return currencies
    return "?", "?"


def _allocations_for(payload: ConversionIn) -> list[TrancheAllocationIn]:
    if payload.allocations:
        return list(payload.allocations)
    if payload.tranche_id is not None:
        return [
            TrancheAllocationIn(tranche_id=payload.tranche_id, source_amount=payload.source_amount)
        ]
    return []


async def _validate_tranches(
    session: AsyncSession, strategy: Strategy, allocations: Sequence[TrancheAllocationIn]
) -> dict[int, Tranche]:
    by_id = {tranche.id: tranche for tranche in strategy.tranches}
    resolved: dict[int, Tranche] = {}
    for allocation in allocations:
        tranche = by_id.get(allocation.tranche_id)
        if tranche is None:
            raise ConversionError(
                f"Tranche {allocation.tranche_id} does not belong to strategy {strategy.id}."
            )
        resolved[allocation.tranche_id] = tranche
    return resolved


def refresh_tranche_status(strategy: Strategy, tranche: Tranche) -> None:
    """Set a tranche's status from what has actually been converted against it."""
    converted = quantize_money(
        sum((c.source_amount for c in strategies.tranche_conversions(strategy, tranche.id)), ZERO)
    )
    if tranche.status in (str(TrancheStatus.SKIPPED), str(TrancheStatus.CANCELLED)):
        return
    if converted <= ZERO:
        # Back to whatever the rate says, not to "completed".
        tranche.status = (
            str(TrancheStatus.TARGET_REACHED)
            if tranche.target_first_reached_at is not None
            else str(TrancheStatus.PENDING)
        )
        tranche.completed_at = None
        return
    if converted >= tranche.calculated_source_amount:
        tranche.status = str(TrancheStatus.COMPLETED)
        tranche.completed_at = tranche.completed_at or utcnow()
    else:
        tranche.status = str(TrancheStatus.PARTIALLY_COMPLETED)
        tranche.completed_at = None


async def create_conversion(
    session: AsyncSession,
    strategy: Strategy | None,
    payload: ConversionIn,
    *,
    currencies: tuple[str, str] | None = None,
    actor: str = "user",
) -> list[Conversion]:
    """Record a conversion, optionally split across several tranches.

    Returns one row per tranche allocation, or a single unassigned row.

    Without a strategy there is nothing to allocate against, so the record is a
    single unassigned row. ``currencies`` names the pair for the audit message,
    which otherwise comes from the strategy.
    """
    duplicate = await find_duplicate(session, payload.provider, payload.provider_transaction_id)
    if duplicate is not None:
        raise DuplicateConversionError(
            f"Transaction {payload.provider_transaction_id} is already recorded "
            f"(conversion {duplicate.id}, {duplicate.executed_at.date().isoformat()})."
        )

    allocations = _allocations_for(payload)
    if strategy is None and allocations:
        raise ConversionError(
            "A tranche belongs to a strategy, so a conversion cannot be assigned to "
            "one without naming the strategy."
        )

    try:
        calc.validate_conversion_amounts(
            payload.source_amount,
            payload.target_amount,
            # With no strategy there is no recorded total to measure against, so
            # only the amounts themselves can be checked. Inventing a remaining
            # balance here would reject perfectly good history.
            strategies.remaining_amount(strategy) if strategy is not None else ZERO,
            allow_exceeding_remaining=(
                payload.correcting_earlier_record if strategy is not None else True
            ),
        )
    except MoneyError as exc:
        raise ConversionError(str(exc)) from exc

    gross_rate = payload.gross_rate or derive_gross_rate(
        payload.source_amount, payload.target_amount
    )
    tranches = (
        await _validate_tranches(session, strategy, allocations) if strategy is not None else {}
    )

    created: list[Conversion] = []
    parts = allocations or [TrancheAllocationIn(tranche_id=0, source_amount=payload.source_amount)]

    for index, part in enumerate(parts):
        share = safe_divide(part.source_amount, payload.source_amount) or Decimal(1)
        # The last part takes the rounding residue so the parts sum exactly to
        # the amount the user entered.
        if index == len(parts) - 1:
            already = sum((c.target_amount for c in created), ZERO)
            target_amount = quantize_money(payload.target_amount - already)
        else:
            target_amount = quantize_money(payload.target_amount * share)

        fee_source = (
            quantize_money(payload.fee_source_currency * share)
            if payload.fee_source_currency is not None
            else None
        )
        fee_target = (
            quantize_money(payload.fee_target_currency * share)
            if payload.fee_target_currency is not None
            else None
        )
        fee_total = total_fee_in_target(fee_source, fee_target, gross_rate)
        net = calc.net_proceeds(target_amount, None)
        _ = net  # target_amount is already what arrived; kept for clarity

        conversion = Conversion(
            strategy_id=strategy.id if strategy is not None else None,
            tranche_id=part.tranche_id or None,
            source_amount=part.source_amount,
            target_amount=target_amount,
            gross_rate=gross_rate,
            effective_rate=derive_gross_rate(part.source_amount, target_amount),
            fee_source_currency=fee_source,
            fee_target_currency=fee_target,
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
        if strategy is not None:
            strategy.conversions.append(conversion)
        created.append(conversion)

    await session.flush()

    if strategy is not None:
        for tranche in tranches.values():
            refresh_tranche_status(strategy, tranche)
        await session.flush()

    source_code, target_code = _currency_pair(strategy, currencies)
    await audit.record(
        session,
        event_type=AuditEventType.CREATED,
        entity_type="conversion",
        entity_id=created[0].id,
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
            "tranche_ids": [c.tranche_id for c in created],
            "provider_transaction_id": payload.provider_transaction_id,
            "correcting_earlier_record": payload.correcting_earlier_record,
            "amounts_estimated": payload.amounts_estimated,
        },
        actor=actor,
    )
    return created


async def update_conversion(
    session: AsyncSession,
    strategy: Strategy | None,
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
        "tranche_id": conversion.tranche_id,
        "fee_total_target_equivalent": conversion.fee_total_target_equivalent,
        "amounts_estimated": conversion.amounts_estimated,
        "notes": conversion.notes,
    }
    previous_tranche_id = conversion.tranche_id

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
    conversion.tranche_id = payload.tranche_id
    conversion.notes = payload.notes
    conversion.simulated = payload.simulated
    conversion.amounts_estimated = payload.amounts_estimated
    conversion.receipt_filename = payload.receipt_filename
    await session.flush()

    if strategy is not None:
        for tranche in strategy.tranches:
            if tranche.id in (previous_tranche_id, payload.tranche_id):
                refresh_tranche_status(strategy, tranche)
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
            "tranche_id": conversion.tranche_id,
            "amounts_estimated": conversion.amounts_estimated,
            "notes": conversion.notes,
        },
        actor=actor,
    )
    return conversion


async def delete_conversion(
    session: AsyncSession,
    strategy: Strategy | None,
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
        "tranche_id": conversion.tranche_id,
        "amounts_estimated": conversion.amounts_estimated,
        "notes": conversion.notes,
        "record_source": conversion.record_source,
    }
    conversion_id = conversion.id
    tranche_id = conversion.tranche_id

    # Removing it from the collection is what deletes the row under
    # ``cascade="all, delete-orphan"``. With no strategy the collection is never
    # touched and ``session.delete`` does the work directly — same outcome by a
    # different route, which is why both paths are tested.
    if strategy is not None and conversion in strategy.conversions:
        strategy.conversions.remove(conversion)
    await session.delete(conversion)
    await session.flush()

    if strategy is not None:
        for tranche in strategy.tranches:
            if tranche.id == tranche_id:
                refresh_tranche_status(strategy, tranche)
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
    strategy_id: int | None = None,
    tranche_id: int | None = None,
    include_simulated: bool = True,
    limit: int = 500,
    offset: int = 0,
) -> list[Conversion]:
    stmt = select(Conversion).order_by(Conversion.executed_at.desc(), Conversion.id.desc())
    if strategy_id is not None:
        stmt = stmt.where(Conversion.strategy_id == strategy_id)
    if tranche_id is not None:
        stmt = stmt.where(Conversion.tranche_id == tranche_id)
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
