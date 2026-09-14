"""tools/puckd/daemon.py -- the loop (docs/sync-agent-plan.md's whole "What
the agent does when the puck appears" section, :41-58, and the gates,
:60-66).

This is P2: it reimplements none of the six P1 modules' protocols -- it
composes them. Every decision that changes what Nick sees traces to a line
in the spec:

    find_puck_port()   the 2 s poll target: Seeed VID 0x2886 first (survives
                        a replug even when macOS renumbers /dev/cu.usbmodemN),
                        then a bare /dev/cu.usbmodem* glob when pyserial
                        itself is not importable (tools/jump:240-251's own
                        fallback, restated).
    run_job_cycle()     one plug-in: steps 1-9 (docs/sync-agent-plan.md:42-55),
                        holding G1 (upload.py) and G2 (flash.py) exactly --
                        clear only when uploaded AND verified; flash only
                        when clear just confirmed the puck empty AND
                        needs_update() AND flash()'s own sha256 gate passes.
    poll_attached()     step 10's 60 s battery poll and the one "Puck
                        charged" per attachment (chg 1->0 at batt_pct>=95).
    run_forever()       the 2 s loop gluing the two above to a menu bar and
                        a Garmin leg that "never blocks the puck job"
                        (docs/sync-agent-plan.md:58) -- every exception in
                        the Garmin leg is swallowed, never the puck's.

Every external effect -- which port is a puck, the wall clock, sleep, the
notifier, the two P1 modules with swappable behaviour (garmin, flash) -- is
an injectable field on DaemonConfig, the same seam pattern flash.py's own
keyword arguments use, so tools/tests/test_puckd_daemon.py can drive the
whole state machine against a real tools/fake_device.py subprocess, a real
rclone pointed at a fake script on PATH (PUCKD_RCLONE, same technique as
tools/tests/test_puckd_upload.py), and a fake garmin/flash module -- with no
real puck, Google account, or 60 s wait anywhere in the suite.

Two strings here are not spec-literal and are cited as such: G1 (a Drive
upload failing) and G2's post-flash failure both need a needs_you `line`/
`action` docs/sync-agent-plan.md never spells out for those two cases (only
the Garmin-expiry title, line 39, and the generic pattern's own example,
line 13, are given verbatim) -- see NEEDS_YOU_UPLOAD_* and PUCK_RESET_*
below for exactly which words are quoted and which are this module's own,
kept as short and as literal-recovery-action as the spec's own voice.
"""

from __future__ import annotations

import glob
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools"))            # `from puckd import ...`
sys.path.insert(0, str(REPO / "tools" / "puckd"))  # bare `import garmin` --
# garmin.py's own module docstring: "import via sys.path.insert(str(REPO /
# 'tools' / 'puckd')); import garmin -- no package __init__.py, matches
# sibling P1 modules already in tools/puckd/".

from puckd import serial_job, upload, notify, menubar, flash  # noqa: E402
import garmin  # noqa: E402


# --------------------------------------------------------------- constants

SEEED_VID = 0x2886  # Seeed Studio's USB vendor id -- the XIAO Sense's own.
POLL_INTERVAL_S = 2.0                    # docs/sync-agent-plan.md's own "loop"
STATS_POLL_INTERVAL_S = 60.0             # spec item 10
GARMIN_INTERVAL_S = 6 * 3600.0           # spec line 57: "every job and every 6 h"
CHARGED_BATT_PCT_MIN = 95                # spec item 10

# spec lines 49/58's own paths, verbatim (upload.upload() prefixes "gdrive:").
INBOX_DIR = "JumpHeight/inbox"
FITS_DIR = "JumpHeight/fits"

STATE_FILENAME = "state.json"            # last ride, under PUCKD_HOME
GARMIN_SEEN_FILENAME = "garmin_last_seen.json"
FIRMWARE_CACHE_DIRNAME = "firmware"
FITS_CACHE_DIRNAME = "fits"

# The deployed site. web/ is the GitHub Pages root: the rider page is
# <site>/sync/ and reads ../firmware/latest.json, so the manifest is at
# <site>/firmware/latest.json (flash.py's own _MANIFEST_PATH). PUCKD_SITE_URL
# overrides it for a bench against a local server.
DEFAULT_SITE_URL = "https://joshcrow.github.io/Jump-height"
SITE_URL_ENV = "PUCKD_SITE_URL"

SPOOL_RETRY_INTERVAL_S = 600.0   # bundles Drive has not confirmed are retried quietly
PENDING_MAX_AGE_S = 24 * 3600.0  # ...and Nick hears about it only after a day
SENT_DIRNAME = "sent"            # spool/sent/ holds what Drive has confirmed
LOG_FILENAME = "daemon.log"      # one line per thing Josh would want to know
OPEN_SETUP_FLAG = "open-setup"   # the launcher leaves this when the icon is clicked while running

# The three "Needs you" messages, and no others. docs/sync-agent-plan.md:
# "ONE notification, ONE action, always phrased the same way." Everything
# the PUCK gets wrong -- a pull that did not complete, a clear that did not
# confirm, a flash that did not come back -- gets the same words, because
# Nick cannot tell those apart and the fix is the same. Drive and Garmin
# both send him to the one place that fixes them.
PUCK_RESET_LINE = "check the puck"
PUCK_RESET_ACTION = "Press the small button on the puck twice."
NEEDS_YOU_UPLOAD_LINE = "reconnect Google Drive"
NEEDS_YOU_UPLOAD_ACTION = "Open Set up in the menu bar."
NEEDS_YOU_GARMIN_LINE = "sign in to Garmin again"
NEEDS_YOU_GARMIN_ACTION = "Open Set up in the menu bar."


