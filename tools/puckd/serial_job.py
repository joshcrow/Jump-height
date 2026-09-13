"""tools/puckd/serial_job.py -- the puck job's serial half (spec steps 1-5, 7).

docs/sync-agent-plan.md:41-58 ("the job") and :60-66 (the gates) are the
contract. This module owns exactly:

    run_job(port_path, spool_dir)   steps 1-5: open the port, read the ride,
                                     verify it exactly as web/sync/sync.js's
                                     verifyPull() does (CONTRACT.md SS2.4/
                                     SS2.5/SS2.5a/SS2.5b), and write the
                                     CONTRACT.md SS2 bundle to spool_dir.
                                     Never clears the puck and never uploads
                                     -- G1 ("never clear unless verified AND
                                     remote size == local size on Drive")
                                     needs upload.py's answer first, which is
                                     the daemon's job to sequence, not this
                                     module's.
    clear_puck(port_path)           step 7: `clear`, then CONFIRM with a
                                     fresh `stats` (stored_jumps=0 AND
                                     trace_bytes=0 -- G4: a reading that did
                                     not happen is a failure, never a zero
                                     assumed), then `tracecheck` where the
                                     puck answers it, because the slow
                                     re-walk is the number main.cpp itself
                                     calls correct when the two disagree.
    read_stats(port_path)           the battery poll (spec item 10): one
                                     `stats`, parsed, never raising -- a
                                     daemon polling every 60 s must survive a
                                     bad read, not crash on one.

Reuses tools/jump's Device / command() / parse_kv() / parse_file_sections() /
_last_tagged() / _query_tracecheck() -- proven on hardware -- instead of
reimplementing the serial protocol. tools/jump has no .py extension, so it
is loaded with importlib the same way tools/tests/test_ingest.py's and
tools/puckd/flash.py's own _load_jump_module() helpers do.

The one deliberate exception to "reuse, don't reimplement": web/sync/
sync.js's doPull() command order is jumps -> traceraw (-> trace on the
ERR unknown_command fallback) -> stats -> selftest (CONTRACT.md SS3.2), which
differs from tools/jump's OWN bench cmd_sync (traceraw first, jumps second).
The spec requires the PAGE's order here, so the small amount of chatter-
parsing _sync_via_traceraw() does inline is re-done here rather than reused,
because reusing that function would reuse its ordering too.

Every device-facing call takes an injectable `device_factory` keyword
argument (a `Callable[[str], object]`), defaulting to the real
`jump.Device` -- same pattern as tools/puckd/flash.py's own filesystem/
serial seams -- so tools/tests/test_puckd_serial_job.py can pin the couple
of shapes (a `clear` that lies about being empty) that
tools/fake_device.py's real wire protocol has no knob for, without ever
touching that file.
"""

from __future__ import annotations

import base64
import binascii
import importlib.machinery
import json
import platform
import re
import time
import types
import zipfile
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parent.parent.parent
JUMP = str(REPO / "tools" / "jump")

# ------------------------------------------------------------- jump loading

_jump_module = None  # cached: tools/jump's top level has no import-time I/O
                      # (just class/def statements) -- see tools/puckd/
                      # flash.py's identical note; safe to load once.


def _load_jump_module():
    """Load tools/jump (no .py extension) for its proven Device / parse_kv /
    parse_file_sections / _last_tagged / _query_tracecheck / F22_MAX_
    OVERREPORT_BYTES -- same technique as tools/tests/test_ingest.py's and
    tools/puckd/flash.py's own helper of the same name."""
    global _jump_module
    if _jump_module is None:
        loader = importlib.machinery.SourceFileLoader("jumpcli_puckd_serial_job", JUMP)
        mod = types.ModuleType("jumpcli_puckd_serial_job")
        mod.__file__ = JUMP
        loader.exec_module(mod)
        _jump_module = mod
    return _jump_module


def _jump():
    return _load_jump_module()


# ----------------------------------------------------------------- version

# Manifest's page_version / user_agent equivalents (CONTRACT.md SS2.2) --
# this module plays the "page"'s role in the pipeline for a bundle it writes,
# so it fills those fields the same way, identifying itself instead.
AGENT_VERSION = "0.1.0"

# CONTRACT.md SS2.5b: a real nrf52 puck's empty trace.csv is the 6-byte
# "t,mag\n" header alone (firmware/src/platform/nrf52/jh_store.cpp:
# 1119-1126), reported as trace_bytes=0 on STATS because that counter only
# starts on the first append (:1058-1063). No named Python constant for this
# exists in tools/jump (it is an inline "<= 6" in _verify_ingest_bundle) --
# this is sync.js's own TRACE_HEADER_BYTES, restated.
TRACE_HEADER_BYTES = 6


# ------------------------------------------------------------------- errors

class PullFailed(RuntimeError):
    """The pull stopped before there was anything to verify or write --
    mirrors web/sync/sync.js's endPullFailed(): "a pull that stopped part-way
    has no bundle to build". The puck is untouched either way (G3); the
    caller's remedy is to retry the whole job on the next plug-in, not to
    salvage a partial bundle from this exception."""

    def __init__(self, message: str, reasons: "list[str] | None" = None):
        super().__init__(message)
        self.reasons = reasons if reasons is not None else [message]


# ------------------------------------------------------------------ results

@dataclass(frozen=True)
class JobResult:
    bundle_path: Path
    verified: bool
    reasons: "list[str]"
    jumps: int
    stats_before: "Optional[str]"
    stats_after: "Optional[str]"
    puck_name: "Optional[str]"
    src: "Optional[str]"


