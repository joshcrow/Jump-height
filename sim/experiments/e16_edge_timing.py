#!/usr/bin/env python3
"""E16 — the −19 ms mystery: why the real board mistimes what the sim does not.

The facts in tension. E13: the detector on RENDERED signals times flights to
~+2 ms at every jump size. The bench, 2026-08-24: 8 real drops measured
**−19 ms ± 9** (airtime SHORT), now applied to every jump as
airtime_offset_s = +0.0192. Whatever produces the real bias is therefore NOT
in the rendering — and `sensor_model.render_session` shows exactly what is
missing: its takeoff is a STEP (riding → airborne in one sample) and its
landing spike rises instantly. Real events have shaped edges:

  takeoff  a hand releasing a board unloads over tens of ms (fingers carry
           less and less); a foil leaving water unloads over roughly
           chord / exit-speed ≈ 50–150 ms. The 0.35 g gate is crossed
           partway down that ramp → takeoff pinned LATE → airtime SHORT.
  landing  a cushion decelerates the board over 10–40 ms (soft rise to
           threshold → landing pinned LATE → airtime LONG-biased); water
           slap is a few ms. The two edges FIGHT, so net bias depends on
           both shapes — which is why it must be mapped, not reasoned out.
  filter   the IMU's internal LPF smooths both edges. A pure delay would
           cancel in a DURATION measurement; a low-pass does not cancel,
           because the two edges have different slopes and a filter shifts
           a threshold crossing by a slope-dependent amount.

Method: synthetic |a|(t) built at 2 kHz with parameterized edges — unload
time constant, landing rise time, single-pole LPF cutoff, true airtime —
then decimated to the real 200 Hz and fed to the REAL detector (shipped
config). Hundreds of noise seeds per cell. No wing ODE: this experiment
isolates edge timing, and a controlled airborne floor does that better than
a full flight would.

The three questions it answers:
 1. WHICH edge shapes reproduce the bench's −19 ± 9 ms at the drop ritual's
    T = 0.455 s? (The cushion-drop region of the map should contain it, or
    the model is wrong and says so.)
 2. Is the bias CONSTANT IN T for a given shape? DECISION #16's entire
    premise — "detection latency is constant in time, so an additive
    correction generalizes" — is tested here, per shape, not assumed.
 3. What bias does the WATER region of the map predict (foil-exit unload
    50–150 ms, water-slap rise 2–10 ms)? The difference from the applied
    +19.2 ms correction is the water-day height-error risk, reported in ms
    and in cm at a 1 s jump.

Reproduce: python3 sim/experiments/e16_edge_timing.py [--quick]
Output:    out/e16_map.csv, out/e16_summary.txt
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import sys
import time
from dataclasses import replace
from multiprocessing import Pool, cpu_count
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

from detector import Detector, load_params     # noqa: E402

OUT = Path(__file__).parent / "out"
FS_TRUE = 2000.0
FS_DET = 200.0
G = 9.80665

TAUS_MS = list(range(0, 181, 10))              # takeoff unload constant
RISES_MS = [1, 3, 5, 8, 12, 16, 22, 30, 40, 60]  # landing rise time
FCS_HZ = [0, 50, 25, 12]                       # IMU LPF cutoff; 0 = none
TRUE_T = [0.35, 0.455, 0.6, 0.8, 1.0, 1.3, 1.6, 2.0]
AIR_FLOOR_G = 0.05                             # spec force while airborne
LAND_PEAK_G = 4.8                              # Simons '25: 4.2-5.5 g
DROP_MEASURED_MS = (-28.0, -10.0)              # bench: -19 +/- 9


def synth(t_true: float, tau_s: float, rise_s: float, seed: int):
    """|a|(t) at 2 kHz: riding -> shaped unload -> floor -> shaped landing."""
    rng = random.Random(seed)
    pre, post = 1.5, 1.0
    n = int((pre + t_true + rise_s + 0.4 + post) * FS_TRUE)
    out = []
    t_land = pre + t_true
    for i in range(n):
        t = i / FS_TRUE
        chop = rng.gauss(0.0, 0.12)
        if t < pre:
            a = 1.0 + chop                      # riding
        elif t < t_land:
            dt = t - pre
            if tau_s > 0:
                # exponential unload from 1 g toward the airborne floor:
                # the gate is crossed partway down this ramp, late.
                a = AIR_FLOOR_G + (1.0 - AIR_FLOOR_G) * math.exp(-dt / tau_s)
            else:
                a = AIR_FLOOR_G
            a += abs(rng.gauss(0.0, 0.02))
        elif t < t_land + rise_s:
            # landing: linear rise to the peak over rise_s
            a = AIR_FLOOR_G + (LAND_PEAK_G - AIR_FLOOR_G) * ((t - t_land) / rise_s)
            a += rng.gauss(0.0, 0.3)
        elif t < t_land + rise_s + 0.05:
            a = LAND_PEAK_G + rng.gauss(0.0, 0.5)
        else:
            a = 1.0 + chop                      # riding again
        out.append(max(0.0, min(a, 16.0)))
    return out


def lpf_decimate(sig, fc_hz: float):
    """Single-pole IIR at fc (the IMU's composite filter stand-in), then
    pick every FS_TRUE/FS_DET-th sample."""
    step = int(FS_TRUE / FS_DET)
    if fc_hz <= 0:
        return sig[::step]
    a = math.exp(-2 * math.pi * fc_hz / FS_TRUE)
    y = sig[0]
    out = []
    for i, x in enumerate(sig):
        y = a * y + (1 - a) * x
        if i % step == 0:
            out.append(y)
    return out


def run_cell(args):
    tau_ms, rise_ms, fc, t_true, seeds = args
    det_p = load_params()
    biases = []
    for s in range(seeds):
        sig = synth(t_true, tau_ms / 1000.0, rise_ms / 1000.0,
                    seed=hash((tau_ms, rise_ms, fc, int(t_true * 1000), s)) & 0xffffffff)
        stream = lpf_decimate(sig, fc)
        det = Detector(det_p)
        ev = None
        for i, a in enumerate(stream):
            e = det.update(i / FS_DET, a)
            if e is not None:
                ev = e
        if ev is not None:
            biases.append(ev.airtime_raw_s - t_true)
    if not biases:
        return (tau_ms, rise_ms, fc, t_true, 0, float("nan"), float("nan"))
    m = sum(biases) / len(biases)
    sd = math.sqrt(sum((b - m) ** 2 for b in biases) / len(biases))
    return (tau_ms, rise_ms, fc, t_true, len(biases), m * 1000, sd * 1000)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=300)
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    taus, rises, fcs, ts, seeds = TAUS_MS, RISES_MS, FCS_HZ, TRUE_T, args.seeds
    if args.quick:
        taus, rises, fcs, ts, seeds = [0, 60, 120], [3, 20], [0, 25], [0.455, 1.0], 30
    jobs = [(a, r, f, t, seeds) for a in taus for r in rises
            for f in fcs for t in ts]
    print(f"E16: {len(jobs)} cells x {seeds} seeds on {cpu_count()} cores",
          flush=True)
    rows = []
    done = 0
    with Pool(max(1, cpu_count() - 2), maxtasksperchild=500) as pool:
        for r in pool.imap_unordered(run_cell, jobs, chunksize=8):
            rows.append(r)
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(jobs)} ({time.time() - t0:.0f}s)",
                      flush=True)
    with open(OUT / "e16_map.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["tau_ms", "rise_ms", "lpf_hz", "true_T_s", "n_detected",
                    "bias_ms", "sd_ms"])
        w.writerows(sorted(rows))

    # --- which shapes reproduce the bench at the ritual's T? ---------------
    lines = [f"E16 — {len(rows)} cells x {seeds} seeds, {time.time() - t0:.0f}s",
             f"bench target: drop ritual measured {DROP_MEASURED_MS} ms at "
             f"T=0.455 s", ""]
    at_drop = [r for r in rows if abs(r[3] - 0.455) < 1e-9 and r[4] > 0]
    match = [r for r in at_drop
             if DROP_MEASURED_MS[0] <= r[5] <= DROP_MEASURED_MS[1]]
    lines.append(f"cells matching the bench window at T=0.455: {len(match)} "
                 f"of {len(at_drop)}")
    for r in sorted(match, key=lambda r: r[5])[:12]:
        lines.append(f"  tau={r[0]:3}ms rise={r[1]:2}ms lpf={r[2]:2}Hz "
                     f"-> bias {r[5]:+6.1f} +/- {r[6]:.1f} ms")

    # --- DECISION #16 constancy check for the matching shapes --------------
    lines.append("")
    lines.append("Is the bias constant in T (the additive-offset premise)? "
                 "spread of per-T bias for each bench-matching shape:")
    for (tau, rise, fc) in sorted({(r[0], r[1], r[2]) for r in match}):
        per_t = sorted((r[3], r[5]) for r in rows
                       if (r[0], r[1], r[2]) == (tau, rise, fc) and r[4] > 0)
        biases = [b for _, b in per_t]
        if len(biases) < 3:
            continue
        lines.append(f"  tau={tau:3} rise={rise:2} lpf={fc:2}: bias "
                     f"{min(biases):+5.1f}..{max(biases):+5.1f} ms across "
                     f"T=0.35..2.0 (spread {max(biases) - min(biases):.1f} ms)")

    # --- water prediction --------------------------------------------------
    lines.append("")
    lines.append("WATER region (foil-exit unload 50-150 ms, slap rise 2-10 ms),"
                 " bias at T=1.0 s:")
    water = [r for r in rows if 50 <= r[0] <= 150 and r[1] <= 10
             and abs(r[3] - 1.0) < 1e-9 and r[4] > 0]
    if water:
        bs = sorted(r[5] for r in water)
        lines.append(f"  bias {bs[0]:+.1f}..{bs[-1]:+.1f} ms "
                     f"(median {bs[len(bs)//2]:+.1f})")
        med = bs[len(bs) // 2]
        resid = med + 19.2   # applied correction is +19.2 ms
        h_err = G * 1.0 * resid / 1000 / 4  # d(h)/d(T) * dT at T=1: g*T/4
        lines.append(f"  after the +19.2 ms correction: residual "
                     f"{resid:+.1f} ms  ->  {h_err * 100:+.1f} cm on a 1 s "
                     f"jump (h≈1.2 m)")
        lines.append("  (positive residual = heights read HIGH; negative = "
                     "LOW)")
    (OUT / "e16_summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
