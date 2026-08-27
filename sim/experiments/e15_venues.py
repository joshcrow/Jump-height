#!/usr/bin/env python3
"""E15 — the measurement error on HIS water: Roanoke Sound and the Nags Head ocean.

E14 answered the sea-state question with generic deep-water waves. The rider's
actual venues (researched 2026-08-27, sources in docs/venues.md) are both
things E14 did not model:

  SOUND (Roanoke Sound near the Manteo/Nags Head causeway): 0.5-2 m deep.
  Deep-water wave physics is simply WRONG there — at Tp 2.5 s and d 1.5 m the
  deep-water approximation needs d > L/2 ≈ 4.9 m, three times the real depth.
  Finite-depth waves are shorter and slower (celerity → sqrt(g·d) ≈ 3.8 m/s),
  which changes how far the surface drops between takeoff and landing.
  Wave inputs: USACE fetch-limited shallow-water growth estimates (flagged
  ESTIMATED — nobody has ever measured Roanoke Sound chop; there is no buoy
  in the sound, confirmed negative).

  OCEAN (Coquina Beach → Jennette's Pier): sampled from 52,230 hourly
  MEASURED (Hs, DPD) records, 2022-2024, from NDBC buoy 44086 sitting
  ~10 nmi off Jennette's Pier (data/venues/44086h*.txt.gz, committed).
  Median Hs 1.07 m @ 8.3 s; p90 2.16 m; p99 3.63 m — E14's biggest tested
  state (1.5 m) is only this ocean's ~p75, so the tail was untested.

Honest limits, where the numbers are made:
  - The buoy is in 21 m of water; the rider is just outside the break in
    ~3-5 m. Swell shoals and refracts on the way in. We model the rider at
    d=4 m with finite-depth dispersion and cap H at 0.6*d (pre-breaking) —
    a modeling CHOICE, flagged, not a measurement. A sensitivity band at
    ±30% Hs brackets it.
  - Sound wave inputs are fetch-formula estimates. The first real session
    trace from the sound will say more than this table does.
  - Same as E14: this measures the TAKEOFF-vs-LANDING-height error of
    h = g*T^2/8. It is not a wave-riding model.

Reproduce: python3 sim/experiments/e15_venues.py --n 25000
Output:    out/e15_summary.txt, out/e15_jumps.csv
"""
from __future__ import annotations

import argparse
import csv
import gzip
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


def finite_depth_k(period_s: float, depth_m: float) -> float:
    """Wavenumber from the full dispersion relation w^2 = g*k*tanh(k*d).
    Newton from the deep-water guess; converges in a handful of steps."""
    w = 2 * math.pi / period_s
    k = w * w / G                        # deep-water start
    for _ in range(30):
        t = math.tanh(k * depth_m)
        f = G * k * t - w * w
        df = G * t + G * k * depth_m * (1 - t * t)
        k2 = k - f / df
        if abs(k2 - k) < 1e-10:
            return k2
        k = k2
    return k


# --- venue condition samplers ------------------------------------------------
# SOUND: (label, wind band) -> (Hs range m, Tp range s, depth range m).
# Ranges from the 2026-08-27 fetch-limited estimates: the low end is the
# short cross-sound fetch, the high end the long down-sound (Pamlico) fetch.
# The 35 kt band is an extrapolation of the saturation trend — ESTIMATED
# twice over, and labeled so it cannot be quoted as measured.
SOUND_BANDS = [
    ("sound_12kt",      (0.10, 0.25), (1.2, 2.2), (1.2, 2.0)),
    ("sound_18kt",      (0.15, 0.40), (1.3, 2.6), (1.2, 2.0)),
    ("sound_25kt",      (0.24, 0.57), (1.5, 3.2), (1.2, 2.0)),
    ("sound_35kt_est",  (0.40, 0.75), (2.0, 3.6), (1.2, 2.0)),
]

