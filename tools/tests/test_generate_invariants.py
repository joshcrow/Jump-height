#!/usr/bin/env python3
"""The synthetic-session builder's shape, and the fixture every sim test grades against.

WHY THIS EXISTS
---------------
2026-09-07. `sim/generate.py` was one of the four modules the 2026-09-06/07
mutation campaign never triaged (F-31's table, `docs/audit-2026-08-22.md:358`:
20 mutants, "not run"). The recheck pass found 14 real survivors in it, and
they split into exactly the shape F-31 names for the other seven modules: a
handful of deliberately-chosen constants that no assertion ever touched.

Two things make this module's version worse than an ordinary coverage gap.

**1. It is one of TWO renderers of the same physics, and the duplication is
undeclared.** `sim/sensor_model.py`'s header says it "matches sim/generate.py's
contract so it is a drop-in richer generator", and three of its `SensorConfig`
defaults are byte-identical duplicates of numbers this module inlines:

  `sim/sensor_model.py:42`  pop_s     = 0.06  <-> `sim/generate.py:55`  int(0.06 * fs_hz)
  `sim/sensor_model.py:43`  landing_s = 0.03  <-> `sim/generate.py:65`  int(0.03 * fs_hz)
  `sim/sensor_model.py:40`  pop_g     = 1.5   <-> `sim/generate.py:56`  rng.uniform(1.0, 2.0)
                                                  (population mean exactly 1.5)

`tools/tests/test_sensor_model_provenance.py` pins all three — on the
`SensorConfig` side only. So the two renderers could disagree about the takeoff
and landing shape while both claim to render the same event, which is the drift
CLAUDE.md section 4 is about. This file is the other side of that duplication.
It also corrects that file's header on one point: it lists `pop_g` among the
fields with "no external source to check against", and the measured equality
above is an external source — an exact one, previously unstated.

`landing_g` is the near-miss in that set: `sim/generate.py:29` uses 5.0 and
`sim/sensor_model.py:41` uses 4.8, so they CANNOT be pinned as an equality.
What both can be pinned to is the range `sensor_model.py:41`'s own comment
cites -- Simons 2025, 4.2-5.5 g -- which the suite already enforces for
`SensorConfig.landing_g` and never enforced here. Measured: pristine 5.0 is
inside it, the campaign's 6.25 mutant is not, and at 6.25 the two renderers
disagree about landing physics by 1.45 g.

**2. The demo fixture certifies itself.** `DEMO_JUMPS` (`sim/generate.py:75-80`)
is the ground truth behind `tools/tests/test_seastate.py:245`,
`tools/tests/test_evaluate.py:73`, `sim/run.py:203` and
`tools/fake_device.py:110`. Both existing test sites take BOTH sides of their
assertions from the list -- test_seastate compares `ev.airtime_raw_s` against
`at` read from `DEMO_JUMPS`, test_evaluate derives its labels.csv truth heights
from the same tuples -- so every value in it can move and both sides move with
it. That is mistake (b) of last night's five, already present in the suite at
two sites. Measured: with the 'huge' airtime moved 2.0 -> 2.5 (+56 % on its
ground-truth height) all four jumps are still detected and every structural
invariant still holds, so invariants ALONE do not close this. What closes it is
running the fixture through the detector and asserting the four takeoff times,
airtimes and heights as LITERALS.

WHAT THE LIST HAS TO SATISFY, which is the part worth pinning
------------------------------------------------------------
The four jumps are not four arbitrary numbers; the `# small hop` / `# medium` /
`# big` / `# huge` labels assert a ladder that spans the detector's accepted
airtime band (`sim/detector.py:51-52`, 0.25-3.00 s) with margin at both ends,
and the heights are separated widely enough that a detector confusing two rungs
is visible. Both are asserted below as invariants, with the literal round-trip
alongside them for the mutations the invariants cannot see. Measured minimum
height gap 0.785 m; the campaign's 1.5 -> 1.875 mutant collapses the top gap to
0.593 m, flattening 'big' and 'huge' into two near-equal rungs.

THE 200 Hz GRID IS LOAD-BEARING FOR THIS FILE
---------------------------------------------
`int(0.06 * fs_hz)` and `max(1, int(0.03 * fs_hz))` are INTEGER truncations, so
the sample rate decides whether a change to either is observable at all. At
50 Hz -- the rate `tools/tests/test_evaluate.py:40` and `tools/fake_device.py:110`
render at -- `int(0.06*50) == int(0.075*50) == 3` and
`max(1, int(0.03*50)) == max(1, int(0.0375*50)) == 1`, so the same tests written
there would pass against a mutated module and prove nothing (CLAUDE.md rule 3:
a reading that did not happen is a finding). Everything here renders at the
200 Hz default, and `test_render_grid_is_200hz` fails loudly if that default
ever moves, rather than letting this file go quiet.

HOW TIGHTLY, MEASURED
---------------------
"large moves are caught" is not "the threshold is tested" — the campaign
header retracted exactly that overstatement mid-run, having declared a guard
well tested on two far-apart endpoints while a 25 % move survived. So each
band here was re-probed with a move far smaller than the campaign's 25 %, and
every one of these is KILLED (2026-09-07, this file alone):

  landing_g          5.0  -> 5.6     (+12 %)   out of the cited 4.2-5.5 band
  landing_g          5.0  -> 2.6     (-48 %)   the other direction: into the
                                               2.50 g landing gate, so the
                                               range AND the detectability
                                               assertions both fail
  airborne sigma     0.05 -> 0.06    (+20 %)
  spike jitter       0.5  -> 0.6     (+20 %)
  pop upper bound    2.0  -> 2.1     (+5 %)
  duration tail      3.0  -> 3.1     (+3 %)
  'huge' airtime     2.0  -> 2.05    (+2.5 %)
  'medium' takeoff   12.0 -> 12.05   (+0.4 %)

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import inspect
import statistics
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sim"))

import generate  # noqa: E402
from detector import Detector, height_for_airtime, load_params  # noqa: E402
from sensor_model import SensorConfig  # noqa: E402

# ---------------------------------------------------------------- literals
# Every expected value in this file is a literal. Deriving one from the
# constant it pins is mistake (b) of 2026-09-06: a probe written at
# `MIN_DPS + 1.0` moves with MIN_DPS, so raising MIN_DPS changed nothing the
# test could see.

FS_HZ = 200.0            # sim/generate.py:26 default; see the header
POP_S = 0.06             # sim/generate.py:55, == SensorConfig.pop_s
POP_SAMPLES = 12         # int(0.06 * 200)
LANDING_S = 0.03         # sim/generate.py:65, == SensorConfig.landing_s
SPIKE_SAMPLES = 6        # max(1, int(0.03 * 200))
POP_BAND = (1.0, 2.0)    # sim/generate.py:56 rng.uniform bounds
POP_MEAN = 1.5           # their population mean, == SensorConfig.pop_g
TAIL_S = 3.0             # sim/generate.py:39 default-duration tail

# Simons 2025's landing-spike range, as cited by sim/sensor_model.py:41 and
# sim/wing_model.py's header, and already applied to SensorConfig.landing_g
# by tools/tests/test_sensor_model_provenance.py:87.
SIMONS_LANDING_G = (4.2, 5.5)

# Copied from sim/detector.py:47-52. Pinned to config/params.json by
# tools/tests/test_params_parity.py; re-stated here as literals so the probes
# below cannot move with the thresholds they are measuring margin against.
FREEFALL_ENTER_G = 0.35
LANDING_THRESHOLD_G = 2.50
MIN_AIRTIME_S = 0.25
MAX_AIRTIME_S = 3.00
LANDING_SETTLE_S = 0.5
G = 9.80665

# sim/generate.py:75-80, as literals. See the header: both existing consumers
# read this list for BOTH sides of their assertions, so nothing pins it.
DEMO_JUMPS_LITERAL = [(5.0, 0.6), (12.0, 1.0), (20.0, 1.5), (30.0, 2.0)]
DEMO_TAKEOFFS = [5.0, 12.0, 20.0, 30.0]
DEMO_AIRTIMES = [0.6, 1.0, 1.5, 2.0]
# h = g*T^2/8 at g = 9.80665, to 4 dp. Hop to huge, spanning 0.25-3.00 s.
DEMO_HEIGHTS = [0.4413, 1.2258, 2.7581, 4.9033]
DEMO_SESSION_SAMPLES = 7000   # int((30.0 + 2.0 + 3.0) * 200)

# The single-jump probe fixture. One jump, chop switched off, so the riding
# baseline is 1 g plus the slow swell and the pop / free-fall / spike regions
# separate on absolute thresholds.
PROBE_JUMP = (2.0, 0.8)
PROBE_DURATION_S = 5.0
PROBE_I0 = 400   # int(2.0 * 200)
PROBE_I1 = 560   # int((2.0 + 0.8) * 200)
SEEDS = 400      # renders in 0.12 s total, measured

# Riding sits at 1.00 +- 0.05 g with chop off; a popped sample is at least
# 1.0 g above that. 1.5 g separates them with 0.45 g of margin on both sides.
POP_GATE_G = 1.5

# The pop rides ON TOP of the swell, so its amplitude is recovered
# DIFFERENTIALLY — window mean minus the mean of the same number of samples
# immediately before it — not by subtracting an analytic copy of
# sim/generate.py:47's swell term. The first draft of this file did subtract
# such a copy, and the swell mutations (0.05 -> 0.0625 and 0.3 -> 0.375, both
# already killed by test_sensor_model_provenance.py) then failed the POP
# assertions instead. A test that fails for a reason other than the one it
# names is no better evidence than mistake (c)'s test that passed for the
# wrong one. Differentially, the 0.3 Hz swell drifts at most 0.006 g across
# the 60 ms window, which the +-0.02 g band below absorbs; measured, the swell
# mutants now leave this class green and die in the file that owns them.
POP_BAND_TOL_G = 0.02


def _probe(seed: int):
    return generate.synth_session(
        [PROBE_JUMP], duration_s=PROBE_DURATION_S, fs_hz=FS_HZ, seed=seed,
        chop_g=0.0)


def _pop_indices(accel) -> list:
    """The samples before takeoff that carry the load-up bump."""
    return [i for i in range(PROBE_I0) if accel[i] > POP_GATE_G]


def _pop_amplitude(accel, idx) -> float:
    """Bump height above the riding level immediately preceding it."""
    ref = accel[idx[0] - len(idx):idx[0]]
    return statistics.fmean(accel[i] for i in idx) - statistics.fmean(ref)


def _run_detector(times, accel):
    det = Detector(load_params())
    return [ev for ev in (det.update(t, a) for t, a in zip(times, accel)) if ev]


class RenderGridAndThresholds(unittest.TestCase):
    """Controls. These do not pin generate.py; they prove this file can fail.

    Both integer truncations below are invisible at 50 Hz, and every margin
    below is measured against a detector threshold. If either premise moves,
    the rest of this file silently stops testing what it says it tests.
    """

    def test_render_grid_is_200hz(self) -> None:
        got = inspect.signature(generate.synth_session).parameters["fs_hz"].default
        self.assertEqual(
            got, 200.0,
            f"synth_session renders at {got} Hz by default, not 200. Every "
            "sample-count assertion in this file assumes 200 Hz, and at 50 Hz "
            "int(0.06*fs) == int(0.075*fs) == 3 and max(1,int(0.03*fs)) == 1 — "
            "the pop and spike probes would pass against a mutated module and "
            "prove nothing. Re-derive POP_SAMPLES/SPIKE_SAMPLES here, do not "
            "just relax the assertions.")

    def test_detector_thresholds_this_file_measures_against(self) -> None:
        p = load_params()
        self.assertEqual(
            (p.freefall_enter_g, p.landing_threshold_g, p.min_airtime_s,
             p.max_airtime_s, p.landing_settle_s, p.g),
            (0.35, 2.50, 0.25, 3.00, 0.5, 9.80665),
            "the detector thresholds this file quotes as literals have moved. "
            "The free-fall margin, the spike margin, the airtime band and the "
            "height ladder are all stated relative to them; re-derive them "
            "here in the same commit (CLAUDE.md section 4).")


class PopWindow(unittest.TestCase):
    """The load-up bump: sim/generate.py:55-58.

    Its duration is an exact duplicate of SensorConfig.pop_s and its amplitude
    distribution has SensorConfig.pop_g as its mean. Campaign survivors:
    0.06 -> 0.075 (12 samples -> 15) and uniform(1.0, 2.0) -> (1.0, 2.5)
    (mean 1.4772 -> 1.7158, measured over 400 seeds).
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.amplitudes = []
        cls.counts = []
        for seed in range(SEEDS):
            _, accel = _probe(seed)
            idx = _pop_indices(accel)
            cls.counts.append(len(idx))
            if idx:
                cls.amplitudes.append(_pop_amplitude(accel, idx))

    def test_pop_is_twelve_samples_ending_at_takeoff(self) -> None:
        """Positive control included: the window must also be contiguous and
        land immediately before takeoff, so a count of 12 elsewhere in the
        signal cannot satisfy this."""
        _, accel = _probe(0)
        idx = _pop_indices(accel)
        self.assertEqual(
            len(idx), POP_SAMPLES,
            f"the pop is {len(idx)} samples at 200 Hz, expected {POP_SAMPLES} "
            f"(= int({POP_S} * 200)). SensorConfig.pop_s is "
            f"{SensorConfig().pop_s} s and sim/generate.py:55 inlines the same "
            "number; one of the two moved and the renderers now disagree about "
            "the takeoff shape.")
        self.assertEqual(idx, list(range(PROBE_I0 - POP_SAMPLES, PROBE_I0)),
                         "the pop window must be contiguous and end on the "
                         f"sample before takeoff (i0={PROBE_I0}); got "
                         f"{idx[:3]}...{idx[-3:]}")
        self.assertEqual(set(self.counts), {POP_SAMPLES},
                         "the pop length must not vary with the seed — only "
                         "its amplitude is random; got "
                         f"{sorted(set(self.counts))} over {SEEDS} seeds")

    def test_pop_duration_equals_sensorconfig_pop_s(self) -> None:
        """The duplication, pinned from generate.py's side."""
        self.assertAlmostEqual(
            POP_SAMPLES / FS_HZ, 0.06, places=9,
            msg="the measured pop duration is not 0.06 s")
        self.assertEqual(
            SensorConfig().pop_s, 0.06,
            f"SensorConfig.pop_s is {SensorConfig().pop_s} s while "
            "sim/generate.py renders a 0.06 s pop. sensor_model.py's header "
            "claims it is a drop-in for generate.py; two renderers with "
            "different takeoff shapes are not drop-ins.")

    def test_every_pop_sits_inside_its_uniform_band(self) -> None:
        """Straddle on the upper bound, which is the mutated one.

        Measured over 400 seeds: pristine spans 1.0043-1.9993 g, so the
        +-0.02 g recovery band (see POP_BAND_TOL_G) holds. The campaign's
        uniform(1.0, 2.5) mutant reaches 2.4989 and a third of its seeds
        exceed 2.02, so one seed would separate them and 400 make it certain.
        """
        lo, hi = POP_BAND
        low, high = min(self.amplitudes), max(self.amplitudes)
        self.assertGreaterEqual(
            low, lo - POP_BAND_TOL_G,
            f"a rendered pop measured {low:.4f} g, below the {lo} g lower "
            f"bound of sim/generate.py:56's uniform. Measured over {SEEDS} "
            "seeds the pristine minimum is 1.0043.")
        self.assertLessEqual(
            high, hi + POP_BAND_TOL_G,
            f"a rendered pop measured {high:.4f} g, above the {hi} g upper "
            f"bound of sim/generate.py:56's uniform. Measured over {SEEDS} "
            "seeds the pristine maximum is 1.9993; the campaign's "
            "uniform(1.0, 2.5) mutant reaches 2.4989.")

    def test_mean_pop_is_sensorconfig_pop_g(self) -> None:
        """Absolute amplitude, not a ratio — mistake (d) of 2026-09-06 was a
        ratio assertion that a uniform scale change left at exactly 1.00.

        uniform(1.0, 2.0) has population mean 1.5, which is SensorConfig.pop_g
        exactly. Measured over 400 seeds: 1.4772 pristine (sampling SEM
        0.0144), 1.7158 for the uniform(1.0, 2.5) mutant. The band below is
        +-0.06 = +-4 SEM, so pristine passes with room and the mutant misses
        by 15 SEM.
        """
        got = statistics.fmean(self.amplitudes)
        self.assertAlmostEqual(
            got, POP_MEAN, delta=0.06,
            msg=f"mean rendered pop is {got:.4f} g over {SEEDS} seeds, not "
                f"{POP_MEAN} +- 0.06. SensorConfig.pop_g is "
                f"{SensorConfig().pop_g} g and is the mean of the distribution "
                "sim/generate.py:56 draws from; the two renderers now apply "
                "load-ups of different strength.")
        self.assertEqual(
            SensorConfig().pop_g, 1.5,
            "SensorConfig.pop_g moved off 1.5, which is the mean of "
            "sim/generate.py:56's uniform(1.0, 2.0). This equality is an "
            "external source for pop_g; test_sensor_model_provenance.py's "
            "header lists it as having none, and that is the line to fix.")


