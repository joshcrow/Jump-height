#!/usr/bin/env python3
"""dualcentral — the TWO-CENTRAL concurrent BLE test, which has never been run.

WHY THIS EXISTS
---------------
The original watch-corruption bug only ever showed up with a Mac AND a watch
connected at the same time. Lines arrived at the watch with a hole in the
middle — the diagnostic caught

    height_m=1.623m=1.658

i.e. exactly the 20 characters " height_ft=5.3 best_" — precisely one MTU-23
payload — missing. `LineReader` glued the fragments back into a `JUMP` that
still parsed and carried wrong numbers.

Root cause (code read, 2026-08-14): `BLEUart::write()` returns 0 when the
SoftDevice has no free TX buffers, and `sendOneChunk()` advanced the queue
tail regardless — a rejected chunk was discarded silently, per-connection.
Two subscribers double the demand on ONE shared TX buffer pool at the same
pacing, which is why two centrals made it visible.

The layer-1 fix (honor `write()`'s return, bounded per-connection retry,
latched chunk length) shipped 2026-08-14 and was verified 2026-08-18: a
240,506-byte `trace` export, 17,031 valid rows, `tx_drops` absent afterwards.
**But that was ONE central.** `docs/ble-dependability.md` §5 asks for the
induced-failure soak with a second central subscribed, and `docs/STATUS.md`
says in as many words: "no bulk export has been run over two concurrent
centrals ... until that measurement exists neither entry should be promoted."

This script is that measurement.

WHAT IT ASSERTS, AND WHY THAT IS THE RIGHT ASSERTION
----------------------------------------------------
`jh_link`'s transmit queue is ONE queue broadcast to every subscribed
connection, and the tail only advances when EVERY subscriber accepted the
chunk (jh_link.cpp:340-363). So the integrity question is not "did each
central get plausible data" — it is **"did both centrals receive the SAME
bytes"**. A per-connection silent drop makes exactly one of the two copies
short, which is invisible to any single-central test and invisible to any
test that only checks that rows parse. So we compare the `FILE ... BEGIN` /
`FILE ... END` bodies BYTE FOR BYTE and report the first differing offset.

`tx_drops` is an ADDER KEY (main.cpp:609-613 — emitted only when non-zero, so
that a healthy STATS line stays byte-identical for every existing client).
**Absent therefore means ZERO, not unknown**, and this script says so in those
words rather than printing "None" and leaving the reader to guess. It is
sampled before AND after the export so the reported number is a delta.

THE TRAP THIS SCRIPT HAS TO AVOID: A FALSE PASS
------------------------------------------------
On macOS, CoreBluetooth shares ONE physical link to a peripheral across every
CBCentralManager in the system. Two `BleakClient`s on one Mac can therefore
look like two centrals to Python while the firmware sees ONE connection — and
both client objects would still receive every notification, so the byte
comparison would PASS having tested nothing at all. That is precisely the kind
of undetectable-but-rare pass this project keeps getting burned by.

So we take an in-band reading instead of assuming. `jh_link::onNotify()` sets
the greet flag on every CCCD enable, and `bleGreet()` broadcasts
"# JumpHeight ..." + "READY" to EVERY currently-subscribed connection
(main.cpp:226-240 documents this deliberately). Subscribe A, then B:

    two real links  ->  A sees 2 READY (its own greet + B's), B sees 1
    one shared link ->  A sees 1 READY,                       B sees 0
                        (B's start_notify never reached the firmware —
                         the OS had notifications on already)

That is evidence, not inference, and it costs no firmware change. If it says
one link the run exits INCONCLUSIVE (2), not PASS. To get two genuinely
separate controllers out of one host, use BlueZ with two adapters
(`--adapter-a hci0 --adapter-b hci1`, refused on non-Linux because the
CoreBluetooth backend silently ignores the flag). Otherwise run the two halves
on two different machines with `--save-dir` on each, then compare the two
`capture_A.bin` files offline with `--compare`.

Usage:
    python3 tools/dualcentral.py                        # find by name prefix
    python3 tools/dualcentral.py --addr 45ED            # pin the puck
    python3 tools/dualcentral.py --command dump --timeout 300
    python3 tools/dualcentral.py --save-dir /tmp/dc     # keep the raw captures
    python3 tools/dualcentral.py --adapter-a hci0 --adapter-b hci1   # Linux
    python3 tools/dualcentral.py --compare host1/capture_A.bin host2/capture_A.bin

Exit codes (a bench script has to be able to tell these apart):
    0  PASS          — two links evidenced, bodies identical, tx_drops zero
    1  FAIL          — body mismatch, a short file, or tx_drops non-zero
    2  INCONCLUSIVE  — only one link evidenced, no FILE frame captured, the
                       export timed out, a link dropped, or tx_drops unread
    3  SETUP         — puck not found, connect failed, bad arguments

STATUS: WRITTEN AND SELF-TESTED, NEVER RUN AGAINST A PUCK (2026-08-18). The
board was busy, so the radio path below is unexercised: the analysis half was
verified against synthetic streams built to main.cpp's framing (including an
injected 20-byte hole, which it catches at the right offset), and the
discovery half was verified only as far as "scan for a name that cannot
exist". Nothing here has yet seen a real second connection, so this file is a
test that is READY to run, not a result. Do not cite it as evidence until a
run exists.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
import sys
import time
from pathlib import Path

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("bleak not installed — pip3 install bleak")

NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e"
NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"  # client -> device (writes)
NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"  # device -> client (notifies)

# main.cpp:345/373 — printFileFramed() brackets every bulk export.
FRAME_BEGIN = re.compile(rb"FILE (\S+) BEGIN\n")
# main.cpp:368-370 — the firmware's own "I shipped you a short file" confession.
INCOMPLETE = re.compile(rb"# WARNING \S+ INCOMPLETE[^\n]*")
# trace.csv is "t,mag" (jh_store kTraceHeader); this is the row shape.
ROW_RE = re.compile(r"^[\d.]+,[\d.]+$")
DROPS_RE = re.compile(r"\btx_drops=(\d+)\b")

EXIT_PASS, EXIT_FAIL, EXIT_INCONCLUSIVE, EXIT_SETUP = 0, 1, 2, 3


# --------------------------------------------------------------- one central
class Central:
    """One connection's worth of state: every byte it received, in order.

    Deliberately captures RAW BYTES, not decoded lines. The failure being
    hunted is bytes vanishing mid-line; decoding first would let a lost chunk
    re-glue into a line that still looks fine — which is the entire reason
    the original bug survived three days without a root cause.
    """

    def __init__(self, tag: str, device, adapter: str | None = None):
        self.tag = tag
        self.device = device
        self.raw = bytearray()
        self.notifs = 0
        self.first_t: float | None = None
        self.last_t: float | None = None
        self.dropped_link = False
        kw = {"adapter": adapter} if adapter else {}  # BlueZ only; ignored elsewhere
        self.client = BleakClient(device, disconnected_callback=self._on_disconnect, **kw)

    def _on_disconnect(self, _client) -> None:
        self.dropped_link = True

    def _on_notify(self, _char, data: bytearray) -> None:
        now = time.monotonic()
        if self.first_t is None:
            self.first_t = now
        self.last_t = now
        self.notifs += 1
        self.raw += data

    async def connect(self) -> None:
        await self.client.connect()
        if self.client.services.get_characteristic(NUS_TX) is None:
            raise RuntimeError(f"{self.tag}: no NUS TX characteristic — wrong device?")

    async def subscribe(self) -> None:
        await self.client.start_notify(NUS_TX, self._on_notify)

    async def send(self, text: str) -> None:
        await self.client.write_gatt_char(NUS_RX, (text + "\n").encode(), response=False)

    @property
    def mtu(self) -> int:
        try:
            return int(self.client.mtu_size)
        except Exception:
            return 0


# ------------------------------------------------------------- frame parsing
def extract_frames(raw: bytes) -> list[tuple[str, bytes]]:
    """Every complete `FILE <name> BEGIN ... FILE <name> END` body, in order.

    Generalised over the file name so this works for `trace` (one frame),
    `jumps` (one) and `dump` (two) without a flag telling it which.
    """
    out: list[tuple[str, bytes]] = []
    pos = 0
    while True:
        m = FRAME_BEGIN.search(raw, pos)
        if not m:
            return out
        name = m.group(1).decode("ascii", "replace")
        end_marker = b"FILE " + m.group(1) + b" END"
        end = raw.find(end_marker, m.end())
        if end < 0:
            return out  # truncated frame: caller sees a missing frame, loudly
        out.append((name, bytes(raw[m.end():end])))
        pos = end + len(end_marker)


def body_stats(body: bytes) -> dict:
    """Bytes / lines / well-formed data rows for one file body."""
    text = body.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # trailing newline before FILE ... END, not a line
    rows = [ln for ln in lines if ROW_RE.match(ln.rstrip("\r"))]
    odd = [ln for ln in lines if ln and not ROW_RE.match(ln.rstrip("\r"))]
    return {
        "bytes": len(body),
        "lines": len(lines),
        "rows": len(rows),
        "odd": odd,
        "sha256": hashlib.sha256(body).hexdigest(),
    }


def first_diff(a: bytes, b: bytes) -> int:
    """Offset of the first differing byte, or -1 when identical.

    Reported as an OFFSET rather than a bool because the offset is the
    diagnosis: a difference at a multiple of the negotiated chunk size is a
    dropped chunk, and where it lands says which connection lost it.
    """
    if a == b:
        return -1                      # fast path: 240 KB compared in C, not Python
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return -1 if len(a) == len(b) else n


def last_stats_line(raw: bytes, since: int = 0) -> str | None:
    text = raw[since:].decode("utf-8", errors="replace")
    hits = [ln for ln in text.split("\n") if ln.startswith("STATS ")]
    return hits[-1] if hits else None


def drops_from(stats_line: str | None) -> int | None:
    """None means the key was ABSENT — which the firmware defines as zero.

    Kept distinct from 0 on purpose so the caller can print WHY it is zero
    (adder-key rule, main.cpp:609) instead of printing "None".
    """
    if stats_line is None:
        return None
    m = DROPS_RE.search(stats_line)
    return int(m.group(1)) if m else None


# ------------------------------------------------------------------ discovery
async def find(name: str, addr: str | None, timeout: float, adapter: str | None):
    """One INDEPENDENT scan. Each call builds its own backend manager, which
    is what gives central B a notification path of its own rather than
    stomping central A's (on CoreBluetooth two clients sharing one discovered
    device share one delegate, and the second overwrites the first)."""
    kw = {"adapter": adapter} if adapter else {}

    def match(d, adv):
        if addr:
            return d.address.lower().startswith(addr.lower())
        return (adv.local_name or d.name or "").lower().startswith(name.lower())

    dev = await BleakScanner.find_device_by_filter(match, timeout=timeout, **kw)
    if dev is None:
        raise RuntimeError(f"no '{addr or name}' found — awake and in range? "
                           f"(a puck in System OFF does not advertise)")
    return dev


async def find_exact(address: str, timeout: float, adapter: str | None):
    """Second scan, pinned to the FIRST scan's exact address.

    Not a nicety: on 2026-08-18 two boards both advertising bare "JumpHeight"
    impersonated each other on the bench and the logger wrote the wrong
    puck's battery into the record. A two-central test that accidentally
    opened one connection to each of two pucks would "fail" for a reason that
    has nothing to do with the transmit queue.
    """
    kw = {"adapter": adapter} if adapter else {}
    dev = await BleakScanner.find_device_by_filter(
        lambda d, _adv: d.address.lower() == address.lower(), timeout=timeout, **kw)
    if dev is None:
        raise RuntimeError(f"second scan could not re-find {address}")
    return dev


# ----------------------------------------------------------------- the poller
async def poll_stats(central: Central, period: float, stop: asyncio.Event) -> int:
    """Central B's job: keep asking for `stats` while A drags a file across.

    NOTE ON THE RX FIFO (BLEUart's is 256 bytes,
    BLEUart.h:45 BLE_UART_DEFAULT_FIFO_DEPTH). `trace` runs to completion
    inside one loop() pass, so every "stats\\n" B sends during the export
    QUEUES rather than interleaving — 6 bytes each, so a 61 s export at the
    default 2 s period parks ~180 bytes in a 256-byte fifo. Comfortable, but
    not by much: at --poll 1 a long export will overrun it and the firmware
    will simply see fewer commands. That costs the test nothing (the replies
    are load, not the measurement) but it must not be mistaken for a defect,
    hence this note and the sent/answered counts in the report.
    """
    sent = 0
    while not stop.is_set():
        try:
            await central.send("stats")
            sent += 1
        except Exception as e:
            print(f"  [B] stats write failed: {type(e).__name__}: {e}")
        try:
            await asyncio.wait_for(stop.wait(), timeout=period)
        except asyncio.TimeoutError:
            pass
    return sent


async def wait_for_marker(central: Central, marker: bytes, deadline: float,
                          label: str) -> bool:
    last_len, last_change = 0, time.monotonic()
    while time.monotonic() < deadline:
        if marker in central.raw:
            return True
        if len(central.raw) != last_len:
            now = time.monotonic()
            if now - last_change > 5.0:
                print(f"  ... {label}: {len(central.raw)} bytes")
                last_change = now
            last_len = len(central.raw)
        await asyncio.sleep(0.2)
    return False


# --------------------------------------------------------------------- report
def report(a: Central, b: Central, args, base_stats: str | None,
           final_stats: str | None, polls_sent: int, save_dir: Path | None) -> int:
    # Two independent flags rather than one running exit code. An earlier
    # version did `status = max(status, EXIT_INCONCLUSIVE)` and, because
    # EXIT_INCONCLUSIVE (2) sorts above EXIT_FAIL (1), a run with mismatched
    # bodies AND one link would have been reported INCONCLUSIVE — downgrading
    # a real, reproduced corruption to "we learned nothing". Severity is not
    # the numeric order of the exit codes: FAIL always outranks INCONCLUSIVE.
    failed = False
    inconclusive = False

    print("\n" + "=" * 72)
    print("PER-CENTRAL CAPTURE")
    print("=" * 72)
    for c in (a, b):
        span = (c.last_t - c.first_t) if (c.first_t and c.last_t) else 0.0
        rate = (len(c.raw) / span) if span > 0 else 0.0
        print(f"  central {c.tag}: {len(c.raw)} bytes in {c.notifs} notifications, "
              f"{span:.1f}s, {rate/1024:.2f} KB/s, MTU {c.mtu}, "
              f"link {'DROPPED' if c.dropped_link else 'held'}")

    fa, fb = extract_frames(bytes(a.raw)), extract_frames(bytes(b.raw))
    print(f"\n  FILE frames: A={[n for n, _ in fa]}  B={[n for n, _ in fb]}")

    if save_dir:
        save_dir.mkdir(parents=True, exist_ok=True)
        for c in (a, b):
            p = save_dir / f"capture_{c.tag}.bin"
            p.write_bytes(bytes(c.raw))
            print(f"  saved {p}")

    # ---- the actual integrity question: same bytes to both subscribers ----
    print("\n" + "=" * 72)
    print("BODY INTEGRITY  (one shared TX queue -> both copies must be identical)")
    print("=" * 72)
    if not fa or not fb:
        print(f"  INCONCLUSIVE: A captured {len(fa)} frame(s), B captured {len(fb)}. "
              f"Nothing to compare — no verdict is available from this run.")
        inconclusive = True
    elif len(fa) != len(fb):
        print(f"  FAIL: A got {len(fa)} frame(s), B got {len(fb)}. "
              f"A subscriber missed a whole file.")
        failed = True

    for i in range(min(len(fa), len(fb))):
        na, ba = fa[i]
        nb, bb = fb[i]
        sa, sb = body_stats(ba), body_stats(bb)
        print(f"\n  frame {i}: A={na!r} B={nb!r}")
        for tag, s in (("A", sa), ("B", sb)):
            print(f"    {tag}: {s['bytes']} bytes, {s['lines']} lines, "
                  f"{s['rows']} well-formed data rows, sha256 {s['sha256'][:16]}")
            if s["odd"]:
                sample = ", ".join(repr(x[:40]) for x in s["odd"][:3])
                print(f"       {len(s['odd'])} non-row line(s), e.g. {sample}")
        if na != nb:
            print(f"    FAIL: frame names differ ({na} vs {nb})")
            failed = True
        d = first_diff(ba, bb)
        if d < 0:
            print(f"    IDENTICAL — both centrals received the same {len(ba)} bytes")
        else:
            failed = True
            print(f"    *** MISMATCH at byte offset {d} "
                  f"(A len {len(ba)}, B len {len(bb)}) ***")
            print(f"        A: {ba[max(0, d-24):d+24]!r}")
            print(f"        B: {bb[max(0, d-24):d+24]!r}")
        for tag, raw in (("A", ba), ("B", bb)):
            for w in INCOMPLETE.findall(raw):
                failed = True
                print(f"    *** {tag}: firmware reported a SHORT FILE: "
                      f"{w.decode('utf-8', 'replace')} ***")

    # ---- tx_drops: absent means zero, and we say that out loud ----
    print("\n" + "=" * 72)
    print("tx_drops  (adder key: main.cpp emits it ONLY when non-zero)")
    print("=" * 72)
    print(f"  final STATS: {final_stats or '(none captured)'}")

    def say(line: str | None) -> str:
        """Three states, never collapsed into one.

        "no reading" and "the key was absent" are NOT the same answer, and the
        firmware's own STATS comment (main.cpp:564-576) makes exactly this
        point about stored_jumps: a reading that could not be taken must never
        be dressed up as a reading of zero. Same rule applies here.
        """
        if line is None:
            return "NOT READ — no STATS line came back (this is not a zero)"
        v = drops_from(line)
        if v is None:
            return ("ABSENT — the firmware emits tx_drops only when non-zero, "
                    "so this is ZERO")
        return str(v)

    print(f"  before export: {say(base_stats)}")
    print(f"  after  export: {say(final_stats)}")
    base_drops, fin = drops_from(base_stats), drops_from(final_stats)
    if final_stats is None:
        print("  INCONCLUSIVE: tx_drops was never read, so this run cannot "
              "clear the counter.")
        inconclusive = True
    else:
        if base_stats is None:
            print("  (baseline was never read — the delta below assumes it was zero)")
            inconclusive = True
        delta = (fin or 0) - (base_drops or 0)
        print(f"  delta over this run: {delta}")
        if (fin or 0) > 0:
            failed = True
            print("  *** NON-ZERO: the firmware gave up on a chunk after "
                  "TX_RETRY_MAX passes and counted the bytes it discarded. ***")

    # ---- did we actually have two links? ----
    n_ready_a = a.raw.count(b"READY")
    n_ready_b = b.raw.count(b"READY")
    print("\n" + "=" * 72)
    print("LINK-COUNT EVIDENCE  (greet broadcast on every CCCD enable)")
    print("=" * 72)
    print(f"  READY lines seen: A={n_ready_a}  B={n_ready_b}   "
          f"(two real links => A>=2, B>=1)")
    print(f"  central B sent {polls_sent} `stats` command(s) during the export")
    two_links = n_ready_a >= 2 and n_ready_b >= 1
    if two_links:
        print("  -> consistent with TWO separate peripheral connections.")
    else:
        print("  -> ONE shared link is the likely reading: B's subscribe never")
        print("     reached the firmware, so no second greet was broadcast.")
        print("     On macOS CoreBluetooth multiplexes one physical link across")
        print("     every central manager, so this run did NOT exercise the")
        print("     two-connection path — the byte comparison above passed")
        print("     without testing anything. Use two BlueZ adapters")
        print("     (--adapter-a hci0 --adapter-b hci1) or two hosts.")
        if args.allow_one_link:
            print("     (--allow-one-link given: not gating the exit code on this)")
        else:
            inconclusive = True

    if a.dropped_link or b.dropped_link:
        print("\n  NOTE: a link dropped during the run — the capture is a partial "
              "record and any PASS above is not trustworthy.")
        inconclusive = True

    # FAIL outranks INCONCLUSIVE: a reproduced corruption is a result even on a
    # run whose setup we could not fully vouch for.
    status = EXIT_FAIL if failed else (EXIT_INCONCLUSIVE if inconclusive else EXIT_PASS)
    print("\n" + "=" * 72)
    verdict = {EXIT_PASS: "PASS", EXIT_FAIL: "FAIL",
               EXIT_INCONCLUSIVE: "INCONCLUSIVE"}[status]
    print(f"VERDICT: {verdict}  (exit {status})")
    print("=" * 72)
    return status


# ------------------------------------------------------------------ main flow
async def run(args) -> int:
    print(f"scanning for '{args.addr or args.name}' ...")
    dev_a = await find(args.name, args.addr, args.scan_timeout, args.adapter_a)
    print(f"  A: {dev_a.address}  {dev_a.name or '(no name)'}")
    dev_b = await find_exact(dev_a.address, args.scan_timeout, args.adapter_b)
    print(f"  B: {dev_b.address}  {dev_b.name or '(no name)'}  (same puck, pinned by address)")

    a = Central("A", dev_a, args.adapter_a)
    b = Central("B", dev_b, args.adapter_b)
    try:
        return await _run_locked(args, a, b)
    finally:
        # ALWAYS hand the slots back. kMaxPrphConnections is 2
        # (jh_link.cpp:170) — this script uses BOTH — so a run that dies
        # holding a connection leaves no slot for the next attempt, and the
        # operator debugs a phantom "puck refuses to connect" instead of the
        # crash that actually happened.
        for c in (a, b):
            try:
                await c.client.disconnect()
            except Exception:
                pass


async def _run_locked(args, a: Central, b: Central) -> int:
    await a.connect()
    print("connected A")
    await a.subscribe()
    # Settle between the two subscribes: the firmware's greet flag is a single
    # bool taken once per loop() pass (jh_link.cpp:541), so two subscribes in
    # the same millisecond could coalesce into ONE greet and make two real
    # links look like one. Serialising them keeps the evidence readable.
    await asyncio.sleep(args.settle)

    await b.connect()
    print("connected B")
    await b.subscribe()
    await asyncio.sleep(args.settle)

    print(f"both subscribed  (MTU A={a.mtu} B={b.mtu})")

    # Baseline tx_drops BEFORE any load, so the reported number is a delta and
    # not a lifetime total inherited from whatever happened earlier today.
    mark = len(a.raw)
    await a.send("stats")
    await asyncio.sleep(2.0)
    base_stats = last_stats_line(bytes(a.raw), mark)

    stop = asyncio.Event()
    poller = asyncio.create_task(poll_stats(b, args.poll, stop))

    print(f"\ncentral A: `{args.command}`   central B: `stats` every {args.poll}s")
    t0 = time.monotonic()
    await a.send(args.command)
    deadline = t0 + args.timeout
    ok = await wait_for_marker(a, b"OK " + args.command.encode(), deadline,
                               "central A")
    elapsed = time.monotonic() - t0
    print(f"  A finished after {elapsed:.1f}s" if ok else
          f"  TIMEOUT after {elapsed:.1f}s — no `OK {args.command}` from A")

    # Let B drain. The queue tail only advances once EVERY subscriber accepted
    # a chunk, so B cannot lag A by much — but "cannot lag much" is a claim
    # about the code, and the point of this script is not to trust claims.
    drain_end = time.monotonic() + args.drain
    while time.monotonic() < drain_end:
        if b"OK " + args.command.encode() in b.raw:
            break
        await asyncio.sleep(0.2)

    stop.set()
    polls_sent = await poller

    # Quiesce, then one clean `stats` for the after-reading. Sent on A because
    # A is the connection that carried the bulk transfer.
    await asyncio.sleep(1.0)
    mark = len(a.raw)
    await a.send("stats")
    await asyncio.sleep(3.0)
    final_stats = last_stats_line(bytes(a.raw), mark)

    save_dir = Path(args.save_dir) if args.save_dir else None
    status = report(a, b, args, base_stats, final_stats, polls_sent, save_dir)

    if not ok and status != EXIT_FAIL:
        # Same severity rule as report(): a timed-out export is INCONCLUSIVE,
        # but it must not mask a mismatch the comparison already proved.
        print("  (export never completed — downgrading to INCONCLUSIVE)")
        status = EXIT_INCONCLUSIVE
    return status


def compare_mode(paths: list[str]) -> int:
    """Offline: compare two captures taken on two DIFFERENT hosts.

    The only way to get two unambiguously separate centrals when the host's
    Bluetooth stack multiplexes (macOS always; one-adapter Linux). Each host
    runs its own half and saves its capture; this compares them with no radio
    involved.
    """
    if len(paths) != 2:
        print("--compare takes exactly two capture files")
        return EXIT_SETUP
    try:
        raws = [Path(p).read_bytes() for p in paths]
    except OSError as e:
        # SETUP, not FAIL. A missing file is the operator mistyping a path;
        # returning FAIL there would put a "the pucks disagreed" verdict in
        # the bench log for a run that never happened.
        print(f"setup failed: cannot read capture: {e}")
        return EXIT_SETUP
    frames = [extract_frames(r) for r in raws]
    print(f"  {paths[0]}: {len(raws[0])} bytes, frames {[n for n, _ in frames[0]]}")
    print(f"  {paths[1]}: {len(raws[1])} bytes, frames {[n for n, _ in frames[1]]}")
    if not frames[0] or not frames[1]:
        print("INCONCLUSIVE: at least one capture has no complete FILE frame")
        return EXIT_INCONCLUSIVE
    status = EXIT_PASS
    if len(frames[0]) != len(frames[1]):
        print(f"FAIL: frame counts differ ({len(frames[0])} vs {len(frames[1])})")
        status = EXIT_FAIL
    for i in range(min(len(frames[0]), len(frames[1]))):
        (na, ba), (nb, bb) = frames[0][i], frames[1][i]
        sa, sb = body_stats(ba), body_stats(bb)
        print(f"\n  frame {i}: {na} / {nb}")
        print(f"    1: {sa['bytes']} bytes, {sa['lines']} lines, {sa['rows']} rows")
        print(f"    2: {sb['bytes']} bytes, {sb['lines']} lines, {sb['rows']} rows")
        d = first_diff(ba, bb)
        if d < 0:
            print(f"    IDENTICAL ({len(ba)} bytes)")
        else:
            status = EXIT_FAIL
            print(f"    *** MISMATCH at offset {d} ***")
            print(f"        1: {ba[max(0, d-24):d+24]!r}")
            print(f"        2: {bb[max(0, d-24):d+24]!r}")
    print(f"\nVERDICT: {'PASS' if status == EXIT_PASS else 'FAIL'} (exit {status})")
    return status


def main() -> int:
    p = argparse.ArgumentParser(
        description="Two-central concurrent BLE integrity test "
                    "(ble-dependability.md §5).")
    p.add_argument("--name", default="JumpHeight",
                   help="advertised name PREFIX to match (default: JumpHeight)")
    p.add_argument("--addr", default=None,
                   help="pin to an address prefix — the unambiguous choice on a "
                        "multi-puck bench")
    p.add_argument("--command", default="trace",
                   help="bulk export central A requests (trace | jumps | dump)")
    p.add_argument("--timeout", type=float, default=180.0,
                   help="seconds to wait for A's export to finish (default: 180; "
                        "240 KB took 61 s on 2026-08-18)")
    p.add_argument("--poll", type=float, default=2.0,
                   help="central B's `stats` period, seconds (default: 2)")
    p.add_argument("--scan-timeout", type=float, default=10.0,
                   help="per-scan discovery timeout, seconds")
    p.add_argument("--settle", type=float, default=2.0,
                   help="pause between the two subscribes, seconds")
    p.add_argument("--drain", type=float, default=15.0,
                   help="extra seconds to let central B catch up after A finishes")
    p.add_argument("--adapter-a", default=None,
                   help="BlueZ adapter for central A (e.g. hci0) — two adapters "
                        "is how one Linux host gets two REAL links")
    p.add_argument("--adapter-b", default=None,
                   help="BlueZ adapter for central B (e.g. hci1)")
    p.add_argument("--save-dir", default=None,
                   help="write each central's raw capture here for forensics")
    p.add_argument("--allow-one-link", action="store_true",
                   help="do not gate the exit code on the two-link evidence "
                        "(only when you have established two links some other way)")
    p.add_argument("--compare", nargs=2, metavar=("CAP1", "CAP2"), default=None,
                   help="offline: compare two saved captures, no radio")
    args = p.parse_args()

    if args.compare:
        return compare_mode(args.compare)

    # bleak forwards unknown kwargs to the backend, and the CoreBluetooth
    # backend simply IGNORES `adapter=`. So on a Mac `--adapter-a hci0
    # --adapter-b hci1` runs happily and changes nothing — the operator would
    # believe they had bought two real controllers and read the resulting PASS
    # as the two-central proof. Refuse instead of silently obliging.
    if (args.adapter_a or args.adapter_b) and not sys.platform.startswith("linux"):
        print(f"--adapter-a/--adapter-b are BlueZ-only and are ignored on "
              f"{sys.platform}. Refusing to run rather than pretend you have "
              f"two controllers. Drop the flags (and read the one-link warning), "
              f"or run this on Linux with two adapters.")
        return EXIT_SETUP
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nstopped — partial run, no verdict")
        return EXIT_INCONCLUSIVE
    except RuntimeError as e:
        print(f"setup failed: {e}")
        return EXIT_SETUP
    except Exception as e:                       # a connect failure is SETUP,
        print(f"setup failed: {type(e).__name__}: {e}")   # not a data verdict
        return EXIT_SETUP


if __name__ == "__main__":
    sys.exit(main())
