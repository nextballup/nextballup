from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from nextballup_api.storage import (
    StorageFailureError,
    StoragePresigner,
    get_storage_presigner,
    normalize_etag,
    storage_head_object,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import ProcessingJobStage, VideoStatus
from nextballup_core.settings import Settings, get_settings
from nextballup_db.models.video import ProcessingJob, Video
from nextballup_worker.audit import write_worker_audit
from nextballup_worker.errors import (
    PermanentProcessingError,
    TransientProcessingError,
)
from nextballup_worker.runtime.jobs import (
    claim_job,
    complete_job,
    fail_job,
    release_job_for_retry,
    set_video_status,
    touch_heartbeat,
)
from nextballup_worker.runtime.media import BrowserMezzanineArtifact, create_browser_mezzanine
from nextballup_worker.tenant import (
    clear_worker_context,
    set_worker_context,
    set_worker_operator_role,
)


@dataclass
class TranscodeResult:
    job_id: uuid.UUID
    status: str
    retryable: bool
    error_code: str | None = None


async def _load_video(session: AsyncSession, video_id: uuid.UUID) -> Video | None:
    result = await session.execute(
        select(Video).where(Video.id == video_id).execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def _verify_storage(
    *,
    video: Video,
    presigner: StoragePresigner,
) -> dict[str, Any]:
    """Re-verify the uploaded object at processing time.

    The API already performed this check at /complete; re-verifying here is
    defense-in-depth against post-complete tampering and catches objects that
    were silently deleted between the API call and the task running.
    """
    key = video.storage_key_raw
    if not key:
        raise PermanentProcessingError(
            "Video has no storage key",
            code=ErrorCode.PROCESSING_OBJECT_MISSING,
        )
    try:
        metadata = await storage_head_object(presigner, key=key)
    except StorageFailureError as exc:
        raise TransientProcessingError(
            "Storage head_object failed",
            code=ErrorCode.PROCESSING_STORAGE_FAILURE,
        ) from exc

    if metadata is None:
        raise PermanentProcessingError(
            "Uploaded object was not found in storage",
            code=ErrorCode.PROCESSING_OBJECT_MISSING,
        )

    actual_size_value = metadata.get("ContentLength")
    actual_size = actual_size_value if isinstance(actual_size_value, int) else None
    if (
        actual_size is not None
        and video.file_size_bytes is not None
        and actual_size != video.file_size_bytes
    ):
        raise PermanentProcessingError(
            "Uploaded object size does not match the declared file size",
            code=ErrorCode.PROCESSING_SIZE_MISMATCH,
        )
    raw_etag = metadata.get("ETag")
    normalized = normalize_etag(raw_etag if isinstance(raw_etag, str) else None)
    # An ETag without a "-N" suffix is the object's MD5; keep it explicit so
    # a future cryptographic verification phase can use it directly without
    # having to re-detect single-vs-multipart from the raw response.
    is_md5_etag = (
        normalized is not None
        and "-" not in normalized
        and len(normalized) == 32
        and all(c in "0123456789abcdefABCDEF" for c in normalized)
    )
    return {
        "storage_key": key,
        "content_length": actual_size,
        "storage_etag": normalized,
        "etag_is_md5": is_md5_etag,
        # The audit trail records that a client-provided checksum was present
        # without re-hashing the object — cryptographic verification is a
        # deferred residual risk (see Phase 5 summary).
        "client_checksum_present": bool(video.checksum_sha256),
    }


async def _materialize_outputs(
    session: AsyncSession,
    *,
    video: Video,
    artifact: BrowserMezzanineArtifact,
) -> dict[str, str | None]:
    """Persist the browser-safe playback artifact metadata onto the video row."""
    await session.execute(
        update(Video)
        .where(Video.id == video.id)
        .values(
            storage_key_mezzanine=artifact.mezzanine_key,
            storage_etag=artifact.storage_etag,
            duration_seconds=artifact.duration_seconds,
            width=artifact.width,
            height=artifact.height,
            fps=artifact.fps,
            codec=artifact.codec,
        )
    )
    await session.commit()
    return {
        "mezzanine": artifact.mezzanine_key,
        "hls": None,
        "thumbnail": None,
    }


async def execute_transcode(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    celery_task_id: str | None = None,
    request_id: str | None = None,
    settings: Settings | None = None,
    storage: StoragePresigner | None = None,
) -> TranscodeResult:
    """Execute the browser-safe mezzanine `transcode` stage for a processing job.

    The function is idempotent against duplicate Celery delivery: the claim is
    atomic and a non-PENDING job returns success-with-skip.
    """
    resolved_settings = settings or get_settings()

    # 1. Admin-role context is enough for the initial lookup — the FORCE-RLS
    # select policies from migration 0005 admit admin-role requests without a
    # team_id GUC. Once we discover the row, we bind the full tenant context.
    await set_worker_operator_role(session)
    job_result = await session.execute(
        select(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .execution_options(populate_existing=True)
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        await clear_worker_context(session)
        return TranscodeResult(
            job_id=job_id, status="skipped", retryable=False, error_code="not_found"
        )
    if job.stage is not ProcessingJobStage.TRANSCODE:
        await clear_worker_context(session)
        raise PermanentProcessingError(
            f"Unexpected stage: {job.stage.value}",
            code=ErrorCode.PROCESSING_JOB_TERMINAL,
        )

    # 2. Bind the true tenant context and claim the job atomically.
    await set_worker_context(session, team_id=job.team_id)
    claimed = await claim_job(session, job_id=job.id, celery_task_id=celery_task_id)
    if claimed is None:
        # Job is already RUNNING or terminal — duplicate delivery / stray retry.
        await clear_worker_context(session)
        return TranscodeResult(job_id=job.id, status="skipped", retryable=False, error_code=None)

    await set_video_status(
        session,
        video_id=claimed.video_id,
        new_status=VideoStatus.PROCESSING,
        allowed_from={VideoStatus.QUEUED, VideoStatus.PROCESSING},
    )
    await write_worker_audit(
        session,
        action=AuditAction.VIDEO_PROCESSING_STARTED,
        team_id=claimed.team_id,
        resource_type="processing_job",
        resource_id=claimed.id,
        request_id=request_id,
        extra={
            "video_id": str(claimed.video_id),
            "stage": claimed.stage.value,
            "celery_task_id": celery_task_id,
        },
    )
    await session.commit()

    # 3. Do the work. Re-verify the uploaded object, then materialize a
    # browser-safe playback mezzanine without exposing the raw upload.
    try:
        await touch_heartbeat(session, job_id=claimed.id, progress_percent=10)
        video = await _load_video(session, claimed.video_id)
        if video is None:
            raise PermanentProcessingError(
                "Video row vanished during processing",
                code=ErrorCode.VIDEO_NOT_FOUND,
            )
        resolved_storage = storage
        if resolved_storage is None:
            resolved_storage = get_storage_presigner(resolved_settings)
        if resolved_storage is None:
            raise PermanentProcessingError(
                "Object storage is not configured",
                code=ErrorCode.STORAGE_NOT_CONFIGURED,
            )
        verification = await _verify_storage(video=video, presigner=resolved_storage)
        await touch_heartbeat(session, job_id=claimed.id, progress_percent=50)
        artifact = await create_browser_mezzanine(
            video=video,
            presigner=resolved_storage,
            settings=resolved_settings,
        )
        outputs = await _materialize_outputs(
            session,
            video=video,
            artifact=artifact,
        )
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_OUTPUT_MATERIALIZED,
            team_id=claimed.team_id,
            resource_type="video",
            resource_id=claimed.video_id,
            request_id=request_id,
            extra={
                "stage": claimed.stage.value,
                "outputs": outputs,
                "transcode_mode": artifact.transcoder,
                "metadata_stripped": True,
                "output_size_bytes": artifact.output_size_bytes,
            },
        )
        await session.commit()
        await touch_heartbeat(session, job_id=claimed.id, progress_percent=80)

    except (PermanentProcessingError, TransientProcessingError) as exc:
        retryable = isinstance(exc, TransientProcessingError)
        return await _handle_task_failure(
            session,
            job=claimed,
            exc=exc,
            request_id=request_id,
            retryable=retryable,
        )
    except Exception as exc:  # unexpected → treat as transient with retry
        return await _handle_task_failure(
            session,
            job=claimed,
            exc=exc,
            request_id=request_id,
            retryable=True,
        )

    # 4. Complete.
    await complete_job(
        session,
        job_id=claimed.id,
        result_metadata={
            "verification": verification,
            "outputs": outputs,
            "transcode_mode": artifact.transcoder,
            "metadata_stripped": True,
            "output_size_bytes": artifact.output_size_bytes,
        },
    )
    # In Phase 4 the pipeline has a single stage — completing it implies the
    # video is fully processed. Downstream phases will only flip to PROCESSED
    # when the last stage completes.
    await set_video_status(
        session,
        video_id=claimed.video_id,
        new_status=VideoStatus.PROCESSED,
        allowed_from={VideoStatus.PROCESSING},
    )
    await write_worker_audit(
        session,
        action=AuditAction.VIDEO_PROCESSING_COMPLETED,
        team_id=claimed.team_id,
        resource_type="processing_job",
        resource_id=claimed.id,
        request_id=request_id,
        extra={
            "video_id": str(claimed.video_id),
            "stage": claimed.stage.value,
            "verification": verification,
        },
    )
    await session.commit()
    await clear_worker_context(session)
    return TranscodeResult(job_id=claimed.id, status="completed", retryable=False, error_code=None)


async def _handle_task_failure(
    session: AsyncSession,
    *,
    job: ProcessingJob,
    exc: BaseException,
    request_id: str | None,
    retryable: bool,
) -> TranscodeResult:
    error_code = getattr(exc, "code", None) or ErrorCode.INTERNAL_ERROR
    error_message = str(exc)[:2000]
    if retryable:
        # Transient: return the job to PENDING so Celery's own scheduled retry
        # can claim it again, but keep celery_task_id populated so beat does not
        # dispatch a second copy.
        attempt = int((job.result_metadata or {}).get("attempt", 0)) + 1
        merged_meta = dict(job.result_metadata or {})
        merged_meta["attempt"] = attempt
        merged_meta["last_error"] = error_message
        merged_meta["last_error_code"] = error_code
        await release_job_for_retry(
            session,
            job_id=job.id,
            result_metadata=merged_meta,
        )
        await set_video_status(
            session,
            video_id=job.video_id,
            new_status=VideoStatus.QUEUED,
            allowed_from={VideoStatus.PROCESSING, VideoStatus.QUEUED},
        )
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_PROCESSING_FAILED,
            team_id=job.team_id,
            resource_type="processing_job",
            resource_id=job.id,
            request_id=request_id,
            extra={
                "video_id": str(job.video_id),
                "stage": job.stage.value,
                "error_code": error_code,
                "error_message": error_message,
                "retryable": True,
                "attempt": attempt,
            },
        )
        await session.commit()
        await clear_worker_context(session)
        return TranscodeResult(
            job_id=job.id, status="failed", retryable=True, error_code=error_code
        )

    # Permanent: mark terminal FAILED + flip video.
    await fail_job(
        session,
        job_id=job.id,
        error_code=error_code,
        error_message=error_message,
    )
    await set_video_status(
        session,
        video_id=job.video_id,
        new_status=VideoStatus.FAILED,
        allowed_from={VideoStatus.PROCESSING, VideoStatus.QUEUED},
    )
    await write_worker_audit(
        session,
        action=AuditAction.VIDEO_PROCESSING_FAILED,
        team_id=job.team_id,
        resource_type="processing_job",
        resource_id=job.id,
        request_id=request_id,
        extra={
            "video_id": str(job.video_id),
            "stage": job.stage.value,
            "error_code": error_code,
            "error_message": error_message,
        },
    )
    await session.commit()
    await clear_worker_context(session)
    return TranscodeResult(job_id=job.id, status="failed", retryable=False, error_code=error_code)
