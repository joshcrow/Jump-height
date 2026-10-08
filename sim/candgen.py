#!/usr/bin/env python3
"""candgen.py — candidate generator CG-1: pop -> partial unload -> landing.

WHY THIS EXISTS
---------------
On 2026-09-23 (vest mount, 19-24 kn) Surfr counted 14 jumps and the stock
free-fall detector found 0: on the torso the longest dip below the 0.35 g
gate lasted 0.12 s (docs/STATUS.md "Field-measured 2026-09-23"). What the
torso does show is a shape: a loaded pop, a partial unload that never
reaches free fall, then a landing impact. This module finds that shape in a
magnitude trace, offline, and emits one row per occurrence. It does not use
the free-fall gate or anything derived from it, and it never touches
`sim/detector.py` (the 1:1 firmware mirror) or any deployed parameter.

THE RULES (verbatim intent of the spec, section 3.7)
----------------------------------------------------
This generator is a pure function of (MotionWindow, CGParams). It cannot see
Surfr, the FIT, or the device's jumps.csv, by construction.

**Parameters must never be chosen by comparing a candidate COUNT with Surfr's
`jumps_total`.** A setting picked because its count matches is fitted to the
very number it would then be "validated" against. It fixes an operating
point; it does not validate one. Parameters may be selected only from
per-jump timestamp matches (`sim/surfr_match.py`) on sessions **held out**
from the evaluation, and never on a session with fewer than 5 matched rows.

`tools/tests/test_candgen.py` enforces the first paragraph structurally: an
`ast` walk of this file refuses any import of the Surfr matcher, of
`load_surfr`, or of the FIT reader.

THE ALGORITHM (spec section 3.2 — exact, so two implementations agree)
---------------------------------------------------------------------
Per contiguous segment (sim/score.py's `contiguous_segments`, 0.5 s gap cut,
1 s backward step = reboot); segments of 10 samples or fewer are skipped.

1. Smooth: sm[i] = mean of mag[j] over the contiguous index run around i
   whose times satisfy |t[j] - t[i]| <= smooth_s/2 (+1 us, so a 0.050 s
   step written to the millisecond is not lost to float rounding). The
   window is truncated at segment edges and NEVER zero-padded: a padded
   average invents low load at every segment edge.
2. Unload runs: maximal runs of consecutive samples with sm < unload_g.
3. Merge A then B when t[B.start] - t[A.end] <= merge_s AND the raw max over
   indices A.end..B.start is below land_g (an impact keeps them apart).
4. Keep min_unload_s <= t[end] - t[start] <= max_unload_s.
5. Pop: raw max over t in [u0 - pop_win_s, u0 + 0.05]; require >= pop_g.
6. Landing: the first raw sample (index order) with mag >= land_g and t in
   [u1 - 0.05, u1 + land_win_s]; land_peak_g = raw max over
   [land_t, land_t + 0.30]. No such sample, no candidate.
7. No double counting: a run whose u0 <= the previous accepted land_t is
   dropped — one landing belongs to one candidate (the same defect
   sim/score.py:726-745 fixed in its own generator).
8. The generator emits rows only. Speed, Surfr, FIT and wind are context
   columns added elsewhere (sim/candctx.py) and are never gates.

Times are in SECONDS everywhere, so a 50 Hz and a 100 Hz trace run
unchanged — but counts are NOT portable across log rates (measured on the
2026-09-14 vest trace: 13 candidates at 50 Hz, 6 on its 25 Hz decimation),
which is why the report also runs every 100 Hz session on its own 2:1
decimation (`decimate`).

Standard library only, like sim/score.py: the ride_loop interpreter is
whatever Python ran `--install`, and nothing guarantees numpy on it.
"""

from __future__ import annotations

import bisect
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional, Sequence

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "sim") not in sys.path:
    sys.path.insert(0, str(REPO / "sim"))

from score import BOOT_RESET_S, MAX_GAP_S, contiguous_segments  # noqa: E402

GEN_VERSION = "cg-1"

# Raw samples within this many seconds after the unload's last sample may be
# the landing; the pop window reaches this far past the unload's first one.
# Fixed by the spec (section 3.2 steps 5-6), not parameters.
POP_LEAD_S = 0.05
LAND_LEAD_S = 0.05
LAND_PEAK_WIN_S = 0.30

