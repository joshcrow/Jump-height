#!/usr/bin/env python3
"""The two headline physics claims, as assertions instead of prose.

WHY THIS EXISTS
---------------
`sim/experiments/RESULTS.md` states the result the whole product rests on:

    "A wing rider holds the wing with their ARMS, and the arm-force ceiling
     caps sustained lift far below [a kite's]. Realistic depowering
     techniques stay at 1.00-1.09x overshoot ... The kite exception does NOT
     transfer to wings."

and names where it comes from: "Everything rests on the arm-ceiling and aero
assumptions. The entire 'wings are ballistic' result flows from the premise
that arm force caps sustained a_v well below a kite's 0.567 g. That premise
is modeled (`aero_model`, `arm_ceiling_bw`)."

That lives in a results file, produced by experiment scripts CI never runs.
The 2026-09-06/07 mutation campaign then reached `sim/wing_model.py` and its
`WingParams` defaults began surviving one after another — `mass_kg`
85 -> 106, `wing_area_m2` 5 -> 6.25, and `RHO_AIR` before them (pinned in
test_physical_constants.py). Nothing in the suite read the numbers the
headline claim is computed from.

So this file asserts the CLAIMS, behaviourally, rather than snapshotting the
constants that feed them. A defaults snapshot fails when someone edits a
number; this fails when someone changes the physics. Both are worth having,
and this is the one that would survive a legitimate re-tuning of the
parameters while still catching a broken model.

Anchors, quoted from RESULTS.md and pinned below:
  * E2, the reference anchor used throughout: arm-capped `aero_model` at
    ceiling 0.40 bw gives overshoot 1.00-1.07x.
  * E6, the kite validation gate: the pipeline reproduces Simons 2025 at
    2.3091x overshoot from a_v = 0.567 g.
  * The closed form: overshoot = 1 / (1 - a_v), matching the integrator to
    better than 0.1 %.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sim"))

import wing_model as wm  # noqa: E402

# Quoted from sim/experiments/RESULTS.md. Literals on purpose.
E2_OVERSHOOT_BAND = (1.00, 1.07)      # arm-capped aero_model at ceiling 0.40 bw
E2_ARM_CEILING_BW = 0.40              # "reference anchor used throughout"
E1_REALISTIC_CEILING = 1.095          # realistic depowering, excluding never-depower
KITE_A_V_G = 0.567                    # Simons 2025, delivered through a harness
KITE_OVERSHOOT = 2.3091               # E6 reproduction
APEX_M = 2.0


def ballistic_overshoot(flight) -> float:
    """h_reported / h_true, where h_reported is the airtime formula's answer.

    Mirrors sim/experiments/e2_montecarlo.py's definition: the detector's
    g*T^2/8 against the integrator's true apex.
    """
    t = flight.true_airtime_s
    reported = wm.G * t * t / 8.0
    return reported / flight.true_apex_m


class WingsAreBallistic(unittest.TestCase):
    """E2's anchor: the claim the product's accuracy argument rests on."""

    def test_pure_ballistic_overshoot_is_unity(self) -> None:
        """The control. Without lift there is nothing to overshoot."""
        f = wm.integrate_flight(wm.vz0_for_ballistic_apex(APEX_M),
                                wm.const_lift(0.0), dt=1e-4)
        got = ballistic_overshoot(f)
        self.assertAlmostEqual(
            got, 1.0, places=2,
            msg=f"a jump with no wing lift must read its true apex; got "
                f"{got:.4f}x. If this fails the integrator or the airtime "
                "formula is wrong, and every claim below is meaningless.")

    def test_default_wing_stays_inside_the_e2_band(self) -> None:
        lo, hi = E2_OVERSHOOT_BAND
        p = wm.WingParams()
        self.assertEqual(
            p.arm_ceiling_bw, E2_ARM_CEILING_BW,
            "WingParams.arm_ceiling_bw is no longer 0.40 bw. RESULTS.md calls "
            "that the reference anchor used throughout, and names it as the "
            "premise the entire 'wings are ballistic' result flows from.")
        f = wm.integrate_flight(wm.vz0_for_ballistic_apex(APEX_M),
                                wm.aero_model(p), dt=1e-4)
        got = ballistic_overshoot(f)
        self.assertTrue(
            lo <= got <= hi,
            f"the default wing model overshoots {got:.4f}x, outside E2's "
            f"stated {lo}-{hi}x anchor band. That band IS the product's "
            "accuracy claim against WOO and Surfr; if the physics moved, "
            "re-run E1/E2 and update RESULTS.md in the same commit.")

    def test_the_realistic_grid_corner_stays_under_e1s_ceiling(self) -> None:
        """E1: realistic depowering at ceiling 0.40 stays <= 1.095x.

        Punishing but not absurd: strong wind and a high coefficient, with
        the DEFAULT sheeted-out technique.
        """
        p = wm.WingParams(wind_mps=18.0, c_max=1.3, mass_kg=70.0)
        f = wm.integrate_flight(wm.vz0_for_ballistic_apex(APEX_M),
                                wm.aero_model(p), dt=1e-4)
        got = ballistic_overshoot(f)
        self.assertLessEqual(
            got, E1_REALISTIC_CEILING,
            f"at 18 m/s, c_max 1.3 and a 70 kg rider the model overshoots "
            f"{got:.4f}x, above E1's stated {E1_REALISTIC_CEILING}x for "
            "realistic depowering. E1 puts anything higher only in the "
            "never-depower corner, which this is not.")

    def test_the_default_technique_is_not_the_absurd_corner(self) -> None:
        self.assertNotEqual(
            wm.WingParams().technique, "constant",
            "the default technique must not be 'constant'. RESULTS.md puts "
            "the 1.169x worst case ONLY in that never-depower corner, and "
            "calls it a physically absurd operating point.")
        self.assertEqual(wm.WingParams().technique, "sheeted_out")


class ApexRoundTrip(unittest.TestCase):
    """vz0_for_ballistic_apex and the integrator must be inverses.

    Added after measurement: the claim tests above catch a broken PREMISE
    but not a broken SCALE, because overshoot is a RATIO. Removing the 2
    from `sqrt(2 g h)` halves every apex and every airtime and leaves the
    ratio at 1.00, so all of them passed. This asserts the absolute value.
    """

    @staticmethod
    def _dragless(apex_m: float):
        """No wing, no body drag, no horizontal speed: pure gravity.

        `integrate_flight` applies body drag by DEFAULT (body_cd_a = 0.5)
        even with no wing lift, and drag is what makes the naive round-trip
        fail. Measured cost: 0.6 % of apex at 0.5 m rising to 2.2 % at 4 m.
        So the inverse relationship only holds with drag switched off, and
        the drag case gets its own assertion below.
        """
        return wm.integrate_flight(wm.vz0_for_ballistic_apex(apex_m),
                                   wm.const_lift(0.0), dt=1e-5,
                                   body_cd_a=0.0, vx0=0.0)

    def test_requested_apex_is_the_apex_reached(self) -> None:
        for apex in (0.5, 1.0, 2.0, 4.0):
            with self.subTest(apex_m=apex):
                f = self._dragless(apex)
                self.assertAlmostEqual(
                    f.true_apex_m, apex, places=3,
                    msg=f"asked for a {apex} m pure-gravity apex and the "
                        f"integrator reached {f.true_apex_m:.4f} m. These two "
                        "functions are exact inverses with drag off; a scale "
                        "error here is invisible to every overshoot ratio.")

    def test_airtime_matches_the_ballistic_formula(self) -> None:
        """h = g T^2 / 8 is the product's headline formula. Assert it holds
        on the case where it is exactly true."""
        for apex in (0.5, 2.0, 4.0):
            with self.subTest(apex_m=apex):
                f = self._dragless(apex)
                want_t = (8.0 * apex / wm.G) ** 0.5
                self.assertAlmostEqual(
                    f.true_airtime_s, want_t, places=3,
                    msg=f"a {apex} m pure-gravity jump must hang for "
                        f"{want_t:.4f} s (T = sqrt(8h/g)); integrator gave "
                        f"{f.true_airtime_s:.4f} s. This formula IS the "
                        "product's height number, so it has to be exact on "
                        "the case where it is exactly true.")

    def test_body_drag_costs_apex_and_costs_more_when_faster(self) -> None:
        """Drag removes energy, and more of it at higher speed.

        Measured 2026-09-07: 0.6 % of apex at 0.5 m, 2.2 % at 4 m. Asserted
        as a monotone trend rather than as those two numbers, so a
        legitimate re-tuning of body_cd_a does not fail this — but drag
        pointing the wrong way, or vanishing, does.
        """
        losses = []
        for apex in (0.5, 1.0, 2.0, 4.0):
            vz0 = wm.vz0_for_ballistic_apex(apex)
            drag = wm.integrate_flight(vz0, wm.const_lift(0.0), dt=1e-5)
            losses.append((apex - drag.true_apex_m) / apex)
            self.assertLess(
                drag.true_apex_m, apex,
                f"body drag must LOWER the {apex} m apex, not raise it; got "
                f"{drag.true_apex_m:.4f} m.")
        self.assertEqual(
            losses, sorted(losses),
            f"the fractional apex loss to drag must grow with jump size, "
            f"since drag scales with speed; got {[round(x, 4) for x in losses]}.")


class WingParamsDefaults(unittest.TestCase):
    """The snapshot half. Measured necessity, not belt-and-braces.

    Of twelve mutations to this module, the claim tests above caught four.
    The eight they missed — mass, wing area, wind, decay constant, body
    drag, force elevation, RHO_AIR and the vz0 scale — all move apex and
    airtime TOGETHER, leaving the overshoot ratio inside E2's 7 %-wide band.
    A ratio cannot see a scale error. So both halves are needed, and saying
    so here is cheaper than rediscovering it.
    """

    SNAPSHOT = {
        "mass_kg": 85.0,            # rider + board + gear
        "wing_area_m2": 5.0,        # projected wing area
        "wind_mps": 10.0,           # true wind speed
        "c_max": 0.8,               # RESULTS.md calls 1.3 "above the 0.8 default"
        "decay_tau_s": 0.25,        # C(t) decay after takeoff
        "technique": "sheeted_out",  # the realistic case
        "force_elev_deg": 35.0,     # aero force elevation above horizontal
        "arm_ceiling_bw": 0.40,     # E2's reference anchor
        "body_cd_a": 0.5,           # rider drag Cd*A
        "harness": False,           # a wing loads arms, not a harness
    }

    def test_defaults_are_unchanged(self) -> None:
        p = wm.WingParams()
        drifted = {k: (getattr(p, k), v) for k, v in self.SNAPSHOT.items()
                   if getattr(p, k) != v}
        self.assertEqual(
            drifted, {},
            "WingParams defaults changed without this snapshot changing. "
            "E1 through E16 were all computed through these; moving one "
            "re-renders every apex, airtime and height those conclusions "
            "rest on. If deliberate, update the table above IN THE SAME "
            f"COMMIT and say what it invalidates. drifted: {drifted}")

    def test_every_field_is_in_the_snapshot(self) -> None:
        """So a NEW parameter cannot be added unpinned."""
        import dataclasses
        fields = {f.name for f in dataclasses.fields(wm.WingParams)}
        self.assertEqual(
            fields, set(self.SNAPSHOT),
            "WingParams gained or lost a field. Add it to the snapshot above "
            f"with a comment saying where its value comes from. missing: "
            f"{sorted(fields - set(self.SNAPSHOT))}; stale: "
            f"{sorted(set(self.SNAPSHOT) - fields)}")

    def test_a_wing_does_not_use_a_harness(self) -> None:
        """The premise, stated as a flag: arms, not a harness."""
        self.assertFalse(
            wm.WingParams().harness,
            "harness must default False. The entire 'wings are ballistic' "
            "result flows from arm force capping sustained lift; a harness "
            "raises that ceiling to kite territory, where the airtime "
            "formula overshoots 2.31x.")


class KiteExceptionDoesNotTransfer(unittest.TestCase):
    """E6's validation gate, and the closed form both rest on."""

    def test_closed_form_matches_the_integrator(self) -> None:
        """overshoot = 1/(1 - a_v), to better than 0.1 %."""
        for a_v in (0.10, 0.40, KITE_A_V_G, 0.70):
            with self.subTest(a_v_g=a_v):
                f = wm.integrate_flight(wm.vz0_for_ballistic_apex(APEX_M),
                                        wm.const_lift(a_v), dt=1e-5)
                got = ballistic_overshoot(f)
                want = wm.closed_form_overshoot(a_v)
                self.assertAlmostEqual(
                    got / want, 1.0, places=3,
                    msg=f"at a_v = {a_v} g the integrator gives {got:.4f}x "
                        f"and the closed form 1/(1-a_v) gives {want:.4f}x. "
                        "RESULTS.md states these agree to better than 0.1 %, "
                        "and that agreement is what licenses quoting the "
                        "closed form as ground truth.")

    def test_a_kite_overshoots_far_more_than_a_wing(self) -> None:
        """The whole point: the kite exception must NOT transfer."""
        kite = wm.closed_form_overshoot(KITE_A_V_G)
        self.assertAlmostEqual(
            kite, KITE_OVERSHOOT, places=3,
            msg=f"a kite's {KITE_A_V_G} g should overshoot "
                f"{KITE_OVERSHOOT}x (E6, reproducing Simons 2025); got "
                f"{kite:.4f}x.")
        self.assertGreater(
            kite, E2_OVERSHOOT_BAND[1] * 2,
            "a kite must overshoot more than twice a wing's worst anchor "
            "value. If these ever converge, the airtime method is no longer "
            "defensible for wings and the product's accuracy claim changes.")


if __name__ == "__main__":
    unittest.main()
