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


async def find(name, seconds=10.0, addr=None):
    """The advertiser matching `name` (prefix) or `addr` (prefix).

    Prefix, not equality: pucks advertise "JumpHeight-XXXX" (unique per board)
    since 2026-08-18, after two same-named boards impersonated each other on
    the bench.

    AMBIGUITY IS ANNOUNCED, NOT SILENTLY RESOLVED (2026-08-20). This used to be
    a `find_device_by_filter`, which returns whichever board answers FIRST. The
    default name is the bare prefix "JumpHeight", so on a two-puck bench every
    unpinned call was a coin flip — and it landed differently on consecutive
    calls, which is how a floating 97 % got written into a death-run log and
    how a whole DC/DC result was attributed to the wrong board. The tool knew
    both boards were there and said nothing.

    So: collect every match. One is unambiguous. Several means the caller did
    not say which board they meant, and the fix is to TELL THEM, then choose
    deterministically (lowest name) so a script at least behaves the same way
    twice. Racy-and-silent is the one behaviour that must not survive.
    """
    matches = {}
    def _seen(d, adv):
        nm = (adv.local_name or d.name or "")
        ok = (d.address.lower().startswith(addr.lower()) if addr
              else nm.lower().startswith(name.lower()))
        if ok:
            matches[d.address] = (d, nm, adv.rssi)
        return False        # never short-circuit: we want the full census

    await BleakScanner.find_device_by_filter(_seen, timeout=seconds)

    if not matches:
        # Raised, not sys.exit()'d: under --watch this is a retryable gap
        # (out of range, momentarily not advertising), not the end of the run.
        raise RuntimeError(f"no '{name}' found — is it awake and in range? "
                           f"(try --scan; a puck in System OFF does not advertise)")

    if len(matches) > 1:
        listing = sorted((nm or "(unnamed)", d.address, rssi)
                         for d, nm, rssi in matches.values())
        print(f"\n⚠️  {len(matches)} boards match '{name}' — this call is AMBIGUOUS:",
              file=sys.stderr)
        for nm, adr, rssi in listing:
            print(f"      {nm:22} {adr}  rssi={rssi}", file=sys.stderr)
        print(f"    Pin the one you mean:  --name {listing[0][0]}   (or --addr <prefix>)",
              file=sys.stderr)
        print(f"    Unpinned reads have corrupted two analyses; power figures "
              f"from the wrong board are worse than no reading.\n", file=sys.stderr)
        chosen = min(matches.values(), key=lambda t: (t[1] or "", t[0].address))
        print(f"    proceeding with {chosen[1]} (lowest name, chosen deterministically)\n",
              file=sys.stderr)
        return chosen[0]

    return next(iter(matches.values()))[0]


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


async def session(args):
    """One connect-and-poll session. Returns when the link drops."""
    dev = await find(args.name, addr=getattr(args, "addr", None))
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


async def run(args):
    """--watch is meant to outlive the link: a multi-hour charge log rides
    through the disconnects a BLE link inevitably has, and a puck that goes
    to sleep and comes back is a gap in the log, not the end of it. A
    one-shot command still fails loudly."""
    while True:
        try:
            await session(args)
            return
        except Exception as e:
            if not args.watch:
                raise
            print(f"[{time.strftime('%H:%M:%S')}] link lost ({type(e).__name__}: {e})"
                  f" — retrying in {args.every:.0f}s\n")
            await asyncio.sleep(args.every)


def main():
    p = argparse.ArgumentParser(description="Send a command to the puck over BLE.")
    p.add_argument("command", nargs="?", default="stats",
                   help="command to send (default: stats)")
    p.add_argument("--name", default="JumpHeight", help="advertised name PREFIX to match")
    p.add_argument("--addr", default=None, help="pin to an address prefix (multi-puck bench)")
    p.add_argument("--watch", action="store_true", help="repeat until ^C")
    p.add_argument("--every", type=float, default=3.0, help="--watch period, seconds")
    p.add_argument("--timeout", type=float, default=4.0, help="reply window, seconds")
    p.add_argument("--scan", action="store_true", help="list advertisers and exit")
    args = p.parse_args()

    try:
        asyncio.run(scan() if args.scan else run(args))
    except KeyboardInterrupt:
        print("\nstopped")
    except RuntimeError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