@dataclass(frozen=True)
class ClearResult:
    ok: bool
    stored_jumps_after: "Optional[int]"
    trace_bytes_after: "Optional[int]"
    tracecheck: "object"  # tools/jump's TraceCheck namedtuple(fast, slow, reason), or None


@dataclass(frozen=True)
class VerifyResult:
    reasons: "list[str]"
    f22_band_applied: bool = False
    f22_note: "Optional[str]" = None
    growth_note: "Optional[str]" = None

    @property
    def verified(self) -> bool:
        return not self.reasons


@dataclass
class PullContext:
    """Everything verify_pull() needs, gathered off the wire by run_job() --
    or built by hand in a test, exactly as tools/tests/test_ingest.py builds
    a manifest dict to drive _verify_ingest_bundle() directly for the shapes
    tools/fake_device.py's real wire cannot produce (header-only, growth:
    its `trace` command never emits the empty-region header, and it has no
    background recording to grow trace_bytes between two `stats` reads)."""

    storage_down: bool = False
    incomplete_text: "Optional[str]" = None       # (a)
    stored_jumps_device: "Optional[int]" = None   # (b)
    jump_rows: int = 0                            # (b)
    trace_format: "Optional[str]" = None          # (c): 'jhtrace-v2-b64' | 'csv' | None
    # raw path
    b64_error: "Optional[str]" = None
    expected_raw: "Optional[int]" = None
    got_raw_len: "Optional[int]" = None
    crc_expected: "Optional[str]" = None
    crc_actual: "Optional[str]" = None
    # csv path
    trace_bytes_device: "Optional[int]" = None
    trace_bytes_after: "Optional[int]" = None
    got_csv_bytes: "Optional[int]" = None


# --------------------------------------------------------------- verify_pull

def verify_pull(ctx: PullContext) -> VerifyResult:
    """Port of web/sync/sync.js's verifyPull() (CONTRACT.md SS2.4, SS2.5,
    SS2.5a, SS2.5b) -- same checks, same order, same F-22 band
    (F22_MAX_OVERREPORT_BYTES, reused from tools/jump so the value can never
    drift a third way -- CONTRACT.md SS2.5a: "duplicated deliberately... and
    must move together") and the same growth window and header-only
    forgiveness. An empty reasons list means verified, exactly as
    web/sync/sync.js:1139-1141 defines it: every applicable check ran and
    passed, and every check that could not run is itself a reason."""
    F22 = _jump().F22_MAX_OVERREPORT_BYTES
    out: "list[str]" = []
    f22_applied = False
    f22_note = None
    growth_note = None

    # (0) fs=down: every count below came back unknown, not zero.
    if ctx.storage_down:
        out.append('The puck is not saving anything (the "NO REC" problem), '
                    "so what came across cannot be trusted. Do NOT empty the "
                    "puck.")

    # (a) the puck's own complaint outranks any arithmetic we do.
    if ctx.incomplete_text:
        out.append("The puck itself said part of the transfer never "
                    "arrived: " + ctx.incomplete_text[:160])

    # (b) jump rows vs the puck's own stored_jumps, only when stored_jumps>0
    # (0 legitimately means an empty puck).
    if (ctx.stored_jumps_device is not None and ctx.stored_jumps_device > 0
            and ctx.jump_rows != ctx.stored_jumps_device):
        out.append(f"Only {ctx.jump_rows} of the puck's "
                   f"{ctx.stored_jumps_device} jumps came across.")

    # (c) the ride data itself.
    if ctx.trace_format == "jhtrace-v2-b64":
        if ctx.b64_error:
            out.append(ctx.b64_error + ".")
        if ctx.expected_raw is None:
            out.append("The puck never said how much ride data to expect, "
                        "so it could not be checked.")
        elif ctx.got_raw_len != ctx.expected_raw:
            out.append(f"Only {(ctx.got_raw_len or 0):,} of the puck's "
                       f"{ctx.expected_raw:,} bytes of ride data came across.")
        if not ctx.crc_expected:
            out.append("The puck never sent its check number for the ride "
                        "data, so it could not be checked.")
        elif ctx.crc_actual != ctx.crc_expected:
            out.append("The ride data arrived damaged -- its check number "
                        "does not match.")
    elif ctx.trace_format == "csv":
        dev_bytes = ctx.trace_bytes_device
        dev_after = ctx.trace_bytes_after
        got = ctx.got_csv_bytes if ctx.got_csv_bytes is not None else 0
        if dev_bytes is None:
            out.append("The puck never said how much ride data to expect, "
                        "so it could not be checked.")
        elif got == dev_bytes:
            pass  # the ordinary case: the counter and the copy agree exactly
        elif dev_bytes == 0 and got <= TRACE_HEADER_BYTES:
            pass  # header-only: an empty region still emits "t,mag\n"
        elif got < dev_bytes and dev_bytes - got <= F22:
            # Audit F-22: the trace region is FULL and the live counter reads
            # high by up to one batch (docs/audit-2026-08-22.md:62-79).
            f22_applied = True
            f22_note = (f"The puck is full -- its counter runs "
                       f"{dev_bytes - got:,} bytes ahead once it fills (a "
                        "known quirk); the copy is complete.")
        elif got > dev_bytes and dev_after is not None and got <= dev_after:
            # The puck kept recording between the two `stats` reads
            # (CONTRACT.md SS2.5b) -- the second stats is the honest ceiling.
            growth_note = ("The puck kept recording while it was plugged in "
                          f"-- {got - dev_bytes:,} extra bytes; that is "
                           "normal.")
        elif got > dev_bytes:
            out.append(f"More ride data arrived than the puck says it has "
                       f"-- {got:,} bytes against the puck's {dev_bytes:,}. "
                        "That does not add up.")
        else:
            out.append(f"Only {got:,} of the puck's {dev_bytes:,} bytes of "
                        "ride data came across.")
    else:
        out.append("No ride data arrived at all.")

    return VerifyResult(reasons=out, f22_band_applied=f22_applied,
                        f22_note=f22_note, growth_note=growth_note)


