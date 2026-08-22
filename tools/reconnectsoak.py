#!/usr/bin/env python3
"""reconnectsoak — cycle BLE connect/disconnect against one puck for hours.

WHY (task #15, BLE dependability). A 3-hour ride is not one connection — it
is dozens: every swim out of range, every crash, every walk up the beach is a
disconnect the watch must recover from. PuckLink's reconnect behavior has
"never been observed on real hardware" (the vision audit's words), and the
puck side has never been cycled deliberately either. This soaks the PUCK half:
does it advertise again promptly after every disconnect, greet correctly on
every re-subscribe, and answer commands on the fresh link — hundreds of times
in a row, with the failure modes counted rather than assumed.

WHAT EACH CYCLE MEASURES
  t_scan     time until the puck's advertisement is seen
  t_connect  scan-match -> connected
  t_ready    connected -> READY greet received (the firmware broadcasts the
             greet on every CCCD enable, so its absence = a real subscribe
             failure, the dualcentral lesson)
  stats_ok   one stats round-trip on the fresh link
  clean      disconnect completed without error

VERDICTS ARE COUNTED, NEVER INFERRED. A cycle that fails any step is recorded
with which step and the exception; uptime is checked every cycle so a puck
reset during the soak is a headline, not a footnote (reas=0 reset of
2026-08-20 remains unexplained).

PIN THE BOARD. Three pucks advertise on this bench tonight. --name is
mandatory, full name, no prefix defaults (bench-playbook rule 2).

Usage:
    python3 tools/reconnectsoak.py --name JumpHeight-8673 --hours 6
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

FIELDS = ["wall", "cycle", "ok", "fail_step", "t_scan", "t_connect", "t_ready",
          "t_stats", "uptime_s", "reset", "detail"]


async def one_cycle(name: str, scan_timeout: float, last_uptime: list):
    """One full connect/verify/disconnect cycle. Returns a row dict."""
    from bleak import BleakClient, BleakScanner
    row = {"ok": 0, "fail_step": "", "t_scan": "", "t_connect": "",
           "t_ready": "", "t_stats": "", "uptime_s": "", "reset": 0,
           "detail": ""}
    lines: list[str] = []
    buf = ""

    def on_notify(_h, data: bytearray):
        nonlocal buf
        buf += data.decode("utf-8", errors="replace")
        while "\n" in buf:
            ln, buf2 = buf.split("\n", 1)
            buf = buf2
            lines.append(ln.rstrip("\r"))

    t0 = time.monotonic()
    dev = await BleakScanner.find_device_by_filter(
        lambda d, adv: (adv.local_name or d.name or "") == name,
        timeout=scan_timeout)
    if dev is None:
        row["fail_step"] = "scan"
        row["detail"] = f"not seen within {scan_timeout}s"
        return row
    row["t_scan"] = f"{time.monotonic()-t0:.2f}"

    t1 = time.monotonic()
    client = BleakClient(dev)
    try:
        await client.connect()
    except Exception as e:
        row["fail_step"] = "connect"
        row["detail"] = f"{type(e).__name__}: {e}"[:150]
        return row
    row["t_connect"] = f"{time.monotonic()-t1:.2f}"

    try:
        t2 = time.monotonic()
        await client.start_notify(NUS_TX, on_notify)
        # READY greet: broadcast on every CCCD enable. Its absence is a
        # subscribe that never reached the firmware.
        ok_ready = False
        while time.monotonic() - t2 < 8.0:
            if any(l.startswith("READY") for l in lines):
                ok_ready = True
                break
            await asyncio.sleep(0.1)
        if not ok_ready:
            row["fail_step"] = "ready"
            row["detail"] = f"no READY in 8s ({len(lines)} lines seen)"
            return row
        row["t_ready"] = f"{time.monotonic()-t2:.2f}"

        t3 = time.monotonic()
        lines.clear()
        await client.write_gatt_char(NUS_RX, b"stats\n", response=False)
        st = None
        while time.monotonic() - t3 < 8.0:
            st = next((l for l in lines if "STATS " in l), None)
            if st:
                break
            await asyncio.sleep(0.1)
        if not st:
            row["fail_step"] = "stats"
            row["detail"] = "no STATS reply in 8s"
            return row
        row["t_stats"] = f"{time.monotonic()-t3:.2f}"

        kv = dict(re.findall(r"(\w+)=([-\w.]+)", st))
        up = float(kv.get("uptime_s", "nan"))
        row["uptime_s"] = f"{up:.0f}"
        if last_uptime[0] is not None and up < last_uptime[0] - 5:
            row["reset"] = 1
        last_uptime[0] = up
        row["ok"] = 1
        return row
    except Exception as e:
        row["fail_step"] = row["fail_step"] or "session"
        row["detail"] = f"{type(e).__name__}: {e}"[:150]
        return row
    finally:
        try:
            await client.disconnect()
        except Exception:
            if row["ok"]:
                row["ok"] = 0
                row["fail_step"] = "disconnect"


async def main_async(args) -> int:
    out = Path(args.out or (REPO / "data" / "logs" /
               f"reconnectsoak-{datetime.now():%Y%m%d-%H%M%S}.csv"))
    out.parent.mkdir(parents=True, exist_ok=True)
    fh = open(out, "a", newline="")
    w = csv.DictWriter(fh, fieldnames=FIELDS)
    w.writeheader(); fh.flush()

    print(f"reconnectsoak -> {out}\n  target {args.name}, {args.hours} h, "
          f"{args.rest}s between cycles")

    end = time.monotonic() + args.hours * 3600
    cycle = ok = resets = 0
    fails: dict = {}
    last_uptime = [None]

    while time.monotonic() < end:
        cycle += 1
        row = await one_cycle(args.name, args.scan_timeout, last_uptime)
        row["wall"] = datetime.now().strftime("%H:%M:%S")
        row["cycle"] = cycle
        w.writerow(row); fh.flush()
        if row["ok"]:
            ok += 1
        else:
            fails[row["fail_step"]] = fails.get(row["fail_step"], 0) + 1
            print(f"  cycle {cycle}: FAIL at {row['fail_step']} — {row['detail']}",
                  flush=True)
        if row["reset"]:
            resets += 1
            print(f"  *** RESET detected at cycle {cycle}", flush=True)
        if cycle % 25 == 0:
            print(f"cycle {cycle}: {ok} ok, fails {fails or 'none'}, "
                  f"resets {resets}", flush=True)
        await asyncio.sleep(args.rest)

    fh.close()
    print(f"\ndone: {cycle} cycles, {ok} ok ({100*ok/max(1,cycle):.1f}%), "
          f"fails {fails or 'none'}, resets {resets}")
    return 0 if (ok == cycle and resets == 0) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True,
                    help="FULL advertised name (three pucks are on this bench)")
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--rest", type=float, default=10.0,
                    help="seconds between cycles (the ride's rhythm, roughly)")
    ap.add_argument("--scan-timeout", type=float, default=20.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if not args.name.startswith("JumpHeight-"):
        print("--name must be a FULL unique name (JumpHeight-XXXX); "
              "a bare prefix is a coin flip on a three-puck bench.")
        return 2
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
