#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)
ROOT_ENV_FILE="${NBU_LOCAL_ALPHA_ENV:-$REPO_ROOT/.env.alpha.local}"
WEB_ENV_FILE="${NBU_LOCAL_ALPHA_WEB_ENV:-$REPO_ROOT/apps/web/.env.alpha.local}"

if [ ! -f "$ROOT_ENV_FILE" ]; then
  echo "Missing $ROOT_ENV_FILE. Copy .env.alpha.local.example to .env.alpha.local first." >&2
  exit 1
fi
if [ ! -f "$WEB_ENV_FILE" ]; then
  echo "Missing $WEB_ENV_FILE. Copy apps/web/.env.alpha.local.example to apps/web/.env.alpha.local first." >&2
  exit 1
fi

set -a
. "$ROOT_ENV_FILE"
. "$WEB_ENV_FILE"
set +a

cd "$REPO_ROOT/apps/web"
exec pnpm exec next dev --turbopack --hostname 127.0.0.1 -p "${WEB_PORT:-3000}"
