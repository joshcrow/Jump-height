#!/usr/bin/env python3
"""Independent re-derivation of the physics numbers in
docs/accuracy-review-2026-09-15.md (sections 3, 5, 6) and its
accuracy-review-assets/physics-notes.md.

Written for the SECOND-OPINION review. It deliberately does NOT import the
repo simulator and does NOT import the first review's scripts: every number is
recomputed from scratch with a different implementation (dense-grid trapezoid
accumulation + independent closed forms) so that agreement is evidence and
disagreement is a finding.

Standard library only. Reads nothing except (optionally) the local session
trace for two measured facts. Writes only physics_recheck.json beside itself.

ALL trajectory numbers below are ANALYSIS of assumed force histories, not
measurements. The two measured items are tagged MEASURED.
"""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

G = 9.80665
TARGET = 0.1524          # +/-0.5 ft in metres
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

# --------------------------------------------------------------------------
# Independent reconstruction: endpoint-constrained double integration.
# Implementation deliberately different from physics_sensitivity.py's
# (trapezoid on velocity + position with a dense grid, apex by refinement).
# --------------------------------------------------------------------------


def apex(a_of_t, T, D=0.0, n=200_000):
    """max_t [z(t)-z(0)] for coordinate acceleration a_of_t on [0,T] with
    z(T)-z(0)=D. Returns (apex_m, apex_t, v0)."""
    dt = T / n
    # unconstrained pass with v(0)=0
    v = 0.0
    z = 0.0
    zs = [0.0]
    a_prev = a_of_t(0.0)
    for i in range(n):
        a_next = a_of_t((i + 1) * dt)
        v_next = v + 0.5 * (a_prev + a_next) * dt
        z = z + 0.5 * (v + v_next) * dt
        v, a_prev = v_next, a_next
        zs.append(z)
    v0 = (D - zs[-1]) / T
    best, best_t = -1e18, 0.0
    for i, zi in enumerate(zs):
        zz = zi + v0 * i * dt
        if zz > best:
            best, best_t = zz, i * dt
    return best, best_t, v0


def kernel_apex_error(da_of_t, T, dD=0.0, n=200_000):
    """max_t |dz(t)| with dz(t) = (t/T)dD - int_0^T K(t,s) da(s) ds,
    computed by the same reconstruction applied to the error alone."""
    e, _, _ = apex(lambda t: -da_of_t(t), T, D=-dD, n=n)   # sign bookkeeping
    e2, _, _ = apex(da_of_t, T, D=dD, n=n)
    return max(abs(e), abs(e2))


out = {"units": "SI unless named otherwise", "kind": "ANALYSIS unless tagged MEASURED"}

# --------------------------------------------------------------------------
# 1. Report section 3.1: the (1-L) support table and the arm-force ceiling.
# --------------------------------------------------------------------------
support_table = []
for L in (0.10, 0.20, 0.30, 0.40):
    support_table.append({
        "constant_upward_support_g": L,
        "ballistic_over_true_ratio": 1.0 / (1.0 - L),
        "overestimate_pct": 100.0 * (1.0 / (1.0 - L) - 1.0),
    })
out["s3_1_support_table"] = support_table

# sim/wing_model.py:88-89 force_elev_deg=35.0 (elevation ABOVE HORIZONTAL),
# arm_ceiling_bw=0.40; aero_model() sets a_up = f*sin(theta)/mass.
arm_vert_g = 0.40 * math.sin(math.radians(35.0))
out["s3_1_arm_ceiling"] = {
    "cap_body_weights": 0.40,
    "force_elevation_deg": 35.0,
    "vertical_component_g": arm_vert_g,
    "airtime_overestimate_pct": 100.0 * (1.0 / (1.0 - arm_vert_g) - 1.0),
    "note": "0.40*sin(35deg); wing_model.aero_model uses sin for a_up, so the "
            "report's reading of the code is correct.",
}

# Absolute (not fractional) error of the airtime formula under constant support:
#   ballistic - true = L * g * T^2 / 8.   This is what the +/-0.5 ft target sees.
abs_err = []
for T in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0):
    row = {"T_s": T}
    for L in (0.10, 0.20, 0.30, arm_vert_g):
        row[f"L={L:.4f}_abs_error_m"] = L * G * T * T / 8.0
    abs_err.append(row)
