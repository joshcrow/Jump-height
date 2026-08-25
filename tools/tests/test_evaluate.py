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

import contextlib
import csv
import io
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


def _relabel(sess: Path, jump_times, height_src="sim", height="1.000"):
    """Rewrite a session's labels.csv with explicit jump takeoff times.
    Used to build the degenerate label files the evaluator must refuse."""
    with open(sess / "labels.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["event", "t_start_s", "t_end_s", "height_m", "height_src",
                    "rotation_deg", "landing", "notes"])
        for t in jump_times:
            w.writerow(["jump", f"{t:.3f}", "", height, height_src, "", "flat", "x"])


def _trace_span(sess: Path):
    ts = [float(r["t"]) for r in csv.DictReader(open(sess / "trace.csv"))]
    return min(ts), max(ts)


def _report(agg, verbose=False) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        evaluate.print_report(agg, verbose=verbose)
    return buf.getvalue()


class SessionDiscoveryTest(unittest.TestCase):
    """Sessions must be found at ANY depth, and a miss must not read as an absence.

    Discovery was `root.glob("*")` — one level. The repo's only labeled session
    sat at data/sessions/jitter-check/<id>/, two levels down, so `jump eval`
    reported 'No labeled sessions found' — the message for 'you have not
    labeled anything yet'. The one piece of ground truth in the project was
    invisible, and nothing on screen could tell the two cases apart
    (CLAUDE.md rule 3)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "sessions"
        self.jumps = generate.DEMO_JUMPS
        self.params = load_params()

    def test_nested_session_is_found(self):
        _write_session(self.root, "jitter-check/20260815-190012", self.jumps)
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(agg["n_sessions"], 1)
        self.assertEqual(agg["matched"], len(self.jumps))

    def test_depth_one_name_is_still_the_bare_leaf(self):
        """The flat case must render exactly as it always did."""
        _write_session(self.root, "20260805-a", self.jumps)
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(agg["sessions"][0]["session"], "20260805-a")

    def test_same_leaf_name_at_two_depths_stays_distinguishable(self):
        """Every `jump sync` dir is a bare timestamp, so two groups can easily
        hold the same leaf name. Reported as `sess.name` they would be two
        identical, unattributable rows in the table."""
        _write_session(self.root, "pull-a/20260815-115418", self.jumps)
        _write_session(self.root, "pull-b/20260815-115418", self.jumps, seed=2)
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        names = sorted(r["session"] for r in agg["sessions"])
        self.assertEqual(names, ["pull-a/20260815-115418", "pull-b/20260815-115418"])

    def test_a_matched_session_is_not_descended_into(self):
        """A session's own subdirectories are its artefacts. Recursing into a
        matched session would score a copy of its trace a second time and
        double the corpus's jump count."""
        sess = _write_session(self.root, "20260805-a", self.jumps)
        backup = sess / "backup"
        backup.mkdir()
        for f in ("trace.csv", "labels.csv"):
            (backup / f).write_text((sess / f).read_text())
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(agg["n_sessions"], 1)
        self.assertEqual(agg["n_true"], len(self.jumps))

    def test_traces_without_labels_are_counted_and_named(self):
        """'I found 14 traces, none labeled' is a different fact from 'I found
        nothing', and only one of them is the user's own doing."""
        for n in ("20260731-092453", "grp/20260731-094036"):
            d = self.root / n
            d.mkdir(parents=True)
            (d / "trace.csv").write_text("t,mag\n0.0,1.0\n0.02,1.0\n")
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(agg["n_sessions"], 0)
        self.assertEqual(sorted(agg["unlabeled_traces"]),
                         ["20260731-092453", "grp/20260731-094036"])
        out = _report(agg)
        self.assertIn("2 director(ies) DO hold a trace.csv but no labels.csv", out)
        self.assertIn("grp/20260731-094036", out)

    def test_empty_root_says_nothing_at_all_was_there(self):
        self.root.mkdir(parents=True)
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        out = _report(agg)
        self.assertIn("No labeled sessions found", out)
        self.assertNotIn("DO hold a trace.csv", out)


