#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)
ENV_FILE="${NBU_LOCAL_ALPHA_PREVIEW_ENV:-$REPO_ROOT/.env.alpha-preview.local}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. Copy .env.alpha-preview.local.example to .env.alpha-preview.local first." >&2
  exit 1
fi

set -a
. "$ENV_FILE"
set +a

cd "$REPO_ROOT"

missing=""
for name in DATABASE_URL CELERY_BROKER_URL CELERY_RESULT_BACKEND S3_ENDPOINT_URL S3_ACCESS_KEY S3_SECRET_KEY S3_BUCKET_RAW; do
  eval "value=\${$name:-}"
  if [ -z "$value" ]; then
    missing="$missing $name"
  fi
done

if [ -z "${DATABASE_URL_RUNTIME:-}" ] && [ -z "${DATABASE_RUNTIME_PASSWORD:-}" ]; then
  missing="$missing DATABASE_URL_RUNTIME-or-DATABASE_RUNTIME_PASSWORD"
fi

if [ -n "$missing" ]; then
  echo "Missing required env values:$missing" >&2
  exit 1
fi

uv run python -c 'from nextballup_core.settings import get_settings; from nextballup_core.demo_preview import validate_demo_preview_runtime; s=get_settings(); validate_demo_preview_runtime(s, startup=True, require_inference_runtime=True); print("backend-preview-runtime-ok")'

training_root=$(uv run python -c 'from nextballup_core.settings import get_settings; print(get_settings().resolve_repo_relative_path(get_settings().cv_demo_training_repo_root))')
uv run --directory "$training_root" --no-sync python scripts/local_demo_infer.py --help >/dev/null
uv run --directory "$training_root" --no-sync python -c 'import rfdetr, torch; print("vision-runtime-ok"); print("mps", torch.backends.mps.is_available())'
