"""Stage → queue routing tests.

The map is small and easy to review, but getting a stage on the wrong
queue silently routes real work to the wrong worker pool, so we pin the
full table here. These are pure unit tests: no DB, no Celery broker.
"""

from __future__ import annotations

import pytest
from nextballup_worker.routing import queue_for_stage

from nextballup_core.enums import ProcessingJobStage
from nextballup_core.settings import Settings


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        celery_task_default_queue="default-q",
        celery_transcode_queue="transcode-q",
        celery_gpu_queue="gpu-q",
        celery_cpu_queue="cpu-q",
    )


@pytest.mark.parametrize(
    ("stage", "expected_attr"),
    [
        (ProcessingJobStage.TRANSCODE, "celery_transcode_queue"),
        (ProcessingJobStage.DETECTION, "celery_gpu_queue"),
        (ProcessingJobStage.TRACKING, "celery_gpu_queue"),
        (ProcessingJobStage.EVENTS, "celery_gpu_queue"),
        (ProcessingJobStage.COURT_MAPPING, "celery_cpu_queue"),
        (ProcessingJobStage.METRICS, "celery_task_default_queue"),
    ],
)
def test_queue_for_stage_routes_each_stage_to_expected_queue(
    stage: ProcessingJobStage, expected_attr: str, settings: Settings
) -> None:
    assert queue_for_stage(stage, settings) == getattr(settings, expected_attr)


def test_queue_for_stage_returns_unique_pool_per_role(settings: Settings) -> None:
    """Each 'role' of queue (GPU, CPU, transcode, default) must resolve to a
    distinct queue so topology changes can actually scale one pool
    independently from the others."""

    gpu = queue_for_stage(ProcessingJobStage.DETECTION, settings)
    cpu = queue_for_stage(ProcessingJobStage.COURT_MAPPING, settings)
    transcode = queue_for_stage(ProcessingJobStage.TRANSCODE, settings)
    default = queue_for_stage(ProcessingJobStage.METRICS, settings)
    assert len({gpu, cpu, transcode, default}) == 4
