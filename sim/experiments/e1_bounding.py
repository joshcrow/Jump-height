"""EXPERIMENT E1 - BOUNDING SWEEP.

Full-factorial grid over the aero wing model to bound the worst plausible
height overshoot the detector (h = g*T^2/8, ballistic assumption) can produce
on physically-grounded wing jumps.

Grid (4*4*4*4*3*4 = 3072 combos):
  technique      in [sheeted_out, mixed, lofted, constant]
  wind_mps       in [6, 10, 14, 18]
  c_max          in [0.4, 0.7, 1.0, 1.3]
  arm_ceiling_bw in [0.30, 0.40, 0.55, 0.75]
  mass_kg        in [70, 85, 100]
  apex (target)  in [1.0, 2.0, 3.0, 5.0]   -> vz0 = vz0_for_ballistic_apex(apex)

For each combo: aero_model(WingParams(...)), integrate_flight(vz0, vx0=0.6*wind,
dt=2e-5), render_session (fixed seed), run the firmware-twin detector.

Records per row: overshoot = detector.height_m / true_apex_m (only if DETECTED),
detected(bool), peak airborne spec_g, true_apex, true_airtime.

Deterministic: fixed integer seeds; multiprocessing results re-sorted by row
index so the CSV is byte-stable.
"""

import sys, os, csv, itertools
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wing_model as wm, sensor_model as sm, selfdiag as sd
from detector import Detector, Params

TECHNIQUES = ["sheeted_out", "mixed", "lofted", "constant"]
WINDS = [6, 10, 14, 18]
CMAXS = [0.4, 0.7, 1.0, 1.3]
CEILINGS = [0.30, 0.40, 0.55, 0.75]
MASSES = [70, 85, 100]
APEXES = [1.0, 2.0, 3.0, 5.0]

DT = 2e-5


def run_one(args):
    idx, technique, wind, c_max, ceiling, mass, apex = args
    wp = wm.WingParams(
        mass_kg=mass, wing_area_m2=5.0, wind_mps=wind, c_max=c_max,
        decay_tau_s=0.25, technique=technique, force_elev_deg=35.0,
        arm_ceiling_bw=ceiling, body_cd_a=0.5, harness=False,
    )
    wing = wm.aero_model(wp)
    vz0 = wm.vz0_for_ballistic_apex(apex)
    flight = wm.integrate_flight(
        vz0, wing, vx0=0.6 * wind, mass_kg=mass, body_cd_a=0.5, dt=DT, max_t=6.0
    )

    # Peak airborne CoM specific force (g) that an ideal accelerometer would see.
    peak_spec_g = max(flight.spec_g) if flight.spec_g else 0.0

    # Render the IMU session and run the firmware-twin detector on every sample.
    takeoff = 2.0
    times, mag, land_time = sm.render_session(
        flight, cfg=sm.SensorConfig(), takeoff_time_s=takeoff, seed=idx
    )
    det = Detector(Params())
    event = None
    for t, a in zip(times, mag):
        ev = det.update(t, a)
        if ev is not None:
            event = ev  # keep the completed jump

    detected = event is not None
    if detected and flight.true_apex_m > 0:
        overshoot = event.height_m / flight.true_apex_m
        det_height = event.height_m
        det_airtime = event.airtime_s
    else:
        overshoot = ""
        det_height = ""
        det_airtime = ""

    return {
        "idx": idx,
        "technique": technique,
        "wind_mps": wind,
        "c_max": c_max,
        "arm_ceiling_bw": ceiling,
        "mass_kg": mass,
        "target_apex_m": apex,
        "vz0": round(vz0, 6),
        "true_apex_m": round(flight.true_apex_m, 6),
        "true_airtime_s": round(flight.true_airtime_s, 6),
        "peak_spec_g": round(peak_spec_g, 6),
        "detected": int(detected),
        "det_airtime_s": det_airtime if det_airtime == "" else round(det_airtime, 6),
        "det_height_m": det_height if det_height == "" else round(det_height, 6),
        "overshoot": overshoot if overshoot == "" else round(overshoot, 6),
    }


