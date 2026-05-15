from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    desc,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from nextballup_core.enums import (
    ModelArtifactStatus,
    ProcessingJobStage,
    ReviewStatus,
    VideoEventType,
)
from nextballup_db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class CVModelArtifact(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Registered CV artifact with provenance and commercial-use attestation."""

    __tablename__ = "cv_model_artifacts"

    stage: Mapped[ProcessingJobStage] = mapped_column(
        Enum(
            ProcessingJobStage,
            name="processing_job_stage",
            values_callable=lambda obj: [e.value for e in obj],
            create_type=False,
        ),
        nullable=False,
    )
    status: Mapped[ModelArtifactStatus] = mapped_column(
        Enum(
            ModelArtifactStatus,
            name="model_artifact_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=ModelArtifactStatus.CANDIDATE,
    )
    artifact_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    artifact_sha256: Mapped[str | None] = mapped_column(String(64))
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    dataset_version_ref: Mapped[str | None] = mapped_column(String(255))
    config_hash: Mapped[str | None] = mapped_column(String(64))
    license: Mapped[str] = mapped_column(String(255), nullable=False)
    commercial_use_allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Minimum plan tier (see `Plan.tier`) entitled to use this artifact.
    # `0` keeps the artifact selectable by every plan, including `free`.
    # Higher values restrict the artifact to paid plans without requiring a
    # second registration row per tier.
    min_plan_tier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    registered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(String(2000))

    __table_args__ = (
        Index("ix_cv_model_artifacts_stage_status", "stage", "status"),
        Index("ix_cv_model_artifacts_stage_status_created", "stage", "status", desc("created_at")),
        Index("ix_cv_model_artifacts_stage_tier", "stage", "min_plan_tier"),
        UniqueConstraint("stage", "model_version", name="uq_cv_model_artifacts_stage_version"),
    )


class VideoFrameClock(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Frame-to-timestamp mapping used to keep CV outputs frame-accurate."""

    __tablename__ = "video_frame_clocks"

    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    output_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    source_pts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    output_pts_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_time_base: Mapped[str] = mapped_column(String(64), nullable=False)
    output_time_base: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["video_id", "team_id"],
            ["videos.id", "videos.team_id"],
            ondelete="CASCADE",
            name="fk_video_frame_clocks_video_team_videos",
        ),
        UniqueConstraint("video_id", "output_frame", name="uq_video_frame_clocks_output_frame"),
        Index("ix_video_frame_clocks_team_video", "team_id", "video_id"),
    )


class VideoObjectDetection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Object detection row for player/ball/rim outputs."""

    __tablename__ = "video_object_detections"

    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cv_model_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    output_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    class_label: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_x: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_y: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_width: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_height: Mapped[float] = mapped_column(Float, nullable=False)
    track_key: Mapped[str | None] = mapped_column(String(128))

    __table_args__ = (
        ForeignKeyConstraint(
            ["video_id", "team_id"],
            ["videos.id", "videos.team_id"],
            ondelete="CASCADE",
            name="fk_video_object_detections_video_team_videos",
        ),
        Index("ix_video_object_detections_team_video_frame", "team_id", "video_id", "output_frame"),
        Index("ix_video_object_detections_label", "class_label"),
    )


class VideoTrack(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tracked object identity over time."""

    __tablename__ = "video_tracks"

    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cv_model_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    track_key: Mapped[str] = mapped_column(String(128), nullable=False)
    class_label: Mapped[str] = mapped_column(String(32), nullable=False)
    first_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    last_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        ForeignKeyConstraint(
            ["video_id", "team_id"],
            ["videos.id", "videos.team_id"],
            ondelete="CASCADE",
            name="fk_video_tracks_video_team_videos",
        ),
        UniqueConstraint("video_id", "track_key", name="uq_video_tracks_video_track_key"),
        Index("ix_video_tracks_team_video", "team_id", "video_id"),
    )


class VideoEvent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Shot/rebound/pass event with review state and shot-clock optionality."""

    __tablename__ = "video_events"

    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cv_model_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_type: Mapped[VideoEventType] = mapped_column(
        Enum(
            VideoEventType,
            name="video_event_type",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    event_time_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    clip_start_time_ms: Mapped[int | None] = mapped_column(BigInteger)
    clip_end_time_ms: Mapped[int | None] = mapped_column(BigInteger)
    output_frame: Mapped[int] = mapped_column(Integer, nullable=False)
    period: Mapped[int | None] = mapped_column(Integer)
    game_clock_ms: Mapped[int | None] = mapped_column(BigInteger)
    shot_clock_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    shot_clock_ms: Mapped[int | None] = mapped_column(BigInteger)
    primary_track_key: Mapped[str | None] = mapped_column(String(128))
    confidence: Mapped[float | None] = mapped_column(Float)
    review_status: Mapped[ReviewStatus] = mapped_column(
        Enum(
            ReviewStatus,
            name="review_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=ReviewStatus.NEEDS_REVIEW,
    )
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        ForeignKeyConstraint(
            ["video_id", "team_id"],
            ["videos.id", "videos.team_id"],
            ondelete="CASCADE",
            name="fk_video_events_video_team_videos",
        ),
        Index("ix_video_events_team_video_time", "team_id", "video_id", "event_time_ms"),
        Index(
            "ix_video_events_team_video_window",
            "team_id",
            "video_id",
            "clip_start_time_ms",
            "clip_end_time_ms",
        ),
        Index("ix_video_events_type_review", "event_type", "review_status"),
    )


class VideoMetric(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Aggregated video metric emitted by the metrics stage."""

    __tablename__ = "video_metrics"

    video_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    team_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("cv_model_artifacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    metric_name: Mapped[str] = mapped_column(String(128), nullable=False)
    metric_value: Mapped[float] = mapped_column(Float, nullable=False)
    period: Mapped[int | None] = mapped_column(Integer)
    metric_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        ForeignKeyConstraint(
            ["video_id", "team_id"],
            ["videos.id", "videos.team_id"],
            ondelete="CASCADE",
            name="fk_video_metrics_video_team_videos",
        ),
        UniqueConstraint(
            "video_id",
            "metric_name",
            "period",
            name="uq_video_metrics_video_name_period",
        ),
        Index("ix_video_metrics_team_video", "team_id", "video_id"),
    )
