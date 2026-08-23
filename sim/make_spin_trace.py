#!/usr/bin/env python3
"""Regenerate data/spin_railed_gyro.csv — the gyro-path parity fixture.

Committed as a file (like data/example_session.csv) so simtest and CI do not
depend on running this; this script exists so the trace can be understood and
adjusted rather than being an opaque table of numbers.

WHAT IT HAS TO PROVE (audit F-16, 2026-08-22). jump_detector.h returns the raw
magnitude when the centripetal term exceeds +-16 g, because a sample that far
out cannot be corrected and subtracting it MANUFACTURES free-fall. The Python
mirror was missing that guard, and the parity harness could not see it: both
sides only ever ran the accel-only update().

So the trace has to make the guard change the OUTCOME, not just an intermediate
value. The first attempt did not — it put the railed gyro inside a genuine
free-fall, where guarded and unguarded both read as free-fall, and produced
zero jumps on every configuration. Green, and worth nothing.

This one puts the railed gyro on a board that is still RIDING at 1 g:

  with the guard     1 g passes through          -> no takeoff   -> 1 jump
  without the guard  sqrt(1 - 34.9^2) clamps to 0 -> free-fall!  -> 2 jumps

The second jump is entirely fabricated, which is the bug in one line. Spin
stops on impact — as it physically does — so the landing spike is not itself
zeroed, and the fake flight completes into a jump instead of being rejected.

The lever arm is a calibration, not a sample, so it cannot ride in the CSV:
both harnesses read JH_SPIN_LEVER_M (simtest sets 0.5).

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

HZ = 200.0
G = 9.80665
LEVER_M = 0.5          # must match JH_SPIN_LEVER_M in tools/jump's simtest
OUT = Path(__file__).resolve().parent.parent / "data" / "spin_railed_gyro.csv"


def rot_g(dps: float) -> float:
    """The centripetal term the detector will try to remove, in g."""
    w = math.radians(dps)
    return (w * w * LEVER_M) / G


def main() -> int:
    rows: list[tuple] = []
    t = 0.0

    def add(seconds: float, mag: float, gyro: float) -> None:
        nonlocal t
        for _ in range(int(seconds * HZ)):
            rows.append((round(t, 5), round(mag, 4), round(gyro, 2)))
            t += 1.0 / HZ

    add(1.50, 1.00, 25.0)     # riding
    # A real jump at low spin, so the correction is negligible (0.025 g). Proves
    # the guard does not cost a genuine detection.
    add(0.30, 3.20, 40.0)     # loading
    add(0.60, 0.10, 40.0)     # airborne
    add(0.05, 4.50, 40.0)     # landing
    add(1.00, 1.00, 30.0)     # settle
    # The case the guard exists for: still riding at 1 g, gyro railed.
    add(0.80, 1.00, 1500.0)   # rot_g ~= 34.9 g
    add(0.05, 4.50, 90.0)     # impact stops the spin
    add(1.50, 1.00, 25.0)

    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "mag", "gyro_dps"])
        w.writerows(rows)

    print(f"wrote {OUT} — {len(rows)} rows, {rows[-1][0]:.2f} s")
    print(f"  rot_g(1500 dps) = {rot_g(1500):.1f} g   (guard trips above 16)")
    print(f"  rot_g(  40 dps) = {rot_g(40):.4f} g  (real jump unaffected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
