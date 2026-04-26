"""Harden RLS for identity and billing tables added in 0012-0015.

Revision ID: 0016
Revises: 0015
Create Date: 2026-04-25

The commercial-readiness migrations added email verification, billing, and MFA
tables. This migration tightens the database boundary around those rows:

* email verification tokens are visible only to the owning user or to the
  token-confirm path after it binds the presented token hash to a local GUC;
* MFA secrets and recovery-code hashes are visible only to the owning user
  (or admin maintenance context);
* account_team_links can be resolved from the already-authorized team context,
  while writes require both team and billing-account context.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

USER_UUID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
USER_ROLE = "NULLIF(current_setting('app.current_user_role', true), '')"
TEAM_UUID = "NULLIF(current_setting('app.current_team_id', true), '')::uuid"
ACCOUNT_UUID = "NULLIF(current_setting('app.current_billing_account_id', true), '')::uuid"
EMAIL_TOKEN_HASH = "NULLIF(current_setting('app.current_email_verification_token_hash', true), '')"


def upgrade() -> None:
    op.execute("ALTER TABLE email_verification_tokens ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE email_verification_tokens FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY email_verification_tokens_select_access ON email_verification_tokens
            FOR SELECT
            USING (
                user_id = {USER_UUID}
                OR token_hash = {EMAIL_TOKEN_HASH}
            );
        """
    )
    op.execute(
        f"""
        CREATE POLICY email_verification_tokens_insert_owner ON email_verification_tokens
            FOR INSERT
            WITH CHECK (user_id = {USER_UUID});
        """
    )
    op.execute(
        f"""
        CREATE POLICY email_verification_tokens_update_access ON email_verification_tokens
            FOR UPDATE
            USING (
                user_id = {USER_UUID}
                OR token_hash = {EMAIL_TOKEN_HASH}
            )
            WITH CHECK (
                user_id = {USER_UUID}
                OR token_hash = {EMAIL_TOKEN_HASH}
            );
        """
    )

    for table in ("user_totp_secrets", "mfa_recovery_codes"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_user_access ON {table}
                FOR ALL
                USING (
                    {USER_ROLE} = 'admin'
                    OR user_id = {USER_UUID}
                )
                WITH CHECK (
                    {USER_ROLE} = 'admin'
                    OR user_id = {USER_UUID}
                );
            """
        )

    for suffix in ("select_access", "write_context"):
        op.execute(f"DROP POLICY IF EXISTS account_team_links_{suffix} ON account_team_links")
    op.execute(
        f"""
        CREATE POLICY account_team_links_select_access ON account_team_links
            FOR SELECT
            USING (
                {USER_ROLE} = 'admin'
                OR billing_account_id = {ACCOUNT_UUID}
                OR team_id = {TEAM_UUID}
            );
        """
    )
    op.execute(
        f"""
        CREATE POLICY account_team_links_write_context ON account_team_links
            FOR ALL
            USING (
                {USER_ROLE} = 'admin'
                OR (
                    billing_account_id = {ACCOUNT_UUID}
                    AND team_id = {TEAM_UUID}
                )
            )
            WITH CHECK (
                {USER_ROLE} = 'admin'
                OR (
                    billing_account_id = {ACCOUNT_UUID}
                    AND team_id = {TEAM_UUID}
                )
            );
        """
    )


def downgrade() -> None:
    for suffix in ("select_access", "insert_owner", "update_access"):
        op.execute(
            f"DROP POLICY IF EXISTS email_verification_tokens_{suffix} ON email_verification_tokens"
        )
    op.execute("ALTER TABLE email_verification_tokens NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE email_verification_tokens DISABLE ROW LEVEL SECURITY")

    for table in ("user_totp_secrets", "mfa_recovery_codes"):
        op.execute(f"DROP POLICY IF EXISTS {table}_user_access ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    for suffix in ("select_access", "write_context"):
        op.execute(f"DROP POLICY IF EXISTS account_team_links_{suffix} ON account_team_links")
    op.execute(
        f"""
        CREATE POLICY account_team_links_select_access ON account_team_links
            FOR SELECT
            USING (
                {USER_ROLE} = 'admin'
                OR billing_account_id = {ACCOUNT_UUID}
            );
        """
    )
    op.execute(
        f"""
        CREATE POLICY account_team_links_write_context ON account_team_links
            FOR ALL
            USING (
                {USER_ROLE} = 'admin'
                OR billing_account_id = {ACCOUNT_UUID}
            )
            WITH CHECK (
                {USER_ROLE} = 'admin'
                OR billing_account_id = {ACCOUNT_UUID}
            );
        """
    )
