from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass, field
from datetime import timedelta

from nextballup_api.storage import (
    StoragePresigner,
    get_storage_presigner,
    storage_abort_multipart,
    storage_delete_object,
)
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.demo_preview import cleanup_expired_demo_previews
from nextballup_core.enums import ProcessingJobStatus, VideoStatus
from nextballup_core.observability import WORKER_JOBS_STALE_RECOVERED_TOTAL
from nextballup_core.settings import Settings, get_settings
from nextballup_db.models.email_verification import EmailVerificationToken
from nextballup_db.models.video import ProcessingJob, Video
from nextballup_worker.audit import write_worker_audit
from nextballup_worker.runtime.jobs import fail_job, now_utc, set_video_status
from nextballup_worker.tenant import (
    clear_worker_context,
    set_worker_context,
    set_worker_operator_role,
)


@dataclass
class CleanupReport:
    recovered_stale_jobs: list[str] = field(default_factory=list)
    abandoned_uploads: list[str] = field(default_factory=list)
    expired_raw_videos: list[str] = field(default_factory=list)
    retried_raw_video_deletes: list[str] = field(default_factory=list)
    expired_demo_previews: list[str] = field(default_factory=list)
    pruned_email_verification_tokens: int = 0
    pruned_csp_reports: int = 0


async def recover_stale_jobs(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    request_id: str | None = None,
) -> list[str]:
    """Terminally fail RUNNING jobs whose heartbeat has gone stale.

    Stale = `heartbeat_at` older than `worker_stale_heartbeat_seconds` — the
    upstream worker process crashed or was evicted. Recovery marks the job
    FAILED, flips the video to FAILED, and writes an audit row so ops can
    correlate with dashboards. We *do not* auto-retry: a failed placeholder
    is cheap to re-run manually, and silent revival makes it easy to miss
    systemic issues.
    """
    resolved = settings or get_settings()
    stale_seconds = max(1, int(resolved.worker_stale_heartbeat_seconds))

    # Operator role lets us discover rows across all tenants; we bind the full
    # tenant context before each per-row update so the WITH CHECK policies pass.
    await set_worker_operator_role(session)
    rows = await session.execute(
        select(ProcessingJob)
        .where(
            ProcessingJob.status == ProcessingJobStatus.RUNNING,
            ProcessingJob.heartbeat_at.is_not(None),
            ProcessingJob.heartbeat_at < func.now() - text(f"interval '{stale_seconds} seconds'"),
        )
        .with_for_update(skip_locked=True)
    )
    recovered: list[str] = []
    for job in list(rows.scalars()):
        await set_worker_context(session, team_id=job.team_id)
        merged_meta = dict(job.result_metadata or {})
        merged_meta["stale_since"] = job.heartbeat_at.isoformat() if job.heartbeat_at else None
        await fail_job(
            session,
            job_id=job.id,
            error_code=ErrorCode.PROCESSING_STALE_RECOVERED,
            error_message="heartbeat stale",
            result_metadata=merged_meta,
        )
        await set_video_status(
            session,
            video_id=job.video_id,
            new_status=VideoStatus.FAILED,
            allowed_from={VideoStatus.PROCESSING, VideoStatus.QUEUED},
        )
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_PROCESSING_RECOVERED_STALE,
            team_id=job.team_id,
            resource_type="processing_job",
            resource_id=job.id,
            request_id=request_id,
            extra={
                "video_id": str(job.video_id),
                "stage": job.stage.value,
                "stale_since": (job.heartbeat_at.isoformat() if job.heartbeat_at else None),
            },
        )
        await session.commit()
        WORKER_JOBS_STALE_RECOVERED_TOTAL.inc()
        recovered.append(str(job.id))
    await clear_worker_context(session)
    return recovered


async def cleanup_abandoned_uploads(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    storage: StoragePresigner | None = None,
    request_id: str | None = None,
) -> list[str]:
    """Mark pending_upload videos whose window has elapsed as failed.

    Best-effort aborts multipart uploads and deletes any single-PUT object
    that made it to storage before the client abandoned /complete. Storage
    cleanup failures are deliberately non-fatal; the DB state still moves to
    FAILED so operators can reconcile leaked objects from audit trails.
    """
    resolved = settings or get_settings()
    grace = timedelta(seconds=resolved.worker_abandoned_upload_grace_seconds)
    cutoff = now_utc() - grace
    resolved_storage = storage
    if resolved_storage is None and resolved.storage_configured():
        resolved_storage = get_storage_presigner(resolved)

    await set_worker_operator_role(session)
    rows = await session.execute(
        select(Video).where(
            Video.status == VideoStatus.PENDING_UPLOAD,
            Video.upload_expires_at.is_not(None),
            Video.upload_expires_at < cutoff,
        )
    )
    abandoned: list[str] = []
    for video in list(rows.scalars()):
        await set_worker_context(session, team_id=video.team_id)
        if (
            resolved_storage is not None
            and video.upload_id is not None
            and video.storage_key_raw is not None
        ):
            with suppress(Exception):
                await storage_abort_multipart(
                    resolved_storage,
                    key=video.storage_key_raw,
                    upload_id=video.upload_id,
                )
        elif resolved_storage is not None and video.storage_key_raw is not None:
            with suppress(Exception):
                await storage_delete_object(resolved_storage, key=video.storage_key_raw)
        await session.execute(
            update(Video)
            .where(Video.id == video.id, Video.status == VideoStatus.PENDING_UPLOAD)
            .values(
                status=VideoStatus.FAILED,
                upload_id=None,
                upload_expires_at=None,
            )
        )
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_UPLOAD_ABANDONED,
            team_id=video.team_id,
            resource_type="video",
            resource_id=video.id,
            request_id=request_id,
            extra={
                "upload_expires_at": (
                    video.upload_expires_at.isoformat() if video.upload_expires_at else None
                ),
                "had_upload_id": video.upload_id is not None,
            },
        )
        await session.commit()
        abandoned.append(str(video.id))
    await clear_worker_context(session)
    return abandoned


