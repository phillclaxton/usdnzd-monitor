"""Conversions that belong to no strategy, and amounts that are estimates.

A conversion is a fact about money that moved. It used to need a Strategy row
to exist before it could be recorded, which put the plan at the centre of an
app whose job is to describe a position. These tests pin the two halves of
undoing that: recording without a strategy, and marking a row whose figures
were reconstructed rather than read off a receipt.

The migration is exercised here too. ``conversions`` cannot be altered in
place on SQLite, so it is rebuilt — and a rebuild loses any index or foreign
key that is not restated, silently and in both directions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config as AlembicConfig
from httpx import AsyncClient
from sqlalchemy import create_engine, text

from alembic import command
from app.database import utcnow
from app.tests.conftest import BACKEND_ROOT, run_migrations
from app.tests.test_conversions import conversion_payload, make_strategy

BEFORE_THIS_MIGRATION = "c41d7a9e5b02"


def unattached_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "executed_at": utcnow().isoformat(),
        "source_amount": "30000",
        "target_amount": "52500",
        "provider": "wise",
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Recording without a strategy
# ---------------------------------------------------------------------------


async def test_a_conversion_can_be_recorded_with_no_strategy(client: AsyncClient) -> None:
    response = await client.post("/api/v1/conversions", json=unattached_payload())
    assert response.status_code == 201, response.text
    (created,) = response.json()
    assert created["strategy_id"] is None
    assert created["tranche_id"] is None
    assert created["gross_rate"] == "1.75000000"

    listed = await client.get("/api/v1/conversions")
    assert [row["id"] for row in listed.json()["conversions"]] == [created["id"]]
    assert listed.json()["total_source_amount"] == "30000.0000"


async def test_an_unattached_conversion_is_not_measured_against_a_balance(
    client: AsyncClient,
) -> None:
    """There is no recorded total to exceed, so any positive amount is history."""
    response = await client.post(
        "/api/v1/conversions", json=unattached_payload(source_amount="9000000")
    )
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    ("field", "value"),
    [("source_amount", "0"), ("target_amount", "0"), ("source_amount", "-1")],
)
async def test_an_unattached_conversion_still_has_to_be_positive(
    client: AsyncClient, field: str, value: str
) -> None:
    response = await client.post("/api/v1/conversions", json=unattached_payload(**{field: value}))
    assert response.status_code == 422, response.text


async def test_a_tranche_cannot_be_named_without_its_strategy(client: AsyncClient) -> None:
    response = await client.post("/api/v1/conversions", json=unattached_payload(tranche_id=1))
    assert response.status_code == 422, response.text
    assert "strategy" in response.text


async def test_a_strategy_that_does_not_exist_is_still_a_404(client: AsyncClient) -> None:
    """Naming nothing is allowed; naming something absent is a typo, not a choice."""
    response = await client.post("/api/v1/conversions", json=unattached_payload(strategy_id=9999))
    assert response.status_code == 404, response.text


async def test_the_audit_message_names_the_configured_pair(client: AsyncClient) -> None:
    """With no strategy to ask, the currencies come from settings."""
    await client.post("/api/v1/conversions", json=unattached_payload())
    events = await client.get("/api/v1/audit-events", params={"entity_type": "conversion"})
    (event,) = events.json()
    assert "30000.0000 USD" in event["message"]
    assert "52500.0000 NZD" in event["message"]


# ---------------------------------------------------------------------------
# Correcting and deleting, on both paths
# ---------------------------------------------------------------------------


async def test_an_unattached_conversion_can_be_corrected(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/conversions", json=unattached_payload())).json()[0]
    response = await client.put(
        f"/api/v1/conversions/{created['id']}",
        json=unattached_payload(
            source_amount="30000",
            target_amount="52470",
            correction_reason="The Wise receipt arrived.",
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["target_amount"] == "52470.0000"
    assert response.json()["strategy_id"] is None


async def test_deleting_works_on_both_paths(client: AsyncClient) -> None:
    """Attached, the delete-orphan cascade removes the row; unattached, the
    session does. Same outcome by different mechanisms, so both are checked."""
    strategy = await make_strategy(client)
    attached = (
        await client.post(
            "/api/v1/conversions",
            json=conversion_payload(strategy["id"], provider_transaction_id="attached"),
        )
    ).json()[0]
    unattached = (
        await client.post(
            "/api/v1/conversions", json=unattached_payload(provider_transaction_id="unattached")
        )
    ).json()[0]

    for conversion_id in (attached["id"], unattached["id"]):
        deleted = await client.delete(f"/api/v1/conversions/{conversion_id}")
        assert deleted.status_code == 200, deleted.text
        assert (await client.get(f"/api/v1/conversions/{conversion_id}")).status_code == 404

    assert (await client.get("/api/v1/conversions")).json()["conversions"] == []


# ---------------------------------------------------------------------------
# Estimated amounts
# ---------------------------------------------------------------------------


async def test_an_estimate_is_recorded_as_an_estimate(client: AsyncClient) -> None:
    created = (
        await client.post("/api/v1/conversions", json=unattached_payload(amounts_estimated=True))
    ).json()[0]
    assert created["amounts_estimated"] is True

    read = await client.get(f"/api/v1/conversions/{created['id']}")
    assert read.json()["amounts_estimated"] is True

    events = await client.get("/api/v1/audit-events", params={"entity_type": "conversion"})
    assert "(amounts estimated)" in events.json()[0]["message"]


async def test_a_conversion_is_confirmed_unless_it_says_otherwise(client: AsyncClient) -> None:
    created = (await client.post("/api/v1/conversions", json=unattached_payload())).json()[0]
    assert created["amounts_estimated"] is False


async def test_entering_the_real_receipt_clears_the_estimate(client: AsyncClient) -> None:
    """The distinction is meant to disappear on its own once the figure is known."""
    created = (
        await client.post("/api/v1/conversions", json=unattached_payload(amounts_estimated=True))
    ).json()[0]
    corrected = await client.put(
        f"/api/v1/conversions/{created['id']}",
        json=unattached_payload(
            target_amount="52487.50",
            amounts_estimated=False,
            correction_reason="Wise receipt entered.",
        ),
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["amounts_estimated"] is False
    assert corrected.json()["target_amount"] == "52487.5000"


async def test_an_unattached_estimate_survives_a_backup_and_restore(client: AsyncClient) -> None:
    """A null strategy and the estimate flag both have to come back intact."""
    await client.post("/api/v1/conversions", json=unattached_payload(amounts_estimated=True))
    document = (await client.post("/api/v1/backup")).json()

    restored = await client.post(
        "/api/v1/restore?replace=true",
        files={"file": ("backup.json", json.dumps(document), "application/json")},
    )
    assert restored.status_code == 200, restored.text

    (row,) = (await client.get("/api/v1/conversions")).json()["conversions"]
    assert row["amounts_estimated"] is True
    assert row["strategy_id"] is None


# ---------------------------------------------------------------------------
# CSV import without a strategy
# ---------------------------------------------------------------------------

CSV = (
    "executed_at,source_amount,target_amount,gross_rate,effective_rate,"
    "fee_source_currency,fee_target_currency,provider,provider_transaction_id,"
    "tranche_reference,notes\n"
    "2026-03-01T00:00:00Z,50000,86000,1.7200,1.7200,,,wise,csv-1,,\n"
)


async def test_a_csv_imports_unattached_when_no_strategy_is_named(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/conversions/import",
        params={"commit": True},
        files={"file": ("history.csv", CSV, "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["imported"] == 1

    (row,) = (await client.get("/api/v1/conversions")).json()["conversions"]
    assert row["strategy_id"] is None
    assert row["source_amount"] == "50000.0000"


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