out["s3_1_absolute_error_of_airtime_formula"] = abs_err
out["s3_1_max_airtime_within_target_for_constant_support"] = {
    f"L={L}": math.sqrt(8 * TARGET / (G * L)) for L in (0.05, 0.10, 0.20, 0.30, 0.40)
}

# --------------------------------------------------------------------------
# 2. Report section 3.3: the late-support counterexample (+15.40%).
#    Re-derived in closed form, NOT via sim/wing_model.py.
# --------------------------------------------------------------------------
T = 1.5
late_frac = 0.48
sup = 0.30 * G
t1 = T * (1 - late_frac)
# equal endpoints: v0*T + int_0^T (T-s) a(s) ds = 0 with a = -g + sup*[s>t1]
v0 = G * T / 2 - sup * (T - t1) ** 2 / (2 * T)
t_apex_cf = v0 / G                    # apex occurs before t1 iff v0/g < t1
apex_cf = v0 * v0 / (2 * G)
apex_num, apex_t_num, _ = apex(lambda t: -G + (sup if t > t1 else 0.0), T, 0.0)
ballistic = G * T * T / 8
out["s3_3_late_support_counterexample"] = {
    "T_s": T, "late_fraction": late_frac, "late_support_g": 0.30,
    "v0_mps_closed_form": v0,
    "apex_closed_form_m": apex_cf,
    "apex_time_closed_form_s": t_apex_cf,
    "apex_before_support_starts": t_apex_cf < t1,
    "apex_numeric_m": apex_num,
    "ballistic_airtime_height_m": ballistic,
    "physical_error_pct": 100.0 * (ballistic / apex_cf - 1.0),
    "absolute_error_m": ballistic - apex_cf,
    "report_value_pct": 15.40199388889798,
}

# --------------------------------------------------------------------------
# 3. Report section 3.3 / physics-notes section 2: three equal-mean-support
#    paths.  Closed form via the kernel, then numerically.
# --------------------------------------------------------------------------
def prof_const(u):
    return 0.20


def prof_central(u):
    return 0.40 if 0.25 <= u < 0.75 else 0.0


def prof_edges(u):
    return 0.40 if (u < 0.25 or u >= 0.75) else 0.0


paths = {}
for name, fn, closed_factor in (("constant_0.2g", prof_const, 0.8),
                                ("central_half_0.4g", prof_central, 0.7),
                                ("edge_quarters_0.4g", prof_edges, 0.9)):
    a_num, t_num, v_num = apex(lambda t, fn=fn: G * (fn(t / T) - 1.0), T, 0.0)
    paths[name] = {
        "apex_numeric_m": a_num, "apex_time_s": t_num, "v0_mps": v_num,
        "apex_closed_form_m": closed_factor * G * T * T / 8,
        "mean_support_g": sum(fn((i + .5) / 20000) for i in range(20000)) / 20000,
    }
paths["spread_edges_minus_central_m"] = (paths["edge_quarters_0.4g"]["apex_closed_form_m"]
                                         - paths["central_half_0.4g"]["apex_closed_form_m"])
out["s3_3_equal_mean_support_paths"] = paths
out["s3_3_kernel_weights_check"] = {
    "int_K(T/2,s)ds_over_whole_T": T * T / 8,
    "int_K(T/2,s)ds_over_central_half": 3 * T * T / 32,
    "int_K(T/2,s)ds_over_edge_quarters": T * T / 8 - 3 * T * T / 32,
    "derivation": "K(T/2,s)=s/2 for s<T/2, (T-s)/2 for s>T/2; the three "
                  "closed-form factors 0.8/0.7/0.9 follow exactly.",
}

