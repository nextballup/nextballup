from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid

from celery import Task
from celery.exceptions import MaxRetriesExceededError
from celery.signals import beat_init, worker_init

from nextballup_core.constants import ErrorCode
from nextballup_core.demo_preview import validate_demo_preview_runtime
from nextballup_core.enums import ProcessingJobStage
from nextballup_core.errors import AppError
from nextballup_core.observability import WORKER_TASK_SECONDS
from nextballup_core.settings import get_settings
from nextballup_worker.audit import write_worker_audit
from nextballup_worker.celery_app import celery_app
from nextballup_worker.errors import (
    PermanentProcessingError,
    TransientProcessingError,
)
from nextballup_worker.routing import queue_for_stage
from nextballup_worker.runtime import (
    dispatch_pending_jobs,
    execute_cv_stage,
    execute_demo_preview,
    execute_transcode,
    fail_job,
    finalize_demo_preview_failure,
    set_video_status,
)
from nextballup_worker.runtime.cleanup import retry_raw_video_storage_deletes, run_full_cleanup
from nextballup_worker.runtime.jobs import get_job
from nextballup_worker.session import worker_session
from nextballup_worker.tenant import (
    clear_worker_context,
    set_worker_context,
    set_worker_operator_role,
)

logger = logging.getLogger(__name__)

_TRANSCODE_NAME = "nextballup_worker.tasks.run_transcode"
_CV_STAGE_NAME = "nextballup_worker.tasks.run_cv_stage"
_DEMO_PREVIEW_NAME = "nextballup_worker.tasks.run_demo_preview"
_DISPATCH_NAME = "nextballup_worker.tasks.dispatch_pending_jobs_task"
_CLEANUP_NAME = "nextballup_worker.tasks.run_cleanup_task"
_RAW_DELETE_RETRY_NAME = "nextballup_worker.tasks.retry_raw_video_storage_deletes_task"


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


class DemoPreviewTask(Task):
    """Terminalize preview state when retries exhaust or an unexpected
    exception escapes the worker task.
    """

    name = _DEMO_PREVIEW_NAME

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
        raw_video_id = args[0]
        try:
            video_id = uuid.UUID(str(raw_video_id))
        except (ValueError, TypeError):
            logger.warning("demo preview on_failure received malformed video id %r", raw_video_id)
            return
        if isinstance(exc, MaxRetriesExceededError):
            error_code = ErrorCode.DEMO_PREVIEW_MACHINE_BUSY
            error_message = (
                "Local demo preview retries exhausted while waiting for machine capacity"
            )
        else:
            error_code = _demo_preview_error_code_from_exception(exc)
            error_message = str(exc)[:2000] or "Local demo preview worker failed unexpectedly"
        asyncio.run(
            _terminal_demo_preview_failure(
                video_id=video_id,
                task_id=task_id,
                error_code=error_code,
                error_message=error_message,
            )
        )


class CVStageTask(TranscodeTask):
    """Same terminal failure handling as transcode, without FFmpeg-specific work."""

    name = _CV_STAGE_NAME

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
            logger.warning("cv on_failure received malformed job id %r", raw_job_id)
            return
        asyncio.run(
            _terminal_failure(
                job_id=job_id,
                task_id=task_id,
                error_code=_error_code_from_exception(exc),
                error_message=str(exc)[:2000],
                fail_video=False,
            )
        )