async def cleanup_expired_raw_videos(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    storage: StoragePresigner | None = None,
    request_id: str | None = None,
) -> list[str]:
    """Delete raw source objects whose retention window has expired.

    Only terminal videos are eligible. Queued/processing videos may still need
    the source object for a retry, so they are intentionally skipped even if
    the policy timestamp has passed.
    """
    resolved = settings or get_settings()
    resolved_storage = storage
    if resolved_storage is None and resolved.storage_configured():
        resolved_storage = get_storage_presigner(resolved)
    if resolved_storage is None:
        return []

    now = now_utc()
    await set_worker_operator_role(session)
    rows = await session.execute(
        select(Video)
        .where(
            Video.storage_key_raw.is_not(None),
            Video.raw_storage_deleted_at.is_(None),
            Video.raw_retention_expires_at.is_not(None),
            Video.raw_retention_expires_at <= now,
            Video.status.in_([VideoStatus.PROCESSED, VideoStatus.FAILED]),
        )
        .with_for_update(skip_locked=True)
        .limit(resolved.raw_video_retention_cleanup_batch_size)
    )
    deleted: list[str] = []
    for video in list(rows.scalars()):
        await set_worker_context(session, team_id=video.team_id)
        raw_key = video.storage_key_raw
        await session.execute(
            update(Video)
            .where(Video.id == video.id, Video.raw_storage_deleted_at.is_(None))
            .values(
                raw_delete_requested_at=now,
                raw_delete_failed_at=None,
                raw_delete_reason="retention_expired",
            )
        )
        await session.commit()
        try:
            await storage_delete_object(resolved_storage, key=raw_key or "")
        except Exception:
            await set_worker_context(session, team_id=video.team_id)
            await write_worker_audit(
                session,
                action=AuditAction.VIDEO_RAW_OBJECT_DELETE_FAILED,
                team_id=video.team_id,
                resource_type="video",
                resource_id=video.id,
                request_id=request_id,
                extra={
                    "raw_retention_expires_at": (
                        video.raw_retention_expires_at.isoformat()
                        if video.raw_retention_expires_at
                        else None
                    ),
                    "delete_reason": "retention_expired",
                },
            )
            await session.execute(
                update(Video)
                .where(Video.id == video.id, Video.raw_storage_deleted_at.is_(None))
                .values(raw_delete_failed_at=now)
            )
            await session.commit()
            continue
        await set_worker_context(session, team_id=video.team_id)
        await session.execute(
            update(Video)
            .where(Video.id == video.id, Video.raw_storage_deleted_at.is_(None))
            .values(
                storage_key_raw=None,
                storage_etag=None,
                raw_storage_deleted_at=now,
                raw_deleted_at=now,
                raw_delete_failed_at=None,
            )
        )
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_RAW_OBJECT_DELETED,
            team_id=video.team_id,
            resource_type="video",
            resource_id=video.id,
            request_id=request_id,
            extra={
                "storage_key_raw": raw_key,
                "raw_retention_expires_at": (
                    video.raw_retention_expires_at.isoformat()
                    if video.raw_retention_expires_at
                    else None
                ),
                "delete_reason": "retention_expired",
            },
        )
        await session.commit()
        deleted.append(str(video.id))
    await clear_worker_context(session)
    return deleted


