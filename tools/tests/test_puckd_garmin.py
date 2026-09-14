"""Tests for tools/puckd/garmin.py (docs/sync-agent-plan.md's Garmin
paragraph, lines 57-58, and setup screen 3, line 37).

No live Garmin, ever: every seam this module makes into garth --
garth.Client, garth.sso.login, garth.sso.resume_login, and a Client
instance's .connectapi / .download / .load / .dump -- is mocked. Where the
call goes through garth's own (real, unmocked) Activity.list(), the mocked
.connectapi() return value is a *recorded-shaped* fixture: camelCase keys
exactly as Garmin Connect's activity-list endpoint sends them, run through
garth's real camelCase->dataclass parsing (verified directly against
garth==0.8.0's source in tools/puckd/garmin.py's module docstring) rather
than a hand-rolled duck-typed stand-in -- so a change to garth's parsing
would show up here, not just a change to this module's assumptions about it.

Run directly:
    python3 -m pytest tools/tests/test_puckd_garmin.py -q
or as part of the full suite:
    python3 -m pytest tools/tests -q
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parent.parent.parent
PUCKD_DIR = REPO / "tools" / "puckd"
sys.path.insert(0, str(PUCKD_DIR))  # garmin.py has no package __init__.py
# (namespace package territory shared with sibling P1 modules) -- imported
# directly by file, the same way this file is its own isolated unit.

import garmin  # noqa: E402
import garth  # noqa: E402


def _valid_zip_bytes() -> bytes:
    """A real, tiny zip -- not just b"PK" -- so the "not a zip" check in
    fetch_new() is exercised against genuine zip bytes on the happy path,
    not a string that merely starts with the right two bytes by luck."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("12345.fit", b"\x00fit-bytes-stand-in")
    return buf.getvalue()


def _recorded_activity(activity_id: int, start_time_gmt: str) -> dict:
    """One entry as Garmin Connect's
    /activitylist-service/activities/search/activities really sends it
    (camelCase, nested activityType/eventType) -- the shape garth's real
    Activity.list() parses via camel_to_snake_dict() + the pydantic
    Activity dataclass."""
    return {
        "activityId": activity_id,
        "activityName": f"Session {activity_id}",
        "activityType": {"typeId": 1, "typeKey": "windsurfing"},
        "eventType": {"typeId": 9, "typeKey": "uncategorized"},
        "startTimeLocal": start_time_gmt,
        "startTimeGMT": start_time_gmt,
        "distance": 1234.5,
    }


