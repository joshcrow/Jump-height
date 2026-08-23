#!/usr/bin/env python3
"""Machine-format detector output, for C++/Python parity checking.

Prints one line per detected jump in the exact format that
firmware/test/host_test.cpp prints, so `./tools/jump simtest` can diff the
two implementations on the same CSV and prove they agree.

Usage:  python3 sim/golden.py < data/example_session.csv
        python3 sim/golden.py path/to/capture.csv
"""

from __future__ import annotations

import os
import sys

from detector import Detector, load_params
from run import load_csv_gyro


def main() -> int:
    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        # Read stdin via a temp-free path: load_csv wants a filename, so
        # spool stdin to a buffer file only if no arg was given.
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as tmp:
            tmp.write(sys.stdin.read())
            path = tmp.name

    times, mag, gyro = load_csv_gyro(path)
    det = Detector(load_params())
    # Mirrors host_test.cpp: the lever is a calibration, not a sample, so it
    # comes from the environment rather than the CSV. Default 0 leaves
    # correct_for_spin() as the identity.
    lever = os.environ.get("JH_SPIN_LEVER_M")
    if lever:
        det.set_spin_lever_m(float(lever))
    # A gyro column routes through the gyro-aware update(), mirroring
    # host_test.cpp's three-column branch. Without this the parity check ran
    # only the accel-only path on BOTH sides and could not see a gyro-path
    # divergence at all (F-16, audit 2026-08-22).
    for i, (t, a) in enumerate(zip(times, mag)):
        ev = det.update(t, a, gyro[i]) if gyro else det.update(t, a)
        if ev is not None:
            print(
                f"JUMP takeoff={ev.takeoff_time_s:.3f} "
                f"airtime_raw={ev.airtime_raw_s:.3f} "
                f"airtime={ev.airtime_s:.3f} height={ev.height_m:.3f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
