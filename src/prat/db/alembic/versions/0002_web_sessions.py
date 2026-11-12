"""web sessions and used login codes

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "web_sessions",
        sa.Column("id_hash", sa.String(length=64), nullable=False),
        sa.Column("csrf_token", sa.String(length=64), nullable=False),
        sa.Column("token_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id_hash", name=op.f("pk_web_sessions")),
    )
    with op.batch_alter_table("web_sessions", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_web_sessions_expires_at"), ["expires_at"], unique=False
        )

    op.create_table(
        "login_nonces",
        sa.Column("nonce_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("nonce_hash", name=op.f("pk_login_nonces")),
    )
    with op.batch_alter_table("login_nonces", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_login_nonces_expires_at"), ["expires_at"], unique=False
        )


def downgrade() -> None:
    with op.batch_alter_table("login_nonces", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_login_nonces_expires_at"))

    op.drop_table("login_nonces")
    with op.batch_alter_table("web_sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_web_sessions_expires_at"))

    op.drop_table("web_sessions")
