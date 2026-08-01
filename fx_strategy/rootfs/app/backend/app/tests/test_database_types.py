"""Decimal and datetime column round-trip tests.

These guard the single most important storage property in the application: a
Decimal written to SQLite comes back as the same Decimal, with no float in the
middle.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, insert, select
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.database import MoneyText, RateText, UTCDateTime

metadata = MetaData()

sample_table = Table(
    "decimal_round_trip",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("rate", RateText()),
    Column("amount", MoneyText()),
    Column("moment", UTCDateTime()),
)


@pytest.fixture
async def table(engine: AsyncEngine) -> AsyncEngine:
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
    return engine


async def test_decimal_round_trip_is_exact(table: AsyncEngine) -> None:
    moment = datetime(2026, 9, 15, 10, 30, tzinfo=timezone(timedelta(hours=12)))
    async with table.begin() as conn:
        await conn.execute(
            insert(sample_table).values(
                id=1,
                rate=Decimal("1.76043210"),
                amount=Decimal("800000.1234"),
                moment=moment,
            )
        )
    async with table.connect() as conn:
        row = (await conn.execute(select(sample_table))).one()

    assert row.rate == Decimal("1.76043210")
    assert isinstance(row.rate, Decimal)
    assert row.amount == Decimal("800000.1234")
    # Stored as UTC, returned as UTC, equal to the original instant.
    assert row.moment == moment.astimezone(UTC)
    assert row.moment.tzinfo is UTC


async def test_values_beyond_column_scale_are_rounded_not_truncated(table: AsyncEngine) -> None:
    async with table.begin() as conn:
        await conn.execute(
            insert(sample_table).values(
                id=2, rate=Decimal("1.123456789"), amount=Decimal("1.99999")
            )
        )
    async with table.connect() as conn:
        row = (await conn.execute(select(sample_table).where(sample_table.c.id == 2))).one()
    assert row.rate == Decimal("1.12345679")
    assert row.amount == Decimal("2.0000")


async def test_naive_datetimes_are_refused(table: AsyncEngine) -> None:
    with pytest.raises(StatementError, match="naive datetimes"):
        async with table.begin() as conn:
            await conn.execute(insert(sample_table).values(id=3, moment=datetime(2026, 1, 1)))


async def test_large_amounts_keep_full_precision(table: AsyncEngine) -> None:
    # A number that float64 cannot represent exactly at 4 decimal places.
    big = Decimal("12345678901234.5678")
    async with table.begin() as conn:
        await conn.execute(insert(sample_table).values(id=4, amount=big))
    async with table.connect() as conn:
        row = (await conn.execute(select(sample_table).where(sample_table.c.id == 4))).one()
    assert row.amount == big
