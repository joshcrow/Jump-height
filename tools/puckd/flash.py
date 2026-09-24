"""tools/puckd/flash.py — the puck's own software update (spec step 8).

docs/sync-agent-plan.md:41-58 (the job) and :60-66 (the gates) are the
contract. This module owns exactly:

    latest_manifest(site_url)          docs/sync-agent-plan.md:51 — fetch
                                        <site>/firmware/latest.json. NEVER
                                        RAISES: any failure to fetch, parse,
                                        or shape-check reads as "no update",
                                        the same silence web/sync/sync.js's
                                        loadFirmwareManifest() (:2361-2379)
                                        uses for a broken manifest.
    needs_update(puck_src, manifest)   docs/sync-agent-plan.md:51's own
                                        words: "puck src != latest.src".
    flash(port_path, uf2_path,
          manifest)                    the sequence measured end-to-end on
                                        silicon 2026-09-11
                                        (docs/serial-parity-2026-09-09.md:
                                        315-354) and specified at
                                        docs/sync-agent-plan.md:52-54.

Reuses tools/jump's Device / command() / scan_ports() / parse_kv() — proven
on hardware — instead of reimplementing the serial protocol. tools/jump has
no .py extension, so it is loaded with importlib the same way
tools/tests/test_ingest.py's and test_cli.py's own _load_jump_module()
helpers do.

flash()'s every filesystem/diskutil/serial action is an injectable keyword
argument with a real-world default (real diskutil, real shutil.copy2, real
tools/jump Device/scan_ports, real time.sleep/monotonic), so
tools/tests/test_puckd_flash.py pins the whole sequence — every timeout,
the sha256 refusal (G2), the present-but-unmounted mount, and the
copy-error-is-success rule (docs/serial-parity-2026-09-09.md:336-340) —
without a puck on the bench. Silicon rehearsal of THIS module (as opposed
to the CLI path already proven) remains the owner's step (plan's P4).

Gate G2 (docs/sync-agent-plan.md:62): "never flash unless: puck empty ...
AND src older than latest.json AND sha256 of the .uf2 matches latest.json".
The "puck empty" and "src differs" halves are the caller's job (needs_update()
here; stored_jumps/trace_bytes==0 is serial_job.py's reading, not this
module's) — flash() itself enforces exactly the sha256 half, first, before
it does anything else to the device or the filesystem.
"""

from __future__ import annotations

import datetime
import errno
import os
import pathlib
import fnmatch
import hashlib
import importlib.machinery
import json
import re
import shutil
import sys
import subprocess
import time
import types
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parent.parent.parent
JUMP = str(REPO / "tools" / "jump")

# web/sync/sync.js:184 — the UF2 bootloader's fixed mass-storage volume name,
# confirmed on silicon 2026-09-11 (docs/serial-parity-2026-09-09.md:331-333).
UF2_VOLUME_NAME = "XIAO-SENSE"
UF2_VOLUME_PATH = Path("/Volumes") / UF2_VOLUME_NAME

_MANIFEST_PATH = "/firmware/latest.json"  # docs/sync-agent-plan.md:51
_MANIFEST_TIMEOUT_S = 10.0

# web/sync/sync.js:2378-2379's own shape check, byte-for-byte: `src` is the
# hex build hash INFO reports, `file` a bare filename ending .uf2 (pasted
# into a download href there; kept here only so a manifest this module
# would refuse is refused for the SAME reason the page refuses it).
_SRC_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+\.uf2$")
# The same rule for the serial-DFU package `dfu_file` names. A bare
# filename ending .zip: it is pasted onto <site>/firmware/ to build a URL,
# so a path separator or a `..` in it must never survive this check.
_DFU_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+\.zip$")

# WHERE THE PACKAGE IS FETCHED FROM. daemon.py:109-110 holds the identical
# pair (DEFAULT_SITE_URL / SITE_URL_ENV) for its own .uf2 fetch and cannot
# be imported here (daemon imports flash, not the other way round), so the
# two copies are pinned together by
# tools/tests/test_puckd_flash.py::TestSiteUrlMatchesTheDaemon, which fails
# if either side is edited alone. CLAUDE.md §4.
DEFAULT_SITE_URL = "https://joshcrow.github.io/Jump-height"
SITE_URL_ENV = "PUCKD_SITE_URL"

VOLUME_WAIT_S = 30.0   # docs/sync-agent-plan.md:52
PORT_WAIT_S = 60.0     # docs/sync-agent-plan.md:54
_POLL_INTERVAL_S = 0.5

# ---------------------------------------------------------------- USB ids
#
# THE FINDING OF 2026-09-15 (docs/STATUS.md, "The post-`uf2` silent puck is
# explained"): after `uf2` the XIAO enters its Adafruit bootloader EVERY
# time, and the bootloader keeps the app's USB product string, its USB
# serial AND its /dev/cu.usbmodemN name. The ONLY thing that changes is the
# USB product id. So a puck that "opens but answers nothing" is not a dead
# puck and not a puck that ignored `uf2` — those are different faults with
# different fixes, and idProduct is the one reading that tells them apart.
USB_PRODUCT_NAME = "XIAO nRF52840 Sense"
PID_APP = 0x8045          # measured: ioreg idProduct 32837, the app running
PID_BOOTLOADER = 0x0045   # measured: ioreg idProduct 69, UF2 bootloader 0.11.0

# `ioreg -p IOUSB -l -w0` measured at ~18 ms on this Mac (2026-09-15), so it
# is cheap enough to read on a failure path without a budget argument.
_IOREG_ARGV = ["ioreg", "-p", "IOUSB", "-l", "-w0"]
_IOREG_TIMEOUT_S = 10.0
# The tree header line of one USB device, e.g.
#   |   +-o XIAO nRF52840 Sense@00100000  <class IOUSBHostDevice, id 0x…>
_IOREG_DEVICE_RE = re.compile(
    r"\+-o\s+(?P<name>[^@]*)@(?P<loc>[0-9a-fA-F]+)\s+<class\s+IOUSBHostDevice")
_IOREG_PID_RE = re.compile(r'"idProduct"\s*=\s*(\d+)')

