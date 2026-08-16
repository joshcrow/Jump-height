#!/usr/bin/env python3
"""E8 — does E7's recommended operating point survive a different world?

THE PROBLEM WITH E7
-------------------
E7 swept the detector across real recorded motion and found that the shipped
configuration keeps a 1.393 g false positive, while two wide parameter plateaus
reject it at no measured cost. That is a real result, and it rests on **one
recording containing ten events, made in a trouser pocket, on land.**

Tuning against that is exactly how a detector gets overfitted. The water is
noisier, the mount is rigid instead of soft, the sensor has gain and offset
error, and a foil jump is longer and smoother than a hand toss. A parameter
choice that is only correct for this specific recording is worse than no change
at all, because it would carry false confidence into a one-shot session.

WHAT THIS DOES
--------------
Re-runs the sweep against systematically perturbed versions of the same
recording, and asks which operating points stay correct across ALL of them:

  * added noise      — water and a hard mount are noisier than a pocket
  * gain error       — the accelerometer's scale factor is not exactly 1
  * offset error     — a static bias in |a|
  * time dilation    — sample-clock error, which stretches measured airtime

"Correct" keeps its E7 meaning: find all nine free-fall-consistent events, and
reject the 1.393 g one. The output ranks parameter points by how many perturbed
worlds they survive. A point that is correct in one world is a coincidence; a
point correct in all of them is a defensible recommendation.

Usage:
    python3 sim/experiments/e8_robustness.py
"""

from __future__ import annotations

import argparse
import bisect
import collections
import csv
import itertools
import random
import sys
import time
from dataclasses import replace
from multiprocessing import Pool, cpu_count
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))
sys.path.insert(0, str(REPO / "sim" / "experiments"))

from detector import Detector, load_params            # noqa: E402
from e7_threshold_sweep import (                      # noqa: E402
    KNOWN_GOOD_S, KNOWN_BAD_S, MATCH_S, TRACE, load_trace, _median)

# Each entry is (label, noise_sigma_g, gain, offset_g, time_scale).
# The clean pass is first so every table has its reference row.
WORLDS = [
    ("clean",            0.000, 1.000,  0.000, 1.0000),
    ("noise_0.01",       0.010, 1.000,  0.000, 1.0000),
    ("noise_0.02",       0.020, 1.000,  0.000, 1.0000),
    ("noise_0.05",       0.050, 1.000,  0.000, 1.0000),
    ("gain_0.95",        0.000, 0.950,  0.000, 1.0000),
    ("gain_1.05",        0.000, 1.050,  0.000, 1.0000),
    ("offset_-0.02",     0.000, 1.000, -0.020, 1.0000),
    ("offset_+0.02",     0.000, 1.000,  0.020, 1.0000),
    ("time_-0.5pct",     0.000, 1.000,  0.000, 0.9950),
    ("time_+0.5pct",     0.000, 1.000,  0.000, 1.0050),
    # Combined worst-case: noisy, mis-scaled and biased at once. If a point
    # survives this it is not relying on any single quantity being clean.
    ("nasty_combo",      0.030, 0.960,  0.015, 1.0030),
    ("nasty_combo_2",    0.030, 1.040, -0.015, 0.9970),
]

_BASE: list[tuple[float, float]] = []


def _init():
    global _BASE
    _BASE = load_trace(TRACE)


def perturb(base, sigma, gain, offset, tscale, seed):
    rnd = random.Random(seed)
    if sigma == 0.0:
        return [(t * tscale, m * gain + offset) for t, m in base]
    return [(t * tscale, m * gain + offset + rnd.gauss(0.0, sigma))
            for t, m in base]


