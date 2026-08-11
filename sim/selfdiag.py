"""Sensor self-diagnosis: can the device flag a non-ballistic jump from the
|a| stream alone, with no video and no gyro?

Physics (wing_model header): in true free-fall the accelerometer reads ~0 g
through the air; under a sustained wing force it reads |F_wing|/m. So the mean
|a| over the airborne window is a DIRECT readout of the wing's specific force —
and because that magnitude includes the horizontal component too, it is always
>= the vertical part that actually biases the height. The flag is therefore
CONSERVATIVE: it can't under-warn about a height-biasing jump.

Flagging needs only |a| magnitude (works on today's hardware, offline over
USB). CORRECTING the height would need to separate the vertical component from
the total, which needs the gravity direction, which needs orientation/AHRS,
which needs the gyro — so this module deliberately stops at the flag and notes
where the gyro would earn its keep.

The adversary is not thermal noise (a sustained 0.05 g over ~1 s is far above a
MEMS noise floor) but the rotation-arm term: a fast spin adds omega^2*r even in
ballistic flight. experiment E4 measures that false-positive rate; the
threshold here is chosen against it.

Pure standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple


@dataclass
class AirWindowFeatures:
    n: int
    mean_g: float          # mean |a| over the trimmed air window (the lift readout)
    median_g: float        # spike-robust variant
    frac_above_eps: float  # fraction of samples above `eps`
    eps: float


def air_window_features(
    times: Sequence[float],
    mag: Sequence[float],
    takeoff_s: float,
    landing_s: float,
    trim_s: float = 0.15,
    eps: float = 0.08,
) -> AirWindowFeatures:
    """Features over the airborne window [takeoff+trim, landing-trim]. The trim
    excludes the pop and the landing spike so the steady-flight |a| is
    isolated."""
    lo = takeoff_s + trim_s
    hi = landing_s - trim_s
    vals = [m for t, m in zip(times, mag) if lo <= t <= hi]
    if not vals:
        return AirWindowFeatures(0, 0.0, 0.0, 0.0, eps)
    s = sorted(vals)
    n = len(s)
    mean_g = sum(s) / n
    median_g = s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])
    frac = sum(1 for v in s if v > eps) / n
    return AirWindowFeatures(n, mean_g, median_g, frac, eps)


def flag_nonballistic(feat: AirWindowFeatures, threshold_g: float = 0.12) -> bool:
    """Flag a jump as non-ballistic (height likely overestimated) when the
    robust mid-flight |a| exceeds threshold_g. Uses the median (spike-robust).
    Default 0.12 g sits above the rotation/noise floor (E4) and below the
    ~0.35 g detector gate, so it fires in the band where the height bias is
    real but detection still succeeds."""
    return feat.median_g > threshold_g


def estimate_overshoot(feat: AirWindowFeatures) -> float:
    """Rough height-overestimate factor implied by the flag, treating the
    measured mid-flight |a| as if it were all vertical (an UPPER bound on the
    bias, since horizontal force inflates |a| without biasing height). = 1 /
    (1 - min(median_g, 0.95)). For triage only; a real correction needs the
    gyro to isolate the vertical component."""
    a = min(feat.median_g, 0.95)
    return 1.0 / (1.0 - a)
