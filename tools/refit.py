#!/usr/bin/env python3
"""tools/refit.py — per-ride refit proposal (docs/accuracy-plan.md).

Runs sim/detector.py's real `Detector` over every session's `trace.csv` for a
grid of the three detection-window thresholds, scores each grid point against
that session's `surfr.json` (where one exists), and proposes a single
replacement for `config/params.json`'s current `freefall_enter_g`,
`max_airtime_s`, `min_airtime_s` — or says to keep what's there.

    ./tools/refit.py                  # scan data/sessions/, print + write data/refit.md
    ./tools/refit.py --root DIR       # a different sessions root (tests use this)
    ./tools/refit.py --no-write       # print only, don't touch --out

THIS TOOL NEVER WRITES config/params.json OR FIRMWARE. It proposes; a human
decides. (CLAUDE.md rule 2: no verdict without a measurement — the verdict
line below is exactly that measurement, not a recommendation to act on it.)

## Grid

    freefall_enter_g in 0.30 .. 0.80 step 0.05   (11 values)
    max_airtime_s    in {3, 4, 5, 6}              (4 values)
    min_airtime_s    in {0.25, 0.35, 0.45}        (3 values)

132 combinations total. Every other Params field (g, freefall_confirm_s,
landing_threshold_g, landing_settle_s, airtime_offset_s, height_scale,
spin_lever_m) is held at whatever `config/params.json` says — this tool
does not touch those.

## Scoring

A session only scores what its `surfr.json` actually carries:
  - `jumps_total`     -> count error   = |detected count - jumps_total|
  - `best_height_ft`  -> height error  = |detected best height (ft) - best_height_ft|
  - `max_airtime_s`   -> airtime error = |detected longest airtime (s) - max_airtime_s|
A session with no `surfr.json`, or one missing a given field, contributes
NOTHING to that metric — never a manufactured zero (CLAUDE.md rule 3).

## Leave-one-session-out

For each session that has a Surfr `jumps_total` ("count session"), fit on
every OTHER count session (pick the grid point minimizing summed count error
over them) and report that fit's count error on the held-out session. This is
the honest generalization check: with N count sessions there are N folds, and
each fold's chosen combo never sees the session it is scored on. It is
reported for the record; it is NOT what gets proposed (see below).

## The proposed combination

Fit on ALL count sessions at once (not leave-one-out) — the single grid point
minimizing summed count error across every count session — because a
proposal for `config/params.json` has to be one set of numbers, and this
tool's job is to recommend the best use of ALL currently available evidence,
with the leave-one-out numbers alongside it as the check on whether it
generalizes.

Ties (equal summed count error) are broken, in order: (1) smallest change
from the CURRENT config values (fewest/smallest threshold moves — a
conservative default, not something the task specifies); (2) grid iteration
order, for a fully deterministic result. Both are ASSUMED, not measured, and
are named here so the choice is auditable.

## Free-parameter budget (docs/accuracy-plan.md: "Never more free parameters
## than sessions / 3.")

Three grid dimensions are being fit. With fewer than 9 count sessions that
budget is already exceeded; the report says so plainly rather than letting a
clean verdict line imply more confidence than the corpus supports.

## Verdict

    PROPOSE  iff  count error strictly improves on EVERY count session
                  AND best-height error does not get worse on any session
                  that has a Surfr best_height_ft
    KEEP     otherwise

Longest-airtime error is computed and reported but does not gate the verdict
— the task that specifies this rule names count error and best-height error
only.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "sim"))

from detector import Params, load_params  # noqa: E402
from run import load_csv, run_detector  # noqa: E402

DEFAULT_CONFIG = REPO / "config" / "params.json"
DEFAULT_SESSIONS_ROOT = REPO / "data" / "sessions"
DEFAULT_OUT = REPO / "data" / "refit.md"

# Same constant sim/run.py hardcodes for the same reason (config/params.json's
# "shared.m_to_ft", generated into two other languages — see that file's
# comment on why it is not re-parsed from JSON here either): one more copy of
# a value that already has a documented multi-language duplication problem
# would not make it safer, only harder to find.
M_TO_FT = 3.28084

GRID_FREEFALL_ENTER_G: List[float] = [round(0.30 + 0.05 * i, 2) for i in range(11)]
GRID_MAX_AIRTIME_S: List[float] = [3.0, 4.0, 5.0, 6.0]
GRID_MIN_AIRTIME_S: List[float] = [0.25, 0.35, 0.45]

ComboKey = Tuple[float, float, float]


def combo_key(p: Params) -> ComboKey:
    return (round(p.freefall_enter_g, 2), p.max_airtime_s, p.min_airtime_s)


def format_combo(p: Params) -> str:
    return (f"freefall_enter_g={p.freefall_enter_g:g}, "
            f"max_airtime_s={p.max_airtime_s:g}, "
            f"min_airtime_s={p.min_airtime_s:g}")


def build_grid(base: Params) -> List[Params]:
    """All 132 combinations, holding every other field at `base`'s value.
    Order is deterministic — g outer, then max_airtime_s, then min_airtime_s —
    and is the grid-order tie-break of last resort in `pick_best`.

    Plus `base` itself, appended LAST, whenever the current config sits off
    the grid. `run()` reads the "before" column out of the grid results at
    combo_key(base) — with an off-grid current config (min_airtime_s=0.30,
    say, or any freefall_enter_g outside 0.30..0.80) that lookup is a
    KeyError and the whole tool dies with a traceback instead of a report.
    Off-grid is not exotic: this tool exists to be re-run after somebody
    hand-tunes config/params.json. Appending it last keeps grid order (the
    last-resort tie-break) unchanged for the 132, and pick_best's distance
    tie-break already puts base first among equals on its own merits."""
    grid = []
    for g in GRID_FREEFALL_ENTER_G:
        for m in GRID_MAX_AIRTIME_S:
            for mn in GRID_MIN_AIRTIME_S:
                grid.append(dataclasses.replace(
                    base, freefall_enter_g=g, max_airtime_s=m, min_airtime_s=mn))
    if combo_key(base) not in {combo_key(p) for p in grid}:
        grid.append(base)
    return grid


# --------------------------------------------------------------------- data

@dataclasses.dataclass
class SessionData:
    name: str
    path: Path
    times: List[float]
    mag: List[float]
    surfr: Optional[dict]


def load_surfr(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def discover_sessions(root: Path) -> List[SessionData]:
    """Every DIRECT child of `root` holding a trace.csv — `data/sessions/*/`,
    literally. This deliberately does NOT recurse the way sim/evaluate.py does
    for labels.csv: the grouping directories it recurses into
    (jitter-check/, walk-overnight/) hold no trace.csv at their own level, so
    a flat scan already excludes them without needing to know their names.

    Each trace.csv is parsed exactly once here (`load_csv`) — this IS the
    "cache parsed traces" step: every combo in the grid reuses these same
    `times`/`mag` lists rather than re-reading the file.
    """
    sessions: List[SessionData] = []
    if not root.exists():
        return sessions
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        trace_path = child / "trace.csv"
        if not trace_path.exists():
            continue
        times, mag = load_csv(str(trace_path))
        surfr = load_surfr(child / "surfr.json")
        sessions.append(SessionData(name=child.name, path=child,
                                    times=times, mag=mag, surfr=surfr))
    return sessions


# ---------------------------------------------------------------- detection

@dataclasses.dataclass
class DetectionSummary:
    count: int
    best_height_m: Optional[float]     # None iff count == 0 — no jump means no apex
    longest_airtime_s: Optional[float]  # None iff count == 0


def run_combo(sess: SessionData, params: Params) -> DetectionSummary:
    events = run_detector(sess.times, sess.mag, params)
    if not events:
        return DetectionSummary(count=0, best_height_m=None, longest_airtime_s=None)
    return DetectionSummary(
        count=len(events),
        best_height_m=max(e.height_m for e in events),
        longest_airtime_s=max(e.airtime_s for e in events),
    )


def evaluate_grid(sessions: Sequence[SessionData],
                  grid: Sequence[Params]) -> Dict[str, Dict[ComboKey, DetectionSummary]]:
    """summary[session.name][combo_key(p)] -> DetectionSummary, for every p in
    grid. This is the one place the actual per-sample cost lives; everything
    downstream is a dict lookup over already-computed results."""
    out: Dict[str, Dict[ComboKey, DetectionSummary]] = {}
    for sess in sessions:
        row: Dict[ComboKey, DetectionSummary] = {}
        for p in grid:
            row[combo_key(p)] = run_combo(sess, p)
        out[sess.name] = row
    return out


# -------------------------------------------------------------------- error

def count_error(summary: DetectionSummary, surfr: Optional[dict]) -> Optional[float]:
    if not surfr or "jumps_total" not in surfr:
        return None
    return abs(summary.count - surfr["jumps_total"])


def height_error_ft(summary: DetectionSummary, surfr: Optional[dict]) -> Optional[float]:
    if not surfr or "best_height_ft" not in surfr:
        return None
    if summary.best_height_m is None:
        return None  # no jump detected at all: there is no "detected height" to compare
    return abs(summary.best_height_m * M_TO_FT - surfr["best_height_ft"])


def airtime_error_s(summary: DetectionSummary, surfr: Optional[dict]) -> Optional[float]:
    if not surfr or "max_airtime_s" not in surfr:
        return None
    if summary.longest_airtime_s is None:
        return None
    return abs(summary.longest_airtime_s - surfr["max_airtime_s"])


# ----------------------------------------------------------------- fitting

def _distance_from_base(p: Params, base: Params) -> float:
    """Tie-break only (see module docstring): normalized by each dimension's
    own grid step, so a one-step move in any of the three counts the same."""
    return (abs(p.freefall_enter_g - base.freefall_enter_g) / 0.05
            + abs(p.max_airtime_s - base.max_airtime_s) / 1.0
            + abs(p.min_airtime_s - base.min_airtime_s) / 0.10)


def total_count_error(session_names: Sequence[str], p: Params,
                      grid_results: Dict[str, Dict[ComboKey, DetectionSummary]],
                      surfr_by_name: Dict[str, dict]) -> float:
    total = 0.0
    key = combo_key(p)
    for name in session_names:
        ce = count_error(grid_results[name][key], surfr_by_name[name])
        assert ce is not None, f"{name} has no jumps_total but was passed as a count session"
        total += ce
    return total


def pick_best(session_names: Sequence[str], grid: Sequence[Params], base: Params,
              grid_results: Dict[str, Dict[ComboKey, DetectionSummary]],
              surfr_by_name: Dict[str, dict]) -> Tuple[Params, float]:
    """The grid point minimizing summed count error over `session_names`.
    `session_names` must all have a Surfr `jumps_total`. Ties broken per the
    module docstring."""
    best_idx, best_p = min(
        enumerate(grid),
        key=lambda ip: (
            total_count_error(session_names, ip[1], grid_results, surfr_by_name),
            _distance_from_base(ip[1], base),
            ip[0],
        ),
    )
    return best_p, total_count_error(session_names, best_p, grid_results, surfr_by_name)


@dataclasses.dataclass
class LosoFold:
    held_out: str
    combo: Optional[Params]
    held_out_count_error: Optional[float]
    note: str = ""


def leave_one_session_out(count_names: Sequence[str], grid: Sequence[Params], base: Params,
                          grid_results: Dict[str, Dict[ComboKey, DetectionSummary]],
                          surfr_by_name: Dict[str, dict]) -> List[LosoFold]:
    folds: List[LosoFold] = []
    for held_out in count_names:
        others = [n for n in count_names if n != held_out]
        if not others:
            folds.append(LosoFold(held_out, None, None,
                                  note="no OTHER count session to fit on"))
            continue
        combo, _ = pick_best(others, grid, base, grid_results, surfr_by_name)
        ce = count_error(grid_results[held_out][combo_key(combo)], surfr_by_name[held_out])
        folds.append(LosoFold(held_out, combo, ce))
    return folds


# ------------------------------------------------------------------ verdict

def decide_verdict(surfr_names: Sequence[str],
                   before: Dict[str, DetectionSummary], after: Dict[str, DetectionSummary],
                   surfr_by_name: Dict[str, dict]) -> Tuple[str, List[str]]:
    """PROPOSE iff count error strictly improves on every session that has a
    Surfr count, and best-height error gets no worse on any session that has
    a Surfr best_height_ft. Equal count error is NOT an improvement — a tie
    buys nothing and the rule says "improves". Equal height error is fine —
    the rule says "gets worse", not "improves"."""
    reasons: List[str] = []
    count_ok = True
    height_ok = True
    for name in surfr_names:
        surfr = surfr_by_name[name]
        b_ce, a_ce = count_error(before[name], surfr), count_error(after[name], surfr)
        if b_ce is not None and a_ce is not None:
            if not (a_ce < b_ce):
                count_ok = False
                reasons.append(f"{name}: count error did not improve "
                               f"({b_ce:g} -> {a_ce:g})")
        b_he, a_he = height_error_ft(before[name], surfr), height_error_ft(after[name], surfr)
        if b_he is not None and a_he is not None:
            if a_he > b_he:
                height_ok = False
                reasons.append(f"{name}: best-height error got worse "
                               f"({b_he:.2f} -> {a_he:.2f} ft)")
    verdict = "PROPOSE" if (count_ok and height_ok) else "KEEP"
    if not reasons:
        reasons.append("every count session's count error strictly improved, "
                       "and no session's best-height error got worse")
    return verdict, reasons


# ------------------------------------------------------------------- report

def _fmt(x: Optional[float], nd: int = 2, unit: str = "") -> str:
    return "—" if x is None else f"{x:.{nd}f}{unit}"


def _fmt_int(x: Optional[int]) -> str:
    return "—" if x is None else str(x)


def render_report(*, config_path: Path, base: Params, sessions: List[SessionData],
                  surfr_sessions: List[SessionData], count_sessions: List[SessionData],
                  proposed: Params, proposed_total_ce: Optional[float],
                  before: Dict[str, DetectionSummary], after: Dict[str, DetectionSummary],
                  loso_folds: List[LosoFold], verdict: str, verdict_reasons: List[str]) -> str:
    surfr_by_name = {s.name: s.surfr for s in surfr_sessions}
    lines: List[str] = []
    a = lines.append

    a("# refit.md — per-ride refit proposal")
    a("")
    a("Generated by `./tools/refit.py` (`tools/refit.py`), per `docs/accuracy-plan.md`. "
      "Grid: `freefall_enter_g` 0.30..0.80 step 0.05 (11 values) x `max_airtime_s` "
      "{3,4,5,6} x `min_airtime_s` {0.25,0.35,0.45} = 132 combinations, run through "
      "the real `sim/detector.py` `Detector` — every other Params field held at the "
      f"`{config_path}` value. This tool does not edit `config/params.json` or "
      "firmware; it proposes.")
    a("")

    a("## Corpus")
    a("")
    a("Each combo runs against the WHOLE of `trace.csv`, in file order — no "
      "epoch alignment, no multi-boot segmentation, no Surfr/Garmin time "
      "window (that alignment work is `sim/score.py`'s, not this tool's). A "
      "session recorded across a power cycle (e.g. a flash ring buffer that "
      "survived a reboot) is scored as one continuous stream of samples, "
      "which is why a count here can differ from a windowed figure in that "
      "session's `score.md`.")
    a("")
    no_surfr = [s.name for s in sessions if s.surfr is None]
    a(f"- {len(sessions)} session(s) under scan have a `trace.csv`.")
    a(f"- {len(surfr_sessions)} of those carry a `surfr.json`; "
      f"{len(count_sessions)} of those have `jumps_total` (\"count sessions\" — "
      "the only ones that can be fit or checked on count).")
    if no_surfr:
        a(f"- {len(no_surfr)} session(s) have `trace.csv` but no `surfr.json` — "
          "no ground truth, so they contribute nothing below (not a 0, an absence): "
          + ", ".join(no_surfr))
    budget = len(count_sessions) / 3
    if budget < 3:
        a(f"- **FREE-PARAMETER BUDGET:** `docs/accuracy-plan.md` — \"Never more free "
          f"parameters than sessions / 3.\" 3 grid dimensions are being fit against "
          f"{len(count_sessions)} count session(s), a budget of {budget:.2f}. Below the "
          "requirement. Treat the proposed combination as a demonstration of the "
          "method on the corpus that exists today, not a validated calibration.")
    a("")

    a("## Current `config/params.json`")
    a("")
    a("| param | value |")
    a("|---|---|")
    a(f"| freefall_enter_g | {base.freefall_enter_g:g} |")
    a(f"| max_airtime_s | {base.max_airtime_s:g} |")
    a(f"| min_airtime_s | {base.min_airtime_s:g} |")
    a(f"| (held fixed) g, freefall_confirm_s, landing_threshold_g, landing_settle_s, "
      f"airtime_offset_s, height_scale, spin_lever_m | "
      f"{base.g:g}, {base.freefall_confirm_s:g}, {base.landing_threshold_g:g}, "
      f"{base.landing_settle_s:g}, {base.airtime_offset_s:g}, {base.height_scale:g}, "
      f"{base.spin_lever_m:g} |")
    a("")

    a("## Proposed values")
    a("")
    if count_sessions:
        a(f"Grid point minimizing summed count error over all {len(count_sessions)} "
          f"count session(s) at once (NOT leave-one-out — see \"Leave-one-session-out\" "
          f"below for the held-out check): summed count error "
          f"{_fmt(proposed_total_ce, 0)}. Ties broken toward the smallest change from "
          "the current values, then grid order (both ASSUMED — see module docstring).")
    else:
        a("No session has a Surfr `jumps_total` — nothing to fit against. Proposed "
          "values equal the current ones.")
    a("")
    a("| param | current | proposed |")
    a("|---|---|---|")
    a(f"| freefall_enter_g | {base.freefall_enter_g:g} | {proposed.freefall_enter_g:g} |")
    a(f"| max_airtime_s | {base.max_airtime_s:g} | {proposed.max_airtime_s:g} |")
    a(f"| min_airtime_s | {base.min_airtime_s:g} | {proposed.min_airtime_s:g} |")
    a("")

    a("## Per-session before / after")
    a("")
    a("Count (against Surfr `jumps_total`):")
    a("")
    a("| session | Surfr count | before count | before err | after count | after err |")
    a("|---|---|---|---|---|---|")
    for s in surfr_sessions:
        surfr = s.surfr or {}
        jt = surfr.get("jumps_total")
        b, af = before[s.name], after[s.name]
        a(f"| {s.name} | {_fmt_int(jt)} | {b.count} | "
          f"{_fmt(count_error(b, surfr), 0)} | {af.count} | "
          f"{_fmt(count_error(af, surfr), 0)} |")
    a("")

    height_rows = [s for s in surfr_sessions if (s.surfr or {}).get("best_height_ft") is not None]
    if height_rows:
        a("Best height, ft (against Surfr `best_height_ft`):")
        a("")
        a("| session | Surfr best ht | before best ht | before err | after best ht | after err |")
        a("|---|---|---|---|---|---|")
        for s in height_rows:
            surfr = s.surfr or {}
            b, af = before[s.name], after[s.name]
            b_ft = b.best_height_m * M_TO_FT if b.best_height_m is not None else None
            a_ft = af.best_height_m * M_TO_FT if af.best_height_m is not None else None
            a(f"| {s.name} | {_fmt(surfr.get('best_height_ft'))} | {_fmt(b_ft)} | "
              f"{_fmt(height_error_ft(b, surfr))} | {_fmt(a_ft)} | "
              f"{_fmt(height_error_ft(af, surfr))} |")
        a("")

    air_rows = [s for s in surfr_sessions if (s.surfr or {}).get("max_airtime_s") is not None]
    if air_rows:
        a("Longest airtime, s (against Surfr `max_airtime_s`) — reported, NOT part of "
          "the verdict gate (the task specifies count error and best-height error only):")
        a("")
        a("| session | Surfr longest air | before longest | before err | after longest | after err |")
        a("|---|---|---|---|---|---|")
        for s in air_rows:
            surfr = s.surfr or {}
            b, af = before[s.name], after[s.name]
            a(f"| {s.name} | {_fmt(surfr.get('max_airtime_s'))} | "
              f"{_fmt(b.longest_airtime_s)} | {_fmt(airtime_error_s(b, surfr))} | "
              f"{_fmt(af.longest_airtime_s)} | {_fmt(airtime_error_s(af, surfr))} |")
        a("")

    a("## Leave-one-session-out")
    a("")
    a("For each count session, fit on every OTHER count session only, then report "
      "that fit's count error on the held-out one. This combo is generally NOT the "
      "proposed combo above (which is fit on all count sessions at once) — it answers "
      "a different question: would a combo tuned without this session have worked on it?")
    a("")
    a("| held-out session | LOSO combo | held-out count error |")
    a("|---|---|---|")
    for f in loso_folds:
        combo_str = format_combo(f.combo) if f.combo is not None else "—"
        ce_str = _fmt(f.held_out_count_error, 0) if f.held_out_count_error is not None else "—"
        note = f" ({f.note})" if f.note else ""
        a(f"| {f.held_out} | {combo_str}{note} | {ce_str} |")
    a("")

    a("## Verdict")
    a("")
    a(f"**{verdict}**")
    a("")
    a("Rule: propose only if count error improves (strictly) on EVERY session that "
      "has a Surfr count, and no session's best-height error gets worse.")
    for r in verdict_reasons:
        a(f"- {r}")
    a("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- CLI

def run(root: Path, config_path: Path) -> str:
    """The whole pipeline, returning the rendered report. Split out from
    main() so tests can call it directly against a temp corpus."""
    base = load_params(config_path)
    sessions = discover_sessions(root)
    surfr_sessions = [s for s in sessions if s.surfr is not None]
    count_sessions = [s for s in surfr_sessions if "jumps_total" in (s.surfr or {})]
    count_names = [s.name for s in count_sessions]
    surfr_by_name = {s.name: (s.surfr or {}) for s in surfr_sessions}

    grid = build_grid(base)
    grid_results = evaluate_grid(surfr_sessions, grid)

    if count_names:
        proposed, proposed_total_ce = pick_best(count_names, grid, base, grid_results, surfr_by_name)
    else:
        proposed, proposed_total_ce = base, None

    loso_folds = leave_one_session_out(count_names, grid, base, grid_results, surfr_by_name)

    before = {s.name: grid_results[s.name][combo_key(base)] for s in surfr_sessions}
    after = {s.name: grid_results[s.name][combo_key(proposed)] for s in surfr_sessions}

    if count_names:
        surfr_names = [s.name for s in surfr_sessions]
        verdict, reasons = decide_verdict(surfr_names, before, after, surfr_by_name)
    else:
        # No count session at all: proposed == base by construction (nothing to
        # fit), so a vacuous PROPOSE would just mean "propose no change", which
        # reads as an actual recommendation. There is no measurement behind it
        # (CLAUDE.md rule 2), so say KEEP and say why.
        verdict = "KEEP"
        reasons = ["no count session (Surfr jumps_total) available; nothing to fit or verify"]

    return render_report(
        config_path=config_path, base=base, sessions=sessions,
        surfr_sessions=surfr_sessions, count_sessions=count_sessions,
        proposed=proposed, proposed_total_ce=proposed_total_ce,
        before=before, after=after, loso_folds=loso_folds,
        verdict=verdict, verdict_reasons=reasons,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DEFAULT_SESSIONS_ROOT),
                    help="data/sessions root — direct children scanned for trace.csv "
                         f"(default: {DEFAULT_SESSIONS_ROOT})")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG),
                    help=f"config/params.json to read CURRENT values from (default: {DEFAULT_CONFIG})")
    ap.add_argument("--out", default=str(DEFAULT_OUT),
                    help=f"where to write the markdown report (default: {DEFAULT_OUT})")
    ap.add_argument("--no-write", action="store_true",
                    help="print the report only; do not write --out")
    args = ap.parse_args(argv)

    report = run(Path(args.root), Path(args.config))
    print(report)
    if not args.no_write:
        out_path = Path(args.out)
        out_path.write_text(report)
        print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
