#!/bin/bash
# packaging/fetch_rclone.sh — vendor a universal2 rclone binary for the app
# bundle. Run by packaging/build.sh; safe to run standalone too.
#
# MEASURED 2026-09-13: rclone.org/downloads currently ships NO single
# "macOS universal" zip for this version — only per-architecture zips
# (rclone-vX.Y.Z-osx-amd64.zip, rclone-vX.Y.Z-osx-arm64.zip). This is a
# finding against the build brief, which assumed one exists; recorded here
# rather than silently substituted. What this script does instead: download
# BOTH official per-arch binaries, verify each against rclone's own
# published SHA256SUMS for this pinned version, then build a universal2
# binary locally with `lipo -create` — the same operation Homebrew's own
# universal formulae perform, not a repackaging of unverified bytes. The
# two source zips' hashes are pinned below; if rclone.org ever does publish
# a real "macOS universal" zip, switch to it and delete the lipo step.
set -euo pipefail

RCLONE_VERSION="v1.75.1"

# From https://downloads.rclone.org/${RCLONE_VERSION}/SHA256SUMS, fetched
# and checked by hand against this script's own download below.
SHA256_AMD64="29253d0288b8fbbac46baad6e5f6add6cb01d462c79f10805bbd4631c4cdf82c"
SHA256_ARM64="c61d7a371c62bcbbe882c3423aa4b8bf63485c248dd0f692997b8f0c3f6d0c6f"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="$HERE/vendor"
WORK_DIR="$VENDOR_DIR/_work-rclone-$RCLONE_VERSION"
OUT_DIR="$VENDOR_DIR/rclone"
OUT_BIN="$OUT_DIR/rclone"
STAMP_FILE="$OUT_DIR/.version"

if [[ -f "$OUT_BIN" && -f "$STAMP_FILE" && "$(cat "$STAMP_FILE")" == "$RCLONE_VERSION" ]]; then
    echo "packaging/fetch_rclone.sh: $OUT_BIN already at $RCLONE_VERSION, skipping fetch"
    lipo -archs "$OUT_BIN"
    exit 0
fi

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR" "$OUT_DIR"

download_and_verify () {
    local arch="$1" expected_sha="$2"
    local zip_name="rclone-${RCLONE_VERSION}-osx-${arch}.zip"
    local url="https://downloads.rclone.org/${RCLONE_VERSION}/${zip_name}"
    local dest="$WORK_DIR/${zip_name}"

    echo "packaging/fetch_rclone.sh: downloading $url"
    curl -fL --retry 3 -o "$dest" "$url"

    local got_sha
    got_sha="$(shasum -a 256 "$dest" | awk '{print $1}')"
    if [[ "$got_sha" != "$expected_sha" ]]; then
        echo "packaging/fetch_rclone.sh: SHA256 MISMATCH for $zip_name" >&2
        echo "  expected: $expected_sha" >&2
        echo "  got:      $got_sha" >&2
        exit 1
    fi
    echo "packaging/fetch_rclone.sh: $zip_name sha256 verified ($got_sha)"

    unzip -q -o "$dest" -d "$WORK_DIR/$arch"
}

download_and_verify "amd64" "$SHA256_AMD64"
download_and_verify "arm64" "$SHA256_ARM64"

AMD64_BIN="$(find "$WORK_DIR/amd64" -type f -name rclone -perm -u+x | head -n1)"
ARM64_BIN="$(find "$WORK_DIR/arm64" -type f -name rclone -perm -u+x | head -n1)"

if [[ -z "$AMD64_BIN" || -z "$ARM64_BIN" ]]; then
    echo "packaging/fetch_rclone.sh: could not find an 'rclone' executable in one of the extracted zips" >&2
    exit 1
fi

echo "packaging/fetch_rclone.sh: amd64 binary archs: $(lipo -archs "$AMD64_BIN")"
echo "packaging/fetch_rclone.sh: arm64 binary archs: $(lipo -archs "$ARM64_BIN")"

lipo -create -output "$OUT_BIN" "$AMD64_BIN" "$ARM64_BIN"
chmod +x "$OUT_BIN"
echo "$RCLONE_VERSION" > "$STAMP_FILE"

rm -rf "$WORK_DIR"

echo "packaging/fetch_rclone.sh: built universal2 rclone at $OUT_BIN"
lipo -archs "$OUT_BIN"
file "$OUT_BIN"
