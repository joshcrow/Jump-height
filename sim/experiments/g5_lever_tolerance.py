"""EXPERIMENT g5 — how precisely must the lever arm `r` be known?

g4 established that the omega^2*r correction fixes spun jumps with a PERFECT
r, and that r mis-set by +-20% reopens failures. It never asked the question
that actually governs whether a calibration scheme is viable: how tight does r
have to be?

That question turned out to matter enormously. Building the self-calibrating
estimator (firmware/include/lever_arm.h) surfaced it the hard way: a
deliberate 5% "safety" shave, added on g4's reasoning that over-estimating
erases landings, broke 5 of 8 lever x spin cases outright. The algebra says
why — in free fall the corrected magnitude is

    a_corr = rot_g * sqrt(1 - k^2),     k = r_assumed / r_true

and that square root amplifies small errors violently: k=0.99 leaves 14% of
rot_g. Since rot_g grows as omega^2 * r, at r=0.5 m and 600 dps it is 5.6 g,
so a 1% error leaves 0.79 g against a 0.35 g free-fall gate.

This maps the REAL band by running the actual detector, rather than trusting
that algebra. The two differ in ways worth knowing: the analytic bound assumes
the residual must stay under the gate for the whole flight, while the detector
only cares at the moments that decide takeoff and landing.

Outputs the usable k-band per (lever, peak spin), and marks where the
self-calibrating estimator's own achieved k lands inside it.

Deterministic (fixed seed). Pure standard library + the sim modules.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sensor_model as sm  # noqa: E402
import wing_model as wm  # noqa: E402
from detector import AIRBORNE, Detector, Params  # noqa: E402
from lever_arm import LeverArm  # noqa: E402

G = 9.80665
SEED = 20260805
TAKEOFF = 2.0
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out",
                   "g5_lever_tolerance.csv")

LEVERS = [0.2, 0.3, 0.5, 0.8]
PEAKS = [300, 600, 900]
K_MIN, K_MAX, K_STEP = 0.90, 1.20, 0.005
ERR_OK_PCT = 15.0


def burst(peak_dps, airtime, t_peak_frac=0.55, width_frac=0.20):
    peak_rps = peak_dps / 360.0
    t_peak = t_peak_frac * airtime
    sigma = max(1e-3, width_frac * airtime)

    def fn(t):
        if t < 0.0:
            return 0.0
        return peak_rps * math.exp(-((t - t_peak) ** 2) / (2.0 * sigma * sigma))
    return fn


def run(times, mags, spin_fn, lever_used):
    p = Params()
    p.spin_lever_m = lever_used
    det = Detector(p)
    ev = None
    for t, a in zip(times, mags):
        e = det.update(t, a, 360.0 * spin_fn(t - TAKEOFF))
        if e is not None:
            ev = e
    return ev


def self_cal_k(times, mags, spin_fn, true_r, airtime):
    """What k does the self-calibrating estimator actually achieve?"""
    arm = LeverArm()
    for t, a in zip(times, mags):
        dt = t - TAKEOFF
        if 0.0 < dt < airtime:
            arm.observe(a, 360.0 * spin_fn(dt))
    return arm.value() / true_r if arm.commit() else None


def main():
    vz0 = wm.vz0_for_ballistic_apex(3.0)
    flight = wm.integrate_flight(vz0, wm.const_lift(0.0), vx0=0.0, dt=2e-5)
    airtime = flight.true_airtime_s

    ref = run(*sm.render_session(flight, sm.SensorConfig(), TAKEOFF, SEED)[:2],
              spin_fn=lambda t: 0.0, lever_used=0.0)
    true_h = ref.height_m
    print(f"reference (no spin): h={true_h:.2f} m, airtime={ref.airtime_raw_s:.2f} s\n")

    rows = []
    print(f"{'lever':>6} {'dps':>5} {'rot_g':>7} | {'usable k band':>22} "
          f"{'width':>7} | {'analytic':>9} | {'selfcal k':>10}")
    for lever in LEVERS:
        for peak in PEAKS:
            spin_fn = burst(peak, airtime)
            cfg = sm.SensorConfig(lever_m=lever)
            times, mags, _ = sm.render_session(flight, cfg, TAKEOFF, SEED,
                                               spin_rps_fn=spin_fn)
            w = math.radians(peak)
            rot_g = (w * w * lever) / G

            good = []
            k = K_MIN
            while k <= K_MAX + 1e-9:
                ev = run(times, mags, spin_fn, lever * k)
                ok = ev is not None and \
                    abs(100.0 * (ev.height_m - true_h) / true_h) < ERR_OK_PCT
                if ok:
                    good.append(k)
                rows.append({"lever_m": lever, "peak_dps": peak,
                             "rot_g": round(rot_g, 3), "k": round(k, 4),
                             "ok": int(ok),
                             "height_m": round(ev.height_m, 3) if ev else None})
                k += K_STEP

            # Analytic bound: residual rot_g*sqrt(1-k^2) under the 0.35 g gate.
            if rot_g <= 0.35:
                analytic = 0.0
            else:
                analytic = math.sqrt(max(0.0, 1 - (0.35 / rot_g) ** 2))

            sk = self_cal_k(times, mags, spin_fn, lever, airtime)
            if good:
                lo, hi = min(good), max(good)
                band = f"{lo:.3f} - {hi:.3f}"
                width = f"{100*(hi-lo):.1f}%"
                inside = "" if sk is None else (" IN" if lo <= sk <= hi else " OUT!")
            else:
                band, width, inside = "none survive", "-", ""
            sks = "-" if sk is None else f"{sk:.4f}{inside}"
            print(f"{lever:>6.2f} {peak:>5} {rot_g:>7.2f} | {band:>22} {width:>7} | "
                  f"{analytic:>9.3f} | {sks:>10}")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    import csv as _csv
    with open(OUT, "w", newline="") as f:
        wtr = _csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        wtr.writeheader()
        wtr.writerows(rows)
    print(f"\nband = k values where height error stays under {ERR_OK_PCT}%")
    print("analytic = k below which the free-fall residual alone exceeds 0.35 g")
    print("selfcal k = what lever_arm.py actually achieves; IN/OUT vs the band")
    print(f"\nCSV -> {OUT}")


if __name__ == "__main__":
    main()
