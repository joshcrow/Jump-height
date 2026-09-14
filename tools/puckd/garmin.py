"""tools/puckd/garmin.py — the watch leg (docs/sync-agent-plan.md's Garmin
paragraph, lines 57-58):

    "Garmin: on every job and every 6 h -- list activities since last_seen,
    download ORIGINAL FIT zips, rclone copy to gdrive:JumpHeight/fits/.
    Never blocks the puck job."

and setup screen 3 (docs/sync-agent-plan.md:37):

    "Your watch    [email] [password] [Sign in] . Skip  (+ a code field
    only if Garmin asks) -> 'Signed in'  [Continue]"
    "Token expiry -> 'Needs you: sign in to Garmin again' opens step 3 alone."

This module owns exactly the calls a caller (the setup page's handler, and
daemon.py's 6-hourly tick) needs and nothing else:

    login(email, password, mfa_code=None) -> LoginResult
    is_signed_in() / ever_signed_in() -> bool
    fetch_new(since_iso, out_dir) -> list[str]
    last_seen(store) / mark_seen(store, iso)

WHY NOT garth ANY MORE (2026-09-13). Garmin changed its login flow in
March 2026. garth's single mobile SSO endpoint now answers 429 Too Many
Requests *before it has looked at a password* -- measured on this Mac
today -- and garth's own author has deprecated the project
(github.com/matin/garth/discussions/222). A client that cannot reach a
credential check cannot sign anybody in, so the import is gone entirely.

WHAT REPLACED IT: `garminconnect` (python-garminconnect) 0.3.15, rebuilt
against Garmin's current web app. Its shape below was read out of the
INSTALLED SOURCE (site-packages/garminconnect/{__init__,client}.py), not
recalled:

  * garminconnect/client.py's Client.login() is a five-strategy chain --
    mobile+cffi, mobile+requests, widget+cffi, portal+cffi,
    portal+requests (client.py:517-527). Only a credential error stops the
    chain; a 429 on one strategy falls through to the next. That is the
    whole reason this module changed: measured here today, the chain gets
    429 on mobile and still reaches Garmin's credential check further
    down, which answered a deliberately fake account with
    "401 Unauthorized (Invalid Username or Password)".
  * Garmin(email, password, return_on_mfa=True).login() returns
    ("needs_mfa", None) instead of blocking on a prompt, and
    Garmin.resume_login(client_state, mfa_code) finishes that same paused
    exchange on the SAME instance (__init__.py:738-743, 907-920, and
    client.py:532-540 where return_on_mfa sets client._mfa_pending). This
    is the two-call shape setup screen 3 needs; see _pending_mfa below.
  * Tokens are persisted by client.dump(path) / client.load(path) as ONE
    file, `garmin_tokens.json`, holding {"di_token", "di_refresh_token",
    "di_client_id"} (client.py:1504-1556, and token_file_path() at
    client.py:59 for the directory -> filename rule this module reuses
    rather than re-deriving). Written 0o600 inside a 0o700 directory by
    garminconnect itself. The PASSWORD IS NEVER PART OF IT.
  * Garmin.get_activities(start, limit) returns the raw camelCase list
    from /activitylist-service/activities/search/activities
    (__init__.py:2382-2418) -- no server-side "since" filter, so
    fetch_new() still pages newest-first and stops at the first activity
    at or before `since_iso`.
  * Garmin.download_activity(id, dl_fmt=Garmin.ActivityDownloadFormat
    .ORIGINAL) returns the as-uploaded zip's bytes (__init__.py:3000-3022).
    Under garth this module had to hardcode that endpoint itself; it no
    longer does, which is why ORIGINAL_DOWNLOAD_PATH is gone.

Token dir = PUCKD_HOME/garmin (the old PUCKD_HOME/garth directory belonged
to a token format garminconnect cannot read, so it is deliberately NOT
reused: a stale garth token pair must read as "not signed in", which is
true, rather than as a corrupt store). PUCKD_HOME defaults to
"~/Library/Application Support/JumpHeight" and is overridable via the
PUCKD_HOME env var so tests never touch a real home directory.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import garminconnect
from garminconnect import (
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)
from garminconnect.client import token_file_path

# --- paths -------------------------------------------------------------

PUCKD_HOME_ENV = "PUCKD_HOME"
DEFAULT_HOME = Path("~/Library/Application Support/JumpHeight").expanduser()
TOKEN_SUBDIR = "garmin"

LIST_PAGE_SIZE = 20  # what Garmin Connect's own web app asks for
MAX_PAGES = 25  # a bounded search, not an infinite one, if since_iso is old

# How far BEFORE last_seen fetch_new() still looks. An activity's
# startTimeGMT is when the RIDE happened; it appears in Garmin Connect only
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


TOKEN_FILENAME = "garmin_tokens.json"


def _token_file() -> Path:
    """The single JSON file garminconnect's own client.dump()/load() use for
    a directory tokenstore -- asked of garminconnect (client.token_file_path)
    rather than hardcoded here, so a rename upstream cannot leave this module
    looking in a directory that nothing writes to (CLAUDE.md §4).

    token_file_path() REJECTS a path with a symlink anywhere in its ancestry
    (client.py:59-87, a deliberate anti-redirect check) by raising
    ValueError. is_signed_in() is called on every daemon tick and must
    answer a bool, so that case falls back to the same name the upstream
    rule produces; garminconnect's own dump()/load() will still refuse such
    a path, loudly, where a refusal belongs."""
    try:
        return Path(token_file_path(str(_token_dir())))
    except ValueError:
        return _token_dir() / TOKEN_FILENAME


# --- login ---------------------------------------------------------------


@dataclass(frozen=True)
class LoginResult:
    ok: bool
    needs_mfa: bool = False
    error: Optional[str] = None


class GarminDownloadError(RuntimeError):
    """fetch_new() got a response that is not a zip for an activity id."""


# A garminconnect login paused on an MFA prompt lives INSIDE the Garmin
# instance that started it: client._mfa_pending plus the half-finished SSO
# session (client.py:532-540, _complete_mfa at client.py:1149), and
# Garmin.resume_login() continues that same object. login()'s public
# signature is (email, password, mfa_code=None) -- there is nowhere in that
# signature to carry the live instance across the two calls setup screen 3
# makes, so it lives here. One desktop app, one setup flow, one signed-in
# Garmin account at a time (docs/sync-agent-plan.md's whole design): a
# second, concurrent login attempt is not a supported case.
_pending_mfa: Optional[dict] = None


LOGIN_FAILED_COPY = "Couldn't sign in. Check your email and password."
GARMIN_BLOCKED_COPY = "Garmin isn't accepting sign-ins right now. Skip for now."
GARMIN_UNREACHABLE_COPY = "Couldn't reach Garmin. Check your connection."
MFA_FAILED_COPY = "That code didn't work. Try again."

# Text markers of a failure that never reached Garmin at all. Matched on the
# message because garminconnect wraps almost everything it catches into its
# own GarminConnectConnectionError (__init__.py:822-828), so by the time an
# exception arrives here the requests.ConnectionError underneath is a string.
_UNREACHABLE_MARKERS = (
    "Max retries exceeded",
    "Failed to establish a new connection",
    "Name or service not known",
    "NameResolution",
    "Temporary failure in name resolution",
    "Network is unreachable",
    "Connection refused",
    "Connection reset",
    "timed out",
    "Read timed out",
)


def _looks_unreachable(exc: BaseException) -> bool:
    name = type(exc).__name__
    # "GarminConnectConnectionError" contains "Connection" and means nothing
    # of the sort, so the library's own wrappers are judged on their text.
    if not name.startswith("GarminConnect") and (
        "Connection" in name or "Timeout" in name
    ):
        return True
    text = str(exc)
    return any(marker in text for marker in _UNREACHABLE_MARKERS)


def _login_error_copy(exc: BaseException) -> str:
    """Four honest sentences for a screen. Measured 2026-09-13: Garmin
    answers the first login strategy with 429 Too Many Requests before it
    has looked at any password, so "check your password" would be a lie for
    a failure that never got that far."""
    text = str(exc)
    if (
        isinstance(exc, GarminConnectTooManyRequestsError)
        or "429" in text
        or "Too Many Requests" in text
        or "403" in text
    ):
        return GARMIN_BLOCKED_COPY
    if (
        isinstance(exc, GarminConnectAuthenticationError)
        or "401" in text
        or "Invalid Username or Password" in text
    ):
        return LOGIN_FAILED_COPY
    if _looks_unreachable(exc):
        return GARMIN_UNREACHABLE_COPY
    if isinstance(exc, GarminConnectConnectionError):
        # "All login strategies exhausted": every route answered SOMETHING
        # that was not a credential check -- an HTML challenge, a WAF page.
        # Garmin was reachable and would not take a sign-in; that is what
        # the blocked copy says, and it is not the password's fault.
        return GARMIN_BLOCKED_COPY
    return LOGIN_FAILED_COPY


def login(email: str, password: str, mfa_code: Optional[str] = None) -> LoginResult:
    """Setup screen 3's [Sign in]. Two shapes of call:

      login(email, password)            -- first attempt
      login(email, password, mfa_code)  -- the code, after needs_mfa=True

    The first call NEVER BLOCKS on an MFA prompt: the Garmin client is
    constructed with return_on_mfa=True, so a code request comes back as
    LoginResult(ok=False, needs_mfa=True) and the window draws its code
    field instead of hanging on a callback that nothing can answer.

    The password is used only for this one SSO exchange and is never
    written to disk. What login() persists on success is garminconnect's
    token file (PUCKD_HOME/garmin/garmin_tokens.json) -- the whole point of
    the token model is that the password never needs to be produced again.
    """
    global _pending_mfa
    try:
        if mfa_code is not None:
            if _pending_mfa is None:
                return LoginResult(
                    ok=False, needs_mfa=False,
                    error="no Garmin sign-in is waiting for a code",
                )
            # The SAME instance that paused: it holds the half-finished SSO
            # session, so the typed email/password are not asked for again
            # (and the second call's copies of them are not used at all).
            api = _pending_mfa["api"]
            api.resume_login(_pending_mfa["client_state"], mfa_code)
            _pending_mfa = None  # only the success path consumes it; a wrong
            # code leaves it in place (client.resume_login keeps _mfa_pending
            # on a bad code, client.py:1620-1633) so the same prompt can be
            # retried without re-asking for email/password.
        else:
            api = garminconnect.Garmin(
                email=email, password=password, return_on_mfa=True,
            )
            # No tokenstore here on purpose: a typed password means "sign me
            # in", not "reuse whatever is on disk" -- Garmin.login(tokenstore)
            # would load an existing token and skip the credentials entirely
            # (__init__.py:680-716).
            status, client_state = api.login()
            if status == "needs_mfa":
                _pending_mfa = {"api": api, "client_state": client_state}
                return LoginResult(ok=False, needs_mfa=True, error=None)
    except Exception as exc:  # noqa: BLE001 -- garminconnect's own exception
        # classes cover only what it recognises. The wire under it is
        # `requests` (and optionally curl_cffi), so a dropped wifi, a DNS
        # failure or a TLS error can arrive as anything, and its SSO flow
        # also parses HTML it did not write (an AttributeError/IndexError the
        # day Garmin changes that page). Either one used to come out of here
        # as a traceback through the setup window's button handler. Every one
        # of them means the same thing on screen and takes the same action.
        # The library's own text (an HTTP status, a URL) is not for a screen
        # -- it goes to stderr, where the daemon log keeps it.
        print(f"garmin login: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        if mfa_code is not None:
            # still pending: the code field stays, and he tries again
            return LoginResult(ok=False, needs_mfa=True, error=MFA_FAILED_COPY)
        return LoginResult(ok=False, needs_mfa=False, error=_login_error_copy(exc))

    token_dir = _token_dir()
    token_dir.mkdir(parents=True, exist_ok=True)
    api.client.dump(str(token_dir))
    # A brand-new token starts with a clean slate: the strike left behind by
    # the token it replaces must not count toward retiring THIS one.
    _clear_auth_strikes()
    # Nothing after this point needs the plaintext password; garminconnect
    # drops its own copy on the non-MFA path (__init__.py:789-792) but not on
    # the two return_on_mfa paths this module uses, so drop it here.
    api.password = None
    return LoginResult(ok=True, needs_mfa=False, error=None)


def ever_signed_in() -> bool:
    """A token file exists at all. The daemon nags about an EXPIRED
    sign-in only; someone who pressed Skip at setup is never asked."""
    return _token_file().exists()


def _jwt_exp(token: str) -> Optional[float]:
    """The `exp` claim of a JWT, read WITHOUT verifying the signature (the
    client has no signing key -- garminconnect does the same thing at
    client.py:263-284 and client.py:1428). None if the token is not a JWT,
    is unreadable, or carries no usable numeric exp."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        exp = payload.get("exp")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        return float(exp)
    except Exception:  # noqa: BLE001 -- an unreadable token is "no expiry
        # I can see", never an exception into a caller that only wants a bool
        return None