class LandingSpike(unittest.TestCase):
    """The landing spike: sim/generate.py:29 and 65-67.

    Campaign survivors: landing_g 5.0 -> 6.25 (out of the cited literature
    range), spike_len 0.03 -> 0.0375 (6 samples -> 7) and the jitter sigma
    0.5 -> 0.625.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.samples = []      # every above-gate sample, pooled
        cls.lengths = []      # how many, per seed
        cls.per_seed = []     # the spike window itself, per seed
        for seed in range(SEEDS):
            _, accel = _probe(seed)
            idx = [i for i in range(PROBE_I1, len(accel))
                   if accel[i] > LANDING_THRESHOLD_G]
            cls.lengths.append(len(idx))
            cls.samples.extend(accel[i] for i in idx)
            cls.per_seed.append(accel[PROBE_I1:PROBE_I1 + SPIKE_SAMPLES])

    def test_spike_is_six_samples_starting_at_touchdown(self) -> None:
        _, accel = _probe(0)
        idx = [i for i in range(PROBE_I1, len(accel))
               if accel[i] > LANDING_THRESHOLD_G]
        self.assertEqual(
            len(idx), SPIKE_SAMPLES,
            f"the landing spike is {len(idx)} samples above the "
            f"{LANDING_THRESHOLD_G} g gate at 200 Hz, expected "
            f"{SPIKE_SAMPLES} (= max(1, int({LANDING_S} * 200))). "
            f"SensorConfig.landing_s is {SensorConfig().landing_s} s and "
            "sim/generate.py:65 inlines the same number; the two renderers now "
            "feed a different number of above-threshold samples into the "
            "detector's landing decision.")
        self.assertEqual(
            idx, list(range(PROBE_I1, PROBE_I1 + SPIKE_SAMPLES)),
            f"the spike must start on the touchdown sample (i1={PROBE_I1}) and "
            f"be contiguous; got {idx}")
        self.assertEqual(set(self.lengths), {SPIKE_SAMPLES},
                         "the spike length must not vary with the seed; got "
                         f"{sorted(set(self.lengths))} over {SEEDS} seeds")

    def test_spike_duration_equals_sensorconfig_landing_s(self) -> None:
        self.assertAlmostEqual(
            SPIKE_SAMPLES / FS_HZ, 0.03, places=9,
            msg="the measured spike duration is not 0.03 s")
        self.assertEqual(
            SensorConfig().landing_s, 0.03,
            f"SensorConfig.landing_s is {SensorConfig().landing_s} s while "
            "sim/generate.py renders a 0.03 s spike. Same event, two "
            "durations, in two modules that claim to be interchangeable.")

    def test_spike_peak_sits_in_the_cited_literature_range(self) -> None:
        """The provenance sim/sensor_model.py:41 cites, applied to the twin.

        Checked twice: the declared default, and the value actually rendered.
        Measured mean spike 4.990 g pristine (400 seeds x 6 samples) against
        6.240 g for the 6.25 mutant.
        """
        lo, hi = SIMONS_LANDING_G
        default = inspect.signature(
            generate.synth_session).parameters["landing_g"].default
        self.assertTrue(
            lo <= default <= hi,
            f"synth_session's landing_g default is {default} g, outside the "
            f"{lo}-{hi} g range sim/sensor_model.py:41 cites from Simons 2025 "
            "for the SAME quantity — the range the suite already enforces for "
            "SensorConfig.landing_g. Either the value drifted or the citation "
            "is wrong, and in this repo a confident wrong citation is worse "
            "than none (CLAUDE.md rule 6).")
        mean = statistics.fmean(self.samples)
        self.assertTrue(
            lo <= mean <= hi,
            f"the rendered landing spike averages {mean:.3f} g, outside the "
            f"cited {lo}-{hi} g band. Pristine measures 4.990 g.")

    def test_every_rendered_landing_is_detectable(self) -> None:
        """Absolute margin over the landing gate, per jump — the reason the
        spike amplitude matters at all.

        Stated per SEED, on the strongest and the mean sample of each spike,
        NOT on the weakest sample pooled across seeds. A first draft did the
        latter (`min(all samples) > 3.0`) and it is not an invariant at all:
        3.0 g is 4 sigma below a 5.0 g peak at sigma 0.5, so across 400 seeds
        x 6 samples one sample lands under it 7 % of the time. It would have
        passed here and flaked in CI, which is CLAUDE.md rule 3 with the
        failure and the pass swapped. What the detector actually requires is
        ONE sample over landing_threshold_g=2.50 per landing. Measured over
        400 seeds: the weakest per-seed maximum is 4.847 g and the weakest
        per-seed mean 4.458 g, both ~7 sigma clear of the 3.5 g floor below.
        """
        weakest_peak = min(max(sp) for sp in self.per_seed)
        weakest_mean = min(statistics.fmean(sp) for sp in self.per_seed)
        self.assertGreater(
            weakest_peak, LANDING_THRESHOLD_G + 1.0,
            f"the weakest rendered landing peaks at {weakest_peak:.3f} g "
            f"against a {LANDING_THRESHOLD_G} g gate (sim/detector.py:49). "
            "Pristine measures 4.847 g. A spike under the gate means the "
            "detector never sees touchdown, so the jump is lost outright, not "
            "shortened — and a fixture that silently loses jumps makes every "
            "detection rate computed from it meaningless.")
        self.assertGreater(
            weakest_mean, LANDING_THRESHOLD_G + 1.0,
            f"the weakest rendered landing averages {weakest_mean:.3f} g "
            f"against a {LANDING_THRESHOLD_G} g gate; pristine measures "
            "4.458 g. Asserted alongside the peak so a single lucky sample "
            "over the gate cannot stand in for a landing spike.")

    def test_spike_jitter_is_snapshotted(self) -> None:
        """SNAPSHOT, and why it is one: sigma has no provenance.

        sim/generate.py:67's 0.5 g jitter has no external source and no
        SensorConfig twin (noise_g=0.02 is per-sample sensor noise applied
        everywhere, a different quantity). Its consequence never reaches a
        detector outcome either: analytically P(all 6 spike samples fall below
        the 2.50 g gate) moves 5.5e-40 -> 1.0e-27 at the campaign's 0.625
        mutant, and even sigma=2.0 only reaches 1.4e-6. So there is no
        invariant to assert and this is a pinned snapshot: pooled stdev
        measured 0.4954 pristine vs 0.6194 mutated, sampling error 0.0072, and
        the +-0.08 band below sits between them at 11 sampling errors from
        either edge. A deliberate change edits this line.
        """
        sd = statistics.pstdev(self.samples)
        self.assertAlmostEqual(
            sd, 0.5, delta=0.08,
            msg=f"landing-spike jitter measures sigma={sd:.4f} g over "
                f"{SEEDS} seeds, not 0.5 +- 0.08. sim/generate.py:67 was "
                "re-tuned without this snapshot changing; if that was "
                "deliberate, update it in the same commit.")


class FreeFallFill(unittest.TestCase):
    """The airborne stretch: sim/generate.py:60-62.

    The module docstring's whole free-fall claim is "|a| ~ 0 g plus a little
    sensor noise". Campaign survivor: sigma 0.05 -> 0.0625.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.samples = []
        for seed in range(SEEDS):
            _, accel = _probe(seed)
            cls.samples.extend(accel[PROBE_I0:PROBE_I1])

    def test_free_fall_stays_under_the_takeoff_gate(self) -> None:
        """The invariant: the fixture's free-fall must READ as free-fall.

        Absolute, not relative — every airborne sample below
        freefall_enter_g=0.35. Measured maximum over 400 seeds x 160 samples:
        0.2415 g (4.8 sigma out), so 0.35 holds with margin. This is the
        assertion that fails outright once sigma grows past ~0.10, where the
        crossing probability is already 0.38 and the fixture stops rendering
        free-fall at all.
        """
        worst = max(self.samples)
        self.assertLess(
            worst, FREEFALL_ENTER_G,
            f"an airborne sample read {worst:.4f} g, at or above the "
            f"{FREEFALL_ENTER_G} g free-fall gate (sim/detector.py:47, "
            "DECISION #41's most-argued threshold). The synthetic jump would "
            "drop out of AIRBORNE mid-flight, and every detection result "
            "rendered through this module changes meaning.")

    def test_airborne_noise_sigma_is_snapshotted(self) -> None:
        """SNAPSHOT, and why it is one: sigma has no provenance either.

        SensorConfig.noise_g=0.02 is a DIFFERENT quantity (per-sample sensor
        noise applied to the whole trace, not the airborne fill), so there is
        no equality to pin and no doc states the value. The stated reason for
        the snapshot is the margin above: at 0.05 the gate sits 7.0 sigma away
        and P(any airborne sample crossing it across DEMO_JUMPS) is 2.6e-9; at
        the campaign's 0.0625 it is 5.6 sigma and 2.2e-5; at 0.10 it is 0.38.
        The test above cannot see the first step of that walk, so the walk
        itself is pinned here.

        Estimator: for |N(0, s)| the mean is 0.7979*s. Measured 0.05017
        pristine vs 0.0627 mutated, sampling error 0.0004; the band is +-0.008,
        20 sampling errors from either edge.
        """
        sigma_hat = statistics.fmean(self.samples) / 0.7978845608028654
        self.assertAlmostEqual(
            sigma_hat, 0.05, delta=0.008,
            msg=f"the airborne noise floor measures sigma={sigma_hat:.5f} g "
                f"over {SEEDS} seeds, not 0.05 +- 0.008 (sim/generate.py:62). "
                "That is the margin between the fixture's free-fall and the "
                "0.35 g takeoff gate; if the change is deliberate, update "
                "this snapshot and the probabilities above in the same "
                "commit.")


