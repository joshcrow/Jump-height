#!/bin/bash
# packaging/fix_universal_libs.sh — close the arm64-only gap py2app's own
# universal2 build leaves behind.
#
# MEASURED 2026-09-13: even with `--arch=universal2` and a universal2
# python.org framework, two compiled extensions genuinely needed by garth
# (garmin.py's own dependency — see packaging/setup.py's _MEASURED_UNUSED
# comment for how "genuinely needed" was measured) came out of the FIRST
# py2app build as arm64-only:
#
#   lib-dynload/psutil/_psutil_osx.so
#   lib-dynload/pydantic_core/_pydantic_core.so
#
# This is not a py2app bug: PyPI simply does not publish universal2 wheels
# for either package (checked their JSON API) — only separate arm64 and
# x86_64 wheels — and pip, run on this arm64 Mac, installed the arm64 one
# into the shared python.org framework. py2app just copied what was on
# disk; it can't invent an x86_64 slice.
#
# The fix mirrors packaging/fetch_rclone.sh's rclone approach: download the
# OFFICIAL x86_64 wheel for the EXACT version already installed (asked
# `pip show`, not hardcoded — a framework upgrade must not silently fetch a
# mismatched version), verify it against PyPI's own published sha256, pull
# just the .so out of it, and `lipo -create` it together with the arm64
# .so py2app already placed in the built app — never touching the shared
# framework's own site-packages, only the disposable dist/ build output.
#
# Run by packaging/build.sh, right after `setup.py py2app`. Safe to run
# again by hand against an existing dist/ build (already-universal2 files
# are left alone).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="/Library/Frameworks/Python.framework/Versions/3.14/bin/python3"
APP="$HERE/dist/JumpHeight Sync.app"
WORK_DIR="$HERE/vendor/_work-universal-libs"

if [[ ! -d "$APP" ]]; then
    echo "packaging/fix_universal_libs.sh: $APP not found — run setup.py py2app first" >&2
    exit 1
fi

rm -rf "$WORK_DIR"
mkdir -p "$WORK_DIR"

# "import name:pip distribution name:bundled path relative to the .app"
TARGETS=(
    "psutil:psutil:Contents/Resources/lib/python3.14/lib-dynload/psutil/_psutil_osx.so"
    "pydantic_core:pydantic-core:Contents/Resources/lib/python3.14/lib-dynload/pydantic_core/_pydantic_core.so"
)

for entry in "${TARGETS[@]}"; do
    IFS=':' read -r name dist_name bundled_rel <<< "$entry"
    bundled_path="$APP/$bundled_rel"

    if [[ ! -f "$bundled_path" ]]; then
        echo "packaging/fix_universal_libs.sh: $name not present in this build ($bundled_rel) — skipping"
        continue
    fi

    archs="$(lipo -archs "$bundled_path" 2>&1)"
    if [[ "$archs" == *x86_64* && "$archs" == *arm64* ]]; then
        echo "packaging/fix_universal_libs.sh: $name already universal2 ($archs)"
        continue
    fi
    echo "packaging/fix_universal_libs.sh: $name is '$archs' only — patching in an x86_64 slice"

    version="$("$PYTHON" -m pip show "$dist_name" 2>/dev/null | awk -F': ' '/^Version:/{print $2}')"
    if [[ -z "$version" ]]; then
        echo "packaging/fix_universal_libs.sh: no installed version found for $dist_name" >&2
        exit 1
    fi
    py_tag="cp$("$PYTHON" -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]}")')"
    echo "packaging/fix_universal_libs.sh: installed $dist_name==$version, interpreter tag $py_tag"

    target_dir="$WORK_DIR/$name"
    mkdir -p "$target_dir"

    # Ask PyPI's JSON API for this EXACT version's x86_64 macOS wheel and
    # its published sha256 — never a hardcoded URL, so an upgraded
    # framework asks for the version that's actually installed. Fetched
    # with curl (the system CA trust store) rather than this framework
    # Python's own urllib, which has no CA bundle configured out of the
    # box — MEASURED here, not assumed: urllib raised
    # CERTIFICATE_VERIFY_FAILED on this exact machine.
    pypi_json="$target_dir/pypi.json"
    curl -fsSL --retry 3 -o "$pypi_json" "https://pypi.org/pypi/$dist_name/json"

    # MEASURED bug, fixed here: a naive `py_tag in filename` substring
    # match picked psutil's "cp314-cp314t-...whl" (the free-threaded/no-GIL
    # ABI build) over its "cp36-abi3-...whl" (the stable-ABI build this
    # regular, GIL-enabled interpreter actually needs) — "cp314" is a
    # substring of "cp314t" too. Fixed by parsing the wheel filename's
    # dash-separated tags properly (PEP 425) and explicitly rejecting any
    # abi tag ending in "t" unless this interpreter is itself
    # free-threaded (sys._is_gil_enabled() — this framework's is not).
    line="$("$PYTHON" - "$pypi_json" "$version" "$py_tag" <<'PYEOF'
