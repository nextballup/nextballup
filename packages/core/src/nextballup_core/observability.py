from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest

REGISTRY = CollectorRegistry(auto_describe=True)

WORKER_TASK_SECONDS = Histogram(
    "worker_task_seconds",
    "Worker task duration in seconds.",
    labelnames=("task", "outcome"),
    registry=REGISTRY,
)
WORKER_JOBS_DISPATCHED_TOTAL = Counter(
    "worker_jobs_dispatched_total",
    "Worker jobs dispatched by stage.",
    labelnames=("stage",),
    registry=REGISTRY,
)
WORKER_JOBS_FAILED_TOTAL = Counter(
    "worker_jobs_failed_total",
    "Worker jobs failed by stage and stable error code.",
    labelnames=("stage", "error_code"),
    registry=REGISTRY,
)
WORKER_JOBS_STALE_RECOVERED_TOTAL = Counter(
    "worker_jobs_stale_recovered_total",
    "Worker jobs terminalized by stale-heartbeat recovery.",
    registry=REGISTRY,
)
WORKER_STORAGE_BYTES_UPLOADED_TOTAL = Counter(
    "worker_storage_bytes_uploaded_total",
    "Bytes uploaded to object storage by worker output stages.",
    registry=REGISTRY,
)
WORKER_TRANSCODE_SECONDS = Histogram(
    "worker_transcode_seconds",
    "Browser mezzanine transcode duration in seconds.",
    registry=REGISTRY,
)
API_CSP_REPORTS_TOTAL = Counter(
    "api_csp_reports_total",
    "CSP reports ingested by violated directive.",
    labelnames=("directive",),
    registry=REGISTRY,
)


def render_metrics() -> bytes:
    return generate_latest(REGISTRY)
