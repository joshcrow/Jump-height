#!/usr/bin/env python3
"""Corpus evaluator — score the detector over LABELED real sessions.

This is the real-data twin of sim/run.py's `report_vs_truth`: that compares
detected jumps to *synthetic* ground truth; this compares them to *video-derived*
ground truth on real captures. It is the backbone of the continuous-improvement
loop — every threshold/detector change is re-run over a frozen labeled corpus
and checked for regressions, instead of tuned by feel (docs/data-pipeline.md).

Why re-run the detector on the stored trace instead of trusting the device's own
jumps.csv: so you can change config/params.json (or the detector) and see the
effect on the WHOLE corpus at once, without reflashing. The device's jumps.csv
is still reported alongside as "what the firmware actually did".

Session layout (as ./tools/jump sync writes it). Sessions are discovered
RECURSIVELY under the root — a session is any directory holding both trace.csv
and labels.csv, at any depth — because captures get grouped by experiment
(jitter-check/<id>/, walk-overnight/pull-a/<id>/) and a one-level scan found
none of them:
  data/sessions/[<group>/…]<id>/
    trace.csv     t,mag                                          — |a| in g, ~50 Hz
    jumps.csv     n,takeoff_s,airtime_raw_s,airtime_s,height_m   — device's detections
    labels.csv    (NEW, this file's ground truth — see below)
    session.json  (NEW, optional provenance — see below)

labels.csv (header required); one row per ground-truth event, keyed to TRACE time:
    event,t_start_s,t_end_s,height_m,rotation_deg,landing,notes
  - event      : jump | trick | foil | carve | pump | wave | crash | ...
  - t_start_s  : trace time of the event (a jump's takeoff)
  - height_m   : video-derived TRUE apex (jump events) — the accuracy/calibration truth
  - rotation_deg, landing, t_end_s, notes : for the trick/landing/riding families; may be blank
  Only `event=jump` rows with a height_m are scored today; the schema is
  deliberately extensible so riding/trick labels accumulate in the same file.

session.json (optional): {"unit","firmware","params_sha","rider","gear",
  "conditions","split","notes"}. `split` ∈ {"train","test"} drives held-out
  evaluation (never tune and validate on the same sessions — the repo's
  threshold-self-derivation rule requires this). Missing file => split "unknown".

Pure standard library.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import textwrap
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from detector import Params, load_params
from run import load_csv, run_detector

MATCH_WINDOW_S = 1.0   # a detected takeoff within this of a labeled one is "the same jump"
DATA_SESSIONS = Path(__file__).resolve().parent.parent / "data" / "sessions"


# --------------------------------------------------------------------- I/O

# Which height_src values may be used as accuracy truth, and why.
#
#   "ruler" — apex displacement measured against a known length in frame.
#             Comes from a different physical channel than the accelerometer
#             and does not use h = g*T^2/8. This is real ground truth.
#   "sim"   — a synthetic session, where the apex is an INPUT to the generator
#             that synthesised the motion, not a re-derivation of the device's
#             own output. The device independently recovers it from the trace,
#             so the comparison is a controlled experiment rather than a
#             tautology. Valid for machinery/regression tests; it says nothing
#             about whether real wings are ballistic, because the generator
#             assumes they are.
#
# Everything else — notably "timing", and anything blank — is circular: it puts
# frame-counted airtime through the same h = g*T^2/8 the firmware uses, so it
# agrees by construction. See docs/data-pipeline.md, "Labeling".
INDEPENDENT_SRC = frozenset({"ruler", "sim"})


def load_labels(path: Path) -> List[dict]:
    """Load labels.csv. Returns all rows as dicts with parsed numeric fields;
    non-jump / unlabeled rows are kept (reported) but only scored where usable."""
    rows: List[dict] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            ev = (r.get("event") or "").strip().lower()
            if not ev:
                continue

            def num(k):
                v = (r.get(k) or "").strip()
                try:
                    return float(v)
                except ValueError:
                    return None
            rows.append({
                "event": ev,
                "t_start_s": num("t_start_s"),
                "t_end_s": num("t_end_s"),
                "height_m": num("height_m"),
                # HOW the truth height was obtained. This is not bookkeeping —
                # it decides whether the height is usable as truth at all.
                # "timing"  : counted airborne frames put through h = g*T^2/8.
                #             That is THE FORMULA UNDER TEST, so scoring against
                #             it measures timing agreement and nothing else; it
                #             passes whether or not wings are ballistic, which is
                #             the entire open question. NOT accuracy truth.
                # "ruler"   : apex displacement measured against a known length
                #             in frame (see docs/data-pipeline.md). Independent
                #             of the accelerometer AND of g*T^2/8. Real truth.
                # ""/absent : unknown provenance — treated as circular, because
                #             assuming otherwise is how a circular number gets
                #             published as an accuracy claim.
                "height_src": (r.get("height_src") or "").strip().lower() or None,
                "rotation_deg": num("rotation_deg"),
                "landing": (r.get("landing") or "").strip() or None,
                "notes": (r.get("notes") or "").strip() or None,
            })
    return rows


def find_sessions(root: Path) -> Tuple[List[Path], List[Path]]:
    """Walk `root` for session dirs. Returns (labeled, trace_only).

    RECURSIVE since 2026-08-23. This was `root.glob("*")` — exactly one level
    deep — while the repo's only labels.csv sits two levels down, at
    data/sessions/jitter-check/20260815-190012/. So `jump eval` printed "No
    labeled sessions found", which is the message for "you have not labeled
    anything yet". The one labeled session in the repo was invisible for as
    long as it existed, and a MISS was indistinguishable from an ABSENCE
    (CLAUDE.md rule 3). Nesting is not exotic here: captures get grouped into
    a named folder per experiment (jitter-check/, walk-overnight/pull-a/), and
    docs/data-pipeline.md's flat `data/sessions/<id>/` is the simple case, not
    the only one.

    A matched session is NOT descended into. Its subdirectories are its own
    artefacts, and re-matching inside one would double-count its jumps.

    `trace_only` is returned rather than discarded because "14 traces, none
    labeled" and "nothing here at all" are different facts and the caller has
    to be able to tell the user which one it is.
    """
    labeled: List[Path] = []
    trace_only: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        d = Path(dirpath)
        names = set(filenames)
        if "trace.csv" in names and "labels.csv" in names:
            labeled.append(d)
            dirnames[:] = []          # a session is a leaf — stop here
            continue
        if "trace.csv" in names:
            trace_only.append(d)
        dirnames.sort()               # deterministic walk order
    return sorted(labeled), sorted(trace_only)


def display_name(sess: Path, root: Path) -> str:
    """Session name for the report, relative to `root`.

    Not `sess.name`: two sessions at different depths can share a leaf name
    (every `jump sync` dir is a timestamp, so `a/20260815-190012` and
    `b/20260815-190012` are entirely possible), and two identical rows in the
    per-session table would be unattributable. A depth-1 session's relative
    path IS its leaf name, so the existing flat output is unchanged.
    """
    try:
        rel = sess.relative_to(root).as_posix()
    except ValueError:
        return sess.name
    return sess.name if rel == "." else rel


def load_session_meta(sess: Path) -> dict:
    p = sess / "session.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except (OSError, ValueError):
            pass
    return {}


def load_device_jumps(sess: Path) -> List[dict]:
    """The device's own jumps.csv: n,takeoff_s,airtime_raw_s,airtime_s,height_m."""
    p = sess / "jumps.csv"
    out: List[dict] = []
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


