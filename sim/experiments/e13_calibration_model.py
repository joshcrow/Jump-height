#!/usr/bin/env python3
"""E13 — does an additive airtime offset actually generalise across jump sizes?

DECISION #16 chose the bench drop ritual on an explicit physical claim:

    "Detection latency is constant in TIME, so an additive correction
     generalizes across jump sizes better than a height multiplier."

That claim has never been tested. It matters more than it looks, because the
drop ritual measures the offset at exactly ONE point — a 1 m drop, 0.455 s of
free fall — and then applies it to real jumps spanning 0.5–6 m apex, i.e.
0.6–2.2 s of airtime, a 5x range the calibration never saw. On 2026-08-24 the
OG was calibrated this way at 8 drops: bias −19 ms, `airtime_offset_s=0.0192`.

E12 then measured a residual the gate cannot explain: **+7.5 cm of systematic
height overestimate at every operating point**, 8.8–10.1 cm RMSE. If the
additive model is right, one offset should be able to drive that bias to ~0 at
every jump size simultaneously. If the residual is size-DEPENDENT, no single
additive constant can, and #16's premise is wrong in a way that biases the
water day's headline number.

Method, and why it is cheap: `sim/detector.py:175` applies the offset AFTER
the `min_airtime_s` gate (`raw < p.min_airtime_s` tests the RAW value), so the
offset cannot change WHETHER a jump is detected — only what height is
reported. So the physics runs once per jump, capturing (true_apex,
raw_airtime), and every calibration model is then evaluated analytically over
that fixed sample. Three models are compared on identical jumps:

    additive    h = g*(raw + a)^2 / 8              (#16's choice)
    multiplic.  h = s * g*raw^2 / 8                (#16's named alternative)
    both        h = s * g*(raw + a)^2 / 8

Each is fitted to minimise RMSE over the whole corpus, then its residual is
reported BINNED BY APEX. A model that is right shows flat residuals across
bins; a model that is wrong shows a trend, and the trend names the error.

Reproduce: python3 sim/experiments/e13_calibration_model.py --n 300000
Output:    out/e13_jumps.csv (raw sample), out/e13_summary.txt
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from multiprocessing import Pool, cpu_count
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))
sys.path.insert(0, str(REPO / "sim" / "experiments"))

import sensor_model as sm                      # noqa: E402
import wing_model as wm                        # noqa: E402
from detector import Detector, load_params     # noqa: E402
from e2_montecarlo import DT, sample_params    # noqa: E402

OUT = Path(__file__).parent / "out"
G = 9.80665
BINS = [(0.0, 0.75), (0.75, 1.25), (1.25, 1.75), (1.75, 2.5),
        (2.5, 3.5), (3.5, 99.0)]


def run_one(row):
    """Physics + detection once. Returns (true_apex, raw_airtime) or None."""
    i, wind, c_max, technique, arm, mass, target_apex = row
    wing = wm.aero_model(wm.WingParams(
        mass_kg=mass, wing_area_m2=5.0, wind_mps=wind, c_max=c_max,
        decay_tau_s=0.25, technique=technique, force_elev_deg=35.0,
        arm_ceiling_bw=arm, body_cd_a=0.5, harness=False))
    flight = wm.integrate_flight(
        wm.vz0_for_ballistic_apex(target_apex), wing, vx0=8.0,
        mass_kg=mass, body_cd_a=0.5, dt=DT, max_t=6.0)
    times, mag, _ = sm.render_session(
        flight, cfg=sm.SensorConfig(), takeoff_time_s=2.0, seed=i)
    det = Detector(load_params())
    ev = None
    for t, a in zip(times, mag):
        e = det.update(t, a)
        if e is not None:
            ev = e
    if ev is None:
        return None
    return (flight.true_apex_m, ev.airtime_raw_s, technique,
            flight.true_airtime_s)


def rmse_bias(sample, height_fn):
    n = 0
    s = 0.0
    sq = 0.0
    for apex, raw, _, _ta in sample:
        e = height_fn(raw) - apex
        s += e
        sq += e * e
        n += 1
    return (math.sqrt(sq / n), s / n) if n else (float("nan"),) * 2


def golden_min(lo, hi, f, iters=80):
    """1-D minimiser, no scipy. Unimodal over the range we search."""
    gr = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - gr * (b - a), a + gr * (b - a)
    for _ in range(iters):
        if f(c) < f(d):
            b, d = d, c
            c = b - gr * (b - a)
        else:
            a, c = c, d
            d = a + gr * (b - a)
    return (a + b) / 2


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300_000)
    ap.add_argument("--seed", type=int, default=91)
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    print(f"E13: n={args.n} seed={args.seed} on {cpu_count()} cores",
          flush=True)

    sample = []
    rows = sample_params(args.n, args.seed)
    done = 0
    with Pool(max(1, cpu_count() - 2)) as pool:
        for r in pool.imap_unordered(run_one, rows, chunksize=64):
            done += 1
            if r is not None:
                sample.append(r)
            if done % 20000 == 0:
                print(f"  {done}/{args.n} ({time.time() - t0:.0f}s, "
                      f"{len(sample)} detected)", flush=True)
    with open(OUT / "e13_jumps.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["true_apex_m", "raw_airtime_s", "technique",
                    "true_airtime_s"])
        for apex, raw, tech, ta in sample:
            w.writerow([round(apex, 4), round(raw, 5), tech, round(ta, 5)])

    shipped_a = load_params().airtime_offset_s
    lines = [f"E13 — n={args.n}, detected {len(sample)}, seed={args.seed}, "
             f"{time.time() - t0:.0f}s",
             f"shipped airtime_offset_s = {shipped_a} (from the 08-24 drops)",
             ""]

    def add(a):
        return lambda raw: G * (raw + a) ** 2 / 8.0

    def mul(s):
        return lambda raw: s * G * raw * raw / 8.0

    best_a = golden_min(-0.05, 0.15, lambda a: rmse_bias(sample, add(a))[0])
    best_s = golden_min(0.5, 1.5, lambda s: rmse_bias(sample, mul(s))[0])
    # joint: fit s given best_a, then re-fit a — two passes is plenty here
    best_as_s = golden_min(0.5, 1.5, lambda s: rmse_bias(
        sample, lambda raw: s * G * (raw + best_a) ** 2 / 8.0)[0])

    models = [
        ("shipped   (a=%.4f)" % shipped_a, add(shipped_a)),
        ("additive  (a=%.4f, fitted)" % best_a, add(best_a)),
        ("multiplic.(s=%.4f, fitted)" % best_s, mul(best_s)),
        ("both      (a=%.4f s=%.4f)" % (best_a, best_as_s),
         lambda raw: best_as_s * G * (raw + best_a) ** 2 / 8.0),
    ]
    lines.append(f"{'model':32} {'RMSE cm':>9} {'bias cm':>9}")
    for name, fn in models:
        r, b = rmse_bias(sample, fn)
        lines.append(f"{name:32} {r * 100:9.2f} {b * 100:+9.2f}")

    lines.append("")
    lines.append("RESIDUAL BY APEX BIN — the actual test of DECISION #16.")
    lines.append("A correct model is FLAT across bins; a trend means one "
                 "constant cannot serve every jump size.")
    lines.append(f"{'apex bin (m)':>14} {'n':>7}  " +
                 "  ".join(f"{n.split()[0]:>12}" for n, _ in models))
    for lo, hi in BINS:
        sub = [x for x in sample if lo <= x[0] < hi]
        if len(sub) < 50:
            continue
        cells = []
        for _, fn in models:
            _, b = rmse_bias(sub, fn)
            cells.append(f"{b * 100:+12.2f}")
        lines.append(f"{lo:6.2f}-{hi:5.2f} {len(sub):7}  " +
                     "  ".join(cells))
    lines.append("")
    lines.append("WHICH ERROR IS IT? — airtime vs formula, per bin.")
    lines.append("If detected-minus-true AIRTIME is the whole story, the "
                 "detector mistimes the flight and a timing correction is "
                 "right. If airtime matches but height still misses, then "
                 "h = g*T^2/8 does not describe these flights and no timing "
                 "constant can fix it.")
    lines.append(f"{'apex bin (m)':>14} {'n':>7} {'d_airtime ms':>13} "
                 f"{'h from TRUE T':>14} {'h from DET T':>13}")
    for lo, hi in BINS:
        sub = [x for x in sample if lo <= x[0] < hi]
        if len(sub) < 50:
            continue
        dta = sum((raw - ta) for _, raw, _, ta in sub) / len(sub)
        h_true = sum(G * ta * ta / 8.0 - apex
                     for apex, _, _, ta in sub) / len(sub)
        h_det = sum(G * raw * raw / 8.0 - apex
                    for apex, raw, _, _ in sub) / len(sub)
        lines.append(f"{lo:6.2f}-{hi:5.2f} {len(sub):7} {dta * 1000:13.1f} "
                     f"{h_true * 100:+13.2f} {h_det * 100:+12.2f}")
    lines.append("")
    lines.append("Columns are BIAS in cm. Read down each column: flat = the "
                 "model generalises; monotonic = it does not.")
    (OUT / "e13_summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
