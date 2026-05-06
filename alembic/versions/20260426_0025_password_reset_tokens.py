"""Add password reset token table.

Revision ID: 0025
Revises: 0024
Create Date: 2026-04-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "nextballup_app"
USER_UUID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
USER_ROLE = "NULLIF(current_setting('app.current_user_role', true), '')"
PASSWORD_RESET_TOKEN_HASH = (
    "NULLIF(current_setting('app.current_password_reset_token_hash', true), '')"
)


def _role_exists() -> bool:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return False
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
            {"role": APP_ROLE},
        ).first()
    )


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
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
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("requested_ip", postgresql.INET),
        sa.Column("requested_user_agent", sa.String(500)),
        sa.Column("reset_ip", postgresql.INET),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_password_reset_tokens_user_id_users",
        ),
        sa.UniqueConstraint("token_hash", name="uq_password_reset_tokens_token_hash"),
    )
    op.create_index(
        "ix_password_reset_tokens_user_created",
        "password_reset_tokens",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_password_reset_tokens_active",
        "password_reset_tokens",
        ["user_id", "used_at", "expires_at"],
    )
    if _role_exists():
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE password_reset_tokens TO {APP_ROLE}"
        )
    op.execute("ALTER TABLE password_reset_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE password_reset_tokens FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY password_reset_tokens_select_access ON password_reset_tokens
            FOR SELECT
            USING (
                user_id = {USER_UUID}
                OR token_hash = {PASSWORD_RESET_TOKEN_HASH}
                OR {USER_ROLE} = 'admin'
            );
        """
    )
    op.execute(
        f"""
        CREATE POLICY password_reset_tokens_insert_owner ON password_reset_tokens
            FOR INSERT
            WITH CHECK (user_id = {USER_UUID});
        """
    )
    op.execute(
        f"""
        CREATE POLICY password_reset_tokens_update_access ON password_reset_tokens
            FOR UPDATE
            USING (
                user_id = {USER_UUID}
                OR token_hash = {PASSWORD_RESET_TOKEN_HASH}
            )
            WITH CHECK (
                user_id = {USER_UUID}
                OR token_hash = {PASSWORD_RESET_TOKEN_HASH}
            );
        """
    )
    op.execute(
        f"""
        CREATE POLICY password_reset_tokens_delete_admin ON password_reset_tokens
            FOR DELETE
            USING ({USER_ROLE} = 'admin');
        """
    )


def downgrade() -> None:
    for suffix in ("select_access", "insert_owner", "update_access", "delete_admin"):
        op.execute(f"DROP POLICY IF EXISTS password_reset_tokens_{suffix} ON password_reset_tokens")
    op.execute("ALTER TABLE password_reset_tokens NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE password_reset_tokens DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_password_reset_tokens_active", table_name="password_reset_tokens")
    op.drop_index("ix_password_reset_tokens_user_created", table_name="password_reset_tokens")
    op.drop_table("password_reset_tokens")
