from __future__ import annotations

import uuid
from dataclasses import dataclass

from nextballup_api.billing import resolve_team_plan
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nextballup_core.constants import AuditAction, ErrorCode
from nextballup_core.enums import (
    ModelArtifactStatus,
    ProcessingJobStage,
    ProcessingJobStatus,
    VideoStatus,
)
from nextballup_core.settings import Settings, get_settings
from nextballup_db.models.cv import CVModelArtifact
from nextballup_db.models.game import Game
from nextballup_db.models.video import ProcessingJob, Video
from nextballup_worker.audit import originating_user_extra, write_worker_audit
from nextballup_worker.runtime.jobs import claim_job, complete_job, fail_job, touch_heartbeat
from nextballup_worker.tenant import (
    clear_worker_context,
    set_worker_context,
    set_worker_operator_role,
)

_NEXT_STAGE: dict[ProcessingJobStage, ProcessingJobStage] = {
    ProcessingJobStage.TRANSCODE: ProcessingJobStage.DETECTION,
    ProcessingJobStage.DETECTION: ProcessingJobStage.TRACKING,
    ProcessingJobStage.TRACKING: ProcessingJobStage.COURT_MAPPING,
    ProcessingJobStage.COURT_MAPPING: ProcessingJobStage.EVENTS,
    ProcessingJobStage.EVENTS: ProcessingJobStage.METRICS,
}
_UPSTREAM_STAGE: dict[ProcessingJobStage, ProcessingJobStage] = {
    next_stage: stage for stage, next_stage in _NEXT_STAGE.items()
}
_CV_STAGES: frozenset[ProcessingJobStage] = frozenset(_UPSTREAM_STAGE)


@dataclass
class CVStageResult:
    job_id: uuid.UUID
    status: str
    retryable: bool
    error_code: str | None = None


async def queue_next_stage_if_enabled(
    session: AsyncSession,
    *,
    video: Video,
    completed_stage: ProcessingJobStage,
    settings: Settings,
    request_id: str | None = None,
) -> ProcessingJob | None:
    """Create the next pipeline job after a stage completes.

    The CV pipeline is opt-in until real model artifacts are registered. This
    keeps commercial playback processing from silently claiming analytics are
    queued before the organization has approved model/data provenance.
    """
    if not settings.cv_pipeline_enabled:
        return None
    next_stage = _NEXT_STAGE.get(completed_stage)
    if next_stage is None:
        return None
    existing = await session.scalar(
        select(ProcessingJob.id).where(
            ProcessingJob.video_id == video.id,
            ProcessingJob.stage == next_stage,
        )
    )
    if existing is not None:
        return None
    job = ProcessingJob(
        video_id=video.id,
        team_id=video.team_id,
        stage=next_stage,
        status=ProcessingJobStatus.PENDING,
        progress_percent=0,
    )
    session.add(job)
    await session.flush()
    await write_worker_audit(
        session,
        action=AuditAction.VIDEO_PROCESSING_QUEUED,
        team_id=video.team_id,
        resource_type="processing_job",
        resource_id=job.id,
        request_id=request_id,
        extra={
            "video_id": str(video.id),
            "stage": next_stage.value,
            "source": f"{completed_stage.value}_completed",
            **(
                {"originating_user_id": str(video.uploaded_by)}
                if video.uploaded_by is not None
                else {}
            ),
        },
    )
    return job


