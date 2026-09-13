"""tools/puckd/upload.py — the Google Drive leg of the puck job (spec step 6).

docs/sync-agent-plan.md:41-58 (the job) and :60-66 (the gates) are the
contract. This module owns exactly:

    authorize()      docs/sync-agent-plan.md:49, :83 — setup screen 2's [Connect]
    is_authorized()  same screen's re-run/skip check
    upload()         docs/sync-agent-plan.md:49 — "rclone copy ...; confirm with
                      rclone lsjson that remote size == local size" — and G4
                      (docs/sync-agent-plan.md:64): "a reading that did not
                      happen is a failure: no stats -> no clear; no lsjson ->
                      no clear". The bundle a caller passes in is only ever
                      cleared from the puck (serial_job.py's job, not this
                      module's) once upload() has returned ok=True.

rclone is a subprocess, never a library: its path comes from PUCKD_RCLONE
(an env var so tests and packaging can pin an exact binary) or PATH
(docs/sync-agent-plan.md:78's bundled universal rclone at runtime). Nothing
here parses or writes rclone's config file directly — "gdrive" is created
and read the same way a person would from a terminal, so `rclone config
create` / `listremotes` / `copy` / `lsjson` are the only vocabulary.

No decision here is a judgment call: ok is True only when a size measured
on the remote (via lsjson, never trusted from copy's own exit code) equals
the size measured on local disk. Every other path — rclone missing, copy
failing, lsjson failing, lsjson silent about the file, a size mismatch —
returns ok=False with `error` naming which measurement was missing or
wrong, never a bare False.
"""

from __future__ import annotations

import json
import re
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

REMOTE_NAME = "gdrive"
RCLONE_ENV = "PUCKD_RCLONE"

_AUTHORIZE_TIMEOUT_S = 300.0  # a person has to look at a browser and click
_LISTREMOTES_TIMEOUT_S = 30.0
_COPY_TIMEOUT_S = 900.0  # a ride bundle can be large; a puck is not fast USB
_LSJSON_TIMEOUT_S = 60.0


@dataclass(frozen=True)
class UploadResult:
    ok: bool
    remote_size: Optional[int]
    local_size: Optional[int]
    error: Optional[str]


class RcloneNotFound(RuntimeError):
    """Neither PUCKD_RCLONE nor PATH names an rclone binary."""