# ------------------------------------------------------------------ helpers

def _int_or_none(v) -> "Optional[int]":
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def _float_or_none(v) -> "Optional[float]":
    """Unlike _int_or_none, never truncates -- uptime_s arrives as
    '%.3f'-formatted (firmware/src/main.cpp's up_key), and trace_epoch_utc
    (CONTRACT.md SS2.3) is computed straight from it. Rounding it to an int
    before subtracting would mis-date every sample in the trace by up to a
    second, silently, under a green checkmark -- the exact CLAUDE.md rule 3
    shape this module exists to avoid elsewhere."""
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _log_lines(raw_lines: "list[str]") -> "list[str]":
    """Port of web/sync/sync.js's onLine() device.log selection (CONTRACT.md
    SS2): every line except a FILE frame's own body -- the BEGIN/END markers
    and any in-frame '#' chatter (the firmware's INCOMPLETE warning included)
    still count, selected "by kind, not by position" (CONTRACT.md SS1.2).
    Device.command() already strips a successful 'OK <cmd>' terminator
    (tools/jump:634-652), so unlike the browser's device.log this list never
    carries one -- the INCOMPLETE scan below is unaffected, since an OK line
    never contains that word."""
    out: "list[str]" = []
    section = None
    for line in raw_lines:
        is_begin = line.startswith("FILE ") and line.endswith(" BEGIN")
        is_end = line.startswith("FILE ") and line.endswith(" END")
        if is_begin:
            section = line.split()[1]
            out.append(line)
        elif is_end:
            section = None
            out.append(line)
        elif section is not None:
            if line.startswith("#"):
                out.append(line)
            # else: a FILE body line -- dropped from device.log.
        else:
            out.append(line)
    return out


def _first_incomplete(lines: "list[str]") -> "Optional[str]":
    for l in lines:
        if "INCOMPLETE" in l and l.lstrip().startswith("#"):
            return l.strip()
    return None


def _extract_puck_name(lines: "list[str]") -> "Optional[str]":
    """'# name=...' chatter (firmware/src/main.cpp:1207-1208), the only
    source of puck_name over a cable (no BLE advertised name in this
    transport) -- CONTRACT.md SS4.1/A2, docs/serial-parity-2026-09-09.md row
    23. tools/fake_device.py's `info` never emits this (CONTRACT.md Appendix
    B2) -- puck_name is None against the fake, a documented gap, not a bug
    here. Last match wins, mirroring web/sync/sync.js's classify()."""
    name = None
    prefix = "# name="
    for l in lines:
        if l.startswith(prefix):
            candidate = l[len(prefix):].strip()
            if candidate:
                name = candidate
    return name


def _storage_down(lines: "list[str]", kv: dict) -> bool:
    if kv.get("fs") == "down":
        return True
    return any(l.lstrip().startswith("# storage NOT MOUNTED") for l in lines)


def _csv_body(section_lines: "list[str]") -> "list[str]":
    """The CSV rows of a FILE frame, with the puck's own in-frame '#'
    chatter dropped -- web/sync/sync.js's onLine() split, restated.

    tools/jump's parse_file_sections() (tools/jump:1585-1596) puts EVERY
    line between BEGIN and END into the body, chatter included, because its
    own callers re-scan the raw lines for the warning separately. The page
    does not: onLine() routes an in-frame '#' line to device.log and NEVER
    to S.jumpsLines/S.traceCsvLines (web/sync/sync.js:629-646), and its
    comment there records the measurement that forced it --

        "a jumps.csv one row short verified TRUE, the warning became the
         missing third 'jump', and step 4 offered to erase the puck."

    printFileFramed() prints "# WARNING <name> INCOMPLETE - N bytes never
    reached the host" AFTER the body and BEFORE "FILE <name> END"
    (firmware/src/main.cpp:463-466), so on a short download that line lands
    inside the frame. Left in, it (a) counts as a jump row in
    _jump_row_count(), which is exactly how check (b)'s cross-check gets
    silenced on the one file that has no crc and no byte count of its own,
    (b) inflates got_csv_bytes so a SHORT trace reports as a surplus, and
    (c) ships a '#' row inside the bundle's jumps.csv/trace.csv, which is
    not what any other producer of a CONTRACT.md SS2.1 bundle writes.
    Measured on this module before this function existed: 2 real rows + the
    warning read as jump_rows=3 against stored_jumps=3, and trace_bytes_got
    ran 88 B over a 28 B device count.

    Discriminating on '#' is unambiguous for the same reason the page says
    it is (web/sync/sync.js:642-644): jumps.csv/trace.csv rows begin with a
    digit or their header word. The raw path already did exactly this
    filter inline (run_job()'s trace.bin body); this is the same rule, for
    the two frames that were missing it.

    The chatter is NOT lost: _log_lines() keeps every in-frame '#' line in
    device.log, which is what check (a) reads and what the bundle carries.
    """
    return [l for l in section_lines if not l.startswith("#")]


