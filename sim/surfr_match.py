#!/usr/bin/env python3
"""surfr_match.py — match Surfr's per-jump rows to CG-1 candidates by TIME.

Matching is by TIMESTAMP; a count agreement is reported nowhere in this
module's output. `jumps_total` is never read here: a test sets it to
anything, or deletes it, and checks that every output stays byte-identical.

**Parameters must never be chosen by comparing a candidate COUNT with Surfr's
`jumps_total`.** A setting picked because its count matches is fitted to the
very number it would then be "validated" against. It fixes an operating
point; it does not validate one. Parameters may be selected only from
per-jump timestamp matches (this module) on sessions **held out** from the
evaluation, and never on a session with fewer than 5 matched rows.

TIME MODEL (spec section 4.2)
-----------------------------
Surfr row i sits at trace time s_i = t(start) + t_into_i + delta, with ONE
per-session offset delta shared by every row. delta absorbs the minute-only
display of the session start, phone-vs-GPS clock error, and Surfr's unknown
definition of a jump's timestamp (takeoff, apex or landing) — which is also
why a candidate is an INTERVAL [pop_t, land_t] and the residual is the
distance from s_i to it (0 inside).

PRIOR ON delta (spec section 4.3, ASSUMED with reasons)
-------------------------------------------------------
truncate: [-5, +65] s   [0, 60) for minute truncation, +-5 s clock/event
round:    [-35, +35] s  +-30 s for rounding, +-5 s
unknown:  [-35, +65] s  the union (today's files)
A fitted delta on a prior boundary is a FINDING, not a result.

FEWER THAN 5 PLACEABLE ROWS: no delta is fitted. Each row lists every
candidate inside its prior window, and each consecutive row pair gets a GAP
TEST — candidate pairs whose pop-time difference equals the Surfr
difference within +-2 s, which is independent of delta.

CHANCE LEVEL, ALWAYS: the slide null. Keep the rows' relative times, slide
the pattern to every 1 s position in the session window whose shift from
the nominal placement exceeds 120 s, rerun the same test there, and report
p_slide = the fraction of slides doing at least as well (by matched-row
count, or by gap-consistent pairs under 5 rows). The slide is CIRCULAR
within the session window — a row pushed past its end re-enters at its
start — so a pattern nearly as long as the session still has slides, and
no slide lands rows in unlogged time where nothing can match (which would
make chance look rarer than it is). This uses the session's own candidate
clustering, which a uniform-density null would ignore.
"""

from __future__ import annotations

import bisect
import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "sim") not in sys.path:
    sys.path.insert(0, str(REPO / "sim"))

from score import parse_t_into_session  # noqa: E402

SCHEMA = "surfr-v1"
TAU_S = 1.5              # ASSUMED: Surfr's 1 s display resolution + 0.5 s
GAP_TOL_S = 2.0          # each t_into has 1 s resolution
MIN_FIT_ROWS = 5         # same "roughly 5+ rows" as sim/score.py's honesty text
SLIDE_MIN_SHIFT_S = 120.0
SLIDE_STEP_S = 1.0
DELTA_STEP_S = 0.1
PRIORS = {"truncate": (-5.0, 65.0), "round": (-35.0, 35.0), "unknown": (-35.0, 65.0)}

VERDICT_ESTABLISHED = "established at session level"
VERDICT_SUGGESTIVE = "suggestive"
VERDICT_CHANCE = "not distinguishable from chance"


@dataclass
class SurfrRow:
    n: object
    t_into_s: float
    t_into_raw: str
    height_ft: Optional[float]
    airtime_s: Optional[float]
    distance_ft: Optional[float]


@dataclass
class Cand:
    """What the matcher needs of a candidate. `extra` is carried to the
    rendered table untouched (speeds, unload, ...)."""
    cid: int
    pop: float
    land: float
    extra: dict = field(default_factory=dict)


