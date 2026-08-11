import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wing_model as wm, sensor_model as sm, selfdiag as sd

G = wm.G
FLAG = 0.12
GATE = 0.35
DT = 2e-5
TAKEOFF = 2.0
SPINS = [0.0,0.25,0.5,0.75,1.0,1.25,1.5,1.75,2.0,2.25,2.5]
LEVERS = [0.1,0.3,0.5,0.8]
NOISE_FLOOR = 0.6745 * sm.SensorConfig().noise_g  # half-normal median of |noise|

def rot_g(spin, lever):
    w = 2*math.pi*spin
    return w*w*lever/G

def median_of(flight, spin, lever, seed=0):
    cfg = sm.SensorConfig(spin_rps=spin, lever_m=lever)
    t, m, land = sm.render_session(flight, cfg=cfg, takeoff_time_s=TAKEOFF, seed=seed)
    f = sd.air_window_features(t, m, TAKEOFF, land)
    return f.median_g

vz0 = wm.vz0_for_ballistic_apex(2.0)
ball = wm.integrate_flight(vz0, wm.const_lift(0.0), dt=DT)
lift = wm.integrate_flight(vz0, wm.const_lift(0.20), dt=DT)
print("ballistic mid spec_g", ball.spec_g[len(ball.spec_g)//2])
print("lift mid spec_g", lift.spec_g[len(lift.spec_g)//2])

# --- independent bare FP, above-gate, gyro FP over 44 combos (seed 0) ---
bare_flag = 0
above_gate = 0
gyro_flag = 0
quad_flag = 0
n = 0
for lev in LEVERS:
    for sp in SPINS:
        r = rot_g(sp, lev)
        med = median_of(ball, sp, lev)
        n += 1
        bare_flag += med > FLAG
        above_gate += med > GATE
        gyro = math.sqrt(max(0.0,(med-NOISE_FLOOR)**2 - r*r))
        quad = math.sqrt(max(0.0, med*med - r*r))
        gyro_flag += gyro > FLAG
        quad_flag += quad > FLAG
print(f"bare FP = {bare_flag}/{n} = {bare_flag/n:.3f}")
print(f"above gate = {above_gate}/{n} = {above_gate/n:.3f}")
print(f"gyro FP = {gyro_flag}/{n} = {gyro_flag/n:.3f}")
print(f"quad-only FP = {quad_flag}/{n} = {quad_flag/n:.3f}")

# --- true-lift retention at lever 0.3 across spins ---
lev = 0.3
kept_g = kept_l = 0
for sp in SPINS:
    r = rot_g(sp, lev)
    med = median_of(lift, sp, lev)
    gyro = math.sqrt(max(0.0,(med-NOISE_FLOOR)**2 - r*r))
    lin = max(0.0, med - r)
    kept_g += gyro > FLAG
    kept_l += lin > FLAG
print(f"true-lift retained gyro = {kept_g}/11 ; linear = {kept_l}/11")
print(f"recovered spec_g (spin0, lever0.3) gyro = {math.sqrt(max(0.0,(median_of(lift,0.0,0.3)-NOISE_FLOOR)**2)):.3f}")

# --- min break spin & medians at lever 0.3 ---
lev = 0.3
min_break = None
for sp in SPINS:
    med = median_of(ball, sp, lev)
    if med > FLAG and min_break is None:
        min_break = sp
    if sp in (0.25, 0.5):
        print(f"lever0.3 spin{sp} median = {med:.4f}  rot={rot_g(sp,lev):.4f}")
print("min break spin lever0.3 =", min_break)
print("analytic crossover =", math.sqrt(FLAG*G/lev)/(2*math.pi))
