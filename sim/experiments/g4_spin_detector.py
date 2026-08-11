"""EXPERIMENT g4 — does a mid-air SPIN BURST corrupt the real airtime detector,
and does inlining a per-sample omega^2*r subtraction recover it?

This is the one gyro sim with genuine leverage (docs/gyro-sim-plan.md): its
output is emergent STATEFUL detector behaviour, not closed-form algebra. E4
only corrected the self-diag *median*; it never fed a per-sample-corrected
stream into the real detector.Detector state machine. Prior art (WOO's 15-20%
height error growing with jump size) is the symptom; nobody has published the
fix on a detector hot path.

Mechanism: the board-mounted accelerometer reads |a| = hypot(specific_force,
omega^2*r/g). At takeoff the rider is not yet spinning, so |a|~0 and the 0.35 g
free-fall gate confirms the launch. Mid-air the spin ramps; if omega^2*r/g
crosses the 2.5 g LANDING threshold the detector fires a FALSE LANDING and
truncates airtime -> large height under-read (or, if the truncated airtime <
0.25 s, the jump is rejected entirely).

The gyro fix: the firmware knows omega(t) from the gyro, so it can subtract the
centripetal term per sample before the detector sees it:
    a_corr(t) = sqrt(max(0, |a|(t)^2 - (omega(t)^2 * r_assumed / g)^2))
This experiment feeds a_corr INTO a real Detector and maps, over peak-spin x
lever-arm:
  (1) where the RAW detector false-lands,
  (2) whether perfect-r correction pushes that break-point past the fastest
      realistic tricks (~900 dps), and
  (3) how a +-20% lever-arm mis-calibration erodes the fix — under-cal leaves a
      residual false-landing, over-cal can ERASE the real landing spike
      (sqrt arg driven negative -> clamped to 0 -> the detector never sees the
      touchdown and the jump runs away / is missed).

The decision it changes: gyro = a recording-only trick-metric extra vs a
DETECTOR HOT-PATH input that must be powered and settled BY takeoff and inlined
into jump_detector.h.

Deterministic (fixed seed). Pure standard library.
"""

import sys, os, csv, math

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wing_model as wm
import sensor_model as sm
from detector import Detector, Params

G = 9.80665
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "g4_spin_detector.csv")
SEED = 20260805


def burst(peak_dps, airtime, t_peak_frac=0.55, width_frac=0.20):
    """A spin burst in rev/s vs time-since-takeoff: ~0 at takeoff (so free-fall
    confirms), a Gaussian bump peaking at t_peak_frac of the airtime. Returns a
    callable t->rps. Peak given in deg/s."""
    peak_rps = (peak_dps / 360.0)
    t_peak = t_peak_frac * airtime
    sigma = max(1e-3, width_frac * airtime)

    def fn(t):
        if t < 0.0:
            return 0.0                       # not spinning while still riding in
        return peak_rps * math.exp(-((t - t_peak) ** 2) / (2.0 * sigma * sigma))
    return fn


def detect(times, mags):
    """Run a fresh detector over a stream; return the (single) JumpEvent or None."""
    det = Detector(Params())
    ev = None
    for t, a in zip(times, mags):
        e = det.update(t, a)
        if e is not None:
            ev = e
    return ev


def corrected_stream(times, mags, takeoff, spin_fn, lever_assumed):
    """Per-sample gyro correction: subtract (omega^2 * r_assumed / g) in
    quadrature. omega(t) is what the gyro measures = the true spin profile."""
    out = []
    for t, a in zip(times, mags):
        w = 2.0 * math.pi * spin_fn(t - takeoff)
        rot_c = (w * w * lever_assumed) / G
        out.append(math.sqrt(max(0.0, a * a - rot_c * rot_c)))
    return out