def validate(surfr: dict) -> tuple[list[SurfrRow], list[str]]:
    """(placeable rows, FINDING lines). Unparseable rows are counted and
    named, never silently dropped."""
    findings: list[str] = []
    schema = surfr.get("schema", SCHEMA)
    if schema != SCHEMA:
        findings.append(f"FINDING: surfr.json schema {schema!r} is not {SCHEMA!r}; "
                        "read as v1 anyway")
    rows: list[SurfrRow] = []
    seen: dict = {}
    dur = surfr.get("duration_s")
    for k, r in enumerate(surfr.get("rows") or [], start=1):
        if not isinstance(r, dict):
            findings.append(f"FINDING: surfr.json row #{k} is not an object — not placed")
            continue
        n = r.get("n", f"#{k}")
        if n in seen:
            findings.append(f"FINDING: surfr.json row n={n} appears more than once "
                            f"(rows #{seen[n]} and #{k})")
        else:
            seen[n] = k
        t = parse_t_into_session(r.get("t_into_session"))
        if t is None:
            findings.append(f"FINDING: surfr.json row n={n} has no parseable "
                            f"t_into_session ({r.get('t_into_session')!r}) — not placed")
            continue
        if t < 0 or (isinstance(dur, (int, float)) and t > float(dur)):
            findings.append(f"FINDING: surfr.json row n={n} t_into_session "
                            f"{r.get('t_into_session')} lies outside [0, duration_s="
                            f"{dur}] s")

        def num(key):
            v = r.get(key)
            return float(v) if isinstance(v, (int, float)) else None
        rows.append(SurfrRow(n=n, t_into_s=t, t_into_raw=str(r.get("t_into_session")),
                             height_ft=num("height_ft"), airtime_s=num("airtime_s"),
                             distance_ft=num("distance_ft")))
    # chronological by n
    numbered = [(r.n, r.t_into_s) for r in rows if isinstance(r.n, (int, float))]
    numbered.sort(key=lambda x: x[0])
    for (n0, t0), (n1, t1) in zip(numbered, numbered[1:]):
        if t1 < t0:
            findings.append(f"FINDING: surfr.json rows are not chronological by n "
                            f"(n={n0} at {t0:.0f} s, n={n1} at {t1:.0f} s)")
            break
    rows.sort(key=lambda r: r.t_into_s)
    return rows, findings


def _dist(s: float, c: Cand) -> float:
    if s < c.pop:
        return s - c.pop
    if s > c.land:
        return s - c.land
    return 0.0


class _Index:
    def __init__(self, cands: Sequence[Cand]):
        self.c = sorted(cands, key=lambda c: c.pop)
        self.pops = [c.pop for c in self.c]
        self.maxlen = max((c.land - c.pop for c in self.c), default=0.0)

    def near(self, lo: float, hi: float) -> list[int]:
        """Indices of candidates whose interval intersects [lo, hi]."""
        j1 = bisect.bisect_right(self.pops, hi)
        j0 = bisect.bisect_left(self.pops, lo - self.maxlen)
        return [j for j in range(j0, j1) if self.c[j].land >= lo]


def _assign(pos: Sequence[float], idx: _Index, tau: float):
    """Monotone one-to-one assignment of rows (ascending pos) to candidates:
    maximise matches, then minimise the sum of |residual|. Sparse chain DP
    over the edges within tau. Returns (n_matched, sum_abs_res, pairs)."""
    edges = []
    for i, s in enumerate(pos):
        for j in idx.near(s - tau, s + tau):
            r = _dist(s, idx.c[j])
            if abs(r) <= tau:
                edges.append((i, j, r))
    if not edges:
        return 0, 0.0, []
    best = []   # per edge: (matches, -sum, prev)
    for e, (i, j, r) in enumerate(edges):
        top = (1, -abs(r), -1)
        for f in range(e):
            i2, j2, _ = edges[f]
            if i2 < i and j2 < j:
                m, sres, _p = best[f]
                cand = (m + 1, sres - abs(r), f)
                if (cand[0], cand[1]) > (top[0], top[1]):
                    top = cand
        best.append(top)
    e = max(range(len(edges)), key=lambda k: (best[k][0], best[k][1]))
    m, sres = best[e][0], -best[e][1]
    pairs = []
    while e >= 0:
        pairs.append(edges[e])
        e = best[e][2]
    pairs.reverse()
    return m, sres, pairs


