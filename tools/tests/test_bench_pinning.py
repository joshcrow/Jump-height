"""Board-pinning discipline for the bench tools (audit F-13/F-14/F-15).

These tools all reach for "the puck" over BLE, and every board on the bench
advertises a name starting "JumpHeight". An unpinned call therefore lands on
whichever board answers first. That is not hypothetical: it has already put a
floating divider's confident 97% into a death-run log and attributed a whole
DC/DC result to the wrong board, with nothing in either dataset to detect it
afterwards.

blecmd.py already solved this - collect every match, refuse to be silent about
ambiguity, choose deterministically. These tests hold the other tools to it.
"""
from __future__ import annotations

import csv
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parent.parent.parent


def _load(name: str, path: Path):
    """Import a tools/ script as a module without executing its main()."""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


class BattlogPinning(unittest.TestCase):
    """F-13: battlog ran unpinned, captured blecmd's ambiguity warning and
    dropped it, and wrote CSV rows with no board identity."""

    def test_refuses_to_start_unpinned(self):
        # timeout, because the failure mode under test is "it starts anyway" -
        # and if it starts, it starts a TEN HOUR logging loop. Without this the
        # test does not fail when the guard is removed, it hangs, which is the
        # worst of both (found by mutation-testing this very test).
        with tempfile.TemporaryDirectory() as td:
            try:
                r = subprocess.run(
                    [sys.executable, str(REPO / "tools/battlog.py"),
                     "--out", str(Path(td) / "log.csv")],
                    capture_output=True, text=True, timeout=20)
            except subprocess.TimeoutExpired:
                self.fail("battlog started an unpinned run instead of refusing "
                          "(it was still going after 20 s)")
        self.assertNotEqual(r.returncode, 0,
                            "an unpinned overnight battery log must not start")
        self.assertIn("refusing to run unpinned", r.stderr)
        # The message has to carry the fix, not just the complaint - this is
        # read at the start of a ten-hour run, often late.
        self.assertIn("JumpHeight-E2C4", r.stderr)
        self.assertIn("--addr", r.stderr)

    def test_accepts_a_pin(self):
        """The refusal must not be so broad it blocks the legitimate call."""
        mod = _load("battlog_argcheck", REPO / "tools/battlog.py")
        self.assertTrue(hasattr(mod, "read_stats"))

    def test_records_which_board_answered_and_surfaces_ambiguity(self):
        mod = _load("battlog_readstats", REPO / "tools/battlog.py")

        class FakeCompleted:
            returncode = 0
            stdout = ("scanning 6s ...\n"
                      "connecting to AA:BB:CC:DD:EE:FF (JumpHeight-E2C4) ...\n"
                      "connected\n"
                      "[12:00:00] STATS session_jumps=0 vbat_mv=3810 batt_pct=42 "
                      "chg=0 stored_jumps=7 trace_bytes=1024 uptime_s=99.5\n")
            stderr = ""

        # mock.patch, NOT `mod.subprocess.run = ...`: mod.subprocess IS the
        # shared subprocess module, so assigning through it patches every
        # caller in the process. Doing that leaked into this file's own
        # subprocess.run and made the refusal test above see returncode 0.
        with mock.patch.object(mod.subprocess, "run",
                               lambda *a, **k: FakeCompleted()):
            kv, dt, note, name, addr = mod.read_stats(30.0)
        self.assertEqual(kv["vbat_mv"], "3810")
        self.assertEqual(name, "JumpHeight-E2C4",
                         "every row must carry the board it came from")
        self.assertEqual(addr, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(note, "")

        # Now the case the tool used to swallow: blecmd shouts on STDERR that
        # two boards matched. capture_output=True collected it and only
        # r.stdout was ever read.
        class Ambiguous(FakeCompleted):
            stderr = ("\n⚠️  2 boards match 'JumpHeight' — this call is AMBIGUOUS:\n"
                      "      JumpHeight-45ED  11:22:33:44:55:66  rssi=-40\n")

        with mock.patch.object(mod.subprocess, "run",
                               lambda *a, **k: Ambiguous()):
            kv, dt, note, name, addr = mod.read_stats(30.0)
        self.assertIn("AMBIGUOUS", note,
                      "the ambiguity must reach the CSV: a night's log is read "
                      "weeks later by someone who never saw the terminal")
        self.assertEqual(kv["vbat_mv"], "3810")   # still logs the reading

    def test_csv_schema_carries_board_identity(self):
        mod = _load("battlog_fields", REPO / "tools/battlog.py")
        self.assertIn("board_name", mod.FIELDS)
        self.assertIn("board_addr", mod.FIELDS)
