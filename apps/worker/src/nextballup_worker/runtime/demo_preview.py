from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.demo_preview import (
    mark_demo_preview_completed,
    mark_demo_preview_failed,
    mark_demo_preview_queued,
    mark_demo_preview_running,
    render_demo_preview_artifact,
    resolve_demo_preview_state,
)
from nextballup_core.errors import AppError, ConflictError, ServiceUnavailableError
from nextballup_core.settings import Settings, get_settings
from nextballup_db.models.video import Video
from nextballup_worker.audit import write_worker_audit
from nextballup_worker.tenant import (
    clear_worker_context,
    set_worker_context,
    set_worker_operator_role,
)

if TYPE_CHECKING:
    from nextballup_api.storage import StoragePresigner


@dataclass
class DemoPreviewResult:
    video_id: uuid.UUID
    status: str
    retryable: bool
    error_code: str | None = None
    error_message: str | None = None


async def _load_video(session: AsyncSession, video_id: uuid.UUID) -> Video | None:
    result = await session.execute(
        select(Video).where(Video.id == video_id).execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


async def finalize_demo_preview_failure(
    session: AsyncSession,
    *,
    video_id: uuid.UUID,
    celery_task_id: str,
    settings: Settings | None = None,
    error_code: str,
    error_message: str,
) -> DemoPreviewResult:
    resolved = settings or get_settings()
    await set_worker_operator_role(session)
    video = await _load_video(session, video_id)
    if video is None:
        await clear_worker_context(session)
        mark_demo_preview_failed(
            settings=resolved,
            video_id=video_id,
            task_id=celery_task_id,
            error_message=error_message,
        )
        return DemoPreviewResult(
            video_id=video_id,
            status="failed",
            retryable=False,
            error_code=ErrorCode.VIDEO_NOT_FOUND,
            error_message=error_message,
        )

    await set_worker_context(session, team_id=video.team_id)
    try:
        mark_demo_preview_failed(
            settings=resolved,
            video_id=video.id,
            task_id=celery_task_id,
            error_message=error_message,
        )
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_DEMO_PREVIEW_FAILED,
            team_id=video.team_id,
            resource_type="video",
            resource_id=video.id,
            request_id=celery_task_id,
            extra={"error_code": error_code, "error_message": error_message},
        )
        await session.commit()
        return DemoPreviewResult(
            video_id=video.id,
            status="failed",
            retryable=False,
            error_code=error_code,
            error_message=error_message,
        )
    finally:
        await clear_worker_context(session)


