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

echo "== packaging/build.sh: done. Not installed — run packaging/install.sh yourself when ready. =="
