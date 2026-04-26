"""Add MFA TOTP enrollment + recovery code tables.

Revision ID: 0015
Revises: 0014
Create Date: 2026-04-25

The TOTP secret is stored encrypted at rest. The cipher is `aes-gcm-pbkdf2`
with the key derived from `MFA_SECRET_KEY`; future hardening moves the key
custody to a managed KMS without changing the column shape.

Recovery codes are stored as SHA-256 hashes only; the plaintext is shown to
the user exactly once at enrollment.

The login challenge flow itself (prompting for a TOTP code mid-login) is
*not* yet wired — see `docs/soc2/MFA_LOGIN_CHALLENGE.md` for the design and
the work it requires. Today the API exposes setup / confirm / disable /
status endpoints and the table provides the authoritative enrollment record
the future challenge step will consume.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "nextballup_app"


def _grant_runtime(table: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    role_exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
        {"role": APP_ROLE},
    ).scalar()
    if role_exists:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {table} TO {APP_ROLE}")


def upgrade() -> None:
    op.create_table(
        "user_totp_secrets",
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
        sa.Column("secret_ciphertext", sa.String(512), nullable=False),
        sa.Column(
            "cipher",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'aes-gcm-pbkdf2'"),
        ),
        sa.Column(
            "issuer_label",
            sa.String(255),
            nullable=False,
            server_default=sa.text("'NextBallUp'"),
        ),
        sa.Column("account_label", sa.String(255), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_user_totp_secrets_user_id_users",
        ),
        sa.UniqueConstraint("user_id", name="uq_user_totp_secrets_user"),
    )
    op.create_index(
        "ix_user_totp_secrets_confirmed",
        "user_totp_secrets",
        ["user_id", "confirmed_at"],
    )

    op.create_table(
        "mfa_recovery_codes",
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
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
            name="fk_mfa_recovery_codes_user_id_users",
        ),
        sa.UniqueConstraint("code_hash", name="uq_mfa_recovery_codes_code_hash"),
    )
    op.create_index(
        "ix_mfa_recovery_codes_user_active",
        "mfa_recovery_codes",
        ["user_id", "used_at"],
    )

    for table in ("user_totp_secrets", "mfa_recovery_codes"):
        _grant_runtime(table)


def downgrade() -> None:
    op.drop_index("ix_mfa_recovery_codes_user_active", table_name="mfa_recovery_codes")
    op.drop_table("mfa_recovery_codes")
    op.drop_index("ix_user_totp_secrets_confirmed", table_name="user_totp_secrets")
    op.drop_table("user_totp_secrets")
