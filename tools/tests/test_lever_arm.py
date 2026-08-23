"""Self-calibrating lever arm — sim/lever_arm.py and its C++ mirror.

The docs all require a "mount-position calibration step" (DECISIONS.md #29,
docs/gyro-sim-plan.md §6, docs/data-pipeline.md's per-unit calibration record)
without specifying a method. This is the method: in free fall specific force
is ~0, so the accelerometer reads the centripetal term directly and r falls
out as |a|*g/omega**2.

The headline test is test_uncalibrated_device_fixes_itself_over_jumps — a
device that starts knowing NOTHING about its mount, ships with the correction
inert (r=0), and converges to correct heights on its own. That is the whole
point; everything else here guards a way it could go quietly wrong.

The UNDER-estimate hazard is the one to watch, which is the opposite of what
this file first assumed. g4's "an over-estimated r erases the landing spike"
led to a 5% safety shave; that shave broke 5 of 8 lever x spin cases, because
an under-estimate leaves a free-fall residual of rot_g*sqrt(1-k**2) and the
sqrt amplifies small errors — 0.79 g against a 0.35 g gate at r=0.5 m /
600 dps. The two errors push in opposite directions and there is no safe side,
so the target is UNBIASED. test_no_deliberate_shave and
test_a_five_percent_short_shave_would_break_it pin both the constant and the
reasoning.

Second way it goes quietly wrong, found by experiment g5: a saturated
accelerometer. Past the ±16 g range a sample's magnitude is a floor, not a
measurement, and a floor makes r read LOW — the dangerous direction. At
r=0.8 m / 900 dps (20.1 g) the unguarded estimator returned k=0.849, the only
case of twelve outside the usable band. CLIP_GUARD_G rejects those samples.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import math
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "sim"))

import sensor_model as sm  # noqa: E402
import wing_model as wm  # noqa: E402
from detector import Detector, Params  # noqa: E402
from lever_arm import CLIP_GUARD_G, SAFETY_FACTOR, LeverArm  # noqa: E402

SEED = 20260805
TAKEOFF = 2.0
APEX_M = 3.0


def _burst(peak_dps, airtime, t_peak_frac=0.55, width_frac=0.20):
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
    """The no-spin jump every corrected result should reproduce."""
    times, mags = sm.render_session(flight, sm.SensorConfig(), TAKEOFF, SEED)[:2]
    det = Detector(Params())
    ev = None
    for t, a in zip(times, mags):
        e = det.update(t, a)
        if e is not None:
            ev = e
    assert ev is not None
    return ev


def _render(flight, peak_dps, lever_m, t_peak_frac=0.55, width_frac=0.20):
    spin_fn = _burst(peak_dps, flight.true_airtime_s, t_peak_frac, width_frac)
    cfg = sm.SensorConfig(lever_m=lever_m)
    times, mags, _ = sm.render_session(flight, cfg, TAKEOFF, SEED, spin_rps_fn=spin_fn)
    return times, mags, spin_fn


def _fly(times, mags, spin_fn, det, arm, takeoff=TAKEOFF):
    """Run one flight through detector + estimator exactly as main.cpp does.

    Observe RAW magnitude while airborne; on leaving AIRBORNE, commit ONLY IF
    the flight ended in a validated jump, and discard otherwise.

    THAT GATE IS THE POINT (F-17, audit 2026-08-22). This function used to
    claim "exactly as main.cpp does" in its docstring and then commit on EVERY
    AIRBORNE exit — the ungated policy main.cpp documents as having walked a
    converged lever arm off by ~50x in minutes. So the flagship test file was
    certifying the harmful policy under the label of the real one, and passing
    while doing it. Its green was not evidence about the shipped behaviour.

    main.cpp's actual shape (main.cpp:1634-1660):

        if (was_airborne && state != AIRBORNE) {
            if (jumped) {
                if (JH_SPIN_SELFARM_ENABLED) { commit(); ... } else discard();
            } else { discard(); airReset(); }
        }

    JH_SPIN_SELFARM_ENABLED is currently 0 — self-arm is compiled OUT for the
    one-shot water session — so on the real device every path discards today.
    These tests exercise the estimator as if it were enabled, which is what
    they are for; what they must not do is exercise a policy that was retired.
    """
    from detector import AIRBORNE

    ev = None
    for t, a in zip(times, mags):
        dps = 360.0 * spin_fn(t - takeoff)
        was_airborne = det.state == AIRBORNE
        if was_airborne:
            arm.observe(a, dps)          # RAW magnitude, never the corrected one
        e = det.update(t, a, dps)
        if e is not None:
            ev = e
        if was_airborne and det.state != AIRBORNE:
            # `e` is the detector's verdict for the flight that just ended: a
            # JumpEvent means validated, None means rejected.
            if e is not None:
                arm.commit()
                det.set_spin_lever_m(arm.value())
            else:
                arm.discard()
    return ev


def _herr(ev, reference):
    if ev is None:
        return None
    return 100.0 * (ev.height_m - reference.height_m) / reference.height_m


# --------------------------------------------------------------- the headline

@pytest.mark.parametrize("true_r", [
    # r=0.2 does NOT self-calibrate under main.cpp's actual commit policy, and
    # this xfail records that rather than hiding it (F-24, filed 2026-08-23).
    #
    # It passed before F-17 only because _fly() was running the RETIRED ungated
    # policy. Traced sample by sample, at r=0.2 / 600 dps the uncalibrated
    # detector produces TWO exits from AIRBORNE:
    #     t=2.925  64 observations  -> REJECTED  -> discard
    #     t=3.560   0 observations  -> validated -> commit, with nothing to commit
    # The flight carrying the data is rejected (2.24 g of uncorrected spin
    # distorts its airtime), and the flight that validates carries none. So the
    # estimator can never bootstrap at this radius. Larger radii work because
    # their first exit is itself validated.
    #
    # strict: if this ever starts passing, the xfail FAILS - so whoever fixes
    # F-24 is told, instead of a silent green.
    pytest.param(0.2, marks=pytest.mark.xfail(
        strict=True,
        reason="F-24: at r=0.2 the observation-bearing flight is rejected and "
               "the validated one has no observations, so self-arm cannot "
               "bootstrap. Not reachable today - JH_SPIN_SELFARM_ENABLED is 0.")),
    0.3, 0.5, 0.8,
])
def test_uncalibrated_device_fixes_itself_over_jumps(flight, reference, true_r):
    """A device that has never been calibrated, told nothing about its mount,
    converges to correct heights on its own."""
    p = Params()
    p.spin_lever_m = 0.0            # knows nothing
    det = Detector(p)
    arm = LeverArm()

    errors = []
    for _ in range(4):
        times, mags, spin_fn = _render(flight, 600, lever_m=true_r)
        ev = _fly(times, mags, spin_fn, det, arm)
        errors.append(_herr(ev, reference))

    assert arm.has_estimate(), "no estimate produced from four spun jumps"
    est = arm.value()
    assert abs(est - true_r) / true_r < 0.12, (
        f"converged to r={est:.3f} vs true {true_r} "
        f"({100*(est-true_r)/true_r:+.1f}%)"
    )
    # The last jump — by which point it has calibrated itself — must be right.
    assert errors[-1] is not None, f"jump lost after self-calibration: {errors}"
    assert abs(errors[-1]) < 15.0, f"height errors across jumps: {errors}"


def test_first_jump_is_uncorrected_then_improves(flight, reference):
    """Honesty check on the mechanism: jump 1 cannot already be corrected —
    there is nothing to correct with yet. The value is that jump 2 onward is."""
    p = Params()
    p.spin_lever_m = 0.0
    det = Detector(p)
    arm = LeverArm()

    times, mags, spin_fn = _render(flight, 600, lever_m=0.3)
    first = _fly(times, mags, spin_fn, det, arm)
    times, mags, spin_fn = _render(flight, 600, lever_m=0.3)
    second = _fly(times, mags, spin_fn, det, arm)

    first_err, second_err = _herr(first, reference), _herr(second, reference)
    first_bad = first is None or abs(first_err) > 15.0
    assert first_bad, (
        f"first jump was already accurate ({first_err}%) — the uncorrected "
        "failure this whole mechanism addresses did not reproduce"
    )
    assert second is not None and abs(second_err) < 15.0, (
        f"second jump should be corrected; got {second_err}%"
    )


# ----------------------------------------------- the shave that must NOT exist

@pytest.mark.parametrize("true_r", [0.2, 0.3, 0.5, 0.8])
def test_estimate_lands_close_to_truth_and_never_short(flight, true_r):
    """This file originally asserted the OPPOSITE — that the estimate is shaved
    BELOW truth, on g4's "over-estimating erases the landing" finding. That was
    measured wrong. The two errors push in opposite directions:

      under-estimate -> free-fall residual rot_g*sqrt(1-k^2). The sqrt
                        amplifies: k=0.99 leaves 14% of rot_g, which at
                        r=0.5 m / 600 dps is 0.79 g against a 0.35 g gate.
                        Takeoff re-pins mid-flight; height reads ~-95% low.
      over-estimate  -> free-fall clamps to 0 (gate happy); only a LARGE
                        over-estimate (g4 used +20%) erases the landing.

    A deliberate 5% short-shave broke 5 of 8 lever x spin cases. So: land close,
    and if anything land slightly high — never short.
    """
    arm = LeverArm()
    times, mags, spin_fn = _render(flight, 600, lever_m=true_r)
    for t, a in zip(times, mags):
        dt = t - TAKEOFF
        if 0.0 < dt < flight.true_airtime_s:
            arm.observe(a, 360.0 * spin_fn(dt))
    assert arm.commit()
    k = arm.value() / true_r
    assert 1.0 <= k < 1.10, (
        f"estimate {arm.value():.4f} vs true {true_r} is k={k:.4f}. Below 1.0 "
        "re-pins takeoff on high-spin jumps; above 1.10 approaches the +20% "
        "that erases landings."
    )


def test_no_deliberate_shave():
    """Lowering SAFETY_FACTOR is not a free safety margin — it breaks
    high-spin jumps outright. Pinned so it is changed only on purpose."""
    assert SAFETY_FACTOR == 1.0


@pytest.mark.parametrize("true_r,peak_dps", [(0.5, 600), (0.8, 600), (0.3, 600)])
def test_a_five_percent_short_shave_would_break_it(flight, reference, true_r, peak_dps):
    """Guards the reasoning, not just the constant: prove that erring short
    actually breaks these jumps, so nobody reintroduces the shave believing it
    is harmless."""
    from detector import AIRBORNE

    p = Params()
    p.spin_lever_m = 0.0
    det = Detector(p)
    arm = LeverArm()
    last = None
    for _ in range(4):
        times, mags, spin_fn = _render(flight, peak_dps, lever_m=true_r)
        ev = None
        for t, a in zip(times, mags):
            dps = 360.0 * spin_fn(t - TAKEOFF)
            was = det.state == AIRBORNE
            if was:
                arm.observe(a, dps)
            e = det.update(t, a, dps)
            if was and det.state != AIRBORNE:
                arm.commit()
                det.set_spin_lever_m(arm.value() * 0.95)  # the shave, applied
            if e is not None:
                ev = e
        last = ev
    err = _herr(last, reference)
    assert last is None or abs(err) > 15.0, (
        f"a 5% short-shave was expected to break r={true_r} @ {peak_dps} dps, "
        f"but height error was only {err:.1f}% — if the sensitivity is genuinely "
        "gone, re-derive the residual math before trusting this"
    )


# --------------------------------------------------- refusals and edge cases

def test_straight_air_produces_no_estimate(flight):
    """A jump with no rotation has no lever-arm information in it. Inventing
    one from noise would be worse than staying uncalibrated."""
    arm = LeverArm()
    times, mags, _ = _render(flight, 0, lever_m=0.3)
    for t, a in zip(times, mags):
        arm.observe(a, 0.0)
    assert not arm.commit()
    assert arm.value() == 0.0
    assert not arm.has_estimate()


def test_value_is_zero_before_any_estimate():
    """Zero is the value that makes the detector's correction the exact
    identity — an uncalibrated device must behave as it always did."""
    arm = LeverArm()
    assert arm.value() == 0.0
    assert not arm.has_estimate()


def test_too_few_samples_are_discarded_not_guessed():
    arm = LeverArm()
    for _ in range(5):
        arm.observe(1.0, 600.0)
    assert not arm.commit()
    assert not arm.has_estimate()


def test_absurd_lever_arms_are_rejected():
    """A near-zero omega would divide out to a huge r. Guarded, or one bad
    sample poisons the median's input set."""
    arm = LeverArm()
    for _ in range(20):
        arm.observe(9.0, 151.0)   # implies r well over 3 m
    assert arm.pending_samples() == 0
    assert not arm.commit()


