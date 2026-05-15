from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from nextballup_api.storage import storage_key_for_demo_preview, storage_upload_file
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.demo_preview import (
    DemoPreviewArtifact,
    mark_demo_preview_completed,
    mark_demo_preview_failed,
    mark_demo_preview_queued,
    mark_demo_preview_running,
    render_demo_preview_artifact,
    resolve_demo_preview_state,
)
from nextballup_core.enums import (
    ProcessingJobStage,
    ProcessingJobStatus,
    ReviewStatus,
    VideoEventType,
)
from nextballup_core.errors import AppError, ConflictError, ServiceUnavailableError
from nextballup_core.settings import Settings, get_settings
from nextballup_db.models.cv import VideoEvent
from nextballup_db.models.game import Game
from nextballup_db.models.video import ProcessingJob, Video
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


_ALPHA_CANDIDATE_SOURCE = "restricted_bard_lora_alpha_video_windows"
_ALPHA_CANDIDATE_EVENT_TYPES = {
    VideoEventType.SHOT_ATTEMPT,
    VideoEventType.SHOT_MADE,
    VideoEventType.REBOUND,
    VideoEventType.PASS,
}
_FORBIDDEN_CANDIDATE_REPORT_TOKENS = (
    "http://",
    "https://",
    "x-amz-",
    "signature=",
    "credential=",
    "bearer ",
    "cookie:",
    "s3_secret",
    "secret_key",
    "access_key",
)


class _CandidateItem(TypedDict):
    candidate_id: str
    event_type: str
    start_time_ms: int
    end_time_ms: int
    predicted_actions: list[str]


async def _load_video(session: AsyncSession, video_id: uuid.UUID) -> Video | None:
    result = await session.execute(
        select(Video).where(Video.id == video_id).execution_options(populate_existing=True)
    )
    return result.scalar_one_or_none()


def _set_preview_failed(video: Video, *, celery_task_id: str, error_message: str) -> None:
    video.demo_preview_status = "failed"
    video.demo_preview_task_id = celery_task_id
    video.demo_preview_error_message = error_message[:1000]


def _set_preview_completed(
    video: Video,
    *,
    celery_task_id: str,
    storage_key: str,
    generated_at: datetime,
) -> None:
    video.demo_preview_status = "completed"
    video.demo_preview_storage_key = storage_key
    video.demo_preview_generated_at = generated_at
    video.demo_preview_task_id = celery_task_id
    video.demo_preview_error_message = None


async def _upload_demo_preview_artifact(
    *,
    storage: StoragePresigner,
    video: Video,
    artifact_path: Path,
) -> str:
    storage_key = storage_key_for_demo_preview(
        team_id=str(video.team_id),
        video_id=str(video.id),
    )
    await storage_upload_file(
        storage,
        key=storage_key,
        source=str(artifact_path),
        content_type="video/mp4",
        metadata={
            "nbu-artifact-type": "alpha-detector-preview",
            "nbu-video-id": str(video.id),
            "nbu-team-id": str(video.team_id),
        },
    )
    return str(storage_key)


async def _load_or_create_events_job(
    session: AsyncSession,
    *,
    video: Video,
    request_id: str,
) -> ProcessingJob:
    job = await session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.video_id == video.id,
            ProcessingJob.stage == ProcessingJobStage.EVENTS,
        )
    )
    now = datetime.now(tz=UTC)
    if job is None:
        job = ProcessingJob(
            video_id=video.id,
            team_id=video.team_id,
            stage=ProcessingJobStage.EVENTS,
            status=ProcessingJobStatus.RUNNING,
            progress_percent=0,
            celery_task_id=request_id,
            started_at=now,
            heartbeat_at=now,
            result_metadata={"source": "alpha_candidate_tags"},
        )
        session.add(job)
        await session.flush()
        await write_worker_audit(
            session,
            action=AuditAction.VIDEO_PROCESSING_STARTED,
            team_id=video.team_id,
            resource_type="processing_job",
            resource_id=job.id,
            request_id=request_id,
            extra={
                "video_id": str(video.id),
                "stage": ProcessingJobStage.EVENTS.value,
                "source": "alpha_candidate_tags",
            },
        )
        return job
    job.status = ProcessingJobStatus.RUNNING
    job.progress_percent = 0
    job.celery_task_id = request_id
    job.started_at = now
    job.heartbeat_at = now
    job.completed_at = None
    job.error_message = None
    return job