# ------------------------------------------------------------------ scoring

def _match(detected_takeoffs: List[float], true_t: float) -> Optional[int]:
    """Index of the nearest detected takeoff within MATCH_WINDOW_S, else None."""
    best, best_dt = None, MATCH_WINDOW_S
    for i, t in enumerate(detected_takeoffs):
        dt = abs(t - true_t)
        if dt < best_dt:
            best, best_dt = i, dt
    return best


def inadmissible_reasons(jump_truth: List[dict], times: List[float]) -> List[str]:
    """Why this session's ground truth cannot be scored. [] means it can.

    Added 2026-08-23, because scoring bad labels is worse than scoring none.
    data/sessions/jitter-check/20260815-190012 carries three `jump` rows all
    stamped t_start_s=15619.172, and the evaluator dutifully reported
    "matched 0/3, missed 3, spurious 3". docs/data-pipeline.md §"THE
    DIAGNOSTIC THAT MATTERS" and docs/session-card.md's troubleshooting table
    both tell the reader that missed ≈ spurious means a video↔trace SYNC
    error, explicitly NOT a broken detector. So the tool was emitting the
    signature for one fault while suffering a different one, and the docs
    would have sent whoever read it off to re-check a sync marker that was
    fine. A confident wrong diagnosis costs more than a blank.

    Each check below has to name a failure it actually prevents; a check that
    only expresses taste would refuse real data on water-day evening, which is
    the one moment there is no time to argue with a tool.
    """
    reasons: List[str] = []

    # (a) No time span. A truncated or empty trace.csv (an interrupted `jump
    #     sync`) scores every label as missed, i.e. "matched 0/N" — again the
    #     detector takes the blame for a file-transfer failure.
    if len(times) < 2:
        reasons.append(f"trace.csv holds {len(times)} sample(s) — there is no "
                       f"time span to score anything against")

    if not jump_truth:
        return reasons

    ts = [j["t_start_s"] for j in jump_truth]

    # (b) Duplicate takeoff timestamps — the jitter-check case, and NOT a
    #     one-off: tools/label.py turns the note "15:05  jump x3" into three
    #     rows at one instant BY DESIGN, and prints "Treat jump rows here as
    #     'roughly N jumps happened around here'" as it writes them. That
    #     warning is true and it is also on a different day, in a different
    #     terminal, in scrollback nobody re-reads — so the file itself has to
    #     carry the disqualification. Two takeoffs cannot share an instant;
    #     N rows at one time are a COUNT, not N timings.
    #
    #     Exact equality only, deliberately. A window-based rule (say, "within
    #     MATCH_WINDOW_S") is a judgement call that could refuse two genuinely
    #     close real jumps, and it is not needed: the failure being prevented
    #     produces byte-identical timestamps.
    counts = Counter(ts)
    dups = sorted(t for t, n in counts.items() if n > 1)
    if dups:
        n_rows = sum(n for t, n in counts.items() if n > 1)
        shown = ", ".join(f"{t:.3f}" for t in dups[:3]) + ("…" if len(dups) > 3 else "")
        reasons.append(
            f"{n_rows} of {len(ts)} jump row(s) share a t_start_s ({shown} s) — "
            f"two takeoffs cannot occur at the same instant, so these are a "
            f"count of jumps, not per-jump timings")

    # (c) Every label outside the trace. Trace time is seconds-since-boot (the
    #     puck has no RTC), so labels keyed to wall-clock or to video-relative
    #     time land nowhere near it — and tools/label.py does that conversion
    #     from session.json's trace_epoch_utc, which is exactly the step that
    #     can be wrong. Nothing is matchable, so "matched 0/N" would once more
    #     accuse the detector of a clock error.
    #
    #     ALL, not ANY: one stray row past the end is still a session with
    #     scoreable jumps in it, and refusing the other seven would throw away
    #     good data. The partial case is reported instead (see out_of_range
    #     in eval_session) so a `missed` count is never silently a labeling
    #     error. MATCH_WINDOW_S of slack because a takeoff that close to the
    #     edge could legitimately match.
    if len(times) >= 2:
        lo, hi = min(times) - MATCH_WINDOW_S, max(times) + MATCH_WINDOW_S
        if all(t < lo or t > hi for t in ts):
            reasons.append(
                f"all {len(ts)} jump label(s) fall outside the trace's time range "
                f"{min(times):.1f}–{max(times):.1f} s (labels span "
                f"{min(ts):.1f}–{max(ts):.1f} s) — they are keyed to a different clock")
    return reasons


