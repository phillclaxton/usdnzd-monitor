"""Version 1 of the internal API."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    audit,
    conversions,
    health,
    home_assistant,
    obligations,
    providers,
    rates,
    settings,
    strategies,
    system,
    tranches,
    wise,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(rates.router)
api_router.include_router(providers.router)
api_router.include_router(obligations.router)
api_router.include_router(strategies.router)
api_router.include_router(tranches.router)
api_router.include_router(conversions.router)
api_router.include_router(wise.router)
api_router.include_router(home_assistant.router)
api_router.include_router(system.router)
api_router.include_router(settings.router)
api_router.include_router(audit.router)

__all__ = ["api_router"]