# --------------------------------------------------------------------------
# 4. physics-notes section 3: the kernel identities.
# --------------------------------------------------------------------------
b = 0.01 * G
num_bias = kernel_apex_error(lambda t: b, T)
out["notes_kernel_identities"] = {
    "constant_bias_max_path_error_closed_form_m": abs(b) * T * T / 8,
    "constant_bias_max_path_error_numeric_m": num_bias,
    "affine_b1_max_path_error_rule": "|b1| T^3/(9*sqrt(3)) at t=T/sqrt(3)",
    "affine_check_b1_1mps3_T1.5_closed_form_m": 1.0 * T ** 3 / (9 * math.sqrt(3)),
    "affine_check_b1_1mps3_T1.5_numeric_m": kernel_apex_error(lambda t: 1.0 * t, T),
}

# --------------------------------------------------------------------------
# 5. Report section 5.2: the bias table and the 'sole-error ceiling'.
# --------------------------------------------------------------------------
bias_tbl = []
for Td in (1, 2, 3, 4):
    bias_tbl.append({
        "T_s": Td,
        "err_5mg_cm": 100 * 0.005 * G * Td * Td / 8,
        "err_10mg_cm": 100 * 0.010 * G * Td * Td / 8,
        "err_40mg_cm": 100 * 0.040 * G * Td * Td / 8,
        "sole_error_ceiling_mg": 1000 * 8 * TARGET / (G * Td * Td),
    })
out["s5_2_bias_table"] = bias_tbl

# Endpoint rule, tilt-leak rule, boundary timing, time scale.
out["s5_2_endpoint_rule"] = {
    "rule": "dz(t) = (t/T) dD; at a midpoint apex the coefficient is 1/2",
    "dD_0.10m_at_midpoint_cm": 5.0,
    "numeric_check_cm": 100 * kernel_apex_error(lambda t: 0.0, T, dD=0.10) / 2,
}
tilt_1deg = math.radians(1.0)
leak_g = 0.3 * math.sin(tilt_1deg) + 0.2 * (math.cos(tilt_1deg) - 1.0)
out["s5_2_tilt_leak"] = {
    "first_order_mg_at_0.3g_horizontal_1deg": 1000 * 0.3 * tilt_1deg,
    "exact_mg_incl_second_order_term_fv_0.2g": 1000 * leak_g,
    "err_at_3s_cm": 100 * (0.3 * tilt_1deg) * G * 9 / 8,
    "err_at_4s_cm": 100 * (0.3 * tilt_1deg) * G * 16 / 8,
}
out["s5_2_boundary_timing"] = {
    "excluded_20ms_at_4mps_cm": 100 * 4.0 * 0.020,
    "dH_from_duration_error_rule": "q*T*dT/4 for constant q, D=0",
    "q0.8g_T3_dT20ms_cm": 100 * 0.8 * G * 3.0 * 0.020 / 4,
}
eps = 0.005
out["s5_2_time_scale"] = {
    "exact_factor": (1 + eps) ** 2,
    "first_order_rule": 1 + 2 * eps,
    "relative_error_of_first_order": (1 + eps) ** 2 - (1 + 2 * eps),
}

# --------------------------------------------------------------------------
# 6. iid noise: the report's declared 0.02 g/sample convention, and the same
#    formula fed with the ACTUAL datasheet noise density (Table 3, +/-16 g).
# --------------------------------------------------------------------------
def midpoint_sd_from_sample_sd(sigma_a, dt, Tt):
    return sigma_a * math.sqrt(dt * Tt ** 3 / 48.0)


noise = {"report_convention_0.02g_per_sample": {
    f"{fs}Hz_T1.5": midpoint_sd_from_sample_sd(0.02 * G, 1.5 / round(1.5 * fs), 1.5)
    for fs in (50, 200)}}
# Datasheet: An = 130 ug/sqrt(Hz) in high-performance mode at FS = +/-16 g.
# Per-sample SD at ODR fs with the part's LPF1 at ODR/2: sigma = n*sqrt(fs/2).
n_asd = 130e-6 * G                       # m/s^2 per sqrt(Hz)
for fs in (104, 208, 416):
    sigma = n_asd * math.sqrt(fs / 2.0)
    noise[f"datasheet_130ug_rtHz_ODR{fs}"] = {
        "per_sample_sd_mg": 1000 * sigma / G,
        "midpoint_sd_mm": {str(Tt): 1000 * midpoint_sd_from_sample_sd(sigma, 1.0 / fs, Tt)
                           for Tt in (1, 2, 3, 4)},
    }
