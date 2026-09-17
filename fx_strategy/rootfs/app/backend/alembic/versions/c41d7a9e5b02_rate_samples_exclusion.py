"""rate samples: exclusion of observations that should not be used

Revision ID: c41d7a9e5b02
Revises: 08383e9b6268
Create Date: 2026-09-17 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

import app.database
from alembic import op

revision: str = "c41d7a9e5b02"
down_revision: str | None = "08383e9b6268"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing rows keep excluded_at NULL, which is "usable" — an upgrade must
    # not silently hide history that was being charted yesterday.
    with op.batch_alter_table("rate_samples", schema=None) as batch_op:
        batch_op.add_column(sa.Column("excluded_at", app.database.UTCDateTime(), nullable=True))
        batch_op.add_column(sa.Column("excluded_reason", sa.Text(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_rate_samples_excluded_at"), ["excluded_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("rate_samples", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_rate_samples_excluded_at"))
        batch_op.drop_column("excluded_reason")
        batch_op.drop_column("excluded_at")
