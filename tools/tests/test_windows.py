"""Unit tests for sim/windows.py -- the time-on-foil feature harness
(docs/roadmap.md Tier-A backlog; docs/research.md §8).

Builds a synthetic multi-segment trace with three known regimes (still,
foil-like, taxi-like) and checks that windows.py's per-window features
SEPARATE the regimes in the expected direction. Per docs/roadmap.md's
Tier-A rule ("tune everything against the first real water-session trace
+ video -- desk guesses about what foiling 'feels like' to the sensor
would be fiction"), these assertions are ORDERING ONLY -- never absolute
thresholds, which must come from real data.

Run via ./tools/jump simtest, or directly:
    python3 -m pytest tools/tests/test_windows.py -q
"""

from __future__ import annotations

import csv
import math
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import List, Tuple

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

import windows  # noqa: E402  (path insert must come first)

FS_HZ = 50.0  # matches config/params.json's firmware.log_hz default


# ------------------------------------------------------- synthetic regimes

def _still(n: int, seed: int, amp: float = 0.01) -> List[float]:
    """Near-motionless: tiny ambient/sensor noise only."""
    rng = random.Random(seed)
    return [1.0 + rng.gauss(0.0, amp) for _ in range(n)]


def _foil(n: int, seed: int, freq_hz: float = 2.0, amp: float = 0.06,
          dither: float = 0.01) -> List[float]:
    """Smooth, low-amplitude, low-frequency oscillation -- foil-borne
    flight's "smooth" vibration signature (docs/roadmap.md's own framing).
    freq_hz=2.0 lands exactly on DFT bin 2 for a 1 s/50 Hz window, well
    inside the low band."""
    rng = random.Random(seed)
    return [1.0 + amp * math.sin(2.0 * math.pi * freq_hz * (i / FS_HZ)) + rng.gauss(0.0, dither)
            for i in range(n)]


def _taxi(n: int, seed: int, amp: float = 0.25) -> List[float]:
    """Broadband, higher-amplitude jitter -- taxiing/hull-slap's "choppy"
    signature."""
    rng = random.Random(seed)
    return [1.0 + rng.gauss(0.0, amp) for _ in range(n)]


def _segment_rows(values: List[float], fs_hz: float = FS_HZ) -> List[Tuple[float, float]]:
    return [(i / fs_hz, v) for i, v in enumerate(values)]


def _naive_dft_power(x: List[float], k: int, n: int) -> float:
    re = sum(x[i] * math.cos(-2.0 * math.pi * k * i / n) for i in range(n))
    im = sum(x[i] * math.sin(-2.0 * math.pi * k * i / n) for i in range(n))
    return re * re + im * im


# --------------------------------------------------------------- split_segments

class TestSplitSegments(unittest.TestCase):
    """The "same splitting rule the CLI autopsy uses" requirement, tested
    in isolation: split wherever time goes backwards."""

    def test_single_segment_no_reset(self):
        rows = [(i * 0.02, 1.0) for i in range(10)]
        segs = windows.split_segments(rows)
        self.assertEqual(len(segs), 1)
        self.assertEqual(len(segs[0]), 10)

    def test_two_resets_make_three_segments(self):
        rows = [
            (0.0, 1.0), (0.1, 2.0), (0.2, 3.0),           # segment A
            (0.0, 4.0), (0.1, 5.0),                        # segment B (reboot)
            (0.0, 6.0), (0.1, 7.0), (0.2, 8.0), (0.3, 9.0),  # segment C (reboot)
        ]
        segs = windows.split_segments(rows)
        self.assertEqual([len(s) for s in segs], [3, 2, 4])
        self.assertEqual([m for _, m in segs[0]], [1.0, 2.0, 3.0])
        self.assertEqual([m for _, m in segs[1]], [4.0, 5.0])
        self.assertEqual([m for _, m in segs[2]], [6.0, 7.0, 8.0, 9.0])

    def test_empty_input(self):
        self.assertEqual(windows.split_segments([]), [])


# --------------------------------------------------------------- Goertzel FFT

