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

Session layout (as ./tools/jump sync writes it):
  data/sessions/<id>/
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
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from detector import Params, load_params
from run import load_csv, run_detector

MATCH_WINDOW_S = 1.0   # a detected takeoff within this of a labeled one is "the same jump"
DATA_SESSIONS = Path(__file__).resolve().parent.parent / "data" / "sessions"


# --------------------------------------------------------------------- I/O

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
                "rotation_deg": num("rotation_deg"),
                "landing": (r.get("landing") or "").strip() or None,
                "notes": (r.get("notes") or "").strip() or None,
            })
    return rows


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


def eval_session(sess: Path, params: Params) -> Optional[dict]:
    """Score one session's re-run detections against its labels. Returns None if
    the session has no labels.csv (nothing to score against)."""
    labels_path = sess / "labels.csv"
    trace_path = sess / "trace.csv"
    if not labels_path.exists() or not trace_path.exists():
        return None

    labels = load_labels(labels_path)
    jump_truth = [l for l in labels if l["event"] == "jump" and l["t_start_s"] is not None]

    times, mag = load_csv(str(trace_path))
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

    # Height error over matched jumps that carry a truth height.
    herr = [p["det"].height_m - p["true"]["height_m"]
            for p in pairs
            if p["det"] is not None and p["true"]["height_m"] is not None]

    meta = load_session_meta(sess)
    return {
        "session": sess.name,
        "split": meta.get("split", "unknown"),
        "meta": meta,
        "n_true": len(jump_truth),
        "matched": len(used),
        "missed": missed,
        "spurious": spurious,
        "height_errors": herr,
        "device_jumps": len(load_device_jumps(sess)),
        "other_labels": sorted({l["event"] for l in labels if l["event"] != "jump"}),
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
    sessions = sorted(p for p in root.glob("*") if p.is_dir())
    results = []
    for s in sessions:
        r = eval_session(s, params)
        if r is None:
            continue
        if split != "all" and r["split"] != split:
            continue
        results.append(r)

    all_err = [e for r in results for e in r["height_errors"]]
    tot_true = sum(r["n_true"] for r in results)
    tot_match = sum(r["matched"] for r in results)
    tot_missed = sum(r["missed"] for r in results)
    tot_spur = sum(r["spurious"] for r in results)
    return {
        "n_sessions": len(results),
        "n_true": tot_true,
        "matched": tot_match,
        "missed": tot_missed,
        "spurious": tot_spur,
        "detection_rate": (tot_match / tot_true) if tot_true else None,
        "height": _agg_height(all_err),
        "sessions": results,
    }


# ------------------------------------------------------------------- report

def _fmt(x, unit="", nd=3):
    return "—" if x is None else f"{x:.{nd}f}{unit}"


def print_report(agg: dict, verbose: bool = False) -> None:
    if agg["n_sessions"] == 0:
        print("No labeled sessions found (need a labels.csv beside trace.csv).")
        print(f"See docs/data-pipeline.md for the labels.csv schema.")
        return
    if verbose:
        print(f"{'session':>20} {'split':>7} {'true':>5} {'match':>6} {'miss':>5} "
              f"{'spur':>5} {'rmse':>7} {'bias':>7}")
        for r in agg["sessions"]:
            h = _agg_height(r["height_errors"])
            print(f"{r['session']:>20} {r['split']:>7} {r['n_true']:>5} {r['matched']:>6} "
                  f"{r['missed']:>5} {r['spurious']:>5} "
                  f"{_fmt(h['rmse'],'m'):>7} {_fmt(h['bias'],'m'):>7}")
        print()
    h = agg["height"]
    print(f"CORPUS: {agg['n_sessions']} session(s), {agg['n_true']} labeled jump(s)")
    print(f"  detection : matched {agg['matched']}/{agg['n_true']} "
          f"(rate {_fmt(agg['detection_rate'], '', 3)}), missed {agg['missed']}, spurious {agg['spurious']}")
    print(f"  height    : n={h['n']}  RMSE {_fmt(h['rmse'],'m')}  bias {_fmt(h['bias'],'m')}  "
          f"MAE {_fmt(h['mae'],'m')}  max|err| {_fmt(h['max_abs'],'m')}")
    print(f"  benchmark : Marčiš'21 video-validated — Surfr 0.51 m, WOO3 0.70 m RMSE (kite big-air)")


def summary_metrics(agg: dict) -> dict:
    """The flat metric dict saved as a baseline / compared for regressions."""
    return {
        "n_sessions": agg["n_sessions"], "n_true": agg["n_true"],
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
