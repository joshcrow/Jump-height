#!/usr/bin/env python3
"""Death-run watcher: poll one pinned puck's STATS over BLE until it dies.

Why this exists (2026-08-24): the DC/DC endurance run. The 2026-08-18 death
run bounded idle draw at <=10 mA by conservation of charge (>=25.7 h on a
250 mAh cell) — measured BEFORE audit F-05 enabled the DC/DC regulator at
every boot. This runs the same method on the same board so the two numbers
are directly comparable, and logs the vbat curve on the way down, which is
the input the gauge re-anchor has been waiting for.

Method notes, so the result can be trusted:
- Probes are `stats` — the side-effect-free liveness command DECISION #34
  mandates. Never `selftest` (its designed failure mode is a reboot).
- Pinned with --name via tools/blecmd.py (which uses blepin's census):
  three boards can advertise, and unpinned reads have corrupted analyses.
- Each poll costs a BLE connection (~seconds). At a 20-minute period that
  perturbation is well under the measurement's own noise, and it is the
  same perturbation the 08-18 run tolerated, so the comparison stays fair.
- Death is declared after MISS_LIMIT consecutive failed polls; the death
  time is the LAST SUCCESSFUL poll, so the uncertainty is one period, and
  a single flaky scan can never fake a death (rule: a reading that did not
  happen is a finding, not a value — but six in a row IS the finding).

Usage:
    nohup python3 tools/deathwatch.py > /dev/null 2>&1 &
Output: data/soaks/dcdc-deathrun-<start>/log.csv  (+ verdict.txt at death)
"""
from __future__ import annotations

import csv
import datetime as dt
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAME = "JumpHeight-E2C4"          # the OG — the only board with a battery
PERIOD_S = 20 * 60
MISS_LIMIT = 6                     # 2 h of silence = dead
POLL_TIMEOUT_S = 90

START = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
OUT = REPO / "data" / "soaks" / f"dcdc-deathrun-{START}"
OUT.mkdir(parents=True, exist_ok=True)
LOG = OUT / "log.csv"


def poll() -> dict | None:
    """One pinned stats read. None on any failure — the caller counts those."""
    try:
        r = subprocess.run(
            [sys.executable, str(REPO / "tools/blecmd.py"),
             "--name", NAME, "stats"],
            capture_output=True, text=True, timeout=POLL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None
    m = re.search(r"STATS .*vbat_mv=(\d+) batt_pct=(\d+) chg=(\d+) "
                  r"uptime_s=([\d.]+)", r.stdout)
    if not m:
        return None
    return {"vbat_mv": int(m.group(1)), "batt_pct": int(m.group(2)),
            "chg": int(m.group(3)), "uptime_s": float(m.group(4))}


def main() -> int:
    new = not LOG.exists()
    misses = 0
    last_ok: dt.datetime | None = None
    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["wallclock_iso", "ok", "vbat_mv", "batt_pct",
                        "chg", "uptime_s"])
            f.flush()
        while True:
            now = dt.datetime.now()
            s = poll()
            if s is None:
                misses += 1
                w.writerow([now.isoformat(timespec="seconds"), 0,
                            "", "", "", ""])
            else:
                misses = 0
                last_ok = now
                w.writerow([now.isoformat(timespec="seconds"), 1,
                            s["vbat_mv"], s["batt_pct"], s["chg"],
                            s["uptime_s"]])
            f.flush()
            if misses >= MISS_LIMIT:
                died = last_ok.isoformat(timespec="seconds") if last_ok \
                    else "never answered"
                (OUT / "verdict.txt").write_text(
                    f"last successful poll: {died}\n"
                    f"declared dead after {MISS_LIMIT} consecutive misses "
                    f"({MISS_LIMIT * PERIOD_S / 3600:.1f} h of silence)\n"
                    f"NOTE: endurance is measured from the UNPLUG (chg 1->0 "
                    f"with vbat falling in this log), not from the first "
                    f"row.\n")
                return 0
            time.sleep(PERIOD_S)


if __name__ == "__main__":
    raise SystemExit(main())