def _jump_row_count(jumps_lines: "list[str]") -> int:
    """endPullOk()'s header-aware count (web/sync/sync.js:1707-1708):
    jumps.csv's header row is not a jump."""
    rows = [l for l in jumps_lines if l.strip() != ""]
    if rows and rows[0].startswith("n,"):
        return len(rows) - 1
    return len(rows)


def _decode_raw_body(body_lines: "list[str]") -> "tuple[bytes, Optional[str]]":
    """Port of web/sync/sync.js's feedB64()/rawPush()/finishRawSink(): decode
    a traceraw FILE trace.bin body a line at a time, preserving its
    truncation semantics -- CONTRACT.md SS1.2: '=' padding appears only at
    the very end, so once it shows up the remainder is the tail. Bytes
    decoded before a corrupt or truncated chunk are kept (crc32 is computed
    over exactly what this returns), because an unverified bundle built from
    a partial trace is exactly the one CONTRACT.md SS2.4 says is worth
    keeping and showing."""
    out = bytearray()
    pending = ""
    error: "Optional[str]" = None
    for line in body_lines:
        if error:
            break
        pending += line
        n = len(pending) if "=" in pending else len(pending) - (len(pending) % 4)
        if n <= 0:
            continue
        chunk, pending = pending[:n], pending[n:]
        try:
            out.extend(base64.b64decode(chunk))
        except (ValueError, binascii.Error):
            error = "part of the ride data was unreadable"
            break
    if pending and error is None:
        error = "the ride data stopped part-way through a chunk"
    return bytes(out), error


def _puck4(puck_name: "Optional[str]") -> str:
    """CONTRACT.md SS4.1's PUCK4, the web/sync/sync.js:1944-1946 reading
    (this module writes bundles, the same role the page plays -- 'xxxx' on a
    miss keeps the filename's shape and is visible, not silent). tools/jump's
    OWN _puck4() (rsplit-based, 'UNKN' fallback) is a *different* function
    for a different role -- naming the INGESTED session directory, not the
    bundle a producer writes -- and CONTRACT.md Appendix A2 records that the
    two deliberately disagree; ingest never reads this module's filename."""
    if puck_name:
        m = re.search(r"JumpHeight-([0-9A-Za-z]{4})", puck_name)
        if m:
            return m.group(1)
    return "xxxx"


def _local_iso(d: datetime) -> str:
    """web/sync/sync.js's localIso(): 'YYYY-MM-DDTHH:MM:SS', no zone suffix
    -- the zone travels separately as tz_offset_min (CONTRACT.md SS2.2) --
    and, not incidentally, exactly what tools/jump's
    _ingest_session_dir_name() feeds to datetime.fromisoformat()."""
    return (f"{d.year:04d}-{d.month:02d}-{d.day:02d}"
            f"T{d.hour:02d}:{d.minute:02d}:{d.second:02d}")


def _utc_iso(d: datetime) -> str:
    u = d.astimezone(timezone.utc)
    return u.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _join_body(lines: "list[str]") -> str:
    """web/sync/sync.js's joinBody(): identical for every existing client
    (CONTRACT.md SS1.2's row-18 parity note) -- a trailing '\\n' iff there is
    a body at all."""
    return ("\n".join(lines) + "\n") if lines else ""


# ------------------------------------------------------------------ manifest