def _rclone_bin() -> str:
    explicit = os.environ.get(RCLONE_ENV)
    if explicit:
        return explicit
    found = shutil.which("rclone")
    if found:
        return found
    raise RcloneNotFound(
        f"no rclone binary: set {RCLONE_ENV} or put rclone on PATH"
    )


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess:
    """Run rclone with args. Raises RcloneNotFound / subprocess.TimeoutExpired
    (both are measurements that did not happen); a plain nonzero exit is
    returned, not raised, so callers can read stdout/stderr."""
    bin_path = _rclone_bin()
    return subprocess.run(
        [bin_path, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


_AUTH_TEMPLATE = Path(__file__).resolve().parent / "assets" / "oauth-done.html"
_CLIENT_FILE = Path(__file__).resolve().parent / "assets" / "google-client.json"
_ABOUT_URL = "https://www.googleapis.com/drive/v3/about?fields=user"
_FILES_URL = "https://www.googleapis.com/drive/v3/files"


def google_client() -> dict:
    """JumpHeight Sync's own OAuth client (assets/google-client.json:
    client_id, client_secret, scope, share_with). rclone's shared client is
    retired during 2026; this is the one Google issued to the project
    jump-height-508521 on 2026-09-13."""
    try:
        return json.loads(_CLIENT_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _token_from_authorize_output(text: str) -> "Optional[str]":
    """`rclone authorize` prints the token between two marker lines:
        Paste the following into your remote machine --->
        {"access_token": ...}
        <---End paste
    """
    m = re.search(r"--->\s*(\{.*?\})\s*<---", text, re.S)
    if m:
        return m.group(1).strip()
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("{") and "access_token" in line:
            return line
    return None


def authorize(timeout: float = _AUTHORIZE_TIMEOUT_S) -> bool:
    """Google consent, then the "gdrive" remote. Two rclone calls:

      rclone authorize drive <client_id> <client_secret> --template <ours>
          opens the browser, listens on 127.0.0.1 for Google's redirect,
          renders OUR page there ("Connected. You can close this tab.")
          and prints the token. With our own client the consent screen
          says "JumpHeight Sync" and the scope is drive.file: the app can
          only ever see files it created itself.
      rclone config create gdrive drive client_id .. client_secret ..
          scope drive.file token <json>   stores it, non-interactively.

    True only if both exited 0 and a token was actually printed.
    """
    client = google_client()
    cid, secret = client.get("client_id"), client.get("client_secret")
    scope = client.get("scope") or "drive"
    auth_args = ["authorize", "drive"]
    if cid and secret:
        auth_args += [cid, secret]
    auth_args += ["--template", str(_AUTH_TEMPLATE)]
    try:
        proc = _run(auth_args, timeout=timeout)
    except (RcloneNotFound, subprocess.TimeoutExpired, OSError):
        return False
    if proc.returncode != 0:
        return False
    token = _token_from_authorize_output(proc.stdout or "")
    if token is None:
        return False
    create_args = ["config", "create", REMOTE_NAME, "drive", "scope", scope]
    if cid and secret:
        create_args += ["client_id", cid, "client_secret", secret]
    create_args += ["token", token, "--non-interactive"]
    try:
        proc = _run(create_args, timeout=60.0)
    except (RcloneNotFound, subprocess.TimeoutExpired, OSError):
        return False
    return proc.returncode == 0


def _folder_id(remote_dir: str) -> "Optional[str]":
    """Drive's id for gdrive:<remote_dir> (rclone lsjson reports IDs)."""
    parent, _, name = remote_dir.strip("/").rpartition("/")
    spec = f"{REMOTE_NAME}:{parent}" if parent else f"{REMOTE_NAME}:"
    try:
        proc = _run(["lsjson", "--dirs-only", spec], timeout=_LSJSON_TIMEOUT_S)
    except (RcloneNotFound, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        for e in json.loads(proc.stdout or "[]"):
            if e.get("Name") == name and e.get("IsDir"):
                return e.get("ID")
    except ValueError:
        pass
    return None


def _default_post_json(url: str, bearer: str, body: dict) -> dict:
    import urllib.request
    from puckd import netctx
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Authorization": f"Bearer {bearer}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15.0, context=netctx.ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def ensure_shared(remote_dir: str = "JumpHeight", email: "Optional[str]" = None,
                  post_json=_default_post_json) -> bool:
    """Give `email` (default: the owner in google-client.json) editor access
    to the top-level Drive folder the app created, so the rides land where
    Josh can reach them without anyone pressing Share. Idempotent: Drive
    treats a repeated grant as a no-op. Never raises; True when Drive
    accepted the grant."""
    email = email or google_client().get("share_with")
    if not email:
        return False
    fid = _folder_id(remote_dir)
    token = _stored_access_token()
    if not fid or not token:
        return False
    try:
        post_json(f"{_FILES_URL}/{fid}/permissions?sendNotificationEmail=false", token,
                  {"role": "writer", "type": "user", "emailAddress": email})
        return True
    except Exception:  # noqa: BLE001 -- a convenience, not a gate
        return False


def _stored_access_token() -> "Optional[str]":
    try:
        # Any real call makes rclone refresh an expired token and write the
        # new one to its config; `about` is the cheapest.
        _run(["about", f"{REMOTE_NAME}:", "--json"], timeout=_LISTREMOTES_TIMEOUT_S)
        proc = _run(["config", "dump"], timeout=_LISTREMOTES_TIMEOUT_S)
    except (RcloneNotFound, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        remote = json.loads(proc.stdout or "{}").get(REMOTE_NAME) or {}
        token = remote.get("token")
        if isinstance(token, str):
            token = json.loads(token)
        return (token or {}).get("access_token")
    except (ValueError, AttributeError):
        return None


def _default_fetch_json(url: str, bearer: str) -> dict:
    import urllib.request
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {bearer}"})
    from puckd import netctx
    with urllib.request.urlopen(req, timeout=10.0, context=netctx.ssl_context()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def account_email(fetch_json=_default_fetch_json) -> "Optional[str]":
    """The signed-in Google account, read back from Drive's `about` with
    the token rclone stored. The Drive scope covers it. None when it
    cannot be read; never raises. The screen then says "Connected to
    Google Drive" instead of naming the account."""
    token = _stored_access_token()
    if not token:
        return None
    try:
        user = (fetch_json(_ABOUT_URL, token) or {}).get("user") or {}
        email = user.get("emailAddress")
        return email if isinstance(email, str) and "@" in email else None
    except Exception:  # noqa: BLE001 -- a label, not a gate
        return None


def is_authorized() -> bool:
    """True iff "gdrive" already exists in rclone's remotes.

    Used to decide whether setup screen 2 shows [Connect] or "Connected as
    ..." on a re-run (docs/sync-agent-plan.md:36, :39). Never raises: a
    missing rclone binary or a failing `listremotes` reads as "not
    authorized", not as an error — G4's silent-failure-is-a-finding rule is
    for the sync job's gates, not for a setup screen deciding which button
    to show.
    """
    try:
        proc = _run(["listremotes"], timeout=_LISTREMOTES_TIMEOUT_S)
    except (RcloneNotFound, subprocess.TimeoutExpired, OSError):
        return False
    if proc.returncode != 0:
        return False
    remotes = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    return f"{REMOTE_NAME}:" in remotes


def upload(local_path: str | Path, remote_dir: str) -> UploadResult:
    """Copy local_path to gdrive:<remote_dir> and verify it landed (G1/G4).

    ok is True ONLY when:
      1. `rclone copy` exits 0, AND
      2. `rclone lsjson` on remote_dir exits 0 and parses, AND
      3. it lists a non-directory entry named local_path's basename, AND
      4. that entry's Size equals local_path's size on disk.

    Any other outcome — rclone missing, copy failing, lsjson failing or
    returning unparseable output, the file absent from the listing, or a
    size mismatch (the short-remote-size case) — is ok=False with `error`
    set to which measurement failed. remote_size is populated whenever
    lsjson yielded *a* size for the file, even a wrong one, so a caller can
    log local vs. remote; it is None only when no listing was ever read.
    """
    local = Path(local_path)
    if not local.is_file():
        return UploadResult(ok=False, remote_size=None, local_size=None,
                             error=f"local file not found: {local}")
    local_size = local.stat().st_size

    trimmed = str(remote_dir).strip("/")
    remote_spec = f"{REMOTE_NAME}:{trimmed}" if trimmed else f"{REMOTE_NAME}:"

    try:
        copy_proc = _run(["copy", str(local), remote_spec], timeout=_COPY_TIMEOUT_S)
    except (RcloneNotFound, OSError) as exc:
        # OSError: PUCKD_RCLONE names a binary that is not there (verified
        # 2026-09-12 to surface as FileNotFoundError from subprocess).
        return UploadResult(ok=False, remote_size=None, local_size=local_size,
                             error=str(exc))
    except subprocess.TimeoutExpired:
        return UploadResult(ok=False, remote_size=None, local_size=local_size,
                             error="rclone copy timed out")

    if copy_proc.returncode != 0:
        return UploadResult(
            ok=False, remote_size=None, local_size=local_size,
            error=f"rclone copy failed (exit {copy_proc.returncode}): "
                  f"{copy_proc.stderr.strip()}",
        )

    # copy exiting 0 is never trusted on its own (G4) — the remote is only
    # believed via a fresh lsjson read.
    try:
        ls_proc = _run(["lsjson", remote_spec], timeout=_LSJSON_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        return UploadResult(ok=False, remote_size=None, local_size=local_size,
                             error="rclone lsjson did not run")

    if ls_proc.returncode != 0:
        return UploadResult(
            ok=False, remote_size=None, local_size=local_size,
            error=f"rclone lsjson failed (exit {ls_proc.returncode}): "
                  f"{ls_proc.stderr.strip()}",
        )

    try:
        entries = json.loads(ls_proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        return UploadResult(ok=False, remote_size=None, local_size=local_size,
                             error=f"rclone lsjson returned unparseable output: {exc}")

    match = next(
        (e for e in entries if isinstance(e, dict)
         and e.get("Name") == local.name and not e.get("IsDir")),
        None,
    )
    if match is None:
        return UploadResult(
            ok=False, remote_size=None, local_size=local_size,
            error=f"{local.name} not found in remote listing after copy",
        )

    remote_size = match.get("Size")
    if remote_size != local_size:
        return UploadResult(
            ok=False, remote_size=remote_size, local_size=local_size,
            error=f"remote size {remote_size!r} != local size {local_size}",
        )

    return UploadResult(ok=True, remote_size=remote_size, local_size=local_size,
                         error=None)
