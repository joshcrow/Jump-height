#!/usr/bin/env python3
"""corpus_recheck.py -- independent re-derivation of the MEASURED corpus claims
used by docs/accuracy-review-second-opinion-2026-09-15.md.

Standard library only. Imports nothing from sim/, tools/, or either review's
other scripts. Reads two private session files read-only:

    data/sessions/20260914-210637-E2C4/jumps.csv
    data/sessions/20260914-210637-E2C4/trace.csv

and writes corpus_recheck.json beside this file. If the session files are
absent the run is marked NOT RUN rather than silently producing nothing --
a reading that did not happen is a finding (CLAUDE.md rule 3).

Produces:
  M1  per-jump med_a_g table, count outside the firmware's predicted 0-0.07 g
      band, count above the 0.35 g free-fall gate, and the lever arm that
      would be required if each median were pure omega^2 r.
  M2  the n_air reconciliation against floor((airtime_raw - 0.08) * 200).
  M3  trace peak magnitude, counts above 8 and 16 normalized g, and the raw
      equivalents at g_baseline = 1.033.
  M4  the mid-flight reloading check for jumps 2 and 3: the maximum |a| that
      occurs strictly inside the reported airtime window.
  M5  the airtime-formula support-error boundary table: the longest airtime
      for which L*g*T^2/8 stays inside 0.1524 m, per constant support L.
"""

import csv
import json
import math
import os
import sys

G = 9.80665
TOL_M = 0.1524
GATE_G = 0.35
BAND_HI_G = 0.07
CONFIRM_S = 0.08
SAMPLE_HZ = 200.0
G_BASELINE = 1.033  # SELFTEST accel PASS detail=1.033g, sync-time selftest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SESSION = os.path.join(ROOT, "data", "sessions", "20260914-210637-E2C4")


def load_jumps(path):
    with open(path, newline="") as fh:
        return [r for r in csv.DictReader(fh)]


