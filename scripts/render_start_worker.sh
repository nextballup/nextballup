#!/bin/sh
set -eu

. scripts/render_runtime.sh

render_prepare_runtime_dirs

render_drop_exec celery -A nextballup_worker.celery_app worker \
  --loglevel=info \
  --concurrency="${CELERY_WORKER_CONCURRENCY:-1}" \
  --max-tasks-per-child="${CELERY_WORKER_MAX_TASKS_PER_CHILD:-5}" \
  --queues=nextballup.default,nextballup.transcode,nextballup.maintenance,nextballup.cpu
