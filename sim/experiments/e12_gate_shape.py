#!/usr/bin/env python3
"""E12 — is there a gate between 0.26 and 0.35 that costs no misses?

E8/E9/E10 built the case for shipping enter 0.26 / min_air 0.30: robust in
12/12 perturbed worlds, no corpus counterexample, zero phantoms in 660 h of
synthetic sea noise per config. This experiment attacks the recommendation's
ONE remaining theoretical flank, which nothing has quantified:

    A lower free-fall gate widens the silent-miss blind spot. A jump is
    missed when sustained wing lift holds |a| above the gate for the whole
    flight (DECISION #30: 5 in 200,000 at the 0.35 gate, all `constant`
    technique). Lowering the gate to 0.26 means flights holding |a| in the
    0.26-0.35 band — CAUGHT today — would also be missed. The sim's spec_g
    distribution says that band is thinly populated (p99 = 0.0667 g), but
    "thin" is a guess until sampled at tail resolution, and a silent miss
    is the worst failure class there is.

Method: E2's exact physics pipeline (same sampling distribution, same
dt=2e-5 integrator, same sensor render — fidelity is NOT cheapened for
speed, because the misses live exactly where fidelity matters), with each
rendered signal fed to BOTH operating points. Paired per-jump verdicts, so
the miss-rate delta carries no cross-sample noise: every disagreement is a
specific jump, logged with its physics (spec_g, technique, wind, apex) for
autopsy. Also accumulated per config: detection rate, height RMSE/bias and
the overshoot distribution — E9 caught the shipped gate pinning a
soft-entry takeoff 0.2 s early on land; this measures the same pin-bias
across millions of water-like jumps.

Outputs:
    out/e12_misses.csv    every jump missed by ANY gate, with its physics
    out/e12_summary.txt   miss rate + height stats per gate

Reproduce: python3 sim/experiments/e12_gate_shape.py --n 200000 --seed 78
(the overnight run's N and seed are printed into its own summary).
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time
from dataclasses import replace
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

# E11 measured the two endpoints: 0.35 misses 3/400k, 0.26 misses 48/400k.
# The interesting question is the shape in between — E9 showed 0.28 and 0.30
# reject E7's 1.393 g slap on land exactly like 0.26 does, but nothing has
# measured what they cost in misses. Physics per jump is ~99% of the runtime,
# so judging five gates on one rendered flight is nearly free.
GATES = [0.26, 0.28, 0.30, 0.32, 0.35]
MINAIR = 0.30   # held at the recommendation's value throughout; E11 showed
                # the gate, not min_airtime, drives the miss rate
CONFIGS = {f"g{g:.2f}": {"freefall_enter_g": g, "min_airtime_s": MINAIR,
                         "freefall_confirm_s": 0.08} for g in GATES}


def run_one(row):
    i, wind, c_max, technique, arm, mass, target_apex = row
    wing = wm.aero_model(wm.WingParams(
        mass_kg=mass, wing_area_m2=5.0, wind_mps=wind, c_max=c_max,
        decay_tau_s=0.25, technique=technique, force_elev_deg=35.0,
        arm_ceiling_bw=arm, body_cd_a=0.5, harness=False,
    ))
    vz0 = wm.vz0_for_ballistic_apex(target_apex)
    flight = wm.integrate_flight(
        vz0, wing, vx0=8.0, mass_kg=mass, body_cd_a=0.5, dt=DT, max_t=6.0)
    true_apex = flight.true_apex_m
    true_air = flight.true_airtime_s
    spec_mid = sm._interp_spec_g(flight, true_air * 0.5) if flight.times \
        else 0.0

    times, mag, _ = sm.render_session(
        flight, cfg=sm.SensorConfig(), takeoff_time_s=2.0, seed=i)

    verdicts = {}
    for name, combo in CONFIGS.items():
        det = Detector(replace(load_params(), **combo))
        event = None
        for t, a in zip(times, mag):
            ev = det.update(t, a)
            if ev is not None:
                event = ev
        verdicts[name] = event

    def pack(ev):
        if ev is None:
            return (0, float("nan"), float("nan"))
        return (1, ev.airtime_s, ev.height_m)

    packed = []
    for name in CONFIGS:
        packed.extend(pack(verdicts[name]))
    return (i, wind, c_max, technique, arm, mass, target_apex,
            true_apex, true_air, spec_mid, *packed)


def wilson_ci(k: int, n: int) -> tuple[float, float]:
    """95% Wilson interval for a proportion — sane at k=0."""
    if n == 0:
        return (float("nan"), float("nan"))
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200_000)
    ap.add_argument("--seed", type=int, default=78,
                    help="fresh seed again — independent of E2 and E11")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    names = list(CONFIGS)
    print(f"E12: n={args.n} seed={args.seed} gates={GATES} "
          f"on {cpu_count()} cores", flush=True)

    miss_f = open(OUT / "e12_misses.csv", "w", newline="")
    mw = csv.writer(miss_f)
    mw.writerow(["idx", "wind_mps", "technique", "true_apex_m",
                 "true_airtime_s", "spec_g_mid",
                 *[f"{n}_det" for n in names]])

    n_done = 0
    miss_n = {n: 0 for n in names}
    herr_sum = {n: 0.0 for n in names}
    herr_sq = {n: 0.0 for n in names}
    herr_n = {n: 0 for n in names}

    rows = sample_params(args.n, args.seed)
    with Pool(max(1, cpu_count() - 2)) as pool:
        for r in pool.imap_unordered(run_one, rows, chunksize=64):
            (i, wind, c_max, tech, arm, mass, tgt, apex, air,
             spec) = r[:10]
            packed = r[10:]
            n_done += 1
            dets = {}
            for k, name in enumerate(names):
                det, _airs, h = packed[k * 3:k * 3 + 3]
                dets[name] = det
                if det:
                    e = h - apex
                    herr_sum[name] += e
                    herr_sq[name] += e * e
                    herr_n[name] += 1
                else:
                    miss_n[name] += 1
            if not all(dets.values()):
                mw.writerow([i, round(wind, 2), tech, round(apex, 3),
                             round(air, 3), round(spec, 4),
                             *[dets[n] for n in names]])
                miss_f.flush()
            if n_done % 20000 == 0:
                el = time.time() - t0
                print(f"  {n_done}/{args.n} ({el:.0f}s) misses "
                      + " ".join(f"{n}={miss_n[n]}" for n in names),
                      flush=True)
    miss_f.close()

    lines = [f"E12 summary — n={n_done}, seed={args.seed}, "
             f"min_airtime_s={MINAIR}, {time.time() - t0:.0f}s"]
    for name in names:
        k = miss_n[name]
        lo, hi = wilson_ci(k, n_done)
        rmse = math.sqrt(herr_sq[name] / herr_n[name]) if herr_n[name] else 0
        bias = herr_sum[name] / herr_n[name] if herr_n[name] else 0
        lines.append(
            f"{name}  missed {k}/{n_done} ({k / n_done:.2e}, "
            f"95% CI {lo:.2e}..{hi:.2e}) | RMSE {rmse * 100:.1f} cm "
            f"bias {bias * 100:+.1f} cm")
    lines.append("Every jump missed by ANY gate is in e12_misses.csv with "
                 "its physics and a per-gate detected flag, so the exact "
                 "gate at which each jump disappears is readable.")
    (OUT / "e12_summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