def main():
    # --- reference ballistic jump (no spin): what a correct detector should report ---
    apex = 3.0
    vz0 = wm.vz0_for_ballistic_apex(apex)
    flight = wm.integrate_flight(vz0, wm.const_lift(0.0), vx0=0.0, dt=2e-5)
    airtime = flight.true_airtime_s
    takeoff = 2.0

    base_ev = detect(*sm.render_session(flight, sm.SensorConfig(), takeoff, SEED)[:2])
    true_h = base_ev.height_m
    true_T = base_ev.airtime_s
    # SELF-CHECK: with spin=0 the extended core must reproduce the ballistic result.
    assert abs(true_h - flight.true_apex_m) / flight.true_apex_m < 0.05, "ballistic pipeline drifted!"
    print(f"reference ballistic jump: true_apex={flight.true_apex_m:.2f} m, "
          f"detector h={true_h:.2f} m, airtime={true_T:.2f} s  (self-check OK)\n")

    LEVERS = [0.2, 0.3, 0.5, 0.8]
    PEAKS = list(range(0, 1001, 50))   # peak spin, deg/s
    MISCAL = {"perfect": 1.0, "under_0.8r": 0.8, "over_1.2r": 1.2}

    rows = []
    for lever in LEVERS:
        # analytic false-landing crossover: omega^2*r/g = 2.5  ->  dps
        w_cross = math.sqrt(2.5 * G / lever)
        dps_cross = math.degrees(w_cross)
        for peak in PEAKS:
            spin_fn = burst(peak, airtime)
            cfg = sm.SensorConfig(lever_m=lever)
            times, mags, land = sm.render_session(flight, cfg, takeoff, SEED, spin_rps_fn=spin_fn)

            raw = detect(times, mags)
            row = {
                "lever_m": lever, "peak_dps": peak, "dps_cross_2.5g": round(dps_cross, 1),
                "raw_detected": int(raw is not None),
                "raw_airtime": round(raw.airtime_s, 3) if raw else None,
                "raw_height": round(raw.height_m, 3) if raw else None,
                "raw_herr_pct": round(100 * (raw.height_m - true_h) / true_h, 1) if raw else None,
            }
            for name, k in MISCAL.items():
                corr = corrected_stream(times, mags, takeoff, spin_fn, lever * k)
                ev = detect(times, corr)
                row[f"corr_{name}_detected"] = int(ev is not None)
                row[f"corr_{name}_airtime"] = round(ev.airtime_s, 3) if ev else None
                row[f"corr_{name}_herr_pct"] = round(100 * (ev.height_m - true_h) / true_h, 1) if ev else None
            rows.append(row)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- analysis ----
    def first_fail(sel, thresh=15.0):
        """min peak_dps at a lever where |height error| exceeds thresh% or the
        jump is missed; None if it never fails up to 1000 dps."""
        return sel

    print(f"{'lever':>6} {'2.5g@dps':>9} | {'RAW break':>9} {'PERFECT':>8} {'UNDER .8r':>9} {'OVER 1.2r':>9}")
    print(f"{'':>6} {'':>9} | {'(dps)':>9} {'break':>8} {'break':>9} {'break':>9}")
    summary = []
    for lever in LEVERS:
        sub = [r for r in rows if r["lever_m"] == lever]
        def brk(prefix):
            for r in sub:
                if r["peak_dps"] == 0:
                    continue
                if prefix == "raw":
                    det, herr = r["raw_detected"], r["raw_herr_pct"]
                else:
                    det, herr = r[f"corr_{prefix}_detected"], r[f"corr_{prefix}_herr_pct"]
                bad = (not det) or (herr is not None and abs(herr) > 15.0)
                if bad:
                    return r["peak_dps"]
            return None
        raw_b = brk("raw"); pf_b = brk("perfect"); un_b = brk("under_0.8r"); ov_b = brk("over_1.2r")
        dps_c = sub[0]["dps_cross_2.5g"]
        fmt = lambda x: (">1000" if x is None else str(x))
        print(f"{lever:>6.2f} {dps_c:>9.0f} | {fmt(raw_b):>9} {fmt(pf_b):>8} {fmt(un_b):>9} {fmt(ov_b):>9}")
        summary.append((lever, dps_c, raw_b, pf_b, un_b, ov_b))

    print("\n(break = min peak-spin in dps where the jump is missed or height error >15%; "
          ">1000 = survives the whole sweep)")

    # ---- landing-erasure probe: a LATE burst (still spinning at touchdown) + over-cal ----
    print("\n--- landing-erasure probe: late-peak burst (spin high at landing), lever 0.3 m ---")
    print(f"{'peak_dps':>8} {'raw_det':>8} {'raw_T':>7} | {'over1.2r_det':>12} {'over1.2r_T':>11}  note")
    for peak in [300, 500, 700, 900]:
        late = burst(peak, airtime, t_peak_frac=0.98, width_frac=0.18)
        cfg = sm.SensorConfig(lever_m=0.3)
        times, mags, land = sm.render_session(flight, cfg, takeoff, SEED, spin_rps_fn=late)
        raw = detect(times, mags)
        corr = corrected_stream(times, mags, takeoff, late, 0.3 * 1.2)
        ev = detect(times, corr)
        note = ""
        if ev is None:
            note = "LANDING ERASED -> jump lost"
        elif ev.airtime_s > true_T * 1.3:
            note = "airtime runaway (late/missed landing)"
        print(f"{peak:>8} {int(raw is not None):>8} "
              f"{(round(raw.airtime_s,2) if raw else '-'):>7} | "
              f"{int(ev is not None):>12} "
              f"{(round(ev.airtime_s,2) if ev else '-'):>11}  {note}")

    print(f"\nCSV -> {OUT}")


if __name__ == "__main__":
    main()
