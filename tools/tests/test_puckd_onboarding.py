#!/usr/bin/env python3
"""The first-run window's logic, without a screen: tools/puckd/onboarding.py's
OnboardingModel drives every string, every transition and which button is the
primary; the AppKit window only renders screen(). These pin the copy verbatim
(the mockup the owner approved is the spec) and every path: Google connects /
fails / is re-read, Garmin expands inline / asks for a code / is wrong / is
skipped, the page that follows, and reopening from "Set up…"."""
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

INTRO = ("Charge the puck from this Mac and its rides sync themselves: the "
         "recording goes to Google Drive, and the matching GPS track comes "
         "over from Garmin Connect. Two sign-ins, once.")
FOOT = "You can come back to this from the wing in the menu bar."


@dataclass
class R:
    """What garmin.login returns."""
    ok: bool = False
    needs_mfa: bool = False
    error: Optional[str] = None


def model(auth=lambda: True, account=lambda: "nick@gmail.com", plan=None, **kw):
    plan = list(plan or [])
    calls = []

    def login(email, password, code):
        calls.append((email, password, code))
        return plan.pop(0)
    m = ob.OnboardingModel(google_authorize=auth, google_account=account,
                           garmin_login=login, **kw)
    m.start()
    m.calls = calls
    return m


def row(screen, key):
    return next(r for r in screen.rows if r.key == key)


def checklist(m):
    """The checklist as it stands. Settling both rows advances the window to
    the "how it works" page, so this looks back at the rows it left behind."""
    m.page = ob.PAGE_CHECKLIST
    return m.screen()


class TheFirstScreen(unittest.TestCase):
    def test_brand_and_intro(self):
        s = model().screen()
        self.assertEqual(s.page, "checklist")
        self.assertEqual(s.title, "JumpHeight")
        self.assertEqual(s.subtitle, "Sync for the puck")
        self.assertEqual(s.intro, INTRO)
        self.assertEqual(s.cards, [], "the how-it-works page is not this page")

    def test_two_rows_in_order(self):
        s = model().screen()
        self.assertEqual([r.key for r in s.rows], ["google", "garmin"])
        g, w = s.rows
        self.assertEqual((g.badge, g.title, g.subtitle), ("1", "Google Drive", "Where the rides go"))
        self.assertEqual((w.badge, w.title, w.subtitle),
                         ("2", "Garmin Connect", "Optional · the GPS track for each ride"))
        self.assertEqual([g.tone, w.tone], ["muted", "muted"])
        self.assertFalse(g.done or w.done)

    def test_the_primary_is_on_the_first_undone_row(self):
        s = model().screen()
        self.assertEqual(row(s, "google").button, ob.Button("Connect", "google", "primary"))
        self.assertEqual(row(s, "garmin").button, ob.Button("Sign in", "garmin", "normal"))
        self.assertEqual(s.primary().label, "Connect")

    def test_footer(self):
        s = model().screen()
        self.assertEqual(s.footer_left, FOOT)
        self.assertEqual(s.footer_buttons, [ob.Button("Skip for now", "skip_all", "text")])


class GoogleRow(unittest.TestCase):
    def test_working_shows_a_spinner_and_no_button_and_no_primary_moves(self):
        m = model()
        m.google = "working"
        s = m.screen()
        g = row(s, "google")
        self.assertTrue(g.busy)
        self.assertIsNone(g.button, "the spinner sits where the button was")
        self.assertEqual(g.subtitle, "Finish in your browser, then come back here.")
        self.assertIsNone(s.primary(), "Return must not jump to Garmin mid-connect")

    def test_connected_names_the_account_and_moves_the_primary(self):
        m = model()
        m.google_connect()
        s = m.screen()
        g = row(s, "google")
        self.assertTrue(g.done)
        self.assertEqual(g.badge, "✓")
        self.assertEqual(g.subtitle, "Connected as nick@gmail.com")
        self.assertEqual(g.tone, "ok")
        self.assertEqual(g.button, ob.Button("Change", "google", "text"))
        self.assertEqual(s.primary().label, "Sign in")
        self.assertEqual(s.page, "checklist", "Garmin is not settled yet")

    def test_connected_without_a_readable_email(self):
        m = model(account=lambda: None)
        m.google_connect()
        self.assertEqual(row(m.screen(), "google").subtitle, "Connected to Google Drive")

    def test_an_unreadable_account_is_not_a_failure(self):
        def boom():
            raise OSError("drive said no")
        m = model(account=boom)
        m.google_connect()
        self.assertEqual(row(m.screen(), "google").subtitle, "Connected to Google Drive")

    def test_failure_is_calm_and_keeps_the_button(self):
        m = model(auth=lambda: False)
        m.google_connect()
        g = row(m.screen(), "google")
        self.assertEqual(g.subtitle, "That didn’t connect. Try again.")
        self.assertEqual(g.tone, "plain", "no red: a retry is not an alarm")
        self.assertEqual(g.button, ob.Button("Connect", "google", "primary"))
        self.assertFalse(g.done)

    def test_change_runs_the_same_authorize(self):
        m = model()
        m.google_connect()
        m.act("google")
        self.assertEqual(row(m.screen(), "google").subtitle, "Connected as nick@gmail.com")


