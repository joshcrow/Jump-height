#!/usr/bin/env python3
"""battlog — log the puck's battery over BLE, unattended, for hours.

WHY THIS BEATS READING `stats` IN THE MORNING
---------------------------------------------
The documented drain protocol is: unplug, wait, plug in, read `stats`, divide
the battery drop by `uptime_s`. That has a hole in it. **`uptime_s` is time
since BOOT, not time since UNPLUG.** If the board was on USB for part of its
uptime — which is normal, and is exactly what happened on 2026-08-15 — the
denominator is wrong and nobody can tell by how much.

Sampling over BLE removes the dependency entirely: the slope between two of
*our own* timestamped samples is the discharge rate, and it does not care when
the cable came out. It also buys:

  * vbat_mv instead of batt_pct. The gauge reports whole percents on a 250 mAh
    cell, so one count is 2.5 mAh — coarse enough to dominate a short run.
    Millivolts resolve the curve properly.
  * reset detection. If uptime_s ever goes backwards the board rebooted, the
    run is void, and we find out at 3am instead of at breakfast.
  * the SHAPE of the curve, not just its endpoints. Lithium discharge is not
    linear; two endpoints across a knee produce a confident wrong answer.

DOES THE SAMPLING CORRUPT WHAT IT MEASURES?
-------------------------------------------
Connecting costs radio time, so in principle yes. The arithmetic says it is
negligible (~14 s per sample, ~0.04 mAh, against ~180 mAh in the cell), but
this project's standing rule is that a number nobody measured is not a number.

So the schedule is deliberately NOT uniform. It runs dense / quiet / dense:

    phase A   sample every SPACING       (sampled)
    phase B   one long quiet gap          (unsampled)
    phase C   sample every SPACING again  (sampled)

The discharge slope across B is sampling-free. Comparing it against A and C
bounds the cost of sampling from the data itself, inside the same run. Total
connected seconds are logged too, so the contamination can always be recomputed
rather than trusted.

Everything is written append-only, one row per attempt, including failures —
a night that half-worked must still be analysable.

Usage:
    python3 tools/battlog.py --out data/logs/battlog.csv --hours 10
    python3 tools/battlog.py --out … --spacing 1800 --quiet-gap 9000
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BLECMD = REPO / "tools" / "blecmd.py"

FIELDS = ["wall_utc", "wall_local", "mono_s", "phase", "ok",
          "uptime_s", "vbat_mv", "batt_pct", "chg",
          "stored_jumps", "trace_bytes", "connect_s", "note"]


def read_stats(timeout_s: float) -> tuple[dict, float, str]:
    """One BLE `stats` read, as a subprocess.

    Deliberately a subprocess and not an in-process bleak call: a wedged BLE
    stack cannot then hang the whole night's loop — the timeout kills a child
    process instead of deadlocking us. Returns (parsed, seconds, note).
    """
    t0 = time.monotonic()
    try:
        r = subprocess.run(
            [sys.executable, str(BLECMD), "stats"],
            capture_output=True, text=True, timeout=timeout_s)
        out = r.stdout
    except subprocess.TimeoutExpired:
        return {}, time.monotonic() - t0, "timeout"
    except Exception as e:                                   # never die at 3am
        return {}, time.monotonic() - t0, f"error:{type(e).__name__}"
    dt = time.monotonic() - t0

    line = next((l for l in out.splitlines() if "STATS " in l), None)
    if not line:
        return {}, dt, "no STATS line"
    kv = dict(re.findall(r"(\w+)=([-\w.]+)", line.split("STATS ", 1)[1]))
    return kv, dt, ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", type=float, default=10.0)
    ap.add_argument("--spacing", type=float, default=1800.0,
                    help="seconds between samples in the dense phases")
    ap.add_argument("--phase-a", type=float, default=3.0 * 3600,
                    help="length of the first dense phase, seconds")
    ap.add_argument("--quiet-gap", type=float, default=2.5 * 3600,
                    help="length of the unsampled control gap, seconds")
    ap.add_argument("--timeout", type=float, default=90.0)
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    new = not out.exists()
    fh = open(out, "a", newline="")
    w = csv.DictWriter(fh, fieldnames=FIELDS)
    if new:
        w.writeheader()
        fh.flush()

    start = time.monotonic()
    end = start + args.hours * 3600
    a_end = start + args.phase_a
    b_end = a_end + args.quiet_gap

    last_uptime = None
    n_ok = n_fail = 0
    connected_total = 0.0

    print(f"battlog -> {out}")
    print(f"  phase A: dense every {args.spacing:.0f}s for {args.phase_a/3600:.1f} h")
    print(f"  phase B: QUIET for {args.quiet_gap/3600:.1f} h  (the control)")
    print(f"  phase C: dense again until {args.hours:.1f} h elapsed")

    while True:
        now = time.monotonic()
        if now >= end:
            break
        phase = "A" if now < a_end else ("B" if now < b_end else "C")

        # Phase B is the control: sample only at its very end, so the whole gap
        # is one uncontaminated interval.
        if phase == "B":
            sleep_to = min(b_end, end)
            while time.monotonic() < sleep_to:
                time.sleep(min(60.0, sleep_to - time.monotonic()))
            continue

        kv, dt, note = read_stats(args.timeout)
        connected_total += dt
        ok = bool(kv)
        n_ok, n_fail = (n_ok + 1, n_fail) if ok else (n_ok, n_fail + 1)

        up = kv.get("uptime_s")
        if ok and last_uptime is not None and up is not None:
            try:
                if float(up) < float(last_uptime) - 5:
                    note = (note + ";" if note else "") + "RESET DETECTED"
            except ValueError:
                pass
        if ok and up is not None:
            last_uptime = up

        now_dt = datetime.now(timezone.utc)
        w.writerow({
            "wall_utc": now_dt.isoformat(timespec="seconds"),
            "wall_local": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mono_s": f"{time.monotonic() - start:.1f}",
            "phase": phase,
            "ok": int(ok),
            "uptime_s": kv.get("uptime_s", ""),
            "vbat_mv": kv.get("vbat_mv", ""),
            "batt_pct": kv.get("batt_pct", ""),
            "chg": kv.get("chg", ""),
            "stored_jumps": kv.get("stored_jumps", ""),
            "trace_bytes": kv.get("trace_bytes", ""),
            "connect_s": f"{dt:.1f}",
            "note": note,
        })
        fh.flush()
        print(f"[{phase}] {datetime.now():%H:%M:%S} "
              f"{'ok ' if ok else 'FAIL'} vbat={kv.get('vbat_mv','?')}mV "
              f"pct={kv.get('batt_pct','?')} up={kv.get('uptime_s','?')} "
              f"({dt:.1f}s){' ' + note if note else ''}", flush=True)

        # Space samples from the END of the read, so a slow connect does not
        # silently shorten the interval it is supposed to be measuring.
        nxt = time.monotonic() + args.spacing
        while time.monotonic() < nxt and time.monotonic() < end:
            time.sleep(min(30.0, max(0.0, nxt - time.monotonic())))

    fh.close()
    print(f"\ndone: {n_ok} ok, {n_fail} failed, "
          f"{connected_total:.0f}s connected total "
          f"({connected_total/3600:.3f} h of radio)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
