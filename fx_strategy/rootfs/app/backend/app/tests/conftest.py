"""Shared test fixtures.

Each test gets its own temporary ``/data`` directory and a database built by
running the real Alembic migrations, so the migration chain is exercised on
every test run rather than only in a dedicated test.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "data"
    path.mkdir()
    return path


@pytest.fixture
def app_config(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Point the process configuration at the temporary data directory."""
    from app.config import AppConfig, get_config, reset_config_cache

    monkeypatch.setenv("FX_DATA_DIR", str(data_dir))
    monkeypatch.setenv("FX_APP_CONFIG_DIR", str(data_dir / "config"))
    monkeypatch.setenv("FX_STATIC_DIR", str(data_dir / "frontend"))
    monkeypatch.setenv("FX_TESTING", "true")
    monkeypatch.setenv("FX_LOG_LEVEL", "warning")
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    reset_config_cache()
    config: AppConfig = get_config()
    yield config
    reset_config_cache()


def run_migrations(database_path: Path, revision: str = "head") -> None:
    """Run the real migration chain against ``database_path``.

    A synchronous connection is handed to ``env.py`` because these fixtures run
    inside an event loop, where the async path's ``asyncio.run`` would fail.
    """
    cfg = AlembicConfig(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    sync_engine = create_engine(f"sqlite:///{database_path}")
    try:
        with sync_engine.begin() as connection:
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, revision)
    finally:
        sync_engine.dispose()


@pytest.fixture
async def engine(app_config: object) -> AsyncIterator[AsyncEngine]:
    from app.config import get_config
    from app.database import create_engine_for, set_engine

    config = get_config()
    run_migrations(config.database_path)
    async_engine = create_engine_for(config)
    set_engine(async_engine)
    try:
        yield async_engine
    finally:
        await async_engine.dispose()
        set_engine(None)


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    from app.database import get_sessionmaker

    async with get_sessionmaker()() as db_session:
        yield db_session
        await db_session.commit()


@pytest.fixture
async def client(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    from app.config import get_config
    from app.main import create_app

    application = create_app(get_config())
    transport = ASGITransport(app=application)
    async with (
        application.router.lifespan_context(application),
        AsyncClient(transport=transport, base_url="http://testserver") as http,
    ):
        yield http