class DurationTail(unittest.TestCase):
    """The default session tail: sim/generate.py:39, `max(t0 + at) + 3.0`."""

    def test_tail_after_the_last_landing_is_three_seconds(self) -> None:
        """Measured from the rendered output against a literal, on a jump list
        of this file's own so the expectation cannot move with DEMO_JUMPS."""
        times, _ = generate.synth_session([PROBE_JUMP], fs_hz=FS_HZ, seed=0)
        tail = len(times) / FS_HZ - (PROBE_JUMP[0] + PROBE_JUMP[1])
        self.assertAlmostEqual(
            tail, TAIL_S, delta=1.0 / FS_HZ,
            msg=f"the default duration leaves {tail:.3f} s after the last "
                f"landing, expected {TAIL_S} s. This is the only thing "
                "standing between the fixture and a silently truncated final "
                "jump (see the straddle below).")

    def test_tail_must_cover_the_final_landing_spike(self) -> None:
        """The straddle, and it is the load-bearing direction: just-under
        FAILS to detect, just-over passes.

        Measured: with the session ending exactly at the last touchdown the
        spike falls outside the array and only 3 of 4 jumps are found; one
        spike duration of tail (0.03 s) already restores 4 of 4. The positive
        control is in the same test, so a fixture that detected nothing at all
        could not be mistaken for a pass.
        """
        last = max(t0 + at for t0, at in DEMO_JUMPS_LITERAL)
        short = generate.synth_session(
            DEMO_JUMPS_LITERAL, duration_s=last, fs_hz=FS_HZ, seed=0,
            chop_g=0.0)
        ok = generate.synth_session(
            DEMO_JUMPS_LITERAL, duration_s=last + LANDING_S, fs_hz=FS_HZ,
            seed=0, chop_g=0.0)
        n_short = len(_run_detector(*short))
        n_ok = len(_run_detector(*ok))
        self.assertEqual(
            n_ok, 4,
            f"positive control: one spike duration ({LANDING_S} s) of tail "
            f"must be enough for all 4 jumps; got {n_ok}. Until this passes "
            "the negative case below proves nothing (mistake (c), "
            "2026-09-06).")
        self.assertEqual(
            n_short, 3,
            "a session truncated at the last touchdown must LOSE that jump "
            f"({n_short} detected, expected 3). If it now finds 4, the "
            "landing spike is being rendered before the touchdown sample and "
            "every airtime in this module is off by the spike length.")


