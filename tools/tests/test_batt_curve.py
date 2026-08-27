"""The battery gauge curve must match the measured discharge, not a guess.

The shipped table was a generic LiPo curve. Measured against a real
run-to-death on the OG (57.1 h idle with DC/DC on, 2026-08-27 — the puck
actually died, this is not a stopped run), it was wrong by up to **27.9
hours**: it read <=20% with 38.9 hours of life remaining, <=5% with 28.1
hours remaining, and 0% for the final five while the board answered every
BLE poll.

That number is not internal telemetry. `docs/rider-brief.md` item 1 tells the
rider his watch will show the puck's battery, so a wrong gauge is a wrong
number on the product's only screen — and this project has a history of
"the battery died" scares that trace straight back to believing it.

This test parses the ACTUAL C++ table out of jh_power.cpp and re-scores it
against the committed discharge curve. It exists because a re-anchored curve
that nothing checks is just a newer guess: the next person to edit that array
has to answer to the measurement.
"""
from __future__ import annotations

import csv
import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SRC = REPO / "firmware/src/platform/nrf52/jh_power.cpp"
CURVE = (REPO / "data/soaks/dcdc-deathrun-20260824-192240/curve.csv")
TOTAL_H = 57.09          # measured, from the run that produced CURVE


def shipped_curve():
    """The {mv, pct} pairs actually compiled into the firmware."""
    txt = SRC.read_text()
    m = re.search(r"const CurvePoint kCurve\[\]\s*=\s*\{(.*?)\};", txt,
                  re.S)
    assert m, "kCurve table not found — did jh_power.cpp move?"
    pairs = re.findall(r"\{\s*(\d+)\s*,\s*(\d+)\s*\}", m.group(1))
    assert pairs, "kCurve parsed but empty"
    return [(int(mv), int(pct)) for mv, pct in pairs]


def pct_for(mv, curve):
    """Mirror of jh_power.cpp's batt_pct() interpolation."""
    if mv >= curve[0][0]:
        return 100
    for i in range(1, len(curve)):
        if mv >= curve[i][0]:
            hi, lo = curve[i - 1], curve[i]
            return lo[1] + (mv - lo[0]) * (hi[1] - lo[1]) / (hi[0] - lo[0])
    return 0


def measured():
    with open(CURVE) as f:
        return [(float(r["hours_on_battery"]), int(r["vbat_mv"]))
                for r in csv.DictReader(f)]


class BattCurveTest(unittest.TestCase):
    def test_curve_is_monotonic(self):
        """Both columns must descend. A non-monotonic table makes the gauge
        climb while the cell drains, which is worse than coarse."""
        c = shipped_curve()
        for a, b in zip(c, c[1:]):
            self.assertGreater(a[0], b[0], f"mV not descending at {a}->{b}")
            self.assertGreater(a[1], b[1], f"pct not descending at {a}->{b}")

    def test_error_against_the_real_discharge_is_bounded(self):
        """The whole point. Worst error must stay small in HOURS, which is
        the unit the rider cares about — 'is there enough for a session?'"""
        c = shipped_curve()
        worst = 0.0
        worst_at = None
        for h, mv in measured():
            true_pct = 100.0 * (TOTAL_H - h) / TOTAL_H
            err_h = abs(true_pct - pct_for(mv, c)) / 100.0 * TOTAL_H
            if err_h > worst:
                worst, worst_at = err_h, (h, mv)
        self.assertLess(
            worst, 4.0,
            f"gauge off by {worst:.1f} h at {worst_at} — the old generic "
            f"table was off by 27.9 h; do not regress toward it")

    def test_gauge_never_wildly_overstates_remaining_life(self):
        """Direction matters more than magnitude. Reading LOW early is
        survivable; reading HIGH near the end sends a rider out on a cell
        that quits mid-session."""
        c = shipped_curve()
        for h, mv in measured():
            true_pct = 100.0 * (TOTAL_H - h) / TOTAL_H
            over_h = (pct_for(mv, c) - true_pct) / 100.0 * TOTAL_H
            self.assertLess(
                over_h, 3.0,
                f"at {mv} mV the gauge claims {over_h:.1f} h MORE life than "
                f"the measurement showed")

    def test_full_cell_reads_full(self):
        """The measured rested-full start was 4091 mV. If that reads under
        100% the anchor is wrong again (SENSE_FIRST_BOOT item 24's bug)."""
        self.assertEqual(pct_for(4091, shipped_curve()), 100)

    def test_dead_cell_reads_zero_at_the_cutoff(self):
        """3.0 V is the LP502030's over-discharge cutoff. At or below it the
        gauge must read 0 — the puck is done."""
        self.assertEqual(pct_for(3000, shipped_curve()), 0)
        self.assertEqual(pct_for(2617, shipped_curve()), 0)


if __name__ == "__main__":
    unittest.main()
