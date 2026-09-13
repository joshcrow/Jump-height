#!/bin/bash
# packaging/uninstall.sh — remove "JumpHeight Sync.app" and its LaunchAgent.
set -euo pipefail

PLIST_DEST="$HOME/Library/LaunchAgents/com.jumpheight.puckd.plist"
DEST_APP="/Applications/JumpHeight Sync.app"

echo "== packaging/uninstall.sh: unloading LaunchAgent =="
launchctl bootout "gui/$UID" "$PLIST_DEST" 2>/dev/null || true

echo "== packaging/uninstall.sh: removing LaunchAgent plist =="
rm -f "$PLIST_DEST"

echo "== packaging/uninstall.sh: removing app =="
rm -rf "$DEST_APP"

echo "== packaging/uninstall.sh: done. =="
echo "Rides already synced to Google Drive are untouched; nothing under"
echo "~/Library/Application Support/JumpHeight was removed by this script."
