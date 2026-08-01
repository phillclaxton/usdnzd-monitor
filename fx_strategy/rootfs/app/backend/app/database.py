"""Database engine, session management and Decimal-safe column types.

SQLite has no decimal column type, and SQLAlchemy's ``Numeric`` degrades to
binary floating point on SQLite.  Financial columns therefore use
:class:`DecimalText`, which stores a canonical fixed-scale string and returns a
:class:`~decimal.Decimal`.  Round-tripping is exact.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, MetaData, String, Text, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

from app.config import AppConfig, get_config
from app.logging_setup import get_logger
from app.money import MONEY_PLACES, RATE_PLACES, to_decimal

log = get_logger(__name__)

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base with a stable constraint naming convention.

    Named constraints matter here because SQLite requires table rebuilds for
    most ALTERs, and Alembic's batch mode needs deterministic names.
    """

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class DecimalText(TypeDecorator[Decimal]):
    """Store a Decimal as a fixed-scale string.

    Values are normalised to a constant number of decimal places on the way in,
    so equality comparisons in SQL behave, and parsed back to Decimal on the way
    out.  Sorting in SQL is *not* reliable for these columns (``"-1"`` sorts
    before ``"0"`` lexicographically but ``"10"`` sorts before ``"9"``), so
    ordering and aggregation are always done in Python or via a companion float
    column that exists only for that purpose.
    """

    impl = String
    cache_ok = True

    def __init__(self, scale: int = MONEY_PLACES, **kwargs: Any) -> None:
        self.scale = scale
        # 24 integer digits is far beyond any plausible balance and keeps the
        # stored representation bounded.
        super().__init__(length=24 + scale + 2, **kwargs)

    @property
    def _quant(self) -> Decimal:
        return Decimal(1).scaleb(-self.scale)

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        return format(to_decimal(value).quantize(self._quant), "f")

    def process_result_value(self, value: Any, dialect: Any) -> Decimal | None:
        if value is None:
            return None
        return Decimal(value)


class RateText(DecimalText):
    """A :class:`DecimalText` at exchange-rate precision (8 places)."""

    cache_ok = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(scale=RATE_PLACES, **kwargs)


class MoneyText(DecimalText):
    """A :class:`DecimalText` at currency-calculation precision (4 places)."""

    cache_ok = True

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(scale=MONEY_PLACES, **kwargs)


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetimes stored as UTC.

    SQLite drops tzinfo, so this type asserts UTC on write and reattaches it on
    read.  All timestamps in the database are UTC; display conversion to the
    user's timezone happens in the frontend.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            raise TypeError(f"expected datetime, got {type(value).__name__}")
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not accepted; attach a timezone")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


#: Convenience aliases used by the models.
JSONText = Text
IdText = String(64)


def utcnow() -> datetime:
    """Timezone-aware current time. The only clock the application reads."""
    return datetime.now(UTC)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_engine_lock = asyncio.Lock()


def _apply_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    """Enable WAL, foreign keys and a sane busy timeout on every connection."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=10000")
    finally:
        cursor.close()


def create_engine_for(config: AppConfig) -> AsyncEngine:
    """Build an async engine for the given configuration."""
    config.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(
        config.database_url,
        echo=False,
        future=True,
        # SQLite writes are serialised anyway; a small pool avoids lock churn.
        pool_pre_ping=True,
    )
    event.listen(engine.sync_engine, "connect", _apply_sqlite_pragmas)
    return engine


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine_for(get_config())
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(get_engine(), expire_on_commit=False, autoflush=False)
    return _sessionmaker


def set_engine(engine: AsyncEngine | None) -> None:
    """Replace the global engine. Used by the test suite."""
    global _engine, _sessionmaker
    _engine = engine
    _sessionmaker = (
        async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        if engine is not None
        else None
    )


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a session that commits on success."""
    async with get_sessionmaker()() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def session_scope() -> AsyncSession:
    """Return a new session for use in background jobs (caller manages it)."""
    return get_sessionmaker()()


async def checkpoint_wal(engine: AsyncEngine | None = None) -> None:
    """Fold the write-ahead log back into the main database file.

    Run periodically and before backups so a Home Assistant snapshot of
    ``/data`` captures a complete database.
    """
    from sqlalchemy import text

    target = engine or get_engine()
    async with target.begin() as conn:
        await conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))


async def integrity_check(engine: AsyncEngine | None = None) -> list[str]:
    """Run ``PRAGMA integrity_check`` and return the reported problems."""
    from sqlalchemy import text

    target = engine or get_engine()
    async with target.connect() as conn:
        rows = (await conn.execute(text("PRAGMA integrity_check"))).scalars().all()
    return [row for row in rows if row != "ok"]


def sqlite_file_size(config: AppConfig | None = None) -> int:
    cfg = config or get_config()
    total = 0
    for suffix in ("", "-wal", "-shm"):
        path = cfg.database_path.with_name(cfg.database_path.name + suffix)
        if path.exists():
            total += path.stat().st_size
    return total


__all__ = [
    "Base",
    "DecimalText",
    "Engine",
    "IdText",
    "JSONText",
    "MoneyText",
    "RateText",
    "UTCDateTime",
    "checkpoint_wal",
    "create_engine_for",
    "dispose_engine",
    "get_engine",
    "get_session",
    "get_sessionmaker",
    "integrity_check",
    "session_scope",
    "set_engine",
    "sqlite_file_size",
    "utcnow",
]