noise["quantization_sd_mg_at_0.488mg_LSB"] = 0.488 / math.sqrt(12)
out["s5_noise"] = noise

# --------------------------------------------------------------------------
# 7. Report section 12.3, 3.2, 6.6: the three loose arithmetic claims.
# --------------------------------------------------------------------------
out["s12_3_half_g_3p3s"] = {"height_m": 0.5 * G * 3.3 ** 2 / 8}
out["s3_2_dynamic_pressure_ratio"] = {
    "downwind_apparent_mps": 11 - 8, "crosswind_apparent_mps": math.hypot(11, 8),
    "squared_ratio": (11 ** 2 + 8 ** 2) / (11 - 8) ** 2,
}
RHO = 1.225
out["s6_6_barometer"] = {
    "Pa_per_15.24cm_at_sea_level": RHO * G * TARGET,
    "dynamic_pressure_at_10mps_Pa": 0.5 * RHO * 10.0 ** 2,
    "height_equivalent_of_full_dynamic_pressure_m": (0.5 * RHO * 100) / (RHO * G),
}

# --------------------------------------------------------------------------
# 8. NEW: gyro-only attitude propagation with no gravity reference.
#    Datasheet Table 3 (DocID030071 Rev 3, p.21): G_TyOff +/-3 dps,
#    G_OffDr +/-0.05 dps/degC, Rn 5 mdps/sqrt(Hz), G_So 70 mdps/LSB @2000 dps.
# --------------------------------------------------------------------------
def tilt_height_error(theta0_deg, beta_dps, fh_g, Tt):
    """Apex-scale height error from an initial tilt theta0 plus a gyro-bias
    tilt ramp beta, leaking horizontal specific force fh into vertical."""
    b0 = fh_g * G * math.radians(theta0_deg)          # m/s^2
    b1 = fh_g * G * math.radians(beta_dps)            # m/s^3
    return abs(b0) * Tt * Tt / 8 + abs(b1) * Tt ** 3 / (9 * math.sqrt(3))


gyro = {"datasheet_rows": {
    "G_TyOff_dps": 3.0, "G_OffDr_dps_per_degC": 0.05,
    "Rn_mdps_rtHz": 5.0, "G_So_mdps_per_LSB_at_2000dps": 70.0,
    "source": "LSM6DS3TR-C datasheet Table 3, DocID030071 Rev 3, pp.21-22"}}
gyro["bias_estimate_noise_floor_dps"] = {
    f"average_{tau}s": 5e-3 / math.sqrt(tau) for tau in (1, 10, 60, 300)}
cases = {
    "uncalibrated_datasheet_TyOff": 3.0,
    "bench_six_position_then_20degC_swing": 0.05 * 20,
    "bench_calibrated_temp_compensated": 0.10,
    "re_zeroed_immediately_before_the_jump": 0.02,
}
gyro["tilt_deg_after"] = {
    name: {str(Tt): beta * Tt for Tt in (1, 2, 3, 4)} for name, beta in cases.items()}
gyro["height_error_cm_fh_0.3g"] = {
    name: {str(Tt): 100 * tilt_height_error(0.0, beta, 0.3, Tt) for Tt in (1, 2, 3, 4)}
    for name, beta in cases.items()}
gyro["height_error_cm_fh_0.5g"] = {
    name: {str(Tt): 100 * tilt_height_error(0.0, beta, 0.5, Tt) for Tt in (1, 2, 3, 4)}
    for name, beta in cases.items()}
# Gyro scale-factor error is NOT covered in the report. It scales with the
# actual rotation angle, not with time, so it dominates for rotating jumps.
gyro["scale_factor_tilt_deg"] = {
    f"{k}pct_over_{ang}deg_of_real_rotation": k / 100.0 * ang
    for k in (1, 2, 3) for ang in (30, 90, 180)}
gyro["scale_factor_height_error_cm_fh_0.3g_T2"] = {
    f"{k}pct_over_{ang}deg": 100 * tilt_height_error(k / 100.0 * ang, 0.0, 0.3, 2.0)
    for k in (1, 2, 3) for ang in (30, 90, 180)}
out["new_a_gyro_attitude"] = gyro

