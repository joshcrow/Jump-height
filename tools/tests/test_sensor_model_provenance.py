#!/usr/bin/env python3
"""SensorConfig's defaults, pinned to the sources its own comments cite.

WHY THIS EXISTS
---------------
On 2026-09-06 the mutation campaign reached `sim/sensor_model.py` and returned
a survivor for EVERY field of `SensorConfig` — fs_hz 200->250, chop_g
0.12->0.15, swell_g 0.05->0.0625, swell_hz 0.3->0.375, pop_g 1.5->1.875,
pop_s 0.06->0.075, landing_g 4.8->6.0, landing_s 0.03->0.0375 — the same
shape as the detector's `Params` defaults earlier the same evening, and in
the module that RENDERS the physics behind every E-series number in the repo.
Nothing read those defaults, so any of them could move and the suite stayed
green.

Three of the comments in that dataclass make checkable claims, and they are
what this file enforces:

  fs_hz            "detector sample rate"        -> config/params.json
                                                    firmware.sample_hz
  chop_g           "matches generate.py"         -> sim/generate.py's default
  landing_g        "Simons 2025: 4.2-5.5 g"      -> must sit in that range

And one duplication the comments do NOT mention: `sim/generate.py:47` INLINES
the swell as `0.05 * sin(2*pi*0.3*t)`, the same two numbers SensorConfig
names as `swell_g` and `swell_hz`. Two renderers with the same sea state
written twice is exactly the drift CLAUDE.md section 4 is about, so the
regex half below pins them together until one of them stops inlining.

The remaining fields (pop_g, pop_s, landing_s, noise_g, pre_s, post_s) have
no external source to check against. They are pinned as a snapshot anyway,
because moving one silently re-renders every simulated jump the repo's
conclusions rest on — E1 through E16 included. Changing one is legitimate;
changing one WITHOUT noticing is the failure this file exists to prevent.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import inspect
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sim"))

import generate  # noqa: E402
from sensor_model import SensorConfig  # noqa: E402

PARAMS_JSON = REPO / "config" / "params.json"
GENERATE_PY = REPO / "sim" / "generate.py"

# Simons 2025's reported landing-spike range, as cited in the dataclass
# comment and in sim/wing_model.py's header.
SIMONS_LANDING_G = (4.2, 5.5)


class CitedProvenance(unittest.TestCase):
    """The three comments that make a checkable claim."""

    def setUp(self) -> None:
        self.cfg = SensorConfig()

    def test_fs_hz_matches_the_firmware_sample_rate(self) -> None:
        want = json.loads(PARAMS_JSON.read_text())["firmware"]["sample_hz"]
        self.assertEqual(
            self.cfg.fs_hz, float(want),
            f"SensorConfig.fs_hz is {self.cfg.fs_hz} but config/params.json "
            f"says the device samples at {want} Hz. The renderer would then "
            "feed the detector at a rate the silicon never produces, and "
            "every timing result derived from it inherits the mismatch.")

    def test_chop_g_matches_generate_py(self) -> None:
        """The comment says 'matches generate.py'. Check that it does."""
        sig = inspect.signature(generate.synth_session)
        want = sig.parameters["chop_g"].default
        self.assertEqual(
            self.cfg.chop_g, want,
            f"SensorConfig.chop_g is {self.cfg.chop_g} and "
            f"generate.synth_session's chop_g default is {want}. The comment "
            "on that field claims they match; one of the two moved. Two "
            "renderers disagreeing about the riding baseline means two "
            "different worlds behind results quoted side by side.")

    def test_landing_g_sits_in_the_cited_literature_range(self) -> None:
        lo, hi = SIMONS_LANDING_G
        self.assertTrue(
            lo <= self.cfg.landing_g <= hi,
            f"SensorConfig.landing_g is {self.cfg.landing_g} g, outside the "
            f"{lo}-{hi} g range the field's own comment cites from Simons "
            "2025. Either the value drifted or the citation is wrong, and in "
            "this repo a confident wrong citation is worse than none.")


class SwellIsWrittenTwice(unittest.TestCase):
    """sim/generate.py inlines the same swell SensorConfig names."""

    def _inlined(self) -> tuple[float, float]:
        src = GENERATE_PY.read_text()
        m = re.search(
            r"([0-9]*\.?[0-9]+)\s*\*\s*math\.sin\(\s*2\s*\*\s*math\.pi\s*\*\s*"
            r"([0-9]*\.?[0-9]+)\s*\*",
            src)
        self.assertIsNotNone(
            m,
            f"could not find the inlined swell term in {GENERATE_PY}. If "
            "generate.py now takes it from SensorConfig, delete this class — "
            "the duplication it guards would be gone. Do NOT just delete the "
            "regex and keep the file.")
        return float(m.group(1)), float(m.group(2))

    def test_amplitude_agrees(self) -> None:
        amp, _ = self._inlined()
        self.assertEqual(
            SensorConfig().swell_g, amp,
            f"SensorConfig.swell_g is {SensorConfig().swell_g} while "
            f"sim/generate.py inlines {amp}. Same sea state, two numbers.")

    def test_frequency_agrees(self) -> None:
        _, hz = self._inlined()
        self.assertEqual(
            SensorConfig().swell_hz, hz,
            f"SensorConfig.swell_hz is {SensorConfig().swell_hz} while "
            f"sim/generate.py inlines {hz}. Same sea state, two numbers.")


class UncitedDefaultsAreSnapshotted(unittest.TestCase):
    """No external source exists for these, so pin them and say why.

    Every number below was chosen deliberately and every E-series result was
    rendered through it. A deliberate change should edit this table in the
    same commit; an accidental one should fail here.
    """

    SNAPSHOT = {
        "pop_g": 1.5,       # load-up bump amplitude before takeoff
        "pop_s": 0.06,      # its duration
        "landing_s": 0.03,  # landing spike duration
        "noise_g": 0.02,    # per-sample gaussian sensor noise
        "pre_s": 2.0,       # riding lead-in before takeoff
        "post_s": 1.5,      # riding tail-out after landing
        "spin_rps": 0.0,    # rotation confound OFF by default
        "lever_m": 0.0,     # ... and no lever arm, so omega^2*r is identity
    }

    def test_defaults_are_unchanged(self) -> None:
        cfg = SensorConfig()
        drifted = {
            name: (getattr(cfg, name), want)
            for name, want in self.SNAPSHOT.items()
            if getattr(cfg, name) != want
        }
        self.assertEqual(
            drifted, {},
            "SensorConfig defaults changed without this snapshot changing. "
            "These render the flights behind E1-E16; moving one re-renders "
            "every simulated jump those conclusions rest on. If the change is "
            "deliberate, update the table above IN THE SAME COMMIT and say "
            f"what it invalidates. drifted (actual, expected): {drifted}")

    def test_rotation_confound_is_off_by_default(self) -> None:
        """Stated separately because it is load-bearing, not cosmetic.

        With spin_rps or lever_m non-zero by default, every experiment that
        builds a bare SensorConfig would silently acquire a spin confound —
        and E3 measured that the median-|a| flag collapses from AUC 1.000 to
        0.258 under exactly that.
        """
        cfg = SensorConfig()
        self.assertEqual((cfg.spin_rps, cfg.lever_m), (0.0, 0.0),
                         "the spin confound must default to OFF; a bare "
                         "SensorConfig is the ballistic baseline.")


if __name__ == "__main__":
    unittest.main()
