"""tools/puckd/selfupdate.py — the APP's own update, the mirror of flash.py.

flash.py updates the puck; this updates the thing that updates the puck.
The rider is an Intel Mac 300 miles away: he must never see a dialog, a
download, a Gatekeeper prompt, or a .dmg. So the running agent fetches its
own manifest, verifies it, swaps the bundle, and asks launchd to restart
it. Nothing is presented to a human at any point.

    current_version()          the INSTALLED bundle's
                               CFBundleShortVersionString, read from
                               Contents/Info.plist with plistlib. Only
                               meaningful when frozen (py2app sets
                               sys.frozen and RESOURCEPATH); a source
                               checkout is "0.0.0-dev", which compares
                               below every real release and is ALSO the
                               value apply() refuses to act on.
    latest_manifest(site_url)  GET <site>/app/latest.json. NEVER RAISES —
                               offline, a non-200, bad JSON, or a
                               mis-shaped body all read as "no update",
                               the same silence flash.latest_manifest()
                               (flash.py:133-175) uses for the firmware.
    needs_update(cur, man)     dotted-integer comparison, strictly greater.
    apply(manifest, ...)       download -> sha256 -> unpack -> stage ->
                               restart, in that order, every step
                               injectable, NEVER RAISING.

WHY app/latest.json is not next to firmware/latest.json's payload: GitHub
Pages caps a file at 100 MB and the zipped bundle is ~110 MB. So the
MANIFEST is served by Pages (same site_url the firmware manifest uses,
same netctx.ssl_context) and the ZIP lives on GitHub Releases, whose
download URL the manifest carries. packaging/release.sh writes both halves.

THE ONE RULE THIS MODULE EXISTS TO HOLD: the installed bundle is never
deleted, moved, or truncated until a complete, sha256-verified replacement
is sitting beside it. That is the same ordering packaging/src/launcher.py's
_copy_to_applications() (launcher.py:120-155) adopted after the rmtree-first
version was found able to leave /Applications with no app at all — reused
here rather than re-derived, because the failure it prevents is worse from
a daemon: there is no Finder window open to notice.

G3 (docs/sync-agent-plan.md's gates) is the caller's half: `launchctl
kickstart -k` kills this process outright, so daemon.py only ever calls
apply() on a tick where no job could still be touching the puck — no puck
attached at all, OR one whose CURRENT attachment has had no job of its own
for daemon.SELFUPDATE_IDLE_S (180 s; the rider was told to leave the puck
plugged in overnight, so "no puck attached" is not a condition that comes
true on its own any more). See daemon.run_forever()'s `puck_idle_long_enough`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

# The LaunchAgent label packaging/src/launcher.py:102 writes. Restated, not
# imported: launcher.py lives in the .app's own source, is not importable
# from tools/puckd/, and CLAUDE.md §4 wants the identifier to have a lookup
# entry in both places rather than one of them quietly drifting.
LABEL = "com.jumpheight.puckd"

DEV_VERSION = "0.0.0-dev"

_MANIFEST_PATH = "/app/latest.json"     # sibling of flash.py:71's "/firmware/latest.json"
_MANIFEST_TIMEOUT_S = 10.0
_DOWNLOAD_TIMEOUT_S = 600.0             # ~110 MB over a rider's home DSL

_VERSION_RE = re.compile(r"^[0-9]+(\.[0-9]+)*$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# Stage vocabulary — UpdateResult.stage names how far an attempt got, in
# the order they run. Also what the daemon log prints, so a "stage=sha256"
# line read over the phone means "it downloaded, and the bytes were wrong".
STAGE_FROZEN = "frozen"
STAGE_DOWNLOAD = "download"
STAGE_SHA256 = "sha256"
STAGE_UNPACK = "unpack"
STAGE_STAGE = "stage"
STAGE_RESTART = "restart"

BUNDLE_NAME = "JumpHeight Sync.app"


@dataclass(frozen=True)
class UpdateResult:
    ok: bool
    stage: str
    error: Optional[str] = None
    version: Optional[str] = None


# ---------------------------------------------------------------- versions

def is_frozen() -> bool:
    """True only inside the py2app bundle. py2app sets both sys.frozen and
    RESOURCEPATH; either one alone is somebody else's freezer, and this
    module refuses to replace anything it cannot positively identify."""
    return bool(getattr(sys, "frozen", False)) and bool(os.environ.get("RESOURCEPATH"))


def installed_bundle_path() -> "Optional[Path]":
    """.../JumpHeight Sync.app, derived from RESOURCEPATH
    (.../Contents/Resources). None when not frozen — there is no bundle to
    replace in a source checkout, and guessing one is how a dev tree gets
    overwritten."""
    rp = os.environ.get("RESOURCEPATH")
    if not rp:
        return None
    bundle = Path(rp).resolve().parent.parent      # Resources -> Contents -> .app
    return bundle if bundle.suffix == ".app" else None


def current_version(*, bundle_path: "Optional[Path]" = None) -> str:
    """CFBundleShortVersionString out of the INSTALLED bundle's Info.plist —
    the same key packaging/setup.py:APP_VERSION writes at build time, read
    back from the thing actually running rather than from a constant this
    file would have to be kept in step with.

    Never raises: a missing plist, an unreadable one, or a value that is
    not a dotted-integer version all read as DEV_VERSION, which compares
    below every release AND is refused by apply(). A version we could not
    read must never be able to look newer than it is."""
    if bundle_path is None:
        if not is_frozen():
            return DEV_VERSION
        bundle_path = installed_bundle_path()
    if bundle_path is None:
        return DEV_VERSION
    try:
        import plistlib
        with open(Path(bundle_path) / "Contents" / "Info.plist", "rb") as f:
            plist = plistlib.load(f)
        value = plist.get("CFBundleShortVersionString")
    except Exception:  # noqa: BLE001 -- see docstring: unreadable == dev
        return DEV_VERSION
    if not isinstance(value, str) or not _VERSION_RE.match(value.strip()):
        return DEV_VERSION
    return value.strip()


_RUNNING_VERSION: "Optional[str]" = None


# The one place a build may come from. The manifest carries the sha256 of
# the file it names, so a manifest-only compromise could name any file and
# its own hash; pinning the host means it can at least only name a file
# that someone with write access to THIS repository's releases put there.
RELEASE_URL_PREFIX = "https://github.com/joshcrow/Jump-height/releases/download/"


def running_version() -> str:
    """The version of the code THIS PROCESS IS EXECUTING — which is not the
    same question as current_version().

    current_version() reads Info.plist off disk. apply() replaces that
    bundle underneath a still-running process, so from the instant a swap
    lands until launchd actually restarts us, the plist says 1.0.1 while
    this interpreter is still running 1.0.0's code. Two things were wrong
    because of that:

      * a kickstart that did not fire (apply() returns ok=True,
        stage=restart) left needs_update() answering False forever, so the
        restart was never retried and the rider kept running the old code;
      * status.json's app_version — the one answer to "which build is he
        actually running?", read by Josh from 300 miles away — named the
        build on disk, not the one running. CLAUDE.md rule 3.

    So this memoises the FIRST reading, which is taken before any swap can
    have happened, and never changes for the life of the process."""
    global _RUNNING_VERSION
    if _RUNNING_VERSION is None:
        _RUNNING_VERSION = current_version()
    return _RUNNING_VERSION


def _parse_version(text: "str | None") -> "tuple[int, ...]":
    """"1.0.10" -> (1, 0, 10). Non-numeric junk in a component contributes
    its leading digits only, so DEV_VERSION ("0.0.0-dev") parses as
    (0, 0, 0) and loses to every release. An unparseable string is (-1,),
    below even that."""
    if not isinstance(text, str):
        return (-1,)
    parts = []
    for chunk in text.strip().split("."):
        m = re.match(r"^[0-9]+", chunk)
        parts.append(int(m.group(0)) if m else 0)
    return tuple(parts) if parts else (-1,)


def _compare(a: str, b: str) -> int:
    """-1/0/1 for a<b / a==b / a>b, zero-padding the shorter side so
    "1.0" == "1.0.0"."""
    ta, tb = _parse_version(a), _parse_version(b)
    n = max(len(ta), len(tb))
    ta = ta + (0,) * (n - len(ta))
    tb = tb + (0,) * (n - len(tb))
    return (ta > tb) - (ta < tb)


# ---------------------------------------------------------------- manifest

def latest_manifest(site_url: str) -> "dict | None":
    """Fetch <site_url>/app/latest.json.

    NEVER RAISES, exactly as flash.latest_manifest() does not: offline, a
    non-200, a body that isn't JSON, a JSON value that isn't an object, a
    `version` that isn't dotted integers, or a `url` that isn't an https
    URL all return None, which needs_update() reads as "no update".

    A manifest whose `sha256` is absent or malformed is NOT rejected here —
    it is passed through so apply()'s gate is the one place "can't verify
    these bytes" gets named, rather than that collapsing into the same
    silence a missing manifest produces (flash.py:140-148's reasoning,
    applied to the same shape of problem)."""
    url = site_url.rstrip("/") + _MANIFEST_PATH
    try:
        from puckd import netctx
        with urllib.request.urlopen(url, timeout=_MANIFEST_TIMEOUT_S,
                                    context=netctx.ssl_context()) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                return None
            body = resp.read()
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None

    try:
        manifest = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
        return None
    if not isinstance(manifest, dict):
        return None

    version = manifest.get("version")
    asset = manifest.get("url")
    if not isinstance(version, str) or not _VERSION_RE.match(version.strip()):
        return None
    if not isinstance(asset, str) or not asset.startswith(RELEASE_URL_PREFIX):
        return None
    return manifest


def needs_update(current: str, manifest: "dict | None") -> bool:
    """True only when the manifest names a STRICTLY greater version. A
    missing manifest, an unparseable one, or an equal/older version is
    False — never a guess in the direction that replaces a working app."""
    if not isinstance(manifest, dict):
        return False
    latest = manifest.get("version")
    if not isinstance(latest, str) or not _VERSION_RE.match(latest.strip()):
        return False
    return _compare(latest.strip(), current) > 0


# ------------------------------------------------------------- the defaults
#
# Every one of these is an apply() keyword argument with this as its
# default, so tools/tests/test_puckd_selfupdate.py drives the whole
# sequence — including the two failure paths that must leave the installed
# bundle byte-identical — with no network, no zip, and no launchd. Same
# seam pattern as flash.flash() (flash.py:253-267).

def _default_download(url: str, dest: Path) -> None:
    """Stream the asset to dest.part, then rename into place, so a half
    download can never be mistaken for a complete one by the sha256 stage
    (it would fail the gate anyway; this just stops it being kept)."""
    from puckd import netctx
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_S,
                                context=netctx.ssl_context()) as resp:
        status = getattr(resp, "status", None) or resp.getcode()
        if status != 200:
            raise OSError(f"HTTP {status} for {url}")
        with open(tmp, "wb") as f:
            shutil.copyfileobj(resp, f, 1024 * 256)
    tmp.replace(dest)


def _default_unpack(zip_path: Path, dest_dir: Path) -> None:
    """`ditto -x -k` first: it is what macOS itself uses and it preserves
    the symlinks and extended attributes inside a .app that zipfile drops
    (a Frameworks/Python.framework/Versions/Current symlink turned into a
    directory copy is a bundle that still launches but is twice the size).
    zipfile is the fallback for a machine with no ditto — never the first
    choice."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(["ditto", "-x", "-k", str(zip_path), str(dest_dir)],
                              capture_output=True, encoding="utf-8", errors="replace", timeout=600)
        if proc.returncode == 0:
            return
    except (OSError, subprocess.TimeoutExpired):
        pass
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest_dir)


