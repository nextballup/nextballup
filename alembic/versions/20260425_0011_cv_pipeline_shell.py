"""Add shot-clock-aware CV pipeline persistence.

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-25

The tables in this migration are intentionally data-first, not model-first:
they let the platform persist frame clocks, detections, tracks, events, and
metrics with artifact provenance before trained models exist. Basketball
levels that do not use a shot clock are represented explicitly via nullable
shot-clock timestamps and a per-game/per-event enabled flag.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "nextballup_app"
TEAM_UUID = "NULLIF(current_setting('app.current_team_id', true), '')::uuid"
USER_UUID = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"

MODEL_ARTIFACT_STATUS = postgresql.ENUM(
    "candidate",
    "active",
    "retired",
    "blocked",
    name="model_artifact_status",
)
VIDEO_EVENT_TYPE = postgresql.ENUM(
    "shot_attempt",
    "shot_made",
    "rebound",
    "pass",
    name="video_event_type",
)
REVIEW_STATUS = postgresql.ENUM(
    "machine_only",
    "needs_review",
    "approved",
    "rejected",
    name="review_status",
)

_TENANT_TABLES = (
    "video_frame_clocks",
    "video_object_detections",
    "video_tracks",
    "video_events",
    "video_metrics",
)


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
    bind = op.get_bind()
    MODEL_ARTIFACT_STATUS.create(bind, checkfirst=True)
    VIDEO_EVENT_TYPE.create(bind, checkfirst=True)
    REVIEW_STATUS.create(bind, checkfirst=True)

    op.add_column(
        "games",
        sa.Column(
            "shot_clock_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column("games", sa.Column("shot_clock_seconds", sa.Integer(), nullable=True))
    op.alter_column("games", "shot_clock_enabled", server_default=None)

    op.create_table(
        "cv_model_artifacts",
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
        sa.Column(
            "stage", postgresql.ENUM(name="processing_job_stage", create_type=False), nullable=False
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="model_artifact_status", create_type=False),
            nullable=False,
        ),
        sa.Column("artifact_uri", sa.String(1024), nullable=False),
        sa.Column("artifact_sha256", sa.String(64)),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("dataset_version_ref", sa.String(255)),
        sa.Column("config_hash", sa.String(64)),
        sa.Column("license", sa.String(255), nullable=False),
        sa.Column(
            "commercial_use_allowed", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("registered_by", postgresql.UUID(as_uuid=True)),
        sa.Column("notes", sa.String(2000)),
        sa.ForeignKeyConstraint(
            ["registered_by"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_cv_model_artifacts_registered_by_users",
        ),
        sa.UniqueConstraint("stage", "model_version", name="uq_cv_model_artifacts_stage_version"),
    )
    op.create_index(
        "ix_cv_model_artifacts_stage_status",
        "cv_model_artifacts",
        ["stage", "status"],
    )

    op.create_table(
        "video_frame_clocks",
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
        sa.Column("source_frame", sa.Integer(), nullable=False),
        sa.Column("output_frame", sa.Integer(), nullable=False),
        sa.Column("source_pts_ms", sa.BigInteger(), nullable=False),
        sa.Column("output_pts_ms", sa.BigInteger(), nullable=False),
        sa.Column("source_time_base", sa.String(64), nullable=False),
        sa.Column("output_time_base", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["video_id", "team_id"],
            ["videos.id", "videos.team_id"],
            ondelete="CASCADE",
            name="fk_video_frame_clocks_video_team_videos",
        ),
        sa.UniqueConstraint("video_id", "output_frame", name="uq_video_frame_clocks_output_frame"),
    )
    op.create_index(
        "ix_video_frame_clocks_team_video", "video_frame_clocks", ["team_id", "video_id"]
    )

    op.create_table(
        "video_object_detections",
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
        sa.Column("model_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("output_frame", sa.Integer(), nullable=False),
        sa.Column("timestamp_ms", sa.BigInteger(), nullable=False),
        sa.Column("class_label", sa.String(32), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("bbox_x", sa.Float(), nullable=False),
        sa.Column("bbox_y", sa.Float(), nullable=False),
        sa.Column("bbox_width", sa.Float(), nullable=False),
        sa.Column("bbox_height", sa.Float(), nullable=False),
        sa.Column("track_key", sa.String(128)),
        sa.ForeignKeyConstraint(
            ["model_artifact_id"], ["cv_model_artifacts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["video_id", "team_id"],
            ["videos.id", "videos.team_id"],
            ondelete="CASCADE",
            name="fk_video_object_detections_video_team_videos",
        ),
    )
    op.create_index(
        "ix_video_object_detections_team_video_frame",
        "video_object_detections",
        ["team_id", "video_id", "output_frame"],
    )
    op.create_index("ix_video_object_detections_label", "video_object_detections", ["class_label"])

    op.create_table(
        "video_tracks",
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
        sa.Column("model_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("track_key", sa.String(128), nullable=False),
        sa.Column("class_label", sa.String(32), nullable=False),
        sa.Column("first_frame", sa.Integer(), nullable=False),
        sa.Column("last_frame", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.ForeignKeyConstraint(
            ["model_artifact_id"], ["cv_model_artifacts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["video_id", "team_id"],
            ["videos.id", "videos.team_id"],
            ondelete="CASCADE",
            name="fk_video_tracks_video_team_videos",
        ),
        sa.UniqueConstraint("video_id", "track_key", name="uq_video_tracks_video_track_key"),
    )
    op.create_index("ix_video_tracks_team_video", "video_tracks", ["team_id", "video_id"])

    op.create_table(
        "video_events",
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
        sa.Column("model_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "event_type",
            postgresql.ENUM(name="video_event_type", create_type=False),
            nullable=False,
        ),
        sa.Column("event_time_ms", sa.BigInteger(), nullable=False),
        sa.Column("output_frame", sa.Integer(), nullable=False),
        sa.Column("period", sa.Integer()),
        sa.Column("game_clock_ms", sa.BigInteger()),
        sa.Column(
            "shot_clock_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("shot_clock_ms", sa.BigInteger()),
        sa.Column("primary_track_key", sa.String(128)),
        sa.Column("confidence", sa.Float()),
        sa.Column(
            "review_status",
            postgresql.ENUM(name="review_status", create_type=False),
            nullable=False,
        ),
        sa.Column("event_metadata", postgresql.JSONB),
        sa.ForeignKeyConstraint(
            ["model_artifact_id"], ["cv_model_artifacts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["video_id", "team_id"],
            ["videos.id", "videos.team_id"],
            ondelete="CASCADE",
            name="fk_video_events_video_team_videos",
        ),
    )
    op.create_index(
        "ix_video_events_team_video_time", "video_events", ["team_id", "video_id", "event_time_ms"]
    )
    op.create_index("ix_video_events_type_review", "video_events", ["event_type", "review_status"])

    op.create_table(
        "video_metrics",
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
        sa.Column("model_artifact_id", postgresql.UUID(as_uuid=True)),
        sa.Column("metric_name", sa.String(128), nullable=False),
        sa.Column("metric_value", sa.Float(), nullable=False),
        sa.Column("period", sa.Integer()),
        sa.Column("metric_metadata", postgresql.JSONB),
        sa.ForeignKeyConstraint(
            ["model_artifact_id"], ["cv_model_artifacts.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["video_id", "team_id"],
            ["videos.id", "videos.team_id"],
            ondelete="CASCADE",
            name="fk_video_metrics_video_team_videos",
        ),
        sa.UniqueConstraint(
            "video_id", "metric_name", "period", name="uq_video_metrics_video_name_period"
        ),
    )
    op.create_index("ix_video_metrics_team_video", "video_metrics", ["team_id", "video_id"])

    for table in _TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(_team_id_select_policy(table))
        for sql in _team_id_write_policies(table):
            op.execute(sql)

    for table in ("cv_model_artifacts", *_TENANT_TABLES):
        _grant_runtime(table)


def downgrade() -> None:
    for table in _TENANT_TABLES:
        for suffix in ("select_access", "insert_context", "update_context", "delete_context"):
            op.execute(f"DROP POLICY IF EXISTS {table}_{suffix} ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")

    op.drop_index("ix_video_metrics_team_video", table_name="video_metrics")
    op.drop_table("video_metrics")
    op.drop_index("ix_video_events_type_review", table_name="video_events")
    op.drop_index("ix_video_events_team_video_time", table_name="video_events")
    op.drop_table("video_events")
    op.drop_index("ix_video_tracks_team_video", table_name="video_tracks")
    op.drop_table("video_tracks")
    op.drop_index("ix_video_object_detections_label", table_name="video_object_detections")
    op.drop_index(
        "ix_video_object_detections_team_video_frame", table_name="video_object_detections"
    )
    op.drop_table("video_object_detections")
    op.drop_index("ix_video_frame_clocks_team_video", table_name="video_frame_clocks")
    op.drop_table("video_frame_clocks")
    op.drop_index("ix_cv_model_artifacts_stage_status", table_name="cv_model_artifacts")
    op.drop_table("cv_model_artifacts")

    op.drop_column("games", "shot_clock_seconds")
    op.drop_column("games", "shot_clock_enabled")

    bind = op.get_bind()
    for enum_type in (REVIEW_STATUS, VIDEO_EVENT_TYPE, MODEL_ARTIFACT_STATUS):
        enum_type.drop(bind, checkfirst=True)