def eval_session(sess: Path, params: Params, name: Optional[str] = None) -> Optional[dict]:
    """Score one session's re-run detections against its labels. Returns None if
    the session has no labels.csv (nothing to score against).

    A session whose labels are inadmissible comes back with a non-empty
    `excluded` list and no scores — present in the results, refusing to
    produce a number. It is never dropped: dropping it would restore the
    miss-looks-like-absence bug this function was fixed for.
    """
    labels_path = sess / "labels.csv"
    trace_path = sess / "trace.csv"
    if not labels_path.exists() or not trace_path.exists():
        return None

    labels = load_labels(labels_path)
    jump_truth = [l for l in labels if l["event"] == "jump" and l["t_start_s"] is not None]

    times, mag = load_csv(str(trace_path))

    meta = load_session_meta(sess)
    disp = name if name is not None else sess.name
    other_labels = sorted({l["event"] for l in labels if l["event"] != "jump"})

    bad = inadmissible_reasons(jump_truth, times)
    if bad:
        return {
            "session": disp,
            "split": meta.get("split", "unknown"),
            "meta": meta,
            "excluded": bad,
            "n_true": len(jump_truth),
            # None, not 0. A zero here would aggregate into the corpus totals
            # as a real, passing measurement of nothing.
            "matched": None,
            "missed": None,
            "spurious": None,
            "height_errors": [],
            "circular_heights": 0,
            "out_of_range": 0,
            "device_jumps": len(load_device_jumps(sess)),
            "other_labels": other_labels,
            "pairs": [],
        }
    detected = run_detector(times, mag, params)
    det_takeoffs = [d.takeoff_time_s for d in detected]

    used = set()
    pairs: List[dict] = []
    missed = 0
    for jt in jump_truth:
        idx = _match([t if i not in used else 1e18 for i, t in enumerate(det_takeoffs)], jt["t_start_s"])
        if idx is None:
            missed += 1
            pairs.append({"true": jt, "det": None})
        else:
            used.add(idx)
            pairs.append({"true": jt, "det": detected[idx]})

    spurious = len(detected) - len(used)

    # Height error over matched jumps that carry an INDEPENDENT truth height.
    #
    # Timing-derived truth (frame-counted airtime -> h = g*T^2/8) is excluded on
    # purpose: the device computes height with that same formula, so the two
    # agree by construction and the resulting RMSE is a measurement of nothing.
    # Counting it would produce a small, confident, meaningless error bar --
    # the most dangerous possible output.
    def _independent(t):
        return t["height_m"] is not None and t.get("height_src") in INDEPENDENT_SRC

    herr = [p["det"].height_m - p["true"]["height_m"]
            for p in pairs
            if p["det"] is not None and _independent(p["true"])]

    # Counted separately so the report can say WHY there is no RMSE rather than
    # printing a bare dash and letting someone assume the labels were missing.
    circular = sum(1 for p in pairs
                   if p["det"] is not None and p["true"]["height_m"] is not None
                   and not _independent(p["true"]))

    # Labels that lie outside the trace entirely, in a session where OTHERS do
    # not (the all-outside case was refused above). Each one is unmatchable and
    # so lands in `missed`, where it is indistinguishable from a jump the
    # detector failed to find. Counted so the report can name it: this is
    # docs/data-pipeline.md's "partial case is the trap" — a plausible rate
    # computed from a silent subset.
    _lo, _hi = min(times) - MATCH_WINDOW_S, max(times) + MATCH_WINDOW_S
    out_of_range = sum(1 for j in jump_truth if j["t_start_s"] < _lo or j["t_start_s"] > _hi)

    return {
        "session": disp,
        "split": meta.get("split", "unknown"),
        "meta": meta,
        "excluded": [],
        "n_true": len(jump_truth),
        "matched": len(used),
        "missed": missed,
        "spurious": spurious,
        "height_errors": herr,
        "circular_heights": circular,
        "out_of_range": out_of_range,
        "device_jumps": len(load_device_jumps(sess)),
        "other_labels": other_labels,
        "pairs": pairs,
    }


