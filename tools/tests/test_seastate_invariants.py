#!/usr/bin/env python3
"""sim/seastate.py's chop and slap model, pinned to its own documented claims.

WHY THIS EXISTS
---------------
2026-09-07. `sim/seastate.py` was one of the three modules the overnight
mutation campaign (`tools/mutation_campaign.py`, 333 mutants / 11 modules
against a then-249-test suite) reached but nobody triaged — see
`docs/audit-2026-08-22.md` F-31, whose one finding is stated once: **the
suite did not read its own constants.** 23 mutants survived here. After
separating F-31's three untestable noise classes (numerically-equivalent
guards, boundary-equality flips on a FLOAT threshold, enum relabelling), 17
were real, reachable gaps. This file closes them.

THE WORST ONE, because it is overnight mistake (c) — a negative test passing
for the wrong reason — at module scale. `sim/seastate.py:146`'s
`while True:` is board_slap's Poisson arrival loop. Flip it to `while
False:` and **every board-slap event disappears**: measured on
board_slap(rate, 6.0, 60.0, seed=0), max |a| falls 6.928 g -> 1.071 g at
rate=2.0 and the count of samples above 2 g falls 579 -> 0. All 64
`TestBoardSlapAlone` cases in test_seastate.py stay green, plus the slap
half of TestCombinedNoise and TestRealJumpOnNoisyBackground, because they
assert ZERO detected jumps — deleting the spikes only makes the stream
cleaner. Structure-borne board slap is the ONE false-positive risk
research.md §6 actually flags (`git show archive/docs-2026-08-23:docs/research.md`),
and the reason this module exists beyond chop. It could be switched off
silently.

The rest, by what they break:

  * `:83` the `H / 2.0` in `a_orb_ms2 = (2.0*pi/T)**2 * (H/2.0)` — 2.0->2.5
    drops the Airy amplitude 20 % (0.06709 g -> 0.05368 g at H=0.3/T=3),
    making the module's OWN worked example (`:61-65`) false. E14/E15's
    published venue error bars — flat +/-6.5 cm, typical ocean +/-9, 25 kt
    sound +/-14, quoted to the rider in docs/session-card.md — come out of
    this amplitude.
  * `:89` the `2.0*pi` inside the sine — 2.0->2.5 gives the wave a 2.4 s
    period when the caller asked for 3.0 s. RESULTS.md's E15 conclusion is
    that the mechanism is "wavelength, not height", and every venue row is
    identified by its period.
  * `:86` `rng.uniform(0.0, 2.0*pi)` — 2.0->2.5 folds the first quadrant
    twice, so P(phase < pi/2) goes 0.2571 -> 0.3963 against a uniform 0.25.
    The docstring (`:71-72`) claims phase uniformity is what gives seed
    variety.
  * `:103` `spike_ms = (10.0, 40.0)` and `:154` the `/1000.0` ms->s
    conversion — the module claims "10-40 ms transients" twice (`:26`,
    `:113`) and these three numbers are the only thing making that true.
    Measured over 812 non-overlapping spikes: `/1250.0` moves the width band
    to 5-30 ms, `40.0->50.0` moves the ceiling to 45+ ms.
  * `:104` `baseline_dither_g = 0.02` and `:57` `dither_g = 0.01` — the
    noise floors under every chop sweep and every E10/E14 exposure hour.
    Each survived a 25 % rise.
  * `:155` `0.25` per-spike amplitude jitter (docstring `:113-114`:
    "jittered +/- ~0.25 g") and `:159` `0.15` within-spike sample noise.
  * `:172` `low_freq_tau_s = 0.05` — the AR(1)/Ornstein-Uhlenbeck time
    constant that is the module's stated reason (`:176-190`) for not using
    i.i.d. Gaussian: it is the ONLY knob deciding whether handling_chatter
    can sustain a multi-sample low excursion. 0.05 -> 0.0625 moves the
    longest sub-0.35 g run from 15 samples (75 ms, just UNDER the
    detector's freefall_confirm_s) to 16 (80 ms, exactly AT it), while rms
    barely moves (ratio 0.9986) — a variance assertion misses it.
  * Four input guards whose EQUALITY half nothing read: `:131` `fs_hz <= 0`
    (at exactly 0.0 the mutant returns a SILENTLY EMPTY stream instead of
    raising — CLAUDE.md rule 3 verbatim), `:241` the `or` in superpose's
    length check (accepts a 3 s timebase superposed onto a 6 s one), `:144`
    `rate > 0.0` (rate=0.0 is how a sweep asks for "baseline only"; the
    mutant crashes on expovariate(0.0)), `:77` `T <= 0.0`.

DELIBERATELY NOT HERE
---------------------
`:48`'s `G = 9.80665` came out of the same campaign and is ALREADY pinned,
in all seven places gravity is written out, by
`tools/tests/test_physical_constants.py::GravityIsOneNumber` (which names
`sim/seastate.py:48` explicitly, and kills 9.80665 -> 12.2583125 there).
Re-testing it here would only add a second place to update. Measured
honestly: the Airy amplitudes below ARE quoted in g, so they divide by G and
three of them do fail on that mutation too — that is a side effect of the
unit, not a pin, and `test_physical_constants.py` remains the one place
gravity is checked. Also absent: the boundary-equality flips on genuinely
FLOAT thresholds and the numerically-equivalent guards, per F-31.

HOW THE ASSERTIONS ARE BUILT — each rule below is an overnight mistake:
  (a) every boundary is STRADDLED: just-over must fail, just-under must
      pass, and where a probe pins a tolerance the moved-constant values are
      asserted to fall OUTSIDE it in both directions.
  (b) every expected value is a LITERAL. Nothing here reads the constant it
      pins in order to compute what it expects.
  (c) every negative test carries a positive control proving the fixture is
      otherwise valid.
  (d) absolute values, not only ratios — the spike-amplitude spreads move
      the extremes while leaving rms flat, so rms alone would miss them.
  (e) provenance is cited as file:line, and the two snapshot-only pins say
      in-line WHY no invariant was available.

Every stream below is seeded and therefore deterministic; the measured
numbers in the comments are from this worktree on CPython 3.14.2.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import inspect
import json
import math
import statistics
import sys
import unittest
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sim"))

import seastate  # noqa: E402  (path insert must come first)

PARAMS_JSON = REPO / "config" / "params.json"

# --------------------------------------------------------------- literals
#
# Every number in this block is written out by hand. Deriving one from
# seastate.py's own constants is overnight mistake (b): a probe at
# `MIN_DPS + 1.0` moves with MIN_DPS, so raising it changed nothing the test
# could see.

FS_HZ = 200.0                 # "All three return ... at 200 Hz" (seastate.py:35)
SAMPLE_MS = 5.0               # 1000 / 200 Hz

# Airy peak orbital acceleration, (2*pi/T)**2 * (H/2) / g, in g. The middle
# row is the module's own fully worked example (seastate.py:61-65,
# "(2*pi/3)**2 * 0.15 / 9.80665 = 0.0671 g") and research.md §6's cited
# "~0.07 g for typical wind chop". The outer two span 15x in amplitude so
# that no single scale error can pass, and (0.8, 1.8) is test_seastate.py's
# deliberate adversarial edge (CHOP_CASES' last row).
AIRY_AMPLITUDE_G: Tuple[Tuple[float, float, float], ...] = (
    (0.1, 2.5, 0.0322),
    (0.3, 3.0, 0.0671),
    (0.8, 1.8, 0.4970),
)
AIRY_AMPLITUDE_TOL = 0.0003   # measured error vs these literals: <= 6e-6

# The documented spike band, seastate.py:26 and :113 ("10-40 ms"), as it
# survives 200 Hz sampling: `int(dur_s * fs_hz)` truncates a uniform
# 10-40 ms draw to whole samples, i.e. 5 ms steps, so the realisable widths
# are {10, 15, 20, 25, 30, 35} ms and 35 — not 40 — is the honest ceiling.
SPIKE_MS_BAND = (10.0, 40.0)
SPIKE_WIDTH_MIN_MS = 10.0
SPIKE_WIDTH_MAX_MS = 35.0
SPIKE_WIDTH_MEAN_MS = 22.5    # mean of {10,15,20,25,30,35}, each 1/6
SPIKE_WIDTH_MEAN_TOL = 0.7    # measured 22.18 over 812 spikes

# Spike amplitude structure. 0.25 g is documented (seastate.py:113-114,
# "jittered +/- ~0.25 g"); 0.15 g is not documented anywhere, see the
# snapshot note above its test.
SPIKE_AMP_JITTER_G = 0.25
SPIKE_AMP_JITTER_TOL = 0.035  # measured 0.2557 over 812 spikes
SPIKE_SAMPLE_NOISE_G = 0.15
SPIKE_SAMPLE_NOISE_TOL = 0.008  # measured 0.1520, ~600 dof

# Dither floors, seastate.py:104 and :57.
SLAP_BASELINE_DITHER_G = 0.02
SLAP_BASELINE_DITHER_TOL = 0.0008   # measured rms 0.02002 over 6000 samples
CHOP_DITHER_G = 0.01
CHOP_DITHER_TOL = 0.0004            # measured rms 0.01010 over 6000 samples

# handling_chatter's AR(1) structure. For deviation d = x + h with x an
# AR(1) of stationary variance frac*amp^2 and h white with (1-frac)*amp^2,
# the lag-1 autocorrelation of d is exactly rho * frac, where
# rho = exp(-dt/tau). With the module's defaults (frac=0.4, tau=0.05 s,
# dt=1/200 s) that is 0.4 * exp(-0.005/0.05) = 0.361935. Written out:
CHATTER_LAG1 = 0.361935
CHATTER_LAG1_TOL = 0.003      # measured 0.36106 over 30 seeds x 60 s
CHATTER_TAU_S = 0.05
CHATTER_LOW_FREQ_FRACTION = 0.4
CHATTER_LAG1_AMP = 0.2        # small enough that seastate's >0 clip never bites
CHATTER_SEEDS = range(30)

FREEFALL_GATE_G = 0.35        # detector.freefall_g, the gate chatter must not fake
FREEFALL_CONFIRM_SAMPLES = 16  # 0.08 s * 200 Hz, both read from params.json below


# --------------------------------------------------------------- helpers

def rms_dev(accel: Sequence[float], base: float = 1.0) -> float:
    return math.sqrt(sum((x - base) ** 2 for x in accel) / len(accel))


def hot_samples(accel: Sequence[float], thresh: float) -> int:
    return sum(1 for x in accel if x > thresh)


def runs_above(accel: Sequence[float], thresh: float) -> List[List[float]]:
    """Consecutive samples above `thresh`, as a list of runs."""
    out: List[List[float]] = []
    i, n = 0, len(accel)
    while i < n:
        if accel[i] > thresh:
            j = i
            while j < n and accel[j] > thresh:
                j += 1
            out.append(list(accel[i:j]))
            i = j
        else:
            i += 1
    return out


def longest_run_below(accel: Sequence[float], thresh: float) -> int:
    best = run = 0
    for x in accel:
        if x < thresh:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return best


# One 4000 s slap stream per seed, at rate=0.05 so arrivals are ~20 s apart
# and spikes essentially never merge (verified: across seeds 7-10 every run
# is 2-7 samples, none longer). A first probe at rate=1.0 contaminated the
# width distribution with merged spikes before this was re-measured.
_SPIKE_SEEDS = (7, 8, 9, 10)
_SPIKE_RATE = 0.05
_SPIKE_DURATION_S = 4000.0
_SPIKE_CACHE: Dict[Tuple[float, float], List[List[float]]] = {}


def spike_runs() -> List[List[List[float]]]:
    """Per-seed lists of spike runs (samples above 3 g) from the slap streams.

    Cached: four 800,000-sample streams, built once for the whole file.
    """
    key = (_SPIKE_RATE, _SPIKE_DURATION_S)
    if key not in _SPIKE_CACHE:
        per_seed = []
        for seed in _SPIKE_SEEDS:
            _, accel = seastate.board_slap(
                _SPIKE_RATE, 6.0, _SPIKE_DURATION_S, seed=seed,
                baseline_dither_g=0.0)
            per_seed.append(runs_above(accel, 3.0))
        _SPIKE_CACHE[key] = per_seed  # type: ignore[assignment]
    return _SPIKE_CACHE[key]  # type: ignore[return-value]


def flat_spike_runs() -> List[List[float]]:
    return [run for per_seed in spike_runs() for run in per_seed]


def chatter_lag1(seeds: Sequence[int], tau: float | None = None,
                 amp: float = CHATTER_LAG1_AMP, duration_s: float = 60.0) -> float:
    """Pooled lag-1 autocorrelation of (accel - 1 g) across seeds."""
    num = den = 0.0
    for seed in seeds:
        kw = {} if tau is None else {"low_freq_tau_s": tau}
        _, accel = seastate.handling_chatter(amp, duration_s, seed=seed, **kw)
        dev = [x - 1.0 for x in accel]
        mean = statistics.fmean(dev)
        dev = [x - mean for x in dev]
        num += sum(dev[i] * dev[i + 1] for i in range(len(dev) - 1))
        den += sum(x * x for x in dev)
    return num / den


def chatter_longest_low_run(seeds: Sequence[int], tau: float | None = None,
                            amp: float = 0.5, duration_s: float = 60.0) -> int:
    best = 0
    for seed in seeds:
        kw = {} if tau is None else {"low_freq_tau_s": tau}
        _, accel = seastate.handling_chatter(amp, duration_s, seed=seed, **kw)
        best = max(best, longest_run_below(accel, FREEFALL_GATE_G))
    return best


def recover_phase(accel: Sequence[float], amp_g: float, omega: float,
                  dt: float) -> float:
    """The wave's starting phase, read back out of a dither-free stream.

    accel[0] and accel[1] give sin(phase) and sin(omega*dt + phase); the
    addition formula turns that pair into cos(phase), and atan2 resolves the
    quadrant. Proven against hand-built streams of known phase in
    PhaseIsUniformOnTheCircle.test_the_recovery_itself_is_sound, so a failure
    below is seastate's phase and not this arithmetic (mistake (c)).
    """
    s0 = (accel[0] - 1.0) / amp_g
    s1 = (accel[1] - 1.0) / amp_g
    c0 = (s1 - math.cos(omega * dt) * s0) / math.sin(omega * dt)
    return math.atan2(s0, c0) % (2.0 * math.pi)


# ------------------------------------------------- board_slap: the arrivals

class PoissonArrivalsActuallyHappen(unittest.TestCase):
    """seastate.py:146 — `while True:` in board_slap's arrival loop.

    The highest-value gap in the module: with the loop dead, the generator
    degenerates to pure 1 g dither and every zero-jump assertion in
    test_seastate.py still passes. So the spikes get asserted here, by their
    absolute magnitude and their count, and the count gets checked against
    the Poisson intensity the docstring (:111-112) names.
    """

    def test_slaps_reach_their_requested_peak(self) -> None:
        _, accel = seastate.board_slap(2.0, 6.0, 60.0, seed=0)
        self.assertEqual(len(accel), 12000)
        self.assertGreater(
            max(accel), 5.0,
            f"board_slap(rate=2.0, peak=6.0, 60 s, seed=0) peaked at "
            f"{max(accel):.4f} g. It must reach its requested 6 g peak "
            "(measured 6.928 g); a baseline-only stream peaks at 1.071 g, "
            "which is what seastate.py:146's arrival loop going dead looks "
            "like. Every TestBoardSlapAlone case stays green in that state.")

    def test_the_hot_sample_count_is_not_zero(self) -> None:
        _, accel = seastate.board_slap(2.0, 6.0, 60.0, seed=0)
        hot = hot_samples(accel, 2.0)
        self.assertGreaterEqual(
            hot, 400,
            f"only {hot} of 12000 samples exceed 2 g (measured 579). "
            "Structure-borne slap is the one false-positive regime "
            "research.md §6 flags; a clean stream is not a passing test.")

    def test_hot_sample_count_tracks_the_arrival_rate(self) -> None:
        """Positive control for the two assertions above (mistake (c)).

        Sensitive to ARRIVALS, not to the baseline: the same call at
        rate=0.05 must be nearly empty. Measured 19, 110, 579, 1302.
        """
        counts = {}
        for rate in (0.05, 0.5, 2.0, 5.0):
            _, accel = seastate.board_slap(rate, 6.0, 60.0, seed=0)
            counts[rate] = hot_samples(accel, 2.0)
        self.assertLess(
            counts[0.05], 100,
            f"rate=0.05 over 60 s should land ~3 spikes, got {counts[0.05]} "
            "hot samples. If this is large the >2 g count is measuring the "
            "baseline dither, not arrivals, and the assertions above pass "
            "for the wrong reason.")
        self.assertLess(counts[0.05], counts[0.5])
        self.assertLess(counts[0.5], counts[2.0])
        self.assertLess(counts[2.0], counts[5.0])

    def test_arrival_count_matches_the_poisson_intensity(self) -> None:
        """seastate.py:111-112 claims a Poisson process at `rate` events/s.

        4 seeds x 4000 s x 0.05 events/s = 200 expected per seed, 800 total;
        3-sigma on 800 is +/-85. Measured 812 (211/203/207/191). A Poisson
        process with zero events is not one.
        """
        per_seed = [len(runs) for runs in spike_runs()]
        total = sum(per_seed)
        self.assertGreaterEqual(
            total, 715,
            f"expected ~800 slap events across {len(_SPIKE_SEEDS)} seeds of "
            f"{_SPIKE_DURATION_S:.0f} s at rate={_SPIKE_RATE}, counted "
            f"{total} ({per_seed}).")
        self.assertLessEqual(total, 885, f"counted {total} events, expected ~800")
        for seed, count in zip(_SPIKE_SEEDS, per_seed):
            self.assertGreaterEqual(count, 158, f"seed {seed}: {count} events, expected ~200")
            self.assertLessEqual(count, 242, f"seed {seed}: {count} events, expected ~200")

    def test_rate_zero_is_a_clean_baseline_not_a_crash(self) -> None:
        """seastate.py:144 — `if rate > 0.0:`, at exactly 0.0.

        rate=0.0 is how a sweep asks for "baseline only" (the control case
        in sim/experiments/e10_seastate_soak.py's
        `lambda d, s: board_slap(rate, peak, d, seed=s)`). Mutating the
        guard to `>=` reaches random.expovariate(0.0) and raises
        ZeroDivisionError, removing a capability the original supports.
        The literal 0.0 is the whole point: rate=0.05 passes either way.
        """
        _, accel = seastate.board_slap(0.0, 5.0, 5.0, seed=0)
        self.assertEqual(len(accel), 1000)
        self.assertLess(
            max(accel), 1.2,
            f"rate=0.0 must produce no spikes at all; peaked at "
            f"{max(accel):.4f} g (measured 1.0714).")
        # Positive control: the same call WITH arrivals does spike, so the
        # assertion above is not vacuous.
        _, hot = seastate.board_slap(2.0, 5.0, 5.0, seed=0)
        self.assertGreater(max(hot), 3.0, "rate=2.0 must spike (measured 5.682 g)")


# ------------------------------------------------- board_slap: spike shape

class SpikesHonourTheDocumentedBand(unittest.TestCase):
    """seastate.py:103 `spike_ms = (10.0, 40.0)` and :154's `/1000.0`.

    The module claims "10-40 ms transients" twice (:26, :113) and
    sim/experiments/e16_edge_timing.py:16 independently uses the same band
    for landing cushions. Spike DWELL decides whether the detector's landing
    gate sees a slap at all, so these are the numbers that make the claim
    true. :154 is a unit conversion — the class of error that stays
    invisible until something asserts the unit.
    """

    def test_measured_widths_span_10_to_35_ms(self) -> None:
        widths = [len(run) * SAMPLE_MS for run in flat_spike_runs()]
        self.assertGreater(len(widths), 700, "no spikes to measure")
        self.assertEqual(
            min(widths), SPIKE_WIDTH_MIN_MS,
            f"shortest slap is {min(widths):.1f} ms, documented floor is "
            f"{SPIKE_WIDTH_MIN_MS:.1f} ms. `/1250.0` in place of :154's "
            "`/1000.0` measures 5.0 ms here.")
        self.assertEqual(
            max(widths), SPIKE_WIDTH_MAX_MS,
            f"longest slap is {max(widths):.1f} ms; at 200 Hz a uniform "
            "10-40 ms draw truncates to a 35 ms ceiling. `spike_ms` "
            "(10.0, 50.0) measures 45+ ms, `/1250.0` measures 30 ms.")
        mean = statistics.fmean(widths)
        self.assertAlmostEqual(
            mean, SPIKE_WIDTH_MEAN_MS, delta=SPIKE_WIDTH_MEAN_TOL,
            msg=f"mean slap width {mean:.3f} ms, expected "
                f"{SPIKE_WIDTH_MEAN_MS} +/- {SPIKE_WIDTH_MEAN_TOL} ms over "
                f"{len(widths)} spikes (measured 22.18). This is the probe "
                "that bites when the band's LOWER bound moves: raising it "
                "10.0 -> 12.5 leaves the measured minimum at 10.0 ms, "
                "because int() quantizes to 5 ms steps, and measures 23.91 "
                "here. `/1250.0` measures 17.5 ms.")

    def test_moved_bounds_fall_outside_the_mean_width_tolerance(self) -> None:
        """Straddle, per mistake (a): probing the ends and generalising to
        the middle is what let a 25 % move through last night. Both a 25 %
        rise in the band's floor and a 25 % rise in its ceiling must be
        REJECTED by exactly the tolerance the test above accepts.
        """
        for band, measured in (((12.5, 40.0), 23.91), ((10.0, 50.0), 27.59)):
            widths: List[float] = []
            for seed in _SPIKE_SEEDS:
                _, accel = seastate.board_slap(
                    _SPIKE_RATE, 6.0, _SPIKE_DURATION_S, seed=seed,
                    baseline_dither_g=0.0, spike_ms=band)
                widths.extend(len(r) * SAMPLE_MS for r in runs_above(accel, 3.0))
            mean = statistics.fmean(widths)
            self.assertGreater(
                abs(mean - SPIKE_WIDTH_MEAN_MS), SPIKE_WIDTH_MEAN_TOL,
                f"spike_ms={band} measures a mean width of {mean:.3f} ms "
                f"(expected ~{measured}), which the tolerance above must "
                "reject. If it does not, that tolerance is too loose to "
                "detect a 25 % move.")

    def test_spike_ms_default_is_the_documented_band(self) -> None:
        got = inspect.signature(seastate.board_slap).parameters["spike_ms"].default
        self.assertEqual(
            got, SPIKE_MS_BAND,
            f"board_slap's spike_ms default is {got}, but seastate.py:26 and "
            ":113 both claim '10-40 ms' and e16_edge_timing.py:16 uses the "
            "same band. Change the docstrings in the same commit "
            "(CLAUDE.md §4).")


class SpikeAmplitudeSpread(unittest.TestCase):
    """seastate.py:155 (`rng.gauss(0.0, 0.25)` per spike) and :159
    (`rng.gauss(0.0, 0.15)` per sample).

    Both survived a 25 % widening while rms deviation stayed flat (ratios
    0.9999 and 1.0005) — overnight mistake (d): asserting an aggregate when
    the bug is in the tails. The spread decides how far past the 2.5 g
    landing gate a nominally 3 g slap can stray, which is the property
    TestBoardSlapAlone leans on.

    The two components are separated by variance decomposition over the 812
    non-overlapping spikes: within-run scatter about each run's own mean
    estimates :159, and the run means' variance minus that estimates :155.
    """

    def test_within_spike_sample_noise(self) -> None:
        """SNAPSHOT PIN, and here is why. :159's 0.15 g has no documented
        value and no external duplicate anywhere in the repo — unlike 0.25 g
        below, which the docstring commits to. It is pinned anyway because
        it shapes the texture the detector's single-sample landing rule
        actually sees, and moving it silently re-renders every slap sweep.
        The literal below is 0.15 written by hand, not read from the module.
        """
        runs = flat_spike_runs()
        ss_within = 0.0
        dof = 0
        for run in runs:
            mean = statistics.fmean(run)
            ss_within += sum((x - mean) ** 2 for x in run)
            dof += len(run) - 1
        got = math.sqrt(ss_within / dof)
        self.assertAlmostEqual(
            got, SPIKE_SAMPLE_NOISE_G, delta=SPIKE_SAMPLE_NOISE_TOL,
            msg=f"within-spike sample sigma is {got:.4f} g over {dof} dof, "
                f"expected {SPIKE_SAMPLE_NOISE_G} +/- "
                f"{SPIKE_SAMPLE_NOISE_TOL} (measured 0.1520). 0.1875 in "
                "place of :159's 0.15 measures ~0.190.")

    def test_per_spike_amplitude_jitter(self) -> None:
        """:113-114 commits to this one: "jittered +/- ~0.25 g"."""
        runs = flat_spike_runs()
        plateaus = [statistics.fmean(run) for run in runs]
        ss_within = sum(
            sum((x - statistics.fmean(run)) ** 2 for x in run) for run in runs)
        dof = sum(len(run) - 1 for run in runs)
        within_var = ss_within / dof
        # A run mean carries within_var/len(run) of its own noise; subtract
        # that off so what is left estimates :155's sigma alone.
        shrink = statistics.fmean([1.0 / len(run) for run in runs])
        got = math.sqrt(max(0.0, statistics.variance(plateaus) - within_var * shrink))
        self.assertAlmostEqual(
            got, SPIKE_AMP_JITTER_G, delta=SPIKE_AMP_JITTER_TOL,
            msg=f"per-spike amplitude sigma is {got:.4f} g over "
                f"{len(plateaus)} spikes, expected {SPIKE_AMP_JITTER_G} +/- "
                f"{SPIKE_AMP_JITTER_TOL} (measured 0.2557). The docstring at "
                "seastate.py:113-114 says '+/- ~0.25 g'; 0.3125 measures "
                "~0.32 and would make that prose false.")

    def test_the_extreme_value_is_pinned_absolutely(self) -> None:
        """Mistake (d) again, from the other side: an absolute extreme.

        rms deviation on this stream is 1.0399 with or without either
        widening (ratios 0.9999 / 1.0005), so only the tail moves. Seeded
        snapshot: 5.7548 g, against 5.886 (:155 -> 0.3125) and 5.812
        (:159 -> 0.1875).
        """
        _, accel = seastate.board_slap(3.0, 5.0, 60.0, seed=0)
        self.assertAlmostEqual(
            max(accel), 5.755, delta=0.03,
            msg=f"board_slap(3.0, 5.0, 60 s, seed=0) peaked at "
                f"{max(accel):.4f} g, expected 5.755 +/- 0.03. A nominally "
                "5 g slap straying further above the 2.5 g landing gate is "
                "the mechanism by which a slap could read as a landing.")


class SlapBaselineDither(unittest.TestCase):
    """seastate.py:104 — `baseline_dither_g = 0.02`.

    Not decoration: :116-118 says the between-spike baseline is
    "deliberately small, because 'no preceding free-fall' is the whole point
    of this regime: a slap must never itself dip toward 0 g first." Raising
    it erodes exactly that structural guarantee, and because the erosion
    only ever makes a false positive MORE likely, a zero-jump assertion can
    never notice it moving in that direction.
    """

    def test_baseline_rms_is_0_02_g(self) -> None:
        _, accel = seastate.board_slap(0.0001, 5.0, 30.0, seed=0)
        # Positive control FIRST (mistake (c)): at rate=1e-4 over 30 s no
        # spike lands, so the rms below really is the baseline and not a
        # spike leaking into the measurement.
        self.assertLess(
            max(accel), 1.2,
            f"a spike landed (peak {max(accel):.4f} g); the rms assertion "
            "below would then be measuring the spike, not the dither.")
        got = rms_dev(accel)
        self.assertAlmostEqual(
            got, SLAP_BASELINE_DITHER_G, delta=SLAP_BASELINE_DITHER_TOL,
            msg=f"between-spike rms is {got:.5f} g, expected "
                f"{SLAP_BASELINE_DITHER_G} +/- {SLAP_BASELINE_DITHER_TOL} "
                "(measured 0.02002). 0.025 measures 0.02502 and raises the "
                "peak baseline reading 1.071 -> 1.089 g.")

    def test_moved_dither_falls_outside_that_tolerance(self) -> None:
        """Straddle (mistake (a)), in both directions around the literal."""
        for dither, measured in ((0.025, 0.02502), (0.015, 0.01501)):
            _, accel = seastate.board_slap(
                0.0001, 5.0, 30.0, seed=0, baseline_dither_g=dither)
            got = rms_dev(accel)
            self.assertGreater(
                abs(got - SLAP_BASELINE_DITHER_G), SLAP_BASELINE_DITHER_TOL,
                f"baseline_dither_g={dither} measures rms {got:.5f} "
                f"(expected ~{measured}); the tolerance above must reject it.")

    def test_baseline_dither_default(self) -> None:
        got = inspect.signature(
            seastate.board_slap).parameters["baseline_dither_g"].default
        self.assertEqual(got, SLAP_BASELINE_DITHER_G)


# ----------------------------------------------- orbital_chop: the physics

class AiryOrbitalAcceleration(unittest.TestCase):
    """seastate.py:83 — the `H / 2.0` in
    `a_orb_ms2 = (2.0*math.pi/T)**2 * (H/2.0)`.

    The `2.0*math.pi` in the same expression was killed overnight; the
    denominator was not. 2.0 -> 2.5 drops the amplitude 20 % and makes the
    module's own worked arithmetic at :61-65 false. E14/E15's venue error
    bars, which docs/session-card.md quotes to the rider, come out of this
    amplitude, and test_seastate.py's adversarial (0.8, 1.8) edge case falls
    from ~0.50 g toward ~0.40 g — eroding the margin the KNOWN-FINDING
    characterization test is calibrated on.
    """

    def test_amplitude_matches_the_airy_closed_form(self) -> None:
        for H, T, want in AIRY_AMPLITUDE_G:
            _, accel = seastate.orbital_chop(H, T, 30.0, seed=0, dither_g=0.0)
            got = (max(accel) - min(accel)) / 2.0
            self.assertAlmostEqual(
                got, want, delta=AIRY_AMPLITUDE_TOL,
                msg=f"orbital_chop(H={H}, T={T}) has peak amplitude "
                    f"{got:.6f} g; Airy gives (2*pi/T)**2 * (H/2) / g = "
                    f"{want} g. research.md §6 cites ~0.07 g for the middle "
                    "row and seastate.py:61-65 works that arithmetic out in "
                    "full. Three rows spanning 15x so no single scale error "
                    "can pass.")

    def test_the_dither_is_what_was_switched_off(self) -> None:
        """Positive control for the fixture above (mistake (c)).

        The amplitude probes pass dither_g=0.0 so that peak-to-peak IS the
        wave. That argument is load-bearing: with the dither left on, the
        same seed's peak-to-peak reads 0.0976 g instead of 0.0671 — 45 %
        high — so if dither_g stopped taking effect the probes above would
        silently be measuring wave-plus-noise and would fail loudly rather
        than pass wrongly.
        """
        _, clean = seastate.orbital_chop(0.3, 3.0, 30.0, seed=0, dither_g=0.0)
        _, noisy = seastate.orbital_chop(0.3, 3.0, 30.0, seed=0)
        self.assertGreater(
            (max(noisy) - min(noisy)) / 2.0, 0.0675,
            "with the dither on, peak-to-peak must overshoot the wave's own "
            "amplitude (measured 0.09759 vs 0.06709)")
        # And with it off the stream is a pure sine: rms = amplitude/sqrt(2)
        # = 0.0671/1.41421 = 0.047447, measured 0.047443.
        self.assertAlmostEqual(rms_dev(clean), 0.047447, delta=0.0002)

    def test_typical_wind_chop_stays_two_orders_below_the_freefall_gate(self) -> None:
        """The module's headline claim (:23-25), as an assertion."""
        _, accel = seastate.orbital_chop(0.3, 3.0, 60.0, seed=0)
        self.assertGreater(
            min(accel), FREEFALL_GATE_G,
            "research.md §6's typical wind chop must never approach the "
            f"{FREEFALL_GATE_G} g free-fall gate; minimum was {min(accel):.4f} g.")