async def execute_cv_stage(
    session: AsyncSession,
    *,
    job_id: uuid.UUID,
    celery_task_id: str | None = None,
    request_id: str | None = None,
    settings: Settings | None = None,
) -> CVStageResult:
    resolved = settings or get_settings()

    await set_worker_operator_role(session)
    job = await session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.id == job_id)
        .execution_options(populate_existing=True)
    )
    if job is None:
        await clear_worker_context(session)
        return CVStageResult(
            job_id=job_id, status="skipped", retryable=False, error_code="not_found"
        )
    if job.stage not in _CV_STAGES:
        await clear_worker_context(session)
        return await _terminal_cv_failure(
            session,
            job=job,
            error_code=ErrorCode.PROCESSING_STAGE_UNKNOWN,
            error_message=f"Unexpected CV stage: {job.stage.value}",
            request_id=request_id,
        )

    await set_worker_context(session, team_id=job.team_id)
    claimed = await claim_job(session, job_id=job.id, celery_task_id=celery_task_id)
    if claimed is None:
        await clear_worker_context(session)
        return CVStageResult(job_id=job.id, status="skipped", retryable=False)

    origin_extra = await originating_user_extra(session, video_id=claimed.video_id)
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

    if not resolved.cv_pipeline_enabled:
        return await _terminal_cv_failure(
            session,
            job=claimed,
            error_code=ErrorCode.CV_PIPELINE_DISABLED,
            error_message="CV pipeline is disabled for this deployment",
            request_id=request_id,
        )

    video = await session.scalar(
        select(Video).where(Video.id == claimed.video_id).execution_options(populate_existing=True)
    )
    if (
        video is None
        or video.status is not VideoStatus.PROCESSED
        or not video.storage_key_mezzanine
    ):
        return await _terminal_cv_failure(
            session,
            job=claimed,
            error_code=ErrorCode.CV_STAGE_PREREQUISITE_MISSING,
            error_message="Browser-safe video artifact is required before CV stages run",
            request_id=request_id,
        )
    upstream = _UPSTREAM_STAGE[claimed.stage]
    upstream_done = await session.scalar(
        select(ProcessingJob.id).where(
            ProcessingJob.video_id == video.id,
            ProcessingJob.stage == upstream,
            ProcessingJob.status == ProcessingJobStatus.COMPLETED,
        )
    )
    if upstream_done is None:
        return await _terminal_cv_failure(
            session,
            job=claimed,
            error_code=ErrorCode.CV_STAGE_PREREQUISITE_MISSING,
            error_message=f"{upstream.value} must complete before {claimed.stage.value}",
            request_id=request_id,
        )

    await touch_heartbeat(session, job_id=claimed.id, progress_percent=25)
    plan_ctx = await resolve_team_plan(session, team_id=video.team_id)
    if plan_ctx is None and _stage_requires_artifact(resolved, claimed.stage):
        return await _terminal_cv_failure(
            session,
            job=claimed,
            error_code=ErrorCode.CV_MODEL_ARTIFACT_REQUIRED,
            error_message=(
                f"No billing entitlement context is available for {claimed.stage.value}"
            ),
            request_id=request_id,
        )
    plan_tier = plan_ctx.plan_tier if plan_ctx is not None else 0
    artifact = await _active_artifact_for_stage(session, claimed.stage, plan_tier=plan_tier)
    if _stage_requires_artifact(resolved, claimed.stage) and artifact is None:
        return await _terminal_cv_failure(
            session,
            job=claimed,
            error_code=ErrorCode.CV_MODEL_ARTIFACT_REQUIRED,
            error_message=(
                f"No commercial CV artifact entitled to plan tier {plan_tier} is "
                f"available for {claimed.stage.value}"
            ),
            request_id=request_id,
        )

    game = await session.get(Game, video.game_id)
    metadata = _stage_metadata(
        stage=claimed.stage,
        video=video,
        game=game,
        artifact=artifact,
    )
    await touch_heartbeat(session, job_id=claimed.id, progress_percent=75)
    await complete_job(session, job_id=claimed.id, result_metadata=metadata)
    await write_worker_audit(
        session,
        action=AuditAction.VIDEO_PROCESSING_COMPLETED,
        team_id=claimed.team_id,
        resource_type="processing_job",
        resource_id=claimed.id,
        request_id=request_id,
        extra={
            "video_id": str(video.id),
            "stage": claimed.stage.value,
            "artifact_id": str(artifact.id) if artifact is not None else None,
            **origin_extra,
        },
    )
    await queue_next_stage_if_enabled(
        session,
        video=video,
        completed_stage=claimed.stage,
        settings=resolved,
        request_id=request_id,
    )
    await session.commit()
    await clear_worker_context(session)
    return CVStageResult(job_id=claimed.id, status="completed", retryable=False)


async def _terminal_cv_failure(
    session: AsyncSession,
    *,
    job: ProcessingJob,
    error_code: str,
    error_message: str,
    request_id: str | None,
) -> CVStageResult:
    await set_worker_context(session, team_id=job.team_id)
    origin_extra = await originating_user_extra(session, video_id=job.video_id)
    await fail_job(
        session,
        job_id=job.id,
        error_code=error_code,
        error_message=error_message,
    )
    await write_worker_audit(
        session,
        action=AuditAction.VIDEO_PROCESSING_FAILED,
        team_id=job.team_id,
        resource_type="processing_job",
        resource_id=job.id,
        request_id=request_id,
        extra={
            "video_id": str(job.video_id),
            "stage": job.stage.value,
            "error_code": error_code,
            "error_message": error_message,
            "retryable": False,
            **origin_extra,
        },
    )
    await session.commit()
    await clear_worker_context(session)
    return CVStageResult(job_id=job.id, status="failed", retryable=False, error_code=error_code)


def _stage_requires_artifact(settings: Settings, stage: ProcessingJobStage) -> bool:
    return settings.cv_require_model_artifacts and stage.value in set(
        settings.cv_model_artifact_required_stages
    )


async def _active_artifact_for_stage(
    session: AsyncSession,
    stage: ProcessingJobStage,
    *,
    plan_tier: int = 0,
) -> CVModelArtifact | None:
    """Pick the best entitled artifact for a stage.

    Selection rules:
      * status = ACTIVE and commercial_use_allowed = True (commercial-license
        gate, identical to prior behavior).
      * `min_plan_tier <= plan_tier`. The DB index on `(stage, min_plan_tier)`
        makes this filter cheap.
      * tie-break by `min_plan_tier DESC` (prefer the highest tier the caller
        is entitled to — premium plans should not get a lower-tier model when
        a premium one exists), then by `created_at DESC` (newest model wins
        for the same tier).
    """
    artifact: CVModelArtifact | None = await session.scalar(
        select(CVModelArtifact)
        .where(
            CVModelArtifact.stage == stage,
            CVModelArtifact.status == ModelArtifactStatus.ACTIVE,
            CVModelArtifact.commercial_use_allowed.is_(True),
            CVModelArtifact.min_plan_tier <= plan_tier,
        )
        .order_by(
            CVModelArtifact.min_plan_tier.desc(),
            CVModelArtifact.created_at.desc(),
        )
    )
    return artifact


def _stage_metadata(
    *,
    stage: ProcessingJobStage,
    video: Video,
    game: Game | None,
    artifact: CVModelArtifact | None,
) -> dict[str, object]:
    shot_clock_enabled = bool(game.shot_clock_enabled) if game is not None else False
    return {
        "stage": stage.value,
        "contract_only": artifact is None,
        "artifact_id": str(artifact.id) if artifact is not None else None,
        "video_id": str(video.id),
        "frame_clock_required": True,
        "shot_clock": {
            "enabled": shot_clock_enabled,
            "seconds": game.shot_clock_seconds if shot_clock_enabled and game is not None else None,
        },
        "outputs": {
            "detections": 0,
            "tracks": 0,
            "events": 0,
            "metrics": 0,
        },
    }
