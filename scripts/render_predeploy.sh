#!/bin/sh
set -eu

. scripts/render_runtime.sh

render_prepare_runtime_dirs

if [ "$(id -u)" = "0" ]; then
  exec gosu "${APP_RUN_USER}:${APP_RUN_GROUP}" /bin/sh -c \
    'alembic upgrade head && python scripts/configure_runtime_db_role.py'
fi

alembic upgrade head
python scripts/configure_runtime_db_role.py
