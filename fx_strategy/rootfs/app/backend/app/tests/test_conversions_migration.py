"""The ``conversions`` rebuild, up and down.

``conversions`` cannot be altered in place on SQLite, so the migration that made
``strategy_id`` nullable and added ``amounts_estimated`` rebuilds the table — and
a rebuild loses any index or foreign key it does not restate, silently and in
both directions. That is what these assert, against populated data.

What the columns then *mean* is pinned in ``test_conversions.py``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, text

from alembic import command
from app.tests.conftest import BACKEND_ROOT, run_migrations

BEFORE_THIS_MIGRATION = "c41d7a9e5b02"


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------


def _insert(connection: Any, table: str, values: dict[str, Any]) -> None:
    """Insert a row, filling any other NOT NULL column with a placeholder.

    The migration does not read those columns; they only have to be non-null
    for the row to exist. Spelling every one of them out here would age badly
    against a schema this test is not about.
    """
    row = dict(values)
    for _cid, name, column_type, not_null, default, _pk in connection.execute(
        text(f"PRAGMA table_info({table})")
    ):
        if name in row or not not_null or default is not None:
            continue
        row[name] = 0 if column_type in ("INTEGER", "BOOLEAN") else "x"
    columns = ",".join(row)
    binds = ",".join(f":{key}" for key in row)
    # The table and column names come from this file, never from input.
    connection.execute(text(f"INSERT INTO {table} ({columns}) VALUES ({binds})"), row)  # noqa: S608


def _shape(connection: Any) -> dict[str, Any]:
    indexes = sorted(
        row[1]
        for row in connection.execute(text("PRAGMA index_list(conversions)"))
        if not row[1].startswith("sqlite_autoindex")
    )
    foreign_keys = sorted(
        (row[2], row[3], row[4], row[6])
        for row in connection.execute(text("PRAGMA foreign_key_list(conversions)"))
    )
    columns = {
        row[1]: {"not_null": bool(row[3])}
        for row in connection.execute(text("PRAGMA table_info(conversions)"))
    }
    return {"indexes": indexes, "foreign_keys": foreign_keys, "columns": columns}


EXPECTED_INDEXES = [
    "ix_conversions_executed_at",
    "ix_conversions_provider_txn",
    "ix_conversions_strategy_executed",
]
EXPECTED_FOREIGN_KEYS = [
    ("strategies", "strategy_id", "id", "CASCADE"),
    ("tranches", "tranche_id", "id", "SET NULL"),
]


def _alembic(database_path: Path, revision: str, *, down: bool = False) -> None:
    config = AlembicConfig(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            (command.downgrade if down else command.upgrade)(config, revision)
    finally:
        engine.dispose()


def test_the_rebuild_keeps_every_index_and_foreign_key(tmp_path: Path) -> None:
    """A batch recreate drops whatever the migration does not restate."""
    database = tmp_path / "chain.sqlite"
    run_migrations(database, BEFORE_THIS_MIGRATION)
    engine = create_engine(f"sqlite:///{database}")
    try:
        with engine.begin() as connection:
            _insert(
                connection,
                "strategies",
                {
                    "id": 1,
                    "name": "USD to NZD",
                    "status": "active",
                    "source_currency": "USD",
                    "target_currency": "NZD",
                    "initial_source_amount": "800000.0000",
                    "funds_available_amount": "800000.0000",
                },
            )
            _insert(connection, "tranches", {"id": 1, "strategy_id": 1, "sequence": 1})
            _insert(
                connection,
                "conversions",
                {
                    "id": 1,
                    "strategy_id": 1,
                    "tranche_id": 1,
                    "source_amount": "120000.0000",
                    "target_amount": "206400.0000",
                    "gross_rate": "1.72000000",
                    "effective_rate": "1.72000000",
                    "provider": "wise",
                    "provider_transaction_id": "tx-1",
                    "executed_at": "2026-01-02T00:00:00+00:00",
                    "record_source": "manual",
                },
            )

        with engine.connect() as connection:
            before = _shape(connection)
        assert before["indexes"] == EXPECTED_INDEXES
        assert before["foreign_keys"] == EXPECTED_FOREIGN_KEYS
        assert before["columns"]["strategy_id"]["not_null"] is True

        _alembic(database, "head")

        with engine.connect() as connection:
            after = _shape(connection)
            rows = list(
                connection.execute(
                    text("SELECT strategy_id, tranche_id, target_amount FROM conversions")
                )
            )
        assert after["indexes"] == EXPECTED_INDEXES
        assert after["foreign_keys"] == EXPECTED_FOREIGN_KEYS
        assert after["columns"]["strategy_id"]["not_null"] is False
        assert after["columns"]["amounts_estimated"]["not_null"] is True
        # The figures come across untouched, and history is not reclassified as
        # an estimate by an upgrade.
        assert rows == [(1, 1, "206400.0000")]
        with engine.connect() as connection:
            assert list(connection.execute(text("SELECT amounts_estimated FROM conversions"))) == [
                (0,)
            ]

        _alembic(database, BEFORE_THIS_MIGRATION, down=True)

        with engine.connect() as connection:
            reverted = _shape(connection)
            rows = list(
                connection.execute(
                    text("SELECT strategy_id, tranche_id, target_amount FROM conversions")
                )
            )
        assert reverted["indexes"] == EXPECTED_INDEXES
        assert reverted["foreign_keys"] == EXPECTED_FOREIGN_KEYS
        assert reverted["columns"]["strategy_id"]["not_null"] is True
        assert "amounts_estimated" not in reverted["columns"]
        assert rows == [(1, 1, "206400.0000")]
    finally:
        engine.dispose()


def test_downgrading_drops_what_the_old_schema_cannot_hold(tmp_path: Path) -> None:
    """An unattached conversion has nowhere to go under a NOT NULL column.

    Losing it is the honest outcome and the reason the downgrade is a
    development convenience rather than something to run on real data.
    """
    database = tmp_path / "loss.sqlite"
    run_migrations(database)
    engine = create_engine(f"sqlite:///{database}")
    try:
        with engine.begin() as connection:
            _insert(
                connection,
                "strategies",
                {
                    "id": 1,
                    "name": "USD to NZD",
                    "status": "active",
                    "source_currency": "USD",
                    "target_currency": "NZD",
                    "initial_source_amount": "800000.0000",
                    "funds_available_amount": "800000.0000",
                },
            )
            for conversion_id, strategy_id in ((1, 1), (2, None)):
                _insert(
                    connection,
                    "conversions",
                    {
                        "id": conversion_id,
                        "strategy_id": strategy_id,
                        "source_amount": "30000.0000",
                        "target_amount": "52500.0000",
                        "gross_rate": "1.75000000",
                        "effective_rate": "1.75000000",
                        "provider": "wise",
                        "executed_at": "2026-01-02T00:00:00+00:00",
                        "record_source": "manual",
                    },
                )

        _alembic(database, BEFORE_THIS_MIGRATION, down=True)

        with engine.connect() as connection:
            assert list(connection.execute(text("SELECT id FROM conversions"))) == [(1,)]
    finally:
        engine.dispose()