def _agg_height(errs: List[float]) -> dict:
    if not errs:
        return {"n": 0, "bias": None, "mae": None, "rmse": None, "max_abs": None}
    n = len(errs)
    return {
        "n": n,
        "bias": sum(errs) / n,
        "mae": sum(abs(e) for e in errs) / n,
        "rmse": math.sqrt(sum(e * e for e in errs) / n),
        "max_abs": max(abs(e) for e in errs),
    }


def eval_corpus(root: Path, params: Params, split: str = "all") -> dict:
    labeled, trace_only = find_sessions(root)
    results = []
    split_filtered = 0
    for s in labeled:
        r = eval_session(s, params, name=display_name(s, root))
        if r is None:
            continue
        if split != "all" and r["split"] != split:
            split_filtered += 1
            continue
        results.append(r)

    # Excluded sessions stay in `sessions` (the report must show them) but
    # contribute to NO total. Folding their zeros into the corpus would let
    # inadmissible labels quietly drag the detection rate down, which is the
    # same lie in aggregate form.
    scored = [r for r in results if not r.get("excluded")]

    all_err = [e for r in scored for e in r["height_errors"]]
    tot_circular = sum(r.get("circular_heights", 0) for r in scored)
    tot_oor = sum(r.get("out_of_range", 0) for r in scored)
    tot_true = sum(r["n_true"] for r in scored)
    tot_match = sum(r["matched"] for r in scored)
    tot_missed = sum(r["missed"] for r in scored)
    tot_spur = sum(r["spurious"] for r in scored)
    return {
        "n_sessions": len(scored),
        "n_true": tot_true,
        "matched": tot_match,
        "missed": tot_missed,
        "spurious": tot_spur,
        "detection_rate": (tot_match / tot_true) if tot_true else None,
        "height": _agg_height(all_err),
        "circular_heights": tot_circular,
        "out_of_range": tot_oor,
        "n_excluded": len(results) - len(scored),
        # Discovery bookkeeping, so "no labeled sessions" can say which kind of
        # nothing it found (2026-08-23).
        "root": str(root),
        "unlabeled_traces": [display_name(p, root) for p in trace_only],
        "split_filtered": split_filtered,
        "sessions": results,
    }