class AiryOrbitalPeriod(unittest.TestCase):
    """seastate.py:89 — the `2.0` in
    `math.sin(2.0 * math.pi * freq_hz * t + phase)`.

    freq_hz is 1/T (:85), so the sine's angular rate must be 2*pi/T. The
    mutant leaves the amplitude untouched and gives a 2.4 s wave when the
    caller asked for 3.0 s — a 1.25x angular-rate error. Period is not a
    cosmetic label here: RESULTS.md's E15 conclusion is that the mechanism
    is "wavelength, not height" (short-period sound chop vs 8-9 s ocean
    swell), every venue row is identified by its period, and a period error
    mis-scales the chop's frequency content against the detector's 80 ms
    confirm window.
    """

    @staticmethod
    def _mean_period(H: float, T: float, duration_s: float) -> Tuple[float, int]:
        times, accel = seastate.orbital_chop(
            H, T, duration_s, seed=0, dither_g=0.0)
        crossings: List[float] = []
        for i in range(1, len(accel)):
            prev, cur = accel[i - 1] - 1.0, accel[i] - 1.0
            if prev <= 0.0 < cur:  # upward zero crossing, linearly interpolated
                crossings.append(times[i - 1] + (times[i] - times[i - 1])
                                 * (-prev / (cur - prev)))
        if len(crossings) < 2:
            return float("nan"), len(crossings)
        return (crossings[-1] - crossings[0]) / (len(crossings) - 1), len(crossings)

    def test_the_wave_has_the_period_the_caller_asked_for(self) -> None:
        """Straddled across three periods (mistake (a)): 1.8 s sound chop,
        research.md's 3.0 s wind chop, and 8.0 s ocean swell. A constant
        offset fails, and so does a 1.25x scale (which measures
        1.44 / 2.40 / 6.40).
        """
        for T in (1.8, 3.0, 8.0):
            got, n = self._mean_period(0.3, T, 60.0)
            self.assertGreaterEqual(n, 7, f"T={T}: only {n} zero crossings")
            self.assertAlmostEqual(
                got, T, delta=0.005,
                msg=f"orbital_chop(H=0.3, T={T}) produced a wave of period "
                    f"{got:.6f} s. orbital_chop's input contract is that T "
                    "IS the period; seastate.py:85 sets freq_hz = 1/T, so "
                    "the sine's angular rate must be 2*pi/T.")

    def test_the_crossing_count_agrees_independently(self) -> None:
        """Second, coarser reading of the same fact, from a different
        statistic: 60 s of a T-second wave has ~60/T upward crossings.
        """
        for T, want in ((1.8, 33), (3.0, 20), (8.0, 7)):
            _, n = self._mean_period(0.3, T, 60.0)
            self.assertAlmostEqual(
                n, want, delta=2,
                msg=f"T={T}: {n} upward crossings in 60 s, expected ~{want}")


