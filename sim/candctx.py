#!/usr/bin/env python3
"""candctx.py — context columns for CG-1 candidates: wall clock, GPS speed,
heading, turn, wind, device-event overlap (spec section 3.4).

Context is DESCRIPTION, never a gate: nothing here removes a candidate. A
value that could not be computed is `None` with a reason string beside it,
never 0 — a reading that did not happen is a finding (CLAUDE.md rule 3).

The FIT file is parsed in one place only: `sim/score.py`'s `load_garmin`
(which uses `tools/fitread.py`). This module reads the resulting GarminData.

Why heading uses the last record PAIR, not a 15 s chord (MEASURED, spec
section 3.4): on 2026-09-23 a 15 s chord before the pop put 9 of 12 headed
candidates at rel_wind -147..-178 ("downwind"); the last record pair puts
them at a median of +68 deg. The chord was averaging across a turn — in 10
of 12 the heading changed by more than 60 deg in the ~10 s before the pop.
So heading-at-pop and turn-before-pop are two separate features.
"""

from __future__ import annotations

import bisect
import datetime as dt
import json
import math
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "sim") not in sys.path:
    sys.path.insert(0, str(REPO / "sim"))

import score  # noqa: E402

UTC = dt.timezone.utc
KN_PER_MS = 1.943844

# Windows relative to the candidate (spec section 3.4). Fixed, not tuned.
SPEED_BEFORE = (-12.0, -2.0)      # relative to pop_t
SPEED_AFTER = (3.0, 13.0)         # relative to land_t
MIN_AFTER = (2.0, 20.0)           # relative to land_t
STOPPED_KN = 1.5
COURSE_MIN_M = 3.0
COURSE_MAX_DT_S = 5.0
COURSE_LOOKBACK = 6
CHORD = (-15.0, -8.0)             # relative to pop_t
CHORD_MIN_M = 10.0
DEVICE_PAD = (-3.0, 1.0)          # [pop_t - 3, land_t + 1]
# ASSUMED: a Smart-Recording record stands for the time until the next one,
# capped here so an auto-pause gap (762 s on 2026-09-23) is not counted as
# riding at the speed before it.
RECORD_HOLD_CAP_S = 10.0
RIDING_KN = 7.0
SPEED_BINS_KN = ((0.0, 4.0), (4.0, 7.0), (7.0, 10.0), (10.0, 13.0), (13.0, None))
# Lag of a watch jump_height change after the nearest device landing.
# ASSUMED band: measured spread 1.4-4.3 s (docs/garmin-corpus-2026-09-15.md
# section 1 item 1), plus margin.
ALIGN_LAG_BAND_S = (0.0, 6.0)
# ASSUMED: NDBC rows are minutes apart and Open-Meteo rows hourly; a wind
# row further than this from the candidate is not its wind.
WIND_MAX_AGE_S = 3600.0
# The Surfr and Garmin windows are two recordings of one ride. MEASURED
# disagreement on the two sessions that have both: Surfr starts -187 s /
# ends +21 s relative to Garmin (2026-09-14 evening) and -43 s / +12 s
# (2026-09-23), score.md section 1. A wrong UTC offset moves BOTH ends by a
# whole hour (3600 s for a DST change between the ride and the sync). This
# threshold sits between the two (ASSUMED): both ends disagreeing by more
# than it is a FINDING, never a "not distinguishable from chance".
SURFR_GARMIN_MAX_DISAGREE_S = 900.0


def wrap180(x: float) -> float:
    return ((x + 180.0) % 360.0) - 180.0


