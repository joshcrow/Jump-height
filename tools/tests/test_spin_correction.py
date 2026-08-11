"""Regression oracle for the detector's omega^2*r spin correction.

Promotes sim/experiments/g4_spin_detector.py from a one-off experiment into a
standing test. g4 asked whether a mid-air spin burst corrupts the REAL
detector state machine and whether a per-sample centripetal subtraction
recovers it; the answers (yes, and yes) are why jump_detector.h now carries
correct_for_spin() on its hot path. These tests pin those answers so the
correction cannot be silently removed, mis-signed, or fed the wrong units.

What is asserted here, in order of what would hurt most if it broke:

  1. r = 0 is EXACTLY the identity — an uncalibrated device is unchanged.
  2. Uncorrected, a realistic spin wrecks height (this is the bug's proof;
     if it ever stops failing, the sensor model has drifted and the rest of
     this file is testing nothing).
  3. Corrected with the true lever arm, height error collapses to ~0 across
     the whole realistic spin range.
  4. Over-estimating r erases a late landing spike — the two-sided clamp
     hazard that makes "err SHORT" the calibration rule.

The reference is the sim's own no-spin ballistic jump, so these are relative
claims about the correction, not absolute claims about wing physics.
"""

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "sim"))

import sensor_model as sm  # noqa: E402
import wing_model as wm  # noqa: E402
from detector import Detector, Params  # noqa: E402

G = 9.80665
SEED = 20260805
TAKEOFF = 2.0
APEX_M = 3.0


def _burst(peak_dps, airtime, t_peak_frac=0.55, width_frac=0.20):
    """Spin burst in rev/s vs time-since-takeoff — ~0 at takeoff so the
    free-fall gate still confirms, Gaussian bump mid-flight. Same shape g4
    used, kept here so the test does not depend on the experiment script."""
    peak_rps = peak_dps / 360.0
    t_peak = t_peak_frac * airtime
    sigma = max(1e-3, width_frac * airtime)

    def fn(t):
        if t < 0.0:
            return 0.0
        return peak_rps * math.exp(-((t - t_peak) ** 2) / (2.0 * sigma * sigma))

    return fn


@pytest.fixture(scope="module")
def flight():
    vz0 = wm.vz0_for_ballistic_apex(APEX_M)
    return wm.integrate_flight(vz0, wm.const_lift(0.0), vx0=0.0, dt=2e-5)


@pytest.fixture(scope="module")
def reference(flight):
    """The no-spin jump the detector should reproduce under any spin."""
    times, mags = sm.render_session(flight, sm.SensorConfig(), TAKEOFF, SEED)[:2]
    ev = _detect(times, mags)
    assert ev is not None, "reference ballistic jump was not detected at all"
    return ev


def _detect(times, mags, spin_fn=None, lever_m=0.0, takeoff=TAKEOFF):
    """Run a fresh detector over a stream. When lever_m > 0 the gyro-aware
    update() path is used, with omega taken from the same spin profile the
    sensor model rendered — i.e. a perfect gyro, which is the point: this
    isolates the CORRECTION, not gyro noise."""
    p = Params()
    p.spin_lever_m = lever_m
    det = Detector(p)
    ev = None
    for t, a in zip(times, mags):
        if spin_fn is not None:
            dps = 360.0 * spin_fn(t - takeoff)
            e = det.update(t, a, dps)
        else:
            e = det.update(t, a)
        if e is not None:
            ev = e
    return ev


def _render(flight, peak_dps, lever_m, t_peak_frac=0.55, width_frac=0.20):
    spin_fn = _burst(peak_dps, flight.true_airtime_s, t_peak_frac, width_frac)
    cfg = sm.SensorConfig(lever_m=lever_m)
    times, mags, _ = sm.render_session(flight, cfg, TAKEOFF, SEED, spin_rps_fn=spin_fn)
    return times, mags, spin_fn


def _herr_pct(ev, reference):
    if ev is None:
        return None
    return 100.0 * (ev.height_m - reference.height_m) / reference.height_m


# ---------------------------------------------------------------- 1. identity

