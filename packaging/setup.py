"""packaging/setup.py — py2app build script for "JumpHeight Sync.app".

Run this with the python.org universal2 build, never the arm64-only
Homebrew python that also lives on this machine:

    /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 \\
        packaging/setup.py py2app --arch=universal2

(packaging/build.sh does exactly this, plus the rclone fetch below.)

WHAT GETS BUNDLED AND WHY, in one place:

  * The app's own code is packaging/src/launcher.py (see its docstring) —
    it is the only file this build adds; tools/puckd/*.py is never edited.

  * tools/jump and the whole tools/puckd/ tree (including setup/*.html|css|
    js) are copied in via `data_files`, NOT via py2app's `packages` option.
    `packages` would let py2app "own" them as an importable package and
    possibly zip them into site-packages.zip — but tools/jump is loaded at
    runtime with importlib.machinery.SourceFileLoader against a literal
    filesystem path (tools/puckd/serial_job.py, flash.py, and this repo's
    own tools/tests/test_ingest.py all use the same technique), which
    cannot read a path inside a zip. `data_files` copies them as inert,
    unzipped files, preserving the exact tools/puckd/-relative layout those
    modules' own `Path(__file__).resolve().parent.parent.parent` REPO
    computations expect once they land under Contents/Resources/tools/ —
    see packaging/src/launcher.py's docstring for the other half of this.

  * pyserial, garth and rumps are NOT imported anywhere in launcher.py or in
    any module py2app's dependency scanner (modulegraph) would otherwise
    walk — menubar.py imports rumps lazily, inside a function body, and
    every tools/puckd module reachable from launcher.py's static imports is
    shipped as inert data, per the point above, so modulegraph never scans
    it for its own imports either. They are named explicitly in `includes`
    below so modulegraph starts from them directly and pulls in their real
    transitive dependencies (garth -> requests/oauthlib/requests-oauthlib;
    rumps -> the pyobjc bridge: objc, Foundation, AppKit) the normal way.
    pyobjc itself is one of those transitive pulls, not a separate include.

  * The bundled rclone binary (packaging/fetch_rclone.sh's universal2
    build, lipo'd from the two official per-arch downloads — see that
    script for why: rclone.org currently ships no single "macOS universal"
    zip) is added as a top-level data file, landing at Contents/Resources/
    rclone; packaging/src/launcher.py points PUCKD_RCLONE at it.
"""

from __future__ import annotations

import sys
from pathlib import Path

from setuptools import setup

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
TOOLS_DIR = REPO / "tools"
PUCKD_DIR = TOOLS_DIR / "puckd"
JUMP_FILE = TOOLS_DIR / "jump"
VENDOR_RCLONE = HERE / "vendor" / "rclone" / "rclone"

APP_VERSION = "1.0.0"

if not JUMP_FILE.is_file():
    sys.exit(f"packaging/setup.py: {JUMP_FILE} not found — run from a full checkout")

if not PUCKD_DIR.is_dir():
    sys.exit(f"packaging/setup.py: {PUCKD_DIR} not found — run from a full checkout")

if not VENDOR_RCLONE.is_file():
    sys.exit(
        f"packaging/setup.py: {VENDOR_RCLONE} not found.\n"
        "Run packaging/fetch_rclone.sh first (packaging/build.sh does this "
        "automatically) — a build with no rclone bundled is a build that "
        "cannot sync anything, so this stops here rather than shipping one."
    )

APP = [str(HERE / "src" / "launcher.py")]

_puckd_top_py = sorted(str(p) for p in PUCKD_DIR.glob("*.py"))
_setup_files = sorted(
    str(p) for p in (PUCKD_DIR / "setup").iterdir() if p.is_file()
)

if not _puckd_top_py:
    sys.exit(f"packaging/setup.py: no .py files found directly under {PUCKD_DIR}")
if not _setup_files:
    sys.exit(f"packaging/setup.py: no files found under {PUCKD_DIR / 'setup'}")