# A segment this short (in samples) is skipped outright (spec 3.2).
MIN_SEGMENT_SAMPLES = 10

# Float slack on the smoothing half-width. Trace times are written to the
# millisecond, so a 0.050 s difference arrives as 0.04999999 or 0.05000001.
_SMOOTH_EPS_S = 1e-6


def fixed_constants() -> dict:
    """Every constant OUTSIDE CGParams that changes what `generate` emits,
    including the two it borrows from sim/score.py (the 0.5 s gap cut and
    the 1 s reboot step that define a segment). They are part of the
    generator's definition, so tools/tests/test_candgen.py pins this dict
    against GEN_VERSION exactly as it pins the CGParams defaults: changing
    any of them — here or in score.py — fails that test unless GEN_VERSION
    changes too, and a new GEN_VERSION is what makes tools/ride_loop.py
    re-run every existing candidates.md. params_id stays the spec's hash of
    {v, params}: within one GEN_VERSION these values cannot differ."""
    return {"POP_LEAD_S": POP_LEAD_S, "LAND_LEAD_S": LAND_LEAD_S,
            "LAND_PEAK_WIN_S": LAND_PEAK_WIN_S,
            "MIN_SEGMENT_SAMPLES": MIN_SEGMENT_SAMPLES,
            "SMOOTH_EPS_S": _SMOOTH_EPS_S, "MAX_GAP_S": MAX_GAP_S,
            "BOOT_RESET_S": BOOT_RESET_S}


@dataclass(frozen=True)
class CGParams:
    """CG-1 thresholds. Changing ANY default requires a new GEN_VERSION —
    tools/tests/test_candgen.py pins this dict the way test_params_parity.py
    pins the detector's."""

    unload_g: float = 0.70      # smoothed |a| below this is "unloaded"
    smooth_s: float = 0.10      # centred moving-average width
    merge_s: float = 0.20       # merge unload runs separated by at most this
    min_unload_s: float = 0.30
    max_unload_s: float = 6.00  # deliberately above Surfr's 3.9 s maximum
    pop_g: float = 2.00         # raw pop peak required
    pop_win_s: float = 1.00     # look-back before the unload starts
    land_g: float = 4.00        # raw landing-impact threshold
    land_win_s: float = 1.00    # look-ahead after the unload ends
    # Flag only. 15.99 g per-axis rail / ~1.03 g rest baseline (review
    # section 3 finding 5). ASSUMED approximate: the boot-time g_baseline
    # the trace is normalised by is not in the bundle.
    rail_g: float = 15.4


def params_id(p: CGParams) -> str:
    """First 8 hex of sha256 over {"v": GEN_VERSION, **params}, sorted keys."""
    blob = json.dumps({"v": GEN_VERSION, **asdict(p)}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:8]


# Presets are named per MOUNT. Both are vest presets; there is no board
# preset until per-jump truth exists on a board session (spec section 7.4).
#
# Where they came from — ASSUMED, stated so nobody mistakes them for fitted
# values: R's values were written into the scratch prototype BEFORE its first
# sweep ran, from STATUS's description of the 2026-09-23 vest shape (pop
# 3-4 g, 0.7-1.5 s of partial unloading at a mean of 0.6-0.7 g, a hard
# landing). L is the loosest corner of the characterization grid. NEITHER
# was chosen by comparing a count to Surfr's total. On 2026-09-23 R happens
# to give 14, the same as Surfr: a coincidence of an a-priori choice, and
# every report that prints both numbers says so.
PRESETS: dict[str, CGParams] = {
    "vest-R": CGParams(),
    "vest-L": CGParams(unload_g=0.80, pop_g=1.50, land_g=2.50),
}

# The characterization grid (spec section 8.1). Descriptive only: no row of
# it is ever selected.
GRID_UNLOAD_G = (0.5, 0.6, 0.7, 0.8)
GRID_POP_G = (1.5, 2.0, 3.0)
GRID_LAND_G = (2.5, 4.0, 6.0)
GRID_MIN_UNLOAD_S = (0.3, 0.5)


def grid_params() -> list[CGParams]:
    out = []
    for mu in GRID_MIN_UNLOAD_S:
        for ug in GRID_UNLOAD_G:
            for pg in GRID_POP_G:
                for lg in GRID_LAND_G:
                    out.append(CGParams(unload_g=ug, pop_g=pg, land_g=lg,
                                        min_unload_s=mu))
    return out