# --------------------------------------------------------------------------
# 9. NEW: accelerometer bias after six-position calibration.
#    Datasheet LA_TyOff +/-40 mg, LA_OffDr +/-0.5 mg/degC,
#    LA_SoDr +/-0.01 %/degC, An 130 ug/rtHz at +/-16 g.
# --------------------------------------------------------------------------
accel = {"datasheet_rows": {
    "LA_TyOff_mg": 40.0, "LA_OffDr_mg_per_degC": 0.5,
    "LA_SoDr_pct_per_degC": 0.01, "An_ug_rtHz_at_16g": 130.0,
    "LA_So_mg_per_LSB_at_16g": 0.488,
    "source": "LSM6DS3TR-C datasheet Table 3, DocID030071 Rev 3, pp.21-22"}}
accel["six_position_fit_residual_sources_mg"] = {
    "pose_tilt_1deg_each_face": 1000 * (1 - math.cos(math.radians(1.0))),
    "pose_tilt_2deg_each_face": 1000 * (1 - math.cos(math.radians(2.0))),
    "averaging_noise_60s_at_ODR208": 1000 * (n_asd * math.sqrt(104) / math.sqrt(60 * 208)) / G,
    "quantization_0.488mg_LSB_mean_of_many": 0.0,
}
accel["temperature_residual_mg"] = {f"dT_{dT}degC": 0.5 * dT for dT in (5, 10, 20, 30)}
accel["scale_temp_effect_mg_during_0.2g_flight"] = {
    f"dT_{dT}degC": 1000 * (1e-4 * dT) * 0.2 for dT in (10, 20, 30)}
accel["scale_temp_effect_if_wrongly_applied_to_full_g_mg"] = {
    f"dT_{dT}degC": 1000 * (1e-4 * dT) * 1.0 for dT in (10, 20, 30)}
accel["height_error_cm"] = {
    f"{mg}mg": {str(Tt): 100 * (mg / 1000.0) * G * Tt * Tt / 8 for Tt in (0.5, 1, 1.5, 2, 3, 4)}
    for mg in (1, 2, 5, 10, 15, 40)}
out["new_b_accel_bias"] = accel

# --------------------------------------------------------------------------
# 10. NEW: 208 Hz ODR vs the 200 Hz software pacer.
#     firmware/src/main.cpp:89 SAMPLE_INTERVAL_US = 1e6/200 = 5000 us;
#     the timebase is RTC1-derived at 976.5625 us per tick (twim_bounded.h:57).
# --------------------------------------------------------------------------
TICK_US = 1e6 / 1024.0
odr = 208.0
period_us = 1e6 / odr
ticks = []
k = 0
while k * 5000 <= 2_000_000:
    ticks.append(math.ceil(k * 5000 / TICK_US) * TICK_US)
    k += 1
deltas = [ticks[i + 1] - ticks[i] for i in range(len(ticks) - 1)]
# which ODR sample index each poll sees (BDU=1 -> latest complete sample)
idx = [math.floor(t / period_us) for t in ticks]
dups = sum(1 for i in range(1, len(idx)) if idx[i] == idx[i - 1])
skips = sum(max(0, idx[i] - idx[i - 1] - 1) for i in range(1, len(idx)))
span_s = (ticks[-1] - ticks[0]) / 1e6
acq = {
    "pacer_interval_us": 5000.0, "odr_hz": odr, "odr_period_us": period_us,
    "rtc_tick_us": TICK_US,
    "poll_interval_min_us": min(deltas), "poll_interval_max_us": max(deltas),
    "poll_interval_mean_us": sum(deltas) / len(deltas),
    "duplicate_reads_per_s": dups / span_s,
    "never_read_odr_samples_per_s": skips / span_s,
    "duplicate_onset_odr_hz": 1e6 / min(deltas),
    "odr_margin_to_duplicates_pct": 100 * (odr - 1e6 / min(deltas)) / odr,
    "accel_gyro_read_separation_us_estimate": 2 * (7 * 9 / 400e3) * 1e6,
    "note": "two separate I2C transactions: readAccelG(0x28) then "
            "readGyroDps(0x22), lsm6ds3_min.h:174,194",
}
# attitude mismatch from reading accel and gyro at different instants
for w in (60, 150, 375):
    acq[f"attitude_mismatch_deg_at_{w}dps"] = {
        "same_odr_tick_0.2ms": w * 0.0002,
        "worst_case_one_odr_period_4.8ms": w * period_us / 1e6,
    }
