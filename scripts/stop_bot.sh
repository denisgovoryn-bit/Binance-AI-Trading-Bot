#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PID_FILE="$PROJECT_DIR/.runtime/crypto-signal-bot.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "crypto-signal-bot is not running"
    exit 0
fi

PID=$(sed -n '1p' "$PID_FILE")
case "$PID" in *[!0-9]*|'') PID="" ;; esac
if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    echo "crypto-signal-bot is not running (stale pid file)"
    exit 0
fi

kill -TERM "$PID"
COUNT=0
while kill -0 "$PID" 2>/dev/null && [ "$COUNT" -lt 30 ]; do
    sleep 1
    COUNT=$((COUNT + 1))
done
if kill -0 "$PID" 2>/dev/null; then
    echo "graceful shutdown timed out for pid $PID" >&2
    exit 1
fi
echo "crypto-signal-bot stopped"
