"""Observation model: render the IMU |a| a real device would log from a
wing_model.Flight, so the firmware-twin detector (detector.py) can be run on
synthetic-but-physical data with known ground truth.

The gap between the CoM specific force (wing_model) and what the sensor
actually reports is the whole game. This module adds, on top of the flight's
CoM specific force:
  * a ~1 g riding baseline with chop noise before takeoff and after landing,
  * a pop / load-up bump just before takeoff,
  * the rotation-arm term (the sensor is off the CoM, so a spin adds
    centripetal omega^2*r even in ballistic flight — the primary false positive
    for the self-diagnosis signal, exercised by experiment E4),
  * sensor noise + range clipping (+-16 g on the Sense's LSM6DS3TR-C),
resampled onto the 200 Hz sample grid the detector consumes.

The output (times, mag_g) is exactly the (t, |a|) stream detector.Detector
eats, and matches sim/generate.py's contract so it is a drop-in richer
generator.

Pure standard library (math + random), matching generate.py.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import List, Tuple

from wing_model import G, Flight


@dataclass
class SensorConfig:
    fs_hz: float = 200.0          # detector sample rate
    chop_g: float = 0.12          # riding baseline chop stddev (matches generate.py)
    swell_g: float = 0.05         # slow swell amplitude
    swell_hz: float = 0.3
    pop_g: float = 1.5            # load-up bump amplitude before takeoff
    pop_s: float = 0.06           # its duration
    landing_g: float = 4.8        # landing spike peak (Simons 2025: 4.2-5.5 g)
    landing_s: float = 0.03       # spike duration
    noise_g: float = 0.02         # per-sample gaussian sensor noise
    clip_g: float = 16.0          # accel full-scale range (Sense: +-16 g)
    # Rotation confound: a constant spin of `spin_rps` about an axis with the
    # sensor at lever arm `lever_m` from the CoM adds omega^2*r centripetal.
    spin_rps: float = 0.0
    lever_m: float = 0.0
    pre_s: float = 2.0            # riding lead-in before takeoff
    post_s: float = 1.5           # riding tail-out after landing


def _interp_spec_g(flight: Flight, t: float) -> float:
    """Interpolate the flight's CoM specific-force magnitude (g) at time t
    (seconds since takeoff). Clamped to the flight window."""
    ts = flight.times
    if not ts:
        return 0.0
    if t <= ts[0]:
        return flight.spec_g[0]
    if t >= ts[-1]:
        return flight.spec_g[-1]
    # binary search
    lo, hi = 0, len(ts) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ts[mid] <= t:
            lo = mid
        else:
            hi = mid
    span = ts[hi] - ts[lo]
    frac = (t - ts[lo]) / span if span > 0 else 0.0
    return flight.spec_g[lo] + frac * (flight.spec_g[hi] - flight.spec_g[lo])


def render_session(
    flight: Flight,
    cfg: SensorConfig | None = None,
    takeoff_time_s: float = 2.0,
    seed: int = 0,
    spin_rps_fn=None,
) -> Tuple[List[float], List[float], float]:
    """Render one jump into a full (times, mag_g) session and return also the
    absolute landing time. The jump's takeoff is placed at takeoff_time_s; the
    session runs from 0 to takeoff + airtime + post_s.

    spin_rps_fn: optional callable (t_since_takeoff_s) -> rev/s. When given, the
    rotation-arm term is computed PER SAMPLE from it (a time-varying spin burst,
    experiment g4) instead of the constant cfg.spin_rps. Default None preserves
    the constant-spin behaviour, so the verified ballistic pipeline (spin=0) is
    untouched. The centripetal term is now also added in quadrature to the
    landing spike, so a spin still turning at touchdown perturbs the landing the
    way a real board-mounted sensor would (matters only when spin>0).
    """
    cfg = cfg or SensorConfig()
    rng = random.Random(seed)
    fs = cfg.fs_hz
    airtime = flight.true_airtime_s
    land_time = takeoff_time_s + airtime
    total = land_time + cfg.post_s
    n = int(total * fs)

    # Rotation-arm centripetal term (g), constant unless spin_rps_fn is given.
    const_omega = 2.0 * math.pi * cfg.spin_rps
    const_rot_g = (const_omega * const_omega * cfg.lever_m) / G

    times: List[float] = []
    mag: List[float] = []
    for i in range(n):
        t = i / fs
        if spin_rps_fn is not None:
            w = 2.0 * math.pi * spin_rps_fn(t - takeoff_time_s)
            rot_g = (w * w * cfg.lever_m) / G
        else:
            rot_g = const_rot_g
        base = 1.0 + rng.gauss(0.0, cfg.chop_g) + cfg.swell_g * math.sin(2 * math.pi * cfg.swell_hz * t)

        if t < takeoff_time_s - cfg.pop_s:
            a = base                                    # riding
        elif t < takeoff_time_s:
            a = base + cfg.pop_g                        # pop / load-up
        elif t < land_time:
            # Airborne: CoM specific force + rotation-arm + sensor noise.
            sg = _interp_spec_g(flight, t - takeoff_time_s)
            # rotation adds in quadrature-ish; treat as scalar magnitude add on
            # the low-|a| airborne signal (dominant when sg~0).
            a = math.hypot(sg, rot_g) + abs(rng.gauss(0.0, cfg.noise_g))
        elif t < land_time + cfg.landing_s:
            # Landing spike + rotation-arm in quadrature (rot_g=0 when spin=0,
            # so this reduces to the plain spike for the ballistic pipeline).
            a = math.hypot(cfg.landing_g + rng.gauss(0.0, 0.5), rot_g)
        else:
            a = base                                    # riding again

        a = max(0.0, min(a, cfg.clip_g))                # range clip
        times.append(t)
        mag.append(a)

    return times, mag, land_time
