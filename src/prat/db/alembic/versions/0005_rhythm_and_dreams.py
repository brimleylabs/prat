"""rhythm and dreams: activity hours and quiet-hours runs

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATETIME = sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "activity_buckets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.String(length=10), nullable=False),
        sa.Column("hour_of_week", sa.Integer(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("last_seen", DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity_buckets")),
        sa.UniqueConstraint(
            "week_start",
            "hour_of_week",
            name=op.f("uq_activity_buckets_week_start"),
        ),
    )

    op.create_table(
        "dreams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trigger", sa.String(length=20), nullable=False),
        sa.Column("window_start", sa.String(length=40), nullable=False),
        sa.Column("window_end", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("phase", sa.String(length=20), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("started_at", DATETIME, nullable=False),
        sa.Column("finished_at", DATETIME, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dreams")),
    )
    with op.batch_alter_table("dreams", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_dreams_started_at"), ["started_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_dreams_status"), ["status"], unique=False)
        batch_op.create_index(batch_op.f("ix_dreams_window_start"), ["window_start"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("dreams", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_dreams_window_start"))
        batch_op.drop_index(batch_op.f("ix_dreams_status"))
        batch_op.drop_index(batch_op.f("ix_dreams_started_at"))
    op.drop_table("dreams")
    op.drop_table("activity_buckets")
