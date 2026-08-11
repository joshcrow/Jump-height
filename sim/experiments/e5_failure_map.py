"""E5 — FAILURE-BOUNDARY MAP.

Sweep the constant vertical wing lift a_v = const_lift(a_v) finely
(0.00..0.60 g, step 0.01) at three jump sizes (ballistic apex 2, 3, 5 m).
For each cell run the full pipeline:

    integrate_flight(const_lift(a_v))  ->  render_session  ->  Detector
                                       ->  self-diag flag

and classify the outcome into a failure band. The question: is there a
DANGEROUS region where a jump is both materially mis-measured AND unflagged,
versus jumps that simply vanish (annoying, but not a wrong number)?

Bands (documented, applied per row):
  HARD-BLIND   : NOT detected                       (jump vanishes; no event)
  FLAGGED      : detected & self-diag flags it       (graceful — user warned)
  SAFE         : detected, unflagged, bias  < 5%     (negligible error)
  MILD-BLIND   : detected, unflagged, 5% <= bias<10% (small silent error)
  DANGER-BLIND : detected, unflagged, bias >= 10%    (material AND silent)

Deterministic: fixed integer seeds throughout.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wing_model as wm, sensor_model as sm, selfdiag as sd
from detector import Detector, Params

import csv

DT = 2e-5                     # accuracy per experiment rules
APEXES = [2.0, 3.0, 5.0]      # ballistic target jump sizes (m)
AV_STEP = 0.01
AV_MAX = 0.60
SEED = 20260804              # fixed seed (deterministic)

BIAS_SAFE = 5.0               # % below which error is negligible
BIAS_MATERIAL = 10.0          # % at/above which error is material


def classify(detected: bool, bias_pct: float, flagged: bool) -> str:
    if not detected:
        return "HARD-BLIND"
    if flagged:
        return "FLAGGED"
    if bias_pct < BIAS_SAFE:
        return "SAFE"
    if bias_pct < BIAS_MATERIAL:
        return "MILD-BLIND"
    return "DANGER-BLIND"


def run_cell(av: float, apex: float):
    """One (a_v, apex) cell -> (detected, bias_pct, flagged, band, extras)."""
    vz0 = wm.vz0_for_ballistic_apex(apex)
    flight = wm.integrate_flight(vz0, wm.const_lift(av), dt=DT)

    cfg = sm.SensorConfig()                    # spin_rps=0, lever_m=0 (no rotation confound)
    takeoff = 2.0
    times, mag, land_time = sm.render_session(flight, cfg=cfg,
                                              takeoff_time_s=takeoff, seed=SEED)

    det = Detector(Params())
    event = None
    for t, a in zip(times, mag):
        ev = det.update(t, a)
        if ev is not None:
            event = ev
            break

    detected = event is not None
    if detected:
        bias_pct = (event.height_m / flight.true_apex_m - 1.0) * 100.0
    else:
        bias_pct = float("nan")

    feat = sd.air_window_features(times, mag, takeoff, land_time)
    flagged = sd.flag_nonballistic(feat)

    band = classify(detected, bias_pct if detected else 0.0, flagged)
    return detected, bias_pct, flagged, band, flight, feat


def main():
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "out", "e5_failure_map.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    n_steps = int(round(AV_MAX / AV_STEP)) + 1
    avs = [round(i * AV_STEP, 2) for i in range(n_steps)]

    rows = []
    for apex in APEXES:
        for av in avs:
            detected, bias_pct, flagged, band, flight, feat = run_cell(av, apex)
            rows.append(dict(
                a_v=av, apex=apex, detected=int(detected),
                bias_pct=(round(bias_pct, 3) if detected else ""),
                flagged=int(flagged), band=band,
                true_apex_m=round(flight.true_apex_m, 4),
                true_airtime_s=round(flight.true_airtime_s, 4),
                median_g=round(feat.median_g, 4),
            ))

    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # ---- console analysis: band boundaries per apex ----
    print(f"wrote {out_path}  ({len(rows)} rows)")

    # anchor check: bias vs closed_form at a middling a_v where detected
    for apex in APEXES:
        print(f"\n=== apex {apex:.0f} m (ballistic) ===")
        prev_band = None
        for r in rows:
            if r["apex"] != apex:
                continue
            if r["band"] != prev_band:
                b = r["bias_pct"]
                print(f"  a_v={r['a_v']:.2f}  -> {r['band']:<12} "
                      f"(bias={b if b != '' else 'n/a':>7}%  "
                      f"flagged={r['flagged']}  median_g={r['median_g']:.3f})")
                prev_band = r["band"]

    # anchor: at a_v=0.20 detected cells, bias%_should ~ (1/(1-0.2)-1)*100 = 25%
    cf20 = (wm.closed_form_overshoot(0.20) - 1.0) * 100.0
    print(f"\nanchor closed_form_overshoot(0.20) bias = {cf20:.2f}%")
    for apex in APEXES:
        for r in rows:
            if r["apex"] == apex and r["a_v"] == 0.20:
                print(f"  apex {apex:.0f}: detected={r['detected']} "
                      f"bias={r['bias_pct']} band={r['band']}")

    # DANGER-BLIND census
    danger = [r for r in rows if r["band"] == "DANGER-BLIND"]
    print(f"\nDANGER-BLIND cells (detected, material bias>=10%, unflagged): {len(danger)}")
    for r in danger:
        print(f"  a_v={r['a_v']:.2f} apex={r['apex']:.0f} "
              f"bias={r['bias_pct']}% median_g={r['median_g']:.3f}")

    return out_path


if __name__ == "__main__":
    main()
