from __future__ import annotations

import base64
import binascii
import csv
import io
import json
import logging
import uuid
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_api.audit import write_audit
from nextballup_api.billing import (
    check_video_storage_quota,
    check_video_upload_quota,
    quota_exceeded_error,
    record_usage,
    release_video_upload_quota_reservation,
    resolve_team_plan,
)
from nextballup_api.demo_preview import (
    queue_demo_preview_request,
    resolve_demo_preview,
    resolve_demo_preview_state,
)
from nextballup_api.deps import get_app_settings, get_current_user, get_db
from nextballup_api.permissions import (
    require_team_coach,
    require_team_member,
    require_verified_account,
)
from nextballup_api.security.jwt import create_playback_token, decode_token
from nextballup_api.security.rate_limit import enforce_rate_limit
from nextballup_api.storage import (
    PresignedUpload,
    StorageFailureError,
    StoragePresigner,
    get_storage_presigner,
    storage_abort_multipart,
    storage_complete_multipart,
    storage_delete_object,
    storage_head_object,
    storage_key_for_video,
    storage_presign_get,
    storage_presign_upload,
)
from nextballup_api.tenant import (
    bind_authenticated_context,
    clear_join_invite_context,
    clear_tenant_context,
    set_tenant_context,
)
from nextballup_api.video_playback_status import derive_playback_status
from nextballup_clips import ClipEvent, build_clip_proposals
from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import (
    InstitutionType,
    ProcessingJobStage,
    ProcessingJobStatus,
    ReviewStatus,
    TeamLevel,
    UserRole,
    VideoEventType,
    VideoStatus,
)
from nextballup_core.errors import (
    AppError,
    AuthenticationError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from nextballup_core.schemas.video import (
    CancelProcessingRequest,
    CancelProcessingResponse,
    CompleteUploadRequest,
    CompleteUploadResponse,
    CreateUploadRequest,
    CreateUploadResponse,
    CreateVideoEventRequest,
    GenerateDemoPreviewResponse,
    PlaybackVerifyRequest,
    PlaybackVerifyResponse,
    PresignedPart,
    ProcessingStageStatus,
    RequeueProcessingRequest,
    RequeueProcessingResponse,
    UpdateVideoEventReviewRequest,
    VideoClipProposalsResponse,
    VideoClipProposalSummary,
    VideoDetailResponse,
    VideoEventSourceValue,
    VideoEventsResponse,
    VideoEventsSummaryCounts,
    VideoEventSummary,
    VideoStatusResponse,
)
from nextballup_core.settings import Settings
from nextballup_db.models.cv import VideoEvent
from nextballup_db.models.game import Game
from nextballup_db.models.team import Team, TeamPrivacyConsent
from nextballup_db.models.user import User
from nextballup_db.models.video import ProcessingJob, Video

router = APIRouter(prefix="/videos", tags=["videos"])
logger = logging.getLogger(__name__)

# The first concrete worker stage materializes a browser-safe MP4 mezzanine;
# downstream CV stages exist in the schema for forward compatibility.
_PIPELINE_STAGES: tuple[ProcessingJobStage, ...] = (
    ProcessingJobStage.TRANSCODE,
    ProcessingJobStage.DETECTION,
    ProcessingJobStage.TRACKING,
    ProcessingJobStage.COURT_MAPPING,
    ProcessingJobStage.EVENTS,
    ProcessingJobStage.METRICS,
)
_ESTIMATED_PROCESSING_MINUTES = 45  # Surfaced as guidance, per API_SPEC.
_SENSITIVE_TEAM_LEVELS: frozenset[TeamLevel] = frozenset(
    {
        TeamLevel.YOUTH,
        TeamLevel.AAU_CLUB,
        TeamLevel.MIDDLE_SCHOOL,
        TeamLevel.HIGH_SCHOOL,
    }
)


@dataclass(frozen=True)
class _PlaybackArtifact:
    """Resolved playback URL + matching token for a single GET response."""

    url: str
    token: str
    fmt: str
    expires_at: datetime


@dataclass(frozen=True)
class _DemoPreviewView:
    status: str
    url: str | None
    generated_at: datetime | None
    error_message: str | None


@dataclass(frozen=True)
class _VideoStorageCleanupResult:
    aborted_multipart: bool = False
    deleted_raw: bool = False
    deleted_mezzanine: bool = False
    deleted_hls: bool = False
    deleted_demo_preview: bool = False
    failures: tuple[str, ...] = ()


def _playback_content_type(fmt: str) -> str | None:
    if fmt == "hls":
        return "application/vnd.apple.mpegurl"
    if fmt == "mp4":
        return "video/mp4"
    return None


def _playback_candidates(video: Video) -> tuple[tuple[str, str], ...]:
    candidates: list[tuple[str, str]] = []
    if video.storage_key_hls:
        candidates.append((video.storage_key_hls, "hls"))
    if video.storage_key_mezzanine:
        candidates.append((video.storage_key_mezzanine, "mp4"))
    return tuple(candidates)


def _demo_preview_view(*, video: Video, settings: Settings) -> _DemoPreviewView:
    if video.demo_preview_status != "idle" or video.demo_preview_storage_key:
        return _DemoPreviewView(
            status=video.demo_preview_status,
            url=(
                f"/api/v1/videos/{video.id}/demo-preview/artifact"
                if video.demo_preview_storage_key
                else None
            ),
            generated_at=video.demo_preview_generated_at
            if video.demo_preview_storage_key
            else None,
            error_message=video.demo_preview_error_message,
        )

    artifact = resolve_demo_preview(settings=settings, video_id=video.id)
    state = resolve_demo_preview_state(settings=settings, video_id=video.id)
    return _DemoPreviewView(
        status=state.status,
        url=artifact.url_path if artifact else None,
        generated_at=artifact.generated_at if artifact else None,
        error_message=state.error_message,
    )


def _object_metadata_value(metadata: dict[str, object], key: str) -> str | None:
    object_metadata = metadata.get("Metadata")
    if not isinstance(object_metadata, dict):
        return None
    value = object_metadata.get(key)
    return value.lower() if isinstance(value, str) else None


def _raw_retention_expires_at(
    settings: Settings,
    *,
    plan_retention_days: int | None = None,
    now: datetime | None = None,
) -> datetime:
    retention_days = plan_retention_days or settings.raw_video_retention_days
    return (now or datetime.now(tz=UTC)) + timedelta(days=retention_days)


def _team_requires_privacy_consent(team: Team, settings: Settings) -> bool:
    if not settings.should_require_sensitive_upload_consent():
        return False
    return (
        team.level in _SENSITIVE_TEAM_LEVELS or team.institution_type == InstitutionType.K12_SCHOOL
    )


def _require_uploader_youth_consent(*, team: Team, user: User, settings: Settings) -> None:
    """Require minor-user consent fields without blocking adult coach uploads."""
    if not _team_requires_privacy_consent(team, settings):
        return
    if user.role in {UserRole.ADMIN, UserRole.COACH}:
        return
    if user.date_of_birth_verified and user.parental_consent_on_file:
        return
    missing: list[str] = []
    if not user.date_of_birth_verified:
        missing.append("date_of_birth_verified")
    if not user.parental_consent_on_file:
        missing.append("parental_consent_on_file")
    raise ForbiddenError(
        "Sensitive youth/K-12 uploads require uploader age verification and parental consent on file",
        code=ErrorCode.PRIVACY_CONSENT_REQUIRED,
        details={
            "reason": "uploader_minor_consent_required",
            "missing": missing,
        },
    )


def _consent_is_current(consent: TeamPrivacyConsent, *, now: datetime) -> bool:
    return (
        consent.revoked_at is None
        and consent.effective_at <= now
        and (consent.expires_at is None or consent.expires_at > now)
    )


async def _resolve_upload_privacy_consent(
    *,
    session: AsyncSession,
    team: Team,
    consent_id: uuid.UUID | None,
    settings: Settings,
) -> TeamPrivacyConsent | None:
    required = _team_requires_privacy_consent(team, settings)
    if consent_id is None:
        if required:
            raise ForbiddenError(
                "A current athlete/guardian privacy consent record is required for this upload",
                code=ErrorCode.PRIVACY_CONSENT_REQUIRED,
            )
        return None

    consent = await session.scalar(
        select(TeamPrivacyConsent).where(
            TeamPrivacyConsent.id == consent_id,
            TeamPrivacyConsent.team_id == team.id,
        )
    )
    now = datetime.now(tz=UTC)
    if consent is None or not _consent_is_current(consent, now=now):
        raise ForbiddenError(
            "Privacy consent record is not current for this team",
            code=ErrorCode.PRIVACY_CONSENT_INVALID,
        )
    if not (
        consent.covers_video_uploads
        and consent.covers_cv_processing
        and consent.athlete_pii_authorized
    ):
        raise ForbiddenError(
            "Privacy consent record does not cover video upload and CV processing",
            code=ErrorCode.PRIVACY_CONSENT_INVALID,
        )
    if required and not consent.minors_authorized:
        raise ForbiddenError(
            "Privacy consent record does not authorize minor athlete processing",
            code=ErrorCode.PRIVACY_CONSENT_INVALID,
        )
    return consent


async def _try_issue_playback(
    *,
    video: Video,
    user: User,
    storage: StoragePresigner | None,
    settings: Settings,
) -> _PlaybackArtifact | None:
    """Issue a presigned URL + scoped JWT only when the video is PROCESSED
    *and* we have an output key *and* storage is configured. Each precondition
    is its own bail-out so the API contract stays stable across deploys
    without storage."""
    if video.status is not VideoStatus.PROCESSED:
        return None
    candidates = _playback_candidates(video)
    if not candidates:
        return None
    if storage is None:
        return None
    # A playback URL that outlives its JWT weakens the whole point of the
    # session-aware verify flow. Cap the presigned URL to the shorter token TTL.
    url_ttl_seconds = min(
        settings.playback_url_expires_seconds,
        settings.playback_token_expire_seconds,
    )
    # Only hand out playback credentials for an artifact that still exists.
    # This keeps the API from issuing dead presigned URLs when a DB row points
    # at stale HLS/mezzanine keys, and lets us gracefully fall back from HLS to
    # mezzanine if the preferred manifest has been removed.
    for storage_key, fmt in candidates:
        metadata = await storage_head_object(storage, key=storage_key)
        if metadata is None:
            continue
        if _uploaded_object_size(metadata) == 0:
            continue
        if (
            fmt == "mp4"
            and video.storage_output_sha256
            and _object_metadata_value(metadata, "nbu-output-sha256")
            != video.storage_output_sha256.lower()
        ):
            continue
        url = await storage_presign_get(
            storage,
            key=storage_key,
            expires_in=url_ttl_seconds,
            response_content_type=_playback_content_type(fmt),
        )
        token, expires_at = create_playback_token(
            subject=user.id,
            role=user.role,
            session_version=user.session_version,
            video_id=video.id,
            team_id=video.team_id,
            settings=settings,
        )
        return _PlaybackArtifact(url=url, token=token, fmt=fmt, expires_at=expires_at)
    return None


def get_storage(
    settings: Settings = Depends(get_app_settings),
) -> StoragePresigner | None:
    """Dependency wrapper so tests can override storage with a fake."""
    return get_storage_presigner(settings)


def _require_storage(presigner: StoragePresigner | None) -> StoragePresigner:
    if presigner is None:
        raise ServiceUnavailableError(
            "Object storage is not configured",
            code=ErrorCode.STORAGE_NOT_CONFIGURED,
        )
    return presigner


def _filename_is_safe(filename: str) -> bool:
    """Reject filenames that could confuse loggers, URL parsers, or storage
    backends. These checks run *before* we trust the extension.

    We reject (rather than silently sanitize) at the API boundary so clients
    get a clear error: it's almost always a bug or an abuse probe when a
    filename contains a control char or a path segment. The downstream
    `_sanitize_filename_component` call in storage.py still hardens the
    storage key as belt-and-suspenders.
    """
    if not filename or filename.strip() != filename:
        return False
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in filename):
        return False
    # Path separators never belong in a filename — they're always either a
    # serializer bug on the client (posting a relative path) or a traversal
    # attempt. Same for `..` runs, which try to escape the containing dir.
    return not ("/" in filename or "\\" in filename or ".." in filename)