class SplitFilterTest(unittest.TestCase):
    """`--split` must select exactly its own sessions, and nothing else.

    Audit F-27 (2026-08-24): the mutation campaign inverted the split
    comparison at `sim/evaluate.py:424` — making it keep precisely the
    sessions it should drop — and the full suite still passed. Nothing
    tested this path. It is inert today because no session in the corpus
    carries a split, and it stops being inert the moment held-out
    evaluation starts, which is exactly when a silently-inverted filter
    would train and validate on the same sessions — the one thing the
    split exists to prevent (docs/data-pipeline.md)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "sessions"
        self.jumps = generate.DEMO_JUMPS
        self.params = load_params()
        _write_session(self.root, "tr", self.jumps, seed=1, split="train")
        _write_session(self.root, "te", self.jumps, seed=2, split="test")

    def _names(self, split):
        agg = evaluate.eval_corpus(self.root, self.params, split=split)
        return sorted(r["session"] for r in agg["sessions"]
                      if r.get("split") == split or split == "all")

    def test_train_selects_only_train(self):
        agg = evaluate.eval_corpus(self.root, self.params, split="train")
        self.assertEqual(agg["n_sessions"], 1)
        self.assertEqual(
            [r["split"] for r in agg["sessions"] if r["split"] == "train"],
            ["train"])

    def test_test_selects_only_test(self):
        agg = evaluate.eval_corpus(self.root, self.params, split="test")
        self.assertEqual(agg["n_sessions"], 1)

    def test_all_selects_both(self):
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(agg["n_sessions"], 2)

    def test_the_two_splits_are_disjoint_and_cover_the_corpus(self):
        """The property that actually matters: train and test must partition
        the corpus. An inverted comparison breaks this even when each
        individual count still looks plausible."""
        tr = evaluate.eval_corpus(self.root, self.params, split="train")
        te = evaluate.eval_corpus(self.root, self.params, split="test")
        al = evaluate.eval_corpus(self.root, self.params, split="all")
        tr_names = {r["session"] for r in tr["sessions"]}
        te_names = {r["session"] for r in te["sessions"]}
        self.assertEqual(tr_names & te_names, set(), "splits overlap")
        self.assertEqual(tr["n_sessions"] + te["n_sessions"],
                         al["n_sessions"])


class LabelAdmissibilityTest(unittest.TestCase):
    """Degenerate ground truth must be REFUSED, not scored.

    Scoring three placeholder rows stamped with one timestamp returned
    'matched 0/3, missed 3, spurious 3'. docs/data-pipeline.md ('THE
    DIAGNOSTIC THAT MATTERS') and docs/session-card.md both instruct the
    reader that missed ≈ spurious means a video↔trace SYNC error and NOT a
    broken detector — so the tool emitted the signature of a fault it did not
    have, and the docs would have sent the reader to re-check a sync marker
    that was fine."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "sessions"
        self.jumps = generate.DEMO_JUMPS
        self.params = load_params()

    def test_duplicate_takeoff_times_are_refused(self):
        sess = _write_session(self.root, "20260815-190012", self.jumps)
        t0 = self.jumps[0][0]
        _relabel(sess, [t0, t0, t0])          # the jitter-check signature
        agg = evaluate.eval_corpus(self.root, self.params, split="all")

        self.assertEqual(agg["n_sessions"], 0, "must not be scored")
        self.assertEqual(agg["n_excluded"], 1)
        # None, never 0: a 0 in the match column is a claim about the detector.
        r = agg["sessions"][0]
        self.assertIsNone(r["matched"])
        self.assertIsNone(r["spurious"])
        self.assertTrue(r["excluded"])
        self.assertIn("same instant", " ".join(r["excluded"]))

    def test_refused_session_is_never_silently_dropped(self):
        """Dropping it would BE the bug: the report would show a clean empty
        corpus while a labeled session sat on disk unexplained."""
        sess = _write_session(self.root, "20260815-190012", self.jumps)
        t0 = self.jumps[0][0]
        _relabel(sess, [t0, t0, t0])
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(len(agg["sessions"]), 1)

        # ...and it must reach the PER-SESSION TABLE without --verbose, where
        # there is normally no table at all. Asserting only that the word
        # EXCLUDED appears somewhere is not enough: the warning paragraph
        # prints it too, so such a test survives deleting the table entirely
        # (caught by mutation-testing this very test). The row marker is
        # produced by _print_table and nothing else.
        out = _report(agg, verbose=False)
        self.assertIn("← EXCLUDED, not scored", out)
        self.assertIn("20260815-190012", out)
        self.assertIn("same instant", out)
        self.assertNotIn("matched 0/3", out)

    def test_refusal_does_not_contaminate_corpus_totals(self):
        """A refused session must contribute no zeros. Folding them in would
        drag the corpus detection rate down with inadmissible data."""
        _write_session(self.root, "good", self.jumps, seed=1)
        bad = _write_session(self.root, "bad", self.jumps, seed=2)
        t0 = self.jumps[0][0]
        _relabel(bad, [t0, t0, t0])

        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(agg["n_sessions"], 1)
        self.assertEqual(agg["n_true"], len(self.jumps))
        self.assertEqual(agg["matched"], len(self.jumps))
        self.assertEqual(agg["detection_rate"], 1.0)
        self.assertEqual(evaluate.summary_metrics(agg)["n_excluded"], 1)

    def test_labels_keyed_to_another_clock_are_refused(self):
        """Trace time is seconds-since-boot; labels converted from wall clock
        with the wrong anchor land nowhere near it. Every one is unmatchable,
        so scoring produces 'matched 0/N' and blames the detector for what is
        a clock error."""
        sess = _write_session(self.root, "20260815-190012", self.jumps)
        lo, hi = _trace_span(sess)
        _relabel(sess, [hi + 5000.0, hi + 5100.0, hi + 5200.0])
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(agg["n_sessions"], 0)
        self.assertIn("different clock", " ".join(agg["sessions"][0]["excluded"]))

    def test_one_stray_label_is_scored_but_reported(self):
        """ALL-outside is a clock error; ONE outside is a session with good
        jumps in it. Refusing the whole session would throw those away — but
        the stray lands in `missed` where it is indistinguishable from a
        detector failure, so it has to be named."""
        sess = _write_session(self.root, "20260815-190012", self.jumps)
        lo, hi = _trace_span(sess)
        _relabel(sess, [t0 for (t0, _) in self.jumps] + [hi + 5000.0])

        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(agg["n_sessions"], 1, "must still be scored")
        self.assertEqual(agg["out_of_range"], 1)
        self.assertEqual(agg["matched"], len(self.jumps))
        self.assertEqual(agg["missed"], 1)
        self.assertIn("outside their trace's time range", _report(agg))

    def test_trace_with_no_time_span_is_refused(self):
        """An interrupted `jump sync` leaves a truncated trace. Every label
        then scores as missed and the detector takes the blame for a
        file-transfer failure."""
        sess = _write_session(self.root, "20260815-190012", self.jumps)
        (sess / "trace.csv").write_text("t,mag\n0.0,1.0\n")
        _relabel(sess, [1.0, 2.0, 3.0])
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(agg["n_sessions"], 0)
        self.assertIn("no time span", " ".join(agg["sessions"][0]["excluded"]))

    def test_clean_labels_are_still_admitted(self):
        """The guard must not refuse good data — that would be a new silent
        failure pointing the other way."""
        _write_session(self.root, "20260805-a", self.jumps)
        agg = evaluate.eval_corpus(self.root, self.params, split="all")
        self.assertEqual(agg["n_excluded"], 0)
        self.assertEqual(agg["n_sessions"], 1)
        self.assertEqual(agg["matched"], len(self.jumps))
        self.assertNotIn("EXCLUDED", _report(agg, verbose=True))


if __name__ == "__main__":
    unittest.main()
