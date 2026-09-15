#!/bin/bash
# packaging/release.sh <version> — cut a release the installed app can find
# by itself.
#
# THE WHOLE POINT: the rider is on an Intel Mac 300 miles away and must never
# see a dialog, a download, or Gatekeeper. tools/puckd/selfupdate.py does the
# swapping; this script produces the two things it needs and nothing else:
#
#   1. a GitHub RELEASE asset, dist/JumpHeight-Sync-<version>.zip. GitHub
#      Pages caps a file at 100 MB and the zipped bundle is ~110 MB, so the
#      payload cannot live in web/ at all. Releases has no such cap.
#   2. web/app/latest.json — served by Pages, the manifest the agent polls
#      every 6 h — carrying that asset's PUBLIC download URL, its sha256 and
#      its byte count.
#
# The sha256 is not decoration: selfupdate.apply() refuses to unpack a single
# byte until the downloaded zip matches it (the same gate flash.py:302-316
# holds for the puck's firmware). A manifest written by hand is a manifest
# with a hand-typed hash in it, which is why this script writes it.
#
# THIS SCRIPT NEVER COMMITS AND NEVER PUSHES. It prints the git commands the
# owner runs. The reason is in MEMORY: staging with `git add -A` while agents
# were running swept unreviewed edits into a commit twice, once onto the live
# rider page. A release script that commits is that mistake with a schedule.
#
# Usage:
#   packaging/release.sh 1.0.1
#
# Requires: `gh` authenticated (it is, as joshcrow), and the python.org
# universal2 build packaging/build.sh insists on.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
    echo "usage: packaging/release.sh <version>    e.g. packaging/release.sh 1.0.1" >&2
    exit 2
fi
if ! [[ "$VERSION" =~ ^[0-9]+(\.[0-9]+)*$ ]]; then
    # selfupdate._VERSION_RE, restated: the agent compares versions as dotted
    # integers, so anything else would publish a version it cannot compare and
    # would therefore silently never install.
    echo "release.sh: version must be dotted integers (e.g. 1.0.1), got '$VERSION'" >&2
    exit 2
fi

if ! command -v gh >/dev/null 2>&1; then
    echo "release.sh: gh not found — the zip is ~110 MB and cannot be served by Pages." >&2
    exit 1
fi

TAG="v$VERSION"
ZIP_NAME="JumpHeight-Sync-$VERSION.zip"
ZIP_PATH="$HERE/dist/$ZIP_NAME"
MANIFEST="$REPO/web/app/latest.json"
ASSET_URL="https://github.com/joshcrow/Jump-height/releases/download/$TAG/$ZIP_NAME"

echo "== release.sh: bumping APP_VERSION in packaging/setup.py to $VERSION =="
/usr/bin/sed -i '' -E "s/^APP_VERSION = \"[^\"]*\"/APP_VERSION = \"$VERSION\"/" "$HERE/setup.py"
grep -n '^APP_VERSION' "$HERE/setup.py"
# ASSERT it took. sed exits 0 whether or not the pattern matched, so a
# renamed constant or a changed spelling would leave the OLD version in the
# bundle while this script publishes a manifest claiming the new one. The
# installed agent would then download ~110 MB, swap in a bundle whose
# Info.plist still says the old version, and -- because the manifest still
# reads newer -- do it again six hours later, forever, on a Mac 300 miles
# away. selfupdate.apply() refuses such a bundle at its unpack stage now;
# this stops it ever being published.
if ! grep -q "^APP_VERSION = \"$VERSION\"$" "$HERE/setup.py"; then
    echo "release.sh: APP_VERSION in packaging/setup.py is not $VERSION after the bump" >&2
    echo "            (the sed pattern did not match -- fix it before releasing)" >&2
    exit 1
fi

echo "== release.sh: building =="
"$HERE/build.sh"

if [[ ! -f "$ZIP_PATH" ]]; then
    echo "release.sh: build.sh did not produce $ZIP_PATH" >&2
    exit 1
fi

SHA256="$(shasum -a 256 "$ZIP_PATH" | awk '{print $1}')"
BYTES="$(stat -f%z "$ZIP_PATH")"
BUILT_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "zip:    $ZIP_PATH"
echo "bytes:  $BYTES"
echo "sha256: $SHA256"

echo "== release.sh: creating GitHub release $TAG =="
if gh release view "$TAG" >/dev/null 2>&1; then
    echo "release.sh: $TAG already exists — uploading the asset over it"
    gh release upload "$TAG" "$ZIP_PATH" --clobber
else
    gh release create "$TAG" "$ZIP_PATH" \
        --title "JumpHeight Sync $VERSION" \
        --notes "The Mac sync agent, v$VERSION.

Installed copies update themselves silently: the running agent checks
web/app/latest.json every 6 h while no puck is attached, verifies this
asset's sha256 before unpacking it, swaps the bundle, and restarts via
launchd. Nobody has to download this by hand."
fi

