"""Integration test: the rider sync page (web/sync/) driven end-to-end.

The web equivalent of test_cli.py for the phone path. It loads the real
web/sync/index.html + sync.js in a real (headless) Chromium and plays the puck
from Python over the page's own test seam (web/sync/CONTRACT.md §3):

    window.__mock  = { feed(line), sent: [] }
    window.__sync  = { state(), lastBundle() }

feed(line) injects a line as if the puck had sent it; sent[] is the exact list
of command strings the page wrote. So the loop below IS the device: it polls
sent[], answers each command with canned protocol lines, and asserts on what
the page does with them. `traceraw`'s reply is built here in Python from
sim/trace_codec.encode_region + zlib.crc32, to exactly the wire framing in
web/sync/CONTRACT.md §1 — so if the page and the contract ever disagree about 76-column
base64, the two chatter lines or the crc, this fails rather than the rider.

WHY the skips are worded, never silent: a reading that did not happen is a
finding (CLAUDE.md rule 3). A missing playwright or an unlaunchable browser
raises SkipTest WITH the reason attached. That alone would still leave a green
suite if the browser vanished, so the skip is ENFORCED elsewhere:
.github/workflows/build.yml installs Chromium and then launches one in its
"Verify Chromium actually launches (web-sync acceptance)" step, which fails the
job if it cannot — the same shape as that file's env:host acceptance step for
F-03. In CI this test therefore always really runs.

WHY tearDown asserts on page errors: an uncaught JavaScript exception makes
every assertion below it meaningless, and a printed warning is not a failing
test (CLAUDE.md rule 3 again).

WHY the two-step browser launch: this environment ships a Chromium whose build
number doesn't match the one Playwright's default lookup expects, so a plain
launch() misses it; we retry pinned at /opt/pw-browsers/chromium.

WHY non-local requests are blocked: the page is contracted to make ZERO
external requests (web/sync/CONTRACT.md §3). Aborting anything that isn't localhost turns
a future stray CDN reference into a test failure instead of a slow page.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import threading
import time
import unittest
import zipfile
import zlib
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
WEB_DIR = REPO / "web"

sys.path.insert(0, str(REPO / "sim"))
import trace_codec  # noqa: E402  — the fixture builder; a real import failure
                    # here is a breakage, not a reason to skip.

CHROMIUM_FALLBACK = "/opt/pw-browsers/chromium"

try:
    from playwright.sync_api import sync_playwright

    _PW_IMPORT_ERROR = None
except Exception as _e:  # ImportError, or a half-installed package
    sync_playwright = None
    _PW_IMPORT_ERROR = _e


# --------------------------------------------------------------- the fixture

LOG_HZ = 50
# 1,500 samples at 50 Hz = 30 s of trace: ~30 blocks, ~4.4 KB of base64 over
# ~58 lines. Big enough that the page's incremental decoder has to carry
# leftover characters between lines, small enough to stay a fast test.
TRACE_PAIRS = [(i / LOG_HZ, 1.0 + 0.35 * ((i % 37) / 37.0)) for i in range(1500)]
TRACE_RAW = trace_codec.encode_region(TRACE_PAIRS, LOG_HZ)
TRACE_CRC = zlib.crc32(TRACE_RAW) & 0xFFFFFFFF
REGION_BYTES = 4 * 1024 * 1024

# len(TRACE_RAW) is 3240, which is 0 mod 3 — so its base64 carries NO '='
# padding at all, and the page's padding branch would never run in CI. A region
# is align4, so N mod 3 is 1 or 2 for two of every three possible sizes: these
# two fixtures (208 bytes -> '==', 3248 bytes -> '=') are the ordinary case,
# not the exotic one. web/sync/CONTRACT.md §1 puts padding "only at the very end", which is
# exactly the assumption feedB64 decodes on.
TRACE_RAW_PAD2 = trace_codec.encode_region(
    [(i / LOG_HZ, 1.0 + 0.35 * ((i % 37) / 37.0)) for i in range(97)], LOG_HZ)
TRACE_RAW_PAD1 = trace_codec.encode_region(
    [(i / LOG_HZ, 1.0 + 0.35 * ((i % 37) / 37.0)) for i in range(1501)], LOG_HZ)
assert len(TRACE_RAW_PAD2) % 3 == 1 and len(TRACE_RAW_PAD1) % 3 == 2, (
    "the padding fixtures must NOT be multiples of 3, or they pin nothing: "
    f"{len(TRACE_RAW_PAD2)}, {len(TRACE_RAW_PAD1)}")

# What the firmware prints when a transfer dropped bytes — INSIDE the FILE
# frame, after the body and before the "FILE <name> END" line
# (firmware/src/main.cpp printFileFramed() and printTraceRawFramed()). Placed
# here so the fixtures below can put it exactly where the puck does.
def incomplete_warning(name: str, missing: int = 40) -> str:
    return (f"# WARNING {name} INCOMPLETE — {missing} bytes never reached the "
            f"host; re-run the download")

JUMPS_HEADER = "n,takeoff_s,airtime_raw_s,airtime_s,height_m"
JUMPS_ROWS = [
    "1,10.000,0.615,0.600,0.441",
    "2,20.000,1.010,1.000,1.226",
    "3,30.000,1.100,1.081,1.433",
]

# The old-firmware fallback body. Any plausible CSV works; what matters is that
# trace_bytes below is EXACTLY its byte count, because that is the whole check
# the csv path has (web/sync/CONTRACT.md §2 verified (c)).
CSV_ROWS = [f"{i / LOG_HZ:.2f},{1.0 + 0.01 * (i % 20):.3f}" for i in range(400)]
CSV_BODY = "t,mag\n" + "".join(r + "\n" for r in CSV_ROWS)
CSV_BYTES = len(CSV_BODY.encode())

INFO_LINES = [
    "INFO fw=0.4.3 sample_hz=200 log_hz=50 motion_thresh_g=0.12 idle_timeout_s=20 "
    "ble=1 vbat_mv=4094 batt_pct=93 chg=1 src=5c80a436",
    "# dcdc=1",
    "# name=JumpHeight-E2C4",
    "PARAMS takeoff_g=1.35 land_g=1.60 min_airtime_s=0.25",
    "CAL airtime_offset_s=0.0192 height_scale=1.000 source=device off_src=device "
    "scale_src=defaults vbat_src=defaults",
    "OK info",
]

SELFTEST_LINES = [
    "SELFTEST BEGIN",
    "SELFTEST i2c PASS detail=0x68",
    "SELFTEST ble PASS detail=advertising",
    "SELFTEST flash PASS detail=1441792B_free",
    "SELFTEST END result=PASS",
    "OK selftest",
]

# What today's OG actually answers an unknown command with (main.cpp prints the
# help line first, then the ERR terminator) — the exact shape the page's
# fallback is allowed to trigger on, and nothing else (web/sync/CONTRACT.md §1).
OLD_FW_TRACERAW = [
    "# commands: help info stats jumps trace dump clear selftest set cal off",
    "ERR unknown_command traceraw",
]


def stats_line(stored_jumps: int, trace_bytes: int, uptime_s: float = 12345.678) -> str:
    return (f"STATS session_jumps=0 session_best_m=0.000 session_best_airtime_s=0.000 "
            f"stored_jumps={stored_jumps} stored_best_m=1.433 trace_bytes={trace_bytes} "
            f"vbat_mv=4094 batt_pct=93 chg=1 uptime_s={uptime_s:.3f}")


def traceraw_frame(raw: bytes, *, drop_tail_lines: int = 0, crc: int | None = None,
                   warn: str | None = None) -> list[str]:
    """The `traceraw` reply, byte for byte per web/sync/CONTRACT.md §1.

    bytes= always advertises the FULL length: `drop_tail_lines` simulates a
    link that stopped part-way, which is exactly how the page is supposed to
    notice a short transfer. `warn` inserts the puck's own INCOMPLETE line
    where printTraceRawFramed() puts it — inside the frame, after the body and
    before "FILE trace.bin END".
    """
    b64 = base64.b64encode(raw).decode("ascii")
    body = [b64[i:i + 76] for i in range(0, len(b64), 76)]
    if drop_tail_lines:
        body = body[:-drop_tail_lines]
    if warn:
        body = body + [warn]
    return (
        [f"# traceraw bytes={len(raw)} log_hz={LOG_HZ} region_bytes={REGION_BYTES}",
         "FILE trace.bin BEGIN"]
        + body
        + ["FILE trace.bin END",
           f"# traceraw crc32={(zlib.crc32(raw) & 0xFFFFFFFF) if crc is None else crc:08x}"
           f" bytes={len(raw)}",
           "OK traceraw"]
    )


# ------------------------------------------------- the full-region fixture
#
# 455 KB went through this page over a real cable on 2026-09-09
# (docs/serial-parity-2026-09-09.md). A FULL trace region is ~35x that, and
# nothing had ever put one through the page. 15,917,153 is not a round number
# invented here: it is what the OG's own `tracecheck` answered on 2026-09-07
# for a full region — "fast=15917918 slow=15917153 DISAGREE — the slow number
# is the correct one" (docs/STATUS.md, F-22). `trace_bytes` is a CSV byte
# count (firmware/src/platform/nrf52/jh_store.cpp:472), so that figure IS the
# size of the body the csv fallback carries, and the csv fallback is the only
# path today's OG (src=5c80a436) can take.
FULL_REGION_CSV_BYTES = 15_917_153

# What the DEFAULT run measures. Sized from the full-region run's own measured
# throughput so the 500 ms progress timer fires several times and the
# assertions below are about a bar that really moved — not a pull that
# finished before the first tick.
DEFAULT_LARGE_CSV_BYTES = 3_000_000

# Opt-in, with the reason attached and the command in it: the full-region run
# is minutes of browser work, and a suite nobody runs because it is slow is a
# suite that stops catching things. The DEFAULT run still drives
# DEFAULT_LARGE_CSV_BYTES through the same code, so the page is never
# unmeasured at scale — only less measured.
FULL_REGION_ENV = "JH_WEB_SYNC_FULL_REGION"
RUN_FULL_REGION = os.environ.get(FULL_REGION_ENV) == "1"
FULL_REGION_SKIP = (
    f"the OG's full {FULL_REGION_CSV_BYTES:,}-byte region measured 15.9 s on "
    f"this bench (Apple M3, headless Chromium 151, 2026-09-10) and would more "
    f"than double this file's run; enable it with {FULL_REGION_ENV}=1 "
    f"python3 -m pytest tools/tests/test_web_sync.py -k full_region -s "
    f"(the default run still drives {DEFAULT_LARGE_CSV_BYTES:,} bytes through "
    f"the same path)")


def js_const(name):
    """Read a numeric `const` out of sync.js rather than restating it here.

    F-31 (commit 5941a37) was exactly this: a suite that did not read its own
    constants pins nothing when the source moves.
    """
    src = (WEB_DIR / "sync" / "sync.js").read_text()
    m = re.search(rf"^const {re.escape(name)} = (\d+);", src, re.M)
    assert m, f"{name} is no longer a plain numeric const in web/sync/sync.js"
    return int(m.group(1))


INACTIVITY_MS = js_const("INACTIVITY_MS")


def plan_csv_body(total_bytes, log_hz=LOG_HZ):
    """(rows, wide) for a trace.csv body of EXACTLY total_bytes.

    The puck prints one trace sample as "%.3f,%.3f\\n"
    (firmware/src/platform/nrf52/jh_store.cpp:559) and t = i/log_hz, so a row
    costs digits(int(t)) + 11 bytes and the body length is a function of the
    row count alone — predictable here without formatting a single float.
    The header "t,mag\\n" is 6 more, counted once (jh_store.cpp:1059).

    Whatever is left over (0..15 bytes) is spent on `wide` rows whose mag is
    >= 10 g, one character each. Those are samples, not padding: a hard
    landing reads over 10 g, and it is precisely that mix of field widths that
    stops a real region's byte count from being a multiple of anything.
    """
    remaining = total_bytes - 6
    rows, digits = 0, 1
    while True:
        lo = 0 if digits == 1 else 10 ** (digits - 1)
        count = (10 ** digits - lo) * log_hz     # rows whose t has `digits` digits
        width = digits + 11
        if count * width <= remaining:
            rows += count
            remaining -= count * width
            digits += 1
            continue
        rows += remaining // width
        remaining -= (remaining // width) * width
        break
    assert 0 <= remaining < digits + 11, remaining
    assert remaining <= rows, "no room to spend the remainder on wide-mag rows"
    return rows, remaining


# The in-page bench. It exists because the alternative corrupts the number
# being taken: pushing ~16 MB of device lines across Playwright's evaluate RPC
# would time the RPC, not the page. So the BODY IS GENERATED INSIDE THE
# BROWSER and handed to window.__mock.feed() one line at a time — the same
# entry point a real line takes (MockTransport.receive -> onLine,
# web/sync/sync.js:355) — and the clock is performance.now() in the page.
#
# What this models, and what it does not:
#  * It feeds in chunks and yields between them, because SerialTransport's
#    _readLoop (web/sync/sync.js:326) yields to the event loop between reads.
#    A single synchronous loop would report a frozen UI that the real link
#    would never produce.
#  * feedCpuMs sums ONLY the chunk loops, so the yields, the DOM sampling and
#    anything Python does are outside it. That is the page's own cost.
#  * bodyWallMs is the whole body including those yields and samples — the
#    harness's clock, stated as such, never as the page's cost.
#  * genCpuMs is measured separately by GENERATE_ONLY_JS: the same loop with
#    the feed() call removed. feedCpuMs - genCpuMs is what the PAGE did.
BENCH_JS = """
(cfg) => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const B = {
    done: false, error: null,
    rows: 0, bodyBytes: 0, chunks: 0,
    feedCpuMs: 0, bodyWallMs: 0, maxChunkMs: 0, maxYieldMs: 0,
    tailMs: null, phase: null,
    perfMemPresent: false, perfMem: [],
    rafIdle: 0, rafBody: 0, rafIdleMs: cfg.rafIdleMs,
    progress: [], bar: [],
  };
  window.__bench = B;

  (async () => {
    const feed = window.__mock.feed;
    const sent = window.__mock.sent;
    let answered = cfg.answeredSoFar;
    const pt = document.getElementById('progress-text');
    const bf = document.getElementById('bar-fill');
    const mem = performance.memory;
    B.perfMemPresent = !!(mem && typeof mem.usedJSHeapSize === 'number');

    // A requestAnimationFrame control FIRST: how many frames this browser
    // hands an idle page. Without it a low frame count during the transfer
    // would be read as a freeze when it only ever meant "headless".
    let raf = 0, rafOn = true;
    const tick = () => { raf++; if (rafOn) requestAnimationFrame(tick); };
    requestAnimationFrame(tick);
    await sleep(cfg.rafIdleMs);
    B.rafIdle = raf;
    raf = 0;

    if (sent[answered] !== 'trace') {
      B.error = 'expected the page to be waiting on `trace`, found '
              + JSON.stringify(sent[answered]);
      B.done = true; rafOn = false; return;
    }
    answered++;

    let lastText = null, lastWidth = null, lastMem = null;
    const sample = (elapsed) => {
      const t = pt.textContent;
      if (t !== lastText) { lastText = t; B.progress.push([Math.round(elapsed), t]); }
      const w = bf.style.width;
      if (w !== lastWidth) { lastWidth = w; B.bar.push([Math.round(elapsed), w]); }
      if (B.perfMemPresent) {
        const u = mem.usedJSHeapSize;
        if (u !== lastMem) { lastMem = u; B.perfMem.push([Math.round(elapsed), u]); }
      }
    };

    const N = cfg.rows, wideFrom = cfg.rows - cfg.wide, hz = cfg.logHz;
    const t0 = performance.now();
    feed('FILE trace.csv BEGIN');
    feed('t,mag');
    B.bodyBytes = 6;
    let i = 0;
    while (i < N) {
      const end = Math.min(N, i + cfg.chunkRows);
      const c0 = performance.now();
      for (; i < end; i++) {
        const s = (i / hz).toFixed(3);
        const m = i >= wideFrom ? (10 + (i % 7) / 10).toFixed(3)
                                : (1 + 0.01 * (i % 20)).toFixed(3);
        const line = s + ',' + m;
        B.bodyBytes += line.length + 1;
        feed(line);
      }
      const c1 = performance.now();
      const dt = c1 - c0;
      B.feedCpuMs += dt;
      if (dt > B.maxChunkMs) B.maxChunkMs = dt;
      B.chunks++;
      sample(c1 - t0);
      await sleep(0);
      const y = performance.now() - c1;
      if (y > B.maxYieldMs) B.maxYieldMs = y;
    }
    B.rows = i;
    feed('FILE trace.csv END');
    feed('OK trace');
    B.bodyWallMs = performance.now() - t0;
    B.rafBody = raf;
    sample(B.bodyWallMs);

    // The rest of the pull, answered from the SAME FakePuck replies Python
    // built — only the delivery is in here, so the puck stays in Python.
    let mark = null;
    const settled = () => {
      const p = window.__sync.state().phase;
      return (p === 'pulled' || p === 'failed') ? p : null;
    };
    const deadline = performance.now() + cfg.tailTimeoutMs;
    for (;;) {
      while (answered < sent.length) {
        const cmd = sent[answered++];
        const reply = cfg.replies[cmd];
        if (!reply) {
          B.error = 'the page sent a command the bench has no canned reply for: ' + cmd;
          break;
        }
        for (const l of reply) feed(l);
        mark = performance.now();     // last line of the last reply
      }
      if (B.error) break;
      // endPullOk runs in the microtask right behind the reply that finished
      // the last capture, so spin microtasks before spending a 1 ms timer
      // clamp on it: this is the number that says whether the page freezes
      // after the bar reaches the end.
      for (let k = 0; k < 200 && !settled(); k++) await Promise.resolve();
      const p = settled();
      if (p) {
        B.phase = p;
        if (mark !== null) B.tailMs = performance.now() - mark;
        break;
      }
      if (performance.now() > deadline) { B.error = 'the pull never settled'; break; }
      await sleep(0);
    }
    rafOn = false;
    B.done = true;
  })().catch((e) => {
    B.error = String((e && e.stack) || e);
    B.done = true;
  });
  return true;
}
"""

# The control: byte-for-byte the same row generation with feed() removed, so
# the fixture's own cost can be subtracted from feedCpuMs instead of being
# reported as the page's. It also recomputes the body length independently of
# plan_csv_body().
GENERATE_ONLY_JS = """
(cfg) => {
  const N = cfg.rows, wideFrom = cfg.rows - cfg.wide, hz = cfg.logHz;
  let bytes = 6, sink = 0;
  const t0 = performance.now();
  for (let i = 0; i < N; i++) {
    const s = (i / hz).toFixed(3);
    const m = i >= wideFrom ? (10 + (i % 7) / 10).toFixed(3)
                            : (1 + 0.01 * (i % 20)).toFixed(3);
    const line = s + ',' + m;
    bytes += line.length + 1;
    sink += line.charCodeAt(0);
  }
  return { ms: performance.now() - t0, bytes: bytes, sink: sink };
}
"""

# Step 3, timed in the page for the same reason: the click, the zip build and
# the hand-off to the browser's downloader, with no Python in the loop.
SEND_JS = """
async (timeoutMs) => {
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const t0 = performance.now();
  document.querySelector('[data-testid=btn-send]').click();
  while (!window.__sync.state().delivered) {
    if (performance.now() - t0 > timeoutMs) return { ms: null, size: null };
    await sleep(5);
  }
  const b = window.__sync.lastBundle();
  return { ms: performance.now() - t0, size: b ? b.blob.size : null,
           name: b ? b.name : null };
}
"""


class FakePuck:
    """Plays the puck: one canned reply per command, in the order asked.

    reply() returns None for a command it was never taught, and the driver
    turns that into a loud failure — an unexpected command must never look
    like a puck that simply said nothing.
    """

    def __init__(self, *, traceraw="ok", stored_jumps=3, jumps_rows=None,
                 trace_bytes=51234, stats_after_clear=(0, 0), raw=None,
                 jumps_warning=False, fs_down=False, csv_rows=None,
                 trace_bytes_growth=0, stats_silent=False):
        self.traceraw = traceraw
        self.stored_jumps = stored_jumps
        self.jumps_rows = JUMPS_ROWS if jumps_rows is None else jumps_rows
        self.trace_bytes = trace_bytes
        # A real puck does not stop recording because someone plugged it in:
        # trace_bytes read at connect is a FLOOR, and the second `stats` the
        # page asks for after the dump is the ceiling. `trace_bytes_growth` is
        # added from the SECOND `stats` on, so a fixture can play a puck that
        # logged N more bytes while it was being handled.
        self.trace_bytes_growth = trace_bytes_growth
        # 'OK stats' with no STATS line in front of it: err is null, and the
        # page has no uptime_s, so no wall-clock anchor for the trace
        # (trace_epoch_utc, web/sync/CONTRACT.md §2.3).
        self.stats_silent = stats_silent
        self.stats_calls = 0
        self.stats_after_clear = stats_after_clear
        self.raw = TRACE_RAW if raw is None else raw
        # The puck complains about a short jumps.csv INSIDE the frame, which is
        # the only protection that file has (it carries no crc, no byte count).
        self.jumps_warning = jumps_warning
        # fs=down: the store never mounted, so every count it reports is
        # unknown rather than zero (firmware/src/main.cpp, `stats`).
        self.fs_down = fs_down
        self.csv_rows = CSV_ROWS if csv_rows is None else csv_rows
        self.cleared = False

    def reply(self, cmd: str):
        if cmd == "info":
            return list(INFO_LINES)
        if cmd == "stats":
            self.stats_calls += 1
            if self.stats_silent:
                return ["OK stats"]
            if self.fs_down:
                # Both lines, from the same `if (!fs_ok)` in main.cpp.
                return ["# storage NOT MOUNTED — the counts below are unknown, not zero",
                        stats_line(0, 0) + " fs=down", "OK stats"]
            if self.cleared:
                j, tb = self.stats_after_clear
                return [stats_line(j, tb), "OK stats"]
            grown = self.trace_bytes + (self.trace_bytes_growth
                                        if self.stats_calls > 1 else 0)
            return [stats_line(self.stored_jumps, grown), "OK stats"]
        if cmd == "jumps":
            rows = list(self.jumps_rows)
            tail = [incomplete_warning("jumps.csv")] if self.jumps_warning else []
            head = [] if self.fs_down else [JUMPS_HEADER]
            return (["FILE jumps.csv BEGIN"] + head + rows + tail
                    + ["FILE jumps.csv END", "OK jumps"])
        if cmd == "traceraw":
            if self.traceraw == "unknown":
                return list(OLD_FW_TRACERAW)
            if self.traceraw == "storage_down":
                return ["ERR traceraw storage_down"]
            if self.traceraw == "short":
                return traceraw_frame(self.raw, drop_tail_lines=4)
            if self.traceraw == "warn":
                return traceraw_frame(self.raw, warn=incomplete_warning("trace.bin"))
            return traceraw_frame(self.raw)
        if cmd == "trace":
            head = [] if self.fs_down else ["t,mag"]
            return (["FILE trace.csv BEGIN"] + head + list(self.csv_rows)
                    + ["FILE trace.csv END", "OK trace"])
        if cmd == "selftest":
            return list(SELFTEST_LINES)
        if cmd == "clear":
            self.cleared = True
            return ["# cleared stored data", "OK clear"]
        return None


# ------------------------------------------------------------------ plumbing

# A Web Serial port that answers, and can be made to go away. Installed as an
# init script so navigator.serial exists before the page's init() reads it.
# Commands are looked up in window.__replies (injected beside this, from the
# same canned lines the mock seam uses), so the JS stays a transport and the
# puck stays in Python.
#
# What this buys: SerialTransport.open/_readLoop/sendLine actually run, and
# window.__fakePort.drop() ends the read loop the way a pulled cable does —
# which is the only way to reach onLinkLost() from a test. It is still a
# script, not a port: it proves nothing about a real cable on a real Mac.
FAKE_SERIAL_JS = """
(() => {
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  const pending = [];
  let waiting = null;
  let ended = false;
  let inbuf = '';
  function deliver(text) {
    const chunk = enc.encode(text);
    if (waiting) { const w = waiting; waiting = null; w({ value: chunk, done: false }); }
    else pending.push(chunk);
  }
  const port = {
    open: async () => {},
    close: async () => {},
    writable: { getWriter: () => ({
      write: async (bytes) => {
        inbuf += dec.decode(bytes);
        let nl;
        while ((nl = inbuf.indexOf('\\n')) >= 0) {
          const cmd = inbuf.slice(0, nl).trim();
          inbuf = inbuf.slice(nl + 1);
          const reply = (window.__replies || {})[cmd];
          if (reply) deliver(reply.join('\\n') + '\\n');
        }
      },
      close: async () => {},
      releaseLock: () => {},
    }) },
    readable: { getReader: () => ({
      read: () => new Promise((resolve) => {
        if (pending.length) { resolve({ value: pending.shift(), done: false }); return; }
        if (ended) { resolve({ value: undefined, done: true }); return; }
        waiting = resolve;
      }),
      cancel: async () => {},
      releaseLock: () => {},
    }) },
  };
  Object.defineProperty(Navigator.prototype, 'serial', {
    configurable: true,
    get: () => ({ requestPort: async () => port }),
  });
  window.__fakePort = { drop: () => {
    ended = true;
    if (waiting) { const w = waiting; waiting = null; w({ value: undefined, done: true }); }
  } };
})();
"""


def _serve(directory: Path):
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _launch_chromium(pw):
    errors = []
    for kwargs in ({}, {"executable_path": CHROMIUM_FALLBACK}):
        try:
            return pw.chromium.launch(headless=True, args=["--no-sandbox"], **kwargs)
        except Exception as e:  # noqa: BLE001 — any launch failure => stated skip
            errors.append(f"{kwargs or 'default'}: {e}")
    raise unittest.SkipTest(
        "no Chromium available to Playwright (CI installs one; see "
        ".github/workflows/build.yml) — " + " | ".join(errors))


@unittest.skipIf(sync_playwright is None,
                 f"playwright not importable: {_PW_IMPORT_ERROR}")
class _WebSyncCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._pw = None
        cls._browser = None
        cls._httpd = None
        try:
            cls._pw = sync_playwright().start()
            cls._browser = _launch_chromium(cls._pw)   # may raise SkipTest
            cls._httpd, cls._port = _serve(WEB_DIR)
        except BaseException:
            cls._shutdown_class()   # never leak a started Playwright on a skip
            raise

    @classmethod
    def _shutdown_class(cls):
        if getattr(cls, "_httpd", None) is not None:
            cls._httpd.shutdown()
            cls._httpd.server_close()
            cls._httpd = None
        for attr, close in (("_browser", "close"), ("_pw", "stop")):
            obj = getattr(cls, attr, None)
            if obj is not None:
                try:
                    getattr(obj, close)()
                except Exception:
                    pass
                setattr(cls, attr, None)

    @classmethod
    def tearDownClass(cls):
        cls._shutdown_class()

    def setUp(self):
        self.context = self._browser.new_context(accept_downloads=True)
        self.context.route("**/*", self._block_external)
        self.page = self.context.new_page()
        self.page.set_default_timeout(8000)
        self._page_errors = []
        self.page.on("pageerror", lambda e: self._page_errors.append(str(e)))
        self._blocked = []
        self._answered = 0

    def tearDown(self):
        errors = list(self._page_errors)
        try:
            self.context.close()
        except Exception as e:  # noqa: BLE001
            # Named, not swallowed: a context that will not close is worth
            # knowing about, but it must not mask the assertion below.
            print(f"warning: the browser context did not close cleanly: {e}")
        # An uncaught JavaScript exception makes every assertion in the test
        # that just ran meaningless — the page may have stopped before the
        # state those assertions read was ever computed. Printing it left the
        # suite green while the page threw on every test (measured: an
        # exception injected into init() produced 4 page errors and 4 passes),
        # which is precisely the shape CLAUDE.md rule 3 names.
        self.assertEqual(errors, [], "the page threw a JavaScript error:\n  "
                                     + "\n  ".join(errors))

    def _block_external(self, route):
        url = route.request.url
        if url.startswith(("http://127.0.0.1", "http://localhost", "data:", "blob:")):
            route.continue_()
        else:
            self._blocked.append(url)
            route.abort()

    # ---------------------------------------------------------------- seam --
    # Step 4 is off for this loan: the page hides the whole section unless the
    # URL carries ?allowclear=1 (ALLOW_CLEAR, web/sync/sync.js). Every test
    # that actually exercises the erase has to ask for it by name, which is
    # the point — the rider's URL never does.
    ALLOW_CLEAR = "?allowclear=1"

    def _open(self, query=""):
        self.page.goto(f"http://127.0.0.1:{self._port}/sync/{query}#mock",
                       wait_until="domcontentloaded")
        self.page.wait_for_function(
            "() => window.__mock && typeof window.__mock.feed === 'function'"
            " && Array.isArray(window.__mock.sent)"
            " && window.__sync && typeof window.__sync.state === 'function'",
            timeout=15000)

    def _feed(self, lines):
        self.page.evaluate("(ls) => ls.forEach((l) => window.__mock.feed(l))", list(lines))

    def _sent(self):
        return self.page.evaluate("() => window.__mock.sent.slice()")

    def _state(self):
        return self.page.evaluate("() => window.__sync.state()")

    def _status(self):
        return self.page.locator('[data-testid=status]').inner_text()

    def _drive(self, puck, until, what, timeout=30.0):
        """Be the puck until `until()` is true: answer every command the page
        sends, then re-check. A command the fake puck was never taught fails
        the test by name rather than stalling until the timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sent = self._sent()
            if len(sent) > self._answered:
                cmd = sent[self._answered]
                self._answered += 1
                lines = puck.reply(cmd)
                self.assertIsNotNone(
                    lines, f"the page sent a command the fake puck has no answer "
                           f"for: {cmd!r} (all sent: {sent})")
                self._feed(lines)
                continue
            if until():
                return
            time.sleep(0.02)
        self.fail(f"timed out waiting for {what}. state={self._state()} "
                  f"sent={self._sent()} status={self._status()!r}")

    def _answer_until(self, puck, stop_before, timeout=15.0):
        """Answer commands one at a time until `stop_before` is the next one
        the page sent, and leave that one unanswered — so the test can play
        that command badly (a frame that never ends)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            sent = self._sent()
            if len(sent) > self._answered:
                cmd = sent[self._answered]
                if cmd == stop_before:
                    return
                self._answered += 1
                lines = puck.reply(cmd)
                self.assertIsNotNone(
                    lines, f"the page sent a command the fake puck has no "
                           f"answer for: {cmd!r} (all sent: {sent})")
                self._feed(lines)
                continue
            time.sleep(0.02)
        self.fail(f"the page never sent {stop_before!r}. sent={self._sent()} "
                  f"state={self._state()}")

    def _connect(self, puck, query=""):
        self._open(query)
        self._drive(puck, lambda: self._state()["phase"] == "connected",
                    "the page to finish reading the puck on connect")

    def _wait_for(self, cond, what, timeout=10.0):
        """Poll a Python-side predicate. Used where page.wait_for_function
        cannot be: its default polling is requestAnimationFrame, which the
        fake clock in the retry test stubs out."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cond():
                return
            time.sleep(0.03)
        self.fail(f"timed out waiting for {what}. state={self._state()} "
                  f"status={self._status()!r}")

    def _pull(self, puck):
        self.page.click("[data-testid=btn-pull]")
        self._drive(puck, lambda: self._state()["phase"] in ("pulled", "failed"),
                    "the pull to finish")

    def _send(self, puck):
        self.page.click("[data-testid=btn-send]")
        self._drive(puck, lambda: self._state()["delivered"] is True,
                    "the bundle to be delivered")

    def _bundle(self):
        """Read lastBundle() out of the page as base64 and open it as a zip."""
        b64 = self.page.evaluate(
            """async () => {
                 const b = window.__sync.lastBundle();
                 if (!b) return null;
                 const u = new Uint8Array(await b.blob.arrayBuffer());
                 let s = '';
                 for (let i = 0; i < u.length; i += 0x8000) {
                   s += String.fromCharCode.apply(null, u.subarray(i, i + 0x8000));
                 }
                 return { name: b.name, b64: btoa(s) };
               }""")
        self.assertIsNotNone(b64, "the page kept no bundle in memory after Send")
        return b64["name"], zipfile.ZipFile(io.BytesIO(base64.b64decode(b64["b64"])))


