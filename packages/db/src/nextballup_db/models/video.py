from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nextballup_core.enums import (
    CameraHeight,
    CameraPosition,
    ProcessingJobStage,
    ProcessingJobStatus,
    VideoStatus,
)
from nextballup_db.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from nextballup_db.models.game import Game


class Video(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Video metadata + storage handles. `team_id` is denormalized so RLS
    policies can match on the active team context without joining `games`."""

    __tablename__ = "videos"

    game_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("games.id", ondelete="CASCADE"),
        nullable=False,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    privacy_consent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("team_privacy_consents.id", ondelete="SET NULL"),
        nullable=True,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    storage_key_raw: Mapped[str | None] = mapped_column(String(1024))
    storage_key_mezzanine: Mapped[str | None] = mapped_column(String(1024))
    storage_key_hls: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[VideoStatus] = mapped_column(
        Enum(
            VideoStatus,
            name="video_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=VideoStatus.PENDING_UPLOAD,
    )
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    content_type: Mapped[str | None] = mapped_column(String(128))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    fps: Mapped[float | None] = mapped_column(Float)
    codec: Mapped[str | None] = mapped_column(String(50))
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    storage_etag: Mapped[str | None] = mapped_column(String(128))
    storage_output_sha256: Mapped[str | None] = mapped_column(String(64))
    camera_position: Mapped[CameraPosition | None] = mapped_column(
        Enum(
            CameraPosition,
            name="camera_position",
            values_callable=lambda obj: [e.value for e in obj],
        )
    )
    camera_height: Mapped[CameraHeight | None] = mapped_column(
        Enum(
            CameraHeight,
            name="camera_height",
            values_callable=lambda obj: [e.value for e in obj],
        )
    )
    thumbnail_url: Mapped[str | None] = mapped_column(String(1024))
    upload_id: Mapped[str | None] = mapped_column(String(255))
    upload_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_delete_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_delete_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_storage_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_delete_reason: Mapped[str | None] = mapped_column(String(64))

    game: Mapped[Game] = relationship(
        back_populates="videos",
        lazy="selectin",
        foreign_keys=[game_id],
    )
    processing_jobs: Mapped[list[ProcessingJob]] = relationship(
        back_populates="video",
        lazy="selectin",
        cascade="all, delete-orphan",
        foreign_keys="ProcessingJob.video_id",
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "team_id"],
            ["games.id", "games.team_id"],
            ondelete="CASCADE",
            name="fk_videos_game_id_team_games",
        ),
        UniqueConstraint("id", "team_id", name="uq_videos_id_team_id"),
        Index("ix_videos_game", "game_id"),
        Index("ix_videos_team", "team_id"),
        Index("ix_videos_status", "status"),
        Index("ix_videos_raw_retention", "raw_retention_expires_at", "raw_deleted_at"),
        Index(
            "ix_videos_raw_delete_retry",
            "raw_delete_requested_at",
            "raw_storage_deleted_at",
        ),
    )


class ProcessingJob(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Per-stage worker tracking row. Phase 3 only inserts placeholder
    `transcode` jobs at completion time; the worker package will drive state
    transitions in subsequent phases."""

    __tablename__ = "processing_jobs"

    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("videos.id", ondelete="CASCADE"),
        nullable=False,
    )
    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
    )
    stage: Mapped[ProcessingJobStage] = mapped_column(
        Enum(
            ProcessingJobStage,
            name="processing_job_stage",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
    )
    status: Mapped[ProcessingJobStatus] = mapped_column(
        Enum(
            ProcessingJobStatus,
            name="processing_job_status",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        nullable=False,
        default=ProcessingJobStatus.PENDING,
    )
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None] = mapped_column(String(2000))
    result_metadata: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    video: Mapped[Video] = relationship(
        back_populates="processing_jobs",
        lazy="selectin",
        foreign_keys=[video_id],
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["video_id", "team_id"],
            ["videos.id", "videos.team_id"],
            ondelete="CASCADE",
            name="fk_processing_jobs_video_id_team_videos",
        ),
        UniqueConstraint("video_id", "stage", name="uq_processing_jobs_video_stage"),
        Index("ix_processing_jobs_video_stage", "video_id", "stage"),
        Index("ix_processing_jobs_team", "team_id"),
        Index("ix_processing_jobs_status_created", "status", "created_at"),
    )