def m1_m2(rows):
    per = []
    n_outside_band = 0
    n_above_gate = 0
    n_reconciled = 0
    for r in rows:
        med_a = float(r["med_a_g"])
        med_w = float(r["med_w_dps"])
        n_air = int(r["n_air"])
        t_raw = float(r["airtime_raw_s"])
        omega = math.radians(med_w)
        lever = (med_a * G) / (omega * omega) if omega > 0 else None
        pred_n = int(math.floor((t_raw - CONFIRM_S) * SAMPLE_HZ))
        ok = pred_n == n_air
        n_reconciled += 1 if ok else 0
        if med_a > BAND_HI_G:
            n_outside_band += 1
        if med_a > GATE_G:
            n_above_gate += 1
        per.append({
            "n": int(r["n"]),
            "airtime_raw_s": t_raw,
            "height_m": float(r["height_m"]),
            "med_a_g": med_a,
            "med_w_dps": med_w,
            "med_acorr_equals_med_a": r["med_acorr_g"] == r["med_a_g"],
            "n_air": n_air,
            "n_air_predicted": pred_n,
            "n_air_reconciles": ok,
            "lever_arm_m_if_all_omega2r": lever,
        })
    return {
        "per_jump": per,
        "n_jumps": len(per),
        "n_outside_predicted_band_0_to_0p07g": n_outside_band,
        "n_above_freefall_gate_0p35g": n_above_gate,
        "n_air_reconciles": n_reconciled,
        "med_a_g_min": min(p["med_a_g"] for p in per),
        "med_a_g_max": max(p["med_a_g"] for p in per),
        "airtime_raw_s_max": max(p["airtime_raw_s"] for p in per),
        "airtime_raw_s_median": sorted(p["airtime_raw_s"] for p in per)[len(per) // 2],
    }


def m3_m4(trace_path, rows):
    peak = -1.0
    peak_t = None
    n_rows = 0
    ge8 = 0
    ge16 = 0
    raw_rail_g = 32768 * 0.000488  # per-axis rail, +-16 g at 0.488 mg/LSB
    ge_raw_rail = 0
    windows = {}
    for r in rows:
        n = int(r["n"])
        if n in (2, 3):
            t0 = float(r["takeoff_s"])
            windows[n] = (t0, t0 + float(r["airtime_raw_s"]), -1.0, None)
    with open(trace_path, newline="") as fh:
        rd = csv.reader(fh)
        next(rd)
        for row in rd:
            if len(row) < 2:
                continue
            t = float(row[0])
            mag = float(row[1])
            n_rows += 1
            if mag > peak:
                peak = mag
                peak_t = t
            if mag >= 8.0:
                ge8 += 1
            if mag >= 16.0:
                ge16 += 1
            if mag * G_BASELINE >= raw_rail_g:
                ge_raw_rail += 1
            for n, (lo, hi, best, bt) in list(windows.items()):
                # strictly inside the reported flight, away from the endpoints
                if lo + 0.05 < t < hi - 0.05 and mag > best:
                    windows[n] = (lo, hi, mag, t)
    return {
        "trace_rows": n_rows,
        "peak_normalized_g": peak,
        "peak_t_s": peak_t,
        "peak_raw_g_at_g_baseline_1p033": peak * G_BASELINE,
        "per_axis_rail_g": raw_rail_g,
        "samples_ge_8_normalized_g": ge8,
        "samples_ge_16_normalized_g": ge16,
        "samples_ge_per_axis_rail_raw": ge_raw_rail,
        "midflight_reload": {
            str(n): {
                "window_s": [w[0], w[1]],
                "max_normalized_g_strictly_inside": w[2],
                "t_of_max_s": w[3],
                "offset_from_takeoff_s": (w[3] - w[0]) if w[3] is not None else None,
            } for n, w in windows.items()
        },
    }


def m5():
    out = []
    for L in (0.05, 0.10, 0.20, 0.30, 0.40):
        # L * g * T^2 / 8 = TOL_M
        T = math.sqrt(8.0 * TOL_M / (L * G))
        out.append({"support_fraction_L": L, "max_airtime_s_within_0p1524m": T})
    return out


def main():
    jpath = os.path.join(SESSION, "jumps.csv")
    tpath = os.path.join(SESSION, "trace.csv")
    result = {
        "constants": {"G": G, "tolerance_m": TOL_M, "g_baseline_assumed": G_BASELINE},
        "M5_support_error_boundary": m5(),
    }
    if not (os.path.exists(jpath) and os.path.exists(tpath)):
        result["M1_M2"] = "NOT RUN - session files absent"
        result["M3_M4"] = "NOT RUN - session files absent"
        sys.stderr.write("session files absent; M1-M4 marked NOT RUN\n")
    else:
        rows = load_jumps(jpath)
        result["M1_M2"] = m1_m2(rows)
        result["M3_M4"] = m3_m4(tpath, rows)
    outp = os.path.join(HERE, "corpus_recheck.json")
    with open(outp, "w") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
    print("wrote", outp)
    if isinstance(result["M1_M2"], dict):
        d = result["M1_M2"]
        print("jumps=%d outside_band=%d above_gate=%d n_air_reconciles=%d/%d"
              % (d["n_jumps"], d["n_outside_predicted_band_0_to_0p07g"],
                 d["n_above_freefall_gate_0p35g"], d["n_air_reconciles"], d["n_jumps"]))
        t = result["M3_M4"]
        print("trace_rows=%d peak=%.3f normalized g at t=%.3f s (raw %.2f g)"
              % (t["trace_rows"], t["peak_normalized_g"], t["peak_t_s"],
                 t["peak_raw_g_at_g_baseline_1p033"]))
        for n, w in sorted(t["midflight_reload"].items()):
            print("  jump %s mid-flight max |a| = %.2f g at +%.3f s"
                  % (n, w["max_normalized_g_strictly_inside"], w["offset_from_takeoff_s"]))
    for r in result["M5_support_error_boundary"]:
        print("  L=%.2f -> airtime formula stays inside +-0.1524 m to T=%.3f s"
              % (r["support_fraction_L"], r["max_airtime_s_within_0p1524m"]))


if __name__ == "__main__":
    main()