def _filename_extension_allowed(filename: str, content_type: str, settings: Settings) -> bool:
    lowered = filename.lower()
    dot = lowered.rfind(".")
    if dot == -1:
        return False
    ext = lowered[dot:]
    allowed = settings.upload_content_type_extensions.get(content_type, [])
    return ext in allowed


def _validate_upload(payload: CreateUploadRequest, settings: Settings) -> None:
    allowed = {ct.lower() for ct in settings.allowed_video_content_types}
    if payload.content_type not in allowed:
        raise ValidationFailedError(
            f"Content type '{payload.content_type}' is not supported",
            code=ErrorCode.INVALID_CONTENT_TYPE,
            details={"allowed": sorted(allowed)},
        )
    if not _filename_is_safe(payload.filename):
        raise ValidationFailedError(
            "Filename contains unsupported characters",
            code=ErrorCode.INVALID_FILENAME,
        )
    if not _filename_extension_allowed(payload.filename, payload.content_type, settings):
        raise ValidationFailedError(
            "Filename extension does not match the declared content type",
            code=ErrorCode.CONTENT_TYPE_EXTENSION_MISMATCH,
            details={
                "content_type": payload.content_type,
                "allowed_extensions": sorted(
                    settings.upload_content_type_extensions.get(payload.content_type, [])
                ),
            },
        )
    if payload.file_size_bytes <= 0:
        raise ValidationFailedError(
            "file_size_bytes must be positive",
            code=ErrorCode.INVALID_FILE_SIZE,
        )
    if payload.file_size_bytes < settings.min_upload_size_bytes:
        raise ValidationFailedError(
            "Requested file is below the minimum upload size",
            code=ErrorCode.FILE_TOO_SMALL,
            details={
                "min_bytes": settings.min_upload_size_bytes,
                "requested_bytes": payload.file_size_bytes,
            },
        )
    if payload.file_size_bytes > settings.max_upload_size_bytes:
        raise ValidationFailedError(
            "Requested file exceeds the upload size limit",
            code=ErrorCode.FILE_TOO_LARGE,
            details={
                "max_bytes": settings.max_upload_size_bytes,
                "requested_bytes": payload.file_size_bytes,
            },
        )


def _build_presigned_response(
    *,
    video_id: uuid.UUID,
    presigned: PresignedUpload,
    expires_at: datetime,
) -> CreateUploadResponse:
    parts_payload: list[PresignedPart] | None = None
    if presigned.parts is not None:
        parts_payload = [
            PresignedPart(part_number=p.part_number, url=p.url) for p in presigned.parts
        ]
    return CreateUploadResponse(
        id=video_id,
        upload_method=presigned.method,
        upload_url=presigned.url,
        upload_headers=presigned.headers,
        upload_id=presigned.upload_id,
        part_size_bytes=presigned.part_size_bytes,
        part_urls=parts_payload,
        expires_at=expires_at,
    )


def _video_detail(
    video: Video,
    jobs: list[ProcessingJob],
    *,
    settings: Settings,
    playback: _PlaybackArtifact | None = None,
    demo_preview_status: str = "idle",
    demo_preview_url: str | None = None,
    demo_preview_generated_at: datetime | None = None,
    demo_preview_error_message: str | None = None,
    demo_preview_enabled: bool = False,
) -> VideoDetailResponse:
    processing_summary: dict[str, str] = {stage.value: "pending" for stage in _PIPELINE_STAGES}
    for job in jobs:
        processing_summary[job.stage.value] = job.status.value
    return VideoDetailResponse(
        id=video.id,
        game_id=video.game_id,
        status=video.status,
        playback_status=derive_playback_status(
            video,
            jobs,
            cv_pipeline_enabled=settings.cv_pipeline_enabled,
        ),
        filename=video.filename,
        file_size_bytes=video.file_size_bytes,
        duration_seconds=video.duration_seconds,
        width=video.width,
        height=video.height,
        fps=video.fps,
        codec=video.codec,
        camera_position=video.camera_position,
        camera_height=video.camera_height,
        checksum_sha256=video.checksum_sha256,
        storage_etag=video.storage_etag,
        storage_output_sha256=video.storage_output_sha256,
        privacy_consent_id=video.privacy_consent_id,
        raw_retention_expires_at=video.raw_retention_expires_at,
        raw_deleted_at=video.raw_deleted_at,
        thumbnail_url=video.thumbnail_url,
        playback_url=playback.url if playback else None,
        playback_token=playback.token if playback else None,
        playback_format=playback.fmt if playback else None,
        token_expires_at=playback.expires_at if playback else None,
        demo_preview_enabled=demo_preview_enabled,
        demo_preview_status=demo_preview_status,
        demo_preview_url=demo_preview_url,
        demo_preview_generated_at=demo_preview_generated_at,
        demo_preview_error_message=demo_preview_error_message,
        processing=processing_summary,
        created_at=video.created_at,
    )


def _video_status(
    video: Video, jobs: list[ProcessingJob], *, settings: Settings
) -> VideoStatusResponse:
    stages: dict[str, ProcessingStageStatus] = {
        stage.value: ProcessingStageStatus(status="pending") for stage in _PIPELINE_STAGES
    }
    active_stage: str | None = None
    progress_pct = 0
    for job in jobs:
        stages[job.stage.value] = ProcessingStageStatus(
            status=job.status.value,
            progress_percent=job.progress_percent,
            started_at=job.started_at,
            heartbeat_at=job.heartbeat_at,
            completed_at=job.completed_at,
            error_message=job.error_message,
        )
        if job.status is ProcessingJobStatus.RUNNING:
            active_stage = job.stage.value
            progress_pct = job.progress_percent
    return VideoStatusResponse(
        status=video.status,
        playback_status=derive_playback_status(
            video,
            jobs,
            cv_pipeline_enabled=settings.cv_pipeline_enabled,
        ),
        stage=active_stage,
        progress_percent=progress_pct,
        stages=stages,
    )


async def _load_video(session: AsyncSession, video_id: uuid.UUID) -> Video | None:
    result = await session.execute(
        select(Video).where(Video.id == video_id).execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _load_video_for_update(session: AsyncSession, video_id: uuid.UUID) -> Video | None:
    result = await session.execute(
        select(Video)
        .where(Video.id == video_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _load_video_for_update_after_binding_tenant(
    session: AsyncSession, video_id: uuid.UUID
) -> Video | None:
    """Bind tenant context before locking a video row under FORCE RLS.

    The runtime app role's update policy requires ``app.current_team_id`` for
    ``SELECT ... FOR UPDATE``. A plain read can discover the team through the
    user-membership select policy; after that, lock the row with the tenant GUC
    set so production behaves like the owner-role tests.
    """
    video = await _load_video(session, video_id)
    if video is None:
        return None
    await set_tenant_context(session, video.team_id)
    return await _load_video_for_update(session, video_id)


async def _record_upload_failure(
    session: AsyncSession,
    *,
    request: Request,
    actor_user_id: uuid.UUID,
    actor_email: str,
    actor_role: UserRole,
    video_id: uuid.UUID | None,
    team_id: uuid.UUID | None,
    extra: dict[str, object],
) -> None:
    try:
        await bind_authenticated_context(
            session,
            user_id=actor_user_id,
            role=actor_role,
            team_id=team_id,
        )
        await write_audit(
            session,
            action=AuditAction.VIDEO_UPLOAD_FAILED,
            request=request,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            resource_type="video",
            resource_id=video_id,
            team_id=team_id,
            extra=extra,
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception(
            "Failed to record video upload failure audit request_id=%s video_id=%s team_id=%s",
            getattr(request.state, "request_id", None),
            video_id,
            team_id,
        )


def _uploaded_object_size(metadata: dict[str, object]) -> int | None:
    content_length = metadata.get("ContentLength")
    if isinstance(content_length, int):
        return content_length
    return None


def _uploaded_object_checksum_sha256(metadata: dict[str, object]) -> str | None:
    raw_metadata = metadata.get("Metadata")
    if isinstance(raw_metadata, dict):
        value = raw_metadata.get("nbu-sha256") or raw_metadata.get("Nbu-Sha256")
        if isinstance(value, str) and len(value) == 64:
            return value.lower()
    raw_checksum = metadata.get("ChecksumSHA256")
    if isinstance(raw_checksum, str):
        try:
            return base64.b64decode(raw_checksum).hex()
        except Exception:
            return None
    return None


async def _delete_raw_object_best_effort(
    presigner: StoragePresigner, storage_key: str | None
) -> None:
    if not storage_key:
        return
    try:
        await storage_delete_object(presigner, key=storage_key)
    except Exception:
        # The S3 implementation already logs. Fakes may raise; completion
        # should still report the validation failure that caused cleanup.
        return


def _video_has_storage_cleanup_work(video: Video) -> bool:
    return bool(
        video.upload_id
        or video.storage_key_raw
        or video.storage_key_mezzanine
        or video.storage_key_hls
        or video.demo_preview_storage_key
    )


async def _delete_video_storage(
    *,
    presigner: StoragePresigner,
    video: Video,
) -> _VideoStorageCleanupResult:
    failures: list[str] = []
    aborted_multipart = False
    deleted_raw = False
    deleted_mezzanine = False
    deleted_hls = False
    deleted_demo_preview = False

    if video.upload_id is not None and video.storage_key_raw:
        try:
            await storage_abort_multipart(
                presigner,
                key=video.storage_key_raw,
                upload_id=video.upload_id,
            )
            aborted_multipart = True
        except Exception:
            failures.append("multipart_upload")

    storage_objects = (
        ("raw", video.storage_key_raw),
        ("mezzanine", video.storage_key_mezzanine),
        ("hls", video.storage_key_hls),
        ("demo_preview", video.demo_preview_storage_key),
    )
    attempted_keys: set[str] = set()
    for label, key in storage_objects:
        if not key or key in attempted_keys:
            continue
        attempted_keys.add(key)
        try:
            await storage_delete_object(presigner, key=key)
        except Exception:
            failures.append(label)
            continue
        if label == "raw":
            deleted_raw = True
        elif label == "mezzanine":
            deleted_mezzanine = True
        elif label == "hls":
            deleted_hls = True
        elif label == "demo_preview":
            deleted_demo_preview = True

    return _VideoStorageCleanupResult(
        aborted_multipart=aborted_multipart,
        deleted_raw=deleted_raw,
        deleted_mezzanine=deleted_mezzanine,
        deleted_hls=deleted_hls,
        deleted_demo_preview=deleted_demo_preview,
        failures=tuple(failures),
    )


async def _mark_video_cleanup_failed(
    *,
    session: AsyncSession,
    request: Request,
    current_user: User,
    video: Video,
    reason: str,
    failures: tuple[str, ...],
) -> None:
    now = datetime.now(tz=UTC)
    video.raw_delete_requested_at = video.raw_delete_requested_at or now
    video.raw_delete_failed_at = now
    video.raw_delete_reason = "user_cleanup"
    await write_audit(
        session,
        action=AuditAction.VIDEO_DELETE_FAILED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="video",
        resource_id=video.id,
        team_id=video.team_id,
        extra={
            "reason": reason,
            "failed_cleanup": list(failures),
            "had_raw_object": video.storage_key_raw is not None,
            "had_mezzanine_object": video.storage_key_mezzanine is not None,
            "had_hls_object": video.storage_key_hls is not None,
            "had_demo_preview_object": video.demo_preview_storage_key is not None,
            "had_multipart_upload": video.upload_id is not None,
        },
    )


async def _assert_raw_object_requeueable(
    *,
    video: Video,
    presigner: StoragePresigner,
) -> None:
    if (
        not video.storage_key_raw
        or video.raw_deleted_at is not None
        or video.raw_storage_deleted_at is not None
    ):
        raise ConflictError(
            "Original upload is no longer available; delete the video and upload again",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"reason": "raw_object_unavailable"},
        )
    metadata = await storage_head_object(presigner, key=video.storage_key_raw)
    if metadata is None:
        raise ConflictError(
            "Original upload is missing from storage; delete the video and upload again",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"reason": "raw_object_missing"},
        )
    actual_size = _uploaded_object_size(metadata)
    if (
        actual_size is not None
        and video.file_size_bytes is not None
        and actual_size != video.file_size_bytes
    ):
        raise ConflictError(
            "Original upload no longer matches the recorded file size",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"reason": "raw_object_size_mismatch"},
        )
    actual_checksum = _uploaded_object_checksum_sha256(metadata)
    if video.checksum_sha256 and actual_checksum and actual_checksum != video.checksum_sha256:
        raise ConflictError(
            "Original upload no longer matches the recorded checksum",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"reason": "raw_object_checksum_mismatch"},
        )


