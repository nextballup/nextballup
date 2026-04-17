"""Stage → Celery queue routing.

Each `ProcessingJobStage` is dispatched to the queue best suited to the
work it performs:

* GPU-bound stages (detection, tracking, events) go to the GPU queue so
  workers with a GPU attached can drain them independently.
* CPU-heavy stages (transcode, court mapping) go to their own queues so a
  slow GPU pool never backs up a court-mapping job that could finish on
  any box.
* Metrics is a thin aggregation and can live on the default queue.

Keeping this mapping in one place means the admin requeue endpoint, the
beat dispatcher, and any future synchronous enqueue path all agree on
which queue to target for a given stage.
"""

from __future__ import annotations

from nextballup_core.enums import ProcessingJobStage
from nextballup_core.settings import Settings


def queue_for_stage(stage: ProcessingJobStage, settings: Settings) -> str:
    if stage is ProcessingJobStage.TRANSCODE:
        return settings.celery_transcode_queue
    if stage in (
        ProcessingJobStage.DETECTION,
        ProcessingJobStage.TRACKING,
        ProcessingJobStage.EVENTS,
    ):
        return settings.celery_gpu_queue
    if stage is ProcessingJobStage.COURT_MAPPING:
        return settings.celery_cpu_queue
    if stage is ProcessingJobStage.METRICS:
        return settings.celery_task_default_queue
    return settings.celery_task_default_queue
