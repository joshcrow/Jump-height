"""EXPERIMENT E2 — MONTE CARLO over a realistic wing-foil jump population.

Sample N=5000 seeded wing jumps across realistic distributions of wind, wing
coefficient, technique, arm ceiling, rider mass, and jump size. For each jump:
integrate the true CoM trajectory (dt=2e-5), render the IMU session, and run the
firmware-twin detector. Record the ballistic overshoot (detector height / true
apex) and the absolute height error in meters (detector.height_m - true_apex_m).

Outputs out/e2_montecarlo.csv (one row per sampled jump) and prints the summary
statistics: mean/p50/p90/p99 overshoot, fraction >1.10x / >1.20x, fraction of
jumps the detector MISSED, and the RMSE of height error over detected jumps —
compared against the Marcis 2021 kite-device benchmark (Surfr 0.51 m, WOO3
0.70 m).

Deterministic: master seed random.Random(12345) draws every parameter serially;
each jump's render seed is its index, so results are independent of the parallel
execution order.
"""

import sys, os, csv, math, random
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wing_model as wm, sensor_model as sm, selfdiag as sd
from detector import Detector, Params

# N and OUT are env-overridable so a tail-resolution run needs no code edit:
#
#   E2_N=200000 E2_OUT=/tmp/e2_200k.csv python3 sim/experiments/e2_montecarlo.py
#
# Defaults are unchanged, and the master seed draws parameters serially by
# index, so jump i is identical at any N — a bigger run REPRODUCES a smaller
# one row for row and only appends. (Verified 2026-08-11: the first 5000 rows
# of a 200k run are bit-identical to the committed 5000-row CSV.)
N = int(os.environ.get("E2_N", "5000"))
MASTER_SEED = 12345
DT = 2e-5
OUT = os.environ.get(
    "E2_OUT",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "e2_montecarlo.csv"))

TECHNIQUES = ["sheeted_out", "mixed", "lofted", "constant"]
TECH_WEIGHTS = [0.60, 0.25, 0.12, 0.03]


