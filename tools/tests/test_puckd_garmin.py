"""Tests for tools/puckd/garmin.py (docs/sync-agent-plan.md's Garmin
paragraph, lines 57-58, and setup screen 3, line 37).

No live Garmin, ever: the single seam this module makes into the client
library -- the `garminconnect.Garmin` CLASS -- is patched, so nothing here
opens a socket. Two things are deliberately NOT faked:

  * the token file's name and location come from garminconnect's own
    client.token_file_path(); the mocked client's dump() writes a real file
    there, with the real {"di_token", "di_refresh_token", "di_client_id"}
    shape read out of the installed client.py (dumps(), client.py:1504) --
    so "the password is nowhere on disk" and is_signed_in() are asserted
    against the bytes garminconnect actually writes, not a stand-in;
  * the ORIGINAL download format is the REAL
    garminconnect.Garmin.ActivityDownloadFormat.ORIGINAL enum member, kept
    on the patched class, so a test passing with dl_fmt=<some mock> is not
    possible.

Activity lists are recorded-shaped fixtures: camelCase keys exactly as
Garmin Connect's /activitylist-service/activities/search/activities sends
them, which is what Garmin.get_activities() returns untouched
(garminconnect/__init__.py:2382-2418).

Run directly:
    python3 -m pytest tools/tests/test_puckd_garmin.py -q
or as part of the full suite:
    python3 -m pytest tools/tests -q
"""

from __future__ import annotations

import base64
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

import requests

REPO = Path(__file__).resolve().parent.parent.parent
PUCKD_DIR = REPO / "tools" / "puckd"
sys.path.insert(0, str(PUCKD_DIR))  # garmin.py has no package __init__.py
# (namespace package territory shared with sibling P1 modules) -- imported
# directly by file, the same way this file is its own isolated unit.

