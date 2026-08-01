"""FastAPI application factory and lifespan."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from app import __version__
from app.api.errors import register_exception_handlers
from app.api.middleware import (
    CorrelationMiddleware,
    CrossOriginGuardMiddleware,
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
)
from app.api.v1 import api_router
from app.config import AppConfig, get_config
from app.database import checkpoint_wal, dispose_engine, get_sessionmaker
from app.logging_setup import configure_logging, get_logger
from app.scheduler import get_scheduler
from app.services import monitor, settings_service
from app.web import FrontendFiles, robots_txt

log = get_logger(__name__)

DESCRIPTION = """
FX Strategy Manager is a self-hosted decision-support tool. It does not provide
financial advice and does not automatically transfer or convert money.
""".strip()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    config: AppConfig = app.state.config
    log.info(
        "starting",
        version=config.app_version,
        arch=config.build_arch,
        simulation_mode=config.simulation_mode,
        mqtt_configured=config.mqtt_configured,
        ingress_entry=config.ingress_entry or "(none)",
    )

    # Materialise the settings document so the first request is not the thing
    # that discovers a broken database.
    async with get_sessionmaker()() as session:
        settings = await settings_service.load_settings(session)
        if config.simulation_mode and not settings.simulation.enabled:
            settings.simulation.enabled = True
            await settings_service.save_settings(
                session,
                settings,
                message="Simulation mode enabled by add-on configuration",
            )
        await session.commit()

    scheduler = get_scheduler()
    scheduler.on_refresh(monitor.run_after_refresh)
    if not config.testing:
        # The test suite drives jobs explicitly; a background poller would make
        # tests depend on wall-clock timing.
        await scheduler.start()
    app.state.scheduler = scheduler

    try:
        yield
    finally:
        log.info("shutting_down")
        await scheduler.shutdown()
        try:
            await checkpoint_wal()
        except Exception as exc:  # pragma: no cover - best-effort on shutdown
            log.warning("wal_checkpoint_failed", error=str(exc))
        await dispose_engine()


def create_app(config: AppConfig | None = None) -> FastAPI:
    cfg = config or get_config()
    configure_logging(cfg.python_log_level)

    app = FastAPI(
        title="FX Strategy Manager",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        # Docs live under /api so they inherit the Ingress prefix cleanly and
        # never collide with a client-side route.
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.config = cfg
    app.state.frontend = FrontendFiles(cfg)

    # Middleware runs bottom-up: correlation IDs are established first.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(CrossOriginGuardMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CorrelationMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router)

    @app.get("/robots.txt", include_in_schema=False)
    async def _robots() -> Response:
        return robots_txt()

    @app.get("/", include_in_schema=False)
    async def _index(request: Request) -> Response:
        return app.state.frontend.index(request)

    @app.get("/{path:path}", include_in_schema=False)
    async def _spa(request: Request, path: str) -> Response:
        # An unmatched API path is a genuine 404, not a client-side route.
        if path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "not_found", "message": f"No route for /{path}"}},
            )
        return app.state.frontend.asset(request, path)

    return app


app = create_app()
