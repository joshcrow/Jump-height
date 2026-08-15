#!/usr/bin/env python3
"""Generate firmware/include/build.gen.h — the firmware's build identity.

WHY THIS EXISTS
---------------
`FW_VERSION` has read "0.4.3" through every fix this project has ever
shipped, including the four-day drive-strength crisis. So the question the
freeze protocol depends on — *is the board in my hand running the code in
this tree?* — has never had an answer. "It says 0.4.3" is not an answer;
every build says 0.4.3.

WHY A SOURCE HASH AND NOT A GIT SHA
-----------------------------------
The obvious move is to bake in `git rev-parse HEAD`. It does not work here,
for two independent reasons:

1. **It is self-invalidating.** This header is a tracked file. Writing HEAD's
   sha into a tracked file changes the tree, which produces a new commit,
   whose sha the header no longer contains. Regenerating chases its own tail
   forever.
2. **A dirty tree makes it lie.** The sha names a commit; the compiler reads
   the working tree. Edit a file without committing and the sha still reports
   the old commit with total confidence. The freeze protocol's whole job is to
   catch exactly that.

So the identity is a hash of **the bytes the compiler actually reads**. It is
deterministic (same sources -> same hash, on any machine, in any order), it
changes if and only if the firmware would change, and a dirty tree cannot
hide from it. That makes the header stable enough to track in git and makes
the check meaningful:

    device INFO src=<hash>  ==  hash recomputed from this working tree
    -> the board is running this code. Not "probably". Exactly.

Deliberately NOT included: a build timestamp. It would make the header change
on every single build, forcing a full rebuild of main.cpp each time and, worse,
making two builds of identical sources look different — which would break the
one comparison this file exists to support.

Usage:
    python3 tools/gen_build.py            # regenerate the header in place
    python3 tools/gen_build.py --check    # exit 1 if the header is stale
    python3 tools/gen_build.py --print    # print the hash, nothing else
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HEADER_PATH = REPO / "firmware" / "include" / "build.gen.h"

# Every input that determines the compiled image. config/params.json is
# included even though params.gen.h is derived from it: if someone edits the
# JSON without regenerating, the hash moves and the mismatch is visible,
# rather than the stale header quietly winning.
HASH_ROOTS = [
    ("firmware/src", ("*.cpp", "*.h", "*.hpp", "*.c")),
    ("firmware/include", ("*.cpp", "*.h", "*.hpp", "*.c")),
]
HASH_FILES = ["config/params.json", "firmware/platformio.ini"]

# The header excludes ITSELF from its own input. Without this the hash would
# be defined in terms of a file containing the hash — no fixed point exists.
EXCLUDE = {HEADER_PATH.resolve()}

HASH_LEN = 8  # 32 bits: ample for "did this change?", short enough to read


def _iter_inputs():
    """Every hashed file, as (repo-relative-posix-path, absolute path)."""
    seen = set()
    for rel_root, patterns in HASH_ROOTS:
        root = REPO / rel_root
        if not root.is_dir():
            continue
        for pattern in patterns:
            for path in root.rglob(pattern):
                rp = path.resolve()
                if rp in EXCLUDE or not path.is_file() or rp in seen:
                    continue
                seen.add(rp)
                yield path.relative_to(REPO).as_posix(), path
    for rel in HASH_FILES:
        path = REPO / rel
        rp = path.resolve()
        if path.is_file() and rp not in EXCLUDE and rp not in seen:
            seen.add(rp)
            yield path.relative_to(REPO).as_posix(), path


def source_hash() -> str:
    """Deterministic hash of the firmware sources.

    Path names are hashed alongside contents, so renaming a file (or adding an
    empty one) moves the hash too. Sorting makes the result independent of
    filesystem iteration order, so two machines agree.
    """
    h = hashlib.sha256()
    for rel, path in sorted(_iter_inputs()):
        h.update(rel.encode())
        h.update(b"\0")
        # Normalise CRLF so a checkout with different line endings does not
        # read as a different build. The compiler does not care; nor should we.
        h.update(path.read_bytes().replace(b"\r\n", b"\n"))
        h.update(b"\0")
    return h.hexdigest()[:HASH_LEN]


def render_header(src: str) -> str:
    return "\n".join([
        "// GENERATED FILE — do not edit.",
        "// Build identity: a hash of the firmware sources themselves.",
        "// Regenerate: ./tools/jump gen      Rationale: tools/gen_build.py",
        "//",
        "// This is what makes the freeze protocol enforceable. If the device's",
        "// INFO line reports a different src= than this tree computes, the board",
        "// is not running this code — whatever git says.",
        "#pragma once",
        "",
        f'#define JH_BUILD_SRC "{src}"',
        "",
    ])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the header is stale (no writes)")
    ap.add_argument("--print", dest="print_only", action="store_true",
                    help="print the source hash and exit")
    args = ap.parse_args()

    src = source_hash()

    if args.print_only:
        print(src)
        return 0

    rendered = render_header(src)

    if args.check:
        current = HEADER_PATH.read_text() if HEADER_PATH.exists() else ""
        if current != rendered:
            print(f"STALE: {HEADER_PATH} does not match the firmware sources — "
                  f"run ./tools/jump gen", file=sys.stderr)
            return 1
        print(f"ok: build identity src={src}")
        return 0

    HEADER_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEADER_PATH.write_text(rendered)
    print(f"wrote {HEADER_PATH}  (src={src})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