# trace decimation aliasing
acq["trace_decimation"] = {
    "accel_LPF1_bandwidth_hz": odr / 2,
    "host_poll_nyquist_hz": 100.0,
    "log_hz_09_14_data": 50.0, "log_nyquist_09_14_hz": 25.0,
    "log_hz_current_config": 100.0, "log_nyquist_current_hz": 50.0,
    "aliased_band_09_14_hz": [25.0, 104.0],
    "note": "no digital low-pass is applied before either decimation "
            "(main.cpp LOG_DECIMATE is a plain counter)",
}
out["new_c_acquisition"] = acq

# clock-scale sensitivity (dH = 2*eps*H)
out["new_c_clock_scale"] = {
    "rule": "dH/H = 2*epsilon for equal endpoints",
    f"H=2m_error_cm": {f"eps_{e}": 100 * 2 * e * 2.0
                       for e in (20e-6, 250e-6, 5e-3, 20e-3)},
    "note": "nRF52840 LFCLK: LFXO is typically <=+/-250 ppm with a crystal; "
            "the internal LFRC is +/-2% uncalibrated. Which one this build "
            "uses was NOT established in this review.",
}

# --------------------------------------------------------------------------
# 11. NEW: endpoint D for a foiling board, and takeoff/landing pitch change.
# --------------------------------------------------------------------------
geo = {"assumptions": "mast 75-90 cm; foiling ride height 30-45 cm; sensor "
                      "lever r = 0.5-0.7 m from the board reference point"}
geo["endpoint_D_error_cm"] = {
    f"dD_{d}m": 100 * d / 2 for d in (0.10, 0.20, 0.30, 0.45, 0.60)}
geo["pitch_change_sensor_rise_cm"] = {
    f"r={r}m_symmetric_+/-{a}deg": 100 * 2 * r * math.sin(math.radians(a))
    for r in (0.5, 0.6, 0.7) for a in (1.5, 5, 10)}
geo["pitch_change_sensor_rise_cm_one_sided"] = {
    f"r=0.6m_{a}deg_change": 100 * 0.6 * abs(math.sin(math.radians(a)))
    for a in (5, 10, 15, 20)}
out["new_fg_geometry"] = geo

# --------------------------------------------------------------------------
# 12. NEW: mount compliance in a WOO O-ring cradle (order of magnitude).
# --------------------------------------------------------------------------
mount = {"assumptions": "Hammond 1551WHGY + Sense + 250 mAh EEMB ~ 55-75 g; "
                        "two nitrile O-rings in shear, k 1e4-1e5 N/m total"}
mount["resonance_hz"] = {
    f"m={m}g_k={k:.0e}Nm": (1 / (2 * math.pi)) * math.sqrt(k / (m / 1000.0))
    for m in (55, 75) for k in (1e4, 3e4, 1e5)}
mount["displacement_for_1g_at_resonance_Q3_mm"] = {
    f"f={f}Hz": 1000 * 3 * G / (2 * math.pi * f) ** 2 for f in (60, 100, 200)}
out["new_e_mount"] = mount

# --------------------------------------------------------------------------
# 13. NEW: Surfr internal consistency (report section 2).
# --------------------------------------------------------------------------
surfr_rows = [(6.32, 3.07), (5.74, 3.39), (9.1, 3.8)]
surf = []
for ft, air in surfr_rows:
    h = ft * 0.3048
    L = 1 - 8 * h / (G * air * air)
    Tb = math.sqrt(8 * h / G)
    # force a 5 m^2 wing would need at 13.6 m/s apparent wind to hold L on 85 kg
    q_dyn = 0.5 * 1.225 * (11 ** 2 + 8 ** 2)
    need_N = L * 85 * G
    surf.append({
        "height_ft": ft, "height_m": h, "airtime_s": air,
        "implied_constant_support_g": L,
        "ballistic_airtime_for_that_height_s": Tb,
        "airtime_ratio_reported_over_ballistic": air / Tb,
        "ballistic_height_for_that_airtime_m": G * air * air / 8,
        "required_sustained_vertical_force_N_at_85kg": need_N,
        "available_force_N_at_C=1.0_5m2_13.6mps": q_dyn * 5.0,
        "required_vertical_force_coefficient": need_N / (q_dyn * 5.0),
    })
