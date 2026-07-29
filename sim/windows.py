#!/usr/bin/env python3
"""Time-on-foil feature harness: per-window vibration features from a
stored trace CSV, ready for session 1 (docs/roadmap.md's Tier-A backlog:
"Time on foil: classify 1 s windows by vibration signature (foil flight is
smooth, taxiing choppy, bobbing still)"; docs/research.md §8: "method
validated at our stored rate (Gomes 2019, 50 Hz, 1 s windows, 90 % vs
video); threshold must be self-derived (no literature)").

This module only EXTRACTS features -- it does not classify or threshold
anything. Per the backlog rule this whole feature is filed under ("Tune
everything against the first real water-session trace + video -- desk
guesses about what foiling 'feels like' to the sensor would be fiction"),
any actual foil/taxi/still cutoffs are for a future pass over a real trace.

Input: a synced-session trace CSV -- t,mag (or t_s,mag_g; both header
spellings accepted) columns, |a| in g, nominally 50 Hz
(config/params.json's firmware.log_hz) -- the same file
`./tools/jump sync` writes to <session>/trace.csv. A trace may contain
multiple power-on segments where t resets to 0 (the device's clock
restarts each boot); this is split the same way tools/jump's own
autopsy/sync paths do (split_segments() below is a deliberate,
independent re-implementation of tools/jump's split_segments() -- same
rule, not an import, per this task's file boundaries), so a window is
never built across a reboot boundary.

Per-window features (1 s windows, 50% overlap -- only whole windows are
emitted; a trailing partial window at a segment's end is dropped):
  * rms_dev    -- population RMS deviation from the window mean (root of
                  the mean squared deviation, i.e. divide by N): an
                  "energy" framing of variability.
  * sd         -- sample standard deviation (Bessel-corrected, divide by
                  N-1): the canonical-statistics framing of the same
                  variability. Both are shipped (they differ by ~1% at a
                  50-sample window) rather than picking one, since the
                  spec names them as separate columns.
  * ptp        -- peak-to-peak: max(window) - min(window).
  * crest_factor -- peak absolute deviation from the mean, divided by
                  rms_dev: a "spikiness" shape metric (impulsive board
                  slap/chop reads high; smooth continuous vibration reads
                  low, with a clean sinusoid's theoretical floor of
                  sqrt(2)).
  * low_band_energy, high_band_energy -- spectral energy split via a
                  Goertzel-algorithm bin power calculation (see
                  goertzel_power() below for why Goertzel instead of a
                  full FFT), each normalized by N (an "average power"
                  framing), computed on the mean-removed window.

CLI:
    python3 sim/windows.py trace.csv -o features.csv

Pure standard library (math + csv + argparse) -- no numpy, per this
repo's no-new-dependencies norm.
"""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

Row = Tuple[float, float]  # (t_s, mag_g)


# --------------------------------------------------------------- segments

def split_segments(rows: List[Row]) -> List[List[Row]]:
    """Split a trace wherever time goes backwards (device rebooted).

    This is a deliberate, independent re-implementation of tools/jump's
    split_segments() -- same rule (any t < previous t starts a new
    segment), re-derived here rather than imported so sim/ stays
    import-independent of the tools/ CLI script (this task's boundary:
    "read tools/jump's split logic ... do not import the CLI"). tools/jump
    applies the identical rule in its own sync/autopsy paths for the same
    reason: a dip right before a reboot must never "land" in the next
    boot's timeline (tools/jump's autopsy comment: a real desk session
    once printed "-55.86s of air" exactly this way).
    """
    segs: List[List[Row]] = []
    cur: List[Row] = []
    last_t: Optional[float] = None
    for t, m in rows:
        if last_t is not None and t < last_t:
            if cur:
                segs.append(cur)
            cur = []
        cur.append((t, m))
        last_t = t
    if cur:
        segs.append(cur)
    return segs


# ------------------------------------------------------------------ CSV I/O

