"""Jump-detection state machine (airtime method) — Python reference.

This is a 1:1 mirror of firmware/include/jump_detector.h. Both take their
tunable settings from config/params.json (the single source of truth): the
firmware via the generated params.gen.h header, this module via load_params().

Physics:  height = height_scale * g * (airtime + airtime_offset)**2 / 8

The two calibration terms default to "off" (offset 0, scale 1):
  * airtime_offset_s — additive correction from bench drop tests
    (./tools/jump drop). Detection latency is constant in *time*, so an
    additive airtime term generalizes across jump sizes better than scaling.
  * height_scale — multiplicative correction from on-water video ground truth.

Pure standard library — no numpy required.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent.parent / "config" / "params.json"


@dataclass
class Params:
    """Tunable thresholds. Defaults match config/params.json; prefer
    load_params() so edits to the JSON take effect everywhere."""

    g: float = 9.80665             # gravity, m/s^2
    freefall_enter_g: float = 0.35     # |a| below this => possible takeoff
    freefall_confirm_s: float = 0.08   # must stay low this long to confirm launch
    landing_threshold_g: float = 2.50  # |a| above this while airborne => landing
    min_airtime_s: float = 0.25        # reject anything shorter (chop/noise)
    max_airtime_s: float = 8.00        # sanity cap; also unsticks AIRBORNE state
    airtime_offset_s: float = 0.0      # calibration: added to raw airtime
    height_scale: float = 1.0          # calibration: multiplies computed height


def load_params(path: str | os.PathLike | None = None) -> Params:
    """Load Params from config/params.json (or JH_CONFIG env override).

    Falls back to the built-in defaults if the file is missing.
    """
    p = Path(path or os.environ.get("JH_CONFIG") or DEFAULT_CONFIG)
    if not p.exists():
        return Params()
    with open(p) as f:
        cfg = json.load(f)
    fields = {k: v for k, v in cfg.get("detector", {}).items() if not k.startswith("_")}
    return Params(**fields)


@dataclass
class JumpEvent:
    takeoff_time_s: float
    airtime_raw_s: float   # measured, uncorrected
    airtime_s: float       # after airtime_offset_s calibration
    height_m: float        # height_scale * g * airtime_s^2 / 8


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
                raw = t_s - self.takeoff_time
                self.state = RIDING
                # Validate on the raw (physical) airtime; report calibrated.
                if p.min_airtime_s <= raw <= p.max_airtime_s:
                    cal = max(0.0, raw + p.airtime_offset_s)
                    return JumpEvent(
                        takeoff_time_s=self.takeoff_time,
                        airtime_raw_s=raw,
                        airtime_s=cal,
                        height_m=p.height_scale * p.g * cal * cal / 8.0,
                    )
            elif t_s - self.takeoff_time > p.max_airtime_s:
                self.state = RIDING  # safety: never saw a landing, reset

        return None


def height_for_airtime(airtime_s: float, g: float = 9.80665) -> float:
    """The core physics, exposed for tests/ground-truth: h = g * T^2 / 8."""
    return g * airtime_s * airtime_s / 8.0
