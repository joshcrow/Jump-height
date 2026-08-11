#!/usr/bin/env python3
"""chargelog — log battery telemetry over USB serial for hours.

BENCH utility, the patient half of SENSE_FIRST_BOOT item 24. The ADC-vs-
meter check needs TWO points at different voltages to tell a proportional
error (SAADC acquisition time, the standing theory) from a fixed offset
(reference or divider constant) — they are indistinguishable at one
voltage. It also needs a full charge to check that batt_pct tracks the
resting curve at all.

Both wants are the same six-hour charge, so this just watches it.

Serial, not BLE (tools/blecmd.py): a charging puck has the cable in by
definition, so USB is available and free — and unlike BLE it neither
drops nor draws the ~2 mA that would slow the very charge being measured.

Usage:
    python3 tools/chargelog.py                       # every 60 s to stdout
    python3 tools/chargelog.py --every 30 --out /tmp/charge.csv

Writes a CSV (iso_time, vbat_mv, batt_pct, chg) alongside the readable
log, so the curve can be plotted without re-parsing wire lines.

SPDX-License-Identifier: MIT
"""

import argparse
import sys
import time
from datetime import datetime

try:
    import serial
except ImportError:
    sys.exit("pyserial not installed — pip3 install pyserial")


def read_stats(port, baud, timeout=4.0):
    """Send `stats`, return the parsed key=value dict from the STATS line."""
    with serial.Serial(port, baud, timeout=0.4) as s:
        s.reset_input_buffer()
        s.write(b"stats\n")
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = s.readline().decode("utf-8", errors="replace").strip()
            if line.startswith("STATS"):
                kv = {}
                for tok in line.split()[1:]:
                    if "=" in tok:
                        k, v = tok.split("=", 1)
                        kv[k] = v
                return kv
    return None


def main():
    p = argparse.ArgumentParser(description="Log battery telemetry over serial.")
    p.add_argument("--port", default="/dev/cu.usbmodem101")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--every", type=float, default=60.0, help="seconds between reads")
    p.add_argument("--out", help="also append CSV here")
    args = p.parse_args()

    csv = None
    if args.out:
        new = True
        try:
            new = open(args.out).read(1) == ""
        except FileNotFoundError:
            pass
        csv = open(args.out, "a", buffering=1)
        if new:
            csv.write("iso_time,vbat_mv,batt_pct,chg\n")

    print(f"logging {args.port} every {args.every:.0f}s — ^C to stop")
    while True:
        stamp = datetime.now().isoformat(timespec="seconds")
        try:
            kv = read_stats(args.port, args.baud)
        except Exception as e:
            # The port vanishes on unplug/reset. That is a gap in the log,
            # not the end of it — the whole point is to outlive the session.
            print(f"[{stamp}] port error ({type(e).__name__}: {e})")
            kv = None
        if kv is None:
            # NEVER silent. An earlier version printed nothing here, and a
            # board that had been sitting in the UF2 bootloader for six
            # minutes looked exactly like a log that had not ticked yet.
            # A reading that did not happen is itself the finding.
            print(f"[{stamp}] NO REPLY — port opens but no STATS line. "
                  f"App not running? (UF2 bootloader mounts /Volumes/XIAO-SENSE; "
                  f"single-tap reset to leave it)")
        elif "vbat_mv" in kv:
            mv, pct, chg = kv["vbat_mv"], kv.get("batt_pct", ""), kv.get("chg", "")
            print(f"[{stamp}] vbat_mv={mv} batt_pct={pct} chg={chg}")
            if csv:
                csv.write(f"{stamp},{mv},{pct},{chg}\n")
        else:
            print(f"[{stamp}] STATS had no battery keys — not a Sense-class board?")
        time.sleep(args.every)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped")
