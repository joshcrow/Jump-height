"""packaging/src/launcher.py — the py2app entry point for "JumpHeight
Sync.app". This is the ONLY new code in the whole bundle: everything the
daemon actually does still lives in tools/puckd/*.py, untouched (the build
brief: do not edit tools/ or web/ or docs/).

Two things this file does, and why each one has to happen HERE rather than
in tools/puckd itself:

  1. Point sys.path at the same repo-relative layout tools/puckd's own
     modules already assume. daemon.py, serial_job.py and flash.py each
     compute their own "REPO" as

         Path(__file__).resolve().parent.parent.parent

     i.e. three directories above wherever the .py file physically sits —
     then read tools/jump (a script with NO .py extension, loaded with
     importlib.machinery.SourceFileLoader, which needs a real path on disk,
     never a zip member) from REPO/tools/jump, and tools/puckd's siblings
     from REPO/tools/puckd/*.

     packaging/setup.py's data_files copy tools/jump and the whole
     tools/puckd/ tree VERBATIM — real, unzipped files, not compiled into
     site-packages.zip — to Contents/Resources/tools/... . So once this
     launcher inserts Contents/Resources/tools onto sys.path before ever
     importing `puckd`, every one of those REPO computations lands on
     Contents/Resources — the exact directory this file just inserted, with
     the path computed once here and re-derived identically, independently,
     by each of those modules. Nothing here duplicates tools/puckd's own
     sys.path lines (daemon.py inserts the same two paths again once it
     loads); that's a harmless redundant insert, not a second source of
     truth — the layout is the one thing this file and daemon.py both read
     off the filesystem, not off each other.

  2. Set PUCKD_RCLONE to the universal2 rclone binary this same build
     bundled at Contents/Resources/rclone (packaging/fetch_rclone.sh).
     tools/puckd/upload.py reads PUCKD_RCLONE, falling back to PATH only if
     it's unset — and Nick's PATH has no rclone on it at all (brew's
     /opt/homebrew/bin/rclone, where it exists, is arm64-only and is not
     bundled). Using setdefault(), not a blind set(), so a developer who
     already exported PUCKD_RCLONE for a bench rehearsal is never overridden
     by the bundled copy.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _resource_path() -> Path:
    # py2app's native launcher stub sets RESOURCEPATH before Python ever
    # starts (py2app/apptemplate/src/main.c) — this is the one env var every
    # py2app app can rely on to find its own Contents/Resources. The
    # fallback (this file's own grandparent-of-grandparent at dev time) only
    # matters for running `python3 packaging/src/launcher.py` directly,
    # unbundled, which the build script never does but a developer might.
    env = os.environ.get("RESOURCEPATH")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


def main() -> None:
    resources = _resource_path()
    tools_dir = resources / "tools"

    sys.path.insert(0, str(tools_dir))            # `from puckd import daemon`
    sys.path.insert(0, str(tools_dir / "puckd"))   # bare `import garmin`, `import flash`

    rclone_bin = resources / "rclone"
    if rclone_bin.is_file():
        os.environ.setdefault("PUCKD_RCLONE", str(rclone_bin))

    from puckd import daemon  # noqa: E402  (path must be set up first)

    daemon.main()


if __name__ == "__main__":
    main()