def load_trace_csv(path: str) -> List[Row]:
    """Load a trace CSV. Accepts this repo's on-disk convention (header
    "t,mag", written by ./tools/jump sync's trace.csv and the firmware's
    own trace store) as well as the more explicit "t_s,mag_g" spelling --
    same flexible column-name matching sim/run.py's load_csv() already
    uses for its own CSV inputs. Non-numeric/blank/comment rows are
    skipped rather than raising, matching the rest of this codebase's
    tolerance for slightly-messy captured data.
    """
    rows: List[Row] = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            return rows
        cols = [c.strip().lower() for c in header]

        def idx(*names: str) -> Optional[int]:
            for nm in names:
                if nm in cols:
                    return cols.index(nm)
            return None

        it = idx("t_s", "t", "time")
        im = idx("mag_g", "mag", "accel_mag", "a")
        if it is None or im is None:
            # No recognizable header -- treat the "header" row itself as
            # data (some captures have no header at all).
            it, im = 0, 1
            f.seek(0)
            reader = csv.reader(f)

        for row in reader:
            if len(row) <= max(it, im):
                continue
            try:
                t = float(row[it])
                m = float(row[im])
            except ValueError:
                continue
            rows.append((t, m))
    return rows


def write_features_csv(path: str, feats: List["WindowFeatures"]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["segment", "window_start_s", "rms_dev", "sd", "ptp",
                    "crest_factor", "low_band_energy", "high_band_energy"])
        for wf in feats:
            w.writerow([
                wf.segment, f"{wf.window_start_s:.3f}", f"{wf.rms_dev:.6f}",
                f"{wf.sd:.6f}", f"{wf.ptp:.6f}", f"{wf.crest_factor:.6f}",
                f"{wf.low_band_energy:.6f}", f"{wf.high_band_energy:.6f}",
            ])


# ------------------------------------------------------------------- FFT

def goertzel_power(x: Sequence[float], k: int, n: int) -> float:
    """Power at DFT bin k (0..n-1) of a length-n real sequence x, via the
    Goertzel algorithm: |X[k]|^2 where X[k] = sum_i x[i] * exp(-2j*pi*k*i/n),
    computed in O(n) per bin without ever forming the complex sequence.

    Why Goertzel instead of a real FFT: we only ever need a handful of
    low-order bins (the low/high split below), not the whole spectrum, so
    there's no benefit to an O(n log n) FFT's extra bins -- and Goertzel
    is a much smaller, easier-to-eyeball-correct recurrence to keep
    stdlib-only (verified against a naive O(n^2) DFT sum during
    development; the two agree to float precision). If a future feature
    needs finer spectral resolution or the full spectrum, a real FFT
    would be the better tool -- for a coarse two-band split this is
    simpler and sufficient.
    """
    w = 2.0 * math.pi * k / n
    cw = math.cos(w)
    c = 2.0 * cw
    sw = math.sin(w)
    s1 = s2 = 0.0
    for xi in x:
        s0 = xi + c * s1 - s2
        s2 = s1
        s1 = s0
    real = s1 - s2 * cw
    imag = s2 * sw
    return real * real + imag * imag