async def _mark_events_job_failed(
    session: AsyncSession,
    *,
    job: ProcessingJob,
    video: Video,
    request_id: str,
    error_message: str,
) -> None:
    await _delete_alpha_candidate_events(session, video=video)
    job.status = ProcessingJobStatus.FAILED
    job.progress_percent = 100
    job.completed_at = datetime.now(tz=UTC)
    job.error_message = f"[{ErrorCode.DEMO_PREVIEW_FAILED}] {error_message}"[:2000]
    job.result_metadata = {
        "source": "alpha_candidate_tags",
        "error_code": ErrorCode.DEMO_PREVIEW_FAILED,
        "error_message": error_message,
        "outputs": {"events": 0},
    }
    await write_worker_audit(
        session,
        action=AuditAction.VIDEO_PROCESSING_FAILED,
        team_id=video.team_id,
        resource_type="processing_job",
        resource_id=job.id,
        request_id=request_id,
        extra={
            "video_id": str(video.id),
            "stage": ProcessingJobStage.EVENTS.value,
            "source": "alpha_candidate_tags",
            "error_code": ErrorCode.DEMO_PREVIEW_FAILED,
            "error_message": error_message,
        },
    )


async def _replace_alpha_candidate_events(
    session: AsyncSession,
    *,
    video: Video,
    report: dict[str, object],
) -> int:
    await _delete_alpha_candidate_events(session, video=video)
    game = await session.get(Game, video.game_id)
    shot_clock_enabled = bool(game.shot_clock_enabled) if game is not None else False
    inserted = 0
    for candidate in _validated_candidate_items(report):
        event_type = VideoEventType(candidate["event_type"])
        event_time_ms = (candidate["start_time_ms"] + candidate["end_time_ms"]) // 2
        pre_ms = max(1000, event_time_ms - candidate["start_time_ms"])
        post_ms = max(1000, candidate["end_time_ms"] - event_time_ms)
        output_frame = _estimated_output_frame(video=video, event_time_ms=event_time_ms)
        session.add(
            VideoEvent(
                video_id=video.id,
                team_id=video.team_id,
                event_type=event_type,
                event_time_ms=event_time_ms,
                clip_start_time_ms=candidate["start_time_ms"],
                clip_end_time_ms=candidate["end_time_ms"],
                output_frame=output_frame,
                shot_clock_enabled=shot_clock_enabled,
                confidence=None,
                review_status=ReviewStatus.NEEDS_REVIEW,
                event_metadata={
                    "source": _ALPHA_CANDIDATE_SOURCE,
                    "candidate_id": candidate["candidate_id"],
                    "not_production_analytics": True,
                    "review_copy": "Review required. Alpha candidate only. Not production analytics.",
                    "predicted_actions": candidate["predicted_actions"],
                    "clip_pre_ms": pre_ms,
                    "clip_post_ms": post_ms,
                },
            )
        )
        inserted += 1
    await session.flush()
    return inserted


async def _delete_alpha_candidate_events(session: AsyncSession, *, video: Video) -> int:
    existing = await session.execute(select(VideoEvent).where(VideoEvent.video_id == video.id))
    deleted = 0
    for row in existing.scalars():
        metadata = row.event_metadata or {}
        if metadata.get("source") == _ALPHA_CANDIDATE_SOURCE:
            await session.delete(row)
            deleted += 1
    if deleted:
        await session.flush()
    return deleted


def _validated_candidate_items(report: dict[str, object]) -> list[_CandidateItem]:
    if report.get("schema_version") != "alpha_video_candidate_tags_v1":
        raise ValueError("Alpha candidate report schema is not supported")
    if report.get("source") != _ALPHA_CANDIDATE_SOURCE:
        raise ValueError("Alpha candidate report source is not supported")
    lineage = report.get("lineage")
    if not isinstance(lineage, dict):
        raise ValueError("Alpha candidate report lineage is missing")
    if lineage.get("restricted_source") is not True:
        raise ValueError("Alpha candidate report must be marked restricted_source=true")
    if lineage.get("demo_only") is not True or report.get("demo_only") is not True:
        raise ValueError("Alpha candidate report must be demo_only=true")
    if lineage.get("review_required") is not True or report.get("review_required") is not True:
        raise ValueError("Alpha candidate report must be review_required=true")
    if (
        lineage.get("commercial_use_allowed") is not False
        or report.get("commercial_use_allowed") is not False
    ):
        raise ValueError("Alpha candidate report must be commercial_use_allowed=false")
    if report.get("not_production_analytics") is not True:
        raise ValueError("Alpha candidate report must be marked not production analytics")
    if report.get("blocker") is not None:
        raise ValueError("Alpha candidate report has a blocker")
    raw_candidates = report.get("candidates")
    if not isinstance(raw_candidates, list):
        raise ValueError("Alpha candidate report candidates must be a list")
    candidates: list[_CandidateItem] = []
    for raw in raw_candidates[:100]:
        if not isinstance(raw, dict):
            continue
        if raw.get("review_required") is not True:
            continue
        if raw.get("commercial_use_allowed") is not False:
            continue
        try:
            event_type = VideoEventType(str(raw.get("event_type")))
        except ValueError:
            continue
        if event_type not in _ALPHA_CANDIDATE_EVENT_TYPES:
            continue
        start = raw.get("start_time_ms")
        end = raw.get("end_time_ms")
        candidate_id = raw.get("candidate_id")
        actions = raw.get("predicted_actions")
        if not isinstance(start, int) or not isinstance(end, int) or end <= start:
            continue
        if not isinstance(candidate_id, str) or not candidate_id:
            continue
        if not isinstance(actions, list):
            actions = []
        safe_actions = [str(action)[:64] for action in actions[:12] if str(action).strip()]
        candidates.append(
            {
                "candidate_id": candidate_id[:128],
                "event_type": event_type.value,
                "start_time_ms": max(0, start),
                "end_time_ms": max(0, end),
                "predicted_actions": safe_actions,
            }
        )
    return candidates


