from __future__ import annotations

import logging
from ssl import CERT_REQUIRED
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init, worker_ready

from nextballup_core.logging import install_log_redaction_filter
from nextballup_core.settings import Settings, get_settings
from nextballup_worker.observability import start_worker_metrics_server

logger = logging.getLogger(__name__)

# The Celery broker/result backend are optional; the worker is allowed to be
# imported without them so tests can exercise task code paths via the runtime
# layer without booting a real broker.
_DEFAULT_BROKER = "memory://"  # in-process transport for tests/import-only.


def _redis_tls_url(url: str | None) -> str | None:
    if not url:
        return url
    parsed = urlsplit(url)
    if parsed.scheme != "rediss":
        return url
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("ssl_cert_reqs", "CERT_REQUIRED")
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query),
            parsed.fragment,
        )
    )


def _redis_tls_options(url: str | None) -> dict[str, object] | None:
    if not url or urlsplit(url).scheme != "rediss":
        return None
    return {"ssl_cert_reqs": CERT_REQUIRED}


def _resolve_broker(settings: Settings) -> str:
    return _redis_tls_url(settings.celery_broker_url) or _DEFAULT_BROKER


def _resolve_backend(settings: Settings) -> str | None:
    return _redis_tls_url(settings.celery_result_backend or settings.celery_broker_url)


def create_celery_app(settings: Settings | None = None) -> Celery:
    install_log_redaction_filter()
    resolved = settings or get_settings()
    broker_url = _resolve_broker(resolved)
    backend_url = _resolve_backend(resolved)
    app = Celery(
        "nextballup_worker",
        broker=broker_url,
        backend=backend_url,
        include=["nextballup_worker.tasks"],
    )
    app_config: dict[str, object] = {
        "task_default_queue": resolved.celery_task_default_queue,
        "task_acks_late": True,
        "task_reject_on_worker_lost": True,
        "task_track_started": True,
        "task_time_limit": 60 * 60 * 3,  # 3 hours hard cap
        "task_soft_time_limit": 60 * 60 * 2,
        "worker_prefetch_multiplier": 1,
        "broker_connection_retry_on_startup": True,
        "result_expires": 60 * 60 * 24,
        "timezone": "UTC",
        "enable_utc": True,
        "beat_schedule": _beat_schedule(resolved),
    }
    broker_ssl = _redis_tls_options(broker_url)
    if broker_ssl:
        app_config["broker_use_ssl"] = broker_ssl
    backend_ssl = _redis_tls_options(backend_url)
    if backend_ssl:
        app_config["redis_backend_use_ssl"] = backend_ssl
    app.conf.update(app_config)
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
        "retry-raw-video-storage-deletes": {
            "task": "nextballup_worker.tasks.retry_raw_video_storage_deletes_task",
            "schedule": max(60, settings.worker_cleanup_interval_seconds),
            "options": {"queue": settings.celery_maintenance_queue},
        },
    }


def _start_child_worker_metrics(**_: object) -> None:
    start_worker_metrics_server()


def _start_solo_worker_metrics(**_: object) -> None:
    start_worker_metrics_server()


worker_process_init.connect(_start_child_worker_metrics, weak=False)
worker_ready.connect(_start_solo_worker_metrics, weak=False)


# Module-level singleton so `celery -A nextballup_worker.celery_app worker`
# discovers the app. Tests don't rely on this singleton — they use the runtime
# module directly.
celery_app: Celery = create_celery_app()

# Keep crontab import live so type checkers/linters don't prune it; it is the
# standard override import downstream phases will use for timezone-sensitive
# beat entries.
_ = crontab