class TestWebSync(_WebSyncCase):
    """The Bluetooth-shaped flow (the '#mock' seam). The plumbing lives in
    _WebSyncCase so TestWebSyncCable below can share it without re-running
    every test here a second time through inheritance."""

    # --------------------------------------------------------------- tests --
    def test_happy_path_traceraw_bundle(self):
        """Connect -> pull over traceraw -> note -> send -> clear, then the
        bundle Josh receives is checked file by file against the fixture.

        Driven with ?allowclear=1, because step 4 does not exist on the URL
        the rider is given (test_step_four_is_hidden_for_this_loan below pins
        that half). This is the flag half: with it, and only after verified
        AND delivered, the button is there and the erase works."""
        puck = FakePuck()
        self._connect(puck, self.ALLOW_CLEAR)

        # Step 1 shows the puck the rider can recognise, without jargon.
        self.assertIn("JumpHeight-E2C4",
                      self.page.locator("[data-testid=puck-name]").inner_text())
        self.assertIn("charging", self.page.locator("[data-testid=battery]").inner_text())

        self._pull(puck)
        st = self._state()
        self.assertTrue(st["verified"], f"a clean traceraw pull must verify: {st['reasons']}")
        self.assertEqual(st["trace_format"], "jhtrace-v2-b64")
        self.assertEqual(st["jump_rows"], 3)

        # The note and one condition chip both have to reach notes.txt.
        self.page.fill("[data-testid=note]", "big one near the end, board felt loose")
        self.page.locator("#chips-sea button").filter(has_text="small chop").click()

        self._send(puck)
        name, z = self._bundle()
        self.assertRegex(name, r"^jumpheight-E2C4-\d{8}-\d{4}\.zip$")

        names = set(z.namelist())
        self.assertEqual(names, {"manifest.json", "jumps.csv", "trace.bin",
                                 "notes.txt", "device.log"})
        # Python's zipfile must be able to read what the page's own zip writer
        # produced — CRCs included, which testzip() checks for every member.
        self.assertIsNone(z.testzip(), "the page wrote a zip with a bad CRC")
        # BOTH writer branches in one bundle: 'store' for trace.bin, deflate
        # for the text. Asserted rather than left to luck, because a silently
        # store-only bundle would leave the deflate branch shipped and never
        # exercised. CI pins the browser (build.yml), so CompressionStream is
        # guaranteed here; a browser without it stores everything, which the
        # page treats as a bigger zip, never a broken one.
        methods = {i.filename: i.compress_type for i in z.infolist()}
        self.assertEqual(methods["trace.bin"], zipfile.ZIP_STORED)
        self.assertIn(zipfile.ZIP_DEFLATED, methods.values(),
                      f"no member was deflated — the deflate branch went untested: {methods}")

        man = json.loads(z.read("manifest.json"))
        for key in ("bundle_version", "page_version", "puck_name", "fw", "src",
                    "synced_at_utc", "synced_at_local", "tz_offset_min", "uptime_s",
                    "trace_epoch_utc", "info_lines", "cal", "stats_before",
                    "stats_after", "selftest_lines", "trace_format", "log_hz",
                    "trace_bytes_device", "trace_bytes_after", "trace_bytes_got",
                    "f22_band_applied",
                    "trace_raw_bytes", "trace_crc32",
                    "stored_jumps_device", "jump_rows", "verified", "cleared",
                    "transfer", "user_agent"):
            self.assertIn(key, man, f"manifest.json is missing web/sync/CONTRACT.md §2 key {key!r}")
        self.assertEqual(man["bundle_version"], 1)
        self.assertEqual(man["puck_name"], "JumpHeight-E2C4")
        self.assertEqual(man["fw"], "0.4.3")
        self.assertEqual(man["src"], "5c80a436")
        self.assertEqual(man["trace_format"], "jhtrace-v2-b64")
        self.assertEqual(man["log_hz"], LOG_HZ)
        self.assertEqual(man["trace_raw_bytes"], len(TRACE_RAW))
        self.assertEqual(man["trace_crc32"], f"{TRACE_CRC:08x}")
        self.assertEqual(man["stored_jumps_device"], 3)
        self.assertEqual(man["jump_rows"], 3)
        self.assertTrue(man["verified"])
        self.assertEqual(man["uptime_s"], 12345.678)
        self.assertEqual(man["transfer"]["transport"], "mock")
        self.assertTrue(man["page_version"], "page_version must not be empty")
        self.assertIn("# name=JumpHeight-E2C4", man["info_lines"])
        self.assertIn("SELFTEST END result=PASS", man["selftest_lines"])

        # The wall-clock anchor: trace_epoch_utc == synced_at_utc - uptime_s.
        # This is the ONLY thing that ever ties trace time to the real world
        # (the puck has no RTC), so it is worth arithmetic, not a presence check.
        import datetime
        synced = datetime.datetime.fromisoformat(man["synced_at_utc"].replace("Z", "+00:00"))
        epoch = datetime.datetime.fromisoformat(man["trace_epoch_utc"].replace("Z", "+00:00"))
        self.assertAlmostEqual((synced - epoch).total_seconds(), man["uptime_s"], places=2)

        # trace.bin is the fixture, byte for byte.
        raw = z.read("trace.bin")
        self.assertEqual(len(raw), len(TRACE_RAW))
        self.assertEqual(raw, TRACE_RAW, "trace.bin differs from the encoded region")
        self.assertEqual(zlib.crc32(raw) & 0xFFFFFFFF, TRACE_CRC)

        # jumps.csv is the body as received, header included.
        self.assertEqual(z.read("jumps.csv").decode(),
                         "\n".join([JUMPS_HEADER] + JUMPS_ROWS) + "\n")

        notes = z.read("notes.txt").decode()
        self.assertTrue(notes.startswith("# JumpHeight rider notes — "),
                        f"notes.txt first line is wrong: {notes.splitlines()[:1]}")
        self.assertIn("big one near the end, board felt loose", notes)
        self.assertIn("sea: small chop", notes)

        log = z.read("device.log").decode()
        self.assertIn("INFO fw=0.4.3", log)
        self.assertIn("STATS session_jumps=0", log)
        self.assertIn("FILE trace.bin BEGIN", log)
        # The frame lines stay, the body does not: device.log is meant to be
        # readable, and the body is ~2.7 MB of base64 on a full region.
        first_b64 = base64.b64encode(TRACE_RAW).decode("ascii")[:76]
        self.assertNotIn(first_b64, log,
                         "device.log must not carry the base64 body (web/sync/CONTRACT.md §2)")

        # Static files only, zero external requests (web/sync/CONTRACT.md §3): the route
        # handler turned nothing away.
        self.assertEqual(self._blocked, [],
                         "the page reached outside localhost — it must be "
                         "self-contained, with no CDN and no framework")

        # Step 4 only now, only under the flag, and it must confirm from the
        # puck's own stats.
        self.assertFalse(self.page.locator("#step-clear").is_hidden(),
                         "?allowclear=1 must bring step 4 back for Josh")
        self.assertFalse(self.page.locator("[data-testid=btn-clear]").is_hidden(),
                         "Clear must be offered once the ride is verified and "
                         "delivered AND the URL asked for it")
        self.page.click("[data-testid=btn-clear]")
        self._drive(puck, lambda: self._state()["phase"] in ("cleared", "failed"),
                    "the puck to confirm it is empty")
        self.assertTrue(self._state()["cleared"])
        self.assertIn("clear", self._sent())
        self.assertIn("Puck is empty and ready for your next ride.", self._status())

    def test_old_firmware_falls_back_to_csv(self):
        """`traceraw` answered with help + 'ERR unknown_command' -> the page
        sends `trace` and bundles trace.csv. Today's OG (src=5c80a436) is
        exactly this puck, so this is the path the rider gets until the next
        flash lands."""
        puck = FakePuck(traceraw="unknown", trace_bytes=CSV_BYTES)
        self._connect(puck)
        self._pull(puck)

        sent = self._sent()
        self.assertIn("traceraw", sent)
        self.assertIn("trace", sent, "the page must fall back to `trace` on "
                                     "ERR unknown_command")
        self.assertLess(sent.index("traceraw"), sent.index("trace"),
                        "`trace` is a FALLBACK — it must be tried second")

        st = self._state()
        self.assertEqual(st["trace_format"], "csv")
        self.assertTrue(st["verified"], f"a byte-exact csv pull must verify: {st['reasons']}")

        self._send(puck)
        name, z = self._bundle()
        names = set(z.namelist())
        self.assertIn("trace.csv", names)
        self.assertNotIn("trace.bin", names)
        self.assertIsNone(z.testzip())
        self.assertEqual(z.read("trace.csv").decode(), CSV_BODY)

        man = json.loads(z.read("manifest.json"))
        self.assertEqual(man["trace_format"], "csv")
        self.assertIsNone(man["trace_raw_bytes"])
        self.assertIsNone(man["trace_crc32"])
        self.assertEqual(man["trace_bytes_device"], CSV_BYTES)
        self.assertEqual(man["trace_bytes_got"], CSV_BYTES)
        self.assertFalse(man["f22_band_applied"],
                         "a byte-exact pull did not need F-22's band")
        self.assertTrue(man["verified"])

    # -------------------------------------------------------- audit F-22 --
    # `STATS trace_bytes` is a LIVE counter — it counts a block's bytes before
    # the block closes — so once the trace region is FULL (the firmware STOPS
    # writing rather than wrapping) it reads HIGH by at most one 50-sample
    # batch: 800 B, "exactly one 50-sample batch at 16 B/line"
    # (docs/audit-2026-08-22.md). Measured on the OG 2026-09-07, which is the
    # csv-only puck the rider actually has: `tracecheck fast=15917918
    # slow=15917153` — a −765 B gap on a download that was COMPLETE.
    #
    # This page cannot ask `tracecheck` (that command re-walks the whole
    # region and takes minutes), so the band is the entire arbiter here, and
    # these three tests are what keeps it one-sided and narrow.
    F22_BAND = 800

    def _csv_pull_with_gap(self, gap):
        """Drive a full csv pull where the puck's counter sits `gap` bytes
        ABOVE the trace.csv it actually sends (negative = it sends more)."""
        puck = FakePuck(traceraw="unknown", trace_bytes=CSV_BYTES + gap)
        self._connect(puck)
        self._pull(puck)
        st = self._state()
        self.assertEqual(st["trace_format"], "csv")
        return puck, st

    def test_csv_f22_band_verifies_and_records_itself(self):
        """The measured −765 B case: verified, with the quirk said out loud
        and written into the manifest. Without this the one puck the rider
        has cannot produce a verified bundle once it fills, and the page
        tells him to re-pull a ride that already came across whole."""
        puck, st = self._csv_pull_with_gap(765)

        self.assertTrue(st["verified"],
                        f"a −765 B F-22 gap is a complete copy: {st['reasons']}")
        self.assertEqual(st["reasons"], [])
        self.assertTrue(st["f22_band_applied"])
        self.assertEqual(st["trace_bytes_device"], CSV_BYTES + 765)
        self.assertEqual(st["trace_bytes_got"], CSV_BYTES)

        # Said out loud, in the rider's language, on the screen he reads.
        note = st["f22_note"]
        self.assertIn("The puck is full", note)
        self.assertIn("765", note)
        self.assertIn("the copy is complete", note)
        self.assertIn(note, self.page.locator("[data-testid=result]").inner_text(),
                      "the forgiveness must be shown, not just recorded")

        self._send(puck)
        _name, z = self._bundle()
        man = json.loads(z.read("manifest.json"))
        self.assertTrue(man["verified"])
        self.assertTrue(man["f22_band_applied"])
        self.assertEqual(man["trace_bytes_device"], CSV_BYTES + 765)
        self.assertEqual(man["trace_bytes_got"], CSV_BYTES)
        # And the whole file really is there.
        self.assertEqual(z.read("trace.csv").decode(), CSV_BODY)

    def test_csv_gap_past_the_band_is_still_a_refusal(self):
        """801 B — one byte past one batch. More than F-22 ever explained, so
        it stays a short download: no step 4, and the puck keeps everything."""
        _puck, st = self._csv_pull_with_gap(self.F22_BAND + 1)

        self.assertFalse(st["verified"], "801 B short must not verify")
        self.assertFalse(st["f22_band_applied"])
        joined = " ".join(st["reasons"])
        self.assertIn("came across", joined)
        self.assertNotIn("known quirk", joined)
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden(),
                        "Clear must never be offered on an unverified ride")
        self.assertIn("still has everything", self._status())

    def test_csv_surplus_is_still_a_refusal(self):
        """One byte MORE than the puck says it holds. F-22 only ever runs the
        device counter high; the band is not "within 800 either way"."""
        _puck, st = self._csv_pull_with_gap(-1)

        self.assertFalse(st["verified"], "a surplus is unexplained, not F-22")
        self.assertFalse(st["f22_band_applied"])
        joined = " ".join(st["reasons"])
        self.assertIn("More ride data arrived than the puck says it has", joined)
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden())

    def test_short_transfer_is_not_verified_and_offers_no_clear(self):
        """The cruellest failure this product can have: recorded perfectly,
        downloaded short, then erased. The puck advertises the full bytes= and
        the link stops four lines early — the page must refuse to verify, must
        not offer step 4, and must say the puck still has everything."""
        puck = FakePuck(traceraw="short")
        self._connect(puck)
        self._pull(puck)

        st = self._state()
        self.assertFalse(st["verified"], "a short transfer must not verify")
        self.assertTrue(st["reasons"], "an unverified pull must name its reasons")
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden(),
                        "Clear must never be offered on an unverified ride")

        status = self._status()
        self.assertIn("still has everything", status,
                      f"the refusal must say the puck kept the ride: {status!r}")

        # Both independent checks fired: fewer bytes AND a crc that can't match.
        joined = " ".join(st["reasons"]).lower()
        self.assertIn("came across", joined)
        self.assertIn("check number", joined)

        # And it never reached the puck's erase command.
        self.assertNotIn("clear", self._sent())

    def test_clear_is_never_sent_before_the_bundle_is_delivered(self):
        """Ordering, asserted at every stage — this is the one command on the
        wire that cannot be undone.

        Run under ?allowclear=1: without the flag there is no step 4 at all
        and this ordering has nothing to say. The flag does NOT weaken the
        gate — verified AND delivered still both have to hold, and that is
        what the middle of this test measures."""
        puck = FakePuck()
        self._connect(puck, self.ALLOW_CLEAR)
        self.assertNotIn("clear", self._sent())
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden())

        self._pull(puck)
        self.assertTrue(self._state()["verified"])
        # Verified but NOT yet delivered: still no step 4, still no clear.
        self.assertFalse(self._state()["delivered"])
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden(),
                        "verified alone must not unlock Clear — delivered too "
                        "(web/sync/CONTRACT.md §3 step 4)")
        self.assertNotIn("clear", self._sent())

        self._send(puck)
        # Delivered, and the flag is on: the button appears, but nothing has
        # gone to the puck until the rider actually taps it.
        self.assertFalse(self.page.locator("[data-testid=btn-clear]").is_hidden())
        self.assertNotIn("clear", self._sent(),
                         "the page must not send `clear` on its own")

    def test_step_four_is_hidden_for_this_loan(self):
        """The rider's URL carries no ?allowclear=1, so there is no step 4 at
        all: no section, no heading, no button — and the page's last word is
        that he is finished.

        The puck is out on loan and the brief is that he must NEVER empty it;
        Josh does that on the bench, where a short download can still be
        re-pulled off a puck that still holds the ride. The verified-AND-
        delivered gate is not what is being measured here: both hold by the
        end of this test and the button is still gone."""
        puck = FakePuck()
        self._connect(puck)
        self.assertTrue(self.page.locator("#step-clear").is_hidden(),
                        "step 4 must not be on the rider's page at all")
        self.assertIn("Josh empties the puck",
                      self.page.locator("#finish-hint").inner_text())

        self._pull(puck)
        self._send(puck)
        st = self._state()
        self.assertTrue(st["verified"])
        self.assertTrue(st["delivered"])
        self.assertTrue(self.page.locator("#step-clear").is_hidden(),
                        "verified AND delivered must not bring step 4 back "
                        "without ?allowclear=1")
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden())

        # The sentence he is left with names who empties the puck. The one it
        # replaced — "Last step: empty the puck so it has room for your next
        # ride" — told him to do the one thing the brief forbids, on the
        # success path, where he is most likely to follow it.
        status = self._status()
        self.assertIn("finished", status)
        self.assertIn("leave the puck to Josh", status)
        self.assertNotIn("empty the puck so it has room", status)
        self.assertNotIn("Last step", status)

        # Belt and braces: doClear() re-checks the flag, so a button un-hidden
        # by hand still writes nothing to the wire.
        self.page.evaluate(
            "() => { document.getElementById('step-clear').hidden = false;"
            "        const b = document.getElementById('btn-clear');"
            "        b.hidden = false; b.disabled = false; }")
        self.page.click("[data-testid=btn-clear]")
        # `clear` would be on the wire synchronously inside doClear; give the
        # page a round-trip anyway before believing it isn't.
        time.sleep(0.3)
        self.assertNotIn("clear", self._sent(),
                         "doClear must re-check ALLOW_CLEAR, not just the button")

    # ------------------------------------------- the stale denominator (3) --
    # `trace_bytes` comes off the CONNECT-time `stats`, latched once. The puck
    # does not stop recording because someone plugged it in, so minutes later
    # the copy can legitimately be BIGGER than that first reading — and the
    # surplus arm turned that into a hard failure that every retry reproduced,
    # because every retry starts by taking the same stale reading again. The
    # pull's own second `stats` is the honest ceiling.

    def _csv_pull_with_growth(self, before_gap, growth):
        """A csv pull where the puck reports `CSV_BYTES - before_gap` at
        connect and `CSV_BYTES - before_gap + growth` on the second `stats`,
        having handed over the whole CSV_BYTES body in between."""
        puck = FakePuck(traceraw="unknown", trace_bytes=CSV_BYTES - before_gap,
                        trace_bytes_growth=growth)
        self._connect(puck)
        self._pull(puck)
        st = self._state()
        self.assertEqual(st["trace_format"], "csv")
        return puck, st

    def test_csv_growth_between_the_two_stats_reads_verifies(self):
        """+700 B logged while the puck was being handled: verified, said out
        loud, and both readings recorded in the manifest."""
        puck, st = self._csv_pull_with_growth(700, 700)

        self.assertTrue(st["verified"],
                        f"a puck that kept logging is not a surplus: {st['reasons']}")
        self.assertEqual(st["reasons"], [])
        self.assertEqual(st["trace_bytes_device"], CSV_BYTES - 700)
        self.assertEqual(st["trace_bytes_after"], CSV_BYTES)
        self.assertEqual(st["trace_bytes_got"], CSV_BYTES)
        self.assertFalse(st["f22_band_applied"],
                         "this is growth, not F-22 — F-22 only ever runs the "
                         "counter HIGH")

        note = st["growth_note"]
        self.assertIn("kept recording while you plugged it in", note)
        self.assertIn("700", note)
        self.assertIn("that is normal", note)
        self.assertIn(note, self.page.locator("[data-testid=result]").inner_text(),
                      "the explanation must be shown, not just recorded")

        self._send(puck)
        _name, z = self._bundle()
        man = json.loads(z.read("manifest.json"))
        self.assertTrue(man["verified"])
        self.assertEqual(man["trace_bytes_device"], CSV_BYTES - 700)
        self.assertEqual(man["trace_bytes_after"], CSV_BYTES,
                         "the after-reading is the ceiling ingest needs to "
                         "re-derive this for itself")
        self.assertEqual(man["trace_bytes_got"], CSV_BYTES)
        self.assertFalse(man["f22_band_applied"])
        self.assertEqual(z.read("trace.csv").decode(), CSV_BODY)

    def test_csv_growth_past_the_after_reading_is_still_a_refusal(self):
        """One byte more than the puck ever said it held, on either reading.
        The window is [before − 800, after]; outside it, unexplained is
        unexplained."""
        _puck, st = self._csv_pull_with_growth(700, 699)

        self.assertFalse(st["verified"], "a surplus past stats-after is not growth")
        self.assertIsNone(st["growth_note"])
        self.assertFalse(st["f22_band_applied"])
        joined = " ".join(st["reasons"])
        self.assertIn("More ride data arrived than the puck says it has", joined)
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden())

    # ------------------------------------------------- the header-only case --
    def test_a_ride_with_no_jumps_is_not_called_nothing(self):
        """"No jumps" and "nothing recorded" are different states.

        MEASURED 2026-09-09, driving the real page over Web Serial against
        JumpHeight-8673: 455 KB of ride data, 0 stored jumps, and the page
        said "Nothing was recorded on the puck." False, and expensive — that
        is the exact shape of the 2026-09-06 water session (47 minutes on the
        water, a full trace, not one real jump in it, docs/STATUS.md), which
        is the most valuable capture this project has. A rider told nothing
        was recorded has every reason not to send it."""
        # A real ride's worth of trace with no jumps detected in it, on the
        # csv path — the only one Nick's OG can take. CSV_ROWS/CSV_BYTES are
        # the module's own body; the fake emits the "t,mag" header itself.
        puck = FakePuck(traceraw="unknown", stored_jumps=0, jumps_rows=[],
                        trace_bytes=CSV_BYTES)
        self._connect(puck)
        self._pull(puck)
        st = self.page.locator("[data-testid=status]").inner_text()
        res = self.page.locator("[data-testid=result]").inner_text()
        self.assertIn("No jumps were detected", st)
        self.assertIn("the whole ride is here", st)
        self.assertNotIn("nothing was recorded", st.lower(),
                         "a full trace is not 'nothing recorded'")
        self.assertNotIn("nothing was recorded", res.lower())
        self.assertTrue(self.page.evaluate("() => window.__sync.state().verified"))

    def test_header_only_trace_region_reads_as_an_empty_puck(self):
        """A real nrf52 puck ALWAYS emits the 6-byte "t,mag\\n" header when it
        dumps trace.csv — read_chunk() sends it before it looks at whether
        there is a single stored byte behind it
        (firmware/src/platform/nrf52/jh_store.cpp:1119-1126) — while
        trace_bytes only starts counting that header on the first append
        (:1058-1063). So a healthy, empty puck reports 0 and hands over 6, and
        the surplus arm called it "6 bytes against the puck's 0 … that does
        not add up": a refusal on the one puck state that is perfectly fine,
        and it made endPullOk's "no jumps saved on it" branch unreachable on
        real hardware. This is the rider's own puck, on the csv path, the
        first ride after Josh emptied it."""
        puck = FakePuck(traceraw="unknown", stored_jumps=0, trace_bytes=0,
                        jumps_rows=[], csv_rows=[])
        self._connect(puck)
        self._pull(puck)

        st = self._state()
        self.assertTrue(st["verified"],
                        f"an empty puck is a perfectly good outcome: {st['reasons']}")
        self.assertEqual(st["trace_bytes_device"], 0)
        self.assertEqual(st["trace_bytes_got"], 6, "the 't,mag' header, and nothing else")
        self.assertFalse(st["f22_band_applied"], "nothing was forgiven by F-22 here")
        self.assertEqual(st["jump_rows"], 0)

        status = self._status()
        self.assertIn("nothing saved on it", status,
                      f"the empty-puck sentence must be reachable: {status!r}")
        self.assertIn("send it so Josh can see why", status)

        self._send(puck)
        _name, z = self._bundle()
        self.assertEqual(z.read("trace.csv").decode(), "t,mag\n")
        self.assertTrue(json.loads(z.read("manifest.json"))["verified"])

    # --------------------------------------------------- the retry poison --
    def test_a_pull_that_died_mid_frame_can_be_retried(self):
        """`S.fileSection` is the page's "which FILE body am I inside" flag.
        It was reset in exactly two places — freshSession() and a
        'FILE … END' line — so a pull that died INSIDE a frame left it
        pointing at trace.csv for the rest of the session. The retry's own
        'FILE jumps.csv BEGIN' then matched `inBody` instead of `isBegin` and
        was swallowed into the trace sink: jumps.csv came out empty, check (b)
        failed, and every retry reproduced the failure of the attempt before
        it. The rider's remedy for a failed pull is to tap the button again,
        so this is the path he is most likely to be on.

        The 30 s inactivity timer is fast-forwarded rather than waited out, so
        what fires is the page's own INACTIVITY_MS timer on the real timeout
        path. With the fake clock in place the DOM is clicked from script:
        Playwright's own actionability polling uses requestAnimationFrame,
        which the clock stubs."""
        puck = FakePuck(traceraw="unknown", trace_bytes=CSV_BYTES)
        self._connect(puck)
        self.page.clock.install()

        # First attempt: `jumps` and `traceraw` answered as usual, then a
        # trace.csv frame that begins and never ends.
        self.page.evaluate("() => document.getElementById('btn-pull').click()")
        self._answer_until(puck, "trace")
        self._answered += 1                     # this one we play ourselves
        self._feed(["FILE trace.csv BEGIN", "t,mag"] + list(CSV_ROWS[:3]))
        self.page.clock.fast_forward(31_000)
        self._wait_for(lambda: self._state()["phase"] == "failed",
                       "the inactivity timer to end the first pull")
        first = self._status()
        self.assertIn("stopped part-way", first)
        self.assertIn("went quiet for 30 seconds", first,
                      f"it must be the INACTIVITY_MS timer that ended it, not "
                      f"some other failure: {first!r}")

        # Second attempt: the same puck, answering everything.
        self.page.evaluate("() => document.getElementById('btn-pull').click()")
        self._drive(puck, lambda: self._state()["phase"] in ("pulled", "failed"),
                    "the retry to finish")

        st = self._state()
        self.assertEqual(st["phase"], "pulled")
        self.assertEqual(st["jump_rows"], 3,
                         "the retry's `FILE jumps.csv BEGIN` was swallowed as "
                         "trace body by the previous attempt's half-open frame")
        self.assertEqual(st["trace_bytes_got"], CSV_BYTES)
        self.assertTrue(st["verified"],
                        f"the retry must verify on its own merits: {st['reasons']}")

    # ------------------------------------------------------ the stats gate --
    def test_a_puck_that_never_answers_stats_cannot_be_pulled(self):
        """`stats` is not just "what is on it": its uptime_s, read in the same
        breath as the phone clock, is the ONLY wall-clock anchor the trace
        ever gets (trace_epoch_utc, web/sync/CONTRACT.md §2.3). A bundle
        pulled without it ships trace_epoch_utc null, which reads downstream
        as a puck with no session behind it — a live puck misdiagnosed from a
        reading that never happened (CLAUDE.md rule 3).

        The quiet shape is the one that mattered: 'OK stats' with no STATS
        line in front of it. `err` is null, so nothing failed; S.statsBefore
        is null, so nothing was read."""
        puck = FakePuck(stats_silent=True)
        self._connect(puck)

        status = self._status()
        self.assertIn("answer its first question", status)
        self.assertIn("reload this page", status)
        self.assertTrue(self.page.locator("[data-testid=btn-pull]").is_disabled(),
                        "step 2 must be shut without the wall-clock anchor")
        self.assertNotIn("jumps", self._sent(),
                         "no pull may start from a puck that did not answer `stats`")

    def test_incomplete_warning_inside_the_jumps_frame_is_heard(self):
        """The puck's own complaint arrives INSIDE the FILE frame
        (firmware/src/main.cpp printFileFramed: the warning is printed after
        the body and before "FILE jumps.csv END"). jumps.csv has no crc and no
        byte count, so web/sync/CONTRACT.md §2 check (a) is the only thing standing between
        a dropped result row and an erased puck.

        Before this was fixed the line was routed into the body sink: it
        counted as the missing third jump, check (b) compared 3 == 3, the page
        said "Got it all" and offered step 4."""
        puck = FakePuck(stored_jumps=3, jumps_rows=JUMPS_ROWS[:2], jumps_warning=True)
        self._connect(puck)
        self._pull(puck)

        st = self._state()
        self.assertFalse(st["verified"],
                         "the puck said the transfer was short — that outranks arithmetic")
        self.assertEqual(st["jump_rows"], 2,
                         "the warning line is not a jump row")
        joined = " ".join(st["reasons"])
        self.assertIn("the puck itself said", joined.lower(),
                      f"check (a) must be the reason, by name: {st['reasons']}")
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden())

        self._send(puck)
        _name, z = self._bundle()
        # The warning belongs in device.log, where `jump ingest` re-runs the
        # same check (tools/jump _verify_ingest_bundle) — and NOT in the
        # results file, where it would read as a jump.
        self.assertIn("INCOMPLETE", z.read("device.log").decode(),
                      "device.log must keep the `#` chatter emitted inside a "
                      "FILE frame (web/sync/CONTRACT.md §2)")
        self.assertEqual(z.read("jumps.csv").decode(),
                         "\n".join([JUMPS_HEADER] + JUMPS_ROWS[:2]) + "\n")
        man = json.loads(z.read("manifest.json"))
        self.assertFalse(man["verified"])
        self.assertEqual(man["jump_rows"], 2)
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden(),
                        "Clear must stay shut on an unverified ride, delivered or not")
        self.assertNotIn("clear", self._sent())

    def test_incomplete_warning_inside_the_trace_frame_is_heard(self):
        """Same line, the other frame (printTraceRawFramed emits it twice
        before "FILE trace.bin END"). This one used to fail for the wrong
        reason — atob rejecting '#' — which told the rider the data was
        damaged when the puck had said something more specific and more
        actionable."""
        puck = FakePuck(traceraw="warn")
        self._connect(puck)
        self._pull(puck)

        st = self._state()
        self.assertFalse(st["verified"])
        joined = " ".join(st["reasons"]).lower()
        self.assertIn("the puck itself said", joined)
        self.assertNotIn("unreadable", joined,
                         "the warning must never reach the base64 decoder")
        # The body itself was whole, so the byte count and the crc still agree:
        # the ONLY reason is the puck's own complaint.
        self.assertEqual(len(st["reasons"]), 1, st["reasons"])

        self._send(puck)
        _name, z = self._bundle()
        self.assertIn("INCOMPLETE", z.read("device.log").decode())
        self.assertEqual(z.read("trace.bin"), TRACE_RAW)

    def test_storage_down_is_never_read_as_an_empty_puck(self):
        """fs=down means the store never mounted, so stored_jumps=0 and
        trace_bytes=0 are readings that did not happen (firmware/src/main.cpp,
        `stats`: "A reading that could not be taken must never be dressed up as
        a reading of zero"). On today's OG — old firmware, so the CSV fallback
        — every byte-count check passes trivially against those zeros, and the
        page used to verify TRUE and offer to erase the puck."""
        puck = FakePuck(traceraw="unknown", fs_down=True, jumps_rows=[], csv_rows=[])
        self._connect(puck)

        # Step 1 must not print zeros it did not read. The byte-count row
        # ("Ride data waiting", #waiting) was cut 2026-09-09 along with "Puck
        # software" (#fw) — diagnostics, not news to a rider — so this test
        # asserts the row is GONE rather than quietly passing against an
        # element that no longer exists (CLAUDE.md rule 3), and then checks
        # the two places that still carry the condition.
        self.assertEqual(self.page.locator("#waiting").count(), 0,
                         "the byte-count row was cut; a test still reading it "
                         "would be asserting on nothing")
        self.assertEqual(self.page.locator("#fw").count(), 0,
                         "the firmware/build row was cut")
        self.assertIn("unknown", self.page.locator("#stored-jumps").inner_text().lower())
        self.assertIn("NO REC", self._status(),
                      "the rider already has a word for this (docs/rider-brief.md item 6)")

        self._pull(puck)
        st = self._state()
        self.assertFalse(st["verified"], "nothing read from an unmounted store verifies")
        self.assertTrue(any("not saving" in r for r in st["reasons"]), st["reasons"])
        self.assertIn("do NOT empty the puck", self._status())

        self._send(puck)
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden(),
                        "step 4 must stay shut for a puck whose store never mounted")
        self.assertNotIn("clear", self._sent())
        _name, z = self._bundle()
        self.assertIn("fs=down", z.read("device.log").decode())
        self.assertFalse(json.loads(z.read("manifest.json"))["verified"])

    def test_storage_down_error_is_translated_not_pasted(self):
        """New firmware answers `traceraw` with `ERR traceraw storage_down`
        (main.cpp handleCommand). The rider is on a beach: he must get a
        sentence, not the wire text — and not the standard "move closer, end
        the activity" advice, which cannot affect a store that never mounted."""
        puck = FakePuck(traceraw="storage_down")
        self._connect(puck)
        self._pull(puck)

        self.assertEqual(self._state()["phase"], "failed")
        status = self._status()
        self.assertNotIn("ERR traceraw storage_down", status,
                         f"protocol text reached the rider: {status!r}")
        self.assertIn("NO REC", status)
        result = self.page.locator("[data-testid=result]").inner_text()
        self.assertNotIn("closer to the puck", result,
                         "retry advice must not be offered where a retry cannot help")
        self.assertNotIn("still has everything", status,
                         "a puck that is not saving cannot vouch for what it holds")

    def _padding_case(self, raw: bytes):
        """Drive one traceraw pull whose base64 body ends in '=' padding, and
        check the bytes that come out the other end.

        One page per case, one case per test method: re-running _open() inside
        a loop does NOT reload (the URL, hash included, is unchanged, so the
        browser treats it as a fragment navigation) and the second case would
        silently re-assert the first one's page — a test that passes without
        testing (CLAUDE.md rule 3). Measured: it failed loudly here first.
        """
        self.assertNotEqual(len(raw) % 3, 0,
                            "this fixture pins the padding branch; a multiple "
                            "of 3 produces no '=' at all")
        puck = FakePuck(raw=raw)
        self._connect(puck)
        self._pull(puck)
        st = self._state()
        self.assertTrue(st["verified"], st["reasons"])
        self._send(puck)
        _name, z = self._bundle()
        self.assertEqual(z.read("trace.bin"), raw, "trace.bin lost the padded tail")
        man = json.loads(z.read("manifest.json"))
        self.assertEqual(man["trace_raw_bytes"], len(raw))
        self.assertEqual(man["trace_crc32"], f"{zlib.crc32(raw) & 0xFFFFFFFF:08x}")

    def test_base64_tail_with_two_pad_chars(self):
        """N mod 3 == 1 -> the body ends '=='. Two thirds of real region sizes
        end in padding; the only other fixture in this file is 3240 bytes
        (0 mod 3) and never took the page's padding branch at all."""
        self._padding_case(TRACE_RAW_PAD2)

    def test_base64_tail_with_one_pad_char(self):
        """N mod 3 == 2 -> the body ends '='."""
        self._padding_case(TRACE_RAW_PAD1)

    def _switch_to_iphone(self):
        """Replace the browser context with one that identifies as an iPhone.

        doSend's iOS branch is otherwise unreachable in CI, and it is the
        branch that runs on the only browser an iPhone rider has (web/sync/CONTRACT.md §3:
        Bluefy is the only one that can reach the puck at all). Chromium is not
        Safari and this proves NOTHING about how Bluefy handles the link — what
        it proves is that the branch runs, builds its link, and still refuses
        to claim delivery.
        """
        self.context.close()
        self.context = self._browser.new_context(
            accept_downloads=True,
            user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                       "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                       "Mobile/15E148 Safari/604.1")
        self.context.route("**/*", self._block_external)
        self.page = self.context.new_page()
        self.page.set_default_timeout(8000)
        self.page.on("pageerror", lambda e: self._page_errors.append(str(e)))

    def test_a_failed_share_sheet_falls_back_to_the_download(self):
        """MEASURED ON THE RIDER'S OWN MAC, 2026-09-10, first real use of the
        page: navigator.share() rejected with "Permission denied" and the
        share/download arms were an if/else with nothing catching it. He was
        left holding a finished 2.1 MB bundle with no way to get it out of the
        page. The ride was never at risk — it stays on the puck — but the only
        move he had was to tell Josh.

        A share that FAILS must fall through to the download."""
        self.page.add_init_script("""
            navigator.share = () => Promise.reject(
                Object.assign(new Error('Permission denied'),
                              {name: 'NotAllowedError'}));
            navigator.canShare = () => true;
        """)
        self._open()
        puck = FakePuck()
        self._connect(puck)
        self._pull(puck)
        with self.page.expect_download() as dl:
            self.page.click("[data-testid=btn-send]")
            self._drive(puck, lambda: self._state()["delivered"],
                        "the download fallback to fire")
        self.assertTrue(dl.value.suggested_filename.endswith(".zip"),
                        dl.value.suggested_filename)
        st = self._state()
        self.assertTrue(st["delivered"], "the fallback must count as delivered")
        res = self.page.locator("[data-testid=result]").inner_text()
        self.assertIn("Saved to your Downloads", res)
        self.assertIn("Nothing was lost", res)

    def test_a_cancelled_share_sheet_does_not_force_a_download(self):
        """The other half, and the reason the fallback is not unconditional:
        AbortError is the rider closing the sheet on purpose. Shoving the file
        into his Downloads because he changed his mind is not a fix."""
        self.page.add_init_script("""
            navigator.share = () => Promise.reject(
                Object.assign(new Error('share canceled'), {name: 'AbortError'}));
            navigator.canShare = () => true;
        """)
        self._open()
        puck = FakePuck()
        self._connect(puck)
        self._pull(puck)
        self.page.click("[data-testid=btn-send]")
        self._drive(puck, lambda: self.page.evaluate(
            "() => !!window.__sync.lastBundle()"), "the bundle to be built")
        self.assertFalse(self._state()["delivered"],
                         "a cancelled share must not count as delivered")
        st = self.page.locator("[data-testid=status]").inner_text()
        res = self.page.locator("[data-testid=result]").inner_text()
        self.assertIn("Not sent yet", st)
        self.assertIn("closed the share sheet", res)
        self.assertNotIn("Saved to your Downloads", res,
                         "a deliberate cancel must not push a file at him")

    def test_iphone_with_no_share_sheet_is_given_something_to_do(self):
        """The dead end: an iPhone rider who has connected, pulled and tapped
        Send is standing in Bluefy already, so "open this page in Bluefy" is
        the one instruction he has provably followed. He must get a link he can
        press instead — and the page must still refuse to call it delivered,
        which is what keeps step 4 shut."""
        self._switch_to_iphone()
        # State the environment assumption rather than quietly testing nothing:
        # if this browser ever grows a share sheet, the branch below is not the
        # one that ran.
        self.assertFalse(
            self.page.evaluate("() => !!(navigator.share && navigator.canShare)"),
            "this browser has a share sheet, so the iOS fallback never ran")

        puck = FakePuck()
        self._connect(puck)
        self._pull(puck)
        self.page.click("[data-testid=btn-send]")
        self._drive(puck, lambda: self.page.evaluate(
            "() => !!window.__sync.lastBundle()"), "the bundle to be built")

        st = self._state()
        self.assertFalse(st["delivered"],
                         "nothing left the phone, so nothing may be called delivered")
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden(),
                        "step 4 must stay shut until the ride has actually gone")
        result = self.page.locator("[data-testid=result]").inner_text()
        self.assertNotIn("Bluefy", result,
                         f"do not name the browser he is standing in: {result!r}")
        link = self.page.locator("[data-testid=result] a.save-link")
        self.assertEqual(link.count(), 1, f"no save link was offered: {result!r}")
        self.assertTrue(link.get_attribute("href").startswith("blob:"),
                        "the link must point at the bundle already in memory")
        self.assertEqual(link.get_attribute("download"), st["bundle"])

    def test_resend_after_clearing_says_the_puck_is_empty(self):
        """`cleared` is how ingest knows whether the puck still holds a copy.
        After step 4 the phone holds the ONLY copy, so a re-send has to rebuild
        rather than ship the cached zip that still says cleared:false — and
        step 2 must not be sitting there enabled, one tap from replacing that
        copy with a pull of an empty puck.

        ?allowclear=1, for the same reason as the happy path: the rider's own
        URL has no step 4 to reach."""
        puck = FakePuck()
        self._connect(puck, self.ALLOW_CLEAR)
        self._pull(puck)
        self._send(puck)
        self.assertFalse(json.loads(self._bundle()[1].read("manifest.json"))["cleared"])

        self.page.click("[data-testid=btn-clear]")
        self._drive(puck, lambda: self._state()["phase"] in ("cleared", "failed"),
                    "the puck to confirm it is empty")
        self.assertTrue(self._state()["cleared"])
        self.assertTrue(self.page.locator("[data-testid=btn-pull]").is_disabled(),
                        "step 2 must shut once the puck is empty — doPull drops "
                        "the in-memory bundle, which is now the only copy")

        self.page.click("[data-testid=btn-send]")
        self._drive(puck, lambda: self.page.evaluate(
            "() => !!window.__sync.lastBundle()"), "the bundle to be rebuilt")
        _name, z = self._bundle()
        man = json.loads(z.read("manifest.json"))
        self.assertTrue(man["cleared"],
                        "a bundle built after the erase must say so")
        self.assertIn("OK clear", z.read("device.log").decode())



