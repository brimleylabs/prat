"""self-improvement: settings, error log, cycles, findings, events, self project flag

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "app_settings",
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_settings")),
    )

    op.create_table(
        "error_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=120), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_error_log")),
    )
    with op.batch_alter_table("error_log", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_error_log_created_at"), ["created_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_error_log_fingerprint"), ["fingerprint"], unique=False)

    op.create_table(
        "improve_cycles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "trigger",
            sa.Enum("schedule", "manual", name="cycle_trigger", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "succeeded",
                "failed",
                "cancelled",
                "skipped",
                name="cycle_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "stage",
            sa.Enum(
                "diagnose",
                "research",
                "select",
                "done",
                name="cycle_stage",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("summary", sa.JSON(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_improve_cycles")),
    )
    with op.batch_alter_table("improve_cycles", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_improve_cycles_started_at"), ["started_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_improve_cycles_status"), ["status"], unique=False)

    op.create_table(
        "improve_findings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("cycle_id", sa.Integer(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "diagnostic",
                "research",
                "roadmap",
                name="finding_source",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("proposal", sa.Text(), nullable=False),
        sa.Column("acceptance_criteria", sa.Text(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column(
            "severity",
            sa.Enum("high", "medium", "low", name="finding_severity", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "effort",
            sa.Enum("low", "medium", "high", name="finding_effort", native_enum=False, length=32),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "new",
                "carded",
                "landed",
                "dismissed",
                "stale",
                "failed",
                name="finding_status",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("card_id", sa.Integer(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("seen_count", sa.Integer(), nullable=False),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["cards.id"],
            name=op.f("fk_improve_findings_card_id_cards"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["improve_cycles.id"],
            name=op.f("fk_improve_findings_cycle_id_improve_cycles"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_improve_findings")),
        sa.UniqueConstraint("fingerprint", name=op.f("uq_improve_findings_fingerprint")),
    )
    with op.batch_alter_table("improve_findings", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_improve_findings_card_id"), ["card_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_improve_findings_status"), ["status"], unique=False)

    op.create_table(
        "improve_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cycle_id", sa.Integer(), nullable=True),
        sa.Column("card_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["cards.id"],
            name=op.f("fk_improve_events_card_id_cards"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["cycle_id"],
            ["improve_cycles.id"],
            name=op.f("fk_improve_events_cycle_id_improve_cycles"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_improve_events")),
    )
    with op.batch_alter_table("improve_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_improve_events_card_id"), ["card_id"], unique=False)
        batch_op.create_index(
            batch_op.f("ix_improve_events_created_at"), ["created_at"], unique=False
        )
        batch_op.create_index(batch_op.f("ix_improve_events_cycle_id"), ["cycle_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_improve_events_kind"), ["kind"], unique=False)

    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_self", sa.Boolean(), server_default=sa.false(), nullable=False)
        )


def downgrade() -> None:
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_column("is_self")

    with op.batch_alter_table("improve_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_improve_events_kind"))
        batch_op.drop_index(batch_op.f("ix_improve_events_cycle_id"))
        batch_op.drop_index(batch_op.f("ix_improve_events_created_at"))
        batch_op.drop_index(batch_op.f("ix_improve_events_card_id"))

    op.drop_table("improve_events")
    with op.batch_alter_table("improve_findings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_improve_findings_status"))
        batch_op.drop_index(batch_op.f("ix_improve_findings_card_id"))

    op.drop_table("improve_findings")
    with op.batch_alter_table("improve_cycles", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_improve_cycles_status"))
        batch_op.drop_index(batch_op.f("ix_improve_cycles_started_at"))

    op.drop_table("improve_cycles")
    with op.batch_alter_table("error_log", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_error_log_fingerprint"))
        batch_op.drop_index(batch_op.f("ix_error_log_created_at"))

    op.drop_table("error_log")
    op.drop_table("app_settings")
