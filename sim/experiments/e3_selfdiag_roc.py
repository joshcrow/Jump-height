"""EXPERIMENT E3 — SELF-DIAGNOSIS ROC.

Question: can the median airborne |a| (feat.median_g) flag a truly
non-ballistic (height-biased) jump, using only the accelerometer magnitude
stream — no gyro?

We build two ROC curves over a threshold sweep on feat.median_g:

  POSITIVE class (truly non-ballistic, height bias > 13%):
      const_lift(a_v) for a_v in a grid 0.12..0.60 g. Mid-flight spec_g == a_v
      exactly (verified anchor), so these SHOULD be flagged.

  NEGATIVE class (truly ballistic, a_v = 0, should NOT be flagged):
      Curve A ("no rotation"):  negatives rendered with spin_rps = 0.
      Curve B ("rotation confound"): negatives are the SAME ballistic jump but
      rendered with a spin sampled in spin_rps ∈ [0, 2.0] rps at lever
      lever_m ∈ [0.1, 0.6] m, adding omega^2 r centripetal to the airborne |a|.

Each flight is integrated once (deterministic given a_v); the sensor render is
repeated over many fixed integer seeds. For each threshold we compute TPR
(positives flagged) and FPR (negatives flagged), then trapezoidal AUC and the
best (Youden J = TPR - FPR) operating point for each curve.

Deterministic: fixed integer seeds everywhere; confound params drawn from a
seeded numpy Generator. Writes out/e3_selfdiag_roc.csv.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wing_model as wm, sensor_model as sm, selfdiag as sd
from detector import Detector, Params  # noqa: F401 (imported per boilerplate)

import numpy as np

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
os.makedirs(OUT_DIR, exist_ok=True)
CSV_PATH = os.path.join(OUT_DIR, "e3_selfdiag_roc.csv")

# ----------------------------------------------------------------- config
DT = 2e-5                      # accuracy-grade integration step (per rules)
TAKEOFF_S = 2.0                # where render_session places takeoff
APEX_TARGET_M = 3.0            # a moderate jump; sets vz0 for every flight
VZ0 = wm.vz0_for_ballistic_apex(APEX_TARGET_M)

A_V_GRID = np.round(np.arange(0.12, 0.601, 0.04), 4)   # positive class, 13 levels
N_SEEDS_POS = 40               # sensor renders per positive a_v level
N_SEEDS_NEG = 520             # negative renders (balance total pos count)

THRESHOLDS = np.round(np.linspace(0.0, 1.00, 201), 5)  # sweep on median_g

# Rotation-confound sampling ranges (Curve B negatives)
SPIN_RANGE = (0.0, 2.0)        # rps
LEVER_RANGE = (0.1, 0.6)       # m
CONFOUND_SEED = 20260804       # deterministic confound-parameter draw


def median_feature(flight, cfg, seed):
    """Render one session and return the trimmed-air-window median |a| (g)."""
    times, mag, land = sm.render_session(
        flight, cfg=cfg, takeoff_time_s=TAKEOFF_S, seed=seed
    )
    feat = sd.air_window_features(times, mag, TAKEOFF_S, land)
    return feat.median_g


# ----------------------------------------------------------- POSITIVE class
# One deterministic flight per a_v level, rendered over many seeds.
pos_medians = []
for j, a_v in enumerate(A_V_GRID):
    flight = wm.integrate_flight(VZ0, wm.const_lift(float(a_v)), dt=DT)
    for k in range(N_SEEDS_POS):
        seed = 1000 * (j + 1) + k            # unique deterministic seed
        cfg = sm.SensorConfig(spin_rps=0.0, lever_m=0.0)
        pos_medians.append(median_feature(flight, cfg, seed))
pos_medians = np.asarray(pos_medians)

# ----------------------------------------------------------- NEGATIVE class
# One deterministic ballistic flight (a_v = 0), rendered over many seeds.
ball = wm.integrate_flight(VZ0, wm.const_lift(0.0), dt=DT)

# Curve A negatives: no rotation.
negA_medians = []
for k in range(N_SEEDS_NEG):
    seed = 500000 + k
    cfg = sm.SensorConfig(spin_rps=0.0, lever_m=0.0)
    negA_medians.append(median_feature(ball, cfg, seed))
negA_medians = np.asarray(negA_medians)

# Curve B negatives: same ballistic flight, but each render carries a spin at a
# lever arm sampled from a seeded Generator (deterministic).
rng = np.random.default_rng(CONFOUND_SEED)
negB_medians = []
negB_rot_g = []
for k in range(N_SEEDS_NEG):
    spin = float(rng.uniform(*SPIN_RANGE))
    lever = float(rng.uniform(*LEVER_RANGE))
    omega = 2.0 * np.pi * spin
    negB_rot_g.append((omega * omega * lever) / wm.G)
    seed = 700000 + k
    cfg = sm.SensorConfig(spin_rps=spin, lever_m=lever)
    negB_medians.append(median_feature(ball, cfg, seed))
negB_medians = np.asarray(negB_medians)
negB_rot_g = np.asarray(negB_rot_g)


# ------------------------------------------------------------- ROC / AUC
def roc(pos, neg, thresholds):
    """flag = median_g > threshold. Returns tpr[], fpr[] over thresholds."""
    tpr = np.array([(pos > t).mean() for t in thresholds])
    fpr = np.array([(neg > t).mean() for t in thresholds])
    return tpr, fpr


def auc_mw(pos, neg):
    """Exact ROC AUC via the Mann-Whitney statistic:
    P(pos > neg) + 0.5 * P(pos == neg). Threshold-grid independent, so it is
    immune to the sampling/duplicate-FPR artifacts of trapezoidal integration
    over a fixed grid."""
    pos = np.sort(np.asarray(pos, dtype=float))
    neg = np.sort(np.asarray(neg, dtype=float))
    n_p, n_n = len(pos), len(neg)
    less = np.searchsorted(neg, pos, side="left")    # negs strictly below each pos
    lesseq = np.searchsorted(neg, pos, side="right")  # negs <= each pos
    equal = lesseq - less
    wins = less.sum() + 0.5 * equal.sum()
    return float(wins / (n_p * n_n))


tprA, fprA = roc(pos_medians, negA_medians, THRESHOLDS)
tprB, fprB = roc(pos_medians, negB_medians, THRESHOLDS)
aucA = auc_mw(pos_medians, negA_medians)
aucB = auc_mw(pos_medians, negB_medians)

# Best operating point by Youden J = TPR - FPR.
jA = tprA - fprA
jB = tprB - fprB
iA = int(np.argmax(jA))
iB = int(np.argmax(jB))

# ---------------------------------------------------------------- write CSV
with open(CSV_PATH, "w") as f:
    f.write("threshold,tpr_A,fpr_A,tpr_B,fpr_B\n")
    for i, thr in enumerate(THRESHOLDS):
        f.write(f"{thr:.5f},{tprA[i]:.6f},{fprA[i]:.6f},{tprB[i]:.6f},{fprB[i]:.6f}\n")

# ---------------------------------------------------------------- report
print(f"CSV written: {CSV_PATH}")
print(f"positives: {len(pos_medians)}  (a_v grid {A_V_GRID[0]}..{A_V_GRID[-1]} g, "
      f"{len(A_V_GRID)} levels x {N_SEEDS_POS} seeds)")
print(f"negatives per curve: {N_SEEDS_NEG}")
print(f"pos median_g  range [{pos_medians.min():.4f}, {pos_medians.max():.4f}]  "
      f"mean {pos_medians.mean():.4f}")
print(f"negA median_g range [{negA_medians.min():.4f}, {negA_medians.max():.4f}]  "
      f"mean {negA_medians.mean():.4f}")
print(f"negB median_g range [{negB_medians.min():.4f}, {negB_medians.max():.4f}]  "
      f"mean {negB_medians.mean():.4f}")
print(f"negB rot_g    range [{negB_rot_g.min():.4f}, {negB_rot_g.max():.4f}]  "
      f"mean {negB_rot_g.mean():.4f}")
print()
print(f"AUC  Curve A (no rotation)      = {aucA:.4f}")
print(f"AUC  Curve B (rotation confound)= {aucB:.4f}")
print(f"AUC degradation                 = {aucA - aucB:.4f}  "
      f"({100*(aucA-aucB)/aucA:.1f}% relative)")
print()
print(f"Best op point A: thr={THRESHOLDS[iA]:.4f}  "
      f"TPR={tprA[iA]:.3f}  FPR={fprA[iA]:.3f}  J={jA[iA]:.3f}")
print(f"Best op point B: thr={THRESHOLDS[iB]:.4f}  "
      f"TPR={tprB[iB]:.3f}  FPR={fprB[iB]:.3f}  J={jB[iB]:.3f}")
print()
# What the shipped 0.12 g default flag does on each curve:
def at_threshold(pos, neg, thr):
    return float((pos > thr).mean()), float((neg > thr).mean())
tA, fA = at_threshold(pos_medians, negA_medians, 0.12)
tB, fB = at_threshold(pos_medians, negB_medians, 0.12)
print(f"Shipped flag (0.12 g): Curve A TPR={tA:.3f} FPR={fA:.3f} | "
      f"Curve B TPR={tB:.3f} FPR={fB:.3f}")
