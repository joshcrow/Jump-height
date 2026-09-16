"""Render measured clock residuals; no filtering, repair or calibration applied."""
from pathlib import Path
import csv

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    root = Path(__file__).resolve().parent
    study = root / "captures/clock-study-20260915"
    with (study / "clean-run1/clock_analysis/residuals.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    paired = [r for r in rows if r.get("host_monotonic_relative_s")]
    with (study / "clean-run2/raw.csv").open() as stream:
        bad = list(csv.DictReader(stream))

    plt.rcParams.update({"font.size": 11, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.titleweight": "bold"})
    fig, axes = plt.subplots(2, 2, figsize=(12, 7.5), constrained_layout=True)
    fig.suptitle("Clock study: measured structure, not a calibration pass", fontsize=17)

    a = axes[0, 0]
    a.plot([float(r["mcu_nominal_s"]) for r in rows],
           [1e3*float(r["mcu_minus_fifo_fit_residual_s"]) for r in rows],
           color="#156b80", linewidth=.5)
    a.set(title="Run 1: MCU versus nominal FIFO time",
          ylabel="Residual after linear fit (ms)", xlabel="MCU elapsed time (s)")

    a = axes[0, 1]
    key = "host_monotonic_seconds_per_nominal_mcu_second_residual_s"
    a.plot([float(r["mcu_nominal_s"]) for r in paired],
           [1e3*float(r[key]) for r in paired], color="#47743a", linewidth=.5)
    a.set(title="Run 1: host delivery versus MCU service",
          ylabel="Residual after linear fit (ms)", xlabel="MCU elapsed time (s)")

    a = axes[1, 0]
    a.plot([float(r["host_monotonic_relative_s"]) for r in paired],
           [1e3*(float(r["host_realtime_relative_s"])-float(r["host_monotonic_relative_s"]))
            for r in paired], color="#95501f", linewidth=1.3)
    a.set(title="Run 1: host wall-clock adjustment",
          ylabel="Realtime minus monotonic elapsed (ms)", xlabel="Host monotonic elapsed time (s)")

    a = axes[1, 1]
    selected = range(3955, 3966)
    delta = [int(bad[i]["sensor_ticks"])-int(bad[i-1]["sensor_ticks"]) for i in selected]
    a.axhline(195, color="#737373", linestyle="--", linewidth=1, label="Typical increment: 195 ticks")
    a.plot(list(selected), delta, "o-", color="#b53f3b", linewidth=1)
    a.set(title="Run 2: rejected timestamp reversal",
          ylabel="Raw timestamp increment (ticks)", xlabel="Sample sequence")
    a.ticklabel_format(useOffset=False, style="plain", axis="x")
    a.legend(fontsize=9, loc="upper left")
    for a in axes.flat:
        a.grid(alpha=.18)
    out = root / "results/clock-study-20260915"
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / "clock-residuals.png", dpi=150)
    print(out / "clock-residuals.png")


if __name__ == "__main__":
    main()
