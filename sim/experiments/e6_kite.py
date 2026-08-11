"""EXPERIMENT E6 — KITE VALIDATION GATE (the credibility anchor).

Prove the whole pipeline reproduces the ONE real non-ballistic dataset
(Simons 2025: kite big-air ~5.1 m true apex at ~3.10 s airtime; ballistic math
h=g*T^2/8 predicts ~11.8 m; overshoot ~2.3x; landing peaks 4.2-5.5 g).

(1) Bisect vz0 so kite_preset(0.567) gives true_airtime ~= 3.10 s; confirm apex,
    ballistic prediction, overshoot. PASS if overshoot in [2.15,2.45] and apex
    in [4.7,5.5] m.
(2) Show WHY wings differ: sweep a_up_g in [0.1..0.7] for the harness (kite)
    and overlay the arm-capped aero wing maximum (aero_model @ arm_ceiling_bw
    =0.40). The arm ceiling is the discriminator.
(3) Render a kite jump, run the firmware-twin detector on it, and confirm the
    self-diagnosis STRONGLY flags it (median|a| ~0.567 g).

Deterministic: fixed seeds; dt=2e-5 in integrate_flight for accuracy.
Writes out/e6_kite.csv.
"""

import sys, os, csv, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wing_model as wm, sensor_model as sm, selfdiag as sd
from detector import Detector, Params, height_for_airtime

DT = 2e-5
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out", "e6_kite.csv")

rows = []  # (section, key, value, note)


def airtime_of(vz0, wing):
    return wm.integrate_flight(vz0, wing, dt=DT).true_airtime_s


# ------------------------------------------------------------------ Part 1
# Bisect vz0 so kite_preset(0.567) yields true_airtime == 3.10 s.
A_V = 0.567
TARGET_T = 3.10
kite = wm.kite_preset(A_V)

lo, hi = 3.0, 12.0  # vz0 bracket (m/s)
for _ in range(60):
    mid = 0.5 * (lo + hi)
    if airtime_of(mid, kite) < TARGET_T:
        lo = mid
    else:
        hi = mid
vz0_kite = 0.5 * (lo + hi)

flight = wm.integrate_flight(vz0_kite, kite, dt=DT)
true_apex = flight.true_apex_m
true_airtime = flight.true_airtime_s
ballistic = height_for_airtime(true_airtime)          # g*T^2/8, what a device reports
overshoot = ballistic / true_apex
# mid-flight spec_g anchor: should equal a_v exactly
mid_idx = min(range(len(flight.times)), key=lambda i: abs(flight.times[i] - true_airtime / 2))
mid_spec_g = flight.spec_g[mid_idx]

p1_pass = (2.15 <= overshoot <= 2.45) and (4.7 <= true_apex <= 5.5)

print(f"[P1] vz0={vz0_kite:.4f}  airtime={true_airtime:.4f}s  apex={true_apex:.4f}m"
      f"  ballistic={ballistic:.4f}m  overshoot={overshoot:.4f}x  mid_spec_g={mid_spec_g:.4f}"
      f"  -> {'PASS' if p1_pass else 'FAIL'}")

rows += [
    ("P1", "a_v_g", f"{A_V}", "sustained harness lift (Simons 2025 kite)"),
    ("P1", "vz0_mps", f"{vz0_kite:.4f}", "bisected for 3.10s airtime"),
    ("P1", "true_airtime_s", f"{true_airtime:.4f}", "target 3.10"),
    ("P1", "true_apex_m", f"{true_apex:.4f}", "target ~5.1 (gate [4.7,5.5])"),
    ("P1", "ballistic_pred_m", f"{ballistic:.4f}", "g*T^2/8, target ~11.8"),
    ("P1", "overshoot_x", f"{overshoot:.4f}", "target ~2.3 (gate [2.15,2.45])"),
    ("P1", "closed_form_overshoot_x", f"{wm.closed_form_overshoot(A_V):.4f}", "1/(1-a_v) analytic"),
    ("P1", "mid_spec_g", f"{mid_spec_g:.4f}", "should == a_v (0.567)"),
    ("P1", "PASS", str(p1_pass), "overshoot in [2.15,2.45] AND apex in [4.7,5.5]"),
]