@dataclass
class GapPair:
    row_a: object
    row_b: object
    surfr_gap_s: float
    cid_a: int
    cid_b: int
    pop_gap_s: float
    land_gap_s: float
    implied_delta_s: float
    residual_b_s: float


@dataclass
class MatchResult:
    ran: bool
    reason: str = ""
    findings: list[str] = field(default_factory=list)
    rows: list[SurfrRow] = field(default_factory=list)
    rows_complete: bool = False
    rounding: str = "unknown"
    prior: tuple[float, float] = PRIORS["unknown"]
    start_t: Optional[float] = None
    window: Optional[tuple[float, float]] = None
    mode: str = ""                       # "fit" | "gap"
    # fit mode
    delta_s: Optional[float] = None
    plateau: Optional[tuple[float, float]] = None
    plateau_n: int = 0
    matched: int = 0
    pairs: list[tuple[SurfrRow, Cand, float]] = field(default_factory=list)
    unmatched_rows: list[tuple[SurfrRow, Optional[Cand], Optional[float]]] = \
        field(default_factory=list)
    unmatched_cands_in: list[Cand] = field(default_factory=list)
    n_cands_outside_surfr: int = 0
    # gap mode
    prior_windows: list[tuple[SurfrRow, float, float, list[Cand]]] = \
        field(default_factory=list)
    gap_pairs: list[GapPair] = field(default_factory=list)
    gap_consistent_row_pairs: int = 0
    # null
    stat: float = 0.0
    p_slide: Optional[float] = None
    n_slides: int = 0
    verdict: str = VERDICT_CHANCE


def _placeable(c: Cand, lo: float, hi: float, tau: float) -> bool:
    return c.land >= lo - tau and c.pop <= hi + tau


def _gap_stat(base: Sequence[float], rows: Sequence[SurfrRow], idx: _Index,
              prior: tuple[float, float], tau: float, tol: float,
              collect: bool = False):
    """Number of consecutive row pairs with a gap-consistent candidate pair
    inside their prior windows (and the pairs, if collect)."""
    d_lo, d_hi = prior
    found = 0
    out: list[GapPair] = []
    for k in range(len(rows) - 1):
        a_lo, a_hi = base[k] + d_lo, base[k] + d_hi
        b_lo, b_hi = base[k + 1] + d_lo, base[k + 1] + d_hi
        ca = [idx.c[j] for j in idx.near(a_lo - tau, a_hi + tau)]
        cb = [idx.c[j] for j in idx.near(b_lo - tau, b_hi + tau)]
        gap = rows[k + 1].t_into_s - rows[k].t_into_s
        hit = False
        for x in ca:
            for y in cb:
                if y.cid == x.cid or y.pop <= x.pop:
                    continue
                if abs((y.pop - x.pop) - gap) <= tol:
                    hit = True
                    if collect:
                        dlt = x.pop - base[k]
                        out.append(GapPair(
                            row_a=rows[k].n, row_b=rows[k + 1].n, surfr_gap_s=gap,
                            cid_a=x.cid, cid_b=y.cid, pop_gap_s=y.pop - x.pop,
                            land_gap_s=y.land - x.land, implied_delta_s=dlt,
                            residual_b_s=y.pop - (base[k + 1] + dlt)))
        found += 1 if hit else 0
    return found, out


def _wrap(x: float, lo: float, w: float) -> float:
    return lo + ((x - lo) % w)


