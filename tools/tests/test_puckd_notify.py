"""tools/puckd/notify.py and tools/puckd/menubar.py — the strings Nick
actually sees are the whole contract here (docs/sync-agent-plan.md: "Three
notifications exist and no others", "words are Nick's", and "the menu is
exactly the five lines in the spec"). Every literal string asserted below is
copied from that spec, not invented here.

Also pins the one architectural requirement that isn't a string: rumps must
never be imported just by importing tools/puckd/menubar — only by actually
building an app — so headless test runs (and CI with rumps absent) never pay
for, or accidentally trigger, a real menu-bar/notification framework.
"""

from __future__ import annotations

import datetime
import importlib
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

sys.path.insert(0, str(REPO / "tools"))

from puckd import notify  # noqa: E402
from puckd import menubar  # noqa: E402


class TestNotifyRender(unittest.TestCase):
    """render() is pure: (title, body), no subprocess, no osascript."""

    def test_synced_with_jumps(self):
        self.assertEqual(notify.render("synced", jumps=12), ("Ride synced · 12 jumps", None))

    def test_synced_zero_jumps_says_no_jumps(self):
        self.assertEqual(notify.render("synced", jumps=0), ("Ride synced · no jumps", None))

    def test_charged(self):
        self.assertEqual(notify.render("charged"), ("Puck charged", None))

    def test_updated(self):
        self.assertEqual(notify.render("updated"), ("Puck updated", None))

    def test_needs_you(self):
        title, body = notify.render(
            "needs_you",
            line="sign in to Garmin again",
            action="Enter the code Garmin just sent.",
        )
        self.assertEqual(title, "Needs you: sign in to Garmin again")
        self.assertEqual(body, "Enter the code Garmin just sent.")

    def test_needs_you_spec_button_example(self):
        # docs/sync-agent-plan.md line 13's own example action text.
        _title, body = notify.render(
            "needs_you", line="reset the puck", action="Press the small button on the puck twice."
        )
        self.assertEqual(body, "Press the small button on the puck twice.")

    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            notify.render("nope")

    def test_only_four_kinds_exist(self):
        self.assertEqual(notify.KINDS, ("synced", "charged", "needs_you", "updated"))


class TestNotifyFires(unittest.TestCase):
    """notify() renders then hands the result to `runner` — and, with an
    injected runner, never touches subprocess/osascript at all."""

    def test_notify_calls_injected_runner_with_rendered_strings(self):
        calls = []

        def fake_runner(title, body):
            calls.append((title, body))

        result = notify.notify("synced", runner=fake_runner, jumps=12)

        self.assertEqual(calls, [("Ride synced · 12 jumps", None)])
        self.assertEqual(result, ("Ride synced · 12 jumps", None))

    def test_notify_needs_you_through_injected_runner(self):
        calls = []
        notify.notify(
            "needs_you",
            runner=lambda t, b: calls.append((t, b)),
            line="sign in to Garmin again",
            action="Enter the code Garmin just sent.",
        )
        self.assertEqual(
            calls,
            [("Needs you: sign in to Garmin again", "Enter the code Garmin just sent.")],
        )

    def test_notify_default_runner_never_invoked_when_overridden(self):
        # If the default (real) runner were used by mistake, this would
        # attempt a real subprocess call; patch it out and assert it's cold.
        original = subprocess.run
        subprocess.run = None  # any call at all raises TypeError
        try:
            notify.notify("charged", runner=lambda t, b: None)
        finally:
            subprocess.run = original

    def test_osascript_runner_shells_out_with_correct_quoting(self):
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

        original = subprocess.run
        subprocess.run = fake_run
        try:
            notify.osascript_runner('He said "hi"', "line with \\ backslash")
        finally:
            subprocess.run = original

        self.assertEqual(captured["args"][0], "osascript")
        self.assertEqual(captured["args"][1], "-e")
        script = captured["args"][2]
        self.assertIn('with title "He said \\"hi\\""', script)
        self.assertIn('display notification "line with \\\\ backslash"', script)

    def test_osascript_runner_empty_body_for_none(self):
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args

        original = subprocess.run
        subprocess.run = fake_run
        try:
            notify.osascript_runner("Puck charged", None)
        finally:
            subprocess.run = original

        self.assertIn('display notification ""', captured["args"][2])
        self.assertIn('with title "Puck charged"', captured["args"][2])


