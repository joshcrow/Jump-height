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
import math
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
    landing_settle_s: float = 0.5      # this long at ordinary g => flight is over
    min_airtime_s: float = 0.25        # reject anything shorter (chop/noise)
    max_airtime_s: float = 3.00        # reject longer: physically absurd for wing jumps
    airtime_offset_s: float = 0.0      # calibration: added to raw airtime
    height_scale: float = 1.0          # calibration: multiplies computed height
    spin_lever_m: float = 0.0          # calibration: mount lever arm, 0 = off


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
        self.last_low_time = 0.0  # last sample that still read as free-fall
        # Why the last confirmed flight did NOT count ("too_short" |
        # "no_landing" | None). Set on the rejection sample, cleared on the
        # next update — mirrors jump_detector.h so tools can narrate misses.
        self.last_reject: str | None = None
        self.last_reject_airtime = 0.0

    def set_calibration(self, airtime_offset_s: float, height_scale: float) -> None:
        """Mirror of jump_detector.h: the two runtime-settable calibration terms."""
        self.p.airtime_offset_s = airtime_offset_s
        self.p.height_scale = height_scale

    def set_spin_lever_m(self, spin_lever_m: float) -> None:
        """Mirror of jump_detector.h: the lever arm is a MOUNT property, so it
        gets its own setter — re-calibrating a mount must not disturb a
        hard-won airtime offset / height scale."""
        self.p.spin_lever_m = spin_lever_m

    @staticmethod
    def correct_for_spin(accel_mag_g: float, gyro_mag_dps: float,
                         spin_lever_m: float, g: float) -> float:
        """Mirror of jump_detector.h's correct_for_spin. Removes the rotation's
        own centripetal term in quadrature:

            a_corr = sqrt(max(0, |a|^2 - (omega^2 * r / g)^2))

        Quadrature, not plain subtraction: the centripetal vector is
        perpendicular to the specific force it contaminates. The max(0,...)
        clamp is two-sided — an OVER-estimated r erases the landing spike
        itself (g4's landing-erasure probe), an under-estimate only leaves a
        residual, so err SHORT when calibrating. r = 0 is the identity.
        """
        if spin_lever_m <= 0.0:
            return accel_mag_g
        w_rad_s = gyro_mag_dps * 0.017453292519943295  # pi/180
        rot_g = (w_rad_s * w_rad_s * spin_lever_m) / g
        sq = accel_mag_g * accel_mag_g - rot_g * rot_g
        return math.sqrt(sq) if sq > 0.0 else 0.0

    def update(self, t_s: float, accel_mag_g: float,
               gyro_mag_dps: float | None = None) -> JumpEvent | None:
        """Feed one sample. Passing gyro_mag_dps spin-corrects it first —
        the Python stand-in for jump_detector.h's gyro-aware update()
        overload. Omitting it is the accel-only path a 3-axis v1 board has."""
        p = self.p
        if gyro_mag_dps is not None:
            accel_mag_g = self.correct_for_spin(
                accel_mag_g, gyro_mag_dps, p.spin_lever_m, p.g)
        self.last_reject = None
        if self.state == RIDING:
            if accel_mag_g < p.freefall_enter_g:
                self.state = CANDIDATE
                self.takeoff_time = t_s  # pin takeoff to the start of the dip

        elif self.state == CANDIDATE:
            if accel_mag_g >= p.freefall_enter_g:
                self.state = RIDING  # popped back up: was just a bump
            elif t_s - self.takeoff_time >= p.freefall_confirm_s:
                self.state = AIRBORNE  # sustained free-fall: real launch
                self.last_low_time = t_s

        elif self.state == AIRBORNE:
            if accel_mag_g < p.freefall_enter_g:
                self.last_low_time = t_s  # still falling
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
                self.last_reject = ("too_short" if raw < p.min_airtime_s
                                    else "no_landing")
                self.last_reject_airtime = raw
            elif t_s - self.last_low_time >= p.landing_settle_s:
                # Ordinary readings for a while: the flight is over but the
                # landing never spiked. Release NOW — stuck-airborne is deaf
                # to the next takeoff, and a later stray spike would close
                # the stale flight as a monster jump (a real desk session
                # stored a "57 m" jump exactly this way).
                self.state = RIDING
                self.last_reject = "no_landing"
                self.last_reject_airtime = t_s - self.takeoff_time
            elif t_s - self.takeoff_time > p.max_airtime_s:
                self.state = RIDING  # belt-and-suspenders: settle fires first
                self.last_reject = "no_landing"
                self.last_reject_airtime = t_s - self.takeoff_time

        return None


def height_for_airtime(airtime_s: float, g: float = 9.80665) -> float:
    """The core physics, exposed for tests/ground-truth: h = g * T^2 / 8."""
    return g * airtime_s * airtime_s / 8.0