def is_signed_in() -> bool:
    """True iff a usable token is on disk -- i.e. a Garmin session can be
    restored without the user typing a password again. Read directly off
    the JSON garminconnect writes (never constructs a live client, never
    makes a network call), so this is safe to call on every job tick
    (docs/sync-agent-plan.md:57) and on every setup-screen re-run
    (docs/sync-agent-plan.md:39) alike.

    "Usable" means: a di_refresh_token (with which garminconnect mints a
    fresh access token by itself, client.py:1378-1419), or failing that a
    di_token whose own JWT exp is still in the future. garminconnect's
    token file records no separate refresh-token expiry, so an expired
    REFRESH token cannot be seen from disk -- that case is caught the only
    place it can be, in _load_api(): a token the API rejects outright is
    retired there so this function starts answering False and daemon.py's
    "sign in to Garmin again" fires instead of nothing happening forever.

    False (never a raised exception) for: no token file, unreadable JSON,
    no tokens in it, or an expired access token with nothing to refresh
    from -- every one of those means the same thing to a caller: show
    [Sign in] again.
    """
    tokens = _read_tokens()
    if tokens is None:
        return False
    if tokens.get("di_refresh_token"):
        return True
    access = tokens.get("di_token")
    if not isinstance(access, str) or not access:
        return False
    exp = _jwt_exp(access)
    if exp is None:
        return False
    return exp > _now_epoch()