# ------------------------------------------------------------------ Part 2
# Sweep a_up_g for the harness (kite): overshoot vs closed form.
# Use a fixed modest takeoff so each is apples-to-apples; overshoot for pure
# const lift is analytic 1/(1-a_v) and independent of vz0 (drag adds a whisker).
print("\n[P2] Harness (kite) sweep — overshoot vs 1/(1-a_v):")
sweep = [0.10, 0.20, 0.30, 0.40, 0.50, 0.567, 0.60, 0.70]
VZ0_SWEEP = 6.0
for a in sweep:
    fl = wm.integrate_flight(VZ0_SWEEP, wm.kite_preset(a), dt=DT)
    over = height_for_airtime(fl.true_airtime_s) / fl.true_apex_m
    cf = wm.closed_form_overshoot(a)
    print(f"     a_up_g={a:.3f}  overshoot={over:.4f}x  closed_form={cf:.4f}x"
          f"  apex={fl.true_apex_m:.3f}m")
    rows.append(("P2_harness", f"a_up_g={a:.3f}",
                 f"{over:.4f}", f"closed_form={cf:.4f}x apex={fl.true_apex_m:.3f}m"))

# Arm-capped aero wing: the realistic wing. Sweep technique x wind, cap 0.40 BW.
# Take the MAX overshoot -> the discriminator (~1.07x, wings ~ballistic).
print("\n[P2] Arm-capped aero wing (arm_ceiling_bw=0.40) — realistic max overshoot:")
techniques = ["sheeted_out", "lofted", "mixed", "constant"]
winds = [8.0, 10.0, 12.0, 14.0]
c_maxes = [0.8, 1.0]
aero_overs = []
best = None
for tech in techniques:
    for wind in winds:
        for cm in c_maxes:
            wp = wm.WingParams(technique=tech, wind_mps=wind, c_max=cm,
                               arm_ceiling_bw=0.40, harness=False)
            fl = wm.integrate_flight(VZ0_SWEEP, wm.aero_model(wp), dt=DT)
            over = height_for_airtime(fl.true_airtime_s) / fl.true_apex_m
            aero_overs.append(over)
            if best is None or over > best[0]:
                best = (over, tech, wind, cm)
aero_max = max(aero_overs)
aero_min = min(aero_overs)
print(f"     aero overshoot range [{aero_min:.4f}, {aero_max:.4f}]x over "
      f"{len(aero_overs)} configs; max @ technique={best[1]} wind={best[2]} c_max={best[3]}")

# The "in principle" arm ceiling: a wing held flat at exactly 0.40 g.
arm_principle = wm.closed_form_overshoot(0.40)  # 1/(1-0.40) = 1.667
print(f"     arm-ceiling IN PRINCIPLE (const 0.40g) = {arm_principle:.4f}x; "
      f"realistic decayed aero max = {aero_max:.4f}x")
print(f"     DISCRIMINATOR: harness 0.567g -> {overshoot:.3f}x  vs  arm-capped wing -> {aero_max:.3f}x")

rows += [
    ("P2_aero", "aero_overshoot_min_x", f"{aero_min:.4f}", "arm-capped realistic wing"),
    ("P2_aero", "aero_overshoot_max_x", f"{aero_max:.4f}",
     f"max @ {best[1]}/wind{best[2]}/c{best[3]}"),
    ("P2_aero", "arm_ceiling_principle_x", f"{arm_principle:.4f}", "1/(1-0.40) const 0.40g"),
    ("P2_aero", "kite_harness_overshoot_x", f"{overshoot:.4f}", "harness 0.567g, no arm cap"),
    ("P2_aero", "discriminator", "arm_ceiling", "harness 2.3x vs arm-capped ~1.07x"),
]