import garmin  # noqa: E402
import garminconnect  # noqa: E402
from garminconnect import (  # noqa: E402
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

# Captured BEFORE any patching: the real enum, so the assertion that
# fetch_new() asks for ORIGINAL cannot be satisfied by a mock attribute.
REAL_FORMATS = garminconnect.Garmin.ActivityDownloadFormat
ORIGINAL = REAL_FORMATS.ORIGINAL


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
    (camelCase, nested activityType/eventType, startTimeGMT with a SPACE
    and no zone marker) -- the dicts Garmin.get_activities() hands back."""
    return {
        "activityId": activity_id,
        "activityName": f"Session {activity_id}",
        "activityType": {"typeId": 1, "typeKey": "windsurfing"},
        "eventType": {"typeId": 9, "typeKey": "uncategorized"},
        "startTimeLocal": start_time_gmt,
        "startTimeGMT": start_time_gmt,
        "distance": 1234.5,
    }


def _jwt(exp: float) -> str:
    """An unsigned-payload JWT carrying just an `exp` claim -- the only part
    of a Garmin di_token this module reads (it cannot verify the signature;
    neither can garminconnect, client.py:263-284)."""
    def seg(obj: dict) -> str:
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{seg({'alg': 'RS256'})}.{seg({'exp': exp})}.signature-not-checked"


class PuckdGarminTestCase(unittest.TestCase):
    """Base: an isolated PUCKD_HOME per test, garmin's module-level MFA
    state reset (login()'s _pending_mfa is a module global by design --
    see garmin.py's docstring on why -- so a leftover from one test must
    never leak into the next)."""

    def setUp(self):
        # realpath: on macOS tempfile hands back /var/folders/..., and /var
        # is a symlink to /private/var. garminconnect's token_file_path()
        # REFUSES a tokenstore with a symlink anywhere in its ancestry
        # (client.py:73-83), so an unresolved temp dir would test a
        # fallback path instead of the real upstream rule.
        self._tmp = os.path.realpath(tempfile.mkdtemp(prefix="puckd-garmin-test-"))
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

    def token_file(self) -> Path:
        return self.token_dir() / "garmin_tokens.json"

    def write_tokens(self, *, di_refresh_token="refresh-tok", di_token=None):
        """The exact JSON garminconnect's Client.dumps() produces
        (client.py:1504-1512) -- nothing else is in that file."""
        d = self.token_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.token_file().write_text(json.dumps({
            "di_token": di_token if di_token is not None else _jwt(time.time() + 3600),
            "di_refresh_token": di_refresh_token,
            "di_client_id": "CLIENT_ID",
        }))

    def mock_api(self, **attrs) -> MagicMock:
        """A stand-in Garmin instance whose client.dump() writes the real
        token file, the way garminconnect's own dump() would."""
        api = MagicMock(name="GarminInstance")
        api.login.return_value = (None, None)
        api.resume_login.return_value = (None, None)

        def dump(path):
            Path(path).mkdir(parents=True, exist_ok=True)
            (Path(path) / "garmin_tokens.json").write_text(json.dumps({
                "di_token": _jwt(time.time() + 3600),
                "di_refresh_token": "refresh-tok",
                "di_client_id": "CLIENT_ID",
            }))

        api.client.dump.side_effect = dump
        for key, value in attrs.items():
            setattr(api, key, value)
        return api

    def patch_garmin_class(self, api: MagicMock):
        """patch.object on the CLASS, keeping the real download-format enum
        reachable on it."""
        cls = MagicMock(name="GarminClass", return_value=api)
        cls.ActivityDownloadFormat = REAL_FORMATS
        return patch.object(garmin.garminconnect, "Garmin", cls), cls


# --- login() ---------------------------------------------------------------


class TestLogin(PuckdGarminTestCase):
    def test_success_persists_tokens_and_returns_ok(self):
        api = self.mock_api()
        patcher, cls = self.patch_garmin_class(api)
        with patcher:
            result = garmin.login("nick@example.com", "hunter2")

        self.assertEqual(result, garmin.LoginResult(ok=True, needs_mfa=False, error=None))
        # Constructed with the typed credentials and the non-blocking MFA
        # mode -- a prompt callback would hang the setup window's button.
        cls.assert_called_once_with(
            email="nick@example.com", password="hunter2", return_on_mfa=True,
        )
        # No tokenstore passed to login(): a typed password means "sign me
        # in", not "reuse whatever is already on disk".
        api.login.assert_called_once_with()
        api.client.dump.assert_called_once_with(str(self.token_dir()))
        self.assertTrue(self.token_file().is_file())
        self.assertTrue(garmin.is_signed_in())
        self.assertTrue(garmin.ever_signed_in())

    def test_the_password_is_never_written_to_disk(self):
        """docs/sync-agent-plan.md's whole token model: the password is used
        for one exchange and never produced again."""
        api = self.mock_api()
        patcher, _ = self.patch_garmin_class(api)
        with patcher:
            self.assertTrue(garmin.login("nick@example.com", "hunter2").ok)

        found = [p for p in Path(self._tmp).rglob("*") if p.is_file()]
        self.assertTrue(found, "the token file should exist to make this meaningful")
        for path in found:
            self.assertNotIn(b"hunter2", path.read_bytes(), str(path))
        # ...and it is not left on the object either.
        self.assertIsNone(api.password)

    def test_wrong_password_is_ok_false_with_login_failed_copy(self):
        exc = GarminConnectAuthenticationError(
            "Authentication failed (401 Unauthorized). "
            "Original error: 401 Unauthorized (Invalid Username or Password)"
        )
        api = self.mock_api()
        api.login.side_effect = exc
        patcher, _ = self.patch_garmin_class(api)
        with patcher:
            result = garmin.login("nick@example.com", "wrong")

        self.assertFalse(result.ok)
        self.assertFalse(result.needs_mfa)
        self.assertEqual(result.error, garmin.LOGIN_FAILED_COPY)
        self.assertNotIn(str(exc), result.error or "")
        # A rejected password must never leave a partial token dir behind.
        self.assertFalse(self.token_file().exists())
        self.assertFalse(garmin.is_signed_in())

    def test_rate_limited_is_the_blocked_copy_not_check_your_password(self):
        """MEASURED 2026-09-13: Garmin answers the first login strategy 429
        before it has looked at a password. Blaming the password would be a
        lie."""
        api = self.mock_api()
        api.login.side_effect = GarminConnectTooManyRequestsError(
            "Too many login attempts. Please wait a few minutes before trying again."
        )
        patcher, _ = self.patch_garmin_class(api)
        with patcher:
            result = garmin.login("nick@example.com", "hunter2")

        self.assertFalse(result.ok)
        self.assertFalse(result.needs_mfa)
        self.assertEqual(result.error, garmin.GARMIN_BLOCKED_COPY)

    def test_a_connection_error_is_the_unreachable_copy(self):
        api = self.mock_api()
        api.login.side_effect = requests.exceptions.ConnectionError(
            "HTTPSConnectionPool(host='connectapi.garmin.com', port=443): "
            "Max retries exceeded (Failed to establish a new connection)"
        )
        patcher, _ = self.patch_garmin_class(api)
        with patcher:
            result = garmin.login("nick@example.com", "hunter2")

        self.assertFalse(result.ok)
        self.assertEqual(result.error, garmin.GARMIN_UNREACHABLE_COPY)

    def test_a_wrapped_connection_error_is_still_the_unreachable_copy(self):
        """garminconnect re-wraps nearly everything it catches into its own
        GarminConnectConnectionError (__init__.py:822-828), whose NAME
        contains 'Connection' regardless of what actually happened -- so the
        text, not the class name, has to decide."""
        api = self.mock_api()
        api.login.side_effect = GarminConnectConnectionError(
            "Login failed: HTTPSConnectionPool(host='sso.garmin.com', port=443): "
            "Max retries exceeded"
        )
        patcher, _ = self.patch_garmin_class(api)
        with patcher:
            result = garmin.login("nick@example.com", "hunter2")
        self.assertEqual(result.error, garmin.GARMIN_UNREACHABLE_COPY)

    def test_all_strategies_exhausted_is_blocked_not_a_bad_password(self):
        api = self.mock_api()
        api.login.side_effect = GarminConnectConnectionError(
            "All login strategies exhausted: unexpected HTML challenge page"
        )
        patcher, _ = self.patch_garmin_class(api)
        with patcher:
            result = garmin.login("nick@example.com", "hunter2")
        self.assertEqual(result.error, garmin.GARMIN_BLOCKED_COPY)

    def test_a_garmin_page_change_is_not_a_traceback_either(self):
        api = self.mock_api()
        api.login.side_effect = AttributeError("'NoneType' object has no attribute 'group'")
        patcher, _ = self.patch_garmin_class(api)
        with patcher:
            result = garmin.login("nick@example.com", "hunter2")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, garmin.LOGIN_FAILED_COPY)