def _default_kickstart() -> bool:
    """`launchctl kickstart -k gui/<uid>/com.jumpheight.puckd` — the same
    domain and label packaging/src/launcher.py:189,258 uses. -k kills the
    running service first, so THIS process does not return from here; the
    new bundle's executable is what launchd starts. True/False is only ever
    observed when the kill did not happen."""
    try:
        proc = subprocess.run(
            ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{LABEL}"],
            capture_output=True, timeout=30)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _find_app(unpack_dir: Path) -> "Optional[Path]":
    """The zip is built with `ditto -c -k --keepParent`, so "JumpHeight
    Sync.app" sits at its root. Accept the exact name first, then a single
    *.app child — but never two, because picking one of two would be a
    guess about which app to install."""
    exact = unpack_dir / BUNDLE_NAME
    if exact.is_dir():
        return exact
    apps = sorted(p for p in unpack_dir.iterdir() if p.suffix == ".app" and p.is_dir())
    return apps[0] if len(apps) == 1 else None


def _bundle_complaint(app: Path, version: str) -> "Optional[str]":
    """Why `app` must NOT be swapped in, or None if it may be.

    The sha256 gate proves the bytes are the bytes the manifest named. It
    proves nothing about whether those bytes are a launchable app: a zip
    built from a half-finished tree, or one whose CFBundleShortVersionString
    does not match what the manifest advertises, passes it intact. Both
    end the same way and there is no way back — the swap lands, launchd
    restarts into a bundle that cannot start, and the agent is gone from a
    Mac 300 miles away with no Finder window to notice.

    Two readings, both off the staged copy and both cheap:

      structure   Contents/Info.plist and at least one file under
                  Contents/MacOS/ (the executable launchd's plist names).
      version     CFBundleShortVersionString == the manifest's version.
                  An Info.plist we cannot read is NOT a complaint (a
                  bundle can be valid and unreadable to plistlib here);
                  a version we CAN read and that disagrees is, because
                  that is exactly a release.sh run whose APP_VERSION bump
                  did not take, and installing it makes needs_update()
                  true forever: a rider's Mac kickstarting itself every
                  six hours, silently, for good."""
    if not (app / "Contents" / "Info.plist").is_file():
        return "staged bundle has no Contents/Info.plist"
    macos = app / "Contents" / "MacOS"
    if not macos.is_dir() or not any(p.is_file() for p in macos.iterdir()):
        return "staged bundle has no executable under Contents/MacOS"
    staged_version = current_version(bundle_path=app)
    if (staged_version != DEV_VERSION and version
            and _compare(staged_version, version) != 0):
        return (f"staged bundle is version {staged_version}, manifest says "
                f"{version} — refusing to install a build that does not "
                f"match the version it is published as")
    return None