# The three sentences a volume_wait timeout may end in. They are not for
# Nick — daemon.py gives him the one "Needs you" line the spec fixes
# (docs/sync-agent-plan.md:44) whatever the cause — they are for the
# daemon.log Josh reads over the phone, and for FlashResult.error.
_WHY_NO_UPDATE_MODE = "the puck did not enter update mode"
_WHY_NO_DISK = "the puck is in update mode but this Mac made no disk"
_WHY_NOT_ON_USB = "puck not found on USB"

# ------------------------------------------------------- the serial DFU
#
# The bootloader's CDC port is live the whole time macOS is failing to
# publish its disk, and it speaks Nordic serial DFU (measured on the bench
# Puck 2026-09-15: `Device programmed.` in 10.2 s). That is the unattended
# way out of the wedge; the 1200-baud touch is not (main.cpp:1309 — the
# UF2 magic is MSC-only by design, and blprobe 2026-09-15 measured the
# touch leaving idProduct at 0x0045, unchanged).
#
# G3 (docs/sync-agent-plan.md:62) still holds: the package this sends is an
# APPLICATION-only DFU package (`--application`, manifest type
# "application"), so nothing here writes the bootloader or the SoftDevice.
DFU_TIMEOUT_S = 180.0
_DFU_BAUD = "115200"   # the board's own upload speed (platformio board json)
# How long the fallback waits for the bootloader's own CDC node to exist
# before giving up on it. Measured: it appeared 0.23 s after idProduct
# flipped (0.68 s -> 0.91 s from the `uf2`), so this is 20x the measurement.
_DFU_PORT_WAIT_S = 5.0
# bench-playbook.md:150-152, the trap this repo already paid for: "pio's
# uploader lies. It prints SUCCESS over a failed adafruit-nrfutil. Trust
# only the literal `Device programmed.` line." Measured again 2026-09-15:
# a serial DFU that died at packet 23 with "No data received on serial
# port" STILL EXITED 0. The marker is the only verdict.
_DFU_OK_MARKER = "Device programmed."
_NRFUTIL_NAME = "adafruit-nrfutil"
# Where it is on this bench. shutil.which() is tried first; this is the
# fallback because the launchd agent inherits no interactive PATH.
_NRFUTIL_FALLBACK = ("/Library/Frameworks/Python.framework/Versions/3.14/"
                     "bin/adafruit-nrfutil")

# daemon.py:115's LOG_FILENAME and garmin.py:94's DEFAULT_HOME, restated for
# the default log sink — see _default_log() for why this module needs one of
# its own. Pinned to the originals by test_puckd_flash.py, same as the site
# URL above.
_LOG_FILENAME = "daemon.log"
_DEFAULT_HOME = Path("~/Library/Application Support/JumpHeight").expanduser()
_PUCKD_HOME_ENV = "PUCKD_HOME"

# CONTRACT.md:789-791: the firmware prints "OK uf2", waits 250 ms, THEN
# reboots — so the command can land normally. serial-parity-2026-09-09.md:
# 328-330 measured the drop as effectively instant from the caller's side
# regardless; either way is handled identically below (see _send_uf2()).
_UF2_SEND_TIMEOUT_S = 5.0
_INFO_TIMEOUT_S = 10.0

_jump_module = None  # cached: tools/jump's top level has no I/O side effects
                      # (just class/def statements) — see flash.py's own
                      # review notes; safe to load once per process.


def _load_jump_module():
    """Load tools/jump (no .py extension) for its proven Device / scan_ports
    / parse_kv — same technique as tools/tests/test_ingest.py's helper of
    the same name. Cached: this execs ~4800 lines of pure definitions with
    no import-time I/O, so re-running it per flash() call would be wasted
    work in a long-lived daemon, not a correctness issue either way."""
    global _jump_module
    if _jump_module is None:
        loader = importlib.machinery.SourceFileLoader("jumpcli_puckd_flash", JUMP)
        mod = types.ModuleType("jumpcli_puckd_flash")
        mod.__file__ = JUMP
        loader.exec_module(mod)
        _jump_module = mod
    return _jump_module


@dataclass(frozen=True)
class FlashResult:
    ok: bool
    src_after: Optional[str]
    stage_reached: str
    error: Optional[str]


# Stage names, in the order docs/sync-agent-plan.md:52-54 specifies them —
# also FlashResult.stage_reached's vocabulary, so a caller (or a human
# reading a "Needs you" log) can tell exactly how far a failed attempt got.
_COPY_SETTLE_S = 10.0

# Serial DFU as a way out of a REFUSED COPY (the disk appeared, the write was
# denied). OFF until somebody measures it: the board is still serving that
# mounted volume, and the only reading this project has of a DFU started
# mid-mount is the one in docs/STATUS.md that died at packet 23 and needed a
# manual write -- worse for the rider than the give-up it would replace, on
# the only board with a battery, 300 miles away. The no-disk caller is
# unaffected and stays on: there is no volume in that case, and its wedge
# recovery is measured (10.6 s, bench, 2026-09-15).
# TO TURN ON: reproduce a refused copy on a bench board, run the fallback
# against it 5+ times, and record the outcome in docs/STATUS.md first.
SERIAL_DFU_ON_REFUSED_COPY = False
_POST_FLASH_SETTLE_S = 2.0   # how long a freshly mounted bootloader volume may refuse writes

STAGE_SHA256 = "sha256"
STAGE_SEND_UF2 = "send_uf2"
STAGE_VOLUME_WAIT = "volume_wait"
STAGE_COPY = "copy"
# The second way onto the board, taken ONLY when volume_wait timed out and
# idProduct says the puck is sitting in its bootloader: Nordic serial DFU
# down the bootloader's own CDC port. Reached instead of STAGE_COPY, and
# followed by the same STAGE_PORT_WAIT / STAGE_INFO verdict.
STAGE_DFU_SERIAL = "dfu_serial"
STAGE_PORT_WAIT = "port_wait"
STAGE_INFO = "info"
STAGE_DONE = "done"