async def _verify_uploaded_object(
    *,
    session: AsyncSession,
    request: Request,
    current_user: User,
    video: Video,
    presigner: StoragePresigner,
    expected_checksum_sha256: str | None = None,
) -> None:
    storage_key = video.storage_key_raw
    if not storage_key:
        await _record_upload_failure(
            session,
            request=request,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
            actor_role=current_user.role,
            video_id=video.id,
            team_id=video.team_id,
            extra={"reason": "missing_storage_key"},
        )
        raise ConflictError(
            "Video is missing its storage key",
            code=ErrorCode.INVALID_VIDEO_STATE,
        )

    metadata = await storage_head_object(presigner, key=storage_key)
    if metadata is None:
        await _record_upload_failure(
            session,
            request=request,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
            actor_role=current_user.role,
            video_id=video.id,
            team_id=video.team_id,
            extra={"reason": "upload_not_found", "storage_key": storage_key},
        )
        raise ConflictError(
            "Uploaded object was not found in storage",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"reason": "upload_not_found"},
        )

    actual_size = _uploaded_object_size(metadata)
    if (
        actual_size is not None
        and video.file_size_bytes is not None
        and actual_size != video.file_size_bytes
    ):
        await _delete_raw_object_best_effort(presigner, storage_key)
        await _record_upload_failure(
            session,
            request=request,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
            actor_role=current_user.role,
            video_id=video.id,
            team_id=video.team_id,
            extra={
                "reason": "upload_size_mismatch",
                "expected_bytes": video.file_size_bytes,
                "actual_bytes": actual_size,
            },
        )
        raise ConflictError(
            "Uploaded object size does not match the declared file size",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={
                "reason": "upload_size_mismatch",
                "expected_bytes": video.file_size_bytes,
                "actual_bytes": actual_size,
            },
        )

    actual_checksum = _uploaded_object_checksum_sha256(metadata)
    if (
        expected_checksum_sha256 is not None
        and actual_checksum is not None
        and actual_checksum != expected_checksum_sha256
    ):
        await _delete_raw_object_best_effort(presigner, storage_key)
        await _record_upload_failure(
            session,
            request=request,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
            actor_role=current_user.role,
            video_id=video.id,
            team_id=video.team_id,
            extra={
                "reason": "upload_checksum_mismatch",
                "expected_sha256": expected_checksum_sha256,
                "actual_sha256": actual_checksum,
            },
        )
        raise ConflictError(
            "Uploaded object checksum does not match the expected SHA-256 digest",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"reason": "upload_checksum_mismatch"},
        )


# ---- POST /videos/upload --------------------------------------------------


