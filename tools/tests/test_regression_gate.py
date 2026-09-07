#!/usr/bin/env python3
"""The regression gate, and the column indices it grades from.

WHY THIS EXISTS
---------------
`sim/evaluate.py`'s `regression_check` exists to return ok=False when the
detector got worse. On 2026-09-06 the mutation campaign found that its three
`ok = False` sites can each be flipped to `ok = True` with the whole suite
green — a gate whose only job is to fail, that cannot fail. Also unpinned:
both tolerances (0.02), the None guards, and every comparison direction.

That is F-27's sibling, in F-27's own file. F-27 was "`jump eval --split` was
unguarded; inverting the filter passed the suite", and it was closed with a
partition property test. This is the same shape one function along.

It matters more than an ordinary coverage gap for two reasons. First,
`data/` holds NO baseline file, so `regression_check` has never once run on
real metrics — untested AND unexercised. Second, its whole point is to be
the thing that stops a bad detector shipping, and on 2026-09-06 the eval
output ("matched 9/9, missed 0, spurious 3") was quoted as evidence in
STATUS.md and in the session notes. Evidence produced by an ungated grader.

Second finding pinned here: `load_device_jumps` reads takeoff, airtime and
height from `parts[1]`, `parts[3]`, `parts[4]` of jumps.csv. Mutating those
indices survived, so the grader could silently read airtime_raw_s as
airtime, or med_a_g as height, and score against the wrong column.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sim"))

from evaluate import load_device_jumps, regression_check  # noqa: E402

# The documented tolerances. Literals, not evaluate.py's defaults — a test
# that reads the constant it pins moves with it (learned the hard way in
# tools/tests/test_lever_arm_invariants.py, same evening).
EXPECTED_RMSE_TOL = 0.02
EXPECTED_RATE_TOL = 0.02

BASE = {"rmse": 0.100, "detection_rate": 0.900, "spurious": 4}


def cur(**over):
    d = dict(BASE)
    d.update(over)
    return d


class GateCatchesRegressions(unittest.TestCase):
    """Each case drives one of the three `ok = False` sites."""

    def test_identical_metrics_pass(self) -> None:
        ok, msgs = regression_check(cur(), BASE)
        self.assertTrue(ok, f"identical metrics must pass; got {msgs}")
        self.assertFalse([m for m in msgs if m.startswith("REGRESSION")])

    def test_rmse_growth_beyond_tolerance_fails(self) -> None:
        ok, msgs = regression_check(cur(rmse=BASE["rmse"] + 0.05), BASE)
        self.assertFalse(
            ok,
            "RMSE grew by 0.05 m and the gate passed. This assertion exists "
            "because `ok = False` in worse_higher() was mutable to `True` "
            "with every test green.")
        self.assertTrue(any(m.startswith("REGRESSION rmse") for m in msgs), msgs)

    def test_rmse_growth_inside_tolerance_passes(self) -> None:
        """Pins the tolerance from below: shrinking it would fail here."""
        ok, _ = regression_check(cur(rmse=BASE["rmse"] + EXPECTED_RMSE_TOL / 2), BASE)
        self.assertTrue(
            ok,
            f"a {EXPECTED_RMSE_TOL / 2} m RMSE move is inside the documented "
            f"{EXPECTED_RMSE_TOL} m tolerance and must not fail the gate.")

    def test_rmse_improvement_passes_and_is_reported(self) -> None:
        ok, msgs = regression_check(cur(rmse=BASE["rmse"] - 0.05), BASE)
        self.assertTrue(ok)
        self.assertTrue(
            any(m.startswith("improved rmse") for m in msgs),
            f"an RMSE improvement must be reported, not silent: {msgs}")

    def test_detection_rate_drop_beyond_tolerance_fails(self) -> None:
        ok, msgs = regression_check(
            cur(detection_rate=BASE["detection_rate"] - 0.10), BASE)
        self.assertFalse(
            ok,
            "detection rate fell 10 points and the gate passed. This drives "
            "the SECOND `ok = False` site, in the hand-written detection-rate "
            "branch rather than worse_higher().")
        self.assertTrue(
            any(m.startswith("REGRESSION detection_rate") for m in msgs), msgs)

    def test_detection_rate_drop_inside_tolerance_passes(self) -> None:
        ok, _ = regression_check(
            cur(detection_rate=BASE["detection_rate"] - EXPECTED_RATE_TOL / 2), BASE)
        self.assertTrue(ok)

    def test_detection_rate_improvement_passes_and_is_reported(self) -> None:
        ok, msgs = regression_check(
            cur(detection_rate=BASE["detection_rate"] + 0.05), BASE)
        self.assertTrue(ok)
        self.assertTrue(
            any(m.startswith("improved detection_rate") for m in msgs), msgs)

    def test_any_extra_spurious_detection_fails(self) -> None:
        """Spurious is checked at tolerance ZERO — one more is a regression.

        Drives the third `ok = False` site, and pins the 0 tolerance: a
        non-zero one would let this pass.
        """
        ok, msgs = regression_check(cur(spurious=BASE["spurious"] + 1), BASE)
        self.assertFalse(
            ok,
            "one additional spurious detection must fail the gate; the "
            "docstring says spurious increases are a regression, with no "
            "tolerance. On the 2026-09-06 vest session the device produced "
            "twice the spurious count the trace-based grader saw, so this is "
            "the number most likely to move for real.")
        self.assertTrue(any(m.startswith("REGRESSION spurious") for m in msgs), msgs)

    def test_fewer_spurious_passes(self) -> None:
        ok, _ = regression_check(cur(spurious=BASE["spurious"] - 1), BASE)
        self.assertTrue(ok)

    def test_a_regression_in_any_one_metric_is_enough(self) -> None:
        """Two improvements must not cancel one regression."""
        ok, _ = regression_check(
            cur(rmse=BASE["rmse"] - 0.05,
                detection_rate=BASE["detection_rate"] + 0.05,
                spurious=BASE["spurious"] + 3), BASE)
        self.assertFalse(
            ok,
            "improvements elsewhere must not offset a regression; the gate "
            "is an AND over metrics, not a score.")


class ToleranceBoundariesAreTight(unittest.TestCase):
    """Probes that STRADDLE each tolerance, not just sit either side of it.

    Added after the first draft of this file let `rmse_tol 0.02 -> 0.025`
    survive: its "inside" probe was +0.01 and its "outside" probe +0.05, so a
    25 % widening fell in the gap between them and changed nothing either
    test could see. Third time this exact mistake appeared on 2026-09-06 —
    the other two are recorded in tools/mutation_campaign.py and
    tools/tests/test_lever_arm_invariants.py. Probing endpoints and
    generalising to the middle is what E12 exists to correct.
    """

    def test_just_over_the_rmse_tolerance_fails(self) -> None:
        ok, msgs = regression_check(
            cur(rmse=BASE["rmse"] + EXPECTED_RMSE_TOL + 0.001), BASE)
        self.assertFalse(
            ok,
            f"an RMSE move of {EXPECTED_RMSE_TOL + 0.001} m is over the "
            f"documented {EXPECTED_RMSE_TOL} m tolerance and must fail. If "
            "this passes, the tolerance was widened.")

    def test_just_under_the_rmse_tolerance_passes(self) -> None:
        ok, _ = regression_check(
            cur(rmse=BASE["rmse"] + EXPECTED_RMSE_TOL - 0.001), BASE)
        self.assertTrue(
            ok,
            f"an RMSE move of {EXPECTED_RMSE_TOL - 0.001} m is under the "
            "tolerance and must pass. If this fails, it was narrowed.")

    def test_just_over_the_rate_tolerance_fails(self) -> None:
        ok, _ = regression_check(
            cur(detection_rate=BASE["detection_rate"] - EXPECTED_RATE_TOL - 0.001),
            BASE)
        self.assertFalse(
            ok,
            f"a detection-rate drop of {EXPECTED_RATE_TOL + 0.001} is over "
            f"the documented {EXPECTED_RATE_TOL} tolerance and must fail.")

    def test_just_under_the_rate_tolerance_passes(self) -> None:
        ok, _ = regression_check(
            cur(detection_rate=BASE["detection_rate"] - EXPECTED_RATE_TOL + 0.001),
            BASE)
        self.assertTrue(ok)


class GateHandlesMissingMetrics(unittest.TestCase):
    """The None guards — a metric absent on either side is skipped, not fatal."""

    def test_missing_in_current_is_skipped(self) -> None:
        d = cur()
        del d["rmse"]
        ok, msgs = regression_check(d, BASE)
        self.assertTrue(ok, f"absent current rmse must be skipped: {msgs}")
        self.assertFalse([m for m in msgs if "rmse" in m], msgs)

    def test_missing_in_baseline_is_skipped(self) -> None:
        b = dict(BASE)
        del b["detection_rate"]
        ok, msgs = regression_check(cur(detection_rate=0.1), b)
        self.assertTrue(
            ok,
            "a baseline with no detection_rate cannot judge one; skipping is "
            "correct, and must not crash on the None.")

    def test_empty_baseline_passes_without_crashing(self) -> None:
        ok, msgs = regression_check(cur(), {})
        self.assertTrue(ok)
        self.assertEqual([m for m in msgs if m.startswith("REGRESSION")], [])


class DeviceJumpColumns(unittest.TestCase):
    """jumps.csv column indices: takeoff=1, airtime=3 (calibrated), height=4.

    The values below are deliberately all DIFFERENT so shifting any index by
    one changes the parsed result.
    """

    HEADER = "n,takeoff_s,airtime_raw_s,airtime_s,height_m,med_a_g,med_w_dps,med_acorr_g,n_air"
    ROW = "1,1000.500,0.700,0.750,0.900,0.111,222,0.111,150"

    def test_reads_the_documented_columns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sess = Path(td)
            (sess / "jumps.csv").write_text(f"{self.HEADER}\n{self.ROW}\n")
            got = load_device_jumps(sess)
        self.assertEqual(len(got), 1, f"one data row expected, got {got}")
        self.assertAlmostEqual(
            got[0]["takeoff"], 1000.500, places=6,
            msg="takeoff must come from takeoff_s (column 1)")
        self.assertAlmostEqual(
            got[0]["airtime"], 0.750, places=6,
            msg="airtime must come from airtime_s, the CALIBRATED column (3) "
                "— not airtime_raw_s (2), which is 0.700 here. The device "
                "applies airtime_offset_s and the grader must score the same "
                "number the rider was shown.")
        self.assertAlmostEqual(
            got[0]["height"], 0.900, places=6,
            msg="height must come from height_m (column 4) — not med_a_g (5), "
                "which is 0.111 here.")

    def test_header_and_comment_rows_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            sess = Path(td)
            (sess / "jumps.csv").write_text(
                f"{self.HEADER}\n# a comment\n{self.ROW}\n")
            got = load_device_jumps(sess)
        self.assertEqual(len(got), 1, f"header and comment must be skipped: {got}")

    def test_missing_file_is_empty_not_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(load_device_jumps(Path(td)), [])


if __name__ == "__main__":
    unittest.main()
