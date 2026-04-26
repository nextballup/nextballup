"""Add server-side refresh token sessions.

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-25

Refresh JWTs are still carried only in httpOnly cookies, but each token's
random `jti` is now tracked server-side by hash so refreshes are one-time-use
and can be revoked independently of the coarse user.session_version.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "nextballup_app"


def upgrade() -> None:
    op.create_table(
        "refresh_sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("jti_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_reason", sa.String(64)),
        sa.Column("replaced_by_session_id", postgresql.UUID(as_uuid=True)),
        sa.Column("ip_address", postgresql.INET),
        sa.Column("user_agent", sa.String(500)),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_refresh_sessions_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["replaced_by_session_id"],
            ["refresh_sessions.id"],
            ondelete="SET NULL",
            name="fk_refresh_sessions_replaced_by_session_id_refresh_sessions",
        ),
        sa.UniqueConstraint("jti_hash", name="uq_refresh_sessions_jti_hash"),
    )
    op.create_index(
        "ix_refresh_sessions_user_created",
        "refresh_sessions",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_refresh_sessions_user_active",
        "refresh_sessions",
        ["user_id", "revoked_at", "expires_at"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        role_exists = bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
            {"role": APP_ROLE},
        ).scalar()
        if role_exists:
            op.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE refresh_sessions TO {APP_ROLE}"
            )


def downgrade() -> None:
    op.drop_index("ix_refresh_sessions_user_active", table_name="refresh_sessions")
    op.drop_index("ix_refresh_sessions_user_created", table_name="refresh_sessions")
    op.drop_table("refresh_sessions")
