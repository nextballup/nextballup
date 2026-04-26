"""Allow admin maintenance pruning of email verification tokens.

Revision ID: 0022
Revises: 0021
Create Date: 2026-04-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_ROLE = "NULLIF(current_setting('app.current_user_role', true), '')"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE POLICY email_verification_tokens_delete_admin
            ON email_verification_tokens
            FOR DELETE
            USING ({USER_ROLE} = 'admin');
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS email_verification_tokens_delete_admin ON email_verification_tokens"
    )
