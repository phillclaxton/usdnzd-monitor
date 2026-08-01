"""Conversion endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Query, UploadFile, status
from fastapi.responses import PlainTextResponse

from app.api.deps import ActorDep, SessionDep, SettingsDep
from app.api.errors import ConflictError, NotFoundError, ValidationError
from app.database import utcnow
from app.models.strategy import Conversion, Strategy
from app.schemas.common import Message
from app.schemas.conversion import (
    ConversionImportPreview,
    ConversionIn,
    ConversionListOut,
    ConversionOut,
    ConversionUpdate,
)
from app.services import conversion_service, csv_io
from app.services import strategy_service as strategies
from app.services.conversion_service import ConversionError, DuplicateConversionError

router = APIRouter(prefix="/conversions", tags=["conversions"])

MAX_UPLOAD_BYTES = 8 * 1024 * 1024


async def _strategy(session: SessionDep, strategy_id: int) -> Strategy:
    strategy = await strategies.get_strategy(session, strategy_id)
    if strategy is None:
        raise NotFoundError(f"Strategy {strategy_id} does not exist.")
    return strategy


async def _conversion(session: SessionDep, conversion_id: int) -> Conversion:
    conversion = await session.get(Conversion, conversion_id)
    if conversion is None:
        raise NotFoundError(f"Conversion {conversion_id} does not exist.")
    return conversion


@router.get("", response_model=ConversionListOut, summary="List conversions")
async def list_conversions(
    session: SessionDep,
    strategy_id: int | None = None,
    tranche_id: int | None = None,
    include_simulated: bool = True,
    limit: int = Query(default=200, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> ConversionListOut:
    rows = await conversion_service.list_conversions(
        session,
        strategy_id=strategy_id,
        tranche_id=tranche_id,
        include_simulated=include_simulated,
        limit=limit,
        offset=offset,
    )
    aggregate = conversion_service.totals(rows)
    return ConversionListOut(
        conversions=[ConversionOut.model_validate(row) for row in rows],
        total_source_amount=aggregate["total_source_amount"],
        total_target_amount=aggregate["total_target_amount"],
        blended_gross_rate=aggregate["blended_gross_rate"],
        blended_effective_rate=aggregate["blended_effective_rate"],
        total_fees=aggregate["total_fees"],
    )


@router.post(
    "",
    response_model=list[ConversionOut],
    status_code=status.HTTP_201_CREATED,
    summary="Record a conversion",
)
async def create_conversion(
    payload: ConversionIn, session: SessionDep, actor: ActorDep
) -> list[ConversionOut]:
    """Record a conversion that has already happened.

    Splitting across tranches produces one row per tranche, sharing the
    transaction reference.
    """
    strategy = await _strategy(session, payload.strategy_id)
    try:
        created = await conversion_service.create_conversion(
            session, strategy, payload, actor=actor
        )
    except DuplicateConversionError as exc:
        raise ConflictError(str(exc)) from exc
    except ConversionError as exc:
        raise ValidationError(str(exc)) from exc
    return [ConversionOut.model_validate(row) for row in created]


@router.get("/export", summary="Export conversions as CSV")
async def export_conversions(
    session: SessionDep, strategy_id: int | None = None
) -> PlainTextResponse:
    """Download in the same format the importer accepts."""
    rows = await conversion_service.list_conversions(session, strategy_id=strategy_id, limit=5000)
    body = csv_io.write_csv(
        [*csv_io.CONVERSION_REQUIRED_COLUMNS, *csv_io.CONVERSION_OPTIONAL_COLUMNS],
        [
            (
                row.executed_at,
                row.source_amount,
                row.target_amount,
                row.gross_rate,
                row.effective_rate,
                row.fee_source_currency,
                row.fee_target_currency,
                row.provider,
                row.provider_transaction_id,
                row.tranche_id,
                row.notes,
            )
            for row in rows
        ],
    )
    filename = f"fx-conversions-{utcnow().date().isoformat()}.csv"
    return PlainTextResponse(
        body,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{conversion_id}", response_model=ConversionOut, summary="Read a conversion")
async def read_conversion(conversion_id: int, session: SessionDep) -> ConversionOut:
    return ConversionOut.model_validate(await _conversion(session, conversion_id))


@router.put("/{conversion_id}", response_model=ConversionOut, summary="Correct a conversion")
async def update_conversion(
    conversion_id: int, payload: ConversionUpdate, session: SessionDep, actor: ActorDep
) -> ConversionOut:
    """Correct a record. The previous values stay in the audit trail."""
    conversion = await _conversion(session, conversion_id)
    strategy = await _strategy(session, conversion.strategy_id)
    try:
        updated = await conversion_service.update_conversion(
            session,
            strategy,
            conversion,
            payload,
            reason=payload.correction_reason,
            actor=actor,
        )
    except DuplicateConversionError as exc:
        raise ConflictError(str(exc)) from exc
    except ConversionError as exc:
        raise ValidationError(str(exc)) from exc
    return ConversionOut.model_validate(updated)


@router.delete("/{conversion_id}", response_model=Message, summary="Delete a conversion")
async def delete_conversion(
    conversion_id: int,
    session: SessionDep,
    actor: ActorDep,
    reason: str = Query(default="", max_length=500),
) -> Message:
    conversion = await _conversion(session, conversion_id)
    strategy = await _strategy(session, conversion.strategy_id)
    await conversion_service.delete_conversion(
        session, strategy, conversion, reason=reason, actor=actor
    )
    return Message(
        message=(f"Conversion {conversion_id} deleted. The audit trail keeps its values.")
    )


@router.post(
    "/import", response_model=ConversionImportPreview, summary="Import conversions from CSV"
)
async def import_conversions(
    session: SessionDep,
    settings: SettingsDep,
    actor: ActorDep,
    strategy_id: int = Query(...),
    file: UploadFile = File(...),
    commit: bool = Query(default=False, description="Set true to write the rows"),
) -> ConversionImportPreview:
    """Validate a CSV of past conversions, writing only when ``commit`` is set."""
    _ = settings
    strategy = await _strategy(session, strategy_id)

    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationError(f"The file exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationError("The file is not UTF-8 text.") from exc

    try:
        parsed = csv_io.parse_conversion_csv(content)
    except csv_io.CsvFormatError as exc:
        raise ValidationError(str(exc)) from exc

    errors: list[dict[str, Any]] = [
        {"row": error.row_number, "message": error.message} for error in parsed.errors[:100]
    ]
    duplicates = 0
    importable: list[ConversionIn] = []
    tranche_ids = {tranche.id for tranche in strategy.tranches}

    for index, row in enumerate(parsed.rows, start=2):
        transaction_id = row.get("provider_transaction_id")
        if await conversion_service.find_duplicate(
            session, row.get("provider", "csv_import"), transaction_id
        ):
            duplicates += 1
            errors.append(
                {
                    "row": index,
                    "message": (
                        f"Transaction {transaction_id} is already recorded and was skipped."
                    ),
                }
            )
            continue

        tranche_reference = row.get("tranche_reference")
        tranche_id: int | None = None
        if tranche_reference:
            try:
                candidate = int(tranche_reference)
            except ValueError:
                candidate = 0
            if candidate in tranche_ids:
                tranche_id = candidate
            else:
                by_sequence = next(
                    (t.id for t in strategy.tranches if str(t.sequence) == tranche_reference),
                    None,
                )
                tranche_id = by_sequence
                if tranche_id is None:
                    errors.append(
                        {
                            "row": index,
                            "message": (
                                f"Tranche {tranche_reference!r} was not found; the row will be "
                                "imported unassigned."
                            ),
                        }
                    )

        importable.append(
            ConversionIn(
                strategy_id=strategy.id,
                executed_at=row["executed_at"],
                source_amount=row["source_amount"],
                target_amount=row["target_amount"],
                gross_rate=row.get("gross_rate"),
                fee_source_currency=row.get("fee_source_currency"),
                fee_target_currency=row.get("fee_target_currency"),
                provider=row.get("provider", "csv_import"),
                provider_transaction_id=transaction_id,
                tranche_id=tranche_id,
                notes=row.get("notes", ""),
                record_source="csv_import",
                # An import is history, so it is allowed to exceed the current
                # remaining balance without being flagged as impossible.
                correcting_earlier_record=True,
            )
        )

    imported = 0
    if commit:
        for payload in importable:
            try:
                await conversion_service.create_conversion(session, strategy, payload, actor=actor)
                imported += 1
            except ConversionError as exc:
                errors.append({"row": 0, "message": str(exc)})

    return ConversionImportPreview(
        total_rows=parsed.total_rows,
        accepted=len(importable),
        rejected=len(parsed.errors),
        duplicates=duplicates,
        errors=errors,
        sample=[
            {
                "executed_at": payload.executed_at.isoformat(),
                "source_amount": format(payload.source_amount, "f"),
                "target_amount": format(payload.target_amount, "f"),
                "tranche_id": payload.tranche_id,
            }
            for payload in importable[:10]
        ],
        imported=imported,
        committed=commit,
    )
