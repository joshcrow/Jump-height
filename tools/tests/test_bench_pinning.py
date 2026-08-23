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


class SharedCensus(unittest.TestCase):
    """F-14: the census logic lives in one place now (tools/blepin.py) instead
    of being re-derived per tool. These test the pure decision, which is the
    part that has been wrong three times."""

    def setUp(self):
        self.blepin = _load("blepin_under_test", REPO / "tools/blepin.py")

    def _dev(self, addr, name, rssi=-50):
        class D:
            pass
        d = D()
        d.address = addr
        d.name = name
        return (d, name, rssi)

    def test_single_match_is_returned_quietly(self):
        import io
        m = {"AA": self._dev("AA", "JumpHeight-E2C4")}
        buf = io.StringIO()
        dev = self.blepin.resolve(m, "JumpHeight", tool="t", stream=buf)
        self.assertEqual(dev.address, "AA")
        self.assertEqual(buf.getvalue(), "",
                         "an unambiguous call must not nag")

    def test_no_match_raises_rather_than_exits(self):
        # Raised, not sys.exit()'d, so a --watch loop can treat a momentary
        # gap as retryable instead of ending the run.
        with self.assertRaises(self.blepin.NoBoardFound):
            self.blepin.resolve({}, "JumpHeight", tool="t")

    def test_ambiguity_is_always_announced(self):
        import io
        m = {"AA": self._dev("AA", "JumpHeight-E2C4"),
             "BB": self._dev("BB", "JumpHeight-45ED")}
        buf = io.StringIO()
        self.blepin.resolve(m, "JumpHeight", tool="t", on_ambiguous="choose",
                            stream=buf)
        out = buf.getvalue()
        self.assertIn("AMBIGUOUS", out)
        self.assertIn("JumpHeight-E2C4", out)
        self.assertIn("JumpHeight-45ED", out)

    def test_choose_is_deterministic(self):
        import io
        m = {"AA": self._dev("AA", "JumpHeight-E2C4"),
             "BB": self._dev("BB", "JumpHeight-45ED")}
        first = self.blepin.resolve(m, "JumpHeight", tool="t",
                                    on_ambiguous="choose", stream=io.StringIO())
        second = self.blepin.resolve(dict(reversed(list(m.items()))), "JumpHeight",
                                     tool="t", on_ambiguous="choose",
                                     stream=io.StringIO())
        self.assertEqual(first.address, second.address,
                         "a script must behave the same way twice even when "
                         "the scan order differs")
        self.assertEqual(first.address, "BB", "lowest name wins (45ED < E2C4)")

    def test_refuse_stops_a_write_operation(self):
        import io
        m = {"AA": self._dev("AA", "JumpHeight-E2C4"),
             "BB": self._dev("BB", "JumpHeight-45ED")}
        with self.assertRaises(self.blepin.AmbiguousBoards):
            self.blepin.resolve(m, "JumpHeight", tool="otadfu",
                                on_ambiguous="refuse", stream=io.StringIO())

    def test_census_never_short_circuits(self):
        """The whole defect in one assertion: find_device_by_filter stops at
        the first True, so the filter must always return False and the caller
        must collect."""
        import asyncio

        class D:
            def __init__(self, addr, name):
                self.address, self.name = addr, name

        class Adv:
            def __init__(self, nm):
                self.local_name, self.rssi = nm, -40

        seen_all = []

        async def fake_find(filt, timeout=None):
            for addr, nm in (("AA", "JumpHeight-E2C4"), ("BB", "JumpHeight-45ED")):
                seen_all.append(filt(D(addr, nm), Adv(nm)))
            return None

        m = asyncio.run(self.blepin.census(fake_find, "JumpHeight"))
        self.assertEqual(len(m), 2, "census must see BOTH boards")
        self.assertTrue(all(v is False for v in seen_all),
                        "the filter must never return True — that is what "
                        "makes bleak stop scanning at the first responder")


