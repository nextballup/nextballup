from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nextballup_core.enums import (
    CameraHeight,
    CameraPosition,
    ReviewStatus,
    UploadMethod,
    VideoEventType,
    VideoStatus,
)

DemoPreviewStatusValue = Literal["idle", "queued", "running", "completed", "failed"]
PlaybackStatusValue = Literal[
    "uploading",
    "queued",
    "transcoding",
    "ready_for_playback",
    "analysis_pending",
    "analysis_running",
    "failed",
]


def _normalize_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(c not in "0123456789abcdef" for c in normalized):
        raise ValueError("checksum_sha256 must be a 64-character lowercase hex SHA-256 digest")
    return normalized


class CreateUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: uuid.UUID
    filename: str = Field(min_length=1, max_length=512)
    file_size_bytes: int = Field(ge=1)
    content_type: str = Field(min_length=1, max_length=128)
    checksum_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    camera_position: CameraPosition | None = None
    camera_height: CameraHeight | None = None
    privacy_consent_id: uuid.UUID | None = None

    @field_validator("content_type")
    @classmethod
    def _normalize_content_type(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("checksum_sha256")
    @classmethod
    def _validate_checksum(cls, value: str | None) -> str | None:
        return _normalize_sha256(value)


class PresignedPart(BaseModel):
    part_number: int
    url: str


class CreateUploadResponse(BaseModel):
    """Response for POST /videos/upload.

    Single PUT and multipart shapes are unified here — clients must look at
    `upload_method` to decide which fields are populated.
    """

    id: uuid.UUID
    upload_method: UploadMethod
    upload_url: str | None = None
    upload_headers: dict[str, str] | None = None
    upload_id: str | None = None
    part_size_bytes: int | None = None
    part_urls: list[PresignedPart] | None = None
    expires_at: datetime


class CompletedPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_number: int = Field(ge=1, le=10_000)
    etag: str = Field(min_length=1, max_length=255)


class CompleteUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    checksum_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    parts: list[CompletedPart] | None = None

    @field_validator("checksum_sha256")
    @classmethod
    def _validate_checksum(cls, value: str | None) -> str | None:
        return _normalize_sha256(value)


class CompleteUploadResponse(BaseModel):
    id: uuid.UUID
    status: VideoStatus
    estimated_processing_minutes: int
    job_id: uuid.UUID


class ProcessingStageStatus(BaseModel):
    status: str
    progress_percent: int | None = None
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


class VideoDetailResponse(BaseModel):
    id: uuid.UUID
    game_id: uuid.UUID
    status: VideoStatus
    playback_status: PlaybackStatusValue
    filename: str
    file_size_bytes: int | None = None
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    codec: str | None = None
    camera_position: CameraPosition | None = None
    camera_height: CameraHeight | None = None
    checksum_sha256: str | None = None
    storage_etag: str | None = None
    storage_output_sha256: str | None = None
    privacy_consent_id: uuid.UUID | None = None
    raw_retention_expires_at: datetime | None = None
    raw_deleted_at: datetime | None = None
    thumbnail_url: str | None = None
    playback_url: str | None = None
    playback_token: str | None = None
    playback_format: str | None = None
    token_expires_at: datetime | None = None
    demo_preview_enabled: bool = False
    demo_preview_status: DemoPreviewStatusValue = "idle"
    demo_preview_url: str | None = None
    demo_preview_generated_at: datetime | None = None
    demo_preview_error_message: str | None = None
    processing: dict[str, str]
    created_at: datetime


class VideoStatusResponse(BaseModel):
    status: VideoStatus
    playback_status: PlaybackStatusValue
    stage: str | None
    progress_percent: int
    stages: dict[str, ProcessingStageStatus]


class VideoListItem(BaseModel):
    """Lightweight row in `GET /games/{id}/videos`.

    The summary deliberately omits signed playback URLs: those require a full
    auth+storage round-trip and should only be issued on demand from
    `GET /videos/{id}`.
    """

    id: uuid.UUID
    filename: str
    status: VideoStatus
    playback_status: PlaybackStatusValue
    file_size_bytes: int | None = None
    duration_seconds: float | None = None
    camera_position: CameraPosition | None = None
    camera_height: CameraHeight | None = None
    created_at: datetime


class VideoListResponse(BaseModel):
    videos: list[VideoListItem]
    total: int


class VideoEventSummary(BaseModel):
    id: uuid.UUID
    event_type: VideoEventType
    event_time_ms: int
    output_frame: int
    period: int | None = None
    game_clock_ms: int | None = None
    shot_clock_enabled: bool
    shot_clock_ms: int | None = None
    primary_track_key: str | None = None
    confidence: float | None = None
    review_status: ReviewStatus
    created_at: datetime


class VideoEventsResponse(BaseModel):
    video_id: uuid.UUID
    shot_clock_enabled: bool
    shot_clock_seconds: int | None = None
    events: list[VideoEventSummary]
    total: int


class CreateVideoEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: VideoEventType
    event_time_ms: int = Field(ge=0)


class UpdateVideoEventReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_status: ReviewStatus


class VideoClipProposalSummary(BaseModel):
    id: str
    source_event_id: uuid.UUID
    event_type: VideoEventType
    label: str
    reason: str
    start_time_ms: int
    end_time_ms: int
    review_status: ReviewStatus
    created_at: datetime


class VideoClipProposalsResponse(BaseModel):
    video_id: uuid.UUID
    proposals: list[VideoClipProposalSummary]
    total: int


class PlaybackVerifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=4096)


class PlaybackVerifyResponse(BaseModel):
    """Minimal proof the caller's playback token is still tied to a live
    session + membership. Clients treat a 200 as 'keep playing' and a 401 as
    'drop the stream and re-auth'."""

    video_id: uuid.UUID
    expires_at: datetime


class RequeueProcessingRequest(BaseModel):
    """Coach/admin request to reset a processing job back to PENDING so the
    beat dispatcher picks it back up. The `stage` argument is required so
    operators don't accidentally requeue every failed stage for a video —
    the recovery story for each stage is different."""

    model_config = ConfigDict(extra="forbid")

    stage: str = Field(min_length=1, max_length=64)


class CancelProcessingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = Field(min_length=1, max_length=64)


class RequeueProcessingResponse(BaseModel):
    job_id: uuid.UUID
    stage: str
    status: str
    requeued_at: datetime


class CancelProcessingResponse(BaseModel):
    job_id: uuid.UUID
    stage: str
    status: str
    cancelled_at: datetime


class GenerateDemoPreviewResponse(BaseModel):
    status: DemoPreviewStatusValue
    preview_url: str | None = None
    generated_at: datetime | None = None