echo "== release.sh: writing $MANIFEST =="
mkdir -p "$(dirname "$MANIFEST")"
/usr/bin/python3 - "$MANIFEST" "$VERSION" "$ASSET_URL" "$SHA256" "$BYTES" "$BUILT_UTC" <<'PY'
import json, sys
path, version, url, sha256, size, built = sys.argv[1:7]
with open(path, "w") as f:
    json.dump({
        "version": version,
        "url": url,
        "sha256": sha256,
        "bytes": int(size),
        "built_utc": built,
        "note": "Written by packaging/release.sh. tools/puckd/selfupdate.py "
                "verifies this sha256 against the downloaded zip before it "
                "unpacks anything.",
    }, f, indent=2)
    f.write("\n")
print(open(path).read())
PY

# ---------------------------------------------------------------------------
# web/firmware/latest.json's DFU fields.
#
# A DIFFERENT manifest from the one above: web/app/latest.json is the Mac
# app's, web/firmware/latest.json is the PUCK's. build.sh just produced
# web/firmware/jumpheight-<tree src>.zip; this records its size and hash so
# tools/puckd/flash.py can verify the download before it uploads a byte
# (the same gate the .uf2 has had since the start).
#
# THE GUARD THAT MATTERS: the zip is written into the manifest ONLY when it
# is the package for the build the manifest already publishes as `src`. The
# firmware manifest is written by hand and moves on its own schedule; a tree
# that has been built past it would otherwise publish a DFU package for a
# DIFFERENT firmware than `file`/`sha256` name, and the serial-DFU fallback
# would quietly flash the wrong build.
FW_MANIFEST="$REPO/web/firmware/latest.json"
if [[ ! -f "$FW_MANIFEST" ]]; then
    echo "== release.sh: no $FW_MANIFEST — nothing to record for the puck =="
else
    FW_SRC="$(/usr/bin/python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("src",""))' "$FW_MANIFEST")"
    FW_DFU_ZIP="$REPO/web/firmware/jumpheight-$FW_SRC.zip"
    if [[ -z "$FW_SRC" ]]; then
        echo "== release.sh: $FW_MANIFEST has no src — leaving its DFU fields alone =="
    elif [[ ! -f "$FW_DFU_ZIP" ]]; then
        # STRIP, don't leave. A manifest whose `src` has moved on but whose
        # dfu_file still names the PREVIOUS build publishes a fallback that
        # would upload the wrong firmware. flash.py's stage 6 catches that
        # (src_after != manifest src -> ok=False), but the right answer is
        # not to offer it: no package for this src means no fallback.
        echo "== release.sh: no $FW_DFU_ZIP (build.sh skipped it, or the tree is"
        echo "   built past the published src=$FW_SRC) — clearing any stale DFU fields =="
        /usr/bin/python3 - "$FW_MANIFEST" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as f:
    manifest = json.load(f)
removed = [k for k in ("dfu_file", "dfu_bytes", "dfu_sha256") if k in manifest]
for k in removed:
    manifest.pop(k)
if removed:
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")
print("  removed: " + (", ".join(removed) if removed else "nothing to remove"))
PY
    else
        echo "== release.sh: recording the puck's DFU package (src=$FW_SRC) =="
        /usr/bin/python3 - "$FW_MANIFEST" "$(basename "$FW_DFU_ZIP")" \
            "$(stat -f%z "$FW_DFU_ZIP")" \
            "$(shasum -a 256 "$FW_DFU_ZIP" | awk '{print $1}')" <<'PY'
import json, sys
path, name, size, sha = sys.argv[1:5]
with open(path) as f:
    manifest = json.load(f)
manifest["dfu_file"] = name
manifest["dfu_bytes"] = int(size)
manifest["dfu_sha256"] = sha
with open(path, "w") as f:
    json.dump(manifest, f, indent=2)
    f.write("\n")
print(f"  dfu_file={name} dfu_bytes={size} dfu_sha256={sha}")
PY
    fi
fi

# The asset is public the moment `gh release create` returns, but the manifest
# is only live once Pages rebuilds — so the ORDER below matters: the release
# exists first (done above), the manifest points at it second. A manifest that
# went live before the asset would have every agent fail its download stage.
cat <<EOF

== release.sh: done. NOTHING WAS COMMITTED. Run these yourself: ==

  git add packaging/setup.py web/app/latest.json
  git add web/firmware/latest.json web/firmware/*.zip     # only if they changed
  git commit -m "Release $VERSION: the app updates itself to it"
  git push

Until that push lands and Pages rebuilds, installed agents still see the
previous manifest and will not update. Verify afterwards with:

  curl -s https://joshcrow.github.io/Jump-height/app/latest.json
  curl -sIL "$ASSET_URL" | head -n1
EOF
