#!/usr/bin/env python3
"""tools/ride_loop.py — the ride watcher on the OWNER's Mac (this machine).

`tools/puckd` (JumpHeight Sync, docs/sync-agent-plan.md) runs on Nick's Mac
and uploads three things to HIS OWN Google Drive, inside a "JumpHeight"
folder it shares with the owner the first time anything confirms
(`upload.ensure_shared`, `tools/puckd/upload.py`):

    JumpHeight/inbox/   ride bundles — the puck's own zip, unchanged, the
                         exact shape `./tools/jump ingest` already reads
                         (`tools/puckd/daemon.py:86-88`, `INBOX_DIR`)
    JumpHeight/fits/    Garmin ORIGINAL FIT zips, one `<id>_ACTIVITY.fit`
                         per zip, from python-garminconnect
                         (`tools/puckd/daemon.py:88`, `FITS_DIR`)
    JumpHeight/log/     `daemon.log` + `status.json`, overwritten on every
                         publish (`tools/puckd/daemon.py:413-495`, `LOG_DIR`)

This script runs here, on the owner's Mac, under a launchd `StartInterval`
(`packaging/com.jumpheight.rideloop.plist`) rather than a persistent daemon
— it does one pass and exits. Each pass:

  1. reads those three folders through a SEPARATE, READ-ONLY rclone remote
     scoped to "Shared with me" (`gdrive-ro`) — Nick's files were never
     granted to any other remote the owner owns, and the flag that finds
     them, `--drive-shared-with-me`, has to be on every call into that
     space, not only the listing (rclone's Drive backend treats it as
     changing which root a path is resolved against).
  2. copies anything new into `data/incoming/` (bundles) and
     `data/incoming/fits/` (Garmin zips), tracked in `data/incoming/seen.json`
     so nothing is re-pulled every 10 minutes forever.
  3. runs `./tools/jump ingest <zip>` on each new bundle — the SAME importer
     `docs/STATUS.md`'s "Mac sync agent" row already proved end to end —
     attaches whichever cached Garmin fit's own recorded activity window
     (read via `tools/fitread.py`, not upload time) overlaps the session's
     trace window, then `./tools/jump score <session>` (docs/accuracy-plan.md's
     loop), tolerating its absence or failure — it is a live, separately-
     built subcommand, not this script's to gate on.
  4. regenerates `data/corpus.md`, uploads it plus any new `score.md` to a
     SECOND, write-capable remote (`gdrive`, the owner's own app-created
     remote, `docs/STATUS.md`'s "Mac sync agent" `google-client.json`
     account) under `JumpHeight/reports/`.
  5. fires at most ONE macOS notification.

Nothing here is a G1-style gate the way `tools/puckd`'s G1-G5 are — this
script only ever COPIES from Nick's shared folder and WRITES into this
repo's own `data/` tree, so there is nothing destructive to guard against.
The discipline that DOES carry over (CLAUDE.md rules 2-3): a reading that
did not happen is logged as exactly that, `run_cycle()` never raises out to
its caller, and every external call — rclone, `./tools/jump`,
`tools/fitread.py`, `osascript`, `launchctl` — is an injectable seam so
`tools/tests/test_ride_loop.py` runs entirely against fakes.

    python3 tools/ride_loop.py            one pass, quiet (what launchd runs)
    python3 tools/ride_loop.py --once     one pass, plus a short report on stdout
    python3 tools/ride_loop.py --install  write the LaunchAgent plist and load it
                                           (the owner runs this by hand — never
                                           run it from an automated session)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parent.parent

# ------------------------------------------------------------------ rclone

RCLONE_ENV = "RIDE_LOOP_RCLONE"  # same idea as tools/puckd/upload.py's
# PUCKD_RCLONE — an explicit env var beats PATH so tests (and, one day,
# a packaged build) can pin an exact binary. Deliberately a DIFFERENT env
# var: this script and tools/puckd run as separate processes with separate
# rclone configs (Nick's account vs the owner's two remotes) and must never
# accidentally share one binary override meant for the other.

_LISTREMOTES_TIMEOUT_S = 30.0
_LSJSON_TIMEOUT_S = 60.0
_COPY_TIMEOUT_S = 900.0    # a full-region Garmin/ride zip is a few MB, not huge,
                          # but a slow home connection is not this script's problem
_INGEST_TIMEOUT_S = 300.0
_SCORE_TIMEOUT_S = 600.0
_FITREAD_TIMEOUT_S = 60.0
_BOOT_RESET_DROP_S = 1.0        # a backward step in trace `t` larger than this is a reboot (sim/score.py uses the same 1 s)
_MIN_FIT_ACTIVITY_S = 300.0     # shorter than this is a false start, not a ride (measured: a 0.7 s, 1-record windsurfing FIT on 2026-08-22)
# Sports that are never the ride. Everything else (windsurfing, kiteboarding,
# surfing, generic/track_me, or a summary with no sport at all) stays eligible.
_NEVER_THE_RIDE_SPORTS = frozenset({
    "walking", "hiking", "running", "swimming", "cycling", "jump_rope",
    "training", "inline_skating", "alpine_skiing", "kayaking", "rowing",
})


class RcloneNotFound(RuntimeError):
    """Neither RIDE_LOOP_RCLONE nor PATH names an rclone binary."""


def _rclone_bin() -> str:
    explicit = os.environ.get(RCLONE_ENV)
    if explicit:
        return explicit
    found = shutil.which("rclone")
    if found:
        return found
    raise RcloneNotFound(f"no rclone binary: set {RCLONE_ENV} or put rclone on PATH")


def rclone_missing_reason() -> "Optional[str]":
    """None when a runnable rclone exists; otherwise WHY there is none.

    Every rclone caller below turns a missing binary into the same False /
    None that a disconnected remote produces, which is how eight hours of
    "gdrive-ro not ready ... `rclone config reconnect`" got logged for a
    fault that was neither (see _run_cycle). This is the one place that can
    tell the two apart, and it answers before any call is attempted."""
    explicit = os.environ.get(RCLONE_ENV)
    if explicit:
        if os.path.isfile(explicit) and os.access(explicit, os.X_OK):
            return None
        return (f"{RCLONE_ENV}={explicit!r} is not a runnable file "
                f"(exists={os.path.exists(explicit)})")
    if shutil.which("rclone"):
        return None
    return (f"{RCLONE_ENV} is unset and no `rclone` is on PATH "
            f"(PATH={os.environ.get('PATH', '')!r})")


def _rclone(args: "list[str]", timeout: float) -> subprocess.CompletedProcess:
    """Run rclone with args. Raises RcloneNotFound / subprocess.TimeoutExpired
    / OSError — all three are "a reading that did not happen" (CLAUDE.md rule
    3); every caller below catches them and logs, never lets one escape."""
    return subprocess.run([_rclone_bin(), *args], capture_output=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def remote_authorized(remote: str) -> bool:
    """True iff `remote:` is one of rclone's configured remotes. False (never
    raised) on a missing binary, a timeout, or a nonzero exit — all of those
    read as "not ready yet", exactly like the top-of-file gate this backs."""
    try:
        proc = _rclone(["listremotes"], timeout=_LISTREMOTES_TIMEOUT_S)
    except (RcloneNotFound, subprocess.TimeoutExpired, OSError):
        return False
    if proc.returncode != 0:
        return False
    names = {ln.strip() for ln in proc.stdout.splitlines() if ln.strip()}
    return f"{remote}:" in names


def list_shared_dir(remote: str, remote_dir: str) -> "Optional[list[dict]]":
    """`rclone lsjson <remote>:<remote_dir> --drive-shared-with-me`, parsed.
    None (never raised) on ANY failure — missing binary, timeout, nonzero
    exit, or output that is not a JSON list — so callers can treat "the
    listing did not happen" as one uniform case."""
    spec = f"{remote}:{remote_dir}"
    try:
        proc = _rclone(["lsjson", spec, "--drive-shared-with-me"],
                       timeout=_LSJSON_TIMEOUT_S)
    except (RcloneNotFound, subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        entries = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return None
    return entries if isinstance(entries, list) else None


# ---------------------------------------------------------------- config

@dataclass
class Config:
    """Every path and every external call this script makes, gathered in
    one place so tools/tests/test_ride_loop.py can override just the seams
    it needs (same shape as tools/puckd/daemon.py's DaemonConfig)."""

    repo_dir: Path
    data_dir: Path
    incoming_dir: Path       # data/incoming — new ride bundle zips
    fits_cache_dir: Path     # data/incoming/fits — new + previously-seen Garmin zips
    rider_log_dir: Path      # data/rider-log — Nick's daemon.log + status.json
    sessions_dir: Path       # data/sessions — where `jump ingest` writes
    seen_path: Path          # data/incoming/seen.json — the copy ledger
    log_path: Path           # data/ride_loop.log
    corpus_path: Path        # data/corpus.md

    ro_remote: str = "gdrive-ro"
    rw_remote: str = "gdrive"
    inbox_dir: str = "JumpHeight/inbox"
    fits_dir: str = "JumpHeight/fits"
    log_dir: str = "JumpHeight/log"
    reports_dir: str = "JumpHeight/reports"

    # ./tools/jump, as an argv prefix — [sys.executable, ".../tools/jump"] by
    # default, overridable so tests point at a fake script that mimics
    # `ingest`/`score` without running the real detector/analysis stack.
    jump_argv: "list[str]" = field(default_factory=list)
    fitread_argv: "list[str]" = field(default_factory=list)

    notifier: "Callable[[str, Optional[str]], None]" = None  # filled below


LOG_FILES = ("daemon.log", "status.json")


def default_config(repo_dir: "Optional[Path]" = None) -> Config:
    repo_dir = Path(repo_dir) if repo_dir else REPO
    data_dir = repo_dir / "data"
    incoming_dir = data_dir / "incoming"
    return Config(
        repo_dir=repo_dir,
        data_dir=data_dir,
        incoming_dir=incoming_dir,
        fits_cache_dir=incoming_dir / "fits",
        rider_log_dir=data_dir / "rider-log",
        sessions_dir=data_dir / "sessions",
        seen_path=incoming_dir / "seen.json",
        log_path=data_dir / "ride_loop.log",
        corpus_path=data_dir / "corpus.md",
        jump_argv=[sys.executable, str(repo_dir / "tools" / "jump")],
        fitread_argv=[sys.executable, str(repo_dir / "tools" / "fitread.py")],
        notifier=osascript_notify,
    )


# -------------------------------------------------------------------- log

def log(cfg: Config, message: str) -> None:
    """Append one UTF-8 line to data/ride_loop.log and echo it to stderr
    (so `--once` and launchd's own StandardErrorPath both show it). Never
    raises: a log write failing must not be how a cycle dies."""
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}  {message}"
    try:
        cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
        with cfg.log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, file=sys.stderr)