def test_clipped_samples_are_rejected():
    """A railed accelerometer reports a FLOOR, not a measurement, and a floor
    makes r read LOW — the direction that re-pins takeoff.

    Found by experiment g5: at r=0.8 m / 900 dps the true centripetal term is
    20.1 g, past the ±16 g range, and the unguarded estimator returned k=0.849
    — the only case of twelve that fell outside the usable band. With the guard
    it returns k=1.0012.
    """
    arm = LeverArm()
    for _ in range(30):
        arm.observe(CLIP_GUARD_G + 0.1, 900.0)     # railed
    assert arm.pending_samples() == 0, "clipped samples were accepted"
    assert not arm.commit()

    arm = LeverArm()
    for _ in range(30):
        arm.observe(CLIP_GUARD_G - 0.1, 900.0)     # just under the rail
    assert arm.pending_samples() == 30, "unclipped samples were wrongly rejected"


def test_clip_guard_sits_below_the_configured_accel_range():
    """Tied to CTRL1_XL's ±16 g in lsm6ds3_min.h. If the range changes, this
    must change with it — pinned so the coupling is not silent."""
    assert 14.0 < CLIP_GUARD_G < 16.0


@pytest.mark.parametrize("true_r,peak_dps", [(0.8, 900)])
def test_saturating_spin_still_estimates_correctly(flight, true_r, peak_dps):
    """End-to-end version of the above, on a rendered flight that genuinely
    saturates the ±16 g range (rot_g = 20.1 g at the peak)."""
    arm = LeverArm()
    times, mags, spin_fn = _render(flight, peak_dps, lever_m=true_r)
    assert max(mags) >= 15.5, "this flight was expected to rail the accel range"
    for t, a in zip(times, mags):
        dt = t - TAKEOFF
        if 0.0 < dt < flight.true_airtime_s:
            arm.observe(a, 360.0 * spin_fn(dt))
    assert arm.commit()
    k = arm.value() / true_r
    assert 1.0 <= k < 1.10, f"saturating flight gave k={k:.4f}"


