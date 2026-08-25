"""Tests for sim/selfdiag.py — the non-ballistic self-diagnostic.

Audit F-26 (2026-08-24): the mutation campaign broke all 11 mutable
constants and comparisons in `sim/selfdiag.py` and **every one survived** a
full 223-test run. Nothing imported the module. Its only exercise was inside
`sim/experiments/*.py`, which are hand-run analysis scripts, not gates — so
a regression here was invisible until someone happened to re-run an
experiment.

That matters because this module is the only automated warning the water day
has for DECISION #30's silent-failure class: a jump whose height is inflated
because sustained wing lift kept the board from ever reaching true free fall.
The module's own header claims the flag is "CONSERVATIVE: it can't
under-warn about a height-biasing jump." Nothing verified that claim.

These tests pin the properties, not the numbers — a threshold may be retuned
against real water data, and a test that merely restates today's constant
would block that for no safety gain. What must not change silently is the
DIRECTION of each relationship and the conservatism guarantee.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

from selfdiag import (  # noqa: E402
    air_window_features, estimate_overshoot, flag_nonballistic)


def window(level_g: float, takeoff=0.0, landing=1.0, fs=200.0):
    """A synthetic flight window holding a constant |a| = level_g."""
    n = int((landing - takeoff) * fs) + 1
    times = [takeoff + i / fs for i in range(n)]
    return times, [level_g] * n


class TestAirWindowFeatures(unittest.TestCase):
    def test_trim_excludes_pop_and_landing_spike(self):
        """The trim exists so takeoff/landing transients cannot contaminate
        the steady-flight readout. Put huge spikes at both ends: the trimmed
        median must ignore them."""
        times, mag = window(0.05, 0.0, 1.0)
        for i in range(10):                    # pop
            mag[i] = 6.0
        for i in range(len(mag) - 10, len(mag)):   # landing
            mag[i] = 9.0
        f = air_window_features(times, mag, 0.0, 1.0, trim_s=0.15)
        self.assertAlmostEqual(f.median_g, 0.05, places=6)
        self.assertLess(f.mean_g, 0.2, "trim did not exclude the spikes")

    def test_wider_trim_takes_fewer_samples(self):
        times, mag = window(0.05, 0.0, 1.0)
        narrow = air_window_features(times, mag, 0.0, 1.0, trim_s=0.05)
        wide = air_window_features(times, mag, 0.0, 1.0, trim_s=0.30)
        self.assertLess(wide.n, narrow.n)

    def test_empty_window_is_reported_not_faked(self):
        """A window with no samples must return n=0, not a plausible-looking
        zero-g reading. A measurement that did not happen is not a value."""
        times, mag = window(0.05, 0.0, 0.10)
        f = air_window_features(times, mag, 0.0, 0.10, trim_s=0.15)
        self.assertEqual(f.n, 0)

    def test_frac_above_eps_tracks_the_level(self):
        times, quiet = window(0.02, 0.0, 1.0)
        _, loud = window(0.50, 0.0, 1.0)
        self.assertEqual(
            air_window_features(times, quiet, 0.0, 1.0, eps=0.08)
            .frac_above_eps, 0.0)
        self.assertEqual(
            air_window_features(times, loud, 0.0, 1.0, eps=0.08)
            .frac_above_eps, 1.0)


class TestFlagIsConservative(unittest.TestCase):
    """The header's load-bearing claim: the flag CANNOT under-warn."""

    def test_true_freefall_does_not_flag(self):
        times, mag = window(0.01, 0.0, 1.0)
        f = air_window_features(times, mag, 0.0, 1.0)
        self.assertFalse(flag_nonballistic(f))

    def test_sustained_lift_flags(self):
        """0.30 g mid-flight is the E11 band — detection still succeeds, so
        the rider gets a height, and it is inflated. This is exactly the case
        the flag exists for."""
        times, mag = window(0.30, 0.0, 1.0)
        f = air_window_features(times, mag, 0.0, 1.0)
        self.assertTrue(flag_nonballistic(f))

    def test_flag_is_monotonic_in_lift(self):
        """More lift must never flag LESS. A non-monotonic flag could stay
        quiet on the worst jumps, which is under-warning by definition."""
        times = window(0.0, 0.0, 1.0)[0]
        flagged = [flag_nonballistic(
            air_window_features(times, [g] * len(times), 0.0, 1.0))
            for g in (0.0, 0.05, 0.10, 0.15, 0.30, 0.60, 1.00)]
        for earlier, later in zip(flagged, flagged[1:]):
            self.assertFalse(earlier and not later,
                             f"flag went quiet as lift rose: {flagged}")

    def test_default_threshold_is_pinned_at_its_boundary(self):
        """Straddle the DEFAULT threshold. The first version of this file
        tested at 0.30 and 0.01 — far outside the boundary — so mutating
        0.12 to 0.15 flipped no verdict and survived. A test that never
        approaches the number it guards is not guarding it (F-26's own
        lesson, applied to F-26's fix)."""
        times = window(0.0, 0.0, 1.0)[0]

        def flags(level):
            return flag_nonballistic(
                air_window_features(times, [level] * len(times), 0.0, 1.0))
        self.assertFalse(flags(0.119), "just under default must not flag")
        self.assertTrue(flags(0.121), "just over default must flag")

    def test_default_eps_is_pinned_at_its_boundary(self):
        times = window(0.0, 0.0, 1.0)[0]

        def frac(level):
            return air_window_features(
                times, [level] * len(times), 0.0, 1.0).frac_above_eps
        self.assertEqual(frac(0.079), 0.0)
        self.assertEqual(frac(0.081), 1.0)

    def test_default_trim_is_pinned(self):
        """The default trim_s must actually be the documented 0.15 s: a
        1.0 s window at 200 Hz leaves [0.15, 0.85], i.e. 141 samples."""
        times, mag = window(0.05, 0.0, 1.0)
        self.assertEqual(air_window_features(times, mag, 0.0, 1.0).n, 141)

    def test_median_not_mean_is_used_for_the_flag(self):
        """The docstring promises a spike-robust statistic. Build a window
        whose MEAN clears the threshold but whose MEDIAN does not: if the
        flag ever switched to the mean, this fires."""
        times = window(0.0, 0.0, 1.0)[0]
        n = len(times)
        mag = [0.02] * n
        for i in range(n // 2, n // 2 + max(1, n // 20)):
            mag[i] = 5.0                      # a short, violent spike
        f = air_window_features(times, mag, 0.0, 1.0)
        self.assertGreater(f.mean_g, 0.12, "test setup: mean must clear it")
        self.assertLess(f.median_g, 0.12)
        self.assertFalse(flag_nonballistic(f),
                         "flag used a spike-sensitive statistic")

    def test_comparisons_are_strict_at_exact_equality(self):
        """`>` not `>=`, pinned where it is actually decidable. "Exceeds the
        threshold" means strictly exceeds: a jump sitting exactly on the
        threshold is not yet flagged, and a sample exactly at eps is not
        "above" it. Only an exact-equality case can tell these apart."""
        times = window(0.0, 0.0, 1.0)[0]
        n = len(times)
        at_thresh = air_window_features(times, [0.12] * n, 0.0, 1.0)
        self.assertFalse(flag_nonballistic(at_thresh, threshold_g=0.12))
        at_eps = air_window_features(times, [0.08] * n, 0.0, 1.0, eps=0.08)
        self.assertEqual(at_eps.frac_above_eps, 0.0)

    def test_median_is_the_true_middle_on_a_varying_window(self):
        """A constant-valued window cannot detect an index error — every
        element is the same, so any index returns the right answer. That is
        why the first version of this file left the median arithmetic
        unguarded. Use a ramp, where a wrong index gives a wrong value, and
        cover BOTH the odd branch and the even branch (0.5 * the middle
        pair), which a single window length can never do."""
        # odd count -> s[n // 2]
        odd_t = [i / 200.0 for i in range(201)]
        odd_v = [i / 100.0 for i in range(201)]          # 0.00 .. 2.00
        f_odd = air_window_features(odd_t, odd_v, odd_t[0], odd_t[-1],
                                    trim_s=0.0)
        self.assertEqual(f_odd.n, 201)
        self.assertAlmostEqual(f_odd.median_g, 1.00, places=6)

        # even count -> 0.5 * (s[n // 2 - 1] + s[n // 2])
        even_t = [i / 200.0 for i in range(200)]
        even_v = [i / 100.0 for i in range(200)]         # 0.00 .. 1.99
        f_even = air_window_features(even_t, even_v, even_t[0], even_t[-1],
                                     trim_s=0.0)
        self.assertEqual(f_even.n, 200)
        self.assertAlmostEqual(f_even.median_g, 0.995, places=6)

    def test_threshold_sits_below_the_detector_gate(self):
        """The docstring's design constraint: the flag must fire in the band
        where height bias is real but detection still WORKS. If the flag
        threshold ever rose above the detector's free-fall gate, every jump
        it warned about would already have been missed entirely, and the
        warning would be dead code."""
        from detector import load_params
        gate = load_params().freefall_enter_g
        times, mag = window(gate - 0.01, 0.0, 1.0)
        f = air_window_features(times, mag, 0.0, 1.0)
        self.assertTrue(
            flag_nonballistic(f),
            "a jump just inside the detector gate must still be flagged")


class TestOvershootEstimate(unittest.TestCase):
    def test_zero_lift_implies_no_overshoot(self):
        times, mag = window(0.0, 0.0, 1.0)
        self.assertAlmostEqual(
            estimate_overshoot(air_window_features(times, mag, 0.0, 1.0)),
            1.0, places=3)

    def test_overshoot_grows_with_lift(self):
        times = window(0.0, 0.0, 1.0)[0]
        vals = [estimate_overshoot(
            air_window_features(times, [g] * len(times), 0.0, 1.0))
            for g in (0.0, 0.1, 0.2, 0.3)]
        for a, b in zip(vals, vals[1:]):
            self.assertGreater(b, a, f"overshoot not monotonic: {vals}")

    def test_estimate_is_an_upper_bound_not_a_correction(self):
        """estimate_overshoot treats all measured |a| as vertical, which is
        the conservative reading — it must never return LESS than 1.0, i.e.
        it can never claim a jump was shorter than measured."""
        times = window(0.0, 0.0, 1.0)[0]
        for g in (0.0, 0.05, 0.2, 0.5, 1.0):
            v = estimate_overshoot(
                air_window_features(times, [g] * len(times), 0.0, 1.0))
            self.assertGreaterEqual(v, 1.0)


if __name__ == "__main__":
    unittest.main()