class DemoFixtureInvariants(unittest.TestCase):
    """What DEMO_JUMPS has to satisfy, stated as invariants.

    These survive a legitimate re-tuning of the list; the round-trip class
    below catches the mutations these cannot see. Both are needed — measured:
    the 'huge' 2.0 -> 2.5 mutant passes every invariant in this class.
    """

    def setUp(self) -> None:
        self.jumps = list(generate.DEMO_JUMPS)
        self.heights = [height_for_airtime(at, G) for _, at in self.jumps]

    def test_four_jumps(self) -> None:
        self.assertEqual(
            len(self.jumps), 4,
            "DEMO_JUMPS is labelled small hop / medium / big / huge — four "
            f"rungs; got {len(self.jumps)}. sim/run.py:203 and "
            "tools/fake_device.py:110 both grade against it.")

    def test_every_airtime_is_inside_the_detectors_accepted_band(self) -> None:
        """With margin at BOTH ends, and the margins asserted absolutely.

        Probing only the ends of a range and generalising to the middle is
        mistake (a); this asserts each rung, and the round-trip class pins
        where each one sits.
        """
        for (t0, at), label in zip(self.jumps,
                                   ("small hop", "medium", "big", "huge")):
            self.assertGreater(
                at, MIN_AIRTIME_S + 0.1,
                f"the '{label}' jump at t0={t0} has airtime {at} s, too close "
                f"to the detector's {MIN_AIRTIME_S} s floor "
                "(sim/detector.py:51) for the fixture to be measuring "
                "detection rather than the rejection boundary.")
            self.assertLess(
                at, MAX_AIRTIME_S - 0.5,
                f"the '{label}' jump at t0={t0} has airtime {at} s, within "
                f"0.5 s of the detector's {MAX_AIRTIME_S} s ceiling "
                "(sim/detector.py:52). A fixture that close to the reject "
                "boundary stops being a detection test.")

    def test_takeoffs_and_airtimes_both_increase(self) -> None:
        t0s = [t0 for t0, _ in self.jumps]
        ats = [at for _, at in self.jumps]
        self.assertEqual(t0s, sorted(t0s),
                         f"takeoff times must be in order for the zip() in "
                         f"test_seastate.py:245 to pair truth with events: "
                         f"{t0s}")
        self.assertEqual(
            ats, sorted(ats),
            "airtimes must increase across the four labelled rungs (small "
            f"hop -> medium -> big -> huge): {ats}")
        self.assertEqual(len(set(ats)), 4,
                         f"the four rungs must be distinct airtimes: {ats}")

    def test_height_ladder_is_separated(self) -> None:
        """The ladder the labels claim, in metres, absolutely.

        Measured minimum consecutive gap 0.785 m. The campaign's 'big'
        1.5 -> 1.875 mutant leaves 'big' 0.593 m under 'huge', collapsing two
        rungs into one; the 0.70 m floor below sits between the two.
        """
        gaps = [self.heights[i + 1] - self.heights[i] for i in range(3)]
        self.assertTrue(
            all(gp > 0.70 for gp in gaps),
            "consecutive DEMO_JUMPS heights must differ by more than 0.70 m "
            "or the small/medium/big/huge ladder stops spanning the band it "
            f"exists to span. heights={[round(h, 4) for h in self.heights]} "
            f"gaps={[round(gp, 4) for gp in gaps]}")
        self.assertGreater(
            self.heights[-1] - self.heights[0], 4.0,
            "the fixture must span at least 4 m from hop to huge; measured "
            f"{self.heights[-1] - self.heights[0]:.3f} m.")

    def test_jumps_are_separated_by_more_than_landing_settle(self) -> None:
        """Each landing must fully settle before the next pop, or two jumps
        merge into one event. Measured minimum gap 6.31 s against a 0.5 s
        landing_settle_s."""
        for i in range(3):
            end = self.jumps[i][0] + self.jumps[i][1] + LANDING_S
            nxt = self.jumps[i + 1][0] - POP_S
            self.assertGreater(
                nxt - end, LANDING_SETTLE_S,
                f"jump {i} ends at {end:.2f} s and jump {i + 1}'s pop starts "
                f"at {nxt:.2f} s, less than landing_settle_s="
                f"{LANDING_SETTLE_S} s apart (sim/detector.py:50). The two "
                "would render as one event and the fixture would be grading "
                "against a jump count it does not contain.")


