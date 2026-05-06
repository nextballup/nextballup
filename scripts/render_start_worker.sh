#!/bin/sh
set -eu

exec celery -A nextballup_worker.celery_app worker \
  --loglevel=info \
  --queues=nextballup.default,nextballup.transcode,nextballup.maintenance,nextballup.cpu
