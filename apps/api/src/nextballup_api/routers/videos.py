from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_api.audit import write_audit
from nextballup_api.deps import get_app_settings, get_current_user, get_db
from nextballup_api.permissions import (
    require_team_coach,
    require_team_member,
    require_user_role,
)
from nextballup_api.security.jwt import create_playback_token, decode_token
from nextballup_api.security.rate_limit import enforce_rate_limit
from nextballup_api.storage import (
    PresignedUpload,
    StorageFailureError,
    StoragePresigner,
    get_storage_presigner,
    storage_complete_multipart,
    storage_head_object,
    storage_key_for_video,
    storage_presign_get,
    storage_presign_upload,
)
from nextballup_api.tenant import (
    clear_join_invite_context,
    clear_tenant_context,
    set_tenant_context,
)
from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import (
    ProcessingJobStage,
    ProcessingJobStatus,
    UserRole,
    VideoStatus,
)
from nextballup_core.errors import (
    AppError,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationFailedError,
)
from nextballup_core.schemas.video import (
    CompleteUploadRequest,
    CompleteUploadResponse,
    CreateUploadRequest,
    CreateUploadResponse,
    PlaybackVerifyRequest,
    PlaybackVerifyResponse,
    PresignedPart,
    ProcessingStageStatus,
    RequeueProcessingRequest,
    RequeueProcessingResponse,
    VideoDetailResponse,
    VideoStatusResponse,
)
from nextballup_core.settings import Settings
from nextballup_db.models.game import Game
from nextballup_db.models.user import User
from nextballup_db.models.video import ProcessingJob, Video

router = APIRouter(prefix="/videos", tags=["videos"])

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


@dataclass(frozen=True)
class _PlaybackArtifact:
    """Resolved playback URL + matching token for a single GET response."""

    url: str
    token: str
    fmt: str
    expires_at: datetime


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
    playback: _PlaybackArtifact | None = None,
) -> VideoDetailResponse:
    processing_summary: dict[str, str] = {stage.value: "pending" for stage in _PIPELINE_STAGES}
    for job in jobs:
        processing_summary[job.stage.value] = job.status.value
    return VideoDetailResponse(
        id=video.id,
        game_id=video.game_id,
        status=video.status,
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
        thumbnail_url=video.thumbnail_url,
        playback_url=playback.url if playback else None,
        playback_token=playback.token if playback else None,
        playback_format=playback.fmt if playback else None,
        token_expires_at=playback.expires_at if playback else None,
        processing=processing_summary,
        created_at=video.created_at,
    )


