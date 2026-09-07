#!/usr/bin/env python3
"""The rev/s -> rad/s conversion in the rendered spin confound.

WHY THIS EXISTS
---------------
`sim/sensor_model.py:104` is `const_omega = 2.0 * math.pi * cfg.spin_rps`.
On 2026-09-06 the mutation campaign changed that 2.0 to 2.5 and every test
passed. It survives because the confound is OFF by default (spin_rps = 0.0),
so nothing in the suite renders a known spin and checks the magnitude it
produces.

That conversion is load-bearing for the repo's most consequential open
decision. E3 measured that median airborne |a| collapses from AUC 1.000 to
0.258 under a spin confound, and E4 measured that a 0.5 rev/s spin on a 0.3 m
lever false-positives 84 % of ballistic jumps — the pair of results that say
F-28's median-|a| gate must not ship. Both numbers come out of this renderer.
With 2.5*pi instead of 2*pi, omega is 1.25x too large and the centripetal
term omega^2*r is 1.5625x too large, so the whole E3/E4 evidence base would
be computed against a confound half again as strong as the physics.

`sim/experiments/e4_rotation.py:40` already carries the analytic reference:

    def analytic_rot_g(spin_rps, lever_m):
        omega = 2.0 * math.pi * spin_rps
        return (omega * omega * lever_m) / wm.G

It lives in an experiment script that CI never runs. This file asserts the
renderer against that same closed form, so the conversion is pinned by the
suite rather than by a script someone has to remember to run.

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

import sensor_model as sm  # noqa: E402
import wing_model as wm  # noqa: E402

TAKEOFF_S = 2.0
APEX_M = 1.0


def analytic_rot_g(spin_rps: float, lever_m: float) -> float:
    """Closed form, written out here rather than imported.

    Mirrors sim/experiments/e4_rotation.py:40 deliberately: importing the
    renderer's own arithmetic would make this test agree with whatever the
    renderer does, which is the failure mode this file exists to catch.
    """
    omega = 2.0 * math.pi * spin_rps
    return (omega * omega * lever_m) / wm.G


def ballistic_flight():
    """A clean ballistic jump to ~1 m apex, no wing lift."""
    vz0 = wm.vz0_for_ballistic_apex(APEX_M)
    return wm.integrate_flight(vz0, wm.const_lift(0.0), dt=1e-4)


def quiet_cfg(**over) -> sm.SensorConfig:
    """Chop, swell and noise off, so the airborne magnitude IS the confound."""
    base = dict(chop_g=0.0, swell_g=0.0, noise_g=0.0)
    base.update(over)
    return sm.SensorConfig(**base)


def airborne_median(times, mag, land_time) -> float:
    """Median |a| strictly inside the flight, clear of the pop and the spike."""
    lo, hi = TAKEOFF_S + 0.05, land_time - 0.05
    inside = [a for t, a in zip(times, mag) if lo <= t <= hi]
    assert inside, "no airborne samples in the window"
    return statistics.median(inside)


class RenderedSpinMatchesTheClosedForm(unittest.TestCase):
    CASES = [(0.25, 0.1), (0.5, 0.3), (1.0, 0.3), (0.5, 0.8), (2.0, 0.1)]

    def test_each_spin_and_lever_matches(self) -> None:
        """Expected value is hypot(residual specific force, omega^2*r).

        The centripetal term enters IN QUADRATURE with whatever specific
        force the flight already carries, and a ballistic flight's is small
        but not zero (air drag; docs/algorithm.md:182 puts the sim p99 at
        0.067 g). Comparing against omega^2*r alone is off by ~0.001 g at
        0.5 rev/s — which is the difference between a correct expectation and
        a loosened tolerance. Measured here from the same flight with the
        confound off, so the assertion stays tight enough to catch the 0.15 g
        mutation this file guards.
        """
        flight = ballistic_flight()
        t0, m0, land0 = sm.render_session(
            flight, cfg=quiet_cfg(), takeoff_time_s=TAKEOFF_S, seed=0)
        residual = airborne_median(t0, m0, land0)
        for spin_rps, lever_m in self.CASES:
            with self.subTest(spin_rps=spin_rps, lever_m=lever_m):
                cfg = quiet_cfg(spin_rps=spin_rps, lever_m=lever_m)
                times, mag, land = sm.render_session(
                    flight, cfg=cfg, takeoff_time_s=TAKEOFF_S, seed=0)
                got = airborne_median(times, mag, land)
                want = math.hypot(residual, analytic_rot_g(spin_rps, lever_m))
                self.assertAlmostEqual(
                    got, want, places=3,
                    msg=f"at {spin_rps} rev/s on a {lever_m} m lever the "
                        f"airborne median should be "f"hypot(residual, omega^2*r/G) = {want:.4f} g; "
                        f"rendered {got:.4f} g. A wrong rev/s -> rad/s "
                        "conversion scales this by the square of the error, "
                        "and E3/E4 -- the evidence that F-28's gate must not "
                        "ship -- are computed through this renderer.")

    def test_a_mis_scaled_conversion_would_be_visible(self) -> None:
        """States the margin, so nobody widens the tolerance to make it pass.

        The 2.0 -> 2.5 mutation inflates omega^2*r by (2.5/2)^2 = 1.5625.
        """
        want = analytic_rot_g(0.5, 0.3)
        inflated = want * (2.5 / 2.0) ** 2
        self.assertGreater(
            abs(inflated - want), 0.1,
            "the mutation this file guards against moves the value by more "
            f"than 0.1 g ({want:.4f} -> {inflated:.4f}), so a places=3 "
            "assertion catches it with room to spare. If you are here to "
            "loosen the tolerance, that is the wrong fix.")


class ConfoundOffMeansBallistic(unittest.TestCase):
    def test_zero_spin_renders_near_free_fall(self) -> None:
        flight = ballistic_flight()
        times, mag, land = sm.render_session(
            flight, cfg=quiet_cfg(), takeoff_time_s=TAKEOFF_S, seed=0)
        got = airborne_median(times, mag, land)
        self.assertLess(
            got, 0.07,
            f"with the confound off, airborne |a| must sit near zero; got "
            f"{got:.4f} g. docs/algorithm.md:182 puts the sim's p99 "
            "mid-flight force at 0.067 g, and E3's AUC 1.000 flag depends on "
            "that being true.")

    def test_zero_lever_cancels_any_spin(self) -> None:
        """r = 0 is the identity, however fast the board is turning."""
        flight = ballistic_flight()
        times, mag, land = sm.render_session(
            flight, cfg=quiet_cfg(spin_rps=3.0, lever_m=0.0),
            takeoff_time_s=TAKEOFF_S, seed=0)
        got = airborne_median(times, mag, land)
        self.assertLess(
            got, 0.07,
            "a sensor ON the rotation axis feels no centripetal term no "
            f"matter the rate; got {got:.4f} g at 3 rev/s and r=0.")


if __name__ == "__main__":
    unittest.main()
