"""Integration test: the browser app (web/) driven end-to-end with Playwright.

This is the web equivalent of test_cli.py — it exercises the real web/index.html
+ app.js in a real (headless) Chromium, talking the real newline protocol, the
same one the firmware and the USB CLI speak. It drives the app through its
built-in test seam: loading `#mock` installs a MockTransport and exposes a tiny,
stable hook —

    window.__mock = { feed(line), sent: [] }

feed(line) injects a line as if the device had sent it; sent[] is the exact
array the app pushes outgoing commands to. So we play "the device" from Python
and assert on what the DOM shows.

WHY it must skip cleanly, never fail, when the tooling is absent: this test runs
inside `./tools/jump simtest`, which has to stay green on a plain laptop with no
Playwright and no browser installed. So a missing `playwright` import or a
browser that won't launch raises unittest.SkipTest, not an error.

WHY the two-step browser launch: this environment ships a Chromium whose build
number doesn't match the one Playwright's default lookup expects, so the plain
launch() misses it; we retry pinned to /opt/pw-browsers/chromium. Either failure
just skips.

WHY we block non-local requests: the page pulls an external browser-flasher
module from a CDN. Because module scripts run in document order, letting that
fetch happen makes app.js wait many seconds for it (and ties the test to the
network). Aborting every non-localhost request makes the app initialise
instantly and keeps the whole test hermetic and well under ~30s.

Selector strategy: the web app is built in parallel by another agent to a shared
contract that promises data-testid hooks ('connect', 'live-height', 'live-best',
'live-count', 'btn-selftest', 'btn-dump', 'session-row', plus the newer
'sync-banner', 'btn-sync', 'session-chart', 'alltime-best',
'btn-clear-after-sync', 'theme-toggle', 'unit-toggle'). We prefer those, but
fall back to ids / classes / roles / visible text so the test is resilient to
exactly how the app labels things, and fails with a loud, specific message
(naming everything it tried) rather than a cryptic timeout if a hook is missing.
"""

from __future__ import annotations

import re
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
WEB_DIR = REPO / "web"

# This environment's Chromium (build number != what pip's playwright expects).
CHROMIUM_FALLBACK = "/opt/pw-browsers/chromium"

# Import Playwright lazily-at-module-load but guarded: on a machine without it,
# the module must still import so unittest can collect and *skip* the class,
# rather than erroring the whole simtest run.
try:
    from playwright.sync_api import expect, sync_playwright

    _PLAYWRIGHT_IMPORT_ERROR = None
except Exception as _e:  # ImportError, or a half-installed package
    sync_playwright = None
    expect = None
    _PLAYWRIGHT_IMPORT_ERROR = _e


def _serve(directory: Path):
    """Serve `directory` on an ephemeral localhost port in a background thread."""
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, httpd.server_address[1]


def _launch_chromium(pw):
    """Launch headless Chromium, skipping (not failing) if none can start.

    Try Playwright's default resolution first, then this environment's pinned
    path. --no-sandbox because CI/containers commonly run as root.
    """
    errors = []
    for kwargs in ({}, {"executable_path": CHROMIUM_FALLBACK}):
        try:
            return pw.chromium.launch(headless=True, args=["--no-sandbox"], **kwargs)
        except Exception as e:  # noqa: BLE001 - any launch failure => skip
            errors.append(f"{kwargs or 'default'}: {e}")
    raise unittest.SkipTest("no Chromium available to Playwright — " + " | ".join(errors))