def _video_status(video: Video, jobs: list[ProcessingJob]) -> VideoStatusResponse:
    stages: dict[str, ProcessingStageStatus] = {
        stage.value: ProcessingStageStatus(status="pending") for stage in _PIPELINE_STAGES
    }
    active_stage: str | None = None
    progress_pct = 0
    for job in jobs:
        stages[job.stage.value] = ProcessingStageStatus(
            status=job.status.value,
            progress_percent=job.progress_percent,
            completed_at=job.completed_at,
            error_message=job.error_message,
        )
        if job.status is ProcessingJobStatus.RUNNING:
            active_stage = job.stage.value
            progress_pct = job.progress_percent
    return VideoStatusResponse(
        status=video.status,
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


async def _record_upload_failure(
    session: AsyncSession,
    *,
    request: Request,
    current_user: User,
    video_id: uuid.UUID | None,
    team_id: uuid.UUID | None,
    extra: dict[str, object],
) -> None:
    await write_audit(
        session,
        action=AuditAction.VIDEO_UPLOAD_FAILED,
        request=request,
        actor_user_id=current_user.id,
        actor_email=current_user.email,
        resource_type="video",
        resource_id=video_id,
        team_id=team_id,
        extra=extra,
    )
    await session.commit()


def _uploaded_object_size(metadata: dict[str, object]) -> int | None:
    content_length = metadata.get("ContentLength")
    if isinstance(content_length, int):
        return content_length
    return None


async def _verify_uploaded_object(
    *,
    session: AsyncSession,
    request: Request,
    current_user: User,
    video: Video,
    presigner: StoragePresigner,
) -> None:
    storage_key = video.storage_key_raw
    if not storage_key:
        await _record_upload_failure(
            session,
            request=request,
            current_user=current_user,
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
            current_user=current_user,
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
        await _record_upload_failure(
            session,
            request=request,
            current_user=current_user,
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

    await enforce_rate_limit(
        request=request,
        settings=settings,
        scope="video_upload",
        subject=str(current_user.id),
        max_attempts=settings.video_upload_rate_limit_attempts,
        window_seconds=settings.video_upload_rate_limit_window_seconds,
    )

    presigner = _require_storage(storage)

    video = Video(
        game_id=game.id,
        team_id=game.team_id,
        uploaded_by=current_user.id,
        filename=payload.filename,
        status=VideoStatus.PENDING_UPLOAD,
        file_size_bytes=payload.file_size_bytes,
        content_type=payload.content_type,
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
        )
    except StorageFailureError as exc:
        failed_video_id = video.id
        failed_game_id = game.id
        failed_team_id = game.team_id
        await session.rollback()
        await _record_upload_failure(
            session,
            request=request,
            current_user=current_user,
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
            "upload_method": presigned.method.value,
        },
    )
    await session.commit()
    await session.refresh(video)

    return _build_presigned_response(video_id=video.id, presigned=presigned, expires_at=expires_at)


# ---- POST /videos/{video_id}/complete -------------------------------------


@router.post(
    "/{video_id}/complete",
    response_model=CompleteUploadResponse,
)
async def complete_upload(
    video_id: uuid.UUID,
    payload: CompleteUploadRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    storage: StoragePresigner | None = Depends(get_storage),
) -> CompleteUploadResponse:
    await clear_join_invite_context(session)
    await clear_tenant_context(session)
    session.sync_session.expunge_all()
    video = await _load_video_for_update(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)
    await set_tenant_context(session, video.team_id)
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
            )
        except StorageFailureError as exc:
            failed_video_id = video.id
            failed_team_id = video.team_id
            failed_upload_id = video.upload_id
            await session.rollback()
            await _record_upload_failure(
                session,
                request=request,
                current_user=current_user,
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
            )
        except StorageFailureError as exc:
            failed_video_id = video.id
            failed_team_id = video.team_id
            await session.rollback()
            await _record_upload_failure(
                session,
                request=request,
                current_user=current_user,
                video_id=failed_video_id,
                team_id=failed_team_id,
                extra={"reason": "storage_head_failed"},
            )
            raise exc

    video.status = VideoStatus.QUEUED
    video.checksum_sha256 = payload.checksum_sha256
    video.upload_id = None
    video.upload_expires_at = None

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
        extra={"checksum_sha256": payload.checksum_sha256},
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
    await session.refresh(video)
    await session.refresh(transcode_job)

    return CompleteUploadResponse(
        id=video.id,
        status=video.status,
        estimated_processing_minutes=_ESTIMATED_PROCESSING_MINUTES,
        job_id=transcode_job.id,
    )


# ---- GET /videos/{video_id} -----------------------------------------------


@router.get("/{video_id}", response_model=VideoDetailResponse)
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
    return _video_detail(video, jobs, playback=playback)


# ---- GET /videos/{video_id}/status ----------------------------------------


@router.get("/{video_id}/status", response_model=VideoStatusResponse)
async def get_video_status(
    video_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
    return _video_status(video, jobs)


# ---- POST /videos/{video_id}/playback/verify ------------------------------


@router.post("/{video_id}/playback/verify", response_model=PlaybackVerifyResponse)
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


# ---- POST /videos/{video_id}/processing/requeue ---------------------------


@router.post("/{video_id}/processing/requeue", response_model=RequeueProcessingResponse)
async def requeue_processing(
    video_id: uuid.UUID,
    payload: RequeueProcessingRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RequeueProcessingResponse:
    """Admin-only: return a terminal processing job (FAILED/COMPLETED) to
    PENDING so the beat dispatcher re-runs it.

    Scope:
    * Restricted to `UserRole.ADMIN` — requeue bypasses the tenant coach
      controls that normally govern video state, so platform admins are the
      only principals trusted to kick stuck jobs.
    * Only jobs in a terminal status can be requeued. Trying to requeue a
      PENDING/RUNNING job is rejected (409) so operators don't race a live
      worker.
    * The video row's status is not rewritten here — the worker will drive
      it forward on the next claim.
    """
    require_user_role(current_user, UserRole.ADMIN)

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

    video = await _load_video(session, video_id)
    if video is None:
        raise NotFoundError("Video not found", code=ErrorCode.VIDEO_NOT_FOUND)

    await set_tenant_context(session, video.team_id)

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
    if job.status not in {ProcessingJobStatus.FAILED, ProcessingJobStatus.COMPLETED}:
        raise ConflictError(
            "Processing job is still active — cannot requeue",
            code=ErrorCode.PROCESSING_JOB_NOT_REQUEUABLE,
            details={"current_status": job.status.value},
        )

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
    await session.refresh(job)

    return RequeueProcessingResponse(
        job_id=job.id,
        stage=stage.value,
        status=job.status.value,
        requeued_at=requeued_at,
    )