class GarminRowInline(unittest.TestCase):
    def _open(self, plan=None):
        m = model(plan=plan)
        m.google_connect()
        m.garmin_open()
        return m

    def test_sign_in_expands_the_row_in_place(self):
        s = self._open().screen()
        w = row(s, "garmin")
        self.assertEqual(s.page, "checklist", "no new screen")
        self.assertEqual(w.subtitle, "Your Garmin email and password. The password is never saved.")
        self.assertEqual(w.fields, [ob.Field("Email"), ob.Field("Password", secure=True)])
        self.assertEqual(w.actions, [ob.Button("Skip", "garmin_skip", "text"),
                                     ob.Button("Sign in", "garmin_signin", "primary")])
        self.assertIsNone(w.button, "the row's own button gives way to the form")
        self.assertEqual(s.primary().action, "garmin_signin", "Return submits")
        self.assertTrue(row(s, "google").done, "row 1 is untouched")

    def test_the_footer_says_where_a_code_would_appear_and_offers_no_skip_for_now(self):
        s = self._open().screen()
        self.assertEqual(s.footer_left, "If Garmin sends a code, a Code field appears here.")
        self.assertEqual(s.footer_buttons, [], "Skip lives in the row while the form is open")

    def test_plain_sign_in(self):
        m = self._open([R(ok=True)])
        m.garmin_signin(["nick@x.com", "hunter2"])
        self.assertEqual(m.calls, [("nick@x.com", "hunter2", None)])
        w = row(checklist(m), "garmin")
        self.assertTrue(w.done)
        self.assertEqual((w.badge, w.subtitle, w.tone), ("✓", "Signed in", "ok"))
        self.assertEqual(w.button, ob.Button("Change", "garmin", "text"))
        self.assertEqual(w.fields, [])

    def test_a_code_field_appears_only_when_garmin_asks(self):
        m = self._open([R(needs_mfa=True), R(ok=True)])
        m.garmin_signin(["nick@x.com", "hunter2"])
        s = m.screen()
        w = row(s, "garmin")
        self.assertEqual(w.fields, [ob.Field("Code")])
        self.assertEqual(w.subtitle, "Garmin sent you a code.")
        self.assertEqual(s.footer_left, FOOT, "the code hint has served its purpose")
        m.garmin_signin(["123456"])
        self.assertEqual(m.calls[1], ("nick@x.com", "hunter2", "123456"),
                         "the email and password he already typed are reused")
        self.assertEqual(row(checklist(m), "garmin").subtitle, "Signed in")
        self.assertEqual(m._password, "", "the password is dropped once signed in")

    def test_working_says_so(self):
        m = self._open()
        m.garmin = "working"
        w = row(m.screen(), "garmin")
        self.assertTrue(w.busy)
        self.assertEqual(w.subtitle, "Signing in…")

    def test_wrong_password_shows_garmins_own_sentence_in_the_row(self):
        m = self._open([R(error="Couldn’t sign in. Check your email and password.")])
        m.garmin_signin(["nick@x.com", "wrong"])
        w = row(m.screen(), "garmin")
        self.assertEqual(w.subtitle, "Couldn’t sign in. Check your email and password.")
        self.assertEqual(w.fields, [ob.Field("Email"), ob.Field("Password", secure=True)])
        self.assertFalse(w.done)

    def test_a_login_that_raises_is_a_failure_not_a_crash(self):
        def boom(email, password, code):
            raise RuntimeError("garth fell over")
        m = model()
        m.garmin_login = boom
        m.google_connect(); m.garmin_open()
        m.garmin_signin(["a", "b"])
        self.assertEqual(row(m.screen(), "garmin").subtitle,
                         "Couldn’t sign in. Check your email and password.")

    def test_skip_collapses_the_row_and_does_not_nag(self):
        m = self._open()
        m.garmin_skip()
        w = row(checklist(m), "garmin")
        self.assertEqual(w.subtitle, "Skipped")
        self.assertEqual(w.fields, [])
        self.assertFalse(w.done)
        self.assertEqual(w.button, ob.Button("Sign in", "garmin", "normal"))
        self.assertEqual(m._password, "")

    def test_change_on_a_signed_in_row_reopens_the_form(self):
        m = self._open([R(ok=True)])
        m.garmin_signin(["nick@x.com", "hunter2"])
        m.page = ob.PAGE_CHECKLIST
        m.act("garmin")
        w = row(m.screen(), "garmin")
        self.assertEqual(w.fields, [ob.Field("Email"), ob.Field("Password", secure=True)])