def _estimated_output_frame(*, video: Video, event_time_ms: int) -> int:
    fps = video.fps if video.fps is not None and video.fps > 0 else 30.0
    return max(0, round((event_time_ms / 1000) * fps))


async def _sync_alpha_candidate_tags(
    session: AsyncSession,
    *,
    video: Video,
    artifact: DemoPreviewArtifact,
    settings: Settings,
    request_id: str,
) -> int | None:
    if not settings.alpha_candidate_tags_enabled():
        return None
    if artifact.candidate_tags_path is None and artifact.candidate_tags_error is None:
        return None
    job = await _load_or_create_events_job(session, video=video, request_id=request_id)
    if artifact.candidate_tags_error is not None:
        await _mark_events_job_failed(
            session,
            job=job,
            video=video,
            request_id=request_id,
            error_message=artifact.candidate_tags_error,
        )
        return None
    if artifact.candidate_tags_path is None:
        await _mark_events_job_failed(
            session,
            job=job,
            video=video,
            request_id=request_id,
            error_message="Alpha candidate tagging report is missing",
        )
        return None
    try:
        raw_report = artifact.candidate_tags_path.read_text(encoding="utf-8")
        _assert_candidate_report_safe(raw_report)
        report = json.loads(raw_report)
        if not isinstance(report, dict):
            raise ValueError("Alpha candidate report must be a JSON object")
        inserted = await _replace_alpha_candidate_events(session, video=video, report=report)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        await _mark_events_job_failed(
            session,
            job=job,
            video=video,
            request_id=request_id,
            error_message=str(exc)[:1000],
        )
        return None
    job.status = ProcessingJobStatus.COMPLETED
    job.progress_percent = 100
    job.completed_at = datetime.now(tz=UTC)
    job.error_message = None
    job.result_metadata = {
        "source": "alpha_candidate_tags",
        "contract_only": True,
        "review_required": True,
        "not_production_analytics": True,
        "outputs": {"events": inserted},
    }
    await write_worker_audit(
        session,
        action=AuditAction.VIDEO_PROCESSING_COMPLETED,
        team_id=video.team_id,
        resource_type="processing_job",
        resource_id=job.id,
        request_id=request_id,
        extra={
            "video_id": str(video.id),
            "stage": ProcessingJobStage.EVENTS.value,
            "source": "alpha_candidate_tags",
            "events": inserted,
        },
    )
    return inserted


def _assert_candidate_report_safe(raw_report: str) -> None:
    lowered = raw_report.lower()
    for token in _FORBIDDEN_CANDIDATE_REPORT_TOKENS:
        if token in lowered:
            raise ValueError("Alpha candidate report contains forbidden sensitive material")


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
        _set_preview_failed(video, celery_task_id=celery_task_id, error_message=error_message)
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

    async def _mark_preview_running() -> None:
        mark_demo_preview_running(
            settings=resolved,
            video_id=video.id,
            task_id=celery_task_id,
        )
        video.demo_preview_status = "running"
        video.demo_preview_started_at = datetime.now(tz=UTC)
        video.demo_preview_task_id = celery_task_id
        video.demo_preview_error_message = None
        await session.commit()
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
            on_started=_mark_preview_running,
        )
        preview_storage_key = await _upload_demo_preview_artifact(
            storage=resolved_storage,
            video=video,
            artifact_path=artifact.output_path,
        )
    except ConflictError as exc:
        if exc.code == ErrorCode.DEMO_PREVIEW_MACHINE_BUSY:
            mark_demo_preview_queued(
                settings=resolved,
                video_id=video.id,
                task_id=celery_task_id,
                generated_at=None,
            )
            video.demo_preview_status = "queued"
            video.demo_preview_task_id = celery_task_id
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
        _set_preview_failed(video, celery_task_id=celery_task_id, error_message=exc.message)
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
        _set_preview_failed(video, celery_task_id=celery_task_id, error_message=exc.message)
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
        _set_preview_failed(video, celery_task_id=celery_task_id, error_message=message)
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
        _set_preview_completed(
            video,
            celery_task_id=celery_task_id,
            storage_key=preview_storage_key,
            generated_at=artifact.generated_at,
        )
        candidate_event_count = await _sync_alpha_candidate_tags(
            session,
            video=video,
            artifact=artifact,
            settings=resolved,
            request_id=celery_task_id,
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
                "alpha_candidate_events": candidate_event_count,
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
