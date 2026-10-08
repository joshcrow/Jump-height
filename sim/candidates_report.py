#!/usr/bin/env python3
"""candidates_report.py — write <session>/candidates.md and candidates.csv.

`./tools/jump candidates <session>...` runs this. It answers, for one
session and without a human (spec section 0):

  Q2  does the trace show the pop -> partial unload -> landing shape at
      speed, and with what unload depth/duration and landing g?
  Q3  how many candidates happen when not riding (< 4 kn) or outside the
      activity (a false-positive PROXY, nothing more)?
  Q4  how often do landings reach the +-16 g rail?
  Q5  once Surfr's per-jump list is transcribed, do candidates line up
      with Surfr's jump times better than chance (sim/surfr_match.py)?

(Q1, the stock detector's own events, is score.md section 3a.)

Everything is descriptive. No candidate is called a jump, no height is
computed anywhere here, and no parameter is selected — see the fixed
"What this cannot conclude" section every report ends with.

Exit status: 0 when every session was written with no FINDING about its
inputs; 1 when a session could not be processed or its surfr.json failed
validation. ride_loop logs a nonzero exit and carries on.

Standard library only (sim/score.py:41 gives the reason).
"""

from __future__ import annotations

import csv
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

import candctx  # noqa: E402
import candgen  # noqa: E402
import score  # noqa: E402
import surfr_match  # noqa: E402

PRESET_R = "vest-R"
PRESET_L = "vest-L"
OUTSIDE_MARGIN_S = 600.0     # motion this close to the window is neither in nor out
HIGH_RATE_HZ = 90.0          # a trace at or above this also runs 2:1-decimated
UNDER_KN = 4.0

CSV_COLUMNS = [
    "session", "gen_version", "params_id", "preset", "mount_id", "source",
    "log_hz", "trace_format", "boot", "region", "cand_id", "pop_t_s", "pop_utc",
    "pop_local", "pop_g", "unload_t0_s", "unload_t1_s", "unload_s",
    "unload_mean_g", "unload_min_g", "land_t_s", "land_peak_g",
    "t_pop_to_land_s", "t_unload_to_land_s", "max_inside_g", "railed",
    "n_samples", "jitter_steps_inside", "truncated_pre", "truncated_post",
    "scale_unverified", "in_garmin_window", "in_surfr_window", "kn_before",
    "n_fit_before", "kn_after", "n_fit_after", "kn_min_after20", "stopped_after",
    "course_at_pop_deg", "course_age_s", "turn_before_pop_deg", "wind_from_deg",
    "wind_src", "rel_wind_deg", "device_event_n", "surfr_n", "surfr_residual_s",
    # six-axis stubs (spec 3.6): absent until a six-axis source exists
    "gyro_peak_dps", "gyro_med_unload_dps", "clipped_samples", "pitch_change_deg",
    "unload_mean_spin_corr_g", "vert_sf_mean_g", "rise_m", "die_temp_c",
]
SIX_AXIS_STUBS = {
    "gyro_peak_dps": "source=trace_mag",
    "gyro_med_unload_dps": "source=trace_mag",
    "clipped_samples": "source=trace_mag",
    "pitch_change_deg": "needs mount transform",
    "unload_mean_spin_corr_g": "source=trace_mag (and no measured lever arm)",
    "vert_sf_mean_g": "source=trace_mag",
    "rise_m": "not implemented — G2",
    "die_temp_c": "source=trace_mag",
}

DIST_FEATURES = ("pop_g", "unload_s", "unload_mean_g", "unload_min_g",
                 "land_peak_g", "t_pop_to_land_s", "max_inside_g",
                 "kn_before", "kn_after")

CANNOT_CONCLUDE = [
    "**Nothing here says any candidate is a jump.** A candidate is a shape in "
    "a magnitude trace. Only per-jump truth (Surfr rows with times, video, lap "
    "presses) can say which ones are jumps.",
    "**No parameter is validated.** vest-R was set before any sweep, from a "
    "description of vest data; vest-L is the loosest grid corner. A candidate "
    "count that equals Surfr's total is a coincidence of an a-priori setting "
    "and is **not evidence** (sim/candgen.py, \"the rules\").",
    "**`t_pop_to_land_s` and `unload_s` are not airtimes**, and **no height "
    "is computed anywhere in this report.**",
    "**Vest results do not transfer to the board.** Both presets are torso "
    "presets. A board session runs them unchanged and is descriptive only.",
    "**Counts depend on log rate** (measured on the 2026-09-14 vest trace: "
    "13 at 50 Hz, 6 on its 25 Hz decimation). A 100 Hz session is compared "
    "with its own 2:1 decimation, never directly with 50 Hz counts.",
    "**Wind direction is regional** (a station or model grid kilometres "
    "away, named in the wind column). `rel_wind` is relative to that, not to "
    "the wind on the water.",
    "**Speed and heading come from 1-8 s Smart-Recording records.** A 1-2 s "
    "flight usually has no record inside it.",
]