# ------------------------------------------------------------------ seen

def load_seen(cfg: Config) -> dict:
    try:
        return json.loads(cfg.seen_path.read_text())
    except (OSError, ValueError):
        return {}


def save_seen(cfg: Config, seen: dict) -> None:
    cfg.seen_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.seen_path.write_text(json.dumps(seen, indent=2, sort_keys=True) + "\n")


def _entry_key(remote_dir: str, entry: dict) -> str:
    """Drive's file ID survives a rename; a size-qualified name is the
    fallback for a backend (the test fake, an alias remote) that doesn't
    report one. Prefixed with remote_dir so the same ledger safely tracks
    both inbox/ and fits/ with zero risk of a name colliding across them."""
    eid = entry.get("ID")
    if eid:
        return f"id:{eid}"
    return f"name:{remote_dir}/{entry.get('Name')}:{entry.get('Size')}"


def sync_new_entries(cfg: Config, remote_dir: str, local_dir: Path,
                     entries: "list[dict]", seen: dict) -> "list[Path]":
    """Copy every *.zip in `entries` not already in `seen` into `local_dir`,
    oldest ModTime first (so a burst of bundles ingests in the order it
    happened, and "the newest Garmin fit" below has a stable population to
    search). Mutates `seen` in place; caller saves it once per cycle.

    A file already sitting in `local_dir` with the SAME size self-heals into
    `seen` rather than being re-copied — belt and braces if the ledger is
    ever lost while the copies are not.

    `seen` tracks the REMOTE COPY, not what `./tools/jump ingest` does with
    it afterwards: a bundle that fails verification stays under
    data/incoming/ for the owner to inspect or re-run with --force by hand.
    It is not re-fetched (the fetch already succeeded) and not silently
    retried (CLAUDE.md rule 3 — a retry that quietly changes nothing every
    10 minutes is its own kind of silent failure)."""
    local_dir.mkdir(parents=True, exist_ok=True)
    new_paths: "list[Path]" = []
    for entry in sorted(entries, key=lambda e: e.get("ModTime") or ""):
        if entry.get("IsDir"):
            continue
        name = entry.get("Name") or ""
        if not name.lower().endswith(".zip"):
            continue
        key = _entry_key(remote_dir, entry)
        dest = local_dir / name
        if key not in seen:
            if dest.is_file() and dest.stat().st_size == entry.get("Size"):
                seen[key] = {"name": name, "size": entry.get("Size"),
                            "modtime": entry.get("ModTime"), "self_healed": True}
                continue
        else:
            continue
        spec = f"{cfg.ro_remote}:{remote_dir}/{name}"
        try:
            proc = _rclone(["copyto", spec, str(dest), "--drive-shared-with-me"],
                           timeout=_COPY_TIMEOUT_S)
        except (RcloneNotFound, subprocess.TimeoutExpired, OSError) as exc:
            log(cfg, f"copy failed for {spec}: {exc!r}")
            continue
        if proc.returncode != 0:
            log(cfg, f"copy failed for {spec}: exit {proc.returncode}: "
                     f"{(proc.stderr or '').strip()[-200:]}")
            continue
        seen[key] = {"name": name, "size": entry.get("Size"), "modtime": entry.get("ModTime")}
        new_paths.append(dest)
    return new_paths


