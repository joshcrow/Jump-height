import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wing_model as wm, sensor_model as sm, selfdiag as sd
from detector import Detector, Params

DT = 2e-5
SEED = 20260804

def cell(av, apex):
    vz0 = wm.vz0_for_ballistic_apex(apex)
    fl = wm.integrate_flight(vz0, wm.const_lift(av), dt=DT)
    times, mag, land = sm.render_session(fl, cfg=sm.SensorConfig(),
                                         takeoff_time_s=2.0, seed=SEED)
    det = Detector(Params())
    ev = None
    for t, a in zip(times, mag):
        e = det.update(t, a)
        if e is not None:
            ev = e; break
    detected = ev is not None
    bias = (ev.height_m / fl.true_apex_m - 1.0) * 100.0 if detected else float('nan')
    feat = sd.air_window_features(times, mag, 2.0, land)
    flagged = sd.flag_nonballistic(feat)
    cf = (wm.closed_form_overshoot(av) - 1.0) * 100.0
    return detected, bias, feat.median_g, flagged, cf, fl.true_apex_m

print(f"{'apex':>4} {'a_v':>5} {'det':>3} {'bias%':>8} {'closed%':>8} {'median':>7} {'flag':>4}  band")
for apex in (2.0, 3.0, 5.0):
    for av in (0.08, 0.09, 0.10, 0.11, 0.20, 0.33, 0.34):
        d, bias, med, fl, cf, ta = cell(av, apex)
        # classify
        if not d: band = "HARD-BLIND"
        elif fl: band = "FLAGGED"
        elif bias < 5: band = "SAFE"
        elif bias < 10: band = "MILD-BLIND"
        else: band = "DANGER-BLIND"
        print(f"{apex:>4.0f} {av:>5.2f} {int(d):>3} {bias:>8.3f} {cf:>8.3f} {med:>7.4f} {int(fl):>4}  {band}")
