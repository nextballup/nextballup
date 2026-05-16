#!/bin/sh
set -eu

APP_RUN_USER="nextballup"
APP_RUN_GROUP="nextballup"
case "${TMPDIR:-}" in
  "" | /tmp)
    TMPDIR="/tmp/nextballup"
    ;;
esac
export APP_RUN_USER APP_RUN_GROUP TMPDIR

render_assert_safe_runtime_dir() {
  case "$1" in
    /tmp/nextballup | /tmp/nextballup/* | /var/data/nextballup-transcode | /var/data/nextballup-transcode/*)
      return 0
      ;;
  esac
  echo "Refusing to prepare unsafe runtime directory: $1" >&2
  exit 70
}

render_prepare_dir() {
  dir="$1"
  if [ -z "$dir" ]; then
    return 0
  fi
  render_assert_safe_runtime_dir "$dir"
  mkdir -p "$dir"
  if [ "$(id -u)" = "0" ]; then
    chown -R "${APP_RUN_USER}:${APP_RUN_GROUP}" "$dir"
  fi
  if [ ! -w "$dir" ]; then
    echo "Runtime directory is not writable by $(id -un): $dir" >&2
    exit 70
  fi
}

render_prepare_runtime_dirs() {
  render_prepare_dir "$TMPDIR"
  render_prepare_dir "${WORKER_MEDIA_TEMP_DIR:-}"
}

render_drop_exec() {
  if [ "$(id -u)" = "0" ]; then
    exec gosu "${APP_RUN_USER}:${APP_RUN_GROUP}" "$@"
  fi
  exec "$@"
}
