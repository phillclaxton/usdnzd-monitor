"""Liveness and readiness endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import ConfigDep, SessionDep
from app.database import utcnow
from app.logging_setup import get_logger

router = APIRouter(prefix="/health", tags=["health"])
log = get_logger(__name__)


@router.get("", summary="Overall health")
async def health(session: SessionDep, config: ConfigDep) -> dict[str, Any]:
    database_ok = True
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:  # pragma: no cover - exercised via readiness tests
        database_ok = False
        log.error("health_database_failed", error=str(exc))

    return {
        "status": "ok" if database_ok else "degraded",
        "version": config.app_version,
        "arch": config.build_arch,
        "simulation_mode": config.simulation_mode,
        "database": "ok" if database_ok else "error",
        "timestamp": utcnow().isoformat(),
    }


@router.get("/live", summary="Liveness probe")
async def live() -> dict[str, str]:
    """Always succeeds while the process is running."""
    return {"status": "alive"}


@router.get("/ready", summary="Readiness probe")
async def ready(session: SessionDep, response: Response) -> dict[str, Any]:
    """Fails when the database is unusable, per the reliability requirements."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception as exc:
        log.error("readiness_failed", error=str(exc))
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "not_ready", "reason": "database_unavailable"}
    return {"status": "ready"}