def test_reset_forgets_a_previous_mount(flight):
    arm = LeverArm()
    times, mags, spin_fn = _render(flight, 600, lever_m=0.3)
    for t, a in zip(times, mags):
        dt = t - TAKEOFF
        if 0.0 < dt < flight.true_airtime_s:
            arm.observe(a, 360.0 * spin_fn(dt))
    assert arm.commit() and arm.has_estimate()
    arm.reset()
    assert not arm.has_estimate() and arm.value() == 0.0


def test_landing_spike_does_not_drag_the_estimate(flight):
    """The median's whole job. Feed a real flight, then a few landing-sized
    samples, and the answer must barely move."""
    def estimate(extra_spikes):
        arm = LeverArm()
        times, mags, spin_fn = _render(flight, 600, lever_m=0.3)
        for t, a in zip(times, mags):
            dt = t - TAKEOFF
            if 0.0 < dt < flight.true_airtime_s:
                arm.observe(a, 360.0 * spin_fn(dt))
        for _ in range(extra_spikes):
            arm.observe(2.6, 600.0)      # landing-magnitude contamination
        arm.commit()
        return arm.value()

    clean, contaminated = estimate(0), estimate(5)
    assert clean > 0
    assert abs(contaminated - clean) / clean < 0.05, (
        f"5 landing-spike samples moved the estimate {clean:.4f} -> "
        f"{contaminated:.4f}; the median is not protecting it"
    )


