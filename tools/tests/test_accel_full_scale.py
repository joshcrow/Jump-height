#!/usr/bin/env python3
"""The +-16 g accelerometer full scale, pinned across two languages.

WHY THIS EXISTS
---------------
The Sense carries an LSM6DS3TR-C at +-16 g. That hardware fact appears in
THREE places, in two languages, with no generator and no single source of
truth:

  1. sim/detector.py       ACCEL_FULL_SCALE_G, used by correct_for_spin's
                           anti-livelock guard (F-16)
  2. sim/sensor_model.py   SensorConfig.clip_g, the render-time range clip
  3. firmware/include/jump_detector.h:127   `if (rot_g > 16.0f)`

config/params.json has a `shared` block whose entire purpose is "constants
that exist in MORE THAN ONE LANGUAGE and must not drift (audit F-18)". This
constant is not in it. It should be, and moving it there means regenerating
params.gen.h and therefore a firmware flash, which CLAUDE.md rule 4 says to
batch. Until that batch, this file is the guard.

MEASURED, 2026-09-06 — why a test and not a comment. The mutation campaign
flagged the detector's guard literal as unpinned, and a sweep found the
suite cannot distinguish 16.0 from ANY value in 10.0-48.0:

    2.0 KILLED   4.0 KILLED   8.0 KILLED
   10.0 SURVIVED  ...  48.0 SURVIVED      <- a 3x-wide blind band
   64.0 KILLED  96.0 KILLED  160.0 KILLED

So the Python guard could be wrong by 3x in either direction, or drift from
the C++ twin, with every one of the 254 tests still green. The guard's whole
job is to refuse a sample the accelerometer physically cannot have produced;
a wrong full scale makes it refuse the wrong samples, and F-16 records what
that cost: a livelock that erased real landing spikes and fabricated jumps.

This does not test the guard's BEHAVIOUR — data/spin_railed_gyro.csv and
tools/tests/test_spin_correction.py do that, and they do kill a guard moved
to 64.0 or removed entirely. This pins its VALUE, and pins the three copies
to each other.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sim"))

from detector import ACCEL_FULL_SCALE_G  # noqa: E402
from sensor_model import SensorConfig  # noqa: E402

FIRMWARE_HEADER = REPO / "firmware" / "include" / "jump_detector.h"

# The datasheet value for the part actually on the board. Changing this line
# means the hardware changed; read this file's header before you do.
LSM6DS3TR_C_FULL_SCALE_G = 16.0


class AccelFullScaleIsOneNumber(unittest.TestCase):
    def test_detector_matches_the_part(self) -> None:
        self.assertEqual(
            ACCEL_FULL_SCALE_G, LSM6DS3TR_C_FULL_SCALE_G,
            "sim/detector.py's ACCEL_FULL_SCALE_G no longer matches the "
            "LSM6DS3TR-C's +-16 g full scale. The anti-livelock guard uses it "
            "to refuse samples the accelerometer physically cannot produce, so "
            "a wrong value refuses the wrong samples (F-16).")

    def test_sensor_model_clip_matches_the_part(self) -> None:
        self.assertEqual(
            SensorConfig().clip_g, LSM6DS3TR_C_FULL_SCALE_G,
            "sim/sensor_model.py's SensorConfig.clip_g no longer matches the "
            "part's full scale. The renderer would then clip at a range the "
            "detector's guard does not expect, and every E-series number "
            "rendered through it inherits the mismatch.")

    def test_firmware_guard_matches_the_python_one(self) -> None:
        """The cross-language half — the one no generator covers."""
        src = FIRMWARE_HEADER.read_text()
        m = re.search(r"rot_g\s*>\s*([0-9]+(?:\.[0-9]+)?)f", src)
        self.assertIsNotNone(
            m,
            f"could not find the anti-livelock guard literal in "
            f"{FIRMWARE_HEADER}. If the guard was renamed or restructured, "
            "update this regex — do NOT delete the assertion, because the "
            "constant is still duplicated across two languages.")
        firmware_value = float(m.group(1))
        self.assertEqual(
            firmware_value, ACCEL_FULL_SCALE_G,
            f"firmware/include/jump_detector.h guards at {firmware_value} g "
            f"while sim/detector.py guards at {ACCEL_FULL_SCALE_G} g. The "
            "Python file is a declared 1:1 mirror of the C++ one; a "
            "divergence here means the simulator and the silicon disagree "
            "about which samples are physically impossible.")

    def test_the_guard_actually_uses_the_constant(self) -> None:
        """Closes the bypass: naming a constant is worthless if the guard
        stops referring to it.

        Verified 2026-09-06 — with only the equality assertions above,
        re-inlining `rot_g > 20.0` into the guard SURVIVED, because nothing
        checked that the guard reads the constant it is supposed to read.
        """
        src = (REPO / "sim" / "detector.py").read_text()
        self.assertIn(
            "if rot_g > ACCEL_FULL_SCALE_G:", src,
            "correct_for_spin's anti-livelock guard no longer reads "
            "ACCEL_FULL_SCALE_G. A literal inlined there is invisible to "
            "every other assertion in this file, which is exactly how the "
            "10-48 g blind band came about in the first place.")

    def test_the_constant_is_not_yet_in_the_shared_block(self) -> None:
        """A REMINDER, not a rule — it fails when the real fix lands.

        config/params.json's `shared` block exists for exactly this kind of
        constant. When someone moves it there and regenerates the headers,
        this test fails and tells them to delete this file's regex half,
        because the generator will then be the guard.
        """
        import json
        shared = json.loads((REPO / "config" / "params.json").read_text())["shared"]
        keys = {k for k in shared if not k.startswith("_")}
        offenders = {k for k in keys if "full_scale" in k or "accel_fs" in k or "clip" in k}
        self.assertEqual(
            offenders, set(),
            "the accelerometer full scale now lives in config/params.json's "
            "shared block — the generator is the single source of truth. "
            "Delete test_firmware_guard_matches_the_python_one and this test, "
            "and let params.gen.h carry it.")


if __name__ == "__main__":
    unittest.main()