# WHY the last latest_manifest() call returned None, or None if it
# succeeded. Diagnostic only -- nothing decides anything on it -- read by
# daemon.py's _maybe_flash() straight after the call on the same thread.
# latest_manifest() stays "NEVER RAISES, None on any failure" (its contract,
# and every test double's); this is what lets the daemon's log say WHICH
# failure. MEASURED on the rider's Mac 2026-09-23 and 09-24: the post-sync
# firmware check ran twice and left no trace at all, while the live manifest
# and .uf2 were both reachable from here -- and from his log there was no way
# to tell this fetch from the .uf2 download as the one that failed.
_LAST_MANIFEST_ERROR: "str | None" = None


def _manifest_fail(reason: str) -> None:
    global _LAST_MANIFEST_ERROR
    _LAST_MANIFEST_ERROR = reason
    return None


def last_manifest_error() -> "str | None":
    return _LAST_MANIFEST_ERROR


def latest_manifest(site_url: str) -> "dict | None":
    """Fetch <site_url>/firmware/latest.json (docs/sync-agent-plan.md:51).

    NEVER RAISES. Offline, a non-200, a body that isn't JSON, a JSON value
    that isn't an object, or one whose `src`/`file` fail the same shape
    check web/sync/sync.js:2376-2379 applies — every one of those returns
    None, which needs_update() reads as "no update". A manifest that DOES
    have a well-shaped src/file but no usable `sha256` is not rejected
    here: it is passed through, so flash()'s G2 gate is the one place that
    "can't verify this is safe to flash" gets reported, rather than that
    failure collapsing into the same silent None a merely-absent manifest
    produces.
    """
    url = site_url.rstrip("/") + _MANIFEST_PATH
    try:
        from puckd import netctx
        with urllib.request.urlopen(url, timeout=_MANIFEST_TIMEOUT_S, context=netctx.ssl_context()) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                return _manifest_fail(f"HTTP {status} from {url}")
            body = resp.read()
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return _manifest_fail(f"{url}: {exc!r}")

    try:
        manifest = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        return _manifest_fail(f"not JSON: {exc!r}")
    if not isinstance(manifest, dict):
        return _manifest_fail("JSON is not an object")

    src = manifest.get("src")
    file_name = manifest.get("file")
    if not isinstance(src, str) or not _SRC_RE.match(src.strip()):
        return _manifest_fail(f"bad src {src!r}")
    if (not isinstance(file_name, str) or ".." in file_name
            or not _FILE_RE.match(file_name.strip())):
        return _manifest_fail(f"bad file {file_name!r}")
    global _LAST_MANIFEST_ERROR
    _LAST_MANIFEST_ERROR = None
    return manifest


def needs_update(puck_src: "str | None", manifest: "dict | None") -> bool:
    """docs/sync-agent-plan.md:51, verbatim: "if puck src != latest.src".

    A manifest that failed to fetch/parse (None) or a puck that hasn't
    reported its own src yet (empty/None) both mean "can't tell" — False,
    never a guess in the direction that would trigger an unwanted flash.
    """
    if manifest is None or not puck_src:
        return False
    if puck_src == manifest.get("src"):
        return False
    # `replaces`: the builds this one may be flashed over. Measured 2026-09-14
    # on the bench: "src != latest.src" alone DOWNGRADED a puck running a
    # newer dev build to the site's older one and left it in its bootloader.
    # A manifest that names what it replaces protects every puck on any
    # other build; a manifest without the key keeps the old rule.
    replaces = manifest.get("replaces")
    if isinstance(replaces, list):
        return puck_src in replaces
    return True


def _default_list_disks() -> str:
    """`diskutil list`'s raw stdout — how the volume_wait stage tells "the
    drive is enumerated but macOS hasn't auto-mounted it" (present-but-
    unmounted, docs/sync-agent-plan.md:52) from "not enumerated yet". A
    failure to even run diskutil reads as an empty listing, not an
    exception — the wait loop above just keeps polling either way."""
    try:
        proc = subprocess.run(["diskutil", "list"], capture_output=True,
                               encoding="utf-8", errors="replace", timeout=10.0)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""


def _default_mount_volume() -> None:
    """`diskutil mount /Volumes/XIAO-SENSE`. Best-effort and silent on
    failure: the caller's wait loop only ever trusts volume_exists()'s next
    read, never this call's own success/failure."""
    try:
        subprocess.run(["diskutil", "mount", str(UF2_VOLUME_PATH)],
                        capture_output=True, encoding="utf-8", errors="replace", timeout=10.0)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _default_copy_file(src: str, dst: str) -> None:
    """Raw bytes only. shutil.copy2 also copies metadata, and the
    bootloader's FAT volume answers that with EACCES ("Permission denied",
    measured on the Puck 2026-09-13) before the board ever reboots; a plain
    write is what `cp` does and what the bootloader wants. The volume
    vanishing mid-write (ENODEV / ENXIO) is the success signature and is
    left to the caller."""
    data = pathlib.Path(src).read_bytes()
    # The volume is listed before it is writable: the first open() for a
    # couple of seconds after it appears fails with EACCES (measured on the
    # Puck 2026-09-13; the same open() succeeds moments later). Retry those
    # for a bounded time; anything else is the caller's to judge.
    deadline = time.monotonic() + _COPY_SETTLE_S
    while True:
        try:
            with open(dst, "wb", buffering=0) as f:
                # buffering=0 gives a RAW file object, whose write() is one
                # write(2) and is allowed to return SHORT without raising --
                # there is no buffered layer left to finish the job. A short
                # write here puts a truncated .uf2 on the volume: the
                # bootloader ignores an incomplete image, the board comes
                # back on the OLD src, and stage 6 reports a mismatch that
                # looks like a bad build. Loop until the bytes are gone; a
                # write that fails because the board rebooted still raises
                # ENODEV, which is this stage's success signature.
                view = memoryview(data)
                while view:
                    written = f.write(view)
                    if not written:
                        # NOT an errno _is_device_not_configured() reads as
                        # success: a write that made no progress is a
                        # failure, and must never be mistaken for the
                        # reboot that ends a good copy.
                        raise OSError(errno.ENOSPC, f"short write to {dst}")
                    view = view[written:]
            return
        except OSError as exc:
            if exc.errno in (errno.EACCES, errno.EBUSY, errno.ENOENT, errno.EPERM) and time.monotonic() < deadline:
                time.sleep(0.5)
                continue
            raise


