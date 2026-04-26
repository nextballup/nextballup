"""Add privacy consent ledger and raw-video retention controls.

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-25

This migration gives the API an enforceable team-scoped consent record that
uploads can reference, and gives the worker explicit raw-video retention
fields so cleanup can remove source objects after the configured policy
window.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "nextballup_app"
TEAM_UUID = "NULLIF(current_setting('app.current_team_id', true), '')::uuid"
USER_UUID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


def _team_id_select_policy(table: str) -> str:
    return f"""
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


def _team_id_write_policies(table: str) -> tuple[str, str, str]:
    return (
        f"""
        CREATE POLICY {table}_insert_context ON {table}
            FOR INSERT
            WITH CHECK (team_id = {TEAM_UUID});
        """,
        f"""
        CREATE POLICY {table}_update_context ON {table}
            FOR UPDATE
            USING (team_id = {TEAM_UUID})
            WITH CHECK (team_id = {TEAM_UUID});
        """,
        f"""
        CREATE POLICY {table}_delete_context ON {table}
            FOR DELETE
            USING (team_id = {TEAM_UUID});
        """,
    )


def upgrade() -> None:
    op.create_table(
        "team_privacy_consents",
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
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recorded_by", postgresql.UUID(as_uuid=True)),
        sa.Column("label", sa.String(120), nullable=False),
        sa.Column("consent_source", sa.String(64), nullable=False),
        sa.Column(
            "covers_video_uploads",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "covers_cv_processing",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column(
            "commercial_ml_training_allowed",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "minors_authorized",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "athlete_pii_authorized",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("evidence_uri", sa.String(1024)),
        sa.Column("evidence_sha256", sa.String(64)),
        sa.Column(
            "effective_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("notes", sa.String(2000)),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
            name="fk_team_privacy_consents_team_id_teams",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_team_privacy_consents_recorded_by_users",
        ),
    )
    op.create_index(
        "ix_team_privacy_consents_team",
        "team_privacy_consents",
        ["team_id"],
    )
    op.create_index(
        "ix_team_privacy_consents_team_active",
        "team_privacy_consents",
        ["team_id", "revoked_at", "expires_at"],
    )

    op.add_column(
        "videos",
        sa.Column("privacy_consent_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "videos",
        sa.Column("raw_retention_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "videos",
        sa.Column("raw_deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "videos",
        sa.Column("raw_delete_reason", sa.String(64), nullable=True),
    )
    op.create_foreign_key(
        "fk_videos_privacy_consent_id_team_privacy_consents",
        "videos",
        "team_privacy_consents",
        ["privacy_consent_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_videos_raw_retention",
        "videos",
        ["raw_retention_expires_at", "raw_deleted_at"],
    )

    op.execute("ALTER TABLE team_privacy_consents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE team_privacy_consents FORCE ROW LEVEL SECURITY")
    op.execute(_team_id_select_policy("team_privacy_consents"))
    for sql in _team_id_write_policies("team_privacy_consents"):
        op.execute(sql)

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        role_exists = bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"),
            {"role": APP_ROLE},
        ).scalar()
        if role_exists:
            op.execute(
                f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE team_privacy_consents TO {APP_ROLE}"
            )


def downgrade() -> None:
    for suffix in (
        "select_access",
        "insert_context",
        "update_context",
        "delete_context",
    ):
        op.execute(f"DROP POLICY IF EXISTS team_privacy_consents_{suffix} ON team_privacy_consents")
    op.execute("ALTER TABLE team_privacy_consents NO FORCE ROW LEVEL SECURITY")

    op.drop_index("ix_videos_raw_retention", table_name="videos")
    op.drop_constraint(
        "fk_videos_privacy_consent_id_team_privacy_consents",
        "videos",
        type_="foreignkey",
    )
    op.drop_column("videos", "raw_delete_reason")
    op.drop_column("videos", "raw_deleted_at")
    op.drop_column("videos", "raw_retention_expires_at")
    op.drop_column("videos", "privacy_consent_id")

    op.drop_index("ix_team_privacy_consents_team_active", table_name="team_privacy_consents")
    op.drop_index("ix_team_privacy_consents_team", table_name="team_privacy_consents")
    op.drop_table("team_privacy_consents")