def _hash_if_exists(p: Path) -> "Optional[str]":
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def sync_rider_log(cfg: Config, log_entries: "list[dict]") -> bool:
    """Copy log/daemon.log and log/status.json from the shared folder into
    data/rider-log/, overwriting every cycle — they are small, and rclone
    itself is the diff each time, so there is no ledger for these two.

    Returns True iff DAEMON.LOG's content changed — i.e. Nick's app actually
    logged something — which is what "Nick: log updated" is for. NOT
    status.json: tools/puckd/daemon.py's publish_log() stamps it with
    `"written": datetime.now()` (daemon.py:466-468) and run_forever() calls
    publish_log() unconditionally on every SPOOL_RETRY_INTERVAL_S tick
    (daemon.py:112 = 600 s), so status.json's BYTES change every ten minutes
    whether or not anything happened. This script's own cycle is also ten
    minutes (packaging/com.jumpheight.rideloop.plist, StartInterval 600), so
    hashing status.json into this verdict meant a macOS notification every
    ten minutes, all day and all night, on the owner's Mac. status.json is
    still copied every cycle — it is what Josh reads for battery, version and
    last_job — it just no longer fires a notification by itself.

    A file simply not present yet in log_entries (Nick's puckd has never
    published) is routine, not an error."""
    by_name = {e.get("Name"): e for e in log_entries if not e.get("IsDir")}
    cfg.rider_log_dir.mkdir(parents=True, exist_ok=True)
    changed = False
    for name in LOG_FILES:
        entry = by_name.get(name)
        if entry is None:
            continue
        dest = cfg.rider_log_dir / name
        old_hash = _hash_if_exists(dest)
        tmp = dest.with_name(dest.name + ".tmp")
        spec = f"{cfg.ro_remote}:{cfg.log_dir}/{name}"
        try:
            proc = _rclone(["copyto", spec, str(tmp), "--drive-shared-with-me"],
                           timeout=_COPY_TIMEOUT_S)
        except (RcloneNotFound, subprocess.TimeoutExpired, OSError) as exc:
            log(cfg, f"rider-log copy failed for {name}: {exc!r}")
            continue
        if proc.returncode != 0:
            log(cfg, f"rider-log copy failed for {name}: exit {proc.returncode}: "
                     f"{(proc.stderr or '').strip()[-150:]}")
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        new_hash = _hash_if_exists(tmp)
        os.replace(tmp, dest)
        if new_hash != old_hash and name == "daemon.log":
            changed = True
    return changed


# ------------------------------------------------------------ prerequisites

def check_prerequisites(cfg: Config) -> "tuple[bool, dict]":
    """The task's own gate: 'if rclone listremotes lacks it [gdrive-ro] or
    lsjson fails: log one line and exit 0.'

    Applied to the REMOTE and to inbox/ only — NOT to all three folders.
    MEASURED 2026-09-14 with rclone v1.75.1: `rclone lsjson <remote>:<dir>`
    on a directory that does not exist exits 3 ("directory not found"), not
    0-with-an-empty-list, so list_shared_dir() reads a never-created folder
    exactly like an authorization failure. Gating the whole cycle on all
    three then has a permanent failure mode: puckd creates JumpHeight/fits/
    only when a Garmin FIT actually uploads, and a rider who skipped Garmin
    at setup never uploads one (tools/puckd/daemon.py:747-750, "Skipped
    Garmin at setup -> never signed in -> never nagged"). His rides would
    pile up in inbox/ while this script logged "not ready yet" every ten
    minutes, forever — a silent failure wearing a routine face (CLAUDE.md
    rule 3).

    The readings themselves tell the two cases apart, so that is what gates:

      * EVERY folder failed -> the remote, not a folder, is the problem.
        That is the spec's case, and it gets the spec's answer: one line,
        exit 0, retry next interval. MEASURED on this Mac 2026-09-14:
        `gdrive-ro` IS in `rclone listremotes` (so remote_authorized() says
        True) and yet every lsjson exits 1 with "empty token found - please
        run rclone config reconnect" — listremotes checks that a remote is
        CONFIGURED, never that it is CONNECTED, so this all-failed case is
        the only thing standing between a half-set-up remote and a cycle
        that thinks Nick's Drive is simply empty.
      * SOME folder failed -> that folder, not the remote. It comes back as
        None for the caller to log as a reading that did not happen
        (CLAUDE.md rule 3) and then read as empty. Nothing this script does
        with a listing is destructive — it only ever copies INTO this repo,
        never deletes, and the seen-ledger is never pruned by absence — so
        reading one as empty can cost at most a ten-minute delay. The three
        folders genuinely appear at three different moments (log/ on puckd's
        first publish_log(), inbox/ on its first confirmed upload, fits/
        only if Garmin is ever signed in), so "all three or nothing" would
        be a gate on a coincidence.

    Returns (ok, {"inbox": [...]|None, "fits": ..., "log": ...}) so nothing
    below lsjson's the same folder twice in one cycle."""
    if not remote_authorized(cfg.ro_remote):
        return False, {}
    listings: dict = {}
    for key, remote_dir in (("inbox", cfg.inbox_dir), ("fits", cfg.fits_dir),
                            ("log", cfg.log_dir)):
        listings[key] = list_shared_dir(cfg.ro_remote, remote_dir)
    if all(v is None for v in listings.values()):
        return False, {}
    return True, listings