def band_energy(x: Sequence[float], fs_hz: float, lo_hz: float, hi_hz: float) -> float:
    """Average power (per sample) in the (lo_hz, hi_hz] band, summed over
    whichever integer DFT bins fall in that range for a length-n window at
    fs_hz -- generalizes correctly even when n*fs_hz doesn't give exactly
    integer-Hz bins (bin k <-> frequency k*fs_hz/n). Always excludes bin 0
    (DC / the window mean, already captured by the window's own mean) and
    never exceeds the Nyquist bin n//2.
    """
    n = len(x)
    if n < 2:
        return 0.0
    lo_bin = max(1, math.ceil(lo_hz * n / fs_hz))
    hi_bin = min(n // 2, math.floor(hi_hz * n / fs_hz))
    total = 0.0
    for k in range(lo_bin, hi_bin + 1):
        total += goertzel_power(x, k, n)
    return total / n


# -------------------------------------------------------------- features

@dataclass
class WindowFeatures:
    segment: int
    window_start_s: float
    rms_dev: float
    sd: float
    ptp: float
    crest_factor: float
    low_band_energy: float
    high_band_energy: float


def compute_window_features(
    window: Sequence[float],
    t_start: float,
    segment: int,
    fs_hz: float,
    low_band_hz: Tuple[float, float] = (1.0, 4.0),
    high_band_hz: Optional[Tuple[float, float]] = None,
) -> WindowFeatures:
    """Compute one window's features. low_band_hz/high_band_hz are a
    provisional split (foil-flight vibration expected low/smooth, taxiing
    expected broader/higher -- docs/roadmap.md's own framing) pending real
    session-1 data to tune against; see the module docstring."""
    n = len(window)
    mean = sum(window) / n
    dev = [x - mean for x in window]

    rms_dev = math.sqrt(sum(d * d for d in dev) / n)
    sd = math.sqrt(sum(d * d for d in dev) / (n - 1)) if n > 1 else 0.0
    ptp = max(window) - min(window)
    peak = max(abs(d) for d in dev)
    crest = (peak / rms_dev) if rms_dev > 1e-12 else 0.0

    if high_band_hz is None:
        high_band_hz = (low_band_hz[1], fs_hz / 2.0)
    low_e = band_energy(dev, fs_hz, low_band_hz[0], low_band_hz[1])
    high_e = band_energy(dev, fs_hz, high_band_hz[0], high_band_hz[1])

    return WindowFeatures(segment, t_start, rms_dev, sd, ptp, crest, low_e, high_e)


def _infer_fs_hz(seg: List[Row], default_hz: float = 50.0) -> float:
    dts = [seg[i][0] - seg[i - 1][0] for i in range(1, len(seg))]
    dts = [d for d in dts if d > 0.0]
    if not dts:
        return default_hz
    dts.sort()
    median_dt = dts[len(dts) // 2]
    return 1.0 / median_dt if median_dt > 0.0 else default_hz


def windows_for_segment(
    seg: List[Row], segment_id: int, window_s: float = 1.0, overlap: float = 0.5,
) -> List[WindowFeatures]:
    """1 s windows (window_s) with 50% overlap (overlap), index-based (not
    wall-clock-based): the sample rate is inferred from the segment's own
    median inter-sample spacing (nominally 50 Hz), window/hop lengths are
    rounded to whole samples, and only FULL windows are emitted -- a
    trailing partial window at the segment's end is dropped rather than
    computed on fewer samples than the rest.
    """
    if len(seg) < 2:
        return []
    fs_hz = _infer_fs_hz(seg)
    window_n = max(2, round(window_s * fs_hz))
    hop_n = max(1, round(window_n * (1.0 - overlap)))

    feats: List[WindowFeatures] = []
    i = 0
    while i + window_n <= len(seg):
        chunk = [m for _, m in seg[i:i + window_n]]
        t_start = seg[i][0]
        feats.append(compute_window_features(chunk, t_start, segment_id, fs_hz))
        i += hop_n
    return feats


def extract_features(rows: List[Row], window_s: float = 1.0, overlap: float = 0.5) -> List[WindowFeatures]:
    feats: List[WindowFeatures] = []
    for seg_id, seg in enumerate(split_segments(rows)):
        feats.extend(windows_for_segment(seg, seg_id, window_s, overlap))
    return feats


# ---------------------------------------------------------------------- CLI

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="trace CSV (t,mag or t_s,mag_g columns), ~50 Hz")
    ap.add_argument("-o", "--out", default="features.csv", help="output features CSV path")
    ap.add_argument("--window-s", type=float, default=1.0, help="window length in seconds (default 1.0)")
    ap.add_argument("--overlap", type=float, default=0.5, help="window overlap fraction (default 0.5)")
    args = ap.parse_args()

    rows = load_trace_csv(args.csv)
    segments = split_segments(rows)
    feats: List[WindowFeatures] = []
    for seg_id, seg in enumerate(segments):
        feats.extend(windows_for_segment(seg, seg_id, args.window_s, args.overlap))

    write_features_csv(args.out, feats)
    print(f"Loaded {len(rows)} sample(s) across {len(segments)} segment(s) from {args.csv}; "
          f"wrote {len(feats)} window(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
