"""Async business logic for worker tasks.

These functions take an `AsyncSession` directly so they are trivially testable
without a Celery broker. The Celery task shims (`nextballup_worker.tasks`)
wrap them with `asyncio.run` + a per-task async engine.
"""

from __future__ import annotations

from nextballup_worker.runtime.cleanup import (
    cleanup_abandoned_uploads,
    recover_stale_jobs,
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
    "complete_job",
    "dispatch_pending_jobs",
    "execute_transcode",
    "fail_job",
    "recover_stale_jobs",
    "release_job_for_retry",
    "set_video_status",
    "touch_heartbeat",
]
