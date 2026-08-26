#!/usr/bin/env python3
"""E14 — what does a non-flat water surface do to the height number?

`docs/algorithm.md` lists the assumption and waves it away in one line:

    "Symmetric parabola / takeoff ~ landing height. True for flat-water
     jumps. Landing on the face of a swell breaks it slightly; AVERAGES OUT
     IN PRACTICE."

Nothing has ever tested "averages out in practice", and every experiment in
this directory ran on perfectly flat water, because `wing_model.integrate_
flight` landed at z=0 by construction (E14 added the `land_z` parameter).

It matters because `h = g*T^2/8` assumes takeoff and landing are at the SAME
height. They are not, on water that moves. Take off from a crest and land in
a trough and the flight is longer than the symmetric one to the same apex, so
the reported height inflates — and the rider's own notion of "how high did I
get" (the session card's video ground truth: "the board's position at takeoff
as zero") is unchanged by the trough. The error is pure measurement bias.

Two questions, and they have different consequences:

  1. Does it average to ZERO over a session? If yes, the docs are right and
     chop only adds spread. If the mean is non-zero, every jump of the water
     day carries a bias no calibration measured on flat water can remove.
  2. How big is the SPREAD? Even a zero-mean error inflates per-jump RMSE,
     and the water day's accuracy claim is per-jump, not per-session.

Model: linear (Airy) deep-water waves, the same theory `sim/seastate.py`
already uses for orbital chop. Surface elevation

    eta(x, t) = a * sin(k*(x - c*t) + phi),  k = 2*pi/L,
    L = g*T^2/(2*pi),  c = L/T  (deep-water dispersion)

The wave MOVES, and its celerity is comparable to board speed (T=5 s gives
c = 7.8 m/s against vx0 = 8 m/s), so a frozen-surface approximation would be
wrong in exactly the regime that matters. Takeoff phase `phi` is sampled
uniformly: the rider is not assumed to time the swell.

Landing point depends on airtime, which depends on the landing height, which
depends on the landing point. Solved by iterating the integration twice —
enough for millimetre convergence at these speeds.

HONESTY: this is a bench model of the sea, like seastate.py. It says what
linear wave theory implies for the airtime method. It is not a measurement of
your break, and the water day remains the arbiter. What it CAN settle is
whether "averages out in practice" is a safe thing to have written down.

Reproduce: python3 sim/experiments/e14_wavy_surface.py --n 40000
Output:    out/e14_jumps.csv, out/e14_summary.txt
"""
from __future__ import annotations

import argparse
import csv
import math
import random
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

# (label, significant height Hs in m, period T in s). Hs=0 is the control and
# MUST reproduce the flat-water result, or the harness is lying.
SEA_STATES = [
    ("flat",            0.00, 0.0),
    ("ripple_0.15_2s",  0.15, 2.0),
    ("chop_0.3_3s",     0.30, 3.0),
    ("chop_0.6_4s",     0.60, 4.0),
    ("swell_1.0_6s",    1.00, 6.0),
    ("swell_1.5_8s",    1.50, 8.0),
]


def surface(a, k, c, phi, x, t):
    """Airy surface elevation, relative to the takeoff point's elevation."""
    if a == 0.0:
        return 0.0
    return a * math.sin(k * (x - c * t) + phi) - a * math.sin(phi)


def run_one(args):
    row, state_idx, phi = args
    label, hs, period = SEA_STATES[state_idx]
    i, wind, c_max, technique, arm, mass, target_apex = row
    wing = wm.aero_model(wm.WingParams(
        mass_kg=mass, wing_area_m2=5.0, wind_mps=wind, c_max=c_max,
        decay_tau_s=0.25, technique=technique, force_elev_deg=35.0,
        arm_ceiling_bw=arm, body_cd_a=0.5, harness=False))
    vz0 = wm.vz0_for_ballistic_apex(target_apex)

    if hs == 0.0:
        a = k = cel = 0.0
    else:
        a = hs / 2.0                       # amplitude = Hs/2
        L = G * period * period / (2 * math.pi)
        k = 2 * math.pi / L
        cel = L / period

    # Iterate: land_z depends on where/when you land, which depends on land_z.
    land_z = 0.0
    flight = None
    for _ in range(2):
        flight = wm.integrate_flight(
            vz0, wing, vx0=8.0, mass_kg=mass, body_cd_a=0.5, dt=DT,
            max_t=6.0, land_z=land_z)
        if flight.true_airtime_s <= 0.0:
            return None                    # never came back down: skip
        x_land = flight.x[-1]
        land_z = surface(a, k, cel, phi, x_land, flight.true_airtime_s)

    true_apex = flight.true_apex_m         # above TAKEOFF, per session card
    true_air = flight.true_airtime_s
    if true_apex <= 0.0:
        return None

    times, mag, _ = sm.render_session(
        flight, cfg=sm.SensorConfig(), takeoff_time_s=2.0, seed=i)
    det = Detector(load_params())
    ev = None
    for t, m in zip(times, mag):
        e = det.update(t, m)
        if e is not None:
            ev = e
    if ev is None:
        return (label, true_apex, land_z, float("nan"), float("nan"))
    h_rep = G * ev.airtime_raw_s ** 2 / 8.0   # raw formula, calibration aside
    return (label, true_apex, land_z, h_rep, true_air)


