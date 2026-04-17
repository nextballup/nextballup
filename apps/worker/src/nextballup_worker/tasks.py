from __future__ import annotations

import asyncio
import logging
import uuid

from celery import Task
from celery.exceptions import MaxRetriesExceededError
from celery.signals import beat_init, worker_init

from nextballup_core.constants import ErrorCode
from nextballup_core.enums import ProcessingJobStage
from nextballup_core.settings import get_settings
from nextballup_worker.audit import write_worker_audit
from nextballup_worker.celery_app import celery_app
from nextballup_worker.errors import (
    PermanentProcessingError,
    TransientProcessingError,
)
from nextballup_worker.routing import queue_for_stage
from nextballup_worker.runtime import (
    cleanup_abandoned_uploads,
    dispatch_pending_jobs,
    execute_transcode,
    fail_job,
    recover_stale_jobs,
    set_video_status,
)
from nextballup_worker.runtime.jobs import get_job
from nextballup_worker.session import worker_session
from nextballup_worker.tenant import (
    clear_worker_context,
    set_worker_context,
    set_worker_operator_role,
)

logger = logging.getLogger(__name__)

_TRANSCODE_NAME = "nextballup_worker.tasks.run_transcode"
_DISPATCH_NAME = "nextballup_worker.tasks.dispatch_pending_jobs_task"
_CLEANUP_NAME = "nextballup_worker.tasks.run_cleanup_task"


class TranscodeTask(Task):
    """Celery Task subclass so we can hook `on_failure` for the dead-letter path.

    On terminal Celery failure (after retries exhausted, or an unrelated
    exception escapes), we persist the job row as FAILED and audit the final
    failure. The in-task code path already handles most of this; this hook is
    the belt-and-suspenders for unhandled exceptions.
    """

    name = _TRANSCODE_NAME
    autoretry_for = (TransientProcessingError,)
    # Celery expects these as class attributes for autoretry_for to fire.
    max_retries = None  # overridden per-invocation via options below
    retry_backoff = True
    retry_backoff_max = 600
    retry_jitter = True

    def on_failure(
        self,
        exc: BaseException,
        task_id: str,
        args: tuple[object, ...],
        kwargs: dict[str, object],
        einfo: object,
    ) -> None:
        if not args:
            return
        raw_job_id = args[0]
        try:
            job_id = uuid.UUID(str(raw_job_id))
        except (ValueError, TypeError):
            logger.warning("on_failure received malformed job id %r", raw_job_id)
            return
        asyncio.run(
            _terminal_failure(
                job_id=job_id,
                task_id=task_id,
                error_code=_error_code_from_exception(exc),
                error_message=str(exc)[:2000],
            )
        )


async def _terminal_failure(
    *, job_id: uuid.UUID, task_id: str, error_code: str, error_message: str
) -> None:
    async with worker_session() as session:
        await set_worker_operator_role(session)
        job = await get_job(session, job_id)
        if job is None:
            await clear_worker_context(session)
            return
        await set_worker_context(session, team_id=job.team_id)
        updated = await fail_job(
            session,
            job_id=job.id,
            error_code=error_code,
            error_message=error_message,
        )
        if updated is not None:
            from nextballup_core.enums import VideoStatus

            await set_video_status(
                session,
                video_id=job.video_id,
                new_status=VideoStatus.FAILED,
            )
        await write_worker_audit(
            session,
            action="videos.processing.failed",
            team_id=job.team_id,
            resource_type="processing_job",
            resource_id=job.id,
            request_id=task_id,
            extra={
                "video_id": str(job.video_id),
                "stage": job.stage.value,
                "error_code": error_code,
                "error_message": error_message,
                "source": "on_failure",
            },
        )
        await session.commit()
        await clear_worker_context(session)


def _error_code_from_exception(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        return code
    if isinstance(exc, MaxRetriesExceededError):
        return ErrorCode.PROCESSING_STORAGE_FAILURE
    if isinstance(exc, PermanentProcessingError):
        return exc.code
    return ErrorCode.INTERNAL_ERROR


def _ensure_runtime_broker_configured() -> None:
    settings = get_settings()
    if settings.app_env != "test" and not settings.celery_broker_url:
        raise RuntimeError(
            "CELERY_BROKER_URL must be configured before starting a worker or beat process"
        )
    # Fail before the worker ever dequeues a job if the runtime DB role is
    # misconfigured. In staging/production, `runtime_database_url()` refuses
    # to fall back to the owner role because that would weaken RLS.
    settings.runtime_database_url()


@worker_init.connect
def _validate_worker_startup(*args: object, **kwargs: object) -> None:
    _ensure_runtime_broker_configured()


@beat_init.connect
def _validate_beat_startup(*args: object, **kwargs: object) -> None:
    _ensure_runtime_broker_configured()


@celery_app.task(
    bind=True,
    base=TranscodeTask,
    name=_TRANSCODE_NAME,
)
def run_transcode(self: TranscodeTask, job_id: str) -> dict[str, str]:
    """Celery entry point. Delegates to the async runtime and translates the
    result into Celery retry semantics."""
    settings = get_settings()
    max_retries = settings.worker_job_max_retries
    job_uuid = uuid.UUID(job_id)

    async def _run() -> dict[str, str]:
        async with worker_session(settings) as session:
            result = await execute_transcode(
                session,
                job_id=job_uuid,
                celery_task_id=self.request.id,
                request_id=self.request.id,
                settings=settings,
            )
            return {
                "job_id": str(result.job_id),
                "status": result.status,
                "retryable": "true" if result.retryable else "false",
                "error_code": result.error_code or "",
            }

    outcome = asyncio.run(_run())
    if outcome["status"] == "failed" and outcome["retryable"] == "true":
        exc = TransientProcessingError(
            outcome.get("error_code") or "transient processing failure",
            code=outcome.get("error_code") or ErrorCode.PROCESSING_STORAGE_FAILURE,
        )
        raise self.retry(
            exc=exc,
            countdown=settings.worker_job_retry_backoff_seconds,
            max_retries=max_retries,
        )
    return outcome


@celery_app.task(name=_DISPATCH_NAME)
def dispatch_pending_jobs_task() -> list[str]:
    settings = get_settings()
    celery_app_local = celery_app  # local binding so mypy narrows once

    def _enqueue(pending_id: uuid.UUID, stage: ProcessingJobStage) -> str:
        queue = queue_for_stage(stage, settings)
        async_result = celery_app_local.send_task(
            _TRANSCODE_NAME,
            args=[str(pending_id)],
            queue=queue,
        )
        return str(async_result.id)

    async def _run() -> list[str]:
        async with worker_session(settings) as session:
            return await dispatch_pending_jobs(
                session,
                enqueue=_enqueue,
                settings=settings,
                request_id="beat.dispatch",
            )

    return asyncio.run(_run())


@celery_app.task(name=_CLEANUP_NAME)
def run_cleanup_task() -> dict[str, list[str]]:
    settings = get_settings()

    async def _run() -> dict[str, list[str]]:
        async with worker_session(settings) as session:
            recovered = await recover_stale_jobs(
                session, settings=settings, request_id="beat.cleanup"
            )
            abandoned = await cleanup_abandoned_uploads(
                session, settings=settings, request_id="beat.cleanup"
            )
            return {"recovered": recovered, "abandoned": abandoned}

    return asyncio.run(_run())