class PhaseIsUniformOnTheCircle(unittest.TestCase):
    """seastate.py:86 — `phase = rng.uniform(0.0, 2.0 * math.pi)`.

    :71-72 states the claim: "A random per-seed phase ... gives seed
    variety". 2.0 -> 2.5 draws from [0, 2.5*pi), which folds [0, 0.5*pi)
    twice, so first-quadrant phases become twice as likely as the rest and
    the 8- and 40-seed sweeps sample the wave's starting position
    non-uniformly. Measured P(phase mod 2*pi < pi/2) = 0.2571 -> 0.3963
    against a uniform 0.25 (0.4000 predicted exactly).

    Read back out of the public stream rather than by re-drawing from
    random.Random(seed), which would only duplicate the line under test.
    """

    N_SEEDS = 20000
    H, T = 0.3, 3.0

    def _phase(self, seed: int) -> float:
        _, accel = seastate.orbital_chop(
            self.H, self.T, 0.02, seed=seed, dither_g=0.0)
        # Amplitude and omega as literals for this (H, T): 0.0671 g from
        # AIRY_AMPLITUDE_G, omega = 2*pi/3.
        return recover_phase(accel, 0.0671, 2.0 * math.pi / 3.0, 1.0 / FS_HZ)

    def test_the_recovery_itself_is_sound(self) -> None:
        """Positive control (mistake (c)): the estimator, on hand-built
        streams whose phase is known by construction. If this passes and the
        next test fails, the fault is seastate's phase, not this arithmetic.
        """
        omega, dt = 2.0 * math.pi / 3.0, 1.0 / FS_HZ
        for want in (0.1, 1.0, 3.0, 5.9):
            built = [1.0 + 0.0671 * math.sin(omega * i * dt + want)
                     for i in range(4)]
            self.assertAlmostEqual(
                recover_phase(built, 0.0671, omega, dt), want, places=9)

    def test_first_quadrant_is_not_over_represented(self) -> None:
        below = sum(1 for seed in range(self.N_SEEDS)
                    if self._phase(seed) < math.pi / 2.0)
        frac = below / self.N_SEEDS
        self.assertAlmostEqual(
            frac, 0.25, delta=0.02,
            msg=f"P(phase < pi/2) = {frac:.4f} over {self.N_SEEDS} seeds; a "
                "uniform phase gives 0.25 (measured 0.2571). Drawing from "
                "[0, 2.5*pi) measures 0.3963, because that range folds the "
                "first quadrant twice.")

    def test_every_recovered_phase_is_on_the_circle(self) -> None:
        phases = [self._phase(seed) for seed in range(200)]
        self.assertGreater(max(phases), 5.5, "phases never reach the fourth quadrant")
        self.assertLess(min(phases), 0.8, "phases never reach the first quadrant")


