#!/usr/bin/env python3
"""blecmd — talk to the puck over BLE from the Mac, no browser, no phone.

BENCH utility. The device speaks ONE protocol (newline-terminated text,
see firmware/src/main.cpp) over two links; ./tools/jump owns the USB
serial one. This owns the BLE one — Nordic UART Service, the same
service/characteristics web/app.js drives from Chrome or Bluefy.

Why it exists: the checks that MATTER are the ones you can only do with
the cable OUT (SENSE_FIRST_BOOT items 24 and 25b) — resting battery
voltage against a meter, and the beach off-ritual. USB serial is
unavailable by definition in those, and Bluefy needs a phone in your
hand. bleak talks BLE straight from the laptop, so the whole read is
scriptable and repeatable while your hands stay on the meter probes.

Usage:
    python3 tools/blecmd.py stats            # one command, print the reply
    python3 tools/blecmd.py info
    python3 tools/blecmd.py --watch stats    # re-issue every 3 s until ^C
    python3 tools/blecmd.py --watch --every 10 stats
    python3 tools/blecmd.py --scan           # list advertisers, don't connect

Options:
    --name NAME       advertised name to match (default: JumpHeight)
    --every SECONDS   --watch period (default: 3)
    --timeout SECONDS reply-collection window per command (default: 4)
    --scan            scan and list what's advertising, then exit

Notes:
  - The puck keeps advertising while connected (jh_link serves more than
    one central), so this can share the device with a watch or a phone —
    that is exactly SENSE_FIRST_BOOT item 14's two-central test.
  - Replies are collected for --timeout seconds rather than parsed for a
    terminator: the protocol has no universal end-of-reply marker, and a
    bench tool should show you everything the device said, including
    lines it volunteered.

SPDX-License-Identifier: MIT
"""

import argparse
import asyncio
import sys
import time

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("bleak not installed — pip3 install bleak")

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # client -> device (writes)
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # device -> client (notifies)


async def scan(seconds=6.0):
    """List everything advertising, flagging anything carrying the NUS."""
    print(f"scanning {seconds:.0f}s ...")
    found = await BleakScanner.discover(timeout=seconds, return_adv=True)
    if not found:
        print("nothing found")
        return
    for dev, adv in sorted(found.values(), key=lambda p: -(p[1].rssi or -999)):
        uuids = [u.lower() for u in (adv.service_uuids or [])]
        tag = "  <-- Nordic UART" if NUS_SERVICE in uuids else ""
        print(f"  {adv.rssi:>4} dBm  {dev.address}  {adv.local_name or '(no name)'}{tag}")


async def find(name, seconds=10.0):
    """Return the first advertiser matching `name` (case-insensitive)."""
    dev = await BleakScanner.find_device_by_filter(
        lambda d, adv: (adv.local_name or d.name or "").lower() == name.lower(),
        timeout=seconds,
    )
    if dev is None:
        sys.exit(f"no '{name}' found — is it awake and in range? "
                 f"(try --scan; a puck in System OFF does not advertise)")
    return dev


class Link:
    """A connected NUS session: send a line, collect what comes back."""

    def __init__(self, client):
        self.client = client
        self._lines = []
        self._buf = ""

    def _on_notify(self, _handle, data: bytearray):
        self._buf += data.decode("utf-8", errors="replace")
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._lines.append(line.rstrip("\r"))

    async def start(self):
        await self.client.start_notify(NUS_TX, self._on_notify)

    async def command(self, text, timeout):
        """Send one line, then collect replies for `timeout` seconds."""
        self._lines.clear()
        await self.client.write_gatt_char(NUS_RX, (text + "\n").encode(), response=False)
        await asyncio.sleep(timeout)
        return list(self._lines)


async def run(args):
    dev = await find(args.name)
    print(f"connecting to {dev.address} ({dev.name or args.name}) ...")
    async with BleakClient(dev) as client:
        link = Link(client)
        await link.start()
        print("connected\n")
        while True:
            stamp = time.strftime("%H:%M:%S")
            lines = await link.command(args.command, args.timeout)
            if not lines:
                print(f"[{stamp}] (no reply in {args.timeout}s)")
            for ln in lines:
                print(f"[{stamp}] {ln}")
            if not args.watch:
                return
            print()
            await asyncio.sleep(max(0.0, args.every - args.timeout))


def main():
    p = argparse.ArgumentParser(description="Send a command to the puck over BLE.")
    p.add_argument("command", nargs="?", default="stats",
                   help="command to send (default: stats)")
    p.add_argument("--name", default="JumpHeight", help="advertised name to match")
    p.add_argument("--watch", action="store_true", help="repeat until ^C")
    p.add_argument("--every", type=float, default=3.0, help="--watch period, seconds")
    p.add_argument("--timeout", type=float, default=4.0, help="reply window, seconds")
    p.add_argument("--scan", action="store_true", help="list advertisers and exit")
    args = p.parse_args()

    try:
        asyncio.run(scan() if args.scan else run(args))
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
