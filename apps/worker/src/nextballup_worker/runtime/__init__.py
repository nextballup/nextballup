"""Async business logic for worker tasks.

These functions take an `AsyncSession` directly so they are trivially testable
without a Celery broker. The Celery task shims (`nextballup_worker.tasks`)
wrap them with `asyncio.run` + a per-task async engine.
"""

from __future__ import annotations

from nextballup_worker.runtime.cleanup import (
    cleanup_abandoned_uploads,
    cleanup_email_verification_tokens,
    cleanup_expired_csp_reports,
    cleanup_expired_raw_videos,
    recover_stale_jobs,
    retry_raw_video_storage_deletes,
)
from nextballup_worker.runtime.cv_pipeline import execute_cv_stage, queue_next_stage_if_enabled
from nextballup_worker.runtime.demo_preview import (
    execute_demo_preview,
    finalize_demo_preview_failure,
)
from nextballup_worker.runtime.dispatch import dispatch_pending_jobs
from nextballup_worker.runtime.jobs import (
    claim_job,
    complete_job,
    fail_job,
    release_job_for_retry,
    set_video_status,
    touch_heartbeat,
)
from nextballup_worker.runtime.transcode import execute_transcode

__all__ = [
    "claim_job",
    "cleanup_abandoned_uploads",
    "cleanup_email_verification_tokens",
    "cleanup_expired_csp_reports",
    "cleanup_expired_raw_videos",
    "complete_job",
    "dispatch_pending_jobs",
    "execute_cv_stage",
    "execute_demo_preview",
    "execute_transcode",
    "fail_job",
    "finalize_demo_preview_failure",
    "queue_next_stage_if_enabled",
    "recover_stale_jobs",
    "release_job_for_retry",
    "retry_raw_video_storage_deletes",
    "set_video_status",
    "touch_heartbeat",
]