# ------------------------------------------------------------------- report

def _fmt(x, unit="", nd=3):
    return "—" if x is None else f"{x:.{nd}f}{unit}"


def _int(x):
    """Counts an excluded session does not have. A dash, never a 0 — a 0 in the
    `match` column is a claim that the detector found nothing."""
    return "—" if x is None else str(x)


def _print_nothing_found(agg: dict) -> None:
    """The empty-corpus message.

    It used to be one line — "No labeled sessions found" — which conflated
    "you have not labeled anything yet" with "the search never reached your
    labels" (it was one level deep) and with "your --split dropped them all".
    Three different problems, three different fixes, one indistinguishable
    message. CLAUDE.md rule 3: a reading that did not happen is a finding.
    """
    print("No labeled sessions found (need a labels.csv beside trace.csv).")
    print(f"  searched (recursively): {agg.get('root', '?')}")

    unlabeled = agg.get("unlabeled_traces") or []
    if unlabeled:
        shown = ", ".join(unlabeled[:5])
        more = f" (+{len(unlabeled) - 5} more)" if len(unlabeled) > 5 else ""
        print(f"  {len(unlabeled)} director(ies) DO hold a trace.csv but no labels.csv:")
        print(f"    {shown}{more}")
        print("  So: traces exist and none of them are labeled. That is a different")
        print("  fact from an empty corpus, and it has a different fix.")

    nf = agg.get("split_filtered", 0)
    if nf:
        print(f"  {nf} labeled session(s) WERE found, then dropped by the --split")
        print("  filter. Re-run with --split all to see them.")

    print("See docs/data-pipeline.md for the labels.csv schema.")


