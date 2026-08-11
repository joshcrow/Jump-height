"""Adversarial independent recompute of E1 headline max overshoot.

Bypass the sensor render + detector entirely. Overshoot the detector would
report = g*T_det^2/8 / true_apex, and T_det ~= true_airtime (anchor). So
compute the *pure* ballistic overshoot g*T_true^2/8 / true_apex directly from
integrate_flight over the SAME 3072 grid, and find the max.
"""
import sys, os, csv, itertools, math
from concurrent.futures import ProcessPoolExecutor
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wing_model as wm

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
    fl = wm.integrate_flight(vz0, wing, vx0=0.6 * wind, mass_kg=mass,
                             body_cd_a=0.5, dt=DT, max_t=6.0)
    T = fl.true_airtime_s
    apex_true = fl.true_apex_m
    ball_h = wm.G * T * T / 8.0
    overshoot = ball_h / apex_true if apex_true > 0 else float("nan")
    peak_spec_g = max(fl.spec_g) if fl.spec_g else 0.0
    # effective sustained a_v implied by the overshoot via closed form inversion
    a_v_eff = 1.0 - 1.0 / overshoot if overshoot > 0 else float("nan")
    return (idx, technique, wind, c_max, ceiling, mass, apex,
            apex_true, T, overshoot, peak_spec_g, a_v_eff)


def main():
    combos = list(itertools.product(TECHNIQUES, WINDS, CMAXS, CEILINGS, MASSES, APEXES))
    jobs = [(i,) + c for i, c in enumerate(combos)]
    print(f"grid = {len(jobs)} combos")
    with ProcessPoolExecutor() as ex:
        rows = list(ex.map(run_one, jobs, chunksize=16))
    rows.sort(key=lambda r: r[0])

    os.makedirs(os.path.join(os.path.dirname(__file__), "out"), exist_ok=True)
    out = os.path.join(os.path.dirname(__file__), "out", "e1_verify.csv")
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["idx", "technique", "wind", "c_max", "ceiling", "mass",
                    "apex", "true_apex", "T", "overshoot", "peak_spec_g", "a_v_eff"])
        w.writerows(rows)

    os_vals = sorted(r[9] for r in rows)
    worst = max(rows, key=lambda r: r[9])
    minr = min(rows, key=lambda r: r[9])

    def pct(v, p):
        k = (len(v) - 1) * p
        lo = int(k); hi = min(lo + 1, len(v) - 1)
        return v[lo] + (v[hi] - v[lo]) * (k - lo)

    print(f"MAX overshoot = {worst[9]:.4f}")
    print(f"  worst combo: tech={worst[1]} wind={worst[2]} c_max={worst[3]} "
          f"ceil={worst[4]} mass={worst[5]} apex={worst[6]}")
    print(f"  true_apex={worst[7]:.4f} T={worst[8]:.4f} a_v_eff={worst[11]:.4f} "
          f"peak_spec_g={worst[10]:.4f}")
    print(f"MIN overshoot = {minr[9]:.4f}")
    print(f"p50={pct(os_vals,0.5):.4f} p90={pct(os_vals,0.9):.4f} p99={pct(os_vals,0.99):.4f}")

    # max among realistic ceilings <= 0.55
    real = [r for r in rows if r[4] <= 0.55]
    wr = max(real, key=lambda r: r[9])
    print(f"max overshoot ceiling<=0.55 = {wr[9]:.4f}")

    # closed-form cross check on worst row a_v_eff
    cf = wm.closed_form_overshoot(worst[11])
    print(f"closed_form_overshoot(a_v_eff={worst[11]:.4f}) = {cf:.4f}")

    # ceiling==0.40 nonconstant max
    c40nc = [r for r in rows if r[4] == 0.40 and r[1] != "constant"]
    print(f"ceiling040 nonconstant max = {max(r[9] for r in c40nc):.4f}")


if __name__ == "__main__":
    main()
