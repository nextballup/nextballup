#!/bin/sh
set -eu

. scripts/render_runtime.sh

render_prepare_runtime_dirs

render_drop_exec celery -A nextballup_worker.celery_app beat --loglevel=info
