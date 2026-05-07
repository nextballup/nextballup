#!/bin/sh
set -eu

# Runs only the alpha detector preview queue. Export the Render/R2/Postgres
# secrets in your shell before starting this process; this script deliberately
# does not load or print them.
export APP_ENV="${APP_ENV:-staging}"
export CV_DEMO_PREVIEW_ENABLED="${CV_DEMO_PREVIEW_ENABLED:-false}"
export CV_ALPHA_DETECTOR_PREVIEW_ENABLED="${CV_ALPHA_DETECTOR_PREVIEW_ENABLED:-true}"
export CV_DEMO_TRAINING_REPO_ROOT="${CV_DEMO_TRAINING_REPO_ROOT:-../nextballup-vision-training}"
export CV_DEMO_PREVIEW_ROOT="${CV_DEMO_PREVIEW_ROOT:-./local_artifacts/demo_previews}"
export CV_DEMO_SAMPLE_FPS="${CV_DEMO_SAMPLE_FPS:-1.0}"
export CELERY_DEMO_PREVIEW_QUEUE="${CELERY_DEMO_PREVIEW_QUEUE:-nextballup.demo_preview}"
export CELERY_WORKER_CONCURRENCY="${CELERY_WORKER_CONCURRENCY:-1}"
export WORKER_MEDIA_TEMP_DIR="${WORKER_MEDIA_TEMP_DIR:-./local_artifacts/alpha-demo-scratch}"

exec celery -A nextballup_worker.celery_app worker \
  --loglevel="${CELERY_LOGLEVEL:-info}" \
  --concurrency="${CELERY_WORKER_CONCURRENCY}" \
  --queues="${CELERY_DEMO_PREVIEW_QUEUE}"