class TestLoginMFA(PuckdGarminTestCase):
    """The exact sequence docs/sync-agent-plan.md:37's watch screen drives:
    [Sign in] -> needs_mfa True -> a code field appears -> the code is
    submitted as a second login() call. The first call must NOT block."""

    def test_mfa_branch_then_second_call_with_code_completes_login(self):
        api = self.mock_api()
        api.login.return_value = ("needs_mfa", None)
        patcher, cls = self.patch_garmin_class(api)

        with patcher:
            first = garmin.login("nick@example.com", "hunter2")

            self.assertEqual(first,
                             garmin.LoginResult(ok=False, needs_mfa=True, error=None))
            self.assertIsNotNone(garmin._pending_mfa)
            # Nothing persisted yet -- there is no token until MFA resolves.
            self.assertFalse(self.token_file().exists())
            self.assertFalse(garmin.is_signed_in())
            api.client.dump.assert_not_called()

            second = garmin.login("nick@example.com", "hunter2", "123456")

        self.assertEqual(second, garmin.LoginResult(ok=True, needs_mfa=False, error=None))
        # The paused exchange is resumed on the SAME instance: the typed
        # credentials are reused, not re-sent, and no second sign-in starts.
        cls.assert_called_once()
        self.assertEqual(api.login.call_count, 1)
        api.resume_login.assert_called_once_with(None, "123456")
        api.client.dump.assert_called_once_with(str(self.token_dir()))
        self.assertIsNone(garmin._pending_mfa)
        self.assertTrue(garmin.is_signed_in())

    def test_wrong_mfa_code_keeps_pending_state_for_a_retry(self):
        api = self.mock_api()
        api.login.return_value = ("needs_mfa", None)
        patcher, _ = self.patch_garmin_class(api)

        with patcher:
            garmin.login("nick@example.com", "hunter2")
            pending_before = garmin._pending_mfa
            api.resume_login.side_effect = GarminConnectAuthenticationError("bad code")
            result = garmin.login("nick@example.com", "hunter2", "111111")

        self.assertFalse(result.ok)
        self.assertEqual(result.error, garmin.MFA_FAILED_COPY)
        self.assertTrue(result.needs_mfa, "the code field must stay on screen")
        # Still pending: the next call can retry the code without
        # re-asking for email/password.
        self.assertIs(garmin._pending_mfa, pending_before)
        self.assertFalse(self.token_file().exists())

    def test_mfa_code_with_no_pending_login_is_a_clean_error(self):
        result = garmin.login("nick@example.com", "hunter2", "000000")
        self.assertEqual(
            result,
            garmin.LoginResult(ok=False, needs_mfa=False,
                               error="no Garmin sign-in is waiting for a code"),
        )


