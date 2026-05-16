#!/bin/sh
set -eu

. scripts/render_runtime.sh

render_prepare_runtime_dirs

render_drop_exec uvicorn nextballup_api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
