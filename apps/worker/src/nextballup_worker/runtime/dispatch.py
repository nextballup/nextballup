from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction
from nextballup_core.enums import ProcessingJobStage, ProcessingJobStatus
from nextballup_core.settings import Settings, get_settings
from nextballup_db.models.video import ProcessingJob
from nextballup_worker.audit import write_worker_audit
from nextballup_worker.runtime.jobs import now_utc
from nextballup_worker.tenant import (
    clear_worker_context,
    set_worker_context,
    set_worker_operator_role,
)


async def dispatch_pending_jobs(
    session: AsyncSession,
    *,
    enqueue: Callable[[uuid.UUID, ProcessingJobStage], str],
    settings: Settings | None = None,
    request_id: str | None = None,
    limit: int = 100,
) -> list[str]:
    """Find PENDING jobs that have not yet been handed to a Celery worker and
    publish them to the appropriate queue.

    `enqueue(job_id, stage) -> celery_task_id` is injected so tests can drive
    this without a real broker. The returned task_id is persisted to the row
    so the API's `GET /videos/{id}/status` reflects the dispatch. Only jobs
    older than a short grace window are dispatched so the caller's initial
    /complete request never races with this pass.
    """
    resolved = settings or get_settings()
    grace = timedelta(seconds=min(5, max(1, resolved.worker_dispatch_interval_seconds // 2)))
    cutoff = now_utc() - grace

    await set_worker_operator_role(session)
    rows = await session.execute(
        select(ProcessingJob)
        .where(
            ProcessingJob.status == ProcessingJobStatus.PENDING,
            ProcessingJob.celery_task_id.is_(None),
            ProcessingJob.created_at <= cutoff,
        )
        .order_by(ProcessingJob.created_at)
        .limit(limit)
    )
    dispatched: list[str] = []
    for job in rows.scalars():
        try:
            task_id = enqueue(job.id, job.stage)
        except Exception:
            # Broker outage — leave the job PENDING; the next beat tick
            # retries. We intentionally do not audit per-failure because the
            # beat will try again within seconds; noisy audit rows would
            # swamp the log.
            continue
        await set_worker_context(session, team_id=job.team_id)
        result = await session.execute(
            update(ProcessingJob)
            .where(
                ProcessingJob.id == job.id,
                ProcessingJob.status == ProcessingJobStatus.PENDING,
                ProcessingJob.celery_task_id.is_(None),
            )
            .values(celery_task_id=task_id)
            .returning(ProcessingJob.id)
        )
        if result.scalar_one_or_none() is None:
            await clear_worker_context(session)
            continue
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_PROCESSING_DISPATCHED,
            team_id=job.team_id,
            resource_type="processing_job",
            resource_id=job.id,
            request_id=request_id,
            extra={
                "video_id": str(job.video_id),
                "stage": job.stage.value,
                "celery_task_id": task_id,
            },
        )
        await session.commit()
        dispatched.append(str(job.id))
    await clear_worker_context(session)
    return dispatched
