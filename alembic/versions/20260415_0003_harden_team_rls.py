"""Harden team-scoped RLS policies for production.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-15

This migration:
  * makes owner connections subject to RLS via FORCE ROW LEVEL SECURITY
  * replaces the original single-GUC policies with safer policies that support:
      - team-scoped request context (`app.current_team_id`)
      - authenticated self-membership context (`app.current_user_id`)
      - invite-code lookup context (`app.current_join_invite_code`)
  * wraps all GUC casts in NULLIF(..., '') so context-clearing never crashes
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TEAM_UUID = "NULLIF(current_setting('app.current_team_id', true), '')::uuid"
USER_UUID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
JOIN_CODE = "NULLIF(current_setting('app.current_join_invite_code', true), '')"


def upgrade() -> None:
    op.execute("DROP POLICY IF EXISTS teams_tenant_isolation ON teams")
    op.execute("DROP POLICY IF EXISTS team_memberships_tenant_isolation ON team_memberships")
    op.execute("DROP POLICY IF EXISTS audit_logs_tenant_isolation ON audit_logs")
    op.execute("DROP POLICY IF EXISTS team_invites_tenant_isolation ON team_invites")

    op.execute("ALTER TABLE teams FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE team_memberships FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE team_invites FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_logs FORCE ROW LEVEL SECURITY")

    op.execute(
        f"""
        CREATE POLICY teams_select_access ON teams
            FOR SELECT
            USING (
                id = {TEAM_UUID}
                OR invite_code = {JOIN_CODE}
                OR EXISTS (
                    SELECT 1
                    FROM team_memberships tm
                    WHERE tm.team_id = teams.id
                      AND tm.user_id = {USER_UUID}
                      AND tm.is_active
                )
            );
        """
    )
    op.execute(
        f"""
        CREATE POLICY teams_insert_context ON teams
            FOR INSERT
            WITH CHECK (id = {TEAM_UUID});
        """
    )
    op.execute(
        f"""
        CREATE POLICY teams_update_context ON teams
            FOR UPDATE
            USING (id = {TEAM_UUID})
            WITH CHECK (id = {TEAM_UUID});
        """
    )
    op.execute(
        f"""
        CREATE POLICY teams_delete_context ON teams
            FOR DELETE
            USING (id = {TEAM_UUID});
        """
    )

    op.execute(
        f"""
        CREATE POLICY team_memberships_select_access ON team_memberships
            FOR SELECT
            USING (
                team_id = {TEAM_UUID}
                OR user_id = {USER_UUID}
            );
        """
    )
    op.execute(
        f"""
        CREATE POLICY team_memberships_insert_context ON team_memberships
            FOR INSERT
            WITH CHECK (team_id = {TEAM_UUID});
        """
    )
    op.execute(
        f"""
        CREATE POLICY team_memberships_update_context ON team_memberships
            FOR UPDATE
            USING (team_id = {TEAM_UUID})
            WITH CHECK (team_id = {TEAM_UUID});
        """
    )
    op.execute(
        f"""
        CREATE POLICY team_memberships_delete_context ON team_memberships
            FOR DELETE
            USING (team_id = {TEAM_UUID});
        """
    )

    op.execute(
        f"""
        CREATE POLICY team_invites_select_access ON team_invites
            FOR SELECT
            USING (
                team_id = {TEAM_UUID}
                OR invite_code = {JOIN_CODE}
            );
        """
    )
    op.execute(
        f"""
        CREATE POLICY team_invites_insert_context ON team_invites
            FOR INSERT
            WITH CHECK (team_id = {TEAM_UUID});
        """
    )
    op.execute(
        f"""
        CREATE POLICY team_invites_update_context ON team_invites
            FOR UPDATE
            USING (team_id = {TEAM_UUID})
            WITH CHECK (team_id = {TEAM_UUID});
        """
    )
    op.execute(
        f"""
        CREATE POLICY team_invites_delete_context ON team_invites
            FOR DELETE
            USING (team_id = {TEAM_UUID});
        """
    )

    op.execute(
        f"""
        CREATE POLICY audit_logs_select_access ON audit_logs
            FOR SELECT
            USING (
                team_id IS NULL
                OR team_id = {TEAM_UUID}
                OR actor_user_id = {USER_UUID}
            );
        """
    )
    op.execute(
        f"""
        CREATE POLICY audit_logs_insert_context ON audit_logs
            FOR INSERT
            WITH CHECK (
                team_id IS NULL
                OR team_id = {TEAM_UUID}
                OR actor_user_id = {USER_UUID}
            );
        """
    )


def downgrade() -> None:
    for policy in (
        "teams_select_access",
        "teams_insert_context",
        "teams_update_context",
        "teams_delete_context",
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON teams")
    for policy in (
        "team_memberships_select_access",
        "team_memberships_insert_context",
        "team_memberships_update_context",
        "team_memberships_delete_context",
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON team_memberships")
    for policy in (
        "team_invites_select_access",
        "team_invites_insert_context",
        "team_invites_update_context",
        "team_invites_delete_context",
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON team_invites")
    for policy in ("audit_logs_select_access", "audit_logs_insert_context"):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON audit_logs")

    op.execute("ALTER TABLE teams NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE team_memberships NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE team_invites NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_logs NO FORCE ROW LEVEL SECURITY")

    op.execute(
        """
        CREATE POLICY teams_tenant_isolation ON teams
            USING (id = NULLIF(current_setting('app.current_team_id', true), '')::uuid);
        """
    )
    op.execute(
        """
        CREATE POLICY team_memberships_tenant_isolation ON team_memberships
            USING (team_id = NULLIF(current_setting('app.current_team_id', true), '')::uuid);
        """
    )
    op.execute(
        """
        CREATE POLICY audit_logs_tenant_isolation ON audit_logs
            USING (
                team_id IS NULL
                OR team_id = NULLIF(current_setting('app.current_team_id', true), '')::uuid
            );
        """
    )
    op.execute(
        """
        CREATE POLICY team_invites_tenant_isolation ON team_invites
            USING (team_id = NULLIF(current_setting('app.current_team_id', true), '')::uuid);
        """
    )