# --------------------------------------------------- F-17: the commit gate

def _fly_samples(times, mags, dps_list, det, arm):
    """_fly() for a hand-built sample stream, same commit gate as main.cpp."""
    from detector import AIRBORNE

    ev = None
    for t, a, dps in zip(times, mags, dps_list):
        was_airborne = det.state == AIRBORNE
        if was_airborne:
            arm.observe(a, dps)
        e = det.update(t, a, dps)
        if e is not None:
            ev = e
        if was_airborne and det.state != AIRBORNE:
            if e is not None:
                arm.commit()
                det.set_spin_lever_m(arm.value())
            else:
                arm.discard()
    return ev


def _phantom_flight(t0, seconds=3.0, hz=200.0, dps=567.0, accel_g=1.0):
    """A phantom flight: a board still RIDING at 1 g under a spin fast enough
    that the correction manufactures free-fall.

    At r=0.5 m, 567 dps gives rot_g ~= 5 g, so sqrt(1 - 25) clamps to 0 and the
    detector sees free-fall that never ends — a max-airtime reject. This is the
    shape main.cpp names as the corruption source: phantom flights are ALL
    rejects, and the retired policy committed them anyway.

    Kept under the rot_g > 16 g anti-livelock guard (F-16) on purpose: above it
    the raw magnitude passes through and no phantom forms at all.

    The stream ENDS with normal riding (1 g, gentle rotation). Without that
    tail the detector is still AIRBORNE when the samples run out, so the flight
    never exits and nothing is committed OR discarded — which quietly made the
    first version of the leak test assert against 64 still-pending
    observations.
    """
    n = int(seconds * hz)
    times = [t0 + i / hz for i in range(n)]
    mags = [accel_g] * n
    spins = [dps] * n

    # Spin down: the manufactured free-fall stops, the flight ends, and with no
    # landing spike (1 g is well under the 2.5 g threshold) it ends REJECTED —
    # which is the whole point of the probe.
    tail = int(1.5 * hz)
    t_end = times[-1]
    times += [t_end + (i + 1) / hz for i in range(tail)]
    mags += [accel_g] * tail
    spins += [30.0] * tail
    return times, mags, spins


