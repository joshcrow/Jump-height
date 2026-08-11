import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wing_model as wm, sensor_model as sm, selfdiag as sd
import numpy as np

DT = 2e-5
TAKEOFF_S = 2.0
VZ0 = wm.vz0_for_ballistic_apex(3.0)
A_V_GRID = np.round(np.arange(0.12, 0.601, 0.04), 4)
N_SEEDS_POS = 40
N_SEEDS_NEG = 520
CONFOUND_SEED = 20260804

def med(flight, cfg, seed):
    times, mag, land = sm.render_session(flight, cfg=cfg, takeoff_time_s=TAKEOFF_S, seed=seed)
    return sd.air_window_features(times, mag, TAKEOFF_S, land).median_g

# positives
pos = []
for j, a_v in enumerate(A_V_GRID):
    fl = wm.integrate_flight(VZ0, wm.const_lift(float(a_v)), dt=DT)
    for k in range(N_SEEDS_POS):
        pos.append(med(fl, sm.SensorConfig(spin_rps=0.0, lever_m=0.0), 1000*(j+1)+k))
pos = np.array(pos)

ball = wm.integrate_flight(VZ0, wm.const_lift(0.0), dt=DT)

# negA
negA = np.array([med(ball, sm.SensorConfig(spin_rps=0.0, lever_m=0.0), 500000+k) for k in range(N_SEEDS_NEG)])

# negB
rng = np.random.default_rng(CONFOUND_SEED)
negB = []
rotg = []
for k in range(N_SEEDS_NEG):
    spin = float(rng.uniform(0.0, 2.0))
    lever = float(rng.uniform(0.1, 0.6))
    om = 2*np.pi*spin
    rotg.append(om*om*lever/wm.G)
    negB.append(med(ball, sm.SensorConfig(spin_rps=spin, lever_m=lever), 700000+k))
negB = np.array(negB); rotg = np.array(rotg)

# INDEPENDENT brute-force AUC = P(pos>neg)+0.5 P(pos==neg)
def auc_brute(p, n):
    gt = 0.0
    for x in p:
        gt += np.sum(x > n) + 0.5*np.sum(x == n)
    return gt/(len(p)*len(n))

# ALSO independent: sklearn-style trapezoid over sorted scores using rank
def auc_trap(p, n):
    labels = np.r_[np.ones(len(p)), np.zeros(len(n))]
    scores = np.r_[p, n]
    order = np.argsort(-scores, kind='mergesort')
    y = labels[order]
    tps = np.cumsum(y)
    fps = np.cumsum(1-y)
    tpr = tps/tps[-1]
    fpr = fps/fps[-1]
    return np.trapezoid(tpr, fpr)

aucA = auc_brute(pos, negA)
aucB = auc_brute(pos, negB)
print("n_pos", len(pos), "n_neg", len(negA))
print("pos median range", round(pos.min(),4), round(pos.max(),4))
print("negA mean", round(negA.mean(),4))
print("negB mean", round(negB.mean(),4), "rotg max", round(rotg.max(),4))
print("AUC_A brute =", round(aucA,4), " trap =", round(auc_trap(pos,negA),4))
print("AUC_B brute =", round(aucB,4), " trap =", round(auc_trap(pos,negB),4))
print("degradation abs =", round(aucA-aucB,4), "rel% =", round(100*(aucA-aucB)/aucA,1))
# fpr_B at thr=1.0
print("fpr_B at thr 1.0 =", round(float((negB>1.0).mean()),4))
# shipped 0.12
print("shipped 0.12 fpr_A =", round(float((negA>0.12).mean()),4), "fpr_B =", round(float((negB>0.12).mean()),4))