async def _terminal_failure(
    *,
    job_id: uuid.UUID,
    task_id: str,
    error_code: str,
    error_message: str,
    fail_video: bool = True,
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
        if updated is not None and fail_video:
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


async def _terminal_demo_preview_failure(
    *,
    video_id: uuid.UUID,
    task_id: str,
    error_code: str,
    error_message: str,
) -> dict[str, str]:
    settings = get_settings()
    async with worker_session(settings) as session:
        terminal = await finalize_demo_preview_failure(
            session,
            video_id=video_id,
            celery_task_id=task_id,
            settings=settings,
            error_code=error_code,
            error_message=error_message,
        )
        return {
            "video_id": str(terminal.video_id),
            "status": terminal.status,
            "retryable": "false",
            "error_code": terminal.error_code or "",
            "error_message": terminal.error_message or "",
        }


def _error_code_from_exception(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if isinstance(code, str):
        return code
    if isinstance(exc, MaxRetriesExceededError):
        return ErrorCode.PROCESSING_STORAGE_FAILURE
    if isinstance(exc, PermanentProcessingError):
        return exc.code
    return ErrorCode.INTERNAL_ERROR


def _demo_preview_error_code_from_exception(exc: BaseException) -> str:
    if isinstance(exc, AppError):
        return exc.code
    return ErrorCode.DEMO_PREVIEW_FAILED


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
    if settings.app_env in ("staging", "production"):
        failures: list[str] = []
        if not settings.worker_media_container_sandbox_enabled:
            failures.append(
                "WORKER_MEDIA_CONTAINER_SANDBOX_ENABLED must be true in staging/production"
            )
        if settings.worker_media_max_cpu_seconds <= 0:
            failures.append("WORKER_MEDIA_MAX_CPU_SECONDS must be non-zero in staging/production")
        if settings.worker_media_max_output_bytes <= 0:
            failures.append("WORKER_MEDIA_MAX_OUTPUT_BYTES must be non-zero in staging/production")
        if failures:
            raise RuntimeError(" / ".join(failures))
    validate_demo_preview_runtime(settings, startup=True)


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
    started = time.perf_counter()

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
    WORKER_TASK_SECONDS.labels(task="transcode", outcome=outcome["status"]).observe(
        time.perf_counter() - started
    )
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


@celery_app.task(bind=True, base=DemoPreviewTask, name=_DEMO_PREVIEW_NAME)
def run_demo_preview(self: DemoPreviewTask, video_id: str) -> dict[str, str]:
    settings = get_settings()
    video_uuid = uuid.UUID(video_id)
    started = time.perf_counter()

    async def _run() -> dict[str, str]:
        async with worker_session(settings) as session:
            result = await execute_demo_preview(
                session,
                video_id=video_uuid,
                celery_task_id=str(self.request.id),
                settings=settings,
            )
            return {
                "video_id": str(result.video_id),
                "status": result.status,
                "retryable": "true" if result.retryable else "false",
                "error_code": result.error_code or "",
                "error_message": result.error_message or "",
            }

    outcome = asyncio.run(_run())
    WORKER_TASK_SECONDS.labels(task="demo_preview", outcome=outcome["status"]).observe(
        time.perf_counter() - started
    )
    if outcome["retryable"] == "true":
        retry_countdown = max(5, settings.worker_job_retry_backoff_seconds)
        retry_limit = max(
            1,
            math.ceil((settings.cv_demo_timeout_seconds + 60) / retry_countdown),
        )
        try:
            raise self.retry(
                countdown=retry_countdown,
                max_retries=retry_limit,
            )
        except MaxRetriesExceededError:
            return asyncio.run(
                _terminal_demo_preview_failure(
                    video_id=video_uuid,
                    task_id=str(self.request.id),
                    error_code=ErrorCode.DEMO_PREVIEW_MACHINE_BUSY,
                    error_message=(
                        "Local demo preview retries exhausted while waiting for machine capacity"
                    ),
                )
            )
    return outcome


@celery_app.task(bind=True, base=CVStageTask, name=_CV_STAGE_NAME)
def run_cv_stage(self: CVStageTask, job_id: str) -> dict[str, str]:
    settings = get_settings()
    job_uuid = uuid.UUID(job_id)
    started = time.perf_counter()

    async def _run() -> dict[str, str]:
        async with worker_session(settings) as session:
            result = await execute_cv_stage(
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
    WORKER_TASK_SECONDS.labels(task="cv_stage", outcome=outcome["status"]).observe(
        time.perf_counter() - started
    )
    return outcome


@celery_app.task(name=_DISPATCH_NAME)
def dispatch_pending_jobs_task() -> list[str]:
    settings = get_settings()
    celery_app_local = celery_app  # local binding so mypy narrows once
    started = time.perf_counter()

    def _enqueue(pending_id: uuid.UUID, stage: ProcessingJobStage) -> str:
        queue = queue_for_stage(stage, settings)
        task_name = _TRANSCODE_NAME if stage is ProcessingJobStage.TRANSCODE else _CV_STAGE_NAME
        async_result = celery_app_local.send_task(
            task_name,
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

    dispatched = asyncio.run(_run())
    WORKER_TASK_SECONDS.labels(task="dispatch", outcome="completed").observe(
        time.perf_counter() - started
    )
    return dispatched


@celery_app.task(name=_CLEANUP_NAME)
def run_cleanup_task() -> dict[str, list[str] | int]:
    settings = get_settings()
    started = time.perf_counter()

    async def _run() -> dict[str, list[str] | int]:
        async with worker_session(settings) as session:
            report = await run_full_cleanup(
                session,
                settings=settings,
                request_id="beat.cleanup",
            )
            return {
                "recovered": report.recovered_stale_jobs,
                "abandoned": report.abandoned_uploads,
                "expired_raw_videos": report.expired_raw_videos,
                "retried_raw_video_deletes": report.retried_raw_video_deletes,
                "expired_demo_previews": report.expired_demo_previews,
                "pruned_email_verification_tokens": report.pruned_email_verification_tokens,
                "pruned_csp_reports": report.pruned_csp_reports,
            }

    report = asyncio.run(_run())
    WORKER_TASK_SECONDS.labels(task="cleanup", outcome="completed").observe(
        time.perf_counter() - started
    )
    return report


@celery_app.task(name=_RAW_DELETE_RETRY_NAME)
def retry_raw_video_storage_deletes_task() -> list[str]:
    settings = get_settings()
    started = time.perf_counter()

    async def _run() -> list[str]:
        async with worker_session(settings) as session:
            return await retry_raw_video_storage_deletes(
                session,
                settings=settings,
                request_id="beat.raw_delete_retry",
            )

    deleted = asyncio.run(_run())
    WORKER_TASK_SECONDS.labels(task="raw_delete_retry", outcome="completed").observe(
        time.perf_counter() - started
    )
    return deleted