@pytest.mark.parametrize("dps", [0.0, 300.0, 900.0])
def test_zero_lever_is_exactly_identity(dps):
    """r = 0 must not perturb the sample AT ALL — an uncalibrated device has
    to behave bit-for-bit as it did before the correction existed."""
    for a in (0.0, 0.35, 1.0, 2.5, 9.9):
        assert Detector.correct_for_spin(a, dps, 0.0, G) == a


def test_zero_lever_detector_matches_accel_only_path(flight, reference):
    """Same claim one level up: with r = 0 the gyro-aware update() and the
    accel-only update() must produce identical jumps."""
    times, mags, spin_fn = _render(flight, 300.0, lever_m=0.3)
    accel_only = _detect(times, mags)
    gyro_path = _detect(times, mags, spin_fn=spin_fn, lever_m=0.0)
    assert (accel_only is None) == (gyro_path is None)
    if accel_only is not None:
        assert accel_only.airtime_raw_s == gyro_path.airtime_raw_s
        assert accel_only.height_m == gyro_path.height_m


# ------------------------------------------------- 2. the bug still reproduces

def test_uncorrected_spin_wrecks_height(flight, reference):
    """g4's headline finding. If this ever passes cleanly, the sensor model
    has drifted and every other assertion here is vacuous — so it is a test
    that the BUG exists, deliberately."""
    times, mags, _ = _render(flight, 300.0, lever_m=0.3)
    ev = _detect(times, mags)  # accel-only: what the firmware used to do
    err = _herr_pct(ev, reference)
    assert ev is None or abs(err) > 15.0, (
        f"expected a large height error from uncorrected 300 dps spin, got {err}%"
    )


# ------------------------------------------------------------ 3. the fix works

@pytest.mark.parametrize("peak_dps", [0, 100, 200, 300, 400, 500, 700, 900, 1000])
def test_correction_recovers_height_across_realistic_spin(flight, reference, peak_dps):
    """Real wing spins run 240-360 mean / 500-900 peak; sweep past both ends.
    With the true lever arm the detector should land back on the no-spin
    answer."""
    times, mags, spin_fn = _render(flight, peak_dps, lever_m=0.3)
    ev = _detect(times, mags, spin_fn=spin_fn, lever_m=0.3)
    assert ev is not None, f"jump lost entirely at {peak_dps} dps"
    err = _herr_pct(ev, reference)
    assert abs(err) < 15.0, f"{peak_dps} dps: height error {err:.1f}% after correction"


@pytest.mark.parametrize("lever_m", [0.2, 0.3, 0.5, 0.8])
def test_correction_holds_across_mount_positions(flight, reference, lever_m):
    """The 2.5 g crossover moves with r (518 dps at 0.3 m, ~317 at 0.8 m), so
    a fix that only worked at one mount would be an accident."""
    times, mags, spin_fn = _render(flight, 600, lever_m=lever_m)
    ev = _detect(times, mags, spin_fn=spin_fn, lever_m=lever_m)
    assert ev is not None, f"jump lost at r={lever_m} m"
    err = _herr_pct(ev, reference)
    assert abs(err) < 15.0, f"r={lever_m}: height error {err:.1f}% after correction"


# --------------------------------------------- 4. the over-calibration hazard

def test_over_estimated_lever_arm_can_erase_a_late_landing(flight, reference):
    """The clamp is two-sided. Still spinning hard at touchdown + an r that is
    too large drives the sqrt argument negative exactly on the landing spike,
    clamps it to zero, and the detector never sees the touchdown. This is why
    the calibration rule is 'err SHORT' — pinned so nobody 'improves' the
    clamp away."""
    times, mags, spin_fn = _render(
        flight, 900, lever_m=0.3, t_peak_frac=0.98, width_frac=0.18)
    honest = _detect(times, mags, spin_fn=spin_fn, lever_m=0.3)
    over = _detect(times, mags, spin_fn=spin_fn, lever_m=0.3 * 1.5)
    over_err = _herr_pct(over, honest if honest is not None else reference)
    assert over is None or (over_err is not None and abs(over_err) > 15.0), (
        "over-calibrated lever arm was expected to damage or erase the landing; "
        f"got {over_err}% — if the hazard is genuinely gone, delete this test "
        "deliberately rather than loosening it"
    )