class TheHowItWorksPage(unittest.TestCase):
    def _settled(self):
        m = model(plan=[R(ok=True)])
        m.google_connect(); m.garmin_open(); m.garmin_signin(["a", "b"])
        return m

    def test_both_rows_settled_advances_the_window(self):
        self.assertEqual(self._settled().screen().page, "how")

    def test_skipping_garmin_settles_it_too(self):
        m = model()
        m.google_connect(); m.garmin_open(); m.garmin_skip()
        self.assertEqual(m.screen().page, "how")

    def test_google_alone_does_not(self):
        m = model()
        m.google_connect()
        self.assertEqual(m.screen().page, "checklist")

    def test_the_brand_row_becomes_the_headline(self):
        s = self._settled().screen()
        self.assertEqual(s.title, "All set")
        self.assertEqual(s.subtitle, "Here’s what to expect")
        self.assertEqual(s.rows, [])
        self.assertEqual(s.intro, "")

    def test_three_cards_each_a_real_piece_of_the_ui(self):
        s = self._settled().screen()
        self.assertEqual([c.art for c in s.cards], ["glyphs", "notification", "menu"])
        self.assertEqual(s.cards[0].title, "The wing in the menu bar")
        self.assertEqual(s.cards[0].body,
                         "Faded: no puck. Solid: puck charging, all synced. "
                         "With a line: syncing now. With a dot: it needs you.")
        self.assertEqual(s.cards[1].title, "One notification per ride")
        self.assertEqual(s.cards[1].body,
                         "Plus “Puck charged”. You never have to open anything.")
        self.assertEqual(s.cards[2].title, "Click the wing for details")
        self.assertEqual(s.cards[2].body,
                         "Charge, last ride, your rides folder, and this setup again.")

    def test_the_cards_sample_copy(self):
        self.assertEqual(ob.CARD_NOTIF_SAMPLE, "Ride synced · 12 jumps")
        self.assertEqual(ob.CARD_MENU_LINES,
                         ("Puck 86% · charging", "Last ride Tue 4:52 pm · 12 jumps",
                          "—", "Open rides folder", "Set up…"))

    def test_the_glyph_strip_is_the_menu_bars_own_four_files(self):
        assets = Path(ob.__file__).resolve().parent / "assets"
        for name in ("menubar-dormant.png", "menubar.png",
                     "menubar-working.png", "menubar-attention.png"):
            self.assertTrue((assets / name).is_file(), f"{name} must ship with the app")

    def test_footer_and_done(self):
        s = self._settled().screen()
        self.assertEqual(s.footer_left, "Plug the puck in to charge, whenever.")
        self.assertEqual(s.footer_buttons, [ob.Button("Done", "done", "primary")])
        self.assertEqual(s.primary().action, "done")

    def test_done_closes(self):
        done = []
        m = self._settled()
        m.on_finished = lambda: done.append(1)
        m.act("done")
        self.assertEqual(done, [1])

    def test_skip_for_now_closes_too(self):
        done = []
        m = model()
        m.on_finished = lambda: done.append(1)
        m.act("skip_all")
        self.assertEqual(done, [1])