def main():
    combos = list(itertools.product(
        TECHNIQUES, WINDS, CMAXS, CEILINGS, MASSES, APEXES
    ))
    jobs = [(i,) + c for i, c in enumerate(combos)]
    print(f"E1: {len(jobs)} combos, dt={DT}")

    with ProcessPoolExecutor() as ex:
        rows = list(ex.map(run_one, jobs, chunksize=16))
    rows.sort(key=lambda r: r["idx"])

    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "out", "e1_bounding.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fields = ["idx", "technique", "wind_mps", "c_max", "arm_ceiling_bw",
              "mass_kg", "target_apex_m", "vz0", "true_apex_m", "true_airtime_s",
              "peak_spec_g", "detected", "det_airtime_s", "det_height_m",
              "overshoot"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out_path} ({len(rows)} rows)")

    # ---------------------------------------------------------------- analysis
    det_rows = [r for r in rows if r["detected"] == 1]
    all_os = sorted(r["overshoot"] for r in det_rows)
    n_det = len(det_rows)
    frac_det = n_det / len(rows)

    def pct(sorted_vals, p):
        if not sorted_vals:
            return float("nan")
        k = (len(sorted_vals) - 1) * p
        lo = int(k)
        hi = min(lo + 1, len(sorted_vals) - 1)
        return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (k - lo)

    max_os = max(all_os) if all_os else float("nan")
    p50 = pct(all_os, 0.50)
    p90 = pct(all_os, 0.90)
    p99 = pct(all_os, 0.99)

    print(f"\n=== E1 RESULTS ({len(rows)} combos) ===")
    print(f"detected: {n_det}/{len(rows)} = {frac_det:.4f}")
    print(f"overshoot among detected: max={max_os:.4f} "
          f"p50={p50:.4f} p90={p90:.4f} p99={p99:.4f}")

    # Sensitivity: mean overshoot per arm-ceiling level (detected only).
    print("\nmean overshoot by arm_ceiling_bw (detected):")
    ceil_means = {}
    for c in CEILINGS:
        vals = [r["overshoot"] for r in det_rows if r["arm_ceiling_bw"] == c]
        m = sum(vals) / len(vals) if vals else float("nan")
        ceil_means[c] = m
        print(f"  ceiling={c:.2f}: n={len(vals):4d} mean_overshoot={m:.4f} "
              f"max={max(vals) if vals else float('nan'):.4f}")

    # Headline: worst-case overshoot for REALISTIC ceilings (<= 0.55 bw).
    realistic = [r["overshoot"] for r in det_rows if r["arm_ceiling_bw"] <= 0.55]
    max_os_real = max(realistic) if realistic else float("nan")
    print(f"\nHEADLINE worst-case overshoot, realistic ceilings (<=0.55 bw): "
          f"{max_os_real:.4f}  (n={len(realistic)})")

    # Cross-check: for 3 rows, effective sustained a_v = 1 - 1/overshoot, and
    # confirm it is a plausible fraction of the arm ceiling.
    print("\ncross-check: effective a_v = 1 - 1/overshoot vs arm ceiling")
    # pick the max-overshoot detected row + two mid rows for range.
    det_sorted = sorted(det_rows, key=lambda r: r["overshoot"])
    picks = [det_sorted[-1], det_sorted[len(det_sorted) // 2], det_sorted[0]]
    for r in picks:
        a_v = 1.0 - 1.0 / r["overshoot"]
        # arm ceiling expressed as vertical specific accel: ceiling * sin(35deg)
        import math
        ceil_vert = r["arm_ceiling_bw"] * math.sin(math.radians(35.0))
        frac = a_v / ceil_vert if ceil_vert else float("nan")
        print(f"  idx={r['idx']} tech={r['technique']} wind={r['wind_mps']} "
              f"c_max={r['c_max']} ceil={r['arm_ceiling_bw']} os={r['overshoot']:.4f} "
              f"-> a_v={a_v:.4f}g  ceil_vert={ceil_vert:.4f}g  a_v/ceil_vert={frac:.3f}")

    return dict(max_os=max_os, p50=p50, p90=p90, p99=p99, frac_det=frac_det,
                max_os_real=max_os_real, ceil_means=ceil_means, n=len(rows))


if __name__ == "__main__":
    main()