# ------------------------------------------------------------------ ingest

_SESSION_LINE_RE = re.compile(r"Session written to:\s*(.+?)\s*$", re.M)


def run_ingest(cfg: Config, zip_path: Path) -> "Optional[Path]":
    """`./tools/jump ingest <zip>`, then read the session directory back out
    of its OWN stdout ('\\n📦 Session written to: <path>', tools/jump's
    cmd_ingest) rather than re-deriving the name from the manifest a second
    time — one place decides that name, and it is cmd_ingest's, not this
    script's to duplicate and risk drifting from."""
    try:
        proc = subprocess.run(cfg.jump_argv + ["ingest", str(zip_path)],
                              cwd=str(cfg.repo_dir), capture_output=True,
                              text=True, timeout=_INGEST_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(cfg, f"ingest raised for {zip_path.name}: {exc!r}")
        return None
    for line in (proc.stdout or "").splitlines():
        log(cfg, f"ingest: {line}")
    for line in (proc.stderr or "").splitlines():
        log(cfg, f"ingest[stderr]: {line}")
    if proc.returncode != 0:
        log(cfg, f"ingest {zip_path.name} failed (exit {proc.returncode})")
        return None
    m = _SESSION_LINE_RE.search(proc.stdout or "")
    if not m:
        log(cfg, f"ingest {zip_path.name} exited 0 but named no session dir — "
                 "a reading that did not happen (CLAUDE.md rule 3)")
        return None
    sess = Path(m.group(1))
    if not sess.is_dir():
        log(cfg, f"ingest {zip_path.name} named {sess}, which does not exist")
        return None
    return sess


def run_score(cfg: Config, session_dir: Path) -> "Optional[Path]":
    """`./tools/jump score <session>`, tolerated on any nonzero exit — the
    task's own words: the subcommand may not exist yet (another agent is
    registering it concurrently) or may legitimately decline a session
    (no trace, no alignment). Either way this is not ride_loop's failure to
    raise on; it is logged, and score.md simply stays absent."""
    try:
        proc = subprocess.run(cfg.jump_argv + ["score", str(session_dir)],
                              cwd=str(cfg.repo_dir), capture_output=True,
                              text=True, timeout=_SCORE_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError) as exc:
        log(cfg, f"score raised for {session_dir.name}: {exc!r}")
        return None
    for line in (proc.stdout or "").splitlines():
        log(cfg, f"score: {line}")
    if proc.returncode != 0:
        log(cfg, f"score exited {proc.returncode} for {session_dir.name} "
                 "(tolerated — docs/accuracy-plan.md, not yet always available)")
        for line in (proc.stderr or "").splitlines():
            log(cfg, f"score[stderr]: {line}")
    md = session_dir / "score.md"
    return md if md.is_file() else None


# ------------------------------------------------------------ Garmin match

def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def session_trace_window(session_dir: Path) -> "Optional[tuple[datetime, datetime]]":
    """The session's coarse wall-clock span: session.json's `trace_epoch_utc`
    (the wall clock at trace t=0) plus trace.csv's own min/max `t`.

    Only the LAST boot's rows count. trace.csv is a flash ring buffer that
    survives a power cycle, and `trace_epoch_utc` pins the boot that was
    running at sync and no other (sim/score.py's Timebase, the excluded-rows
    rule). Rows before the last backward step in `t` belong to an earlier
    boot with an unknown epoch: adding THEIR `t` to this epoch is not a wide
    window, it is a wrong one. Measured 2026-09-15 across the 24 dated
    sessions (docs/garmin-corpus-2026-09-15.md, finding 10): with min/max
    over every row, 20260910-103108-E2C4 got an 81.3 h window that put the
    real 09-09 ride outside it, and one window ended 2026-09-17 — in the
    future. A reset is any drop in `t` larger than `_BOOT_RESET_DROP_S`;
    the sub-second jitter the scorer also records is not one."""
    try:
        sess = json.loads((session_dir / "session.json").read_text())
    except (OSError, ValueError):
        return None
    epoch = sess.get("trace_epoch_utc")
    if not epoch:
        return None
    try:
        epoch_dt = _parse_iso(epoch)
    except ValueError:
        return None
    tmin = tmax = prev = None
    try:
        with (session_dir / "trace.csv").open() as f:
            next(f, None)  # header: "t,mag"
            for line in f:
                cell = line.split(",", 1)[0].strip()
                if not cell:
                    continue
                try:
                    t = float(cell)
                except ValueError:
                    continue
                if prev is not None and prev - t > _BOOT_RESET_DROP_S:
                    tmin = tmax = None      # a new boot: forget the old one
                prev = t
                tmin = t if tmin is None else min(tmin, t)
                tmax = t if tmax is None else max(tmax, t)
    except OSError:
        return None
    if tmin is None:
        return None
    return epoch_dt + timedelta(seconds=tmin), epoch_dt + timedelta(seconds=tmax)


def fit_activity_summary(cfg: Config, fit_path: Path) -> "Optional[dict]":
    """`tools/fitread.py --out <tmp>`'s fit-summary.json for one zip: the
    activity's own start_utc/end_utc plus, when the reader supplies them,
    sport/sub_sport/records. Everything `fit_activity_window()` and
    `pick_matching_fit()` know about a FIT comes through here.
    (the project's already-tested FIT reader, docs/STATUS.md, 41 tests) run
    as a SUBPROCESS rather than imported — `fitread.read_fit_bytes()` calls
    `sys.exit(2)` on a malformed input (its own documented contract), which
    would kill this whole process if called in-line against a corrupt or
    still-uploading zip. Shelling out is the same "one place owns the
    analysis" precedent `./tools/jump score`/`replay`/`eval` already use for
    sim/score.py, sim/run.py and sim/evaluate.py."""
    cache_key = None
    try:
        st = fit_path.stat()
        cache_key = f"{fit_path.name}:{st.st_size}:{int(st.st_mtime)}"
        cached = _summary_cache_get(cfg, cache_key)
        if cached is not None:
            return cached
    except OSError:
        pass
    summary = _fit_activity_summary_uncached(cfg, fit_path)
    if summary is not None and cache_key is not None:
        _summary_cache_put(cfg, cache_key, summary)
    return summary


_SUMMARY_CACHE_NAME = ".fit-summaries.json"


def _summary_cache_path(cfg: Config) -> Path:
    return cfg.fits_cache_dir / _SUMMARY_CACHE_NAME


def _summary_cache_get(cfg: Config, key: str) -> "Optional[dict]":
    """A FIT never changes once written, so its summary is keyed on
    name+size+mtime and kept next to the zips. Without this the picker ran
    tools/fitread.py once per cached zip per NEW session — 338 subprocesses
    on 2026-09-15, minutes per session — and the every-ride loop paid it
    every cycle a bundle arrived."""
    try:
        return json.loads(_summary_cache_path(cfg).read_text()).get(key)
    except (OSError, ValueError, AttributeError):
        return None


def _summary_cache_put(cfg: Config, key: str, summary: dict) -> None:
    path = _summary_cache_path(cfg)
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}
    data[key] = summary
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    except OSError:
        pass


