"""Add email verification token table.

Revision ID: 0012
Revises: 0011
Create Date: 2026-04-25

The verification flow stores only the SHA-256 hash of each token. Raw tokens
are minted by the API, delivered out-of-band by the configured email provider,
and never persisted. Replay protection comes from `used_at`; expiry comes from
`expires_at`.

The table is intentionally not tenant-scoped (it has no `team_id` column), so
no row-level-security policy is created — admit/deny are entirely keyed off
`user_id` at the application layer. The runtime app role still receives the
standard CRUD grants so RLS-on tables remain readable from the same session.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "nextballup_app"


def upgrade() -> None:
    op.create_table(
        "email_verification_tokens",
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
        sa.Column("confirmed_ip", postgresql.INET),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_email_verification_tokens_user_id_users",
        ),
        sa.UniqueConstraint("token_hash", name="uq_email_verification_tokens_token_hash"),
    )
    op.create_index(
        "ix_email_verification_tokens_user_created",
        "email_verification_tokens",
        ["user_id", "created_at"],
    )
    op.create_index(
        "ix_email_verification_tokens_active",
        "email_verification_tokens",
        ["user_id", "used_at", "expires_at"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        role_exists = bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
            {"role": APP_ROLE},
        ).scalar()
        if role_exists:
            op.execute(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE email_verification_tokens "
                f"TO {APP_ROLE}"
            )


def downgrade() -> None:
    op.drop_index("ix_email_verification_tokens_active", table_name="email_verification_tokens")
    op.drop_index(
        "ix_email_verification_tokens_user_created", table_name="email_verification_tokens"
    )
    op.drop_table("email_verification_tokens")
