#!/usr/bin/env python3
"""score.py — align a real session to its ground truth, generate jump
candidates liberally, and score them against what Surfr says.

This is `docs/accuracy-plan.md` turned into a command. That plan opens with a
measured contradiction: on 2026-09-14 evening Surfr counted 32 jumps with a
longest airtime of 3.8 s, the puck's firmware stored 11 with a best height of
1.11 m, and a replay of the same trace found 7 events (gate 0.35 g) or 25
(gate 0.50 g, cap 6 s). The plan's diagnosis is that a wing jump is not
ballistic — the wing carries part of the rider, so mid-air load sits near
0.4-0.6 g instead of near 0 — and that the fix is to stop gating on free-fall
and start generating candidates from a LOAD BAND, then score them.

WHAT THIS FILE IS NOT
---------------------
It is not a second detector. `sim/detector.py` is a 1:1 mirror of
`firmware/include/jump_detector.h` and changing it changes silicon; this
module is the offline half of the loop, deliberately loose (recall first),
and it imports the stock Detector only to print it alongside as a control.
Nothing here is allowed to edit it.

WHY THE AIRTIME HAS TWO DEFINITIONS
-----------------------------------
A candidate carries both `band_s` (how long the load actually stayed inside
the band) and `airtime_s` (takeoff to the landing spike). They are NOT the
same number and the difference is the whole finding of this session:

  measured, data/sessions/20260914-210637-E2C4/trace.csv, all 648,808 rows,
  by this module's own lowest_mean_load() — the lowest MEAN load over any
  contiguous window of the entire 417-minute trace is 0.9439 g at 3.0 s and
  0.9447 g at 3.8 s. The deepest 1.0 s window is 0.5765 g and the deepest
  2.0 s window 0.8560 g.

So a "3.8 s flight at 0.4-0.6 g" does not exist in this trace under any
threshold, because no threshold can find a low-load interval that is not
there. `band_s` is what the trace supports; `airtime_s` is the looser
contact-to-contact reading that can reach Surfr's timescale only by counting
ordinary-load time as flight. Reporting one without the other would hide
which of the two is doing the work — see the scorecard's honesty section.

Pure standard library plus `tools/fitread.py` for the Garmin file. There is
no second FIT reader here on purpose (CLAUDE.md section 4: an identifier —
or a parser — without a single lookup entry is a rediscovery waiting to
happen); `garmin.fit` may be absent and the whole command still runs.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "sim") not in sys.path:
    sys.path.insert(0, str(REPO / "sim"))
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from detector import Detector, Params, height_for_airtime  # noqa: E402

UTC = dt.timezone.utc
G = 9.80665

# A run of samples cannot bridge a recording gap. The puck sleeps on an idle
# timeout (idle_timeout_s=20 in this session's INFO line), so the trace is not
# one continuous stream: MEASURED on the evening trace, 41 contiguous segments
# separated by 38 gaps longer than 1 s, the largest 3,408 s. Stitching across
# one would manufacture a flight out of a nap.
MAX_GAP_S = 0.5

FT_PER_M = 3.280839895


# ------------------------------------------------------------------- loading


def load_trace(path: Path) -> tuple[list[float], list[float]]:
    """(times_s, accel_magnitude_g) from a trace.csv with a `t,mag` header."""
    times: list[float] = []
    mag: list[float] = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            raise ValueError(f"{path}: empty file")
        cols = [c.strip().lower() for c in header]
        try:
            it, im = cols.index("t"), cols.index("mag")
        except ValueError:
            it, im = 0, 1  # headerless / differently named: positional
            for row in (header,):
                try:
                    times.append(float(row[it]))
                    mag.append(float(row[im]))
                except (ValueError, IndexError):
                    pass
        for row in reader:
            try:
                times.append(float(row[it]))
                mag.append(float(row[im]))
            except (ValueError, IndexError):
                continue
    return times, mag


# A backward step larger than this is a reboot (uptime restarted), not
# timestamp jitter. MEASURED: the evening trace's 15 backward steps are all
# between -0.024 s and -0.078 s and land on 2 s boundaries (a periodic
# flush/re-sync artifact); the morning trace's single one is -287,847.9 s.
# Nothing in either file lies between 0.1 s and 1 s, so this threshold is not
# balanced on a knife edge.
BOOT_RESET_S = 1.0


def is_continuous(dt_s: float, max_gap_s: float = MAX_GAP_S) -> bool:
    """Do two consecutive samples belong to the same unbroken run?

    Both ends matter. Too large a FORWARD step is the puck's idle sleep. A
    large BACKWARD step is a reboot — and testing only `dt <= max_gap_s`
    accepts it, because -287,847 s is very much less than 0.5. That is how a
    multi-boot ring buffer gets stitched into one imaginary continuous
    session; see Timebase. Sub-second backward jitter is tolerated on purpose
    (measured worst case -0.078 s), since rejecting it would fragment real
    flights for no gain.
    """
    return -BOOT_RESET_S <= dt_s <= max_gap_s


def contiguous_segments(times: Sequence[float],
                        max_gap_s: float = MAX_GAP_S) -> list[tuple[int, int]]:
    """Inclusive (start, end) index pairs of runs that are continuous
    end-to-end by is_continuous()."""
    if not times:
        return []
    segs = []
    start = 0
    for i in range(1, len(times)):
        if not is_continuous(times[i] - times[i - 1], max_gap_s):
            segs.append((start, i - 1))
            start = i
    segs.append((start, len(times) - 1))
    return segs


@dataclass
class Timebase:
    """What the trace's `t` column actually does, before anything trusts it.

    `trace_epoch_utc` pins ONE number: the wall clock at uptime t=0 of the
    boot that was running when the bundle synced. The trace is a flash ring
    buffer that survives a power cycle, so a file can hold rows from an
    EARLIER boot whose t values count from a different, unknown epoch. Mapping
    those with this session's epoch produces confident nonsense — measured on
    data/sessions/20260914-104207-E2C4, where the first row is t=76,608.8 s
    and the last is t=12.7 s.
    """

    n_rows: int
    t_min: float
    t_max: float
    boot_resets: list[tuple[int, float, float]]   # (row, from_t, to_t)
    jitter_steps: list[tuple[int, float]]         # (row, negative delta)
    last_boot_start_idx: int                      # first row of the final boot
    last_boot_t0: float
    last_boot_t1: float

    @property
    def multi_boot(self) -> bool:
        return bool(self.boot_resets)


def analyse_timebase(times: Sequence[float]) -> Timebase:
    resets: list[tuple[int, float, float]] = []
    jitter: list[tuple[int, float]] = []
    for i in range(len(times) - 1):
        d = times[i + 1] - times[i]
        if d < -BOOT_RESET_S:
            resets.append((i, times[i], times[i + 1]))
        elif d < 0:
            jitter.append((i, d))
    start = resets[-1][0] + 1 if resets else 0
    return Timebase(
        n_rows=len(times), t_min=min(times), t_max=max(times),
        boot_resets=resets, jitter_steps=jitter, last_boot_start_idx=start,
        last_boot_t0=times[start], last_boot_t1=times[-1])


def load_session_json(sess: Path) -> dict:
    p = sess / "session.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except ValueError:
        return {}


def load_surfr(sess: Path) -> Optional[dict]:
    p = sess / "surfr.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except ValueError:
        return None


def load_device_jumps(sess: Path) -> list[dict]:
    """The device's own jumps.csv. Same shape sim/evaluate.py reads."""
    p = sess / "jumps.csv"
    out: list[dict] = []
    if not p.exists():
        return out
    with open(p, newline="") as f:
        for parts in csv.reader(f):
            if len(parts) < 5 or parts[0].strip() in ("n", "#") or parts[0].startswith("#"):
                continue
            try:
                out.append({"takeoff": float(parts[1]), "airtime": float(parts[3]),
                            "height": float(parts[4])})
            except (ValueError, IndexError):
                continue
    return out