def _fit_activity_summary_uncached(cfg: Config, fit_path: Path) -> "Optional[dict]":
    with tempfile.TemporaryDirectory(prefix="ride_loop_fit_") as td:
        try:
            proc = subprocess.run(cfg.fitread_argv + [str(fit_path), "--out", td],
                                  capture_output=True, text=True,
                                  timeout=_FITREAD_TIMEOUT_S)
        except (subprocess.TimeoutExpired, OSError) as exc:
            log(cfg, f"fitread raised for {fit_path.name}: {exc!r}")
            return None
        if proc.returncode != 0:
            log(cfg, f"fitread exited {proc.returncode} for {fit_path.name}: "
                     f"{(proc.stderr or '').strip()[-200:]}")
            return None
        try:
            summary = json.loads((Path(td) / "fit-summary.json").read_text())
        except (OSError, ValueError) as exc:
            log(cfg, f"fitread for {fit_path.name}: unreadable fit-summary.json: {exc!r}")
            return None
    return summary if isinstance(summary, dict) else None


def _summary_window(summary: "Optional[dict]") -> "Optional[tuple[datetime, datetime]]":
    if not summary:
        return None
    start, end = summary.get("start_utc"), summary.get("end_utc")
    if not start or not end:
        return None
    try:
        return _parse_iso(start), _parse_iso(end)
    except ValueError:
        return None


def fit_activity_window(cfg: Config, fit_path: Path) -> "Optional[tuple[datetime, datetime]]":
    """The FIT's own recorded start/end (see fit_activity_summary)."""
    return _summary_window(fit_activity_summary(cfg, fit_path))


def _overlap_seconds(a: "tuple[datetime, datetime]", b: "tuple[datetime, datetime]") -> float:
    """Seconds the two windows share; 0.0 when they only touch or miss."""
    lo = max(a[0], b[0])
    hi = min(a[1], b[1])
    return max(0.0, (hi - lo).total_seconds())


def _windows_overlap(a: "tuple[datetime, datetime]", b: "tuple[datetime, datetime]") -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def pick_matching_fit(cfg: Config, session_window: "tuple[datetime, datetime]",
                      fit_paths: "list[Path]") -> "Optional[Path]":
    """The fit among `fit_paths` whose OWN recorded activity window shares the
    MOST time with `session_window`; ties (including two windows that only
    touch at an endpoint, 0 s of shared time) go to the newest end_utc. Both
    windows are read from the activity itself, never from a zip's upload
    mtime — Garmin's sync to the phone, and the phone's to Drive, can lag the
    ride by hours.

    Most-overlap rather than plain newest-overlap because a session window is
    long and a Garmin activity is short: the puck's trace spans everything
    between the first and last motion-gate opening of the day, which on the
    rider's own 2026-09-14 file is 417 min (data/sessions/20260914-210637-E2C4,
    25,039 s of span). Any activity recorded that day overlaps it. "Newest
    overlapping" then attaches the evening dog walk to the morning's foil
    session — silently, and into a corpus whose whole purpose is accuracy
    comparison. Shared duration picks the activity actually recorded
    alongside the trace.

    Three gates, each from a measured mis-pick (docs/garmin-corpus-2026-09-15.md,
    finding 10): shared time must be > 0 s (on 2026-08-22 the old rule attached
    a 0.7-second, 1-record `windsurfing` false start on 0.0 s of shared time);
    the activity must be at least `_MIN_FIT_ACTIVITY_S` long (same file); and
    a sport that is never the ride (`_NEVER_THE_RIDE_SPORTS`) is skipped, so
    the day's dog walk cannot win on overlap. A summary with no sport at all
    stays eligible — absence of a tag is not evidence of a walk."""
    best: "Optional[tuple[Path, float, datetime]]" = None
    for p in fit_paths:
        summary = fit_activity_summary(cfg, p)
        w = _summary_window(summary)
        if w is None:
            continue
        sport = (summary.get("sport") or "").lower()
        if sport in _NEVER_THE_RIDE_SPORTS:
            continue
        if (w[1] - w[0]).total_seconds() < _MIN_FIT_ACTIVITY_S:
            continue
        shared = _overlap_seconds(session_window, w)
        if shared <= 0:
            continue
        if best is None or (shared, w[1]) > (best[1], best[2]):
            best = (p, shared, w[1])
    return best[0] if best else None


def install_garmin_fit(session_dir: Path, fit_zip_path: Path) -> bool:
    """Unzip fit_zip_path's single `<id>_ACTIVITY.fit` into
    `<session>/garmin.fit` — docs/sync-agent-plan.md's Garmin leg: 'the zip
    holds one <id>_ACTIVITY.fit'."""
    try:
        with zipfile.ZipFile(fit_zip_path) as zf:
            names = [n for n in zf.namelist()
                    if n.upper().endswith("_ACTIVITY.FIT") and not n.startswith("__MACOSX/")]
            if len(names) != 1:
                return False
            data = zf.read(names[0])
    except (OSError, zipfile.BadZipFile, KeyError):
        return False
    try:
        (session_dir / "garmin.fit").write_bytes(data)
    except OSError:
        return False
    return True


# --------------------------------------------------------------- corpus.md

