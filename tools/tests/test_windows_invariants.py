#!/usr/bin/env python3
"""sim/windows.py's bookkeeping constants, as invariants instead of prose.

WHY THIS EXISTS
---------------
2026-09-07. The overnight mutation campaign (`tools/mutation_campaign.py`,
333 mutants across 11 modules) reached `sim/windows.py` last and returned
**20 survivors against a green suite**. Recheck separated them; seventeen
were real, reachable gaps and they are all one shape — F-31's shape, stated
in `docs/audit-2026-08-22.md`: **the suite did not read its own constants.**
`tools/tests/test_windows.py` is deliberately ORDERING-ONLY ("never absolute
thresholds, which must come from real data"), which is right for the feature
CLAIMS and leaves every number in the module unpinned.

What survived, and what it costs. Measured on all four real device traces in
`data/firmware-archive/` (1,610,287 samples), not on synthetic data:

  * `windows.py:198` `min(n // 2, ...)` -> `n // 3`. The Nyquist cap on the
    top DFT bin drops a third of the spectrum. Every one of the 4758 windows
    of session-copy-20260824-183054 changes: window 0's high_band_energy
    0.098934 -> 0.058539, a 41 % drop. `low_band_energy` is untouched, so the
    low/high ratio the foiling classifier would threshold on is silently
    rescaled with no other symptom.
  * `windows.py:242` `fs_hz / 2.0` -> `/ 2.5`. The high band's top edge stops
    being Nyquist: 20 Hz instead of 25 Hz. A 22 Hz tone reads
    high_band_energy 0.125 unmutated and exactly 0.0 mutated.
  * `windows.py:224` `low_band_hz = (1.0, 4.0)` -> `(1.0, 5.0)`. Both bands
    move at once (line 242 derives the high band's bottom from
    `low_band_hz[1]`), and energy transfers from high to low on every window
    of every real trace.
  * `windows.py:285` `overlap = 0.5` -> `0.625`. Hop 25 -> 19 samples;
    +31 % rows in every features.csv (4758 -> 6258 windows on that same
    trace). `test_windows.py`'s "7 windows" CLI assertion did NOT constrain
    it: that path is satisfied by argparse's own separate 0.5 on line 300.
  * `windows.py:255` `dts[len(dts) // 2]` -> `// 3`. The inferred sample rate
    stops being the MEDIAN spacing. All 29642 windows of
    session-copy-20260829-110356 change.
  * `windows.py:249` `default_hz = 50.0` -> `62.5`. The device's log rate is
    `config/params.json`'s `firmware.log_hz`, and the module's own header
    (lines 15-19) cites it by name. Nothing read either.

and seven guards — integer boundaries and one logical operator — every one of
them constructible: a 2-sample window, a 1-sample window, a duplicated
timestamp, an exactly-short CSV row, a half-recognized header. Lines 85, 123,
131, 195, 251, 269, 272. These are NOT the float-boundary noise class the
campaign header documents; integer equality is reachable, so `n < 2` vs
`n <= 2` has an input that separates them and it is in this file.

Seventeen mutations, fifteen distinct defects: `n <= 2` and `n < 3` are the
same predicate over integers (line 195), and so are `len(seg) <= 2` and
`len(seg) < 3` (line 269). One test kills each pair, which is worth knowing
before writing two.

WHAT THIS FILE DOES DIFFERENTLY, each item from a mistake made on 2026-09-06
--------------------------------------------------------------------------
  * Behavioural first. `n // 2` is pinned by Parseval's theorem and by a
    tone sitting exactly on the Nyquist bin, not by snapshotting "2".
  * Every boundary straddled: bin 25 in / bin 24 out, n=2 computed / n=1
    guarded, 4.5 Hz high / 3.5 Hz low, a duplicate timestamp joins / a
    backwards one splits.
  * Every expected value is a LITERAL. A probe written as `MIN + 1.0` moves
    with the constant it pins and pins nothing.
  * Every negative test carries a positive control: the same fixture minus
    the one thing under test, asserted to still work. A crafted-fixture test
    that fails for an unrelated reason passes whichever way the guard goes.
  * Absolute values, not only ratios. A ratio cannot see a scale error.
  * The one constant with NO invariant and NO provenance -- the 4 Hz band
    split -- is a snapshot, and the reason it can only be a snapshot is
    written above it.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import inspect
import json
import math
import random
import re
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List, Sequence, Tuple

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sim"))

import windows as W  # noqa: E402  (path insert must come first)

PARAMS_JSON = REPO / "config" / "params.json"
WINDOWS_PY = REPO / "sim" / "windows.py"
JUMP_CLI = REPO / "tools" / "jump"

# The device log rate, and the window geometry the module documents. LITERALS
# on purpose: a test that reads the constant it pins moves with it.
LOG_HZ = 50.0          # config/params.json firmware.log_hz
WINDOW_S = 1.0         # windows.py:26 "1 s windows"
WINDOW_FLOOR_N = 2     # windows.py:272 max(2, ...), paired with :195 n < 2


# --------------------------------------------------------------- fixtures

def _tone(freq_hz: float, n: int, amp: float = 0.1, fs_hz: float = LOG_HZ) -> List[float]:
    """A pure sine. Choose n so freq_hz*n/fs_hz is a whole number and the
    tone lands on exactly one DFT bin, with zero mean over the window."""
    return [amp * math.sin(2.0 * math.pi * freq_hz * (i / fs_hz)) for i in range(n)]


def _nyquist(n: int = 50) -> List[float]:
    """The fs/2 signal: +1, -1, +1, ... All its power is in bin n//2.

    A 25 Hz SINE at 50 Hz samples to all zeros (sin(pi*i) == 0), so the
    Nyquist component has to be built as a cosine. This is the only signal
    that can tell bin n//2 from bin n//2 - 1.
    """
    return [1.0 if i % 2 == 0 else -1.0 for i in range(n)]


def _rows(values: Sequence[float], fs_hz: float = LOG_HZ) -> List[Tuple[float, float]]:
    return [(i / fs_hz, v) for i, v in enumerate(values)]


def _source_lines(path: Path) -> List[str]:
    return path.read_text().splitlines()


def _whole_line_count(path: Path, pattern: str) -> int:
    """Count lines matching `pattern` in full, after stripping indentation.

    Whole lines on purpose: on 2026-09-06 a mutation applied with a bare
    string `.replace()` hit a module DOCSTRING's mention of a constant
    instead of the code line, and the verdict recorded against it was
    meaningless. Anchor on the line.
    """
    rx = re.compile(pattern)
    return sum(1 for ln in _source_lines(path) if rx.fullmatch(ln.strip()))


# ------------------------------------------------------------ Nyquist cap

class NyquistIsTheTopOfTheSpectrum(unittest.TestCase):
    """`windows.py:198`'s `n // 2` and `:242`'s `fs_hz / 2.0`.

    These are two expressions of one fact -- bin n//2 IS the frequency
    fs/2 -- so they are pinned against each other and against the identity,
    the way test_physical_constants.py pins gravity across its seven copies.
    `band_energy`'s own docstring (windows.py:190-192) states it in prose:
    "never exceeds the Nyquist bin n//2".

    A pure mathematical identity, so this is not a snapshot of a desk choice
    and must never become one.
    """

    def test_the_nyquist_bin_is_included_in_the_band(self) -> None:
        """Absolute value, not a ratio. 41 % of the high band went missing
        under the `n // 3` mutant with nothing else to see."""
        got = W.band_energy(_nyquist(50), LOG_HZ, 4.0, 25.0)
        self.assertAlmostEqual(
            got, 50.0, places=9,
            msg=f"the +1/-1 window at 50 Hz puts ALL its power in bin 25 = "
                f"n//2, so its average power over the (4, 25] band is exactly "
                f"50.0; got {got!r}. A lower cap than n//2 silently drops the "
                "top of every high_band_energy in the repo -- measured as a "
                "41 % drop on all 4758 windows of "
                "data/firmware-archive/session-copy-20260824-183054.")

    def test_one_bin_below_nyquist_excludes_it(self) -> None:
        """The just-under half of the straddle: an INTEGER bin boundary, so
        equality is reachable and 24 vs 25 is a real distinction."""
        got = W.band_energy(_nyquist(50), LOG_HZ, 4.0, 24.9)
        self.assertLess(
            got, 1e-12,
            f"a (4, 24.9] band stops at bin floor(24.9) = 24 and must NOT "
            f"pick up the bin-25 signal; got {got!r}. If this passes while "
            "the test above fails, the cap moved rather than the bin.")

    def test_asking_above_nyquist_adds_nothing(self) -> None:
        """The cap itself: bins above n//2 are aliases, never extra energy."""
        capped = W.band_energy(_nyquist(50), LOG_HZ, 4.0, 25.0)
        beyond = W.band_energy(_nyquist(50), LOG_HZ, 4.0, 1000.0)
        self.assertAlmostEqual(
            beyond, 50.0, places=9,
            msg=f"a (4, 1000] band on a 50 Hz window must clamp to the "
                f"Nyquist bin and read the same 50.0 as (4, 25]; got "
                f"{beyond!r}.")
        self.assertEqual(capped, beyond)

    def test_parseval_holds_over_the_retained_band(self) -> None:
        """The strongest form: the retained bins must carry ALL the variance.

        For a mean-removed length-n window, sum over k=1..n-1 of |X_k|^2 is
        n * sum(d^2), and |X_k| == |X_{n-k}|. So the bins band_energy keeps
        (1..n//2) must sum to exactly half of that, plus half of the
        self-paired Nyquist bin when n is even. Any cap below n//2 loses
        real energy and this fails; that is a theorem, not a tuning choice.
        """
        for n in (33, 50, 51, 64):
            with self.subTest(n=n):
                rng = random.Random(n)
                x = [rng.gauss(0.0, 1.0) for _ in range(n)]
                mean = sum(x) / n
                dev = [v - mean for v in x]
                got = W.band_energy(dev, LOG_HZ, 0.0, LOG_HZ / 2.0)
                self_paired = W.goertzel_power(dev, n // 2, n) / n if n % 2 == 0 else 0.0
                want = (sum(d * d for d in dev) + self_paired) / 2.0
                self.assertAlmostEqual(
                    got, want, places=6,
                    msg=f"n={n}: band_energy over (0, fs/2] gave {got!r} but "
                        f"Parseval requires {want!r}. The retained bins are "
                        "not carrying the window's variance, so the cap or "
                        "the bin power is wrong.")

    def test_the_default_high_band_reaches_nyquist(self) -> None:
        """Ties `:242`'s fs_hz/2.0 to `:198`'s n//2 through the public API.

        compute_window_features derives the high band's top edge itself, so
        this is the only assertion that sees line 242.
        """
        f = W.compute_window_features(_nyquist(50), 0.0, 0, LOG_HZ)
        self.assertAlmostEqual(
            f.high_band_energy, 50.0, places=9,
            msg=f"the default high band must run up to fs/2 = 25 Hz, where "
                f"the +1/-1 window's entire 50.0 of power sits; got "
                f"{f.high_band_energy!r}. At fs/2.5 the band tops out at "
                "20 Hz and goes blind to real content it exists to capture.")

    def test_a_22hz_tone_lands_in_the_high_band(self) -> None:
        """Between fs/2.5 = 20 Hz and fs/2 = 25 Hz. Absolute value: a 0.1 g
        tone on exactly one bin of a 50-sample window has average power
        (0.1 * 50 / 2)^2 / 50 = 0.125 exactly."""
        f = W.compute_window_features(_tone(22.0, 50), 0.0, 0, LOG_HZ)
        self.assertAlmostEqual(
            f.high_band_energy, 0.125, places=9,
            msg=f"a 22 Hz, 0.1 g tone must read high_band_energy 0.125; got "
                f"{f.high_band_energy!r}. Measured 0.0 under the fs/2.5 "
                "mutant -- the band silently stops existing above 20 Hz.")


# --------------------------------------------------------- the band split

class TheFourHertzSplitIsWhereTheSplitIs(unittest.TestCase):
    """`windows.py:224`'s `low_band_hz = (1.0, 4.0)`.

    SNAPSHOT, and the reason it can only be a snapshot is on the record.
    There is no external source: the archived research file states it
    outright -- "no published vibration signature separating foilborne from
    hullborne states exists in reachable literature" (`git show
    archive/docs-2026-08-23:docs/research.md`, "The moat, confirmed") -- and
    the module itself calls the split "a provisional split ... pending real
    session-1 data to tune against" (windows.py:227-230). So 4.0 Hz is a
    deliberate desk choice with nothing to check it against, which is the
    test_wing_physics_claims.py snapshot shape rather than the
    test_sensor_model_provenance.py provenance shape.

    It is worth pinning anyway, and more than most: it is the primary
    discriminator the time-on-foil classifier is meant to threshold, and
    moving it moves BOTH features at once (line 242 derives the high band's
    bottom edge from `low_band_hz[1]`) on every window of every real trace.
    When session-1 data tunes it, change it here IN THE SAME COMMIT and say
    what it invalidates.
    """

    def test_the_split_defaults_are_unchanged(self) -> None:
        got = inspect.signature(W.compute_window_features).parameters["low_band_hz"].default
        self.assertEqual(
            got, (1.0, 4.0),
            f"compute_window_features's low_band_hz default is {got!r}, not "
            "(1.0, 4.0). That is legitimate to change once real session-1 "
            "data exists to tune against -- but not silently: it re-scales "
            "low_band_energy AND high_band_energy on every window of every "
            "trace already captured.")

    def test_the_high_band_starts_where_the_low_band_ends(self) -> None:
        """The derivation on line 242, behaviourally: one split, not two."""
        f = W.compute_window_features(_tone(2.0, 100), 0.0, 0, LOG_HZ,
                                      low_band_hz=(1.0, 8.0))
        self.assertAlmostEqual(
            f.low_band_energy, 0.25, places=9,
            msg="a 2 Hz tone must sit inside a (1, 8] low band.")
        g = W.compute_window_features(_tone(6.0, 100), 0.0, 0, LOG_HZ,
                                      low_band_hz=(1.0, 4.0))
        self.assertAlmostEqual(
            g.high_band_energy, 0.25, places=9,
            msg=f"with the split at 4 Hz a 6 Hz tone must be entirely in the "
                f"high band; got high={g.high_band_energy!r}, "
                f"low={g.low_band_energy!r}. If the high band does not begin "
                "at low_band_hz[1] the two bands no longer tile the "
                "spectrum and energy is lost or double-counted.")

    def test_just_over_the_split_is_high_and_just_under_is_low(self) -> None:
        """Straddle. 100 samples at 50 Hz puts 3.5 Hz on bin 7 and 4.5 Hz on
        bin 9 exactly, so neither tone is smeared and each belongs to
        precisely one band. Absolute values: a 0.1 g single-bin tone in a
        100-sample window is (0.1 * 100 / 2)^2 / 100 = 0.25 exactly.
        """
        low = W.compute_window_features(_tone(3.5, 100), 0.0, 0, LOG_HZ)
        self.assertAlmostEqual(
            low.low_band_energy, 0.25, places=9,
            msg=f"3.5 Hz is below the 4 Hz split and must read 0.25 in the "
                f"LOW band; got {low.low_band_energy!r}. This is the "
                "just-under control -- it stays put under a 4->5 Hz move, so "
                "if it fails the fixture is broken, not the split.")
        self.assertLess(low.high_band_energy, 1e-12)

        high = W.compute_window_features(_tone(4.5, 100), 0.0, 0, LOG_HZ)
        self.assertAlmostEqual(
            high.high_band_energy, 0.25, places=9,
            msg=f"4.5 Hz is above the 4 Hz split and must read 0.25 in the "
                f"HIGH band; got high={high.high_band_energy!r}, "
                f"low={high.low_band_energy!r}. Under a 4->5 Hz split this "
                "tone changes hands entirely -- measured (0.116, 0.053) "
                "instead of (0.070, 0.110) on a 50-sample window -- and it "
                "is the discriminator the whole feature exists for.")
        self.assertLess(
            high.low_band_energy, 1e-12,
            "4.5 Hz must contribute NOTHING to the low band. It doing so is "
            "the exact signature of the split having moved above it.")


# ------------------------------------------------------------- 50% overlap

class FiftyPercentOverlapIsWrittenSixTimes(unittest.TestCase):
    """`windows.py:285` and `:260`, both `overlap = 0.5`.

    The cross-site-duplication shape of test_sensor_model_provenance.py. Six
    copies of one number: THREE independent code literals --
    extract_features's default (line 285), windows_for_segment's default
    (line 260) and argparse's own (line 300) -- and three statements in
    prose: the module docstring (line 26, "1 s windows, 50% overlap"),
    windows_for_segment's docstring (line 262) and the CLI help text
    (line 300, "default 0.5").

    `windows_for_segment`'s default is dead through every internal caller
    (extract_features and main() both pass overlap explicitly), which is
    precisely why nothing caught it: it governs only direct calls from
    outside code -- which is what the future foiling classifier would be.
    """

    # 4 s at 50 Hz = 200 samples. A 1 s window is 50 samples, a 50 % overlap
    # is a 25-sample hop, so (200 - 50) / 25 + 1 = 7 whole windows starting
    # every 0.5 s. Every number here is a literal.
    N_SAMPLES = 200
    WANT_WINDOWS = 7
    WANT_STARTS = [0.0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0]

    def _trace(self) -> List[Tuple[float, float]]:
        return _rows([1.0 + v for v in _tone(2.0, self.N_SAMPLES)])

    def test_extract_features_defaults_to_a_half_window_hop(self) -> None:
        feats = W.extract_features(self._trace())
        starts = [round(f.window_start_s, 6) for f in feats]
        self.assertEqual(
            len(feats), self.WANT_WINDOWS,
            f"200 samples at 50 Hz with the documented 1 s / 50 % geometry "
            f"gives exactly 7 whole windows; got {len(feats)}. At overlap "
            "0.625 it gives 8 -- and +31 % rows on every real trace "
            "(4758 -> 6258 on session-copy-20260824-183054).")
        self.assertEqual(
            starts, self.WANT_STARTS,
            f"window starts must be 0.5 s apart -- half of the 1 s window; "
            f"got {starts}. The count alone does not pin the hop.")

    def test_windows_for_segment_defaults_to_the_same_hop(self) -> None:
        """The dead-through-internal-callers default, called the way outside
        code would call it: positionally, with no keywords."""
        feats = W.windows_for_segment(self._trace(), 0)
        starts = [round(f.window_start_s, 6) for f in feats]
        self.assertEqual(len(feats), self.WANT_WINDOWS)
        self.assertEqual(
            starts, self.WANT_STARTS,
            f"windows_for_segment's own overlap default must be 50 %, as its "
            f"docstring (windows.py:262) states; got starts {starts}. Every "
            "internal caller passes overlap explicitly, so this default is "
            "only ever exercised from outside -- and only by this test.")

    def test_the_realized_overlap_is_one_half(self) -> None:
        """Measured from the output, not read from the default."""
        feats = W.extract_features(self._trace())
        hop_s = feats[1].window_start_s - feats[0].window_start_s
        self.assertAlmostEqual(
            1.0 - hop_s / WINDOW_S, 0.5, places=9,
            msg=f"the hop came out {hop_s!r} s against a {WINDOW_S} s "
                f"window, i.e. an overlap of {1.0 - hop_s / WINDOW_S!r}, not "
                "the 0.5 the module docstring claims on line 26.")

    def test_overlap_zero_is_the_positive_control(self) -> None:
        """Proves the parameter is honoured at all, so a failure above is
        the DEFAULT drifting and not the windowing being broken."""
        feats = W.extract_features(self._trace(), overlap=0.0)
        self.assertEqual([round(f.window_start_s, 6) for f in feats],
                         [0.0, 1.0, 2.0, 3.0],
                         "with no overlap, 200 samples must give 4 "
                         "back-to-back 1 s windows.")

    def test_every_written_copy_of_the_overlap_agrees(self) -> None:
        src = WINDOWS_PY.read_text()
        arg_default = re.search(
            r'^\s*ap\.add_argument\("--overlap".*?default=([0-9.]+).*$',
            src, re.MULTILINE)
        self.assertIsNotNone(
            arg_default,
            f"could not find the --overlap argparse default in {WINDOWS_PY}. "
            "If the CLI stopped declaring its own literal and now reads "
            "extract_features's default, delete this assertion -- the "
            "duplication it guards would be gone. Do NOT just loosen the "
            "regex and keep the test.")
        help_default = re.search(r"window overlap fraction \(default ([0-9.]+)\)", src)
        self.assertIsNotNone(help_default, "CLI help text no longer states the default")

        got = {
            "extract_features": inspect.signature(W.extract_features).parameters["overlap"].default,
            "windows_for_segment": inspect.signature(W.windows_for_segment).parameters["overlap"].default,
            "argparse": float(arg_default.group(1)),
            "cli help text": float(help_default.group(1)),
        }
        drifted = {k: v for k, v in got.items() if v != 0.5}
        self.assertEqual(
            drifted, {},
            f"the 50 % overlap is written out three times as a code literal "
            f"and three more times in prose, and these disagree with 0.5: "
            f"{drifted}. "
            "CLAUDE.md section 4: an identifier without a lookup entry is a "
            "rediscovery waiting to happen -- and here the CLI's own literal "
            "is what let test_windows.py's 7-window assertion pass while "
            "extract_features's default moved underneath it.")

    def test_the_prose_still_says_fifty_percent(self) -> None:
        """If the geometry is retuned, these two sentences must move too."""
        for label, doc in (("module docstring", W.__doc__),
                           ("windows_for_segment docstring", W.windows_for_segment.__doc__)):
            with self.subTest(where=label):
                self.assertIn(
                    "50% overlap", doc or "",
                    f"the {label} no longer states the 50 % overlap it is "
                    "cited for above. Prose and code must move together.")

    def test_one_second_windows(self) -> None:
        """The other half of the documented geometry, same shape."""
        for fn in (W.extract_features, W.windows_for_segment):
            with self.subTest(fn=fn.__name__):
                self.assertEqual(
                    inspect.signature(fn).parameters["window_s"].default, 1.0,
                    f"{fn.__name__}'s window_s default must be 1.0 s. "
                    "docs/research.md records the method as validated at "
                    "'50 Hz, 1 s windows' (Gomes 2019, 90.3 % vs video); a "
                    "different window length is outside what that supports.")


# ----------------------------------------------------------- sample rate

class SampleRateIsTheMedianSpacing(unittest.TestCase):
    """`windows.py:255`'s `dts[len(dts) // 2]` and `:251`'s `d > 0.0`.

    windows_for_segment's docstring (windows.py:263-265) states the rule:
    "the sample rate is inferred from the segment's own median inter-sample
    spacing (nominally 50 Hz)". `// 2` is what implements "median", and the
    `> 0.0` filter is what makes it the median spacing OF THE DATA rather
    than of a list padded with zeros. fs_hz then feeds every band edge
    (`lo_bin = max(1, ceil(lo_hz * n / fs_hz))`), so a small error in it
    flips which bin a band starts at: mutating `// 2` to `// 3` changed all
    29642 windows of session-copy-20260829-110356.
    """

    def test_even_spacing_gives_that_rate(self) -> None:
        """Positive control. If this fails, the fixtures below prove nothing."""
        got = W._infer_fs_hz([(i * 0.02, 1.0) for i in range(10)])
        self.assertAlmostEqual(
            got, 50.0, places=6,
            msg=f"20 ms spacing is 50 Hz; got {got!r}.")

    def test_the_middle_spacing_wins_not_the_lower_quartile(self) -> None:
        """Spacings sorted [0.01, 0.01, 0.02, 0.04, 0.04]: the median is
        0.02 (50 Hz), the mean is 0.024 (41.67 Hz), the lower-quartile-ish
        element `// 3` picks is 0.01 (100 Hz) and the extremes are 100 Hz and
        25 Hz. Only the median gives 50.0, so this one fixture separates the
        index from every plausible alternative at once."""
        seg = [(t, 1.0) for t in (0.0, 0.01, 0.02, 0.04, 0.08, 0.12)]
        got = W._infer_fs_hz(seg)
        self.assertAlmostEqual(
            got, 50.0, places=6,
            msg=f"spacings [0.01, 0.01, 0.02, 0.04, 0.04] have median 0.02, "
                f"so the inferred rate is 50.0 Hz; got {got!r}. 100.0 means "
                "the index moved down the sorted list (`// 3`), 41.67 means "
                "it became a mean, 25.0 means it took the largest gap.")
        self.assertNotAlmostEqual(
            got, 100.0, places=3,
            msg="100 Hz is the SECOND-smallest spacing, which is what "
                "dts[len(dts) // 3] returns for this fixture.")

    def test_one_long_pause_does_not_move_the_rate(self) -> None:
        """Why a median and not a mean: a single stalled write must not
        halve the inferred rate for the whole segment."""
        seg = [(i * 0.02, 1.0) for i in range(20)] + [(20 * 0.02 + 5.0, 1.0)]
        got = W._infer_fs_hz(seg)
        self.assertAlmostEqual(
            got, 50.0, places=6,
            msg=f"nineteen 20 ms gaps and one 5 s gap is still a 50 Hz "
                f"segment; got {got!r}. A mean would read 2.6 Hz and every "
                "window in the segment would be built at the wrong rate.")

    def test_duplicate_timestamps_are_not_counted_as_spacings(self) -> None:
        """`:251`'s `d > 0.0`. Four samples at t=0 then two real 10 ms steps:
        three zero spacings would OUTVOTE the two real ones and drag the
        median to 0.0, at which point line 256's fallback quietly substitutes
        the 50 Hz default for a 100 Hz segment."""
        seg = [(0.0, 1.0), (0.0, 1.0), (0.0, 1.0), (0.0, 1.0),
               (0.01, 1.0), (0.02, 1.0)]
        got = W._infer_fs_hz(seg)
        self.assertAlmostEqual(
            got, 100.0, places=6,
            msg=f"the only real spacing in this segment is 10 ms, so the "
                f"rate is 100.0 Hz; got {got!r}. 50.0 means the zero "
                "spacings were kept, the median came out 0.0, and the "
                "default_hz fallback fired -- indistinguishable, from the "
                "outside, from a correctly-inferred 50 Hz segment.")

    def test_the_same_segment_without_the_duplicates_is_the_control(self) -> None:
        """Proves the fixture's real spacing is 10 ms and that the duplicate
        rows are what do the work above -- not the rate, not the length."""
        seg = [(0.0, 1.0), (0.01, 1.0), (0.02, 1.0)]
        self.assertAlmostEqual(W._infer_fs_hz(seg), 100.0, places=6)


class TheFallbackRateIsTheDeviceLogRate(unittest.TestCase):
    """`windows.py:249`'s `default_hz = 50.0`.

    The strongest provenance in the module and the same shape as
    test_sensor_model_provenance.py's fs_hz assertion: `config/params.json`
    holds `"log_hz": 50`, and windows.py:15-19 cites it by name -- "nominally
    50 Hz (config/params.json's firmware.log_hz)". Mutating it to 62.5 made a
    60-sample segment yield 0 windows instead of 1 (window_n becomes 62), and
    nothing failed.
    """

    def test_the_default_matches_params_json(self) -> None:
        want = json.loads(PARAMS_JSON.read_text())["firmware"]["log_hz"]
        got = inspect.signature(W._infer_fs_hz).parameters["default_hz"].default
        self.assertEqual(
            got, float(want),
            f"_infer_fs_hz's default_hz is {got} but config/params.json says "
            f"the device logs at {want} Hz, which windows.py:15-19 cites by "
            "name. Every window length and band edge in a segment with no "
            "usable timestamps would then be computed at a rate the device "
            "never produces.")

    def test_a_segment_with_no_usable_spacing_falls_back_to_it(self) -> None:
        """Behavioural, and it is reachable: the firmware writes t in
        milliseconds, so a stalled clock gives identical timestamps."""
        want = float(json.loads(PARAMS_JSON.read_text())["firmware"]["log_hz"])
        got = W._infer_fs_hz([(0.0, 1.0), (0.0, 1.0)])
        self.assertAlmostEqual(
            got, want, places=9,
            msg=f"a segment whose every timestamp reads 0.0 has no spacing "
                f"to infer from and must fall back to the device log rate "
                f"{want} Hz; got {got!r}.")

    def test_the_log_rate_other_test_files_declare_agrees(self) -> None:
        """CLAUDE.md section 4, applied to the suite itself.

        Three other test files declare the log rate as a bare constant with
        a comment claiming it matches params.json, and on 2026-09-07 not one
        of them asserted it -- so all four could have drifted together with
        the module and the suite stayed green.
        """
        want = float(json.loads(PARAMS_JSON.read_text())["firmware"]["log_hz"])
        here = Path(__file__).parent
        declarations = {
            "test_windows.py": r"FS_HZ\s*=\s*([0-9.]+)",
            "test_trace_codec.py": r"LOG_HZ\s*=\s*([0-9.]+)",
            "test_codec_contracts.py": r"LOG_HZ\s*=\s*([0-9.]+)",
        }
        drifted = {}
        for fname, pattern in declarations.items():
            path = here / fname
            if not path.exists():
                continue  # renamed or removed; not this file's business
            m = re.search(r"^\s*" + pattern + r".*$", path.read_text(), re.MULTILINE)
            self.assertIsNotNone(
                m,
                f"{fname} no longer declares the log rate. If it stopped "
                "needing one, remove it from the table above IN THE SAME "
                "COMMIT; do not loosen the regex.")
            if float(m.group(1)) != want:
                drifted[fname] = float(m.group(1))
        self.assertEqual(
            drifted, {},
            f"these test files declare a log rate that disagrees with "
            f"config/params.json's {want}: {drifted}. Each of their comments "
            "claims it matches; a confident wrong citation is worse than no "
            "citation (CLAUDE.md rule 6).")

    def test_the_log_rate_is_still_fifty(self) -> None:
        """The literal, stated once, so params.json cannot be edited to make
        the provenance tests above pass. 50 Hz is a SUPPORTED choice, not an
        arbitrary one: archived docs/research.md records "50 Hz retained
        after verification of adequacy" (Gomes 2019, 1 s windows, 90.3 % wave
        detection vs video). Changing it is legitimate; changing it without
        re-reading that is not."""
        got = float(json.loads(PARAMS_JSON.read_text())["firmware"]["log_hz"])
        self.assertEqual(
            got, LOG_HZ,
            f"config/params.json's firmware.log_hz is now {got}, not 50. "
            "Every stored-trace test in this suite and the whole windowing "
            "geometry are written against 50 Hz; update them and this "
            "assertion in the same commit.")


# ------------------------------------------------------------ reboot split

class OnlyBackwardsTimeStartsANewSegment(unittest.TestCase):
    """`windows.py:85`'s `t < last_t`.

    The rule the function exists for, stated in its own docstring
    (windows.py:71-79) and re-implemented byte-identically in the CLI
    (tools/jump:1377) -- a deliberate duplication the module docstring calls
    out. Flipping it to `<=` splits a power-on run in two at any duplicated
    timestamp: windows straddling the split are lost and every later segment
    id shifts. The docstring cites the real cost of getting this wrong: a
    desk session once printed "-55.86s of air" from mis-split time.

    HONEST LIMIT: I measured 0 duplicate timestamps across all 1,610,287
    samples of the four traces in data/firmware-archive/ (the firmware logs
    at 20 ms and trace.csv holds 3 decimals, so a duplicate needs two
    samples inside 1 ms). Reboots themselves ARE in that data -- 7, 2, 0 and
    11 backwards steps -- so the live path is exercised. The equality case is
    constructible, not observed.
    """

    def test_a_duplicated_timestamp_stays_in_one_segment(self) -> None:
        rows = [(0.00, 1.0), (0.02, 2.0), (0.02, 3.0), (0.04, 4.0)]
        segs = W.split_segments(rows)
        self.assertEqual(
            [len(s) for s in segs], [4],
            f"two samples sharing t = 0.02 is a clock resolution artefact, "
            f"not a reboot, and must stay in ONE segment; got "
            f"{[len(s) for s in segs]}. Splitting there drops the windows "
            "that straddle it and renumbers every later segment.")
        self.assertEqual([m for _, m in segs[0]], [1.0, 2.0, 3.0, 4.0])

    def test_a_backwards_timestamp_does_split(self) -> None:
        """The just-over half of the straddle, and the positive control: the
        fixture differs from the one above by 0.01 s on one row."""
        rows = [(0.00, 1.0), (0.02, 2.0), (0.01, 3.0), (0.04, 4.0)]
        segs = W.split_segments(rows)
        self.assertEqual(
            [len(s) for s in segs], [2, 2],
            f"t going 0.02 -> 0.01 IS a reboot and must start a new segment; "
            f"got {[len(s) for s in segs]}. If this fails the splitter is "
            "broken outright and the equality test above proves nothing.")

    def test_the_cli_implements_the_same_line(self) -> None:
        """The cross-file duplication, anchored on WHOLE LINES.

        windows.py:68-80 declares itself "a deliberate, independent
        re-implementation of tools/jump's split_segments() -- same rule",
        and the two lines are byte-identical today. Nothing checked that.
        """
        rule = r"if last_t is not None and t < last_t:"
        for path in (WINDOWS_PY, JUMP_CLI):
            with self.subTest(file=path.name):
                self.assertEqual(
                    _whole_line_count(path, rule), 1,
                    f"{path} must contain exactly one line reading "
                    f"`{rule}`. The two implementations are documented as "
                    "sharing this rule; if one relaxes it to `<=` they "
                    "disagree about where a session's segments are, and the "
                    "CLI and the harness would report different airtime "
                    "from the same trace.")

    def test_the_docstring_still_states_the_strict_rule(self) -> None:
        # Whitespace-normalized: the claim is wrapped across two lines.
        doc = " ".join((W.split_segments.__doc__ or "").split())
        self.assertIn(
            "any t < previous t starts a new segment", doc,
            "split_segments' docstring no longer states the strict rule it "
            "is pinned to above. Prose and code must move together.")


# --------------------------------------------------------------- messy CSV

class MessyCsvIsSkippedNotFatal(unittest.TestCase):
    """`windows.py:123`'s `or` and `:131`'s `<=`.

    load_trace_csv's docstring (windows.py:103-105) promises that
    "non-numeric/blank/comment rows are skipped rather than raising, matching
    the rest of this codebase's tolerance for slightly-messy captured data".
    Both mutants turn a skip into an exception, taking the whole loader down
    on input it promises to tolerate.

    HONEST LIMIT: all four real traces use the recognized `t,mag` header and
    contain no short rows, so both cases are constructible rather than
    observed. Every negative test below is paired with the same fixture
    minus the one offending row, asserted to load -- on 2026-09-06 a
    negative codec test passed for the wrong reason (its blocks failed a CRC
    check before reaching the guard under test) and the mutation survived
    anyway.
    """

    def _load(self, text: str) -> List[Tuple[float, float]]:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.csv"
            p.write_text(text)
            return W.load_trace_csv(str(p))

    def test_a_half_recognized_header_falls_back_to_positional_columns(self) -> None:
        """`t,mag_x`: `t` matches, but there is no bare `mag`, so `im` stays
        None. With `or` the loader falls back to columns 0 and 1; with `and`
        it keeps `im = None` and dies at `max(it, im)` with a TypeError."""
        got = self._load("t,mag_x\n0.00,1.0\n0.02,1.1\n")
        self.assertEqual(
            got, [(0.0, 1.0), (0.02, 1.1)],
            f"a three-axis export header such as 't,mag_x' recognizes the "
            f"time column and not the magnitude column; the loader must fall "
            f"back to positional columns rather than crash. got {got!r}. "
            "Positional fallback is the ONLY handling this module has for an "
            "unrecognized header.")

    def test_a_fully_recognized_header_is_the_control(self) -> None:
        """The same two data rows, loaded through the normal path. If this
        fails, the test above is failing for an unrelated reason."""
        self.assertEqual(self._load("t,mag\n0.00,1.0\n0.02,1.1\n"),
                         [(0.0, 1.0), (0.02, 1.1)])

    def test_no_header_at_all_is_the_other_control(self) -> None:
        """Both columns unrecognized -- the case the fallback was written
        for, and the case that behaves identically under `or` and `and`. It
        is here so a failure can be located to the half-recognized case."""
        self.assertEqual(self._load("0.00,1.0\n0.02,1.1\n"),
                         [(0.0, 1.0), (0.02, 1.1)])

    def test_an_exactly_short_row_is_skipped(self) -> None:
        """`len(row) <= max(it, im)`: with a `t,mag` header max(it, im) is 1,
        so a one-field row -- a partially flushed final line -- has
        len(row) == 1 exactly. An INTEGER boundary: equality is reachable,
        unlike the float-threshold flips the campaign header files as noise.
        With `<` the guard misses it and row[1] raises IndexError."""
        got = self._load("t,mag\n0.00,1.0\n0.02\n0.04,1.2\n")
        self.assertEqual(
            got, [(0.0, 1.0), (0.04, 1.2)],
            f"the truncated line '0.02' must be skipped, not fatal; got "
            f"{got!r}. A trace whose last line was cut off mid-flush is "
            "exactly the slightly-messy captured data the docstring "
            "promises to tolerate.")

    def test_the_same_file_without_the_short_row_is_the_control(self) -> None:
        """Positive control: identical rows, identical header, no short line.
        This loads under both variants, so the short row is what does the
        work above."""
        self.assertEqual(self._load("t,mag\n0.00,1.0\n0.04,1.2\n"),
                         [(0.0, 1.0), (0.04, 1.2)])

    def test_a_row_one_field_longer_is_kept(self) -> None:
        """The just-over half of the straddle: len(row) == max(it, im) + 1 is
        the shortest usable row and must NOT be skipped."""
        got = self._load("t,mag\n0.00,1.0\n0.02,1.1,extra\n")
        self.assertEqual(
            got, [(0.0, 1.0), (0.02, 1.1)],
            f"a two-field row is complete for a 't,mag' header and a "
            f"three-field one has a trailing extra; both must load. got "
            f"{got!r}. If the guard rejected these the loader would drop "
            "real samples silently, which is worse than raising.")

    def test_blank_and_comment_rows_are_skipped(self) -> None:
        """The rest of the docstring's promise, so a regression there is
        also caught here."""
        got = self._load("t,mag\n0.00,1.0\n\n# rebooted here\n0.04,1.2\n")
        self.assertEqual(got, [(0.0, 1.0), (0.04, 1.2)])


# ------------------------------------------------ the two-sample floor

class TwoSampleWindowsAreTheFloorAndAreComputed(unittest.TestCase):
    """`windows.py:195`'s `n < 2`, `:269`'s `len(seg) < 2`, `:272`'s `max(2, ...)`.

    These three 2s are one design: the window floor on line 272 exists so
    that `band_energy` never sees a window below its own bail-out on line
    195, and line 269 must not pre-empt either. No prose states any of them,
    so they are pinned AGAINST EACH OTHER and behaviourally -- a 2-sample
    window is the smallest thing this module will build, and it must be
    built rather than silently zeroed or dropped.

    All three are INTEGER boundaries. n == 2, len(seg) == 2 and a 2-sample
    floor are each exactly constructible, so `< 2` vs `<= 2` has a real
    input separating it -- these are not the measure-zero float flips.
    """

    def test_a_two_sample_window_has_real_band_energy(self) -> None:
        """n == 2 keeps one usable bin (bin 1 == Nyquist), and its power is
        (x0 - x1)^2 / n = 0.4^2 / 2 = 0.08 exactly. Returning 0.0 there
        makes a short window look like a silent one."""
        got = W.band_energy([1.0, 1.4], LOG_HZ, 4.0, 25.0)
        self.assertAlmostEqual(
            got, 0.08, places=9,
            msg=f"band_energy on a 2-sample window must return its real "
                f"bin-1 power 0.08, not 0.0; got {got!r}. Reachable through "
                "the pipeline: window_n = max(2, round(window_s * fs_hz)) is "
                "exactly 2 whenever round(window_s * fs_hz) <= 2.")
        self.assertGreater(got, 0.0)

    def test_a_one_sample_window_returns_zero(self) -> None:
        """The just-under half: at n == 1 there is no non-DC bin at all, so
        the guard must fire. Straddling both sides is what pins the 2."""
        self.assertEqual(
            W.band_energy([1.0], LOG_HZ, 4.0, 25.0), 0.0,
            "a 1-sample window has nothing but DC and must return 0.0.")
        self.assertEqual(W.band_energy([], LOG_HZ, 4.0, 25.0), 0.0)

    def test_a_two_sample_segment_yields_one_window(self) -> None:
        """`:269`. The 10 s gap makes the inferred rate 0.1 Hz, so
        window_n = max(2, round(1.0 * 0.1)) = 2 and the segment fills exactly
        one whole window -- which windows_for_segment's docstring
        (windows.py:266-267) promises to emit ("only FULL windows are
        emitted"). Discarding it pre-empts that rule."""
        feats = W.extract_features([(0.0, 1.0), (10.0, 1.4)])
        self.assertEqual(
            len(feats), 1,
            f"a 2-sample segment that fills a whole window must produce that "
            f"window; got {len(feats)}.")
        self.assertAlmostEqual(feats[0].ptp, 0.4, places=9)
        self.assertAlmostEqual(feats[0].sd, 0.28284271247461906, places=9)

    def test_a_one_sample_segment_yields_nothing(self) -> None:
        """The just-under half of `:269`."""
        self.assertEqual(W.extract_features([(0.0, 1.0)]), [])
        self.assertEqual(W.extract_features([]), [])

    def test_the_window_floor_is_exactly_two_samples(self) -> None:
        """`:272`. window_s = 0.02 s at 50 Hz rounds to 1 sample, so the
        floor is what sets the length -- and the emitted windows' ptp reveals
        it: on values 1.0, 2.0, 4.0 a 2-sample window gives ptps
        [1.0, 2.0] and a 3-sample one gives a single [3.0]."""
        rows = [(0.0, 1.0), (0.02, 2.0), (0.04, 4.0)]
        feats = W.extract_features(rows, window_s=0.02)
        self.assertEqual(
            [f.ptp for f in feats], [1.0, 2.0],
            f"the floor must clamp the window to exactly 2 samples, giving "
            f"two windows of ptp 1.0 and 2.0; got "
            f"{[f.ptp for f in feats]}. A floor of 3 emits one window of ptp "
            "3.0 instead, and a floor of 1 would hand band_energy a window "
            "its own guard on line 195 refuses to process.")
        self.assertEqual([round(f.window_start_s, 6) for f in feats], [0.0, 0.02])

    def test_the_floor_and_the_bail_out_are_the_same_number(self) -> None:
        """Stated as an assertion because nothing else states it: line 272's
        floor is 2 BECAUSE line 195 refuses n < 2. If they ever disagree,
        one of them is silently producing zeros."""
        rows = [(i * 0.02, 1.0 + 0.1 * (i % 2)) for i in range(6)]
        feats = W.extract_features(rows, window_s=0.001)
        self.assertGreater(len(feats), 0, "the floor must still emit windows")
        for f in feats:
            self.assertGreater(
                f.high_band_energy, 0.0,
                "every window the floor emits must be long enough for "
                f"band_energy to process, i.e. at least {WINDOW_FLOOR_N} "
                "samples. A zero here means the floor dropped below "
                "band_energy's own minimum and the feature is silently dead.")


# -------------------------------------------------- single-sample contract

class SingleSampleWindowsDoNotDivideByZero(unittest.TestCase):
    """`windows.py:236`'s `if n > 1 else 0.0`.

    The module docstring (windows.py:31-33) defines `sd` as the "sample
    standard deviation (Bessel-corrected, divide by N-1)", which is exactly
    why an n == 1 special case has to exist: N-1 is 0 there. Flipping the
    guard to `n >= 1` raises ZeroDivisionError.

    Unreachable through this module's own pipeline (line 272's floor
    guarantees window_n >= 2), so this is an API-contract gap on the PUBLIC
    compute_window_features rather than a pipeline gap -- which is why it is
    filed low and not dismissed. The foiling classifier would be an outside
    caller.
    """

    def test_a_single_sample_window_is_flat_not_fatal(self) -> None:
        f = W.compute_window_features([1.0], 0.0, 0, LOG_HZ)
        self.assertEqual(
            (f.sd, f.rms_dev, f.ptp, f.crest_factor), (0.0, 0.0, 0.0, 0.0),
            f"a 1-sample window has no variability, so every dispersion "
            f"feature is 0.0; got sd={f.sd!r}, rms_dev={f.rms_dev!r}, "
            f"ptp={f.ptp!r}, crest={f.crest_factor!r}. Without the n > 1 "
            "guard this raises ZeroDivisionError on sqrt(0 / (n - 1)).")

    def test_two_samples_use_the_bessel_divisor(self) -> None:
        """The just-over half of the straddle, with absolute values: mean
        1.2, deviations -0.2 and +0.2, so sum(d^2) = 0.08 and
        sd = sqrt(0.08 / 1) = 0.282842712..., rms_dev = sqrt(0.08 / 2) = 0.2.
        A ratio alone would not see a scale error in either."""
        f = W.compute_window_features([1.0, 1.4], 0.0, 0, LOG_HZ)
        self.assertAlmostEqual(
            f.sd, 0.28284271247461906, places=12,
            msg=f"sd must divide by N-1 = 1; got {f.sd!r}.")
        self.assertAlmostEqual(
            f.rms_dev, 0.2, places=12,
            msg=f"rms_dev must divide by N = 2; got {f.rms_dev!r}.")
        self.assertAlmostEqual(
            f.ptp, 0.4, places=12)

    def test_the_two_dispersion_features_differ_by_the_bessel_factor(self) -> None:
        """The docstring's own claim, at several lengths: the pair exists
        precisely because one divides by N and the other by N-1 (windows.py:
        28-35, "both are shipped ... rather than picking one")."""
        rng = random.Random(11)
        for n in (2, 3, 10, 50):
            with self.subTest(n=n):
                win = [1.0 + rng.gauss(0.0, 0.05) for _ in range(n)]
                f = W.compute_window_features(win, 0.0, 0, LOG_HZ)
                self.assertAlmostEqual(
                    f.sd / f.rms_dev, math.sqrt(n / (n - 1.0)), places=9,
                    msg=f"n={n}: sd/rms_dev must be sqrt(n/(n-1)) = "
                        f"{math.sqrt(n / (n - 1.0)):.9f}; got "
                        f"{f.sd / f.rms_dev:.9f}. The two features are the "
                        "same variability under two divisors; if the ratio "
                        "moves, one of the divisors did.")


if __name__ == "__main__":
    unittest.main()
