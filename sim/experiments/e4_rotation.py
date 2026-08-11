"""E4 — ROTATION CONFOUND & GYRO FIX.

Quantify the rotation-arm false positive on the ballistic self-diagnosis flag,
find the minimum spin that breaks the bare flag, and prove that subtracting the
gyro-known centripetal term omega^2*r/g restores specificity.

Physics recap (sensor_model.render_session):
  a_air = hypot(spec_g, rot_g) + |noise|,  rot_g = (2*pi*spin_rps)^2 * lever_m / G
For a BALLISTIC jump const_lift(0), spec_g is ~0 (only tiny body drag), so the
airborne median is dominated by rot_g: a pure spin can masquerade as sustained
wing lift and false-positive the flag (threshold 0.12 g). A gyro knows omega and
r, so it can remove rot_g. Because rot_g enters IN QUADRATURE, the physically
correct removal is corrected = sqrt(median^2 - rot_g^2); we report the naive
linear subtraction (median - rot_g) too, as the task specifies, and show why the
quadrature form is what actually restores true-lift detection.
"""

import sys, os, csv, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wing_model as wm, sensor_model as sm, selfdiag as sd
from detector import Detector, Params

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT, exist_ok=True)

FLAG_THRESH = 0.12     # self-diag non-ballistic flag
GATE_G = 0.35          # detector free-fall gate (Params.freefall_enter_g)
# Sensor noise floor bias on the median: render adds +|gauss(0, noise_g)| on top
# of hypot(spec_g, rot_g). The median of a half-normal is 0.6745*sigma, a
# device-characterizable constant the gyro path removes before quadrature.
NOISE_FLOOR_G = 0.6745 * sm.SensorConfig().noise_g   # = 0.0135 g
SEEDS = list(range(10))  # fixed deterministic seed set for the flag RATE
DT = 2e-5
TAKEOFF = 2.0

SPINS = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5]
LEVERS = [0.1, 0.3, 0.5, 0.8]


def analytic_rot_g(spin_rps, lever_m):
    omega = 2.0 * math.pi * spin_rps
    return (omega * omega * lever_m) / wm.G


def render_feat(flight, spin_rps, lever_m, seed):
    cfg = sm.SensorConfig(spin_rps=spin_rps, lever_m=lever_m)
    times, mag, land = sm.render_session(flight, cfg=cfg, takeoff_time_s=TAKEOFF, seed=seed)
    feat = sd.air_window_features(times, mag, TAKEOFF, land)
    return feat, times, mag, land


def detector_detects(times, mag):
    det = Detector(Params())
    for t, a in zip(times, mag):
        ev = det.update(t, a)
        if ev is not None:
            return True
    return False


