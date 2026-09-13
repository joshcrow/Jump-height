#!/usr/bin/env python3
"""tools/puckd/setup/server.py — the local HTTP server behind the four
setup screens (docs/sync-agent-plan.md, "Setup — four screens, one button
each, run once. Local web page opened by the app.").

Serves the three static files in this directory (index.html, setup.css,
setup.js) at both `/` and `/setup` — the latter because
tools/puckd/menubar.py:33's `DEFAULT_SETUP_URL` is
"http://127.0.0.1:17888/setup", and a page served with no trailing slash
resolves its own relative `setup.css`/`setup.js` references to `/setup.css`
and `/setup.js` (RFC 3986 merge), which this server also answers — so the
menu bar's "Set up…" opens this page with zero extra wiring. `serve()`
therefore also defaults to port 17888; tests always pass `port=0` instead, so
a socket already held by a running daemon never fails a test run.

Three small JSON endpoints the page's own setup.js posts to
(docs/sync-agent-plan.md, "server.py" build note):

    POST /api/google/start   — calls the injected `google_authorize()`.
                                {"ok": true, "account": "<email>"} or
                                {"ok": false, "account": null, "error": "…"}.
    POST /api/garmin/login   — calls the injected `garmin_login(email,
                                password, mfa_code)`.
                                {"ok": true} | {"ok": false, "needs_mfa":
                                true} | {"ok": false, "error": "…"}.
    GET  /api/status         — this run's own idea of what is already
                                connected, so a re-run from the menu bar
                                (spec: "Re-runnable from the menu bar; each
                                step individually") does not ask again for
                                what a previous run of THIS PROCESS already
                                established.
    POST /api/notify/request — calls the injected `notify_permission()`
                                when screen 4 shows. Fire-and-forget: "the
                                macOS notification prompt fires here; no
                                copy of ours" (spec, screen 4) — the response
                                is always {"ok": true} regardless of what the
                                hook did, because the screen has nothing to
                                say about it either way.

WIRING, DELIBERATELY LEFT TO THE CALLER. `google_authorize`, `garmin_login`
and `notify_permission` are required keyword arguments with no default that
reaches into a sibling module — `upload.py`, `garmin.py` and `notify.py` are
built by other agents in parallel (CLAUDE.md: "never edit a file you were not
assigned"), and this file must be fully constructible and fully testable
whether or not they exist yet, or ever import cleanly. Three things were
actually measured by reading those files once they appeared, recorded here
so whoever composes daemon.py does not have to re-discover them:

  * `garmin.login(email, password, mfa_code=None) -> LoginResult` (a
    dataclass with `.ok` / `.needs_mfa` / `.error`) matches this endpoint's
    contract exactly — `garmin_login=garmin.login` composes with no adapter.
    `_field()` below reads either that dataclass or a plain dict, so a fake
    built as a dict in a test and the real dataclass are handled the same
    way.
  * `upload.authorize() -> bool` (tools/puckd/upload.py) does NOT return an
    account string — it is rclone's own `config create` exit code, and
    nothing in that module reads back a signed-in email. This endpoint's
    `google_authorize` contract is "returns the account label, raises on
    failure" (what screen 2's "Connected as …" needs); wiring the real
    function needs an adapter in front of it (or a new call in upload.py) to
    produce that label — not built here, since upload.py is not this file's
    to extend. Left as an open question rather than papered over with an
    invented value.
  * `notify.py` (tools/puckd/notify.py) has no notification-*permission*
    call at all — it only sends the three already-granted notifications via
    `osascript`. There is currently no real function to wire
    `notify_permission` to; a caller supplies one (e.g. a pyobjc
    `UNUserNotificationCenter.requestAuthorization` call) or a no-op.
"""

from __future__ import annotations

import json
import threading
import urllib.parse
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

SETUP_DIR = Path(__file__).resolve().parent
DEFAULT_PORT = 17888  # matches tools/puckd/menubar.py:33's DEFAULT_SETUP_URL

# Keyed by exact request path, never by directory listing — this server has
# exactly three files to hand out and no reason to accept a path at all.
STATIC_FILES: dict[str, tuple[str, str]] = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/setup": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/setup.css": ("setup.css", "text/css; charset=utf-8"),
    "/setup.js": ("setup.js", "text/javascript; charset=utf-8"),
}

GoogleAuthorize = Callable[[], str]
GarminLogin = Callable[[str, str, Optional[str]], Any]
NotifyPermission = Callable[[], None]


