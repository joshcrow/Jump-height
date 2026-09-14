#!/bin/bash
# packaging/build.sh — build "JumpHeight Sync.app" as a universal2 py2app
# bundle. Does NOT install anything (see packaging/install.sh for that,
# which this script never calls).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"

if [[ ! -x "$PYTHON" ]]; then
    echo "packaging/build.sh: $PYTHON not found." >&2
    echo "  This build requires the python.org universal2 build — py2app" >&2
    echo "  and rumps must already be installed into it. The Homebrew" >&2
    echo "  python on this machine (/opt/homebrew/bin/python3) is arm64-only" >&2
    echo "  and will not produce a universal2 app." >&2
    exit 1
fi

echo "== packaging/build.sh: using $("$PYTHON" -VV | head -n1) =="

echo "== packaging/build.sh: vendoring rclone =="
"$HERE/fetch_rclone.sh"

echo "== packaging/build.sh: cleaning previous build/dist =="
rm -rf "$HERE/build" "$HERE/dist"

echo "== packaging/build.sh: drawing the icon =="
"$PYTHON" "$HERE/icon/make_icon.py"

echo "== packaging/build.sh: running py2app (universal2) =="
(
    cd "$HERE"
    "$PYTHON" setup.py py2app --arch=universal2
)

APP_PATH="$HERE/dist/JumpHeight Sync.app"
EXE_PATH="$APP_PATH/Contents/MacOS/JumpHeight Sync"

if [[ ! -x "$EXE_PATH" ]]; then
    echo "packaging/build.sh: build finished but $EXE_PATH is not there or not executable" >&2
    exit 1
fi

echo "== packaging/build.sh: patching any arm64-only libs py2app left behind =="
"$HERE/fix_universal_libs.sh"

echo "== packaging/build.sh: scanning the whole bundle for arm64-only binaries =="
arm64_only=0
while IFS= read -r -d '' f; do
    archs="$(lipo -archs "$f" 2>/dev/null || true)"
    if [[ -n "$archs" && "$archs" != *x86_64* ]]; then
        echo "  ARM64-ONLY: $f ($archs)"
        arm64_only=$((arm64_only + 1))
    fi
done < <(find "$APP_PATH" \( -name "*.so" -o -name "*.dylib" \) -print0)
echo "arm64-only binaries remaining: $arm64_only"

echo "== packaging/build.sh: build output =="
echo "app:      $APP_PATH"
echo "size:     $(du -sh "$APP_PATH" | awk '{print $1}')"
echo "executable archs: $(lipo -archs "$EXE_PATH")"
file "$EXE_PATH"

echo "== packaging/build.sh: building the dmg =="
STAGE="$(mktemp -d)"
cp -R "$APP_PATH" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG_PATH="$HERE/dist/JumpHeight Sync.dmg"
rm -f "$DMG_PATH"
hdiutil create -volname "JumpHeight Sync" -srcfolder "$STAGE" -ov -format UDZO -quiet "$DMG_PATH"
rm -rf "$STAGE"
echo "dmg:      $DMG_PATH ($(du -sh "$DMG_PATH" | awk '{print $1}'))"

# The self-update payload. The .dmg above is what a HUMAN installs; this zip
# is what the installed app downloads and swaps in by itself
# (tools/puckd/selfupdate.py). `ditto -c -k --keepParent` is the macOS
# spelling: it preserves the symlinks and xattrs inside the bundle (zip(1)
# does not) and puts "JumpHeight Sync.app" at the ROOT of the archive, which
# is exactly what selfupdate._find_app() looks for.
#
# The sha256 and byte count printed here are what packaging/release.sh puts
# into web/app/latest.json, and what selfupdate.py's gate verifies before it
# unpacks anything. A build that prints them is a build whose manifest can be
# written without re-deriving a single number by hand.
APP_VERSION="$("$PYTHON" -c "import re,sys;print(re.search(r'^APP_VERSION = \"([^\"]+)\"', open('$HERE/setup.py').read(), re.M).group(1))")"
ZIP_PATH="$HERE/dist/JumpHeight-Sync-$APP_VERSION.zip"
echo "== packaging/build.sh: building the self-update zip (v$APP_VERSION) =="
rm -f "$ZIP_PATH"
ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"
ZIP_SHA256="$(shasum -a 256 "$ZIP_PATH" | awk '{print $1}')"
ZIP_BYTES="$(stat -f%z "$ZIP_PATH")"
echo "zip:      $ZIP_PATH"
echo "bytes:    $ZIP_BYTES"
echo "sha256:   $ZIP_SHA256"

echo "== packaging/build.sh: done. Nothing installed: opening the app is the install. =="