def main():
    # A modest ballistic jump: apex ~2 m -> airtime ~1.28 s (well inside the
    # detector's [0.25, 3.0] s window and long enough for a trimmed air window).
    vz0 = wm.vz0_for_ballistic_apex(2.0)
    ball = wm.integrate_flight(vz0, wm.const_lift(0.0), dt=DT)
    print(f"ballistic: apex={ball.true_apex_m:.3f} m  airtime={ball.true_airtime_s:.3f} s  "
          f"mid spec_g={ball.spec_g[len(ball.spec_g)//2]:.4f} g")

    # A truly non-ballistic jump for the specificity check: const_lift(0.2)
    # (0.20 g sustained -> analytic 1.25x overshoot; spec_g == 0.20 mid-flight).
    lift = wm.integrate_flight(vz0, wm.const_lift(0.20), dt=DT)
    print(f"lift(0.2): apex={lift.true_apex_m:.3f} m  airtime={lift.true_airtime_s:.3f} s  "
          f"mid spec_g={lift.spec_g[len(lift.spec_g)//2]:.4f} g")

    rows = []
    for lever in LEVERS:
        for spin in SPINS:
            rot = analytic_rot_g(spin, lever)
            # deterministic anchor at seed 0
            feat0, t0, m0, _ = render_feat(ball, spin, lever, seed=0)
            med0 = feat0.median_g
            detected0 = detector_detects(t0, m0)
            # flag rate over the fixed seed set
            flags = 0
            for s in SEEDS:
                f, _, _, _ = render_feat(ball, spin, lever, seed=s)
                if sd.flag_nonballistic(f, FLAG_THRESH):
                    flags += 1
            flag_rate = flags / len(SEEDS)
            # naive linear subtraction (as literally specified)
            corr_lin = max(0.0, med0 - rot)
            # plain quadrature removal of the arm term
            corr_quad = math.sqrt(max(0.0, med0 * med0 - rot * rot))
            # physically-correct gyro path: strip the characterized noise floor,
            # then remove rot_g in quadrature (that is how it entered the signal)
            corr_gyro = math.sqrt(max(0.0, (med0 - NOISE_FLOOR_G) ** 2 - rot * rot))
            rows.append(dict(
                spin_rps=spin, lever_m=lever,
                analytic_rot_g=rot,
                measured_median_g=med0,
                flag_bare=int(med0 > FLAG_THRESH),
                above_gate=int(med0 > GATE_G),
                detector_detects=int(detected0),
                flag_rate=flag_rate,
                corr_median_lin_g=corr_lin,
                flag_after_gyro_lin=int(corr_lin > FLAG_THRESH),
                corr_median_quad_g=corr_quad,
                flag_after_gyro_quad=int(corr_quad > FLAG_THRESH),
                corr_median_gyro_g=corr_gyro,
                flag_after_gyro=int(corr_gyro > FLAG_THRESH),
            ))

    csv_path = os.path.join(OUT, "e4_rotation.csv")
    with open(csv_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {csv_path} ({len(rows)} rows)")

    # ---- (1) minimum spin at lever=0.3 that false-positives the bare flag ----
    lev = 0.3
    min_break = None
    for spin in SPINS:
        rot = analytic_rot_g(spin, lev)
        feat0, _, _, _ = render_feat(ball, spin, lev, seed=0)
        broke = feat0.median_g > FLAG_THRESH
        print(f"  lever=0.3 spin={spin:4.2f}  rot_g={rot:.4f}  median_g={feat0.median_g:.4f}  "
              f"flag={'YES' if broke else 'no'}")
        if broke and min_break is None:
            min_break = spin
    print(f"MIN SPIN @lever=0.3 that breaks bare flag: {min_break} rps")

    # analytic crossover: rot_g == 0.12 -> spin = sqrt(0.12*G/lever)/(2pi)
    spin_cross = math.sqrt(FLAG_THRESH * wm.G / lev) / (2 * math.pi)
    print(f"  analytic crossover spin (rot_g=0.12) @lever=0.3: {spin_cross:.3f} rps")

    # ---- (2) gyro restores specificity: FP rate on ballistic collapses,
    #          true lift(0.2) stays flagged -------------------------------------
    # bare FP rate across the WHOLE grid (ballistic should never flag)
    bare_fp = sum(r["flag_bare"] for r in rows) / len(rows)
    gyro_fp_lin = sum(r["flag_after_gyro_lin"] for r in rows) / len(rows)
    gyro_fp_quad = sum(r["flag_after_gyro_quad"] for r in rows) / len(rows)
    gyro_fp = sum(r["flag_after_gyro"] for r in rows) / len(rows)
    print(f"ballistic grid FP rate: bare={bare_fp:.3f}  lin={gyro_fp_lin:.3f}  "
          f"quad={gyro_fp_quad:.3f}  gyro(quad+noisefloor)={gyro_fp:.3f}")

    # true lift(0.2) @lever=0.3 across spins: does each correction keep it flagged?
    kept_gyro = kept_quad = kept_lin = total = 0
    for spin in SPINS:
        rot = analytic_rot_g(spin, lev)
        f, _, _, _ = render_feat(lift, spin, lev, seed=0)
        med = f.median_g
        cl = max(0.0, med - rot)
        cq = math.sqrt(max(0.0, med * med - rot * rot))
        cg = math.sqrt(max(0.0, (med - NOISE_FLOOR_G) ** 2 - rot * rot))
        total += 1
        kept_lin += int(cl > FLAG_THRESH)
        kept_quad += int(cq > FLAG_THRESH)
        kept_gyro += int(cg > FLAG_THRESH)
        print(f"  lift(0.2) lever=0.3 spin={spin:4.2f}  rot_g={rot:.4f}  median={med:.4f}  "
              f"gyro={cg:.4f}({'flag' if cg>FLAG_THRESH else 'MISS'})  "
              f"lin={cl:.4f}({'flag' if cl>FLAG_THRESH else 'MISS'})")
    print(f"true-lift(0.2) retained: gyro={kept_gyro}/{total}  quad={kept_quad}/{total}  "
          f"lin={kept_lin}/{total}")

    return dict(
        min_break=min_break, spin_cross=spin_cross,
        bare_fp=bare_fp, gyro_fp=gyro_fp, gyro_fp_quad=gyro_fp_quad, gyro_fp_lin=gyro_fp_lin,
        kept_gyro=kept_gyro, kept_quad=kept_quad, kept_lin=kept_lin, total=total,
        csv_path=csv_path,
    )


if __name__ == "__main__":
    main()