class PuckdGarminTestCase(unittest.TestCase):
    """Base: an isolated PUCKD_HOME per test, garmin's module-level MFA
    state reset (login()'s _pending_mfa is a module global by design --
    see garmin.py's docstring on why -- so a leftover from one test must
    never leak into the next)."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="puckd-garmin-test-")
        self._old_env = os.environ.get(garmin.PUCKD_HOME_ENV)
        os.environ[garmin.PUCKD_HOME_ENV] = self._tmp
        garmin._pending_mfa = None

    def tearDown(self):
        garmin._pending_mfa = None
        if self._old_env is None:
            os.environ.pop(garmin.PUCKD_HOME_ENV, None)
        else:
            os.environ[garmin.PUCKD_HOME_ENV] = self._old_env
        shutil.rmtree(self._tmp, ignore_errors=True)

    def token_dir(self) -> Path:
        return Path(self._tmp) / garmin.TOKEN_SUBDIR

    def write_tokens(self, refresh_token_expires_at: float):
        d = self.token_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "oauth1_token.json").write_text(json.dumps({
            "oauth_token": "tok", "oauth_token_secret": "sec",
            "mfa_token": None, "mfa_expiration_timestamp": None,
            "domain": "garmin.com",
        }))
        (d / "oauth2_token.json").write_text(json.dumps({
            "scope": "s", "jti": "j", "token_type": "Bearer",
            "access_token": "a", "refresh_token": "r",
            "expires_in": 3600, "expires_at": time.time() + 3600,
            "refresh_token_expires_in": 7776000,
            "refresh_token_expires_at": refresh_token_expires_at,
        }))


# --- login() ---------------------------------------------------------------


class TestLogin(PuckdGarminTestCase):
    def test_success_persists_tokens_and_returns_ok(self):
        mock_client = MagicMock()
        fake_oauth1, fake_oauth2 = object(), object()

        with patch.object(garmin.garth, "Client", return_value=mock_client) as Client, \
             patch.object(garmin.garth.sso, "login",
                           return_value=(fake_oauth1, fake_oauth2)) as sso_login:
            result = garmin.login("nick@example.com", "hunter2")

        Client.assert_called_once_with()
        sso_login.assert_called_once_with(
            "nick@example.com", "hunter2",
            client=mock_client, return_on_mfa=True,
        )
        self.assertEqual(result, garmin.LoginResult(ok=True, needs_mfa=False, error=None))
        self.assertIs(mock_client.oauth1_token, fake_oauth1)
        self.assertIs(mock_client.oauth2_token, fake_oauth2)
        mock_client.dump.assert_called_once_with(str(self.token_dir()))

    def test_bad_credentials_is_ok_false_with_error_not_needs_mfa(self):
        exc = garth.exc.GarthException(msg="SSO error: PASSWORD_INVALID: bad credentials")
        with patch.object(garmin.garth, "Client", return_value=MagicMock()), \
             patch.object(garmin.garth.sso, "login", side_effect=exc):
            result = garmin.login("nick@example.com", "wrong")

        self.assertFalse(result.ok)
        self.assertFalse(result.needs_mfa)
        self.assertEqual(result.error, "Couldn't sign in. Check your email and password.")
        self.assertNotIn(str(exc), result.error)
        # A rejected password must never leave a partial token dir behind.
        self.assertFalse(self.token_dir().exists())

    def test_mfa_branch_then_second_call_with_code_completes_login(self):
        """The exact sequence docs/sync-agent-plan.md:37's watch screen
        drives: [Sign in] -> needs_mfa True -> a code field appears ->
        the code is submitted as a second login() call."""
        mock_client = MagicMock()

        def fake_sso_login(email, password, client=None, return_on_mfa=False):
            # Echo the *same* client object back in client_state, exactly as
            # garth/sso.py's real login() does (client_state["client"] = client).
            return "needs_mfa", {
                "client": client,
                "login_params": {"clientId": "x"},
                "mfa_method": "email",
            }

        with patch.object(garmin.garth, "Client", return_value=mock_client), \
             patch.object(garmin.garth.sso, "login", side_effect=fake_sso_login):
            first = garmin.login("nick@example.com", "hunter2")

        self.assertEqual(first, garmin.LoginResult(ok=False, needs_mfa=True, error=None))
        self.assertIsNotNone(garmin._pending_mfa)
        # Nothing persisted yet -- there is no token pair until MFA resolves.
        self.assertFalse(self.token_dir().exists())

        fake_oauth1, fake_oauth2 = object(), object()
        with patch.object(garmin.garth.sso, "resume_login",
                           return_value=(fake_oauth1, fake_oauth2)) as resume:
            second = garmin.login("nick@example.com", "hunter2", mfa_code="123456")

        resume.assert_called_once()
        called_state, called_code = resume.call_args.args
        self.assertIs(called_state["client"], mock_client)
        self.assertEqual(called_code, "123456")

        self.assertEqual(second, garmin.LoginResult(ok=True, needs_mfa=False, error=None))
        self.assertIsNone(garmin._pending_mfa)
        self.assertIs(mock_client.oauth1_token, fake_oauth1)
        self.assertIs(mock_client.oauth2_token, fake_oauth2)
        mock_client.dump.assert_called_once_with(str(self.token_dir()))

    def test_mfa_code_with_no_pending_login_is_a_clean_error(self):
        result = garmin.login("nick@example.com", "hunter2", mfa_code="000000")
        self.assertEqual(
            result,
            garmin.LoginResult(ok=False, needs_mfa=False,
                                error="no Garmin sign-in is waiting for a code"),
        )

    def test_wrong_mfa_code_keeps_pending_state_for_a_retry(self):
        mock_client = MagicMock()
        with patch.object(garmin.garth, "Client", return_value=mock_client), \
             patch.object(garmin.garth.sso, "login",
                           return_value=("needs_mfa",
                                         {"client": mock_client, "login_params": {},
                                          "mfa_method": "email"})):
            garmin.login("nick@example.com", "hunter2")

        pending_before = garmin._pending_mfa
        with patch.object(garmin.garth.sso, "resume_login",
                           side_effect=garth.exc.GarthException(msg="wrong code")):
            result = garmin.login("nick@example.com", "hunter2", mfa_code="111111")

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "That code didn't work. Try again.")
        self.assertTrue(result.needs_mfa, "the code field must stay on screen")
        # Still pending: the next call can retry the code without
        # re-asking for email/password.
        self.assertIs(garmin._pending_mfa, pending_before)


# --- is_signed_in() ----------------------------------------------------


class TestIsSignedIn(PuckdGarminTestCase):
    def test_no_tokens_on_disk_is_false(self):
        self.assertFalse(garmin.is_signed_in())

    def test_valid_unexpired_refresh_token_is_true(self):
        self.write_tokens(refresh_token_expires_at=time.time() + 7_776_000)
        self.assertTrue(garmin.is_signed_in())

    def test_expired_refresh_token_is_false(self):
        self.write_tokens(refresh_token_expires_at=time.time() - 10)
        self.assertFalse(garmin.is_signed_in())

    def test_malformed_token_file_is_false_not_a_crash(self):
        d = self.token_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "oauth1_token.json").write_text("{}")
        (d / "oauth2_token.json").write_text("not json at all")
        self.assertFalse(garmin.is_signed_in())

    def test_missing_expiry_field_is_false(self):
        d = self.token_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "oauth1_token.json").write_text("{}")
        (d / "oauth2_token.json").write_text(json.dumps({"access_token": "a"}))
        self.assertFalse(garmin.is_signed_in())


# --- fetch_new() -------------------------------------------------------


class TestFetchNew(PuckdGarminTestCase):
    def setUp(self):
        super().setUp()
        self.write_tokens(refresh_token_expires_at=time.time() + 7_776_000)
        self.out_dir = Path(self._tmp) / "fits"

    def test_not_signed_in_raises(self):
        (self.token_dir() / "oauth2_token.json").unlink()
        with self.assertRaises(RuntimeError):
            garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

    def test_downloads_original_and_returns_written_paths(self):
        recorded = [_recorded_activity(555001, "2026-09-10 18:32:07")]
        mock_client = MagicMock()
        mock_client.connectapi.return_value = recorded
        mock_client.download.return_value = _valid_zip_bytes()

        with patch.object(garmin.garth, "Client", return_value=mock_client):
            got = garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

        self.assertEqual(got, [str(self.out_dir / "555001.zip")])
        self.assertTrue((self.out_dir / "555001.zip").is_file())
        self.assertEqual((self.out_dir / "555001.zip").read_bytes(), _valid_zip_bytes())

        mock_client.download.assert_called_once_with(
            garmin.ORIGINAL_DOWNLOAD_PATH.format(activity_id=555001)
        )
        # No leftover partial-write name.
        self.assertFalse((self.out_dir / "555001.zip.part").exists())

    def test_skips_already_seen_ids_without_downloading_again(self):
        self.out_dir.mkdir(parents=True)
        already = self.out_dir / "555000.zip"
        already.write_bytes(b"previously-downloaded-bytes")

        recorded = [
            _recorded_activity(555001, "2026-09-10 18:32:07"),
            _recorded_activity(555000, "2026-09-09 12:00:00"),
        ]
        mock_client = MagicMock()
        mock_client.connectapi.return_value = recorded
        mock_client.download.return_value = _valid_zip_bytes()

        with patch.object(garmin.garth, "Client", return_value=mock_client):
            got = garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

        self.assertEqual(got, [str(self.out_dir / "555001.zip")])
        mock_client.download.assert_called_once_with(
            garmin.ORIGINAL_DOWNLOAD_PATH.format(activity_id=555001)
        )
        # The pre-existing file is completely untouched.
        self.assertEqual(already.read_bytes(), b"previously-downloaded-bytes")

    def test_stops_at_since_iso_and_does_not_download_older(self):
        recorded = [
            _recorded_activity(2, "2026-09-10 18:32:07"),  # newer than since
            _recorded_activity(1, "2026-08-01 00:00:00"),  # older than since
        ]
        mock_client = MagicMock()
        mock_client.connectapi.return_value = recorded
        mock_client.download.return_value = _valid_zip_bytes()

        with patch.object(garmin.garth, "Client", return_value=mock_client):
            got = garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

        self.assertEqual(got, [str(self.out_dir / "2.zip")])
        mock_client.download.assert_called_once_with(
            garmin.ORIGINAL_DOWNLOAD_PATH.format(activity_id=2)
        )

    def test_paginates_across_pages_stopping_on_a_short_page(self):
        page1 = [_recorded_activity(4, "2026-09-10 00:00:00"),
                  _recorded_activity(3, "2026-09-09 00:00:00")]
        page2 = [_recorded_activity(2, "2026-09-08 00:00:00")]  # short page

        mock_client = MagicMock()
        mock_client.connectapi.side_effect = [page1, page2]
        mock_client.download.return_value = _valid_zip_bytes()

        with patch.object(garmin.garth, "Client", return_value=mock_client), \
             patch.object(garmin, "LIST_PAGE_SIZE", 2):
            got = garmin.fetch_new("2000-01-01T00:00:00Z", str(self.out_dir))

        self.assertEqual(
            sorted(got),
            sorted(str(self.out_dir / f"{i}.zip") for i in (4, 3, 2)),
        )
        starts = [call.kwargs.get("params", {}).get("start")
                  for call in mock_client.connectapi.call_args_list]
        self.assertEqual(starts, [0, 2])

    def test_corrupt_download_raises_and_writes_nothing(self):
        recorded = [_recorded_activity(9, "2026-09-10 18:32:07")]
        mock_client = MagicMock()
        mock_client.connectapi.return_value = recorded
        mock_client.download.return_value = b"<html>not a zip</html>"

        with patch.object(garmin.garth, "Client", return_value=mock_client):
            with self.assertRaises(garmin.GarminDownloadError):
                garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

        self.assertFalse((self.out_dir / "9.zip").exists())
        self.assertFalse((self.out_dir / "9.zip.part").exists())

    def test_empty_download_raises(self):
        recorded = [_recorded_activity(9, "2026-09-10 18:32:07")]
        mock_client = MagicMock()
        mock_client.connectapi.return_value = recorded
        mock_client.download.return_value = b""

        with patch.object(garmin.garth, "Client", return_value=mock_client):
            with self.assertRaises(garmin.GarminDownloadError):
                garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

    def test_no_activities_returns_empty_list(self):
        mock_client = MagicMock()
        mock_client.connectapi.return_value = []
        with patch.object(garmin.garth, "Client", return_value=mock_client):
            got = garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))
        self.assertEqual(got, [])
        mock_client.download.assert_not_called()


# --- last_seen() / mark_seen() ------------------------------------------


class TestLastSeenStore(PuckdGarminTestCase):
    def store_path(self) -> Path:
        return Path(self._tmp) / "garmin_last_seen.json"

    def test_missing_store_returns_epoch_default(self):
        self.assertEqual(garmin.last_seen(self.store_path()), garmin.DEFAULT_SINCE_ISO)

    def test_mark_then_last_seen_round_trips(self):
        store = self.store_path()
        garmin.mark_seen(store, "2026-09-10T18:32:07.123Z")
        self.assertEqual(garmin.last_seen(store), "2026-09-10T18:32:07.123Z")

    def test_mark_seen_leaves_no_tmp_file_behind(self):
        store = self.store_path()
        garmin.mark_seen(store, "2026-09-10T18:32:07.123Z")
        tmp = store.with_suffix(store.suffix + ".tmp")
        self.assertFalse(tmp.exists())
        self.assertTrue(store.exists())

    def test_corrupted_store_returns_default_not_a_crash(self):
        store = self.store_path()
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("{not json")
        self.assertEqual(garmin.last_seen(store), garmin.DEFAULT_SINCE_ISO)

    def test_mark_seen_creates_parent_dirs(self):
        store = Path(self._tmp) / "nested" / "dir" / "garmin_last_seen.json"
        garmin.mark_seen(store, "2026-09-10T18:32:07Z")
        self.assertEqual(garmin.last_seen(store), "2026-09-10T18:32:07Z")


class TestLateUploadedActivity(PuckdGarminTestCase):
    """The failure this window exists for: a ride HAPPENS on Tuesday and
    reaches Garmin Connect on Thursday, when the watch next sees the phone.
    daemon.py advances last_seen to "now" after any successful fetch, so
    Tuesday's activity sorts older than Thursday's watermark and, without
    LOOKBACK_S, is skipped silently and forever -- a ride that never arrives
    and nothing anywhere says why (CLAUDE.md rule 3)."""

    def setUp(self):
        super().setUp()
        self.write_tokens(refresh_token_expires_at=time.time() + 7_776_000)
        self.out_dir = Path(self._tmp) / "fits"

    def test_an_activity_older_than_last_seen_but_inside_the_window_is_fetched(self):
        recorded = [_recorded_activity(555002, "2026-09-08 15:00:00")]  # Tuesday
        mock_client = MagicMock()
        mock_client.connectapi.return_value = recorded
        mock_client.download.return_value = _valid_zip_bytes()

        with patch.object(garmin.garth, "Client", return_value=mock_client):
            got = garmin.fetch_new("2026-09-10T09:00:00Z", str(self.out_dir))

        self.assertEqual(got, [str(self.out_dir / "555002.zip")])

    def test_the_window_is_bounded_not_a_full_history_rescan(self):
        """Bounded, so the 6-hourly tick never walks the whole account."""
        recorded = [_recorded_activity(444000, "2026-06-01 10:00:00")]
        mock_client = MagicMock()
        mock_client.connectapi.return_value = recorded
        mock_client.download.return_value = _valid_zip_bytes()

        with patch.object(garmin.garth, "Client", return_value=mock_client):
            got = garmin.fetch_new("2026-09-10T09:00:00Z", str(self.out_dir))

        self.assertEqual(got, [], "months old: outside the window, not re-fetched")
        mock_client.download.assert_not_called()

    def test_a_re_listed_activity_is_never_downloaded_twice(self):
        """What makes the window free: the id-on-disk skip already dedupes."""
        self.out_dir.mkdir(parents=True)
        (self.out_dir / "555002.zip").write_bytes(_valid_zip_bytes())
        recorded = [_recorded_activity(555002, "2026-09-08 15:00:00")]
        mock_client = MagicMock()
        mock_client.connectapi.return_value = recorded

        with patch.object(garmin.garth, "Client", return_value=mock_client):
            got = garmin.fetch_new("2026-09-10T09:00:00Z", str(self.out_dir))

        self.assertEqual(got, [])
        mock_client.download.assert_not_called()


class TestLoginNeverRaisesIntoTheSetupWindow(PuckdGarminTestCase):
    """login() is wired straight to a button in the native setup window. A
    dropped wifi is requests.ConnectionError, not a GarthException, and used
    to come out as a traceback rather than a line of copy."""

    def test_a_network_error_is_the_same_one_line_of_copy(self):
        with patch.object(garmin.garth, "Client", return_value=MagicMock()), \
             patch.object(garmin.garth.sso, "login",
                          side_effect=OSError("Network is unreachable")):
            result = garmin.login("nick@example.com", "hunter2")
        self.assertFalse(result.ok)
        self.assertFalse(result.needs_mfa)
        self.assertEqual(result.error, garmin.LOGIN_FAILED_COPY)

    def test_a_garmin_page_change_is_not_a_traceback_either(self):
        with patch.object(garmin.garth, "Client", return_value=MagicMock()), \
             patch.object(garmin.garth.sso, "login",
                          side_effect=AttributeError("'NoneType' has no group")):
            result = garmin.login("nick@example.com", "hunter2")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, garmin.LOGIN_FAILED_COPY)

    def test_the_password_is_never_written_to_the_token_dir(self):
        """docs/sync-agent-plan.md's whole token model: the password is used
        for one exchange and never produced again."""
        fake_oauth1, fake_oauth2 = MagicMock(), MagicMock()
        mock_client = MagicMock()
        with patch.object(garmin.garth, "Client", return_value=mock_client), \
             patch.object(garmin.garth.sso, "login",
                          return_value=(fake_oauth1, fake_oauth2)):
            self.assertTrue(garmin.login("nick@example.com", "hunter2").ok)
        for path in Path(self._tmp).rglob("*"):
            if path.is_file():
                self.assertNotIn(b"hunter2", path.read_bytes(), str(path))


if __name__ == "__main__":
    unittest.main()