def _resilient(page, candidates, what):
    """Return the first matching locator, trying each candidate in order.

    `candidates` is a list of (kind, value):
        ('testid', 'live-height')          -> [data-testid="live-height"]
        ('css',    '#live-height-m')       -> raw CSS
        ('role',   ('button', 'download')) -> get_by_role(name=/download/i)
        ('text',   '^Sessions$')           -> get_by_text(/.../i)
    Raises a specific AssertionError naming everything tried if none match, so a
    renamed/absent contract hook fails helpfully instead of as a bare timeout.
    """
    tried = []
    for kind, value in candidates:
        tried.append(f"{kind}:{value}")
        try:
            if kind == "testid":
                loc = page.locator(f'[data-testid="{value}"]')
            elif kind == "css":
                loc = page.locator(value)
            elif kind == "role":
                role, name = value
                loc = page.get_by_role(role, name=re.compile(name, re.I))
            elif kind == "text":
                loc = page.get_by_text(re.compile(value, re.I))
            else:
                continue
            if loc.count() > 0:
                return loc.first
        except Exception:  # noqa: BLE001 - a bad selector just moves to the next
            continue
    raise AssertionError(
        f"Couldn't find {what} in the web app. Tried, in order: {', '.join(tried)}. "
        "The app is expected to expose the contracted data-testid (or a "
        "compatible id/class/role).")


@unittest.skipIf(sync_playwright is None,
                 f"playwright not importable: {_PLAYWRIGHT_IMPORT_ERROR}")