class TestWebSyncAtRegionScale(_WebSyncCase):
    """The csv fallback at the size a REAL full trace region actually is.

    Everything measured before this went through the page at 455 KB — the real
    cable pull on 2026-09-09 (docs/serial-parity-2026-09-09.md). A full region
    is 15,917,153 bytes, ~35x that, and at the 64.9 KB/s that pull measured it
    is about four minutes of steady streaming. Nothing had ever put a body
    that size through the page's main-thread line handling, so its cost per
    line, its heap, its progress updates and its zip step were all unmeasured.
    No board is needed to find out: the mock transport feeds the same lines a
    puck would.

    The csv path, not traceraw, because that is the only path Nick's OG can
    take (src=5c80a436 predates `traceraw`, docs/STATUS.md).

    WHAT IS TIMED, AND BY WHOSE CLOCK. Read BENCH_JS's header first. The short
    version: the body is generated inside the browser and fed one line at a
    time to window.__mock.feed, `feed_cpu_ms` sums only the chunk loops, and
    `generate_only_ms` is the same generation with feed() removed, so
    `page_line_handling_ms` is the page's own work and nothing else. The wall
    figures are labelled as wall and include the yields and the sampling.
    """

    # ~8 KB of wire text per yield, which is the shape SerialTransport's read
    # loop has: read a CDC burst, push its lines, go back to the event loop
    # (web/sync/sync.js:326). Feeding all of it in one synchronous loop would
    # manufacture a freeze the real link never produces.
    CHUNK_ROWS = 512
    POLL_S = 0.25

    def _measure_csv_pull(self, total_bytes):
        rows, wide = plan_csv_body(total_bytes)
        puck = FakePuck(traceraw="unknown", trace_bytes=total_bytes)
        console = []
        self.page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
        # performance.memory is the obvious API for a heap peak;
        # Runtime.getHeapUsage is here because that one turned out not to be
        # a measurement at all in this browser. Both are read, and the
        # assertions at the bottom pin which of them is real.
        cdp = self.context.new_cdp_session(self.page)

        self._connect(puck)
        self.page.click("[data-testid=btn-pull]")
        self._answer_until(puck, "trace")

        cfg = {"answeredSoFar": self._answered, "rows": rows, "wide": wide,
               "chunkRows": self.CHUNK_ROWS, "logHz": LOG_HZ,
               "rafIdleMs": 300, "tailTimeoutMs": 120000,
               "replies": {c: puck.reply(c) for c in ("stats", "selftest")}}

        heap_start = cdp.send("Runtime.getHeapUsage")["usedSize"]
        self.page.evaluate(BENCH_JS, cfg)
        began = time.monotonic()
        outside, heap_peak = [], heap_start
        while True:
            time.sleep(self.POLL_S)
            # Sampled from OUTSIDE the page, which is the only observer that
            # can tell a moving bar from a frozen one: if the main thread were
            # blocked, this evaluate would not return either.
            snap = self.page.evaluate(
                "() => ({ done: window.__bench.done,"
                " text: document.getElementById('progress-text').textContent,"
                " bar: document.getElementById('bar-fill').style.width,"
                " hidden: document.getElementById('progress').hidden })")
            used = cdp.send("Runtime.getHeapUsage")["usedSize"]
            heap_peak = max(heap_peak, used)
            outside.append({"t_s": round(time.monotonic() - began, 2),
                            "progress_text": snap["text"], "bar": snap["bar"],
                            "hidden": snap["hidden"],
                            "heap_mb": round(used / 1e6, 1)})
            if snap["done"]:
                break
            self.assertLess(time.monotonic() - began, 900,
                            f"the in-page bench never finished. last={outside[-1]}")
        b = self.page.evaluate("() => window.__bench")
        self.assertIsNone(b["error"], f"the in-page bench failed: {b['error']}")
        self._answered = len(self._sent())
        heap_peak = max(heap_peak, cdp.send("Runtime.getHeapUsage")["usedSize"])

        # Step 3, timed by the page's own clock, then read back through the
        # existing helper so Python's zipfile has to accept what it produced.
        send = self.page.evaluate(SEND_JS, 180000)
        self.assertIsNotNone(send["ms"], "Send never reported delivered")
        heap_peak = max(heap_peak, cdp.send("Runtime.getHeapUsage")["usedSize"])
        name, z = self._bundle()
        trace_csv = z.read("trace.csv")
        manifest = json.loads(z.read("manifest.json"))
        device_log = z.read("device.log").decode()

        # The fixture's own cost, measured the same way with feed() removed,
        # and an independent recount of the body length.
        gen = self.page.evaluate(GENERATE_ONLY_JS, cfg)

        st = self._state()
        m = {
            "body_bytes": b["bodyBytes"],
            "rows": b["rows"],
            "wide_mag_rows": wide,
            "lines_fed": b["rows"] + 2,          # + the header + the BEGIN line
            "chunks": b["chunks"],
            "body_wall_ms": round(b["bodyWallMs"], 1),
            "feed_cpu_ms": round(b["feedCpuMs"], 1),
            "generate_only_ms": round(gen["ms"], 1),
            "page_line_handling_ms": round(b["feedCpuMs"] - gen["ms"], 1),
            "max_chunk_ms": round(b["maxChunkMs"], 2),
            "max_yield_ms": round(b["maxYieldMs"], 2),
            "tail_ms": None if b["tailMs"] is None else round(b["tailMs"], 1),
            "send_ms": round(send["ms"], 1),
            "bundle_bytes": send["size"],
            "bundle_name": send["name"],
            "trace_csv_in_zip": len(trace_csv),
            "heap_start_bytes": heap_start,
            "heap_peak_bytes": heap_peak,
            "perf_memory_present": b["perfMemPresent"],
            "perf_memory_distinct_values": sorted({v for _t, v in b["perfMem"]}),
            "raf_idle": b["rafIdle"],
            "raf_idle_ms": b["rafIdleMs"],
            "raf_during_body": b["rafBody"],
            "progress_in_page": b["progress"],
            "bar_in_page": b["bar"],
            "outside_samples": outside,
            "console": console,
            "pull_seconds_page": manifest["transfer"]["seconds"],
            "bytes_received_page": manifest["transfer"]["bytes_received"],
            "verified": st["verified"],
            "reasons": st["reasons"],
            "phase": b["phase"],
            "f22_band_applied": st["f22_band_applied"],
            "inactivity_ms": INACTIVITY_MS,
        }

        # ---- 1/3: the body is the size claimed, and it verified -------------
        self.assertEqual(b["bodyBytes"], total_bytes,
                         "the fed body is not the size this test claims to measure")
        self.assertEqual(gen["bytes"], total_bytes,
                         "plan_csv_body and the generator disagree about the body length")
        self.assertEqual(b["phase"], "pulled", f"the pull did not finish: {st}")
        self.assertEqual(st["trace_format"], "csv")
        self.assertEqual(st["reasons"], [],
                         f"verifyPull objected at {total_bytes:,} bytes: {st['reasons']}")
        self.assertTrue(st["verified"])
        self.assertEqual(st["trace_bytes_got"], total_bytes,
                         "the page counted a different number of bytes than were fed")
        self.assertFalse(st["f22_band_applied"],
                         "this is the byte-EXACT case; F-22's band must not be what passed it")

        # ---- 4: the progress UI moved while the data flowed ----------------
        # Sampled from outside the page, more than once, and required to show
        # at least two different readings AND a percentage that is neither 0
        # nor 100 — a bar that only ever showed its endpoints would satisfy a
        # weaker assertion while looking frozen to the rider.
        texts = [s["progress_text"] for s in outside]
        self.assertGreater(len(set(texts)), 1,
                           f"the progress text never changed during the pull: {texts}")
        pcts = sorted({int(g.group(1)) for t in texts
                       for g in [re.search(r"(\d+)\s*%", t)] if g})
        self.assertTrue([p for p in pcts if 0 < p < 100],
                        f"no partial percentage was ever on screen: {texts}")
        bars = sorted({s["bar"] for s in outside if s["bar"]})
        self.assertGreater(len(bars), 1, f"the bar never moved: {bars}")
        m["percentages_seen"] = pcts
        m["bar_widths_seen"] = bars

        # ---- 2: which heap number is real ------------------------------------
        # performance.memory.usedJSHeapSize is present here and NEVER MOVES: it
        # reported 10,000,000 flat while Runtime.getHeapUsage went 2.8 MB ->
        # 100+ MB across the same run (measured 2026-09-10, headless Chromium
        # 151). Chrome quantises it hard outside a cross-origin-isolated page.
        # A peak taken from it would be a fabricated number, so the heap figure
        # this test reports comes from CDP — and BOTH facts are asserted, so
        # neither can quietly stop being true.
        m["perf_memory_moves"] = len(m["perf_memory_distinct_values"]) > 1
        self.assertFalse(
            m["perf_memory_moves"],
            "performance.memory.usedJSHeapSize now moves in this browser "
            f"({m['perf_memory_distinct_values']}) — it did not on 2026-09-10. "
            "Switch the heap figure to it and delete this assertion.")
        self.assertGreater(
            heap_peak, 4 * heap_start,
            f"Runtime.getHeapUsage never moved either ({heap_start} -> "
            f"{heap_peak}) — then no heap here was measured at all")

        # ---- 4, the other half: the page kept painting ------------------------
        # requestAnimationFrame is evidence of "not frozen" ONLY if this
        # browser hands an idle page frames at all, which is what rafIdle is
        # for. Compare RATES: the body runs far longer than the idle window.
        idle_fps = b["rafIdle"] / (b["rafIdleMs"] / 1000.0)
        body_fps = b["rafBody"] / (b["bodyWallMs"] / 1000.0)
        m["raf_idle_fps"] = round(idle_fps, 1)
        m["raf_body_fps"] = round(body_fps, 1)
        self.assertGreater(idle_fps, 10,
                           "this browser gives an idle page no animation frames, "
                           "so rAF says nothing here about a freeze")
        self.assertGreater(body_fps, idle_fps * 0.5,
                           f"the page fell to {body_fps:.0f} fps while the body "
                           f"streamed, against {idle_fps:.0f} idle — a freeze")

        # ---- 5: the 30 s inactivity timer -----------------------------------
        # It resets on EVERY line (armCaptureTimer, web/sync/sync.js:661), so
        # it can only trip on a gap between lines. The longest gap this run
        # produced is the longest chunk plus the longest yield; assert the
        # headroom rather than just "it did not trip", which a lucky run also
        # satisfies.
        worst_gap_ms = b["maxChunkMs"] + b["maxYieldMs"]
        m["worst_line_gap_ms"] = round(worst_gap_ms, 2)
        self.assertLess(worst_gap_ms, INACTIVITY_MS / 10,
                        f"a gap between lines came within 10x of INACTIVITY_MS "
                        f"({INACTIVITY_MS} ms): {worst_gap_ms:.1f} ms")

        # ---- 6: the bundle ---------------------------------------------------
        self.assertIsNone(z.testzip(), "the page wrote a zip with a bad CRC")
        self.assertEqual(len(trace_csv), total_bytes,
                         "trace.csv in the zip is not the body that was fed")
        self.assertTrue(trace_csv.startswith(b"t,mag\n0.000,1.000\n"),
                        f"trace.csv starts wrong: {trace_csv[:40]!r}")
        self.assertTrue(trace_csv.endswith(b"\n"))
        self.assertEqual(manifest["trace_format"], "csv")
        self.assertEqual(manifest["trace_bytes_got"], total_bytes)
        self.assertEqual(manifest["trace_bytes_device"], total_bytes)
        self.assertTrue(manifest["verified"])
        self.assertFalse(manifest["f22_band_applied"])
        # device.log stays readable at this scale: the body must not be in it.
        self.assertIn("FILE trace.csv BEGIN", device_log)
        self.assertNotIn("0.000,1.000", device_log,
                         "device.log carried the trace body (CONTRACT.md §2)")
        m["device_log_bytes"] = len(device_log)

        # Item 6's comparison, stated with its caveat rather than as a
        # prediction. The real cable pull on 2026-09-09 zipped a 466,154-byte
        # trace.csv into a bundle the run recorded as "91 KB"
        # (docs/serial-parity-2026-09-09.md) — ~20% of the body. THIS fixture's
        # rows cycle through 20 mag values and deflate far better than a real
        # puck's noise, so the ratio below is a FLOOR for what Nick's full
        # region will produce, never an estimate of it.
        m["bundle_over_body_pct"] = round(100.0 * send["size"] / total_bytes, 2)
        m["real_09_09_body_and_bundle"] = (466154, "91 KB, as printed")

        # ---- 7: nothing on the console, nothing blocked ----------------------
        errors = [c for c in console if c.startswith("error")]
        self.assertEqual(errors, [], f"the page logged console errors: {errors}")
        self.assertEqual(self._blocked, [], "the page reached outside localhost")
        return m

    @staticmethod
    def _report(label, m):
        print(f"\n--- {label} ---")
        for k in ("body_bytes", "rows", "lines_fed", "chunks", "body_wall_ms",
                  "feed_cpu_ms", "generate_only_ms", "page_line_handling_ms",
                  "max_chunk_ms", "max_yield_ms", "worst_line_gap_ms",
                  "inactivity_ms", "tail_ms", "send_ms", "bundle_bytes",
                  "trace_csv_in_zip", "device_log_bytes", "heap_start_bytes",
                  "heap_peak_bytes", "perf_memory_present",
                  "perf_memory_distinct_values", "perf_memory_moves",
                  "raf_idle", "raf_idle_ms", "raf_during_body",
                  "raf_idle_fps", "raf_body_fps",
                  "pull_seconds_page", "bytes_received_page",
                  "verified", "reasons", "f22_band_applied",
                  "bundle_over_body_pct", "real_09_09_body_and_bundle",
                  "percentages_seen", "bar_widths_seen", "console"):
            print(f"  {k} = {m.get(k)!r}")
        print("  progress text, sampled from outside the page:")
        for s in m["outside_samples"]:
            print(f"    t={s['t_s']:>7.2f}s  bar={s['bar']:>5}  "
                  f"heap={s['heap_mb']:>6.1f} MB  {s['progress_text']!r}")

    def test_csv_pull_at_three_megabytes(self):
        """The default run: 3 MB of trace.csv, ~205k lines, every check the
        full-region test makes. Sized so the 500 ms progress timer fires
        several times — at 1 MB the pull can finish before its first tick, and
        then "the bar moved" would be asserting nothing."""
        m = self._measure_csv_pull(DEFAULT_LARGE_CSV_BYTES)
        self._report("csv pull, 3,000,000 B", m)

    @unittest.skipUnless(RUN_FULL_REGION, FULL_REGION_SKIP)
    def test_csv_pull_at_the_ogs_full_region(self):
        """15,917,153 bytes — the OG's own full-region figure, 2026-09-07."""
        m = self._measure_csv_pull(FULL_REGION_CSV_BYTES)
        self._report(f"csv pull, {FULL_REGION_CSV_BYTES:,} B (OG full region)", m)




