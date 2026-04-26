"""Harden append-only grants and soft-delete write policies.

Revision ID: 0023
Revises: 0022
Create Date: 2026-04-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "nextballup_app"
TEAM_UUID = "NULLIF(current_setting('app.current_team_id', true), '')::uuid"

TEAM_WRITE_TABLES = (
    "team_memberships",
    "team_invites",
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
APPEND_ONLY_TABLES = ("audit_logs", "csp_reports", "usage_events")


def _role_exists() -> bool:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return False
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
        ).first()
    )


def _visibility_clause(table: str, column: str) -> str:
    return f"nextballup_team_visible({table}.{column}) AND {column} = {TEAM_UUID}"


def _replace_team_write_policies(*, soft_delete: bool) -> None:
    for policy in ("insert_context", "update_context", "delete_context"):
        op.execute(f"DROP POLICY IF EXISTS teams_{policy} ON teams")
    for table in TEAM_WRITE_TABLES:
        for policy in ("insert_context", "update_context", "delete_context"):
            op.execute(f"DROP POLICY IF EXISTS {table}_{policy} ON {table}")

    teams_update_delete = _visibility_clause("teams", "id") if soft_delete else f"id = {TEAM_UUID}"
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
            USING ({teams_update_delete})
            WITH CHECK ({teams_update_delete});
        """
    )
    op.execute(
        f"""
        CREATE POLICY teams_delete_context ON teams
            FOR DELETE
            USING ({teams_update_delete});
        """
    )

    for table in TEAM_WRITE_TABLES:
        clause = _visibility_clause(table, "team_id") if soft_delete else f"team_id = {TEAM_UUID}"
        op.execute(
            f"""
            CREATE POLICY {table}_insert_context ON {table}
                FOR INSERT
                WITH CHECK ({clause});
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_update_context ON {table}
                FOR UPDATE
                USING ({clause})
                WITH CHECK ({clause});
            """
        )
        op.execute(
            f"""
            CREATE POLICY {table}_delete_context ON {table}
                FOR DELETE
                USING ({clause});
            """
        )


def _revoke_append_only_mutation_grants() -> None:
    if not _role_exists():
        return
    for table in APPEND_ONLY_TABLES:
        op.execute(f"REVOKE UPDATE, DELETE ON TABLE {table} FROM {APP_ROLE}")


def _restore_append_only_mutation_grants() -> None:
    if not _role_exists():
        return
    for table in APPEND_ONLY_TABLES:
        op.execute(f"GRANT UPDATE, DELETE ON TABLE {table} TO {APP_ROLE}")


def upgrade() -> None:
    _replace_team_write_policies(soft_delete=True)
    _revoke_append_only_mutation_grants()


def downgrade() -> None:
    _restore_append_only_mutation_grants()
    _replace_team_write_policies(soft_delete=False)
