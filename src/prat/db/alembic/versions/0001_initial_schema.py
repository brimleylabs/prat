"""initial schema

Revision ID: 0001
Revises: (none)
Create Date: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("checkout_path", sa.Text(), nullable=False),
        sa.Column("agent_path", sa.Text(), nullable=False),
        sa.Column("default_branch", sa.String(length=200), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
        sa.UniqueConstraint("slug", name=op.f("uq_projects_slug")),
    )
    op.create_table(
        "cards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", sa.Text(), nullable=False),
        sa.Column(
            "complexity",
            sa.Enum("low", "medium", "high", name="complexity", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "ready",
                "running",
                "review",
                "rework",
                "done",
                "cancelled",
                "failed",
                "blocked",
                name="card_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "autonomy",
            sa.Enum(
                "read_only", "low", "medium", "high", name="autonomy", native_enum=False, length=32
            ),
            nullable=True,
        ),
        sa.Column("budget_usd", sa.Float(), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_cards_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cards")),
    )
    with op.batch_alter_table("cards", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_cards_project_id"), ["project_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_cards_status"), ["status"], unique=False)

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "running",
                "interrupted",
                "succeeded",
                "failed",
                "blocked",
                "cancelled",
                name="run_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.Enum(
                "queued",
                "running",
                "interrupted",
                "succeeded",
                "failed",
                "blocked",
                "cancelled",
                name="run_outcome",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("runtime", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=255), nullable=True),
        sa.Column(
            "autonomy",
            sa.Enum(
                "read_only",
                "low",
                "medium",
                "high",
                name="run_autonomy",
                native_enum=False,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("budget_usd", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("worktree_path", sa.Text(), nullable=True),
        sa.Column("branch", sa.String(length=255), nullable=True),
        sa.Column("base_commit", sa.String(length=64), nullable=True),
        sa.Column("start_commit", sa.String(length=64), nullable=True),
        sa.Column("head_commit", sa.String(length=64), nullable=True),
        sa.Column("spec", sa.JSON(), nullable=True),
        sa.Column("feedback", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("journal_path", sa.Text(), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name=op.f("fk_runs_card_id_cards"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
    )
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_runs_card_id"), ["card_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_runs_status"), ["status"], unique=False)

    op.create_table(
        "comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column(
            "author",
            sa.Enum(
                "human", "reviewer", "system", name="comment_author", native_enum=False, length=32
            ),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name=op.f("fk_comments_card_id_cards"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_comments_run_id_runs"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
    )
    with op.batch_alter_table("comments", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_comments_card_id"), ["card_id"], unique=False)

    op.create_table(
        "events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("card_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name=op.f("fk_events_card_id_cards"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_events_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_events_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_events")),
    )
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_events_card_id"), ["card_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_events_project_id"), ["project_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_events_run_id"), ["run_id"], unique=False)

    op.create_table(
        "run_steps",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column(
            "name",
            sa.Enum(
                "prepare",
                "plan",
                "execute",
                "verify",
                "critique",
                "summarize",
                "finalize",
                name="step_name",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "succeeded",
                "failed",
                "skipped",
                name="step_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_run_steps_run_id_runs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_steps")),
        sa.UniqueConstraint("run_id", "name", name="run_step"),
    )
    with op.batch_alter_table("run_steps", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_run_steps_run_id"), ["run_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("run_steps", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_run_steps_run_id"))

    op.drop_table("run_steps")
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_events_run_id"))
        batch_op.drop_index(batch_op.f("ix_events_project_id"))
        batch_op.drop_index(batch_op.f("ix_events_card_id"))

    op.drop_table("events")
    with op.batch_alter_table("comments", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_comments_card_id"))

    op.drop_table("comments")
    with op.batch_alter_table("runs", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_runs_status"))
        batch_op.drop_index(batch_op.f("ix_runs_card_id"))

    op.drop_table("runs")
    with op.batch_alter_table("cards", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cards_status"))
        batch_op.drop_index(batch_op.f("ix_cards_project_id"))

    op.drop_table("cards")
    op.drop_table("projects")