@router.post(
    "/upload",
    response_model=CreateUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def initiate_upload(
    payload: CreateUploadRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    storage: StoragePresigner | None = Depends(get_storage),
) -> CreateUploadResponse:
    _validate_upload(payload, settings)
    require_verified_account(current_user, settings=settings)
    actor_user_id = current_user.id
    actor_email = current_user.email
    actor_role = current_user.role

    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    # Game lookup is RLS-protected by the user_id fallback in the games
    # SELECT policy; an unauthorized user gets None → 404.
    game_result = await session.execute(
        select(Game).where(Game.id == payload.game_id).execution_options(populate_existing=True)
    )
    game = game_result.scalar_one_or_none()
    if game is None:
        raise NotFoundError("Game not found", code=ErrorCode.GAME_NOT_FOUND)
    await set_tenant_context(session, game.team_id)
    await require_team_coach(session, user=current_user, team_id=game.team_id)
    team = await session.get(Team, game.team_id)
    if team is None or not team.is_active or team.deleted_at is not None:
        raise NotFoundError("Team not found", code=ErrorCode.TEAM_NOT_FOUND)
    privacy_consent = await _resolve_upload_privacy_consent(
        session=session,
        team=team,
        consent_id=payload.privacy_consent_id,
        settings=settings,
    )
    _require_uploader_youth_consent(team=team, user=current_user, settings=settings)

    await enforce_rate_limit(
        request=request,
        settings=settings,
        scope="video_upload",
        subject=str(current_user.id),
        max_attempts=settings.video_upload_rate_limit_attempts,
        window_seconds=settings.video_upload_rate_limit_window_seconds,
    )

    # Plan quota gate. Provisions a free billing account for the team if one
    # does not yet exist (legacy / pre-migration teams) and rejects with a
    # documented error when usage would exceed the plan ceiling.
    quota_check = await check_video_upload_quota(
        session,
        team_id=game.team_id,
        owner_user_id=current_user.id,
        settings=settings,
    )
    if not quota_check.allowed:
        await write_audit(
            session,
            action=AuditAction.BILLING_QUOTA_DENIED,
            request=request,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
            resource_type="video",
            team_id=game.team_id,
            extra={
                "quota_key": quota_check.quota_key,
                "limit": quota_check.limit,
                "used": quota_check.used,
                "plan_code": quota_check.plan_code,
            },
        )
        await session.commit()
        raise quota_exceeded_error(quota_check)
    storage_quota_check = await check_video_storage_quota(
        session,
        team_id=game.team_id,
        owner_user_id=current_user.id,
        settings=settings,
        additional_bytes=payload.file_size_bytes,
    )
    if not storage_quota_check.allowed:
        await write_audit(
            session,
            action=AuditAction.BILLING_QUOTA_DENIED,
            request=request,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
            resource_type="video",
            team_id=game.team_id,
            extra={
                "quota_key": storage_quota_check.quota_key,
                "limit": storage_quota_check.limit,
                "used": storage_quota_check.used,
                "requested": payload.file_size_bytes,
                "plan_code": storage_quota_check.plan_code,
            },
        )
        await session.commit()
        raise quota_exceeded_error(storage_quota_check)

    presigner = _require_storage(storage)

    video = Video(
        game_id=game.id,
        team_id=game.team_id,
        uploaded_by=current_user.id,
        privacy_consent_id=privacy_consent.id if privacy_consent is not None else None,
        filename=payload.filename,
        status=VideoStatus.PENDING_UPLOAD,
        file_size_bytes=payload.file_size_bytes,
        content_type=payload.content_type,
        checksum_sha256=payload.checksum_sha256,
        camera_position=payload.camera_position,
        camera_height=payload.camera_height,
    )
    session.add(video)
    await session.flush()

    storage_key = storage_key_for_video(
        team_id=str(game.team_id), video_id=str(video.id), filename=payload.filename
    )
    try:
        presigned = await storage_presign_upload(
            presigner,
            key=storage_key,
            content_type=payload.content_type,
            file_size_bytes=payload.file_size_bytes,
            checksum_sha256=payload.checksum_sha256,
        )
    except StorageFailureError as exc:
        failed_video_id = video.id
        failed_game_id = game.id
        failed_team_id = game.team_id
        await session.rollback()
        await _record_upload_failure(
            session,
            request=request,
            actor_user_id=actor_user_id,
            actor_email=actor_email,
            actor_role=actor_role,
            video_id=failed_video_id,
            team_id=failed_team_id,
            extra={
                "reason": "storage_presign_failed",
                "game_id": str(failed_game_id),
                "filename": payload.filename,
                "file_size_bytes": payload.file_size_bytes,
                "content_type": payload.content_type,
            },
        )
        raise exc
    expires_at = datetime.now(tz=UTC) + timedelta(seconds=settings.upload_url_expires_seconds)

    video.storage_key_raw = storage_key
    video.upload_id = presigned.upload_id
    video.upload_expires_at = expires_at

    await write_audit(
        session,
        action=AuditAction.VIDEO_UPLOAD_INITIATED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="video",
        resource_id=video.id,
        team_id=game.team_id,
        extra={
            "game_id": str(game.id),
            "filename": payload.filename,
            "file_size_bytes": payload.file_size_bytes,
            "content_type": payload.content_type,
            "checksum_sha256": payload.checksum_sha256,
            "privacy_consent_id": (
                str(privacy_consent.id) if privacy_consent is not None else None
            ),
            "upload_method": presigned.method.value,
            "quota_plan_code": quota_check.plan_code,
            "quota_used_after": quota_check.used + 1,
            "storage_quota_used_after": storage_quota_check.used + payload.file_size_bytes,
        },
    )
    # Record metered usage for billing aggregation. The check above already
    # provisioned a billing account if needed, so this is always against a
    # known account_id resolved via the team link.
    plan_ctx = await resolve_team_plan(session, team_id=game.team_id)
    if plan_ctx is not None:
        await record_usage(
            session,
            billing_account_id=plan_ctx.account_id,
            event_key="video.upload.initiated",
            team_id=game.team_id,
            metadata={"video_id": str(video.id), "size_bytes": payload.file_size_bytes},
        )
        await record_usage(
            session,
            billing_account_id=plan_ctx.account_id,
            event_key="video.storage.bytes_reserved",
            quantity=payload.file_size_bytes,
            team_id=game.team_id,
            metadata={"video_id": str(video.id)},
        )
    await session.commit()
    await bind_authenticated_context(
        session,
        user_id=current_user.id,
        role=current_user.role,
        team_id=game.team_id,
    )
    await session.refresh(video)

    return _build_presigned_response(video_id=video.id, presigned=presigned, expires_at=expires_at)


# ---- POST /videos/{video_id}/complete -------------------------------------


@router.post(
    "/{video_id:uuid}/cancel-upload",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def cancel_upload(
    video_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    storage: StoragePresigner | None = Depends(get_storage),
) -> None:
    require_verified_account(current_user, settings=settings)
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video_for_update_after_binding_tenant(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await require_team_coach(session, user=current_user, team_id=video.team_id)

    if video.status is VideoStatus.FAILED:
        return None
    if video.status is not VideoStatus.PENDING_UPLOAD:
        raise ConflictError(
            "Video is not awaiting upload cancellation",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"current_status": video.status.value},
        )

    presigner = _require_storage(storage)
    upload_id = video.upload_id
    storage_key = video.storage_key_raw
    if upload_id is not None and storage_key:
        await storage_abort_multipart(presigner, key=storage_key, upload_id=upload_id)
    elif storage_key:
        await _delete_raw_object_best_effort(presigner, storage_key)

    video.status = VideoStatus.FAILED
    video.upload_id = None
    video.upload_expires_at = None
    await release_video_upload_quota_reservation(
        session,
        team_id=video.team_id,
        video_id=video.id,
        reason="user_cancelled",
    )
    await write_audit(
        session,
        action=AuditAction.VIDEO_UPLOAD_ABANDONED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="video",
        resource_id=video.id,
        team_id=video.team_id,
        extra={
            "reason": "user_cancelled",
            "storage_key": storage_key,
            "multipart": upload_id is not None,
        },
    )
    await session.commit()
    await bind_authenticated_context(
        session,
        user_id=current_user.id,
        role=current_user.role,
        team_id=video.team_id,
    )
    return None


@router.delete("/{video_id:uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_failed_video(
    video_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    storage: StoragePresigner | None = Depends(get_storage),
) -> None:
    """Coach/admin cleanup for failed videos and abandoned upload rows.

    This is intentionally narrow for alpha recovery: processed videos are not
    removable through this path, and cleanup only succeeds once known storage
    handles have been removed or aborted. Storage failures leave the row in a
    failed auditable state so operators can retry or reconcile R2 manually.
    """
    require_verified_account(current_user, settings=settings)
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video_for_update_after_binding_tenant(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await require_team_coach(session, user=current_user, team_id=video.team_id)

    if video.status not in {VideoStatus.FAILED, VideoStatus.PENDING_UPLOAD}:
        raise ConflictError(
            "Only failed videos or pending uploads can be deleted from this recovery path",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"current_status": video.status.value},
        )

    cleanup = _VideoStorageCleanupResult()
    if _video_has_storage_cleanup_work(video):
        if storage is None:
            await _mark_video_cleanup_failed(
                session=session,
                request=request,
                current_user=current_user,
                video=video,
                reason="storage_not_configured",
                failures=("storage",),
            )
            await session.commit()
            raise ServiceUnavailableError(
                "Object storage is not configured; video cleanup cannot be completed",
                code=ErrorCode.STORAGE_NOT_CONFIGURED,
            )
        cleanup = await _delete_video_storage(presigner=storage, video=video)
        if cleanup.failures:
            await _mark_video_cleanup_failed(
                session=session,
                request=request,
                current_user=current_user,
                video=video,
                reason="storage_cleanup_failed",
                failures=cleanup.failures,
            )
            await session.commit()
            raise StorageFailureError(
                "Video cleanup failed while deleting storage objects",
                details={"failed_cleanup": list(cleanup.failures)},
            )

    released_usage = await release_video_upload_quota_reservation(
        session,
        team_id=video.team_id,
        video_id=video.id,
        reason="user_deleted_failed_video",
    )
    await write_audit(
        session,
        action=AuditAction.VIDEO_DELETED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="video",
        resource_id=video.id,
        team_id=video.team_id,
        extra={
            "reason": "user_deleted_failed_video",
            "previous_status": video.status.value,
            "quota_released": released_usage is not None,
            "storage_cleanup": {
                "aborted_multipart": cleanup.aborted_multipart,
                "deleted_raw": cleanup.deleted_raw,
                "deleted_mezzanine": cleanup.deleted_mezzanine,
                "deleted_hls": cleanup.deleted_hls,
                "deleted_demo_preview": cleanup.deleted_demo_preview,
            },
        },
    )
    await session.delete(video)
    await session.commit()
    return None


@router.post(
    "/{video_id:uuid}/complete",
    response_model=CompleteUploadResponse,
)
async def complete_upload(
    video_id: uuid.UUID,
    payload: CompleteUploadRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    storage: StoragePresigner | None = Depends(get_storage),
) -> CompleteUploadResponse:
    require_verified_account(current_user, settings=settings)
    actor_user_id = current_user.id
    actor_email = current_user.email
    actor_role = current_user.role
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video_for_update_after_binding_tenant(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await require_team_coach(session, user=current_user, team_id=video.team_id)

    # Idempotent: a duplicate /complete after a successful one returns the
    # existing transcode job rather than failing.
    if video.status in {VideoStatus.QUEUED, VideoStatus.PROCESSING, VideoStatus.PROCESSED}:
        existing_job = await session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.video_id == video.id,
                ProcessingJob.stage == ProcessingJobStage.TRANSCODE,
            )
        )
        if existing_job is None:
            raise AppError(
                "Video is in an inconsistent state",
                code=ErrorCode.INVALID_VIDEO_STATE,
                status_code=409,
            )
        return CompleteUploadResponse(
            id=video.id,
            status=video.status,
            estimated_processing_minutes=_ESTIMATED_PROCESSING_MINUTES,
            job_id=existing_job.id,
        )

    if video.status is not VideoStatus.PENDING_UPLOAD:
        raise ConflictError(
            "Video is not awaiting completion",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"current_status": video.status.value},
        )

    expected_checksum = payload.checksum_sha256 or video.checksum_sha256
    if (
        payload.checksum_sha256
        and video.checksum_sha256
        and payload.checksum_sha256 != video.checksum_sha256
    ):
        raise ValidationFailedError(
            "checksum_sha256 does not match the checksum declared at upload initiation",
            code=ErrorCode.VALIDATION_FAILED,
        )

    if video.upload_id is not None:
        if not payload.parts:
            raise ValidationFailedError(
                "parts is required to complete a multipart upload",
                code=ErrorCode.MULTIPART_PARTS_REQUIRED,
            )
        seen: set[int] = set()
        for part in payload.parts:
            if part.part_number in seen:
                raise ValidationFailedError(
                    "Duplicate part_number in parts",
                    code=ErrorCode.INVALID_MULTIPART_PARTS,
                )
            seen.add(part.part_number)
        presigner = _require_storage(storage)
        try:
            await storage_complete_multipart(
                presigner,
                key=video.storage_key_raw or "",
                upload_id=video.upload_id,
                parts=[{"PartNumber": p.part_number, "ETag": p.etag} for p in payload.parts],
            )
            await _verify_uploaded_object(
                session=session,
                request=request,
                current_user=current_user,
                video=video,
                presigner=presigner,
                expected_checksum_sha256=expected_checksum,
            )
        except StorageFailureError as exc:
            failed_video_id = video.id
            failed_team_id = video.team_id
            failed_upload_id = video.upload_id
            await session.rollback()
            await _record_upload_failure(
                session,
                request=request,
                actor_user_id=actor_user_id,
                actor_email=actor_email,
                actor_role=actor_role,
                video_id=failed_video_id,
                team_id=failed_team_id,
                extra={"reason": "storage_complete_failed", "upload_id": failed_upload_id},
            )
            raise exc
    else:
        presigner = _require_storage(storage)
        try:
            await _verify_uploaded_object(
                session=session,
                request=request,
                current_user=current_user,
                video=video,
                presigner=presigner,
                expected_checksum_sha256=expected_checksum,
            )
        except StorageFailureError as exc:
            failed_video_id = video.id
            failed_team_id = video.team_id
            await session.rollback()
            await _record_upload_failure(
                session,
                request=request,
                actor_user_id=actor_user_id,
                actor_email=actor_email,
                actor_role=actor_role,
                video_id=failed_video_id,
                team_id=failed_team_id,
                extra={"reason": "storage_head_failed"},
            )
            raise exc

    video.status = VideoStatus.QUEUED
    video.checksum_sha256 = expected_checksum
    video.upload_id = None
    video.upload_expires_at = None
    plan_ctx = await resolve_team_plan(session, team_id=video.team_id)
    video.raw_retention_expires_at = video.raw_retention_expires_at or _raw_retention_expires_at(
        settings,
        plan_retention_days=(plan_ctx.raw_video_retention_days if plan_ctx is not None else None),
    )

    transcode_job = ProcessingJob(
        video_id=video.id,
        team_id=video.team_id,
        stage=ProcessingJobStage.TRANSCODE,
        status=ProcessingJobStatus.PENDING,
        progress_percent=0,
    )
    session.add(transcode_job)
    await session.flush()

    await write_audit(
        session,
        action=AuditAction.VIDEO_UPLOAD_COMPLETED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="video",
        resource_id=video.id,
        team_id=video.team_id,
        extra={"checksum_sha256": expected_checksum},
    )
    await write_audit(
        session,
        action=AuditAction.VIDEO_PROCESSING_QUEUED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="processing_job",
        resource_id=transcode_job.id,
        team_id=video.team_id,
        extra={"stage": transcode_job.stage.value, "video_id": str(video.id)},
    )
    await session.commit()
    await bind_authenticated_context(
        session,
        user_id=current_user.id,
        role=current_user.role,
        team_id=video.team_id,
    )
    await session.refresh(video)
    await session.refresh(transcode_job)

    return CompleteUploadResponse(
        id=video.id,
        status=video.status,
        estimated_processing_minutes=_ESTIMATED_PROCESSING_MINUTES,
        job_id=transcode_job.id,
    )


# ---- GET /videos/{video_id} -----------------------------------------------


