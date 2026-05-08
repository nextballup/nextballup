#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)
ENV_FILE="${NBU_LOCAL_ALPHA_PREVIEW_ENV:-$REPO_ROOT/.env.alpha-preview.local}"

# Runs only the alpha detector preview queue. Keep Render/R2/Postgres secrets
# in the gitignored env file; this script deliberately does not print them.
if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. Copy .env.alpha-preview.local.example to .env.alpha-preview.local first." >&2
  exit 1
fi

set -a
. "$ENV_FILE"
set +a

export APP_ENV="${APP_ENV:-staging}"
export CV_DEMO_PREVIEW_ENABLED="${CV_DEMO_PREVIEW_ENABLED:-false}"
export CV_ALPHA_DETECTOR_PREVIEW_ENABLED="${CV_ALPHA_DETECTOR_PREVIEW_ENABLED:-true}"
export CV_DEMO_TRAINING_REPO_ROOT="${CV_DEMO_TRAINING_REPO_ROOT:-../nextballup-vision-training}"
export CV_DEMO_PREVIEW_ROOT="${CV_DEMO_PREVIEW_ROOT:-./local_artifacts/demo_previews}"
export CV_DEMO_SAMPLE_FPS="${CV_DEMO_SAMPLE_FPS:-1.0}"
export CELERY_DEMO_PREVIEW_QUEUE="${CELERY_DEMO_PREVIEW_QUEUE:-nextballup.demo_preview}"
export CELERY_WORKER_CONCURRENCY="${CELERY_WORKER_CONCURRENCY:-1}"
export WORKER_MEDIA_TEMP_DIR="${WORKER_MEDIA_TEMP_DIR:-./local_artifacts/alpha-demo-scratch}"

if [ -z "${UV_BIN:-}" ]; then
  if command -v uv >/dev/null 2>&1; then
    UV_BIN=$(command -v uv)
  elif [ -n "${HOME:-}" ] && [ -x "$HOME/.local/bin/uv" ]; then
    UV_BIN="$HOME/.local/bin/uv"
  elif [ -x "/Users/$(id -un)/.local/bin/uv" ]; then
    UV_BIN="/Users/$(id -un)/.local/bin/uv"
  elif [ -x /opt/homebrew/bin/uv ]; then
    UV_BIN=/opt/homebrew/bin/uv
  elif [ -x /usr/local/bin/uv ]; then
    UV_BIN=/usr/local/bin/uv
  else
    echo "Missing uv. Set UV_BIN to the absolute uv executable path." >&2
    exit 1
  fi
fi

cd "$REPO_ROOT"
exec "$UV_BIN" run celery -A nextballup_worker.celery_app worker \
  --loglevel="${CELERY_LOGLEVEL:-info}" \
  --concurrency="${CELERY_WORKER_CONCURRENCY}" \
  --queues="${CELERY_DEMO_PREVIEW_QUEUE}"
