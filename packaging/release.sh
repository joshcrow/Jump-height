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

# The asset is public the moment `gh release create` returns, but the manifest
# is only live once Pages rebuilds — so the ORDER below matters: the release
# exists first (done above), the manifest points at it second. A manifest that
# went live before the asset would have every agent fail its download stage.
cat <<EOF

== release.sh: done. NOTHING WAS COMMITTED. Run these yourself: ==

  git add packaging/setup.py web/app/latest.json
  git commit -m "Release $VERSION: the app updates itself to it"
  git push

Until that push lands and Pages rebuilds, installed agents still see the
previous manifest and will not update. Verify afterwards with:

  curl -s https://joshcrow.github.io/Jump-height/app/latest.json
  curl -sIL "$ASSET_URL" | head -n1
EOF