def _print_table(sessions: List[dict]) -> None:
    # Width follows the longest name instead of a fixed 20, because names are
    # now paths relative to the root and a nested one overflows the column and
    # shears the whole row. All-flat corpora still render at 20, unchanged.
    w = max(20, max(len(r["session"]) for r in sessions))
    print(f"{'session':>{w}} {'split':>7} {'true':>5} {'match':>6} {'miss':>5} "
          f"{'spur':>5} {'rmse':>7} {'bias':>7}")
    for r in sessions:
        h = _agg_height(r["height_errors"])
        mark = "  ← EXCLUDED, not scored (see below)" if r.get("excluded") else ""
        print(f"{r['session']:>{w}} {r['split']:>7} {r['n_true']:>5} "
              f"{_int(r['matched']):>6} {_int(r['missed']):>5} {_int(r['spurious']):>5} "
              f"{_fmt(h['rmse'],'m'):>7} {_fmt(h['bias'],'m'):>7}{mark}")
    print()


def _print_excluded(excluded: List[dict]) -> None:
    """Say WHY a session was refused, in the same shape as the height_src block
    below: the labels are there, and they are not admissible."""
    if not excluded:
        return
    print()
    print(f"  ⚠️  {len(excluded)} session(s) EXCLUDED: the labels are there and are")
    print( "      NOT admissible, so no score was computed from them. Refusing is the")
    print( "      point: scoring them emits 'matched 0/N … spurious N', and both")
    print( "      docs/data-pipeline.md and docs/session-card.md tell the reader that")
    print( "      signature means a video↔trace SYNC error and NOT a broken detector.")
    print( "      The tool would have been handing over a confident wrong diagnosis.")
    for r in excluded:
        print(f"      • {r['session']}  ({r['n_true']} jump row(s))")
        for why in r["excluded"]:
            for i, line in enumerate(textwrap.wrap(why, width=68)):
                print(f"          {'–' if i == 0 else ' '} {line}")
    print( "      Fix: per-jump takeoff times from video (docs/data-pipeline.md")
    print( "      'Labeling'). tools/label.py's `jump xN` writes N rows at ONE")
    print( "      timestamp on purpose — those are 'roughly N jumps happened around")
    print( "      here', which is a useful note and is not per-jump ground truth.")


def print_report(agg: dict, verbose: bool = False) -> None:
    sessions = agg.get("sessions", [])
    excluded = [r for r in sessions if r.get("excluded")]

    if not sessions:
        _print_nothing_found(agg)
        return

    # The per-session table is normally a --verbose extra. An EXCLUDED session
    # forces it on: leaving that row out of the default view would hide the
    # refusal, which is this same silent-failure bug wearing a new hat.
    if verbose or excluded:
        _print_table(sessions)

    if agg["n_sessions"] == 0:
        print("CORPUS: nothing scored — every labeled session found was excluded.")
        _print_excluded(excluded)
        return

    h = agg["height"]
    print(f"CORPUS: {agg['n_sessions']} session(s), {agg['n_true']} labeled jump(s)")
    print(f"  detection : matched {agg['matched']}/{agg['n_true']} "
          f"(rate {_fmt(agg['detection_rate'], '', 3)}), missed {agg['missed']}, spurious {agg['spurious']}")
    print(f"  height    : n={h['n']}  RMSE {_fmt(h['rmse'],'m')}  bias {_fmt(h['bias'],'m')}  "
          f"MAE {_fmt(h['mae'],'m')}  max|err| {_fmt(h['max_abs'],'m')}")
    print(f"  benchmark : Marčiš'21 video-validated — Surfr 0.51 m, WOO3 0.70 m RMSE (kite big-air)")
    _print_excluded(excluded)

    # Labels outside the trace in an otherwise-scoreable session. They land in
    # `missed` and look exactly like detector failures.
    noor = agg.get("out_of_range", 0)
    if noor:
        print()
        print(f"  ⚠️  {noor} jump label(s) lie outside their trace's time range and are")
        print( "      therefore counted as MISSED, though nothing could have matched")
        print( "      them. The detection rate above is understated by that much — do")
        print( "      not read it as a detector fault (docs/data-pipeline.md, 'the")
        print( "      partial case is the trap').")
    # Say WHY there is no RMSE. A bare dash reads as "you forgot to label";
    # this case is the opposite — the labels are there and are not admissible.
    nc = agg.get("circular_heights", 0)
    if nc:
        print()
        print(f"  ⚠️  {nc} matched jump(s) carry a truth height that is NOT independent")
        print( "      (height_src is not 'ruler'), so they are EXCLUDED from RMSE above.")
        print( "      A height derived from counted airtime via h = g*T^2/8 is the very")
        print( "      formula the device uses. Scoring one against the other measures")
        print( "      timing agreement and would pass whether or not wings are")
        print( "      ballistic — which is the entire open question.")
        print( "      Fix: measure apex against a known length in frame and set")
        print( "      height_src=ruler. See docs/data-pipeline.md 'Labeling'.")


