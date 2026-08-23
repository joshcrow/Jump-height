"""Adversarial FALSE-POSITIVE property tests for sim/seastate.py's noise
generators (docs/roadmap.md Tier-A backlog; docs/research.md §6/§8).

########################################################################
# THESE ARE ROBUSTNESS PROPERTY TESTS, NOT TUNING.
#
# Every threshold exercised here comes from config/params.json via
# detector.load_params() -- the SAME thresholds the live system runs.
# Nothing in this file, ever, adjusts config/params.json or
# sim/detector.py to make a noise stream stop triggering. Where a case
# below found a real false positive during development, the response was
# a permanent, named, PASSING test that documents it (see
# TestKnownFinding_ExtremeChopPlusHandlingJitter) -- not a quieter noise
# generator and not a moved threshold.
#
# And per docs/roadmap.md's Tier-A backlog framing ("desk guesses about
# what foiling 'feels like' to the sensor would be fiction"): every noise
# parameter below is a bench stand-in grounded in docs/research.md's cited
# numbers, not a validated sea model. Synthetic sea remains FICTION until
# a real on-water session trace exists -- these tests prove the detector
# is robust to OUR best-effort synthetic noise, nothing more.
########################################################################

Run via ./tools/jump simtest, or directly:
    python3 -m pytest tools/tests/test_seastate.py -q
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

import seastate  # noqa: E402  (path insert must come first)
from detector import AIRBORNE, CANDIDATE, RIDING, Detector, JumpEvent, load_params  # noqa: E402
from generate import DEMO_JUMPS, synth_session  # noqa: E402

DURATION_S = 60.0   # "≥60 s each" per the property-test brief
SEEDS = range(8)    # "several seeds" for the main sweeps


# --------------------------------------------------------------- helpers

def run_stream(det: Detector, times: List[float], accel: List[float]) -> List[JumpEvent]:
    """Feed one |a| stream through an existing Detector; return the
    JumpEvents produced.

    Also enforces, sample by sample, the general form of "no stuck
    AIRBORNE": whatever closes a flight -- a counted jump, a too_short
    reject, or a no_landing reject -- must report an elapsed time no
    longer than the detector's OWN documented max_airtime_s rail. That is
    sim/detector.py's own contract (see its landing_settle_s / "belt and
    suspenders" comments), checked here on every sample of every stream
    in this file, not just the dedicated landing-settle test below.
    """
    events: List[JumpEvent] = []
    for t, a in zip(times, accel):
        ev = det.update(t, a)
        if ev is not None:
            events.append(ev)
            assert ev.airtime_raw_s <= det.p.max_airtime_s + 1e-6, (
                f"JumpEvent airtime_raw_s={ev.airtime_raw_s:.3f} exceeds "
                f"max_airtime_s={det.p.max_airtime_s} -- detector's own rail violated")
        elif det.last_reject is not None:
            assert det.last_reject in ("too_short", "no_landing"), (
                f"unexpected last_reject value: {det.last_reject!r}")
            assert det.last_reject_airtime <= det.p.max_airtime_s + 1e-6, (
                f"last_reject_airtime={det.last_reject_airtime:.3f} exceeds "
                f"max_airtime_s={det.p.max_airtime_s} -- possible stuck AIRBORNE")
    return events


def assert_zero_jumps(case: unittest.TestCase, times: List[float], accel: List[float],
                       label: str) -> None:
    det = Detector(load_params())
    events = run_stream(det, times, accel)
    case.assertEqual(
        events, [],
        f"{label}: expected ZERO detected jumps from noise alone, got {len(events)}: "
        f"{[(round(e.takeoff_time_s, 3), round(e.airtime_raw_s, 3)) for e in events]}")
    case.assertNotEqual(det.state, AIRBORNE,
                        f"{label}: detector ended the stream stuck in AIRBORNE")


# -------------------------------------------------------- orbital chop alone

# (H metres, T seconds). Spans typical wind chop up to the edge of what
# "smooth orbital motion" can even mean physically -- research.md §6's own
# example is (0.3, 3.0), ~0.07 g. (0.8, 1.8) has steepness H/L ~= 0.16,
# at/beyond the ~1/7 deep-water breaking limit (that sea state would
# likely be breaking/whitecapping in reality, not smooth orbital motion);
# it's included anyway as the deliberate adversarial edge, and a 40-seed
# calibration pass (see the final report) confirms it stays clean ALONE
# even there -- consistent with the structural argument that chop can dip
# but never spikes above the 2.5 g landing gate by itself.
CHOP_CASES: List[Tuple[float, float]] = [
    (0.1, 2.5),
    (0.15, 1.5),
    (0.3, 3.0),
    (0.5, 3.0),
    (0.5, 2.0),
    (0.6, 2.0),
    (0.8, 1.8),
]


class TestOrbitalChopAlone(unittest.TestCase):
    """research.md §6: "chop cannot false-trigger free-fall" -- two orders
    of magnitude below the 0.35 g gate for typical wind chop. This sweep
    pushes toward the edge of physical plausibility and it still holds."""

    def test_zero_false_positives(self):
        for H, T in CHOP_CASES:
            for seed in SEEDS:
                times, accel = seastate.orbital_chop(H, T, DURATION_S, seed=seed)
                assert_zero_jumps(self, times, accel,
                                  f"orbital_chop(H={H}, T={T}, seed={seed})")


# -------------------------------------------------------- board slap alone

# (rate events/s, peak g). rate=5.0 means a slap roughly every 200 ms,
# continuously, for 60 s -- deliberately absurd, kept anyway to show the
# structural guarantee ("no preceding free-fall") holds regardless of how
# hard the sweep leans on it.
SLAP_CASES: List[Tuple[float, float]] = [
    (0.05, 3.0),
    (0.1, 6.0),
    (0.2, 4.0),
    (0.5, 5.0),
    (1.0, 6.0),
    (2.0, 3.0),
    (2.0, 6.0),
    (5.0, 6.0),
]


class TestBoardSlapAlone(unittest.TestCase):
    """research.md §6's actual flagged false-positive risk (structure-
    borne, unlike chop) -- but by construction it never dips first, so the
    detector's CANDIDATE state is never even entered."""

    def test_zero_false_positives(self):
        for rate, peak in SLAP_CASES:
            for seed in SEEDS:
                times, accel = seastate.board_slap(rate, peak, DURATION_S, seed=seed)
                assert_zero_jumps(self, times, accel,
                                  f"board_slap(rate={rate}, peak={peak}, seed={seed})")


