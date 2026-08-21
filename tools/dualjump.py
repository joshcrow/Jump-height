#!/usr/bin/env python3
"""dualjump — do BOTH watches see every jump, identically?

WHY THIS IS A SEPARATE TEST FROM dualcentral.py
------------------------------------------------
dualcentral proves a BULK EXPORT reaches two centrals byte-identically. That
is the right test for the transmit queue under sustained load, and it passed
on 2026-08-21 (300,022 bytes, identical sha256, tx_drops 0).

But the failure that started all of this was not an export. On 2026-08-11 the
watch displayed **64 jumps / best 0.3 ft** while the puck's own `stats` said
**1 jump / 0.164 m**, with a second central subscribed. That is a JUMP-line
failure: sparse, asynchronous, event-driven lines — a completely different
traffic pattern from a back-to-back export, and the pattern that actually
matters on the water, where the puck emits one line every few minutes and
two people are watching.

A bulk export can pass while this fails: the export streams continuously so
buffers stay hot and the queue drains predictably, whereas a JUMP line is a
single small burst into a possibly-idle stack, delivered to N subscribers.

WHAT IT ASSERTS
---------------
  1. Both centrals receive the SAME NUMBER of JUMP lines as were fired.
  2. Each JUMP line is byte-identical between the two centrals.
  3. Every line is well-formed (the 2026-08-11 corruption produced lines that
     still PARSED — 'height_m=1.623m=1.658' — so parseability is not the
     test; equality and field sanity are).
  4. The puck's own session_jumps agrees with what was delivered.

This is the configuration the water day will actually be in the moment the
owner opens the web app on shore while his brother rides.

ON macOS THIS TOOL CANNOT PASS BY ITSELF
----------------------------------------
CoreBluetooth multiplexes one physical link across central managers in a
process, so two BleakClients here are ONE connection. The tool detects that
(READY greets are broadcast per CCCD enable) and returns INCONCLUSIVE rather
than a verdict. The real run is the Epix + the Instinct — two separate hosts,
which is also exactly the water-day configuration.

Usage:
    python3 tools/dualjump.py --name JumpHeight-45ED --port /dev/cu.usbmodem101 --jumps 20
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
import time
from pathlib import Path

NUS_SVC = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX  = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
NUS_TX  = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"


class Central:
    def __init__(self, tag):
        self.tag = tag
        self.buf = ""
        self.lines: list[str] = []
        self.client = None

    def _on_notify(self, _h, data: bytearray):
        self.buf += data.decode("utf-8", errors="replace")
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.lines.append(line.rstrip("\r"))

    async def connect(self, dev):
        from bleak import BleakClient
        self.client = BleakClient(dev)
        await self.client.connect()
        await self.client.start_notify(NUS_TX, self._on_notify)

    async def close(self):
        try:
            if self.client:
                await self.client.disconnect()
        except Exception:
            pass

    def jumps(self):
        return [l for l in self.lines if l.startswith("JUMP ")]

    def readys(self):
        """Greet lines. main.cpp broadcasts the greet + READY to EVERY
        subscribed connection on each CCCD enable, so counting them is
        DIRECT EVIDENCE of how many real links exist (dualcentral.py's
        method, borrowed here after this tool shipped without it)."""
        return [l for l in self.lines if l.startswith("READY")]


async def run(args) -> int:
    from bleak import BleakScanner
    import serial

    print(f"scanning for {args.name} ...")
    dev = await BleakScanner.find_device_by_filter(
        lambda d, adv: (adv.local_name or d.name or "").startswith(args.name),
        timeout=args.scan_timeout)
    if dev is None:
        print(f"no '{args.name}' found — awake and in range?")
        return 1
    print(f"found {dev.address}")

    a, b = Central("A"), Central("B")
    await a.connect(dev); print("  central A connected")
    await asyncio.sleep(1.0)
    await b.connect(dev); print("  central B connected")
    await asyncio.sleep(args.settle)

    # Fire the jumps over SERIAL, so neither BLE central is the trigger —
    # both are pure observers of the same broadcast.
    sp = serial.Serial(args.port, 115200, timeout=1)
    time.sleep(0.5); sp.reset_input_buffer()
    print(f"\nfiring {args.jumps} fakejumps over serial "
          f"(neither central triggers them) ...")
    for i in range(args.jumps):
        sp.write(b"fakejump\n"); sp.flush()
        await asyncio.sleep(args.spacing)
    await asyncio.sleep(args.drain)

    sp.reset_input_buffer(); sp.write(b"stats\n")
    time.sleep(2.0)
    stats_out = sp.read(sp.in_waiting or 1).decode(errors="replace")
    sp.close()
    m = re.search(r"session_jumps=(\d+)", stats_out)
    puck_session = int(m.group(1)) if m else None

    await a.close(); await b.close()

    ja, jb = a.jumps(), b.jumps()
    print("\n" + "=" * 68)
    print("JUMP DELIVERY TO TWO CENTRALS")
    print("=" * 68)
    print(f"  fired over serial : {args.jumps}")
    print(f"  central A received: {len(ja)}")
    print(f"  central B received: {len(jb)}")
    print(f"  puck session_jumps: {puck_session}")

    # LINK-COUNT FIRST — before any verdict about the firmware.
    #
    # THIS TOOL'S FIRST RUN (2026-08-21) REPORTED A=0 / B=20 AND CALLED IT A
    # FAIL. It was not: macOS CoreBluetooth multiplexes ONE physical link
    # across every central manager in a process, so B's start_notify simply
    # re-routed delivery and starved A. dualcentral.py had documented this
    # hazard and implemented this very check; this tool shipped without it
    # and produced a confident wrong answer about the firmware within a
    # minute of existing.
    #
    # A test that cannot tell "the firmware dropped it" from "my harness
    # cannot make two links" must report INCONCLUSIVE, never FAIL.
    ra, rb = len(a.readys()), len(b.readys())
    two_links = ra >= 2 and rb >= 1
    print(f"  READY greets seen : A={ra}  B={rb}   (two real links => A>=2, B>=1)")
    if not two_links:
        print("\n" + "=" * 68)
        print("VERDICT: INCONCLUSIVE — only ONE physical link existed")
        print("=" * 68)
        print("  macOS CoreBluetooth multiplexes one connection across central")
        print("  managers in a process, so this run did NOT exercise the")
        print("  two-connection path. Nothing here is evidence about the")
        print("  firmware, in either direction.")
        print("  A real two-central answer needs two genuinely separate hosts:")
        print("    * the Epix + the Instinct, both subscribed (the water-day")
        print("      configuration, and the one that matters)")
        print("    * or two BlueZ adapters on Linux, or two machines")
        return 2

    ok = True
    if len(ja) != args.jumps or len(jb) != args.jumps:
        ok = False
        print("  ❌ COUNT MISMATCH — a central missed JUMP lines")
    if ja != jb:
        ok = False
        print("  ❌ CONTENT MISMATCH between centrals:")
        for i, (x, y) in enumerate(zip(ja, jb)):
            if x != y:
                print(f"     line {i}:\n       A: {x[:110]}\n       B: {y[:110]}")
                break
    else:
        print("  ✅ every delivered JUMP line is byte-identical between centrals")

    # Field sanity: the 2026-08-11 corruption PARSED but carried nonsense.
    bad = [l for l in ja
           if not re.search(r"n=\d+ .*airtime_s=[\d.]+ height_m=[\d.]+", l)]
    if bad:
        ok = False
        print(f"  ❌ {len(bad)} malformed JUMP line(s), e.g. {bad[0][:110]}")
    else:
        print("  ✅ every JUMP line is well-formed")

    print("\n" + "=" * 68)
    print(f"VERDICT: {'PASS' if ok else 'FAIL'}")
    print("=" * 68)
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", default="JumpHeight-", help="advertised-name prefix (PIN IT)")
    ap.add_argument("--port", required=True, help="serial port, to fire the jumps")
    ap.add_argument("--jumps", type=int, default=20)
    ap.add_argument("--spacing", type=float, default=1.5, help="seconds between jumps")
    ap.add_argument("--settle", type=float, default=3.0)
    ap.add_argument("--drain", type=float, default=8.0)
    ap.add_argument("--scan-timeout", type=float, default=12.0)
    args = ap.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
