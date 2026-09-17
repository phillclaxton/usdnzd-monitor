"""conversions: optional strategy, and a flag for reconstructed amounts

A conversion is a fact about money that moved. Requiring a Strategy row to
record one made the plan the centre of the app; it is not. ``strategy_id``
becomes nullable, and ``amounts_estimated`` marks a row whose figures were
reconstructed rather than read off a receipt.

SQLite cannot ``ALTER COLUMN``, so ``conversions`` is rebuilt. The table is
restated in full via ``copy_from`` rather than reflected: SQLite does not
report foreign key *names*, so a reflected rebuild would rename both
constraints, and every index has to be named here or the rebuild drops it.
Both losses are silent, which is why there is a test asserting the shape with
``PRAGMA index_list`` and ``PRAGMA foreign_key_list`` in both directions.

Revision ID: b7c3f1d2e904
Revises: c41d7a9e5b02
Create Date: 2026-09-17 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

import app.database
from alembic import op

revision: str = "b7c3f1d2e904"
down_revision: str | None = "c41d7a9e5b02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _conversions(*, strategy_nullable: bool, with_estimated_flag: bool) -> sa.Table:
    """The ``conversions`` table as it stands *before* the operation using it.

    ``copy_from`` has to describe the existing table exactly: alembic builds
    the replacement from this definition plus the batch operations, and copies
    the rows across on that basis.
    """
    columns: list[sa.Column[object] | sa.SchemaItem] = [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("strategy_id", sa.Integer(), nullable=strategy_nullable),
        sa.Column("tranche_id", sa.Integer(), nullable=True),
        sa.Column("source_amount", app.database.MoneyText(length=30), nullable=False),
        sa.Column("target_amount", app.database.MoneyText(length=30), nullable=False),
        sa.Column("gross_rate", app.database.RateText(length=34), nullable=False),
        sa.Column("effective_rate", app.database.RateText(length=34), nullable=False),
        sa.Column("fee_source_currency", app.database.MoneyText(length=30), nullable=True),
        sa.Column("fee_target_currency", app.database.MoneyText(length=30), nullable=True),
        sa.Column("fee_total_target_equivalent", app.database.MoneyText(length=30), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("provider_transaction_id", sa.String(length=128), nullable=True),
        sa.Column("executed_at", app.database.UTCDateTime(), nullable=False),
        sa.Column("record_source", sa.String(length=16), nullable=False),
        sa.Column("simulated", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("receipt_filename", sa.String(length=200), nullable=True),
        sa.Column("created_at", app.database.UTCDateTime(), nullable=False),
        sa.Column("updated_at", app.database.UTCDateTime(), nullable=False),
    ]
    if with_estimated_flag:
        columns.append(
            sa.Column("amounts_estimated", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    table = sa.Table(
        "conversions",
        sa.MetaData(),
        *columns,
        sa.ForeignKeyConstraint(
            ["strategy_id"],
            ["strategies.id"],
            name="fk_conversions_strategy_id_strategies",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tranche_id"],
            ["tranches.id"],
            name="fk_conversions_tranche_id_tranches",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_conversions"),
    )
    sa.Index("ix_conversions_executed_at", table.c.executed_at)
    sa.Index("ix_conversions_provider_txn", table.c.provider, table.c.provider_transaction_id)
    sa.Index("ix_conversions_strategy_executed", table.c.strategy_id, table.c.executed_at)
    return table


def upgrade() -> None:
    with op.batch_alter_table(
        "conversions",
        schema=None,
        recreate="always",
        copy_from=_conversions(strategy_nullable=False, with_estimated_flag=False),
    ) as batch_op:
        batch_op.alter_column("strategy_id", existing_type=sa.Integer(), nullable=True)
        # Existing rows were entered from receipts, so they are not estimates.
        batch_op.add_column(
            sa.Column("amounts_estimated", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    # A conversion with no strategy cannot be represented by the old schema, and
    # there is nowhere sensible to put it. It goes — which is why this direction
    # is for development, not for data anyone is keeping.
    op.execute(sa.text("DELETE FROM conversions WHERE strategy_id IS NULL"))
    with op.batch_alter_table(
        "conversions",
        schema=None,
        recreate="always",
        copy_from=_conversions(strategy_nullable=True, with_estimated_flag=True),
    ) as batch_op:
        batch_op.drop_column("amounts_estimated")
        batch_op.alter_column("strategy_id", existing_type=sa.Integer(), nullable=False)