# Takeoff speed sampled from the RIDER'S OWN riding, not assumed. 21-point
# quantile grid of his fast-reach band (>= p75 of foiling speed, 8 sessions,
# ~5,200 samples; derivation in data/nick-sessions/analysis.md — the raw GPS
# never leaves that gitignored directory, only these de-identified quantiles
# do). Median 6.5 m/s; the old assumption vx0=8.0 was his p95. Wave error
# scales with vx0 x airtime, so the fixed 8.0 overstated venue spreads ~15%.
VX0_QUANTILES = [6.01, 6.06, 6.1, 6.14, 6.19, 6.23, 6.29, 6.34, 6.39, 6.46,
                 6.52, 6.61, 6.68, 6.77, 6.88, 7.01, 7.16, 7.33, 7.55, 7.88,
                 12.05]

OCEAN_DEPTH = 4.0        # rider just outside the break — modeling choice
OCEAN_BREAK_CAP = 0.6    # H capped at this fraction of depth (pre-breaking)


def load_ocean_climatology() -> list[tuple[float, float]]:
    """(Hs, DPD) pairs measured hourly at buoy 44086, 2022-2024."""
    pairs = []
    for yr in (2022, 2023, 2024):
        p = REPO / f"data/venues/44086h{yr}.txt.gz"
        with gzip.open(p, "rt") as f:
            for ln in f:
                if ln.startswith("#"):
                    continue
                cols = ln.split()
                try:
                    hs, dpd = float(cols[8]), float(cols[9])
                except (ValueError, IndexError):
                    continue
                if hs < 90 and dpd < 90 and hs > 0 and dpd > 0:
                    pairs.append((hs, dpd))
    return pairs


def run_one(args):
    (row, label, amp, period, depth, phi, vx0) = args
    i, wind, c_max, technique, arm, mass, target_apex = row
    wing = wm.aero_model(wm.WingParams(
        mass_kg=mass, wing_area_m2=5.0, wind_mps=wind, c_max=c_max,
        decay_tau_s=0.25, technique=technique, force_elev_deg=35.0,
        arm_ceiling_bw=arm, body_cd_a=0.5, harness=False))
    vz0 = wm.vz0_for_ballistic_apex(target_apex)

    if amp > 0:
        k = finite_depth_k(period, depth)
        cel = (2 * math.pi / period) / k
    else:
        k = cel = 0.0

    def surf(x, t):
        if amp == 0.0:
            return 0.0
        return amp * math.sin(k * (x - cel * t) + phi) - amp * math.sin(phi)

    land_z = 0.0
    flight = None
    for _ in range(2):
        flight = wm.integrate_flight(
            vz0, wing, vx0=vx0, mass_kg=mass, body_cd_a=0.5, dt=DT,
            max_t=6.0, land_z=land_z)
        if flight.true_airtime_s <= 0.0:
            return None
        land_z = surf(flight.x[-1], flight.true_airtime_s)

    true_apex = flight.true_apex_m
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
        return (label, true_apex, land_z, float("nan"))
    return (label, true_apex, land_z, G * ev.airtime_raw_s ** 2 / 8.0)


