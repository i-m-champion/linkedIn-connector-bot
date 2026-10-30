#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT="${PORT:-8000}"

echo "Stopping LinkedIn Connector Bot on port $PORT..."
PIDS=$(lsof -t -i :"$PORT" 2>/dev/null || true)
if [ -n "$PIDS" ]; then
    kill -9 $PIDS 2>/dev/null || true
    echo "Stopped PID(s): $PIDS"
else
    echo "No process running on port $PORT."
fi

# Clean up stale locks
find "$DIR/browser_profiles" -type f -name "Singleton*" -delete 2>/dev/null || true
echo "Done."
