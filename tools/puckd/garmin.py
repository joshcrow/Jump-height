"""tools/puckd/garmin.py — the watch leg (docs/sync-agent-plan.md's Garmin
paragraph, lines 57-58):

    "Garmin: on every job and every 6 h -- garth: list activities since
    last_seen, download ORIGINAL FIT zips, rclone copy to
    gdrive:JumpHeight/fits/. Never blocks the puck job."

and setup screen 3 (docs/sync-agent-plan.md:37):

    "Your watch    [email] [password] [Sign in] . Skip  (+ a code field
    only if Garmin asks) -> 'Signed in'  [Continue]"
    "Token expiry -> 'Needs you: sign in to Garmin again' opens step 3 alone."

This module owns exactly the four calls a caller (the setup page's handler,
and daemon.py's 6-hourly tick) needs and nothing else:

    login(email, password, mfa_code=None) -> LoginResult
    is_signed_in() -> bool
    fetch_new(since_iso, out_dir) -> list[str]
    last_seen(store) / mark_seen(store, iso)

garth (https://github.com/matin/garth) is the only Garmin client library the
build is allowed to use. Its shape, verified against the installed
garth==0.8.0 by reading its source directly (not from memory, since garth's
own README is thin and it is explicitly deprecated upstream):

  * garth.sso.login(email, password, client=c, return_on_mfa=True) returns
    either (OAuth1Token, OAuth2Token) on success, or the literal string
    "needs_mfa" as result[0] with a client_state dict as result[1]
    (garth/sso.py's login()).
  * garth.sso.resume_login(client_state, mfa_code) finishes that paused
    flow and returns (OAuth1Token, OAuth2Token) (garth/sso.py's
    resume_login()).
  * A garth.http.Client's tokens are persisted with client.dump(dir) /
    client.load(dir) as oauth1_token.json / oauth2_token.json
    (garth.http.OAUTH1_TOKEN_FILE / OAUTH2_TOKEN_FILE) -- plain JSON, no
    encryption, which is exactly why the password itself is never written
    anywhere by this module.
  * OAuth2Token.refresh_token_expires_at (garth/http.py) is the field that
    determines whether garth can still mint new access tokens without the
    user signing in again; is_signed_in() reads it directly rather than
    constructing a live Client, so it never makes a network call.
  * garth.Activity.list(limit=, start=, client=) (garth/data/activity.py)
    pages the activity list but has no server-side date filter, so
    fetch_new() paginates newest-first and stops at the first activity at
    or before `since_iso`.
  * There is no garth helper for the ORIGINAL-format download; garth.http
    .Client.download() is a generic GET-and-return-bytes. The endpoint
    path used here (ORIGINAL_DOWNLOAD_PATH) is the one every unofficial
    Garmin Connect client (python-garminconnect, GarminDB, ...) uses for
    it -- garth itself does not document or test it. This path is
    UNVERIFIED against a live account (see not_done in the build report).

Token dir = PUCKD_HOME/garth, where PUCKD_HOME defaults to
"~/Library/Application Support/JumpHeight" and is overridable via the
PUCKD_HOME env var so tests never touch a real home directory.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import garth

# --- paths -------------------------------------------------------------

PUCKD_HOME_ENV = "PUCKD_HOME"
DEFAULT_HOME = Path("~/Library/Application Support/JumpHeight").expanduser()
TOKEN_SUBDIR = "garth"

# Community-documented Garmin Connect endpoint for the as-uploaded
# ("ORIGINAL") activity file, always zipped by the service even when the
# original upload was a single .fit. Not part of garth's public API --
# see the module docstring.
ORIGINAL_DOWNLOAD_PATH = "/download-service/files/activity/{activity_id}"

LIST_PAGE_SIZE = 20  # garth.Activity.list's own default
MAX_PAGES = 25  # a bounded search, not an infinite one, if since_iso is old

# How far BEFORE last_seen fetch_new() still looks. An activity's
# start_time_gmt is when the RIDE happened; it appears in Garmin Connect only
# when the watch next syncs to the phone, which can be days later. daemon.py
# advances last_seen to "now" after any successful fetch, so without this
# window a ride that started on Tuesday and uploaded on Thursday -- the
# ordinary case for a watch that only syncs when the phone is nearby -- sorts
# older than last_seen and is never downloaded, silently, forever. Re-listing
# a week costs a page or two; re-DOWNLOADING costs nothing, because the
# `dest.exists()` skip below already dedupes by activity id.
LOOKBACK_S = 7 * 24 * 3600

# First run: no last_seen yet recorded. Far enough in the past that "list
# activities since last_seen" means "all of them" without a magic sentinel.
DEFAULT_SINCE_ISO = "1970-01-01T00:00:00Z"


def puckd_home() -> Path:
    override = os.environ.get(PUCKD_HOME_ENV)
    return Path(override).expanduser() if override else DEFAULT_HOME


def _token_dir() -> Path:
    return puckd_home() / TOKEN_SUBDIR


def _oauth2_token_path() -> Path:
    return _token_dir() / "oauth2_token.json"


def _oauth1_token_path() -> Path:
    return _token_dir() / "oauth1_token.json"


# --- login ---------------------------------------------------------------


@dataclass(frozen=True)
class LoginResult:
    ok: bool
    needs_mfa: bool = False
    error: Optional[str] = None


class GarminDownloadError(RuntimeError):
    """fetch_new() got a response that is not a zip for an activity id."""


# A garth SSO login paused on an MFA prompt returns a client_state dict
# (the live client + the in-progress login params) that must be handed
# back into garth.sso.resume_login() with the code. login()'s public
# signature is (email, password, mfa_code=None) -- there is nowhere in
# that signature to carry the paused state across the two calls setup
# screen 3 makes, so it lives here. One desktop app, one setup flow, one
# signed-in Garmin account at a time (docs/sync-agent-plan.md's whole
# design): a second, concurrent login attempt is not a supported case.
_pending_mfa: Optional[dict] = None


LOGIN_FAILED_COPY = "Couldn't sign in. Check your email and password."
MFA_FAILED_COPY = "That code didn't work. Try again."


def login(email: str, password: str, mfa_code: Optional[str] = None) -> LoginResult:
    """Setup screen 3's [Sign in]. Two shapes of call:

      login(email, password)            -- first attempt
      login(email, password, mfa_code)  -- the code, after needs_mfa=True

    The password is used only to construct this one garth SSO exchange; it
    is never written to disk. What login() persists on success is the
    garth token pair (oauth1_token.json / oauth2_token.json under
    PUCKD_HOME/garth) -- the whole point of garth's token model is that
    the password never needs to be produced again.
    """
    global _pending_mfa
    try:
        if mfa_code is not None:
            if _pending_mfa is None:
                return LoginResult(
                    ok=False, needs_mfa=False,
                    error="no Garmin sign-in is waiting for a code",
                )
            client = _pending_mfa["client"]
            oauth1, oauth2 = garth.sso.resume_login(_pending_mfa, mfa_code)
            _pending_mfa = None  # only the success path consumes it; a
            # wrong code leaves it in place so the same code prompt can be
            # retried without re-asking for email/password.
        else:
            client = garth.Client()
            result = garth.sso.login(
                email, password, client=client, return_on_mfa=True,
            )
            if result[0] == "needs_mfa":
                _pending_mfa = result[1]
                return LoginResult(ok=False, needs_mfa=True, error=None)
            oauth1, oauth2 = result
    except Exception:  # noqa: BLE001 -- garth.exc.GarthException is only the
        # errors garth RAISES ITSELF. The wire under it is `requests`, so a
        # dropped wifi, a DNS failure or a TLS error arrives as
        # requests.ConnectionError, and garth's SSO flow also parses HTML it
        # did not write (an AttributeError/IndexError the day Garmin changes
        # that page -- this route "has broken and been fixed before", the
        # module docstring). Either one used to come out of here as a
        # traceback through the setup window's button handler. Every one of
        # them means the same thing on screen and takes the same action.
        # garth's own text (an HTTP status, a URL) is not for a screen.
        if mfa_code is not None:
            # still pending: the code field stays, and he tries again
            return LoginResult(ok=False, needs_mfa=True, error=MFA_FAILED_COPY)
        return LoginResult(ok=False, needs_mfa=False, error=LOGIN_FAILED_COPY)

    client.oauth1_token, client.oauth2_token = oauth1, oauth2
    token_dir = _token_dir()
    token_dir.mkdir(parents=True, exist_ok=True)
    client.dump(str(token_dir))
    return LoginResult(ok=True, needs_mfa=False, error=None)


def ever_signed_in() -> bool:
    """A token file exists at all. The daemon nags about an EXPIRED
    sign-in only; someone who pressed Skip at setup is never asked."""
    return _oauth1_token_path().exists() or _oauth2_token_path().exists()


def is_signed_in() -> bool:
    """True iff a token pair is on disk and its refresh token has not
    expired -- i.e. garth can still mint fresh access tokens without the
    user typing a password again. Read directly off the JSON garth writes
    (never constructs a live Client, never makes a network call), so this
    is safe to call on every job tick (docs/sync-agent-plan.md:57) and on
    every setup-screen re-run (docs/sync-agent-plan.md:39) alike.

    False (never a raised exception) for: no token files, unreadable
    JSON, a missing expiry field, or an expired refresh token -- every one
    of those means the same thing to a caller: show [Sign in] again.
    """
    oauth1_path = _oauth1_token_path()
    oauth2_path = _oauth2_token_path()
    if not oauth1_path.is_file() or not oauth2_path.is_file():
        return False
    try:
        oauth2 = json.loads(oauth2_path.read_text())
    except (OSError, ValueError):
        return False
    expires_at = oauth2.get("refresh_token_expires_at")
    if not isinstance(expires_at, (int, float)):
        return False
    return expires_at > _now_epoch()


def _now_epoch() -> float:
    return datetime.now(tz=timezone.utc).timestamp()


# --- fetching activities ---------------------------------------------------


def _load_client() -> Any:
    if not is_signed_in():
        raise RuntimeError("garmin: not signed in (call login() first)")
    client = garth.Client()
    client.load(str(_token_dir()))
    return client


def _parse_iso(iso: str) -> datetime:
    """Parse an ISO-8601 timestamp (accepts a trailing 'Z') to a naive UTC
    datetime, matching the naive datetimes garth attaches to
    Activity.start_time_gmt (Garmin's API sends "startTimeGMT" with no
    offset -- it is already UTC, just not marked as such)."""
    text = iso[:-1] + "+00:00" if iso.endswith("Z") else iso
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def fetch_new(since_iso: str, out_dir: "str | Path") -> list:
    """List activities newer than since_iso (less LOOKBACK_S -- see that
    constant: a watch uploads a ride days after it happened) and download each
    one's ORIGINAL FIT zip into out_dir, skipping any activity id whose
    zip is already there. Returns the paths actually written this call
    (already-seen ids are not re-downloaded and are not in the list).

    Raises RuntimeError if not signed in (callers are expected to check
    is_signed_in() first -- daemon.py's Garmin leg "never blocks the puck
    job" precisely by checking before calling, not by this function
    guessing at a safe default). Raises GarminDownloadError if a download
    comes back not looking like a zip: a corrupt or truncated read must
    never be written to disk and returned as though it succeeded (the
    same "a reading that did not happen is a finding" rule serial_job.py
    applies to the puck's own stats/verify reads).
    """
    client = _load_client()
    since_dt = _parse_iso(since_iso) - timedelta(seconds=LOOKBACK_S)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    downloaded: list = []
    start = 0
    for _ in range(MAX_PAGES):
        page = garth.Activity.list(limit=LIST_PAGE_SIZE, start=start, client=client)
        if not page:
            break

        stop = False
        for activity in page:
            started = getattr(activity, "start_time_gmt", None)
            if started is not None and started <= since_dt:
                stop = True
                break

            activity_id = activity.activity_id
            dest = out_path / f"{activity_id}.zip"
            if dest.exists():
                continue  # already-seen id: not re-downloaded, not returned

            data = client.download(
                ORIGINAL_DOWNLOAD_PATH.format(activity_id=activity_id)
            )
            if not data or not data.startswith(b"PK"):
                raise GarminDownloadError(
                    f"activity {activity_id}: response is not a zip "
                    f"({len(data) if data else 0} bytes)"
                )
            tmp = dest.with_suffix(dest.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(dest)
            downloaded.append(str(dest))

        if stop or len(page) < LIST_PAGE_SIZE:
            break
        start += LIST_PAGE_SIZE

    return downloaded


# --- last_seen bookkeeping --------------------------------------------------


def last_seen(store: "str | Path") -> str:
    """The ISO timestamp fetch_new() should search after. DEFAULT_SINCE_ISO
    (the epoch) on first run or if `store` is missing/unreadable -- never
    raises, since "no state yet" is a normal first call, not a failure."""
    path = Path(store)
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return DEFAULT_SINCE_ISO
    iso = data.get("last_seen")
    return iso if isinstance(iso, str) and iso else DEFAULT_SINCE_ISO


def mark_seen(store: "str | Path", iso: str) -> None:
    """Persist `iso` as the new last_seen. Written to a temp file and
    renamed into place so a crash mid-write leaves the previous value
    intact rather than a half-written store (docs/sync-agent-plan.md's G3
    is stated for the puck spool, but "an interrupted write must never
    corrupt state" is the same rule applied here)."""
    path = Path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"last_seen": iso}))
    tmp.replace(path)