# ------------------------------------------------------------ the window

@dataclass
class Timebase:
    kind: str = "mcu_uptime"            # "mcu_uptime" | "sensor_fifo"
    tick_us_nominal: Optional[float] = None
    tick_us_measured: Optional[float] = None
    scale_note: str = ""

    @property
    def scale_unverified(self) -> bool:
        """Sensor-FIFO ticks measured ~24.33 us, not the nominal 25 us
        (research/usb_recorder/TIMEBASE_STUDY.md:6-7). A FIFO window that
        knows only the nominal tick has unverified durations."""
        return self.kind == "sensor_fifo" and self.tick_us_measured is None


@dataclass
class MotionWindow:
    """One continuous stretch of motion. `generate` reads only t_s and mag_g,
    so a magnitude-trace segment and a future six-axis event window run
    through the same code."""

    window_id: str
    boot: str                           # "last" | "earlier"
    source: str                         # "trace_mag" | "six_axis"
    log_hz: Optional[float]
    t_s: list[float]
    mag_g: list[float]
    mag_norm_src: str = "boot_rest"     # firmware/src/main.cpp:1764
    acc_g: Optional[list] = None
    gyro_dps: Optional[list] = None
    clip_mask: Optional[list] = None
    die_temp_c: Optional[list] = None
    timebase: Timebase = field(default_factory=Timebase)
    pre_s: Optional[float] = None       # event windows only
    post_s: Optional[float] = None
    mount_id: str = "UNKNOWN"
    # Smoothed magnitude and raw unload runs, keyed by threshold, so a sweep
    # over 72 parameter sets smooths once. Never part of equality or repr.
    _cache: dict = field(default_factory=dict, repr=False, compare=False)


def windows_from_trace(t: Sequence[float], mag: Sequence[float],
                       log_hz: Optional[float], boot_split: int = 0,
                       mount_id: str = "UNKNOWN",
                       label: str = "trace") -> list[MotionWindow]:
    """One MotionWindow per contiguous segment. Rows before `boot_split`
    (the first row of the last boot, sim/score.py Timebase) are boot
    'earlier'; no segment spans the split because a reboot is itself a
    segment break (is_continuous)."""
    out: list[MotionWindow] = []
    for k, (s, e) in enumerate(contiguous_segments(t, MAX_GAP_S)):
        out.append(MotionWindow(
            window_id=f"{label}:seg{k}",
            boot="earlier" if s < boot_split else "last",
            source="trace_mag", log_hz=log_hz,
            t_s=list(t[s:e + 1]), mag_g=list(mag[s:e + 1]),
            mount_id=mount_id))
    return out


def windows_from_events(path) -> list[MotionWindow]:
    """No on-device six-axis event format exists yet. A bundle carrying event
    files must fail loudly, not be ignored (CLAUDE.md rule 3)."""
    raise NotImplementedError("no on-device six-axis event format exists yet")


def decimate(t: Sequence[float], mag: Sequence[float],
             factor: int = 2) -> tuple[list[float], list[float]]:
    """Every `factor`-th row: the firmware's own logging rule (it logs every
    LOG_DECIMATE-th 200 Hz sample), applied once more. 100 Hz -> 50 Hz."""
    return list(t[::factor]), list(mag[::factor])


# ------------------------------------------------------------ candidates

@dataclass
class Candidate:
    window_id: str
    boot: str
    pop_t_s: float
    pop_g: float
    unload_t0_s: float
    unload_t1_s: float
    unload_s: float
    unload_mean_g: float
    unload_min_g: float
    land_t_s: float
    land_peak_g: float
    t_pop_to_land_s: float      # NOT an airtime
    t_unload_to_land_s: float   # NOT an airtime
    max_inside_g: Optional[float]
    railed: bool
    n_samples: int
    jitter_steps_inside: int
    truncated_pre: bool = False
    truncated_post: bool = False