class ChopDither(unittest.TestCase):
    """seastate.py:57 — `dither_g = 0.01`.

    :72-75 describes this term qualitatively ("a small fixed-amplitude
    Gaussian dither approximates ordinary accelerometer self-noise riding on
    top of the smooth swell") but names NO number, so this is a SNAPSHOT
    pin, for the same reason test_sensor_model_provenance.py snapshots
    pop_g/pop_s/landing_s: it is the simulated noise floor under all seven
    of test_seastate.py's CHOP_CASES and every E10/E14 exposure hour, and
    changing it is legitimate while changing it WITHOUT noticing is not.
    """

    def test_dither_rms_is_0_01_g(self) -> None:
        _, accel = seastate.orbital_chop(0.0, 3.0, 30.0, seed=0)
        got = rms_dev(accel)
        self.assertAlmostEqual(
            got, CHOP_DITHER_G, delta=CHOP_DITHER_TOL,
            msg=f"with H=0 the residual rms IS the dither: {got:.5f} g, "
                f"expected {CHOP_DITHER_G} +/- {CHOP_DITHER_TOL} (measured "
                "0.01010). 0.0125 measures 0.01263.")

    def test_h_zero_really_removes_the_wave(self) -> None:
        """Positive control for the H=0 fixture (mistake (c)): if H=0 left
        any wave behind, the rms above would be measuring the wave.
        """
        _, flat = seastate.orbital_chop(0.0, 3.0, 30.0, seed=0)
        _, wavy = seastate.orbital_chop(0.3, 3.0, 30.0, seed=0)
        self.assertGreater(
            rms_dev(wavy), 4.0 * rms_dev(flat),
            f"H=0.3 rms {rms_dev(wavy):.5f} vs H=0.0 rms "
            f"{rms_dev(flat):.5f} (measured 0.04833 vs 0.01010)")

    def test_moved_dither_falls_outside_that_tolerance(self) -> None:
        for dither, measured in ((0.0125, 0.01263), (0.0075, 0.00758)):
            _, accel = seastate.orbital_chop(0.0, 3.0, 30.0, seed=0,
                                             dither_g=dither)
            self.assertGreater(
                abs(rms_dev(accel) - CHOP_DITHER_G), CHOP_DITHER_TOL,
                f"dither_g={dither} measures rms {rms_dev(accel):.5f} "
                f"(expected ~{measured}); the tolerance must reject it.")

    def test_dither_default(self) -> None:
        got = inspect.signature(seastate.orbital_chop).parameters["dither_g"].default
        self.assertEqual(got, CHOP_DITHER_G)


