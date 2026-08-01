"""Version 1 of the internal API."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    audit,
    conversions,
    health,
    home_assistant,
    rates,
    settings,
    strategies,
    tranches,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(rates.router)
api_router.include_router(strategies.router)
api_router.include_router(tranches.router)
api_router.include_router(conversions.router)
api_router.include_router(home_assistant.router)
api_router.include_router(settings.router)
api_router.include_router(audit.router)

__all__ = ["api_router"]
