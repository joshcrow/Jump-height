"""Jump-detection state machine (airtime method) — Python reference.

This is a 1:1 mirror of firmware/include/jump_detector.h. Keep the two in sync:
tune parameters here against captured data, then copy them into the header.

Physics:  height = g * airtime**2 / 8    (see docs/algorithm.md)

Pure standard library — no numpy required.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Params:
    """Tunable thresholds. Defaults match jump_detector.h."""

    g: float = 9.80665             # gravity, m/s^2
    freefall_enter_g: float = 0.35     # |a| below this => possible takeoff
    freefall_confirm_s: float = 0.08   # must stay low this long to confirm launch
    landing_threshold_g: float = 2.50  # |a| above this while airborne => landing
    min_airtime_s: float = 0.25        # reject anything shorter (chop/noise)
    max_airtime_s: float = 8.00        # sanity cap; also unsticks AIRBORNE state


@dataclass
class JumpEvent:
    takeoff_time_s: float
    airtime_s: float
    height_m: float


# States
RIDING, CANDIDATE, AIRBORNE = 0, 1, 2


class Detector:
    """Streaming detector. Feed samples via update(); it returns a JumpEvent
    exactly on the sample that completes a valid jump, else None."""

    def __init__(self, params: Params | None = None) -> None:
        self.p = params or Params()
        self.state = RIDING
        self.takeoff_time = 0.0

    def update(self, t_s: float, accel_mag_g: float) -> JumpEvent | None:
        p = self.p
        if self.state == RIDING:
            if accel_mag_g < p.freefall_enter_g:
                self.state = CANDIDATE
                self.takeoff_time = t_s  # pin takeoff to the start of the dip

        elif self.state == CANDIDATE:
            if accel_mag_g >= p.freefall_enter_g:
                self.state = RIDING  # popped back up: was just a bump
            elif t_s - self.takeoff_time >= p.freefall_confirm_s:
                self.state = AIRBORNE  # sustained free-fall: real launch

        elif self.state == AIRBORNE:
            if accel_mag_g > p.landing_threshold_g:
                airtime = t_s - self.takeoff_time
                self.state = RIDING
                if p.min_airtime_s <= airtime <= p.max_airtime_s:
                    return JumpEvent(
                        takeoff_time_s=self.takeoff_time,
                        airtime_s=airtime,
                        height_m=p.g * airtime * airtime / 8.0,
                    )
            elif t_s - self.takeoff_time > p.max_airtime_s:
                self.state = RIDING  # safety: never saw a landing, reset

        return None


def height_for_airtime(airtime_s: float, g: float = 9.80665) -> float:
    """The core physics, exposed for tests/ground-truth: h = g * T^2 / 8."""
    return g * airtime_s * airtime_s / 8.0