async def retry_raw_video_storage_deletes(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    storage: StoragePresigner | None = None,
    request_id: str | None = None,
) -> list[str]:
    """Retry raw-object storage deletes that were DB-marked but not cleared."""
    resolved = settings or get_settings()
    resolved_storage = storage
    if resolved_storage is None and resolved.storage_configured():
        resolved_storage = get_storage_presigner(resolved)
    if resolved_storage is None:
        return []

    await set_worker_operator_role(session)
    rows = await session.execute(
        select(Video)
        .where(
            Video.storage_key_raw.is_not(None),
            Video.raw_delete_requested_at.is_not(None),
            Video.raw_storage_deleted_at.is_(None),
        )
        .with_for_update(skip_locked=True)
        .limit(resolved.raw_video_retention_cleanup_batch_size)
    )
    deleted: list[str] = []
    for video in list(rows.scalars()):
        await set_worker_context(session, team_id=video.team_id)
        raw_key = video.storage_key_raw
        try:
            await storage_delete_object(resolved_storage, key=raw_key or "")
        except Exception:
            await write_worker_audit(
                session,
                action=AuditAction.VIDEO_RAW_OBJECT_DELETE_FAILED,
                team_id=video.team_id,
                resource_type="video",
                resource_id=video.id,
                request_id=request_id,
                extra={
                    "raw_deleted_at": (
                        video.raw_deleted_at.isoformat() if video.raw_deleted_at else None
                    ),
                    "raw_delete_requested_at": (
                        video.raw_delete_requested_at.isoformat()
                        if video.raw_delete_requested_at
                        else None
                    ),
                    "delete_reason": video.raw_delete_reason,
                },
            )
            await session.execute(
                update(Video)
                .where(Video.id == video.id, Video.raw_storage_deleted_at.is_(None))
                .values(raw_delete_failed_at=now_utc())
            )
            await session.commit()
            continue
        completed_at = now_utc()
        await session.execute(
            update(Video)
            .where(Video.id == video.id, Video.raw_storage_deleted_at.is_(None))
            .values(
                storage_key_raw=None,
                storage_etag=None,
                raw_storage_deleted_at=completed_at,
                raw_deleted_at=completed_at,
                raw_delete_failed_at=None,
            )
        )
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_RAW_OBJECT_DELETED,
            team_id=video.team_id,
            resource_type="video",
            resource_id=video.id,
            request_id=request_id,
            extra={
                "storage_key_raw": raw_key,
                "raw_deleted_at": (
                    video.raw_deleted_at.isoformat() if video.raw_deleted_at else None
                ),
                "raw_delete_requested_at": (
                    video.raw_delete_requested_at.isoformat()
                    if video.raw_delete_requested_at
                    else None
                ),
                "delete_reason": video.raw_delete_reason,
                "source": "retry",
            },
        )
        await session.commit()
        deleted.append(str(video.id))
    await clear_worker_context(session)
    return deleted


async def cleanup_email_verification_tokens(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    request_id: str | None = None,
) -> int:
    _ = settings
    now = now_utc()
    await set_worker_operator_role(session)
    stale_ids = (
        select(EmailVerificationToken.id)
        .where(
            EmailVerificationToken.used_at.is_not(None) | (EmailVerificationToken.expires_at < now),
        )
        .limit(1000)
    )
    rows = await session.execute(
        delete(EmailVerificationToken)
        .where(EmailVerificationToken.id.in_(stale_ids))
        .returning(EmailVerificationToken.id)
    )
    pruned = len(rows.scalars().all())
    if pruned:
        await write_worker_audit(
            session,
            action=AuditAction.USER_EMAIL_VERIFICATION_TOKENS_PRUNED,
            team_id=None,
            resource_type="email_verification_token",
            request_id=request_id,
            extra={
                "count": pruned,
                "criterion": "used_or_expired",
            },
        )
    await session.commit()
    return pruned


async def cleanup_expired_csp_reports(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    request_id: str | None = None,
) -> int:
    _ = request_id
    resolved = settings or get_settings()
    cutoff = now_utc() - timedelta(days=resolved.csp_report_retention_days)
    await set_worker_operator_role(session)
    pruned = int(
        await session.scalar(
            text("SELECT nextballup_prune_csp_reports(:cutoff)").bindparams(cutoff=cutoff)
        )
        or 0
    )
    await session.commit()
    return pruned


async def run_full_cleanup(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    storage: StoragePresigner | None = None,
    request_id: str | None = None,
) -> CleanupReport:
    """One-shot cleanup entry point for the maintenance beat tick."""
    recovered = await recover_stale_jobs(session, settings=settings, request_id=request_id)
    abandoned = await cleanup_abandoned_uploads(
        session, settings=settings, storage=storage, request_id=request_id
    )
    expired_raw_videos = await cleanup_expired_raw_videos(
        session, settings=settings, storage=storage, request_id=request_id
    )
    retried_raw_video_deletes = await retry_raw_video_storage_deletes(
        session, settings=settings, storage=storage, request_id=request_id
    )
    expired_demo_previews = cleanup_expired_demo_previews(settings=settings or get_settings())
    pruned_email_tokens = await cleanup_email_verification_tokens(
        session, settings=settings, request_id=request_id
    )
    pruned_csp_reports = await cleanup_expired_csp_reports(
        session, settings=settings, request_id=request_id
    )
    return CleanupReport(
        recovered_stale_jobs=recovered,
        abandoned_uploads=abandoned,
        expired_raw_videos=expired_raw_videos,
        retried_raw_video_deletes=retried_raw_video_deletes,
        expired_demo_previews=expired_demo_previews,
        pruned_email_verification_tokens=pruned_email_tokens,
        pruned_csp_reports=pruned_csp_reports,
    )
