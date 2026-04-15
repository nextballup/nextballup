from __future__ import annotations

import logging
from typing import Any

from celery import Celery
from celery.schedules import crontab

from nextballup_core.settings import Settings, get_settings

logger = logging.getLogger(__name__)

# The Celery broker/result backend are optional; the worker is allowed to be
# imported without them so tests can exercise task code paths via the runtime
# layer without booting a real broker.
_DEFAULT_BROKER = "memory://"  # in-process transport for tests/import-only.


def _resolve_broker(settings: Settings) -> str:
    return settings.celery_broker_url or _DEFAULT_BROKER


def _resolve_backend(settings: Settings) -> str | None:
    return settings.celery_result_backend or settings.celery_broker_url


def create_celery_app(settings: Settings | None = None) -> Celery:
    resolved = settings or get_settings()
    app = Celery(
        "nextballup_worker",
        broker=_resolve_broker(resolved),
        backend=_resolve_backend(resolved),
        include=["nextballup_worker.tasks"],
    )
    app.conf.update(
        task_default_queue=resolved.celery_task_default_queue,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_track_started=True,
        task_time_limit=60 * 60 * 3,  # 3 hours hard cap
        task_soft_time_limit=60 * 60 * 2,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
        result_expires=60 * 60 * 24,
        timezone="UTC",
        enable_utc=True,
        beat_schedule=_beat_schedule(resolved),
    )
    return app


def _beat_schedule(settings: Settings) -> dict[str, Any]:
    """Beat schedule wiring. The task names resolve in
    `nextballup_worker.tasks` which is included via `create_celery_app`."""
    return {
        "dispatch-pending-jobs": {
            "task": "nextballup_worker.tasks.dispatch_pending_jobs_task",
            "schedule": max(1, settings.worker_dispatch_interval_seconds),
            "options": {"queue": settings.celery_maintenance_queue},
        },
        "run-maintenance-cleanup": {
            "task": "nextballup_worker.tasks.run_cleanup_task",
            "schedule": max(60, settings.worker_cleanup_interval_seconds),
            "options": {"queue": settings.celery_maintenance_queue},
        },
    }


# Module-level singleton so `celery -A nextballup_worker.celery_app worker`
# discovers the app. Tests don't rely on this singleton — they use the runtime
# module directly.
celery_app: Celery = create_celery_app()

# Keep crontab import live so type checkers/linters don't prune it; it is the
# standard override import downstream phases will use for timezone-sensitive
# beat entries.
_ = crontab