class TestWebSyncCable(_WebSyncCase):
    """The USB-cable link (Web Serial) added 2026-09-07 for the rider's
    Intel MacBook. Headless Chromium has navigator.serial, so the button is
    genuinely offered here; what it cannot do is show a real port picker, so
    requestPort is stubbed — to reject the way a cancelled picker does, or to
    hand back the scripted port in FAKE_SERIAL_JS above.

    Against that scripted port SerialTransport.open/_readLoop/sendLine do run
    (test_link_lost_over_the_cable_says_to_reload). What no test here can
    exercise is a REAL port: enumeration, the 400 ms drain, a cable half out.
    That still needs a real Chrome on a real cable, and none is on this
    bench — say so rather than pretend."""

    def _open_plain(self, init_script=None):
        if init_script:
            self.page.add_init_script(init_script)
        self.page.goto(f"http://127.0.0.1:{self._port}/sync/",
                       wait_until="domcontentloaded")
        self.page.wait_for_function(
            "() => document.getElementById('page-version').textContent.length > 0",
            timeout=15000)

    def _open_mock_usb(self):
        self.page.goto(f"http://127.0.0.1:{self._port}/sync/#mock-usb",
                       wait_until="domcontentloaded")
        self.page.wait_for_function(
            "() => window.__mock && window.__sync && typeof window.__sync.state === 'function'",
            timeout=15000)

    def test_cable_button_is_offered_where_web_serial_exists(self):
        self._open_plain()
        has_serial = self.page.evaluate("() => !!navigator.serial")
        usb = self.page.locator("[data-testid=btn-connect-usb]")
        self.assertEqual(usb.is_visible(), has_serial,
                         "the cable button must appear exactly when the browser can use a cable")

    def test_cable_button_hidden_without_web_serial(self):
        self._open_plain("Object.defineProperty(Navigator.prototype, 'serial', "
                         "{ get: () => undefined, configurable: true });")
        self.assertTrue(self.page.locator("[data-testid=btn-connect-usb]").is_hidden())

    def _visible_connect_buttons(self):
        return [b for b in ("btn-connect-usb", "btn-connect")
                if self.page.locator(f"#{b}").is_visible()]

    def test_only_one_way_in_is_ever_offered(self):
        """ONE path per device — the change the owner asked for on 2026-09-09.

        Chrome on a Mac reports BOTH navigator.serial and navigator.bluetooth,
        and the page used to show every link the browser could make: two
        competing black Connect buttons, a hint each, and an unmeasured
        Bluetooth time estimate, with nothing on the page saying which to
        press. Nick has one configuration — that Mac, Chrome, the USB cable —
        so where there is a serial port the cable is the ONLY thing offered.
        Bluetooth (and its 20-30 minute estimate) appears only where there is
        no serial port at all: a phone.

        Both halves are asserted here, and neither depends on what this
        particular browser happens to support. The first version of this test
        DID depend on it — it asserted the browser had Web Bluetooth, which is
        true of Chrome on the owner's Mac and false of CI's headless Chromium
        on Linux, so it failed in CI for a reason that had nothing to do with
        the page (run 34421541968, 2026-09-09). Refusing to pass vacuously was
        right; depending on the environment to supply the shape was not. The
        page branches on `!!navigator.bluetooth`, so a stub exercises exactly
        the branch a Mac takes, on any machine."""
        self._open_plain("if (!navigator.bluetooth) Object.defineProperty("
                         "Navigator.prototype, 'bluetooth', "
                         "{ get: () => ({ requestDevice: () => {} }), "
                         "configurable: true });")
        self.assertTrue(self.page.evaluate("() => !!navigator.serial"),
                        "no Web Serial in this browser — the computer case "
                        "below was never exercised")
        self.assertTrue(self.page.evaluate("() => !!navigator.bluetooth"),
                        "the two-transport shape was not established, so "
                        "hiding the Bluetooth button would prove nothing")

        self.assertEqual(self._visible_connect_buttons(), ["btn-connect-usb"],
                         "with a serial port the cable button must be the only "
                         "Connect button on screen")
        self.assertEqual(
            self.page.locator("[data-testid=btn-connect-usb]").inner_text().strip(),
            "Connect")
        self.assertTrue(self.page.locator("#ble-hint").is_hidden(),
                        "the Bluetooth hint must go with its button")
        self.assertTrue(self.page.locator("#ble-time-hint").is_hidden(),
                        "the 20-30 minute figure is a BLUETOOTH estimate and "
                        "must not appear on the cable path")
        self.assertFalse(self.page.locator("#usb-hint").is_hidden())
        # The rows cut in the same pass: a byte count and a build hash.
        self.assertEqual(self.page.locator("#waiting").count(), 0)
        self.assertEqual(self.page.locator("#fw").count(), 0)

        # The phone: no serial port, so Bluetooth is the one way in. doConnect
        # stays reachable — this change is visibility, not wiring.
        self._open_plain("Object.defineProperty(Navigator.prototype, 'serial', "
                         "{ get: () => undefined, configurable: true });")
        # The second goto has no fragment, so it is a real navigation and the
        # init script above really ran. Checked, not assumed: if it did not,
        # every assertion below would be re-reading the FIRST page and passing
        # for the wrong reason (CLAUDE.md rule 3 — the shape _padding_case
        # already got caught by).
        self.assertFalse(self.page.evaluate("() => !!navigator.serial"),
                         "the page did not reload without Web Serial — the "
                         "phone case below was never exercised")
        self.assertEqual(self._visible_connect_buttons(), ["btn-connect"],
                         "without a serial port Bluetooth must be offered")
        self.assertFalse(self.page.locator("#ble-hint").is_hidden())
        self.assertFalse(self.page.locator("#ble-time-hint").is_hidden(),
                         "the Bluetooth estimate belongs on the Bluetooth path")
        self.assertTrue(self.page.locator("#usb-hint").is_hidden())

    def test_no_link_at_all_says_which_browser_to_use(self):
        self._open_plain("Object.defineProperty(Navigator.prototype, 'serial', "
                         "{ get: () => undefined, configurable: true });"
                         "Object.defineProperty(Navigator.prototype, 'bluetooth', "
                         "{ get: () => undefined, configurable: true });")
        status = self._status()
        # The Mac leads: it is the rider's ONE configuration and the remedy he
        # can act on in ten seconds. The Android and iPhone sentences stay
        # behind it — this page is also the phone fallback, and Bluefy is the
        # only iPhone browser that reaches the puck at all.
        self.assertIn("On a Mac, open this page in Chrome and use the cable",
                      status, f"the Mac case must lead: {status!r}")
        self.assertLess(status.index("Mac"), status.index("Android"),
                        f"Android must not come before the Mac: {status!r}")
        self.assertIn("Chrome", status)
        self.assertIn("cable", status)
        self.assertIn("Bluefy", status)

    def test_cancelled_port_picker_is_a_plain_sentence(self):
        self._open_plain(
            "Object.defineProperty(Navigator.prototype, 'serial', { configurable: true, "
            "get: () => ({ requestPort: () => Promise.reject("
            "new DOMException('No port selected by the user.', 'NotFoundError')) }) });")
        self.page.click("[data-testid=btn-connect-usb]")
        self.page.wait_for_function(
            "() => document.querySelector('[data-testid=status]').textContent.includes('No puck picked')",
            timeout=8000)
        status = self._status()
        self.assertIn("cable", status)
        # The string Chrome actually shows, measured off the board with ioreg
        # 2026-09-09: USB Product Name "XIAO nRF52840 Sense". It is NOT
        # "JumpHeight" — that is the BLE advertised name and cannot appear in
        # a USB port picker, so telling him to look for it sent him hunting
        # for something that does not exist.
        self.assertIn("XIAO nRF52840 Sense", status)
        self.assertNotIn("JumpHeight", status,
                         "the BLE name cannot appear in a USB port picker")
        # It must name the button by the label the button actually carries.
        # That label became "Connect" on 2026-09-09 when the cable became the
        # only path on a computer; this sentence said 'tap "Connect with the
        # cable"', which now names nothing on screen (CLAUDE.md §4, the mirror
        # case).
        self.assertIn('tap "Connect" and choose', status,
                      f"the retry sentence names a button that is not there: {status!r}")
        # The connect button comes back: he can try again.
        self.assertFalse(self.page.locator("[data-testid=btn-connect-usb]").is_disabled())

    def test_a_cable_that_will_not_open_says_to_reload(self):
        """The port is there and it will not open — another program has it,
        or the CDC device is wedged. He is told to unplug it, and then the
        thing nobody thinks of on their own: reload the page."""
        self._open_plain(
            "Object.defineProperty(Navigator.prototype, 'serial', { configurable: true, "
            "get: () => ({ requestPort: () => Promise.resolve({ open: () => "
            "Promise.reject(new DOMException('Failed to open serial port.', "
            "'NetworkError')) }) }) });")
        self.page.click("[data-testid=btn-connect-usb]")
        self._wait_for(lambda: "plug it back in" in self._status(),
                       "the failed open to be reported")
        status = self._status()
        self.assertIn("reload this page", status,
                      f"the last-resort remedy must be offered: {status!r}")

    def test_link_lost_over_the_cable_says_to_reload(self):
        """A connected session whose port goes away mid-session — the cable
        pulled, the puck rebooted. onLinkLost() is the only place that
        sentence appears for a live link, and it is reachable only with a
        transport that really closes, which is what FAKE_SERIAL_JS is for.

        The drop is taken while the page is IDLE on purpose: a drop during an
        in-flight command is reported by that command's own failure path
        instead, which is a different sentence."""
        replies = {"info": list(INFO_LINES),
                   "stats": [stats_line(3, CSV_BYTES), "OK stats"]}
        self._open_plain("window.__replies = " + json.dumps(replies) + ";"
                         + FAKE_SERIAL_JS)
        self.page.click("[data-testid=btn-connect-usb]")
        self._wait_for(lambda: "Now tap" in self._status(),
                       "the cable session to finish reading the puck")
        self.assertTrue(self.page.evaluate("() => window.__sync.state().connected"))

        self.page.evaluate("() => window.__fakePort.drop()")
        self._wait_for(lambda: "dropped" in self._status(),
                       "the page to notice the link went away")

        status = self._status()
        self.assertIn("cable", status, "over USB he must not be told to move closer")
        self.assertNotIn("out of range", status)
        self.assertIn('tap "Connect" and start again', status,
                      f"the retry sentence names a button that is not there: {status!r}")
        self.assertIn("reload this page", status,
                      f"the last-resort remedy must be offered: {status!r}")
        self.assertFalse(self.page.evaluate("() => window.__sync.state().connected"))

    def test_usb_session_records_its_transport_and_gives_cable_advice(self):
        """A cable-shaped mock session: the manifest names the transport, and a
        short copy tells him to check the cable, not to move closer or end a
        watch activity — neither of which can help over USB."""
        puck = FakePuck(traceraw="short")
        self._open_mock_usb()
        self._drive(puck, lambda: self._state()["phase"] == "connected",
                    "the page to finish reading the puck on connect")

        # The instruction while it copies has to name the thing he is holding.
        # doPull's line said "Keep the phone next to the puck" on EVERY
        # transport; over the cable the puck is plugged into a Mac and there is
        # no phone in the loop at all. Read here rather than in _pull() because
        # setStatus runs synchronously in doPull before the first command.
        self.page.click("[data-testid=btn-pull]")
        copying = self._status()
        self.assertIn("Leave the puck plugged in", copying,
                      f"the cable copy must not send him looking for a phone: {copying!r}")
        self.assertNotIn("phone", copying)
        self._drive(puck, lambda: self._state()["phase"] in ("pulled", "failed"),
                    "the pull to finish")
        st = self._state()
        self.assertFalse(st["verified"])
        status = self._status()
        self.assertIn("cable", status)
        self.assertNotIn("watch", status)
        self.assertNotIn("closer", status)
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden())
        # Send is still offered (an unverified bundle is the one Josh most
        # wants to see); the manifest carries the transport.
        self.page.click("[data-testid=btn-send]")
        self._drive(puck, lambda: self.page.evaluate("() => !!window.__sync.lastBundle()"),
                    "the bundle to be built")
        _name, zf = self._bundle()
        manifest = json.loads(zf.read("manifest.json"))
        self.assertEqual(manifest["transfer"]["transport"], "usb")
        self.assertFalse(manifest["verified"])


# Last in the file, deliberately. This sat at line 1899 with TestWebSyncCable
# defined at :1903, so `python3 tools/tests/test_web_sync.py` ran unittest.main()
# before that class existed: 8 cable tests never ran and the file reported OK
# (24 tests where pytest collects 32). CLAUDE.md rule 3 — a reading that did
# not happen must never look like a pass. Found 2026-09-10.
if __name__ == "__main__":
    unittest.main()
