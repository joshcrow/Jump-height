"""tools/tests/test_puckd_setup.py — the setup wizard (tools/puckd/setup/)
driven end-to-end, the same shape as test_web_sync.py for the rider's page:
a real (headless) Chromium loads the actual index.html/setup.css/setup.js
served by the actual tools/puckd/setup/server.py, with `google_authorize`,
`garmin_login` and `notify_permission` swapped for fakes this file controls
— never a mocked DOM, never a re-implementation of the page's own logic.

docs/sync-agent-plan.md ("Setup") is the contract: four screens, one press
each (Skip is the one stated secondary action, on screen 3 only), copy
verbatim, and — per the build instructions for this file — "exactly one
enabled button is on screen at every stage". That last rule is checked after
every state change below via `_enabled_primary_buttons()`, which counts only
`.btn` (the primary, black button): `.btn-link` (Skip) is a deliberately
different, secondary weight, the same shape web/sync/sync.css already gives
its own re-save link ("a re-save is link-styled, never a second black
button") — so Skip's presence is asserted on separately, never folded into
the "one enabled button" count.

WHY the skips are worded, never silent: a reading that did not happen is a
finding (CLAUDE.md rule 3). A missing playwright or an unlaunchable browser
raises SkipTest WITH the reason attached — the same two-step Chromium launch
as test_web_sync.py, for the same measured reason (this environment's
Chromium build number does not match Playwright's default lookup).

WHY tearDown asserts on page errors: an uncaught JavaScript exception makes
every assertion above it meaningless (CLAUDE.md rule 3 again) — copied from
test_web_sync.py's own tearDown for the same reason.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent.parent
SETUP_DIR = REPO / "tools" / "puckd" / "setup"
PUCKD_DIR = REPO / "tools" / "puckd"

# tools/puckd/ has no __init__.py — flat modules on sys.path, the same
# convention the rest of tools/ already uses (e.g. tools/fake_device.py's
# own `sys.path.insert(0, ... / "tools")`).
sys.path.insert(0, str(SETUP_DIR))
import server as puckd_server  # tools/puckd/setup/server.py  # noqa: E402

# Read-only: proves server.py's `_field()` really accepts the REAL
# garmin.LoginResult dataclass, not just a test's plain dict — never edited,
# only imported (CLAUDE.md: "never edit a file you were not assigned").
sys.path.insert(0, str(PUCKD_DIR))
import garmin as real_garmin  # tools/puckd/garmin.py  # noqa: E402

CHROMIUM_FALLBACK = "/opt/pw-browsers/chromium"

try:
    from playwright.sync_api import sync_playwright

    _PW_IMPORT_ERROR = None
except Exception as _e:  # ImportError, or a half-installed package
    sync_playwright = None
    _PW_IMPORT_ERROR = _e


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


class FakeGoogle:
    """A fake `google_authorize()`. `.succeed(account)` / `.fail(message)`
    set what the NEXT call does — a real OAuth attempt either completes with
    an account or it does not, never both at once."""

    def __init__(self) -> None:
        self._account: Optional[str] = None
        self._error: Optional[str] = None
        self.calls = 0

    def succeed(self, account: str) -> None:
        self._account, self._error = account, None

    def fail(self, message: str) -> None:
        self._account, self._error = None, message

    def __call__(self) -> str:
        self.calls += 1
        if self._error is not None:
            raise RuntimeError(self._error)
        return self._account  # type: ignore[return-value]


class FakeGarmin:
    """A fake `garmin_login(email, password, mfa_code)`. `plan` is consumed
    one entry per call, in order — the same two-call shape the real
    garmin.login() has for the MFA dance (a plain call, then one carrying
    the code). An entry may be a dict OR a real `garmin.LoginResult` —
    exercising both is the point of TestGarminResultShapes below."""

    def __init__(self, plan=()) -> None:
        self._plan = list(plan)
        self.calls: list[tuple[str, str, Optional[str]]] = []

    def __call__(self, email: str, password: str, mfa_code: Optional[str]):
        self.calls.append((email, password, mfa_code))
        if not self._plan:
            raise AssertionError("garmin_login called more times than scripted")
        return self._plan.pop(0)


@unittest.skipIf(sync_playwright is None,
                 f"playwright not importable: {_PW_IMPORT_ERROR}")
class _PuckdSetupCase(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._pw = None
        cls._browser = None
        try:
            cls._pw = sync_playwright().start()
            cls._browser = _launch_chromium(cls._pw)  # may raise SkipTest
        except BaseException:
            cls._shutdown_class()  # never leak a started Playwright on a skip
            raise

    @classmethod
    def _shutdown_class(cls):
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
        self.google = FakeGoogle()
        self.garmin = FakeGarmin()
        self.notify_calls: list[bool] = []
        self._httpd = None
        self._port = None
        self.context = self._browser.new_context()
        self.context.route("**/*", self._block_external)
        self.page = self.context.new_page()
        self.page.set_default_timeout(8000)
        self._page_errors: list[str] = []
        self.page.on("pageerror", lambda e: self._page_errors.append(str(e)))
        self._blocked: list[str] = []

    def tearDown(self):
        errors = list(self._page_errors)
        try:
            self.context.close()
        except Exception as e:  # noqa: BLE001
            print(f"warning: the browser context did not close cleanly: {e}")
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
        # An uncaught JavaScript exception makes every assertion in the test
        # that just ran meaningless (CLAUDE.md rule 3) — printing it and
        # moving on would leave the suite green while the page threw.
        self.assertEqual(errors, [], "the page threw a JavaScript error:\n  "
                                      + "\n  ".join(errors))

    def _block_external(self, route):
        url = route.request.url
        if url.startswith(("http://127.0.0.1", "http://localhost")):
            route.continue_()
        else:
            self._blocked.append(url)
            route.abort()

    # ---------------------------------------------------------------- seam --

    def _start_server(self, garmin_plan=()):
        self.garmin = FakeGarmin(garmin_plan)
        self._httpd, self._port = puckd_server.serve(
            google_authorize=self.google,
            garmin_login=self.garmin,
            notify_permission=lambda: self.notify_calls.append(True),
            port=0,
        )

    def _open(self, path="/"):
        self.page.goto(f"http://127.0.0.1:{self._port}{path}",
                        wait_until="domcontentloaded")
        self.page.wait_for_function(
            "() => !!(window.__setup "
            "&& typeof window.__setup.showScreen === 'function')",
            timeout=15000)

    def _visible_screen(self) -> Optional[int]:
        for n in (1, 2, 3, 4):
            if self.page.locator(f"[data-testid=screen-{n}]").is_visible():
                return n
        return None

    def _enabled_primary_buttons(self) -> list[str]:
        """Every visible-and-enabled `.btn` on the currently-visible
        screen. See the module docstring for why `.btn-link` is excluded."""
        loc = self.page.locator(".screen:not([hidden]) .btn")
        out = []
        for i in range(loc.count()):
            b = loc.nth(i)
            if b.is_visible() and b.is_enabled():
                out.append(b.get_attribute("data-testid"))
        return out

    def _assert_one_enabled_button(self, expected_testid: str, where: str):
        enabled = self._enabled_primary_buttons()
        self.assertEqual(enabled, [expected_testid],
                          f"expected exactly one enabled button ({expected_testid}) "
                          f"{where}, found {enabled}")


class TestScreenOne(_PuckdSetupCase):

    def test_opens_on_screen_one_with_its_one_button(self):
        self._start_server()
        self._open()
        self.assertEqual(self._visible_screen(), 1)
        self.assertEqual(
            self.page.locator("[data-testid=btn-continue-1]").inner_text(), "Continue")
        self._assert_one_enabled_button("btn-continue-1", "on screen 1")

    def test_continue_advances_to_screen_two(self):
        self._start_server()
        self._open()
        self.page.click("[data-testid=btn-continue-1]")
        self.assertEqual(self._visible_screen(), 2)
        self._assert_one_enabled_button("btn-google", "on screen 2, before pressing Connect")
        self.assertEqual(self.page.locator("[data-testid=btn-google]").inner_text(),
                          "Connect")


class TestGoogleConnect(_PuckdSetupCase):

    def _to_screen_two(self):
        self._open()
        self.page.click("[data-testid=btn-continue-1]")
        self.assertEqual(self._visible_screen(), 2)

    def test_connect_success_shows_the_account_and_offers_continue(self):
        self.google.succeed("nick@gmail.com")
        self._start_server()
        self._to_screen_two()
        self.page.click("[data-testid=btn-google]")
        self.page.wait_for_selector("[data-testid=google-status]:visible")
        self.assertEqual(self.page.locator("[data-testid=google-status]").inner_text(),
                          "Connected as nick@gmail.com")
        self.assertEqual(self.google.calls, 1)
        self._assert_one_enabled_button("btn-google", "after a successful connect")
        self.assertEqual(self.page.locator("[data-testid=btn-google]").inner_text(),
                          "Continue")
        # The reused button now performs the second action.
        self.page.click("[data-testid=btn-google]")
        self.assertEqual(self._visible_screen(), 3)

    def test_connect_failure_leaves_connect_pressable_again(self):
        self.google.fail("the browser closed before consent finished")
        self._start_server()
        self._to_screen_two()
        self.page.click("[data-testid=btn-google]")
        self.page.wait_for_selector("[data-testid=google-status]:visible")
        # Whatever rclone raised, the screen shows one fixed sentence.
        self.assertEqual(self.page.locator("[data-testid=google-status]").inner_text(),
                          "Google sign-in did not complete.")
        # Still screen 2, still exactly the one Connect button — a failure
        # does not advance the wizard or leave zero pressable buttons.
        self.assertEqual(self._visible_screen(), 2)
        self._assert_one_enabled_button("btn-google", "after a failed connect")
        self.assertEqual(self.page.locator("[data-testid=btn-google]").inner_text(),
                          "Connect")
        # Retrying is the same button, and it can now succeed.
        self.google.succeed("nick@gmail.com")
        self.page.click("[data-testid=btn-google]")
        self.page.wait_for_function(
            "() => document.querySelector('[data-testid=btn-google]').textContent"
            " === 'Continue'")
        self.assertEqual(self.google.calls, 2)


class TestGarminLogin(_PuckdSetupCase):

    def _to_screen_three(self, garmin_plan=()):
        self._start_server(garmin_plan)
        self._open()
        self.page.click("[data-testid=btn-continue-1]")
        self.google.succeed("nick@gmail.com")
        self.page.click("[data-testid=btn-google]")
        self.page.wait_for_function(
            "() => document.querySelector('[data-testid=btn-google]').textContent"
            " === 'Continue'")
        self.page.click("[data-testid=btn-google]")
        self.assertEqual(self._visible_screen(), 3)

    def test_plain_login_reaches_signed_in_and_continue(self):
        self._to_screen_three(garmin_plan=[{"ok": True}])
        self._assert_one_enabled_button("btn-garmin", "on screen 3, before signing in")
        self.assertFalse(self.page.locator("[data-testid=btn-skip]").is_disabled())
        self.page.fill("[data-testid=garmin-email]", "nick@example.com")
        self.page.fill("[data-testid=garmin-password]", "hunter2")
        self.page.click("[data-testid=btn-garmin]")
        self.page.wait_for_selector("[data-testid=garmin-status]:visible")
        self.assertEqual(self.page.locator("[data-testid=garmin-status]").inner_text(),
                          "Signed in")
        self.assertEqual(self.garmin.calls, [("nick@example.com", "hunter2", None)])
        # The form fields and Skip disappear once signed in; only Continue remains.
        self.assertTrue(self.page.locator("[data-testid=garmin-email]").is_hidden())
        self.assertTrue(self.page.locator("[data-testid=garmin-password]").is_hidden())
        self.assertTrue(self.page.locator("[data-testid=btn-skip]").is_hidden())
        self._assert_one_enabled_button("btn-garmin", "after a successful sign-in")
        self.assertEqual(self.page.locator("[data-testid=btn-garmin]").inner_text(),
                          "Continue")
        self.assertEqual(self.notify_calls, [], "screen 4 not reached yet")
        self.page.click("[data-testid=btn-garmin]")
        self.assertEqual(self._visible_screen(), 4)
        self.assertEqual(self.notify_calls, [True],
                          "the notification-permission hook must fire once screen 4 shows")

    def test_mfa_branch_reveals_the_code_field_then_signs_in(self):
        self._to_screen_three(garmin_plan=[
            {"ok": False, "needs_mfa": True},
            {"ok": True},
        ])
        self.assertTrue(self.page.locator("[data-testid=garmin-code]").is_hidden(),
                         "the code field must not appear before Garmin asks for it")
        self.page.fill("[data-testid=garmin-email]", "nick@example.com")
        self.page.fill("[data-testid=garmin-password]", "hunter2")
        self.page.click("[data-testid=btn-garmin]")
        self.page.wait_for_selector("[data-testid=garmin-code]:visible")
        # Still screen 3, still exactly one enabled primary button, Skip
        # still available — the code prompt is not a failure state.
        self.assertEqual(self._visible_screen(), 3)
        self._assert_one_enabled_button("btn-garmin", "once Garmin has asked for a code")
        self.assertFalse(self.page.locator("[data-testid=btn-skip]").is_disabled())
        self.page.fill("[data-testid=garmin-code]", "123456")
        self.page.click("[data-testid=btn-garmin]")
        self.page.wait_for_selector("[data-testid=garmin-status]:visible")
        self.assertEqual(self.page.locator("[data-testid=garmin-status]").inner_text(),
                          "Signed in")
        self.assertEqual(self.garmin.calls, [
            ("nick@example.com", "hunter2", None),
            ("nick@example.com", "hunter2", "123456"),
        ])
        self._assert_one_enabled_button("btn-garmin", "after the code is accepted")

    def test_plain_failure_shows_the_error_and_never_reveals_the_code_field(self):
        self._to_screen_three(garmin_plan=[
            {"ok": False, "error": "invalid credentials"},
        ])
        self.page.fill("[data-testid=garmin-email]", "nick@example.com")
        self.page.fill("[data-testid=garmin-password]", "wrong")
        self.page.click("[data-testid=btn-garmin]")
        self.page.wait_for_selector("[data-testid=garmin-status]:visible")
        self.assertEqual(self.page.locator("[data-testid=garmin-status]").inner_text(),
                          "invalid credentials")
        self.assertTrue(self.page.locator("[data-testid=garmin-code]").is_hidden(),
                         "a plain failure is not an MFA prompt")
        self.assertEqual(self._visible_screen(), 3)
        self._assert_one_enabled_button("btn-garmin", "after a plain login failure")
        self.assertFalse(self.page.locator("[data-testid=btn-skip]").is_disabled())


class TestSkip(_PuckdSetupCase):

    def test_skip_reaches_screen_four_without_calling_garmin_login(self):
        self._start_server(garmin_plan=[])
        self._open()
        self.page.click("[data-testid=btn-continue-1]")
        self.google.succeed("nick@gmail.com")
        self.page.click("[data-testid=btn-google]")
        self.page.wait_for_function(
            "() => document.querySelector('[data-testid=btn-google]').textContent"
            " === 'Continue'")
        self.page.click("[data-testid=btn-google]")
        self.assertEqual(self._visible_screen(), 3)
        self.page.click("[data-testid=btn-skip]")
        self.assertEqual(self._visible_screen(), 4)
        self.assertEqual(self.garmin.calls, [],
                          "Skip must never call garmin_login")
        self.assertEqual(self.notify_calls, [True])
        self._assert_one_enabled_button("btn-close", "on screen 4")
        self.assertEqual(self.page.locator("[data-testid=btn-close]").inner_text(),
                          "Close")


class TestStepQueryParam(_PuckdSetupCase):

    def test_step_query_param_opens_that_screen_directly(self):
        """"Re-runnable from the menu bar; each step individually" /
        "Token expiry -> ... opens step 3 alone" (docs/sync-agent-plan.md)."""
        self._start_server()
        self._open("/setup?step=3")
        self.assertEqual(self._visible_screen(), 3)
        self._assert_one_enabled_button("btn-garmin", "landing directly on step 3")

    def test_served_at_slash_setup_for_the_menu_bar_default(self):
        """tools/puckd/menubar.py:33's DEFAULT_SETUP_URL is
        "http://127.0.0.1:17888/setup" — this path must serve the same page,
        with its relative setup.css/setup.js references still resolving."""
        self._start_server()
        self._open("/setup")
        self.assertEqual(self._visible_screen(), 1)
        # A stylesheet that failed to load would leave the primary button
        # unstyled but still present; the real signal is that the browser
        # did not have to be told about it — no console/page error, which
        # tearDown already asserts.
        self.assertEqual(self.page.locator("[data-testid=btn-continue-1]").inner_text(),
                          "Continue")


class TestGarminResultShapes(_PuckdSetupCase):
    """server.py's `_field()` claims a fake dict and the REAL
    garmin.LoginResult dataclass are handled identically — checked here
    against the actual class from tools/puckd/garmin.py, not a re-typed
    stand-in."""

    def test_real_login_result_dataclass_works_exactly_like_a_dict(self):
        self._start_server(garmin_plan=[
            real_garmin.LoginResult(ok=False, needs_mfa=True, error=None),
            real_garmin.LoginResult(ok=True, needs_mfa=False, error=None),
        ])
        self._open()
        self.page.click("[data-testid=btn-continue-1]")
        self.google.succeed("nick@gmail.com")
        self.page.click("[data-testid=btn-google]")
        self.page.wait_for_function(
            "() => document.querySelector('[data-testid=btn-google]').textContent"
            " === 'Continue'")
        self.page.click("[data-testid=btn-google]")
        self.page.fill("[data-testid=garmin-email]", "nick@example.com")
        self.page.fill("[data-testid=garmin-password]", "hunter2")
        self.page.click("[data-testid=btn-garmin]")
        self.page.wait_for_selector("[data-testid=garmin-code]:visible")
        self.page.fill("[data-testid=garmin-code]", "000000")
        self.page.click("[data-testid=btn-garmin]")
        self.page.wait_for_selector("[data-testid=garmin-status]:visible")
        self.assertEqual(self.page.locator("[data-testid=garmin-status]").inner_text(),
                          "Signed in")


if __name__ == "__main__":
    unittest.main()