out["new_i_surfr_consistency"] = surf

# --------------------------------------------------------------------------
# 14. MEASURED items from the local corpus (no simulation).
# --------------------------------------------------------------------------
meas = {}
jumps = ROOT / "data/sessions/20260914-210637-E2C4/jumps.csv"
if jumps.is_file():
    rows = list(csv.DictReader(jumps.open()))
    hs = sorted(float(r["height_m"]) for r in rows)
    ar = sorted(float(r["airtime_raw_s"]) for r in rows)
    ma = sorted(float(r["med_a_g"]) for r in rows)
    mw = sorted(float(r["med_w_dps"]) for r in rows)
    mid = len(rows) // 2
    # Can rotation explain the measured in-flight |a|?  If the whole median
    # magnitude were centripetal, omega^2 * r = med_a * g, so
    #   r_required = med_a*g / omega^2.
    per_jump = []
    for r in rows:
        ma_g = float(r["med_a_g"])
        w = float(r["med_w_dps"]) * math.pi / 180.0
        per_jump.append({
            "n": int(r["n"]), "airtime_raw_s": float(r["airtime_raw_s"]),
            "height_m": float(r["height_m"]), "med_a_g": ma_g,
            "med_w_dps": float(r["med_w_dps"]), "n_air": int(r["n_air"]),
            "lever_arm_m_needed_if_all_rotation": (ma_g * G / (w * w)) if w > 0 else None,
            "true_apex_m_if_med_a_were_constant_support":
                max(0.0, (1 - ma_g)) * G * float(r["airtime_s"]) ** 2 / 8,
        })
    meas["MEASURED_per_jump_rotation_test"] = per_jump
    meas["MEASURED_jumps_csv"] = {
        "n": len(rows), "median_height_m": hs[mid], "max_height_m": hs[-1],
        "median_airtime_raw_s": ar[mid], "max_airtime_raw_s": ar[-1],
        "median_med_a_g": ma[mid], "min_med_a_g": ma[0], "max_med_a_g": ma[-1],
        "median_med_w_dps": mw[mid], "max_med_w_dps": mw[-1],
        "n_with_med_a_above_0.35g": sum(1 for x in ma if x > 0.35),
        "n_with_med_a_above_0.07g": sum(1 for x in ma if x > 0.07),
        "predicted_ballistic_band_g": [0.0, 0.07],
        "source": "data/sessions/20260914-210637-E2C4/jumps.csv; the band is "
                  "firmware/src/main.cpp:351 'predicted band 0-0.07 g'",
    }
trace = ROOT / "data/sessions/20260914-210637-E2C4/trace.csv"
if trace.is_file():
    mx, mxt, n, o8, o16 = 0.0, 0.0, 0, 0, 0
    with trace.open() as f:
        for r in csv.DictReader(f):
            a = float(r["mag"]); n += 1
            if a > mx:
                mx, mxt = a, float(r["t"])
            if a >= 8.0:
                o8 += 1
            if a >= 16.0:
                o16 += 1
    meas["MEASURED_trace_peak"] = {
        "rows": n, "max_mag_g": mx, "at_t_s": mxt,
        "samples_ge_8g": o8, "samples_ge_16g": o16,
        "per_axis_rail_g": 32768 * 0.000488,
        "magnitude_rail_if_all_three_axes_railed_g": 32768 * 0.000488 * math.sqrt(3),
        "note": "trace is a 4:1 decimation of the 200 Hz stream (log_hz=50), "
                "so the true per-sample peak is >= this",
    }