def _read_tokens() -> Optional[dict]:
    try:
        data = json.loads(_token_file().read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _now_epoch() -> float:
    return datetime.now(tz=timezone.utc).timestamp()


# --- fetching activities ---------------------------------------------------


def _retire_rejected_tokens() -> None:
    """A stored token Garmin itself refuses is not a sign-in; leaving it on
    disk would keep is_signed_in() answering True forever while every fetch
    failed silently -- the exact shape CLAUDE.md rule 3 forbids. Renamed
    rather than deleted, so the evidence survives for a post-mortem."""
    token = _token_file()
    try:
        token.replace(token.with_name(token.name + ".rejected"))
    except OSError as exc:
        print(f"garmin: could not retire rejected token: {exc}", file=sys.stderr)


AUTH_STRIKES_FILENAME = "auth_strikes.json"
AUTH_STRIKES_TO_RETIRE = 2


def _strikes_file() -> Path:
    return _token_dir() / AUTH_STRIKES_FILENAME


def _auth_strikes() -> int:
    try:
        data = json.loads(_strikes_file().read_text())
    except (OSError, ValueError):
        return 0
    n = data.get("strikes") if isinstance(data, dict) else None
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0


def _record_auth_strike() -> int:
    n = _auth_strikes() + 1
    try:
        _strikes_file().parent.mkdir(parents=True, exist_ok=True)
        _strikes_file().write_text(json.dumps({"strikes": n}))
    except OSError:
        pass
    return n


def _clear_auth_strikes() -> None:
    try:
        _strikes_file().unlink()
    except OSError:
        pass


def _load_api() -> Any:
    """An authenticated Garmin client rebuilt from the stored token. No
    email/password is given to it: with a tokenstore and no credentials,
    garminconnect loads the token, refreshes it if it is close to expiry,
    and verifies it against the API (__init__.py:680-758).

    A rejected token is retired only on the SECOND consecutive refusal.
    The first one is written down and the token is left alone. Reason: an
    expired refresh token and a Garmin that is having a bad hour arrive
    here as the same GarminConnectAuthenticationError -- the unofficial
    route (this module's own docstring) answers a maintenance window with
    whatever it feels like, and garminconnect turns a 401 from any of its
    five strategies into this class. Retiring on the first one costs the
    rider a "sign in to Garmin again" notification, his password and an
    MFA code, to fix a token that was never broken. The daemon's leg runs
    every 6 h, so a genuinely dead token is still retired the same day,
    and a strike is cleared the moment a login succeeds."""
    if not is_signed_in():
        raise RuntimeError("garmin: not signed in (call login() first)")
    api = garminconnect.Garmin()
    try:
        api.login(str(_token_dir()))
    except GarminConnectAuthenticationError:
        strikes = _record_auth_strike()
        if strikes >= AUTH_STRIKES_TO_RETIRE:
            _retire_rejected_tokens()
        else:
            print(f"garmin: token refused ({strikes}/{AUTH_STRIKES_TO_RETIRE}); "
                  "keeping it until the next attempt also fails", file=sys.stderr)
        raise
    _clear_auth_strikes()
    return api


def _parse_iso(iso: str) -> datetime:
    """Parse an ISO-8601 timestamp (accepts a trailing 'Z') to a naive UTC
    datetime, matching the naive timestamps Garmin Connect puts in
    "startTimeGMT" ("2026-09-10 18:32:07" -- already UTC, just not marked
    as such)."""
    text = iso[:-1] + "+00:00" if iso.endswith("Z") else iso
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _activity_start(activity: dict) -> Optional[datetime]:
    raw = activity.get("startTimeGMT")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return _parse_iso(raw)
    except ValueError:
        return None


def fetch_new(since_iso: str, out_dir: "str | Path") -> list:
    """List activities newer than since_iso (less LOOKBACK_S -- see that
    constant: a watch uploads a ride days after it happened) and download each
    one's ORIGINAL FIT zip into out_dir, skipping any activity id whose
    zip is already there. Returns the paths actually written this call
    (already-seen ids are not re-downloaded and are not in the list).

    The <id>.zip naming is the contract daemon.py's _run_garmin() relies on:
    it uploads every *.zip in out_dir that has no sibling <name>.sent marker,
    so a download that happened is re-offered to Drive until Drive confirms.

    Raises RuntimeError if not signed in (callers are expected to check
    is_signed_in() first -- daemon.py's Garmin leg "never blocks the puck
    job" precisely by checking before calling, not by this function
    guessing at a safe default). Raises GarminDownloadError if a download
    comes back not looking like a zip: a corrupt or truncated read must
    never be written to disk and returned as though it succeeded (the
    same "a reading that did not happen is a finding" rule serial_job.py
    applies to the puck's own stats/verify reads).
    """
    api = _load_api()
    since_dt = _parse_iso(since_iso) - timedelta(seconds=LOOKBACK_S)
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    downloaded: list = []
    start = 0
    for _ in range(MAX_PAGES):
        page = api.get_activities(start, LIST_PAGE_SIZE)
        if not page:
            break

        stop = False
        for activity in page:
            started = _activity_start(activity)
            if started is not None and started <= since_dt:
                stop = True
                break

            activity_id = activity.get("activityId")
            if activity_id is None:
                continue
            dest = out_path / f"{activity_id}.zip"
            if dest.exists():
                continue  # already-seen id: not re-downloaded, not returned

            data = api.download_activity(
                activity_id,
                dl_fmt=garminconnect.Garmin.ActivityDownloadFormat.ORIGINAL,
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
