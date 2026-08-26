"""Forward physics model for the wing-foil ballistic question.

The open question (docs/research.md §2 "kite exception"): a jump's height is
computed from airtime as h = g*T^2/8, which assumes *ballistic* flight (only
gravity acts). Kite jumps are NOT ballistic — the tether holds the rider aloft,
so the same airtime corresponds to a much lower true apex (Simons 2025: 5.1 m
true at 3.1 s airtime, where ballistic math says 11.8 m — a 2.3x overshoot).
The wing question: does a hand-held wing add enough sustained lift mid-air to
make wing jumps non-ballistic too? There is zero literature; this model is the
desk experiment that bounds the answer.

This module is the "truth" generator. It integrates the rider's centre-of-mass
trajectory during a jump under gravity + a wing aerodynamic force + body drag,
and reports the TRUE apex height and TRUE airtime. sensor_model.py then renders
what the IMU would actually measure from that truth, and detector.py (the
firmware twin) estimates height back from it — so the whole pipeline can be
compared against ground truth the detector never sees.

Key physics the whole program rests on:
  * The accelerometer measures SPECIFIC FORCE (proper acceleration), not
    coordinate acceleration. In free-fall it reads ~0 regardless of
    orientation. Under a non-gravitational force F it reads |F|/m. So during
    flight |a| ~ |F_wing|/m — the wing's force per mass is DIRECTLY the
    airborne |a| the sensor sees. That is the self-diagnosis signal.
  * For a constant vertical wing acceleration a_v (in g), flight is
    reduced-gravity ballistics with g_eff = g*(1 - a_v). The detector computes
    h = g*T^2/8 while the true apex is g_eff*T^2/8, so:
          h_reported / h_true = g / (g - a_v*g) = 1 / (1 - a_v)
    This closed form (closed_form_overshoot below) is the analytic anchor
    every numerical result is checked against.
  * The wing force is transmitted through the rider's ARMS, so its magnitude is
    physically capped at what the arms can hold (arm_ceiling_bw body weights).
    A kite loads a harness at the centre of mass with no such cap — which is
    precisely why kites reach the ~0.567 g sustained lift that produces the
    2.3x kite overshoot, and why wings should sit far closer to ballistic.

Pure standard library (math only), matching sim/generate.py's no-dependency
discipline. numpy/matplotlib are allowed only in the offline experiment layer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Tuple

G = 9.80665           # gravity, m/s^2 (matches detector.Params.g)
RHO_AIR = 1.225       # sea-level air density, kg/m^3

# A wing "specific acceleration" function: given (t_since_takeoff, vx, vz)
# it returns the non-gravitational acceleration the wing imparts to the rider,
# as (a_up, a_horiz_downwind) in m/s^2. Decoupling the integrator from the force
# model lets const_lift(), aero_model(), and the kite preset all feed the same
# integrator.
WingAccel = Callable[[float, float, float], Tuple[float, float]]


# --------------------------------------------------------------- force models

def const_lift(a_up_g: float, a_horiz_g: float = 0.0) -> WingAccel:
    """Constant wing specific acceleration, in g. The analytic case: with
    a_horiz=0 the flight is exactly reduced-gravity ballistics and the result
    must match closed_form_overshoot(a_up_g)."""
    au = a_up_g * G
    ah = a_horiz_g * G

    def fn(_t: float, _vx: float, _vz: float) -> Tuple[float, float]:
        return (au, ah)

    return fn


@dataclass
class WingParams:
    """Aerodynamic + rider parameters for the physically-grounded wing model."""

    mass_kg: float = 85.0          # rider + board + gear
    wing_area_m2: float = 5.0      # projected wing area
    wind_mps: float = 10.0         # true wind speed (downwind = +x)
    c_max: float = 0.8             # peak aero force coefficient (loaded)
    decay_tau_s: float = 0.25      # time constant of C(t) decay after takeoff
    technique: str = "sheeted_out"  # 'sheeted_out' | 'lofted' | 'mixed' | 'constant'
    force_elev_deg: float = 35.0   # elevation of the aero force above horizontal
    arm_ceiling_bw: float = 0.40   # cap on |F_wing| in body weights (arm limit)
    body_cd_a: float = 0.5         # rider drag Cd*A (m^2), for body drag term
    # A kite loads a harness, not the arms: set a high ceiling to model it.
    harness: bool = False


def _coeff_schedule(p: WingParams) -> Callable[[float], float]:
    """C(t) technique schedules. t is seconds since takeoff."""
    if p.technique == "constant":
        return lambda t: p.c_max
    if p.technique == "lofted":
        # Rider actively sheets in to get lofted: C stays high, slow decay.
        return lambda t: p.c_max * math.exp(-t / max(1e-3, 4.0 * p.decay_tau_s))
    if p.technique == "mixed":
        # High pop at takeoff, decays over decay_tau to a small residual.
        return lambda t: 0.15 * p.c_max + 0.85 * p.c_max * math.exp(-t / max(1e-3, p.decay_tau_s))
    # 'sheeted_out' (default, the realistic case): depower fast to near zero.
    return lambda t: p.c_max * math.exp(-t / max(1e-3, p.decay_tau_s))


def aero_model(p: WingParams) -> WingAccel:
    """Physically-grounded wing force: F = 1/2 rho V_app^2 A C(t), split into
    vertical (hang-time-extending) and horizontal (downwind) components by the
    force elevation angle, with |F| capped by the arm-force ceiling (unless
    harness=True). V_app is the apparent wind = true wind - rider velocity, so
    it changes through the jump (the nonlinear coupling)."""
    coeff = _coeff_schedule(p)
    theta = math.radians(p.force_elev_deg)
    sin_t, cos_t = math.sin(theta), math.cos(theta)
    f_ceiling = math.inf if p.harness else p.arm_ceiling_bw * p.mass_kg * G

    def fn(t: float, vx: float, vz: float) -> Tuple[float, float]:
        # Apparent wind seen by the wing (rider moving through the air mass).
        vax = p.wind_mps - vx
        vaz = -vz
        v_app_sq = vax * vax + vaz * vaz
        f = 0.5 * RHO_AIR * v_app_sq * p.wing_area_m2 * coeff(t)
        f = min(f, f_ceiling)
        a_up = f * sin_t / p.mass_kg
        a_horiz = f * cos_t / p.mass_kg
        return (a_up, a_horiz)

    return fn


def kite_preset(a_up_g: float = 0.567) -> WingAccel:
    """The kite anchor: a sustained upward specific acceleration through a
    harness. 0.567 g reproduces Simons 2025 (5.1 m true @ 3.1 s airtime,
    2.3x ballistic overshoot). Used by the kite-validation experiment to prove
    the pipeline reproduces the one real non-ballistic dataset."""
    return const_lift(a_up_g, 0.0)


# ------------------------------------------------------------- integration

@dataclass
class Flight:
    """Result of integrating one jump's trajectory (ground truth)."""

    times: List[float] = field(default_factory=list)     # s, since takeoff
    z: List[float] = field(default_factory=list)          # m, height above takeoff
    x: List[float] = field(default_factory=list)          # m, downwind
    vz: List[float] = field(default_factory=list)         # m/s
    vx: List[float] = field(default_factory=list)         # m/s
    spec_g: List[float] = field(default_factory=list)     # |specific force| in g, at CoM
    true_apex_m: float = 0.0
    true_airtime_s: float = 0.0
    # The airtime the detector *would* read is ~true_airtime; the height bias
    # comes from the formula, not the timing (verified by the ballistic case).