def summary_metrics(agg: dict) -> dict:
    """The flat metric dict saved as a baseline / compared for regressions."""
    return {
        "n_sessions": agg["n_sessions"], "n_true": agg["n_true"],
        # Recorded so a frozen baseline shows that a session was REFUSED rather
        # than just appearing to have fewer sessions than the corpus on disk.
        "n_excluded": agg.get("n_excluded", 0),
        "detection_rate": agg["detection_rate"],
        "rmse": agg["height"]["rmse"], "bias": agg["height"]["bias"],
        "mae": agg["height"]["mae"], "missed": agg["missed"], "spurious": agg["spurious"],
    }


def regression_check(cur: dict, baseline: dict, rmse_tol: float = 0.02,
                     rate_tol: float = 0.02) -> Tuple[bool, List[str]]:
    """Compare current metrics to a saved baseline. Returns (ok, messages).
    Fails if RMSE grew by > rmse_tol (m), detection rate dropped by > rate_tol,
    or spurious detections increased."""
    msgs = []
    ok = True

    def worse_higher(key, tol, unit=""):
        nonlocal ok
        c, b = cur.get(key), baseline.get(key)
        if c is None or b is None:
            return
        if c > b + tol:
            ok = False
            msgs.append(f"REGRESSION {key}: {b:.3f}{unit} → {c:.3f}{unit} (+{c-b:.3f})")
        elif c < b - tol:
            msgs.append(f"improved {key}: {b:.3f}{unit} → {c:.3f}{unit} ({c-b:+.3f})")

    worse_higher("rmse", rmse_tol, "m")
    # detection rate: worse means LOWER
    c, b = cur.get("detection_rate"), baseline.get("detection_rate")
    if c is not None and b is not None:
        if c < b - rate_tol:
            ok = False
            msgs.append(f"REGRESSION detection_rate: {b:.3f} → {c:.3f} ({c-b:+.3f})")
        elif c > b + rate_tol:
            msgs.append(f"improved detection_rate: {b:.3f} → {c:.3f} ({c-b:+.3f})")
    worse_higher("spurious", 0, "")
    return ok, msgs


# ---------------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(DATA_SESSIONS),
                    help="sessions root (default data/sessions)")
    ap.add_argument("--split", default="all", choices=["all", "train", "test", "unknown"],
                    help="evaluate only sessions with this session.json split")
    ap.add_argument("--verbose", "-v", action="store_true", help="per-session table")
    ap.add_argument("--save", help="write the summary metrics JSON to this path (a baseline)")
    ap.add_argument("--baseline", help="compare against a saved baseline JSON; exit 1 on regression")
    args = ap.parse_args()

    params = load_params()
    agg = eval_corpus(Path(args.root), params, split=args.split)
    print_report(agg, verbose=args.verbose)

    metrics = summary_metrics(agg)
    if args.save:
        Path(args.save).write_text(json.dumps(metrics, indent=2) + "\n")
        print(f"\nsaved baseline -> {args.save}")

    if args.baseline:
        base = json.loads(Path(args.baseline).read_text())
        ok, msgs = regression_check(metrics, base)
        print("\nregression vs baseline:")
        for m in msgs:
            print("  " + m)
        if not msgs:
            print("  (no material change)")
        print("  RESULT:", "PASS ✅" if ok else "FAIL ❌")
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
