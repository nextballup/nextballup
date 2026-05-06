"""Run visibility sync triggers as security-definer functions.

Revision ID: 0026
Revises: 0025
Create Date: 2026-04-26
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TEAM_UUID = "NULLIF(current_setting('app.current_team_id', true), '')::uuid"
USER_UUID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
USER_ROLE = "NULLIF(current_setting('app.current_user_role', true), '')"
JOIN_CODE = "NULLIF(current_setting('app.current_join_invite_code', true), '')"
ACCOUNT_UUID = "NULLIF(current_setting('app.current_billing_account_id', true), '')::uuid"
ADMIN_ROLE = f"{USER_ROLE} = 'admin'"


def _replace_teams_select_policy(*, use_direct_deleted_at: bool) -> None:
    visibility = (
        "(teams.deleted_at IS NULL OR nextballup_include_deleted())"
        if use_direct_deleted_at
        else "nextballup_team_visible(teams.id)"
    )
    op.execute("DROP POLICY IF EXISTS teams_select_access ON teams")
    op.execute(
        f"""
        CREATE POLICY teams_select_access ON teams
            FOR SELECT
            USING (
                {visibility}
                AND (
                    {ADMIN_ROLE}
                    OR id = {TEAM_UUID}
                    OR invite_code = {JOIN_CODE}
                    OR EXISTS (
                        SELECT 1
                        FROM team_memberships tm
                        WHERE tm.team_id = teams.id
                          AND tm.user_id = {USER_UUID}
                          AND tm.is_active
                    )
                )
            );
        """
    )


def _replace_billing_account_policies(*, use_direct_deleted_at: bool) -> None:
    visibility = (
        "(billing_accounts.deleted_at IS NULL OR nextballup_include_deleted())"
        if use_direct_deleted_at
        else "nextballup_billing_account_visible(id)"
    )
    for suffix in (
        "select_access",
        "write_context",
        "insert_context",
        "update_context",
        "delete_context",
    ):
        op.execute(f"DROP POLICY IF EXISTS billing_accounts_{suffix} ON billing_accounts")

    op.execute(
        f"""
        CREATE POLICY billing_accounts_select_access ON billing_accounts
            FOR SELECT
            USING (
                {visibility}
                AND (
                    {ADMIN_ROLE}
                    OR id = {ACCOUNT_UUID}
                    OR owner_user_id = {USER_UUID}
                )
            );
        """
    )
    if use_direct_deleted_at:
        op.execute(
            f"""
            CREATE POLICY billing_accounts_insert_context ON billing_accounts
                FOR INSERT
                WITH CHECK (
                    {ADMIN_ROLE}
                    OR id = {ACCOUNT_UUID}
                );
            """
        )
        op.execute(
            f"""
            CREATE POLICY billing_accounts_update_context ON billing_accounts
                FOR UPDATE
                USING (
                    {visibility}
                    AND (
                        {ADMIN_ROLE}
                        OR id = {ACCOUNT_UUID}
                    )
                )
                WITH CHECK (
                    {visibility}
                    AND (
                        {ADMIN_ROLE}
                        OR id = {ACCOUNT_UUID}
                    )
                );
            """
        )
        op.execute(
            f"""
            CREATE POLICY billing_accounts_delete_context ON billing_accounts
                FOR DELETE
                USING (
                    {visibility}
                    AND (
                        {ADMIN_ROLE}
                        OR id = {ACCOUNT_UUID}
                    )
                );
            """
        )
    else:
        op.execute(
            f"""
            CREATE POLICY billing_accounts_write_context ON billing_accounts
                FOR ALL
                USING (
                    {visibility}
                    AND (
                        {ADMIN_ROLE}
                        OR id = {ACCOUNT_UUID}
                    )
                )
                WITH CHECK (
                    {visibility}
                    AND (
                        {ADMIN_ROLE}
                        OR id = {ACCOUNT_UUID}
                    )
                );
            """
        )


def _create_functions(*, security_definer: bool) -> None:
    security = "SECURITY DEFINER" if security_definer else "SECURITY INVOKER"
    search_path = "SET search_path = public, pg_temp" if security_definer else ""
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION sync_team_visibility()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        {security}
        {search_path}
        AS $$
        BEGIN
            INSERT INTO team_visibility (team_id, deleted_at)
            VALUES (NEW.id, NEW.deleted_at)
            ON CONFLICT (team_id) DO UPDATE SET deleted_at = EXCLUDED.deleted_at;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION sync_billing_account_visibility()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        {security}
        {search_path}
        AS $$
        BEGIN
            INSERT INTO billing_account_visibility (billing_account_id, deleted_at)
            VALUES (NEW.id, NEW.deleted_at)
            ON CONFLICT (billing_account_id) DO UPDATE SET deleted_at = EXCLUDED.deleted_at;
            RETURN NEW;
        END;
        $$;
        """
    )


def upgrade() -> None:
    _replace_teams_select_policy(use_direct_deleted_at=True)
    _replace_billing_account_policies(use_direct_deleted_at=True)
    _create_functions(security_definer=True)
    op.execute("REVOKE ALL ON FUNCTION sync_team_visibility() FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION sync_billing_account_visibility() FROM PUBLIC")


def downgrade() -> None:
    _replace_teams_select_policy(use_direct_deleted_at=False)
    _replace_billing_account_policies(use_direct_deleted_at=False)
    _create_functions(security_definer=False)
    op.execute("GRANT EXECUTE ON FUNCTION sync_team_visibility() TO PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION sync_billing_account_visibility() TO PUBLIC")
