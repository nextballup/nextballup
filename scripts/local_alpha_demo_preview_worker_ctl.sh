#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd "$SCRIPT_DIR/.." && pwd)
RUN_DIR="$REPO_ROOT/local_artifacts/alpha-demo-worker"
PID_FILE="$RUN_DIR/worker.pid"
LOG_FILE="$RUN_DIR/worker.log"
LAUNCHD_LABEL="com.nextballup.alpha-demo-preview-worker"
LAUNCHD_PLIST="$RUN_DIR/$LAUNCHD_LABEL.plist"
LAUNCHD_DOMAIN="gui/$(id -u)"
LAUNCHD_SERVICE="$LAUNCHD_DOMAIN/$LAUNCHD_LABEL"

mkdir -p "$RUN_DIR"

use_launchd() {
  [ "$(uname -s)" = "Darwin" ] && command -v launchctl >/dev/null 2>&1
}

launchd_loaded() {
  launchctl print "$LAUNCHD_SERVICE" >/dev/null 2>&1
}

write_launchd_plist() {
  cat >"$LAUNCHD_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LAUNCHD_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-dimsu</string>
    <string>$REPO_ROOT/scripts/local_alpha_demo_preview_worker.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$REPO_ROOT</string>
  <key>StandardOutPath</key>
  <string>$LOG_FILE</string>
  <key>StandardErrorPath</key>
  <string>$LOG_FILE</string>
  <key>RunAtLoad</key>
  <true/>
</dict>
</plist>
EOF
}

is_running() {
  if [ ! -f "$PID_FILE" ]; then
    return 1
  fi
  pid=$(cat "$PID_FILE")
  if [ -z "$pid" ]; then
    return 1
  fi
  kill -0 "$pid" 2>/dev/null
}

case "${1:-status}" in
  start)
    if use_launchd; then
      if launchd_loaded; then
        echo "alpha preview worker launchd job already loaded: $LAUNCHD_SERVICE"
        echo "log: $LOG_FILE"
        exit 0
      fi
      write_launchd_plist
      launchctl bootstrap "$LAUNCHD_DOMAIN" "$LAUNCHD_PLIST"
      launchctl kickstart -k "$LAUNCHD_SERVICE"
      echo "started alpha preview worker launchd job: $LAUNCHD_SERVICE"
      echo "log: $LOG_FILE"
      exit 0
    fi
    if is_running; then
      echo "alpha preview worker already running: pid $(cat "$PID_FILE")"
      exit 0
    fi
    cd "$REPO_ROOT"
    nohup caffeinate -dimsu scripts/local_alpha_demo_preview_worker.sh \
      >"$LOG_FILE" 2>&1 &
    echo "$!" >"$PID_FILE"
    echo "started alpha preview worker: pid $(cat "$PID_FILE")"
    echo "log: $LOG_FILE"
    ;;
  stop)
    if use_launchd; then
      if ! launchd_loaded; then
        echo "alpha preview worker launchd job is not loaded"
        exit 0
      fi
      launchctl bootout "$LAUNCHD_SERVICE"
      echo "stopped alpha preview worker launchd job: $LAUNCHD_SERVICE"
      exit 0
    fi
    if ! is_running; then
      echo "alpha preview worker is not running"
      rm -f "$PID_FILE"
      exit 0
    fi
    pid=$(cat "$PID_FILE")
    kill "$pid"
    rm -f "$PID_FILE"
    echo "stopped alpha preview worker: pid $pid"
    ;;
  status)
    if use_launchd; then
      if launchd_loaded; then
        echo "alpha preview worker launchd job loaded: $LAUNCHD_SERVICE"
        echo "log: $LOG_FILE"
        launchctl print "$LAUNCHD_SERVICE" | awk '/state =|pid =|last exit code =/ {print "  " $0}'
        exit 0
      fi
      echo "alpha preview worker launchd job is not loaded"
      exit 1
    fi
    if is_running; then
      echo "alpha preview worker running: pid $(cat "$PID_FILE")"
      echo "log: $LOG_FILE"
    else
      echo "alpha preview worker is not running"
      rm -f "$PID_FILE"
      exit 1
    fi
    ;;
  logs)
    if [ -f "$LOG_FILE" ]; then
      tail -n "${2:-80}" "$LOG_FILE"
    else
      echo "missing log: $LOG_FILE" >&2
      exit 1
    fi
    ;;
  *)
    echo "usage: $0 {start|stop|status|logs [lines]}" >&2
    exit 2
    ;;
esac