# ---------------------------------------------------- handling chatter alone

# amp (g). Calibrated safe up to 0.5 g (40 seeds clean at 0.50 g; false
# positives start appearing at 0.55 g -- see TestKnownFinding below for
# what happens past this range, and the final report for the full sweep).
# 0.5 g of broadband+low-frequency "carried around" jitter is already a
# lot (footsteps, bumping around in a bag) -- treated as this regime's
# practical ceiling.
CHATTER_CASES_SAFE: List[float] = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5]


class TestHandlingChatterAlone(unittest.TestCase):
    """Broadband + low-frequency "carried around" jitter, up to a
    generously-plausible 0.5 g, never by itself produces a detected jump."""

    def test_zero_false_positives(self):
        for amp in CHATTER_CASES_SAFE:
            for seed in SEEDS:
                times, accel = seastate.handling_chatter(amp, DURATION_S, seed=seed)
                assert_zero_jumps(self, times, accel,
                                  f"handling_chatter(amp={amp}, seed={seed})")


# ------------------------------------------------------------- combinations

# (H, T, rate, peak, amp). Realistic-to-aggressive chop + slap + chatter
# together. Deliberately excludes the extreme chop corner (H>=0.6 at
# T<=2.0) combined with chatter>=0.3 -- that specific combination is the
# known finding, tested on its own in TestKnownFinding below, not asserted
# clean here.
COMBO_CASES: List[Tuple[float, float, float, float, float]] = [
    (0.1, 2.0, 0.1, 4.0, 0.1),
    (0.3, 3.0, 0.5, 5.0, 0.2),
    (0.5, 3.0, 1.0, 6.0, 0.3),
    (0.3, 3.0, 2.0, 6.0, 0.4),
    (0.5, 3.0, 2.0, 6.0, 0.4),
]