# ------------------------------------------------------------ port finding

def _pyserial_candidates() -> "list[tuple[str, Optional[int]]]":
    from serial.tools import list_ports  # type: ignore

    return [(p.device, p.vid) for p in list_ports.comports()]


def find_puck_port(
    list_ports_fn: "Callable[[], list[tuple[str, Optional[int]]]] | None" = None,
) -> "Optional[str]":
    """The 2 s poll target. The Seeed VID (0x2886) is tried first -- pyserial
    reports it regardless of which /dev/cu.usbmodemN macOS happens to assign
    on this particular replug, so it is the one identification that
    survives a port-number change; falling back to a bare
    /dev/cu.usbmodem* glob only when pyserial itself is not importable
    mirrors tools/jump's own scan_ports() fallback (tools/jump:240-251) for
    the same reason. Multiple candidates (two pucks on one bench) are
    resolved by sorted()[0] -- deterministic, not a guess at "the right
    one" -- exactly the ambiguity CLAUDE.md's board registry exists to
    avoid living inside one process's silent pick; a real multi-puck bench
    is `--name`-pinned tooling's job (CLAUDE.md ss1), not this poll's.
    """
    try:
        candidates = (list_ports_fn or _pyserial_candidates)()
    except ImportError:
        candidates = [(d, None) for d in glob.glob("/dev/cu.usbmodem*")]

    seeed = sorted(d for d, vid in candidates if vid == SEEED_VID)
    if seeed:
        return seeed[0]
    modem = sorted(
        d for d, vid in candidates if isinstance(d, str) and d.startswith("/dev/cu.usbmodem")
    )
    return modem[0] if modem else None


# --------------------------------------------------------------- state I/O

def _state_path(home_dir: "Path | str") -> Path:
    return Path(home_dir) / STATE_FILENAME