class OtaDfuPinning(unittest.TestCase):
    """F-14: otadfu had no --name at all; pinning was an env var, and the
    match was a bare first-responder prefix. On 2026-08-12 that flashed the
    WRONG BOARD and the post-flash check was fooled by the same collision."""

    def test_has_a_name_flag_and_says_why_it_matters(self):
        r = subprocess.run([sys.executable, str(REPO / "tools/otadfu.py"), "--help"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--name", r.stdout)
        self.assertIn("--addr", r.stdout)

    def test_refuses_ambiguity_because_it_writes_firmware(self):
        """The policy itself, not a docstring: a JumpHeight lookup must ask for
        'refuse'. Everything else here is recoverable by retrying; a wrong
        flash is not."""
        import asyncio
        mod = _load("otadfu_under_test", REPO / "tools/otadfu.py")
        self.assertIn("PIN", dir(mod))
        self.assertIn("FLASHED_ADDR", dir(mod),
                      "the post-flash check must be able to compare against "
                      "the board actually sent to DFU")

        seen = {}

        async def fake_census(_find, name, addr=None, seconds=0.0):
            seen["addr"] = addr
            return {"AA": ("dev", name, -40)}

        def fake_resolve(matches, name, *, tool, on_ambiguous="choose", stream=None):
            seen["policy"] = on_ambiguous
            return "chosen"

        with mock.patch.object(mod.blepin, "census", fake_census), \
             mock.patch.object(mod.blepin, "resolve", fake_resolve):
            mod.PIN["name"], mod.PIN["addr"] = "JumpHeight-E2C4", None
            asyncio.run(mod.find("JumpHeight-E2C4", 1.0))
            self.assertEqual(seen["policy"], "refuse",
                             "flashing must never guess between boards")

            # A board sitting in the bootloader has no JumpHeight name to pin
            # to, and one board in DFU at a time is a bench invariant - so
            # AdaDFU stays on the lenient policy deliberately.
            asyncio.run(mod.find("AdaDFU", 1.0))
            self.assertEqual(seen["policy"], "choose")
            self.assertIsNone(seen["addr"],
                              "an address pin must not be applied to AdaDFU")


def _load_jump():
    """tools/jump has no .py extension, so it needs an explicit loader.

    Compiled from the SOURCE TEXT rather than via SourceFileLoader.exec_module,
    which consults __pycache__. That bit during mutation testing: "MIN_SAMPLES
    = 3" and "MIN_SAMPLES = 1" are the same number of bytes, so after restoring
    the file the cached bytecode of the MUTANT was still valid by size, and the
    restored code kept failing its own test. A stale .pyc makes a mutation run
    report whatever it likes.
    """
    import types
    path = REPO / "tools" / "jump"
    src = path.read_text()
    mod = types.ModuleType("jump_under_test")
    mod.__file__ = str(path)
    sys.modules["jump_under_test"] = mod
    exec(compile(src, str(path), "exec"), mod.__dict__)
    return mod


class BoardsVerdictNeedsRealSamples(unittest.TestCase):
    """F-15: `jump boards` enforced its three-sample minimum on the REQUEST and
    not on what actually arrived.

    Failed reads were dropped with a bare `continue` - no counter - so one
    successful read out of four still reached the verdict, which then ran
    stability maths on a single sample. Reproduced by stubbing 3 failures of 4
    against JumpHeight-45ED, a board with no battery at all: "battery present
    and stable (97%) - this board can measure power."

    One sample has no spread, and no spread reads as a perfectly steady cell.
    """

    def setUp(self):
        self.jump = _load_jump()

    class _Args:
        samples = 4
        scan_timeout = 1.0

    def _run_boards(self, replies):
        """Drive cmd_boards with a scripted sequence of stats replies."""
        import io
        import contextlib
        seq = list(replies)

        def fake_stats(name):
            return seq.pop(0) if seq else {}

        self.sleeps = []
        buf = io.StringIO()
        with mock.patch.object(self.jump, "_ble_scan_names",
                               lambda **k: ["JumpHeight-45ED"]), \
             mock.patch.object(self.jump, "_ble_stats_pinned", fake_stats), \
             mock.patch.object(self.jump.time, "sleep", self.sleeps.append), \
             contextlib.redirect_stdout(buf):
            self.jump.cmd_boards(self._Args())
        return buf.getvalue()

    def test_one_good_read_of_four_is_inconclusive_not_a_verdict(self):
        out = self._run_boards([
            {"vbat_mv": "4139", "chg": "0", "batt_pct": "97", "src": "abc"},
            {}, {}, {},
        ])
        self.assertIn("INCONCLUSIVE", out, out)
        self.assertIn("1/4", out, "say how many reads actually landed")
        # The exact wrong answer this ticket exists to prevent.
        self.assertNotIn("can measure power", out)
        self.assertNotIn("stable", out.lower().replace("unstable", ""))
        # And the failures must be visible, not silently absorbed.
        self.assertIn("no reply", out, out)

    def test_three_good_reads_still_reach_a_verdict(self):
        """The guard must not be so broad it refuses legitimate runs - a
        floating board that answers every time still has to be caught."""
        out = self._run_boards([
            {"vbat_mv": "4139", "chg": "0", "batt_pct": "97", "src": "abc"},
            {"vbat_mv": "4136", "chg": "0", "batt_pct": "97", "src": "abc"},
            {"vbat_mv": "3739", "chg": "0", "batt_pct": "60", "src": "abc"},
            {"vbat_mv": "3900", "chg": "0", "batt_pct": "70", "src": "abc"},
        ])
        self.assertNotIn("INCONCLUSIVE", out, out)
        # 400 mV of swing in seconds is the floating-divider signature.
        self.assertIn("400 mV", out, out)

    def test_a_real_cell_reads_stable(self):
        out = self._run_boards([
            {"vbat_mv": "3810", "chg": "0", "batt_pct": "42", "src": "abc"},
            {"vbat_mv": "3812", "chg": "0", "batt_pct": "42", "src": "abc"},
            {"vbat_mv": "3809", "chg": "0", "batt_pct": "42", "src": "abc"},
            {"vbat_mv": "3811", "chg": "0", "batt_pct": "42", "src": "abc"},
        ])
        self.assertNotIn("INCONCLUSIVE", out, out)
        self.assertIn("3810", out)

    def test_failed_reads_still_wait_between_attempts(self):
        """The method measures INERTIA - a real cell cannot move much in a few
        seconds - so the reads have to be seconds apart. The failure path used
        to `continue` past the sleep, turning four attempts into one fast burst
        that measures the ADC's noise floor instead.

        Not covered by the assertions above: they stub time.sleep, so a mutant
        that drops the sleep on failures passes them unnoticed. Found by
        mutation-testing this file.
        """
        self._run_boards([{"vbat_mv": "4139", "chg": "0", "src": "abc"}, {}, {}, {}])
        # 4 attempts => 3 gaps, regardless of how many of them failed.
        self.assertEqual(len(self.sleeps), 3,
                         f"expected a wait after each of the first 3 attempts, "
                         f"got {self.sleeps}")
        self.assertTrue(all(s >= 2.0 for s in self.sleeps), self.sleeps)
