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


def authorize(timeout: float = _AUTHORIZE_TIMEOUT_S) -> bool:
    """Set up the "gdrive" remote (docs/sync-agent-plan.md:36's [Connect]).

    Runs `rclone config create gdrive drive scope drive` with no client_id
    or token supplied — rclone's normal (non-headless) behavior for that is
    to open the browser to Google's consent screen and listen on
    127.0.0.1 for the redirect itself, "so the browser consent returns to
    localhost automatically" (docs/sync-agent-plan.md:49) with no port
    forwarding or copy-pasted code. Returns True only if rclone exits 0 —
    consent completed and the remote is now in rclone's config.
    """
    try:
        proc = _run(
            ["config", "create", REMOTE_NAME, "drive", "scope", "drive"],
            timeout=timeout,
        )
    except (RcloneNotFound, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


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
    except (RcloneNotFound, subprocess.TimeoutExpired):
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
    except RcloneNotFound as exc:
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
    except subprocess.TimeoutExpired:
        return UploadResult(ok=False, remote_size=None, local_size=local_size,
                             error="rclone lsjson timed out")

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
