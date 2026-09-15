"""Unit tests for tools/refit.py — the per-ride refit proposal tool
(docs/accuracy-plan.md; task: "TASK C — per-ride refit proposal").

Two layers, matching how tools/refit.py is actually used:
  * pure selection/verdict logic (`pick_best`, `leave_one_session_out`,
    `decide_verdict`) tested against hand-built DetectionSummary tables, so
    the tie-break and gating rules are checked independent of the detector;
  * end-to-end runs against synthetic sessions on disk (trace.csv +
    surfr.json, via sim/generate.synth_session), so the real
    sim/detector.py Detector, the real config/params.json, and the report
    renderer are all exercised together.

Every end-to-end assertion is anchored to a MEASURED fact (not assumed): with
config/params.json's real current values, sim.generate.DEMO_JUMPS is detected
as exactly 4 jumps (verified by running the detector once before writing this
file). A synthetic session whose surfr.json also says 4 therefore has a
before-count-error of 0 for that session, which cannot strictly improve — so
the corpus-level verdict is deterministically KEEP no matter what any other
session in the same corpus does. That property, not a fitted number, is what
the end-to-end tests check.

Run via ./tools/jump simtest, or directly:
    python3 -m pytest tools/tests/test_refit.py -q
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))
sys.path.insert(0, str(REPO / "tools"))

import generate  # noqa: E402
from detector import Params, load_params  # noqa: E402
import refit  # noqa: E402


def _write_trace(sess: Path, times, mag) -> None:
    with open(sess / "trace.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "mag"])
        for t, a in zip(times, mag):
            w.writerow([f"{t:.4f}", f"{a:.4f}"])


def _write_session(root: Path, name: str, jumps, surfr: Optional[dict],
                   seed: int = 1, duration_s: Optional[float] = None) -> Path:
    sess = root / name
    sess.mkdir(parents=True)
    times, mag = generate.synth_session(jumps, duration_s=duration_s, seed=seed, fs_hz=50.0)
    _write_trace(sess, times, mag)
    if surfr is not None:
        (sess / "surfr.json").write_text(json.dumps(surfr))
    return sess


def _hand_built_trace(segments, fs_hz: float = 50.0):
    """A noise-free |a| trace from (duration_s, level_g) segments — used where
    a test needs an exact, deterministic accel floor (generate.synth_session's
    chop noise makes threshold-crossing counts probabilistic instead)."""
    times, mag = [], []
    t, dt = 0.0, 1.0 / fs_hz
    for duration_s, level_g in segments:
        for _ in range(int(round(duration_s * fs_hz))):
            times.append(round(t, 4))
            mag.append(level_g)
            t += dt
    return times, mag


def _write_hand_built_session(root: Path, name: str, segments, surfr: Optional[dict]) -> Path:
    sess = root / name
    sess.mkdir(parents=True)
    times, mag = _hand_built_trace(segments)
    _write_trace(sess, times, mag)
    if surfr is not None:
        (sess / "surfr.json").write_text(json.dumps(surfr))
    return sess


# Riding baseline, and two jump shapes: a fully-ballistic one (near-0 g in the
# air -- detected at ANY freefall_enter_g grid value) and a "wing" one whose
# in-air floor (0.55 g) sits ABOVE the current config's 0.35 g gate but BELOW
# several grid values -- exactly the real, measured shape from
# docs/accuracy-plan.md ("mid-air load sits near 0.4-0.6 g" for a wing).
# min/max airtime bounds never bind for either shape at any grid point (both
# airtimes are ~1s, and the grid's tightest max is 3s / loosest min is 0.45s),
# so only freefall_enter_g decides these counts -- deterministically.
_BASE = (2.0, 1.0)
_BALLISTIC_JUMP = [(0.1, 1.8), (0.5, 0.05), (0.04, 3.0)]
_WING_JUMP = [(0.1, 1.8), (1.0, 0.55), (0.04, 3.0)]


# --------------------------------------------------------------------- grid

class GridTest(unittest.TestCase):
    def test_grid_size_and_values(self):
        base = load_params()
        grid = refit.build_grid(base)
        self.assertEqual(len(grid), 11 * 4 * 3)
        self.assertEqual(sorted({round(p.freefall_enter_g, 2) for p in grid}),
                          [round(0.30 + 0.05 * i, 2) for i in range(11)])
        self.assertEqual(sorted({p.max_airtime_s for p in grid}), [3.0, 4.0, 5.0, 6.0])
        self.assertEqual(sorted({p.min_airtime_s for p in grid}), [0.25, 0.35, 0.45])
        # No duplicate combos.
        self.assertEqual(len({refit.combo_key(p) for p in grid}), len(grid))

    def test_grid_holds_non_swept_fields_at_base(self):
        base = load_params()
        perturbed = refit.dataclasses.replace(base, landing_threshold_g=9.0,
                                              airtime_offset_s=0.5)
        grid = refit.build_grid(perturbed)
        self.assertTrue(all(p.landing_threshold_g == 9.0 for p in grid))
        self.assertTrue(all(p.airtime_offset_s == 0.5 for p in grid))

    def test_current_config_is_a_grid_point(self):
        # render_report's "before" column is a dict LOOKUP of combo_key(base)
        # in grid_results, not a separate detector run -- this must hold.
        base = load_params()
        grid = refit.build_grid(base)
        self.assertIn(refit.combo_key(base), {refit.combo_key(p) for p in grid})

    def test_an_off_grid_current_config_is_still_a_grid_point(self):
        # It holds for today's config/params.json by luck, not by
        # construction: 0.35 / 3 / 0.25 all happen to sit on the grid. Hand-
        # tune min_airtime_s to 0.30 -- something this tool exists to be
        # re-run after -- and the "before" lookup used to be a KeyError that
        # killed the whole run with a traceback instead of a report.
        off_grid = refit.dataclasses.replace(load_params(), min_airtime_s=0.30)
        grid = refit.build_grid(off_grid)
        self.assertEqual(len(grid), 11 * 4 * 3 + 1)
        self.assertEqual(refit.combo_key(grid[-1]), refit.combo_key(off_grid))
        self.assertEqual(len({refit.combo_key(p) for p in grid}), len(grid))


# --------------------------------------------------------------- discovery

class DiscoverSessionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_direct_children_with_trace_csv(self):
        _write_session(self.root, "a", generate.DEMO_JUMPS, {"jumps_total": 4}, seed=1)
        _write_session(self.root, "b", generate.DEMO_JUMPS, None, seed=2)
        sessions = refit.discover_sessions(self.root)
        self.assertEqual(sorted(s.name for s in sessions), ["a", "b"])
        by_name = {s.name: s for s in sessions}
        self.assertIsNotNone(by_name["a"].surfr)
        self.assertIsNone(by_name["b"].surfr)

    def test_nested_group_dir_is_not_descended_into(self):
        # jitter-check/<id>/trace.csv style nesting: the group dir itself has
        # no trace.csv, and this scanner is ONE level deep, deliberately (see
        # module docstring) -- unlike sim/evaluate.py's recursive walk.
        _write_session(self.root / "jitter-check", "20260101-000000",
                       generate.DEMO_JUMPS, {"jumps_total": 4})
        self.assertEqual(refit.discover_sessions(self.root), [])

    def test_missing_root_returns_empty_not_a_crash(self):
        self.assertEqual(refit.discover_sessions(self.root / "does-not-exist"), [])

    def test_malformed_surfr_json_is_none_not_a_crash(self):
        sess = self.root / "bad"
        sess.mkdir()
        times, mag = generate.synth_session(generate.DEMO_JUMPS, seed=1, fs_hz=50.0)
        _write_trace(sess, times, mag)
        (sess / "surfr.json").write_text("{not json")
        sessions = refit.discover_sessions(self.root)
        self.assertEqual(len(sessions), 1)
        self.assertIsNone(sessions[0].surfr)

    def test_a_file_named_like_a_session_is_skipped(self):
        (self.root / "not-a-dir.txt").write_text("x")
        self.assertEqual(refit.discover_sessions(self.root), [])

    def test_trace_is_parsed_once_and_reused_by_evaluate_grid(self):
        # "cache parsed traces": discover_sessions reads trace.csv exactly
        # once; deleting the file afterward proves evaluate_grid never
        # re-reads it.
        sess = _write_session(self.root, "a", generate.DEMO_JUMPS, {"jumps_total": 4})
        sessions = refit.discover_sessions(self.root)
        (sess / "trace.csv").unlink()
        grid = refit.build_grid(load_params())[:3]
        results = refit.evaluate_grid(sessions, grid)
        self.assertEqual(len(results["a"]), 3)


# ----------------------------------------------------------------- run_combo

class RunComboTest(unittest.TestCase):
    def _session(self, jumps, seed=1, duration_s=None):
        times, mag = generate.synth_session(jumps, duration_s=duration_s, seed=seed, fs_hz=50.0)
        return refit.SessionData(name="x", path=Path("."), times=times, mag=mag, surfr=None)

    def test_default_params_find_all_demo_jumps(self):
        base = load_params()
        summary = refit.run_combo(self._session(generate.DEMO_JUMPS), base)
        self.assertEqual(summary.count, len(generate.DEMO_JUMPS))
        self.assertIsNotNone(summary.best_height_m)
        self.assertIsNotNone(summary.longest_airtime_s)

    def test_tight_max_airtime_corrupts_the_longest_jump(self):
        # DEMO_JUMPS' longest true airtime is 2.0s. A 1.2s cap rejects that
        # flight at the cutoff (jump_detector.h's belt-and-suspenders exit)
        # and then re-latches onto the tail of the same free-fall/landing —
        # MEASURED to still leave the total count at 4, but the reported
        # longest airtime can no longer be anywhere near the true 2.0s.
        base = load_params()
        tight = refit.dataclasses.replace(base, max_airtime_s=1.2)
        summary = refit.run_combo(self._session(generate.DEMO_JUMPS), tight)
        self.assertLess(summary.longest_airtime_s, 1.3)

    def test_no_jumps_gives_none_not_zero_for_height_and_airtime(self):
        base = load_params()
        summary = refit.run_combo(self._session([], duration_s=5.0), base)
        self.assertEqual(summary.count, 0)
        self.assertIsNone(summary.best_height_m)
        self.assertIsNone(summary.longest_airtime_s)


# --------------------------------------------------------------- error funcs

class ErrorFunctionsTest(unittest.TestCase):
    def test_count_error(self):
        s = refit.DetectionSummary(count=5, best_height_m=1.0, longest_airtime_s=1.0)
        self.assertIsNone(refit.count_error(s, {}))
        self.assertIsNone(refit.count_error(s, None))
        self.assertEqual(refit.count_error(s, {"jumps_total": 8}), 3)

    def test_height_error_needs_a_detected_jump(self):
        no_jump = refit.DetectionSummary(count=0, best_height_m=None, longest_airtime_s=None)
        self.assertIsNone(refit.height_error_ft(no_jump, {"best_height_ft": 9.1}))
        one_jump = refit.DetectionSummary(count=1, best_height_m=1.0, longest_airtime_s=0.9)
        self.assertIsNone(refit.height_error_ft(one_jump, {}))
        got = refit.height_error_ft(one_jump, {"best_height_ft": 9.1})
        self.assertAlmostEqual(got, abs(1.0 * refit.M_TO_FT - 9.1), places=6)

    def test_airtime_error(self):
        s = refit.DetectionSummary(count=1, best_height_m=1.0, longest_airtime_s=2.0)
        self.assertIsNone(refit.airtime_error_s(s, {}))
        self.assertEqual(refit.airtime_error_s(s, {"max_airtime_s": 1.5}), 0.5)


# -------------------------------------------------------------------- fitting

class PickBestTest(unittest.TestCase):
    def setUp(self):
        self.base = Params()  # freefall_enter_g=0.35 max_airtime_s=3.0 min_airtime_s=0.25

    def _s(self, count):
        return refit.DetectionSummary(count=count, best_height_m=1.0, longest_airtime_s=1.0)

    def test_picks_minimum_summed_count_error(self):
        p_a = refit.dataclasses.replace(self.base, freefall_enter_g=0.40)
        p_b = refit.dataclasses.replace(self.base, freefall_enter_g=0.60)
        grid = [self.base, p_a, p_b]
        surfr = {"s1": {"jumps_total": 10}, "s2": {"jumps_total": 4}}
        grid_results = {
            "s1": {refit.combo_key(self.base): self._s(10),
                   refit.combo_key(p_a): self._s(9),
                   refit.combo_key(p_b): self._s(8)},
            "s2": {refit.combo_key(self.base): self._s(0),
                   refit.combo_key(p_a): self._s(4),
                   refit.combo_key(p_b): self._s(2)},
        }
        # base: |10-10|+|0-4|=4 ; p_a: |9-10|+|4-4|=1 ; p_b: |8-10|+|2-4|=4
        best, total = refit.pick_best(["s1", "s2"], grid, self.base, grid_results, surfr)
        self.assertEqual(refit.combo_key(best), refit.combo_key(p_a))
        self.assertEqual(total, 1)

    def test_tie_break_prefers_smallest_change_from_current_config(self):
        near = refit.dataclasses.replace(self.base, freefall_enter_g=0.40)
        far = refit.dataclasses.replace(self.base, freefall_enter_g=0.80)
        grid = [far, near]  # deliberately not in grid-natural order
        surfr = {"s1": {"jumps_total": 5}}
        grid_results = {"s1": {refit.combo_key(far): self._s(3), refit.combo_key(near): self._s(3)}}
        best, _ = refit.pick_best(["s1"], grid, self.base, grid_results, surfr)
        self.assertEqual(refit.combo_key(best), refit.combo_key(near))

    def test_tie_break_falls_back_to_grid_order(self):
        p1 = refit.dataclasses.replace(self.base, min_airtime_s=0.35)   # 1 step (of 0.10)
        p2 = refit.dataclasses.replace(self.base, max_airtime_s=4.0)    # 1 step (of 1.0) -- same normalized distance
        grid = [p1, p2]
        surfr = {"s1": {"jumps_total": 5}}
        grid_results = {"s1": {refit.combo_key(p1): self._s(3), refit.combo_key(p2): self._s(3)}}
        best, _ = refit.pick_best(["s1"], grid, self.base, grid_results, surfr)
        self.assertEqual(refit.combo_key(best), refit.combo_key(p1))


class LosoTest(unittest.TestCase):
    def setUp(self):
        self.base = Params()

    def _s(self, count):
        return refit.DetectionSummary(count=count, best_height_m=1.0, longest_airtime_s=1.0)

    def test_each_fold_never_looks_at_its_own_held_out_session(self):
        p_lo = refit.dataclasses.replace(self.base, freefall_enter_g=0.40)  # 1 step from base
        p_hi = refit.dataclasses.replace(self.base, freefall_enter_g=0.70)  # 7 steps from base
        grid = [p_lo, p_hi]
        surfr = {"s1": {"jumps_total": 10}, "s2": {"jumps_total": 10}, "s3": {"jumps_total": 2}}
        # p_lo: perfect for s1/s2, bad for s3. p_hi: perfect for s3, bad for s1/s2.
        grid_results = {
            "s1": {refit.combo_key(p_lo): self._s(10), refit.combo_key(p_hi): self._s(3)},
            "s2": {refit.combo_key(p_lo): self._s(10), refit.combo_key(p_hi): self._s(3)},
            "s3": {refit.combo_key(p_lo): self._s(9), refit.combo_key(p_hi): self._s(2)},
        }
        folds = {f.held_out: f for f in
                refit.leave_one_session_out(["s1", "s2", "s3"], grid, self.base, grid_results, surfr)}

        # Held out s1: fits on {s2, s3}. p_lo=0+7=7, p_hi=7+0=7 -- tied, broken
        # toward p_lo (closer to base). Its error on the held-out s1 is 0.
        self.assertEqual(refit.combo_key(folds["s1"].combo), refit.combo_key(p_lo))
        self.assertEqual(folds["s1"].held_out_count_error, 0)

        # Held out s3: fits on {s1, s2} ONLY -- both perfect under p_lo, so
        # p_lo wins outright even though p_hi is perfect for s3 itself. If the
        # fold had peeked at s3 it would have picked p_hi (error 0 there);
        # instead the held-out error is p_lo's real (bad) score on s3.
        self.assertEqual(refit.combo_key(folds["s3"].combo), refit.combo_key(p_lo))
        self.assertEqual(folds["s3"].held_out_count_error, 7)

    def test_single_count_session_cannot_be_held_out(self):
        grid = [self.base]
        surfr = {"only": {"jumps_total": 5}}
        grid_results = {"only": {refit.combo_key(self.base): self._s(5)}}
        folds = refit.leave_one_session_out(["only"], grid, self.base, grid_results, surfr)
        self.assertEqual(len(folds), 1)
        self.assertIsNone(folds[0].combo)
        self.assertIsNone(folds[0].held_out_count_error)
        self.assertIn("no OTHER", folds[0].note)


# --------------------------------------------------------------------- verdict

class DecideVerdictTest(unittest.TestCase):
    def _s(self, count, height_m=None):
        return refit.DetectionSummary(count=count, best_height_m=height_m,
                                      longest_airtime_s=1.0 if count else None)

    def test_propose_when_every_session_improves_and_height_does_not_worsen(self):
        surfr = {"a": {"jumps_total": 10, "best_height_ft": 5.0}, "b": {"jumps_total": 4}}
        before = {"a": self._s(7, 1.0), "b": self._s(2)}
        after = {"a": self._s(9, 1.5), "b": self._s(3)}
        verdict, _ = refit.decide_verdict(["a", "b"], before, after, surfr)
        self.assertEqual(verdict, "PROPOSE")

    def test_keep_when_one_session_does_not_strictly_improve(self):
        surfr = {"a": {"jumps_total": 10}, "b": {"jumps_total": 4}}
        before = {"a": self._s(10), "b": self._s(2)}   # a already perfect: 0 cannot improve
        after = {"a": self._s(10), "b": self._s(4)}
        verdict, reasons = refit.decide_verdict(["a", "b"], before, after, surfr)
        self.assertEqual(verdict, "KEEP")
        self.assertTrue(any("a" in r and "did not improve" in r for r in reasons))

    def test_keep_when_a_best_height_error_gets_worse(self):
        surfr = {"a": {"jumps_total": 10, "best_height_ft": 5.0}}
        before = {"a": self._s(8, 1.0)}
        after = {"a": self._s(10, 3.0)}   # count now perfect, but height error grew
        verdict, reasons = refit.decide_verdict(["a"], before, after, surfr)
        self.assertEqual(verdict, "KEEP")
        self.assertTrue(any("got worse" in r for r in reasons))

    def test_equal_height_error_is_not_a_regression(self):
        surfr = {"a": {"jumps_total": 10, "best_height_ft": 5.0}}
        before = {"a": self._s(8, 1.0)}
        after = {"a": self._s(10, 1.0)}   # count improves, height error unchanged
        verdict, _ = refit.decide_verdict(["a"], before, after, surfr)
        self.assertEqual(verdict, "PROPOSE")

    def test_nothing_to_check_is_vacuously_propose(self):
        # The pure gate has no sessions to fail on; run() special-cases the
        # "no count session at all" case separately (see EndToEndTest below)
        # rather than surfacing this vacuous truth as the corpus verdict.
        verdict, _ = refit.decide_verdict([], {}, {}, {})
        self.assertEqual(verdict, "PROPOSE")


# ------------------------------------------------------------------ end-to-end

class EndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "sessions"
        self.root.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_perfectly_matched_session_forces_keep(self):
        # MEASURED: config/params.json's real current values detect exactly
        # 4 jumps on generate.DEMO_JUMPS (seed=1). Surfr also says 4, so this
        # session's before-count-error is 0 and cannot strictly improve --
        # the corpus verdict is KEEP no matter what the other session does.
        _write_session(self.root, "perfect", generate.DEMO_JUMPS, {"jumps_total": 4}, seed=1)
        _write_session(self.root, "no-ground-truth", generate.DEMO_JUMPS, None, seed=2)
        report = refit.run(self.root, refit.DEFAULT_CONFIG)
        self.assertIn("# refit.md", report)
        self.assertIn("**KEEP**", report)
        self.assertIn("perfect: count error did not improve", report)
        self.assertIn("no-ground-truth", report)     # named as lacking ground truth
        self.assertIn("## Leave-one-session-out", report)
        self.assertIn("no OTHER count session to fit on", report)  # only 1 count session

    def test_free_parameter_budget_warning_appears_below_the_line(self):
        _write_session(self.root, "perfect", generate.DEMO_JUMPS, {"jumps_total": 4}, seed=1)
        report = refit.run(self.root, refit.DEFAULT_CONFIG)
        # 1 count session / 3 < 3 grid dimensions -- docs/accuracy-plan.md's
        # "sessions / 3" budget is exceeded and must be named, not silently applied.
        self.assertIn("FREE-PARAMETER BUDGET", report)
        self.assertIn("docs/accuracy-plan.md", report)

    def test_an_off_grid_config_produces_a_report_not_a_traceback(self):
        # End to end, through the real detector: a config whose min_airtime_s
        # (0.30) is not one of the three grid values still gets a before
        # column and a verdict.
        cfg_path = Path(self.tmp.name) / "params-off-grid.json"
        cfg = json.loads(refit.DEFAULT_CONFIG.read_text())
        cfg["detector"]["min_airtime_s"] = 0.30
        cfg_path.write_text(json.dumps(cfg))
        _write_session(self.root, "perfect", generate.DEMO_JUMPS, {"jumps_total": 4}, seed=1)
        report = refit.run(self.root, cfg_path)
        self.assertIn("# refit.md", report)
        self.assertIn("| min_airtime_s | 0.3 |", report)
        self.assertRegex(report, r"\*\*(PROPOSE|KEEP)\*\*")

    def test_empty_corpus_does_not_crash_and_keeps(self):
        report = refit.run(self.root, refit.DEFAULT_CONFIG)
        self.assertIn("0 session(s) under scan", report)
        self.assertIn("**KEEP**", report)
        self.assertIn("no count session", report)

    def test_main_writes_the_report_file(self):
        _write_session(self.root, "perfect", generate.DEMO_JUMPS, {"jumps_total": 4}, seed=1)
        out = Path(self.tmp.name) / "refit.md"
        code = refit.main(["--root", str(self.root), "--config", str(refit.DEFAULT_CONFIG),
                           "--out", str(out)])
        self.assertEqual(code, 0)
        self.assertTrue(out.exists())
        self.assertIn("# refit.md", out.read_text())

    def test_no_write_flag_skips_the_file(self):
        _write_session(self.root, "perfect", generate.DEMO_JUMPS, {"jumps_total": 4}, seed=1)
        out = Path(self.tmp.name) / "should-not-exist.md"
        code = refit.main(["--root", str(self.root), "--config", str(refit.DEFAULT_CONFIG),
                           "--out", str(out), "--no-write"])
        self.assertEqual(code, 0)
        self.assertFalse(out.exists())

    def test_a_wing_style_jump_missed_by_the_current_gate_can_produce_a_propose(self):
        # "short_partial": one fully-ballistic jump (detected at any gate) plus
        # one "wing" jump whose 0.55 g floor is ABOVE the current 0.35 g gate,
        # so the current config only finds 1 of the 2 Surfr jumps.
        # "wing_only": just the wing jump; current config finds 0 of 1.
        # Both are measured (above) to reach 0 error once freefall_enter_g
        # rises to >= 0.60 g, with max/min airtime unaffected either way --
        # so count error improves on EVERY count session and nothing about
        # best-height is even present to gate on. PROPOSE is the only
        # possible outcome, MEASURED, not asserted from theory alone.
        _write_hand_built_session(
            self.root, "short_partial",
            [_BASE] + _BALLISTIC_JUMP + [_BASE] + _WING_JUMP + [_BASE],
            {"jumps_total": 2})
        _write_hand_built_session(
            self.root, "wing_only", [_BASE] + _WING_JUMP + [_BASE], {"jumps_total": 1})
        report = refit.run(self.root, refit.DEFAULT_CONFIG)
        self.assertIn("**PROPOSE**", report)
        self.assertIn("| freefall_enter_g | 0.35 | 0.6 |", report)
        # and the per-session table shows the actual before/after counts:
        self.assertIn("| short_partial | 2 | 1 | 1 | 2 | 0 |", report)
        self.assertIn("| wing_only | 1 | 0 | 1 | 1 | 0 |", report)


if __name__ == "__main__":
    unittest.main()
