#!/usr/bin/env bash
# ==============================================================================
# One-Click Launcher for LinkedIn Connector Bot Dashboard
# Double-click this file anytime to open the dashboard in your browser.
# ==============================================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8000
URL="http://127.0.0.1:$PORT"

echo "Checking LinkedIn Connector Bot status..."

# 1. Check if the server is already active and responding on port 8000
IS_ACTIVE=0
if curl -s -o /dev/null -w "%{http_code}" "$URL/" 2>/dev/null | grep -q "200\|303"; then
    IS_ACTIVE=1
fi

# 2. If not active, start it in the background
if [ $IS_ACTIVE -eq 0 ]; then
    echo "Starting LinkedIn Bot server in the background..."
    
    # Check if a stale process is clinging to port 8000
    OLD_PID=$(lsof -t -i :"$PORT" 2>/dev/null || true)
    if [ -n "$OLD_PID" ]; then
        kill -9 $OLD_PID 2>/dev/null || true
        sleep 1
    fi

    # Clean any stale locks
    find "$PROJECT_DIR/browser_profiles" -type f -name "Singleton*" -delete 2>/dev/null || true

    # Launch daemon
    cd "$PROJECT_DIR"
    nohup "$PROJECT_DIR/.venv/bin/python" -m uvicorn main:app --host 127.0.0.1 --port "$PORT" > "$PROJECT_DIR/bot.log" 2>&1 &
    
    # Wait for server to come up
    for i in {1..20}; do
        if curl -s -o /dev/null -w "%{http_code}" "$URL/" 2>/dev/null | grep -q "200\|303"; then
            IS_ACTIVE=1
            break
        fi
        sleep 0.5
    done
fi

# 3. Open browser
if [ $IS_ACTIVE -eq 1 ]; then
    echo "✓ Bot is running! Opening dashboard: $URL"
    open "$URL"
    osascript -e 'display notification "Dashboard is open at http://127.0.0.1:8000" with title "LinkedIn Bot"' 2>/dev/null || true
else
    echo "⚠️ Opening browser to $URL..."
    open "$URL"
fi

sleep 1
exit 0