class TestCombinedNoise(unittest.TestCase):
    """All three regimes superposed at once, realistic-to-aggressive
    parameters, must still produce zero detected jumps."""

    def test_zero_false_positives(self):
        for H, T, rate, peak, amp in COMBO_CASES:
            for seed in SEEDS:
                chop = seastate.orbital_chop(H, T, DURATION_S, seed=seed)
                slap = seastate.board_slap(rate, peak, DURATION_S, seed=seed + 1000)
                chatter = seastate.handling_chatter(amp, DURATION_S, seed=seed + 2000)
                times, accel = seastate.superpose(chop, slap, chatter)
                assert_zero_jumps(
                    self, times, accel,
                    f"combo(H={H},T={T},rate={rate},peak={peak},amp={amp},seed={seed})")


# ------------------------------------------------ real jump on noisy background

class TestRealJumpOnNoisyBackground(unittest.TestCase):
    """A real synthetic jump (sim/generate.py's own synthesis -- imported,
    not copied) superposed on realistic chop + board slap is STILL
    detected, with airtime within tolerance of ground truth.

    Board-slap arrivals are excluded from a small guard band around each
    known flight: a board cannot be slapping through chop AT THE SAME
    INSTANT it is airborne (mutually exclusive physical states), and the
    detector's single-threshold landing rule means a stray slap landing
    mid-flight would (correctly, per that rule) be read as the landing --
    a real, separate collision hazard this test isn't about (see
    seastate.board_slap's docstring).
    """

    TOLERANCE_S = 0.03  # a few sample periods at 200 Hz; see report for measured deviation

    def test_detected_within_tolerance(self):
        for jump_seed, noise_seed in ((0, 10), (1, 11), (2, 12)):
            times, base = synth_session(
                DEMO_JUMPS, duration_s=DURATION_S, seed=jump_seed, chop_g=0.0)
            guard = [(t0 - 0.05, t0 + at + 0.05) for t0, at in DEMO_JUMPS]
            chop = seastate.orbital_chop(0.3, 3.0, DURATION_S, seed=noise_seed)
            slap = seastate.board_slap(0.3, 5.0, DURATION_S, seed=noise_seed + 500,
                                       exclude_windows=guard)
            _, noisy = seastate.superpose((times, base), chop, slap)

            det = Detector(load_params())
            events = run_stream(det, times, noisy)

            self.assertEqual(
                len(events), len(DEMO_JUMPS),
                f"jump_seed={jump_seed} noise_seed={noise_seed}: expected "
                f"{len(DEMO_JUMPS)} jumps, got {len(events)}")
            for (t0, at), ev in zip(DEMO_JUMPS, events):
                self.assertAlmostEqual(
                    ev.airtime_raw_s, at, delta=self.TOLERANCE_S,
                    msg=f"jump at t0={t0}: true airtime {at}s, detected "
                        f"{ev.airtime_raw_s:.3f}s (jump_seed={jump_seed} "
                        f"noise_seed={noise_seed})")


# --------------------------------------------------- landing_settle release path