# ----------------------------------------------------------------- alignment


def parse_iso_utc(s: str | None) -> Optional[dt.datetime]:
    """ISO 8601 -> aware UTC datetime. Accepts the trailing `Z` form."""
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.replace(tzinfo=UTC) if d.tzinfo is None else d.astimezone(UTC)


# The rider's timezone. surfr.json records `session_start_local` with no
# offset, and the bundle's manifest carries the phone/laptop offset that was
# in force at sync (`tz_offset_min`). Prefer the measured manifest value;
# this constant is the stated fallback, not a silent assumption.
FALLBACK_TZ_OFFSET_MIN = -240  # America/New_York, UTC-4, 2026-09-14


def surfr_tz_offset_min(sess_json: dict, surfr: dict) -> tuple[int, str]:
    """(offset_minutes, where it came from). Never guesses silently."""
    if isinstance(surfr, dict) and surfr.get("tz_offset_min") is not None:
        return int(surfr["tz_offset_min"]), "surfr.json tz_offset_min"
    man = (sess_json or {}).get("manifest") or {}
    if man.get("tz_offset_min") is not None:
        return int(man["tz_offset_min"]), "session.json manifest.tz_offset_min"
    return FALLBACK_TZ_OFFSET_MIN, f"ASSUMED {FALLBACK_TZ_OFFSET_MIN} min (America/New_York)"


def surfr_window(sess_json: dict, surfr: dict | None):
    """(start_utc, end_utc, offset_min, offset_source) for the Surfr session.

    `session_start_local` is shown to the MINUTE in the app, so this start is
    good to +-30 s at best and the whole point of the offset solver below is
    that it must be fitted, not trusted.
    """
    if not surfr:
        return None, None, None, None
    off_min, src = surfr_tz_offset_min(sess_json, surfr)
    local = surfr.get("session_start_local")
    if not local:
        return None, None, off_min, src
    try:
        naive = dt.datetime.fromisoformat(local)
    except ValueError:
        return None, None, off_min, src
    start = naive.replace(tzinfo=dt.timezone(dt.timedelta(minutes=off_min))).astimezone(UTC)
    dur = surfr.get("duration_s")
    end = start + dt.timedelta(seconds=float(dur)) if dur else None
    return start, end, off_min, src


