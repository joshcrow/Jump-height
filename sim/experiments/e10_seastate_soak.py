#!/usr/bin/env python3
"""E10 — the false-positive budget, given hours instead of property tests.

The proposed water-day budget is <1 phantom jump per riding hour. Today that
number has NOTHING under it: test_seastate.py proves the detector survives
short adversarial bursts, E7/E9 replay real land motion, and no experiment
has ever measured a RATE — phantoms per hour of sustained sea-like motion —
because a rate needs exposure hours, and exposure hours need wall clock.
Tonight the wall clock is free (the DC/DC death run owns the bench anyway).

Method: seastate.py's three regimes (orbital chop, board slap, handling
chatter) plus their superpositions, at escalating intensity, in 1-hour
seeded streams, replayed through four named operating points:

    shipped   enter 0.35 / min_air 0.25   (what the puck runs today)
    e8_rec    enter 0.26 / min_air 0.30   (E8's 12-of-12-worlds point)
    plateau_a enter 0.24 / min_air 0.30   (robust-region corner)
    plateau_b enter 0.28 / min_air 0.30   (robust-region corner)

Every phantom is logged with the median-|a|-during-claimed-flight autopsy.
The per-regime rate gets a Poisson 95% upper bound, because "0 phantoms in
N hours" must never be reported as "rate is 0" — it is "rate < 3/N with 95%
confidence", which is the honest shape of a negative result (CLAUDE.md
rule 3 applied to statistics).

HONESTY, stated where the numbers are made: seastate.py is a bench stand-in
"grounded in research.md's cited numbers — not a validated model of real sea
state" (its own header). A rate measured here bounds the detector's
behaviour on THIS noise family. The water day measures the real one. What
this run CAN honestly settle is the comparison — whether the shipped point
phantoms more than the E8 plateau on identical noise — and that comparison
is what the morning decision brief needs.

Reproduce:  python3 sim/experiments/e10_seastate_soak.py [--quick]
            (full run sized for ~overnight on a laptop; deterministic seeds)
Output:     sim/experiments/out/e10_phantoms.csv, e10_rates.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from dataclasses import replace
from multiprocessing import Pool, cpu_count
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

from detector import Detector, load_params            # noqa: E402
from seastate import (                                # noqa: E402
    board_slap, handling_chatter, orbital_chop, superpose)

OUT = Path(__file__).parent / "out"

CONFIGS = {
    "shipped":   {"freefall_enter_g": 0.35, "min_airtime_s": 0.25,
                  "freefall_confirm_s": 0.08},
    "e8_rec":    {"freefall_enter_g": 0.26, "min_airtime_s": 0.30,
                  "freefall_confirm_s": 0.08},
    "plateau_a": {"freefall_enter_g": 0.24, "min_airtime_s": 0.30,
                  "freefall_confirm_s": 0.08},
    "plateau_b": {"freefall_enter_g": 0.28, "min_airtime_s": 0.30,
                  "freefall_confirm_s": 0.08},
}

# (regime label, builder(duration_s, seed) -> (times, mags))
# Intensities bracket research.md's cited numbers from below and above:
# chop to H=1.2 m is well past wind-chop; slap to 8/min at 4 g is a rough
# day; chatter 0.15 g is "in a bag on a car seat".


def _regimes():
    def chop(H, T):
        return lambda d, s: orbital_chop(H, T, d, seed=s)

    def slap(rate, peak):
        return lambda d, s: board_slap(rate, peak, d, seed=s)

    def chat(amp):
        return lambda d, s: handling_chatter(amp, d, seed=s)

    def mix(H, T, rate, peak, amp):
        def f(d, s):
            return superpose(orbital_chop(H, T, d, seed=s),
                             board_slap(rate, peak, d, seed=s + 1),
                             handling_chatter(amp, d, seed=s + 2))
        return f

    return [
        ("chop_H0.3_T3",       chop(0.3, 3.0)),
        ("chop_H0.6_T4",       chop(0.6, 4.0)),
        ("chop_H1.2_T5",       chop(1.2, 5.0)),
        ("slap_2min_2g",       slap(2.0, 2.0)),
        ("slap_4min_3g",       slap(4.0, 3.0)),
        ("slap_8min_4g",       slap(8.0, 4.0)),
        ("chatter_0.05",       chat(0.05)),
        ("chatter_0.15",       chat(0.15)),
        ("mix_moderate",       mix(0.3, 3.0, 2.0, 2.0, 0.05)),
        ("mix_rough",          mix(0.6, 4.0, 4.0, 3.0, 0.10)),
        ("mix_nasty",          mix(1.2, 5.0, 8.0, 4.0, 0.15)),
    ]


def _median(xs):
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def poisson_upper95(k: int, hours: float) -> float:
    """95% upper bound on rate/h having seen k events in `hours`.
    chi2-free form: for k=0 this is 3.00/hours (the rule of three);
    for k>0 use the Garwood bound via a small series-free approximation
    (k + 1.96*sqrt(k) + 1.92 keeps within a few % for k >= 1)."""
    if hours <= 0:
        return float("nan")
    if k == 0:
        return 3.0 / hours
    return (k + 1.96 * math.sqrt(k) + 1.92) / hours


def run_stream(args) -> list[dict]:
    (regime, seed, duration_s, cfg_items) = args
    builders = dict(_regimes())
    times, mags = builders[regime](duration_s, seed)
    out = []
    for cfg_name, combo in cfg_items:
        det = Detector(replace(load_params(), **combo))
        fed = 0
        for t, m in zip(times, mags):
            fed += 1
            ev = det.update(t, m)
            if ev is not None:
                lo = max(0, int(ev.takeoff_time_s * 200))
                hi = min(len(mags), int(
                    (ev.takeoff_time_s + ev.airtime_raw_s) * 200) + 1)
                out.append({"regime": regime, "seed": seed,
                            "config": cfg_name,
                            "t_s": round(ev.takeoff_time_s, 3),
                            "airtime_s": round(ev.airtime_s, 3),
                            "median_air_g": round(_median(
                                mags[lo:hi]) if hi > lo else float("nan"), 3)})
        assert fed == len(times), f"{regime}/{seed}: fed {fed} != {len(times)}"
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true",
                    help="1 seed x 2 regimes x 0.1 h, to prove the harness")
    ap.add_argument("--seeds", type=int, default=40,
                    help="streams per regime (default 40 x 1 h each)")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    regimes = [r for r, _ in _regimes()]
    duration = 3600.0
    seeds = list(range(args.seeds))
    if args.quick:
        regimes, seeds, duration = regimes[:2], [0], 360.0

    cfg_items = list((n, c) for n, c in CONFIGS.items())
    jobs = [(r, s, duration, cfg_items) for r in regimes for s in seeds]
    hours_per_cell = len(seeds) * duration / 3600.0
    print(f"E10: {len(regimes)} regimes x {len(seeds)} seeds x "
          f"{duration / 3600:.2f} h x {len(cfg_items)} configs "
          f"({hours_per_cell:.0f} h exposure per regime/config) "
          f"on {cpu_count()} cores", flush=True)

    t0 = time.time()
    rows: list[dict] = []
    done = 0
    with Pool(max(1, cpu_count() - 2)) as pool:
        for out in pool.imap_unordered(run_stream, jobs):
            rows.extend(out)
            done += 1
            if done % 20 == 0:
                print(f"  {done}/{len(jobs)} streams "
                      f"({time.time() - t0:.0f}s, "
                      f"{len(rows)} phantoms so far)", flush=True)

    with open(OUT / "e10_phantoms.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["regime", "seed", "config",
                                          "t_s", "airtime_s",
                                          "median_air_g"])
        w.writeheader()
        w.writerows(rows)

    counts: dict[tuple, int] = {}
    for r in rows:
        counts[(r["regime"], r["config"])] = \
            counts.get((r["regime"], r["config"]), 0) + 1
    with open(OUT / "e10_rates.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["regime", "config", "phantoms", "exposure_h",
                    "rate_per_h", "rate_upper95_per_h",
                    "meets_1_per_h_budget"])
        for regime in regimes:
            for cfg in CONFIGS:
                k = counts.get((regime, cfg), 0)
                ub = poisson_upper95(k, hours_per_cell)
                w.writerow([regime, cfg, k, round(hours_per_cell, 2),
                            round(k / hours_per_cell, 4), round(ub, 4),
                            "yes" if ub < 1.0 else "NO"])
    print(f"wrote e10_phantoms.csv ({len(rows)} phantoms) and e10_rates.csv "
          f"in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