def stats(errs):
    n = len(errs)
    if not n:
        return (float("nan"),) * 3
    m = sum(errs) / n
    sd = math.sqrt(sum((e - m) ** 2 for e in errs) / n) if n > 1 else 0.0
    rmse = math.sqrt(sum(e * e for e in errs) / n)
    return m, sd, rmse


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=25_000, help="jumps per cell")
    ap.add_argument("--seed", type=int, default=1501)
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    rng = random.Random(args.seed)
    base = sample_params(args.n, args.seed)
    clim = load_ocean_climatology()
    print(f"E15: ocean climatology n={len(clim)} measured hours; "
          f"{len(SOUND_BANDS) + 4} cells x {args.n} jumps "
          f"on {cpu_count()} cores", flush=True)

    def vx0():
        # inverse-CDF draw on the quantile grid, linear between knots
        u = rng.random() * 20
        i = int(u)
        f = u - i
        lo = VX0_QUANTILES[i]
        hi = VX0_QUANTILES[min(20, i + 1)]
        return lo + f * (hi - lo)

    jobs = []
    # flat control — must reproduce E14's flat row or the harness drifted
    for row in base:
        jobs.append((row, "flat_control", 0.0, 0.0, 99.0, 0.0, vx0()))
    for label, (h0, h1), (t0_, t1), (d0, d1) in SOUND_BANDS:
        for row in base:
            hs = rng.uniform(h0, h1)
            jobs.append((row, label, hs / 2.0, rng.uniform(t0_, t1),
                         rng.uniform(d0, d1), rng.uniform(0, 2 * math.pi),
                         vx0()))
    # ocean: sample MEASURED (Hs, DPD) hours; cap pre-breaking at the
    # rider's depth; sensitivity rows at +-30% Hs
    for label, scale in (("ocean_measured", 1.0),
                         ("ocean_hs-30pct", 0.7), ("ocean_hs+30pct", 1.3)):
        for row in base:
            hs, dpd = clim[rng.randrange(len(clim))]
            hs = min(hs * scale, OCEAN_BREAK_CAP * OCEAN_DEPTH)
            jobs.append((row, label, hs / 2.0, dpd, OCEAN_DEPTH,
                         rng.uniform(0, 2 * math.pi), vx0()))

    per: dict = {}
    done = 0
    with Pool(max(1, cpu_count() - 2), maxtasksperchild=2000) as pool:
        for r in pool.imap_unordered(run_one, jobs, chunksize=32):
            done += 1
            if r is None:
                continue
            label, apex, land_z, h_rep = r
            if h_rep == h_rep:
                per.setdefault(label, []).append((h_rep - apex, apex))
            if done % 25000 == 0:
                print(f"  {done}/{len(jobs)} ({time.time() - t0:.0f}s)",
                      flush=True)

    with open(OUT / "e15_jumps.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cell", "err_m", "true_apex_m"])
        for label, rows in per.items():
            for e, a in rows:
                w.writerow([label, round(e, 4), round(a, 4)])

    lines = [f"E15 — {args.n} jumps/cell, seed={args.seed}, "
             f"{time.time() - t0:.0f}s",
             "Sound waves: finite-depth Airy (the sound is 0.5-2 m deep; "
             "deep-water math is wrong there). Wave inputs ESTIMATED from "
             "USACE fetch formulas — no buoy exists in the sound.",
             "Ocean waves: sampled from 52,230 MEASURED hours at NDBC 44086 "
             "(off Jennette's Pier, 2022-24), rider modeled at d=4 m, "
             "H capped pre-breaking. 35kt sound band is a flagged "
             "extrapolation.", ""]
    order = (["flat_control"] + [b[0] for b in SOUND_BANDS] +
             ["ocean_measured", "ocean_hs-30pct", "ocean_hs+30pct"])
    lines.append(f"{'cell':18} {'n':>7} {'bias cm':>9} {'sd cm':>8} "
                 f"{'RMSE cm':>9}")
    for label in order:
        rows = per.get(label, [])
        m, sd, rmse = stats([e for e, _ in rows])
        lines.append(f"{label:18} {len(rows):7} {m * 100:9.2f} "
                     f"{sd * 100:8.2f} {rmse * 100:9.2f}")
    # session-best inflation, the number on the watch
    lines.append("")
    lines.append(f"{'cell':18} {'best inflation cm':>18}  "
                 "(bootstrap, 20-jump sessions, 2000 draws)")
    brng = random.Random(args.seed + 999)
    for label in order:
        pool_rows = per.get(label, [])
        if len(pool_rows) < 40:
            continue
        infl = 0.0
        for _ in range(2000):
            pick = [pool_rows[brng.randrange(len(pool_rows))]
                    for _ in range(20)]
            infl += (max(a + e for e, a in pick) - max(a for _, a in pick))
        lines.append(f"{label:18} {infl / 2000 * 100:+18.2f}")
    (OUT / "e15_summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
