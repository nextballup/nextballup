from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from nextballup_api.storage import (
    StoragePresigner,
    get_storage_presigner,
    storage_abort_multipart,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import ProcessingJobStatus, VideoStatus
from nextballup_core.settings import Settings, get_settings
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
    threshold = now_utc() - timedelta(seconds=resolved.worker_stale_heartbeat_seconds)

    # Operator role lets us discover rows across all tenants; we bind the full
    # tenant context before each per-row update so the WITH CHECK policies pass.
    await set_worker_operator_role(session)
    rows = await session.execute(
        select(ProcessingJob).where(
            ProcessingJob.status == ProcessingJobStatus.RUNNING,
            ProcessingJob.heartbeat_at.is_not(None),
            ProcessingJob.heartbeat_at < threshold,
        )
    )
    recovered: list[str] = []
    for job in rows.scalars():
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

    Best-effort aborts the multipart upload when upload_id is populated — the
    storage layer logs and swallows any abort error so a single stuck bucket
    doesn't block the cleanup pass.
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
    for video in rows.scalars():
        await set_worker_context(session, team_id=video.team_id)
        if (
            resolved_storage is not None
            and video.upload_id is not None
            and video.storage_key_raw is not None
        ):
            await storage_abort_multipart(
                resolved_storage,
                key=video.storage_key_raw,
                upload_id=video.upload_id,
            )
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
    return CleanupReport(recovered_stale_jobs=recovered, abandoned_uploads=abandoned)
