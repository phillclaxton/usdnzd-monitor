"""Simulation, backup, restore and diagnostics endpoints."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import JSONResponse

from app.api.deps import ActorDep, SessionDep, SettingsDep
from app.api.errors import ValidationError
from app.database import utcnow
from app.money import MoneyError, quantize_rate, to_decimal
from app.schemas.common import Message, RateStr, Schema, StrictSchema
from app.services import backup as backup_service
from app.services import simulation

router = APIRouter(tags=["system"])

MAX_RESTORE_BYTES = 64 * 1024 * 1024


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------


class SimulationStatusOut(Schema):
    enabled: bool
    banner: str
    simulated_rate: RateStr | None
    time_acceleration: int
    force_provider_error: bool
    force_disagreement: bool
    simulated_samples: int
    simulated_conversions: int
    replay_cursor: int


class SimulationConfigIn(StrictSchema):
    enabled: bool | None = None
    time_acceleration: int | None = None
    force_provider_error: bool | None = None
    force_disagreement: bool | None = None


class SimulationRateIn(StrictSchema):
    rate: RateStr


class SimulationReplayIn(StrictSchema):
    rates: list[str]
    seconds_between: int = 60


class ReplayOut(Schema):
    steps: int
    samples_written: int
    notifications: int
    final_rate: RateStr | None
    events: list[str]


@router.get("/simulation", response_model=SimulationStatusOut, summary="Simulation status")
async def simulation_status(session: SessionDep, settings: SettingsDep) -> SimulationStatusOut:
    result = await simulation.status(session, settings)
    return SimulationStatusOut(**asdict(result))


@router.put("/simulation", response_model=SimulationStatusOut, summary="Configure simulation")
async def configure_simulation(
    payload: SimulationConfigIn, session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> SimulationStatusOut:
    values = payload.model_dump(exclude_none=True)
    updated = await simulation.configure(session, settings, values, actor=actor)
    result = await simulation.status(session, updated)
    return SimulationStatusOut(**asdict(result))


@router.post("/simulation/rate", response_model=Message, summary="Inject a simulated rate")
async def simulate_rate(
    payload: SimulationRateIn, session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> Message:
    try:
        sample = await simulation.set_rate(session, settings, payload.rate, actor=actor)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return Message(message=f"Simulated rate {sample.rate} recorded.")


@router.post("/simulation/replay", response_model=ReplayOut, summary="Replay a rate series")
async def replay_rates(
    payload: SimulationReplayIn, session: SessionDep, settings: SettingsDep, actor: ActorDep
) -> ReplayOut:
    """Play a series of rates through the whole pipeline.

    Samples are spaced apart in simulated time, so the confirmation rules are
    exercised exactly as they are in real running.
    """
    if not payload.rates:
        raise ValidationError("Supply at least one rate to replay.")
    if len(payload.rates) > 500:
        raise ValidationError("A replay is limited to 500 rates.")
    try:
        rates = [quantize_rate(to_decimal(value, field="rate")) for value in payload.rates]
    except MoneyError as exc:
        raise ValidationError(str(exc)) from exc
    if any(rate <= 0 for rate in rates):
        raise ValidationError("Every replayed rate must be greater than zero.")

    try:
        result = await simulation.replay(
            session,
            settings,
            rates,
            seconds_between=max(payload.seconds_between, 1),
            actor=actor,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    return ReplayOut(**asdict(result))


@router.post("/simulation/reset", response_model=Message, summary="Reset simulated data")
async def reset_simulation(session: SessionDep, actor: ActorDep) -> Message:
    """Delete every simulated record. Real data is untouched."""
    removed = await simulation.reset(session, actor=actor)
    return Message(
        message=(
            "Simulated data removed: "
            + ", ".join(f"{count} {name}" for name, count in removed.items())
            + ". Real records were not touched."
        )
    )


# ---------------------------------------------------------------------------
# Backup and restore
# ---------------------------------------------------------------------------


@router.post("/backup", summary="Create a backup")
async def create_backup(
    session: SessionDep, settings: SettingsDep, actor: ActorDep, download: bool = True
) -> JSONResponse:
    """Export everything except credentials, as a JSON document."""
    document = await backup_service.create_backup(session, settings, actor=actor)
    headers = {}
    if download:
        filename = f"fx-strategy-backup-{utcnow().date().isoformat()}.json"
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return JSONResponse(document, headers=headers)


class RestoreOut(Schema):
    restored: dict[str, int]
    message: str


@router.post("/restore", response_model=RestoreOut, summary="Restore a backup")
async def restore_backup(
    session: SessionDep,
    actor: ActorDep,
    file: UploadFile = File(...),
    replace: bool = Query(default=False, description="Overwrite existing data instead of refusing"),
) -> RestoreOut:
    raw = await file.read(MAX_RESTORE_BYTES + 1)
    if len(raw) > MAX_RESTORE_BYTES:
        raise ValidationError(
            f"The file exceeds the {MAX_RESTORE_BYTES // (1024 * 1024)} MB limit."
        )
    try:
        document = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("The file is not valid JSON.") from exc

    try:
        counts = await backup_service.restore_backup(
            session, document, replace=replace, actor=actor
        )
    except backup_service.RestoreError as exc:
        raise ValidationError(str(exc)) from exc

    return RestoreOut(
        restored=counts,
        message=(
            f"Restored {sum(counts.values())} rows. Credentials are not included in a "
            "backup, so re-enter any API tokens."
        ),
    )


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------


@router.get("/diagnostics", summary="Diagnostics")
async def diagnostics(
    session: SessionDep, settings: SettingsDep, include_logs: bool = True
) -> dict[str, Any]:
    """Everything useful for troubleshooting, with nothing sensitive in it."""
    return await backup_service.diagnostics(session, settings, include_logs=include_logs)


@router.get("/diagnostics/bundle", summary="Download a diagnostics bundle")
async def diagnostics_bundle(session: SessionDep, settings: SettingsDep) -> JSONResponse:
    document = await backup_service.diagnostics(session, settings, include_logs=True)
    filename = f"fx-strategy-diagnostics-{utcnow().date().isoformat()}.json"
    return JSONResponse(
        document, headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@router.post("/diagnostics/integrity-check", summary="Check database integrity")
async def integrity_check_endpoint() -> dict[str, Any]:
    """Run SQLite's integrity check. Read-only: it never repairs anything."""
    from app.database import integrity_check

    problems = await integrity_check()
    return {
        "ok": not problems,
        "problems": problems,
        "note": (
            "This check does not repair anything. If problems are reported, download "
            "the database and restore from a backup."
        ),
    }
