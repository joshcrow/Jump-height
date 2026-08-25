#!/usr/bin/env python3
"""E9 — the E7/E8 question, asked of EVERY recording ever made.

E7 swept thresholds against one recording (one pocket, one walk, ten
events) and found the shipped operating point keeps a 1.393 g non-free-fall
event. E8 showed the recommendation (enter 0.26 / min_air 0.30) survives 12
perturbed worlds while the shipped point survives none — but both rest on
that single recording, and E8's own caveat stands: none of this is water.

The corpus has grown since 08-15: walk sessions, jitter checks, desk
sessions, calibration drops, USB pulls. This replays ALL of it — every
trace.csv under data/ — through a compact threshold grid centred on E7's
plateaus, and reports per-config, per-session:

  - events detected, and the per-hour rate
  - a median-airborne-|a| autopsy of every event (E7's own arbiter:
    genuine free fall reads well under 0.2 g; slap-class events read high)
  - where configs DISAGREE — the 1.393 g class made visible corpus-wide

Dedup: `jump sync` pulled some sessions twice (pull-a/pull-b are the same
recording). Traces are deduplicated by content hash, or double-counted
hours would silently flatter the per-hour rates.

Honesty: this corpus is pockets, desks, benches and one overnight walk.
It contains no water. A config that wins here has won on land motion.

Reproduce:  python3 sim/experiments/e9_corpus_replay.py [--quick]
Output:     sim/experiments/out/e9_events.csv, e9_summary.csv
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from dataclasses import replace
from itertools import product
from multiprocessing import Pool, cpu_count
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

from detector import Detector, load_params  # noqa: E402

OUT = Path(__file__).parent / "out"

# Compact grid: E7's plateau region + the shipped point + sentinels either
# side. 7 x 4 x 3 = 84 configs. "shipped" and "e8_rec" are called out by name
# in the summary so the headline comparison cannot be lost in the grid.
ENTER = [0.20, 0.22, 0.24, 0.26, 0.28, 0.30, 0.35]
MINAIR = [0.20, 0.25, 0.30, 0.35]
CONFIRM = [0.06, 0.08, 0.10]
SHIPPED = {"freefall_enter_g": 0.35, "min_airtime_s": 0.25,
           "freefall_confirm_s": 0.08}
E8_REC = {"freefall_enter_g": 0.26, "min_airtime_s": 0.30,
          "freefall_confirm_s": 0.08}


def find_traces() -> list[tuple[str, Path]]:
    """Every trace.csv under data/, deduplicated by content hash."""
    seen: dict[str, Path] = {}
    for p in sorted((REPO / "data").rglob("trace.csv")) + \
            sorted((REPO / "data" / "diagnostics").glob("*.csv")):
        h = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        if h not in seen:
            seen[h] = p
    return [(h, p) for h, p in seen.items()]


def load_trace(path: Path) -> list[tuple[float, float]]:
    rows: list[tuple[float, float]] = []
    with open(path, newline="") as f:
        for r in csv.reader(f):
            try:
                rows.append((float(r[0]), float(r[1])))
            except (ValueError, IndexError):
                continue  # header / malformed line
    return rows


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def airborne_median(trace, t_takeoff, airtime_s) -> float:
    """Median |a| across the flight window — the free-fall arbiter."""
    xs = [m for t, m in trace if t_takeoff <= t <= t_takeoff + airtime_s]
    return _median(xs) if xs else float("nan")


def run_one(args) -> list[dict]:
    (h, path_str, combo) = args
    trace = load_trace(Path(path_str))
    if len(trace) < 2:
        return []
    det = Detector(replace(load_params(), **combo))
    fed = 0
    events = []
    for t, m in trace:
        fed += 1
        ev = det.update(t, m)
        if ev is not None:
            events.append(ev)
    # E7's invariant: every sample consumed, or the run does not count.
    assert fed == len(trace), f"{path_str}: fed {fed} != {len(trace)}"
    span_h = (trace[-1][0] - trace[0][0]) / 3600.0
    out = []
    for ev in events:
        out.append({
            "trace": h, "path": str(Path(path_str).relative_to(REPO)),
            "enter": combo["freefall_enter_g"],
            "minair": combo["min_airtime_s"],
            "confirm": combo["freefall_confirm_s"],
            "t_takeoff": round(ev.takeoff_time_s, 3),
            "airtime_s": round(ev.airtime_s, 3),
            "median_air_g": round(
                airborne_median(trace, ev.takeoff_time_s, ev.airtime_raw_s), 3),
            "span_h": round(span_h, 4),
        })
    if not events:  # keep the exposure hours even when nothing fired
        out.append({"trace": h,
                    "path": str(Path(path_str).relative_to(REPO)),
                    "enter": combo["freefall_enter_g"],
                    "minair": combo["min_airtime_s"],
                    "confirm": combo["freefall_confirm_s"],
                    "t_takeoff": "", "airtime_s": "", "median_air_g": "",
                    "span_h": round(span_h, 4)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="2 configs x 2 traces, to prove the harness")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    traces = find_traces()
    combos = [dict(zip(("freefall_enter_g", "min_airtime_s",
                        "freefall_confirm_s"), c))
              for c in product(ENTER, MINAIR, CONFIRM)]
    for named in (SHIPPED, E8_REC):
        if named not in combos:
            combos.append(named)
    if args.quick:
        traces, combos = traces[:2], [SHIPPED, E8_REC]

    total_h = 0.0
    jobs = [(h, str(p), c) for (h, p) in traces for c in combos]
    print(f"E9: {len(traces)} unique traces x {len(combos)} configs "
          f"= {len(jobs)} replays on {cpu_count()} cores")

    rows: list[dict] = []
    with Pool(max(1, cpu_count() - 2)) as pool:
        for out in pool.imap_unordered(run_one, jobs, chunksize=4):
            rows.extend(out)

    with open(OUT / "e9_events.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # Summary: per config, events by autopsy class + rate per hour.
    per: dict[tuple, dict] = {}
    hours_per_trace = {r["trace"]: r["span_h"] for r in rows}
    total_h = sum(hours_per_trace.values())
    for r in rows:
        if r["t_takeoff"] == "":
            continue
        k = (r["enter"], r["minair"], r["confirm"])
        d = per.setdefault(k, {"events": 0, "freefall_like": 0, "high_g": 0})
        d["events"] += 1
        med = r["median_air_g"]
        if med == med and med <= 0.20:
            d["freefall_like"] += 1
        elif med == med and med >= 0.50:
            d["high_g"] += 1
    with open(OUT / "e9_summary.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["enter", "minair", "confirm", "events",
                    "freefall_like", "high_g_suspect", "ambiguous",
                    "events_per_hour", "corpus_hours"])
        for k in sorted(per):
            d = per[k]
            amb = d["events"] - d["freefall_like"] - d["high_g"]
            w.writerow([*k, d["events"], d["freefall_like"], d["high_g"],
                        amb, round(d["events"] / total_h, 3),
                        round(total_h, 2)])

    for name, c in (("shipped", SHIPPED), ("e8_rec", E8_REC)):
        k = (c["freefall_enter_g"], c["min_airtime_s"],
             c["freefall_confirm_s"])
        d = per.get(k, {"events": 0, "freefall_like": 0, "high_g": 0})
        print(f"  {name:8} enter={k[0]} minair={k[1]} confirm={k[2]}: "
              f"{d['events']} events over {total_h:.1f} h "
              f"({d['freefall_like']} free-fall-like, "
              f"{d['high_g']} high-g suspects)")
    print(f"wrote {OUT / 'e9_events.csv'} and e9_summary.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