class TestMenubarLines(unittest.TestCase):
    """The two status lines, with fixed inputs — exact spec wording."""

    def test_puck_line_charging(self):
        self.assertEqual(menubar.format_puck_line(86, True), "Puck 86% · charging")

    def test_puck_line_not_charging(self):
        self.assertEqual(menubar.format_puck_line(86, False), "Puck 86%")

    def test_puck_line_unknown_pct(self):
        self.assertEqual(menubar.format_puck_line(None, False), "Puck —")

    def test_ride_line_matches_spec_example(self):
        # "Last ride Tue 4:52 pm · 12 jumps" — 2026-09-08 16:52 is a Tuesday.
        dt = datetime.datetime(2026, 9, 8, 16, 52)
        self.assertEqual(dt.strftime("%a"), "Tue")
        self.assertEqual(menubar.format_ride_line(dt, 12), "Last ride Tue 4:52 pm · 12 jumps")

    def test_ride_line_single_digit_hour_has_no_leading_zero(self):
        dt = datetime.datetime(2026, 9, 8, 9, 5)
        self.assertEqual(menubar.format_ride_line(dt, 3), "Last ride Tue 9:05 am · 3 jumps")

    def test_ride_line_zero_jumps(self):
        dt = datetime.datetime(2026, 9, 8, 16, 52)
        self.assertEqual(menubar.format_ride_line(dt, 0), "Last ride Tue 4:52 pm · no jumps")

    def test_ride_line_never_synced(self):
        self.assertEqual(menubar.format_ride_line(None, None), "Last ride —")

    def test_no_title_ever_the_glyph_carries_attention(self):
        # State is encoded by redrawing the wing, never by a title or badge.
        self.assertIsNone(menubar.format_icon_title(False))
        self.assertIsNone(menubar.format_icon_title(True))
        self.assertNotEqual(menubar.glyph_state(False, None, True),
                            menubar.glyph_state(True, None, True))


class TestMenubarOpeners(unittest.TestCase):
    def test_default_opener_shells_to_open(self):
        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args

        original = subprocess.run
        subprocess.run = fake_run
        try:
            menubar.default_opener("/tmp/somewhere")
        finally:
            subprocess.run = original

        self.assertEqual(captured["args"], ["open", "/tmp/somewhere"])


class TestMenubarAppWiring(unittest.TestCase):
    """set_state() and the five-line menu, exercised through the real
    rumps.App (installed in this environment) — but only here, never at
    module import."""

    def test_app_has_exactly_five_menu_lines(self):
        app = menubar.make_app(opener=lambda _target: None)
        # This module builds four items; rumps.App supplies "Quit" itself
        # (via its own quit_button default, applied when the real NSMenu is
        # built at run()) — five lines total, as the spec requires.
        self.assertEqual(len(app.menu), 4)
        self.assertEqual(app.quit_button.title, "Quit")
        names = list(app.menu.keys())
        self.assertIn("Open rides folder", names)
        self.assertIn("Set up…", names)

    def test_set_state_rewrites_status_lines_and_icon(self):
        app = menubar.make_app(opener=lambda _target: None)
        dt = datetime.datetime(2026, 9, 8, 16, 52)

        app.set_state(puck_pct=86, charging=True, last_ride_dt=dt, last_jumps=12, attention=False)
        self.assertEqual(app._status_puck.title, "Puck 86% · charging")
        self.assertEqual(app._status_ride.title, "Last ride Tue 4:52 pm · 12 jumps")
        self.assertEqual(app.title, menubar.ICON_IDLE)

        app.set_state(puck_pct=40, charging=False, last_ride_dt=dt, last_jumps=0, attention=True)
        self.assertEqual(app._status_puck.title, "Puck 40%")
        self.assertEqual(app._status_ride.title, "Last ride Tue 4:52 pm · no jumps")
        self.assertEqual(app.title, menubar.ICON_ATTENTION)

    def test_open_rides_folder_calls_opener_with_spool_dir(self):
        calls = []
        app = menubar.make_app(spool_dir=Path("/tmp/spool-test"), opener=calls.append)
        app._open_rides_folder(None)
        self.assertEqual(calls, ["/tmp/spool-test"])

    def test_open_setup_calls_opener_with_setup_url(self):
        calls = []
        app = menubar.make_app(setup_url="http://127.0.0.1:9/setup", opener=calls.append)
        app._open_setup(None)
        self.assertEqual(calls, ["http://127.0.0.1:9/setup"])


class TestMenubarLazyImport(unittest.TestCase):
    """Importing tools/puckd/menubar must never require rumps — only
    building an app (build_app_class()/make_app()) may."""

    def test_module_import_does_not_require_rumps(self):
        # Setting sys.modules["rumps"] = None makes the import system raise
        # ImportError on any `import rumps` — the standard way to simulate
        # "not installed" without needing a custom meta_path finder.
        missing = object()
        saved = sys.modules.pop("rumps", missing)
        saved_menubar = sys.modules.pop("puckd.menubar", None)
        sys.modules["rumps"] = None
        try:
            reloaded = importlib.import_module("puckd.menubar")
            # Importing succeeded with rumps blocked: proves the import is lazy.
            self.assertEqual(reloaded.format_icon_title(False), None)
            with self.assertRaises(ImportError):
                reloaded.build_app_class()
        finally:
            del sys.modules["rumps"]
            if saved is not missing:
                sys.modules["rumps"] = saved
            if saved_menubar is not None:
                sys.modules["puckd.menubar"] = saved_menubar
            else:
                sys.modules.pop("puckd.menubar", None)
            importlib.import_module("puckd.menubar")


if __name__ == "__main__":
    unittest.main()
