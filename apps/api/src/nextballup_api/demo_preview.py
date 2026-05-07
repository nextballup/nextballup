from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from nextballup_api.storage import StoragePresigner
from nextballup_core.constants import ErrorCode
from nextballup_core.demo_preview import (
    acquire_demo_preview_queue_lock,
    demo_preview_url_path,
    mark_demo_preview_completed,
    mark_demo_preview_failed,
    mark_demo_preview_queued,
    release_demo_preview_lock,
    resolve_demo_preview,
    resolve_demo_preview_state,
    validate_demo_preview_runtime,
)
from nextballup_core.enums import VideoStatus
from nextballup_core.errors import ConflictError, ServiceUnavailableError
from nextballup_core.schemas.video import DemoPreviewStatusValue, GenerateDemoPreviewResponse
from nextballup_core.settings import Settings
from nextballup_db.models.video import Video

__all__ = [
    "QueuedDemoPreviewResult",
    "queue_demo_preview_request",
    "resolve_demo_preview",
    "resolve_demo_preview_state",
]

logger = logging.getLogger(__name__)
_DEMO_PREVIEW_STATUSES = {"idle", "queued", "running", "completed", "failed"}


def _demo_preview_status(value: str) -> DemoPreviewStatusValue:
    if value not in _DEMO_PREVIEW_STATUSES:
        return "idle"
    return cast("DemoPreviewStatusValue", value)


@dataclass(frozen=True)
class QueuedDemoPreviewResult:
    response: GenerateDemoPreviewResponse
    enqueued: bool
    task_id: str | None = None


def _require_demo_preview_enabled(settings: Settings) -> None:
    if settings.local_demo_preview_enabled():
        return
    raise ServiceUnavailableError(
        "Local demo preview is not enabled for this environment",
        code=ErrorCode.DEMO_PREVIEW_NOT_ENABLED,
    )


def _response_for_current_state(
    *,
    settings: Settings,
    video_id: uuid.UUID,
) -> GenerateDemoPreviewResponse:
    current_state = resolve_demo_preview_state(settings=settings, video_id=video_id)
    current_artifact = resolve_demo_preview(settings=settings, video_id=video_id)
    return GenerateDemoPreviewResponse(
        status=current_state.status,
        preview_url=current_artifact.url_path if current_artifact else None,
        generated_at=current_artifact.generated_at if current_artifact else None,
    )


def _response_for_video_preview_state(video: Video) -> GenerateDemoPreviewResponse:
    status = getattr(video, "demo_preview_status", "idle")
    preview_storage_key = getattr(video, "demo_preview_storage_key", None)
    preview_url = demo_preview_url_path(video.id) if preview_storage_key else None
    return GenerateDemoPreviewResponse(
        status=_demo_preview_status(status),
        preview_url=preview_url,
        generated_at=getattr(video, "demo_preview_generated_at", None) if preview_url else None,
    )


def queue_demo_preview_request(
    *,
    video: Video,
    settings: Settings,
    storage: StoragePresigner,
) -> QueuedDemoPreviewResult:
    _require_demo_preview_enabled(settings)
    validate_demo_preview_runtime(
        settings,
        startup=False,
        require_inference_runtime=settings.cv_demo_preview_enabled,
    )
    if video.status is not VideoStatus.PROCESSED:
        raise ConflictError(
            "Video must finish processing before a demo preview can be generated",
            code=ErrorCode.INVALID_VIDEO_STATE,
            details={"current_status": video.status.value},
        )
    if not video.storage_key_mezzanine:
        raise ConflictError(
            "Processed video is missing its mezzanine artifact",
            code=ErrorCode.INVALID_VIDEO_STATE,
        )
    if not storage.is_configured():
        raise ServiceUnavailableError(
            "Object storage is not configured",
            code=ErrorCode.STORAGE_NOT_CONFIGURED,
        )
    if getattr(video, "demo_preview_status", "idle") in {"queued", "running"}:
        return QueuedDemoPreviewResult(
            response=_response_for_video_preview_state(video),
            enqueued=False,
            task_id=getattr(video, "demo_preview_task_id", None),
        )

    queue_lock = None
    try:
        queue_lock = acquire_demo_preview_queue_lock(settings=settings, video_id=video.id)
    except ConflictError:
        response = _response_for_current_state(settings=settings, video_id=video.id)
        if response.status in {"queued", "running"}:
            return QueuedDemoPreviewResult(response=response, enqueued=False)
        raise ServiceUnavailableError(
            "Local demo preview queue is temporarily busy",
            code=ErrorCode.DEMO_PREVIEW_IN_PROGRESS,
        ) from None

    try:
        current_state = resolve_demo_preview_state(settings=settings, video_id=video.id)
        current_artifact = resolve_demo_preview(settings=settings, video_id=video.id)
        if current_state.status in {"queued", "running"}:
            return QueuedDemoPreviewResult(
                response=GenerateDemoPreviewResponse(
                    status=current_state.status,
                    preview_url=current_artifact.url_path if current_artifact else None,
                    generated_at=current_artifact.generated_at if current_artifact else None,
                ),
                enqueued=False,
            )

        queued_state = mark_demo_preview_queued(
            settings=settings,
            video_id=video.id,
            task_id=None,
            generated_at=current_artifact.generated_at if current_artifact else None,
            requested_at=datetime.now(tz=UTC),
        )
        try:
            task_id = _enqueue_demo_preview_task(video_id=video.id, settings=settings)
        except Exception as exc:
            logger.exception(
                "Failed to enqueue local demo preview task", extra={"video_id": str(video.id)}
            )
            if current_artifact is not None:
                mark_demo_preview_completed(
                    settings=settings,
                    video_id=video.id,
                    task_id=queued_state.task_id,
                    generated_at=current_artifact.generated_at,
                )
            else:
                mark_demo_preview_failed(
                    settings=settings,
                    video_id=video.id,
                    task_id=queued_state.task_id,
                    error_message="Unable to enqueue the local demo preview worker task",
                )
            raise ServiceUnavailableError(
                "Local demo preview worker is unavailable",
                code=ErrorCode.DEMO_PREVIEW_FAILED,
            ) from exc

        mark_demo_preview_queued(
            settings=settings,
            video_id=video.id,
            task_id=task_id,
            generated_at=current_artifact.generated_at if current_artifact else None,
            requested_at=queued_state.requested_at,
        )
        return QueuedDemoPreviewResult(
            response=GenerateDemoPreviewResponse(
                status="queued",
                preview_url=current_artifact.url_path if current_artifact else None,
                generated_at=current_artifact.generated_at if current_artifact else None,
            ),
            enqueued=True,
            task_id=task_id,
        )
    finally:
        release_demo_preview_lock(queue_lock)


def _enqueue_demo_preview_task(*, video_id: uuid.UUID, settings: Settings) -> str:
    from nextballup_worker.celery_app import celery_app

    result = celery_app.send_task(
        "nextballup_worker.tasks.run_demo_preview",
        args=[str(video_id)],
        queue=settings.celery_demo_preview_queue,
    )
    return str(result.id)