def _default_ioreg() -> str:
    """`ioreg -p IOUSB -l -w0`'s raw stdout. A failure to run it reads as an
    empty listing — usb_product_id() then answers None ("puck not found on
    USB"), which is the truth about what we could read, not a guess."""
    try:
        proc = subprocess.run(_IOREG_ARGV, capture_output=True,
                               encoding="utf-8", errors="replace",
                               timeout=_IOREG_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""


def _port_prefix_for_location(loc_hex: str) -> str:
    """The /dev/cu.usbmodem… prefix macOS gives a device at this USB
    locationID. macOS names a CDC node `/dev/cu.usbmodem<L><NN>`, where <L>
    is the locationID's hex with its trailing zeros stripped and <NN> the
    USB interface number. Measured on the Puck 2026-09-15: locationID
    0x00100000 (`…Sense@00100000`) -> /dev/cu.usbmodem101, i.e. L="1",
    NN="01". ONE board has been measured, so this is used only to CHOOSE
    between several XIAOs, never as the sole evidence a device is there."""
    stripped = loc_hex.lstrip("0").rstrip("0")
    return "/dev/cu.usbmodem" + stripped


def _xiao_usb_devices(ioreg_text: str) -> "list[tuple[str, int]]":
    """Every XIAO nRF52840 Sense in an `ioreg -p IOUSB -l -w0` dump, as
    (port_prefix, idProduct). Both the app and the bootloader match: they
    share the product string (that is the whole point — only idProduct
    differs)."""
    out: "list[tuple[str, int]]" = []
    starts = list(_IOREG_DEVICE_RE.finditer(ioreg_text))
    for i, match in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(ioreg_text)
        block = ioreg_text[match.start():end]
        if USB_PRODUCT_NAME not in match.group("name") and \
                USB_PRODUCT_NAME not in block:
            continue
        pid_match = _IOREG_PID_RE.search(block)
        if pid_match is None:
            continue
        out.append((_port_prefix_for_location(match.group("loc")),
                    int(pid_match.group(1))))
    return out


def usb_product_id(port_path: "str | None",
                   *, ioreg_fn: "Callable[[], str] | None" = None) -> "int | None":
    """The USB idProduct of the XIAO behind `port_path`, or None.

    PID_APP (0x8045) means the application is running; PID_BOOTLOADER
    (0x0045) means the Adafruit UF2 bootloader is. None means we could not
    establish it — nothing on USB, ioreg unreadable, or (CLAUDE.md §1:
    "Three boards can advertise at once ... this has flashed one wrong
    board") more than one XIAO whose idProducts disagree and none of whose
    port prefixes matches `port_path`. A guess between two boards here would
    put the WRONG board's state into a failure message, so it answers None
    and flash() logs the raw list alongside it — the reading that did not
    happen stays visible (CLAUDE.md rule 3).

    `ioreg_fn` is the injectable seam the tests drive with captured ioreg
    text; production passes nothing and gets the real `ioreg`.
    """
    devices = _xiao_usb_devices((ioreg_fn or _default_ioreg)())
    if not devices:
        return None
    matches = [(prefix, pid) for prefix, pid in devices
               if port_path and prefix and port_path.startswith(prefix)]
    if matches:
        # LONGEST prefix wins. The prefixes nest: a board at locationID
        # 0x00100000 owns "/dev/cu.usbmodem1", which is also a prefix of a
        # second board's "/dev/cu.usbmodem14201". Taking the longest match
        # is what makes /dev/cu.usbmodem14201 the SECOND board rather than
        # an ambiguity between the two.
        longest = max(len(prefix) for prefix, _pid in matches)
        candidates = [pid for prefix, pid in matches if len(prefix) == longest]
    else:
        candidates = [pid for _prefix, pid in devices]
    return candidates[0] if len(set(candidates)) == 1 else None


def _fmt_pid(pid: "int | None") -> str:
    return "none" if pid is None else f"0x{pid:04x}"


def _why_no_volume(pid: "int | None") -> str:
    """Which failure a volume_wait timeout actually was, from idProduct."""
    if pid == PID_BOOTLOADER:
        return _WHY_NO_DISK
    if pid == PID_APP:
        return _WHY_NO_UPDATE_MODE
    if pid is None:
        return _WHY_NOT_ON_USB
    return f"unexpected USB idProduct {_fmt_pid(pid)}"


def puckd_home() -> Path:
    """garmin.puckd_home(), without importing garmin (which pulls in
    garminconnect — a heavy third-party import this module has no other
    reason to take, and which is absent from a bare checkout)."""
    override = os.environ.get(_PUCKD_HOME_ENV)
    return Path(override).expanduser() if override else _DEFAULT_HOME


def _default_log(msg: str) -> None:
    """Append one timestamped line to PUCKD_HOME/daemon.log, the same file
    and the same format daemon.py:326-352 writes.

    This module needs its own default because daemon.py calls flash() with
    only `device_factory=` (daemon.py:590-592) and daemon.py is not edited
    by this change: without a default, the one line saying WHICH failure a
    volume_wait timeout was would exist only in FlashResult.error, and the
    fallback's own narrative — package fetched, sha256 ok, marker seen or
    not — would exist nowhere at all. NEVER RAISES, for daemon.py's own
    measured reason: a log line must not be able to end the thing it logs.
    """
    try:
        path = puckd_home() / _LOG_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", errors="replace") as f:
            stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
            f.write(f"{stamp} {msg}\n")
    except Exception:  # noqa: BLE001 -- a lost line is the lesser harm
        pass


def default_fetch(site_url: str, file_name: str,
                  dest_dir: "str | Path") -> "Optional[Path]":
    """Download <site_url>/firmware/<file_name> into dest_dir.

    The same contract (and the same silence on failure) as daemon.py's
    _default_fetch_uf2 at daemon.py:223-248, which fetches the .uf2 by the
    identical URL rule; this one exists because flash() must fetch the DFU
    package itself — daemon.py knows nothing about `dfu_file` and is not
    edited by this change. NEVER RAISES: any failure returns None, and the
    fallback is then skipped with one log line rather than turned into an
    exception on a maintenance path."""
    url = site_url.rstrip("/") + "/firmware/" + file_name
    try:
        from puckd import netctx
        with urllib.request.urlopen(url, timeout=60.0,
                                     context=netctx.ssl_context()) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status != 200:
                return None
            data = resp.read()
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    dest = Path(dest_dir)
    try:
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / file_name
        tmp = path.with_suffix(path.suffix + ".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    except OSError:
        return None
    return path


def dfu_package(manifest: dict, cache_dir: Path, site_url: str,
                fetch_fn: "Callable[[str, str, Path], Optional[Path]]",
                log: "Callable[[str], None]") -> "Optional[Path]":
    """The verified serial-DFU package named by manifest['dfu_file'], or
    None with exactly one log line saying why.

    G2's sha256 half applied to the second image: the .zip is hashed against
    manifest['dfu_sha256'] and REFUSED on any mismatch, for the same reason
    flash() refuses a .uf2 whose hash is wrong. A manifest with no
    `dfu_file` is not an error — it is a site that has not published a DFU
    package yet — so it skips quietly (one line), leaving the volume_wait
    verdict exactly as it was.

    A cached copy in cache_dir is preferred over a download, and a cached
    copy that FAILS the hash is deleted rather than kept: otherwise one
    corrupt download would refuse the fallback on every plug-in for ever.
    """
    name = manifest.get("dfu_file") if isinstance(manifest, dict) else None
    if not name:
        log("flash: manifest names no dfu_file — no serial-DFU fallback to try")
        return None
    if (not isinstance(name, str) or ".." in name
            or not _DFU_FILE_RE.match(name.strip())):
        log(f"flash: manifest dfu_file {name!r} is not a bare .zip name — skipping")
        return None
    name = name.strip()
    expected = manifest.get("dfu_sha256")
    if not expected:
        log("flash: manifest has no dfu_sha256 — refusing the serial-DFU "
            "fallback (G2)")
        return None

    path = Path(cache_dir) / name
    if not path.is_file():
        fetched = fetch_fn(site_url, name, Path(cache_dir))
        if fetched is None:
            log(f"flash: could not fetch {name} — no serial-DFU fallback")
            return None
        path = Path(fetched)
    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        log(f"flash: could not read {path}: {exc} — no serial-DFU fallback")
        return None
    if actual.lower() != str(expected).strip().lower():
        log(f"flash: {name} is {actual}, manifest says {expected} — refusing "
            "the serial-DFU fallback (G2), and discarding the bad copy")
        try:
            path.unlink()
        except OSError:
            pass
        return None
    log(f"flash: {name} sha256 ok ({actual[:12]}…)")
    return path


def _default_run_dfu(argv: "list[str]", timeout_s: float) -> "tuple[int, str]":
    """Run adafruit-nrfutil, returning (returncode, stdout+stderr). Never
    raises: a missing binary or a blown timeout comes back as a non-zero
    code and its text, which serial_dfu() judges exactly as it judges any
    other output — by the marker, never by the code."""
    try:
        proc = subprocess.run(argv, capture_output=True, encoding="utf-8",
                               errors="replace", timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return 124, f"adafruit-nrfutil did not finish within {timeout_s:.0f}s"
    except OSError as exc:
        return 127, f"could not run adafruit-nrfutil: {exc}"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _default_nrfutil_argv0() -> "Optional[list[str]]":
    """How to invoke adafruit-nrfutil, as the leading argv.

    Inside the shipped app there is no `adafruit-nrfutil` script and no PATH
    worth the name, but the `nordicsemi` package is vendored (pure Python:
    nordicsemi, click, ecdsa, six -- measured 2026-09-15, zero compiled
    extensions), so the bundle's own interpreter runs it as a module. On a
    bench with the script installed, the script is used as before."""
    try:
        import nordicsemi  # noqa: F401  -- vendored in the bundle, pip on the bench
        return [sys.executable, "-m", "nordicsemi"]
    except Exception:  # noqa: BLE001
        pass
    found = shutil.which(_NRFUTIL_NAME)
    if found:
        return [found]
    return [_NRFUTIL_FALLBACK] if os.path.exists(_NRFUTIL_FALLBACK) else None


def serial_dfu(port_path: str, package_path: "str | Path", *,
               run_dfu: "Callable[[list, float], tuple[int, str]] | None" = None,
               nrfutil_path: "Optional[str]" = None,
               timeout_s: float = DFU_TIMEOUT_S) -> "tuple[bool, str]":
    """Upload `package_path` to the bootloader on `port_path` over Nordic
    serial DFU. Returns (programmed, detail).

    THE VERDICT IS THE LITERAL `Device programmed.` LINE and nothing else —
    bench-playbook.md:150-152, re-measured 2026-09-15 when a DFU that died
    at packet 23 exited 0 and printed a traceback. `--singlebank` matches
    what PlatformIO's own uploader passes for this board
    (platform-nordicnrf52 builder/main.py:325-333); `--touch` is NOT passed:
    the board is already in DFU, PlatformIO does not pass it either, and the
    1200-baud touch was measured doing nothing to this bootloader
    (idProduct 0x0045 before and after).
    """
    exe = [nrfutil_path] if nrfutil_path else _default_nrfutil_argv0()
    if not exe:
        return False, (f"{_NRFUTIL_NAME} is not installed on this Mac — "
                        "no serial-DFU fallback")
    argv = [*exe, "dfu", "serial",
            "--package", str(package_path),
            "--port", port_path,
            "-b", _DFU_BAUD,
            "--singlebank"]
    code, output = (run_dfu or _default_run_dfu)(argv, timeout_s)
    if _DFU_OK_MARKER in output:
        return True, f"{_DFU_OK_MARKER} (rc={code})"
    return False, _dfu_complaint(code, output)


def _dfu_complaint(code: int, output: str) -> str:
    """One line for the log out of nrfutil's several. Prefers the line that
    says what actually went wrong over the return code, which lies."""
    for line in (output or "").splitlines():
        line = line.strip()
        if any(m in line for m in ("Failed to upgrade", "Error is:",
                                    "No data received", "Timed out",
                                    "could not run", "did not finish")):
            return f"no '{_DFU_OK_MARKER}' line (rc={code}): {line[:160]}"
    return f"no '{_DFU_OK_MARKER}' line (rc={code})"


def _is_device_not_configured(exc: BaseException) -> bool:
    """macOS's ENODEV, spelled the way shutil/cp report it once the
    bootloader's reboot unmounts the drive mid-write
    (docs/serial-parity-2026-09-09.md:336-340) — THE SUCCESS SIGNATURE for
    the copy stage, not a failure. Matched on errno first (robust to
    wording) and the exact string second (what a real OSError/IOError from
    a vanished mount actually says on this platform)."""
    return (getattr(exc, "errno", None) in (errno.ENODEV, errno.ENXIO, errno.EIO)
            or "Device not configured" in str(exc))


def flash(
    port_path: str,
    uf2_path: "str | Path",
    manifest: dict,
    *,
    device_factory: "Callable[[str], object] | None" = None,
    scan_ports: "Callable[[], list] | None" = None,
    volume_path: "Path | None" = None,
    volume_exists: "Callable[[], bool] | None" = None,
    list_disks: "Callable[[], str] | None" = None,
    mount_volume: "Callable[[], None] | None" = None,
    copy_file: "Callable[[str, str], None] | None" = None,
    sleep: "Callable[[float], None]" = time.sleep,
    now: "Callable[[], float]" = time.monotonic,
    volume_wait_s: float = VOLUME_WAIT_S,
    port_wait_s: float = PORT_WAIT_S,
    poll_interval_s: float = _POLL_INTERVAL_S,
    # --- the volume_wait diagnosis and the serial-DFU fallback ----------
    usb_product_id_fn: "Callable[[str], int | None] | None" = None,
    ioreg_fn: "Callable[[], str] | None" = None,
    site_url: "str | None" = None,
    fetch_fn: "Callable[[str, str, Path], Optional[Path]] | None" = None,
    dfu_cache_dir: "Path | None" = None,
    run_dfu: "Callable[[list, float], tuple[int, str]] | None" = None,
    nrfutil_path: "Optional[str]" = None,
    dfu_timeout_s: float = DFU_TIMEOUT_S,
    log: "Callable[[str], None] | None" = None,
) -> FlashResult:
    """docs/sync-agent-plan.md:52-54, exactly, in order:

        1. verify sha256 of the .uf2 against manifest['sha256'] BEFORE
           ANYTHING ELSE (gate G2) — no device touched, no filesystem
           touched, if this fails.
        2. send `uf2` over port_path via tools/jump's Device.command().
        3. wait up to volume_wait_s for /Volumes/XIAO-SENSE; if `diskutil
           list` shows the volume present but volume_exists() is still
           False, mount it (measured 2026-09-11). On a TIMEOUT, read the
           XIAO's USB idProduct and say which failure this was: 0x8045 "the
           puck did not enter update mode", 0x0045 "the puck is in update
           mode but this Mac made no disk", nothing "puck not found on USB".
        4. copy the .uf2 onto the volume. An OSError whose errno/message
           says "Device not configured" IS SUCCESS — the reboot unmounted
           the drive mid-write (docs/serial-parity-2026-09-09.md:336-340);
           any other copy failure is a real failure.
        4b. INSTEAD of 4, when 3 timed out and idProduct was 0x0045: the
           puck IS in its bootloader and its CDC port is live, so upload
           manifest['dfu_file'] over Nordic serial DFU (sha256-gated first,
           exactly as the .uf2 is). Only the literal `Device programmed.`
           line counts as done. No dfu_file in the manifest -> one log line
           and the stage-3 verdict stands.
        5. wait up to port_wait_s for a /dev/cu.usbmodem* port to return.
        6. open it, send `info`, and read src= back. ok=True only if that
           src equals manifest['src']. Reached the same way from 4 and 4b:
           a DFU that PRINTED its marker is still not a flash that came
           back, and only this stage may say ok=True.

    Every device/filesystem action is a keyword-injectable callable so
    tools/tests/test_puckd_flash.py can pin this sequence without a puck on
    the bench; production callers pass none of them and get the real thing
    (tools/jump's Device/scan_ports, real diskutil, real shutil.copy2, real
    time). `device_factory(port) -> obj` need only support `.command(cmd,
    timeout=...) -> list[str]` and `.close()` — the same two members of
    tools/jump's Device this module actually calls.

    Never raises: every failure mode returns FlashResult(ok=False, ...)
    with `stage_reached` naming where it stopped and `error` naming why —
    CLAUDE.md rule 3, a reading that did not happen is a finding, applies
    to a flash attempt exactly as it does to a sync.
    """
    uf2_path = Path(uf2_path)
    volume_path = volume_path or UF2_VOLUME_PATH

    # ---- Stage 1: sha256, BEFORE ANYTHING ELSE (gate G2) ----------------
    expected_sha256 = manifest.get("sha256") if isinstance(manifest, dict) else None
    if not expected_sha256:
        return FlashResult(False, None, STAGE_SHA256,
                            "manifest has no sha256 — refusing to flash (G2)")
    try:
        actual_sha256 = hashlib.sha256(uf2_path.read_bytes()).hexdigest()
    except OSError as exc:
        return FlashResult(False, None, STAGE_SHA256,
                            f"couldn't read {uf2_path}: {exc}")
    if actual_sha256.lower() != str(expected_sha256).strip().lower():
        return FlashResult(
            False, None, STAGE_SHA256,
            f"sha256 mismatch: {uf2_path.name} is {actual_sha256}, "
            f"manifest says {expected_sha256} — refusing to flash (G2)")

    jump = _load_jump_module()
    device_factory = device_factory or jump.Device
    scan_ports = scan_ports or jump.scan_ports
    list_disks = list_disks or _default_list_disks
    mount_volume = mount_volume or _default_mount_volume
    copy_file = copy_file or _default_copy_file
    log = log or _default_log
    fetch_fn = fetch_fn or default_fetch
    if usb_product_id_fn is None:
        _ioreg = ioreg_fn
        usb_product_id_fn = lambda p: usb_product_id(p, ioreg_fn=_ioreg)  # noqa: E731
    if site_url is None:
        site_url = os.environ.get(SITE_URL_ENV) or DEFAULT_SITE_URL
    if dfu_cache_dir is None:
        # Next to the .uf2 daemon.py already downloaded
        # (PUCKD_HOME/firmware/, daemon.py:584-586), so the package is
        # fetched once and reused on every later plug-in.
        dfu_cache_dir = uf2_path.parent
    if volume_exists is None:
        _vp = volume_path
        volume_exists = lambda: _vp.is_dir()  # noqa: E731

    # ---- Stage 2: send uf2 ------------------------------------------------
    try:
        dev = device_factory(port_path)
    except Exception as exc:
        return FlashResult(False, None, STAGE_SEND_UF2,
                            f"couldn't open {port_path} to send uf2: {exc}")
    try:
        try:
            dev.command("uf2", timeout=_UF2_SEND_TIMEOUT_S)
        except Exception:
            # docs/serial-parity-2026-09-09.md:328-330: the CDC port
            # dropping right here IS the reboot, not an error — whether the
            # "OK uf2" line beats the drop (CONTRACT.md:789-791) or not,
            # both outcomes mean the same thing: proceed to stage 3.
            pass
    finally:
        try:
            dev.close()
        except Exception:
            pass

    # ---- Stage 3: wait for the volume, mounting it if present-but-unmounted
    deadline = now() + volume_wait_s
    mount_attempted = False
    found = volume_exists()
    while not found:
        if not mount_attempted and UF2_VOLUME_NAME in list_disks():
            mount_volume()
            mount_attempted = True
            found = volume_exists()
            if found:
                break
        if now() >= deadline:
            break
        sleep(poll_interval_s)
        found = volume_exists()
    def _read_pid() -> "int | None":
        """idProduct, or None when we could not tell. A diagnosis must never
        become the failure (this is called on a path that is already
        failing), so every exception reads as "could not tell"."""
        try:
            return usb_product_id_fn(port_path)
        except Exception as exc:  # noqa: BLE001
            log(f"flash: could not read idProduct: {exc!r}")
            return None

    def _serial_dfu_recovery(pid: "int | None", why_here: str,
                             fail_stage: str, fail_error: str
                             ) -> "Optional[FlashResult]":
        """The second way onto a board that is already in its bootloader.

        None means RECOVERED -- the caller falls through to stages 5 and 6,
        which remain the only thing allowed to say ok=True. A FlashResult
        means give up and return exactly that.

        ONLY from the bootloader. From 0x8045 there is nothing listening for
        DFU; from "we could not tell" we would be uploading into a board we
        have not identified, which is how the wrong board got flashed on
        2026-08-12 (CLAUDE.md #1).

        Two callers, because there are two ways to be stuck in a bootloader
        with the app un-flashed: macOS never published the disk (the MODE
        SENSE stall, ~1 in 9), and macOS published a disk it then refused to
        let us write. MEASURED on the rider's Mac 2026-09-20 16:09: `copy to
        /Volumes/XIAO-SENSE/jumpheight-c5eea285.uf2 failed: [Errno 13]
        Permission denied` after the _COPY_SETTLE_S retry was exhausted,
        then "needs you: check the puck" -- and the puck stayed in its
        bootloader, recording nothing, for two days.

        WHETHER SERIAL DFU WORKS WITH THE VOLUME STILL MOUNTED IS
        UNMEASURED, and the one adjacent reading is a FAILURE:
        docs/STATUS.md files it under "Unmeasured" and records that "one DFU
        attempt started mid-mount died at packet 23 and exited 0 ... after
        that pair the board sat in its bootloader 12 min before a manual
        write recovered it (n=1)". The no-disk caller never has a volume
        mounted; the refused-copy caller always does. That is why the
        second caller is OFF by default -- see SERIAL_DFU_ON_REFUSED_COPY."""
        if pid != PID_BOOTLOADER:
            log(f"flash: no serial-DFU fallback -- USB idProduct "
                f"{_fmt_pid(pid)} is not the bootloader's "
                f"{_fmt_pid(PID_BOOTLOADER)}")
            return FlashResult(False, None, fail_stage, fail_error)
        package = dfu_package(manifest, Path(dfu_cache_dir), site_url,
                              fetch_fn, log)
        if package is None:
            return FlashResult(False, None, fail_stage, fail_error)

        # The bootloader's CDC node is not there the instant idProduct
        # flips. MEASURED on the bench Puck 2026-09-15: idProduct became
        # 0x0045 at 0.68 s and /dev/cu.usbmodem101 came back at 0.91 s, and
        # a fallback that fired at 0.68 s died on "could not open port
        # ... [Errno 2]" -- a real failure produced by our own impatience,
        # which would have read as "the serial DFU does not work here".
        node_deadline = now() + _DFU_PORT_WAIT_S
        while port_path not in scan_ports():
            if now() >= node_deadline:
                log(f"flash: {port_path} never came back as a bootloader port "
                    f"within {_DFU_PORT_WAIT_S:g}s -- no serial-DFU fallback")
                return FlashResult(False, None, fail_stage, fail_error)
            sleep(poll_interval_s)

        log(f"flash: {why_here}, but the puck is in its bootloader -- "
            f"serial DFU of {package.name} on {port_path}")
        programmed, detail = serial_dfu(
            port_path, package, run_dfu=run_dfu, nrfutil_path=nrfutil_path,
            timeout_s=dfu_timeout_s)
        log(f"flash: serial DFU: {detail}")
        if not programmed:
            return FlashResult(False, None, STAGE_DFU_SERIAL,
                               f"{fail_error}; serial DFU fallback: {detail}")
        return None

    if not found:
        # NAME THE FAILURE. Three faults have looked identical from here --
        # a puck that ignored `uf2`, a puck sitting in its bootloader whose
        # disk macOS never published, and a puck that is not on USB at all.
        # idProduct separates them in ~18 ms (docs/STATUS.md, 2026-09-15).
        pid = _read_pid()
        why = _why_no_volume(pid)
        if pid is None:
            # "could not tell" has two very different causes — nothing on
            # USB, and several XIAOs that disagree. The raw list separates
            # them for whoever reads the log, without the failure MESSAGE
            # (which Nick's daemon turns into one fixed line) growing a
            # branch it does not need.
            try:
                seen = [_fmt_pid(p) for _prefix, p
                        in _xiao_usb_devices((ioreg_fn or _default_ioreg)())]
            except Exception:  # noqa: BLE001
                seen = []
            log(f"flash: XIAOs seen on USB: {seen or 'none'}")
        # :g, not :.0f — a test (or a bench run) that shrinks the wait to
        # 0.5 s printed "never appeared within 0s", which reads as a bug in
        # the wait rather than a deliberately short one.
        timeout_error = (f"{volume_path} never appeared within "
                          f"{volume_wait_s:g}s — {why} "
                          f"(USB idProduct {_fmt_pid(pid)})")
        log(f"flash: volume_wait timed out on {port_path}: {why} "
            f"(USB idProduct {_fmt_pid(pid)})")

        # ---- Stage 4b: the serial-DFU fallback ------------------------
        gave_up = _serial_dfu_recovery(pid, "no disk", STAGE_VOLUME_WAIT,
                                       timeout_error)
        if gave_up is not None:
            return gave_up
        # Programmed is not "came back". Stages 5 and 6 below are still the
        # only thing allowed to say ok=True.
    else:
        # ---- Stage 4: copy; a "Device not configured" error IS success --
        dest = str(volume_path / uf2_path.name)
        copy_error = None
        try:
            copy_file(str(uf2_path), dest)
        except OSError as exc:
            if not _is_device_not_configured(exc):
                copy_error = f"copy to {dest} failed: {exc}"
            # THAT MESSAGE IS THE SUCCESS SIGNATURE
            # (docs/serial-parity-2026-09-09.md:338) — fall through.
        except Exception as exc:  # noqa: BLE001
            copy_error = f"copy to {dest} failed: {exc}"
        if copy_error is not None:
            # The disk appeared and then refused the write. The board is
            # still in its bootloader with nothing flashed into it, and
            # returning here (what 1.0.6 did) leaves the puck a USB drive
            # until a human power-cycles it. MEASURED on the rider's Mac
            # 2026-09-20: two days out of service. The recovery below is
            # written and tested but DISABLED -- see
            # SERIAL_DFU_ON_REFUSED_COPY for the reading that has to happen
            # before it is allowed near an unattended board.
            log(f"flash: {copy_error}")
            if not SERIAL_DFU_ON_REFUSED_COPY:
                return FlashResult(False, None, STAGE_COPY, copy_error)
            gave_up = _serial_dfu_recovery(_read_pid(), "the disk refused the write",
                                           STAGE_COPY, copy_error)
            if gave_up is not None:
                return gave_up

    # ---- Stage 5: wait for a /dev/cu.usbmodem* port to return -------------
    # THE PUCK WE JUST FLASHED, not "a puck". macOS usually re-enumerates the
    # same board at the same /dev/cu.usbmodemN, so port_path itself is
    # preferred whenever it is back; only if it is not do we fall back to
    # another usbmodem, and then to the LOWEST-sorted one rather than
    # whatever order scan_ports() happened to return. On Nick's Mac there is
    # one board and every rule picks the same port; on a bench with three
    # (CLAUDE.md ss1: "Three boards can advertise at once ... this has
    # flashed one wrong board") the old first-match could read `info` off a
    # neighbour and report src= from a board this flash never touched --
    # a wrong PASS, not a wrong failure.
    # Stage 5a: the bootloader's CDC port carries the SAME /dev/cu.usbmodemN
    # name as the app's (measured on the Puck 2026-09-13: the old wait
    # "found" the bootloader port at once and then lost it mid-`info`). So
    # first wait for the reboot itself -- the port disappearing -- bounded
    # by time AND by poll count so a board that re-enumerates faster than we
    # poll cannot hang this; if it never drops, carry on to the return wait.
    deadline = now() + port_wait_s
    polls = 0
    while now() < deadline and polls < 40:
        polls += 1
        if port_path not in (c for c in scan_ports() if fnmatch.fnmatch(c, "/dev/cu.usbmodem*")):
            break
        sleep(poll_interval_s)
    new_port = None
    while new_port is None:
        candidates = sorted(
            c for c in scan_ports() if fnmatch.fnmatch(c, "/dev/cu.usbmodem*"))
        if port_path in candidates:
            new_port = port_path
        elif candidates:
            new_port = candidates[0]
        if new_port is not None or now() >= deadline:
            break
        sleep(poll_interval_s)
    if new_port is None:
        return FlashResult(
            False, None, STAGE_PORT_WAIT,
            f"no /dev/cu.usbmodem* port returned within {port_wait_s:.0f}s")

    # ---- Stage 6: info; ok only if src_after == manifest['src'] -----------
    try:
        dev2 = device_factory(new_port)
    except Exception as exc:
        return FlashResult(False, None, STAGE_INFO,
                            f"couldn't reopen {new_port} for info: {exc}")
    try:
        try:
            try:
                info_lines = dev2.command("info", timeout=_INFO_TIMEOUT_S)
            except Exception:  # noqa: BLE001 -- the first read right after
                # re-enumeration can catch the CDC port before it is ready
                # (measured: "write failed: Device not configured"); one
                # settle-and-retry, then the normal verdict.
                sleep(_POST_FLASH_SETTLE_S)
                info_lines = dev2.command("info", timeout=_INFO_TIMEOUT_S)
        except Exception as exc:
            return FlashResult(
                False, None, STAGE_INFO,
                f"puck didn't answer info after flashing: {exc}")
    finally:
        try:
            dev2.close()
        except Exception:
            pass

    src_after = None
    for line in info_lines:
        kv = jump.parse_kv(line)
        if kv.get("_tag") == "INFO" and "src" in kv:
            src_after = kv["src"]
            break
    expected_src = manifest.get("src")
    if src_after is None:
        return FlashResult(False, None, STAGE_INFO,
                            "info after flashing had no src= field")
    if src_after != expected_src:
        return FlashResult(
            False, src_after, STAGE_INFO,
            f"puck reports src={src_after}, expected {expected_src}")
    return FlashResult(True, src_after, STAGE_DONE, None)
