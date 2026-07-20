#!/usr/bin/env python3
"""Machine-format detector output, for C++/Python parity checking.

Prints one line per detected jump in the exact format that
firmware/test/host_test.cpp prints, so `./tools/jump simtest` can diff the
two implementations on the same CSV and prove they agree.

Usage:  python3 sim/golden.py < data/example_session.csv
        python3 sim/golden.py path/to/capture.csv
"""

from __future__ import annotations

import sys

from detector import Detector, load_params
from run import load_csv


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

    times, mag = load_csv(path)
    det = Detector(load_params())
    for t, a in zip(times, mag):
        ev = det.update(t, a)
        if ev is not None:
            print(
                f"JUMP takeoff={ev.takeoff_time_s:.3f} "
                f"airtime_raw={ev.airtime_raw_s:.3f} "
                f"airtime={ev.airtime_s:.3f} height={ev.height_m:.3f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