# ------------------------------------------------ handling_chatter: AR(1)

class ChatterIsMeanReverting(unittest.TestCase):
    """seastate.py:172 — `low_freq_tau_s = 0.05`, and :214's tau>0 guard.

    :182-184: "a mean-reverting AR(1)/Ornstein-Uhlenbeck process with time
    constant low_freq_tau_s (default 50 ms, the same order as the detector's
    freefall_confirm_s=0.08 s)". That cited relationship is pinnable across
    files the way test_physical_constants.py pins G, and tau is the ONLY
    knob controlling whether the model can sustain a multi-sample low
    excursion — the module's stated reason (:176-190) for not using i.i.d.
    Gaussian. :171's sibling knob (low_freq_fraction) WAS killed overnight;
    this one was not.
    """

    def test_the_cited_freefall_confirm_relationship_holds(self) -> None:
        params = json.loads(PARAMS_JSON.read_text())
        confirm_s = float(params["detector"]["freefall_confirm_s"])
        sample_hz = float(params["firmware"]["sample_hz"])
        self.assertEqual(confirm_s, 0.08, "seastate.py:183 cites freefall_confirm_s=0.08 s")
        self.assertEqual(sample_hz, FS_HZ)
        self.assertEqual(
            int(round(confirm_s * sample_hz)), FREEFALL_CONFIRM_SAMPLES,
            "the detector's free-fall confirm window is 16 samples at 200 Hz; "
            "the run-length assertion below is calibrated on that.")

    def test_lag1_autocorrelation_matches_the_closed_form(self) -> None:
        """The cheap, strict pin on tau. For d = x + h with x an AR(1) of
        stationary variance frac*amp^2 and h white with (1-frac)*amp^2, the
        lag-1 autocorrelation of d is exactly rho*frac with
        rho = exp(-dt/tau) — so 0.4 * exp(-(1/200)/0.05) = 0.361935, written
        out as a literal above.
        """
        got = chatter_lag1(CHATTER_SEEDS)
        self.assertAlmostEqual(
            got, CHATTER_LAG1, delta=CHATTER_LAG1_TOL,
            msg=f"lag-1 autocorrelation of handling_chatter's deviation is "
                f"{got:.5f} over 30 seeds x 60 s, expected {CHATTER_LAG1} "
                f"+/- {CHATTER_LAG1_TOL} (measured 0.36106). That number is "
                "low_freq_fraction * exp(-(1/fs)/low_freq_tau_s), so it pins "
                "seastate.py:171 and :172 together.")

    def test_a_moved_tau_falls_outside_that_tolerance(self) -> None:
        """Straddled in BOTH directions (mistake (a)): +25 % and -20 % must
        each be rejected by exactly the tolerance the test above accepts.
        """
        for tau, measured in ((0.0625, 0.36798), (0.04, 0.35247)):
            got = chatter_lag1(CHATTER_SEEDS, tau=tau)
            self.assertGreater(
                abs(got - CHATTER_LAG1), CHATTER_LAG1_TOL,
                f"low_freq_tau_s={tau} measures lag-1 {got:.5f} (expected "
                f"~{measured}); the tolerance above must reject it, or a "
                "25 % move in the AR(1) time constant passes unseen.")

    def test_chatter_cannot_sustain_the_freefall_confirm_window(self) -> None:
        """The behavioural consequence, which is why tau matters at all.

        With the defaults, the longest sub-0.35 g run over 30 seeds x 60 s
        is 15 samples = 75 ms, just UNDER the detector's 80 ms confirm
        window. tau=0.0625 makes it 16 = exactly AT it. rms barely moves
        (ratio 0.9986), so only this behavioural reading catches it.
        """
        got = chatter_longest_low_run(CHATTER_SEEDS)
        self.assertLess(
            got, FREEFALL_CONFIRM_SAMPLES,
            f"handling_chatter(0.5, 60 s) sustained {got} consecutive "
            f"samples below {FREEFALL_GATE_G} g (measured 15); "
            f"{FREEFALL_CONFIRM_SAMPLES} would fill the detector's free-fall "
            "confirm window, which is the adversarial power of this regime "
            "and the thing low_freq_tau_s sets.")

    def test_that_run_length_probe_can_actually_fail(self) -> None:
        """Positive control (mistake (c)): a deliberately slower AR(1) DOES
        breach the window, so the assertion above is a measurement and not a
        property of the probe. Measured 29 samples at tau=0.2, 18 at 0.1.
        """
        self.assertGreaterEqual(
            chatter_longest_low_run(CHATTER_SEEDS, tau=0.2),
            FREEFALL_CONFIRM_SAMPLES,
            "a 200 ms AR(1) time constant must be able to hold the stream "
            "below the free-fall gate for the whole confirm window; if it "
            "cannot, the probe above proves nothing.")

    def test_tau_and_fraction_defaults(self) -> None:
        params = inspect.signature(seastate.handling_chatter).parameters
        self.assertEqual(
            params["low_freq_tau_s"].default, CHATTER_TAU_S,
            "seastate.py:183 says 'default 50 ms'; change both together "
            "(CLAUDE.md §4).")
        self.assertEqual(params["low_freq_fraction"].default,
                         CHATTER_LOW_FREQ_FRACTION)

    def test_tau_zero_degenerates_to_white_noise(self) -> None:
        """seastate.py:214 — `... if low_freq_tau_s > 0.0 else 0.0`, at
        exactly 0.0.

        The guard exists so tau=0 means "no low-frequency component, pure
        white noise" — the straw-man baseline the docstring contrasts the
        AR(1) model against, and the natural way to A/B whether the
        low-frequency term is what causes a false positive. Mutating `>` to
        `>=` evaluates exp(-dt/0.0) and raises ZeroDivisionError, removing
        that capability. Negative tau reaches the else branch either way, so
        0.0 is the only separating input (mistake (c)).
        """
        _, at_zero = seastate.handling_chatter(0.3, 5.0, seed=0, low_freq_tau_s=0.0)
        self.assertEqual(len(at_zero), 1000)
        _, negative = seastate.handling_chatter(0.3, 5.0, seed=0, low_freq_tau_s=-1.0)
        self.assertEqual(
            at_zero, negative,
            "tau=0.0 and tau=-1.0 must both take the rho=0.0 branch and "
            "produce the identical stream.")
        self.assertLess(
            abs(chatter_lag1(range(8), tau=0.0)), 0.01,
            "with rho=0 the stream must be white (measured lag-1 0.0017)")
        # Positive control: the default DOES correlate, so the assertion
        # above is measuring the degenerate branch and not a flat zero.
        self.assertGreater(chatter_lag1(range(8)), 0.30,
                           "the default tau must correlate (measured 0.36074)")


