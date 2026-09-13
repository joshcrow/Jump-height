#!/usr/bin/env python3
"""The first-run window's logic, without a screen: tools/puckd/onboarding.py's
OnboardingModel drives every string and transition; the AppKit window only
renders screen(). These pin the copy verbatim and the three paths (connect
fails/succeeds, Garmin plain/MFA/skip), plus the account read-back."""
from __future__ import annotations

import json
import os
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from puckd import onboarding as ob  # noqa: E402
from puckd import upload  # noqa: E402
sys.path.insert(0, str(HERE))
from test_puckd_upload import FakeRclone  # noqa: E402
import tempfile  # noqa: E402


@dataclass
class R:
    ok: bool = False
    needs_mfa: bool = False
    error: Optional[str] = None


def model(auth=lambda: True, account=lambda: "nick@gmail.com", plan=None):
    plan = list(plan or [])
    calls = []

    def login(email, password, code):
        calls.append((email, password, code))
        return plan.pop(0)
    m = ob.OnboardingModel(google_authorize=auth, google_account=account, garmin_login=login)
    m.start()
    m.calls = calls
    return m


class ConnectScreen(unittest.TestCase):
    def test_first_screen_is_welcome_plus_connect(self):
        s = model().screen()
        self.assertEqual(s.title, "JumpHeight")
        self.assertEqual(s.body, "Plug the puck in to charge. Rides upload themselves.\n"
                                 "First, connect the Google Drive folder they go to.")
        self.assertEqual(s.buttons, [("Connect Google Drive", "connect")])
        self.assertEqual(s.fields, [])

    def test_connect_success_names_the_account_then_continues(self):
        m = model()
        m.connect()
        s = m.screen()
        self.assertEqual(s.status, "Connected as nick@gmail.com")
        self.assertEqual(s.buttons, [("Continue", "next")])
        m.next()
        self.assertEqual(m.screen().step, "watch")

    def test_connect_without_a_readable_email_still_says_connected(self):
        m = model(account=lambda: None)
        m.connect()
        self.assertEqual(m.screen().status, "Connected to Google Drive")

    def test_connect_failure_keeps_the_connect_button(self):
        m = model(auth=lambda: False)
        m.connect()
        s = m.screen()
        self.assertEqual(s.status, "That didn\u2019t connect. Try again.")
        self.assertEqual(s.buttons, [("Connect Google Drive", "connect")])
        m.next()
        self.assertEqual(m.step, "connect", "cannot continue without Drive")

    def test_already_connected_opens_on_continue(self):
        m = ob.OnboardingModel(google_authorize=lambda: True, google_account=lambda: "n@x.com",
                               garmin_login=lambda *a: R())
        m.start(already_connected=True)
        self.assertEqual(m.screen().buttons, [("Continue", "next")])


class WatchScreen(unittest.TestCase):
    def _at_watch(self, plan):
        m = model(plan=plan); m.connect(); m.next(); return m

    def test_fields_and_buttons(self):
        s = self._at_watch([]).screen()
        self.assertEqual(s.title, "Your watch")
        self.assertEqual(s.fields, ["Email", "Password"])
        self.assertEqual(s.buttons, [("Sign in", "signin"), ("Skip", "skip")])

    def test_plain_sign_in(self):
        m = self._at_watch([R(ok=True)])
        m.signin(["nick@x.com", "hunter2"])
        self.assertEqual(m.calls, [("nick@x.com", "hunter2", None)])
        self.assertEqual(m.screen().status, "Signed in")
        self.assertEqual(m.screen().buttons, [("Continue", "next")])
        m.next()
        self.assertEqual(m.screen().step, "done")

    def test_mfa_shows_a_code_field_then_signs_in(self):
        m = self._at_watch([R(needs_mfa=True), R(ok=True)])
        m.signin(["nick@x.com", "hunter2"])
        s = m.screen()
        self.assertEqual(s.fields, ["Code"])
        self.assertEqual(s.status, "Garmin sent you a code.")
        m.signin(["123456"])
        self.assertEqual(m.calls[1], ("nick@x.com", "hunter2", "123456"))
        self.assertTrue(m.signed_in)
        self.assertEqual(m._password, "", "the password is dropped once signed in")

    def test_wrong_password_shows_the_modules_sentence(self):
        m = self._at_watch([R(error="Couldn\u2019t sign in. Check your email and password.")])
        m.signin(["nick@x.com", "wrong"])
        self.assertEqual(m.screen().status, "Couldn\u2019t sign in. Check your email and password.")
        self.assertEqual(m.screen().fields, ["Email", "Password"])

    def test_skip_goes_to_done(self):
        m = self._at_watch([])
        m.skip()
        s = m.screen()
        self.assertEqual(s.step, "done")
        self.assertEqual(s.title, "Done")
        self.assertEqual(s.buttons, [("Close", "close")])
        self.assertIn("wing in the menu bar", s.body)

    def test_close_calls_on_finished(self):
        done = []
        m = model(); m.on_finished = lambda: done.append(1)
        m.connect(); m.next(); m.skip(); m.close()
        self.assertEqual(done, [1])


class GoogleFlowThroughRclone(unittest.TestCase):
    """authorize() = `rclone authorize drive --template ours` then
    `config create ... token <json>`; account_email() reads the token back
    with `config dump` and asks Drive who it is."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._tmp.cleanup)
        self.rig = FakeRclone(Path(self._tmp.name) / "rig")

    def test_authorize_calls_both_and_passes_our_template(self):
        with patch.dict(os.environ, self.rig.base_env()):
            self.assertTrue(upload.authorize())
            self.assertTrue(upload.is_authorized())
        calls = self.rig.calls()
        auth = [c for c in calls if c[:1] == ["authorize"]][0]
        self.assertEqual(auth[1], "drive")
        self.assertEqual(auth[2], "--template")
        self.assertTrue(auth[3].endswith("assets/oauth-done.html"))
        self.assertTrue(Path(auth[3]).is_file(), "the template must ship with the app")
        create = [c for c in calls if c[:2] == ["config", "create"]][0]
        self.assertIn("token", create)
        self.assertIn("ya29.fake", create[create.index("token") + 1])

    def test_account_email_reads_the_token_back_and_asks_drive(self):
        seen = {}

        def fetch(url, bearer):
            seen["url"], seen["bearer"] = url, bearer
            return {"user": {"emailAddress": "nick@gmail.com"}}
        with patch.dict(os.environ, self.rig.base_env()):
            upload.authorize()
            self.assertEqual(upload.account_email(fetch_json=fetch), "nick@gmail.com")
        self.assertEqual(seen["bearer"], "ya29.fake")
        self.assertIn("drive/v3/about", seen["url"])

    def test_account_email_is_none_when_drive_cannot_be_asked(self):
        with patch.dict(os.environ, self.rig.base_env()):
            upload.authorize()
            self.assertIsNone(upload.account_email(fetch_json=lambda u, b: (_ for _ in ()).throw(OSError())))

    def test_token_parser(self):
        out = "Paste the following into your remote machine --->\n{\"access_token\":\"x\"}\n<---End paste\n"
        self.assertEqual(json.loads(upload._token_from_authorize_output(out))["access_token"], "x")
        self.assertIsNone(upload._token_from_authorize_output("nothing here"))


if __name__ == "__main__":
    unittest.main()