@router.get("/{video_id:uuid}", response_model=VideoDetailResponse)
async def get_video(
    video_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    storage: StoragePresigner | None = Depends(get_storage),
) -> VideoDetailResponse:
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await set_tenant_context(session, video.team_id)
    await require_team_member(session, user=current_user, team_id=video.team_id)

    jobs_result = await session.execute(
        select(ProcessingJob).where(ProcessingJob.video_id == video.id)
    )
    jobs = list(jobs_result.scalars().all())
    playback = await _try_issue_playback(
        video=video,
        user=current_user,
        storage=storage,
        settings=settings,
    )
    demo_preview = _demo_preview_view(video=video, settings=settings)
    if playback is not None:
        await write_audit(
            session,
            action=AuditAction.VIDEO_PLAYBACK_ISSUED,
            request=request,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
            resource_type="video",
            resource_id=video.id,
            team_id=video.team_id,
            extra={
                "format": playback.fmt,
                "expires_at": playback.expires_at.isoformat(),
            },
        )
        await session.commit()
    return _video_detail(
        video,
        jobs,
        settings=settings,
        playback=playback,
        demo_preview_enabled=settings.local_demo_preview_enabled(),
        demo_preview_status=demo_preview.status,
        demo_preview_url=demo_preview.url,
        demo_preview_generated_at=demo_preview.generated_at,
        demo_preview_error_message=demo_preview.error_message,
    )


# ---- GET /videos/{video_id}/status ----------------------------------------


@router.get("/{video_id:uuid}/status", response_model=VideoStatusResponse)
async def get_video_status(
    video_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> VideoStatusResponse:
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await set_tenant_context(session, video.team_id)
    await require_team_member(session, user=current_user, team_id=video.team_id)

    jobs_result = await session.execute(
        select(ProcessingJob).where(ProcessingJob.video_id == video.id)
    )
    jobs = list(jobs_result.scalars().all())
    return _video_status(video, jobs, settings=settings)


# ---- GET /videos/{video_id}/events ----------------------------------------


_COACH_REVIEW_STATUSES = frozenset(
    {ReviewStatus.NEEDS_REVIEW, ReviewStatus.APPROVED, ReviewStatus.REJECTED}
)
_MANUAL_EVENT_SOURCE = "coach_manual_alpha_tag"
_VIDEO_EVENTS_DEFAULT_LIMIT = 50
_VIDEO_EVENTS_MAX_LIMIT = 100
_DEFAULT_CLIP_PRE_MS = 4_000
_DEFAULT_CLIP_POST_MS = 6_000
_MAX_REVIEW_WINDOW_MS = 60_000
_EVENT_EXPORT_FORMAT_EXTENSIONS = {
    "csv": "csv",
    "json": "json",
    "sportscode_xml": "xml",
    "package_zip": "zip",
}
_EVENT_EXPORT_FORMAT_MEDIA_TYPES = {
    "csv": "text/csv",
    "json": "application/json",
    "sportscode_xml": "application/xml",
    "package_zip": "application/zip",
}
_EVENT_EXPORT_FIELDS = (
    "video_id",
    "event_id",
    "label",
    "event_type",
    "review_status",
    "source",
    "clip_start_seconds",
    "clip_end_seconds",
    "event_seconds",
    "clip_start_time_ms",
    "clip_end_time_ms",
    "event_time_ms",
    "created_at",
    "notes",
)
_EVENT_TYPE_EXPORT_LABELS: dict[VideoEventType, str] = {
    VideoEventType.SHOT_ATTEMPT: "Shot attempt",
    VideoEventType.SHOT_MADE: "Made shot",
    VideoEventType.REBOUND: "Rebound",
    VideoEventType.PASS: "Pass",
}
_EXPORT_README = """NextBallUp alpha timeline export

This package contains reviewed candidate clip windows only. It does not include
raw video, signed playback URLs, storage keys, model prompts, private model
artifact paths, or internal restricted-source lineage.

Files:
- reviewed_windows.csv: coach-facing timeline rows.
- manifest.json: app-neutral timeline manifest.
- sportscode.xml: Sportscode-style XML timeline for import testing.
- review_feedback.json: review labels for future model-evaluation/training
  pipelines, gated by the consent flags inside the file.

Direct Hudl cloud upload is not implemented. Validate imports in your editing
software before relying on an export in a production workflow.
"""

_EVENT_EXPORT_NOTES = (
    "Review only. Not production analytics. Direct Hudl cloud export is not implemented."
)


def _derive_event_source(row: VideoEvent) -> VideoEventSourceValue:
    """Map opaque internal source labels to a stable, externally safe value.

    Never leak the underlying restricted-source string — coaches only need to
    know whether a candidate came from the alpha detector or from a manual
    tag, and exposing internal lineage keys would invite scraping.
    """
    metadata = row.event_metadata or {}
    if metadata.get("source") == _MANUAL_EVENT_SOURCE:
        return "manual"
    return "alpha_model"


def _duration_ms(video: Video) -> int | None:
    return int(video.duration_seconds * 1000) if video.duration_seconds else None


def _window_from_event(row: VideoEvent, *, video: Video) -> tuple[int, int]:
    duration_ms = _duration_ms(video)
    if row.clip_start_time_ms is not None and row.clip_end_time_ms is not None:
        return (
            _clamp_clip_time(row.clip_start_time_ms, duration_ms=duration_ms),
            _clamp_clip_time(row.clip_end_time_ms, duration_ms=duration_ms),
        )
    metadata = row.event_metadata or {}
    metadata_start = metadata.get("clip_start_time_ms")
    metadata_end = metadata.get("clip_end_time_ms")
    if (
        isinstance(metadata_start, int)
        and not isinstance(metadata_start, bool)
        and isinstance(metadata_end, int)
        and not isinstance(metadata_end, bool)
    ):
        return (
            _clamp_clip_time(metadata_start, duration_ms=duration_ms),
            _clamp_clip_time(metadata_end, duration_ms=duration_ms),
        )
    pre_ms = metadata.get("clip_pre_ms")
    post_ms = metadata.get("clip_post_ms")
    pre = (
        pre_ms if isinstance(pre_ms, int) and not isinstance(pre_ms, bool) else _DEFAULT_CLIP_PRE_MS
    )
    post = (
        post_ms
        if isinstance(post_ms, int) and not isinstance(post_ms, bool)
        else _DEFAULT_CLIP_POST_MS
    )
    start = _clamp_clip_time(row.event_time_ms - pre, duration_ms=duration_ms)
    end = _clamp_clip_time(row.event_time_ms + post, duration_ms=duration_ms)
    if end <= start:
        end = _clamp_clip_time(start + 1000, duration_ms=duration_ms)
    return start, end


def _clamp_clip_time(value: int, *, duration_ms: int | None) -> int:
    clamped = max(0, value)
    if duration_ms is not None:
        clamped = min(clamped, duration_ms)
    return clamped


def _resolve_clip_window(
    *,
    event_time_ms: int,
    duration_ms: int | None,
    clip_start_time_ms: int | None,
    clip_end_time_ms: int | None,
) -> tuple[int, int]:
    if (clip_start_time_ms is None) != (clip_end_time_ms is None):
        raise ValidationFailedError(
            "Clip review windows require both start and end timestamps",
            details={
                "clip_start_time_ms": clip_start_time_ms,
                "clip_end_time_ms": clip_end_time_ms,
            },
        )
    start = (
        max(0, event_time_ms - _DEFAULT_CLIP_PRE_MS)
        if clip_start_time_ms is None
        else clip_start_time_ms
    )
    end = event_time_ms + _DEFAULT_CLIP_POST_MS if clip_end_time_ms is None else clip_end_time_ms
    start = _clamp_clip_time(start, duration_ms=duration_ms)
    end = _clamp_clip_time(end, duration_ms=duration_ms)
    if start >= end:
        raise ValidationFailedError(
            "Clip review window start must be before the end",
            details={"clip_start_time_ms": start, "clip_end_time_ms": end},
        )
    if not (start <= event_time_ms <= end):
        raise ValidationFailedError(
            "Event timestamp must sit inside the clip review window",
            details={
                "event_time_ms": event_time_ms,
                "clip_start_time_ms": start,
                "clip_end_time_ms": end,
            },
        )
    if end - start > _MAX_REVIEW_WINDOW_MS:
        raise ValidationFailedError(
            "Clip review windows cannot exceed 60 seconds",
            details={
                "clip_start_time_ms": start,
                "clip_end_time_ms": end,
                "max_window_ms": _MAX_REVIEW_WINDOW_MS,
            },
        )
    return start, end


def _video_event_summary(row: VideoEvent, *, video: Video) -> VideoEventSummary:
    clip_start_time_ms, clip_end_time_ms = _window_from_event(row, video=video)
    return VideoEventSummary(
        id=row.id,
        event_type=row.event_type,
        event_time_ms=row.event_time_ms,
        clip_start_time_ms=clip_start_time_ms,
        clip_end_time_ms=clip_end_time_ms,
        output_frame=row.output_frame,
        period=row.period,
        game_clock_ms=row.game_clock_ms,
        shot_clock_enabled=row.shot_clock_enabled,
        shot_clock_ms=row.shot_clock_ms,
        primary_track_key=row.primary_track_key,
        confidence=row.confidence,
        review_status=row.review_status,
        source=_derive_event_source(row),
        created_at=row.created_at,
    )


def _clip_event_from_row(row: VideoEvent, *, video: Video) -> ClipEvent:
    metadata = dict(row.event_metadata or {})
    if row.clip_start_time_ms is not None and row.clip_end_time_ms is not None:
        clip_start_time_ms, clip_end_time_ms = _window_from_event(row, video=video)
        metadata["clip_pre_ms"] = row.event_time_ms - clip_start_time_ms
        metadata["clip_post_ms"] = clip_end_time_ms - row.event_time_ms
    return ClipEvent(
        id=row.id,
        event_type=row.event_type.value,
        event_time_ms=row.event_time_ms,
        confidence=row.confidence,
        review_status=row.review_status.value,
        created_at=row.created_at,
        metadata=metadata,
    )


def _seconds(value_ms: int) -> float:
    return round(value_ms / 1000, 3)


def _export_label(event_type: VideoEventType) -> str:
    return _EVENT_TYPE_EXPORT_LABELS.get(event_type, event_type.value.replace("_", " ").title())


def _safe_event_export_row(row: VideoEvent, *, video: Video) -> dict[str, str | int | float]:
    summary = _video_event_summary(row, video=video)
    label = _export_label(summary.event_type)
    return {
        "video_id": str(video.id),
        "event_id": str(summary.id),
        "label": label,
        "event_type": summary.event_type.value,
        "review_status": summary.review_status.value,
        "source": summary.source,
        "clip_start_seconds": _seconds(summary.clip_start_time_ms),
        "clip_end_seconds": _seconds(summary.clip_end_time_ms),
        "event_seconds": _seconds(summary.event_time_ms),
        "clip_start_time_ms": summary.clip_start_time_ms,
        "clip_end_time_ms": summary.clip_end_time_ms,
        "event_time_ms": summary.event_time_ms,
        "created_at": summary.created_at.isoformat(),
        "notes": _EVENT_EXPORT_NOTES,
    }


def _render_event_csv(rows: list[dict[str, str | int | float]]) -> str:
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(_EVENT_EXPORT_FIELDS), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def _render_event_manifest(
    *,
    video: Video,
    review_status: ReviewStatus,
    generated_at: datetime,
    rows: list[dict[str, str | int | float]],
) -> str:
    payload = {
        "schema_version": "nextballup_alpha_event_export_v1",
        "video_id": str(video.id),
        "review_status": review_status.value,
        "generated_at": generated_at.isoformat(),
        "not_production_analytics": True,
        "direct_hudl_cloud_export_implemented": False,
        "includes_video_bytes": False,
        "events": rows,
    }
    return json.dumps(payload, separators=(",", ":"))


def _render_review_feedback_manifest(
    *,
    video: Video,
    review_status: ReviewStatus,
    generated_at: datetime,
    rows: list[dict[str, str | int | float]],
    consent: TeamPrivacyConsent | None,
) -> str:
    commercial_training_allowed = bool(
        consent is not None
        and consent.covers_cv_processing
        and consent.commercial_ml_training_allowed
    )
    payload = {
        "schema_version": "nextballup_alpha_review_feedback_v1",
        "video_id": str(video.id),
        "team_id": str(video.team_id),
        "review_status": review_status.value,
        "generated_at": generated_at.isoformat(),
        "governance": {
            "review_only": True,
            "not_production_analytics": True,
            "includes_video_bytes": False,
            "includes_signed_urls": False,
            "commercial_training_allowed": commercial_training_allowed,
            "commercial_training_consent_id": str(consent.id)
            if commercial_training_allowed and consent is not None
            else None,
            "global_model_training_requires_operator_rights_review": True,
        },
        "examples": [
            {
                "event_id": row["event_id"],
                "video_id": row["video_id"],
                "label": row["event_type"],
                "review_status": row["review_status"],
                "accepted": row["review_status"] == ReviewStatus.APPROVED.value,
                "source": row["source"],
                "clip_start_time_ms": row["clip_start_time_ms"],
                "clip_end_time_ms": row["clip_end_time_ms"],
                "event_time_ms": row["event_time_ms"],
            }
            for row in rows
        ],
    }
    return json.dumps(payload, separators=(",", ":"))


def _render_sportscode_xml(rows: list[dict[str, str | int | float]]) -> str:
    root = ET.Element("file")
    all_instances = ET.SubElement(root, "ALL_INSTANCES")
    for index, row in enumerate(rows, start=1):
        instance = ET.SubElement(all_instances, "instance")
        ET.SubElement(instance, "ID").text = str(index)
        ET.SubElement(instance, "start").text = f"{float(row['clip_start_seconds']):.3f}"
        ET.SubElement(instance, "end").text = f"{float(row['clip_end_seconds']):.3f}"
        ET.SubElement(instance, "code").text = str(row["label"])
        status_label = ET.SubElement(instance, "label")
        ET.SubElement(status_label, "group").text = "NextBallUp review"
        ET.SubElement(status_label, "text").text = str(row["review_status"])
        source_label = ET.SubElement(instance, "label")
        ET.SubElement(source_label, "group").text = "NextBallUp source"
        ET.SubElement(source_label, "text").text = str(row["source"])
    ET.indent(root, space="  ")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        + ET.tostring(root, encoding="unicode", short_empty_elements=False)
        + "\n"
    )


