#!/usr/bin/env python3
"""Generate firmware/include/params.gen.h from config/params.json.

config/params.json is the single source of truth for every tunable setting.
The Python simulator reads the JSON directly at runtime; the firmware can't,
so this script bakes it into a C header. `./tools/jump flash` runs this
automatically — you should never need to edit the header by hand.

Usage:
    python3 tools/gen_params.py            # regenerate the header in place
    python3 tools/gen_params.py --check    # exit 1 if the header is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO / "config" / "params.json"
HEADER_PATH = REPO / "firmware" / "include" / "params.gen.h"


def fmt_value(v) -> str:
    """Render a JSON number as a C literal (floats get an f suffix)."""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = f"{v:g}"
        if "." not in s and "e" not in s and "E" not in s:
            s += ".0"  # "8" -> "8.0" so the f suffix forms a valid C literal
        return s + "f"
    raise ValueError(f"unsupported param type: {v!r}")


def fmt_summary(v) -> str:
    """Render a number for the PARAMS summary string (no C suffix)."""
    if isinstance(v, int):
        return str(v)
    return f"{v:g}"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path) as f:
        cfg = json.load(f)
    for section in ("detector", "firmware"):
        if section not in cfg:
            raise SystemExit(f"{path}: missing '{section}' section")
    return cfg


def render_header(cfg: dict) -> str:
    lines = [
        "// GENERATED FILE — do not edit.",
        "// Source of truth: config/params.json  (regenerate: ./tools/jump gen)",
        "#pragma once",
        "",
    ]
    for section in ("detector", "firmware"):
        lines.append(f"// --- {section} ---")
        for key, val in cfg[section].items():
            if key.startswith("_"):
                continue
            lines.append(f"#define JH_{key.upper()} {fmt_value(val)}")
        lines.append("")
    # Summary string of detector params: the firmware echoes this on `info`
    # so the CLI can confirm the flashed device matches the local config.
    summary = " ".join(
        f"{k}={fmt_summary(v)}"
        for k, v in sorted(cfg["detector"].items())
        if not k.startswith("_")
    )
    lines.append(f'#define JH_PARAMS_SUMMARY "{summary}"')
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="don't write; exit 1 if the committed header is stale")
    args = ap.parse_args()

    rendered = render_header(load_config())
    if args.check:
        current = HEADER_PATH.read_text() if HEADER_PATH.exists() else ""
        if current != rendered:
            print(f"STALE: {HEADER_PATH} does not match {CONFIG_PATH} — "
                  "run ./tools/jump gen", file=sys.stderr)
            return 1
        print("params.gen.h is up to date")
        return 0

    HEADER_PATH.parent.mkdir(parents=True, exist_ok=True)
    HEADER_PATH.write_text(rendered)
    print(f"wrote {HEADER_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
