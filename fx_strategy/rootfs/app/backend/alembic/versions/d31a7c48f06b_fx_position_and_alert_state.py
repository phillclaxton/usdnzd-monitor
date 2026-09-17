"""fx position and alert state

Two new tables and nothing touched. ``fx_position`` is the singleton holding
what is held and what it is measured against; ``fx_alert_state`` remembers the
value each alert condition last spoke at, which the existing time-only cooldown
cannot express.

Revision ID: d31a7c48f06b
Revises: b7c3f1d2e904
Create Date: 2026-09-17 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

import app.database
from alembic import op

revision: str = "d31a7c48f06b"
down_revision: str | None = "b7c3f1d2e904"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fx_position",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_currency", sa.String(length=3), nullable=False),
        sa.Column("target_currency", sa.String(length=3), nullable=False),
        sa.Column("current_source_balance", app.database.MoneyText(length=30), nullable=False),
        sa.Column("baseline_rate", app.database.RateText(length=34), nullable=True),
        sa.Column("baseline_date", sa.Date(), nullable=True),
        sa.Column("floating_loan_rate", app.database.RateText(length=34), nullable=True),
        sa.Column("current_offset_shortfall_nzd", app.database.MoneyText(length=30), nullable=True),
        sa.Column("monthly_nzd_burn", app.database.MoneyText(length=30), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", app.database.UTCDateTime(), nullable=False),
        sa.Column("updated_at", app.database.UTCDateTime(), nullable=False),
        # There is one position, not a list of them. The constraint is what
        # stops a second row ever appearing to be picked between.
        sa.CheckConstraint("id = 1", name=op.f("ck_fx_position_single_row")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fx_position")),
    )

    op.create_table(
        "fx_alert_state",
        sa.Column("alert_key", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("last_value", app.database.RateText(length=34), nullable=True),
        sa.Column("last_reference_value", app.database.RateText(length=34), nullable=True),
        sa.Column("last_money_value", app.database.MoneyText(length=30), nullable=True),
        sa.Column("qualifying_samples", sa.Integer(), nullable=False),
        sa.Column("first_qualifying_at", app.database.UTCDateTime(), nullable=True),
        sa.Column("last_sample_at", app.database.UTCDateTime(), nullable=True),
        sa.Column("last_sample_rate", app.database.RateText(length=34), nullable=True),
        sa.Column("last_notified_at", app.database.UTCDateTime(), nullable=True),
        sa.Column("notification_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", app.database.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("alert_key", name=op.f("pk_fx_alert_state")),
    )


def downgrade() -> None:
    op.drop_table("fx_alert_state")
    op.drop_table("fx_position")
