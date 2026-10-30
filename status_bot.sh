#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8000}"

echo "=== LinkedIn Connector Bot Status ==="
PIDS=$(lsof -t -i :"$PORT" 2>/dev/null || true)
if [ -n "$PIDS" ]; then
    echo "Status: RUNNING (PID: $PIDS on port $PORT)"
    ps -p $PIDS -o pid,ppid,%cpu,%mem,time,command
    echo ""
    echo "--- Live API Scheduler Status ---"
    curl -s "http://127.0.0.1:$PORT/api/scheduler/status" | python3 -m json.tool 2>/dev/null || echo "API response pending..."
else
    # Also check if it's currently on 8000
    PIDS_8000=$(lsof -t -i :8000 2>/dev/null || true)
    if [ -n "$PIDS_8000" ]; then
        echo "Note: A process is active on port 8000 (PID: $PIDS_8000)."
    fi
    echo "Status: STOPPED (Port $PORT is free)"
fi