# ------------------------------------------------------------------ Part 3
# Render the kite jump and run the firmware-twin detector + self-diagnosis.
print("\n[P3] Render kite jump -> detector + self-diagnosis:")
cfg = sm.SensorConfig(landing_g=4.8)  # Simons landing peaks 4.2-5.5 g
takeoff_s = 2.0
times, mag, land_time = sm.render_session(flight, cfg=cfg, takeoff_time_s=takeoff_s, seed=7)

det = Detector(Params())
event = None
for t, a in zip(times, mag):
    ev = det.update(t, a)
    if ev is not None:
        event = ev
        break

# Self-diagnosis on the airborne window.
feat = sd.air_window_features(times, mag, takeoff_s, land_time)
flagged = sd.flag_nonballistic(feat)
est_over = sd.estimate_overshoot(feat)

# What the airtime-formula WOULD report from the true airtime (the device's
# height model), regardless of whether the streaming gate caught this jump.
formula_report = height_for_airtime(true_airtime)

if event is not None:
    det_status = "DETECTED"
    det_height = event.height_m
    det_airtime = event.airtime_s
    print(f"     detector DETECTED: airtime={det_airtime:.3f}s height={det_height:.3f}m")
else:
    det_status = "SILENT_MISS"
    det_height = float("nan")
    det_airtime = float("nan")
    print(f"     detector SILENT MISS: airborne |a|~{feat.median_g:.3f}g >= "
          f"{Params().freefall_enter_g}g gate never crossed (and airtime "
          f"{true_airtime:.2f}s > {Params().max_airtime_s}s max)")

print(f"     airtime-formula height (g*T^2/8) = {formula_report:.3f}m (the 11.8m the device 'believes')")
print(f"     self-diag: median|a|={feat.median_g:.4f}g mean|a|={feat.mean_g:.4f}g "
      f"frac_above_eps={feat.frac_above_eps:.3f} flagged={flagged} est_overshoot={est_over:.3f}x")

rows += [
    ("P3", "detector_status", det_status, "streaming firmware-twin result"),
    ("P3", "detector_height_m", f"{det_height:.4f}", "height detector would log (nan if missed)"),
    ("P3", "detector_airtime_s", f"{det_airtime:.4f}", "measured airtime (nan if missed)"),
    ("P3", "airtime_formula_height_m", f"{formula_report:.4f}",
     "g*T^2/8 from true airtime = the ~11.8m ballistic belief"),
    ("P3", "selfdiag_median_g", f"{feat.median_g:.4f}", "should ~0.567 (== a_v)"),
    ("P3", "selfdiag_mean_g", f"{feat.mean_g:.4f}", "air-window lift readout"),
    ("P3", "selfdiag_frac_above_eps", f"{feat.frac_above_eps:.4f}", "eps=0.08g"),
    ("P3", "selfdiag_flagged", str(flagged), "flag_nonballistic (threshold 0.12g)"),
    ("P3", "selfdiag_est_overshoot_x", f"{est_over:.4f}", "1/(1-median), triage upper bound"),
    ("P3", "landing_peak_g", f"{cfg.landing_g}", "Simons range 4.2-5.5g"),
]

# ------------------------------------------------------------------ Headline
headline_pass = p1_pass and flagged
print("\n" + "=" * 70)
print(f"E6 HEADLINE: {'PASS' if headline_pass else 'FAIL'} — kite 2.3x reproduction")
print(f"  true_apex={true_apex:.3f}m  ballistic={ballistic:.3f}m  overshoot={overshoot:.4f}x")
print(f"  self-diag median|a|={feat.median_g:.3f}g flagged={flagged}")
print("=" * 70)
rows.insert(0, ("HEADLINE", "PASS", str(headline_pass),
                f"overshoot={overshoot:.4f}x apex={true_apex:.4f}m flagged={flagged}"))

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with open(OUT, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["section", "key", "value", "note"])
    w.writerows(rows)
print(f"\nWrote {OUT} ({len(rows)} rows)")