class ReopenedFromTheMenuBar(unittest.TestCase):
    def test_it_reads_the_world_again(self):
        m = model(google_is_authorized=lambda: True, garmin_is_signed_in=lambda: False)
        m.reopen()
        s = m.screen()
        self.assertEqual(s.page, "checklist")
        self.assertEqual(row(s, "google").subtitle, "Connected as nick@gmail.com")
        self.assertEqual(row(s, "garmin").subtitle, "Optional · the GPS track for each ride")
        self.assertEqual(s.primary().label, "Sign in")

    def test_how_it_works_stays_reachable(self):
        m = model(google_is_authorized=lambda: True, garmin_is_signed_in=lambda: True)
        m.reopen()
        s = m.screen()
        self.assertEqual(s.footer_buttons, [ob.Button("How it works", "how", "text")])
        m.act("how")
        self.assertEqual(m.screen().page, "how")

    def test_a_reopened_window_never_jumps_to_the_page_under_him(self):
        m = model(plan=[R(ok=True)], google_is_authorized=lambda: False,
                  garmin_is_signed_in=lambda: False)
        m.reopen()
        m.google_connect()
        m.garmin_open()
        m.garmin_signin(["a", "b"])
        self.assertTrue(m.settled())
        self.assertEqual(m.screen().page, "checklist")

    def test_a_probe_that_throws_leaves_the_last_known_state(self):
        def boom():
            raise OSError("rclone is gone")
        m = model(google_is_authorized=boom, garmin_is_signed_in=boom)
        m.google_connect()
        m.reopen()
        self.assertTrue(row(m.screen(), "google").done)


class TheWindowDecidesNothing(unittest.TestCase):
    """Everything the AppKit layer branches on lives here, so it can't drift."""

    def test_every_action_a_screen_offers_is_dispatchable(self):
        offered = set()
        for m in (model(), model(auth=lambda: False),
                  model(google_is_authorized=lambda: True, garmin_is_signed_in=lambda: True)):
            m.google_connect()
            m.reopened = m.google_is_authorized is not None
            for state in ("todo", "form", "done", "skipped"):
                m.garmin = state
                for page in ("checklist", "how"):
                    m.page = page
                    s = m.screen()
                    for r in s.rows:
                        offered.update(b.action for b in r.actions)
                        if r.button:
                            offered.add(r.button.action)
                    offered.update(b.action for b in s.footer_buttons)
        m = model()
        for action in sorted(offered):
            m.act(action, ["a", "b"])              # no KeyError, no branch missed
        self.assertEqual(offered, {"google", "garmin", "garmin_signin", "garmin_skip",
                                   "how", "done", "skip_all"})

    def test_the_slow_actions_are_the_two_that_touch_the_network(self):
        self.assertEqual(ob.OnboardingModel.SLOW, ("google", "garmin_signin"))

    def test_exactly_one_primary_per_screen(self):
        for m, label in ((model(), "fresh"),):
            seen = [b for r in m.screen().rows
                    for b in list(r.actions) + ([r.button] if r.button else [])
                    if b.style == "primary"]
            self.assertEqual(len(seen), 1, label)


class StartingUp(unittest.TestCase):
    def test_start_reflects_what_is_already_true(self):
        m = model()
        m.start(google_connected=True, garmin_signed_in=True)
        s = m.screen()
        self.assertEqual(s.page, "checklist", "a first paint never opens on the page")
        self.assertTrue(row(s, "google").done and row(s, "garmin").done)
        self.assertEqual(row(s, "google").subtitle, "Connected as nick@gmail.com")

    def test_production_model_is_wired_to_the_real_modules(self):
        import importlib
        garmin = importlib.import_module("puckd.garmin")
        with patch.object(upload, "is_authorized", lambda: False), \
             patch.object(garmin, "is_signed_in", lambda: False):
            m = ob.production_model()
        # production_model imports the modules by their bare names (the app's
        # own sys.path), so compare what they ARE, not object identity.
        wired = {"google_authorize": m.google_authorize, "google_account": m.google_account,
                 "garmin_login": m.garmin_login, "google_is_authorized": m.google_is_authorized,
                 "garmin_is_signed_in": m.garmin_is_signed_in}
        self.assertEqual({k: (v.__module__.split(".")[-1], v.__name__) for k, v in wired.items()},
                         {"google_authorize": ("upload", "authorize"),
                          "google_account": ("upload", "account_email"),
                          "garmin_login": ("garmin", "login"),
                          "google_is_authorized": ("upload", "is_authorized"),
                          "garmin_is_signed_in": ("garmin", "is_signed_in")})
        self.assertEqual(m.screen().page, "checklist")
        self.assertIsNotNone(upload.authorize and garmin.login)

    def test_daemon_still_finds_the_names_it_calls(self):
        """tools/puckd/daemon.py:_onboarding / run_setup use exactly these."""
        for name in ("production_model", "make_window", "OnboardingModel"):
            self.assertTrue(hasattr(ob, name), name)
        self.assertTrue(callable(ob.OnboardingModel.on_finished))


