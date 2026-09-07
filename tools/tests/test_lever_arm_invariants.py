#!/usr/bin/env python3
"""The lever-arm module's three documented load-bearing choices, pinned.

WHY THIS EXISTS
---------------
`sim/lever_arm.py`'s own docstring names "the three load-bearing choices
(identical in both languages)": raw magnitude in, MEDIAN not mean, and the
result used UNSHAVED at SAFETY_FACTOR = 1.0. On 2026-09-06 the mutation
campaign reached that module, and a targeted re-run against its own two test
files (test_lever_arm.py + test_spin_correction.py) found that most of those
choices are documented and not tested:

    median index n//2 -> n//3    SURVIVED
    parity n%2 -> n%3            SURVIVED
    drop even-case average       SURVIVED
    median -> mean               SURVIVED     <- choice 2, by name
    median -> max                KILLED
    median -> min                KILLED
    MIN_DPS 150 -> 187.5         SURVIVED
    min samples 8 -> 9           SURVIVED
    min samples 8 -> 2           KILLED
    SLOTS 64 -> 65               KILLED
    mount bound 3.0 -> 3.75      SURVIVED
    SAFETY_FACTOR 1.0 -> 0.95    SURVIVED     <- choice 3, and see below

The last line is the one that matters most. The module docstring records that
a deliberate 5 % short-shave "broke 5 of 8 lever x spin cases; removing it
fixed all 8. There is no safe side: aim UNBIASED." So the exact regression
this module was fixed for could be reintroduced, one character at a time,
with every test still green. That is CLAUDE.md rule 3 in its purest form,
and it is the same shape as F-17 on this very file — where retired docstring
text survived and the test was still implementing the retired rule.

These tests are BEHAVIOURAL where they can be: they drive observe()/commit()
and assert on value(), so a shave applied anywhere in the path fails, not
just an edit to the named constant.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import math
import statistics
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sim"))

import lever_arm as la  # noqa: E402
from lever_arm import LeverArm  # noqa: E402

SPIN_DPS = 300.0  # comfortably above MIN_DPS, keeps accel well under the clip

# The gate values are written out as LITERALS on purpose. A test that derives
# its inputs from the constant it is pinning cannot detect a change to that
# constant — it moves with it and passes. That bit this very file on
# 2026-09-06: an earlier draft probed at `la.MIN_DPS + 1.0`, so raising
# MIN_DPS to 187.5 simply moved the probe and survived.
EXPECTED_MIN_DPS = 150.0
EXPECTED_MIN_SAMPLES = 8
EXPECTED_MOUNT_BOUND_M = 3.0


def accel_for(r_m: float, dps: float = SPIN_DPS) -> float:
    """The raw |a| a sample must carry for observe() to derive lever arm r_m.

    Inverse of observe()'s own r = (|a| * G) / omega^2.
    """
    w = dps * math.pi / 180.0
    return r_m * w * w / la.G


def feed(arm: LeverArm, r_values, dps: float = SPIN_DPS) -> None:
    for r in r_values:
        arm.observe(accel_for(r, dps), dps)


class MedianNotMean(unittest.TestCase):
    """Choice 2: median, because a landing spike drags a mean anywhere."""

    # Nine ASCENDING, distinct values with one high outlier. Chosen so the
    # true median (index 4) differs from the mean, from index 3, and from the
    # even-branch average of indices 3 and 4 — so a corrupted index, a
    # corrupted parity test, and a mean all produce a different number.
    NINE = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 2.00]

    def test_first_commit_returns_the_exact_median(self) -> None:
        arm = LeverArm()
        feed(arm, self.NINE)
        self.assertEqual(arm.pending_samples(), 9)
        self.assertTrue(arm.commit())
        want = statistics.median(self.NINE)          # 0.30
        self.assertAlmostEqual(
            arm.value(), want, places=9,
            msg=f"first commit must equal the median of the observations "
                f"({want}); got {arm.value()}. mean would be "
                f"{statistics.fmean(self.NINE):.4f}, s[n//3] would be "
                f"{sorted(self.NINE)[9 // 3]}, and the even-branch average "
                f"would be {0.5 * (sorted(self.NINE)[3] + sorted(self.NINE)[4])}. "
                "sim/lever_arm.py's docstring calls median-not-mean one of "
                "three load-bearing choices.")

    def test_the_outlier_does_not_move_the_estimate(self) -> None:
        """The property the median was chosen FOR, stated directly."""
        arm_a, arm_b = LeverArm(), LeverArm()
        feed(arm_a, self.NINE)
        # same set, but the outlier is an order of magnitude closer to a
        # landing spike's worth of apparent lever arm
        spiked = self.NINE[:-1] + [2.95]
        feed(arm_b, spiked)
        arm_a.commit()
        arm_b.commit()
        self.assertAlmostEqual(
            arm_a.value(), arm_b.value(), places=9,
            msg="growing the single largest observation changed the estimate, "
                "so the statistic is not robust to one outlier. A mean would "
                "move here; a median must not.")

    def test_even_length_averages_the_two_middles(self) -> None:
        ten = [0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.45, 0.50, 2.00]
        arm = LeverArm()
        feed(arm, ten)
        self.assertTrue(arm.commit())
        want = statistics.median(ten)               # 0.5 * (0.30 + 0.35)
        self.assertAlmostEqual(
            arm.value(), want, places=9,
            msg=f"even-length median must average the two middle values "
                f"({want}); got {arm.value()}. Taking s[n//2] alone gives "
                f"{sorted(ten)[5]}.")


class UnshavedResult(unittest.TestCase):
    """Choice 3: SAFETY_FACTOR = 1.0, aim UNBIASED.

    A deliberate 5 % short-shave broke 5 of 8 lever x spin cases and was
    removed (sim/lever_arm.py docstring note 3; jump_detector.h:96 retracted
    the same advice on 2026-08-10 as MEASURED WRONG).
    """

    def test_constant_is_exactly_one(self) -> None:
        self.assertEqual(
            la.SAFETY_FACTOR, 1.0,
            "SAFETY_FACTOR must stay 1.0. Erring SHORT is not the safe side: "
            "an under-estimate leaves a free-fall residual of "
            "rot_g*sqrt(1-k^2), and the sqrt amplifies small errors badly — "
            "k=0.99 leaves 14 % of rot_g, which at r=0.5 m and 600 dps is "
            "0.79 g against a 0.35 g gate. A 5 % shave broke 5 of 8 "
            "lever x spin cases; removing it fixed all 8.")

    def test_estimate_is_not_shaved_anywhere_in_the_path(self) -> None:
        """Behavioural twin of the above, so a shave applied elsewhere fails."""
        r = 0.50
        arm = LeverArm()
        feed(arm, [r] * 8)
        self.assertTrue(arm.commit())
        self.assertAlmostEqual(
            arm.value(), r, places=9,
            msg=f"eight identical observations of r={r} must commit to "
                f"exactly {r}; got {arm.value()}. Any factor applied on the "
                "way out is a bias, and this module's measured conclusion is "
                "that there is no safe side to bias toward.")


class AdmissionGates(unittest.TestCase):
    def test_min_dps_constant_matches_the_documented_value(self) -> None:
        self.assertEqual(
            la.MIN_DPS, EXPECTED_MIN_DPS,
            "MIN_DPS changed. It decides how many real flights can "
            "self-calibrate at all, and F-24 already records that self-arm "
            "cannot bootstrap at a small lever arm — raising it makes that "
            "worse. Update EXPECTED_MIN_DPS here and say why.")

    def test_below_the_gate_is_not_observed(self) -> None:
        dps = EXPECTED_MIN_DPS - 1.0        # literal, not la.MIN_DPS
        arm = LeverArm()
        arm.observe(accel_for(0.5, dps), dps)
        self.assertEqual(
            arm.pending_samples(), 0,
            f"a sample at {dps} dps must be dropped: below MIN_DPS the "
            "omega^2 denominator makes r meaningless.")

    def test_just_above_the_gate_is_observed(self) -> None:
        """Pins the gate from above, so RAISING MIN_DPS fails here."""
        dps = EXPECTED_MIN_DPS + 1.0        # literal, not la.MIN_DPS
        arm = LeverArm()
        arm.observe(accel_for(0.5, dps), dps)
        self.assertEqual(
            arm.pending_samples(), 1,
            f"a sample at {dps} dps must be accepted. If this fails, MIN_DPS "
            "was raised above it and fewer real flights can self-calibrate.")

    def test_absurd_lever_arm_is_rejected(self) -> None:
        arm = LeverArm()
        arm.observe(accel_for(EXPECTED_MOUNT_BOUND_M + 0.01), SPIN_DPS)
        self.assertEqual(
            arm.pending_samples(), 0,
            "an implied lever arm over 3 m is absurd for a board mount and "
            "must be dropped before it reaches the median.")

    def test_plausible_lever_arm_is_accepted(self) -> None:
        arm = LeverArm()
        arm.observe(accel_for(EXPECTED_MOUNT_BOUND_M - 0.01), SPIN_DPS)
        self.assertEqual(
            arm.pending_samples(), 1,
            "a lever arm just under the 3 m bound must still be accepted; "
            "raising that bound silently widens what counts as plausible.")

    def test_railed_sample_is_rejected(self) -> None:
        arm = LeverArm()
        arm.observe(la.CLIP_GUARD_G, SPIN_DPS)
        self.assertEqual(
            arm.pending_samples(), 0,
            "a railed accelerometer reading is a floor, not a measurement "
            "(F-16); it must never enter the estimate.")


class CommitThreshold(unittest.TestCase):
    def test_one_short_of_the_minimum_does_not_commit(self) -> None:
        arm = LeverArm()
        feed(arm, [0.5] * (EXPECTED_MIN_SAMPLES - 1))
        self.assertFalse(
            arm.commit(),
            "fewer than 8 observations must not produce an estimate.")
        self.assertFalse(arm.has_estimate())

    def test_exactly_the_minimum_does_commit(self) -> None:
        """Pins the threshold from above, so RAISING it also fails."""
        arm = LeverArm()
        feed(arm, [0.5] * EXPECTED_MIN_SAMPLES)
        self.assertTrue(
            arm.commit(),
            "exactly 8 observations must be enough to commit. If the minimum "
            "was raised, that changes how many flights can self-calibrate — "
            "F-24 already records that self-arm cannot bootstrap at a small "
            "lever arm.")
        self.assertTrue(arm.has_estimate())

    def test_a_failed_commit_clears_the_pending_samples(self) -> None:
        """Stale observations merging into the next flight's median is the
        2026-08-12 gyro-crash-hunt finding."""
        arm = LeverArm()
        feed(arm, [0.5] * (EXPECTED_MIN_SAMPLES - 1))
        arm.commit()
        self.assertEqual(
            arm.pending_samples(), 0,
            "a refused commit must still drop its samples, or up to 64 stale "
            "observations merge into the NEXT flight's median.")


if __name__ == "__main__":
    unittest.main()