def load_state(home_dir: "Path | str") -> dict:
    """The daemon's own persisted memory (docs/sync-agent-plan.md's build
    note: "Persist state (last ride, last_seen) under PUCKD_HOME"). Missing
    or corrupt reads as "nothing yet" -- a fresh install's first tick, not
    an error -- same posture as garmin.last_seen()'s own file read."""
    try:
        data = json.loads(_state_path(home_dir).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(home_dir: "Path | str", state: dict) -> None:
    """Atomic write (.tmp + replace), the same pattern garmin.mark_seen()
    uses -- a crash mid-write must leave the PREVIOUS state intact, not a
    half-written one."""
    path = _state_path(home_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)


def _read_bundle_manifest(bundle_path: "Path | str") -> dict:
    with zipfile.ZipFile(bundle_path) as zf:
        return json.loads(zf.read("manifest.json"))


def _record_ride(cfg: "DaemonConfig", jumps: int, bundle_path: "Path | str") -> None:
    """"Last ride Tue 4:52 pm . 12 jumps" needs a wall-clock moment for the
    ride -- reads it back out of the bundle's OWN manifest.json
    (synced_at_local, CONTRACT.md SS2.2/SS2.3) rather than capturing a
    second, possibly-different `datetime.now()` here: one clock reading per
    ride, reused, not two that could disagree."""
    try:
        synced_at_local = _read_bundle_manifest(bundle_path).get("synced_at_local")
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        synced_at_local = None
    state = load_state(cfg.home_dir)
    if synced_at_local:
        state["last_ride_iso"] = synced_at_local
    state["last_ride_jumps"] = jumps
    save_state(cfg.home_dir, state)


# ------------------------------------------------------------------ config

def _default_fetch_uf2(site_url: str, file_name: str, dest_dir: "Path | str") -> "Optional[Path]":
    """Download <site_url>/firmware/<file_name> -- the sibling path to
    flash.py's own _MANIFEST_PATH ("/firmware/latest.json", flash.py:71) --
    to dest_dir, returning the local path. NEVER RAISES: any failure to
    reach or read it returns None, which _maybe_flash() below reads as "try
    again next job", the same silence latest_manifest() uses for a broken
    manifest -- a firmware update is maintenance, not urgent, and must
    never turn a network blip into a needs_you (only touching the DEVICE
    and not coming back does that -- G4)."""
    url = site_url.rstrip("/") + "/firmware/" + file_name
    try:
        from puckd import netctx
        with urllib.request.urlopen(url, timeout=60.0, context=netctx.ssl_context()) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                return None
            data = resp.read()
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / file_name
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


@dataclass
class DaemonConfig:
    """Every external effect the daemon touches, gathered so the whole state
    machine can be driven by a test with a fake for each one -- the same
    seam pattern flash.py's own injectable keywords use (flash.py:225-241).

    Production (build_config()) leaves every injectable at its real
    default; tests override device_factory (a real tools/jump.Device
    talking to a spawned tools/fake_device.py), garmin_module (a small
    fake), flash_module (a small fake or the real one against a local HTTP
    manifest server, test_puckd_flash.py's own technique), notifier (a
    recording runner), and the two clocks.
    """

    home_dir: Path
    spool_dir: Path
    site_url: str = ""
    device_factory: "Callable[[str], object] | None" = None
    notifier: "Callable[[str, Optional[str]], None]" = notify.osascript_runner
    garmin_module: object = None
    flash_module: object = None
    fetch_uf2_fn: "Callable[[str, str, Path], Optional[Path]]" = _default_fetch_uf2
    share_fn: "Callable[[str], bool]" = upload.ensure_shared
    # Called with "reading" / "uploading" / "emptying" / "updating" as the job
    # moves, and None when it is done -- run_forever() wires it to the menu
    # bar so the glyph and the first panel line follow the work.
    on_phase: "Callable[[Optional[str]], None]" = lambda phase: None
    inbox_dir: str = INBOX_DIR
    fits_dir: str = FITS_DIR
    garmin_interval_s: float = GARMIN_INTERVAL_S
    now: "Callable[[], float]" = time.time
    sleep: "Callable[[float], None]" = time.sleep
    # Small cross-call bookkeeping (the Garmin needs_you dedup flag, the
    # menu-bar "attention" flag) that doesn't belong on any one call's
    # return value -- a plain dict, not more dataclass fields, because
    # nothing outside this module reads it.
    runtime: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.garmin_module is None:
            self.garmin_module = garmin
        if self.flash_module is None:
            self.flash_module = flash


def build_config(*, home_dir: "Path | str | None" = None,
                 site_url: "Optional[str]" = None) -> DaemonConfig:
    """Production wiring: home_dir defaults to garmin.puckd_home() (the one
    definition of PUCKD_HOME, reused rather than re-derived), spool_dir to
    its "spool" child (menubar.py's own SPOOL_DIR default, computed here
    instead of imported so PUCKD_HOME overrides both consistently), and
    site_url from PUCKD_SITE_URL (see SITE_URL_ENV's own note above)."""
    home = Path(home_dir) if home_dir is not None else garmin.puckd_home()
    return DaemonConfig(
        home_dir=home,
        spool_dir=home / "spool",
        site_url=site_url if site_url is not None else (os.environ.get(SITE_URL_ENV) or DEFAULT_SITE_URL),
    )


# ------------------------------------------------------------- needs_you

def _fire_needs_you(cfg: DaemonConfig, line: str, action: str) -> None:
    notify.notify("needs_you", runner=cfg.notifier, line=line, action=action)
    cfg.runtime["needs_you_active"] = True
    _log(cfg, f"needs you: {line}")


def _log(cfg: DaemonConfig, msg: str) -> None:
    """Append one timestamped line to PUCKD_HOME/daemon.log. Never raises.
    Nick never reads this; Josh does, over the phone."""
    try:
        path = Path(cfg.home_dir) / LOG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        # encoding pinned: under launchd there is no locale, so the bundled
        # Python's default is ASCII, and the first em dash in a message
        # raised UnicodeEncodeError out of the LOGGER -- inside the loop's
        # own exception handler, which killed the loop thread while the menu
        # bar stayed up (measured 2026-09-14 07:58). A log line must never
        # be able to end the thing it is logging.
        with path.open("a", encoding="utf-8", errors="replace") as f:
            f.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {msg}\n")
    except Exception:  # noqa: BLE001 -- see above; a lost line is the lesser harm
        pass


# ------------------------------------------------------------- the spool

def _pending_bundles(cfg: DaemonConfig) -> "list[Path]":
    """Bundles Drive has not confirmed: every .zip in the spool root.
    Confirmed ones live in spool/sent/."""
    return sorted(Path(cfg.spool_dir).glob("*.zip"))


def _mark_sent(cfg: DaemonConfig, bundle_path: "Path | str") -> Path:
    src = Path(bundle_path)
    sent = Path(cfg.spool_dir) / SENT_DIRNAME
    sent.mkdir(parents=True, exist_ok=True)
    dest = sent / src.name
    n = 2
    while dest.exists():
        dest = sent / f"{src.stem}-{n}{src.suffix}"
        n += 1
    src.replace(dest)
    return dest


def _upload_bundle(cfg: DaemonConfig, bundle_path: "Path | str") -> "tuple[bool, Optional[Path]]":
    """G1's remote-size check lives in upload.upload(); this only moves a
    CONFIRMED bundle into spool/sent/ so the spool root is exactly the
    retry list. Returns (ok, new path)."""
    result = upload.upload(bundle_path, cfg.inbox_dir)
    if getattr(result, "ok", False):
        cfg.runtime["drive_needs_you_sent"] = False
        if not cfg.runtime.get("folder_shared"):
            # The folder exists now (drive.file: the app made it). Share it
            # with Josh once per run; Drive ignores repeats anyway.
            cfg.runtime["folder_shared"] = bool(cfg.share_fn(cfg.inbox_dir.split("/")[0]))
        return True, _mark_sent(cfg, bundle_path)
    _log(cfg, f"upload did not confirm: {Path(bundle_path).name}: {getattr(result, 'error', '')}")
    return False, None


def _drive_needs_you_if_due(cfg: DaemonConfig) -> None:
    """An upload that fails is retried, not announced. Nick hears about
    Drive in exactly two cases: the remote is gone (setup undone, token
    revoked) or a ride has sat unconfirmed for PENDING_MAX_AGE_S. Once per
    episode; a confirmed upload re-arms it."""
    pending = _pending_bundles(cfg)
    if not pending or cfg.runtime.get("drive_needs_you_sent"):
        return
    # stat() each one defensively: retry_spool() moves a confirmed bundle to
    # spool/sent/ between the glob above and this read, so a plain
    # comprehension can raise FileNotFoundError on the very tick an upload
    # finally succeeded -- the one tick where nothing is wrong at all.
    mtimes = []
    for p in pending:
        try:
            mtimes.append(p.stat().st_mtime)
        except OSError:
            pass
    if not mtimes:
        return
    oldest_age = cfg.now() - min(mtimes)
    authorized = upload.is_authorized()
    if not authorized and not (Path(cfg.spool_dir) / SENT_DIRNAME).is_dir():
        # Drive was never connected at all: that is setup, which the window
        # is already asking for. "reconnect" would be a lie.
        return
    if oldest_age >= PENDING_MAX_AGE_S or not authorized:
        _fire_needs_you(cfg, NEEDS_YOU_UPLOAD_LINE, NEEDS_YOU_UPLOAD_ACTION)
        cfg.runtime["drive_needs_you_sent"] = True


LOG_DIR = "JumpHeight/log"        # where daemon.log and status.json go on Drive
STATUS_FILENAME = "status.json"


def publish_log(cfg: DaemonConfig, report: "Optional[JobCycleReport]" = None,
                stats: "Optional[dict]" = None) -> bool:
    """Copy daemon.log and a small status.json into the rider's shared Drive
    folder, so Josh can read what happened without asking. Called after
    every job and on the spool-retry tick. A few KB; rclone overwrites in
    place. Never raises; False when Drive did not confirm."""
    try:
        home = Path(cfg.home_dir)
        status = load_state(home)
        status.update({
            "written": datetime.now().astimezone().isoformat(timespec="seconds"),
            "site": cfg.site_url,
        })
        if report is not None:
            status["last_job"] = {
                "port": report.port, "empty": report.empty, "pulled": report.pulled,
                "verified": report.verified, "jumps": report.jumps, "uploaded": report.uploaded,
                "cleared": report.cleared, "flashed": report.flashed, "src": report.src,
                "needs_you": report.needs_you,
                "bundle": Path(report.bundle_path).name if report.bundle_path else None,
            }
        if stats:
            status["puck"] = {k: stats.get(k) for k in
                              ("vbat_mv", "batt_pct", "chg", "trace_bytes", "stored_jumps", "trace_full", "error")}
        sp = home / STATUS_FILENAME
        sp.write_text(json.dumps(status, indent=2, default=str))
        ok = True
        for name in (LOG_FILENAME, STATUS_FILENAME):
            f = home / name
            if f.is_file():
                ok = bool(getattr(upload.upload(f, LOG_DIR), "ok", False)) and ok
        return ok
    except Exception as exc:  # noqa: BLE001 -- telemetry must never cost a sync
        _log(cfg, f"publish_log raised: {exc!r}")
        return False


def retry_spool(cfg: DaemonConfig) -> int:
    """Upload whatever Drive has not confirmed yet. Returns how many
    confirmed this time. Never raises."""
    n = 0
    for p in _pending_bundles(cfg):
        try:
            ok, _ = _upload_bundle(cfg, p)
        except Exception as exc:  # noqa: BLE001 -- the loop must outlive rclone
            _log(cfg, f"upload raised: {exc!r}")
            ok = False
        if ok:
            n += 1
    _drive_needs_you_if_due(cfg)
    return n


# --------------------------------------------------------------- the job

@dataclass(frozen=True)
class JobCycleReport:
    """What one plug-in's worth of run_job_cycle() did -- for `once`'s
    plain-text report and for tests to assert against without re-deriving
    it from notifier calls alone."""

    port: str
    pulled: bool
    verified: "Optional[bool]"
    jumps: "Optional[int]"
    reasons: "list[str]"
    uploaded: bool
    cleared: bool
    flashed: bool
    needs_you: "Optional[tuple[str, str]]"
    bundle_path: "Optional[Path]"
    src: "Optional[str]"
    empty: bool = False   # the puck had no ride: nothing pulled, nothing said
    # The `stats` this cycle ALREADY read off the puck before doing anything
    # else. Carried out so run_forever() can seed the attachment session
    # from it instead of opening the port a fifth time for the same numbers:
    # every open pays Device.drain_boot()'s 5 s window (docs/STATUS.md:703,
    # "five opens per cycle; 28 s on the fake"), and a battery percentage
    # measured 30 s ago is the same percentage.
    stats: "Optional[dict]" = None


def _maybe_flash(
    port_path: str, puck_src: "Optional[str]", cfg: DaemonConfig
) -> "tuple[bool, Optional[tuple[str, str]]]":
    """G2 (docs/sync-agent-plan.md:62), the half of it not already enforced
    by flash.flash() itself: called ONLY once run_job_cycle()'s own
    clear_puck() has just confirmed stored_jumps=0 AND trace_bytes=0 (the
    "puck empty" clause), so this function's own job is only needs_update()
    and the manifest fetch. flash()'s own sha256 check (the "matches
    latest.json" clause) is still the one gate that runs first inside it,
    unconditionally, before anything touches the device.

    Returns (flashed, needs_you) -- needs_you is surfaced to the caller
    (rather than only fired through the notifier) so run_job_cycle()'s own
    report reflects it too: a flash is the one step that can still turn a
    fully-synced, fully-cleared cycle into one Nick needs to act on.

    A manifest miss, a needs_update()=False verdict, or an unreachable .uf2
    file are all silent -- "try again next job", same posture as
    latest_manifest()'s own contract. Only a flash() call that ACTUALLY
    TOUCHED THE DEVICE (stage_reached past STAGE_SHA256) and still failed
    fires needs_you -- G4: "no src after flash -> Needs you"; a sha256
    mismatch caught before anything was sent never touched the puck, so it
    is not one.
    """
    if not cfg.site_url:
        return False, None
    manifest = cfg.flash_module.latest_manifest(cfg.site_url)
    if not cfg.flash_module.needs_update(puck_src, manifest):
        return False, None
    uf2_path = cfg.fetch_uf2_fn(
        cfg.site_url, manifest["file"], Path(cfg.home_dir) / FIRMWARE_CACHE_DIRNAME
    )
    if uf2_path is None:
        return False, None
    cfg.on_phase("updating")
    result = cfg.flash_module.flash(
        port_path, uf2_path, manifest, device_factory=cfg.device_factory
    )
    if result.ok:
        notify.notify("updated", runner=cfg.notifier)
        _log(cfg, f"puck updated to {result.src_after}")
        return True, None
    _log(cfg, f"flash: ok={result.ok} stage={result.stage_reached} src_after={result.src_after} {result.error or ''}")
    if result.stage_reached != flash.STAGE_SHA256:
        needs_you = (PUCK_RESET_LINE, PUCK_RESET_ACTION)
        _fire_needs_you(cfg, *needs_you)
        return False, needs_you
    return False, None


def run_job_cycle(port_path: str, cfg: DaemonConfig) -> JobCycleReport:
    """One plug-in, steps 1-9 (docs/sync-agent-plan.md:42-55).

    A fresh `stats` comes first. A puck with no ride on it (stored_jumps=0
    AND trace_bytes=0) is the everyday case -- Nick charges it every night --
    and the answer to it is silence: nothing pulled, nothing uploaded,
    nothing said. Only the update check runs, since that same reading is
    G2's "puck empty" clause.

    Otherwise serial_job.run_job() covers steps 1-5 (open, pull, verify,
    write the bundle) and never clears or uploads -- G1's other half, and
    step 7, are this function's job:

        PullFailed (the pull did not complete structurally) -> needs_you,
            nothing uploaded, nothing cleared, the puck untouched (G3).
        upload not confirmed (G1) -> NO clear, NO notification: the bundle
            stays in the spool root and retry_spool() owns it from here.
            The puck keeps its data, so the next plug-in pulls it again.
        upload confirmed AND verified -> clear_puck() (step 7); a clear
            that doesn't confirm -> needs_you (G4). Only a CONFIRMED clear
            says "Ride synced" (step 9): synced means safe on Drive AND off
            the puck, in that order.
        upload confirmed AND NOT verified -> nothing cleared, nothing said:
            the ride is safe on Drive and there is nothing for Nick to do
            about a content-level question; CONTRACT.md's own words for
            exactly this bundle -- "an unverified bundle is exactly the one
            Josh most wants to look at" -- are why it is Josh's Drive
            folder to find, not Nick's notification to receive.

    Step 8 (flash) only runs once clear_puck() has itself just confirmed
    the puck empty -- see _maybe_flash().

    Everything from the upload on is wrapped in a broad except so an
    unexpected exception out of upload.py/flash.py can never take the loop
    down (G3 applies to the daemon's own process staying up, not only to
    the puck's data). The bundle is already on disk by then; the spool
    retry owns it.
    """
    try:
        return _run_job_cycle(port_path, cfg)
    finally:
        cfg.on_phase(None)


def _run_job_cycle(port_path: str, cfg: DaemonConfig) -> JobCycleReport:
    cfg.on_phase("reading")
    pre = serial_job.read_stats(port_path, device_factory=cfg.device_factory)
    if pre.get("stored_jumps") == 0 and pre.get("trace_bytes") == 0:
        src = serial_job.read_src(port_path, device_factory=cfg.device_factory)
        try:
            flashed, needs_you = _maybe_flash(port_path, src, cfg)
        except Exception as exc:  # noqa: BLE001 -- the flash leg is the one
            # branch of the empty-puck path that reaches the network and a
            # third module; it must not be able to end the loop. (The other
            # _maybe_flash() call, below, is already inside the broad try.)
            _log(cfg, f"update check raised: {exc!r}")
            flashed, needs_you = False, None
        return JobCycleReport(
            port=port_path, pulled=False, verified=None, jumps=0, reasons=[],
            uploaded=False, cleared=False, flashed=flashed, needs_you=needs_you,
            bundle_path=None, src=src, empty=True, stats=pre,
        )

    try:
        result = serial_job.run_job(port_path, cfg.spool_dir, device_factory=cfg.device_factory)
    except serial_job.PullFailed as exc:
        _log(cfg, "pull failed: " + "; ".join(exc.reasons))
        # The port going away is not something Nick did wrong and not
        # something "press the button twice" fixes -- he pulled the cable, or
        # macOS renumbered the device. Nothing was cleared, the ride is still
        # on the puck, and the next plug-in runs the whole job again (G3), so
        # this one is silent. Every OTHER structural failure still gets the
        # one "check the puck" line.
        needs_you = None if getattr(exc, "port_gone", False) else (
            PUCK_RESET_LINE, PUCK_RESET_ACTION)
        if needs_you is not None:
            _fire_needs_you(cfg, *needs_you)
        return JobCycleReport(
            port=port_path, pulled=False, verified=None, jumps=None,
            reasons=list(exc.reasons), uploaded=False, cleared=False, flashed=False,
            needs_you=needs_you, bundle_path=None, src=None,
            stats=pre,
        )

    uploaded = False
    cleared = False
    flashed = False
    needs_you: "Optional[tuple[str, str]]" = None
    bundle_path: Path = Path(result.bundle_path)
    if not result.verified:
        _log(cfg, f"unverified: {bundle_path.name}: " + "; ".join(result.reasons))

    try:
        cfg.on_phase("uploading")
        uploaded, sent_path = _upload_bundle(cfg, bundle_path)
        if uploaded:
            bundle_path = sent_path

        if not uploaded:
            _drive_needs_you_if_due(cfg)
        elif result.verified:
            cfg.on_phase("emptying")
            clear_result = serial_job.clear_puck(port_path, device_factory=cfg.device_factory)
            cleared = clear_result.ok
            if not cleared:
                _log(cfg, "clear did not confirm")
                needs_you = (PUCK_RESET_LINE, PUCK_RESET_ACTION)
                _fire_needs_you(cfg, *needs_you)
            else:
                notify.notify("synced", runner=cfg.notifier, jumps=result.jumps)
                cfg.runtime["needs_you_active"] = False
                _log(cfg, f"ride synced: {result.jumps} jumps: {bundle_path.name}")
                flashed, flash_needs_you = _maybe_flash(port_path, result.src, cfg)
                needs_you = needs_you or flash_needs_you
        # else: uploaded but not verified -- see the docstring above.
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        _log(cfg, f"job raised after the pull: {exc!r}")

    return JobCycleReport(
        port=port_path, pulled=True, verified=result.verified, jumps=result.jumps,
        reasons=list(result.reasons), uploaded=uploaded, cleared=cleared, flashed=flashed,
        needs_you=needs_you, bundle_path=bundle_path, src=result.src, stats=pre,
    )


# --------------------------------------------------------------- Garmin leg

def _run_garmin(cfg: DaemonConfig) -> None:
    """docs/sync-agent-plan.md:57-58: "on every job and every 6 h ... Never
    blocks the puck job." Every exception below is swallowed -- an
    unofficial API (garmin.py's own module docstring: "it has broken and
    been fixed before") must never be able to take the puck loop down with
    it, and the caller (run_forever()) never awaits this beyond calling it.

    Token expiry fires docs/sync-agent-plan.md:39's needs_you exactly once
    per expiry -- cfg.runtime['garmin_needs_you_sent'] is cleared the
    moment is_signed_in() is true again, so a re-run of setup screen 3
    silences it without this function needing to know that happened.
    """
    try:
        g = cfg.garmin_module
        if not g.is_signed_in():
            # Skipped Garmin at setup -> never signed in -> never nagged.
            ever = getattr(g, "ever_signed_in", None)
            if ever is not None and not ever():
                return
            if not cfg.runtime.get("garmin_needs_you_sent"):
                _fire_needs_you(cfg, NEEDS_YOU_GARMIN_LINE, NEEDS_YOU_GARMIN_ACTION)
                cfg.runtime["garmin_needs_you_sent"] = True
            return
        cfg.runtime["garmin_needs_you_sent"] = False

        home = Path(cfg.home_dir)
        store = home / GARMIN_SEEN_FILENAME
        since_iso = g.last_seen(store)
        out_dir = home / FITS_CACHE_DIRNAME
        downloaded = g.fetch_new(since_iso, out_dir)
        # Upload every FIT on disk that Drive has not confirmed, not just
        # the ones downloaded THIS tick. The download side dedupes on the
        # file already being there, so a FIT whose upload failed once (wifi
        # down for the ten seconds rclone ran) was never offered to Drive
        # again -- it sat in the cache looking exactly like a success. The
        # `.sent` marker is the same idea as the puck spool's sent/
        # directory: a confirmation on disk, not an assumption.
        for fit_path in sorted(Path(out_dir).glob("*.zip")):
            marker = fit_path.with_name(fit_path.name + ".sent")
            if marker.exists():
                continue
            if getattr(upload.upload(fit_path, cfg.fits_dir), "ok", False):
                marker.write_text("")
        if downloaded:
            g.mark_seen(store, datetime.now().astimezone().isoformat(timespec="seconds"))
    except Exception:
        pass


# -------------------------------------------------------- attached polling

@dataclass
class AttachmentSession:
    """Per-plug-in tracking for step 10 -- reset by run_forever() every time
    the port disappears and a new one appears, so "fires exactly once" is
    "once per attachment", never once ever."""

    port: str
    prev_chg: "Optional[int]" = None
    charged_notified: bool = False
    last_stats_poll: float = 0.0
    last_puck_pct: "Optional[int]" = None
    last_puck_charging: bool = False
    last_stats: "Optional[dict]" = None


def poll_attached(port_path: str, cfg: DaemonConfig, session: AttachmentSession) -> dict:
    """One `stats` read while the puck sits attached (step 10). Fires
    "Puck charged" the one time chg goes 1->0 with batt_pct>=95 -- both
    conditions read off the SAME `stats` reply, never a stale one, and
    `session.charged_notified` makes it exactly-once per attachment even if
    the poll keeps running for hours afterward."""
    return apply_stats(
        serial_job.read_stats(port_path, device_factory=cfg.device_factory),
        cfg, session)


def apply_stats(stats: dict, cfg: DaemonConfig, session: AttachmentSession) -> dict:
    session.last_stats = stats
    """poll_attached()'s half that does not open the port, so a `stats`
    ALREADY read this tick (run_job_cycle()'s own first reading) can feed
    the charged rule and the menu bar without paying a second
    Device.drain_boot() -- 5 s per open, five opens a cycle, measured
    (docs/STATUS.md:703). Pure bookkeeping: same transition rule, same
    exactly-once flag."""
    if not isinstance(stats, dict):
        return {}
    chg = stats.get("chg")
    pct = stats.get("batt_pct")
    if (
        session.prev_chg == 1 and chg == 0
        and pct is not None and pct >= CHARGED_BATT_PCT_MIN
        and not session.charged_notified
    ):
        notify.notify("charged", runner=cfg.notifier)
        session.charged_notified = True
    if chg is not None:
        session.prev_chg = chg
    session.last_puck_pct = pct
    session.last_puck_charging = bool(chg)
    return stats


# ------------------------------------------------------------- menu bar

def _update_menubar(app, cfg: DaemonConfig, session: "Optional[AttachmentSession]") -> None:
    """Rewrite the two status lines menubar.py's PuckdApp.set_state() owns.
    `app` is None in every test that doesn't care about the menu bar
    (menubar.py's own design keeps rumps out of the import path unless
    build_app_class()/make_app() is actually called) -- a no-op then."""
    if app is None:
        return
    state = load_state(cfg.home_dir)
    last_ride_dt = None
    iso = state.get("last_ride_iso")
    if iso:
        try:
            last_ride_dt = datetime.fromisoformat(iso)
        except ValueError:
            last_ride_dt = None
    last_jumps = state.get("last_ride_jumps")
    pct = session.last_puck_pct if session else None
    charging = session.last_puck_charging if session else False
    attention = bool(cfg.runtime.get("needs_you_active"))
    phase = cfg.runtime.get("phase")
    try:
        app.set_state(pct, charging, last_ride_dt, last_jumps, attention,
                      phase=phase, attached=session is not None)
    except Exception as exc:  # noqa: BLE001 -- set_state() touches AppKit
        # (NSStatusItem) from the POLL THREAD, not the main thread. rumps
        # tolerates that in practice, but a label that fails to redraw must
        # never be able to cost the job that already succeeded.
        _log(cfg, f"menu bar update raised: {exc!r}")


# ----------------------------------------------------------------- the loop

def run_forever(
    cfg: DaemonConfig,
    *,
    find_port: "Callable[[], Optional[str]]" = find_puck_port,
    on_cycle: "Optional[Callable[[JobCycleReport], None]]" = None,
    app=None,
    max_iterations: "Optional[int]" = None,
) -> None:
    """The 2 s poll loop. A NEW port (one that wasn't the currently-attached
    one) runs run_job_cycle() once; the SAME port on later ticks only gets
    step 10's 60 s battery poll; the port going away resets attachment
    tracking so the next arrival is a fresh "plug-in" (a second plug-in
    after a failed upload is indistinguishable from a first one here -- it
    just runs the whole job again, which is what actually retries a failed
    upload: the puck was never cleared, so the fresh pull carries the same
    data forward until an upload finally confirms and clear_puck() runs).

    `max_iterations` bounds the loop for tests (and nothing else) --
    production (`daemon.main()`) never passes it, so the loop is
    unconditional there, exactly like every other "the daemon" in this
    repo's docs.
    """
    current_port: "Optional[str]" = None
    session_ref: "list" = [None]

    def on_phase(phase):
        cfg.runtime["phase"] = phase
        _update_menubar(app, cfg, session_ref[0])
    cfg.on_phase = on_phase
    session: "Optional[AttachmentSession]" = None
    last_garmin = cfg.now()
    last_spool = cfg.now()

    i = 0
    while max_iterations is None or i < max_iterations:
        i += 1
        try:
            port = find_port()
        except Exception as exc:  # noqa: BLE001 -- pyserial's comports() has
            # more failure modes than the ImportError find_puck_port() is
            # written for (a device node disappearing under the IOKit walk).
            # "No puck this tick" is the honest reading of a scan that did
            # not complete, and the loop must outlive it either way.
            _log(cfg, f"port scan raised (continuing): {exc!r}")
            port = None
        now = cfg.now()

        try:
            if port and port != current_port:
                current_port = port
                session = AttachmentSession(port=port, last_stats_poll=now)
                session_ref[0] = session
                report = run_job_cycle(port, cfg)
                if report.pulled and report.uploaded and report.verified:
                    _record_ride(cfg, report.jumps or 0, report.bundle_path)
                if on_cycle is not None:
                    on_cycle(report)
                if report.uploaded:
                    retry_spool(cfg)        # the network is evidently back
                publish_log(cfg, report, report.stats)
                # The cycle's own opening `stats` is this attachment's first
                # battery reading -- reused instead of opening the port again
                # for the same numbers (see apply_stats()).
                apply_stats(report.stats or {}, cfg, session)
                _update_menubar(app, cfg, session)
                last_garmin = now
                _run_garmin(cfg)
            elif port and port == current_port and session is not None:
                if now - session.last_stats_poll >= STATS_POLL_INTERVAL_S:
                    session.last_stats_poll = now
                    poll_attached(port, cfg, session)
                    _update_menubar(app, cfg, session)
            elif not port and current_port is not None:
                current_port = None
                session = None
                session_ref[0] = None
                _update_menubar(app, cfg, None)

            if now - last_garmin >= cfg.garmin_interval_s:
                last_garmin = now
                _run_garmin(cfg)
            if now - last_spool >= SPOOL_RETRY_INTERVAL_S:
                last_spool = now
                retry_spool(cfg)
                publish_log(cfg, None, session.last_stats if session else None)
        except Exception as exc:  # noqa: BLE001 -- THE LAST LINE OF DEFENCE.
            # main() runs this function on a DAEMON THREAD behind rumps: an
            # exception that reaches here ends the thread, and nothing says
            # so -- the menu bar stays up, the icon stays idle, and syncing
            # is dead until the app is quit and reopened. That is exactly
            # the silent failure CLAUDE.md rule 3 forbids. Measured
            # 2026-09-13 with a device_factory that raises OSError (an
            # unplug mid-cycle): the loop died on the first tick.
            # Everything below this line therefore survives one bad tick and
            # tries again in POLL_INTERVAL_S, with the reason on disk for
            # Josh. current_port is deliberately left as it is: a tick that
            # blew up is retried on the next arrival, not re-run in a spin.
            try:
                _log(cfg, f"loop tick raised (continuing): {exc!r}")
            except Exception:  # noqa: BLE001 -- the handler must outlive its own logging
                pass

        cfg.sleep(POLL_INTERVAL_S)


# ------------------------------------------------------------- `once`/setup

def run_once_report(port_path: "Optional[str]", cfg: DaemonConfig) -> str:
    """`python -m puckd once`'s plain-text report -- runs exactly one job
    cycle (no loop, no 60 s poll) and the Garmin leg, then returns a report
    a person reads on the bench, never a structure a script parses."""
    if port_path is None:
        port_path = find_puck_port()
        if port_path is None:
            return "no puck found -- plug it in (data cable), or pass a port"

    report = run_job_cycle(port_path, cfg)
    _run_garmin(cfg)

    lines = [f"port: {report.port}"]
    if report.empty:
        lines.append("puck: empty, nothing to sync")
        lines.append(f"src: {report.src}")
        lines.append(f"flashed: {report.flashed}")
        if report.needs_you:
            lines.append(f"needs_you: {report.needs_you[0]} -- {report.needs_you[1]}")
        return "\n".join(lines)
    if not report.pulled:
        lines.append("pull: FAILED")
        lines.extend(f"  - {r}" for r in report.reasons)
        return "\n".join(lines)

    lines.append(f"jumps: {report.jumps}")
    lines.append(f"verified: {report.verified}")
    for r in report.reasons:
        lines.append(f"  ! {r}")
    lines.append(f"bundle: {report.bundle_path}")
    lines.append(f"uploaded: {report.uploaded}")
    lines.append(f"cleared: {report.cleared}")
    lines.append(f"flashed: {report.flashed}")
    if report.needs_you:
        lines.append(f"needs_you: {report.needs_you[0]} -- {report.needs_you[1]}")
    return "\n".join(lines)


def _onboarding(app=None):
    """The native first-run window (tools/puckd/onboarding.py), wired to the
    real modules. Returns an object with .show(); None if AppKit is not
    importable (a stripped install), in which case there is no setup UI
    rather than a crashed daemon."""
    try:
        from puckd import onboarding
        model = onboarding.production_model()
        return onboarding.make_window(model)
    except Exception:  # noqa: BLE001
        return None


def run_setup() -> None:
    """`python -m puckd setup`: the onboarding window alone, no daemon."""
    from AppKit import NSApplication  # type: ignore

    nsapp = NSApplication.sharedApplication()
    win = _onboarding()
    if win is None:
        print("could not build the setup window (see tools/puckd/onboarding.py)")
        return
    win.model.on_finished = lambda: nsapp.terminate_(None)
    win.show()
    nsapp.run()


def main() -> None:
    """`python -m puckd`: the real, long-running agent. The menu bar owns
    the main thread (rumps.App.run()'s requirement); the poll loop runs on
    a daemon thread behind it. The first launch (no Drive remote yet) opens
    the setup window by itself; "Set up…" in the menu opens it again."""
    cfg = build_config()
    _log(cfg, f"agent started: site={cfg.site_url} home={cfg.home_dir}")
    win = _onboarding()
    app = menubar.make_app(spool_dir=cfg.spool_dir, opener=menubar.default_opener,
                           on_setup=(win.show if win is not None else None))
    t = threading.Thread(target=run_forever, args=(cfg,), kwargs={"app": app}, daemon=True)
    t.start()
    import rumps

    if win is not None and not upload.is_authorized():
        def first_run(_timer):
            _timer.stop()
            win.show()
        rumps.Timer(first_run, 1.0).start()      # once the run loop is up

    if win is not None:
        def icon_clicked(_timer):
            if consume_open_flag(cfg):
                win.show()
        rumps.Timer(icon_clicked, 1.0).start()   # a click on the app icon = show the window
    app.run()


def consume_open_flag(cfg: DaemonConfig) -> bool:
    """True once per click on the app icon: the launcher writes
    PUCKD_HOME/open-setup instead of restarting a running agent; the menu
    bar's timer removes it and shows the window. Never raises."""
    try:
        flag = Path(cfg.home_dir) / OPEN_SETUP_FLAG
        if flag.exists():
            flag.unlink()
            return True
    except OSError:
        pass
    return False


if __name__ == "__main__":  # pragma: no cover -- exercised via `python -m puckd`
    main()