class TestLandingSettleReleasePath(unittest.TestCase):
    """Covers the landing_settle_s release path directly (sim/detector.py's
    own comment: "a real desk session stored a '57 m' jump exactly this
    way" when this path was missing). Hand-built signal: a confirmed
    free-fall dip that is NEVER followed by a landing spike -- just
    ordinary chop-baseline readings for longer than landing_settle_s. The
    detector must release back to RIDING with last_reject == "no_landing"
    (not stay stuck in AIRBORNE), and a real jump immediately afterward
    must still be detected correctly by the SAME detector instance.
    """

    def test_soft_landing_releases_and_detector_is_not_stuck(self):
        p = load_params()
        fs_hz = 200.0
        dt = 1.0 / fs_hz
        det = Detector(p)

        t = 0.0
        # Phase 0: ordinary chop-baseline riding for a couple of seconds.
        _, baseline_a = seastate.orbital_chop(0.2, 2.0, 2.0, fs_hz=fs_hz, seed=1)
        for a in baseline_a:
            self.assertIsNone(det.update(t, a))
            t += dt
        self.assertEqual(det.state, RIDING)

        # Phase 1: a confirmed free-fall dip -- long enough to CONFIRM
        # (freefall_confirm_s + margin) but with no landing spike to come.
        dip_s = p.freefall_confirm_s + 0.15
        n_dip = int(dip_s * fs_hz)
        for _ in range(n_dip):
            self.assertIsNone(det.update(t, 0.05))
            t += dt
        self.assertIn(det.state, (CANDIDATE, AIRBORNE))
        self.assertEqual(det.state, AIRBORNE, "dip should have confirmed a launch")

        # Phase 2: back to ordinary chop-baseline readings -- NO spike
        # above landing_threshold_g ever occurs -- for comfortably longer
        # than landing_settle_s.
        _, settle_a = seastate.orbital_chop(
            0.2, 2.0, p.landing_settle_s + 0.6, fs_hz=fs_hz, seed=2)
        released = False
        for a in settle_a:
            self.assertLess(a, p.landing_threshold_g)  # sanity: chop never spikes
            ev = det.update(t, a)
            self.assertIsNone(ev, "a soft landing must never emit a JumpEvent")
            t += dt
            if det.state != AIRBORNE:
                released = True
                self.assertEqual(det.last_reject, "no_landing")
                self.assertGreater(det.last_reject_airtime, 0.0)
                break

        self.assertTrue(released, "detector never released -- STUCK AIRBORNE")
        self.assertNotEqual(det.state, AIRBORNE)

        # Phase 3: prove it's not just released but genuinely healthy --
        # continue feeding the SAME detector a real jump (sim/generate.py's
        # own synthesis) right after; it must be detected normally.
        _, jump_accel = synth_session(
            [(1.0, 0.8)], duration_s=6.0, seed=3, chop_g=0.02, fs_hz=fs_hz)
        events: List[JumpEvent] = []
        for a in jump_accel:
            ev = det.update(t, a)
            if ev is not None:
                events.append(ev)
            t += dt
        self.assertEqual(len(events), 1, "detector failed to detect a normal jump "
                         "immediately after a soft-landing release -- possible stuck state")
        self.assertAlmostEqual(events[0].airtime_raw_s, 0.8, delta=0.03)


# ------------------------------------------------------------- known finding