# --- is_signed_in() / ever_signed_in() ---------------------------------


class TestIsSignedIn(PuckdGarminTestCase):
    def test_no_tokens_on_disk_is_false(self):
        self.assertFalse(garmin.is_signed_in())
        self.assertFalse(garmin.ever_signed_in())

    def test_a_refresh_token_is_signed_in(self):
        self.write_tokens()
        self.assertTrue(garmin.is_signed_in())
        self.assertTrue(garmin.ever_signed_in())

    def test_an_expired_access_token_with_a_refresh_token_is_still_signed_in(self):
        """garminconnect refreshes an expiring di_token by itself
        (client.py:1378-1419) -- that is not a re-login."""
        self.write_tokens(di_token=_jwt(time.time() - 60))
        self.assertTrue(garmin.is_signed_in())

    def test_an_expired_access_token_with_no_refresh_token_is_false(self):
        self.write_tokens(di_refresh_token=None, di_token=_jwt(time.time() - 60))
        self.assertFalse(garmin.is_signed_in())
        self.assertTrue(garmin.ever_signed_in(), "the file exists: he DID sign in once")

    def test_an_unexpired_access_token_with_no_refresh_token_is_true(self):
        self.write_tokens(di_refresh_token=None, di_token=_jwt(time.time() + 3600))
        self.assertTrue(garmin.is_signed_in())

    def test_malformed_token_file_is_false_not_a_crash(self):
        d = self.token_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.token_file().write_text("not json at all")
        self.assertFalse(garmin.is_signed_in())

    def test_empty_token_object_is_false(self):
        d = self.token_dir()
        d.mkdir(parents=True, exist_ok=True)
        self.token_file().write_text("{}")
        self.assertFalse(garmin.is_signed_in())

    def test_a_non_jwt_access_token_is_false(self):
        self.write_tokens(di_refresh_token=None, di_token="not-a-jwt")
        self.assertFalse(garmin.is_signed_in())


# --- fetch_new() -------------------------------------------------------