_asset_files = sorted(str(p) for p in (PUCKD_DIR / "assets").glob("*.png"))
if not _asset_files:
    sys.exit(f"packaging/setup.py: no menu-bar images under {PUCKD_DIR / 'assets'} (run icon/make_icon.py)")

DATA_FILES = [
    ("tools", [str(JUMP_FILE)]),
    ("tools/puckd", _puckd_top_py),
    ("tools/puckd/setup", _setup_files),
    ("tools/puckd/assets", _asset_files),
    ("", [str(VENDOR_RCLONE)]),
]


# MEASURED, not guessed (2026-09-13): this Python.org framework install is
# a shared, general-purpose dev environment (it also carries editable
# installs of two unrelated AI-agent side projects that pull in fastapi,
# mcp, anthropic, openai, opentelemetry, pandas, matplotlib, ... via
# garth's own real dependency, `logfire`). modulegraph's static bytecode
# scan cannot tell "logfire imports numpy/pandas conditionally, never
# exercised by garth's actual login flow" from "the app needs numpy" — it
# only knows the name resolves on THIS machine, so it bundled all of it: a
# ~330 MB app for a menu-bar rclone/serial daemon. Names below were cut by
# DIFFING sys.modules before/after `import garth`, `import garth.sso`,
# `import garth.http`, `import garth.stats`, `import garth.data` (every
# garth submodule tools/puckd/garmin.py touches) and separately `import
# rumps` / `import serial` — three real interpreter runs, not a guess —
# then excluding every third-party top-level name from the first build's
# actual bundle contents that did NOT show up in any of those three
# import traces. Excluding a name that garth needed would surface
# immediately as an ImportError in this build's own Rosetta/native smoke
# test (see the test transcripts) — it did not.
_MEASURED_UNUSED = [
    "annotated_doc", "anthropic", "anyio", "sniffio", "argcomplete",
    "click", "colorama", "contourpy", "cryptography", "cffi", "pycparser",
    "cycler", "dateutil", "distro", "docstring_parser", "et_xmlfile",
    "fastapi", "fontTools", "h11", "httpcore", "httptools", "httpx",
    "httpx_sse", "iniconfig", "jaraco", "jinja2", "markupsafe", "jiter",
    "jsonschema", "jsonschema_specifications", "jwt", "rsa", "pyasn1",
    "pyasn1_modules", "kiwisolver", "markdown_it", "mdurl", "mcp",
    "mpl_toolkits", "multipart", "python_multipart", "numpy", "openai",
    "openpyxl", "PIL", "pluggy", "pyparsing", "pytest", "referencing",
    "rpds", "sse_starlette", "starlette", "uvicorn", "watchfiles",
    "wsproto", "yaml", "tkinter", "matplotlib", "pandas", "tornado",
    "websockets",
    # Not a garth/rumps/serial dependency at all — a compiled mypyc build of
    # mypy itself, present only because this shared dev environment has it
    # installed. MEASURED arm64-only (packaging/fix_universal_libs.sh's own
    # scan) with no x86_64 wheel worth chasing for a tool the app never
    # imports — excluded rather than patched.
    "mypy",
]

OPTIONS = {
    "py2app": {
        "arch": "universal2",
        "iconfile": str(Path(__file__).resolve().parent / "icon" / "JumpHeight.icns"),
        "argv_emulation": False,
        "optimize": 0,
        "includes": [
            "serial",
            "serial.tools.list_ports",
            "rumps",
            "garth",
        ],
        "excludes": _MEASURED_UNUSED,
        "plist": {
            "CFBundleName": "JumpHeight Sync",
            "CFBundleDisplayName": "JumpHeight Sync",
            "CFBundleIdentifier": "com.jumpheight.puckd",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "LSUIElement": True,
            "LSMinimumSystemVersion": "11.0",
            "NSHumanReadableCopyright": "JumpHeight",
        },
    }
}

setup(
    name="JumpHeight Sync",
    app=APP,
    data_files=DATA_FILES,
    options=OPTIONS,
    setup_requires=["py2app"],
)