def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Read `name` off `obj` whether it is a mapping (a test's plain dict)
    or an attribute-bearing object (garmin.LoginResult, a real dataclass) —
    so a fake and the real return type are handled identically."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class SetupState:
    """What this run of the wizard has established, read back by
    GET /api/status. Deliberately in-memory and per-process: the two things
    it tracks (a Google account, a Garmin sign-in) are also independently
    checkable at any time via `upload.is_authorized()` / `garmin.is_signed_in()`
    on the real modules — this is only this page's own short-term memory of
    what happened during the run in front of it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._google_account: Optional[str] = None
        self._garmin_signed_in: bool = False

    def set_google_account(self, account: str) -> None:
        with self._lock:
            self._google_account = account

    def set_garmin_signed_in(self, value: bool) -> None:
        with self._lock:
            self._garmin_signed_in = value

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "google_account": self._google_account,
                "garmin_signed_in": self._garmin_signed_in,
            }


def make_handler(
    *,
    google_authorize: GoogleAuthorize,
    garmin_login: GarminLogin,
    notify_permission: NotifyPermission,
    state: Optional[SetupState] = None,
) -> type[BaseHTTPRequestHandler]:
    """Build a BaseHTTPRequestHandler bound to these three injectables and
    this state — a fresh class per call (a closure, not a module global) so
    a test can spin up several independently-configured servers side by
    side without one's fakes leaking into another's."""

    state = state if state is not None else SetupState()

    class Handler(BaseHTTPRequestHandler):
        server_version = "JumpHeightSetup/1"

        def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A003
            pass  # a passing test run should print nothing about it

        # ------------------------------------------------------- plumbing --

        def _send_json(self, obj: dict, status: int = 200) -> None:
            body = json.dumps(obj).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length") or "0")
            raw = self.rfile.read(length) if length else b""
            if not raw:
                return {}
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return {}
            return parsed if isinstance(parsed, dict) else {}

        def _send_static(self, path: str) -> None:
            entry = STATIC_FILES.get(path)
            if entry is None:
                self._send_json({"error": "not found"}, status=404)
                return
            filename, content_type = entry
            data = (SETUP_DIR / filename).read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        # ----------------------------------------------------------- verbs --

        def do_GET(self) -> None:  # noqa: N802 — BaseHTTPRequestHandler's own naming
            # `self.path` carries the query string verbatim (e.g.
            # "/setup?step=3" for the menu bar's re-run-one-step links) —
            # every route below matches on the PATH alone, never the raw
            # string, or "?step=3" would 404 a page that loads perfectly
            # fine with no query at all.
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/status":
                self._send_json(state.snapshot())
                return
            self._send_static(path)

        def do_POST(self) -> None:  # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/google/start":
                self._handle_google_start()
            elif path == "/api/garmin/login":
                self._handle_garmin_login()
            elif path == "/api/notify/request":
                self._handle_notify_request()
            else:
                self._send_json({"error": "not found"}, status=404)

        # --------------------------------------------------------- routes --

        def _handle_google_start(self) -> None:
            try:
                account = google_authorize()
            except Exception as exc:  # noqa: BLE001 — any failure becomes the
                # screen's own error text; the rider/owner has no other way
                # to read a raised exception from a background HTTP call.
                self._send_json({"ok": False, "account": None, "error": str(exc)})
                return
            if not account:
                self._send_json({
                    "ok": False, "account": None,
                    "error": "Google sign-in did not complete.",
                })
                return
            state.set_google_account(account)
            self._send_json({"ok": True, "account": account, "error": None})

        def _handle_garmin_login(self) -> None:
            body = self._read_json()
            email = body.get("email", "") or ""
            password = body.get("password", "") or ""
            mfa_code = body.get("mfa_code") or None
            try:
                result = garmin_login(email, password, mfa_code)
            except Exception as exc:  # noqa: BLE001 — see _handle_google_start
                self._send_json({"ok": False, "needs_mfa": False, "error": str(exc)})
                return
            ok = bool(_field(result, "ok", False))
            needs_mfa = bool(_field(result, "needs_mfa", False))
            error = _field(result, "error", None)
            if ok:
                state.set_garmin_signed_in(True)
            elif not needs_mfa and not error:
                error = "Garmin sign-in did not complete."
            self._send_json({"ok": ok, "needs_mfa": needs_mfa, "error": error})

        def _handle_notify_request(self) -> None:
            try:
                notify_permission()
            except Exception:  # noqa: BLE001 — fire-and-forget either way;
                # screen 4 has no copy of ours regardless of the outcome
                # (docs/sync-agent-plan.md, screen 4).
                pass
            self._send_json({"ok": True})

    return Handler


def serve(
    *,
    google_authorize: GoogleAuthorize,
    garmin_login: GarminLogin,
    notify_permission: NotifyPermission,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    state: Optional[SetupState] = None,
) -> tuple[ThreadingHTTPServer, int]:
    """Start the setup server on a background daemon thread. Returns
    `(httpd, port)` — callers hold `httpd` only to shut it down later
    (`httpd.shutdown(); httpd.server_close()`); `port` is the real bound
    port, which matters when `port=0` asked for any free one."""
    handler = make_handler(
        google_authorize=google_authorize,
        garmin_login=garmin_login,
        notify_permission=notify_permission,
        state=state,
    )
    httpd = ThreadingHTTPServer((host, port), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _wire_production() -> dict:
    """Import the real sibling modules for a standalone run (`python3
    server.py`). Imported here, lazily, and ONLY from `main()` — never from
    module load or from `make_handler`/`serve` — so this file's public API
    has zero import-time coupling to modules another agent may still be
    writing."""
    import sys

    sys.path.insert(0, str(SETUP_DIR.parent))  # tools/puckd/ — flat modules,
    # no package __init__.py, same convention as the rest of tools/ (e.g.
    # tools/fake_device.py's own `sys.path.insert(0, ... / "tools")`).
    import garmin  # tools/puckd/garmin.py
    import upload  # tools/puckd/upload.py

    def google_authorize() -> str:
        # upload.authorize() returns bool, not an account label — see the
        # module docstring's second measured note. Until upload.py grows a
        # call that returns one, a real run reports the fixed remote name
        # rather than inventing an email nobody asked rclone for.
        if not upload.authorize():
            raise RuntimeError("Google sign-in did not complete.")
        return upload.REMOTE_NAME

    def notify_permission() -> None:
        pass  # no real permission call exists yet — see the module docstring

    return {
        "google_authorize": google_authorize,
        "garmin_login": garmin.login,
        "notify_permission": notify_permission,
    }


def main() -> None:  # pragma: no cover — exercised by a person, not pytest
    httpd, port = serve(**_wire_production(), port=DEFAULT_PORT)
    print(f"JumpHeight setup server on http://127.0.0.1:{port}/setup")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        httpd.shutdown()
        httpd.server_close()


if __name__ == "__main__":  # pragma: no cover
    main()