async def _latest_active_training_consent(
    session: AsyncSession,
    *,
    team_id: uuid.UUID,
) -> TeamPrivacyConsent | None:
    now = datetime.now(tz=UTC)
    return cast(
        "TeamPrivacyConsent | None",
        await session.scalar(
            select(TeamPrivacyConsent)
            .where(
                TeamPrivacyConsent.team_id == team_id,
                TeamPrivacyConsent.revoked_at.is_(None),
                (TeamPrivacyConsent.expires_at.is_(None)) | (TeamPrivacyConsent.expires_at > now),
            )
            .order_by(TeamPrivacyConsent.effective_at.desc(), TeamPrivacyConsent.created_at.desc())
            .limit(1)
        ),
    )


def _render_event_package(
    *,
    video: Video,
    review_status: ReviewStatus,
    generated_at: datetime,
    rows: list[dict[str, str | int | float]],
    consent: TeamPrivacyConsent | None,
) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("README.txt", _EXPORT_README)
        archive.writestr("reviewed_windows.csv", _render_event_csv(rows))
        archive.writestr(
            "manifest.json",
            _render_event_manifest(
                video=video,
                review_status=review_status,
                generated_at=generated_at,
                rows=rows,
            ),
        )
        archive.writestr("sportscode.xml", _render_sportscode_xml(rows))
        archive.writestr(
            "review_feedback.json",
            _render_review_feedback_manifest(
                video=video,
                review_status=review_status,
                generated_at=generated_at,
                rows=rows,
                consent=consent,
            ),
        )
    return output.getvalue()


def _estimated_output_frame(*, video: Video, event_time_ms: int) -> int:
    fps = video.fps if video.fps is not None and video.fps > 0 else 30.0
    return max(0, round((event_time_ms / 1000) * fps))


def _encode_event_cursor(*, event_time_ms: int, event_id: uuid.UUID) -> str:
    raw = f"{event_time_ms}:{event_id.hex}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_event_cursor(cursor: str) -> tuple[int, uuid.UUID]:
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + padding).decode()
        time_part, id_part = raw.split(":", 1)
        return int(time_part), uuid.UUID(hex=id_part)
    except (UnicodeDecodeError, ValueError, binascii.Error) as exc:
        raise ValidationFailedError(
            "Invalid pagination cursor",
            details={"cursor": cursor},
        ) from exc


@router.get("/{video_id:uuid}/events", response_model=VideoEventsResponse)
async def list_video_events(
    video_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=_VIDEO_EVENTS_MAX_LIMIT)] = _VIDEO_EVENTS_DEFAULT_LIMIT,
    cursor: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    review_status: Annotated[list[ReviewStatus] | None, Query()] = None,
    event_type: Annotated[list[VideoEventType] | None, Query()] = None,
    source: Annotated[
        Literal["alpha_model", "manual", "all"] | None,
        Query(description="alpha_model | manual | all (default all)"),
    ] = None,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VideoEventsResponse:
    """Paginated coach-review listing of candidate video events.

    Returns alpha-model candidates and manual coach tags side by side, ordered
    by event time. Coaches/admins only. Bounded by `limit` (max 100). Use the
    returned `next_cursor` to fetch additional pages.
    """
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await set_tenant_context(session, video.team_id)
    # Tighter than the prior member-level read: the events listing is the
    # coach review surface, and players/read-only members must not be able to
    # enumerate alpha candidates.
    await require_team_coach(session, user=current_user, team_id=video.team_id)

    game = await session.get(Game, video.game_id)

    base = select(VideoEvent).where(VideoEvent.video_id == video.id)

    summary = await _compute_event_summary_counts(session, video_id=video.id)

    filtered = base
    if review_status:
        filtered = filtered.where(VideoEvent.review_status.in_(list(review_status)))
    if event_type:
        filtered = filtered.where(VideoEvent.event_type.in_(list(event_type)))
    if source == "manual":
        filtered = filtered.where(
            VideoEvent.event_metadata["source"].astext == _MANUAL_EVENT_SOURCE
        )
    elif source == "alpha_model":
        filtered = filtered.where(
            (VideoEvent.event_metadata["source"].astext.is_(None))
            | (VideoEvent.event_metadata["source"].astext != _MANUAL_EVENT_SOURCE)
        )

    total = await session.scalar(select(func.count()).select_from(filtered.subquery()))

    paginated = filtered.order_by(VideoEvent.event_time_ms.asc(), VideoEvent.id.asc())
    if cursor:
        cur_time, cur_id = _decode_event_cursor(cursor)
        paginated = paginated.where(
            (VideoEvent.event_time_ms > cur_time)
            | ((VideoEvent.event_time_ms == cur_time) & (VideoEvent.id > cur_id))
        )
    paginated = paginated.limit(limit + 1)

    rows = list((await session.execute(paginated)).scalars())
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    events = [_video_event_summary(row, video=video) for row in rows]
    next_cursor: str | None = None
    if has_more and rows:
        last = rows[-1]
        next_cursor = _encode_event_cursor(event_time_ms=last.event_time_ms, event_id=last.id)

    return VideoEventsResponse(
        video_id=video.id,
        shot_clock_enabled=bool(game.shot_clock_enabled) if game is not None else False,
        shot_clock_seconds=game.shot_clock_seconds if game is not None else None,
        events=events,
        total=int(total or 0),
        next_cursor=next_cursor,
        summary=summary,
    )


async def _compute_event_summary_counts(
    session: AsyncSession, *, video_id: uuid.UUID
) -> VideoEventsSummaryCounts:
    """One-shot aggregate for the candidate-review filter chips.

    Counts every event for the video — bounded at the per-video scale, well
    below any expected scan cost — split by review status and by source.
    """
    manual_source_expr = VideoEvent.event_metadata["source"].astext == _MANUAL_EVENT_SOURCE
    result = (
        await session.execute(
            select(
                func.count().label("total"),
                func.count()
                .filter(VideoEvent.review_status == ReviewStatus.NEEDS_REVIEW)
                .label("needs_review"),
                func.count()
                .filter(VideoEvent.review_status == ReviewStatus.APPROVED)
                .label("approved"),
                func.count()
                .filter(VideoEvent.review_status == ReviewStatus.REJECTED)
                .label("rejected"),
                func.count()
                .filter(VideoEvent.review_status == ReviewStatus.MACHINE_ONLY)
                .label("machine_only"),
                func.count().filter(manual_source_expr).label("manual_source"),
            ).where(VideoEvent.video_id == video_id)
        )
    ).one()
    total = int(result.total or 0)
    manual = int(result.manual_source or 0)
    return VideoEventsSummaryCounts(
        total=total,
        needs_review=int(result.needs_review or 0),
        approved=int(result.approved or 0),
        rejected=int(result.rejected or 0),
        machine_only=int(result.machine_only or 0),
        alpha_model_source=max(0, total - manual),
        manual_source=manual,
    )


