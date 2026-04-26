from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.enums import ProcessingJobStatus, VideoStatus
from nextballup_core.observability import WORKER_JOBS_FAILED_TOTAL
from nextballup_db.models.video import ProcessingJob, Video


async def claim_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    celery_task_id: str | None,
) -> ProcessingJob | None:
    """Atomically transition a processing_job from PENDING to RUNNING while
    recording the Celery task id.

    Returns the updated ProcessingJob row on success. Returns None if the job is
    already RUNNING/terminal or has been deleted. This is the worker's
    duplicate-delivery safety boundary.
    """
    result = await session.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.status == ProcessingJobStatus.PENDING,
        )
        .values(
            status=ProcessingJobStatus.RUNNING,
            celery_task_id=celery_task_id,
            started_at=func.coalesce(ProcessingJob.started_at, func.now()),
            heartbeat_at=func.now(),
        )
        .returning(ProcessingJob)
        .execution_options(populate_existing=True)
    )
    job = result.scalar_one_or_none()
    await session.commit()
    return job


async def touch_heartbeat(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    progress_percent: int | None = None,
) -> None:
    """Bump heartbeat_at (and optionally progress_percent) for a running job."""
    values: dict[str, Any] = {"heartbeat_at": func.now()}
    if progress_percent is not None:
        values["progress_percent"] = progress_percent
    await session.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.status == ProcessingJobStatus.RUNNING,
        )
        .values(**values)
    )
    await session.commit()


async def complete_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    result_metadata: dict[str, Any] | None = None,
) -> ProcessingJob | None:
    """Transition a RUNNING job to COMPLETED. Does not touch video state — the
    caller decides whether this stage being complete means the pipeline is done
    (Phase 4 only has one stage; downstream phases will fan-out)."""
    result = await session.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.status == ProcessingJobStatus.RUNNING,
        )
        .values(
            status=ProcessingJobStatus.COMPLETED,
            progress_percent=100,
            completed_at=func.now(),
            error_message=None,
            result_metadata=result_metadata,
        )
        .returning(ProcessingJob)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def fail_job(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    error_code: str,
    error_message: str,
    result_metadata: dict[str, Any] | None = None,
) -> ProcessingJob | None:
    """Mark a job terminally FAILED.

    Tolerates jobs already in FAILED state (idempotent — on_failure may fire
    after an in-task mark). Does not touch video state; caller owns that
    transition so the sequence of audit events is correct.
    """
    merged_meta: dict[str, Any] = dict(result_metadata or {})
    merged_meta["error_code"] = error_code
    result = await session.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.status.in_([ProcessingJobStatus.PENDING, ProcessingJobStatus.RUNNING]),
        )
        .values(
            status=ProcessingJobStatus.FAILED,
            completed_at=func.now(),
            error_message=f"[{error_code}] {error_message}"[:2000],
            result_metadata=merged_meta,
        )
        .returning(ProcessingJob)
        .execution_options(populate_existing=True)
    )
    job = result.scalar_one_or_none()
    if job is not None:
        WORKER_JOBS_FAILED_TOTAL.labels(stage=job.stage.value, error_code=error_code).inc()
    return job


async def release_job_for_retry(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    result_metadata: dict[str, Any] | None = None,
) -> ProcessingJob | None:
    """Return a RUNNING job to PENDING for a scheduled retry.

    The existing celery_task_id is intentionally preserved so the dispatcher
    does not mistake the row for a never-enqueued job while Celery's own retry
    delivery is pending.
    """
    result = await session.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.status == ProcessingJobStatus.RUNNING,
        )
        .values(
            status=ProcessingJobStatus.PENDING,
            progress_percent=0,
            error_message=None,
            result_metadata=result_metadata,
            heartbeat_at=func.now(),
        )
        .returning(ProcessingJob)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def set_video_status(
    session: AsyncSession,
    *,
    video_id: uuid.UUID,
    new_status: VideoStatus,
    allowed_from: set[VideoStatus] | None = None,
) -> Video | None:
    """Transition a video row's status, guarding against out-of-order updates.

    When `allowed_from` is supplied, the UPDATE is conditional on the current
    status — this is how we avoid a late worker clobbering a manual operator
    override. The returned row reflects the post-update state, or None if the
    row was not in an allowed starting state.
    """
    stmt = update(Video).where(Video.id == video_id)
    if allowed_from:
        stmt = stmt.where(Video.status.in_(list(allowed_from)))
    stmt = stmt.values(status=new_status).returning(Video).execution_options(populate_existing=True)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> ProcessingJob | None:
    """Fresh read, bypassing any stale identity-map state. Worker flows often
    re-read after an UPDATE to observe the committed row."""
    result = await session.execute(
        select(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


def now_utc() -> datetime:
    return datetime.now(tz=UTC)