import json, sys

pypi_json, version, py_tag = sys.argv[1:4]
with open(pypi_json) as fh:
    data = json.load(fh)

gil_enabled = getattr(sys, "_is_gil_enabled", lambda: True)()

def tags(filename):
    stem = filename[:-len(".whl")]
    parts = stem.split("-")
    # distribution-version(-build)?-python-abi-platform
    return parts[-3], parts[-2], parts[-1]  # python_tag, abi_tag, platform_tag

candidates = []
for f in data["releases"].get(version, []):
    name = f["filename"]
    if not name.endswith(".whl") or "macosx" not in name or "x86_64" not in name:
        continue
    py_t, abi_t, _ = tags(name)
    if abi_t.endswith("t") and not gil_enabled is False:
        continue  # free-threaded ABI; this interpreter is GIL-enabled
    if abi_t == "abi3" or py_t == py_tag:
        candidates.append((0 if abi_t == "abi3" else 1, f))

if not candidates:
    print("NO_WHEEL")
    raise SystemExit(0)
candidates.sort(key=lambda pair: pair[0])
chosen = candidates[0][1]
print(f"{chosen['url']}\t{chosen['digests']['sha256']}\t{chosen['filename']}")
PYEOF
)"
    if [[ "$line" == "NO_WHEEL" ]]; then
        echo "packaging/fix_universal_libs.sh: no x86_64 wheel found for $dist_name==$version ($py_tag)." >&2
        echo "  Leaving $name arm64-only — a Rosetta/Intel run will surface this as a real ImportError, not a silent gap." >&2
        continue
    fi

    IFS=$'\t' read -r url sha256_expected filename <<< "$line"
    wheel_path="$target_dir/$filename"
    echo "packaging/fix_universal_libs.sh: downloading $filename"
    curl -fL --retry 3 -o "$wheel_path" "$url"

    sha256_got="$(shasum -a 256 "$wheel_path" | awk '{print $1}')"
    if [[ "$sha256_got" != "$sha256_expected" ]]; then
        echo "packaging/fix_universal_libs.sh: SHA256 MISMATCH for $filename" >&2
        echo "  expected: $sha256_expected" >&2
        echo "  got:      $sha256_got" >&2
        exit 1
    fi
    echo "packaging/fix_universal_libs.sh: $filename sha256 verified ($sha256_got)"

    extract_dir="$target_dir/extracted"
    rm -rf "$extract_dir"
    unzip -q -o "$wheel_path" -d "$extract_dir"

    x86_so="$(find "$extract_dir" -iname "*.so" | head -n1)"
    if [[ -z "$x86_so" ]]; then
        echo "packaging/fix_universal_libs.sh: no .so found inside $filename" >&2
        exit 1
    fi

    cp "$bundled_path" "$target_dir/arm64_orig.so"
    lipo -create -output "$bundled_path" "$target_dir/arm64_orig.so" "$x86_so"
    echo "packaging/fix_universal_libs.sh: $name patched -> $(lipo -archs "$bundled_path")"
done

rm -rf "$WORK_DIR"
echo "packaging/fix_universal_libs.sh: done"
