#!/usr/bin/env python3
"""E11 — the miss-cost of the E8 recommendation, at tail resolution.

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

Outputs (streamed, so a killed run still yields data):
    out/e11_disagreements.csv   every jump the two configs judge differently
    out/e11_misses.csv          every miss, either config, with physics
    out/e11_summary.txt         rates, paired deltas, height stats

Reproduce: python3 sim/experiments/e11_gate_miss_cost.py --n 1000 --seed 77
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

SHIPPED = {"freefall_enter_g": 0.35, "min_airtime_s": 0.25,
           "freefall_confirm_s": 0.08}
E8_REC = {"freefall_enter_g": 0.26, "min_airtime_s": 0.30,
          "freefall_confirm_s": 0.08}


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
    for name, combo in (("ship", SHIPPED), ("rec", E8_REC)):
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

    ship = pack(verdicts["ship"])
    rec = pack(verdicts["rec"])
    return (i, wind, c_max, technique, arm, mass, target_apex,
            true_apex, true_air, spec_mid, *ship, *rec)


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
    ap.add_argument("--n", type=int, default=1_000_000)
    ap.add_argument("--seed", type=int, default=77,
                    help="fresh master seed — NOT E2's, so this is an "
                         "independent draw, not a rerun")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    print(f"E11: n={args.n} seed={args.seed} dt={DT} on {cpu_count()} cores",
          flush=True)

    dis_f = open(OUT / "e11_disagreements.csv", "w", newline="")
    miss_f = open(OUT / "e11_misses.csv", "w", newline="")
    hdr = ["idx", "wind_mps", "c_max", "technique", "arm", "mass",
           "target_apex_m", "true_apex_m", "true_airtime_s", "spec_g_mid",
           "ship_det", "ship_air", "ship_h", "rec_det", "rec_air", "rec_h"]
    dis_w = csv.writer(dis_f); dis_w.writerow(hdr)
    miss_w = csv.writer(miss_f); miss_w.writerow(hdr)

    # accumulators — full rows are never held (20M rows would be ~3 GB)
    n_done = 0
    det_n = {"ship": 0, "rec": 0}
    miss_n = {"ship": 0, "rec": 0}
    both_missed = 0
    only_ship_missed = 0
    only_rec_missed = 0
    herr_sum = {"ship": 0.0, "rec": 0.0}
    herr_sq = {"ship": 0.0, "rec": 0.0}
    herr_n = {"ship": 0, "rec": 0}

    rows = sample_params(args.n, args.seed)
    with Pool(max(1, cpu_count() - 2)) as pool:
        for r in pool.imap_unordered(run_one, rows, chunksize=64):
            (i, wind, c_max, tech, arm, mass, tgt, apex, air, spec,
             sdet, sair, sh, rdet, rair, rh) = r
            n_done += 1
            row_csv = [i, round(wind, 2), round(c_max, 3), tech,
                       round(arm, 3), round(mass, 1), round(tgt, 3),
                       round(apex, 3), round(air, 3), round(spec, 4),
                       sdet, round(sair, 3) if sair == sair else "",
                       round(sh, 3) if sh == sh else "",
                       rdet, round(rair, 3) if rair == rair else "",
                       round(rh, 3) if rh == rh else ""]
            if sdet != rdet:
                dis_w.writerow(row_csv); dis_f.flush()
                if sdet and not rdet:
                    only_rec_missed += 1
                else:
                    only_ship_missed += 1
            if not sdet or not rdet:
                miss_w.writerow(row_csv)
                if not sdet and not rdet:
                    both_missed += 1
            for name, det_flag, h in (("ship", sdet, sh), ("rec", rdet, rh)):
                if det_flag:
                    det_n[name] += 1
                    e = h - apex
                    herr_sum[name] += e; herr_sq[name] += e * e
                    herr_n[name] += 1
                else:
                    miss_n[name] += 1
            if n_done % 20000 == 0:
                el = time.time() - t0
                print(f"  {n_done}/{args.n} ({el:.0f}s, "
                      f"{n_done / el:.0f}/s) misses ship={miss_n['ship']} "
                      f"rec={miss_n['rec']} disagreements="
                      f"{only_ship_missed + only_rec_missed}", flush=True)
    dis_f.close(); miss_f.close()

    lines = [f"E11 summary — n={n_done}, seed={args.seed}, dt={DT}, "
             f"{time.time() - t0:.0f}s"]
    for name in ("ship", "rec"):
        k = miss_n[name]
        lo, hi = wilson_ci(k, n_done)
        rmse = math.sqrt(herr_sq[name] / herr_n[name]) if herr_n[name] else 0
        bias = herr_sum[name] / herr_n[name] if herr_n[name] else 0
        lines.append(
            f"{name:5} missed {k}/{n_done} "
            f"({k / n_done:.2e}, 95% CI {lo:.2e}..{hi:.2e}) | "
            f"height RMSE {rmse * 100:.1f} cm bias {bias * 100:+.1f} cm "
            f"over {herr_n[name]} detections")
    lines.append(f"paired: both_missed={both_missed}  "
                 f"missed ONLY by rec={only_rec_missed}  "
                 f"missed ONLY by ship={only_ship_missed}")
    lines.append("Every disagreement and every miss is in the CSVs with its "
                 "physics; the rec-only misses' spec_g_mid values are the "
                 "0.26-0.35 band population, measured not guessed.")
    (OUT / "e11_summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