SPACE_FACTOR = 4   # zip + unpacked copy + staged copy + headroom, in zip sizes


def _space_needed(manifest: dict) -> int:
    size = manifest.get("bytes") if isinstance(manifest, dict) else None
    size = size if isinstance(size, int) and size > 0 else 120_000_000
    return size * SPACE_FACTOR


def free_bytes(path: "Path | str") -> "Optional[int]":
    """Free bytes on the volume holding `path`; None if it cannot be read."""
    import shutil
    try:
        return shutil.disk_usage(str(path)).free
    except OSError:
        return None


def _prune_downloads(download_dir: Path, keep: str) -> None:
    """Everything in PUCKD_HOME/updates that is not this attempt's. A
    failed unpack leaves a <version>.zip behind and a failed download a
    <version>.zip.part; without this they accumulate one ~110 MB file per
    release that never installed, on the disk of somebody who will never
    look in that directory. Never raises."""
    try:
        for child in Path(download_dir).iterdir():
            name = child.name
            if name in (f"{keep}.zip", f"{keep}.zip.part", keep, ".lock"):
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                _unlink(child)
    except OSError:
        pass


class _AlreadyRunning(Exception):
    pass


def _update_lock(download_dir: Path):
    """An exclusive, non-blocking lock so two agents cannot swap the same
    bundle at once.

    That is not hypothetical: packaging/src/launcher.py hands off to
    launchd, and a rider who opens the app again (or whose old LaunchAgent
    copy is still registered) can have two frozen processes alive with the
    SAME RESOURCEPATH. Both would download to the same
    updates/<version>.zip — interleaved writes, a sha256 that fails for
    reasons nothing explains — and then both would rename the same
    installed bundle, where the loser's rollback deletes the winner's app.

    flock is held by the KERNEL on behalf of the process, so the one exit
    this module cannot tidy up after — `launchctl kickstart -k` killing us
    mid-apply — releases it for free. No stale lock is possible."""
    try:
        import fcntl
    except ImportError:      # not macOS: no second agent to race with
        return None
    Path(download_dir).mkdir(parents=True, exist_ok=True)
    fh = open(Path(download_dir) / ".lock", "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        raise _AlreadyRunning("another agent is already applying an update")
    return fh


# ------------------------------------------------------------------- apply

def apply(
    manifest: dict,
    *,
    bundle_path: "Path | str | None",
    download_dir: "Path | str",
    log: "Callable[[str], None]" = lambda msg: None,
    frozen: "Optional[bool]" = None,
    download_fn: "Callable[[str, Path], None]" = _default_download,
    unpack_fn: "Callable[[Path, Path], None]" = _default_unpack,
    copytree_fn: "Callable[[Path, Path], None] | None" = None,
    rename_fn: "Callable[[Path, Path], None]" = os.rename,
    rmtree_fn: "Callable[[Path], None] | None" = None,
    kickstart_fn: "Callable[[], bool]" = _default_kickstart,
    free_bytes_fn=free_bytes,
) -> UpdateResult:
    """Download, verify, and swap in the app named by `manifest`.

    NEVER RAISES. Every failure is an UpdateResult(ok=False, stage=...,
    error=...) whose stage says exactly how far it got — CLAUDE.md rule 3:
    an update that did not happen must not look like one that did.

    ORDER, and why each step is where it is:

      frozen    A source checkout is refused outright. `bundle_path` is not
                trusted to be a bundle just because a caller passed one.
      download  To download_dir/<version>.zip. Not resumable: a rider's
                110 MB over a night is cheaper than the code that would
                resume it, and a truncated file fails the next gate.
      sha256    THE GATE. Nothing is unpacked, and nothing beside the
                installed bundle is created, until the bytes match
                manifest['sha256']. A missing or malformed sha256 in the
                manifest fails here too — the same refusal flash() makes
                at flash.py:303-306 rather than trusting an unverified
                image.
      unpack    into download_dir/<version>/. Still nothing touched in
                /Applications.
      stage     copy the unpacked bundle to "<installed>.app.new", rename
                installed -> ".app.old", ".app.new" -> installed, delete
                ".app.old". launcher.py:120-155's ordering. A failure at
                ANY point here rolls back so the installed bundle is
                exactly what it was.
      restart   launchctl kickstart -k. This process does not survive it.

    ok=True with a non-None error means the swap LANDED and only the
    restart did not — the new version takes effect at the next launch.
    """
    if copytree_fn is None:
        def copytree_fn(src: Path, dst: Path) -> None:      # noqa: D401
            shutil.copytree(src, dst, symlinks=True)
    if rmtree_fn is None:
        def rmtree_fn(p: Path) -> None:
            shutil.rmtree(p, ignore_errors=True)

    version = str((manifest or {}).get("version") or "")

    if frozen is None:
        frozen = is_frozen()
    if not frozen:
        log("selfupdate: not a frozen bundle — refusing to replace a dev checkout")
        return UpdateResult(False, STAGE_FROZEN,
                            "not frozen: a source checkout is never replaced", version)
    if bundle_path is None:
        return UpdateResult(False, STAGE_FROZEN, "no installed bundle path", version)
    bundle_path = Path(bundle_path)
    if not bundle_path.is_dir():
        return UpdateResult(False, STAGE_FROZEN,
                            f"installed bundle not found: {bundle_path}", version)

    url = (manifest or {}).get("url")
    if not isinstance(url, str) or not url.startswith(RELEASE_URL_PREFIX):
        return UpdateResult(False, STAGE_DOWNLOAD, "manifest has no https url", version)

    # ---- Stage 0: is it already sitting there? -------------------------
    # The swap can land and the kickstart still fail (launchd refusing the
    # domain, a bootout in flight). The bundle on disk is then ALREADY this
    # version while this process is still running the old code, and what is
    # owed is a restart, not another 110 MB download every six hours until
    # the rider next logs out. running_version() is what keeps
    # needs_update() true across that gap.
    if version and _compare(current_version(bundle_path=bundle_path), version) == 0:
        log(f"selfupdate: {version} is already installed; asking launchd to restart")
        try:
            ok = bool(kickstart_fn())
        except Exception as exc:  # noqa: BLE001
            return UpdateResult(True, STAGE_RESTART, repr(exc), version)
        return UpdateResult(True, STAGE_RESTART,
                            None if ok else "kickstart returned non-zero", version)

    # One agent at a time. Held until this call returns, or until the
    # kernel releases it because kickstart -k killed us.
    try:
        lock = _update_lock(Path(download_dir))
    except _AlreadyRunning as exc:
        log("selfupdate: another agent holds the update lock — skipping this check")
        return UpdateResult(False, STAGE_DOWNLOAD, str(exc), version)
    except OSError as exc:
        return UpdateResult(False, STAGE_DOWNLOAD, repr(exc), version)
    try:
        return _apply_locked(
            manifest, version=version, url=url, bundle_path=bundle_path,
            download_dir=Path(download_dir), log=log, download_fn=download_fn,
            unpack_fn=unpack_fn, copytree_fn=copytree_fn, rename_fn=rename_fn,
            rmtree_fn=rmtree_fn, kickstart_fn=kickstart_fn, free_bytes_fn=free_bytes_fn)
    finally:
        try:
            if lock is not None:
                lock.close()
        except OSError:
            pass


def _apply_locked(
    manifest: dict, *, version: str, url: str, bundle_path: Path,
    download_dir: Path, log, download_fn, unpack_fn, copytree_fn, rename_fn,
    rmtree_fn, kickstart_fn,
    free_bytes_fn=free_bytes,
) -> UpdateResult:
    """apply()'s body, with the lock held. Split out only so the lock has a
    single, unmissable release point; the ordering and every guarantee in
    apply()'s docstring are here."""
    # ---- Stage 1: download -------------------------------------------
    try:
        down = Path(download_dir)
        down.mkdir(parents=True, exist_ok=True)
        zip_path = down / f"{version or 'update'}.zip"
        _prune_downloads(down, version or "update")
        need = _space_needed(manifest)
        free = free_bytes_fn(down)
        if free is not None and free < need:
            # Measured on the rider's Mac 2026-09-14: a full disk failed the
            # download, then the unpack, every six hours, with nothing said
            # in his words. Say the number once and skip the 110 MB attempt.
            return UpdateResult(False, STAGE_DOWNLOAD,
                                f"not enough free disk: {free // 1_000_000} MB free, "
                                f"about {need // 1_000_000} MB needed", version)
        log(f"selfupdate: downloading {version} from {url}")
        download_fn(url, zip_path)
        if not zip_path.is_file():
            return UpdateResult(False, STAGE_DOWNLOAD, "download produced no file", version)
    except Exception as exc:  # noqa: BLE001 -- never raises (see docstring)
        log(f"selfupdate: download failed: {exc!r}")
        return UpdateResult(False, STAGE_DOWNLOAD, repr(exc), version)

    # ---- Stage 2: sha256, THE GATE, before anything is unpacked -------
    expected = (manifest or {}).get("sha256")
    if not isinstance(expected, str) or not _SHA256_RE.match(expected.strip()):
        log("selfupdate: manifest has no usable sha256 — refusing to unpack")
        _unlink(zip_path)
        return UpdateResult(False, STAGE_SHA256, "manifest has no usable sha256", version)
    try:
        actual = _sha256_file(zip_path)
    except OSError as exc:
        return UpdateResult(False, STAGE_SHA256, repr(exc), version)
    if actual.lower() != expected.strip().lower():
        log(f"selfupdate: sha256 mismatch ({actual} != {expected.strip()}) — nothing unpacked")
        _unlink(zip_path)     # a wrong file is never kept to be retried as-is
        return UpdateResult(False, STAGE_SHA256,
                            f"sha256 mismatch: got {actual}, manifest says {expected.strip()}",
                            version)
    log(f"selfupdate: sha256 ok ({actual[:12]}…)")

    # ---- Stage 3: unpack ---------------------------------------------
    unpack_dir = Path(download_dir) / (version or "update")
    try:
        rmtree_fn(unpack_dir)
        unpack_fn(zip_path, unpack_dir)
        new_app = _find_app(unpack_dir)
        if new_app is None:
            return UpdateResult(False, STAGE_UNPACK,
                                f"no single .app at the root of {zip_path.name}", version)
        complaint = _bundle_complaint(new_app, version)
        if complaint is not None:
            log(f"selfupdate: {complaint} — nothing swapped")
            return UpdateResult(False, STAGE_UNPACK, complaint, version)
    except Exception as exc:  # noqa: BLE001
        log(f"selfupdate: unpack failed: {exc!r}")
        return UpdateResult(False, STAGE_UNPACK, repr(exc), version)
    log(f"selfupdate: unpacked {new_app.name}")

    # ---- Stage 4: stage — the installed bundle's only dangerous moment
    staged = bundle_path.with_name(bundle_path.name + ".new")
    old = bundle_path.with_name(bundle_path.name + ".old")
    try:
        rmtree_fn(staged)
        rmtree_fn(old)
        copytree_fn(new_app, staged)
    except Exception as exc:  # noqa: BLE001 -- installed bundle NOT touched yet
        log(f"selfupdate: staging copy failed: {exc!r}")
        rmtree_fn(staged)
        return UpdateResult(False, STAGE_STAGE, repr(exc), version)

    moved_aside = False
    try:
        rename_fn(bundle_path, old)                 # same volume: atomic
        moved_aside = True
        rename_fn(staged, bundle_path)
    except Exception as exc:  # noqa: BLE001
        log(f"selfupdate: swap failed: {exc!r} — rolling back")
        if moved_aside and not bundle_path.exists():
            try:
                rename_fn(old, bundle_path)         # the installed app, back
            except Exception as exc2:  # noqa: BLE001 -- nothing left to try
                log(f"selfupdate: ROLLBACK FAILED: {exc2!r}; app is at {old}")
                return UpdateResult(False, STAGE_STAGE,
                                    f"{exc!r}; rollback failed: {exc2!r}", version)
        rmtree_fn(staged)
        return UpdateResult(False, STAGE_STAGE, repr(exc), version)

    rmtree_fn(old)
    rmtree_fn(unpack_dir)
    _unlink(zip_path)
    log(f"selfupdate: {version} is in place at {bundle_path}")

    # ---- Stage 5: restart. This call does not return on a live Mac. ---
    try:
        ok = bool(kickstart_fn())
    except Exception as exc:  # noqa: BLE001
        log(f"selfupdate: kickstart raised: {exc!r}")
        return UpdateResult(True, STAGE_RESTART, repr(exc), version)
    if not ok:
        log("selfupdate: kickstart did not report success — the new version "
            "takes effect at the next launch")
        return UpdateResult(True, STAGE_RESTART, "kickstart returned non-zero", version)
    return UpdateResult(True, STAGE_RESTART, None, version)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _unlink(path: Path) -> None:
    try:
        Path(path).unlink()
    except OSError:
        pass
