#!/bin/sh
set -eu

exec celery -A nextballup_worker.celery_app beat --loglevel=info
