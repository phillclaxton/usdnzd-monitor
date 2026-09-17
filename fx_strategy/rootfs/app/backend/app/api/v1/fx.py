"""The FX position endpoints.

Deliberately *not* aliases of the existing routers. ``/conversions`` records
what happened; ``/fx`` describes what is held and what it is worth, and the two
differ in one load-bearing way: ``POST /fx/conversions`` reduces the balance and
``POST /conversions`` does not.

Every route here is a thin call into :mod:`app.services.position_service`, whose
function names match the specification, so a later ChatGPT integration is a
second transport over the same calls rather than a second implementation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, status

from app.api.deps import ActorDep, SessionDep, SettingsDep
from app.api.errors import ConflictError, ValidationError
from app.models.position import FxPosition
from app.schemas.position import (
    AlertHistoryRow,
    ConversionHistoryOut,
    ConversionHistoryRow,
    FxStateOut,
    ImportPreview,
    PositionIn,
    PositionMetrics,
    PositionOut,
    PositionPatch,
    RealisedSplit,
    RecordConversionIn,
    StateExport,
    StateImport,
)
from app.services import position_service
from app.services.conversion_service import ConversionError, DuplicateConversionError
from app.services.position_math import RealisedTotal
from app.services.position_service import PositionError

router = APIRouter(prefix="/fx", tags=["fx position"])


def _split(realised: RealisedTotal) -> RealisedSplit:
    return RealisedSplit(
        confirmed=realised.confirmed,
        estimated=realised.estimated,
        total=realised.total,
        includes_estimates=realised.includes_estimates,
    )


def _metrics_out(metrics: dict[str, Any]) -> PositionMetrics:
    values = dict(metrics)
    values["realised"] = _split(values["realised"])
    return PositionMetrics.model_validate(values)


def _position_out(position: FxPosition) -> PositionOut:
    return PositionOut.model_validate(position)


@router.get("/state", response_model=FxStateOut, summary="The current FX position")
async def read_state(session: SessionDep, settings: SettingsDep) -> FxStateOut:
    """What is held, what it is worth, and what waiting for it costs.

    Returns **200 with ``position: null``** before anything has been entered.
    An empty position is a normal state to describe, not an error, so the
    first-run case does not arrive at the frontend as a failed request.
    """
    state = await position_service.get_state(session, settings)
    return FxStateOut(
        position=_position_out(state.position) if state.position is not None else None,
        metrics=_metrics_out(state.metrics) if state.metrics is not None else None,
        latest_conversion=position_service.latest_conversion_summary(state.latest_conversion),
        conversion_count=state.conversion_count,
    )


@router.post(
    "/state",
    response_model=PositionOut,
    status_code=status.HTTP_200_OK,
    summary="Save the whole position",
)
async def write_state(payload: PositionIn, session: SessionDep, actor: ActorDep) -> PositionOut:
    """Replace every field. Use ``PATCH`` to change one of them."""
    return _position_out(await position_service.replace_state(session, payload, actor=actor))


@router.patch("/state", response_model=PositionOut, summary="Update part of the position")
async def patch_state(payload: PositionPatch, session: SessionDep, actor: ActorDep) -> PositionOut:
    """Apply only the fields present in the request body.

    A field sent as ``null`` is cleared; a field left out is untouched.
    """
    try:
        position = await position_service.update_state(session, payload, actor=actor)
    except PositionError as exc:
        raise ValidationError(str(exc)) from exc
    return _position_out(position)


@router.get("/conversions", response_model=ConversionHistoryOut, summary="Conversion history")
async def read_conversions(session: SessionDep) -> ConversionHistoryOut:
    """Every recorded conversion with what it gained against the baseline.

    Not an alias of ``GET /conversions``: the per-row improvement and the
    running cumulative are what this endpoint exists for, and they only mean
    anything against the position's stored baseline rate.
    """
    history = await position_service.get_conversion_history(session)
    return ConversionHistoryOut(
        conversions=[
            ConversionHistoryRow(
                id=row.id,
                executed_at=row.executed_at,
                source_amount=row.source_amount,
                target_amount=row.target_amount,
                gross_rate=row.gross_rate,
                effective_rate=row.effective_rate,
                fee_source_currency=row.fee_source_currency,
                fee_total_target_equivalent=row.fee_total_target_equivalent,
                provider=row.provider,
                record_source=row.record_source,
                notes=row.notes,
                amounts_estimated=row.amounts_estimated,
                simulated=row.simulated,
                improvement=improvement.improvement,
                cumulative_improvement=improvement.cumulative,
                fee_unrecorded=improvement.fee_unrecorded,
            )
            for row, improvement in history.conversions
        ],
        baseline_rate=history.baseline_rate,
        realised=_split(history.realised),
    )


@router.post(
    "/conversions",
    response_model=ConversionHistoryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Record a conversion and reduce the balance",
)
async def record_conversion(
    payload: RecordConversionIn,
    session: SessionDep,
    settings: SettingsDep,
    actor: ActorDep,
) -> ConversionHistoryOut:
    """Record a conversion that has just happened.

    **This reduces the held balance**, which is the whole reason it is separate
    from ``POST /conversions``. It does not change the offset shortfall: not
    every conversion goes to the mortgage, and assuming one did would quietly
    corrupt the carrying cost.

    Correcting or deleting a conversion afterwards through ``/conversions/{id}``
    never credits the balance back. The balance is yours to state; a correction
    is restated on the position form.
    """
    try:
        await position_service.record_conversion(session, settings, payload, actor=actor)
    except DuplicateConversionError as exc:
        raise ConflictError(str(exc)) from exc
    except PositionError as exc:
        raise ValidationError(str(exc)) from exc
    except ConversionError as exc:
        raise ValidationError(str(exc)) from exc
    return await read_conversions(session)


@router.get("/alerts", response_model=list[AlertHistoryRow], summary="Alert history")
async def read_alerts(
    session: SessionDep,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[AlertHistoryRow]:
    """What the app has said, newest first.

    Reads the notification log, so an alert appears here whichever rule raised
    it. Undelivered rows are included on purpose: a notification that failed to
    send is exactly the one worth seeing.
    """
    rows = await position_service.get_alert_history(session, limit=limit, offset=offset)
    return [AlertHistoryRow.model_validate(row) for row in rows]


@router.get("/state/export", response_model=StateExport, summary="Export the position")
async def export_state(session: SessionDep, settings: SettingsDep) -> StateExport:
    """The whole position as a portable document.

    Realised improvement is exported as three fields rather than one, so a
    consumer reading only ``realised_confirmed`` cannot pick up an estimate by
    accident.
    """
    return StateExport.model_validate(await position_service.export_state(session, settings))


@router.post("/state/import", response_model=ImportPreview, summary="Import a position")
async def import_state(
    payload: StateImport,
    session: SessionDep,
    settings: SettingsDep,
    actor: ActorDep,
    commit: bool = Query(default=False, description="Set true to write the document"),
    replace_conversions: bool = Query(
        default=False, description="Replace the conversions already recorded"
    ),
) -> ImportPreview:
    """Load a position and its history, previewing by default.

    The conversions are written as **history**: the balance in the document is
    stored exactly as given rather than being decremented by each one, because
    it is already the balance after them.

    Importing the same history twice would double the realised gain with nothing
    downstream able to tell, so a document carrying conversions is refused while
    any are already recorded unless ``replace_conversions`` is set.
    """
    try:
        result = await position_service.import_state(
            session,
            settings,
            payload,
            commit=commit,
            replace_conversions=replace_conversions,
            actor=actor,
        )
    except PositionError as exc:
        raise ConflictError(str(exc)) from exc
    except ConversionError as exc:
        raise ValidationError(str(exc)) from exc

    values = dict(result)
    values["realised"] = _split(values["realised"])
    return ImportPreview.model_validate(values)
