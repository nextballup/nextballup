"""Add soft-delete visibility controls for teams and billing accounts.

Revision ID: 0021
Revises: 0020
Create Date: 2026-04-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "nextballup_app"
TEAM_UUID = "NULLIF(current_setting('app.current_team_id', true), '')::uuid"
USER_UUID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
USER_ROLE = "NULLIF(current_setting('app.current_user_role', true), '')"
JOIN_CODE = "NULLIF(current_setting('app.current_join_invite_code', true), '')"
ACCOUNT_UUID = "NULLIF(current_setting('app.current_billing_account_id', true), '')::uuid"
ADMIN_ROLE = f"{USER_ROLE} = 'admin'"

TEAM_TABLES = (
    "games",
    "videos",
    "processing_jobs",
    "team_privacy_consents",
    "video_frame_clocks",
    "video_object_detections",
    "video_tracks",
    "video_events",
    "video_metrics",
)


def _revoke_runtime(table: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    role_exists = bind.execute(
        sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
        {"role": APP_ROLE},
    ).scalar()
    if role_exists:
        op.execute(f"REVOKE ALL ON TABLE {table} FROM {APP_ROLE}")


def _team_select_policy(table: str, *, soft_delete: bool, include_admin: bool) -> str:
    visibility = f"nextballup_team_visible({table}.team_id) AND " if soft_delete else ""
    admin_clause = f"{ADMIN_ROLE} OR " if include_admin else ""
    return f"""
        CREATE POLICY {table}_select_access ON {table}
            FOR SELECT
            USING (
                {visibility}(
                    {admin_clause}team_id = {TEAM_UUID}
                    OR EXISTS (
                        SELECT 1
                        FROM team_memberships tm
                        WHERE tm.team_id = {table}.team_id
                          AND tm.user_id = {USER_UUID}
                          AND tm.is_active
                    )
                )
            );
    """


def _teams_select_policy(*, soft_delete: bool) -> str:
    visibility = "nextballup_team_visible(teams.id) AND " if soft_delete else ""
    return f"""
        CREATE POLICY teams_select_access ON teams
            FOR SELECT
            USING (
                {visibility}(
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


def _team_memberships_select_policy(*, soft_delete: bool) -> str:
    visibility = "nextballup_team_visible(team_memberships.team_id) AND " if soft_delete else ""
    return f"""
        CREATE POLICY team_memberships_select_access ON team_memberships
            FOR SELECT
            USING (
                {visibility}(
                    {ADMIN_ROLE}
                    OR team_id = {TEAM_UUID}
                    OR user_id = {USER_UUID}
                )
            );
    """


def _team_invites_select_policy(*, soft_delete: bool) -> str:
    visibility = "nextballup_team_visible(team_invites.team_id) AND " if soft_delete else ""
    return f"""
        CREATE POLICY team_invites_select_access ON team_invites
            FOR SELECT
            USING (
                {visibility}(
                    {ADMIN_ROLE}
                    OR team_id = {TEAM_UUID}
                    OR invite_code = {JOIN_CODE}
                )
            );
    """


def _audit_logs_select_policy(*, soft_delete: bool) -> str:
    team_visible = "nextballup_team_visible(audit_logs.team_id) AND " if soft_delete else ""
    return f"""
        CREATE POLICY audit_logs_select_access ON audit_logs
            FOR SELECT
            USING (
                team_id IS NULL
                OR (
                    {team_visible}(
                        {ADMIN_ROLE}
                        OR team_id = {TEAM_UUID}
                        OR actor_user_id = {USER_UUID}
                    )
                )
            );
    """


def _account_team_links_select_policy(*, soft_delete: bool) -> str:
    visibility = (
        "nextballup_billing_account_visible(billing_account_id) "
        "AND nextballup_team_visible(team_id) AND "
        if soft_delete
        else ""
    )
    return f"""
        CREATE POLICY account_team_links_select_access ON account_team_links
            FOR SELECT
            USING (
                {visibility}(
                    {ADMIN_ROLE}
                    OR billing_account_id = {ACCOUNT_UUID}
                    OR team_id = {TEAM_UUID}
                )
            );
    """


def _account_team_links_write_policy(*, soft_delete: bool) -> str:
    visibility = (
        "nextballup_billing_account_visible(billing_account_id) "
        "AND nextballup_team_visible(team_id) AND "
        if soft_delete
        else ""
    )
    return f"""
        CREATE POLICY account_team_links_write_context ON account_team_links
            FOR ALL
            USING (
                {visibility}(
                    {ADMIN_ROLE}
                    OR (
                        billing_account_id = {ACCOUNT_UUID}
                        AND team_id = {TEAM_UUID}
                    )
                )
            )
            WITH CHECK (
                {visibility}(
                    {ADMIN_ROLE}
                    OR (
                        billing_account_id = {ACCOUNT_UUID}
                        AND team_id = {TEAM_UUID}
                    )
                )
            );
    """


def _billing_accounts_select_policy(*, soft_delete: bool) -> str:
    visibility = "nextballup_billing_account_visible(id) AND " if soft_delete else ""
    return f"""
        CREATE POLICY billing_accounts_select_access ON billing_accounts
            FOR SELECT
            USING (
                {visibility}(
                    {ADMIN_ROLE}
                    OR id = {ACCOUNT_UUID}
                    OR owner_user_id = {USER_UUID}
                )
            );
    """


def _billing_accounts_write_policy(*, soft_delete: bool) -> str:
    visibility = "nextballup_billing_account_visible(id) AND " if soft_delete else ""
    return f"""
        CREATE POLICY billing_accounts_write_context ON billing_accounts
            FOR ALL
            USING (
                {visibility}(
                    {ADMIN_ROLE}
                    OR id = {ACCOUNT_UUID}
                )
            )
            WITH CHECK (
                {visibility}(
                    {ADMIN_ROLE}
                    OR id = {ACCOUNT_UUID}
                )
            );
    """


def _account_scoped_select_policy(table: str, *, soft_delete: bool) -> str:
    account_visible = (
        f"nextballup_billing_account_visible({table}.billing_account_id) AND "
        if soft_delete
        else ""
    )
    team_visible = (
        f"({table}.team_id IS NULL OR nextballup_team_visible({table}.team_id)) AND "
        if soft_delete and table == "usage_events"
        else ""
    )
    return f"""
        CREATE POLICY {table}_select_access ON {table}
            FOR SELECT
            USING (
                {account_visible}{team_visible}(
                    {ADMIN_ROLE}
                    OR billing_account_id = {ACCOUNT_UUID}
                )
            );
    """


def _account_scoped_write_policy(table: str, *, soft_delete: bool) -> str:
    account_visible = (
        f"nextballup_billing_account_visible({table}.billing_account_id) AND "
        if soft_delete
        else ""
    )
    team_visible = (
        f"({table}.team_id IS NULL OR nextballup_team_visible({table}.team_id)) AND "
        if soft_delete and table == "usage_events"
        else ""
    )
    return f"""
        CREATE POLICY {table}_write_context ON {table}
            FOR ALL
            USING (
                {account_visible}{team_visible}(
                    {ADMIN_ROLE}
                    OR billing_account_id = {ACCOUNT_UUID}
                )
            )
            WITH CHECK (
                {account_visible}{team_visible}(
                    {ADMIN_ROLE}
                    OR billing_account_id = {ACCOUNT_UUID}
                )
            );
    """


def _replace_policies(*, soft_delete: bool) -> None:
    for table in (
        "teams",
        "team_memberships",
        "team_invites",
        "audit_logs",
        *TEAM_TABLES,
        "account_team_links",
        "billing_accounts",
        "subscriptions",
        "usage_events",
    ):
        for suffix in (
            "select_access",
            "write_context",
        ):
            op.execute(f"DROP POLICY IF EXISTS {table}_{suffix} ON {table}")

    op.execute(_teams_select_policy(soft_delete=soft_delete))
    op.execute(_team_memberships_select_policy(soft_delete=soft_delete))
    op.execute(_team_invites_select_policy(soft_delete=soft_delete))
    op.execute(_audit_logs_select_policy(soft_delete=soft_delete))
    for table in TEAM_TABLES:
        op.execute(
            _team_select_policy(
                table,
                soft_delete=soft_delete,
                include_admin=table in {"games", "videos", "processing_jobs"},
            )
        )
    op.execute(_account_team_links_select_policy(soft_delete=soft_delete))
    op.execute(_account_team_links_write_policy(soft_delete=soft_delete))
    op.execute(_billing_accounts_select_policy(soft_delete=soft_delete))
    op.execute(_billing_accounts_write_policy(soft_delete=soft_delete))
    for table in ("subscriptions", "usage_events"):
        op.execute(_account_scoped_select_policy(table, soft_delete=soft_delete))
        op.execute(_account_scoped_write_policy(table, soft_delete=soft_delete))


def upgrade() -> None:
    op.add_column("teams", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("billing_accounts", sa.Column("deleted_at", sa.DateTime(timezone=True)))

    op.create_table(
        "team_visibility",
        sa.Column("team_id", sa.UUID(), primary_key=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "billing_account_visibility",
        sa.Column("billing_account_id", sa.UUID(), primary_key=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["billing_account_id"], ["billing_accounts.id"], ondelete="CASCADE"
        ),
    )
    _revoke_runtime("team_visibility")
    _revoke_runtime("billing_account_visibility")
    op.execute("INSERT INTO team_visibility (team_id, deleted_at) SELECT id, deleted_at FROM teams")
    op.execute(
        "INSERT INTO billing_account_visibility (billing_account_id, deleted_at) "
        "SELECT id, deleted_at FROM billing_accounts"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION sync_team_visibility()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO team_visibility (team_id, deleted_at)
            VALUES (NEW.id, NEW.deleted_at)
            ON CONFLICT (team_id) DO UPDATE SET deleted_at = EXCLUDED.deleted_at;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_sync_team_visibility
        AFTER INSERT OR UPDATE OF deleted_at ON teams
        FOR EACH ROW EXECUTE FUNCTION sync_team_visibility();
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sync_billing_account_visibility()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO billing_account_visibility (billing_account_id, deleted_at)
            VALUES (NEW.id, NEW.deleted_at)
            ON CONFLICT (billing_account_id) DO UPDATE SET deleted_at = EXCLUDED.deleted_at;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_sync_billing_account_visibility
        AFTER INSERT OR UPDATE OF deleted_at ON billing_accounts
        FOR EACH ROW EXECUTE FUNCTION sync_billing_account_visibility();
        """
    )
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION nextballup_include_deleted()
        RETURNS boolean
        LANGUAGE sql
        STABLE
        AS $$
            SELECT {ADMIN_ROLE}
                   AND NULLIF(current_setting('app.include_deleted', true), '') = 'true';
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION nextballup_team_visible(row_team_id uuid)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT COALESCE(
                (
                    SELECT tv.deleted_at IS NULL OR nextballup_include_deleted()
                    FROM team_visibility tv
                    WHERE tv.team_id = row_team_id
                ),
                false
            );
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION nextballup_billing_account_visible(row_account_id uuid)
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
            SELECT COALESCE(
                (
                    SELECT bav.deleted_at IS NULL OR nextballup_include_deleted()
                    FROM billing_account_visibility bav
                    WHERE bav.billing_account_id = row_account_id
                ),
                false
            );
        $$;
        """
    )
    _replace_policies(soft_delete=True)


def downgrade() -> None:
    _replace_policies(soft_delete=False)
    op.execute("DROP FUNCTION IF EXISTS nextballup_billing_account_visible(uuid)")
    op.execute("DROP FUNCTION IF EXISTS nextballup_team_visible(uuid)")
    op.execute("DROP FUNCTION IF EXISTS nextballup_include_deleted()")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_billing_account_visibility ON billing_accounts")
    op.execute("DROP FUNCTION IF EXISTS sync_billing_account_visibility()")
    op.execute("DROP TRIGGER IF EXISTS trg_sync_team_visibility ON teams")
    op.execute("DROP FUNCTION IF EXISTS sync_team_visibility()")
    op.drop_table("billing_account_visibility")
    op.drop_table("team_visibility")
    op.drop_column("billing_accounts", "deleted_at")
    op.drop_column("teams", "deleted_at")
