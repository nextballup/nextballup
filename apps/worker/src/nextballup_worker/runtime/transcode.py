from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from nextballup_api.billing import resolve_team_plan
from nextballup_api.storage import (
    StorageFailureError,
    StoragePresigner,
    get_storage_presigner,
    normalize_etag,
    storage_delete_object,
    storage_head_object,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import ProcessingJobStage, ProcessingJobStatus, VideoStatus
from nextballup_core.observability import (
    WORKER_STORAGE_BYTES_UPLOADED_TOTAL,
    WORKER_TRANSCODE_SECONDS,
)
from nextballup_core.settings import Settings, get_settings
from nextballup_db.models.video import ProcessingJob, Video
from nextballup_worker.audit import originating_user_extra, write_worker_audit
from nextballup_worker.errors import (
    PermanentProcessingError,
    TransientProcessingError,
)
from nextballup_worker.runtime.cv_pipeline import queue_next_stage_if_enabled
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

logger = logging.getLogger(__name__)

_STORAGE_DIAGNOSTIC_KEYS = frozenset(
    {
        "operation",
        "provider_error_code",
        "http_status_code",
        "request_id",
        "exception_type",
        "storage_key_sha256",
    }
)


def _metadata_checksum_sha256(metadata: dict[str, Any]) -> str | None:
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


async def _claimed_job_still_active(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    celery_task_id: str | None,
    lock: bool = False,
) -> bool:
    stmt = select(ProcessingJob).where(
        ProcessingJob.id == job_id,
        ProcessingJob.status == ProcessingJobStatus.RUNNING,
    )
    if celery_task_id is not None:
        stmt = stmt.where(ProcessingJob.celery_task_id == celery_task_id)
    if lock:
        stmt = stmt.with_for_update()
    result = await session.execute(stmt.execution_options(populate_existing=True))
    return result.scalar_one_or_none() is not None


async def _delete_transient_output_best_effort(
    presigner: StoragePresigner,
    artifact: BrowserMezzanineArtifact,
) -> None:
    with suppress(Exception):
        await storage_delete_object(presigner, key=artifact.mezzanine_key)


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
        storage_failure = _sanitized_storage_failure_details(exc)
        logger.warning(
            "Transcode storage verification failed: operation=%s provider_error_code=%s "
            "http_status_code=%s exception_type=%s storage_key_sha256=%s",
            storage_failure.get("operation", "unknown"),
            storage_failure.get("provider_error_code", "unknown"),
            storage_failure.get("http_status_code", "unknown"),
            storage_failure.get("exception_type", "unknown"),
            storage_failure.get("storage_key_sha256", "unknown"),
        )
        raise TransientProcessingError(
            "Storage head_object failed",
            code=ErrorCode.PROCESSING_STORAGE_FAILURE,
            details={"storage_failure": storage_failure} if storage_failure else None,
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
    actual_checksum = _metadata_checksum_sha256(metadata)
    if video.checksum_sha256 and actual_checksum and actual_checksum != video.checksum_sha256:
        raise PermanentProcessingError(
            "Uploaded object checksum does not match the recorded SHA-256 digest",
            code=ErrorCode.PROCESSING_CHECKSUM_MISMATCH,
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
    settings: Settings,
    job_id: uuid.UUID,
    celery_task_id: str | None,
) -> dict[str, str | None] | None:
    """Persist the browser-safe playback artifact metadata onto the video row."""
    await set_worker_context(session, team_id=video.team_id)
    if not await _claimed_job_still_active(
        session,
        job_id=job_id,
        celery_task_id=celery_task_id,
        lock=True,
    ):
        await session.commit()
        return None
    plan_ctx = await resolve_team_plan(session, team_id=video.team_id)
    retention_days = (
        plan_ctx.raw_video_retention_days
        if plan_ctx is not None and plan_ctx.raw_video_retention_days is not None
        else settings.raw_video_retention_days
    )
    raw_retention_expires_at = video.raw_retention_expires_at or datetime.now(tz=UTC) + timedelta(
        days=retention_days
    )
    result = await session.execute(
        update(Video)
        .where(Video.id == video.id, Video.status == VideoStatus.PROCESSING)
        .values(
            storage_key_mezzanine=artifact.mezzanine_key,
            storage_etag=artifact.storage_etag,
            storage_output_sha256=artifact.output_sha256,
            duration_seconds=artifact.duration_seconds,
            width=artifact.width,
            height=artifact.height,
            fps=artifact.fps,
            codec=artifact.codec,
            raw_retention_expires_at=raw_retention_expires_at,
        )
        .returning(Video.id)
    )
    if result.scalar_one_or_none() is None:
        await session.commit()
        return None
    await session.commit()
    if artifact.output_size_bytes is not None:
        WORKER_STORAGE_BYTES_UPLOADED_TOTAL.inc(artifact.output_size_bytes)
    return {
        "mezzanine": artifact.mezzanine_key,
        "hls": None,
        "thumbnail": None,
    }


async def _await_with_heartbeat(
    awaitable: Any,
    *,
    session: AsyncSession,
    job_id: uuid.UUID,
    team_id: uuid.UUID,
    celery_task_id: str | None,
    settings: Settings,
    progress_percent: int,
) -> Any:
    """Await a long-running stage while keeping the DB heartbeat fresh."""
    interval = max(1, settings.worker_heartbeat_interval_seconds)
    task = asyncio.create_task(awaitable)
    try:
        while not task.done():
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=interval)
            except TimeoutError:
                await set_worker_context(session, team_id=team_id)
                await touch_heartbeat(
                    session,
                    job_id=job_id,
                    celery_task_id=celery_task_id,
                    progress_percent=progress_percent,
                )
        return await task
    finally:
        if not task.done():
            task.cancel()


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

    await set_worker_context(session, team_id=claimed.team_id)
    await set_video_status(
        session,
        video_id=claimed.video_id,
        new_status=VideoStatus.PROCESSING,
        allowed_from={VideoStatus.QUEUED, VideoStatus.PROCESSING},
    )
    origin_extra = await originating_user_extra(
        session,
        video_id=claimed.video_id,
        team_id=claimed.team_id,
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
            **origin_extra,
        },
    )
    await session.commit()

    # 3. Do the work. Re-verify the uploaded object, then materialize a
    # browser-safe playback mezzanine without exposing the raw upload.
    try:
        await set_worker_context(session, team_id=claimed.team_id)
        await touch_heartbeat(
            session,
            job_id=claimed.id,
            celery_task_id=celery_task_id,
            progress_percent=10,
        )
        await set_worker_context(session, team_id=claimed.team_id)
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
        await set_worker_context(session, team_id=claimed.team_id)
        await touch_heartbeat(
            session,
            job_id=claimed.id,
            celery_task_id=celery_task_id,
            progress_percent=50,
        )
        transcode_started = time.perf_counter()
        artifact = await _await_with_heartbeat(
            create_browser_mezzanine(
                video=video,
                presigner=resolved_storage,
                settings=resolved_settings,
            ),
            session=session,
            job_id=claimed.id,
            team_id=claimed.team_id,
            celery_task_id=celery_task_id,
            settings=resolved_settings,
            progress_percent=50,
        )
        WORKER_TRANSCODE_SECONDS.observe(time.perf_counter() - transcode_started)
        outputs = await _materialize_outputs(
            session,
            video=video,
            artifact=artifact,
            settings=resolved_settings,
            job_id=claimed.id,
            celery_task_id=celery_task_id,
        )
        if outputs is None:
            await _delete_transient_output_best_effort(resolved_storage, artifact)
            await clear_worker_context(session)
            return TranscodeResult(
                job_id=claimed.id,
                status="skipped",
                retryable=False,
                error_code=ErrorCode.PROCESSING_CANCELLED,
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
                "output_sha256": artifact.output_sha256,
                "output_size_bytes": artifact.output_size_bytes,
                **origin_extra,
            },
        )
        await session.commit()
        await set_worker_context(session, team_id=claimed.team_id)
        await touch_heartbeat(
            session,
            job_id=claimed.id,
            celery_task_id=celery_task_id,
            progress_percent=80,
        )

    except (PermanentProcessingError, TransientProcessingError) as exc:
        retryable = isinstance(exc, TransientProcessingError)
        return await _handle_task_failure(
            session,
            job=claimed,
            exc=exc,
            request_id=request_id,
            retryable=retryable,
            celery_task_id=celery_task_id,
        )
    except Exception as exc:  # unexpected → treat as transient with retry
        return await _handle_task_failure(
            session,
            job=claimed,
            exc=exc,
            request_id=request_id,
            retryable=True,
            celery_task_id=celery_task_id,
        )

    # 4. Complete.
    await set_worker_context(session, team_id=claimed.team_id)
    completed_job = await complete_job(
        session,
        job_id=claimed.id,
        celery_task_id=celery_task_id,
        result_metadata={
            "verification": verification,
            "outputs": outputs,
            "transcode_mode": artifact.transcoder,
            "metadata_stripped": True,
            "output_sha256": artifact.output_sha256,
            "output_size_bytes": artifact.output_size_bytes,
        },
    )
    if completed_job is None:
        await clear_worker_context(session)
        return TranscodeResult(
            job_id=claimed.id,
            status="skipped",
            retryable=False,
            error_code=ErrorCode.PROCESSING_CANCELLED,
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
            "output_sha256": artifact.output_sha256,
            **origin_extra,
        },
    )
    video = await _load_video(session, claimed.video_id)
    if video is not None:
        await queue_next_stage_if_enabled(
            session,
            video=video,
            completed_stage=ProcessingJobStage.TRANSCODE,
            settings=resolved_settings,
            request_id=request_id,
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
    celery_task_id: str | None,
) -> TranscodeResult:
    await set_worker_context(session, team_id=job.team_id)
    if not await _claimed_job_still_active(
        session,
        job_id=job.id,
        celery_task_id=celery_task_id,
    ):
        await clear_worker_context(session)
        return TranscodeResult(
            job_id=job.id,
            status="skipped",
            retryable=False,
            error_code=ErrorCode.PROCESSING_CANCELLED,
        )
    error_code = getattr(exc, "code", None) or ErrorCode.INTERNAL_ERROR
    error_message = str(exc)[:2000]
    error_details = getattr(exc, "details", None)
    origin_extra = await originating_user_extra(
        session,
        video_id=job.video_id,
        team_id=job.team_id,
    )
    if retryable:
        # Transient: return the job to PENDING so Celery's own scheduled retry
        # can claim it again, but keep celery_task_id populated so beat does not
        # dispatch a second copy.
        attempt = int((job.result_metadata or {}).get("attempt", 0)) + 1
        merged_meta = dict(job.result_metadata or {})
        merged_meta["attempt"] = attempt
        merged_meta["last_error"] = error_message
        merged_meta["last_error_code"] = error_code
        if isinstance(error_details, dict):
            merged_meta["last_error_details"] = error_details
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
        extra = {
            "video_id": str(job.video_id),
            "stage": job.stage.value,
            "error_code": error_code,
            "error_message": error_message,
            "retryable": True,
            "attempt": attempt,
            **origin_extra,
        }
        if isinstance(error_details, dict):
            extra.update(error_details)
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_PROCESSING_FAILED,
            team_id=job.team_id,
            resource_type="processing_job",
            resource_id=job.id,
            request_id=request_id,
            extra=extra,
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
    extra = {
        "video_id": str(job.video_id),
        "stage": job.stage.value,
        "error_code": error_code,
        "error_message": error_message,
        **origin_extra,
    }
    if isinstance(error_details, dict):
        extra.update(error_details)
    await write_worker_audit(
        session,
        action=AuditAction.VIDEO_PROCESSING_FAILED,
        team_id=job.team_id,
        resource_type="processing_job",
        resource_id=job.id,
        request_id=request_id,
        extra=extra,
    )
    await session.commit()
    await clear_worker_context(session)
    return TranscodeResult(job_id=job.id, status="failed", retryable=False, error_code=error_code)


def _sanitized_storage_failure_details(exc: StorageFailureError) -> dict[str, Any]:
    details = getattr(exc, "details", None)
    if not isinstance(details, dict):
        return {}
    sanitized: dict[str, Any] = {}
    for key, value in details.items():
        if key not in _STORAGE_DIAGNOSTIC_KEYS:
            continue
        if isinstance(value, str):
            sanitized[key] = value[:256]
        elif isinstance(value, int):
            sanitized[key] = value
    return sanitized
