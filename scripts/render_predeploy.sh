#!/bin/sh
set -eu

alembic upgrade head
python scripts/configure_runtime_db_role.py