# ----------------------------------------------------------- input guards

class InputGuardsRefuseTheBoundary(unittest.TestCase):
    """seastate.py:131 `if fs_hz <= 0:` and :77 `if T <= 0.0:`, at exactly
    zero — the guards' equality halves, which nothing read.

    :131 is the expensive one and it is CLAUDE.md rule 3 verbatim: with the
    mutant, board_slap(1.0, 5.0, 5.0, fs_hz=0.0) returns ('ok', 0 samples)
    instead of raising, because n = int(duration_s * 0) = 0 and the list
    comprehensions never divide. A caller then loops over nothing, the
    detector sees no samples, and "zero jumps detected" reads exactly like a
    clean pass. A reading that did not happen must not look like one.
    """

    def test_board_slap_rejects_fs_hz_of_exactly_zero(self) -> None:
        with self.assertRaises(ValueError):
            seastate.board_slap(1.0, 5.0, 5.0, fs_hz=0.0)

    def test_board_slap_still_works_just_above_zero(self) -> None:
        """The just-under half of the straddle (mistake (a)), and the
        positive control: fs_hz=1.0 must return a real 5-sample stream, so
        the rejection above is about zero and not about the fixture.
        """
        times, accel = seastate.board_slap(1.0, 5.0, 5.0, fs_hz=1.0)
        self.assertEqual(len(times), 5)
        self.assertEqual(len(accel), 5)

    def test_negative_fs_hz_also_raises_but_proves_nothing(self) -> None:
        """Recorded so nobody mistakes it for the boundary probe: fs_hz=-1.0
        raises ValueError with or WITHOUT the mutation, so on its own it is
        overnight mistake (c) — a negative test passing for the wrong reason.
        """
        with self.assertRaises(ValueError):
            seastate.board_slap(1.0, 5.0, 5.0, fs_hz=-1.0)

    def test_orbital_chop_rejects_T_of_exactly_zero(self) -> None:
        """With `T < 0.0` the caller gets ZeroDivisionError from
        `2.0*math.pi/T` instead of the intended ValueError. It still fails
        loudly — unlike :131 nothing silently passes — but T=0.0 is a
        literal a caller can pass or compute (T = 1/f with f unset).
        """
        with self.assertRaises(ValueError):
            seastate.orbital_chop(0.3, 0.0, 5.0)

    def test_orbital_chop_still_works_just_above_zero(self) -> None:
        times, accel = seastate.orbital_chop(0.3, 0.5, 5.0, seed=0)
        self.assertEqual(len(times), 1000)
        self.assertEqual(len(accel), 1000)

    def test_negative_T_also_raises_but_proves_nothing(self) -> None:
        with self.assertRaises(ValueError):
            seastate.orbital_chop(0.3, -1.0, 5.0)


