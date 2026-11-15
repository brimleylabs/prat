"""crews, jobs and epics: run usage and live state, card parts and holds, job runs

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATETIME = sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("usage", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("live", sa.JSON(), nullable=True))
    with op.batch_alter_table("cards", schema=None) as batch_op:
        batch_op.add_column(sa.Column("parent_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("depends_on", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("hold_reason", sa.Text(), nullable=True))
        batch_op.create_index(batch_op.f("ix_cards_parent_id"), ["parent_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_cards_parent_id_cards"),
            "cards",
            ["parent_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_table(
        "job_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("command", sa.JSON(), nullable=True),
        sa.Column("workdir", sa.Text(), nullable=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("exit_code", sa.Integer(), nullable=True),
        sa.Column("progress_path", sa.Text(), nullable=True),
        sa.Column("log_path", sa.Text(), nullable=True),
        sa.Column("database", sa.String(length=16), nullable=False),
        sa.Column("data_state", sa.String(length=16), nullable=True),
        sa.Column("last", sa.JSON(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", DATETIME, nullable=False),
        sa.Column("started_at", DATETIME, nullable=True),
        sa.Column("finished_at", DATETIME, nullable=True),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name=op.f("fk_job_runs_card_id_cards"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_job_runs_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job_runs")),
    )
    with op.batch_alter_table("job_runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_job_runs_project_id"), ["project_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_job_runs_card_id"), ["card_id"], unique=False)


def downgrade() -> None:
    op.drop_table("job_runs")
    with op.batch_alter_table("cards", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_cards_parent_id_cards"), type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_cards_parent_id"))
        batch_op.drop_column("hold_reason")
        batch_op.drop_column("depends_on")
        batch_op.drop_column("parent_id")
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_column("live")
        batch_op.drop_column("usage")