@router.get("/{video_id:uuid}/events/export", response_class=Response)
async def export_video_events(
    video_id: uuid.UUID,
    request: Request,
    export_format: Annotated[
        Literal["csv", "json", "sportscode_xml", "package_zip"],
        Query(alias="format", description="csv | json | sportscode_xml | package_zip"),
    ] = "csv",
    review_status: Annotated[ReviewStatus, Query()] = ReviewStatus.APPROVED,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    """Export reviewed candidate windows without leaking model metadata.

    This is intentionally file-based. Direct Hudl/cloud editing integrations
    should stay separate until partner access and legal review are real.
    """
    require_verified_account(current_user, settings=settings)
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await set_tenant_context(session, video.team_id)
    await require_team_coach(session, user=current_user, team_id=video.team_id)

    rows = list(
        (
            await session.execute(
                select(VideoEvent)
                .where(VideoEvent.video_id == video.id)
                .where(VideoEvent.review_status == review_status)
                .order_by(VideoEvent.clip_start_time_ms, VideoEvent.event_time_ms)
            )
        ).scalars()
    )
    safe_rows = [_safe_event_export_row(row, video=video) for row in rows]
    generated_at = datetime.now(tz=UTC)
    consent = (
        await _latest_active_training_consent(session, team_id=video.team_id)
        if export_format == "package_zip"
        else None
    )
    await write_audit(
        session,
        action=AuditAction.VIDEO_EVENT_EXPORT_CREATED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="video",
        resource_id=video.id,
        team_id=video.team_id,
        extra={
            "format": export_format,
            "review_status": review_status.value,
            "event_count": len(safe_rows),
            "includes_video_bytes": False,
            "not_production_analytics": True,
        },
    )
    await session.commit()
    await bind_authenticated_context(
        session,
        user_id=current_user.id,
        role=current_user.role,
        team_id=video.team_id,
    )

    extension = _EVENT_EXPORT_FORMAT_EXTENSIONS[export_format]
    filename = f"nextballup-{video.id}-events.{extension}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if export_format == "json":
        return Response(
            content=_render_event_manifest(
                video=video,
                review_status=review_status,
                generated_at=generated_at,
                rows=safe_rows,
            ),
            media_type=_EVENT_EXPORT_FORMAT_MEDIA_TYPES[export_format],
            headers=headers,
        )
    if export_format == "sportscode_xml":
        return Response(
            content=_render_sportscode_xml(safe_rows),
            media_type=_EVENT_EXPORT_FORMAT_MEDIA_TYPES[export_format],
            headers=headers,
        )
    if export_format == "package_zip":
        return Response(
            content=_render_event_package(
                video=video,
                review_status=review_status,
                generated_at=generated_at,
                rows=safe_rows,
                consent=consent,
            ),
            media_type=_EVENT_EXPORT_FORMAT_MEDIA_TYPES[export_format],
            headers=headers,
        )

    return Response(
        content=_render_event_csv(safe_rows),
        media_type=_EVENT_EXPORT_FORMAT_MEDIA_TYPES[export_format],
        headers=headers,
    )


@router.post(
    "/{video_id:uuid}/events", response_model=VideoEventSummary, status_code=status.HTTP_201_CREATED
)
async def create_manual_video_event(
    video_id: uuid.UUID,
    payload: CreateVideoEventRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> VideoEventSummary:
    require_verified_account(current_user, settings=settings)
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await set_tenant_context(session, video.team_id)
    await require_team_coach(session, user=current_user, team_id=video.team_id)

    if video.status is not VideoStatus.PROCESSED:
        raise ConflictError(
            "Manual video tags can only be added after playback processing finishes",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"current_status": video.status.value},
        )
    duration_ms = _duration_ms(video)
    if duration_ms is not None and payload.event_time_ms > duration_ms:
        raise ValidationFailedError(
            "Manual video tag timestamp is outside the video duration",
            details={"duration_ms": duration_ms},
        )
    clip_start_time_ms, clip_end_time_ms = _resolve_clip_window(
        event_time_ms=payload.event_time_ms,
        duration_ms=duration_ms,
        clip_start_time_ms=payload.clip_start_time_ms,
        clip_end_time_ms=payload.clip_end_time_ms,
    )

    game = await session.get(Game, video.game_id)
    row = VideoEvent(
        video_id=video.id,
        team_id=video.team_id,
        event_type=payload.event_type,
        event_time_ms=payload.event_time_ms,
        clip_start_time_ms=clip_start_time_ms,
        clip_end_time_ms=clip_end_time_ms,
        output_frame=_estimated_output_frame(video=video, event_time_ms=payload.event_time_ms),
        shot_clock_enabled=bool(game.shot_clock_enabled) if game is not None else False,
        confidence=None,
        review_status=ReviewStatus.NEEDS_REVIEW,
        event_metadata={
            "source": _MANUAL_EVENT_SOURCE,
            "not_production_analytics": True,
            "review_copy": "Coach-created alpha tag. Review before export.",
            "clip_pre_ms": payload.event_time_ms - clip_start_time_ms,
            "clip_post_ms": clip_end_time_ms - payload.event_time_ms,
        },
    )
    session.add(row)
    await session.flush()
    await write_audit(
        session,
        action=AuditAction.VIDEO_EVENT_MANUAL_CREATED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="video_event",
        resource_id=row.id,
        team_id=video.team_id,
        extra={
            "video_id": str(video.id),
            "event_type": row.event_type.value,
            "event_time_ms": row.event_time_ms,
            "clip_start_time_ms": row.clip_start_time_ms,
            "clip_end_time_ms": row.clip_end_time_ms,
            "not_production_analytics": True,
        },
    )
    await session.commit()
    await bind_authenticated_context(
        session,
        user_id=current_user.id,
        role=current_user.role,
        team_id=video.team_id,
    )
    await session.refresh(row)
    return _video_event_summary(row, video=video)


@router.patch(
    "/{video_id:uuid}/events/{event_id:uuid}/review",
    response_model=VideoEventSummary,
)
async def update_video_event_review(
    video_id: uuid.UUID,
    event_id: uuid.UUID,
    payload: UpdateVideoEventReviewRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> VideoEventSummary:
    require_verified_account(current_user, settings=settings)
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await set_tenant_context(session, video.team_id)
    await require_team_coach(session, user=current_user, team_id=video.team_id)

    if payload.review_status not in _COACH_REVIEW_STATUSES:
        raise ValidationFailedError(
            "Coach review can only mark a video event as needs review, approved, or rejected",
            details={"review_status": payload.review_status.value},
        )
    row = await session.scalar(
        select(VideoEvent).where(VideoEvent.id == event_id, VideoEvent.video_id == video.id)
    )
    if row is None:
        raise NotFoundError("Video event not found", code=ErrorCode.NOT_FOUND)

    previous_status = row.review_status
    previous_clip_start_time_ms, previous_clip_end_time_ms = _window_from_event(row, video=video)
    if payload.clip_start_time_ms is not None or payload.clip_end_time_ms is not None:
        row.clip_start_time_ms, row.clip_end_time_ms = _resolve_clip_window(
            event_time_ms=row.event_time_ms,
            duration_ms=_duration_ms(video),
            clip_start_time_ms=payload.clip_start_time_ms,
            clip_end_time_ms=payload.clip_end_time_ms,
        )
    row.review_status = payload.review_status
    metadata = dict(row.event_metadata or {})
    metadata["reviewed_at"] = datetime.now(tz=UTC).isoformat()
    metadata["review_source"] = "coach_review"
    if row.clip_start_time_ms is not None and row.clip_end_time_ms is not None:
        metadata["clip_pre_ms"] = row.event_time_ms - row.clip_start_time_ms
        metadata["clip_post_ms"] = row.clip_end_time_ms - row.event_time_ms
    row.event_metadata = metadata
    await write_audit(
        session,
        action=AuditAction.VIDEO_EVENT_REVIEWED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="video_event",
        resource_id=row.id,
        team_id=video.team_id,
        extra={
            "video_id": str(video.id),
            "previous_review_status": previous_status.value,
            "review_status": row.review_status.value,
            "event_type": row.event_type.value,
            "previous_clip_start_time_ms": previous_clip_start_time_ms,
            "previous_clip_end_time_ms": previous_clip_end_time_ms,
            "clip_start_time_ms": row.clip_start_time_ms,
            "clip_end_time_ms": row.clip_end_time_ms,
        },
    )
    await session.commit()
    await bind_authenticated_context(
        session,
        user_id=current_user.id,
        role=current_user.role,
        team_id=video.team_id,
    )
    await session.refresh(row)
    return _video_event_summary(row, video=video)


# ---- GET /videos/{video_id}/clip-proposals -------------------------------


@router.get("/{video_id:uuid}/clip-proposals", response_model=VideoClipProposalsResponse)
async def list_video_clip_proposals(
    video_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> VideoClipProposalsResponse:
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await set_tenant_context(session, video.team_id)
    await require_team_coach(session, user=current_user, team_id=video.team_id)

    candidate_limit = min(limit * 4, 400)
    rows = await session.execute(
        select(VideoEvent)
        .where(VideoEvent.video_id == video.id)
        .where(VideoEvent.review_status.in_((ReviewStatus.NEEDS_REVIEW, ReviewStatus.MACHINE_ONLY)))
        .order_by(VideoEvent.event_time_ms, VideoEvent.output_frame)
        .limit(candidate_limit)
    )
    proposals = build_clip_proposals(
        [_clip_event_from_row(row, video=video) for row in rows.scalars()],
        duration_seconds=video.duration_seconds,
        limit=limit,
    )
    response_items = [
        VideoClipProposalSummary(
            id=proposal.id,
            source_event_id=proposal.source_event_id,
            event_type=VideoEventType(proposal.event_type),
            label=proposal.label,
            reason=proposal.reason,
            start_time_ms=proposal.start_time_ms,
            end_time_ms=proposal.end_time_ms,
            review_status=ReviewStatus(proposal.review_status),
            created_at=proposal.created_at,
        )
        for proposal in proposals
        if proposal.created_at is not None
    ]
    return VideoClipProposalsResponse(
        video_id=video.id,
        proposals=response_items,
        total=len(response_items),
    )


# ---- POST /videos/{video_id}/playback/verify ------------------------------


@router.post("/{video_id:uuid}/playback/verify", response_model=PlaybackVerifyResponse)
async def verify_playback_token(
    video_id: uuid.UUID,
    payload: PlaybackVerifyRequest,
    session: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_app_settings),
    current_user: User = Depends(get_current_user),
) -> PlaybackVerifyResponse:
    """Cross-check a live playback token against the current session.

    The flow is: the client holds a playback token handed out by the video
    detail endpoint and periodically asks the server if it's still valid. The
    server rejects the token if (a) it's expired / malformed, (b) it was
    minted for a different user, (c) the user's session_version has moved
    (e.g. they logged out), or (d) the token scopes a video the caller is no
    longer entitled to read. A 200 means 'keep playing'; any 4xx means 'drop
    the stream'. This is what turns playback tokens from theatre into actual
    revocation, without having to invalidate presigned URLs individually.
    """
    try:
        claims = decode_token(
            payload.token,
            expected_type="playback",
            settings=settings,
            audience=settings.playback_token_audience,
        )
    except AuthenticationError as exc:
        raise exc

    try:
        token_user_id = uuid.UUID(claims["sub"])
        token_video_id = uuid.UUID(claims["vid"])
        token_team_id = uuid.UUID(claims["tid"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Malformed playback token") from exc

    # Binding checks: the token must be for *this* request's video AND the
    # currently authenticated user. Mismatch means the client is trying to
    # replay a token across videos or sessions.
    if token_video_id != video_id:
        raise AuthenticationError("Playback token does not match video")
    if token_user_id != current_user.id:
        raise AuthenticationError("Playback token belongs to a different user")
    if claims.get("sv") != current_user.session_version:
        raise AuthenticationError("Session has been invalidated")

    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    if video.team_id != token_team_id:
        raise AuthenticationError("Playback token tenant mismatch")
    await set_tenant_context(session, video.team_id)
    # Current membership still required — a user who was removed from the team
    # between issuance and verify must be dropped here.
    await require_team_member(session, user=current_user, team_id=video.team_id)

    exp = datetime.fromtimestamp(int(claims["exp"]), tz=UTC)
    return PlaybackVerifyResponse(video_id=video_id, expires_at=exp)


# ---- POST /videos/{video_id}/demo-preview ---------------------------------


@router.post(
    "/{video_id:uuid}/demo-preview",
    response_model=GenerateDemoPreviewResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_demo_preview(
    video_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    storage: StoragePresigner | None = Depends(get_storage),
) -> GenerateDemoPreviewResponse:
    require_verified_account(current_user, settings=settings)
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await set_tenant_context(session, video.team_id)
    await require_team_coach(session, user=current_user, team_id=video.team_id)

    try:
        await enforce_rate_limit(
            request=request,
            settings=settings,
            scope="video_demo_preview",
            subject=str(current_user.id),
            max_attempts=settings.video_demo_preview_rate_limit_attempts,
            window_seconds=settings.video_demo_preview_rate_limit_window_seconds,
        )
        result = queue_demo_preview_request(
            video=video,
            storage=_require_storage(storage),
            settings=settings,
        )
        if result.enqueued:
            now = datetime.now(tz=UTC)
            video.demo_preview_status = result.response.status
            video.demo_preview_requested_at = now
            video.demo_preview_started_at = None
            video.demo_preview_task_id = result.task_id
            video.demo_preview_error_message = None
    except AppError as exc:
        await write_audit(
            session,
            action=AuditAction.VIDEO_DEMO_PREVIEW_REJECTED,
            request=request,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
            resource_type="video",
            resource_id=video.id,
            team_id=video.team_id,
            extra={
                "error_code": exc.code,
                "error_message": exc.message,
                "sample_fps": settings.cv_demo_sample_fps,
            },
        )
        await session.commit()
        raise
    if result.enqueued:
        await write_audit(
            session,
            action=AuditAction.VIDEO_DEMO_PREVIEW_REQUESTED,
            request=request,
            actor_user_id=current_user.id,
            actor_email=current_user.email,
            resource_type="video",
            resource_id=video.id,
            team_id=video.team_id,
            extra={
                "status": result.response.status,
                "sample_fps": settings.cv_demo_sample_fps,
            },
        )
    await session.commit()
    return result.response


# ---- GET /videos/{video_id}/demo-preview/artifact -------------------------


@router.delete(
    "/{video_id:uuid}/demo-preview",
    response_model=GenerateDemoPreviewResponse,
)
async def cancel_demo_preview(
    video_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> GenerateDemoPreviewResponse:
    require_verified_account(current_user, settings=settings)
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await set_tenant_context(session, video.team_id)
    await require_team_coach(session, user=current_user, team_id=video.team_id)

    if video.demo_preview_status not in {"queued", "running"}:
        raise ConflictError(
            "Only queued or running alpha detector previews can be cancelled",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"current_status": video.demo_preview_status},
        )

    cancelled_at = datetime.now(tz=UTC)
    previous_status = video.demo_preview_status
    previous_task_id = video.demo_preview_task_id
    video.demo_preview_status = "failed"
    video.demo_preview_error_message = (
        "Alpha detector preview was cancelled. Fix the local worker setup, then generate again."
    )
    video.demo_preview_task_id = None
    video.demo_preview_started_at = None
    video.demo_preview_requested_at = cancelled_at
    await write_audit(
        session,
        action=AuditAction.VIDEO_DEMO_PREVIEW_CANCELLED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="video",
        resource_id=video.id,
        team_id=video.team_id,
        extra={
            "previous_status": previous_status,
            "previous_task_id": previous_task_id,
        },
    )
    await session.commit()
    return GenerateDemoPreviewResponse(
        status="failed",
        preview_url=(
            f"/api/v1/videos/{video.id}/demo-preview/artifact"
            if video.demo_preview_storage_key
            else None
        ),
        generated_at=video.demo_preview_generated_at if video.demo_preview_storage_key else None,
    )


@router.get("/{video_id:uuid}/demo-preview/artifact")
async def get_demo_preview_artifact(
    video_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    storage: StoragePresigner | None = Depends(get_storage),
) -> Response:
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await set_tenant_context(session, video.team_id)
    await require_team_member(session, user=current_user, team_id=video.team_id)

    if video.demo_preview_storage_key:
        presigner = _require_storage(storage)
        metadata = await storage_head_object(presigner, key=video.demo_preview_storage_key)
        if metadata is None or _uploaded_object_size(metadata) == 0:
            raise NotFoundError("Demo preview not found", code=ErrorCode.NOT_FOUND)
        url = await storage_presign_get(
            presigner,
            key=video.demo_preview_storage_key,
            expires_in=settings.demo_preview_url_expires_seconds,
            response_content_type="video/mp4",
        )
        return RedirectResponse(
            url=url,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={
                "Cache-Control": "private, no-store, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    artifact = resolve_demo_preview(settings=settings, video_id=video.id)
    if artifact is None:
        raise NotFoundError("Demo preview not found", code=ErrorCode.NOT_FOUND)
    return FileResponse(
        path=artifact.output_path,
        media_type="video/mp4",
        filename=f"{video.id}.demo-preview.mp4",
        headers={
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# ---- POST /videos/{video_id}/processing/cancel ----------------------------


@router.post("/{video_id:uuid}/processing/cancel", response_model=CancelProcessingResponse)
async def cancel_processing(
    video_id: uuid.UUID,
    payload: CancelProcessingRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
) -> CancelProcessingResponse:
    """Coach/admin recovery for a running transcode that is stuck in alpha.

    The API cannot reliably terminate an already-running FFmpeg subprocess on
    Render, so cancellation is a control-plane transition: the current claimed
    job is marked FAILED and late worker writes are ignored by celery_task_id
    ownership checks in the worker runtime.
    """
    require_verified_account(current_user, settings=settings)

    try:
        stage = ProcessingJobStage(payload.stage)
    except ValueError as exc:
        raise ValidationFailedError(
            "Unknown processing stage",
            code=ErrorCode.PROCESSING_STAGE_UNKNOWN,
            details={"stage": payload.stage},
        ) from exc

    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()

    video = await _load_video_for_update_after_binding_tenant(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await require_team_coach(session, user=current_user, team_id=video.team_id)

    if stage is not ProcessingJobStage.TRANSCODE:
        raise ConflictError(
            "Only running transcode jobs can be cancelled from this recovery path",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"stage": stage.value},
        )
    if video.status not in {VideoStatus.PROCESSING, VideoStatus.QUEUED}:
        raise ConflictError(
            "Video is not currently processing",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"current_status": video.status.value},
        )

    job = await session.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.video_id == video.id,
            ProcessingJob.stage == stage,
        )
        .with_for_update()
    )
    if job is None:
        raise NotFoundError(
            "Processing job not found for this stage",
            code=ErrorCode.PROCESSING_JOB_NOT_FOUND,
        )
    if job.status is not ProcessingJobStatus.RUNNING:
        raise ConflictError(
            "Only running processing jobs can be cancelled",
            code=ErrorCode.PROCESSING_JOB_NOT_REQUEUABLE,
            details={"current_status": job.status.value},
        )

    cancelled_at = datetime.now(tz=UTC)
    previous_metadata = dict(job.result_metadata or {})
    previous_metadata.update(
        {
            "cancelled_at": cancelled_at.isoformat(),
            "cancelled_by_user_id": str(current_user.id),
            "cancel_reason": "user_cancelled_processing",
            "previous_progress_percent": job.progress_percent,
            "previous_celery_task_present": job.celery_task_id is not None,
        }
    )
    job.status = ProcessingJobStatus.FAILED
    job.completed_at = cancelled_at
    job.error_message = f"[{ErrorCode.PROCESSING_CANCELLED}] Processing cancelled by coach/admin"
    job.result_metadata = previous_metadata
    video.status = VideoStatus.FAILED

    await write_audit(
        session,
        action=AuditAction.VIDEO_PROCESSING_CANCELLED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="processing_job",
        resource_id=job.id,
        team_id=video.team_id,
        extra={
            "video_id": str(video.id),
            "stage": stage.value,
            "previous_progress_percent": job.progress_percent,
            "had_celery_task_id": job.celery_task_id is not None,
        },
    )
    await session.commit()
    await bind_authenticated_context(
        session,
        user_id=current_user.id,
        role=current_user.role,
        team_id=video.team_id,
    )

    return CancelProcessingResponse(
        job_id=job.id,
        stage=stage.value,
        status=job.status.value,
        cancelled_at=cancelled_at,
    )


# ---- POST /videos/{video_id}/processing/requeue ---------------------------


@router.post("/{video_id:uuid}/processing/requeue", response_model=RequeueProcessingResponse)
async def requeue_processing(
    video_id: uuid.UUID,
    payload: RequeueProcessingRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_app_settings),
    storage: StoragePresigner | None = Depends(get_storage),
) -> RequeueProcessingResponse:
    """Coach/admin recovery: retry a failed transcode when the raw upload is
    still available.

    This deliberately does not weaken RLS or audit. The caller must be a coach
    for the video's team (or platform admin), the job must be a failed
    transcode, and storage is checked before the row is returned to PENDING.
    """
    require_verified_account(current_user, settings=settings)

    try:
        stage = ProcessingJobStage(payload.stage)
    except ValueError as exc:
        raise ValidationFailedError(
            "Unknown processing stage",
            code=ErrorCode.PROCESSING_STAGE_UNKNOWN,
            details={"stage": payload.stage},
        ) from exc

    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()

    video = await _load_video_for_update_after_binding_tenant(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await require_team_coach(session, user=current_user, team_id=video.team_id)
    if stage is not ProcessingJobStage.TRANSCODE:
        raise ConflictError(
            "Only failed transcode jobs can be retried from this recovery path",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"stage": stage.value},
        )
    if video.status is not VideoStatus.FAILED:
        raise ConflictError(
            "Video is not in a failed state",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"current_status": video.status.value},
        )

    job = await session.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.video_id == video.id,
            ProcessingJob.stage == stage,
        )
        .with_for_update()
    )
    if job is None:
        raise NotFoundError(
            "Processing job not found for this stage",
            code=ErrorCode.PROCESSING_JOB_NOT_FOUND,
        )
    if job.status is not ProcessingJobStatus.FAILED:
        raise ConflictError(
            "Only failed processing jobs can be retried",
            code=ErrorCode.PROCESSING_JOB_NOT_REQUEUABLE,
            details={"current_status": job.status.value},
        )

    presigner = _require_storage(storage)
    await _assert_raw_object_requeueable(video=video, presigner=presigner)

    video.status = VideoStatus.QUEUED
    job.status = ProcessingJobStatus.PENDING
    job.progress_percent = 0
    job.error_message = None
    job.result_metadata = None
    job.celery_task_id = None
    job.started_at = None
    job.completed_at = None
    job.heartbeat_at = None

    requeued_at = datetime.now(tz=UTC)

    await write_audit(
        session,
        action=AuditAction.VIDEO_PROCESSING_REQUEUED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="processing_job",
        resource_id=job.id,
        team_id=video.team_id,
        extra={
            "video_id": str(video.id),
            "stage": stage.value,
        },
    )
    await session.commit()
    await bind_authenticated_context(
        session,
        user_id=current_user.id,
        role=current_user.role,
        team_id=video.team_id,
    )
    await session.refresh(job)

    return RequeueProcessingResponse(
        job_id=job.id,
        stage=stage.value,
        status=job.status.value,
        requeued_at=requeued_at,
    )
