"""Add games, videos, and processing_jobs tables.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-15

Phase 3 migration. Adds:
  * games — team-scoped match records (parent of videos)
  * videos — upload/playback metadata, denormalizes team_id for RLS
  * processing_jobs — per-stage worker tracking, denormalizes team_id for RLS
  * FORCE ROW LEVEL SECURITY policies consistent with 0003 — team-scoped
    select via active team_id GUC OR self-membership lookup via user_id GUC;
    insert/update/delete gated by current_team_id only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TEAM_UUID = "NULLIF(current_setting('app.current_team_id', true), '')::uuid"
USER_UUID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


GAME_TYPE = postgresql.ENUM(
    "scrimmage",
    "preseason",
    "regular_season",
    "tournament",
    "playoff",
    "practice",
    "film_exchange",
    name="game_type",
)
GAME_STATUS = postgresql.ENUM(
    "scheduled", "uploading", "processing", "completed", "failed", name="game_status"
)
CAMERA_POSITION = postgresql.ENUM(
    "sideline",
    "baseline",
    "elevated_corner",
    "broadcast",
    "other",
    name="camera_position",
)
CAMERA_HEIGHT = postgresql.ENUM("floor", "elevated", "overhead", name="camera_height")
VIDEO_STATUS = postgresql.ENUM(
    "pending_upload",
    "uploading",
    "uploaded",
    "transcoding",
    "queued",
    "processing",
    "processed",
    "failed",
    name="video_status",
)
PROCESSING_JOB_STAGE = postgresql.ENUM(
    "transcode",
    "detection",
    "tracking",
    "court_mapping",
    "events",
    "metrics",
    name="processing_job_stage",
)
PROCESSING_JOB_STATUS = postgresql.ENUM(
    "pending", "running", "completed", "failed", name="processing_job_status"
)


_NEW_TENANT_TABLES = ("games", "videos", "processing_jobs")


def _team_id_select_policy(table: str) -> str:
    """SELECT policy that admits when the active team context matches OR when
    the authenticated user is an active member of the row's team."""
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
    insert_sql = f"""
        CREATE POLICY {table}_insert_context ON {table}
            FOR INSERT
            WITH CHECK (team_id = {TEAM_UUID});
    """
    update_sql = f"""
        CREATE POLICY {table}_update_context ON {table}
            FOR UPDATE
            USING (team_id = {TEAM_UUID})
            WITH CHECK (team_id = {TEAM_UUID});
    """
    delete_sql = f"""
        CREATE POLICY {table}_delete_context ON {table}
            FOR DELETE
            USING (team_id = {TEAM_UUID});
    """
    return insert_sql, update_sql, delete_sql


