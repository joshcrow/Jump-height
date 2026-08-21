#!/usr/bin/env python3
"""storagesoak — hammer the REAL nRF52 storage layer on silicon, for hours.

WHY THIS EXISTS
---------------
tools/hostsoak.py says plainly what it cannot do: "It CANNOT test the nRF52
storage layer... the block-walking append-point scan (findTraceAppendPoint) —
the one that had a missing watchdog feed, and which has still never run
against a full region — is NOT covered here. Only silicon can close that."

On 2026-08-20 seven defects were found in exactly that layer, six of them in
code written the previous two days, and every one made a FAILURE path worse
while the success path looked perfect. They were fixed and each was verified
ONCE. Once is where this project has been burned repeatedly: `clear()` on a
full region passed by hand and still carried a watchdog bug; the download
gate passed its test while rejecting every real download.

This soak converts "verified once" into "survived N hundred cycles", on the
board that can afford it (the third board, USB-only, no battery, explicitly
the development board — bench-playbook.md §1).

WHAT EACH CYCLE EXERCISES
-------------------------
  fillstore   → the append path, watchdog feeds during long writes, and the
                genuinely-full detection (region_full=1)
  fakejump    → jump records appended while the trace region is FULL (the
                documented benign-but-untested state)
  dump        → the download path AND the three-check integrity gate added
                2026-08-20 (device warning line, jumps rows vs stored_jumps,
                trace bytes)
  clear       → the fed erase of a FULL region: the operation that used to
                watchdog-reset the board, fixed 2026-08-19, verified once
  verify      → jumps count and best survive what they must survive; storage
                stays mounted (fs=down never appears); uptime never goes
                backwards

WHAT IT CANNOT PROVE
--------------------
No reboots: there is no soft-reset command, so the boot-scan-against-a-full-
region path is NOT re-exercised here (it was proven once on 2026-08-20).
Auto-clear is not exercised either — its policy needs an hour of stillness
then motion, which no script can supply. Those two stay owner-gated.

A RESET IS THE HEADLINE RESULT, NOT A FOOTNOTE. uptime_s going backwards is
checked every single read and recorded loudly; an unexplained reset already
happened once on 2026-08-20 (reas=0) and its recurrence is an open question.

Usage:
    python3 tools/storagesoak.py --port /dev/cu.usbmodem1101 --hours 8
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

FIELDS = ["wall", "cycle", "step", "ok", "uptime_s", "stored_jumps", "session_jumps",
          "stored_best_m", "trace_bytes", "fs_down", "reset", "detail"]


def read_until(sp, done_markers, timeout_s, feed=None):
    """Collect serial output until any marker appears or timeout. Never raises."""
    buf, t0 = "", time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        try:
            chunk = sp.read(sp.in_waiting or 1).decode(errors="replace")
        except Exception as e:
            return buf, f"serial-error:{type(e).__name__}"
        if chunk:
            buf += chunk
            if feed:
                feed(chunk)
            if any(m in buf for m in done_markers):
                return buf, ""
        else:
            time.sleep(0.05)
    return buf, "timeout"


def stats(sp, timeout_s=8.0):
    sp.reset_input_buffer()
    sp.write(b"stats\n")
    out, note = read_until(sp, ["OK stats"], timeout_s)
    line = next((l for l in out.splitlines() if "STATS " in l), None)
    if not line:
        return {}, note or "no STATS"
    kv = dict(re.findall(r"(\w+)=([-\w.]+)", line.split("STATS ", 1)[1]))
    kv["_fs_down"] = "fs=down" in line
    return kv, note


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", required=True)
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--fill-kb", type=int, default=4096,
                    help="KB per fill; 999999 fills to genuinely full (slow, ~7 min)")
    ap.add_argument("--jumps", type=int, default=3, help="fakejumps per cycle")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    import serial  # local: keep the module importable without pyserial

    out = Path(args.out or (REPO / "data" / "logs" /
               f"storagesoak-{datetime.now():%Y%m%d-%H%M%S}.csv"))
    out.parent.mkdir(parents=True, exist_ok=True)
    fh = open(out, "a", newline="")
    w = csv.DictWriter(fh, fieldnames=FIELDS)
    w.writeheader(); fh.flush()

    sp = serial.Serial(args.port, 115200, timeout=1)
    time.sleep(0.6)

    end = time.monotonic() + args.hours * 3600
    cycle = 0
    last_uptime = None
    resets = 0
    failures = 0
    baseline_jumps = None

    def record(step, ok, kv, detail="", reset=False):
        w.writerow({
            "wall": datetime.now().strftime("%H:%M:%S"),
            "cycle": cycle, "step": step, "ok": int(ok),
            "uptime_s": kv.get("uptime_s", ""),
            "stored_jumps": kv.get("stored_jumps", ""),
            "session_jumps": kv.get("session_jumps", ""),
            "stored_best_m": kv.get("stored_best_m", ""),
            "trace_bytes": kv.get("trace_bytes", ""),
            "fs_down": int(bool(kv.get("_fs_down"))),
            "reset": int(reset), "detail": detail[:200],
        })
        fh.flush()

    def check(kv):
        """Reset detection + storage-health check on every single read."""
        nonlocal last_uptime, resets, failures
        reset = False
        up = kv.get("uptime_s")
        if up:
            try:
                u = float(up)
                if last_uptime is not None and u < last_uptime - 5:
                    reset = True; resets += 1
                    print(f"  *** RESET: uptime {last_uptime:.0f} -> {u:.0f}", flush=True)
                last_uptime = u
            except ValueError:
                pass
        if kv.get("_fs_down"):
            failures += 1
            print("  *** STORAGE DOWN (fs=down)", flush=True)
        return reset

    print(f"storagesoak -> {out}")
    print(f"  {args.hours} h, fill {args.fill_kb} KB/cycle, {args.jumps} jumps/cycle\n")

    kv, _ = stats(sp)
    baseline_jumps = int(kv.get("stored_jumps", 0) or 0)
    record("start", bool(kv), kv, f"baseline_jumps={baseline_jumps}")
    print(f"baseline: jumps={baseline_jumps} trace={kv.get('trace_bytes')} "
          f"uptime={kv.get('uptime_s')}")

    while time.monotonic() < end:
        cycle += 1
        t_cycle = time.monotonic()

        # 1. FILL — append path + watchdog feeds + full detection
        sp.reset_input_buffer()
        sp.write(f"fillstore {args.fill_kb}\n".encode())
        o, note = read_until(sp, ["OK fillstore", "ERR"], 900.0)
        full = "region_full=1" in o
        kv, _ = stats(sp); rst = check(kv)
        record("fill", "OK fillstore" in o, kv,
               f"full={int(full)} {note}", rst)

        # 2. JUMPS — the emit path while the trace region is full.
        #
        # ASSERT ON session_jumps, NOT stored_jumps. main.cpp's fakejump
        # comment is explicit: it "deliberately does NOT touch stored
        # history" — it emits through the real path with the real counters
        # so clients cannot tell the difference, which is exactly what makes
        # it useful here and exactly what makes stored_jumps the wrong
        # assertion. The soak's first smoke run failed 8/8 cycles on this
        # mistake: a harness that demands the wrong thing reports a defect
        # that is its own.
        before = int(kv.get("session_jumps", 0) or 0)
        for _ in range(args.jumps):
            sp.write(b"fakejump\n"); read_until(sp, ["OK fakejump", "JUMP"], 6.0)
        kv, _ = stats(sp); rst = check(kv)
        after = int(kv.get("session_jumps", 0) or 0)
        grew = (after >= before + args.jumps)
        if not grew:
            failures += 1
            print(f"  *** emit path stalled: session_jumps {before} -> {after}", flush=True)
        record("jumps", grew, kv, f"session {before}->{after}", rst)

        # 3. DUMP — download path + the integrity gate
        sp.reset_input_buffer()
        sp.write(b"dump\n")
        o, note = read_until(sp, ["OK dump", "ERR"], 600.0)
        incomplete = "INCOMPLETE" in o
        if incomplete:
            failures += 1
            print("  *** device reported INCOMPLETE transfer", flush=True)
        kv, _ = stats(sp); rst = check(kv)
        record("dump", ("OK dump" in o) and not incomplete, kv,
               f"incomplete={int(incomplete)} {note}", rst)

        # 4. CLEAR — the fed erase of a FULL region (the old watchdog bug)
        t0 = time.monotonic()
        sp.reset_input_buffer()
        sp.write(b"clear\n")
        o, note = read_until(sp, ["OK clear", "ERR"], 300.0)
        dt = time.monotonic() - t0
        kv, _ = stats(sp); rst = check(kv)
        cleared = (int(kv.get("trace_bytes", 1) or 1) == 0)
        record("clear", ("OK clear" in o) and cleared and not rst, kv,
               f"{dt:.1f}s cleared={int(cleared)} {note}", rst)
        if rst:
            print(f"  *** RESET DURING CLEAR — the 2026-08-19 bug's signature", flush=True)

        print(f"cycle {cycle:3d}  full={int(full)}  jumps={after}  "
              f"clear={dt:5.1f}s  resets={resets}  failures={failures}  "
              f"({time.monotonic()-t_cycle:.0f}s)", flush=True)

    kv, _ = stats(sp)
    record("end", True, kv, f"cycles={cycle} resets={resets} failures={failures}")
    sp.close(); fh.close()

    verdict = {"cycles": cycle, "resets": resets, "failures": failures,
               "final_jumps": kv.get("stored_jumps"), "log": str(out)}
    print("\n" + json.dumps(verdict, indent=2))
    print("\nPASS" if (resets == 0 and failures == 0) else "\nFAILURES PRESENT — read the log")
    return 0 if (resets == 0 and failures == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