class GoogleFlowThroughRclone(unittest.TestCase):
    """authorize() = `rclone authorize drive --template ours` then
    `config create ... token <json>`; account_email() reads the token back
    with `config dump` and asks Drive who it is."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(); self.addCleanup(self._tmp.cleanup)
        self.rig = FakeRclone(Path(self._tmp.name) / "rig")

    def test_authorize_calls_both_and_passes_our_template(self):
        if not upload.google_client().get("client_id"):
            self.skipTest("google-client.json is not on this machine (it is never committed)")
        with patch.dict(os.environ, self.rig.base_env()):
            self.assertTrue(upload.authorize())
            self.assertTrue(upload.is_authorized())
        calls = self.rig.calls()
        client = upload.google_client()
        self.assertTrue(client["client_id"].endswith(".apps.googleusercontent.com"))
        self.assertEqual(client["scope"], "drive.file", "the narrow scope: only files the app made")
        auth = [c for c in calls if c[:1] == ["authorize"]][0]
        self.assertEqual(auth[1], "drive")
        import base64
        blob = json.loads(base64.b64decode(auth[2]))
        self.assertEqual(blob, {"client_id": client["client_id"], "client_secret": client["client_secret"],
                                "scope": "drive.file"},
                         "our own client AND the narrow scope, in the one form rclone honours")
        self.assertEqual(auth[3], "--template")
        self.assertTrue(auth[4].endswith("assets/oauth-done.html"))
        self.assertTrue(Path(auth[4]).is_file(), "the template must ship with the app")
        create = [c for c in calls if c[:2] == ["config", "create"]][0]
        self.assertEqual(create[create.index("scope") + 1], "drive.file")
        self.assertEqual(create[create.index("client_id") + 1], client["client_id"])
        self.assertIn("ya29.fake", create[create.index("token") + 1])

    def test_ensure_shared_grants_the_owner_editor_access_to_the_folder(self):
        posted = {}

        def post(url, bearer, body):
            posted["url"], posted["bearer"], posted["body"] = url, bearer, body
            return {"id": "perm1"}
        with patch.dict(os.environ, self.rig.base_env()):
            upload.authorize()
            (Path(self.rig.root) / "store" / "gdrive" / "JumpHeight" / "inbox").mkdir(parents=True, exist_ok=True) \
                if hasattr(self.rig, "root") else None
            # make the folder exist on the fake remote the way an upload would
            local = Path(self._tmp.name) / "ride.zip"; local.write_bytes(b"PK")
            upload.upload(local, "JumpHeight/inbox")
            self.assertTrue(upload.ensure_shared("JumpHeight", post_json=post))
        self.assertIn("/files/id-JumpHeight/permissions", posted["url"])
        self.assertEqual(posted["body"], {"role": "writer", "type": "user",
                                          "emailAddress": "joshcrow1193@gmail.com"})
        self.assertEqual(posted["bearer"], "ya29.fake")

    def test_ensure_shared_is_false_when_the_folder_is_not_there_yet(self):
        with patch.dict(os.environ, self.rig.base_env()):
            upload.authorize()
            self.assertFalse(upload.ensure_shared("JumpHeight", post_json=lambda *a: {}))

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

    def test_token_parser_raw_json(self):
        out = "Paste the following into your remote machine --->\n{\"access_token\":\"x\"}\n<---End paste\n"
        self.assertEqual(json.loads(upload._token_from_authorize_output(out))["access_token"], "x")
        self.assertIsNone(upload._token_from_authorize_output("nothing here"))

    def test_token_parser_base64_config_token(self):
        """What current rclone prints (measured 2026-09-13: the raw-JSON-only
        parser said 'didn't connect' after a successful consent)."""
        import base64
        inner = json.dumps({"token": json.dumps({"access_token": "y", "refresh_token": "r"})})
        blob = base64.b64encode(inner.encode()).decode().rstrip("=")
        out = f"Paste the following into your remote machine --->\n{blob}\n<---End paste\n"
        self.assertEqual(json.loads(upload._token_from_authorize_output(out))["access_token"], "y")
        inner2 = json.dumps({"access_token": "z"})
        blob2 = base64.b64encode(inner2.encode()).decode()
        self.assertEqual(json.loads(upload._token_from_authorize_output(blob2))["access_token"], "z")


if __name__ == "__main__":
    unittest.main()
