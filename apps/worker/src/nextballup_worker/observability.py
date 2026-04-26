from __future__ import annotations

import logging
import multiprocessing

from prometheus_client import start_http_server

from nextballup_core.observability import (
    API_CSP_REPORTS_TOTAL,
    REGISTRY,
    WORKER_JOBS_DISPATCHED_TOTAL,
    WORKER_JOBS_FAILED_TOTAL,
    WORKER_JOBS_STALE_RECOVERED_TOTAL,
    WORKER_STORAGE_BYTES_UPLOADED_TOTAL,
    WORKER_TASK_SECONDS,
    WORKER_TRANSCODE_SECONDS,
    render_metrics,
)
from nextballup_core.settings import Settings, get_settings

logger = logging.getLogger(__name__)
_STARTED_ENDPOINTS: set[tuple[str, int]] = set()


def _process_port_offset() -> int:
    identity = getattr(multiprocessing.current_process(), "_identity", ())
    if not identity:
        return 0
    first = identity[0]
    return max(0, int(first) - 1)


def start_worker_metrics_server(settings: Settings | None = None) -> tuple[str, int] | None:
    resolved = settings or get_settings()
    if not resolved.observability_worker_metrics_enabled:
        return None

    host = resolved.observability_worker_metrics_host
    port = resolved.observability_worker_metrics_port + (
        _process_port_offset() % resolved.observability_worker_metrics_port_span
    )
    endpoint = (host, port)
    if endpoint in _STARTED_ENDPOINTS:
        return endpoint

    start_http_server(port, addr=host, registry=REGISTRY)
    _STARTED_ENDPOINTS.add(endpoint)
    logger.info("Worker metrics server listening on %s:%s", host, port)
    return endpoint


def _reset_worker_metrics_server_for_tests() -> None:
    _STARTED_ENDPOINTS.clear()


__all__ = [
    "API_CSP_REPORTS_TOTAL",
    "REGISTRY",
    "WORKER_JOBS_DISPATCHED_TOTAL",
    "WORKER_JOBS_FAILED_TOTAL",
    "WORKER_JOBS_STALE_RECOVERED_TOTAL",
    "WORKER_STORAGE_BYTES_UPLOADED_TOTAL",
    "WORKER_TASK_SECONDS",
    "WORKER_TRANSCODE_SECONDS",
    "_reset_worker_metrics_server_for_tests",
    "render_metrics",
    "start_worker_metrics_server",
]
