#!/bin/sh
set -eu

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
RUNTIME_DIR="$PROJECT_DIR/.runtime"
PID_FILE="$RUNTIME_DIR/crypto-signal-bot.pid"
mkdir -p "$RUNTIME_DIR"

if [ -f "$PID_FILE" ]; then
    PID=$(sed -n '1p' "$PID_FILE")
    case "$PID" in *[!0-9]*|'') PID="" ;; esac
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        echo "crypto-signal-bot already running (pid $PID)"
        exit 0
    fi
fi

cd "$PROJECT_DIR"
nohup .venv/bin/python scripts/run_telegram.py >> "$RUNTIME_DIR/launcher.log" 2>&1 &
STARTED_PID=$!
sleep 2
if kill -0 "$STARTED_PID" 2>/dev/null; then
    echo "crypto-signal-bot started (pid $STARTED_PID)"
else
    echo "crypto-signal-bot failed to start; inspect $RUNTIME_DIR/launcher.log" >&2
    exit 1
fi