def upgrade() -> None:
    bind = op.get_bind()
    for enum_type in (
        GAME_TYPE,
        GAME_STATUS,
        CAMERA_POSITION,
        CAMERA_HEIGHT,
        VIDEO_STATUS,
        PROCESSING_JOB_STAGE,
        PROCESSING_JOB_STATUS,
    ):
        enum_type.create(bind, checkfirst=True)

    # ---- games ----
    op.create_table(
        "games",
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
        sa.Column("opponent_name", sa.String(255)),
        sa.Column(
            "game_type",
            postgresql.ENUM(name="game_type", create_type=False),
            nullable=False,
        ),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("time", sa.Time),
        sa.Column("location", sa.String(255)),
        sa.Column("is_home", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column(
            "status",
            postgresql.ENUM(name="game_status", create_type=False),
            nullable=False,
            server_default=sa.text("'scheduled'"),
        ),
        sa.Column("periods", sa.Integer, nullable=False, server_default=sa.text("4")),
        sa.Column(
            "period_length_minutes",
            sa.Integer,
            nullable=False,
            server_default=sa.text("8"),
        ),
        sa.Column("score_team", sa.Integer),
        sa.Column("score_opponent", sa.Integer),
        sa.Column("notes", sa.String(2000)),
        sa.Column("processing_metadata", postgresql.JSONB),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], ondelete="CASCADE", name="fk_games_team_id_teams"
        ),
    )
    op.create_index("ix_games_team_date", "games", ["team_id", "date"])
    op.create_index("ix_games_status", "games", ["status"])

    # ---- videos ----
    op.create_table(
        "videos",
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
        sa.Column("game_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("uploaded_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("storage_key_raw", sa.String(1024)),
        sa.Column("storage_key_mezzanine", sa.String(1024)),
        sa.Column("storage_key_hls", sa.String(1024)),
        sa.Column(
            "status",
            postgresql.ENUM(name="video_status", create_type=False),
            nullable=False,
            server_default=sa.text("'pending_upload'"),
        ),
        sa.Column("file_size_bytes", sa.BigInteger),
        sa.Column("content_type", sa.String(128)),
        sa.Column("duration_seconds", sa.Float),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("fps", sa.Float),
        sa.Column("codec", sa.String(50)),
        sa.Column("checksum_sha256", sa.String(64)),
        sa.Column("camera_position", postgresql.ENUM(name="camera_position", create_type=False)),
        sa.Column("camera_height", postgresql.ENUM(name="camera_height", create_type=False)),
        sa.Column("thumbnail_url", sa.String(1024)),
        sa.Column("upload_id", sa.String(255)),
        sa.Column("upload_expires_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["game_id"], ["games.id"], ondelete="CASCADE", name="fk_videos_game_id_games"
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], ondelete="CASCADE", name="fk_videos_team_id_teams"
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_videos_uploaded_by_users",
        ),
    )
    op.create_index("ix_videos_game", "videos", ["game_id"])
    op.create_index("ix_videos_team", "videos", ["team_id"])
    op.create_index("ix_videos_status", "videos", ["status"])

    # ---- processing_jobs ----
    op.create_table(
        "processing_jobs",
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
        sa.Column("video_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("team_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "stage",
            postgresql.ENUM(name="processing_job_stage", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="processing_job_status", create_type=False),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "progress_percent",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("celery_task_id", sa.String(255)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.String(2000)),
        sa.Column("result_metadata", postgresql.JSONB),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(
            ["video_id"],
            ["videos.id"],
            ondelete="CASCADE",
            name="fk_processing_jobs_video_id_videos",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"],
            ["teams.id"],
            ondelete="CASCADE",
            name="fk_processing_jobs_team_id_teams",
        ),
    )
    op.create_index("ix_processing_jobs_video_stage", "processing_jobs", ["video_id", "stage"])
    op.create_index("ix_processing_jobs_team", "processing_jobs", ["team_id"])
    op.create_index("ix_processing_jobs_status", "processing_jobs", ["status"])

    # ---- Forced RLS ----
    for table in _NEW_TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(_team_id_select_policy(table))
        for sql in _team_id_write_policies(table):
            op.execute(sql)


def downgrade() -> None:
    for table in _NEW_TENANT_TABLES:
        for suffix in (
            "select_access",
            "insert_context",
            "update_context",
            "delete_context",
        ):
            op.execute(f"DROP POLICY IF EXISTS {table}_{suffix} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    op.drop_index("ix_processing_jobs_status", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_team", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_video_stage", table_name="processing_jobs")
    op.drop_table("processing_jobs")

    op.drop_index("ix_videos_status", table_name="videos")
    op.drop_index("ix_videos_team", table_name="videos")
    op.drop_index("ix_videos_game", table_name="videos")
    op.drop_table("videos")

    op.drop_index("ix_games_status", table_name="games")
    op.drop_index("ix_games_team_date", table_name="games")
    op.drop_table("games")

    bind = op.get_bind()
    for enum_type in (
        PROCESSING_JOB_STATUS,
        PROCESSING_JOB_STAGE,
        VIDEO_STATUS,
        CAMERA_HEIGHT,
        CAMERA_POSITION,
        GAME_STATUS,
        GAME_TYPE,
    ):
        enum_type.drop(bind, checkfirst=True)