def integrate_flight(
    vz0: float,
    wing: WingAccel,
    vx0: float = 8.0,
    mass_kg: float = 85.0,
    body_cd_a: float = 0.5,
    dt: float = 1e-4,
    max_t: float = 6.0,
    land_z: float = 0.0,
) -> Flight:
    """Integrate a single jump from takeoff (z=0, vz=vz0>0) until it lands
    (z back down through `land_z`), via RK4.

    `land_z` exists for E14 (asymmetric water surface). Takeoff is always the
    origin, so `land_z` is the landing surface height RELATIVE TO the takeoff
    point: negative = landing in a trough below where you left the water,
    positive = landing on a crest above it. It defaults to 0.0, which is the
    flat-water assumption every experiment before E14 ran under
    (docs/algorithm.md: "symmetric parabola / takeoff ~ landing height").
    `true_apex_m` remains apex above the TAKEOFF point regardless, because
    that is the ground truth the session card defines for video labelling
    ("the board's position at takeoff as zero, not the horizon"). Returns the ground-truth trajectory plus the
    specific-force magnitude at the centre of mass (what an ideal accelerometer
    at the CoM would read, in g).

    Forces: gravity (-g), wing (a_up, a_horiz from `wing`), body air drag.
    Gravity does NOT appear in spec_g — the accelerometer only senses the
    non-gravitational part (wing + drag).
    """
    def deriv(state: Tuple[float, float, float, float], t: float):
        x, z, vx, vz = state
        a_up, a_horiz = wing(t, vx, vz)
        # Body drag opposes velocity (small but real).
        v = math.hypot(vx, vz)
        drag = 0.5 * RHO_AIR * body_cd_a * v  # = 0.5 rho Cd A |v|, force/(m*v) factor below
        ax_drag = -drag * vx / mass_kg
        az_drag = -drag * vz / mass_kg
        ax = a_horiz + ax_drag
        az = a_up - G + az_drag
        return (vx, vz, ax, az)

    f = Flight()
    state = (0.0, 0.0, vx0, vz0)
    t = 0.0
    prev_z = 0.0
    while t < max_t:
        x, z, vx, vz = state
        # Specific force at CoM = non-gravitational acceleration = wing + drag.
        a_up, a_horiz = wing(t, vx, vz)
        v = math.hypot(vx, vz)
        drag = 0.5 * RHO_AIR * body_cd_a * v
        sfx = a_horiz - drag * vx / mass_kg
        sfz = a_up - drag * vz / mass_kg
        spec_g = math.hypot(sfx, sfz) / G

        f.times.append(t)
        f.x.append(x)
        f.z.append(z)
        f.vx.append(vx)
        f.vz.append(vz)
        f.spec_g.append(spec_g)

        # Landing: z crosses back down through `land_z` after having gone up.
        if t > 0.0 and prev_z > land_z and z <= land_z:
            # Linear-interpolate the exact landing time for a clean airtime.
            denom = prev_z - z
            frac = (prev_z - land_z) / denom if denom != 0 else 1.0
            f.true_airtime_s = (t - dt) + frac * dt
            break
        prev_z = z

        # RK4 step.
        k1 = deriv(state, t)
        s2 = tuple(state[i] + 0.5 * dt * k1[i] for i in range(4))
        k2 = deriv(s2, t + 0.5 * dt)
        s3 = tuple(state[i] + 0.5 * dt * k2[i] for i in range(4))
        k3 = deriv(s3, t + 0.5 * dt)
        s4 = tuple(state[i] + dt * k3[i] for i in range(4))
        k4 = deriv(s4, t + dt)
        state = tuple(
            state[i] + (dt / 6.0) * (k1[i] + 2 * k2[i] + 2 * k3[i] + k4[i])
            for i in range(4)
        )
        t += dt

    f.true_apex_m = max(f.z) if f.z else 0.0
    if f.true_airtime_s == 0.0 and f.times:
        f.true_airtime_s = f.times[-1]
    return f


# ---------------------------------------------------------------- anchors

def closed_form_overshoot(a_v_g: float) -> float:
    """h_reported / h_true for a constant vertical wing acceleration a_v_g (in
    g). = 1 / (1 - a_v_g). The analytic ground truth for the constant-lift
    case; integrate_flight(const_lift(a_v_g)) must reproduce it."""
    if a_v_g >= 1.0:
        return math.inf
    return 1.0 / (1.0 - a_v_g)


def vz0_for_ballistic_apex(apex_m: float) -> float:
    """Takeoff vertical speed that yields a given apex under pure gravity
    (v = sqrt(2 g h)). Convenient way to request a jump of a target size."""
    return math.sqrt(2.0 * G * apex_m)


def ballistic_airtime(apex_m: float) -> float:
    """Total airtime of a pure-ballistic jump to apex_m: T = 2*sqrt(2h/g)."""
    return 2.0 * math.sqrt(2.0 * apex_m / G)