class SuperposeTimebaseGuard(unittest.TestCase):
    """seastate.py:241 — the `or` in `if len(t) != n or len(a) != n:`.

    :232-234 states the contract: "All streams must share the same
    duration_s and fs_hz (so the same times_s array applies to all of
    them)". Mutating `or` to `and` accepts a stream whose TIMES array is the
    wrong length while its accel array happens to match — a silent wrong
    answer (a 3 s timebase superposed onto a 6 s one, 1200 samples returned)
    rather than an error. Both `!=` -> `==` mutants on this line died
    overnight, because every valid call passes the guard; the OR combination
    itself was read by nothing.
    """

    def setUp(self) -> None:
        self.t6, self.a6 = seastate.orbital_chop(0.3, 3.0, 6.0, seed=0)
        self.t6b, self.a6b = seastate.orbital_chop(0.2, 2.0, 6.0, seed=1)
        self.t3, self.a3 = seastate.orbital_chop(0.3, 3.0, 3.0, seed=0)

    def test_matching_streams_superpose(self) -> None:
        """Positive control FIRST (mistake (c)): the 6 s fixtures superpose
        cleanly and onto ONE shared 1 g baseline, so every rejection below
        came from the length check and not from a broken fixture.
        """
        self.assertEqual(len(self.t6), 1200)
        self.assertEqual(len(self.t3), 600)
        times, accel = seastate.superpose((self.t6, self.a6), (self.t6b, self.a6b))
        self.assertEqual(len(times), 1200)
        self.assertEqual(len(accel), 1200)
        for i in (0, 1, 599, 1199):
            self.assertAlmostEqual(
                accel[i],
                1.0 + (self.a6[i] - 1.0) + (self.a6b[i] - 1.0), places=12,
                msg="superpose must sum deviations onto a single 1 g "
                    "baseline (seastate.py:228-231)")

    def test_wrong_times_length_with_right_accel_is_rejected(self) -> None:
        """The OR's LEFT half — the one the `and` mutant lets through."""
        with self.assertRaises(ValueError):
            seastate.superpose((self.t6, self.a6), (self.t3, self.a6))

    def test_wrong_accel_length_with_right_times_is_rejected(self) -> None:
        """The OR's RIGHT half. Under the `and` mutant this raises
        IndexError rather than ValueError, so assertRaises(ValueError) still
        catches it.
        """
        with self.assertRaises(ValueError):
            seastate.superpose((self.t6, self.a6), (self.t6, self.a3))

    def test_both_lengths_wrong_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            seastate.superpose((self.t6, self.a6), (self.t3, self.a3))

    def test_no_streams_at_all_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            seastate.superpose()


if __name__ == "__main__":
    unittest.main()