class DemoFixtureRoundTrip(unittest.TestCase):
    """The fixture through the detector, against literals.

    This is the class that closes the self-certification described in the
    header: expectations are the literals above, never `DEMO_JUMPS` itself.
    Measured pristine: 4 events at t0 = 5.0/12.0/20.0/30.0 s, raw airtimes
    0.6/1.0/1.5/2.0 s, truth heights 0.4413/1.2258/2.7581/4.9033 m.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.times, cls.accel = generate.synth_session(
            generate.DEMO_JUMPS, seed=0)
        cls.events = _run_detector(cls.times, cls.accel)

    def test_the_default_session_is_seven_thousand_samples(self) -> None:
        """Absolute length: 30.0 + 2.0 + 3.0 s at 200 Hz. Moves if any takeoff
        time, the last airtime, or the tail moves."""
        self.assertEqual(
            len(self.times), DEMO_SESSION_SAMPLES,
            f"the default demo session is {len(self.times)} samples, expected "
            f"{DEMO_SESSION_SAMPLES} (35.0 s at 200 Hz). tools/fake_device.py:110 "
            "replays this same list as a 50 Hz trace, so its length is a "
            "shipped artefact, not just a test fixture.")

    def test_all_four_jumps_are_detected(self) -> None:
        self.assertEqual(
            len(self.events), 4,
            f"the demo session yielded {len(self.events)} events, expected 4 "
            "— the fixture's whole purpose is four known jumps with exact "
            "ground truth.")

    def test_detected_takeoff_times_match_the_literals(self) -> None:
        got = [round(ev.takeoff_time_s, 4) for ev in self.events]
        self.assertEqual(
            len(got), 4, f"expected 4 events, got {got}")
        for want, have in zip(DEMO_TAKEOFFS, got):
            self.assertAlmostEqual(
                have, want, delta=0.02,
                msg=f"detected takeoff at {have} s, expected {want} s. "
                    f"DEMO_JUMPS' takeoff times are the fixture's lead-in and "
                    "spacing; nothing else in the suite reads them, because "
                    "test_seastate.py:245 takes both sides of its comparison "
                    f"from the same list. all detected: {got}")

    def test_detected_airtimes_match_the_literals(self) -> None:
        got = [round(ev.airtime_raw_s, 4) for ev in self.events]
        self.assertEqual(len(got), 4, f"expected 4 events, got {got}")
        for want, have in zip(DEMO_AIRTIMES, got):
            self.assertAlmostEqual(
                have, want, delta=0.02,
                msg=f"detected raw airtime {have} s, expected {want} s. "
                    f"all detected: {got}")

    def test_ground_truth_heights_match_the_literals(self) -> None:
        """Absolute heights in metres, from h = g*T^2/8 on the DETECTED
        airtimes — so this fails if the rendered fixture drifts, not only if
        the list is edited."""
        got = [height_for_airtime(ev.airtime_raw_s, G) for ev in self.events]
        self.assertEqual(len(got), 4, f"expected 4 events, got {got}")
        for want, have in zip(DEMO_HEIGHTS, got):
            self.assertAlmostEqual(
                have, want, delta=0.01,
                msg=f"the demo fixture rendered a {have:.4f} m jump where "
                    f"{want} m is expected (h = g*T^2/8, g={G}). The four "
                    "demo heights are 0.4413 / 1.2258 / 2.7581 / 4.9033 m and "
                    "no doc quotes them, so this file is the only place they "
                    f"are written down. all detected: "
                    f"{[round(h, 4) for h in got]}")

    def test_no_spurious_detections_in_the_riding_stretches(self) -> None:
        """Positive control for the class: the session must not merely produce
        four events, it must produce them at the four known times and nowhere
        else — 25 s of this session is ordinary riding."""
        windows = [(t0 - 0.2, t0 + at + 0.2) for t0, at in DEMO_JUMPS_LITERAL]
        for ev in self.events:
            self.assertTrue(
                any(lo <= ev.takeoff_time_s <= hi for lo, hi in windows),
                f"an event at t={ev.takeoff_time_s:.3f} s falls outside all "
                f"four known jump windows {windows}; the chop baseline is "
                "manufacturing detections.")


if __name__ == "__main__":
    unittest.main()
