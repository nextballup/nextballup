from __future__ import annotations

from collections.abc import Iterable

from nextballup_core.enums import ProcessingJobStage, ProcessingJobStatus, VideoStatus
from nextballup_core.schemas.video import PlaybackStatusValue
from nextballup_db.models.video import ProcessingJob, Video

_CV_STAGES: frozenset[ProcessingJobStage] = frozenset(
    {
        ProcessingJobStage.DETECTION,
        ProcessingJobStage.TRACKING,
        ProcessingJobStage.COURT_MAPPING,
        ProcessingJobStage.EVENTS,
        ProcessingJobStage.METRICS,
    }
)


def derive_playback_status(
    video: Video,
    jobs: Iterable[ProcessingJob],
    *,
    cv_pipeline_enabled: bool,
) -> PlaybackStatusValue:
    if video.status is VideoStatus.FAILED:
        return "failed"
    if video.status in {VideoStatus.PENDING_UPLOAD, VideoStatus.UPLOADING}:
        return "uploading"
    if video.status in {VideoStatus.UPLOADED, VideoStatus.QUEUED}:
        return "queued"
    if video.status in {VideoStatus.TRANSCODING, VideoStatus.PROCESSING}:
        return "transcoding"
    if video.status is not VideoStatus.PROCESSED:
        return "queued"
    if not cv_pipeline_enabled:
        return "ready_for_playback"

    cv_jobs = [job for job in jobs if job.stage in _CV_STAGES]
    if any(job.status is ProcessingJobStatus.RUNNING for job in cv_jobs):
        return "analysis_running"
    if not any(job.status is ProcessingJobStatus.COMPLETED for job in cv_jobs):
        return "analysis_pending"
    return "ready_for_playback"