def match(surfr: dict, start_t: Optional[float], cands: Sequence[Cand],
          window: Optional[tuple[float, float]], *, tau: float = TAU_S,
          gap_tol: float = GAP_TOL_S) -> MatchResult:
    """Run the matcher. `start_t` is the trace time of Surfr's displayed
    session start; `window` the session window the slide null runs in.
    Never reads `jumps_total`."""
    rows, findings = validate(surfr)
    rounding = surfr.get("start_rounding") or "unknown"
    if rounding not in PRIORS:
        findings.append(f"FINDING: start_rounding {rounding!r} is not one of "
                        f"{sorted(PRIORS)}; the 'unknown' prior is used")
        rounding = "unknown"
    prior = PRIORS[rounding]
    res = MatchResult(ran=False, findings=findings, rows=rows,
                      rows_complete=surfr.get("rows_complete") is True,
                      rounding=rounding, prior=prior, start_t=start_t, window=window)
    if start_t is None:
        res.reason = "the Surfr start cannot be placed on the trace clock"
        return res
    if not rows:
        res.reason = "no placeable Surfr rows"
        return res
    if window is None:
        res.reason = "no session window for the slide null"
        return res
    lo, hi = window
    W = hi - lo
    in_win = [c for c in cands if c.land >= lo and c.pop <= hi]
    idx = _Index(in_win)
    base = [start_t + r.t_into_s for r in rows]
    res.ran = True

    slides = [d for d in (k * SLIDE_STEP_S for k in range(int(W // SLIDE_STEP_S) + 1))
              if min(d, W - d) > SLIDE_MIN_SHIFT_S and d < W]
    res.n_slides = len(slides)

    if len(rows) < MIN_FIT_ROWS:
        res.mode = "gap"
        d_lo, d_hi = prior
        for r, b in zip(rows, base):
            res.prior_windows.append(
                (r, b + d_lo, b + d_hi,
                 [idx.c[j] for j in idx.near(b + d_lo - tau, b + d_hi + tau)]))
        stat, gp = _gap_stat(base, rows, idx, prior, tau, gap_tol, collect=True)
        res.gap_pairs = gp
        res.gap_consistent_row_pairs = stat
        res.stat = stat
        if len(rows) < 2:
            res.p_slide = None
            res.verdict = VERDICT_CHANCE
            res.findings.append("one placeable row: no gap test is possible, and "
                                "no offset can be fitted")
            return res
        ge = 0
        for d in slides:
            sb = [_wrap(b + d, lo, W) for b in base]
            s2, _ = _gap_stat(sb, rows, idx, prior, tau, gap_tol)
            ge += 1 if s2 >= stat else 0
        res.p_slide = ge / len(slides) if slides else None
        res.verdict = _verdict(0, res.p_slide)   # < 5 rows can never be "established"
        return res

    # ----- fit mode: delta over the prior grid, 0.1 s steps
    res.mode = "fit"
    d_lo, d_hi = prior
    n_steps = int(round((d_hi - d_lo) / DELTA_STEP_S))
    deltas = [d_lo + k * DELTA_STEP_S for k in range(n_steps + 1)]
    scored = []
    for dlt in deltas:
        m, sres, pairs = _assign([b + dlt for b in base], idx, tau)
        scored.append((m, -sres, dlt, pairs))
    best = max(scored, key=lambda x: (x[0], x[1]))
    m, nsres, dlt, pairs = best
    res.delta_s = dlt
    res.matched = m
    same = [x[2] for x in scored if x[0] == m]
    res.plateau = (min(same), max(same))
    res.plateau_n = len(same)
    if abs(dlt - d_lo) < 1e-9 or abs(dlt - d_hi) < 1e-9:
        res.findings.append(
            f"FINDING: the fitted offset {dlt:+.1f} s lies ON the prior boundary "
            f"[{d_lo:+g}, {d_hi:+g}] s — the true offset may lie outside the prior; "
            "this is not a result")
    matched_rows = set()
    matched_c = set()
    for i, j, r in pairs:
        res.pairs.append((rows[i], idx.c[j], r))
        matched_rows.add(i)
        matched_c.add(j)
    for i, r in enumerate(rows):
        if i in matched_rows:
            continue
        s = base[i] + dlt
        if idx.c:
            j = min(range(len(idx.c)), key=lambda k: abs(_dist(s, idx.c[k])))
            res.unmatched_rows.append((r, idx.c[j], _dist(s, idx.c[j])))
        else:
            res.unmatched_rows.append((r, None, None))
    s_lo = start_t + dlt
    s_hi = start_t + dlt + float(surfr.get("duration_s") or 0.0)
    for j, c in enumerate(idx.c):
        if j in matched_c:
            continue
        if surfr.get("duration_s") and c.land >= s_lo and c.pop <= s_hi:
            res.unmatched_cands_in.append(c)
        else:
            res.n_cands_outside_surfr += 1
    res.stat = m
    # Slide null: the same fit at every circular slide. Under a circular
    # slide every row's position depends only on x = (slide + offset) mod W,
    # so the matched count F(x) is computed once per 0.1 s grid point and
    # each slide's best fit is the max of F over its prior range.
    N = max(1, int(round(W / DELTA_STEP_S)))
    F: dict = {}

    def f_at(k: int) -> int:
        k %= N
        got = F.get(k)
        if got is None:
            x = k * DELTA_STEP_S
            got = _assign(sorted(_wrap(b + x, lo, W) for b in base), idx, tau)[0]
            F[k] = got
        return got

    k_lo = int(round(d_lo / DELTA_STEP_S))
    k_hi = int(round(d_hi / DELTA_STEP_S))
    ge = 0
    for d in slides:
        kd = int(round(d / DELTA_STEP_S))
        bm = 0
        for k in range(kd + k_lo, kd + k_hi + 1):
            v = f_at(k)
            if v > bm:
                bm = v
                if bm >= m:
                    break
        ge += 1 if bm >= m else 0
    res.p_slide = ge / len(slides) if slides else None
    res.verdict = _verdict(m, res.p_slide)
    return res


def _verdict(matched: int, p: Optional[float]) -> str:
    if p is None:
        return VERDICT_CHANCE
    if matched >= MIN_FIT_ROWS and p < 0.01:
        return VERDICT_ESTABLISHED
    if p < 0.10:
        return VERDICT_SUGGESTIVE
    return VERDICT_CHANCE


# ------------------------------------------------------------------ render

def _f(v, fmt="{:.2f}", absent="absent"):
    return absent if v is None else fmt.format(v)


def render(res: MatchResult, t_label=lambda t: f"{t:.2f} s") -> list[str]:
    """Section 5 of candidates.md. Prints no count comparison: only rows,
    candidates, times, residuals, the null and the verdict."""
    L: list[str] = []
    add = L.append
    for f in res.findings:
        add(f"- {f}")
    if res.findings:
        add("")
    if not res.ran:
        add(f"Did not run: {res.reason}.")
        add("")
        return L
    add(f"- placeable rows: {len(res.rows)}; start rounding `{res.rounding}`, "
        f"so the prior on the offset is [{res.prior[0]:+g}, {res.prior[1]:+g}] s; "
        f"tolerance {TAU_S:g} s; candidates are the **vest-L** superset; "
        f"matching is by TIMESTAMP.")
    if not res.rows_complete:
        add("- `rows_complete` is not true: an unmatched candidate has **no "
            "transcribed row**; it cannot be called anything else.")
    add("")
    if res.mode == "gap":
        add(f"**No offset fitted: {len(res.rows)} row(s) < {MIN_FIT_ROWS}.** "
            "Every candidate inside each row's prior window is listed, so the "
            "ambiguity is explicit.")
        add("")
        add("| Surfr n | t_into | height | airtime | prior window (trace t) | candidates in it (id: pop → land) |")
        add("|---|---|---|---|---|---|")
        for r, a, b, cs in res.prior_windows:
            lst = "; ".join(f"{c.cid}: {c.pop:.2f} → {c.land:.2f}" for c in cs) or "none"
            add(f"| {r.n} | {r.t_into_raw} | {_f(r.height_ft, '{:.2f} ft')} | "
                f"{_f(r.airtime_s, '{:.2f} s')} | {a:.1f} .. {b:.1f} | {lst} |")
        add("")
        add(f"**Gap test** (independent of the offset): candidate pairs whose "
            f"pop-time difference equals the Surfr difference within ±{GAP_TOL_S:g} s.")
        add("")
        if res.gap_pairs:
            add("| rows | Surfr gap | candidates | pop gap | land gap | implied offset | 2nd row residual (pop) |")
            add("|---|---|---|---|---|---|---|")
            for g in res.gap_pairs:
                add(f"| {g.row_a} → {g.row_b} | {g.surfr_gap_s:.0f} s | "
                    f"{g.cid_a} → {g.cid_b} | {g.pop_gap_s:.2f} s | {g.land_gap_s:.2f} s | "
                    f"{g.implied_delta_s:+.1f} s | {g.residual_b_s:+.2f} s |")
        else:
            add("No gap-consistent candidate pair.")
        add("")
        add(f"- consecutive row pairs with a gap-consistent candidate pair: "
            f"{res.gap_consistent_row_pairs} of {max(0, len(res.rows) - 1)}")
    else:
        add(f"- fitted offset: **{res.delta_s:+.1f} s**; plateau (offsets reaching "
            f"the same {res.matched} matched rows): {res.plateau[0]:+.1f} .. "
            f"{res.plateau[1]:+.1f} s ({res.plateau_n} grid points of "
            f"{DELTA_STEP_S:g} s)")
        add(f"- matched rows: {res.matched} of {len(res.rows)}")
        add("")
        add("**Matched pairs**")
        add("")
        if res.pairs:
            keys = sorted({k for _, c, _ in res.pairs for k in c.extra})
            add("| Surfr n | t_into | height | airtime | cand | pop | land | residual | "
                + " | ".join(keys) + " |")
            add("|" + "---|" * (8 + len(keys)))
            for r, c, rr in res.pairs:
                add(f"| {r.n} | {r.t_into_raw} | {_f(r.height_ft, '{:.2f} ft')} | "
                    f"{_f(r.airtime_s, '{:.2f} s')} | {c.cid} | {t_label(c.pop)} | "
                    f"{t_label(c.land)} | {rr:+.2f} s | "
                    + " | ".join(str(c.extra.get(k, '')) for k in keys) + " |")
        else:
            add("None.")
        add("")
        add("**Surfr rows with no candidate**")
        add("")
        if res.unmatched_rows:
            for r, c, d in res.unmatched_rows:
                near = (f"nearest candidate {c.cid} at {d:+.2f} s" if c is not None
                        else "no candidate at all")
                add(f"- row {r.n} ({r.t_into_raw}): {near}")
        else:
            add("None.")
        add("")
        title = ("Candidates with no Surfr row (possible false positives — the "
                 "transcription is marked complete)" if res.rows_complete
                 else "Candidates with no transcribed row")
        add(f"**{title}**, inside the Surfr window only; "
            f"{res.n_cands_outside_surfr} more are out of Surfr coverage.")
        add("")
        if res.unmatched_cands_in:
            add(", ".join(f"{c.cid} ({t_label(c.pop)})" for c in res.unmatched_cands_in))
        else:
            add("None.")
    add("")
    p = "not computed" if res.p_slide is None else f"{res.p_slide:.3f}"
    add(f"- slide null: {res.n_slides} circular 1 s slides (shift > "
        f"{SLIDE_MIN_SHIFT_S:g} s) of the row pattern within the session window; "
        f"p_slide = {p} (fraction of slides doing at least as well).")
    add(f"- **verdict: {res.verdict}.** This is a statement about timing "
        "alignment, not an accuracy verdict.")
    add("")
    return L


def load_surfr_strict(path: Path) -> tuple[Optional[dict], Optional[str]]:
    """(surfr dict, None) or (None, FINDING reason). Unlike score.load_surfr,
    an unreadable file is reported, not read as absent."""
    if not path.exists():
        return None, None
    try:
        d = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        return None, f"FINDING: surfr.json is unreadable ({exc.__class__.__name__}: {exc})"
    if not isinstance(d, dict):
        return None, "FINDING: surfr.json is not a JSON object"
    return d, None