def test_a_rejected_phantom_flight_cannot_move_the_calibration(flight):
    """F-17 regression. main.cpp:1634-1660 commits ONLY flights that ended in a
    validated jump, because committing rejects walked a converged lever arm off
    by ~50x in minutes.

    tools/tests/test_lever_arm.py's own _fly() implemented the RETIRED policy
    while its docstring claimed to mirror main.cpp — so the flagship file
    certified the harmful behaviour under the label of the correct one, and
    passed.
    """
    true_r = 0.5
    p = Params()
    p.spin_lever_m = 0.0
    det = Detector(p)
    arm = LeverArm()

    # Converge on real jumps first.
    for _ in range(3):
        times, mags, spin_fn = _render(flight, 600, lever_m=true_r)
        _fly(times, mags, spin_fn, det, arm)
    assert arm.has_estimate(), "fixture failed to converge before the probe"
    converged = arm.value()
    assert abs(converged - true_r) / true_r < 0.12, converged

    # Now a run of phantom flights. Every one is a reject.
    t = 100.0
    for _ in range(6):
        times, mags, dps = _phantom_flight(t)
        ev = _fly_samples(times, mags, dps, det, arm)
        assert ev is None, "the phantom must be REJECTED or this proves nothing"
        t = times[-1] + 1.0

    assert arm.value() == pytest.approx(converged, rel=1e-9), (
        f"phantom flights moved the calibration from {converged:.4f} to "
        f"{arm.value():.4f} — the retired ungated commit policy is back")


