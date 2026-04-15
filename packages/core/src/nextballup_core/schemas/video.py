from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nextballup_core.enums import (
    CameraHeight,
    CameraPosition,
    UploadMethod,
    VideoStatus,
)


class CreateUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    game_id: uuid.UUID
    filename: str = Field(min_length=1, max_length=512)
    file_size_bytes: int = Field(ge=1)
    content_type: str = Field(min_length=1, max_length=128)
    camera_position: CameraPosition | None = None
    camera_height: CameraHeight | None = None

    @field_validator("content_type")
    @classmethod
    def _normalize_content_type(cls, value: str) -> str:
        return value.strip().lower()


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


class CompleteUploadResponse(BaseModel):
    id: uuid.UUID
    status: VideoStatus
    estimated_processing_minutes: int
    job_id: uuid.UUID


class ProcessingStageStatus(BaseModel):
    status: str
    progress_percent: int | None = None
    completed_at: datetime | None = None
    error_message: str | None = None


class VideoDetailResponse(BaseModel):
    id: uuid.UUID
    game_id: uuid.UUID
    status: VideoStatus
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
    thumbnail_url: str | None = None
    playback_url: str | None = None
    playback_token: str | None = None
    playback_format: str | None = None
    token_expires_at: datetime | None = None
    processing: dict[str, str]
    created_at: datetime


class VideoStatusResponse(BaseModel):
    status: VideoStatus
    stage: str | None
    progress_percent: int
    stages: dict[str, ProcessingStageStatus]
