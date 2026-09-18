"""Reading and changing the FX position.

The function names here are the ones the specification asks for — ``get_state``,
``update_state``, ``get_conversion_history``, ``record_conversion``,
``get_alert_history`` — so that a later ChatGPT integration is a new transport
over these calls rather than a second implementation of them. Everything the
``/fx`` router does, it does by calling one of these.

The arithmetic lives in :mod:`app.services.position_math` and the database
access lives here; neither reaches into the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import utcnow
from app.logging_setup import get_logger
from app.models.alert import NotificationLog
from app.models.audit import AuditEventType
from app.models.position import POSITION_ID, FxAlertState, FxPosition
from app.models.strategy import Conversion
from app.money import ZERO, quantize_money
from app.schemas.position import (
    FxStateOut,
    ImportConversion,
    PositionIn,
    PositionMetrics,
    PositionOut,
    PositionPatch,
    RealisedSplit,
    RecordConversionIn,
    StateImport,
)
from app.schemas.settings import Settings
from app.services import audit, conversion_service, position_math, rate_service
from app.services import calculations as calc
from app.services.conversion_service import ConversionError, derive_gross_rate
from app.services.position_math import ConversionFact, RealisedTotal
from app.services.rate_service import CurrentRate

log = get_logger(__name__)

#: Columns the manual state form owns, in the order the form shows them.
POSITION_FIELDS = (
    "source_currency",
    "target_currency",
    "current_source_balance",
    "baseline_rate",
    "baseline_date",
    "floating_loan_rate",
    "current_offset_shortfall_nzd",
    "monthly_nzd_burn",
    "notes",
)


class PositionError(ValueError):
    """The position could not be read or changed as asked."""


@dataclass(slots=True)
class PositionState:
    """Everything ``GET /fx/state`` answers with."""

    position: FxPosition | None
    metrics: dict[str, Any] | None
    latest_conversion: Conversion | None
    conversion_count: int


def _fact(conversion: Conversion) -> ConversionFact:
    return ConversionFact(
        source_amount=conversion.source_amount,
        target_amount=conversion.target_amount,
        fee_source_currency=conversion.fee_source_currency,
        amounts_estimated=conversion.amounts_estimated,
    )


def _audit_values(position: FxPosition) -> dict[str, Any]:
    return {name: getattr(position, name) for name in POSITION_FIELDS}


async def get_position(session: AsyncSession) -> FxPosition | None:
    """The one position row, or ``None`` before anything has been entered."""
    return await session.get(FxPosition, POSITION_ID)


async def _real_conversions(session: AsyncSession) -> list[Conversion]:
    """Recorded conversions, oldest first.

    Oldest first because the cumulative column only means anything in the order
    the conversions happened. Simulated rows are excluded: a replay must not
    change what the position is said to be worth.
    """
    stmt = (
        select(Conversion)
        .where(Conversion.simulated.is_(False))
        .order_by(Conversion.executed_at.asc(), Conversion.id.asc())
    )
    return list((await session.execute(stmt)).scalars().all())


def _realised(conversions: list[Conversion], baseline: Decimal | None) -> RealisedTotal:
    return position_math.cumulative_realised([_fact(row) for row in conversions], baseline)


def _metrics(
    position: FxPosition, conversions: list[Conversion], current: CurrentRate
) -> dict[str, Any]:
    """Every derived figure, computed fresh from what is stored.

    Nothing in here is persisted, so there is no stored total that can drift out
    of step with the rows it came from.
    """
    rate = current.rate
    baseline = position.baseline_rate
    balance = position.current_source_balance

    realised = _realised(conversions, baseline)
    unrealised = position_math.unrealised_improvement(balance, rate, baseline)
    target_value = position_math.current_value(balance, rate)

    completed = [
        calc.CompletedConversion(
            source_amount=row.source_amount,
            target_amount=row.target_amount,
            gross_rate=row.gross_rate,
            fee_target_equivalent=row.fee_total_target_equivalent,
        )
        for row in conversions
    ]

    return {
        "current_rate": rate,
        "rate_status": current.status,
        "current_target_value": target_value,
        "realised": realised,
        "unrealised_improvement": unrealised,
        # Deliberately the *confirmed* half plus unrealised: a single headline a
        # consumer can rely on, with the estimated part left visible in
        # ``realised`` rather than folded in here.
        "total_improvement_confirmed": position_math.total_improvement(
            realised.confirmed, unrealised
        ),
        "total_source_converted": quantize_money(
            sum((row.source_amount for row in conversions), ZERO), field="total converted"
        ),
        "total_target_received": quantize_money(
            sum((row.target_amount for row in conversions), ZERO), field="total received"
        ),
        "total_fees_target": calc.total_fees(completed),
        "daily_carrying_cost": position_math.daily_carrying_cost(
            position.current_offset_shortfall_nzd, position.floating_loan_rate
        ),
        "monthly_carrying_cost": position_math.monthly_carrying_cost(
            position.current_offset_shortfall_nzd, position.floating_loan_rate
        ),
        "months_of_burn": position_math.months_of_burn(target_value, position.monthly_nzd_burn),
    }


async def get_state(session: AsyncSession, settings: Settings) -> PositionState:
    """The position and everything derived from it.

    Returns a state with ``position`` set to ``None`` on a fresh install rather
    than raising: having entered nothing yet is a normal condition to describe,
    not an error.
    """
    position = await get_position(session)
    conversions = await _real_conversions(session)
    count = len(conversions)
    latest = conversions[-1] if conversions else None
    if position is None:
        return PositionState(
            position=None, metrics=None, latest_conversion=latest, conversion_count=count
        )
    current = await rate_service.current_rate(session, settings)
    return PositionState(
        position=position,
        metrics=_metrics(position, conversions, current),
        latest_conversion=latest,
        conversion_count=count,
    )


async def replace_state(
    session: AsyncSession, payload: PositionIn, *, actor: str = "user"
) -> FxPosition:
    """Write the whole position, creating it if this is the first time."""
    position = await get_position(session)
    before = _audit_values(position) if position is not None else None
    created = position is None
    if position is None:
        position = FxPosition(id=POSITION_ID)
        session.add(position)
    # Attributes, never ``model_dump``: these schemas serialize Decimals to
    # strings, so a dump would hand the database text where a Decimal belongs.
    for name in POSITION_FIELDS:
        setattr(position, name, getattr(payload, name))
    position.source_currency = position.source_currency.upper()
    position.target_currency = position.target_currency.upper()
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.CREATED if created else AuditEventType.UPDATED,
        entity_type="fx_position",
        entity_id=POSITION_ID,
        message=("Position created" if created else "Position updated"),
        before=before,
        after=_audit_values(position),
        actor=actor,
    )
    return position


async def update_state(
    session: AsyncSession, patch: PositionPatch, *, actor: str = "user"
) -> FxPosition:
    """Apply only the fields the caller actually sent.

    ``model_fields_set`` rather than a null check, so that clearing the baseline
    rate is expressible: a field sent as null means "clear this", a field left
    out means "leave it alone", and reading the attribute cannot tell them
    apart.
    """
    position = await get_position(session)
    if position is None:
        raise PositionError("There is no position to update yet. Save the position first.")
    before = _audit_values(position)
    changed = [name for name in POSITION_FIELDS if name in patch.model_fields_set]
    for name in changed:
        value = getattr(patch, name)
        if name in ("source_currency", "target_currency") and value is not None:
            value = str(value).upper()
        if name in ("current_source_balance", "notes") and value is None:
            # Neither is nullable in the database; sending null means "no change"
            # rather than "make this empty", because there is no empty balance.
            continue
        setattr(position, name, value)
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type="fx_position",
        entity_id=POSITION_ID,
        message=f"Position updated: {', '.join(changed) or 'no fields'}",
        before=before,
        after=_audit_values(position),
        actor=actor,
    )
    return position


@dataclass(slots=True)
class ConversionHistory:
    conversions: list[tuple[Conversion, position_math.ConversionImprovement]]
    baseline_rate: Decimal | None
    realised: RealisedTotal
    #: What was converted and received in total, from
    #: :func:`conversion_service.totals`. A property of the list, not of the
    #: position — a history with no position saved still has totals.
    totals: dict[str, Decimal | None]


async def get_conversion_history(
    session: AsyncSession, *, newest_first: bool = True
) -> ConversionHistory:
    """Every recorded conversion with what it gained against the baseline.

    The improvements are computed oldest-first so the cumulative column counts
    up the way the conversions happened, then the pairs are reversed for display
    if asked. Reversing after the arithmetic is what keeps the running total
    correct in both orders.
    """
    position = await get_position(session)
    baseline = position.baseline_rate if position is not None else None
    conversions = await _real_conversions(session)
    rows = position_math.improvements([_fact(row) for row in conversions], baseline)
    paired = list(zip(conversions, rows, strict=True))
    if newest_first:
        paired.reverse()
    return ConversionHistory(
        conversions=paired,
        baseline_rate=baseline,
        realised=_realised(conversions, baseline),
        totals=conversion_service.totals(conversions),
    )


async def record_conversion(
    session: AsyncSession,
    settings: Settings,
    payload: RecordConversionIn,
    *,
    actor: str = "user",
) -> Conversion:
    """Record a conversion that has just happened **and reduce the balance**.

    That side effect is the entire reason this exists beside
    ``POST /conversions``, which stays free of them.

    It deliberately does *not* touch the offset shortfall. Not every conversion
    goes to the mortgage, and assuming one did would quietly corrupt the
    carrying cost — the figure most likely to be acted on.
    """
    position = await get_position(session)
    if position is None:
        raise PositionError(
            "There is no position yet. Save the position before recording a conversion."
        )
    if payload.source_amount > position.current_source_balance:
        # Refused rather than clamped to zero. Clamping would swallow the
        # discrepancy and leave a balance that is merely plausible, when what
        # has actually happened is that the balance is out of date — and only
        # the person holding the money can say what it really is.
        raise PositionError(
            f"This conversion is {payload.source_amount} {position.source_currency}, but "
            f"the position holds {position.current_source_balance}. Update the balance "
            "first if more has arrived since it was last stated."
        )

    conversion_in = conversion_service.ConversionIn(
        executed_at=payload.executed_at or utcnow(),
        source_amount=payload.source_amount,
        target_amount=payload.target_amount,
        gross_rate=payload.gross_rate,
        fee_source_currency=payload.fee_source_currency,
        fee_target_currency=payload.fee_target_currency,
        provider=payload.provider,
        provider_transaction_id=payload.provider_transaction_id,
        notes=payload.notes,
        amounts_estimated=payload.amounts_estimated,
        # Whether more was converted than is held is checked below, against the
        # balance. The record itself only validates the amounts.
        correcting_earlier_record=True,
    )
    conversion = await conversion_service.create_conversion(
        session,
        conversion_in,
        currencies=(position.source_currency, position.target_currency),
        actor=actor,
    )

    before_balance = position.current_source_balance
    position.current_source_balance = quantize_money(
        before_balance - payload.source_amount, field="balance"
    )
    await session.flush()

    await audit.record(
        session,
        event_type=AuditEventType.UPDATED,
        entity_type="fx_position",
        entity_id=POSITION_ID,
        message=(
            f"Balance reduced by {payload.source_amount} {position.source_currency} "
            f"for conversion {conversion.id}"
        ),
        before={"current_source_balance": before_balance},
        after={"current_source_balance": position.current_source_balance},
        actor=actor,
    )
    return conversion


async def alert_states(session: AsyncSession) -> dict[str, FxAlertState]:
    """The stored value each movement-alert condition last spoke at, by key.

    A key is also the notification's ``entity_id``, which is what lets the log
    and this table be joined at all without either knowing about the other.
    """
    rows = (await session.execute(select(FxAlertState))).scalars().all()
    return {row.alert_key: row for row in rows}


async def get_alert_history(
    session: AsyncSession, *, limit: int = 100, offset: int = 0
) -> list[NotificationLog]:
    """What the app has actually said, newest first.

    Reads the existing notification log rather than a second store, so an alert
    appears here whether it came from the movement rules or from anything else.
    Undelivered rows are included: a notification that failed to send is
    precisely the one worth seeing.
    """
    stmt = (
        select(NotificationLog)
        .order_by(NotificationLog.created_at.desc(), NotificationLog.id.desc())
        .limit(min(limit, 1000))
        .offset(max(offset, 0))
    )
    return list((await session.execute(stmt)).scalars().all())


async def export_state(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    """The position as a portable document, split totals included."""
    state = await get_state(session, settings)
    position = state.position
    conversions = await _real_conversions(session)
    baseline = position.baseline_rate if position is not None else None
    realised = _realised(conversions, baseline)
    metrics = state.metrics or {}

    return {
        "as_of": utcnow(),
        "source_currency": position.source_currency
        if position
        else settings.general.source_currency,
        "target_currency": position.target_currency
        if position
        else settings.general.target_currency,
        "source_balance": position.current_source_balance if position else ZERO,
        "current_rate": metrics.get("current_rate"),
        "baseline_rate": baseline,
        "baseline_date": position.baseline_date if position else None,
        "offset_shortfall_nzd": position.current_offset_shortfall_nzd if position else None,
        "floating_rate": position.floating_loan_rate if position else None,
        "monthly_nzd_burn": position.monthly_nzd_burn if position else None,
        "realised_confirmed": realised.confirmed,
        "realised_estimated": realised.estimated,
        "realised_total": realised.total,
        "includes_estimates": realised.includes_estimates,
        "unrealised_gain_nzd": metrics.get("unrealised_improvement"),
        "conversions": [
            {
                "executed_at": row.executed_at.isoformat(),
                "source_amount": format(row.source_amount, "f"),
                "target_amount": format(row.target_amount, "f"),
                "gross_rate": format(row.gross_rate, "f"),
                "fee_source_currency": (
                    format(row.fee_source_currency, "f")
                    if row.fee_source_currency is not None
                    else None
                ),
                "fee_target_currency": (
                    format(row.fee_target_currency, "f")
                    if row.fee_target_currency is not None
                    else None
                ),
                "provider": row.provider,
                "provider_transaction_id": row.provider_transaction_id,
                "notes": row.notes,
                "amounts_estimated": row.amounts_estimated,
            }
            for row in conversions
        ],
    }


def _complete(row: ImportConversion) -> tuple[Decimal, Decimal, Decimal]:
    """Fill in whichever of source, target and rate was left out.

    Deriving the missing one is algebra on two known figures, not an estimate,
    which is why a row completed this way is not flagged as estimated. The
    schema has already guaranteed that two of the three are present.
    """
    source, target, rate = row.source_amount, row.target_amount, row.gross_rate
    if source is None:
        assert target is not None and rate is not None
        source = quantize_money(target / rate, field="converted amount")
    elif target is None:
        assert rate is not None
        target = quantize_money(source * rate, field="amount received")
    elif rate is None:
        rate = derive_gross_rate(source, target)
    return source, target, rate


async def import_state(
    session: AsyncSession,
    settings: Settings,
    payload: StateImport,
    *,
    commit: bool = False,
    replace_conversions: bool = False,
    actor: str = "user",
) -> dict[str, Any]:
    """Load a position and its history as **backfill**, not as live activity.

    The balance is written exactly as given. It is already the balance *after*
    the conversions in the document, so putting them through
    :func:`record_conversion` would decrement it a second time and leave the
    position short by the whole converted total. This is the single subtlety in
    the import path and the reason it is a separate function rather than a loop
    over the live one.
    """
    existing = (await session.execute(select(func.count()).select_from(Conversion))).scalar_one()
    warnings: list[str] = []

    if existing and not replace_conversions:
        warnings.append(
            f"{existing} conversion(s) are already recorded. Importing these as well "
            "would count the same history twice and overstate the realised gain. "
            "Set replace_conversions=true to replace them."
        )

    rows: list[tuple[ImportConversion, Decimal, Decimal, Decimal]] = []
    for index, row in enumerate(payload.completed_conversions, start=1):
        try:
            source, target, rate = _complete(row)
        except (ConversionError, ArithmeticError) as exc:
            warnings.append(f"Conversion {index} could not be completed: {exc}")
            continue
        if source <= ZERO or target <= ZERO:
            warnings.append(f"Conversion {index} has a non-positive amount and was skipped.")
            continue
        rows.append((row, source, target, rate))

    blocked = bool(existing) and not replace_conversions
    facts = [
        ConversionFact(
            source_amount=source,
            target_amount=target,
            fee_source_currency=row.fee_source_currency,
            amounts_estimated=row.amounts_estimated,
        )
        for row, source, target, _rate in rows
    ]
    realised = position_math.cumulative_realised(facts, payload.baseline_rate)

    result: dict[str, Any] = {
        "committed": False,
        "position_written": False,
        "conversions_to_write": 0 if blocked else len(rows),
        "conversions_written": 0,
        "existing_conversions": existing,
        "replaced_conversions": 0,
        "resulting_balance": payload.current_source_balance,
        "realised": realised,
        "warnings": warnings,
    }
    if not commit:
        return result
    if blocked:
        raise PositionError(warnings[0])

    replaced = 0
    if replace_conversions and existing:
        # Every conversion, not just the unsimulated ones: the count that
        # blocked the import counted them all, and leaving simulated rows
        # behind after "replace my history" would be a surprise.
        for existing_row in (await session.execute(select(Conversion))).scalars().all():
            await session.delete(existing_row)
        await session.flush()
        replaced = existing

    await replace_state(
        session,
        PositionIn(
            source_currency=payload.source_currency,
            target_currency=payload.target_currency,
            current_source_balance=payload.current_source_balance,
            baseline_rate=payload.baseline_rate,
            baseline_date=payload.baseline_date,
            floating_loan_rate=payload.floating_loan_rate,
            current_offset_shortfall_nzd=payload.current_offset_shortfall_nzd,
            monthly_nzd_burn=payload.monthly_nzd_burn,
            notes=payload.notes,
        ),
        actor=actor,
    )

    written = 0
    for row, source, target, rate in rows:
        await conversion_service.create_conversion(
            session,
            conversion_service.ConversionIn(
                executed_at=row.executed_at or utcnow(),
                source_amount=source,
                target_amount=target,
                gross_rate=rate,
                fee_source_currency=row.fee_source_currency,
                fee_target_currency=row.fee_target_currency,
                provider=row.provider,
                provider_transaction_id=row.provider_transaction_id,
                notes=row.notes,
                record_source="manual",
                amounts_estimated=row.amounts_estimated,
                # History predates the current balance by definition, so it is
                # allowed to exceed it — exactly as the CSV importer already
                # assumes.
                correcting_earlier_record=True,
            ),
            currencies=(payload.source_currency, payload.target_currency),
            actor=actor,
        )
        written += 1

    # Re-read the balance rather than trusting the payload: ``replace_state``
    # is what actually wrote it, and this is the figure the caller will check.
    position = await get_position(session)
    balance = position.current_source_balance if position else ZERO

    await audit.record(
        session,
        event_type=AuditEventType.IMPORTED,
        entity_type="fx_position",
        entity_id=POSITION_ID,
        message=(
            f"State imported: {written} conversion(s) as history, balance set to "
            f"{balance} {payload.source_currency}"
            + (f", {replaced} existing conversion(s) replaced" if replaced else "")
        ),
        after={"conversions": written, "balance": balance, "replaced": replaced},
        actor=actor,
    )

    result.update(
        committed=True,
        position_written=True,
        conversions_written=written,
        replaced_conversions=replaced,
        resulting_balance=balance,
    )
    return result


def realised_out(realised: RealisedTotal) -> RealisedSplit:
    return RealisedSplit(
        confirmed=realised.confirmed,
        estimated=realised.estimated,
        total=realised.total,
        includes_estimates=realised.includes_estimates,
    )


def metrics_out(metrics: dict[str, Any]) -> PositionMetrics:
    values = dict(metrics)
    values["realised"] = realised_out(values["realised"])
    return PositionMetrics.model_validate(values)


def state_out(state: PositionState) -> FxStateOut:
    """The whole state in one shape.

    Both the ``/fx`` router and the Home Assistant publisher answer with the
    same figures, so they read them through the same function rather than each
    assembling them and slowly disagreeing.
    """
    return FxStateOut(
        position=PositionOut.model_validate(state.position) if state.position is not None else None,
        metrics=metrics_out(state.metrics) if state.metrics is not None else None,
        latest_conversion=latest_conversion_summary(state.latest_conversion),
        conversion_count=state.conversion_count,
    )


def latest_conversion_summary(conversion: Conversion | None) -> dict[str, Any] | None:
    """The one-line "most recent conversion" the dashboard shows."""
    if conversion is None:
        return None
    return {
        "id": conversion.id,
        "executed_at": conversion.executed_at.isoformat(),
        "source_amount": format(conversion.source_amount, "f"),
        "target_amount": format(conversion.target_amount, "f"),
        "gross_rate": format(conversion.gross_rate, "f"),
        "amounts_estimated": conversion.amounts_estimated,
    }


__all__ = [
    "ConversionHistory",
    "PositionError",
    "PositionState",
    "alert_states",
    "export_state",
    "get_alert_history",
    "get_conversion_history",
    "get_position",
    "get_state",
    "import_state",
    "latest_conversion_summary",
    "metrics_out",
    "realised_out",
    "record_conversion",
    "replace_state",
    "state_out",
    "update_state",
]