def test_the_phantom_probe_would_have_caught_the_retired_policy(flight):
    """The probe above only means something if the phantoms it feeds WOULD
    corrupt a converged estimate under the old rule. This runs the same
    phantoms through the retired policy — commit on every AIRBORNE exit — and
    requires the calibration to move badly.

    Without this, a phantom that quietly produced no observations at all would
    make the regression test above pass for the wrong reason.
    """
    from detector import AIRBORNE

    true_r = 0.5
    p = Params()
    p.spin_lever_m = 0.0
    det = Detector(p)
    arm = LeverArm()
    for _ in range(3):
        times, mags, spin_fn = _render(flight, 600, lever_m=true_r)
        _fly(times, mags, spin_fn, det, arm)
    converged = arm.value()

    t = 100.0
    for _ in range(6):
        times, mags, dps = _phantom_flight(t)
        for tt, a, w in zip(times, mags, dps):
            was_airborne = det.state == AIRBORNE
            if was_airborne:
                arm.observe(a, w)
            det.update(tt, a, w)
            if was_airborne and det.state != AIRBORNE:
                arm.commit()            # THE RETIRED POLICY: no jump gate
                det.set_spin_lever_m(arm.value())
        t = times[-1] + 1.0

    drift = abs(arm.value() - converged) / converged
    assert drift > 0.20, (
        f"the retired policy only moved the estimate {100*drift:.1f}% "
        f"({converged:.4f} -> {arm.value():.4f}); this probe is too weak to "
        f"prove the gate in the test above is doing anything")


def test_discarded_observations_do_not_leak_into_the_next_flight(flight):
    """Discarding has to CLEAR the pending samples, not merely skip the fold.

    The 2026-08-12 gyro-crash-hunt found this on the C++ side: a flight that
    exits AIRBORNE without committing leaves up to 64 observations pending,
    and they then merge into the NEXT flight's median and walk the estimate to
    a blend no real flight produced. lever_arm.h's discard() clears them; the
    Python mirror had no discard() at all until F-17.

    Not covered by the phantom test above - there, every flight is discarded
    and no real one follows, so a no-op discard() passes it unnoticed. Found by
    mutation-testing that test.
    """
    true_r = 0.5
    p = Params()
    p.spin_lever_m = 0.0
    det = Detector(p)
    arm = LeverArm()
    for _ in range(3):
        times, mags, spin_fn = _render(flight, 600, lever_m=true_r)
        _fly(times, mags, spin_fn, det, arm)
    converged = arm.value()
    assert arm.has_estimate()

    # One rejected phantom, which must leave nothing behind...
    times, mags, dps = _phantom_flight(100.0)
    assert _fly_samples(times, mags, dps, det, arm) is None
    assert not arm._r, "a discarded flight must leave no pending observations"

    # ...then a real jump, whose estimate must be its own.
    times, mags, spin_fn = _render(flight, 600, lever_m=true_r)
    _fly(times, mags, spin_fn, det, arm)
    assert abs(arm.value() - true_r) / true_r < 0.12, (
        f"estimate {arm.value():.4f} drifted from {converged:.4f} after a "
        f"discarded phantom — its samples leaked into this flight's median")