def bearing(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@dataclass
class Context:
    """Everything wall-clock about one session, in TRACE time (last boot)."""

    epoch_utc: Optional[dt.datetime]
    tz_offset_min: int
    tz_src: str
    garmin_reason: str                      # "" when present
    fit_t: list[float] = field(default_factory=list)
    fit_kn: list[Optional[float]] = field(default_factory=list)
    pos_t: list[float] = field(default_factory=list)
    pos_ll: list[tuple[float, float]] = field(default_factory=list)
    jh_t: list[float] = field(default_factory=list)       # watch jump_height
    jh_v: list[float] = field(default_factory=list)
    garmin_window: Optional[tuple[float, float]] = None
    surfr_window: Optional[tuple[float, float]] = None
    surfr_window_reason: str = ""
    wind_t: list[float] = field(default_factory=list)
    wind_from: list[float] = field(default_factory=list)
    wind_src: str = ""
    wind_reason: str = ""
    device: list[tuple[str, float, Optional[float]]] = field(default_factory=list)
    # The UTC offset is a measurement with a source, or an ASSUMPTION, and is
    # printed as one (candidates.md sections 0 and 5). Lines here that say
    # FINDING are copied into the report's findings.
    tz_lines: list[str] = field(default_factory=list)

    def t_to_utc(self, t: float) -> Optional[dt.datetime]:
        return None if self.epoch_utc is None else self.epoch_utc + dt.timedelta(seconds=t)

    def t_to_local(self, t: float) -> Optional[dt.datetime]:
        u = self.t_to_utc(t)
        return None if u is None else u + dt.timedelta(minutes=self.tz_offset_min)

    def utc_to_t(self, when: dt.datetime) -> Optional[float]:
        return None if self.epoch_utc is None else (when - self.epoch_utc).total_seconds()


def _load_wind(sess: Path, ctx: Context) -> None:
    p = sess / "wind.json"
    if not p.exists():
        ctx.wind_reason = "no wind.json"
        return
    try:
        w = json.loads(p.read_text())
    except (OSError, ValueError) as exc:
        ctx.wind_reason = f"wind.json unreadable ({exc.__class__.__name__})"
        return
    src = (w.get("sources") or {})
    rows = []
    label = ""
    nd = src.get("ndbc") or {}
    for h in nd.get("hours") or []:
        if h.get("wdir") is None:
            continue
        when = score.parse_iso_utc(h.get("time_utc"))
        if when is not None:
            rows.append((when, float(h["wdir"])))
    if rows:
        st = nd.get("station") or {}
        km = st.get("distance_km")
        label = (f"NDBC {st.get('id', '?')} wdir"
                 + (f" ({km:.1f} km away)" if isinstance(km, (int, float)) else ""))
    else:
        om = src.get("open_meteo_weather") or {}
        for h in om.get("hours") or []:
            if h.get("wind_direction_10m") is None:
                continue
            when = score.parse_iso_utc(h.get("time_utc"))
            if when is not None:
                rows.append((when, float(h["wind_direction_10m"])))
        if rows:
            km = om.get("grid_distance_km")
            label = ("Open-Meteo wind_direction_10m (model)"
                     + (f" ({km:.1f} km grid)" if isinstance(km, (int, float)) else ""))
    if not rows:
        ctx.wind_reason = "wind.json has no wind direction"
        return
    if ctx.epoch_utc is None:
        ctx.wind_reason = "no trace epoch"
        return
    rows.sort()
    ctx.wind_t = [ctx.utc_to_t(r[0]) for r in rows]
    ctx.wind_from = [r[1] for r in rows]
    ctx.wind_src = label


def build_context(sess: Path, garmin: Optional[score.GarminData] = None) -> Context:
    sj = score.load_session_json(sess)
    epoch = score.parse_iso_utc(sj.get("trace_epoch_utc"))
    surfr = score.load_surfr(sess)
    off, off_src = score.surfr_tz_offset_min(sj, surfr or {})
    g = garmin if garmin is not None else score.load_garmin(sess)
    ctx = Context(epoch_utc=epoch, tz_offset_min=off, tz_src=off_src,
                  garmin_reason="" if g.present else g.reason)
    if epoch is None:
        ctx.garmin_reason = ctx.garmin_reason or "no trace epoch"
        ctx.surfr_window_reason = "no trace epoch"
    if epoch is not None and g.present:
        e = epoch.timestamp()
        for ts, sp, lat, lon in g.samples:
            t = ts - e
            ctx.fit_t.append(t)
            ctx.fit_kn.append(sp * KN_PER_MS if sp is not None else None)
            if lat is not None and lon is not None:
                ctx.pos_t.append(t)
                ctx.pos_ll.append((lat, lon))
        for ts, v in g.jump_heights:
            ctx.jh_t.append(ts - e)
            ctx.jh_v.append(v)
        ctx.garmin_window = (g.start_utc.timestamp() - e, g.end_utc.timestamp() - e)
    s0 = None
    if epoch is not None:
        s0, s1, _, _ = score.surfr_window(sj, surfr)
        if s0 is not None and s1 is not None:
            ctx.surfr_window = (ctx.utc_to_t(s0), ctx.utc_to_t(s1))
        else:
            probs = score.surfr_header_problems(surfr) if surfr is not None else []
            ctx.surfr_window_reason = (
                "no surfr.json" if surfr is None else
                "surfr.json malformed: " + "; ".join(probs) if probs else
                "surfr.json has no usable start/duration")
    _tz_checks(ctx, sj, surfr, s0)
    _load_wind(sess, ctx)
    rows, _ = score.load_device_jump_rows(sess)
    for r in rows:
        n = r.get("n")
        n_s = str(int(n)) if isinstance(n, float) else str(n)
        air = r.get("airtime_raw_s") if isinstance(r.get("airtime_raw_s"), float) else None
        ctx.device.append((n_s, r["takeoff_s"], air))
    return ctx


def us_dst_changes_utc(year: int) -> list[dt.datetime]:
    """The two US daylight-saving changes of `year`, in UTC, to the hour for
    US Eastern (2:00 local: 07:00 UTC in March, 06:00 UTC in November).
    Second Sunday of March, first Sunday of November (15 USC 260a). Other US
    zones change 1-6 h later the same day, which no check here is near."""
    def nth_sunday(month: int, n: int) -> dt.date:
        d = dt.date(year, month, 1)
        d += dt.timedelta(days=(6 - d.weekday()) % 7)
        return d + dt.timedelta(weeks=n - 1)
    mar, nov = nth_sunday(3, 2), nth_sunday(11, 1)
    return [dt.datetime(mar.year, mar.month, mar.day, 7, tzinfo=UTC),
            dt.datetime(nov.year, nov.month, nov.day, 6, tzinfo=UTC)]


def _tz_checks(ctx: Context, sj: dict, surfr, surfr_start_utc) -> None:
    """Say which UTC offset places local times and Surfr rows, where it came
    from, and raise a FINDING where it can be wrong without anything else
    noticing:

      * an ASSUMED offset when a Surfr start has to be placed with it;
      * a manifest offset (the one in force at SYNC) with a US DST change
        between the ride and the sync: off by exactly 60 min;
      * Surfr and Garmin windows that disagree at BOTH ends by more than
        SURFR_GARMIN_MAX_DISAGREE_S.

    A 3600 s placement error is far outside the matcher's [-35, +65] s
    prior, so without these the report would read "not distinguishable from
    chance" — a wrong clock passing as an honest null."""
    add = ctx.tz_lines.append
    add(f"UTC offset for local times and Surfr placement: {ctx.tz_offset_min:+d} min "
        f"({ctx.tz_src})")
    has_start = surfr_start_utc is not None
    if ctx.tz_src.startswith("ASSUMED") and has_start:
        add(f"FINDING: the UTC offset that places the Surfr rows is ASSUMED "
            f"({ctx.tz_offset_min:+d} min); add `tz_offset_min` to surfr.json. "
            f"A wrong offset is a whole-hour error that the matcher reads as chance.")
    synced = score.parse_iso_utc((sj or {}).get("synced_at_utc"))
    ride = surfr_start_utc
    if ride is None and ctx.epoch_utc is not None and ctx.garmin_window is not None:
        ride = ctx.t_to_utc(ctx.garmin_window[0])
    if "manifest" in ctx.tz_src and synced is not None and ride is not None:
        lo, hi = sorted((ride, synced))
        for ch in [c for y in range(lo.year, hi.year + 1) for c in us_dst_changes_utc(y)]:
            if lo < ch <= hi:
                add(f"FINDING: a US daylight-saving change ({ch:%Y-%m-%d}) lies between "
                    f"the ride ({ride:%Y-%m-%d %H:%MZ}) and the sync "
                    f"({synced:%Y-%m-%d %H:%MZ}); the manifest offset is the one in force "
                    f"at SYNC, so local times and the Surfr placement may be 60 min "
                    f"off. Add the ride's `tz_offset_min` to surfr.json.")
                break
    if ctx.surfr_window is not None and ctx.garmin_window is not None:
        ds = ctx.surfr_window[0] - ctx.garmin_window[0]
        de = ctx.surfr_window[1] - ctx.garmin_window[1]
        add(f"Surfr vs Garmin window: Surfr starts {ds:+.0f} s and ends {de:+.0f} s "
            f"relative to the Garmin activity")
        if min(abs(ds), abs(de)) > SURFR_GARMIN_MAX_DISAGREE_S:
            add(f"FINDING: the Surfr window disagrees with the Garmin window at "
                f"BOTH ends by more than {SURFR_GARMIN_MAX_DISAGREE_S:g} s (start "
                f"{ds:+.0f} s, end {de:+.0f} s). The UTC offset ({ctx.tz_offset_min:+d} "
                f"min, {ctx.tz_src}) or the transcribed start is probably wrong; "
                f"every Surfr placement below inherits it.")


def session_window(ctx: Context) -> Optional[tuple[float, float]]:
    """The union of the Surfr and Garmin windows, as sim/score.py scopes its
    own counts (score_session). None when neither exists."""
    ws = [w for w in (ctx.surfr_window, ctx.garmin_window) if w is not None]
    if not ws:
        return None
    return min(w[0] for w in ws), max(w[1] for w in ws)


# ------------------------------------------------------------- per candidate

def _in(ts: list[float], lo: float, hi: float) -> range:
    return range(bisect.bisect_left(ts, lo), bisect.bisect_right(ts, hi))


def _speeds(ctx: Context, lo: float, hi: float) -> list[float]:
    return [ctx.fit_kn[i] for i in _in(ctx.fit_t, lo, hi) if ctx.fit_kn[i] is not None]


def course_at(ctx: Context, t: float) -> tuple[Optional[float], Optional[float]]:
    """(bearing, age_s) of the last pair of consecutive positioned records,
    >= 3 m apart and <= 5 s apart, at or before t; looking back at most 6
    records. (None, None) when no pair qualifies."""
    k = bisect.bisect_right(ctx.pos_t, t) - 1
    for j in range(k, max(0, k - COURSE_LOOKBACK), -1):
        if j < 1:
            break
        t0, t1 = ctx.pos_t[j - 1], ctx.pos_t[j]
        (a0, o0), (a1, o1) = ctx.pos_ll[j - 1], ctx.pos_ll[j]
        if t1 - t0 <= COURSE_MAX_DT_S and haversine_m(a0, o0, a1, o1) >= COURSE_MIN_M:
            return bearing(a0, o0, a1, o1), t - t1
    return None, None


def chord_course(ctx: Context, lo: float, hi: float) -> Optional[float]:
    r = _in(ctx.pos_t, lo, hi)
    if len(r) < 2:
        return None
    (a0, o0), (a1, o1) = ctx.pos_ll[r[0]], ctx.pos_ll[r[-1]]
    if haversine_m(a0, o0, a1, o1) < CHORD_MIN_M:
        return None
    return bearing(a0, o0, a1, o1)


def wind_at(ctx: Context, t: float) -> Optional[float]:
    """Nearest-in-time wind direction, or None when the nearest row is more
    than WIND_MAX_AGE_S away (wind.json covers the activity window; a
    candidate hours outside it gets no wind rather than a stale one)."""
    if not ctx.wind_t:
        return None
    i = bisect.bisect_left(ctx.wind_t, t)
    best = min((j for j in (i - 1, i) if 0 <= j < len(ctx.wind_t)),
               key=lambda j: abs(ctx.wind_t[j] - t))
    if abs(ctx.wind_t[best] - t) > WIND_MAX_AGE_S:
        return None
    return ctx.wind_from[best]


def enrich(c, ctx: Context) -> tuple[dict, dict]:
    """(values, reasons) for one candidate. `values[k] is None` always has
    `reasons[k]` saying why."""
    v: dict = {}
    why: dict = {}
    keys = ("pop_utc", "pop_local", "in_garmin_window", "in_surfr_window",
            "kn_before", "n_fit_before", "kn_after", "n_fit_after",
            "kn_min_after20", "stopped_after", "course_at_pop_deg", "course_age_s",
            "turn_before_pop_deg", "wind_from_deg", "wind_src", "rel_wind_deg",
            "device_event_n")
    for k in keys:
        v[k] = None
    # device overlap needs no clock, only the same boot
    if c.boot == "last":
        lo, hi = c.pop_t_s + DEVICE_PAD[0], c.land_t_s + DEVICE_PAD[1]
        hits = [n for n, tk, _ in ctx.device if lo <= tk <= hi]
        v["device_event_n"] = ",".join(hits) if hits else ""
    else:
        why["device_event_n"] = "boot=earlier"
    if c.boot != "last":
        for k in keys:
            if v[k] is None:
                why[k] = "boot=earlier (no epoch)"
        return v, why
    if ctx.epoch_utc is None:
        for k in keys:
            if v[k] is None:
                why[k] = "no trace_epoch_utc"
        return v, why
    u = ctx.t_to_utc(c.pop_t_s)
    v["pop_utc"] = u.isoformat().replace("+00:00", "Z")
    v["pop_local"] = ctx.t_to_local(c.pop_t_s).strftime("%Y-%m-%d %H:%M:%S")
    if ctx.garmin_window is not None:
        g0, g1 = ctx.garmin_window
        v["in_garmin_window"] = g0 <= c.pop_t_s <= g1
    else:
        why["in_garmin_window"] = ctx.garmin_reason or "no garmin window"
    if ctx.surfr_window is not None:
        s0, s1 = ctx.surfr_window
        v["in_surfr_window"] = s0 <= c.pop_t_s <= s1
    else:
        why["in_surfr_window"] = ctx.surfr_window_reason or "no surfr window"

    if not ctx.fit_t:
        for k in ("kn_before", "n_fit_before", "kn_after", "n_fit_after",
                  "kn_min_after20", "stopped_after", "course_at_pop_deg",
                  "course_age_s", "turn_before_pop_deg", "rel_wind_deg"):
            why[k] = ctx.garmin_reason or "no FIT records"
    else:
        sb = _speeds(ctx, c.pop_t_s + SPEED_BEFORE[0], c.pop_t_s + SPEED_BEFORE[1])
        v["n_fit_before"] = len(sb)
        if sb:
            v["kn_before"] = statistics.median(sb)
        else:
            why["kn_before"] = "no FIT speed record in [pop-12, pop-2] s"
        sa = _speeds(ctx, c.land_t_s + SPEED_AFTER[0], c.land_t_s + SPEED_AFTER[1])
        v["n_fit_after"] = len(sa)
        if sa:
            v["kn_after"] = statistics.median(sa)
        else:
            why["kn_after"] = "no FIT speed record in [land+3, land+13] s"
        sm = _speeds(ctx, c.land_t_s + MIN_AFTER[0], c.land_t_s + MIN_AFTER[1])
        if sm:
            v["kn_min_after20"] = min(sm)
            v["stopped_after"] = v["kn_min_after20"] <= STOPPED_KN
        else:
            why["kn_min_after20"] = why["stopped_after"] = \
                "no FIT speed record in [land+2, land+20] s"
        crs, age = course_at(ctx, c.pop_t_s)
        if crs is None:
            why["course_at_pop_deg"] = why["course_age_s"] = \
                "no qualifying record pair (>= 3 m, <= 5 s) in the last 6 fixes"
            why["turn_before_pop_deg"] = "no course at pop"
        else:
            v["course_at_pop_deg"], v["course_age_s"] = crs, age
            ch = chord_course(ctx, c.pop_t_s + CHORD[0], c.pop_t_s + CHORD[1])
            if ch is None:
                why["turn_before_pop_deg"] = "no chord >= 10 m in [pop-15, pop-8] s"
            else:
                v["turn_before_pop_deg"] = wrap180(crs - ch)
    wf = wind_at(ctx, c.pop_t_s)
    if wf is None:
        reason = ctx.wind_reason or f"no wind row within {WIND_MAX_AGE_S / 60:.0f} min"
        why["wind_from_deg"] = why["wind_src"] = reason
        why.setdefault("rel_wind_deg", reason)
    else:
        v["wind_from_deg"], v["wind_src"] = wf, ctx.wind_src
        if v["course_at_pop_deg"] is not None:
            v["rel_wind_deg"] = wrap180(v["course_at_pop_deg"] - wf)
        else:
            why.setdefault("rel_wind_deg", "no course at pop")
    return v, why


# ---------------------------------------------------------- session-level

def riding_minutes_by_bin(ctx: Context, lo: float, hi: float) -> Optional[list[float]]:
    """Minutes spent in each SPEED_BINS_KN bin over [lo, hi]. Each FIT record
    holds its speed until the next record, capped at RECORD_HOLD_CAP_S."""
    if not ctx.fit_t:
        return None
    out = [0.0] * len(SPEED_BINS_KN)
    for i in range(len(ctx.fit_t)):
        t = ctx.fit_t[i]
        if not (lo <= t <= hi) or ctx.fit_kn[i] is None:
            continue
        nxt = ctx.fit_t[i + 1] if i + 1 < len(ctx.fit_t) else t + 1.0
        hold = min(max(0.0, nxt - t), RECORD_HOLD_CAP_S)
        b = speed_bin(ctx.fit_kn[i])
        out[b] += hold / 60.0
    return out


def speed_bin(kn: float) -> int:
    for k, (a, b) in enumerate(SPEED_BINS_KN):
        if kn >= a and (b is None or kn < b):
            return k
    return 0


def bin_label(k: int) -> str:
    a, b = SPEED_BINS_KN[k]
    return f"{a:g}-{b:g} kn" if b is not None else f">= {a:g} kn"


def tack_minutes(ctx: Context, lo: float, hi: float) -> Optional[tuple[float, float, float]]:
    """(minutes with wind from the rider's LEFT, from the RIGHT, unheaded) at
    >= RIDING_KN over [lo, hi]. A record's heading is its pair with the
    previous positioned record (same >= 3 m / <= 5 s rule as course_at)."""
    if not ctx.fit_t or not ctx.wind_t:
        return None
    left = right = none = 0.0
    for i in range(len(ctx.fit_t)):
        t = ctx.fit_t[i]
        kn = ctx.fit_kn[i]
        if not (lo <= t <= hi) or kn is None or kn < RIDING_KN:
            continue
        nxt = ctx.fit_t[i + 1] if i + 1 < len(ctx.fit_t) else t + 1.0
        hold = min(max(0.0, nxt - t), RECORD_HOLD_CAP_S) / 60.0
        j = bisect.bisect_right(ctx.pos_t, t) - 1
        crs = None
        if j >= 1 and abs(ctx.pos_t[j] - t) < 1e-6:
            (a0, o0), (a1, o1) = ctx.pos_ll[j - 1], ctx.pos_ll[j]
            if ctx.pos_t[j] - ctx.pos_t[j - 1] <= COURSE_MAX_DT_S and \
                    haversine_m(a0, o0, a1, o1) >= COURSE_MIN_M:
                crs = bearing(a0, o0, a1, o1)
        wf = wind_at(ctx, t)
        if crs is None or wf is None:
            none += hold
        elif wrap180(crs - wf) > 0:
            left += hold
        else:
            right += hold
    return left, right, none


def alignment_lags(ctx: Context) -> Optional[list[tuple[float, float, Optional[float], str]]]:
    """Each watch jump_height value change: (t_change, new value, lag after
    the nearest PRECEDING device landing, that landing's n). The first
    non-null value counts as a change (the field appears with the first
    jump). A change with no landing before it is paired with the first
    landing after it, so its lag is NEGATIVE — an ALIGNMENT FINDING, never
    clipped away. None when the FIT carries no jump_height at all.

    MEASURED on data/sessions/20260914-210637-E2C4: 6 changes, lags
    +1.47..+3.43 s (agrees with docs/garmin-corpus-2026-09-15.md section 1
    item 1, +1.42..+3.38 s)."""
    if not ctx.jh_t:
        return None
    landings = sorted((tk + air, n) for n, tk, air in ctx.device if air is not None)
    lt = [x[0] for x in landings]
    out = []
    prev = None
    for t, val in zip(ctx.jh_t, ctx.jh_v):
        # A change TO zero is a reset or a no-jump placeholder, not a jump.
        if val != prev and val != 0:
            if landings:
                k = bisect.bisect_right(lt, t) - 1
                ln, n = landings[k] if k >= 0 else landings[0]
                out.append((t, val, t - ln, n))
            else:
                out.append((t, val, None, ""))
        prev = val
    return out
