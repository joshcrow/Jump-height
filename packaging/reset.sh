#!/bin/sh
# packaging/reset.sh — put THIS Mac back to "never had JumpHeight Sync", so the
# first-run experience can be tested again. Josh's tool. Removes:
#   the login item, the app in /Applications, the app's data
#   (~/Library/Application Support/JumpHeight: state, spool, Garmin token),
#   and the "gdrive" remote from rclone's config (the Google connection).
# It does not touch anything on Drive.
set -u
LABEL=com.jumpheight.puckd
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
pkill -f "JumpHeight Sync" 2>/dev/null || true
rm -rf "/Applications/JumpHeight Sync.app"
rm -rf "$HOME/Library/Application Support/JumpHeight"
for RC in rclone /opt/homebrew/bin/rclone "/Applications/JumpHeight Sync.app/Contents/Resources/rclone"; do
    command -v "$RC" >/dev/null 2>&1 && { "$RC" config delete gdrive 2>/dev/null; break; }
done
echo "reset: login item, app, data and the Google connection are gone."
