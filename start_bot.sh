#!/usr/bin/env bash
# ==============================================================================
# LinkedIn Connector Bot - Startup & Self-Healing Script
# ==============================================================================

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

PORT="${PORT:-8000}"

echo "=== [$(date '+%Y-%m-%d %H:%M:%S')] Starting LinkedIn Connector Bot ==="

# 1. Kill any stale process listening on this port
OLD_PIDS=$(lsof -t -i :"$PORT" 2>/dev/null || true)
if [ -n "$OLD_PIDS" ]; then
    echo "Clearing existing process on port $PORT (PID: $OLD_PIDS)..."
    kill -9 $OLD_PIDS 2>/dev/null || true
    sleep 1
fi

# 2. Clear any stale Chromium singleton lockfiles in browser profiles
find "$DIR/browser_profiles" -type f -name "Singleton*" -delete 2>/dev/null || true

# 3. Ensure virtual environment is present
if [ -f "$DIR/.venv/bin/python" ]; then
    PYTHON_EXEC="$DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_EXEC="$(command -v python3)"
else
    echo "ERROR: Python 3 not found!"
    exit 1
fi

# 4. Launch the application
echo "Launching bot on http://127.0.0.1:$PORT..."
exec "$PYTHON_EXEC" -m uvicorn main:app --host 127.0.0.1 --port "$PORT"
