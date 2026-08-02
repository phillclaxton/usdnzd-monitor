"""Shared FastAPI dependencies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import AppConfig, get_config
from app.database import get_session
from app.schemas.settings import Settings
from app.services import settings_service

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ConfigDep = Annotated[AppConfig, Depends(get_config)]


async def settings_dep(session: SessionDep) -> Settings:
    return await settings_service.load_settings(session)


SettingsDep = Annotated[Settings, Depends(settings_dep)]


async def current_actor(request: Request) -> str:
    """Identify the caller for audit purposes.

    Ingress terminates Home Assistant authentication before the request reaches
    us and forwards the authenticated user in ``X-Remote-User-Name``.  When the
    header is absent the caller is a local API client.
    """
    name = request.headers.get("X-Remote-User-Display-Name") or request.headers.get(
        "X-Remote-User-Name"
    )
    if name:
        return name[:64]
    return "api"


ActorDep = Annotated[str, Depends(current_actor)]


async def _noop() -> AsyncIterator[None]:  # pragma: no cover - placeholder for symmetry
    yield None
