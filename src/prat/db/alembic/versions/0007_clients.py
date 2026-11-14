"""clients: who a project's work is for

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATETIME = sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql", "mariadb")


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=63), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", DATETIME, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clients")),
        sa.UniqueConstraint("slug", name=op.f("uq_clients_slug")),
    )
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.add_column(sa.Column("client_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_projects_client_id"), ["client_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_projects_client_id_clients"),
            "clients",
            ["client_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_projects_client_id_clients"), type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_projects_client_id"))
        batch_op.drop_column("client_id")
    op.drop_table("clients")