def _clip(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def sample_params(n, seed):
    """Draw n parameter sets serially from one seeded RNG (deterministic)."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        wind = _clip(rng.gauss(11.0, 3.0), 4.0, 22.0)
        c_max = _clip(rng.gauss(0.7, 0.25), 0.2, 1.4)
        technique = rng.choices(TECHNIQUES, weights=TECH_WEIGHTS, k=1)[0]
        arm = _clip(rng.gauss(0.40, 0.08), 0.2, 0.7)
        mass = _clip(rng.gauss(85.0, 10.0), 55.0, 120.0)
        # Jump size: lognormal so most sit in 0.5-4 m with a thin tail to ~6 m.
        target_apex = _clip(rng.lognormvariate(math.log(1.5), 0.55), 0.5, 6.5)
        rows.append((i, wind, c_max, technique, arm, mass, target_apex))
    return rows


def run_one(row):
    """Integrate, render, detect one jump. Returns a result dict. Runs in a
    worker process; the per-jump render seed is the jump index."""
    i, wind, c_max, technique, arm, mass, target_apex = row

    wing = wm.aero_model(wm.WingParams(
        mass_kg=mass, wing_area_m2=5.0, wind_mps=wind, c_max=c_max,
        decay_tau_s=0.25, technique=technique, force_elev_deg=35.0,
        arm_ceiling_bw=arm, body_cd_a=0.5, harness=False,
    ))
    vz0 = wm.vz0_for_ballistic_apex(target_apex)
    flight = wm.integrate_flight(
        vz0, wing, vx0=8.0, mass_kg=mass, body_cd_a=0.5, dt=DT, max_t=6.0,
    )
    true_apex = flight.true_apex_m
    true_airtime = flight.true_airtime_s

    # Mid-flight specific force (the self-diagnosis / lift readout, in g).
    if flight.times:
        mid_t = true_airtime * 0.5
        spec_mid = sm._interp_spec_g(flight, mid_t)
    else:
        spec_mid = 0.0

    # Render the IMU session (default sensor, no spin confound) and detect.
    cfg = sm.SensorConfig()
    times, mag, land_time = sm.render_session(
        flight, cfg=cfg, takeoff_time_s=2.0, seed=i,
    )
    det = Detector(Params())
    event = None
    for t, a in zip(times, mag):
        ev = det.update(t, a)
        if ev is not None:
            event = ev  # keep the (single) completed jump

    detected = event is not None
    if detected:
        det_air = event.airtime_s
        det_h = event.height_m
        overshoot = det_h / true_apex if true_apex > 0 else float("nan")
        herr = det_h - true_apex
    else:
        det_air = float("nan")
        det_h = float("nan")
        overshoot = float("nan")
        herr = float("nan")

    return {
        "idx": i, "wind_mps": wind, "c_max": c_max, "technique": technique,
        "arm_ceiling_bw": arm, "mass_kg": mass, "target_apex_m": target_apex,
        "true_apex_m": true_apex, "true_airtime_s": true_airtime,
        "spec_g_mid": spec_mid, "detected": int(detected),
        "det_airtime_s": det_air, "det_height_m": det_h,
        "overshoot": overshoot, "height_err_m": herr,
    }


def percentile(sorted_vals, p):
    """Linear-interpolated percentile p in [0,100] over a sorted list."""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)


def main():
    rows = sample_params(N, MASTER_SEED)

    results = [None] * N
    with ProcessPoolExecutor(max_workers=os.cpu_count()) as ex:
        for res in ex.map(run_one, rows, chunksize=16):
            results[res["idx"]] = res

    # Write CSV (deterministic order by idx).
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fields = ["idx", "wind_mps", "c_max", "technique", "arm_ceiling_bw",
              "mass_kg", "target_apex_m", "true_apex_m", "true_airtime_s",
              "spec_g_mid", "detected", "det_airtime_s", "det_height_m",
              "overshoot", "height_err_m"]
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in results:
            w.writerow(r)

    # --- Statistics ---------------------------------------------------------
    detected = [r for r in results if r["detected"]]
    missed = [r for r in results if not r["detected"]]
    n_det = len(detected)
    frac_missed = len(missed) / N

    over = sorted(r["overshoot"] for r in detected)
    mean_over = sum(over) / len(over) if over else float("nan")
    p50 = percentile(over, 50)
    p90 = percentile(over, 90)
    p99 = percentile(over, 99)
    frac_110 = sum(1 for o in over if o > 1.10) / len(over) if over else float("nan")
    frac_120 = sum(1 for o in over if o > 1.20) / len(over) if over else float("nan")

    herrs = [r["height_err_m"] for r in detected]
    rmse = math.sqrt(sum(e * e for e in herrs) / len(herrs)) if herrs else float("nan")
    mean_err = sum(herrs) / len(herrs) if herrs else float("nan")
    mae = sum(abs(e) for e in herrs) / len(herrs) if herrs else float("nan")

    apex_lo = min(r["true_apex_m"] for r in results)
    apex_hi = max(r["true_apex_m"] for r in results)

    print(f"N sampled            : {N}")
    print(f"detected             : {n_det}  ({100*n_det/N:.2f}%)")
    print(f"MISSED               : {len(missed)}  (frac {frac_missed:.4f})")
    print(f"true apex range      : {apex_lo:.2f} .. {apex_hi:.2f} m")
    print(f"overshoot mean       : {mean_over:.4f}x")
    print(f"overshoot p50        : {p50:.4f}x")
    print(f"overshoot p90        : {p90:.4f}x")
    print(f"overshoot p99        : {p99:.4f}x")
    print(f"frac >1.10x          : {frac_110:.4f}")
    print(f"frac >1.20x          : {frac_120:.4f}")
    print(f"height err mean (bias): {mean_err:+.4f} m")
    print(f"height err MAE       : {mae:.4f} m")
    print(f"height err RMSE      : {rmse:.4f} m   (Marcis21: Surfr 0.51, WOO3 0.70, WOO2 0.95)")
    print(f"CSV -> {OUT}")

    # Per-technique missed breakdown (why detection fails: sustained lift).
    for tech in TECHNIQUES:
        grp = [r for r in results if r["technique"] == tech]
        gm = sum(1 for r in grp if not r["detected"])
        if grp:
            print(f"  {tech:12s}: n={len(grp):4d} missed={gm:3d} "
                  f"({100*gm/len(grp):.1f}%)")


if __name__ == "__main__":
    main()