def _build_manifest(*, puck_name, info_kv, cal_line, synced_at, stats_before_line,
                    stats_after_line, stats_before_kv, stats_after_kv,
                    info_lines, selftest_lines, trace_format, log_hz,
                    trace_raw_len, trace_crc_hex, verify: VerifyResult,
                    jump_rows, transport_bytes, transport_seconds) -> dict:
    """CONTRACT.md SS2.2's keys, in the same shape web/sync/sync.js's
    buildManifest() writes them -- this module plays the "page"'s role in
    the pipeline for a bundle it writes."""
    uptime = _float_or_none(stats_before_kv.get("uptime_s"))
    is_raw = trace_format == "jhtrace-v2-b64"
    trace_epoch_utc = None
    if uptime is not None:
        trace_epoch_utc = _utc_iso(datetime.fromtimestamp(
            synced_at.timestamp() - uptime, tz=synced_at.tzinfo))

    return {
        "bundle_version": 1,
        "page_version": f"puckd-{AGENT_VERSION}",
        "puck_name": puck_name,
        "fw": info_kv.get("fw"),
        "src": info_kv.get("src"),
        "synced_at_utc": _utc_iso(synced_at),
        "synced_at_local": _local_iso(synced_at),
        "tz_offset_min": int(synced_at.utcoffset().total_seconds() // 60)
                         if synced_at.utcoffset() is not None else None,
        "uptime_s": uptime,
        "trace_epoch_utc": trace_epoch_utc,
        "info_lines": info_lines,
        "cal": cal_line,
        "stats_before": stats_before_line,
        "stats_after": stats_after_line,
        "selftest_lines": selftest_lines,
        "trace_format": trace_format,
        "log_hz": log_hz,
        "trace_bytes_device": _int_or_none(stats_before_kv.get("trace_bytes")),
        # csv-path-only (CONTRACT.md SS2.2); null on the raw path where
        # trace_raw_bytes is a different unit (decoded binary, not csv
        # text). The real value is filled in by the caller, which alone
        # holds the PullContext this was computed from -- see run_job()'s
        # `manifest["trace_bytes_got"] = ctx.got_csv_bytes` right after this
        # call returns.
        "trace_bytes_got": None,
        "f22_band_applied": verify.f22_band_applied,
        "trace_bytes_after": _int_or_none(stats_after_kv.get("trace_bytes")),
        "trace_raw_bytes": trace_raw_len if is_raw else None,
        "trace_crc32": trace_crc_hex if is_raw else None,
        "stored_jumps_device": _int_or_none(stats_before_kv.get("stored_jumps")),
        "jump_rows": jump_rows,
        "verified": verify.verified,
        "cleared": False,
        "transfer": {
            "transport": "usb",
            "seconds": transport_seconds,
            "bytes_received": transport_bytes,
            "mtu": None,
        },
        "user_agent": f"puckd/{AGENT_VERSION} (macOS; python {platform.python_version()})",
    }


def _write_bundle(spool_dir: "Path | str", manifest: dict, jumps_lines: "list[str]",
                  trace_format: "Optional[str]", trace_bin_bytes: "Optional[bytes]",
                  trace_csv_lines: "Optional[list[str]]", device_log: "list[str]",
                  synced_at: datetime) -> Path:
    """CONTRACT.md SS2.1's files, in a real zip (Python's zipfile, unlike the
    hand-rolled writer web/sync/sync.js needs because a browser has no build
    step -- this is Python, so there is no reason to reimplement one)."""
    spool = Path(spool_dir)
    spool.mkdir(parents=True, exist_ok=True)
    # web/sync/sync.js's bundleName() (:1949-1951), unchanged for the
    # ordinary case -- CONTRACT.md SS4.1's shape, minute resolution.
    stem = f"jumpheight-{_puck4(manifest.get('puck_name'))}-{synced_at:%Y%m%d}-{synced_at:%H%M}"
    path = spool / f"{stem}.zip"
    # ...but NEVER over a bundle already in the spool. The page writes to the
    # rider's Downloads folder, where the browser itself de-duplicates and a
    # person is watching; this writes to an unattended spool, and the daemon's
    # own recovery instruction to Nick is "Unplug the puck and plug it back
    # in" (daemon.py's NEEDS_YOU_UPLOAD_ACTION), which puts a second full pull
    # of the same puck inside the same MINUTE as the first. Measured
    # 2026-09-13: three run_job_cycle() calls in one minute left exactly one
    # zip in the spool -- the third had silently replaced the first two.
    # Harmless while both pulls verify, but G3 says "the spool keeps the
    # bundle", and the case that costs something is real: pull 1 verified and
    # failed only its upload, pull 2 came up short. Clobbering loses the good
    # one and keeps the bad one, with nothing on any surface saying so
    # (CLAUDE.md rule 3). A suffix is visible, ordered, and costs a stat().
    if path.exists():
        n = 2
        while (spool / f"{stem}-{n}.zip").exists():
            n += 1
        path = spool / f"{stem}-{n}.zip"

    notes = f"# JumpHeight rider notes -- {_local_iso(synced_at)}\n"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        zf.writestr("jumps.csv", _join_body(jumps_lines))
        if trace_format == "jhtrace-v2-b64":
            zf.writestr("trace.bin", trace_bin_bytes or b"")
        elif trace_format == "csv":
            zf.writestr("trace.csv", _join_body(trace_csv_lines or []))
        zf.writestr("notes.txt", notes)
        zf.writestr("device.log", _join_body(device_log))
    return path


# --------------------------------------------------------------------- job

_INFO_TIMEOUT_S = 10.0
_STATS_TIMEOUT_S = 10.0
_JUMPS_TIMEOUT_S = 60.0
_TRACERAW_TIMEOUT_S = 180.0
_TRACE_TIMEOUT_S = 180.0
_SELFTEST_TIMEOUT_S = 15.0
# tools/jump:2194-2198: "clear erases every used sector -- ~40 ms each, so a
# well-used region is ~20 s of work. The old 10 s timeout reported failure
# on a clear that was merely SLOW." Reused verbatim, not re-derived.
_CLEAR_TIMEOUT_S = 60.0


def run_job(port_path: str, spool_dir: "Path | str", *,
           device_factory: "Callable[[str], object] | None" = None) -> JobResult:
    """docs/sync-agent-plan.md's "the job", steps 1-5:

      1. open the port; info, stats
      2. jumps; traceraw -> falls back to trace       (web/sync/sync.js's
         3. stats again; selftest                      exact order, CONTRACT.md SS3.2)
      4. verify exactly as the page does                (verify_pull(), above)
      5. write the bundle to spool_dir                  (CONTRACT.md SS2)

    Raises PullFailed (nothing written, puck untouched -- G3) when the pull
    itself did not complete structurally: info/stats not answering, `jumps`
    or a non-fallback `traceraw`/`trace` ERR. A content-level problem within
    a structurally-complete pull (F-22 outside its band, a jump-row
    mismatch, ...) is NOT one of these -- it still returns a JobResult with
    verified=False and a written bundle, exactly as web/sync/sync.js's
    endPullOk() does: "an unverified bundle is exactly the one Josh most
    wants to look at".

    Never calls `clear` -- that is clear_puck()'s job, called by the daemon
    only once upload.py has confirmed the bundle landed (G1)."""
    jump = _jump()
    device_factory = device_factory or jump.Device
    dev = device_factory(port_path)
    device_log: "list[str]" = []
    try:
        dev.drain_boot()

        try:
            info_lines = dev.command("info", timeout=_INFO_TIMEOUT_S)
        except TimeoutError as e:
            raise PullFailed(f"the puck didn't answer 'info': {e}") from e
        device_log.extend(_log_lines(info_lines))
        if info_lines and info_lines[-1].startswith("ERR"):
            raise PullFailed(f"the puck refused 'info': {info_lines[-1]}",
                             reasons=[info_lines[-1]])
        info_kv = jump._last_tagged(info_lines, "INFO ")
        cal_line = next((l for l in info_lines if l.startswith("CAL ")), None)

        try:
            stats_before_lines = dev.command("stats", timeout=_STATS_TIMEOUT_S)
        except TimeoutError as e:
            raise PullFailed(f"the puck didn't answer its first 'stats': {e}") from e
        device_log.extend(_log_lines(stats_before_lines))
        stats_before_kv = jump._last_tagged(stats_before_lines, "STATS ")
        if not stats_before_kv:
            raise PullFailed(
                "the puck didn't answer its first question (no STATS line)",
                reasons=["no STATS line in the reply to 'stats'"])
        stats_before_line = next(
            (l for l in stats_before_lines if l.startswith("STATS ")), None)
        # The ONE wall-clock anchor the trace ever gets (CONTRACT.md SS2.3):
        # uptime_s and this machine's clock, read in the same breath.
        synced_at = datetime.now().astimezone()

        pull_log_start = len(device_log)
        pull_started = time.monotonic()
        transport_bytes = 0

        def _count(lines):
            nonlocal transport_bytes
            transport_bytes += sum(len(l.encode("utf-8")) + 1 for l in lines)

        try:
            jumps_lines = dev.command("jumps", timeout=_JUMPS_TIMEOUT_S)
        except TimeoutError as e:
            raise PullFailed(f"the puck didn't answer 'jumps': {e}") from e
        device_log.extend(_log_lines(jumps_lines))
        _count(jumps_lines)
        if jumps_lines and jumps_lines[-1].startswith("ERR"):
            raise PullFailed(f"the puck refused 'jumps': {jumps_lines[-1]}",
                             reasons=[jumps_lines[-1]])
        jumps_csv_lines = _csv_body(
            jump.parse_file_sections(jumps_lines).get("jumps.csv", []))

        trace_format: "Optional[str]" = None
        trace_bin_bytes: "Optional[bytes]" = None
        trace_csv_lines: "Optional[list[str]]" = None
        b64_error = None
        expected_raw = None
        got_raw_len = None
        crc_expected = None
        crc_actual = None
        log_hz = None

        try:
            traceraw_lines = dev.command("traceraw", timeout=_TRACERAW_TIMEOUT_S)
        except TimeoutError as e:
            raise PullFailed(f"the puck didn't answer 'traceraw': {e}") from e
        last = traceraw_lines[-1].strip() if traceraw_lines else ""

        if last == "ERR unknown_command traceraw":
            # CONTRACT.md SS1.4: fall back to CSV on THIS ERR only.
            device_log.extend(_log_lines(traceraw_lines))
            _count(traceraw_lines)
            try:
                trace_lines = dev.command("trace", timeout=_TRACE_TIMEOUT_S)
            except TimeoutError as e:
                raise PullFailed(f"the puck didn't answer 'trace': {e}") from e
            device_log.extend(_log_lines(trace_lines))
            _count(trace_lines)
            if trace_lines and trace_lines[-1].startswith("ERR"):
                raise PullFailed(f"the puck refused 'trace': {trace_lines[-1]}",
                                 reasons=[trace_lines[-1]])
            trace_format = "csv"
            trace_csv_lines = _csv_body(
                jump.parse_file_sections(trace_lines).get("trace.csv", []))
        elif last.startswith("ERR"):
            # Every OTHER ERR (storage_down, traceraw_unsupported, ...) is
            # reported as-is and aborts -- CONTRACT.md SS1.4: "there is no CSV
            # path that recovers from the storage layer being down."
            device_log.extend(_log_lines(traceraw_lines))
            _count(traceraw_lines)
            raise PullFailed(f"the puck refused 'traceraw': {last}", reasons=[last])
        else:
            device_log.extend(_log_lines(traceraw_lines))
            _count(traceraw_lines)
            trace_format = "jhtrace-v2-b64"
            files = jump.parse_file_sections(traceraw_lines)
            # Same rule as _csv_body() above, on the raw frame.
            body_lines = _csv_body(files.get("trace.bin", []))
            trace_bin_bytes, b64_error = _decode_raw_body(body_lines)
            got_raw_len = len(trace_bin_bytes)
            crc_actual = f"{zlib.crc32(trace_bin_bytes) & 0xffffffff:08x}"
            # web/sync/sync.js's classify(): '# traceraw bytes=N log_hz=H
            # region_bytes=R' before the frame, '# traceraw crc32=xxx bytes=N'
            # after -- told apart by which carries crc32=.
            for line in traceraw_lines:
                if line.startswith("# traceraw"):
                    kv = jump.parse_kv(line)
                    # web/sync/sync.js's classify(): bytes= is checked on
                    # EVERY traceraw chatter line, first one wins -- a
                    # second bytes= that disagrees (on the crc line) is a
                    # finding, not a tie-break, and the count check speaks
                    # to it, not this extraction.
                    if expected_raw is None and "bytes" in kv:
                        expected_raw = _int_or_none(kv["bytes"])
                    if kv.get("log_hz") is not None:
                        log_hz = _int_or_none(kv["log_hz"])
                    if "crc32" in kv:
                        crc_expected = str(kv["crc32"]).lower()

        try:
            stats_after_lines = dev.command("stats", timeout=_STATS_TIMEOUT_S)
        except TimeoutError as e:
            raise PullFailed(f"the puck didn't answer its second 'stats': {e}") from e
        device_log.extend(_log_lines(stats_after_lines))
        _count(stats_after_lines)
        if stats_after_lines and stats_after_lines[-1].startswith("ERR"):
            raise PullFailed(f"the puck refused its second 'stats': "
                             f"{stats_after_lines[-1]}",
                             reasons=[stats_after_lines[-1]])
        stats_after_kv = jump._last_tagged(stats_after_lines, "STATS ")
        stats_after_line = next(
            (l for l in stats_after_lines if l.startswith("STATS ")), None)

        # selftest is diagnostic, not required (CONTRACT.md SS3 step 2): a
        # failure or a timeout here costs a manifest field, never the ride.
        try:
            selftest_lines = dev.command("selftest", timeout=_SELFTEST_TIMEOUT_S)
        except TimeoutError:
            selftest_lines = ["# page: selftest did not finish"]
        device_log.extend(_log_lines(selftest_lines))
        _count(selftest_lines)

        transport_seconds = round(time.monotonic() - pull_started, 1)

        if log_hz is None:
            log_hz = _int_or_none(info_kv.get("log_hz"))

        jump_rows = _jump_row_count(jumps_csv_lines)
        storage_down = (_storage_down(info_lines, info_kv)
                        or _storage_down(stats_before_lines, stats_before_kv)
                        or _storage_down(stats_after_lines, stats_after_kv))
        incomplete_text = _first_incomplete(device_log[pull_log_start:])
        stored_jumps_device = _int_or_none(stats_before_kv.get("stored_jumps"))

        ctx = PullContext(
            storage_down=storage_down,
            incomplete_text=incomplete_text,
            stored_jumps_device=stored_jumps_device,
            jump_rows=jump_rows,
            trace_format=trace_format,
            b64_error=b64_error,
            expected_raw=expected_raw,
            got_raw_len=got_raw_len,
            crc_expected=crc_expected,
            crc_actual=crc_actual,
            trace_bytes_device=_int_or_none(stats_before_kv.get("trace_bytes")),
            trace_bytes_after=_int_or_none(stats_after_kv.get("trace_bytes")),
            got_csv_bytes=(len(_join_body(trace_csv_lines).encode("utf-8"))
                          if trace_format == "csv" else None),
        )
        verify = verify_pull(ctx)

        puck_name = _extract_puck_name(device_log)

        manifest = _build_manifest(
            puck_name=puck_name, info_kv=info_kv, cal_line=cal_line,
            synced_at=synced_at, stats_before_line=stats_before_line,
            stats_after_line=stats_after_line, stats_before_kv=stats_before_kv,
            stats_after_kv=stats_after_kv, info_lines=info_lines,
            selftest_lines=selftest_lines, trace_format=trace_format,
            log_hz=log_hz, trace_raw_len=got_raw_len, trace_crc_hex=crc_actual,
            verify=verify, jump_rows=jump_rows, transport_bytes=transport_bytes,
            transport_seconds=transport_seconds)
        # trace_bytes_got: null on the raw path, the csv body length on the
        # csv path (CONTRACT.md SS2.2) -- _build_manifest can't see ctx, so
        # it is corrected here rather than threading one more parameter
        # through every raw-path caller too.
        manifest["trace_bytes_got"] = ctx.got_csv_bytes

        bundle_path = _write_bundle(
            spool_dir, manifest, jumps_csv_lines, trace_format, trace_bin_bytes,
            trace_csv_lines, device_log, synced_at)

        return JobResult(
            bundle_path=bundle_path, verified=verify.verified,
            reasons=verify.reasons, jumps=jump_rows,
            stats_before=stats_before_line, stats_after=stats_after_line,
            puck_name=puck_name, src=info_kv.get("src"))
    finally:
        dev.close()


# --------------------------------------------------------------- clear_puck

def clear_puck(port_path: str, *,
               device_factory: "Callable[[str], object] | None" = None) -> ClearResult:
    """docs/sync-agent-plan.md's step 7: `clear`, confirm with a FRESH
    `stats` (never trust `clear`'s own OK -- G4), then `tracecheck` where the
    puck answers it.

    ok is True only when the confirming `stats` reads BOTH stored_jumps==0
    AND trace_bytes==0 -- web/sync/sync.js's doClear(): "never call a wipe
    done on a reading that says otherwise" -- AND, when tracecheck answered,
    its slow (re-walked) number agrees: main.cpp's own wording for a
    fast/slow mismatch is "the slow number is the correct one"
    (tools/jump's TraceCheck/_query_tracecheck), so a live 0 a full walk
    still finds bytes behind is not actually empty. tracecheck is best-
    effort ("where available"): an older firmware's ERR unknown_command, a
    timeout, or any other failure to answer leaves `tracecheck` naming why
    but does not by itself fail an otherwise-confirmed clear.

    Never raises: every failure -- a `clear` that errors or times out, a
    `stats` that errors, times out, or is silent -- comes back as
    ok=False, exactly like this module's read_stats()."""
    jump = _jump()
    device_factory = device_factory or jump.Device
    dev = device_factory(port_path)
    try:
        dev.drain_boot()

        try:
            clear_lines = dev.command("clear", timeout=_CLEAR_TIMEOUT_S)
        except TimeoutError:
            return ClearResult(ok=False, stored_jumps_after=None,
                               trace_bytes_after=None, tracecheck=None)
        if clear_lines and clear_lines[-1].startswith("ERR"):
            return ClearResult(ok=False, stored_jumps_after=None,
                               trace_bytes_after=None, tracecheck=None)

        try:
            stats_lines = dev.command("stats", timeout=_STATS_TIMEOUT_S)
        except TimeoutError:
            return ClearResult(ok=False, stored_jumps_after=None,
                               trace_bytes_after=None, tracecheck=None)
        if stats_lines and stats_lines[-1].startswith("ERR"):
            return ClearResult(ok=False, stored_jumps_after=None,
                               trace_bytes_after=None, tracecheck=None)

        kv = jump._last_tagged(stats_lines, "STATS ")
        jumps_after = _int_or_none(kv.get("stored_jumps"))
        bytes_after = _int_or_none(kv.get("trace_bytes"))
        ok = jumps_after == 0 and bytes_after == 0

        tracecheck = None
        if ok:
            tracecheck = jump._query_tracecheck(dev)
            if tracecheck.slow is not None and tracecheck.slow > 0:
                ok = False

        return ClearResult(ok=ok, stored_jumps_after=jumps_after,
                           trace_bytes_after=bytes_after, tracecheck=tracecheck)
    finally:
        dev.close()


# --------------------------------------------------------------- read_stats

def read_src(port_path: str, *,
             device_factory: "Callable[[str], object] | None" = None) -> "Optional[str]":
    """The firmware build on the puck, from `info` (src=...). Never raises;
    None when the puck did not answer, which needs_update() reads as
    "unknown, do not flash"."""
    jump = _jump()
    device_factory = device_factory or jump.Device
    try:
        dev = device_factory(port_path)
    except Exception:
        return None
    try:
        dev.drain_boot()
        try:
            lines = dev.command("info", timeout=_INFO_TIMEOUT_S)
        except TimeoutError:
            return None
        kv = jump._last_tagged(lines, "INFO ")
        return kv.get("src") if kv else None
    finally:
        dev.close()


def read_stats(port_path: str, *,
               device_factory: "Callable[[str], object] | None" = None) -> dict:
    """The battery poll (docs/sync-agent-plan.md item 10): one `stats`,
    parsed into the menu-bar's raw materials. Never raises -- a daemon
    polling every 60 s while the puck is attached must survive one bad read
    (a stall, a stray ERR) rather than crash the whole loop over it; on any
    failure every field comes back None and `error` names why.

    trace_full is firmware's own STATS adder key (firmware/src/main.cpp:850-
    856, landed with F-36's fix, src=54c6826d, "Nothing tells the rider the
    trace is full... This is the STATS key") -- read directly, never
    inferred from a byte-count threshold. tools/jump ~:1747-1756 explicitly
    DISABLED a config/params.json trace_max_bytes comparison for exactly
    this field's job, because that constant is CSV-decoded-byte-sized on an
    ESP32-era store the nRF52 store does not use, while STATS trace_bytes is
    raw region bytes -- a unit mismatch that fired ~44 minutes into any
    session (CLAUDE.md rule 6: cite what the source says, and it says don't).
    tools/fake_device.py does not emit this key (no board on any bench has
    ever filled a region against this firmware build) -- trace_full is
    always False against the fake, a documented gap, not a bug here.

    Like STATS's other counts, trace_full/stored_jumps/trace_bytes go to
    None (not a false/zero) when fs=down: "a reading that could not be
    taken must never be dressed up as a reading of zero"
    (firmware/src/main.cpp's own comment on this exact line)."""
    jump = _jump()
    device_factory = device_factory or jump.Device
    empty = {"vbat_mv": None, "batt_pct": None, "chg": None, "trace_bytes": None,
             "stored_jumps": None, "trace_full": None, "error": None}
    try:
        dev = device_factory(port_path)
    except Exception as e:
        return {**empty, "error": f"could not open the port: {e}"}
    try:
        dev.drain_boot()
        try:
            lines = dev.command("stats", timeout=_STATS_TIMEOUT_S)
        except TimeoutError as e:
            return {**empty, "error": f"the puck didn't answer 'stats': {e}"}
        if lines and lines[-1].startswith("ERR"):
            return {**empty, "error": f"the puck refused 'stats': {lines[-1]}"}
        kv = jump._last_tagged(lines, "STATS ")
        if not kv:
            return {**empty, "error": "no STATS line in the reply to 'stats'"}

        fs_down = _storage_down(lines, kv)
        return {
            "vbat_mv": _int_or_none(kv.get("vbat_mv")),
            "batt_pct": _int_or_none(kv.get("batt_pct")),
            "chg": _int_or_none(kv.get("chg")),
            "trace_bytes": None if fs_down else _int_or_none(kv.get("trace_bytes")),
            "stored_jumps": None if fs_down else _int_or_none(kv.get("stored_jumps")),
            "trace_full": None if fs_down else (kv.get("trace_full") == "1"),
            "error": None,
        }
    finally:
        dev.close()