meas["MEASURED_selftest_rest_magnitude_g"] = {
    "value": 1.033,
    "source": "data/sessions/20260914-210637-E2C4/session.json "
              "manifest.selftest_lines: 'SELFTEST accel PASS detail=1.033g'",
    "implied_composite_offset_plus_scale_error_mg": 33.0,
    "note": "main.cpp:749 adopts this as g_baseline and divides |a| by it. "
            "That is a SCALE correction; if the error is an OFFSET it "
            "survives almost intact at low specific force.",
    "residual_at_0.2g_if_error_is_pure_offset_mg":
        1000 * ((0.2 + 0.033) / 1.033 - 0.2),
}
out["measured"] = meas

# --------------------------------------------------------------------------
# 15. The budget table, per jump class.  ANALYSIS, not measurement.
# --------------------------------------------------------------------------
SCEN = {
    #            b_mg  theta0_deg beta_dps  fh_g   dD_m   dT_s  pitch_deg  eps
    "best":     (2.0,  0.2,       0.05,     0.20,  0.10,  0.005, 1.5,     250e-6),
    "realistic":(10.0, 0.5,       0.30,     0.30,  0.30,  0.020, 5.0,     250e-6),
    "worst":    (40.0, 2.0,       3.00,     0.50,  0.60,  0.060, 10.0,    2e-2),
}
# Reference apex per jump class. A 3-4 s wingfoil flight does NOT reach the
# 0.2 g-support height (8.8 / 15.7 m); it reaches 2-3 m, which forces a small
# net downward acceleration q = 8H/T^2 and a large support fraction. Using the
# plausible apex instead of a fixed support fraction keeps the timing term
# honest at long durations.
PLAUSIBLE_APEX = {0.5: 0.30, 1.0: 1.00, 1.5: 1.50, 2.0: 2.00, 3.0: 2.00, 4.0: 2.80}
budget = []
for Tt in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0):
    href = PLAUSIBLE_APEX[Tt]
    q = 8 * href / (Tt * Tt)              # implied net downward accel, m/s^2
    row = {"T_s": Tt, "plausible_apex_m": href,
           "implied_net_down_accel_g": q / G,
           "implied_constant_support_g": 1 - q / G,
           "airtime_formula_error_m": (1 - q / G) * G * Tt * Tt / 8}
    for scen, (bmg, th0, beta, fh, dD, dT, pitch, e) in SCEN.items():
        bias = (bmg / 1000.0) * G * Tt * Tt / 8
        tilt = tilt_height_error(th0, beta, fh, Tt)
        endp = dD / 2.0
        tim = q * Tt * dT / 4.0
        clk = 2 * e * href
        mnt = 0.6 * abs(math.sin(math.radians(pitch)))
        row[scen] = {
            "bias_cm": 100 * bias, "tilt_cm": 100 * tilt,
            "endpoint_cm": 100 * endp, "timing_cm": 100 * tim,
            "clock_cm": 100 * clk, "pitch_geometry_cm": 100 * mnt,
            "same_sign_sum_cm": 100 * (bias + tilt + endp + tim + clk + mnt),
            "rss_cm": 100 * math.sqrt(bias ** 2 + tilt ** 2 + endp ** 2
                                      + tim ** 2 + clk ** 2 + mnt ** 2),
            "within_15.24cm_same_sign": (bias + tilt + endp + tim + clk + mnt) <= TARGET,
            "within_15.24cm_rss": math.sqrt(bias ** 2 + tilt ** 2 + endp ** 2
                                            + tim ** 2 + clk ** 2 + mnt ** 2) <= TARGET,
        }
    budget.append(row)
out["budget_table_ANALYSIS"] = {
    "scenario_inputs": {k: dict(zip(
        ("bias_mg", "initial_tilt_deg", "gyro_bias_dps", "horizontal_force_g",
         "endpoint_uncertainty_m", "timing_error_s", "pitch_change_deg",
         "clock_scale_eps"), v)) for k, v in SCEN.items()},
    "rows": budget,
    "excluded": "mount compliance / aliased vibration and accelerometer "
                "clipping are NOT in these numbers: neither has been measured "
                "on this unit in this enclosure.",
}

path = HERE / "physics_recheck.json"
path.write_text(json.dumps(out, indent=2, sort_keys=False) + "\n")
print(json.dumps({"output": str(path)}, indent=2))
