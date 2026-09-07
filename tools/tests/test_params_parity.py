#!/usr/bin/env python3
"""Params defaults vs config/params.json — the one invariant nothing pinned.

WHY THIS EXISTS
---------------
Found 2026-09-06 by the mutation campaign, at mutant 3 of 333: changing
`sim/detector.py`'s `freefall_confirm_s` default from 0.08 to 0.1 — a 25 %
move in a launch-confirmation window that DECISION #41 and E12 spent 200,000
simulated jumps settling — survived all 249 tests. Nothing in the suite reads
those defaults.

Inspecting that survivor turned up a second thing the suite could not see:
`Params`' own docstring said "Defaults match config/params.json", and it did
not. `airtime_offset_s` is 0.0 in the dataclass and 0.0192 in the JSON.

That mismatch is CORRECT and load-bearing, which is exactly why it needs a
test rather than a fix. `airtime_offset_s` is an empirical correction for the
DEVICE's takeoff-edge detection latency (−19 ms, measured over 8 drops on
2026-08-24, E16). The simulator has no such latency: it renders flights from
the physics. Eight experiment scripts — e1, e2, e4, e5, e5_verify, e6, g4,
g5 — construct a bare `Params()` on purpose so they measure physics, not
physics plus a hardware correction that does not apply to them.

So the hazard runs the other way from how it looks. Someone "tidying up" the
mismatch by setting the default to 0.0192 would silently add 19 ms to every
airtime in every bare-`Params()` experiment, shifting every height in the
E-series evidence base, with no test complaining. This file makes that
tidy-up fail loudly.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import dataclasses
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from sim.detector import Params, load_params  # noqa: E402

CONFIG = REPO / "config" / "params.json"

# The single field whose dataclass default deliberately differs from the JSON.
# Keyed to the reason, not just the name, so a future reader gets the why.
DELIBERATE_EXCEPTIONS = {
    "airtime_offset_s": (
        "a correction for DEVICE edge-detection latency; the simulator has no "
        "such latency, and eight E-series scripts use a bare Params() so they "
        "measure physics rather than physics plus a hardware correction"
    ),
}


class ParamsMatchConfig(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = json.loads(CONFIG.read_text())["detector"]
        self.defaults = Params()

    def test_every_field_exists_in_both(self) -> None:
        """A field in one place and not the other is a drift waiting to happen."""
        in_code = {f.name for f in dataclasses.fields(Params)}
        in_json = {k for k in self.cfg if not k.startswith("_")}
        self.assertEqual(
            in_code, in_json,
            "config/params.json and sim.detector.Params have drifted apart. "
            f"only in code: {sorted(in_code - in_json)}; "
            f"only in json: {sorted(in_json - in_code)}")

    def test_defaults_match_json_except_the_documented_one(self) -> None:
        """Every default equals the JSON, bar the documented exception.

        This is the assertion the mutation campaign proved missing: it fails
        on any edit to a detector threshold default that does not also land
        in config/params.json.
        """
        mismatched = {}
        for f in dataclasses.fields(Params):
            if f.name in DELIBERATE_EXCEPTIONS:
                continue
            got = getattr(self.defaults, f.name)
            want = self.cfg[f.name]
            if got != want:
                mismatched[f.name] = (got, want)
        self.assertEqual(
            mismatched, {},
            "sim.detector.Params defaults no longer match config/params.json. "
            "config/params.json is the single source of truth (it also "
            "generates params.gen.h and Params.gen.mc), so fix the dataclass "
            f"default, not the JSON. mismatches (code, json): {mismatched}")

    def test_simulator_airtime_offset_default_stays_zero(self) -> None:
        """The deliberate exception, pinned in the direction that can hurt.

        Setting this default to the device's measured offset would add that
        offset to every airtime in every bare-Params() experiment. Read this
        file's header before changing it.
        """
        self.assertEqual(
            self.defaults.airtime_offset_s, 0.0,
            "Params().airtime_offset_s must stay 0.0. It is a DEVICE latency "
            "correction and the simulator has no such latency; eight E-series "
            "scripts build a bare Params() and would silently gain the offset. "
            f"Reason on record: {DELIBERATE_EXCEPTIONS['airtime_offset_s']}")

    def test_load_params_does_pick_up_the_json_offset(self) -> None:
        """The other half: the calibrated path must NOT be zero.

        Without this, a JSON edited to 0.0 would look identical to the
        deliberate default above, and the drop calibration would be silently
        absent everywhere — the failure mode STATUS.md calls a calibration
        provenance loss.
        """
        loaded = load_params(CONFIG)
        self.assertEqual(
            loaded.airtime_offset_s, self.cfg["airtime_offset_s"],
            "load_params() must return the JSON's airtime_offset_s")
        self.assertNotEqual(
            loaded.airtime_offset_s, 0.0,
            "config/params.json's airtime_offset_s is 0.0, so the measured "
            "drop calibration (-19 ms, 8 drops, 2026-08-24) is not in the "
            "config any more. If that is intentional, say so here; otherwise "
            "restore it before trusting a single height.")

    def test_missing_config_falls_back_to_defaults(self) -> None:
        """The documented fallback, so the exception above cannot hide a
        missing file: a bad path returns defaults rather than raising."""
        got = load_params(REPO / "config" / "does-not-exist.json")
        self.assertEqual(got, Params())


if __name__ == "__main__":
    unittest.main()
