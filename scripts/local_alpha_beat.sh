#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)
ENV_FILE="${NBU_LOCAL_ALPHA_ENV:-$REPO_ROOT/.env.alpha.local}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. Copy .env.alpha.local.example to .env.alpha.local first." >&2
  exit 1
fi

set -a
. "$ENV_FILE"
set +a

cd "$REPO_ROOT"
exec uv run celery -A nextballup_worker.celery_app beat --loglevel=info
