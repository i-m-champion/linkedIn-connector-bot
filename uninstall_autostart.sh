#!/usr/bin/env bash
PLIST_PATH="$HOME/Library/LaunchAgents/com.user.linkedin-connector-bot.plist"

echo "=== Uninstalling LinkedIn Connector Bot Auto-Start ==="

# 1. Remove LaunchAgent plist if present
launchctl bootout gui/$(id -u)/com.user.linkedin-connector-bot 2>/dev/null || true
if [ -f "$PLIST_PATH" ]; then
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
    rm -f "$PLIST_PATH"
    echo "✓ Legacy LaunchAgent removed."
fi

# 2. Remove from macOS Login Items
osascript -e 'tell application "System Events"
    try
        delete (every login item whose name is "LinkedIn Bot")
    end try
end tell' 2>/dev/null || true

echo "✓ LinkedIn Bot removed from macOS Login Items."
echo "=== Done ==="
