#!/usr/bin/env python3
"""Run the jump detector on synthetic data (default) or a captured CSV.

Examples:
    python3 sim/run.py                     # synthetic demo session vs ground truth
    python3 sim/run.py --seed 7            # different synthetic noise
    python3 sim/run.py --csv data/my_session.csv   # replay a real capture
    python3 sim/run.py --plot              # also plot (needs matplotlib)

CSV formats accepted:
    t,mag               # |a| in g — what the firmware logs (trace.csv, via
                        # `./tools/jump sync`)
    t_s,ax,ay,az        # per-axis in g (magnitude computed here)

No third-party dependencies required (matplotlib only for --plot).
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from typing import List, Tuple

from detector import Detector, JumpEvent, Params, height_for_airtime, load_params
from generate import DEMO_JUMPS, synth_session


def run_detector(times: List[float], accel_mag: List[float], params: Params) -> List[JumpEvent]:
    det = Detector(params)
    out: List[JumpEvent] = []
    for t, a in zip(times, accel_mag):
        ev = det.update(t, a)
        if ev is not None:
            out.append(ev)
    return out


def load_csv(path: str) -> Tuple[List[float], List[float]]:
    """Load a capture and return (times, accel_magnitude_g)."""
    times: List[float] = []
    mag: List[float] = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if header is None:
            raise SystemExit(f"{path}: empty file")
        cols = [c.strip().lower() for c in header]

        def idx(*names: str) -> int | None:
            for nm in names:
                if nm in cols:
                    return cols.index(nm)
            return None

        it = idx("t_s", "t", "time")
        imag = idx("mag", "accel_mag", "a")
        ix, iy, iz = idx("ax", "accel_x"), idx("ay", "accel_y"), idx("az", "accel_z")
        if it is None or (imag is None and None in (ix, iy, iz)):
            raise SystemExit(
                f"{path}: need a time column plus either 'mag' or 'ax,ay,az'. Got: {cols}"
            )

        for row in reader:
            if not row or len(row) <= it:
                continue
            try:
                t = float(row[it])
                if imag is not None:
                    m = float(row[imag])
                else:
                    ax, ay, az = float(row[ix]), float(row[iy]), float(row[iz])
                    m = math.sqrt(ax * ax + ay * ay + az * az)
            except (ValueError, IndexError):
                continue  # skip comment/blank/malformed lines
            times.append(t)
            mag.append(m)
    if not times:
        raise SystemExit(f"{path}: no data rows parsed")
    return times, mag


def m_to_ft(m: float) -> float:
    return m * 3.28084


def report_detected(jumps: List[JumpEvent]) -> None:
    if not jumps:
        print("No jumps detected.")
        return
    print(f"\nDetected {len(jumps)} jump(s):")
    print(f"  {'#':>2}  {'takeoff':>8}  {'airtime':>8}  {'height':>16}")
    for i, j in enumerate(jumps, 1):
        print(
            f"  {i:>2}  {j.takeoff_time_s:>7.2f}s  {j.airtime_s:>7.2f}s"
            f"  {j.height_m:>6.2f} m ({m_to_ft(j.height_m):>4.1f} ft)"
        )
    best = max(jumps, key=lambda j: j.height_m)
    print(f"  best: {best.height_m:.2f} m ({m_to_ft(best.height_m):.1f} ft)")


def report_vs_truth(detected: List[JumpEvent], truth: List[Tuple[float, float]]) -> int:
    """Compare detected jumps to synthetic ground truth. Returns process exit code."""
    print(f"\nGround truth vs detected ({len(truth)} known jumps):")
    print(f"  {'true T':>7}  {'true h':>8}  {'det T':>7}  {'det h':>8}  {'err':>8}")
    errors: List[float] = []
    matched = 0
    for (t0, at) in truth:
        true_h = height_for_airtime(at)
        # nearest detected takeoff within 1.0 s
        cand = [j for j in detected if abs(j.takeoff_time_s - t0) < 1.0]
        if cand:
            j = min(cand, key=lambda j: abs(j.takeoff_time_s - t0))
            err = j.height_m - true_h
            errors.append(abs(err))
            matched += 1
            print(
                f"  {at:>6.2f}s  {true_h:>6.2f}m  {j.airtime_s:>6.2f}s  "
                f"{j.height_m:>6.2f}m  {err:>+6.2f}m"
            )
        else:
            print(f"  {at:>6.2f}s  {true_h:>6.2f}m  {'—':>7}  {'—':>8}  {'MISSED':>8}")

    spurious = len(detected) - matched
    print(f"\n  matched {matched}/{len(truth)}, spurious detections: {spurious}")
    if errors:
        mae = sum(errors) / len(errors)
        print(f"  mean abs height error: {mae:.3f} m  (max {max(errors):.3f} m)")
    ok = matched == len(truth) and spurious == 0
    print("  RESULT:", "PASS ✅" if ok else "CHECK ⚠️  (tune detector params in config/params.json)")
    return 0 if ok else 1


def maybe_plot(times: List[float], mag: List[float], detected: List[JumpEvent]) -> None:
    try:
        import matplotlib.pyplot as plt  # type: ignore
    except ImportError:
        print("\n(--plot needs matplotlib: pip install matplotlib)", file=sys.stderr)
        return
    p = load_params()
    plt.figure(figsize=(12, 4))
    plt.plot(times, mag, lw=0.7, label="|a| (g)")
    plt.axhline(p.freefall_enter_g, color="green", ls="--", lw=0.8, label="free-fall enter")
    plt.axhline(p.landing_threshold_g, color="red", ls="--", lw=0.8, label="landing")
    for j in detected:
        plt.axvspan(j.takeoff_time_s, j.takeoff_time_s + j.airtime_s, color="orange", alpha=0.2)
        plt.text(j.takeoff_time_s, p.landing_threshold_g, f"{j.height_m:.1f}m", fontsize=8)
    plt.xlabel("time (s)")
    plt.ylabel("|a| (g)")
    plt.legend(loc="upper right", fontsize=8)
    plt.title("Jump detection")
    plt.tight_layout()
    plt.show()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--csv", help="replay a captured CSV instead of synthetic data")
    ap.add_argument("--seed", type=int, default=0, help="synthetic noise seed")
    ap.add_argument("--plot", action="store_true", help="plot the signal + detections")
    args = ap.parse_args()

    params = load_params()

    if args.csv:
        times, mag = load_csv(args.csv)
        print(f"Loaded {len(times)} samples from {args.csv} "
              f"({times[-1] - times[0]:.1f}s @ ~{len(times) / max(1e-9, times[-1] - times[0]):.0f} Hz)")
        detected = run_detector(times, mag, params)
        report_detected(detected)
        if args.plot:
            maybe_plot(times, mag, detected)
        return 0

    # Synthetic mode: known ground truth, verify the detector.
    truth = DEMO_JUMPS
    times, mag = synth_session(truth, seed=args.seed)
    print(f"Synthetic session: {times[-1]:.0f}s, {len(times)} samples, "
          f"{len(truth)} known jumps (seed={args.seed}).")
    detected = run_detector(times, mag, params)
    report_detected(detected)
    code = report_vs_truth(detected, truth)
    if args.plot:
        maybe_plot(times, mag, detected)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
