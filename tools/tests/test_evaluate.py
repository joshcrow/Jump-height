"""Unit tests for sim/evaluate.py -- the labeled-corpus evaluator that scores
the detector against video-derived ground truth (docs/data-pipeline.md).

Builds a synthetic labeled session on disk (trace.csv + labels.csv +
session.json), runs the corpus evaluator, and checks: perfect-match scoring on
clean synthetic data, extensible non-jump labels parse, the split filter, and
the regression gate fires in the correct DIRECTION (worse-than-baseline fails,
identical/improved passes). Absolute error values are synthetic-only; the point
is the harness plumbing, not tuned thresholds.

Run via ./tools/jump simtest, or directly:
    python3 -m pytest tools/tests/test_evaluate.py -q
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

import generate  # noqa: E402
import evaluate  # noqa: E402
from detector import load_params, height_for_airtime  # noqa: E402


def _write_session(root: Path, name: str, jumps, seed: int = 1,
                   split: str = "train", extra_labels=()):
    """Write a synthetic session dir: trace.csv (50 Hz) + labels.csv + session.json.
    Ground-truth height per jump = g*airtime^2/8 of the true airtime."""
    sess = root / name
    sess.mkdir(parents=True)
    times, mag = generate.synth_session(jumps, seed=seed, fs_hz=50.0)
    with open(sess / "trace.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "mag"])
        for t, a in zip(times, mag):
            w.writerow([f"{t:.4f}", f"{a:.4f}"])
    with open(sess / "labels.csv", "w", newline="") as f:
        w = csv.writer(f)
        # height_src="sim": in a synthetic session the apex is an INPUT to the
        # generator, not a re-derivation of the device's own measurement, so it
        # is admissible truth (sim/evaluate.py INDEPENDENT_SRC). A real session
        # labelled from counted video frames would be "timing" and would be
        # excluded from RMSE — deliberately.
        w.writerow(["event", "t_start_s", "t_end_s", "height_m", "height_src",
                    "rotation_deg", "landing", "notes"])
        for (t0, at) in jumps:
            w.writerow(["jump", f"{t0:.2f}", "", f"{height_for_airtime(at):.3f}", "sim",
                        "", "flat", "synthetic"])
        for row in extra_labels:
            # Callers still pass the original 7-column shape; splice the new
            # height_src column in so later fields do not shift by one.
            row = list(row)
            if len(row) == 7:
                row = row[:4] + [""] + row[4:]
            w.writerow(row)
    (sess / "session.json").write_text(json.dumps(
        {"unit": "sense-01", "firmware": "0.4.3", "split": split}))
    return sess


class EvaluatorTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "sessions"
        self.jumps = generate.DEMO_JUMPS
        _write_session(
            self.root, "20260805-a", self.jumps, seed=1, split="train",
            extra_labels=[["foil", "8.0", "25.0", "", "", "", "on foil"],
                          ["carve", "15.0", "16.0", "", "", "", "hard carve"]])
        self.params = load_params()

    def test_perfect_match_on_synthetic(self):
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(agg["n_sessions"], 1)
        self.assertEqual(agg["n_true"], len(self.jumps))
        self.assertEqual(agg["matched"], len(self.jumps))
        self.assertEqual(agg["missed"], 0)
        self.assertEqual(agg["spurious"], 0)
        self.assertEqual(agg["detection_rate"], 1.0)
        # clean synthetic -> height error is essentially zero
        self.assertLess(agg["height"]["rmse"], 0.1)

    def test_circular_height_truth_is_excluded_from_rmse(self):
        """A height derived from counted airtime must NOT produce an RMSE.

        This is the failure the whole labelling procedure is designed around:
        h = g*T^2/8 is the formula the firmware uses, so a "truth" height built
        the same way agrees by construction. It yields a small, confident,
        meaningless error bar — and it would pass whether or not wings are
        ballistic, which is the entire question the water session exists to
        answer. Detection must still be scored; only the height is barred."""
        root = Path(tempfile.mkdtemp()) / "sessions"
        _write_session(root, "20260805-c", self.jumps, seed=1, split="train")
        # Downgrade the provenance to the circular kind, changing nothing else.
        lp = root / "20260805-c" / "labels.csv"
        lp.write_text(lp.read_text().replace(",sim,", ",timing,"))

        agg = evaluate.eval_corpus(root, self.params, split="all")
        self.assertEqual(agg["matched"], len(self.jumps),
                         "detection must still be scored")
        self.assertIsNone(agg["height"]["rmse"],
                          "circular truth must not produce an accuracy number")
        self.assertEqual(agg["height"]["n"], 0)
        self.assertEqual(agg["circular_heights"], len(self.jumps),
                         "and the report must be able to say why")

    def test_blank_height_src_is_treated_as_circular(self):
        """Unknown provenance defaults to inadmissible.

        Assuming the friendly interpretation is exactly how a circular number
        gets published as an accuracy claim."""
        root = Path(tempfile.mkdtemp()) / "sessions"
        _write_session(root, "20260805-d", self.jumps, seed=1, split="train")
        lp = root / "20260805-d" / "labels.csv"
        lp.write_text(lp.read_text().replace(",sim,", ",,"))
        agg = evaluate.eval_corpus(root, self.params, split="all")
        self.assertIsNone(agg["height"]["rmse"])
        self.assertEqual(agg["circular_heights"], len(self.jumps))

    def test_extensible_labels_parsed(self):
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(agg["sessions"][0]["other_labels"], ["carve", "foil"])

    def test_split_filter(self):
        _write_session(self.root, "20260805-b", self.jumps, seed=2, split="test")
        train = evaluate.eval_corpus(self.root, self.params, split="train")
        test = evaluate.eval_corpus(self.root, self.params, split="test")
        allc = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(train["n_sessions"], 1)
        self.assertEqual(test["n_sessions"], 1)
        self.assertEqual(allc["n_sessions"], 2)

    def test_no_labels_is_skipped(self):
        # a session with a trace but no labels.csv must not be scored
        bare = self.root / "20260805-bare"
        bare.mkdir()
        (bare / "trace.csv").write_text("t,mag\n0.0,1.0\n")
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(agg["n_sessions"], 1)  # only the labeled one

    def test_regression_gate_direction(self):
        good = {"rmse": 0.04, "detection_rate": 0.95, "spurious": 1}
        worse = {"rmse": 0.09, "detection_rate": 0.80, "spurious": 4}
        better = {"rmse": 0.02, "detection_rate": 0.98, "spurious": 0}
        ok_same, msgs_same = evaluate.regression_check(good, good)
        self.assertTrue(ok_same)
        self.assertEqual(msgs_same, [])
        ok_worse, _ = evaluate.regression_check(worse, good)
        self.assertFalse(ok_worse)               # worse-than-baseline must FAIL
        ok_better, _ = evaluate.regression_check(better, good)
        self.assertTrue(ok_better)               # improvement passes


if __name__ == "__main__":
    unittest.main()
