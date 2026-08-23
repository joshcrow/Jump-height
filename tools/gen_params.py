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
MONKEYC_PATH = REPO / "garmin" / "jumpfield" / "source" / "Params.gen.mc"


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
    if isinstance(v, str):
        # Strings arrive with the 'shared' section (F-18): the BLE UUIDs are
        # the same text in C++, Monkey C, Python and JS, and were hand-copied
        # into six files.
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    raise ValueError(f"unsupported param type: {v!r}")


def fmt_summary(v) -> str:
    """Render a number for the PARAMS summary string (no C suffix)."""
    if isinstance(v, int):
        return str(v)
    return f"{v:g}"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path) as f:
        cfg = json.load(f)
    for section in ("detector", "firmware", "shared"):
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
    for section in ("detector", "firmware", "shared"):
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


def fmt_mc(v) -> str:
    """Render a JSON value as a Monkey C literal."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, str):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    raise ValueError(f"unsupported param type: {v!r}")


def render_monkeyc(cfg: dict) -> str:
    """Emit the SHARED constants for the watch app (audit F-18, 2026-08-22).

    Only the 'shared' section. The detector's thresholds are deliberately not
    generated here: Model.mc's MAX_HEIGHT_M / MAX_AIRTIME_S are impossibility
    bounds for its corruption gate ("~5x any real wing jump; rejects nonsense
    only"), NOT the detector's tuned thresholds, and generating one from the
    other would couple two values that are supposed to differ. The re-count
    this ticket asked for is what surfaced that.
    """
    lines = [
        "// GENERATED FILE — do not edit.",
        "// Source of truth: config/params.json  (regenerate: ./tools/jump gen)",
        "//",
        "// Constants that exist in more than one language. Before this file the",
        "// m->ft factor was written out by hand in nine places across five",
        "// languages and the BLE UUIDs in six files; UnitsFmt.mc's own comment",
        "// said \"same constant as web/app.js\", which is a duplicate",
        "// acknowledged in a comment rather than removed.",
        "module Params {",
    ]
    for key, val in cfg["shared"].items():
        if key.startswith("_"):
            continue
        lines.append(f"    const {key.upper()} = {fmt_mc(val)};")
    lines.append("}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="don't write; exit 1 if the committed header is stale")
    args = ap.parse_args()

    cfg = load_config()
    outputs = [
        (HEADER_PATH, render_header(cfg)),
        (MONKEYC_PATH, render_monkeyc(cfg)),
    ]

    if args.check:
        # --check covers EVERY output, not just the first (F-18). A generator
        # whose staleness check only guards one of its files will let the
        # others drift, which is the failure this ticket exists to prevent.
        stale = [p for p, want in outputs
                 if (p.read_text() if p.exists() else "") != want]
        if stale:
            for p in stale:
                print(f"STALE: {p} does not match {CONFIG_PATH} — "
                      "run ./tools/jump gen", file=sys.stderr)
            return 1
        print(f"generated params are up to date ({len(outputs)} files)")
        return 0

    for p, want in outputs:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(want)
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
