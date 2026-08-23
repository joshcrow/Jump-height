"""Self-calibrating mount lever arm — Python reference.

1:1 mirror of firmware/include/lever_arm.h. Read that file's header for the
full rationale; the short version:

In flight the board is in free fall, so specific force is ~0 and whatever the
accelerometer reads IS the rotation's own centripetal term:

    |a| = omega**2 * r / g      ->      r = |a| * g / omega**2

which lets the firmware measure its own lever arm from any spun jump. This
matters because r is NOT "where you stuck the puck" — it is the distance from
the ROTATION AXIS to the puck, and the axis is set by the trick, not the
mount. No tape measure can produce it.

The three load-bearing choices (identical in both languages):
  1. RAW accelerometer magnitude in, never the corrected one — using the
     corrected value would be circular.
  2. Median, not mean — takeoff and landing transients are not free fall, and
     a landing spike would drag a mean anywhere.
  3. Use the result UNSHAVED (SAFETY_FACTOR = 1.0). The instinct is to err
     short, because an over-estimated r erases the landing spike (g4). But the
     two errors push in OPPOSITE directions and there is no safe side: an
     under-estimate leaves a free-fall residual of rot_g*sqrt(1-k**2), and the
     sqrt amplifies small errors — k=0.99 leaves 14% of rot_g, which at
     r=0.5 m / 600 dps is 0.79 g against a 0.35 g gate, re-pinning takeoff
     mid-flight for a ~-95% height error. Measured: a 5% shave broke 5 of 8
     lever x spin cases; no shave fixed all 8 at +0.0%. See lever_arm.h's
     design note 3 for the full derivation.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import math

G = 9.80665

SLOTS = 64
MIN_DPS = 150.0
SAFETY_FACTOR = 1.0  # unbiased on purpose — see module docstring note 3
BLEND = 0.35

# Accelerometer saturation guard, tied to the ±16 g CTRL1_XL setting in
# lsm6ds3_min.h. A railed sample's magnitude is a FLOOR, not a measurement, and
# a floor makes r read LOW — the direction that re-pins takeoff. Found by
# experiment g5: at r=0.8 m / 900 dps the true centripetal term is 20.1 g and
# the unguarded estimator returned k=0.849, the only case of twelve outside the
# usable band. If the accel range changes, this must change with it.
CLIP_GUARD_G = 15.5


class LeverArm:
    """Streaming per-flight estimator. Mirror of jump::LeverArm."""

    def __init__(self) -> None:
        self._dps: list[float] = []
        self._r: list[float] = []
        self._value = 0.0
        self._has = False

    def observe(self, accel_mag_g_raw: float, gyro_mag_dps: float) -> None:
        """Feed one sample taken while AIRBORNE.

        accel_mag_g_raw is the UNCORRECTED magnitude in g; gyro_mag_dps is the
        bias-corrected rate magnitude in deg/s.
        """
        if gyro_mag_dps < MIN_DPS:
            return
        if accel_mag_g_raw >= CLIP_GUARD_G:
            return  # railed: a floor, not a reading
        w = gyro_mag_dps * 0.017453292519943295  # rad/s
        r = (accel_mag_g_raw * G) / (w * w)
        if not (r > 0.0) or r > 3.0:  # NaN-safe; 3 m is absurd for a mount
            return

        if len(self._dps) < SLOTS:
            self._dps.append(gyro_mag_dps)
            self._r.append(r)
            return
        # Full: replace the weakest sample, but only if this one beats it.
        weakest = min(range(SLOTS), key=lambda i: self._dps[i])
        if gyro_mag_dps > self._dps[weakest]:
            self._dps[weakest] = gyro_mag_dps
            self._r[weakest] = r

    def commit(self) -> bool:
        """Close out a flight; fold its median into the running estimate.

        Call ONLY when the flight ended in a VALIDATED JUMP. Every other exit
        from AIRBORNE must call discard() instead.

        This docstring used to say "call whenever the detector leaves AIRBORNE,
        jump or no jump — a rejected flight still carries perfectly good
        rotation data." That rule was the root of a confirmed calibration-
        corruption path and main.cpp retired it: phantom flights (max-airtime
        rejects manufactured by a railed gyro) are ALL rejects, and committing
        them walked a converged lever arm off by ~50x in minutes. The retired
        text survived here, and tools/tests/test_lever_arm.py was still
        implementing it (F-17, audit 2026-08-22).
        """
        if len(self._r) < 8:
            self._dps.clear()
            self._r.clear()
            return False

        s = sorted(self._r)
        n = len(s)
        median = s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])
        self._dps.clear()
        self._r.clear()

        shaved = median * SAFETY_FACTOR
        self._value = (self._value + BLEND * (shaved - self._value)
                       if self._has else shaved)
        self._has = True
        return True

    def discard(self) -> None:
        """Throw away this flight's observations without folding them in.

        Mirrors lever_arm.h's discard(). Every AIRBORNE exit that is not a
        validated jump ends here — and so does a jump when self-arm is
        compiled out, which is main.cpp's current state.

        Dropping the samples matters beyond just not committing them: up to 64
        stale observations left pending would otherwise merge into the NEXT
        flight's median and walk the estimate to a blend no real flight
        produced (the 2026-08-12 gyro-crash-hunt finding).
        """
        self._dps.clear()
        self._r.clear()

    def value(self) -> float:
        """Best estimate in metres, or 0.0 if none yet — the value that makes
        the detector's correction the exact identity."""
        return self._value if self._has else 0.0

    def has_estimate(self) -> bool:
        return self._has

    def pending_samples(self) -> int:
        return len(self._r)

    def reset(self) -> None:
        """For a re-mount: discard what was learned about the old position."""
        self._has = False
        self._value = 0.0
        self._dps.clear()
        self._r.clear()
