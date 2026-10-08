#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PID_FILE="$PROJECT_DIR/.runtime/crypto-signal-bot.pid"
LOG_FILE="$PROJECT_DIR/.runtime/crypto-signal-bot.log"

if [ -f "$PID_FILE" ]; then
    PID=$(sed -n '1p' "$PID_FILE")
    case "$PID" in *[!0-9]*|'') PID="" ;; esac
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "crypto-signal-bot RUNNING (pid $PID)"
        echo "log: $LOG_FILE"
        exit 0
    fi
fi
echo "crypto-signal-bot STOPPED"
exit 1
