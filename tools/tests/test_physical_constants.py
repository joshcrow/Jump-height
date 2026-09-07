#!/usr/bin/env python3
"""Gravity and air density, pinned everywhere they are written out.

WHY THIS EXISTS
---------------
`sim/wing_model.py:48`'s `RHO_AIR = 1.225` came out of the 2026-09-06/07
mutation campaign as a survivor (1.225 -> 1.53125, all tests green). It is
sea-level air density, and it multiplies both the wing force
(`0.5 * RHO_AIR * v_app^2 * A * C`) and the body drag — so every apex, every
airtime and therefore every height in E1 through E16 scales with it.

Chasing that survivor turned up the bigger fact. **Gravity is written out in
SEVEN places, and only one of them is generated:**

  1. `config/params.json`            detector.g            <- source of truth
  2. `firmware/include/params.gen.h` `#define JH_G`        <- generated from it
  3. `sim/detector.py`               `Params.g`            (pinned in test_params_parity.py)
  4. `sim/wing_model.py:47`          `G` — comment: "matches detector.Params.g"
  5. `sim/seastate.py:48`            `G` — comment: "matches sim/detector.py's Params.g default"
  6. `sim/lever_arm.py:38`           `G` — no comment at all
  7. `firmware/include/lever_arm.h`  `static constexpr float kG`

Two of those comments make a claim that nothing checked. And number 7 is a
hand-written C++ duplicate sitting in the same include directory as the
GENERATED `JH_G` — the generator already owns this constant and that header
does not use it. That is a real cleanup, not just a test gap: swapping `kG`
for `JH_G` regenerates nothing but does touch firmware, so it is batched per
CLAUDE.md rule 4 rather than done here. This file is the guard until then.

Nothing in the repo would have caught gravity drifting in one place and not
the others, which is the general form CLAUDE.md section 4 states: an
identifier without a lookup entry is a rediscovery waiting to happen.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sim"))

import detector as det  # noqa: E402
import lever_arm as la  # noqa: E402
import seastate as ss  # noqa: E402
import wing_model as wm  # noqa: E402

PARAMS_JSON = REPO / "config" / "params.json"
PARAMS_GEN_H = REPO / "firmware" / "include" / "params.gen.h"
LEVER_ARM_H = REPO / "firmware" / "include" / "lever_arm.h"

# Standard gravity, and sea-level air density at 15 C. Literals on purpose —
# a test that reads the constant it pins moves with it.
STANDARD_GRAVITY = 9.80665
SEA_LEVEL_RHO_AIR = 1.225


def _json_g() -> float:
    return float(json.loads(PARAMS_JSON.read_text())["detector"]["g"])


class GravityIsOneNumber(unittest.TestCase):
    """All seven copies, checked against config/params.json."""

    def test_the_source_of_truth_is_standard_gravity(self) -> None:
        self.assertEqual(
            _json_g(), STANDARD_GRAVITY,
            "config/params.json's detector.g is no longer standard gravity. "
            "Everything below is checked against it, so this assertion comes "
            "first: if the source of truth moved, say why here.")

    def test_python_detector_agrees(self) -> None:
        self.assertEqual(det.Params().g, _json_g())

    def test_wing_model_agrees(self) -> None:
        self.assertEqual(
            wm.G, _json_g(),
            "sim/wing_model.py:47's comment claims it 'matches "
            "detector.Params.g'. It no longer does. Every E-series apex is "
            "integrated with this value.")

    def test_seastate_agrees(self) -> None:
        self.assertEqual(
            ss.G, _json_g(),
            "sim/seastate.py:48's comment claims it matches detector's "
            "Params.g default. It no longer does — and E14/E15's venue error "
            "bars come out of this module.")

    def test_lever_arm_agrees(self) -> None:
        self.assertEqual(
            la.G, _json_g(),
            "sim/lever_arm.py:38 carries gravity with no comment saying "
            "where it came from. It still has to agree: r = |a|*G/omega^2, so "
            "a wrong G scales every self-calibrated lever arm.")

    def test_generated_firmware_header_agrees(self) -> None:
        """Also proves the generator has actually been run."""
        m = re.search(r"#define\s+JH_G\s+([0-9.]+)f?", PARAMS_GEN_H.read_text())
        self.assertIsNotNone(m, f"no JH_G define found in {PARAMS_GEN_H}")
        self.assertEqual(
            float(m.group(1)), _json_g(),
            "firmware/include/params.gen.h's JH_G disagrees with "
            "config/params.json. That header is GENERATED, so this means "
            "`./tools/jump gen` has not been run since the JSON changed and "
            "the firmware would be built with a stale constant.")

    def test_hand_written_firmware_duplicate_agrees(self) -> None:
        """The one copy no generator owns.

        `lever_arm.h` defines its own kG rather than using the generated
        JH_G from the same directory. Until that is folded into a flash
        batch, this is what keeps it honest.
        """
        src = LEVER_ARM_H.read_text()
        m = re.search(r"constexpr\s+float\s+kG\s*=\s*([0-9.]+)f?", src)
        self.assertIsNotNone(
            m,
            f"no kG constant found in {LEVER_ARM_H}. If it now uses JH_G, "
            "delete this test and say so — the generator would then own it.")
        self.assertEqual(
            float(m.group(1)), _json_g(),
            "firmware/include/lever_arm.h's hand-written kG disagrees with "
            "config/params.json. It is the C++ twin of sim/lever_arm.py's G, "
            "and the two are declared 1:1 mirrors.")


class AirDensity(unittest.TestCase):
    def test_rho_air_is_sea_level(self) -> None:
        self.assertEqual(
            wm.RHO_AIR, SEA_LEVEL_RHO_AIR,
            "sim/wing_model.py's RHO_AIR is no longer 1.225 kg/m^3. It scales "
            "the wing force and the body drag linearly, so every apex, "
            "airtime and height in E1-E16 moves with it. The mutation "
            "campaign changed it by 25 % with the whole suite green.")

    def test_rho_air_is_actually_used_in_both_force_terms(self) -> None:
        """Pinning the value is pointless if a term stops reading it."""
        src = (REPO / "sim" / "wing_model.py").read_text()
        uses = len(re.findall(r"RHO_AIR", src))
        self.assertGreaterEqual(
            uses, 4,
            "RHO_AIR should appear in its definition, the wing force, and "
            f"both drag terms — found {uses} references. If a force model "
            "stopped using it, a literal was probably inlined, and every "
            "assertion above became decorative.")


if __name__ == "__main__":
    unittest.main()
