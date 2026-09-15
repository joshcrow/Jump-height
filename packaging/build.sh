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

REPO="$(cd "$HERE/.." && pwd)"

# ---------------------------------------------------------------------------
# The puck's SERIAL-DFU package — web/firmware/jumpheight-<src>.zip.
#
# Not the app, and not the .uf2. This is the second way onto the board, and
# it exists because of the failure measured on 2026-09-15: after `uf2` the
# XIAO always enters its bootloader (USB idProduct 0x0045), but macOS
# sometimes never publishes that bootloader's disk, and the rider's update
# then dies at flash.py's volume_wait with the board left in DFU. The
# bootloader's CDC port is live throughout and speaks Nordic serial DFU, so
# tools/puckd/flash.py falls back to uploading THIS package down it — gated
# on latest.json's dfu_sha256 exactly as the .uf2 is gated on sha256.
#
# --sd-req 0x0123 is S140 7.3.0, and it is not a guess:
#   * the board definition PlatformIO builds against says so
#     (~/.platformio/platforms/nordicnrf52/boards/xiaoblesense_adafruit.json:
#     "sd_version": "7.3.0", "sd_fwid": "0x0123"), and its builder passes
#     exactly `--dev-type 0x0052 --sd-req <that>` (builder/main.py:136-145);
#   * the bootloader on the bench Puck prints `SoftDevice: S140 7.3.0` in
#     /Volumes/XIAO-SENSE/INFO_UF2.TXT (read 2026-09-15);
#   * a real serial DFU of this package into that bootloader answered
#     `Device programmed.` — a wrong sd-req is refused by the bootloader,
#     so that upload is the measurement.
# The assertion below re-checks it every build, because a silently wrong
# init packet would publish a fallback that can never land.
#
# Skipped, NOT failed, when the firmware has not been built here or
# adafruit-nrfutil is absent: this script's contract is the .app, and a
# release must not be blocked by a missing PlatformIO build directory.
# ---------------------------------------------------------------------------
FW_HEX="$REPO/firmware/.pio/build/xiaoblesense_adafruit/firmware.hex"
FW_SRC="$(sed -n 's/^#define JH_BUILD_SRC "\(.*\)"$/\1/p' "$REPO/firmware/include/build.gen.h" || true)"
NRFUTIL="$(command -v adafruit-nrfutil || true)"
if [[ -z "$NRFUTIL" && -x "/Library/Frameworks/Python.framework/Versions/3.14/bin/adafruit-nrfutil" ]]; then
    NRFUTIL="/Library/Frameworks/Python.framework/Versions/3.14/bin/adafruit-nrfutil"
fi
DFU_ZIP=""
if [[ -z "$FW_SRC" ]]; then
    echo "== packaging/build.sh: no JH_BUILD_SRC in firmware/include/build.gen.h — skipping the DFU package =="
elif [[ ! -f "$FW_HEX" ]]; then
    echo "== packaging/build.sh: $FW_HEX not built — skipping the DFU package =="
    echo "   (build it with ./tools/jump build, then re-run, to publish jumpheight-$FW_SRC.zip)"
elif [[ -z "$NRFUTIL" ]]; then
    echo "== packaging/build.sh: adafruit-nrfutil not found — skipping the DFU package =="
else
    DFU_ZIP="$REPO/web/firmware/jumpheight-$FW_SRC.zip"
    # genpkg is NOT reproducible — it stamps the zip entries with the
    # current time, so two runs over the SAME firmware.hex produce two
    # different sha256s (measured 2026-09-15). latest.json's dfu_sha256 is
    # what flash.py checks before it uploads anything, so regenerating a
    # package that is already published and already recorded would silently
    # disable the fallback for every puck until release.sh ran again. If
    # the existing zip is the one the manifest names, leave it ALONE. A
    # changed firmware gets a changed src, and therefore a new filename.
    KEEP_EXISTING=""
    if [[ -f "$DFU_ZIP" && -f "$REPO/web/firmware/latest.json" ]]; then
        RECORDED="$("$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1])).get("dfu_sha256",""))' "$REPO/web/firmware/latest.json")"
        if [[ -n "$RECORDED" && "$RECORDED" == "$(shasum -a 256 "$DFU_ZIP" | awk '{print $1}')" ]]; then
            KEEP_EXISTING="yes"
        fi
    fi
    if [[ -n "$KEEP_EXISTING" ]]; then
        echo "== packaging/build.sh: web/firmware/jumpheight-$FW_SRC.zip already matches latest.json's dfu_sha256 — keeping it =="
        echo "dfu zip:    $DFU_ZIP (unchanged)"
        echo "dfu sha256: $RECORDED"
    else
        echo "== packaging/build.sh: building the puck's serial-DFU package (src=$FW_SRC) =="
        mkdir -p "$REPO/web/firmware"
        rm -f "$DFU_ZIP"
        "$NRFUTIL" dfu genpkg \
            --dev-type 0x0052 \
            --sd-req 0x0123 \
            --application "$FW_HEX" \
            "$DFU_ZIP"
        # ASSERT the init packet. genpkg exits 0 whatever it wrote, and a
        # package whose device type or SoftDevice requirement is wrong is
        # refused by the bootloader at its START packet — i.e. it would fail
        # only on the rider's Mac, in the one situation the fallback exists for.
        "$PYTHON" - "$DFU_ZIP" <<'PY'
import json, sys, zipfile
path = sys.argv[1]
with zipfile.ZipFile(path) as z:
    app = json.loads(z.read("manifest.json"))["manifest"]["application"]
init = app["init_packet_data"]
problems = []
if init.get("device_type") != 0x52:
    problems.append(f"device_type is {init.get('device_type')}, expected 82 (0x0052)")
if init.get("softdevice_req") != [0x0123]:
    problems.append(f"softdevice_req is {init.get('softdevice_req')}, expected [291] (S140 7.3.0)")
if problems:
    sys.stderr.write("packaging/build.sh: the DFU package's init packet is wrong:\n")
    for p in problems:
        sys.stderr.write(f"  {p}\n")
    raise SystemExit(1)
print(f"init packet ok: dev-type 0x0052, sd-req 0x0123 (S140 7.3.0)")
PY
        DFU_SHA256="$(shasum -a 256 "$DFU_ZIP" | awk '{print $1}')"
        DFU_BYTES="$(stat -f%z "$DFU_ZIP")"
        echo "dfu zip:    $DFU_ZIP"
        echo "dfu bytes:  $DFU_BYTES"
        echo "dfu sha256: $DFU_SHA256"
        echo "   (packaging/release.sh copies these into web/firmware/latest.json's"
        echo "    dfu_file / dfu_bytes / dfu_sha256 — flash.py verifies that hash"
        echo "    before it uploads a single byte. A zip published WITHOUT that"
        echo "    manifest update is a fallback that refuses itself, silently.)"
    fi
fi

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
