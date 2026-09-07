"""Integration test: the rider sync page (web/sync/) driven end-to-end.

The web equivalent of test_cli.py for the phone path. It loads the real
web/sync/index.html + sync.js in a real (headless) Chromium and plays the puck
from Python over the page's own test seam (CONTRACT §3):

    window.__mock  = { feed(line), sent: [] }
    window.__sync  = { state(), lastBundle() }

feed(line) injects a line as if the puck had sent it; sent[] is the exact list
of command strings the page wrote. So the loop below IS the device: it polls
sent[], answers each command with canned protocol lines, and asserts on what
the page does with them. `traceraw`'s reply is built here in Python from
sim/trace_codec.encode_region + zlib.crc32, to exactly the wire framing in
CONTRACT §1 — so if the page and the contract ever disagree about 76-column
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
external requests (CONTRACT §3). Aborting anything that isn't localhost turns
a future stray CDN reference into a test failure instead of a slow page.
"""

from __future__ import annotations

import base64
import io
import json
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
# not the exotic one. CONTRACT §1 puts padding "only at the very end", which is
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
# the csv path has (CONTRACT §2 verified (c)).
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
# fallback is allowed to trigger on, and nothing else (CONTRACT §1).
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
    """The `traceraw` reply, byte for byte per CONTRACT §1.

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


class FakePuck:
    """Plays the puck: one canned reply per command, in the order asked.

    reply() returns None for a command it was never taught, and the driver
    turns that into a loud failure — an unexpected command must never look
    like a puck that simply said nothing.
    """

    def __init__(self, *, traceraw="ok", stored_jumps=3, jumps_rows=None,
                 trace_bytes=51234, stats_after_clear=(0, 0), raw=None,
                 jumps_warning=False, fs_down=False, csv_rows=None):
        self.traceraw = traceraw
        self.stored_jumps = stored_jumps
        self.jumps_rows = JUMPS_ROWS if jumps_rows is None else jumps_rows
        self.trace_bytes = trace_bytes
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
            if self.fs_down:
                # Both lines, from the same `if (!fs_ok)` in main.cpp.
                return ["# storage NOT MOUNTED — the counts below are unknown, not zero",
                        stats_line(0, 0) + " fs=down", "OK stats"]
            if self.cleared:
                j, tb = self.stats_after_clear
                return [stats_line(j, tb), "OK stats"]
            return [stats_line(self.stored_jumps, self.trace_bytes), "OK stats"]
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
    def _open(self):
        self.page.goto(f"http://127.0.0.1:{self._port}/sync/#mock",
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

    def _connect(self, puck):
        self._open()
        self._drive(puck, lambda: self._state()["phase"] == "connected",
                    "the page to finish reading the puck on connect")

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
        bundle Josh receives is checked file by file against the fixture."""
        puck = FakePuck()
        self._connect(puck)

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
                    "trace_bytes_device", "trace_raw_bytes", "trace_crc32",
                    "stored_jumps_device", "jump_rows", "verified", "cleared",
                    "transfer", "user_agent"):
            self.assertIn(key, man, f"manifest.json is missing CONTRACT §2 key {key!r}")
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
                         "device.log must not carry the base64 body (CONTRACT §2)")

        # Static files only, zero external requests (CONTRACT §3): the route
        # handler turned nothing away.
        self.assertEqual(self._blocked, [],
                         "the page reached outside localhost — it must be "
                         "self-contained, with no CDN and no framework")

        # Step 4 only now, and it must confirm from the puck's own stats.
        self.assertFalse(self.page.locator("[data-testid=btn-clear]").is_hidden(),
                         "Clear must be offered once the ride is verified and delivered")
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
        self.assertTrue(man["verified"])

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
        wire that cannot be undone."""
        puck = FakePuck()
        self._connect(puck)
        self.assertNotIn("clear", self._sent())
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden())

        self._pull(puck)
        self.assertTrue(self._state()["verified"])
        # Verified but NOT yet delivered: still no step 4, still no clear.
        self.assertFalse(self._state()["delivered"])
        self.assertTrue(self.page.locator("[data-testid=btn-clear]").is_hidden(),
                        "verified alone must not unlock Clear — delivered too "
                        "(CONTRACT §3 step 4)")
        self.assertNotIn("clear", self._sent())

        self._send(puck)
        # Delivered: the button appears, but nothing has gone to the puck until
        # the rider actually taps it.
        self.assertFalse(self.page.locator("[data-testid=btn-clear]").is_hidden())
        self.assertNotIn("clear", self._sent(),
                         "the page must not send `clear` on its own")

    def test_incomplete_warning_inside_the_jumps_frame_is_heard(self):
        """The puck's own complaint arrives INSIDE the FILE frame
        (firmware/src/main.cpp printFileFramed: the warning is printed after
        the body and before "FILE jumps.csv END"). jumps.csv has no crc and no
        byte count, so CONTRACT §2 check (a) is the only thing standing between
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
                      "FILE frame (CONTRACT §2)")
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

        # Step 1 must not print zeros it did not read.
        self.assertIn("unknown", self.page.locator("#stored-jumps").inner_text().lower())
        self.assertIn("unknown", self.page.locator("#waiting").inner_text().lower())
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
        branch that runs on the only browser an iPhone rider has (CONTRACT §3:
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
        copy with a pull of an empty puck."""
        puck = FakePuck()
        self._connect(puck)
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


if __name__ == "__main__":
    unittest.main()


class TestWebSyncCable(_WebSyncCase):
    """The USB-cable link (Web Serial) added 2026-09-07 for the rider's
    Intel MacBook. Headless Chromium has navigator.serial, so the button is
    genuinely offered here; what it cannot do is show a real port picker, so
    requestPort is stubbed to reject the way a cancelled picker does. The
    transport itself (SerialTransport.open/_readLoop) is exercised by nobody
    but a real Chrome on a real cable — say so rather than pretend."""

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

    def test_no_link_at_all_says_which_browser_to_use(self):
        self._open_plain("Object.defineProperty(Navigator.prototype, 'serial', "
                         "{ get: () => undefined, configurable: true });"
                         "Object.defineProperty(Navigator.prototype, 'bluetooth', "
                         "{ get: () => undefined, configurable: true });")
        status = self._status()
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
        self.assertIn("usbmodem", status)
        # Both connect buttons come back: he can try again.
        self.assertFalse(self.page.locator("[data-testid=btn-connect-usb]").is_disabled())

    def test_usb_session_records_its_transport_and_gives_cable_advice(self):
        """A cable-shaped mock session: the manifest names the transport, and a
        short copy tells him to check the cable, not to move closer or end a
        watch activity — neither of which can help over USB."""
        puck = FakePuck(traceraw="short")
        self._open_mock_usb()
        self._drive(puck, lambda: self._state()["phase"] == "connected",
                    "the page to finish reading the puck on connect")
        self._pull(puck)
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
