#!/bin/bash
# packaging/install.sh — install "JumpHeight Sync.app" and its LaunchAgent
# on THIS Mac. Not run as part of the build; the owner runs it by hand,
# on the machine it should actually run on.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_APP="$HERE/dist/JumpHeight Sync.app"
DEST_APP="/Applications/JumpHeight Sync.app"
PLIST_SRC="$HERE/com.jumpheight.puckd.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.jumpheight.puckd.plist"

if [[ ! -d "$SRC_APP" ]]; then
    echo "packaging/install.sh: $SRC_APP not found — run packaging/build.sh first" >&2
    exit 1
fi

echo "== packaging/install.sh: copying app to /Applications =="
rm -rf "$DEST_APP"
cp -R "$SRC_APP" "$DEST_APP"

echo "== packaging/install.sh: installing LaunchAgent =="
mkdir -p "$HOME/Library/LaunchAgents"
cp "$PLIST_SRC" "$PLIST_DEST"

# Unload a previous copy first (bootstrap fails if the label is already
# loaded) — errors ignored, since "not currently loaded" is the common case.
launchctl bootout "gui/$UID" "$PLIST_DEST" 2>/dev/null || true

launchctl bootstrap "gui/$UID" "$PLIST_DEST"
launchctl enable "gui/$UID/com.jumpheight.puckd"

echo "== packaging/install.sh: done. JumpHeight Sync is installed and will run at login. =="
echo "First launch needs a right-click -> Open (the app is unsigned) — see packaging/README.md."