class _Seg:
    """Index helpers over one segment whose times are ascending up to small
    backward jitter. `idx_range(lo, hi)` returns a superset index range
    [i0, i1) of every sample with lo <= t <= hi, exact under jitter:
    prefix-max of t is monotone (everything before i0 has t < lo) and
    suffix-min of t is monotone (everything from i1 on has t > hi)."""

    def __init__(self, t: list[float]):
        self.t = t
        n = len(t)
        pmax = [0.0] * n
        m = float("-inf")
        for i, v in enumerate(t):
            if v > m:
                m = v
            pmax[i] = m
        smin = [0.0] * n
        m = float("inf")
        for i in range(n - 1, -1, -1):
            if t[i] < m:
                m = t[i]
            smin[i] = m
        self.pmax = pmax
        self.smin = smin

    def idx_range(self, lo: float, hi: float) -> tuple[int, int]:
        i0 = bisect.bisect_left(self.pmax, lo)
        i1 = bisect.bisect_right(self.smin, hi)
        return i0, i1


def _smooth(t: list[float], m: list[float], width_s: float,
            seg: Optional["_Seg"] = None) -> list[float]:
    """sm[i] = mean of m[j] over every j in the segment with
    |t[j] - t[i]| <= width_s/2. Truncated at the edges, never zero-padded.

    Set semantics, exactly as the spec states it, including under the
    trace's sub-second backward jitter (a flush/re-sync artifact: measured
    -0.024..-0.078 s steps on the 2026-09-14 trace). Where no backward step
    lies inside the window, the window is one contiguous index range and a
    prefix sum gives it in O(1); only windows that straddle a backward step
    are filtered sample by sample.

    The scratch prototype used a fixed kernel of round(width/median dt)
    samples instead. The two agree wherever the sample spacing is regular;
    at an irregular step the index kernel spans a different time interval.
    MEASURED on the 2026-09-14 vest trace: one 0.106 s forward step at
    t=9629.947 -> 9630.053 s lets the prototype's 5-sample kernel average
    2.07-2.19 g samples from across the step into the first two unloaded
    samples. That one event is the whole difference between this module and
    spec section 8.1: R and L counts are identical on both vest sessions,
    four Sep-14 grid cells (unload_g <= 0.6, land_g 2.5) gain one candidate,
    and one L candidate's unload starts 0.02 s earlier.
    """
    n = len(t)
    h = width_s / 2.0 + _SMOOTH_EPS_S
    seg = seg or _Seg(t)
    pref = [0.0] * (n + 1)
    back = [0] * (n + 1)      # back[k] = backward steps among t[0..k-1]
    acc = 0.0
    for i, v in enumerate(m):
        acc += v
        pref[i + 1] = acc
        back[i + 1] = back[i] + (1 if i > 0 and t[i] < t[i - 1] else 0)
    out = [0.0] * n
    for i in range(n):
        ti = t[i]
        i0, i1 = seg.idx_range(ti - h, ti + h)
        # Conservative: a step AT i0 or just past i1-1 can also put a
        # superset-range sample outside the window, so count those too.
        if back[min(i1 + 1, n)] - back[i0] == 0:
            out[i] = (pref[i1] - pref[i0]) / (i1 - i0)
        else:
            vals = [m[j] for j in range(i0, i1) if abs(t[j] - ti) <= h]
            out[i] = sum(vals) / len(vals)
    return out


def _runs_below(sm: list[float], thr: float) -> list[tuple[int, int]]:
    runs = []
    start = -1
    for i, v in enumerate(sm):
        if v < thr:
            if start < 0:
                start = i
        elif start >= 0:
            runs.append((start, i - 1))
            start = -1
    if start >= 0:
        runs.append((start, len(sm) - 1))
    return runs


def _segment_prep(win: MotionWindow, smooth_s: float):
    """[(seg_start, seg_end, _Seg, smoothed)] for every segment of the window
    with more than MIN_SEGMENT_SAMPLES samples; cached on the window."""
    key = ("prep", smooth_s)
    got = win._cache.get(key)
    if got is not None:
        return got
    out = []
    t, m = win.t_s, win.mag_g
    for s, e in contiguous_segments(t, MAX_GAP_S):
        if e - s < MIN_SEGMENT_SAMPLES:
            continue
        ts = list(t[s:e + 1])
        ms = list(m[s:e + 1])
        sg = _Seg(ts)
        out.append((ts, ms, sg, _smooth(ts, ms, smooth_s, sg)))
    win._cache[key] = out
    return out