class TestWebApp(unittest.TestCase):
    # ---- browser + server: one per class, cleaned up even on a partial start ----
    @classmethod
    def setUpClass(cls):
        cls._pw = None
        cls._browser = None
        cls._httpd = None
        try:
            cls._pw = sync_playwright().start()
            cls._browser = _launch_chromium(cls._pw)  # may raise SkipTest
            cls._httpd, cls._port = _serve(WEB_DIR)
        except BaseException:
            cls._shutdown_class()  # don't leak a started Playwright on skip/error
            raise

    @classmethod
    def _shutdown_class(cls):
        if getattr(cls, "_httpd", None) is not None:
            cls._httpd.shutdown()
            cls._httpd.server_close()  # close the listening socket too
            cls._httpd = None
        if getattr(cls, "_browser", None) is not None:
            try:
                cls._browser.close()
            except Exception:
                pass
            cls._browser = None
        if getattr(cls, "_pw", None) is not None:
            try:
                cls._pw.stop()
            except Exception:
                pass
            cls._pw = None

    @classmethod
    def tearDownClass(cls):
        cls._shutdown_class()

    # ---- a fresh, network-isolated page per test (fresh localStorage too) ----
    def setUp(self):
        self.context = self._browser.new_context()
        self.context.route("**/*", self._block_external)
        self.page = self.context.new_page()
        self.page.set_default_timeout(8000)
        self._page_errors = []
        self.page.on("pageerror", lambda e: self._page_errors.append(str(e)))

    def tearDown(self):
        # Surface any uncaught page errors — they explain otherwise-mysterious
        # failures (e.g. the app threw before wiring up __mock).
        if self._page_errors:
            print("web page errors:\n  " + "\n  ".join(self._page_errors))
        try:
            self.context.close()
        except Exception:
            pass

    @staticmethod
    def _block_external(route):
        url = route.request.url
        if url.startswith(("http://127.0.0.1", "http://localhost", "data:", "blob:")):
            route.continue_()
        else:
            route.abort()

    # ------------------------------------------------------------- helpers ----
    def _open(self):
        """Load the app in mock mode and wait for the __mock hook to appear."""
        self.page.goto(f"http://127.0.0.1:{self._port}/#mock",
                       wait_until="domcontentloaded")
        self.page.wait_for_function(
            "() => window.__mock && typeof window.__mock.feed === 'function'"
            " && Array.isArray(window.__mock.sent)",
            timeout=15000)

    def _feed(self, *lines):
        """Inject one or more protocol lines as if the device had sent them."""
        self.page.evaluate("(ls) => ls.forEach((l) => window.__mock.feed(l))",
                           list(lines))

    def _sent(self):
        return self.page.evaluate("() => window.__mock.sent")

    def _html_attr(self, name):
        """Read an attribute off <html> (e.g. data-theme, data-unit) — that's
        where the theme/unit choice actually lives, per app.js."""
        return self.page.evaluate(
            "(n) => document.documentElement.getAttribute(n)", name)

    def _local_storage(self, key):
        return self.page.evaluate("(k) => localStorage.getItem(k)", key)

    # -------------------------------------------------------------- tests ----
    def test_live_jumps_update_the_dom(self):
        """A STATE line + two JUMP lines drive the live height/best/count DOM."""
        self._open()

        # In #mock the app auto-connects to the demo transport; confirm the UI
        # reflects "connected" (not still "Disconnected").
        status = _resilient(self.page, [
            ("testid", "connect"), ("css", "#conn-status"), ("css", ".pill")],
            "connection-status indicator")
        self.assertNotIn("Disconnected", status.text_content() or "",
                         "in #mock the app should auto-connect to the demo transport")

        self._feed("STATE recording")
        state = _resilient(self.page, [
            ("testid", "live-state"), ("css", "#live-state"), ("css", ".badge")],
            "recording-state badge")
        expect(state).to_contain_text("recording")

        # First jump: ~0.44 m. It becomes the latest height and the session best,
        # and the count reads 1.
        self._feed("JUMP n=1 airtime_raw_s=0.615 airtime_s=0.600 "
                   "height_m=0.441 height_ft=1.4 best_m=0.441")
        height = _resilient(self.page, [
            ("testid", "live-height"), ("css", "#live-height-m"), ("css", ".live-height")],
            "live height readout")
        best = _resilient(self.page, [
            ("testid", "live-best"), ("css", "#live-best-m")], "session-best readout")
        count = _resilient(self.page, [
            ("testid", "live-count"), ("css", "#live-count")], "jump-count readout")
        expect(height).to_contain_text("0.44")
        expect(best).to_contain_text("0.44")
        expect(count).to_contain_text("1")

        # Second, bigger jump: ~1.23 m. Latest height + best move up; count is 2.
        self._feed("JUMP n=2 airtime_raw_s=1.010 airtime_s=1.000 "
                   "height_m=1.226 height_ft=4.0 best_m=1.226")
        expect(height).to_contain_text("1.23")
        expect(best).to_contain_text("1.23")
        expect(count).to_contain_text("2")

    def test_selftest_block_renders_result_rows(self):
        """Clicking self-test sends the command; a full SELFTEST block renders."""
        self._open()

        # The contracted self-test button should enqueue a 'selftest' command.
        btn = _resilient(self.page, [
            ("testid", "btn-selftest"), ("css", "#btn-selftest"),
            ("role", ("button", "self.?test"))], "self-test button")
        btn.click()
        self.assertIn("selftest", self._sent(),
                      "clicking the self-test button should send the 'selftest' command")

        # The device answers with a full block (including the Phase-3 BLE row);
        # the page must render the per-check rows and the overall result.
        self._feed(
            "SELFTEST BEGIN",
            "SELFTEST i2c PASS detail=0x68",
            "SELFTEST whoami PASS detail=0x68",
            "SELFTEST accel PASS detail=1.002g",
            "SELFTEST noise PASS detail=0.0061g",
            "SELFTEST ble PASS detail=advertising",
            "SELFTEST flash PASS detail=1441792B_free",
            "SELFTEST END result=PASS",
        )
        card = _resilient(self.page, [
            ("testid", "selftest"), ("css", "#selftest-card"), ("css", ".selftest")],
            "self-test result card")
        expect(card).to_contain_text("i2c")
        expect(card).to_contain_text("ble")   # the Phase-3 BLE check must show
        expect(card).to_contain_text("flash")
        expect(card).to_contain_text("PASS")

    def test_dump_flow_creates_a_session_row(self):
        """Download session -> feed a full dump -> a saved session row appears."""
        self._open()

        # The download control lives on the Sessions tab, which is display:none
        # until selected — so switch to it first (text reads work regardless of
        # visibility, but a click needs the element visible).
        tab = _resilient(self.page, [
            ("testid", "tab-sessions"), ("css", '[data-tab="sessions"]'),
            ("role", ("tab", "sessions")), ("text", r"^Sessions$")], "Sessions tab")
        tab.click()

        dump = _resilient(self.page, [
            ("testid", "btn-dump"), ("css", "#btn-download-session"),
            ("role", ("button", "download"))], "download-session button")
        dump.click()
        self.assertIn("dump", self._sent(),
                      "clicking download should send the 'dump' command")

        # Reply exactly as the device/CLI protocol does: framed jumps.csv +
        # trace.csv sections, terminated by 'OK dump'.
        self._feed(
            "FILE jumps.csv BEGIN",
            "n,takeoff_s,airtime_raw_s,airtime_s,height_m",
            "1,10.000,0.615,0.600,0.441",
            "2,20.000,1.010,1.000,1.226",
            "FILE jumps.csv END",
            "FILE trace.csv BEGIN",
            "t,mag",
            "FILE trace.csv END",
            "OK dump",
        )

        row = _resilient(self.page, [
            ("testid", "session-row"), ("css", ".session"),
            ("css", "#sessions-list .card")], "saved-session row")
        # The two fed jumps, best = 1.226 m -> shown as 1.23.
        expect(row).to_contain_text("2 jumps")
        expect(row).to_contain_text("1.23")

    def test_sync_banner_flow_syncs_session_and_offers_clear(self):
        """STATS carrying stored_jumps>0 raises the cross-tab sync banner;
        clicking Sync sends 'dump', and feeding back a framed dump saves a
        session (with its per-jump bar chart), updates the all-time chip, and
        offers to clear the device — only now, after the save is verified."""
        self._open()

        # Before any STATS arrives the banner must be hidden — this is the
        # regression check for the [hidden]-vs-display:flex cascade bug (a
        # class's display rule silently beat the hidden attribute until
        # style.css gained a global [hidden]{display:none !important} guard).
        banner = _resilient(self.page, [
            ("testid", "sync-banner"), ("css", "#sync-banner")], "sync banner")
        expect(banner).to_be_hidden()

        # A STATS line with stored_jumps (+ the optional trace_bytes, parsed
        # if present per the parallel STATS change) should raise the banner.
        self._feed("STATS session_jumps=0 session_best_m=0 "
                   "stored_jumps=5 stored_best_m=1.790 trace_bytes=2048")
        expect(banner).to_be_visible()
        expect(banner).to_contain_text("5")

        sync_btn = _resilient(self.page, [
            ("testid", "btn-sync"), ("css", "#btn-sync"),
            ("role", ("button", "sync"))], "sync button")
        sync_btn.click()
        self.assertIn("dump", self._sent(),
                      "clicking Sync should send the 'dump' command")

        # Reply exactly as the device/CLI protocol does: framed jumps.csv (2
        # rows) + trace.csv sections, terminated by 'OK dump'.
        self._feed(
            "FILE jumps.csv BEGIN",
            "n,takeoff_s,airtime_raw_s,airtime_s,height_m",
            "1,10.000,0.615,0.600,0.441",
            "2,20.000,1.010,1.000,1.226",
            "FILE jumps.csv END",
            "FILE trace.csv BEGIN",
            "t,mag",
            "0.00,1.00",
            "0.05,1.01",
            "0.10,1.03",
            "FILE trace.csv END",
            "OK dump",
        )

        row = _resilient(self.page, [
            ("testid", "session-row"), ("css", ".session"),
            ("css", "#sessions-list .card")], "saved-session row")
        expect(row).to_contain_text("2 jumps")

        clear_btn = _resilient(self.page, [
            ("testid", "btn-clear-after-sync"), ("css", ".after-sync .btn-danger"),
            ("role", ("button", "clear device"))], "post-sync clear-device offer")
        expect(clear_btn).to_be_visible()

        # ---- session chart: one bar per jump --------------------------------
        # Both the inline just-synced panel and the saved session card render
        # their own copy of the chart (same session), so this testid matches
        # twice; _resilient returns the first (document order: sync-result
        # panel comes before the sessions list in index.html).
        chart = _resilient(self.page, [
            ("testid", "session-chart"), ("css", ".chart")], "session bar chart")
        # Bars are drawn as rounded-top <path> marks (fill=--series); each
        # jump also gets a same-sized transparent hit-target <rect> layered on
        # top for tap/hover, so counting rect+path together would double-count.
        bars = chart.locator("svg path")
        self.assertEqual(bars.count(), 2,
                          "session-chart should draw exactly one bar per jump")

        # ---- all-time chip reflects the freshly-synced session --------------
        chip = _resilient(self.page, [
            ("testid", "alltime-best"), ("css", "#chip-alltime-best")],
            "all-time-best chip")
        expect(chip).to_contain_text("4.0 ft")  # best of the two jumps, 1.226 m

        # ---- clearing is only ever offered here, post-save -------------------
        clear_btn.click()
        self.assertIn("clear", self._sent(),
                      "clicking the post-sync Clear device button should send 'clear'")
        # ...and once cleared, the banner must actually go away (regression
        # check: it used to stay visible forever showing stale pre-clear text).
        expect(banner).to_be_hidden()

    def test_theme_toggle_is_one_tap_and_persists(self):
        """Dark mode is ONE tap away (the old three-state cycle was a real
        usability complaint). Before any tap the app follows the OS scheme
        (light, in this headless run); each tap flips light<->dark and the
        explicit choice persists to localStorage."""
        self._open()

        self.assertEqual(self._html_attr("data-theme"), "light",
                          "no stored preference + light OS scheme should boot light")

        toggle = _resilient(self.page, [
            ("testid", "theme-toggle"), ("css", "#btn-theme")],
            "theme toggle button")

        toggle.click()  # light -> dark, one tap
        self.assertEqual(self._html_attr("data-theme"), "dark",
                          "one tap must reach dark mode")
        self.assertEqual(self._local_storage("jh_theme"), "dark",
                          "the chosen theme should persist to localStorage")

        toggle.click()  # dark -> light
        self.assertEqual(self._html_attr("data-theme"), "light",
                          "second tap returns to light")
        self.assertEqual(self._local_storage("jh_theme"), "light")

    def test_unit_toggle_flips_hero_unit(self):
        """After a jump comes in, the unit toggle flips the preferred unit —
        data-unit on <html>, persisted to localStorage — and its own label
        flips between offering metres and feet."""
        self._open()

        self._feed("JUMP n=1 airtime_raw_s=0.615 airtime_s=0.600 "
                   "height_m=1.790 height_ft=5.9 best_m=1.790")

        # The toggle lives on the Live tab.
        live_tab = _resilient(self.page, [
            ("css", '[data-tab="live"]'), ("role", ("tab", "live")),
            ("text", r"^Live$")], "Live tab")
        live_tab.click()

        height = _resilient(self.page, [
            ("testid", "live-height"), ("css", "#live-height-m")],
            "live height (m) readout")
        expect(height).to_contain_text("1.79")

        self.assertEqual(self._html_attr("data-unit"), "ft",
                          "the owner thinks in feet, so ft is the default preferred unit")
        toggle = _resilient(self.page, [
            ("testid", "unit-toggle"), ("css", "#btn-unit")],
            "unit toggle button")
        expect(toggle).to_contain_text("meters")  # offers to switch TO metres

        toggle.click()
        self.assertEqual(self._html_attr("data-unit"), "m",
                          "clicking the unit toggle should flip the preferred unit")
        self.assertEqual(self._local_storage("jh_unit"), "m",
                          "the preferred unit should persist to localStorage")
        expect(toggle).to_contain_text("feet")  # now offers to switch back

        toggle.click()
        self.assertEqual(self._html_attr("data-unit"), "ft")
        self.assertEqual(self._local_storage("jh_unit"), "ft")
        expect(toggle).to_contain_text("meters")


if __name__ == "__main__":
    unittest.main()