def stats(vals):
    n = len(vals)
    if n == 0:
        return (float("nan"),) * 3
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / n) if n > 1 else 0.0
    rmse = math.sqrt(sum(v * v for v in vals) / n)
    return mean, sd, rmse


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=40_000,
                    help="jumps PER sea state")
    ap.add_argument("--seed", type=int, default=1301)
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    print(f"E14: {len(SEA_STATES)} sea states x {args.n} jumps "
          f"on {cpu_count()} cores", flush=True)

    rng = random.Random(args.seed)
    base = sample_params(args.n, args.seed)
    jobs = []
    for si in range(len(SEA_STATES)):
        for row in base:
            jobs.append((row, si, rng.uniform(0, 2 * math.pi)))

    per = {lab: [] for lab, _, _ in SEA_STATES}
    rows_out = []
    done = 0
    with Pool(max(1, cpu_count() - 2)) as pool:
        for r in pool.imap_unordered(run_one, jobs, chunksize=32):
            done += 1
            if r is None:
                continue
            label, apex, land_z, h_rep, true_air = r
            if h_rep == h_rep:
                per[label].append((h_rep - apex, apex, land_z))
                rows_out.append([label, round(apex, 4), round(land_z, 4),
                                 round(h_rep, 4), round(true_air, 4)])
            if done % 20000 == 0:
                print(f"  {done}/{len(jobs)} ({time.time() - t0:.0f}s)",
                      flush=True)

    with open(OUT / "e14_jumps.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sea_state", "true_apex_m", "land_z_m", "h_reported_m",
                    "true_airtime_s"])
        w.writerows(rows_out)

    lines = [f"E14 — {args.n} jumps per sea state, seed={args.seed}, "
             f"{time.time() - t0:.0f}s",
             "h_reported is the RAW formula g*T^2/8 (calibration set aside), "
             "against true apex above the TAKEOFF point.",
             ""]
    lines.append(f"{'sea state':18} {'n':>7} {'bias cm':>9} {'sd cm':>8} "
                 f"{'RMSE cm':>9} {'|bias| vs flat':>15}")
    flat_bias = None
    for lab, hs, period in SEA_STATES:
        errs = [e for e, _, _ in per[lab]]
        mean, sd, rmse = stats(errs)
        if lab == "flat":
            flat_bias = mean
        delta = (mean - flat_bias) * 100 if flat_bias is not None else 0.0
        lines.append(f"{lab:18} {len(errs):7} {mean * 100:9.2f} "
                     f"{sd * 100:8.2f} {rmse * 100:9.2f} {delta:+15.2f}")

    lines.append("")
    lines.append("Q1 — does it AVERAGE OUT? Compare each row's bias to the "
                 "flat control. A number near 0 in the last column vindicates "
                 "docs/algorithm.md; a growing one does not.")
    lines.append("Q2 — how much SPREAD does it add? The sd column. Even a "
                 "zero-mean error degrades the PER-JUMP accuracy claim, which "
                 "is the one the water day makes.")
    lines.append("")
    lines.append("Bias split by whether the landing was BELOW or ABOVE the "
                 "takeoff point (the mechanism check: a trough should inflate "
                 "the reading, a crest should shrink it):")
    lines.append(f"{'sea state':18} {'n trough':>9} {'bias cm':>9}"
                 f" {'n crest':>9} {'bias cm':>9}")
    for lab, hs, period in SEA_STATES:
        if hs == 0.0:
            continue
        tro = [e for e, _, lz in per[lab] if lz < -0.02]
        cre = [e for e, _, lz in per[lab] if lz > 0.02]
        lines.append(f"{lab:18} {len(tro):9} {stats(tro)[0] * 100:9.2f}"
                     f" {len(cre):9} {stats(cre)[0] * 100:9.2f}")
    # --- the number the rider actually sees -------------------------------
    # The watch displays SESSION BEST. A maximum over jumps each carrying
    # zero-mean noise is NOT zero-mean: the max picks up whichever jump the
    # noise flattered most, so session best is biased UPWARD even when
    # per-jump bias is zero. Chop therefore inflates the headline number by a
    # mechanism the per-jump table above cannot show. Bootstrap sessions of
    # 20 jumps from each sea state's pool and compare reported-best to
    # true-best on the SAME jumps.
    lines.append("")
    lines.append("SESSION BEST — the number on the watch (bootstrap, "
                 "20-jump sessions, 2000 draws).")
    lines.append("Per-jump bias can be zero while this is not: a max over "
                 "noisy jumps selects the luckiest reading.")
    lines.append(f"{'sea state':18} {'true best m':>12} {'reported m':>11} "
                 f"{'inflation cm':>13}")
    brng = random.Random(args.seed + 999)
    for lab, hs, period in SEA_STATES:
        pool = per[lab]
        if len(pool) < 40:
            continue
        tb = rb = 0.0
        draws = 2000
        for _ in range(draws):
            pick = [pool[brng.randrange(len(pool))] for _ in range(20)]
            true_best = max(apex for _, apex, _ in pick)
            rep_best = max(apex + e for e, apex, _ in pick)
            tb += true_best
            rb += rep_best
        tb /= draws
        rb /= draws
        lines.append(f"{lab:18} {tb:12.3f} {rb:11.3f} "
                     f"{(rb - tb) * 100:+13.2f}")
    lines.append("")
    lines.append("Read the last column as: how much the session's headline "
                 "height is overstated purely because the water was moving.")

    (OUT / "e14_summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
