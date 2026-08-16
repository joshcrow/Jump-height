#!/usr/bin/env python3
"""hostsoak — run the REAL firmware core natively, for hours, unattended.

WHAT THIS IS
------------
`env:host` compiles `firmware/src/main.cpp` — byte-for-byte the same
chip-agnostic core the boards run — against host implementations of the four
platform seams. So the command dispatch, the motion gate, the detector, the
jump accounting and the emit layer can be exercised on a laptop, thousands of
times faster than anyone can do it by hand with a puck.

WHAT IT CAN AND CANNOT PROVE — read this before believing a green run
---------------------------------------------------------------------
It CANNOT test the nRF52 storage layer. `platform/host/jh_store.cpp` says so
explicitly: it keeps jumps.csv/trace.csv as plain CSV and is "deliberately NOT
the nRF52 platform's binary-trace/region-file approach". So the block-walking
append-point scan (`findTraceAppendPoint`) — the one that had a missing
watchdog feed, and which has still never run against a full region — is NOT
covered here. Only silicon can close that.

What it CAN do, and none of it has been done before:

  1. **Cross the trace cap.** `trace_append()` returns true exactly once, when
     the trace fills, and main.cpp narrates that transition. No device has ever
     reached this point, so the behaviour past it is unobserved: do jumps still
     get detected? still get stored? does anything spin or crash? A session
     that outlives its trace budget must degrade, not fail.
  2. **Boot/resume cycles.** Every restart re-derives trace byte count and
     header state from what is already on disk. Doing that ~100 times against a
     growing store checks the resume arithmetic far harder than a person
     power-cycling a puck ever will.
  3. **Long-run stability.** Hours of continuous operation, watching for
     crashes, hangs, and monotonicity violations in the jump counter.

Usage:
    python3 tools/hostsoak.py --cycles 100 --seconds 30
    python3 tools/hostsoak.py --prefill 1990000     # start just under the cap
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROGRAM = REPO / "firmware" / ".pio" / "build" / "host" / "program"
TRACE_CAP = 2_000_000          # JH_TRACE_MAX_BYTES, params.gen.h

# Jumps close enough together to generate plenty of records and trace bytes,
# with rests so the motion gate opens and closes repeatedly (that transition is
# what flushes the trace, so it matters that it happens often).
IMU_SCRIPT = """\
# hostsoak: a jump-dense timeline. Loops its last segment forever.
rest 1
jump 0.9
rest 1
jump 1.1
rest 1
chop 2 0.4
jump 0.8
rest 1
jump 1.0
rest 2
"""


def parse_stats(text: str) -> dict:
    line = next((l for l in text.splitlines() if "STATS " in l), None)
    if not line:
        return {}
    return dict(re.findall(r"(\w+)=([-\w.]+)", line.split("STATS ", 1)[1]))


def one_cycle(host_dir: Path, script: Path, seconds: float) -> dict:
    """Boot the core, let it run, ask for stats, shut it down cleanly."""
    env = dict(os.environ, JH_HOST_DIR=str(host_dir), JH_IMU_SCRIPT=str(script))
    t0 = time.time()
    p = subprocess.Popen([str(PROGRAM)], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, env=env)
    out = []
    boot_s = None
    try:
        # Wait for the boot banner, so "did it come up?" is measured, not assumed.
        deadline = time.time() + 20
        while time.time() < deadline:
            line = p.stdout.readline()
            if not line:
                break
            out.append(line)
            if "JumpHeight fw" in line:
                boot_s = time.time() - t0
                break

        time.sleep(seconds)
        p.stdin.write("stats\n")
        p.stdin.flush()
        time.sleep(1.5)
        p.stdin.write("stats\n")
        p.stdin.flush()
        time.sleep(1.5)
    except Exception:
        pass
    finally:
        try:
            p.send_signal(signal.SIGTERM)
            rest = p.communicate(timeout=10)[0] or ""
        except Exception:
            p.kill()
            rest = ""
        out.append(rest)

    text = "".join(out)
    kv = parse_stats(text)
    return {
        "boot_s": boot_s,
        "booted": boot_s is not None,
        "exit": p.returncode,
        "stats": kv,
        "trace_full_narrated": ("trace" in text.lower() and "full" in text.lower()),
        "text_tail": text[-1500:],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cycles", type=int, default=60)
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--prefill", type=int, default=0,
                    help="pre-fill trace.csv to N bytes to reach the cap fast")
    ap.add_argument("--out", default=str(REPO / "data" / "logs" / "hostsoak.json"))
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    if not PROGRAM.exists():
        print(f"host build missing: {PROGRAM}\n  build it: pio run -e host",
              file=sys.stderr)
        return 1

    work = Path(tempfile.mkdtemp(prefix="hostsoak-"))
    host_dir = work / "store"
    host_dir.mkdir()
    script = work / "imu.txt"
    script.write_text(IMU_SCRIPT)

    if args.prefill:
        # Valid CSV, so resume treats it as a real trace rather than junk.
        line = "1000.000,1.000\n"
        n = max(0, args.prefill // len(line))
        with open(host_dir / "trace.csv", "w") as f:
            f.write("t,mag\n")
            f.write(line * n)
        print(f"pre-filled trace.csv to {(host_dir/'trace.csv').stat().st_size} bytes "
              f"(cap {TRACE_CAP})")

    print(f"hostsoak: {args.cycles} cycles x {args.seconds}s   store={host_dir}")
    results = []
    last_jumps = -1
    failures = []
    cap_seen_at = None

    for i in range(1, args.cycles + 1):
        r = one_cycle(host_dir, script, args.seconds)
        kv = r["stats"]
        sj = int(kv["stored_jumps"]) if kv.get("stored_jumps", "").isdigit() else None
        tb = int(kv["trace_bytes"]) if kv.get("trace_bytes", "").isdigit() else None

        if not r["booted"]:
            failures.append(f"cycle {i}: never printed a boot banner")
        if sj is None:
            failures.append(f"cycle {i}: no parseable stats")
        elif last_jumps >= 0 and sj < last_jumps:
            failures.append(f"cycle {i}: stored_jumps WENT BACKWARDS "
                            f"{last_jumps} -> {sj}")
        if sj is not None:
            last_jumps = sj
        if tb is not None and tb >= TRACE_CAP and cap_seen_at is None:
            cap_seen_at = i
            print(f"  *** trace cap reached at cycle {i} (trace_bytes={tb}) ***")

        results.append({"cycle": i, "booted": r["booted"], "boot_s": r["boot_s"],
                        "exit": r["exit"], "stored_jumps": sj, "trace_bytes": tb})
        if i % 5 == 0 or i == 1:
            print(f"  cycle {i:>3}: boot {r['boot_s']}s  jumps={sj}  "
                  f"trace={tb}  exit={r['exit']}", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "cycles": args.cycles, "seconds": args.seconds, "prefill": args.prefill,
        "trace_cap": TRACE_CAP, "cap_reached_at_cycle": cap_seen_at,
        "failures": failures, "results": results,
    }, indent=2))

    print(f"\n=== hostsoak done ===")
    print(f"  cycles run     : {len(results)}")
    print(f"  booted OK      : {sum(1 for r in results if r['booted'])}/{len(results)}")
    print(f"  final jumps    : {last_jumps}")
    print(f"  cap reached    : {'cycle ' + str(cap_seen_at) if cap_seen_at else 'no'}")
    print(f"  failures       : {len(failures)}")
    for f_ in failures[:20]:
        print(f"    - {f_}")
    print(f"  report         : {out}")
    if not args.keep:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