def _session_puck4(session_dir: Path, session_json: dict) -> str:
    unit = session_json.get("unit") or ""
    if "-" in unit:
        return unit.rsplit("-", 1)[-1]
    # A bench-derived session.json can carry unit=null; the directory's own
    # name still ends in web/sync/CONTRACT.md §4's PUCK4 when it came from a
    # bundle (tools/jump's _ingest_session_dir_name). 4 uppercase-hex-ish
    # characters after the last '-' is the shape; anything else is UNKN
    # rather than a guess.
    tail = session_dir.name.rsplit("-", 1)[-1] if "-" in session_dir.name else ""
    return tail if len(tail) == 4 else "UNKN"


def _session_when(session_dir: Path) -> str:
    m = re.match(r"^(\d{8})-(\d{6})", session_dir.name)
    if not m:
        return session_dir.name
    try:
        dt = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return session_dir.name
    return dt.strftime("%Y-%m-%d %H:%M")


def read_session_jumps(session_dir: Path) -> "tuple[int, float]":
    """(count, best_height_m) from <session>/jumps.csv — the
    `n,takeoff_s,airtime_raw_s,airtime_s,height_m[,...]` header every
    jumps.csv in this repo shares. Reimplemented minimally here (rather than
    importing tools/jump, a script, or reaching into sim/) so this file has
    no dependency on the modules another agent is concurrently writing."""
    try:
        lines = (session_dir / "jumps.csv").read_text().splitlines()
    except OSError:
        return 0, 0.0
    count, best = 0, 0.0
    for ln in lines:
        if not ln.strip() or ln.startswith(("n,", "#")):
            continue
        cells = ln.split(",")
        if len(cells) < 5:
            continue
        try:
            height = float(cells[4])
        except ValueError:
            continue
        count += 1
        best = max(best, height)
    return count, best


def read_surfr_count(session_dir: Path) -> "Optional[int]":
    try:
        data = json.loads((session_dir / "surfr.json").read_text())
    except (OSError, ValueError):
        return None
    n = data.get("jumps_total")
    return n if isinstance(n, int) else None


def read_score_summary(session_dir: Path) -> "Optional[str]":
    """The first line in score.md containing 'FINDING' — sim/score.py's own
    convention for a notable, non-routine line (a multi-boot ring buffer,
    a failed alignment, a missing surfr.json — see its render_scorecard()).
    A score.md with no FINDING line scored cleanly; that is worth a line
    too, so it is not treated as absent."""
    try:
        text = (session_dir / "score.md").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if "FINDING" in line:
            return line.strip().lstrip("-").strip()
    return "scored, no findings flagged"


_SYNTHETIC_SRC_PREFIX = "fakedev"   # tools/fake_device.py BUILD_SRC = "fakedev0"


def synthetic_source(session_dir: Path) -> "Optional[str]":
    """The bench fake's build id if this session came from tools/fake_device.py,
    else None. The fake announces itself in the INFO line the daemon copies
    into device.log (`INFO src=fakedev0 ...`); real firmware puts a git hash
    there. Measured 2026-09-15: data/sessions/20260913-012409-UNKN was such a
    bundle -- 4 jumps at takeoff_s 5.000/12.000/20.000/30.000, best 5.00 m --
    and it sat in data/corpus.md as a ride for two days. A synthetic session
    in an ACCURACY corpus is a manufactured measurement (CLAUDE.md rule 3)."""
    try:
        head = (session_dir / "device.log").read_text(encoding="utf-8",
                                                      errors="replace")[:4096]
    except OSError:
        return None
    m = re.search(r"\bsrc=(" + _SYNTHETIC_SRC_PREFIX + r"\S*)", head)
    return m.group(1) if m else None


def corpus_line(session_dir: Path) -> str:
    try:
        session_json = json.loads((session_dir / "session.json").read_text())
    except (OSError, ValueError):
        session_json = {}
    when = _session_when(session_dir)
    puck4 = _session_puck4(session_dir, session_json)
    fake = synthetic_source(session_dir)
    if fake is not None:
        return (f"{when}  {puck4}  SYNTHETIC — src={fake} is tools/fake_device.py, "
                "a bench artifact, not a ride; excluded from every count")
    if not (session_dir / "jumps.csv").is_file():
        # read_session_jumps() answers an unreadable jumps.csv with (0, 0.0),
        # which is the right shape for the notification total and the wrong
        # thing to print here: "0 puck jumps, best 0.00 m" in an ACCURACY
        # corpus is a manufactured measurement, indistinguishable from a real
        # ride on which nothing was detected (CLAUDE.md rule 3; tools/refit.py
        # keeps the same distinction -- "never a manufactured zero").
        #
        # WHAT it says has to be measured too. "ingest did not finish" is a
        # CAUSE, and it is only supportable for a directory that came from an
        # ingest at all — a session.json is what an ingested bundle leaves
        # behind. data/sessions/ also holds bench artifacts that were never
        # ingests (jitter-check/ and walk-overnight/ hold no jumps.csv and no
        # session.json), and diagnosing those as a failed ingest is a verdict
        # without a measurement (CLAUDE.md rule 2 / 2.6).
        parts = ["no jumps.csv — ingest did not finish" if session_json
                 else "no jumps.csv and no session.json — not an ingested session"]
    else:
        count, best = read_session_jumps(session_dir)
        parts = [f"{count} puck jumps, best {best:.2f} m"]
    surfr_n = read_surfr_count(session_dir)
    if surfr_n is not None:
        parts.append(f"Surfr {surfr_n} jumps")
    summary = read_score_summary(session_dir)
    if summary is not None:
        parts.append(f"score: {summary}")
    return f"{when}  {puck4}  " + "; ".join(parts)


def write_corpus(cfg: Config) -> Path:
    """Regenerate data/corpus.md from EVERY directory under data/sessions/,
    not only the ones ingested this cycle — docs/accuracy-plan.md's own
    corpus table is meant to grow to cover the whole history, and a
    from-scratch rebuild is self-healing if a session was added by hand."""
    dirs = sorted(d for d in cfg.sessions_dir.iterdir() if d.is_dir()) \
        if cfg.sessions_dir.is_dir() else []
    lines = [
        "# corpus.md — generated by tools/ride_loop.py; do not hand-edit.",
        "",
        "One line per session under data/sessions/, regenerated every cycle.",
        "Same fields as docs/accuracy-plan.md's own hand-kept corpus table:",
        "date, puck jumps + best height, a Surfr count when surfr.json has",
        "been transcribed, and a score.md finding when one has been generated.",
        "",
    ]
    for d in dirs:
        try:
            lines.append(corpus_line(d))
        except Exception as exc:  # noqa: BLE001 -- one bad session must not blank the corpus
            lines.append(f"{d.name}  (could not summarise: {exc!r})")
    cfg.corpus_path.parent.mkdir(parents=True, exist_ok=True)
    cfg.corpus_path.write_text("\n".join(lines) + "\n")
    return cfg.corpus_path