def _seg_runs(win: MotionWindow, k: int, sm: list[float], smooth_s: float,
              unload_g: float) -> list[tuple[int, int]]:
    key = ("runs", smooth_s, unload_g, k)
    got = win._cache.get(key)
    if got is None:
        got = _runs_below(sm, unload_g)
        win._cache[key] = got
    return got


def generate(win: MotionWindow, p: CGParams) -> list[Candidate]:
    """Every CG-1 candidate in one window, in time order. A pure function of
    (window samples, params): nothing else is read."""
    out: list[Candidate] = []
    w_t0 = min(win.t_s) if win.t_s else 0.0
    w_t1 = max(win.t_s) if win.t_s else 0.0
    bounded = win.source != "trace_mag" or win.pre_s is not None or win.post_s is not None
    for k, (ts, ms, seg, sm) in enumerate(_segment_prep(win, p.smooth_s)):
        runs = _seg_runs(win, k, sm, p.smooth_s, p.unload_g)
        merged: list[list[int]] = []
        for a, b in runs:
            if merged:
                pa, pb = merged[-1]
                if ts[a] - ts[pb] <= p.merge_s and max(ms[pb:a + 1]) < p.land_g:
                    merged[-1][1] = b
                    continue
            merged.append([a, b])
        last_land = float("-inf")
        for a, b in merged:
            u0, u1 = ts[a], ts[b]
            dur = u1 - u0
            if dur < p.min_unload_s or dur > p.max_unload_s:
                continue
            if u0 <= last_land:
                continue
            # pop
            plo, phi = u0 - p.pop_win_s, u0 + POP_LEAD_S
            i0, i1 = seg.idx_range(plo, phi)
            pk = -1
            for i in range(i0, i1):
                if plo <= ts[i] <= phi and (pk < 0 or ms[i] > ms[pk]):
                    pk = i
            if pk < 0 or ms[pk] < p.pop_g:
                continue
            # landing: the first sample (index order) at or above land_g
            llo, lhi = u1 - LAND_LEAD_S, u1 + p.land_win_s
            j0, j1 = seg.idx_range(llo, lhi)
            li = -1
            for i in range(j0, j1):
                if llo <= ts[i] <= lhi and ms[i] >= p.land_g:
                    li = i
                    break
            if li < 0:
                continue
            land_t = ts[li]
            q0, q1 = seg.idx_range(land_t, land_t + LAND_PEAK_WIN_S)
            lpk = -1
            for i in range(q0, q1):
                if land_t <= ts[i] <= land_t + LAND_PEAK_WIN_S and \
                        (lpk < 0 or ms[i] > ms[lpk]):
                    lpk = i
            pop_t = ts[pk]
            run = ms[a:b + 1]
            r0, r1 = seg.idx_range(pop_t, land_t)
            inside = [ms[i] for i in range(r0, r1) if pop_t < ts[i] < land_t]
            railed = any(ms[i] >= p.rail_g for i in range(i0, i1)
                         if plo <= ts[i] <= phi) or \
                any(ms[i] >= p.rail_g for i in range(q0, q1)
                    if land_t <= ts[i] <= land_t + LAND_PEAK_WIN_S)
            lo_i, hi_i = min(pk, li), max(pk, li)
            jitter = sum(1 for i in range(lo_i, hi_i) if ts[i + 1] < ts[i])
            out.append(Candidate(
                window_id=win.window_id, boot=win.boot,
                pop_t_s=pop_t, pop_g=ms[pk],
                unload_t0_s=u0, unload_t1_s=u1, unload_s=dur,
                unload_mean_g=sum(run) / len(run), unload_min_g=min(run),
                land_t_s=land_t, land_peak_g=ms[lpk],
                t_pop_to_land_s=land_t - pop_t,
                t_unload_to_land_s=land_t - u0,
                max_inside_g=max(inside) if inside else None,
                railed=railed,
                n_samples=hi_i - lo_i + 1,
                jitter_steps_inside=jitter,
                truncated_pre=bounded and plo < w_t0,
                truncated_post=bounded and lhi > w_t1,
            ))
            last_land = land_t
    return out


def generate_all(windows: Sequence[MotionWindow], p: CGParams) -> list[Candidate]:
    out: list[Candidate] = []
    for w in windows:
        out.extend(generate(w, p))
    return out