def run(job) -> dict:
    world, combo = job
    label, sigma, gain, offset, tscale = world
    trace = perturb(_BASE, sigma, gain, offset, tscale, seed=hash(label) & 0xFFFF)
    times = [t for t, _ in trace]
    mags = [m for _, m in trace]

    det = Detector(replace(load_params(), **combo))
    events = []
    for t, mag in trace:
        ev = det.update(t, mag)
        if ev is not None:
            events.append(ev)

    classified = []
    for ev in events:
        t0 = ev.takeoff_time_s
        lo = bisect.bisect_left(times, t0)
        hi = bisect.bisect_right(times, t0 + ev.airtime_raw_s)
        classified.append((t0, _median(mags[lo:hi])))

    # Ground-truth times are from the clean recording; time_scale moves the
    # recording under them, so compare against scaled expectations.
    good = [g * tscale for g in KNOWN_GOOD_S]
    bad = [b * tscale for b in KNOWN_BAD_S]
    kept_good = sum(1 for g in good if any(abs(t0 - g) <= MATCH_S for t0, _ in classified))
    kept_bad = sum(1 for b in bad if any(abs(t0 - b) <= MATCH_S for t0, _ in classified))
    extra = len(classified) - kept_good - kept_bad

    out = dict(combo)
    out.update({"world": label, "n_events": len(events), "kept_good": kept_good,
                "kept_bad": kept_bad, "extra": extra,
                "correct": int(kept_good == len(KNOWN_GOOD_S)
                               and kept_bad == 0 and extra == 0)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO / "sim" / "experiments" / "out" / "e8_robustness.csv"))
    ap.add_argument("--jobs", type=int, default=max(1, cpu_count() - 2))
    args = ap.parse_args()

    grid = {
        "freefall_enter_g": [0.20, 0.24, 0.26, 0.28, 0.30, 0.32, 0.35, 0.40],
        "min_airtime_s": [0.25, 0.30, 0.35, 0.40, 0.45],
        "freefall_confirm_s": [0.06, 0.08, 0.10],
    }
    keys = list(grid)
    combos = [dict(zip(keys, v)) for v in itertools.product(*grid.values())]
    jobs = [(w, c) for w in WORLDS for c in combos]

    print(f"E8 robustness: {len(combos)} points x {len(WORLDS)} worlds "
          f"= {len(jobs)} runs")
    t0 = time.time()
    rows = []
    with Pool(args.jobs, initializer=_init) as pool:
        for i, r in enumerate(pool.imap_unordered(run, jobs, chunksize=4), 1):
            rows.append(r)
            if i % 200 == 0:
                print(f"  {i}/{len(jobs)}  ({(time.time()-t0)/60:.1f} min)", flush=True)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    # Rank operating points by how many worlds they are correct in.
    score = collections.defaultdict(int)
    detail = collections.defaultdict(list)
    for r in rows:
        k = (r["freefall_enter_g"], r["min_airtime_s"], r["freefall_confirm_s"])
        score[k] += r["correct"]
        if not r["correct"]:
            detail[k].append(r["world"])

    nw = len(WORLDS)
    ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
    print(f"\n=== operating points by worlds survived (max {nw}) ===")
    print(f"{'enter_g':>8} {'min_air':>8} {'confirm':>8}  {'survived':>8}   fails in")
    for k, v in ranked[:18]:
        fails = ",".join(detail[k][:4]) if detail[k] else "-"
        print(f"{k[0]:>8.2f} {k[1]:>8.2f} {k[2]:>8.2f}  {v:>4}/{nw}     {fails}")

    ship = (0.35, 0.25, 0.08)
    print(f"\nSHIPPED {ship}: survived {score.get(ship,0)}/{nw}"
          f"   fails in: {','.join(detail.get(ship,[])) or '-'}")
    perfect = [k for k, v in score.items() if v == nw]
    print(f"correct in ALL {nw} worlds: {len(perfect)} points")
    for k in sorted(perfect)[:12]:
        print(f"   enter_g={k[0]:.2f} min_airtime={k[1]:.2f} confirm={k[2]:.2f}")
    print(f"\ndone in {(time.time()-t0)/60:.1f} min -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