def parse_t_into_session(s) -> Optional[float]:
    """'15:21' -> 921.0 seconds. Also accepts 'H:MM:SS' and a bare number."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    parts = str(s).strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    if len(nums) == 1:
        return nums[0]
    if len(nums) == 2:
        return nums[0] * 60 + nums[1]
    if len(nums) == 3:
        return nums[0] * 3600 + nums[1] * 60 + nums[2]
    return None


@dataclass
class GarminData:
    present: bool
    reason: str = ""
    start_utc: Optional[dt.datetime] = None
    end_utc: Optional[dt.datetime] = None
    record_count: int = 0
    # (epoch_seconds, speed_ms, lat_deg, lon_deg), ascending by time
    samples: list[tuple[float, Optional[float], Optional[float], Optional[float]]] = \
        field(default_factory=list)

    def speed_before(self, when: dt.datetime,
                     max_age_s: float = 30.0) -> Optional[float]:
        """enhanced_speed of the nearest record AT OR BEFORE `when`.

        Returns None rather than a number when the nearest record is older
        than max_age_s: Garmin smart recording leaves gaps, and a speed from
        40 s earlier is not this jump's approach speed (CLAUDE.md section 2.3
        — a reading that did not happen must not look like one that did).
        """
        if not self.samples:
            return None
        target = when.timestamp()
        lo, hi = 0, len(self.samples)
        while lo < hi:
            mid = (lo + hi) // 2
            if self.samples[mid][0] <= target:
                lo = mid + 1
            else:
                hi = mid
        i = lo - 1
        while i >= 0:
            ts, speed, _, _ = self.samples[i]
            if target - ts > max_age_s:
                return None
            if speed is not None:
                return speed
            i -= 1
        return None


def load_garmin(sess: Path) -> GarminData:
    """Read garmin.fit through tools/fitread.py. Absence is a state, not an error."""
    p = sess / "garmin.fit"
    if not p.exists():
        return GarminData(present=False, reason="garmin.fit absent")
    try:
        import fitread  # tools/fitread.py — the repo's one FIT reader
    except ImportError as exc:
        return GarminData(present=False, reason=f"fitread unavailable ({exc})")
    try:
        blob, label = fitread.read_fit_bytes(p)
        scanned = fitread.scan(blob, label)
    except SystemExit as exc:  # fitread.die() on an unreadable file
        return GarminData(present=False, reason=f"garmin.fit unreadable ({exc})")

    samples = []
    for rec in scanned["stamped"]:
        ts = rec["timestamp"].astimezone(UTC)
        lat = rec["position_lat"]
        lon = rec["position_long"]
        samples.append((
            ts.timestamp(),
            rec["enhanced_speed"],
            lat * fitread.SEMICIRCLE_TO_DEG if lat is not None else None,
            lon * fitread.SEMICIRCLE_TO_DEG if lon is not None else None,
        ))
    samples.sort(key=lambda r: r[0])
    if not samples:
        return GarminData(present=False, reason="garmin.fit has no timestamped records")
    return GarminData(
        present=True,
        start_utc=dt.datetime.fromtimestamp(samples[0][0], UTC),
        end_utc=dt.datetime.fromtimestamp(samples[-1][0], UTC),
        record_count=len(scanned["records"]),
        samples=samples,
    )


@dataclass
class Alignment:
    epoch_utc: Optional[dt.datetime]
    epoch_source: str
    trace_start_s: float
    trace_end_s: float
    garmin: GarminData
    surfr_start: Optional[dt.datetime]
    surfr_end: Optional[dt.datetime]
    surfr_tz_offset_min: Optional[int]
    surfr_tz_source: Optional[str]
    findings: list[str]
    timebase: Optional[Timebase] = None

    def t_to_utc(self, t_s: float) -> Optional[dt.datetime]:
        if self.epoch_utc is None:
            return None
        return self.epoch_utc + dt.timedelta(seconds=t_s)

    def utc_to_t(self, when: dt.datetime) -> Optional[float]:
        if self.epoch_utc is None:
            return None
        return (when - self.epoch_utc).total_seconds()

    @property
    def trace_start_utc(self) -> Optional[dt.datetime]:
        return self.t_to_utc(self.trace_start_s)

    @property
    def trace_end_utc(self) -> Optional[dt.datetime]:
        return self.t_to_utc(self.trace_end_s)


def align(sess: Path, times: Sequence[float]) -> Alignment:
    """Map trace time to wall clock and CHECK the mapping against Garmin.

    The check is the point. `trace_epoch_utc` is written by the syncing host
    from the puck's uptime; if the puck's clock ran slow, or the epoch was
    computed from a stale uptime, every wall-clock number downstream is
    silently wrong. So this reports, with numbers, whether the Garmin
    activity lands inside the trace — and separately whether the epoch is
    self-consistent with the bundle's own `synced_at_utc` - `uptime_s`.
    """
    sj = load_session_json(sess)
    epoch = parse_iso_utc(sj.get("trace_epoch_utc"))
    epoch_source = "session.json trace_epoch_utc"
    findings: list[str] = []

    garmin = load_garmin(sess)
    surfr = load_surfr(sess)
    s_start, s_end, s_off, s_off_src = surfr_window(sj, surfr)

    tb = analyse_timebase(times) if times else None

    # The wall-clock-mappable region is the LAST boot only — see Timebase.
    t0 = tb.last_boot_t0 if tb else 0.0
    t1 = tb.last_boot_t1 if tb else 0.0

    a = Alignment(epoch_utc=epoch, epoch_source=epoch_source, trace_start_s=t0,
                  trace_end_s=t1, garmin=garmin, surfr_start=s_start,
                  surfr_end=s_end, surfr_tz_offset_min=s_off,
                  surfr_tz_source=s_off_src, findings=findings, timebase=tb)

    if tb is not None:
        if tb.multi_boot:
            first = tb.boot_resets[0]
            findings.append(
                f"FINDING — MULTI-BOOT TRACE: the `t` column restarts "
                f"{len(tb.boot_resets)} time(s) (row {first[0]}: "
                f"t={first[1]:.3f} s -> {first[2]:.3f} s). trace.csv is a flash "
                f"ring buffer that survived a power cycle, so it holds rows from "
                f"more than one boot. `trace_epoch_utc` pins only the boot that "
                f"was running at sync, so the {tb.last_boot_start_idx:,} row(s) "
                f"before the last reset CANNOT be placed on the wall clock and "
                f"are excluded from every wall-clock number below. That leaves "
                f"{tb.n_rows - tb.last_boot_start_idx:,} mappable row(s), "
                f"t={tb.last_boot_t0:.3f}..{tb.last_boot_t1:.3f} s "
                f"({(tb.last_boot_t1 - tb.last_boot_t0):.1f} s). The excluded rows "
                f"are still real data — they simply have no known epoch.")
        if tb.jitter_steps:
            worst = min(d for _, d in tb.jitter_steps)
            findings.append(
                f"timebase jitter: {len(tb.jitter_steps)} backward step(s) smaller "
                f"than the {BOOT_RESET_S:g} s reboot threshold, worst {worst:.3f} s. "
                f"Too small to be a reboot and too small to move any airtime here, "
                f"but recorded rather than silently sorted away.")

    if epoch is None:
        findings.append(
            "FINDING: session.json carries no `trace_epoch_utc`. Trace time cannot "
            "be mapped to wall clock, so NO alignment, matching or Surfr "
            "comparison below is possible. Everything wall-clock is omitted, "
            "not defaulted.")
        return a

    # --- self-consistency: does epoch + uptime land on the sync instant?
    synced = parse_iso_utc(sj.get("synced_at_utc"))
    uptime = sj.get("device_uptime_s_at_sync")
    if synced is not None and uptime is not None:
        implied = epoch + dt.timedelta(seconds=float(uptime))
        err = (implied - synced).total_seconds()
        verdict = "consistent" if abs(err) <= 1.0 else "INCONSISTENT"
        findings.append(
            f"epoch self-check ({verdict}): trace_epoch_utc + device_uptime_s_at_sync "
            f"({float(uptime):.3f} s) = {implied.isoformat()}, vs synced_at_utc "
            f"{synced.isoformat()} — residual {err:+.3f} s.")
    else:
        findings.append(
            "FINDING: session.json lacks `synced_at_utc` and/or "
            "`device_uptime_s_at_sync`, so the epoch could not be self-checked. "
            "It is taken on trust below.")

    # The last trace sample should land at/near the sync instant too — an
    # independent check that uses the trace itself rather than the manifest.
    if synced is not None:
        last = a.t_to_utc(t1)
        findings.append(
            f"last trace sample t={t1:.3f} s maps to {last.isoformat()}, "
            f"{(last - synced).total_seconds():+.3f} s from synced_at_utc.")

    trace_lo, trace_hi = a.t_to_utc(t0), a.t_to_utc(t1)
    findings.append(
        f"trace window: t={t0:.3f}..{t1:.3f} s -> {trace_lo.isoformat()} .. "
        f"{trace_hi.isoformat()} ({(t1 - t0) / 60.0:.1f} min).")

    # --- the brief's explicit question: does the Garmin activity fall inside?
    if not garmin.present:
        findings.append(
            f"FINDING: {garmin.reason}. The trace-to-Garmin alignment check "
            "DID NOT RUN — this is an absence of evidence, not a passing check, "
            "and no approach speeds are available for any candidate below.")
    else:
        g0 = a.utc_to_t(garmin.start_utc)
        g1 = a.utc_to_t(garmin.end_utc)
        findings.append(
            f"garmin window: {garmin.start_utc.isoformat()} .. "
            f"{garmin.end_utc.isoformat()} ({garmin.record_count} records) -> "
            f"trace t={g0:.1f}..{g1:.1f} s.")
        inside = t0 <= g0 and g1 <= t1
        if inside:
            findings.append(
                f"ALIGNMENT OK: the whole Garmin activity lands inside the trace — "
                f"{g0 - t0:.1f} s after the first trace sample and {t1 - g1:.1f} s "
                f"before the last. trace_epoch_utc is not obviously wrong.")
        else:
            lead = max(0.0, t0 - g0)
            lag = max(0.0, g1 - t1)
            findings.append(
                f"FINDING — ALIGNMENT FAILS: the Garmin activity does NOT fit "
                f"inside the trace. It starts {lead:.1f} s before the first trace "
                f"sample and ends {lag:.1f} s after the last. Either "
                f"trace_epoch_utc is wrong or the puck's clock drifted; every "
                f"wall-clock number below inherits that error.")

        # Corroboration that does not depend on the manifest: the puck only
        # logs while it is moving, so the longest continuous stretch of trace
        # should sit under the activity if the epoch is right.
        segs = contiguous_segments(times)
        if segs:
            si, sj_ = max(segs, key=lambda se: times[se[1]] - times[se[0]])
            seg_lo, seg_hi = times[si], times[sj_]
            overlap = max(0.0, min(seg_hi, g1) - max(seg_lo, g0))
            gspan = max(1e-9, g1 - g0)
            findings.append(
                f"corroboration: the trace's longest continuous segment is "
                f"t={seg_lo:.1f}..{seg_hi:.1f} s ({(seg_hi - seg_lo) / 60.0:.1f} min); "
                f"it covers {100.0 * overlap / gspan:.1f} % of the Garmin activity. "
                f"The puck logs only while moving, so a high figure here is "
                f"independent evidence the epoch is right.")

    # --- Surfr
    if surfr is None:
        findings.append("FINDING: no surfr.json — no reference count, height or "
                        "airtime for this session. Nothing below is scored.")
    elif s_start is None:
        findings.append(
            "FINDING: surfr.json carries no usable `session_start_local`, so "
            "Surfr rows cannot be placed on the clock and the offset solver "
            "DID NOT RUN.")
    else:
        s0 = a.utc_to_t(s_start)
        s1 = a.utc_to_t(s_end) if s_end else None
        findings.append(
            f"surfr window (tz offset {s_off:+d} min from {s_off_src}): "
            f"{s_start.isoformat()}"
            + (f" .. {s_end.isoformat()}" if s_end else " .. (no duration_s)")
            + f" -> trace t={s0:.1f}"
            + (f"..{s1:.1f} s." if s1 is not None else " s.")
            + "  NOTE: Surfr shows session start to the MINUTE, so this window "
              "is good to about +-30 s before any fitting.")
        if garmin.present and s1 is not None:
            g0 = a.utc_to_t(garmin.start_utc)
            g1 = a.utc_to_t(garmin.end_utc)
            ov = max(0.0, min(s1, g1) - max(s0, g0))
            findings.append(
                f"surfr-vs-garmin overlap: {ov:.0f} s "
                f"({100.0 * ov / max(1e-9, s1 - s0):.1f} % of the Surfr session). "
                f"Surfr starts {s0 - g0:+.0f} s relative to the Garmin start and "
                f"ends {s1 - g1:+.0f} s relative to its end.")
    return a


# ---------------------------------------------------------------- candidates


@dataclass
class CandidateParams:
    """Load-band candidate generator settings.

    Every default here is MEASURED on data/sessions/20260914-210637-E2C4 by
    the sweep this module prints (`sweep_table`), not carried over from
    config/params.json and not guessed. They are offline recall settings:
    the plan's shape is "generate liberally, then score", so these are chosen
    to be loose, and precision is expected to come from features later.
    """

    band_g: float = 0.60        # flight = load below this
    pop_g: float = 1.50         # takeoff pop required just before
    spike_g: float = 2.50       # landing spike required just after
    min_air_s: float = 0.40
    max_air_s: float = 8.00     # deliberately above Surfr's 3.8 s max
    band_tol_s: float = 0.10    # excursions above band_g shorter than this do
                                # not end the band (a strict per-sample band
                                # found 0 candidates at band_g <= 0.6 —
                                # measured, sweep below)
    pop_window_s: float = 1.00
    spike_window_s: float = 2.00
    max_gap_s: float = MAX_GAP_S


@dataclass
class Candidate:
    takeoff_s: float
    land_s: float
    airtime_s: float      # takeoff -> landing spike (contact to contact)
    band_s: float         # how long the load actually stayed inside the band
    mean_load_g: float    # mean |a| over the flight, in g
    min_load_g: float
    pop_g: float
    spike_g: float
    speed_ms: Optional[float] = None
    takeoff_utc: Optional[dt.datetime] = None

    @property
    def lift_fraction(self) -> float:
        """L, the fraction of the rider's weight the wing carries in flight.

        docs/accuracy-plan.md writes "L = mean load / g". `mean_load_g` is
        already expressed IN g (the trace's `mag` column is a magnitude in g,
        not in m/s^2), so L is that number directly — dividing by 9.80665
        again would be a units error that shrinks every correction by 10x.
        """
        return self.mean_load_g

    @property
    def h_ballistic_m(self) -> float:
        return height_for_airtime(self.airtime_s, G)

    @property
    def h_lift_m(self) -> float:
        """h = (1 - L) g T^2 / 8.

        Clamped at 0: L > 1 means the mean load in "flight" was above 1 g,
        i.e. the candidate was never unloaded, and the model has no meaning
        there. A negative height would be worse than a zero — it would look
        like a measurement.
        """
        return max(0.0, (1.0 - self.lift_fraction)) * G * self.airtime_s ** 2 / 8.0


def find_candidates(times: Sequence[float], mag: Sequence[float],
                    cp: CandidateParams | None = None) -> list[Candidate]:
    """Load-band candidate generator (docs/accuracy-plan.md, "Detection").

    A candidate is: a takeoff pop above `pop_g` in the `pop_window_s` before,
    then a sustained band of load below `band_g` lasting at least
    `min_air_s` (brief excursions shorter than `band_tol_s` do not end it),
    then a landing spike above `spike_g` within `spike_window_s` after the
    band. Airtime runs takeoff -> spike; `band_s` records how much of that
    was genuinely unloaded.

    No run may bridge a gap longer than `max_gap_s` (the puck sleeps).
    """
    cp = cp or CandidateParams()
    n = len(times)
    out: list[Candidate] = []
    i = 0
    while i < n:
        if mag[i] >= cp.band_g:
            i += 1
            continue

        # Extend the band, tolerating excursions shorter than band_tol_s.
        last_low = i
        k = i
        while k + 1 < n and is_continuous(times[k + 1] - times[k], cp.max_gap_s):
            k += 1
            if mag[k] < cp.band_g:
                last_low = k
            elif times[k] - times[last_low] > cp.band_tol_s:
                break
        band_s = times[last_low] - times[i]

        if band_s < cp.min_air_s:
            i = max(last_low + 1, i + 1)
            continue

        # Landing spike: first sample above spike_g after the band ends.
        land = None
        j = last_low + 1
        while (j < n and times[j] - times[last_low] <= cp.spike_window_s
               and is_continuous(times[j] - times[j - 1], cp.max_gap_s)):
            if mag[j] > cp.spike_g:
                land = j
                break
            j += 1
        if land is None:
            i = max(last_low + 1, i + 1)
            continue

        airtime = times[land] - times[i]
        if not (cp.min_air_s <= airtime <= cp.max_air_s):
            i = max(last_low + 1, i + 1)
            continue

        # Takeoff pop: peak load in the window before the band opened.
        # The walk back must stop at a discontinuity, not just at the window
        # edge. `times[i] - times[lo - 1] <= pop_window_s` is TRUE across a
        # boot reset (the difference is about -287,000), so without the
        # continuity guard a candidate at the start of one boot borrows its
        # takeoff pop from the end of the previous one — a fabricated pop for
        # a jump that has none. Found by
        # tools/tests/test_score.py::test_boot_reset_cannot_manufacture_a_flight.
        lo = i
        while (lo > 0 and times[i] - times[lo - 1] <= cp.pop_window_s
               and is_continuous(times[lo] - times[lo - 1], cp.max_gap_s)):
            lo -= 1
        pop = max(mag[lo:i]) if i > lo else 0.0
        if pop < cp.pop_g:
            i = max(last_low + 1, i + 1)
            continue

        seg = mag[i:land]
        out.append(Candidate(
            takeoff_s=times[i], land_s=times[land], airtime_s=airtime,
            band_s=band_s, mean_load_g=sum(seg) / len(seg), min_load_g=min(seg),
            pop_g=pop, spike_g=mag[land]))
        # Resume PAST the landing spike, not at last_low + 1. Restarting
        # inside the flight lets a band that was cut short by an excursion
        # re-open before `land` and reach the SAME landing sample, emitting
        # one jump as two — the very double-count band_tol_s exists to
        # prevent (see test_band_tolerance_survives_a_single_stray_sample).
        # MEASURED on data/sessions/20260914-210637-E2C4 at the scorecard's
        # own chosen operating point (band 0.7 g, pop 1.2 g, spike 2.5 g,
        # min air 0.4 s): candidates at t=10743.627 and t=10744.347 shared
        # the landing sample t=10745.367, so the "32 candidates = Surfr's
        # 32" that fixed the operating point was 31 events plus a duplicate.
        # 20 of the 90 sweep grid points carried at least one such pair, up
        # to 15 of them at band_g=0.8.
        i = max(land + 1, i + 1)
    return out


def device_jump_coverage(jumps: Sequence[dict], times: Sequence[float],
                         sess_json: dict) -> str:
    """Do the device's own stored jumps belong to THIS trace?

    They often do not. `jumps.csv` is the device's STORED list, which survives
    a power cycle exactly as the trace ring buffer does — and the bundle's own
    `stats_before` distinguishes `session_jumps` (this power-up) from
    `stored_jumps` (everything kept). MEASURED on
    data/sessions/20260914-104207-E2C4: `session_jumps=0`, 20 stored rows, and
    their takeoff times (170,011 s / 10,605 s / 21,544 s) come from at least
    three different boots. Comparing 20 stored jumps to what a detector finds
    in this trace would be comparing a lifetime to an afternoon.
    """
    if not jumps:
        return "no jumps.csv rows"
    lo, hi = min(times), max(times)
    inside = sum(1 for j in jumps if lo <= j["takeoff"] <= hi)
    bits = [f"{len(jumps)} stored row(s); {inside} have a takeoff_s inside the "
            f"trace's t range ({lo:.1f}..{hi:.1f} s)"]
    stats = ((sess_json or {}).get("manifest") or {}).get("stats_before") or ""
    for tok in str(stats).split():
        if tok.startswith("session_jumps="):
            n = tok.split("=", 1)[1]
            bits.append(f"the bundle's own `stats_before` says `{tok}`")
            if n == "0":
                bits.append("so NONE of these rows were recorded in this "
                            "session — they are the device's carried-over "
                            "stored list, and must not be read as this "
                            "session's detections")
            break
    else:
        bits.append("`stats_before` carries no `session_jumps`, so how many of "
                    "these rows belong to this session WAS NOT DETERMINED")
    return "; ".join(bits)


def run_stock_detector(times: Sequence[float], mag: Sequence[float],
                       params: Params | None = None) -> list:
    """The unmodified firmware-mirror detector, as a control. Never tuned here."""
    det = Detector(params or Params())
    events = []
    for t, m in zip(times, mag):
        ev = det.update(t, m)
        if ev is not None:
            events.append(ev)
    return events


def slice_window(times: Sequence[float], mag: Sequence[float],
                 t_lo: Optional[float], t_hi: Optional[float]):
    """The sub-trace inside [t_lo, t_hi]; the whole thing if either is None."""
    if t_lo is None or t_hi is None:
        return list(times), list(mag)
    out_t, out_m = [], []
    for t, m in zip(times, mag):
        if t_lo <= t <= t_hi:
            out_t.append(t)
            out_m.append(m)
    return out_t, out_m


# --------------------------------------------------------------------- sweep

# The grid the brief asks for. band_g runs 0.4..0.8 because that is the range
# docs/accuracy-plan.md says the replay showed for this rider and wing; the
# other axes bracket the firmware's own values (landing_threshold_g 2.5,
# min_airtime_s 0.25) so the stock settings are inside the grid rather than
# outside it.
SWEEP_BAND_G = (0.4, 0.5, 0.6, 0.7, 0.8)
SWEEP_POP_G = (1.2, 1.5, 2.0)
SWEEP_SPIKE_G = (2.0, 2.5)
SWEEP_MIN_AIR_S = (0.30, 0.40, 0.60)


@dataclass
class SweepRow:
    band_g: float
    pop_g: float
    spike_g: float
    min_air_s: float
    count: int
    top_airtimes: list[float]
    top_band_s: list[float]
    longest_band_s: float


def sweep_table(times: Sequence[float], mag: Sequence[float],
                base: CandidateParams | None = None) -> list[SweepRow]:
    base = base or CandidateParams()
    rows: list[SweepRow] = []
    for band in SWEEP_BAND_G:
        for pop in SWEEP_POP_G:
            for spike in SWEEP_SPIKE_G:
                for mn in SWEEP_MIN_AIR_S:
                    cp = CandidateParams(
                        band_g=band, pop_g=pop, spike_g=spike, min_air_s=mn,
                        max_air_s=base.max_air_s, band_tol_s=base.band_tol_s,
                        pop_window_s=base.pop_window_s,
                        spike_window_s=base.spike_window_s,
                        max_gap_s=base.max_gap_s)
                    cands = find_candidates(times, mag, cp)
                    airs = sorted((c.airtime_s for c in cands), reverse=True)
                    bands = sorted((c.band_s for c in cands), reverse=True)
                    rows.append(SweepRow(
                        band_g=band, pop_g=pop, spike_g=spike, min_air_s=mn,
                        count=len(cands), top_airtimes=airs[:3],
                        top_band_s=bands[:3],
                        longest_band_s=bands[0] if bands else 0.0))
    return rows


def best_row_for_count(rows: Sequence[SweepRow], target: int) -> Optional[SweepRow]:
    """The grid point whose candidate count is closest to `target`.

    Ties break toward the TIGHTER setting (lower band_g, then higher pop_g,
    then higher spike_g): with one session and no per-jump truth, the count
    alone cannot tell a right detector from a lucky one, so when two settings
    agree equally well the one that admits less is preferred.
    """
    if not rows:
        return None
    return min(rows, key=lambda r: (abs(r.count - target), r.band_g,
                                    -r.pop_g, -r.spike_g, -r.min_air_s))


# ------------------------------------------------------ low-load reachability

def lowest_mean_load(times: Sequence[float], mag: Sequence[float],
                     window_s: float,
                     max_gap_s: float = MAX_GAP_S) -> Optional[tuple[float, float]]:
    """(lowest mean load in g, trace time it starts) over any contiguous
    window of `window_s`, or None if no contiguous window that long exists.

    This is the question the thresholds cannot answer: a sweep can only find
    a low-load interval that is in the data. If the answer here is ~1 g for
    a 3 s window, then no band_g whatsoever yields a 3 s flight, and any
    tuning that appears to is measuring something else.
    """
    best = None
    for s, e in contiguous_segments(times, max_gap_s):
        i = s
        for j in range(s, e + 1):
            # Shrink to the TIGHTEST window ending at j that still spans at
            # least window_s. Advancing i while `times[j] - times[i] > window_s`
            # instead and then demanding equality is the trap this comment
            # exists for: at 50 Hz a 3.0 s window lands on a sample boundary
            # and a 3.8 s one does not, so the 3.8 s row reported "no
            # contiguous window that long" inside a 103-minute unbroken
            # stretch — a silent failure dressed as a finding (CLAUDE.md §2.3).
            while i < j and times[j] - times[i + 1] >= window_s:
                i += 1
            if times[j] - times[i] < window_s:
                continue
            m = sum(mag[i:j + 1]) / (j + 1 - i)
            if best is None or m < best[0]:
                best = (m, times[i])
    return best


# -------------------------------------------------------------- offset solver


@dataclass
class OffsetFit:
    offset_s: float
    residuals_s: list[Optional[float]]
    matched_idx: list[Optional[int]]
    searched_s: float
    n_rows: int

    @property
    def mean_abs_residual_s(self) -> Optional[float]:
        got = [abs(r) for r in self.residuals_s if r is not None]
        return sum(got) / len(got) if got else None


def solve_offset(surfr_takeoff_s: Sequence[float],
                 candidate_takeoff_s: Sequence[float],
                 search_s: float = 120.0,
                 step_s: float = 0.25,
                 max_pair_s: float = 20.0) -> Optional[OffsetFit]:
    """The ONE per-session offset that best aligns Surfr row times to candidate
    takeoffs, searched over +-`search_s`.

    Cost is the sum of |nearest candidate - (surfr + offset)|, with a row that
    has no candidate within `max_pair_s` charged the full `max_pair_s` rather
    than being dropped — otherwise the solver would "improve" the fit by
    sliding rows until they matched nothing at all.
    """
    if not surfr_takeoff_s or not candidate_takeoff_s:
        return None
    # (takeoff, ORIGINAL index) so a match can be reported by position.
    # Looking the index back up by float VALUE — `{t: i for i, t in
    # enumerate(...)}` — silently collapses two candidates that share a
    # takeoff time and returns None for the loser, which renders as "NO
    # CANDIDATE within the pairing window" for a row that did in fact match.
    # data/sessions/20260914-210637-E2C4/trace.csv does carry a repeated
    # timestamp (t=9902.002), so equal takeoffs are not hypothetical.
    order = sorted(range(len(candidate_takeoff_s)),
                   key=lambda i: candidate_takeoff_s[i])
    cands = [candidate_takeoff_s[i] for i in order]

    def nearest(x: float) -> tuple[Optional[int], float]:
        lo, hi = 0, len(cands)
        while lo < hi:
            mid = (lo + hi) // 2
            if cands[mid] < x:
                lo = mid + 1
            else:
                hi = mid
        best_i, best_d = None, max_pair_s
        for i in (lo - 1, lo):
            if 0 <= i < len(cands):
                d = abs(cands[i] - x)
                if d < best_d:
                    best_i, best_d = i, d
        return best_i, (best_d if best_i is not None else max_pair_s)

    best = None
    steps = int(round(2 * search_s / step_s)) + 1
    for k in range(steps):
        off = -search_s + k * step_s
        cost = sum(nearest(s + off)[1] for s in surfr_takeoff_s)
        if best is None or cost < best[0] - 1e-12:
            best = (cost, off)
    off = best[1]

    resid: list[Optional[float]] = []
    idx: list[Optional[int]] = []
    for s in surfr_takeoff_s:
        i, _ = nearest(s + off)
        if i is None:
            resid.append(None)
            idx.append(None)
        else:
            resid.append(cands[i] - (s + off))
            idx.append(order[i])
    return OffsetFit(offset_s=off, residuals_s=resid, matched_idx=idx,
                     searched_s=search_s, n_rows=len(surfr_takeoff_s))


# ------------------------------------------------------------------ scorecard


@dataclass
class SessionScore:
    sess: Path
    alignment: Alignment
    n_trace_rows: int
    sample_hz: Optional[float]
    n_segments: int
    device_jumps: list[dict]
    stock_events: list
    stock_events_gate50: list
    candidates: list[Candidate]
    chosen: CandidateParams
    sweep: list[SweepRow]
    surfr: Optional[dict]
    surfr_rows_utc: list[tuple[dict, Optional[dt.datetime]]]
    fit: Optional[OffsetFit]
    low_load: dict
    window_note: str
    device_jump_note: str


def score_session(sess: Path, verbose: bool = False) -> Optional[SessionScore]:
    trace = sess / "trace.csv"
    if not trace.exists():
        return None
    times, mag = load_trace(trace)
    if not times:
        return None

    sj = load_session_json(sess)
    djumps = load_device_jumps(sess)
    a = align(sess, times)
    surfr = load_surfr(sess)

    n_rows_total = len(times)   # the whole file, before any boot restriction
    diffs = sorted(times[i + 1] - times[i] for i in range(len(times) - 1))
    median_dt = diffs[len(diffs) // 2] if diffs else None
    hz = (1.0 / median_dt) if median_dt else None
    segs = contiguous_segments(times)

    # Scope the comparison to the session window when we have one: Surfr's 32
    # counts jumps inside its own session, and matching that against a count
    # taken over 417 minutes of trace (which includes the drive and the beach)
    # would be comparing two different things.
    t_lo = t_hi = None
    window_note = ("whole trace — no Surfr/Garmin window available, so the "
                   "count below is NOT comparable to a Surfr session count")
    if a.epoch_utc is not None:
        lo_c = [x for x in (a.surfr_start, a.garmin.start_utc if a.garmin.present else None)
                if x is not None]
        hi_c = [x for x in (a.surfr_end, a.garmin.end_utc if a.garmin.present else None)
                if x is not None]
        if lo_c and hi_c:
            t_lo = a.utc_to_t(min(lo_c))
            t_hi = a.utc_to_t(max(hi_c))
            window_note = (f"session window t={t_lo:.0f}..{t_hi:.0f} s "
                           f"({(t_hi - t_lo) / 60.0:.1f} min) — the union of the "
                           f"Surfr and Garmin windows")

    # A multi-boot ring buffer holds rows from a boot with no known epoch.
    # Those rows are REAL DATA — detection needs no wall clock — so they are
    # kept whenever nothing wall-clock-derived is being applied to them. But a
    # t-range slice would silently mix the two, because each boot counts from
    # its own zero and their t values overlap: on the morning trace, t=12 s
    # occurs in both boots, 80 hours apart. So the last-boot restriction is
    # applied EXACTLY when a wall-clock window is.
    tb = a.timebase
    if tb is not None and tb.multi_boot:
        if t_lo is not None:
            times = times[tb.last_boot_start_idx:]
            mag = mag[tb.last_boot_start_idx:]
            window_note += (
                f"; restricted to the last of {len(tb.boot_resets) + 1} boots "
                f"({len(times):,} of {tb.n_rows:,} rows), because a wall-clock "
                f"window is being applied and the earlier boot(s) have no epoch")
        else:
            window_note += (
                f"; spans {len(tb.boot_resets) + 1} boots ({tb.n_rows:,} rows). No "
                f"wall-clock window is applied, so all boots are scored — "
                f"detection needs no epoch — but the counts below are a sum over "
                f"separate power-ups, NOT one session")

    wt, wm = slice_window(times, mag, t_lo, t_hi)

    rows = sweep_table(wt, wm)
    target = (surfr or {}).get("jumps_total")
    chosen_row = best_row_for_count(rows, int(target)) if target else None
    if chosen_row is not None:
        chosen = CandidateParams(band_g=chosen_row.band_g, pop_g=chosen_row.pop_g,
                                 spike_g=chosen_row.spike_g,
                                 min_air_s=chosen_row.min_air_s)
    else:
        chosen = CandidateParams()
    cands = find_candidates(wt, wm, chosen)

    # Attach wall clock and the Garmin approach speed.
    for c in cands:
        c.takeoff_utc = a.t_to_utc(c.takeoff_s)
        if a.garmin.present and c.takeoff_utc is not None:
            c.speed_ms = a.garmin.speed_before(c.takeoff_utc)

    stock = run_stock_detector(wt, wm, Params())
    stock50 = run_stock_detector(wt, wm, Params(freefall_enter_g=0.5, max_airtime_s=6.0))

    low_load = {}
    for w in (1.0, 2.0, 3.0, 3.8):
        got = lowest_mean_load(wt, wm, w)
        low_load[w] = got

    # Surfr rows on the clock.
    rows_utc: list[tuple[dict, Optional[dt.datetime]]] = []
    for r in (surfr or {}).get("rows") or []:
        secs = parse_t_into_session(r.get("t_into_session"))
        when = (a.surfr_start + dt.timedelta(seconds=secs)) \
            if (a.surfr_start is not None and secs is not None) else None
        rows_utc.append((r, when))

    fit = None
    if rows_utc and all(w is not None for _, w in rows_utc) and cands and a.epoch_utc:
        surfr_t = [a.utc_to_t(w) for _, w in rows_utc]
        fit = solve_offset(surfr_t, [c.takeoff_s for c in cands])

    return SessionScore(
        sess=sess, alignment=a, n_trace_rows=n_rows_total, sample_hz=hz,
        n_segments=len(segs), device_jumps=djumps,
        stock_events=stock, stock_events_gate50=stock50, candidates=cands,
        chosen=chosen, sweep=rows, surfr=surfr, surfr_rows_utc=rows_utc,
        fit=fit, low_load=low_load, window_note=window_note,
        device_jump_note=device_jump_coverage(djumps, times, sj))


def _fmt_list(xs: Iterable[float], fmt: str = "{:.2f}") -> str:
    xs = list(xs)
    return ", ".join(fmt.format(x) for x in xs) if xs else "-"


def _surfr_top_airtimes(surfr: Optional[dict]) -> str:
    """THIS session's reported Surfr airtimes, never another session's.

    The literal "3.8 / 3.4 / 3.1 s for the 2026-09-14 evening" used to be
    baked into this line and into section 5, so the 2026-09-14 MORNING card
    — a multi-boot ring buffer with 0 transcribed rows and no Surfr airtime
    at all — printed the evening's three numbers as though they were its
    own. A hard-coded figure in a card whose header promises "every number
    below is measured from the files in this directory" is the exact defect
    CLAUDE.md section 2.6 names.
    """
    if not surfr:
        return "no surfr.json"
    airs = sorted((float(r["airtime_s"]) for r in (surfr.get("rows") or [])
                   if r.get("airtime_s") is not None), reverse=True)
    if airs:
        return (f"top three of {len(airs)} transcribed row(s): "
                f"{_fmt_list(airs[:3], '{:.2f}')} s")
    mx = surfr.get("max_airtime_s")
    if mx is not None:
        return f"only a session maximum was transcribed: {mx} s"
    return "no airtime transcribed for this session"


def render_scorecard(s: SessionScore) -> str:
    a = s.alignment
    L: list[str] = []
    add = L.append

    add(f"# score.md — {s.sess.name}")
    add("")
    add(f"Generated by `./tools/jump score` (`sim/score.py`), the loop in "
        f"`docs/accuracy-plan.md`. Every number below is measured from the "
        f"files in this directory; assumptions are labelled ASSUMED.")
    add("")
    add(f"- trace.csv: {s.n_trace_rows:,} rows, "
        f"{('%.1f Hz median' % s.sample_hz) if s.sample_hz else 'rate unknown'}, "
        f"{s.n_segments} contiguous segment(s) at a {MAX_GAP_S:g} s gap cut")
    add(f"- device jumps.csv: {len(s.device_jumps)} rows"
        + (f", best height {max(j['height'] for j in s.device_jumps):.3f} m"
           if s.device_jumps else ""))
    add(f"- jumps.csv vs this trace: {s.device_jump_note}")
    if s.surfr:
        add(f"- surfr.json: {s.surfr.get('jumps_total', '?')} jumps, "
            f"best {s.surfr.get('best_height_ft', '?')} ft, "
            f"max airtime {s.surfr.get('max_airtime_s', '?')} s, "
            f"{len(s.surfr.get('rows') or [])} row(s) transcribed")
    else:
        add("- surfr.json: absent")
    add("")

    add("## 1. Alignment")
    add("")
    for f in a.findings:
        add(f"- {f}")
    add("")

    add("## 2. Is a Surfr-length flight even in this trace?")
    add("")
    add("The lowest MEAN load over any contiguous window of the given length, "
        "anywhere in the scored window. A threshold can only find a low-load "
        "interval that exists; this says whether one does.")
    add("")
    add("| window | lowest mean load | at trace t |")
    add("|---|---|---|")
    for w in sorted(s.low_load):
        got = s.low_load[w]
        if got is None:
            add(f"| {w:.1f} s | no contiguous window that long | - |")
        else:
            add(f"| {w:.1f} s | {got[0]:.3f} g | {got[1]:.1f} s |")
    add("")

    add("## 3. Stock detector vs load-band candidates")
    add("")
    add(f"Scope: {s.window_note}.")
    add("")
    add("| generator | count | longest airtime | top 3 airtimes |")
    add("|---|---|---|---|")
    st = sorted((e.airtime_s for e in s.stock_events), reverse=True)
    st5 = sorted((e.airtime_s for e in s.stock_events_gate50), reverse=True)
    add(f"| stock Detector (config defaults, gate 0.35 g, cap 3.0 s) | "
        f"{len(s.stock_events)} | {(f'{st[0]:.2f} s' if st else '-')} | "
        f"{_fmt_list(st[:3])} |")
    add(f"| stock Detector (gate 0.50 g, cap 6.0 s) | {len(s.stock_events_gate50)} | "
        f"{(f'{st5[0]:.2f} s' if st5 else '-')} | {_fmt_list(st5[:3])} |")
    ca = sorted((c.airtime_s for c in s.candidates), reverse=True)
    cb = sorted((c.band_s for c in s.candidates), reverse=True)
    add(f"| load band (band {s.chosen.band_g:g} g, pop {s.chosen.pop_g:g} g, "
        f"spike {s.chosen.spike_g:g} g, min air {s.chosen.min_air_s:g} s) | "
        f"{len(s.candidates)} | {(f'{ca[0]:.2f} s' if ca else '-')} | "
        f"{_fmt_list(ca[:3])} |")
    add(f"| ...of which time actually inside the band (`band_s`) | "
        f"{len(s.candidates)} | {(f'{cb[0]:.2f} s' if cb else '-')} | "
        f"{_fmt_list(cb[:3])} |")
    if s.surfr:
        add(f"| **Surfr (reference)** | **{s.surfr.get('jumps_total', '?')}** | "
            f"**{s.surfr.get('max_airtime_s', '?')} s** | "
            f"{_surfr_top_airtimes(s.surfr)} |")
    add("")

    add("### Sweep")
    add("")
    add("Counts and the longest airtimes over the grid. `band_s` columns say how "
        "much of each airtime was genuinely unloaded — the gap between the two "
        "is the finding.")
    add("")
    add("| band_g | pop_g | spike_g | min_air_s | N | top airtimes (s) | longest band_s |")
    add("|---|---|---|---|---|---|---|")
    for r in s.sweep:
        add(f"| {r.band_g:.1f} | {r.pop_g:.1f} | {r.spike_g:.1f} | {r.min_air_s:.2f} | "
            f"{r.count} | {_fmt_list(r.top_airtimes)} | {r.longest_band_s:.2f} |")
    add("")
    if s.surfr and s.surfr.get("jumps_total"):
        add(f"Chosen row: the grid point whose count is nearest Surfr's "
            f"{s.surfr['jumps_total']} — band {s.chosen.band_g:g} g, pop "
            f"{s.chosen.pop_g:g} g, spike {s.chosen.spike_g:g} g, min air "
            f"{s.chosen.min_air_s:g} s, giving {len(s.candidates)}. "
            f"Matching a count is NOT evidence the same jumps were found.")
        add("")

    add("## 4. Matched pairs")
    add("")
    if not s.surfr_rows_utc:
        add("No Surfr rows were transcribed for this session, so nothing could be "
            "matched. The offset solver DID NOT RUN.")
    elif s.fit is None:
        add("Surfr rows exist but could not be placed on the clock (missing "
            "`session_start_local`, `trace_epoch_utc`, or candidates). The offset "
            "solver DID NOT RUN.")
    else:
        f = s.fit
        mar = f.mean_abs_residual_s
        add(f"Fitted offset: **{f.offset_s:+.2f} s** (searched +-{f.searched_s:.0f} s, "
            f"{f.n_rows} row(s), mean |residual| "
            f"{('%.2f s' % mar) if mar is not None else 'n/a'}).")
        add("")
        add("| Surfr # | Surfr t_into | Surfr ht | Surfr air | matched takeoff (t) | "
            "residual | our airtime | our band_s | mean load | ballistic | lift-corr | speed |")
        add("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for n, ((row, when), res, ci) in enumerate(
                zip(s.surfr_rows_utc, f.residuals_s, f.matched_idx), 1):
            if ci is None:
                add(f"| {row.get('n', n)} | {row.get('t_into_session', '?')} | "
                    f"{row.get('height_ft', '?')} ft | {row.get('airtime_s', '?')} s | "
                    "NO CANDIDATE within the pairing window | - | - | - | - | - | - | - |")
                continue
            c = s.candidates[ci]
            bh = c.h_ballistic_m
            lh = c.h_lift_m
            add(f"| {row.get('n', n)} | {row.get('t_into_session', '?')} | "
                f"{row.get('height_ft', '?')} ft | {row.get('airtime_s', '?')} s | "
                f"{c.takeoff_s:.2f} s | {res:+.2f} s | {c.airtime_s:.2f} s | "
                f"{c.band_s:.2f} s | {c.mean_load_g:.3f} g | "
                f"{bh:.2f} m ({bh * FT_PER_M:.2f} ft) | "
                f"{lh:.2f} m ({lh * FT_PER_M:.2f} ft) | "
                f"{('%.2f m/s' % c.speed_ms) if c.speed_ms is not None else 'absent'} |")
    add("")

    add("## 5. What this cannot conclude")
    add("")
    n_rows = len(s.surfr_rows_utc)
    add(f"- **{n_rows} transcribed Surfr row(s).** "
        f"`docs/accuracy-plan.md` allows at most `sessions / 3` free parameters. "
        f"With one session and {n_rows} row(s) that budget is zero: nothing below "
        f"is a fitted calibration, and no height_scale or airtime_offset may be "
        f"derived from this file.")
    # The row count here used to be the literal "2" — so the morning card,
    # with ZERO transcribed rows and an offset solver that never ran, still
    # asserted "a fitted offset over 2 rows". Say what this session has.
    if s.fit is not None:
        add(f"- **A fitted offset over {s.fit.n_rows} row(s) is not a fitted "
            f"offset.** {s.fit.n_rows} point(s) can be slid onto "
            f"{len(s.candidates)} candidate(s) at many offsets; the residuals "
            "say the alignment is self-consistent, not that the pairing is "
            "right. The offset becomes a measurement at roughly 5+ rows, or "
            "immediately with one rider lap press per jump "
            "(`tools/fitread.py` already reads `lap_trigger=manual`).")
    else:
        add("- **No offset was fitted.** The solver did not run for this "
            "session, so nothing below rests on a Surfr-to-candidate pairing. "
            "The offset becomes a measurement at roughly 5+ transcribed rows, "
            "or immediately with one rider lap press per jump "
            "(`tools/fitread.py` already reads `lap_trigger=manual`).")
    add("- **A matching count is not matching jumps.** The sweep row chosen above "
        "was chosen BECAUSE its count is near Surfr's. Without per-jump truth "
        "that is circular; it fixes the operating point, it does not validate it.")
    add("- **Surfr's own model is unvalidated.** The plan lists its bias as "
        "unknown. Agreeing with Surfr is the product goal until video says "
        "otherwise; it is not the same as being right.")
    if s.low_load:
        got3 = s.low_load.get(3.0)
        if got3 is not None:
            # NAME THE SCOPE. This figure is computed over the SCORED window,
            # not the whole file, and the two differ: measured on
            # data/sessions/20260914-210637-E2C4, 0.947 g over the 103-minute
            # Surfr/Garmin union and 0.944 g over all 417 minutes. Saying
            # "in the scored data" without saying what that was is how the
            # 103-minute number gets quoted as a 417-minute one.
            scope = s.window_note.split(" — ")[0].split(";")[0].strip()
            line = (f"- **The airtime disagreement is not a threshold problem.** "
                    f"The lowest mean load over ANY contiguous 3.0 s window in "
                    f"the scored data ({scope}) is {got3[0]:.3f} g. No value of "
                    f"`band_g` produces a 3 s low-load flight")
            if s.surfr and (s.surfr.get("rows") or s.surfr.get("max_airtime_s")):
                line += (f", so this session's reported Surfr airtimes "
                         f"({_surfr_top_airtimes(s.surfr)}) cannot be reproduced "
                         f"by retuning this generator. Either Surfr's airtime "
                         f"measures something other than an unloaded interval, or "
                         f"the puck did not record the flight the way the model "
                         f"assumes. That is the next measurement, and video is "
                         f"what settles it.")
            else:
                line += (". No Surfr airtime was transcribed for this session, "
                         "so there is nothing here to reproduce or contradict — "
                         "the figure stands on its own.")
            add(line)
    if not s.alignment.garmin.present:
        add(f"- **No Garmin file** ({s.alignment.garmin.reason}): no approach "
            f"speeds, and the epoch check did not run.")
    add("")
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------------- cli


def discover_sessions(root: Path) -> list[Path]:
    """Every directory under `root` holding a trace.csv, sorted by name."""
    if not root.exists():
        return []
    return sorted({p.parent for p in root.rglob("trace.csv")})


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="jump score",
        description="Align a session to Garmin/Surfr, generate load-band jump "
                    "candidates, score them, and write <session>/score.md.")
    ap.add_argument("sessions", nargs="*", type=Path,
                    help="session directories (default: every data/sessions/* "
                         "with a trace.csv)")
    ap.add_argument("--root", type=Path, default=REPO / "data" / "sessions",
                    help="sessions root used when none are named")
    ap.add_argument("--no-write", action="store_true",
                    help="print the scorecard but do not write score.md")
    args = ap.parse_args(argv)

    sessions = list(args.sessions) or discover_sessions(args.root)
    if not sessions:
        print(f"no sessions with a trace.csv under {args.root}")
        return 1

    rc = 0
    for sess in sessions:
        sess = Path(sess)
        if not (sess / "trace.csv").exists():
            print(f"!! {sess}: no trace.csv — skipped (this is a finding, not a pass)")
            rc = 1
            continue
        s = score_session(sess)
        if s is None:
            print(f"!! {sess}: trace.csv held no usable rows — skipped")
            rc = 1
            continue
        card = render_scorecard(s)
        print(card)
        if not args.no_write:
            out = sess / "score.md"
            out.write_text(card)
            print(f"wrote {out}")
        print("")
    return rc


if __name__ == "__main__":
    sys.exit(main())