class TestGoertzelCorrectness(unittest.TestCase):
    """Sanity-checks goertzel_power() against a direct O(n^2) DFT sum --
    independent of the ordering property tests below, since a subtly
    wrong bin-power formula could still happen to preserve ordering by
    luck."""

    def test_matches_naive_dft(self):
        rng = random.Random(0)
        n = 50
        x = [math.sin(2.0 * math.pi * 3 * i / n) * 0.06 + rng.gauss(0.0, 0.02) for i in range(n)]
        for k in range(0, n // 2 + 1):
            g = windows.goertzel_power(x, k, n)
            d = _naive_dft_power(x, k, n)
            self.assertAlmostEqual(g, d, places=6, msg=f"bin k={k}")

    def test_pure_tone_concentrates_in_its_own_bin(self):
        n = 50
        freq_hz = 3.0  # lands exactly on bin 3 for a 1 s / 50 Hz window
        x = [math.sin(2.0 * math.pi * freq_hz * (i / FS_HZ)) for i in range(n)]
        powers = [windows.goertzel_power(x, k, n) for k in range(1, n // 2 + 1)]
        peak_bin = 1 + powers.index(max(powers))
        self.assertEqual(peak_bin, 3)


# ------------------------------------------------- feature ordering (main test)

class TestWindowFeaturesSeparateRegimes(unittest.TestCase):
    """docs/roadmap.md's Tier-A "time on foil" framing: foil flight
    smooth, taxiing choppy, bobbing still. Builds a synthetic trace with
    three known regimes as three separate (reboot-separated) segments --
    this both exercises multi-segment handling AND gives each regime a
    clean, non-straddling set of windows to compare -- and checks that
    windows.py's features separate them in the expected direction.
    ORDERING ONLY, never absolute thresholds (docs/research.md §8 /
    roadmap.md's Tier-A rule: those must come from a real session trace).
    """

    @classmethod
    def setUpClass(cls):
        n = int(8 * FS_HZ)
        rows = (_segment_rows(_still(n, 1)) + _segment_rows(_foil(n, 2))
                + _segment_rows(_taxi(n, 3)))
        cls.segments = windows.split_segments(rows)
        cls.feats = windows.extract_features(rows)
        cls.by_seg = {i: [wf for wf in cls.feats if wf.segment == i] for i in range(3)}

    def _mean(self, seg_id: int, attr: str) -> float:
        vals = [getattr(wf, attr) for wf in self.by_seg[seg_id]]
        return sum(vals) / len(vals)

    def test_three_reboot_separated_segments_recovered(self):
        self.assertEqual(len(self.segments), 3)
        for i in range(3):
            self.assertGreater(len(self.by_seg[i]), 0, f"segment {i} produced no windows")

    def test_rms_dev_orders_still_lt_foil_lt_taxi(self):
        still, foil, taxi = (self._mean(i, "rms_dev") for i in range(3))
        self.assertLess(still, foil)
        self.assertLess(foil, taxi)

    def test_sd_orders_still_lt_foil_lt_taxi(self):
        still, foil, taxi = (self._mean(i, "sd") for i in range(3))
        self.assertLess(still, foil)
        self.assertLess(foil, taxi)

    def test_peak_to_peak_orders_still_lt_foil_lt_taxi(self):
        still, foil, taxi = (self._mean(i, "ptp") for i in range(3))
        self.assertLess(still, foil)
        self.assertLess(foil, taxi)

    def test_crest_factor_orders_foil_lt_taxi(self):
        # "still" is deliberately excluded here: at near-zero variance,
        # still and taxi are BOTH essentially i.i.d.-noise-shaped (just
        # different amplitudes), so crest factor alone isn't a meaningful
        # still-vs-taxi discriminator. foil's clean low-frequency
        # oscillation is the one that's genuinely less "spiky" -- exactly
        # what crest factor is for.
        foil, taxi = (self._mean(i, "crest_factor") for i in (1, 2))
        self.assertLess(foil, taxi)

    def test_low_band_fraction_orders_foil_gt_taxi(self):
        def low_frac(seg_id: int) -> float:
            fracs = []
            for wf in self.by_seg[seg_id]:
                total = wf.low_band_energy + wf.high_band_energy
                if total > 0:
                    fracs.append(wf.low_band_energy / total)
            return sum(fracs) / len(fracs)

        foil_frac, taxi_frac = low_frac(1), low_frac(2)
        self.assertGreater(foil_frac, taxi_frac)


# --------------------------------------------------------------------- edges

class TestEdgeCases(unittest.TestCase):
    def test_too_short_segment_yields_no_windows(self):
        rows = _segment_rows(_still(10, 0))  # 10 samples << one 50-sample window
        self.assertEqual(windows.extract_features(rows), [])

    def test_empty_trace(self):
        self.assertEqual(windows.extract_features([]), [])

    def test_load_trace_csv_accepts_t_s_mag_g_header(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.csv"
            p.write_text("t_s,mag_g\n0.000,1.0\n0.020,1.01\n0.040,0.99\n")
            rows = windows.load_trace_csv(str(p))
        self.assertEqual(rows, [(0.0, 1.0), (0.02, 1.01), (0.04, 0.99)])

    def test_load_trace_csv_accepts_repo_convention_header(self):
        # header written by ./tools/jump sync's trace.csv / the firmware's
        # own trace store (see firmware/src/platform/*/jh_store.cpp).
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "trace.csv"
            p.write_text("t,mag\n0.000,1.0\n0.020,1.01\n")
            rows = windows.load_trace_csv(str(p))
        self.assertEqual(rows, [(0.0, 1.0), (0.02, 1.01)])


# ----------------------------------------------------------------------- CLI

class TestCLIEndToEnd(unittest.TestCase):
    """python3 sim/windows.py trace.csv -o features.csv, exactly as specified."""

    def test_cli_writes_features_csv(self):
        n = int(4 * FS_HZ)
        rows = _segment_rows(_foil(n, 42))
        with tempfile.TemporaryDirectory() as td:
            trace_path = Path(td) / "trace.csv"
            out_path = Path(td) / "features.csv"
            with open(trace_path, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["t", "mag"])
                for t, m in rows:
                    w.writerow([f"{t:.3f}", f"{m:.4f}"])

            r = subprocess.run(
                [sys.executable, str(REPO / "sim" / "windows.py"), str(trace_path),
                 "-o", str(out_path)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertTrue(out_path.exists())

            with open(out_path, newline="") as f:
                reader = csv.reader(f)
                header = next(reader)
                data_rows = list(reader)

        self.assertEqual(header, ["segment", "window_start_s", "rms_dev", "sd", "ptp",
                                  "crest_factor", "low_band_energy", "high_band_energy"])
        # 4 s @ 50 Hz = 200 samples; 1 s window (50 samples) / 50% overlap
        # (25-sample hop) -> (200-50)/25 + 1 = 7 whole windows.
        self.assertEqual(len(data_rows), 7)


if __name__ == "__main__":
    unittest.main()
