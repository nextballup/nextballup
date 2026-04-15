"""Harden Phase 3 RLS and processing job integrity.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-15

This migration:
  * adds a uniqueness guarantee for one processing job per (video, stage)
  * extends SELECT RLS policies with an admin-role fallback so operator
    backstop behavior keeps working under FORCE ROW LEVEL SECURITY
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TEAM_UUID = "NULLIF(current_setting('app.current_team_id', true), '')::uuid"
USER_UUID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"
JOIN_CODE = "NULLIF(current_setting('app.current_join_invite_code', true), '')"
ADMIN_ROLE = "NULLIF(current_setting('app.current_user_role', true), '') = 'admin'"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_processing_jobs_video_stage", "processing_jobs", ["video_id", "stage"]
    )

    op.execute("DROP POLICY IF EXISTS teams_select_access ON teams")
    op.execute("DROP POLICY IF EXISTS team_memberships_select_access ON team_memberships")
    op.execute("DROP POLICY IF EXISTS team_invites_select_access ON team_invites")
    op.execute("DROP POLICY IF EXISTS audit_logs_select_access ON audit_logs")
    op.execute("DROP POLICY IF EXISTS games_select_access ON games")
    op.execute("DROP POLICY IF EXISTS videos_select_access ON videos")
    op.execute("DROP POLICY IF EXISTS processing_jobs_select_access ON processing_jobs")

    op.execute(
        f"""
        CREATE POLICY teams_select_access ON teams
            FOR SELECT
            USING (
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
            );
        """
    )
    op.execute(
        f"""
        CREATE POLICY team_memberships_select_access ON team_memberships
            FOR SELECT
            USING (
                {ADMIN_ROLE}
                OR team_id = {TEAM_UUID}
                OR user_id = {USER_UUID}
            );
        """
    )
    op.execute(
        f"""
        CREATE POLICY team_invites_select_access ON team_invites
            FOR SELECT
            USING (
                {ADMIN_ROLE}
                OR team_id = {TEAM_UUID}
                OR invite_code = {JOIN_CODE}
            );
        """
    )
    op.execute(
        f"""
        CREATE POLICY audit_logs_select_access ON audit_logs
            FOR SELECT
            USING (
                {ADMIN_ROLE}
                OR team_id IS NULL
                OR team_id = {TEAM_UUID}
                OR actor_user_id = {USER_UUID}
            );
        """
    )
    for table in ("games", "videos", "processing_jobs"):
        op.execute(
            f"""
            CREATE POLICY {table}_select_access ON {table}
                FOR SELECT
                USING (
                    {ADMIN_ROLE}
                    OR team_id = {TEAM_UUID}
                    OR EXISTS (
                        SELECT 1
                        FROM team_memberships tm
                        WHERE tm.team_id = {table}.team_id
                          AND tm.user_id = {USER_UUID}
                          AND tm.is_active
                    )
                );
            """
        )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS teams_select_access ON teams")
    op.execute("DROP POLICY IF EXISTS team_memberships_select_access ON team_memberships")
    op.execute("DROP POLICY IF EXISTS team_invites_select_access ON team_invites")
    op.execute("DROP POLICY IF EXISTS audit_logs_select_access ON audit_logs")
    op.execute("DROP POLICY IF EXISTS games_select_access ON games")
    op.execute("DROP POLICY IF EXISTS videos_select_access ON videos")
    op.execute("DROP POLICY IF EXISTS processing_jobs_select_access ON processing_jobs")

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
        CREATE POLICY audit_logs_select_access ON audit_logs
            FOR SELECT
            USING (
                team_id IS NULL
                OR team_id = {TEAM_UUID}
                OR actor_user_id = {USER_UUID}
            );
        """
    )
    for table in ("games", "videos", "processing_jobs"):
        op.execute(
            f"""
            CREATE POLICY {table}_select_access ON {table}
                FOR SELECT
                USING (
                    team_id = {TEAM_UUID}
                    OR EXISTS (
                        SELECT 1
                        FROM team_memberships tm
                        WHERE tm.team_id = {table}.team_id
                          AND tm.user_id = {USER_UUID}
                          AND tm.is_active
                    )
                );
            """
        )

    op.drop_constraint("uq_processing_jobs_video_stage", "processing_jobs", type_="unique")
