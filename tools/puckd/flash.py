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

import errno
import fnmatch
import hashlib
import importlib.machinery
import json
import re
import shutil
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

VOLUME_WAIT_S = 30.0   # docs/sync-agent-plan.md:52
PORT_WAIT_S = 60.0     # docs/sync-agent-plan.md:54
_POLL_INTERVAL_S = 0.5

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
STAGE_SHA256 = "sha256"
STAGE_SEND_UF2 = "send_uf2"
STAGE_VOLUME_WAIT = "volume_wait"
STAGE_COPY = "copy"
STAGE_PORT_WAIT = "port_wait"
STAGE_INFO = "info"
STAGE_DONE = "done"


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
        with urllib.request.urlopen(url, timeout=_MANIFEST_TIMEOUT_S) as resp:
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

    src = manifest.get("src")
    file_name = manifest.get("file")
    if not isinstance(src, str) or not _SRC_RE.match(src.strip()):
        return None
    if (not isinstance(file_name, str) or ".." in file_name
            or not _FILE_RE.match(file_name.strip())):
        return None
    return manifest


def needs_update(puck_src: "str | None", manifest: "dict | None") -> bool:
    """docs/sync-agent-plan.md:51, verbatim: "if puck src != latest.src".

    A manifest that failed to fetch/parse (None) or a puck that hasn't
    reported its own src yet (empty/None) both mean "can't tell" — False,
    never a guess in the direction that would trigger an unwanted flash.
    """
    if manifest is None or not puck_src:
        return False
    return puck_src != manifest.get("src")


def _default_list_disks() -> str:
    """`diskutil list`'s raw stdout — how the volume_wait stage tells "the
    drive is enumerated but macOS hasn't auto-mounted it" (present-but-
    unmounted, docs/sync-agent-plan.md:52) from "not enumerated yet". A
    failure to even run diskutil reads as an empty listing, not an
    exception — the wait loop above just keeps polling either way."""
    try:
        proc = subprocess.run(["diskutil", "list"], capture_output=True,
                               text=True, timeout=10.0)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout or ""


def _default_mount_volume() -> None:
    """`diskutil mount /Volumes/XIAO-SENSE`. Best-effort and silent on
    failure: the caller's wait loop only ever trusts volume_exists()'s next
    read, never this call's own success/failure."""
    try:
        subprocess.run(["diskutil", "mount", str(UF2_VOLUME_PATH)],
                        capture_output=True, text=True, timeout=10.0)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _default_copy_file(src: str, dst: str) -> None:
    shutil.copy2(src, dst)


def _is_device_not_configured(exc: BaseException) -> bool:
    """macOS's ENODEV, spelled the way shutil/cp report it once the
    bootloader's reboot unmounts the drive mid-write
    (docs/serial-parity-2026-09-09.md:336-340) — THE SUCCESS SIGNATURE for
    the copy stage, not a failure. Matched on errno first (robust to
    wording) and the exact string second (what a real OSError/IOError from
    a vanished mount actually says on this platform)."""
    return (getattr(exc, "errno", None) == errno.ENODEV
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
) -> FlashResult:
    """docs/sync-agent-plan.md:52-54, exactly, in order:

        1. verify sha256 of the .uf2 against manifest['sha256'] BEFORE
           ANYTHING ELSE (gate G2) — no device touched, no filesystem
           touched, if this fails.
        2. send `uf2` over port_path via tools/jump's Device.command().
        3. wait up to volume_wait_s for /Volumes/XIAO-SENSE; if `diskutil
           list` shows the volume present but volume_exists() is still
           False, mount it (measured 2026-09-11).
        4. copy the .uf2 onto the volume. An OSError whose errno/message
           says "Device not configured" IS SUCCESS — the reboot unmounted
           the drive mid-write (docs/serial-parity-2026-09-09.md:336-340);
           any other copy failure is a real failure.
        5. wait up to port_wait_s for a /dev/cu.usbmodem* port to return.
        6. open it, send `info`, and read src= back. ok=True only if that
           src equals manifest['src'].

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
    if not found:
        return FlashResult(
            False, None, STAGE_VOLUME_WAIT,
            f"{volume_path} never appeared within {volume_wait_s:.0f}s")

    # ---- Stage 4: copy; a "Device not configured" error IS the success ---
    dest = str(volume_path / uf2_path.name)
    try:
        copy_file(str(uf2_path), dest)
    except OSError as exc:
        if not _is_device_not_configured(exc):
            return FlashResult(False, None, STAGE_COPY,
                                f"copy to {dest} failed: {exc}")
        # THAT MESSAGE IS THE SUCCESS SIGNATURE
        # (docs/serial-parity-2026-09-09.md:338) — fall through.
    except Exception as exc:
        return FlashResult(False, None, STAGE_COPY,
                            f"copy to {dest} failed: {exc}")

    # ---- Stage 5: wait for a /dev/cu.usbmodem* port to return -------------
    deadline = now() + port_wait_s
    new_port = None
    while new_port is None:
        for candidate in scan_ports():
            if fnmatch.fnmatch(candidate, "/dev/cu.usbmodem*"):
                new_port = candidate
                break
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