# ------------------------------------------------------------------ helpers

def quantile(xs: Sequence[float], q: float) -> float:
    """Linear interpolation between order statistics (numpy's default)."""
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = q * (len(s) - 1)
    i = int(math.floor(pos))
    frac = pos - i
    return s[i] if i + 1 >= len(s) else s[i] + (s[i + 1] - s[i]) * frac


def read_mount(sess: Path) -> tuple[str, str]:
    """(mount_id, display) — never inferred."""
    p = sess / "mount.json"
    if not p.exists():
        return "UNKNOWN", "UNKNOWN (no mount.json)"
    try:
        d = json.loads(p.read_text())
        mid = str(d["mount_id"])
    except (OSError, ValueError, KeyError, TypeError):
        return "UNKNOWN", "UNKNOWN (mount.json unreadable or has no mount_id)"
    try:
        reg = json.loads((REPO / "config" / "mounts.json").read_text())
    except (OSError, ValueError):
        reg = {}
    if mid not in reg or mid.startswith("_"):
        return mid, f"UNKNOWN (id {mid!r} not in config/mounts.json)"
    src = d.get("source")
    return mid, mid + (f" (source: {src})" if src else "")


def logged_minutes(t: Sequence[float], segs, pred) -> float:
    tot = 0.0
    for s, e in segs:
        for i in range(s, e):
            if pred(t[i]):
                d = t[i + 1] - t[i]
                if d > 0:
                    tot += d
    return tot / 60.0


@dataclass
class Row:
    preset: str
    params_id: str
    cand: candgen.Candidate
    region: str
    ctx: dict
    why: dict
    cand_id: int = 0
    surfr_n: object = None
    surfr_residual_s: Optional[float] = None
    also_in_r: Optional[bool] = None


@dataclass
class SessionReport:
    sess: Path
    n_rows: int
    log_hz: Optional[float]
    log_hz_src: str
    measured_hz: Optional[float]
    trace_format: str
    n_boots: int
    mount_id: str
    mount_display: str
    ctx: candctx.Context
    window: Optional[tuple[float, float]]
    window_src: str
    in_minutes: Optional[float]
    out_minutes: Optional[float]
    rows: dict = field(default_factory=dict)            # preset -> [Row]
    grid: list = field(default_factory=list)             # (params, n_native, n_dec|None)
    decimated: bool = False
    match: Optional[surfr_match.MatchResult] = None
    surfr_note: str = ""
    findings: list = field(default_factory=list)
    inputs: list = field(default_factory=list)
    align_lines: list = field(default_factory=list)


def _region(c: candgen.Candidate, window) -> str:
    # With no session window the whole trace is counted, every boot included
    # (the scope line says so, `_count` counts so, and the per-hour rate uses
    # every boot's logged minutes): one rule for section 1, the grid and the
    # rate. The `boot` column still says which boot a candidate came from.
    if window is None:
        return "no_window"
    if c.boot != "last":
        return "earlier_boot"
    lo, hi = window
    if lo <= c.pop_t_s <= hi:
        return "session"
    if lo - OUTSIDE_MARGIN_S <= c.pop_t_s <= hi + OUTSIDE_MARGIN_S:
        return "margin"
    return "outside"


def _count(cands, window) -> int:
    if window is None:
        return len(cands)
    return sum(1 for c in cands if _region(c, window) == "session")