class TestFetchNew(PuckdGarminTestCase):
    def setUp(self):
        super().setUp()
        self.write_tokens()
        self.out_dir = Path(self._tmp) / "fits"

    def api_for(self, pages, download=None) -> MagicMock:
        api = self.mock_api()
        if isinstance(pages, list) and pages and isinstance(pages[0], list):
            api.get_activities.side_effect = pages
        else:
            api.get_activities.return_value = pages
        api.download_activity.return_value = (
            _valid_zip_bytes() if download is None else download
        )
        return api

    def test_not_signed_in_raises(self):
        self.token_file().unlink()
        with self.assertRaises(RuntimeError):
            garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

    def test_downloads_original_and_returns_written_paths(self):
        api = self.api_for([_recorded_activity(555001, "2026-09-10 18:32:07")])
        patcher, cls = self.patch_garmin_class(api)
        with patcher:
            got = garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

        self.assertEqual(got, [str(self.out_dir / "555001.zip")])
        self.assertEqual((self.out_dir / "555001.zip").read_bytes(), _valid_zip_bytes())
        # Rebuilt from the stored token, with no credentials at all.
        cls.assert_called_once_with()
        api.login.assert_called_once_with(str(self.token_dir()))
        api.download_activity.assert_called_once_with(555001, dl_fmt=ORIGINAL)
        # <id>.zip is the name daemon.py's _run_garmin() globs for, and no
        # leftover partial-write name is left beside it.
        self.assertFalse((self.out_dir / "555001.zip.part").exists())

    def test_skips_already_seen_ids_without_downloading_again(self):
        self.out_dir.mkdir(parents=True)
        already = self.out_dir / "555000.zip"
        already.write_bytes(b"previously-downloaded-bytes")
        # The daemon's upload marker for it; fetch_new must not disturb it.
        (self.out_dir / "555000.zip.sent").write_text("")

        api = self.api_for([
            _recorded_activity(555001, "2026-09-10 18:32:07"),
            _recorded_activity(555000, "2026-09-09 12:00:00"),
        ])
        patcher, _ = self.patch_garmin_class(api)
        with patcher:
            got = garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

        self.assertEqual(got, [str(self.out_dir / "555001.zip")])
        api.download_activity.assert_called_once_with(555001, dl_fmt=ORIGINAL)
        self.assertEqual(already.read_bytes(), b"previously-downloaded-bytes")
        self.assertTrue((self.out_dir / "555000.zip.sent").exists())

    def test_stops_at_since_iso_and_does_not_download_older(self):
        api = self.api_for([
            _recorded_activity(2, "2026-09-10 18:32:07"),  # newer than since
            _recorded_activity(1, "2026-08-01 00:00:00"),  # older than since
        ])
        patcher, _ = self.patch_garmin_class(api)
        with patcher:
            got = garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

        self.assertEqual(got, [str(self.out_dir / "2.zip")])
        api.download_activity.assert_called_once_with(2, dl_fmt=ORIGINAL)

    def test_paginates_across_pages_stopping_on_a_short_page(self):
        page1 = [_recorded_activity(4, "2026-09-10 00:00:00"),
                 _recorded_activity(3, "2026-09-09 00:00:00")]
        page2 = [_recorded_activity(2, "2026-09-08 00:00:00")]  # short page
        api = self.api_for([page1, page2])
        patcher, _ = self.patch_garmin_class(api)
        with patcher, patch.object(garmin, "LIST_PAGE_SIZE", 2):
            got = garmin.fetch_new("2000-01-01T00:00:00Z", str(self.out_dir))

        self.assertEqual(sorted(got),
                         sorted(str(self.out_dir / f"{i}.zip") for i in (4, 3, 2)))
        starts = [call.args[0] for call in api.get_activities.call_args_list]
        self.assertEqual(starts, [0, 2])

    def test_corrupt_download_raises_and_writes_nothing(self):
        api = self.api_for([_recorded_activity(9, "2026-09-10 18:32:07")],
                           download=b"<html>not a zip</html>")
        patcher, _ = self.patch_garmin_class(api)
        with patcher, self.assertRaises(garmin.GarminDownloadError):
            garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

        self.assertFalse((self.out_dir / "9.zip").exists())
        self.assertFalse((self.out_dir / "9.zip.part").exists())

    def test_empty_download_raises(self):
        api = self.api_for([_recorded_activity(9, "2026-09-10 18:32:07")], download=b"")
        patcher, _ = self.patch_garmin_class(api)
        with patcher, self.assertRaises(garmin.GarminDownloadError):
            garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

    def test_no_activities_returns_empty_list(self):
        api = self.api_for([])
        patcher, _ = self.patch_garmin_class(api)
        with patcher:
            got = garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))
        self.assertEqual(got, [])
        api.download_activity.assert_not_called()

    def test_a_token_garmin_itself_rejects_is_retired_not_left_to_rot(self):
        """CLAUDE.md rule 3: garminconnect's token file records no
        refresh-token expiry, so an expired refresh token is invisible to
        is_signed_in(). If the rejected token stayed on disk, is_signed_in()
        would answer True forever, every fetch would fail silently, and
        daemon.py would never fire "sign in to Garmin again"."""
        api = self.mock_api()
        api.login.side_effect = GarminConnectAuthenticationError("token rejected")
        patcher, _ = self.patch_garmin_class(api)
        with patcher, self.assertRaises(GarminConnectAuthenticationError):
            garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

        self.assertFalse(self.token_file().exists())
        self.assertFalse(garmin.is_signed_in())
        self.assertTrue(self.token_file().with_name(
            self.token_file().name + ".rejected").is_file())

    def test_a_transient_error_does_not_retire_the_token(self):
        api = self.mock_api()
        api.login.side_effect = GarminConnectConnectionError("Max retries exceeded")
        patcher, _ = self.patch_garmin_class(api)
        with patcher, self.assertRaises(GarminConnectConnectionError):
            garmin.fetch_new("2026-09-01T00:00:00Z", str(self.out_dir))

        self.assertTrue(self.token_file().is_file())
        self.assertTrue(garmin.is_signed_in())


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
    """The failure LOOKBACK_S exists for: a ride HAPPENS on Tuesday and
    reaches Garmin Connect on Thursday, when the watch next sees the phone.
    daemon.py advances last_seen to "now" after any successful fetch, so
    Tuesday's activity sorts older than Thursday's watermark and, without
    the window, is skipped silently and forever -- a ride that never arrives
    and nothing anywhere says why (CLAUDE.md rule 3)."""

    def setUp(self):
        super().setUp()
        self.write_tokens()
        self.out_dir = Path(self._tmp) / "fits"

    def _api(self, activities) -> MagicMock:
        api = self.mock_api()
        api.get_activities.return_value = activities
        api.download_activity.return_value = _valid_zip_bytes()
        return api

    def test_an_activity_older_than_last_seen_but_inside_the_window_is_fetched(self):
        api = self._api([_recorded_activity(555002, "2026-09-08 15:00:00")])  # Tuesday
        patcher, _ = self.patch_garmin_class(api)
        with patcher:
            got = garmin.fetch_new("2026-09-10T09:00:00Z", str(self.out_dir))
        self.assertEqual(got, [str(self.out_dir / "555002.zip")])

    def test_the_window_is_bounded_not_a_full_history_rescan(self):
        """Bounded, so the 6-hourly tick never walks the whole account."""
        api = self._api([_recorded_activity(444000, "2026-06-01 10:00:00")])
        patcher, _ = self.patch_garmin_class(api)
        with patcher:
            got = garmin.fetch_new("2026-09-10T09:00:00Z", str(self.out_dir))
        self.assertEqual(got, [], "months old: outside the window, not re-fetched")
        api.download_activity.assert_not_called()

    def test_a_re_listed_activity_is_never_downloaded_twice(self):
        """What makes the window free: the id-on-disk skip already dedupes."""
        self.out_dir.mkdir(parents=True)
        (self.out_dir / "555002.zip").write_bytes(_valid_zip_bytes())
        api = self._api([_recorded_activity(555002, "2026-09-08 15:00:00")])
        patcher, _ = self.patch_garmin_class(api)
        with patcher:
            got = garmin.fetch_new("2026-09-10T09:00:00Z", str(self.out_dir))
        self.assertEqual(got, [])
        api.download_activity.assert_not_called()


class TestNoGarthAnywhere(unittest.TestCase):
    """garth is deprecated upstream and its login endpoint answers 429
    before reading a password (measured 2026-09-13). An accidental
    re-import would put that dead path back in the app bundle, silently."""

    def test_the_module_does_not_import_garth(self):
        source = (PUCKD_DIR / "garmin.py").read_text()
        for line in source.splitlines():
            stripped = line.strip()
            self.assertFalse(stripped.startswith(("import garth", "from garth")), line)
        self.assertNotIn("garth", sys.modules.get("garmin").__dict__)


if __name__ == "__main__":
    unittest.main()
