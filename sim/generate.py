"""Synthesize realistic IMU accelerometer-magnitude sessions with known jumps.

Lets you develop and test the detector with no hardware, against exact ground
truth. Pure standard library (uses `random` + `math`).

A session is described as a list of (takeoff_time_s, airtime_s) jumps. For each
jump we render:
  * a short "pop"/load-up bump just before takeoff,
  * a free-fall stretch (|a| ~ 0 g) for the airtime,
  * a landing spike (several g),
on top of a ~1 g riding baseline with chop noise and slow swell.
"""

from __future__ import annotations

import math
import random
from typing import List, Tuple

Jump = Tuple[float, float]  # (takeoff_time_s, airtime_s)


def synth_session(
    jumps: List[Jump],
    duration_s: float | None = None,
    fs_hz: float = 200.0,
    seed: int = 0,
    chop_g: float = 0.12,
    landing_g: float = 5.0,
) -> Tuple[List[float], List[float]]:
    """Return (times, accel_magnitude_g) for a synthetic session.

    times are seconds; accel_magnitude_g is |a| in g-units — exactly what the
    detector consumes.
    """
    rng = random.Random(seed)

    if duration_s is None:
        duration_s = max(t0 + at for t0, at in jumps) + 3.0
    n = int(duration_s * fs_hz)

    times = [i / fs_hz for i in range(n)]
    accel = [0.0] * n

    # Riding baseline: ~1 g gravity + chop noise + slow swell.
    for i in range(n):
        accel[i] = 1.0 + rng.gauss(0.0, chop_g) + 0.05 * math.sin(2 * math.pi * 0.3 * times[i])

    # Carve each jump into the signal.
    for (t0, at) in jumps:
        i0 = int(t0 * fs_hz)
        i1 = int((t0 + at) * fs_hz)

        # Pop / load-up just before takeoff (you edge and unweight to launch).
        pop_start = max(0, i0 - int(0.06 * fs_hz))
        pop = rng.uniform(1.0, 2.0)
        for i in range(pop_start, min(i0, n)):
            accel[i] += pop

        # Free-fall during the air: |a| ~ 0 g plus a little sensor noise.
        for i in range(max(0, i0), min(i1, n)):
            accel[i] = abs(rng.gauss(0.0, 0.05))

        # Landing spike for a few ms.
        spike_len = max(1, int(0.03 * fs_hz))
        for i in range(min(i1, n), min(i1 + spike_len, n)):
            accel[i] = landing_g + rng.gauss(0.0, 0.5)

    # Magnitude can't be negative.
    accel = [a if a > 0.0 else 0.0 for a in accel]
    return times, accel


# A default demo session used by run.py: a mix of small and big jumps.
DEMO_JUMPS: List[Jump] = [
    (5.0, 0.6),   # small hop
    (12.0, 1.0),  # medium
    (20.0, 1.5),  # big
    (30.0, 2.0),  # huge
]