def analyse(sess: Path) -> Optional[SessionReport]:
    trace = sess / "trace.csv"
    if not trace.exists():
        return None
    t, m = score.load_trace(trace)
    if not t:
        return None
    sj = score.load_session_json(sess)
    man = sj.get("manifest") or {}
    tb = score.analyse_timebase(t)
    diffs = sorted(t[i + 1] - t[i] for i in range(min(len(t) - 1, 200000))
                   if 0 < t[i + 1] - t[i] <= score.MAX_GAP_S)
    measured_hz = (1.0 / diffs[len(diffs) // 2]) if diffs else None
    if man.get("log_hz") is not None:
        log_hz, log_hz_src = float(man["log_hz"]), "manifest"
    elif measured_hz:
        log_hz, log_hz_src = round(measured_hz), "measured median spacing"
    else:
        log_hz, log_hz_src = None, "unknown"
    mount_id, mount_display = read_mount(sess)
    garmin = score.load_garmin(sess)
    ctx = candctx.build_context(sess, garmin)
    window = candctx.session_window(ctx)
    if window is None:
        why = []
        if ctx.epoch_utc is None:
            why.append("no trace_epoch_utc")
        else:
            why.append(f"Garmin: {ctx.garmin_reason or 'no window'}")
            why.append(f"Surfr: {ctx.surfr_window_reason or 'no window'}")
        window_src = (f"no session window ({'; '.join(why)}): the whole trace is "
                      f"counted, all boots")
    else:
        parts = [n for n, w in (("Surfr", ctx.surfr_window), ("Garmin", ctx.garmin_window))
                 if w is not None]
        window_src = (f"{' ∪ '.join(parts)} window, trace t={window[0]:.1f}.."
                      f"{window[1]:.1f} s ({(window[1] - window[0]) / 60:.1f} min)")

    segs = score.contiguous_segments(t)
    if window is not None:
        lo, hi = window
        in_min = logged_minutes(t[tb.last_boot_start_idx:],
                                score.contiguous_segments(t[tb.last_boot_start_idx:]),
                                lambda x: lo <= x <= hi)
        out_min = logged_minutes(t, segs, lambda x: True) - in_min - logged_minutes(
            t[tb.last_boot_start_idx:],
            score.contiguous_segments(t[tb.last_boot_start_idx:]),
            lambda x: (lo - OUTSIDE_MARGIN_S <= x < lo) or (hi < x <= hi + OUTSIDE_MARGIN_S))
    else:
        in_min = logged_minutes(t, segs, lambda x: True)
        out_min = None

    rep = SessionReport(
        sess=sess, n_rows=len(t), log_hz=log_hz, log_hz_src=log_hz_src,
        measured_hz=measured_hz, trace_format=str(man.get("trace_format") or "csv"),
        n_boots=len(tb.boot_resets) + 1, mount_id=mount_id,
        mount_display=mount_display, ctx=ctx, window=window, window_src=window_src,
        in_minutes=in_min, out_minutes=out_min)

    wins = candgen.windows_from_trace(t, m, log_hz, tb.last_boot_start_idx, mount_id)
    for name in (PRESET_R, PRESET_L):
        p = candgen.PRESETS[name]
        pid = candgen.params_id(p)
        rows = []
        for c in candgen.generate_all(wins, p):
            v, why = candctx.enrich(c, ctx)
            rows.append(Row(preset=name, params_id=pid, cand=c,
                            region=_region(c, window), ctx=v, why=why))
        for k, r in enumerate(rows, start=1):
            r.cand_id = k
        rep.rows[name] = rows
    r_keys = {(round(r.cand.pop_t_s, 3), round(r.cand.land_t_s, 3))
              for r in rep.rows[PRESET_R]}
    for r in rep.rows[PRESET_L]:
        r.also_in_r = (round(r.cand.pop_t_s, 3), round(r.cand.land_t_s, 3)) in r_keys

    # grid (descriptive), native and, at >= 90 Hz, 2:1-decimated
    rep.decimated = bool((log_hz or 0) >= HIGH_RATE_HZ or (measured_hz or 0) >= HIGH_RATE_HZ)
    dwins = None
    if rep.decimated:
        dt_, dm_ = candgen.decimate(t, m, 2)
        dtb = score.analyse_timebase(dt_)
        dwins = candgen.windows_from_trace(dt_, dm_, (log_hz or 100) / 2,
                                           dtb.last_boot_start_idx, mount_id)
    for p in candgen.grid_params():
        n_nat = _count(candgen.generate_all(wins, p), window)
        n_dec = _count(candgen.generate_all(dwins, p), window) if dwins is not None else None
        rep.grid.append((p, n_nat, n_dec))

    _inputs(rep, sj)
    _surfr(rep, sj)
    return rep


def _inputs(rep: SessionReport, sj: dict) -> None:
    s = rep.sess
    add = rep.inputs.append
    add(f"trace.csv: present, {rep.n_rows:,} rows")
    add(f"garmin.fit: present, {len(rep.ctx.fit_t):,} stamped records" if rep.ctx.fit_t
        else f"garmin.fit: ABSENT or unusable ({rep.ctx.garmin_reason or 'no records'})")
    if rep.ctx.wind_t:
        add(f"wind.json: present ({rep.ctx.wind_src})")
    else:
        add(f"wind.json: ABSENT ({rep.ctx.wind_reason})")
    if (s / "surfr.json").exists():
        add("surfr.json: present")
    else:
        add("surfr.json: ABSENT (no Surfr data for this session)")
    add(f"mount.json: {'present' if (s / 'mount.json').exists() else 'ABSENT'} "
        f"→ mount {rep.mount_display}")
    for ln in rep.ctx.tz_lines:
        add(ln)
        if ln.startswith("FINDING"):
            rep.findings.append(ln)
    # score.md's own alignment verdict, quoted rather than recomputed
    try:
        sm = (s / "score.md").read_text()
        lines = [ln.strip("- ").strip() for ln in sm.splitlines()
                 if "ALIGNMENT OK" in ln or "ALIGNMENT FAILS" in ln
                 or "epoch self-check" in ln]
        rep.align_lines += [f"score.md §1: {ln}" for ln in lines] or \
            ["score.md §1: no alignment verdict line (garmin.fit absent or no epoch)"]
    except OSError:
        rep.align_lines.append("score.md: ABSENT — run `./tools/jump score` for the "
                               "alignment verdict")
    lags = candctx.alignment_lags(rep.ctx)
    if lags is None:
        rep.align_lines.append(
            "watch jump_height check: did not run ("
            + ("the FIT carries no jump_height value" if rep.ctx.fit_t else
               f"no FIT: {rep.ctx.garmin_reason or 'no records'}") + ")")
    elif not lags:
        rep.align_lines.append("watch jump_height check: the FIT's jump_height never "
                               "changes value — nothing to compare (not a pass)")
    else:
        a, b = candctx.ALIGN_LAG_BAND_S
        vals = [lg for _, _, lg, _ in lags if lg is not None]
        neg = [lg for lg in vals if lg < 0]
        if not vals:
            verdict = "did not run: no device landing to compare with (not a pass)"
        elif neg:
            verdict = (f"ALIGNMENT FINDING: {len(neg)} change(s) come BEFORE the "
                       f"nearest device landing")
            rep.findings.append(verdict)
        elif all(a <= lg <= b for lg in vals):
            verdict = f"consistent (every lag in [{a:g}, {b:g}] s)"
        else:
            verdict = f"outside the [{a:g}, {b:g}] s band — check the alignment"
        rep.align_lines.append(
            f"watch jump_height check: {len(lags)} value change(s); lag after the "
            f"nearest preceding device landing: "
            + ", ".join(f"{lg:+.2f} s (n={n})" if lg is not None else "no landing"
                        for _, _, lg, n in lags)
            + f" — {verdict}")


def _surfr(rep: SessionReport, sj: dict) -> None:
    path = rep.sess / "surfr.json"
    surfr, err = surfr_match.load_surfr_strict(path)
    if err:
        rep.findings.append(err)
        rep.surfr_note = f"did not run: {err}"
        return
    if surfr is None:
        rep.surfr_note = "did not run: no surfr.json"
        return
    s0, _, _, _ = score.surfr_window(sj, surfr)
    start_t = rep.ctx.utc_to_t(s0) if s0 is not None else None
    cands = []
    for r in rep.rows[PRESET_L]:
        if r.cand.boot != "last":
            continue
        c = r.cand
        extra = {
            "t_pop_to_land": f"{c.t_pop_to_land_s:.2f} s",
            "unload": f"{c.unload_s:.2f} s @ {c.unload_mean_g:.2f} g",
            "kn before→after": _kn_pair(r),
            "in vest-R": "yes" if r.also_in_r else "no",
        }
        cands.append(surfr_match.Cand(cid=r.cand_id, pop=c.pop_t_s, land=c.land_t_s,
                                      extra=extra))
    res = surfr_match.match(surfr, start_t, cands, rep.window)
    if start_t is None:
        # Say WHICH input is missing, not just that the start has no place.
        res.reason = ("the Surfr start cannot be placed on the trace clock: "
                      + ("session.json has no trace_epoch_utc"
                         if rep.ctx.epoch_utc is None else
                         "surfr.json has no session_start_local"))
    rep.match = res
    rep.findings += [f for f in res.findings if "FINDING" in f]
    by_id = {r.cand_id: r for r in rep.rows[PRESET_L]}
    for row, c, resid in res.pairs:
        by_id[c.cid].surfr_n = row.n
        by_id[c.cid].surfr_residual_s = resid


def _kn_pair(r: Row) -> str:
    a, b = r.ctx.get("kn_before"), r.ctx.get("kn_after")
    f = (lambda v: "–" if v is None else f"{v:.1f}")
    return f"{f(a)}→{f(b)}"


# ------------------------------------------------------------------ render

def _fmt(v, fmt="{:.2f}"):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return fmt.format(v)
    return str(v)


def write_csv(rep: SessionReport, path: Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_COLUMNS)
        for name in (PRESET_R, PRESET_L):
            for r in rep.rows[name]:
                c = r.cand
                d = {
                    "session": rep.sess.name, "gen_version": candgen.GEN_VERSION,
                    "params_id": r.params_id, "preset": name, "mount_id": rep.mount_id,
                    "source": "trace_mag", "log_hz": _fmt(rep.log_hz, "{:g}"),
                    "trace_format": rep.trace_format, "boot": c.boot,
                    "region": r.region, "cand_id": r.cand_id,
                    "pop_t_s": _fmt(c.pop_t_s, "{:.3f}"),
                    "pop_g": _fmt(c.pop_g, "{:.3f}"),
                    "unload_t0_s": _fmt(c.unload_t0_s, "{:.3f}"),
                    "unload_t1_s": _fmt(c.unload_t1_s, "{:.3f}"),
                    "unload_s": _fmt(c.unload_s, "{:.3f}"),
                    "unload_mean_g": _fmt(c.unload_mean_g, "{:.3f}"),
                    "unload_min_g": _fmt(c.unload_min_g, "{:.3f}"),
                    "land_t_s": _fmt(c.land_t_s, "{:.3f}"),
                    "land_peak_g": _fmt(c.land_peak_g, "{:.3f}"),
                    "t_pop_to_land_s": _fmt(c.t_pop_to_land_s, "{:.3f}"),
                    "t_unload_to_land_s": _fmt(c.t_unload_to_land_s, "{:.3f}"),
                    "max_inside_g": _fmt(c.max_inside_g, "{:.3f}"),
                    "railed": _fmt(c.railed), "n_samples": c.n_samples,
                    "jitter_steps_inside": c.jitter_steps_inside,
                    "truncated_pre": _fmt(c.truncated_pre),
                    "truncated_post": _fmt(c.truncated_post),
                    "scale_unverified": "0",
                    "surfr_n": "" if r.surfr_n is None else r.surfr_n,
                    "surfr_residual_s": _fmt(r.surfr_residual_s, "{:.2f}"),
                }
                for k in ("pop_utc", "pop_local", "in_garmin_window", "in_surfr_window",
                          "n_fit_before", "n_fit_after", "stopped_after", "wind_src",
                          "device_event_n"):
                    d[k] = _fmt(r.ctx.get(k))
                for k in ("kn_before", "kn_after", "kn_min_after20"):
                    d[k] = _fmt(r.ctx.get(k), "{:.2f}")
                for k in ("course_at_pop_deg", "course_age_s", "turn_before_pop_deg",
                          "wind_from_deg", "rel_wind_deg"):
                    d[k] = _fmt(r.ctx.get(k), "{:.1f}")
                w.writerow([d.get(k, "") for k in CSV_COLUMNS])


def _when(rep: SessionReport, r: Row) -> str:
    loc = r.ctx.get("pop_local")
    return loc[11:] if loc else f"t={r.cand.pop_t_s:.1f}"


def _v(r: Row, k: str, fmt: str = "{:.1f}") -> str:
    v = r.ctx.get(k)
    if v is None:
        return "absent"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return fmt.format(v) if isinstance(v, (int, float)) else str(v)


def render_md(rep: SessionReport) -> str:
    L: list[str] = []
    add = L.append
    pR = candgen.PRESETS[PRESET_R]
    pL = candgen.PRESETS[PRESET_L]
    idR, idL = candgen.params_id(pR), candgen.params_id(pL)
    add(f"# candidates.md — {rep.sess.name}")
    add("")
    add(f"Generated by `./tools/jump candidates` (`sim/candidates_report.py`), gen "
        f"{candgen.GEN_VERSION}, presets {PRESET_R} {idR} / {PRESET_L} {idL}.")
    add(f"mount: {rep.mount_display} · trace: {rep.n_rows:,} rows, log_hz "
        f"{_fmt(rep.log_hz, '{:g}') or 'unknown'} ({rep.log_hz_src}"
        + (f"; measured median {rep.measured_hz:.1f} Hz" if rep.measured_hz else "")
        + f"), format {rep.trace_format}, {rep.n_boots} boot(s) · Every number below "
          "is computed from this directory.")
    add("")
    for f in rep.findings:
        add(f"- {f}")
    if rep.findings:
        add("")

    # 0. inputs
    add("## 0. Inputs")
    add("")
    for ln in rep.inputs:
        add(f"- {ln}")
    add(f"- scope: {rep.window_src}")
    for ln in rep.align_lines:
        add(f"- {ln}")
    add("")

    # 1. counts
    add("## 1. Counts")
    add("")
    nR = sum(1 for r in rep.rows[PRESET_R] if r.region in ("session", "no_window"))
    nL = sum(1 for r in rep.rows[PRESET_L] if r.region in ("session", "no_window"))
    scope = ("in the session window" if rep.window else
             "over the whole trace (no window"
             + (f", all {rep.n_boots} boots summed" if rep.n_boots > 1 else "") + ")")
    per_h = (lambda n: f"{n / (rep.in_minutes / 60):.1f}/h"
             if rep.in_minutes else "n/a")
    add(f"- {PRESET_R}: **{nR}** {scope} ({per_h(nR)} of logged motion, "
        f"{rep.in_minutes:.1f} min logged)")
    add(f"- {PRESET_L}: **{nL}** {scope} ({per_h(nL)})")
    if rep.window:
        oR = sum(1 for r in rep.rows[PRESET_R] if r.region in ("outside", "earlier_boot"))
        oL = sum(1 for r in rep.rows[PRESET_L] if r.region in ("outside", "earlier_boot"))
        add(f"- outside the window (more than {OUTSIDE_MARGIN_S / 60:.0f} min from it, "
            f"plus any earlier boot; {rep.out_minutes:.1f} min of logged motion — "
            f"walking, carrying, rigging; that no jumps happened there is ASSUMED): "
            f"{PRESET_R} {oR}, {PRESET_L} {oL}"
            + (f" (vest-R at t = {', '.join(f'{r.cand.pop_t_s:.0f}' for r in rep.rows[PRESET_R] if r.region in ('outside', 'earlier_boot'))})" if oR else ""))
    if (rep.sess / "surfr.json").exists():
        add("- A count is never compared with Surfr's total here. If the two happen "
            "to be equal, that is a coincidence of an a-priori setting, not evidence.")
    add("")
    add("**Grid** (descriptive — no row of this grid is selected). Counts "
        + ("in the session window" if rep.window else "over the whole trace")
        + (", native / 2:1-decimated (the 50 Hz logging rule)" if rep.decimated else "")
        + ".")
    add("")
    for mu in candgen.GRID_MIN_UNLOAD_S:
        add(f"`min_unload_s = {mu:g}`")
        add("")
        add("| unload_g | pop_g | " + " | ".join(f"land {lg:g}" for lg in candgen.GRID_LAND_G) + " |")
        add("|---|---|" + "---|" * len(candgen.GRID_LAND_G))
        for ug in candgen.GRID_UNLOAD_G:
            for pg in candgen.GRID_POP_G:
                cells = []
                for lg in candgen.GRID_LAND_G:
                    for p, n, nd in rep.grid:
                        if (p.min_unload_s, p.unload_g, p.pop_g, p.land_g) == (mu, ug, pg, lg):
                            tag = ""
                            if p == pR:
                                tag = " (R)"
                            elif p == pL:
                                tag = " (L)"
                            cells.append(f"{n}" + (f" / {nd}" if nd is not None else "") + tag)
                add(f"| {ug:g} | {pg:g} | " + " | ".join(cells) + " |")
        add("")

    # 2. candidates (vest-R)
    add(f"## 2. Candidates ({PRESET_R})")
    add("")
    rowsR = [r for r in rep.rows[PRESET_R] if r.region in ("session", "no_window")]
    if not rowsR:
        add("None " + scope + ".")
    else:
        add("| id | local time | pop g | unload s | mean | min | land peak | pop→land | "
            "max inside | kn before | kn after | kn min 20 s | stopped | rel wind | "
            "turn | device n | Surfr n | railed |")
        add("|" + "---|" * 18)
        for r in rowsR:
            c = r.cand
            add(f"| {r.cand_id} | {_when(rep, r)} | {c.pop_g:.2f} | {c.unload_s:.2f} | "
                f"{c.unload_mean_g:.2f} | {c.unload_min_g:.2f} | {c.land_peak_g:.2f} | "
                f"{c.t_pop_to_land_s:.2f} | "
                f"{'absent' if c.max_inside_g is None else f'{c.max_inside_g:.2f}'} | "
                f"{_v(r, 'kn_before')} | {_v(r, 'kn_after')} | {_v(r, 'kn_min_after20')} | "
                f"{_v(r, 'stopped_after')} | {_v(r, 'rel_wind_deg', '{:+.0f}')} | "
                f"{_v(r, 'turn_before_pop_deg', '{:+.0f}')} | "
                f"{r.ctx.get('device_event_n') or '–'} | "
                f"{'–' if r.surfr_n is None else r.surfr_n} | "
                f"{'RAIL' if c.railed else ''} |")
        absent = {}
        for r in rowsR:
            for k, why in r.why.items():
                absent.setdefault(f"{k}: {why}", 0)
                absent[f"{k}: {why}"] += 1
        if absent:
            add("")
            add("absent cells (reason, count): " + "; ".join(
                f"{k} ×{n}" for k, n in sorted(absent.items())))
    add("")
    add("`pop→land` and `unload s` are NOT airtimes. Six-axis columns "
        "(gyro, clipping, pitch, spin-corrected load, vertical specific force, "
        "rise, die temperature) are absent: source=trace_mag.")
    add("")

    # 3. distributions
    add("## 3. Distributions")
    add("")
    add("min / p25 / median / p75 / max, " + scope + ".")
    add("")
    add(f"| feature | {PRESET_R} | {PRESET_L} |")
    add("|---|---|---|")
    for k in DIST_FEATURES:
        cells = []
        for name in (PRESET_R, PRESET_L):
            vals = []
            for r in rep.rows[name]:
                if r.region not in ("session", "no_window"):
                    continue
                v = getattr(r.cand, k, None) if hasattr(r.cand, k) else r.ctx.get(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    vals.append(float(v))
            if vals:
                cells.append(f"N={len(vals)}: " + " / ".join(
                    f"{quantile(vals, q):.2f}" for q in (0, .25, .5, .75, 1)))
            else:
                cells.append("absent")
        add(f"| {k} | " + " | ".join(cells) + " |")
    for name in (PRESET_R, PRESET_L):
        rr = [r for r in rep.rows[name] if r.region in ("session", "no_window") and r.cand.railed]
        add(f"")
        add(f"- {name} railed (≥ {candgen.PRESETS[name].rail_g:g} g normalised): "
            f"{len(rr)}" + (" — " + ", ".join(f"{r.cand.land_peak_g:.1f} g" for r in rr)
                             if rr else ""))
    add("")

    # 4. riding context
    add("## 4. Riding context")
    add("")
    if rep.window is None or not rep.ctx.fit_t:
        add("Did not run: " + ("no session window" if rep.window is None else
                               f"no FIT speed records ({rep.ctx.garmin_reason})")
            + ". Under-4-kn and stop counts are absent, not zero.")
    else:
        lo, hi = rep.window
        mins = candctx.riding_minutes_by_bin(rep.ctx, lo, hi)
        add(f"Riding minutes: each FIT record holds its speed until the next, capped "
            f"at {candctx.RECORD_HOLD_CAP_S:g} s (ASSUMED, so an auto-pause gap is not "
            f"counted as riding).")
        add("")
        add(f"| kn_before bin | riding min | {PRESET_R} | {PRESET_L} |")
        add("|---|---|---|---|")
        for b in range(len(candctx.SPEED_BINS_KN)):
            cnt = {}
            for name in (PRESET_R, PRESET_L):
                cnt[name] = sum(1 for r in rep.rows[name] if r.region == "session"
                                and r.ctx.get("kn_before") is not None
                                and candctx.speed_bin(r.ctx["kn_before"]) == b)
            add(f"| {candctx.bin_label(b)} | {mins[b]:.1f} | {cnt[PRESET_R]} | {cnt[PRESET_L]} |")
        na = {n: sum(1 for r in rep.rows[n] if r.region == "session"
                     and r.ctx.get("kn_before") is None) for n in (PRESET_R, PRESET_L)}
        add(f"| kn_before absent | – | {na[PRESET_R]} | {na[PRESET_L]} |")
        add("")
        for name in (PRESET_R, PRESET_L):
            sess_rows = [r for r in rep.rows[name] if r.region == "session"]
            under = [r for r in sess_rows if r.ctx.get("kn_before") is not None
                     and r.ctx["kn_before"] < UNDER_KN]
            st = [r for r in sess_rows if r.ctx.get("stopped_after") is True]
            sa = [r for r in sess_rows if r.ctx.get("stopped_after") is None]
            add(f"- {name}: {len(under)} candidate(s) under {UNDER_KN:g} kn before the pop"
                + (" (" + ", ".join(f"{_when(rep, r)} at {r.ctx['kn_before']:.1f} kn"
                                    for r in under) + ")" if under else "")
                + f"; {len(st)} of {len(sess_rows)} followed by a stop "
                  f"(≤ {candctx.STOPPED_KN:g} kn within 20 s of landing)"
                + (f", {len(sa)} with no speed after" if sa else ""))
        tk = candctx.tack_minutes(rep.ctx, lo, hi)
        if tk is None:
            add(f"- tack balance: did not run ({rep.ctx.wind_reason or 'no wind'})")
        else:
            l_, r_, n_ = tk
            side = {n: (sum(1 for r in rep.rows[n] if r.region == "session"
                            and (r.ctx.get("rel_wind_deg") or 0) > 0),
                        sum(1 for r in rep.rows[n] if r.region == "session"
                            and r.ctx.get("rel_wind_deg") is not None
                            and r.ctx["rel_wind_deg"] <= 0))
                    for n in (PRESET_R, PRESET_L)}
            add(f"- tack balance at ≥ {candctx.RIDING_KN:g} kn: wind from the rider's "
                f"left {l_:.1f} min / right {r_:.1f} min ({n_:.1f} min unheaded); "
                f"{PRESET_R} candidates left/right {side[PRESET_R][0]}/{side[PRESET_R][1]}, "
                f"{PRESET_L} {side[PRESET_L][0]}/{side[PRESET_L][1]}.")
    add("")

    # 5. Surfr
    add("## 5. Surfr")
    add("")
    if rep.match is None:
        note = rep.surfr_note[:1].upper() + rep.surfr_note[1:]
        add(note if note.endswith(".") else note + ".")
        add("")
    else:
        add(f"- {rep.ctx.tz_lines[0]}" if rep.ctx.tz_lines else
            "- UTC offset: absent")
        def lbl(t):
            loc = rep.ctx.t_to_local(t)
            return loc.strftime("%H:%M:%S") if loc else f"t={t:.2f}"
        L.extend(surfr_match.render(rep.match, lambda t: f"{lbl(t)} ({t:.2f})"))

    # 6. cannot conclude
    add("## 6. What this cannot conclude")
    add("")
    for b in CANNOT_CONCLUDE:
        add(f"- {b}")
    if rep.mount_id == "UNKNOWN":
        add("- **The mount is UNKNOWN** for this session (no usable mount.json). "
            "Nothing here says whether this is torso or board data.")
    if rep.match is not None and rep.match.ran and rep.match.mode == "gap":
        add(f"- **{len(rep.match.rows)} transcribed Surfr row(s) cannot establish an "
            "offset, a match, or a height relationship.** With fewer than 5 rows "
            "the free-parameter budget is zero.")
    if rep.ctx.wind_t == []:
        add(f"- No wind direction ({rep.ctx.wind_reason}): `rel_wind` is absent.")
    add("")
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------- cli

def run_session(sess: Path, write: bool = True) -> tuple[int, str]:
    rep = analyse(sess)
    if rep is None:
        return 1, f"!! {sess}: no usable trace.csv — skipped (a finding, not a pass)"
    md = render_md(rep)
    if write:
        write_csv(rep, sess / "candidates.csv")
        (sess / "candidates.md").write_text(md)
    rc = 1 if rep.findings else 0
    nR = sum(1 for r in rep.rows[PRESET_R] if r.region in ("session", "no_window"))
    summary = (f"{sess.name}: {nR} candidates ({PRESET_R})"
               + (f"; {len(rep.findings)} FINDING(s)" if rep.findings else ""))
    return rc, md + "\n" + summary


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="jump candidates",
        description="Generate CG-1 pop/unload/landing candidates for a session, add "
                    "speed/heading/wind context, match transcribed Surfr rows by "
                    "time, and write <session>/candidates.md + candidates.csv.")
    ap.add_argument("sessions", nargs="+", type=Path, help="session directories")
    ap.add_argument("--no-write", action="store_true",
                    help="print candidates.md but write nothing")
    args = ap.parse_args(argv)
    rc = 0
    for sess in args.sessions:
        code, text = run_session(Path(sess), write=not args.no_write)
        print(text)
        if not args.no_write and code != 2 and (Path(sess) / "candidates.md").exists():
            print(f"wrote {Path(sess) / 'candidates.md'}")
        rc = max(rc, code)
    return rc


if __name__ == "__main__":
    sys.exit(main())
