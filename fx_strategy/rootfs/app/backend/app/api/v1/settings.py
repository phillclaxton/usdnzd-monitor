"""Settings endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import ActorDep, SessionDep
from app.schemas.settings import Settings, SettingsUpdate
from app.services import settings_service

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=Settings, summary="Read settings")
async def read_settings(session: SessionDep) -> Settings:
    return await settings_service.load_settings(session)


@router.put("", response_model=Settings, summary="Update settings")
async def update_settings(
    payload: SettingsUpdate, session: SessionDep, actor: ActorDep
) -> Settings:
    """Replace the supplied sections. Omitted sections are left untouched."""
    return await settings_service.update_settings(session, payload, actor=actor)