async def execute_demo_preview(
    session: AsyncSession,
    *,
    video_id: uuid.UUID,
    celery_task_id: str,
    settings: Settings | None = None,
    storage: StoragePresigner | None = None,
) -> DemoPreviewResult:
    resolved = settings or get_settings()
    resolved_storage = storage
    if resolved_storage is None:
        from nextballup_api.storage import get_storage_presigner

        resolved_storage = get_storage_presigner(resolved)
    if resolved_storage is None:
        mark_demo_preview_failed(
            settings=resolved,
            video_id=video_id,
            task_id=celery_task_id,
            error_message="Object storage is not configured for local demo previews",
        )
        return DemoPreviewResult(
            video_id=video_id,
            status="failed",
            retryable=False,
            error_code=ErrorCode.STORAGE_NOT_CONFIGURED,
            error_message="Object storage is not configured for local demo previews",
        )

    await set_worker_operator_role(session)
    video = await _load_video(session, video_id)
    if video is None:
        await clear_worker_context(session)
        mark_demo_preview_failed(
            settings=resolved,
            video_id=video_id,
            task_id=celery_task_id,
            error_message="Video not found for local demo preview generation",
        )
        return DemoPreviewResult(
            video_id=video_id,
            status="failed",
            retryable=False,
            error_code=ErrorCode.VIDEO_NOT_FOUND,
            error_message="Video not found for local demo preview generation",
        )

    await set_worker_context(session, team_id=video.team_id)
    try:
        artifact = await render_demo_preview_artifact(
            video_id=video.id,
            video_status=video.status,
            storage_key_mezzanine=video.storage_key_mezzanine,
            download_file=lambda key, destination: _storage_download_file(
                presigner=resolved_storage,
                key=key,
                destination=destination,
            ),
            settings=resolved,
            on_started=lambda: (
                mark_demo_preview_running(
                    settings=resolved,
                    video_id=video.id,
                    task_id=celery_task_id,
                ),
                None,
            )[-1],
        )
    except ConflictError as exc:
        if exc.code == ErrorCode.DEMO_PREVIEW_MACHINE_BUSY:
            mark_demo_preview_queued(
                settings=resolved,
                video_id=video.id,
                task_id=celery_task_id,
                generated_at=None,
            )
            await session.commit()
            return DemoPreviewResult(
                video_id=video.id,
                status="failed",
                retryable=True,
                error_code=exc.code,
                error_message=exc.message,
            )
        if exc.code == ErrorCode.DEMO_PREVIEW_IN_PROGRESS:
            current_state = resolve_demo_preview_state(settings=resolved, video_id=video.id)
            await session.commit()
            return DemoPreviewResult(
                video_id=video.id,
                status="skipped",
                retryable=False,
                error_code=exc.code,
                error_message=current_state.error_message,
            )
        mark_demo_preview_failed(
            settings=resolved,
            video_id=video.id,
            task_id=celery_task_id,
            error_message=exc.message,
        )
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_DEMO_PREVIEW_FAILED,
            team_id=video.team_id,
            resource_type="video",
            resource_id=video.id,
            request_id=celery_task_id,
            extra={"error_code": exc.code, "error_message": exc.message},
        )
        await session.commit()
        return DemoPreviewResult(
            video_id=video.id,
            status="failed",
            retryable=False,
            error_code=exc.code,
            error_message=exc.message,
        )
    except (ServiceUnavailableError, AppError) as exc:
        mark_demo_preview_failed(
            settings=resolved,
            video_id=video.id,
            task_id=celery_task_id,
            error_message=exc.message,
        )
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_DEMO_PREVIEW_FAILED,
            team_id=video.team_id,
            resource_type="video",
            resource_id=video.id,
            request_id=celery_task_id,
            extra={"error_code": exc.code, "error_message": exc.message},
        )
        await session.commit()
        return DemoPreviewResult(
            video_id=video.id,
            status="failed",
            retryable=False,
            error_code=exc.code,
            error_message=exc.message,
        )
    except Exception as exc:
        message = str(exc)[:1000] or "Local demo preview worker failed unexpectedly"
        mark_demo_preview_failed(
            settings=resolved,
            video_id=video.id,
            task_id=celery_task_id,
            error_message=message,
        )
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_DEMO_PREVIEW_FAILED,
            team_id=video.team_id,
            resource_type="video",
            resource_id=video.id,
            request_id=celery_task_id,
            extra={"error_code": ErrorCode.INTERNAL_ERROR, "error_message": message},
        )
        await session.commit()
        return DemoPreviewResult(
            video_id=video.id,
            status="failed",
            retryable=False,
            error_code=ErrorCode.INTERNAL_ERROR,
            error_message=message,
        )
    else:
        mark_demo_preview_completed(
            settings=resolved,
            video_id=video.id,
            task_id=celery_task_id,
            generated_at=artifact.generated_at,
        )
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_DEMO_PREVIEW_GENERATED,
            team_id=video.team_id,
            resource_type="video",
            resource_id=video.id,
            request_id=celery_task_id,
            extra={
                "generated_at": artifact.generated_at.isoformat(),
                "sample_fps": resolved.cv_demo_sample_fps,
            },
        )
        await session.commit()
        return DemoPreviewResult(
            video_id=video.id,
            status="completed",
            retryable=False,
        )
    finally:
        await clear_worker_context(session)


async def _storage_download_file(
    *,
    presigner: StoragePresigner,
    key: str,
    destination: str,
) -> None:
    from nextballup_api.storage import storage_download_file

    await storage_download_file(presigner, key=key, destination=destination)