def upload_reports(cfg: Config, items: "list[tuple[Path, str]]") -> None:
    """rclone copyto each (local_path, remote_name) to gdrive:JumpHeight/reports/.
    Every session's score.md shares the same local basename, so each is
    given a session-qualified remote name (the caller's job) rather than
    overwriting the last one uploaded."""
    for local_path, remote_name in items:
        if not local_path.is_file():
            continue
        spec = f"{cfg.rw_remote}:{cfg.reports_dir}/{remote_name}"
        try:
            proc = _rclone(["copyto", str(local_path), spec], timeout=_COPY_TIMEOUT_S)
        except (RcloneNotFound, subprocess.TimeoutExpired, OSError) as exc:
            log(cfg, f"report upload failed for {remote_name}: {exc!r}")
            continue
        if proc.returncode != 0:
            log(cfg, f"report upload failed for {remote_name}: exit {proc.returncode}: "
                     f"{(proc.stderr or '').strip()[-200:]}")
        else:
            log(cfg, f"uploaded {remote_name} to {spec}")


# ------------------------------------------------------------------ notify

def _osascript_escape(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def osascript_notify(title: str, body: "Optional[str]") -> None:
    """The real notifier: a macOS notification via osascript. Never called
    from tests — they pass their own `notifier` into Config. Same known,
    not-fixed caveat tools/puckd/notify.py documents: an unsigned app's
    osascript notification is attributed to Script Editor."""
    script = "display notification {b} with title {t}".format(
        b=_osascript_escape(body or ""), t=_osascript_escape(title))
    subprocess.run(["osascript", "-e", script], check=False,
                   capture_output=True, encoding="utf-8", errors="replace")


def fire_notification(cfg: Config, title: str, body: "Optional[str]") -> "tuple[str, Optional[str]]":
    try:
        cfg.notifier(title, body)
    except Exception as exc:  # noqa: BLE001 -- a lost notification must not cost anything else
        log(cfg, f"notify raised: {exc!r}")
    log(cfg, f"notified: {title!r}")
    return title, body


# --------------------------------------------------------------- the cycle

@dataclass
class CycleReport:
    ready: bool = False
    new_bundles: "list[Path]" = field(default_factory=list)
    new_fits: "list[Path]" = field(default_factory=list)
    sessions: "list[Path]" = field(default_factory=list)
    score_mds: "list[Path]" = field(default_factory=list)
    log_changed: bool = False
    notified: "Optional[tuple[str, Optional[str]]]" = None
    errors: "list[str]" = field(default_factory=list)


def run_cycle(cfg: Config) -> CycleReport:
    """One full pass. Never raises — every step below is wrapped so a
    failure anywhere becomes a logged line and a report.errors entry, not
    an unhandled exception reaching launchd."""
    report = CycleReport()
    # One pass at a time. Measured 2026-09-15: a manual --once and a launchd
    # run overlapped; the seen-ledger is a read-modify-write and an ingest
    # is not idempotent, so the second pass must yield, not race.
    lock_path = Path(cfg.data_dir) / ".ride_loop.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_fh = open(lock_path, "w")
        import fcntl
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        log(cfg, "another ride_loop pass is running -- this one yields")
        report.errors.append("another pass running")
        return report
    try:
        _run_cycle(cfg, report)
    except Exception as exc:  # noqa: BLE001 -- CLAUDE.md: never raise out of the loop
        log(cfg, f"run_cycle raised: {exc!r}")
        report.errors.append(repr(exc))
    try:
        lock_fh.close()
    except OSError:
        pass
    return report


def _run_cycle(cfg: Config, report: CycleReport) -> None:
    # "No rclone binary" and "the remote is not connected" are different
    # faults with different fixes, and remote_authorized() swallows the first
    # into the second's answer (False). MEASURED 2026-09-15: the LaunchAgent
    # installed at 04:05 UTC carried no EnvironmentVariables, launchd's PATH
    # is /usr/bin:/bin:/usr/sbin:/sbin, rclone lives in /opt/homebrew/bin —
    # so every ten-minute cycle from 04:05 to 11:57 UTC logged "gdrive-ro not
    # ready ... `rclone config reconnect`" (data/ride_loop.log) while a shell
    # run listed all three folders fine. Eight hours of a log line naming
    # three causes, none of which was the actual one. Name it.
    missing = rclone_missing_reason()
    if missing is not None:
        log(cfg, f"NO RCLONE BINARY: {missing}. This is NOT a {cfg.ro_remote} "
                 f"authorization problem — nothing was asked of Drive at all. "
                 f"Under launchd, PATH is whatever the plist sets; "
                 f"packaging/com.jumpheight.rideloop.plist sets both "
                 f"{RCLONE_ENV} and a PATH containing /opt/homebrew/bin, so a "
                 f"stale installed plist is the first thing to check "
                 f"(re-run --install). Skipping this cycle.")
        report.errors.append("no rclone binary")
        return
    ok, listings = check_prerequisites(cfg)
    if not ok:
        log(cfg, f"{cfg.ro_remote} not ready (not configured, or configured "
                 "but not connected — `rclone config reconnect`, or the share "
                 "is gone: nothing listed at all) — skipping this cycle, will "
                 "retry at the next launchd interval")
        return
    report.ready = True

    # A folder that did not list is named, in the log AND in the report, and
    # then read as empty for this cycle -- never silently conflated with "it
    # was empty" (CLAUDE.md rule 3). MEASURED: real rclone answers a folder
    # that does not exist the same way it answers a real failure (exit 3),
    # and puckd creates the three at three different moments, so this is the
    # routine case on a young install, not an alarm.
    for key, remote_dir in (("inbox", cfg.inbox_dir), ("fits", cfg.fits_dir),
                            ("log", cfg.log_dir)):
        if listings.get(key) is None:
            log(cfg, f"{cfg.ro_remote}:{remote_dir} did not list (not created "
                     "yet, or unreadable) — read as empty for this cycle")
            report.errors.append(f"{remote_dir} did not list")
            listings[key] = []

    seen = load_seen(cfg)
    report.new_bundles = sync_new_entries(cfg, cfg.inbox_dir, cfg.incoming_dir,
                                          listings["inbox"], seen)
    report.new_fits = sync_new_entries(cfg, cfg.fits_dir, cfg.fits_cache_dir,
                                       listings["fits"], seen)
    save_seen(cfg, seen)
    if report.new_fits:
        log(cfg, f"cached {len(report.new_fits)} new Garmin fit zip(s)")

    report.log_changed = sync_rider_log(cfg, listings["log"])

    all_fits = sorted(cfg.fits_cache_dir.glob("*.zip")) if cfg.fits_cache_dir.is_dir() else []

    total_jumps, best_height = 0, 0.0
    for zip_path in report.new_bundles:
        sess = run_ingest(cfg, zip_path)
        if sess is None:
            report.errors.append(f"ingest failed: {zip_path.name}")
            continue
        fake = synthetic_source(sess)
        if fake is not None:
            log(cfg, f"{sess.name}: src={fake} is the bench fake device — "
                     "listed as SYNTHETIC in corpus.md, not scored, not counted")
            continue
        report.sessions.append(sess)

        window = session_trace_window(sess)
        if window is None:
            log(cfg, f"{sess.name}: no usable trace window — Garmin match skipped")
        elif not all_fits:
            log(cfg, f"{sess.name}: no cached Garmin fit to match against")
        else:
            fit = pick_matching_fit(cfg, window, all_fits)
            if fit is None:
                log(cfg, f"{sess.name}: no cached Garmin fit overlaps its trace window")
            elif install_garmin_fit(sess, fit):
                log(cfg, f"{sess.name}: attached {fit.name} as garmin.fit")
            else:
                log(cfg, f"{sess.name}: {fit.name} matched but could not be unzipped")

        score_md = run_score(cfg, sess)
        if score_md is not None:
            report.score_mds.append(score_md)

        n, best = read_session_jumps(sess)
        total_jumps += n
        best_height = max(best_height, best)

    write_corpus(cfg)

    upload_items = [(cfg.corpus_path, "corpus.md")]
    upload_items += [(md, f"{md.parent.name}-score.md") for md in report.score_mds]
    upload_reports(cfg, upload_items)

    if report.sessions:
        report.notified = fire_notification(
            cfg, f"Nick: {total_jumps} jumps, best {best_height:.2f} m", None)
    elif report.log_changed:
        report.notified = fire_notification(cfg, "Nick: log updated", None)
    else:
        log(cfg, "nothing new this cycle")


# --------------------------------------------------------------------- CLI

PLIST_LABEL = "com.jumpheight.rideloop"
PLIST_TEMPLATE_PATH = REPO / "packaging" / "com.jumpheight.rideloop.plist"


def render_plist(cfg: Config) -> str:
    template = PLIST_TEMPLATE_PATH.read_text()
    return (template
            .replace("__PYTHON__", sys.executable)
            .replace("__SCRIPT__", str(cfg.repo_dir / "tools" / "ride_loop.py")))


def install(cfg: Config, run_launchctl: "Callable[..., subprocess.CompletedProcess]" = subprocess.run) -> bool:
    """Write the real plist (placeholders filled in) to
    ~/Library/LaunchAgents/com.jumpheight.rideloop.plist and load it —
    `launchctl bootout` first (best-effort; a stale copy from an earlier
    install is not an error) then `launchctl bootstrap gui/$UID`, the same
    two calls packaging/reset.sh and com.jumpheight.puckd.plist's own
    comment already use for the sibling agent. NEVER called automatically —
    the task's own instruction: the owner runs this by hand."""
    try:
        content = render_plist(cfg)
    except OSError as exc:
        log(cfg, f"install: could not read the plist template: {exc!r}")
        return False
    dest = Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
    except OSError as exc:
        log(cfg, f"install: could not write {dest}: {exc!r}")
        return False

    uid = os.getuid()
    try:
        run_launchctl(["launchctl", "bootout", f"gui/{uid}", str(dest)],
                      capture_output=True, text=True)
    except (OSError, subprocess.TimeoutExpired):
        pass  # nothing was loaded yet — expected on a first install
    try:
        proc = run_launchctl(["launchctl", "bootstrap", f"gui/{uid}", str(dest)],
                             capture_output=True, text=True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        log(cfg, f"install: wrote {dest}, but launchctl bootstrap raised: {exc!r}")
        return False
    ok = getattr(proc, "returncode", 1) == 0
    detail = "" if ok else f": {(getattr(proc, 'stderr', '') or '').strip()}"
    log(cfg, f"install: wrote {dest}; launchctl bootstrap {'ok' if ok else 'FAILED' + detail}")
    return ok


def render_report(report: CycleReport) -> str:
    lines = [
        f"ready: {report.ready}",
        f"new bundles: {len(report.new_bundles)}",
        f"new Garmin fits cached: {len(report.new_fits)}",
        f"sessions ingested: {[s.name for s in report.sessions]}",
        f"score.md written: {[m.parent.name for m in report.score_mds]}",
        f"rider log changed: {report.log_changed}",
    ]
    if report.notified:
        lines.append(f"notified: {report.notified[0]!r}")
    if report.errors:
        lines.append(f"errors: {report.errors}")
    return "\n".join(lines)


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="The ride watcher on the owner's Mac — docs/sync-agent-plan.md's "
                    "counterpart running here instead of on Nick's machine.")
    ap.add_argument("--once", action="store_true",
                    help="run a single cycle, print a short report, and exit — "
                         "identical work to a plain invocation (what launchd's "
                         "StartInterval runs), plus the report on stdout")
    ap.add_argument("--install", action="store_true",
                    help="write packaging/com.jumpheight.rideloop.plist to "
                         "~/Library/LaunchAgents and load it with launchctl")
    return ap


def main(argv: "Optional[list[str]]" = None) -> int:
    args = build_arg_parser().parse_args(argv)
    cfg = default_config()
    if args.install:
        return 0 if install(cfg) else 1
    report = run_cycle(cfg)
    if args.once:
        print(render_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
