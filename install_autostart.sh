#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_PATH="$DIR/LinkedIn Bot.app"
PLIST_PATH="$HOME/Library/LaunchAgents/com.user.linkedin-connector-bot.plist"

echo "=== Installing LinkedIn Connector Bot Auto-Start ==="

# 1. Unload and remove legacy LaunchAgent plist to avoid Desktop TCC permissions issues
launchctl bootout gui/$(id -u)/com.user.linkedin-connector-bot 2>/dev/null || true
if [ -f "$PLIST_PATH" ]; then
    echo "Cleaning up legacy LaunchAgent plist..."
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
fi

# 2. Register LinkedIn Bot.app in macOS Login Items
echo "Registering LinkedIn Bot in macOS Login Items..."
osascript -e "tell application \"System Events\"
    set appPath to \"$APP_PATH\"
    set existingItems to (name of every login item)
    if existingItems does not contain \"LinkedIn Bot\" then
        make login item at end with properties {path:appPath, hidden:false}
    end if
end tell"

# 3. Verify registration
if osascript -e 'tell application "System Events" to get name of every login item' | grep -q "LinkedIn Bot"; then
    echo "✓ macOS Login Item successfully registered for: LinkedIn Bot.app"
    echo "✓ The bot will now automatically launch on every system boot / user login!"
else
    echo "⚠️ Login Item registration check failed. You can also add '$APP_PATH' manually in macOS System Settings > General > Login Items."
fi

# 4. Check if the bot server is currently running, if not start it
if ! curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8000/" 2>/dev/null | grep -q "200\|303"; then
    echo "Starting bot server..."
    "$DIR/Open_LinkedIn_Bot.command" >/dev/null 2>&1 &
fi

echo "=== Setup Complete! ==="