class TestKnownFinding_ExtremeChopPlusHandlingJitter(unittest.TestCase):
    """
    ####################################################################
    # REAL FINDING (2026-07-29 calibration), reported here, not hidden.
    #
    # At the outer edge of what "orbital chop" can even mean physically --
    # H=0.8 m at T=1.8 s, steepness H/L ~= 0.16, AT/BEYOND the ~1/7 deep-
    # water breaking limit (in reality this sea state is almost certainly
    # breaking/whitecapping, not smooth orbital motion) -- the chop's own
    # Airy-formula amplitude is ~0.50 g, uncomfortably close to the 0.35 g
    # free-fall gate on its own (though, per TestOrbitalChopAlone, it
    # STILL never false-positives ALONE: it can dip low but never spikes
    # above the 2.5 g landing gate by itself).
    #
    # Superposing even MODEST handling jitter (handling_chatter amp as low
    # as ~0.4 g -- well inside TestHandlingChatterAlone's own safe range
    # when chop is realistic) on top of this specific edge-of-plausible
    # chop DOES occasionally produce a real false-positive jump: the
    # independent chatter fluctuation happens, by chance, to push the
    # chop's already-shallow trough below 0.35 g for the 80 ms confirm
    # window, and a later chatter-tail sample happens to spike above 2.5 g
    # within the valid airtime window. Empirically, this exact chop+
    # chatter pairing (no slap) reproduces on 4/40 seeds below; a nearby
    # variant that also adds aggressive board_slap reproduced 7/40 during
    # calibration (see the final report for the full sweep).
    #
    # Calibration also ruled out board_slap as a contributor: the SAME
    # extreme chop + the most aggressive slap tested (rate=2/s, peak=6 g)
    # + ZERO chatter is perfectly clean (0/40 seeds) -- this is a
    # chop-times-chatter interaction specifically, not a slap effect. And
    # REALISTIC chop (research.md's own H=0.3 m / T=3 s example) combined
    # with the SAME chatter amplitude stays perfectly clean (see
    # test_realistic_chop_same_chatter_stays_clean below, and
    # TestCombinedNoise above) -- this is not a general chop+chatter
    # problem, it is specifically an edge-of-physical-plausibility-chop
    # problem, at chop amplitudes that (per the steepness check above)
    # arguably describe a breaking sea our simple Airy model shouldn't be
    # trusted to represent as smooth orbital motion in the first place.
    #
    # This is NOT tuned away: config/params.json and sim/detector.py are
    # both out of bounds for this task's file list, and even in-bounds,
    # "widen the gate" is exactly the wrong response to a corner this
    # narrow and this far outside realistic wind chop. It is reported
    # here as a permanent, named, PASSING test -- a regression alarm, not
    # a swept-under-the-rug bug: if a future change makes this corner
    # MORE dangerous, or a more moderate/realistic corner starts tripping
    # too, that is worth noticing immediately. If this test ever starts
    # failing because the false-positive rate dropped to zero here, that
    # is not obviously good news either -- re-read this comment and the
    # final report before touching anything.
    ####################################################################
    """

    EXTREME_H, EXTREME_T = 0.8, 1.8
    CHATTER_AMP = 0.4
    N_SEEDS = 40  # this exact chop+chatter pairing reproduces on 4/40 of these seeds

    def test_extreme_chop_plus_chatter_is_not_perfectly_robust(self):
        n_false_positive_seeds = 0
        detail = []
        for seed in range(self.N_SEEDS):
            chop = seastate.orbital_chop(self.EXTREME_H, self.EXTREME_T, DURATION_S, seed=seed)
            chatter = seastate.handling_chatter(self.CHATTER_AMP, DURATION_S, seed=seed + 2000)
            times, accel = seastate.superpose(chop, chatter)
            det = Detector(load_params())
            events = run_stream(det, times, accel)
            if events:
                n_false_positive_seeds += 1
                detail.append((seed, len(events), [round(e.height_m, 3) for e in events]))

        self.assertGreater(
            n_false_positive_seeds, 0,
            "expected this known-extreme corner to reproduce at least one false "
            "positive across 40 seeds (it did during calibration -- if this now "
            "reads 0, the finding may no longer reproduce; re-verify before "
            "treating that as good news, and update this comment either way)")

        # F-19 (audit 2026-08-22): the lower bound alone made this test green at
        # 40/40 false positives -- a detector that fired on every seed would have
        # read as "the known finding still reproduces". A characterization test
        # needs a ceiling or it only proves the corner is not PERFECT.
        #
        # Baseline: 4/40 (10%), measured 2026-08-23 on seeds 22, 23, 29, 31 --
        # the same 4/40 the class comment has recorded since calibration, so the
        # rate has been stable across every detector change since.
        #
        # The bound is 8, double the baseline, not 4. This is a deterministic
        # seeded test, so an exact-match assert would be tighter -- but it would
        # also fail on any legitimate params.json tuning that moves one seed,
        # and a gate that cries wolf on ordinary tuning gets loosened rather
        # than investigated. 8 tolerates drift while still catching a real
        # regression in chop robustness.
        self.assertLessEqual(
            n_false_positive_seeds, 8,
            f"chop false-positive rate rose to {n_false_positive_seeds}/40; the "
            f"measured baseline is 4/40 and this test allows up to 8/40. This is "
            f"a REGRESSION in detector robustness to steep chop, not a flaky "
            f"test -- the seeds are fixed. Find what changed before touching "
            f"this number, and if the new rate is genuinely acceptable, say why "
            f"in DECISIONS.md and move the baseline, not just the ceiling.")
        print(f"\n  [KNOWN FINDING] extreme chop(H={self.EXTREME_H},T={self.EXTREME_T}) "
              f"+ handling_chatter(amp={self.CHATTER_AMP}): {n_false_positive_seeds}/"
              f"{self.N_SEEDS} seeds produced a false-positive jump. Detail: {detail}")

    def test_realistic_chop_same_chatter_stays_clean(self):
        """Contrast case: same test file, same chatter amplitude, same
        seed range as the finding above -- research.md's own realistic
        chop example does NOT reproduce it, confirming this is
        specifically an edge-of-plausibility-chop problem, not a general
        chop+chatter one."""
        for seed in range(self.N_SEEDS):
            chop = seastate.orbital_chop(0.3, 3.0, DURATION_S, seed=seed)
            chatter = seastate.handling_chatter(self.CHATTER_AMP, DURATION_S, seed=seed + 2000)
            times, accel = seastate.superpose(chop, chatter)
            det = Detector(load_params())
            events = run_stream(det, times, accel)
            self.assertEqual(
                events, [],
                f"realistic chop + chatter(amp={self.CHATTER_AMP}) seed={seed} "
                f"produced {len(events)} unexpected jump(s)")


if __name__ == "__main__":
    unittest.main()
