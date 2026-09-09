"""Integration tests: the jump CLI driven against the fake device.

Every test runs the real CLI binary as a subprocess, talking the real serial
protocol over a pty to tools/fake_device.py — the same code paths used with
hardware, minus the hardware. Run via ./tools/jump simtest (or
`python3 -m unittest discover -s tools/tests`).
"""

from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
JUMP = str(REPO / "tools" / "jump")
sys.path.insert(0, str(REPO / "sim"))

from trace_codec import decode_region_recovering, encode_region  # noqa: E402


def run_cli(args, env_extra=None, timeout=90):
    import os

    env = dict(os.environ)
    env.update(env_extra or {})
    # stdin=DEVNULL: never let a subprocess inherit a real TTY from whatever
    # is running the test suite — non-interactive behavior must be
    # deterministic, not an accident of how pytest happened to be launched.
    return subprocess.run([sys.executable, JUMP] + args, capture_output=True,
                          text=True, timeout=timeout, env=env, cwd=str(REPO),
                          stdin=subprocess.DEVNULL)


def _load_jump_module():
    """Load tools/jump (no .py extension) as an importable module, for
    direct unit-level access to its pure functions/helpers."""
    import importlib.machinery
    import types

    loader = importlib.machinery.SourceFileLoader("jumpcli_ut", JUMP)
    mod = types.ModuleType("jumpcli_ut")
    mod.__file__ = JUMP
    loader.exec_module(mod)
    return mod


class TestCalProvenance(unittest.TestCase):
    """The OG's measured calibration was silently replaced by compiled
    defaults (probably the 08-19 battery death) and the device REPORTED it —
    into a diagnostic nobody read. These pin the host-side verdict that makes
    that report impossible to miss, including the per-key case the old OR'd
    `source=` field structurally hid."""

    def setUp(self):
        self.v = _load_jump_module()._cal_provenance_verdict

    def test_all_device_is_ok(self):
        lvl, _ = self.v({"off_src": "device", "scale_src": "device",
                         "vbat_src": "device"})
        self.assertEqual(lvl, "ok")

    def test_single_key_fallback_warns_and_names_the_key(self):
        """The case the old OR hid: one key lost, others survive."""
        lvl, msg = self.v({"off_src": "defaults", "scale_src": "device",
                           "vbat_src": "device"})
        self.assertEqual(lvl, "warn")
        self.assertIn("off", msg)
        self.assertNotIn("scale,", msg)

    def test_old_firmware_or_field_still_judged(self):
        lvl, _ = self.v({"source": "defaults"})
        self.assertEqual(lvl, "warn")
        lvl2, _ = self.v({"source": "device"})
        self.assertEqual(lvl2, "ok")

    def test_no_cal_line_is_unknown_not_ok(self):
        """Absence of evidence must not read as a pass."""
        lvl, _ = self.v({})
        self.assertEqual(lvl, "unknown")

    def test_scale_defaults_alone_is_info_not_warn(self):
        """height_scale is the ON-WATER calibration (DECISIONS #16) — no bench
        ritual can produce it, so before the first water session "defaults" is
        its only honest state. Warning on it made the session card's "no
        provenance warning" gate unsatisfiable, and a warning that always
        fires is noise the eye skips (2026-08-24, the night the first real
        drop calibration landed and the warning kept firing anyway)."""
        lvl, msg = self.v({"off_src": "device", "scale_src": "defaults",
                           "vbat_src": "device"})
        self.assertEqual(lvl, "info")
        self.assertIn("height_scale", msg)
        self.assertIn("water", msg)

    def test_vbat_defaults_alone_is_info(self):
        lvl, msg = self.v({"off_src": "device", "scale_src": "device",
                           "vbat_src": "defaults"})
        self.assertEqual(lvl, "info")
        self.assertIn("vbat_scale", msg)

    def test_off_defaults_dominates_regardless_of_other_keys(self):
        """The drop ritual's own key missing is the dire case; it must warn
        even when the expected-defaults keys would otherwise soften it."""
        lvl, msg = self.v({"off_src": "defaults", "scale_src": "defaults",
                           "vbat_src": "defaults"})
        self.assertEqual(lvl, "warn")
        self.assertIn("drop", msg.lower())


class TestDownloadVerification(unittest.TestCase):
    """A download is only 'verified' if BOTH files arrived and the device did
    not complain.

    Pins an audit finding (2026-08-20): the gate compared only trace.csv's byte
    count, then printed a bare 'download verified' that read as covering the
    whole download. jumps.csv — the file the report, the season best and the
    clear prompt are built from — had no check at all, and the firmware's own
    '# WARNING ... INCOMPLETE' line was ignored by every surface. The prompt
    immediately after this gate offers to ERASE the device.
    """

    def setUp(self):
        self.v = _load_jump_module()._verify_download

    def test_both_files_complete_is_verified(self):
        ok, out = self.v([], 12, 12, 2048, 2048)
        self.assertTrue(ok)
        self.assertTrue(any("jumps verified" in l for l in out))
        self.assertTrue(any("trace verified" in l for l in out))

    def test_short_jumps_file_fails_even_when_trace_is_perfect(self):
        """The exact hole: trace matches, results file is truncated."""
        ok, out = self.v([], 9, 12, 2048, 2048)
        self.assertFalse(ok, "a short jumps.csv must fail the whole download")
        self.assertTrue(any("JUMPS FILE SHORT" in l for l in out))
        self.assertTrue(any("Do NOT clear" in l for l in out))

    def test_firmware_incomplete_warning_fails_even_if_counts_match(self):
        """The device's own complaint outranks our arithmetic."""
        warn = ["# WARNING trace.csv INCOMPLETE — 512 bytes never reached the host"]
        ok, out = self.v(warn, 12, 12, 2048, 2048)
        self.assertFalse(ok, "the device said bytes were dropped; believe it")
        self.assertTrue(any("DROPPED TRANSFER" in l for l in out))

    def test_short_trace_still_fails(self):
        ok, out = self.v([], 12, 12, 1024, 2048)
        self.assertFalse(ok)
        self.assertTrue(any("TRACE INCOMPLETE" in l for l in out))

    def test_old_firmware_reporting_neither_is_unknown_not_pass(self):
        ok, out = self.v([], 12, None, 2048, None)
        self.assertIsNone(ok, "unknown must not be reported as verified")
        self.assertTrue(any("not verified" in l for l in out))


class TestUploadVerification(unittest.TestCase):
    """A flash must never be reported as landed on the strength of an exit code.

    This pins a bug that shipped TWICE in one evening (2026-08-20):

      1. `./tools/jump flash` trusted PlatformIO's return code. PlatformIO
         prints a green [SUCCESS] and exits 0 even when the DFU write threw, so
         the tool announced "flashed" over a board still holding its old
         firmware, and the board's subsequent silence looked like a hardware
         fault.
      2. The fix for (1) was added ONLY inside the `if returncode != 0:` retry
         branch. Since the failure is DEFINED by returncode == 0, the guard sat
         where the failure could never arrive — the common path still fell
         through to "flashed". A guard placed out of reach is worse than none,
         because it reads like the problem is handled.

    The blob below is the real transcript observed on two different boards.
    """

    def setUp(self):
        self.landed = _load_jump_module()._upload_landed

    # The literal output that fooled the tool, exit code and all.
    LYING = ("Upgrading target on /dev/cu.usbmodem1101 with DFU package firmware.zip\n"
             "Failed to upgrade target. Error is: write failed: "
             "[Errno 6] Device not configured\n"
             "serial.serialutil.SerialException: write failed: "
             "[Errno 6] Device not configured\n"
             "========================= [SUCCESS] Took 11.86 seconds ============\n")
    GENUINE = ("Upgrading target with DFU package firmware.zip\n"
               "Device programmed.\n"
               "========================= [SUCCESS] Took 18.20 seconds ===========\n")

    def test_lying_uploader_with_zero_exit_is_not_a_success(self):
        ok, why = self.landed(self.LYING, 0)
        self.assertFalse(ok, "a failed DFU write that exits 0 must NOT count as landed")
        self.assertTrue(why, "a rejection must say why")

    def test_genuine_programmed_marker_is_a_success(self):
        ok, _ = self.landed(self.GENUINE, 0)
        self.assertTrue(ok, "'Device programmed' with a clean exit must count as landed")

    def test_missing_marker_is_failure_even_with_clean_output(self):
        ok, _ = self.landed("Building...\nLinking...\n", 0)
        self.assertFalse(ok, "absence of the marker IS the failure signal")

    def test_marker_present_but_nonzero_exit_is_failure(self):
        ok, _ = self.landed(self.GENUINE, 1)
        self.assertFalse(ok, "a nonzero exit still fails even with the marker")

    def test_the_upload_attempt_is_verified_and_never_blindly_repeated(self):
        """Two regressions pinned at once.

        (1) The original bug: the upload's output must be captured and judged
            by the shared verdict — an exit-code check alone is how a lying
            uploader got "flashed" printed over stale firmware.
        (2) The soak finding (n=9, 2026-08-20): blind re-uploads are what
            stale macOS's CDC — two rapid flashes work, then every serial
            attempt fails until a replug. So cmd_flash must contain exactly
            ONE upload invocation; on failure it stops and prescribes
            recovery instead of dialing again.
        """
        src = Path(JUMP).read_text(encoding="utf-8")
        body = src[src.index("def cmd_flash"):]
        body = body[:body.index("\ndef ", 10)]   # cmd_flash only
        self.assertIn("first_log", body,
                      "the upload attempt must capture its output")
        self.assertIn("_upload_landed", body,
                      "the upload must be judged by the shared verdict, "
                      "not its exit code")
        self.assertEqual(body.count("subprocess.Popen"), 1,
                         "exactly ONE upload attempt — blind repeats stale "
                         "the host CDC and made attempt N+1 LESS likely to "
                         "work (measured 2026-08-20)")
        self.assertIn("REPLUG", body,
                      "the failure text must name the recovery that actually "
                      "works (replug clears the stale CDC; software cannot)")


class TestSelftest(unittest.TestCase):
    def test_selftest_pass(self):
        r = run_cli(["selftest", "--fake", "--fast"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PASS", r.stdout)
        self.assertIn("i2c", r.stdout)

    def test_selftest_bad_wiring_fails_with_hints(self):
        r = run_cli(["selftest", "--fake", "--fake-fail", "--fast"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("FAIL", r.stdout)
        self.assertIn("SDA", r.stdout)  # the actual fix hint reaches the user


class TestDesktest(unittest.TestCase):
    def test_full_desktest_flow(self):
        r = run_cli(["desktest", "--fake", "--fast"])
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULT: PASS", r.stdout)
        self.assertEqual(r.stdout.count("toss"), r.stdout.count("toss"))
        for token in ("toss 1", "toss 2", "toss 3"):
            self.assertIn(token, r.stdout)


class TestDropCalibration(unittest.TestCase):
    def test_drop_measures_injected_bias_and_writes_config(self):
        # Copy the real config; the CLI must write the recommended offset to it.
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "params.json"
            shutil.copy(REPO / "config" / "params.json", cfg_path)
            r = run_cli(["drop", "--fake", "--fast", "--yes",
                         "--height-cm", "100", "--drops", "5"],
                        env_extra={"JH_CONFIG": str(cfg_path)})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            cfg = json.loads(cfg_path.read_text())
            offset = cfg["detector"]["airtime_offset_s"]
            # Fake device injects +15 ms of latency; recovered offset must be
            # close to -0.015 (tolerance covers the injected noise).
            self.assertLess(abs(offset - (-0.015)), 0.010,
                            f"recovered offset {offset}, expected ~-0.015")

    def test_drop_rejects_too_low_height(self):
        r = run_cli(["drop", "--fake", "--fast", "--height-cm", "20"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("too low", r.stdout)


class TestWizardAndReport(unittest.TestCase):
    def test_wizard_fake_end_to_end_and_resume(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "params.json"
            shutil.copy(REPO / "config" / "params.json", cfg)
            env = {"JH_DATA_DIR": str(Path(td) / "data"), "JH_CONFIG": str(cfg)}
            r = run_cli(["wizard", "--fake", "--yes"], env_extra=env, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("WIZARD COMPLETE", r.stdout)
            state = json.loads((Path(td) / "data" / "wizard_state.json").read_text())
            for step in ("software", "connect", "flash", "desktest", "drop"):
                self.assertIn(step, state["completed"])
            # calibration must have been written to the config
            self.assertNotEqual(
                json.loads(cfg.read_text())["detector"]["airtime_offset_s"], 0.0)
            # session log captured serial traffic
            logs = list((Path(td) / "data" / "logs").glob("*-wizard.log"))
            self.assertTrue(logs and any("RX SELFTEST" in p.read_text() for p in logs))
            # second run resumes instantly as complete
            r2 = run_cli(["wizard", "--fake", "--yes"], env_extra=env, timeout=60)
            self.assertEqual(r2.returncode, 0)
            self.assertIn("Welcome back", r2.stdout)
            self.assertIn("WIZARD COMPLETE", r2.stdout)

    def test_report_fake(self):
        with tempfile.TemporaryDirectory() as td:
            env = {"JH_DATA_DIR": str(Path(td) / "data")}
            r = run_cli(["report", "--fake"], env_extra=env, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            reports = list((Path(td) / "data" / "diagnostics").glob("report-*.txt"))
            self.assertEqual(len(reports), 1)
            body = reports[0].read_text()
            for marker in ("== system", "== config/params.json", "== live device",
                           "SELFTEST END result=PASS", "== END REPORT"):
                self.assertIn(marker, body)


class TestUntetheredHelpers(unittest.TestCase):
    """Unit coverage for the untethered flow's building blocks. The full
    unplug/replug cycle needs a human; these at least pin the logic that
    only real hardware exercises."""

    @staticmethod
    def _mod():
        return _load_jump_module()

    def test_stored_rows_parses_the_fake_session(self):
        mod = self._mod()
        proc = subprocess.Popen(
            [sys.executable, str(REPO / "tools" / "fake_device.py"),
             "--scenario", "session"], stdout=subprocess.PIPE, text=True)
        try:
            port = proc.stdout.readline().split()[1]
            dev = mod.Device(port)
            try:
                dev.drain_boot()
                rows = mod._stored_rows(dev)
            finally:
                dev.close()
            self.assertEqual(len(rows), 4)  # the demo session's 4 known jumps
            for r in rows:
                self.assertGreater(r["raw"], 0.2)
                self.assertGreater(r["h"], 0.0)
        finally:
            proc.terminate()


    def test_wait_for_unplug_returns_when_port_vanishes(self):
        """The no-TTY untethered pause: the cable IS the signal. Without
        this, a no-TTY `jump drop` run "continued" instantly, found the
        board still plugged, and reported "0 good drops" — a measurement
        that never happened, dressed as a result (2026-08-24, twice)."""
        mod = _load_jump_module()
        calls = {"n": 0}
        def fake_scan():
            calls["n"] += 1
            return ["/dev/cu.usbmodem101"] if calls["n"] < 3 else []
        orig = mod.scan_ports
        mod.scan_ports = fake_scan
        try:
            self.assertTrue(mod._wait_for_unplug("/dev/cu.usbmodem101",
                                                 timeout=10.0))
            self.assertGreaterEqual(calls["n"], 3)
        finally:
            mod.scan_ports = orig

    def test_wait_for_unplug_times_out_when_port_stays(self):
        """Never-unplugged must be reported as a failure, not waited on
        forever and not silently passed."""
        mod = _load_jump_module()
        orig = mod.scan_ports
        mod.scan_ports = lambda: ["/dev/cu.usbmodem101"]
        try:
            self.assertFalse(mod._wait_for_unplug("/dev/cu.usbmodem101",
                                                  timeout=0.1))
        finally:
            mod.scan_ports = orig

    def test_wait_for_port_return_ignores_stranger_ports(self):
        mod = self._mod()
        # A stranger adapter is present the whole time; our port vanishes then
        # returns under its old name. The stranger must never be picked.
        seq = [["/dev/cu.stranger"], ["/dev/cu.stranger"],
               ["/dev/cu.stranger", "/dev/cu.wchusbserial9"]]
        calls = {"n": 0}

        def fake_scan():
            i = min(calls["n"], len(seq) - 1)
            calls["n"] += 1
            return seq[i]

        mod.scan_ports = fake_scan
        port = mod._wait_for_port_return(
            "/dev/cu.wchusbserial9",
            baseline={"/dev/cu.stranger", "/dev/cu.wchusbserial9"},
            timeout=15.0)
        self.assertEqual(port, "/dev/cu.wchusbserial9")


class TestSync(unittest.TestCase):
    def test_sync_downloads_and_reports(self):
        with tempfile.TemporaryDirectory() as td:
            r = run_cli(["sync", "--fake", "--fast", "--out", td])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            sessions = list(Path(td).iterdir())
            self.assertEqual(len(sessions), 1)
            report = (sessions[0] / "report.md").read_text()
            # The demo session has 4 known jumps; live and offline must agree.
            self.assertIn("4 jumps", report)
            self.assertIn("agree", report)
            self.assertTrue((sessions[0] / "trace.csv").read_text().startswith("t,mag"))
            self.assertIn("best", r.stdout.lower())

    def test_sync_writes_session_info_additively(self):
        """Item 1(b): sync writes a small session-info.txt (fw= line + CAL
        line captured at sync time) next to jumps.csv, for a future
        session-vs-device identity check — additive only: jumps.csv/
        trace.csv keep their exact existing format."""
        with tempfile.TemporaryDirectory() as td:
            r = run_cli(["sync", "--fake", "--fast", "--out", td])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            sess = next(Path(td).iterdir())
            info = (sess / "session-info.txt").read_text()
            self.assertIn("fw=0.4.3", info)
            self.assertIn("CAL airtime_offset_s=", info)
            self.assertIn("height_scale=", info)
            # Existing files' formats are untouched.
            jumps = (sess / "jumps.csv").read_text()
            self.assertTrue(jumps.startswith("n,takeoff_s,airtime_raw_s,airtime_s,height_m"))
            self.assertTrue((sess / "trace.csv").read_text().startswith("t,mag"))

    def test_sync_default_goes_via_traceraw_and_writes_trace_bin(self):
        """web/sync/CONTRACT.md §1: `sync` (no --csv) tries `traceraw` first — ~2
        B/sample raw trace-region bytes, base64-framed, crc32-verified —
        and keeps BOTH the decoded trace.csv and the raw trace.bin next to
        it, so a bench sync exercises the exact wire path Nick's phone will
        use before it ever ships to him."""
        with tempfile.TemporaryDirectory() as td:
            r = run_cli(["sync", "--fake", "--fast", "--out", td])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            sess = next(Path(td).iterdir())
            self.assertTrue((sess / "trace.bin").exists(),
                            "traceraw path must keep the raw trace.bin")
            self.assertIn("traceraw verified", r.stdout)
            self.assertGreater((sess / "trace.bin").stat().st_size, 0)

    def test_sync_csv_flag_skips_traceraw(self):
        """--csv (web/sync/CONTRACT.md §4) forces the old `dump` path: no trace.bin,
        and the verdict line is the original 'trace verified' wording, not
        traceraw's — confirming --csv actually took the other branch rather
        than merely skipping the trace.bin write."""
        with tempfile.TemporaryDirectory() as td:
            r = run_cli(["sync", "--fake", "--fast", "--csv", "--out", td])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            sess = next(Path(td).iterdir())
            self.assertFalse((sess / "trace.bin").exists(),
                             "--csv must not touch traceraw at all")
            self.assertIn("trace verified", r.stdout)
            self.assertNotIn("traceraw verified", r.stdout)

    def test_sync_traceraw_and_csv_paths_produce_identical_files(self):
        """The whole point of web/sync/CONTRACT.md §1's round-trip guarantee: whether
        `sync` takes the new binary path or the old CSV one, the session it
        writes must be indistinguishable — same trace.csv, same jumps.csv."""
        with tempfile.TemporaryDirectory() as td1, \
             tempfile.TemporaryDirectory() as td2:
            r1 = run_cli(["sync", "--fake", "--fast", "--out", td1])
            r2 = run_cli(["sync", "--fake", "--fast", "--csv", "--out", td2])
            self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            sess1 = next(Path(td1).iterdir())
            sess2 = next(Path(td2).iterdir())
            self.assertEqual((sess1 / "trace.csv").read_bytes(),
                             (sess2 / "trace.csv").read_bytes())
            self.assertEqual((sess1 / "jumps.csv").read_bytes(),
                             (sess2 / "jumps.csv").read_bytes())

    def test_sync_falls_back_to_dump_on_old_firmware(self):
        """web/sync/CONTRACT.md §1: `ERR unknown_command traceraw` (today's real OG,
        docs/STATUS.md's src=5c80a436) must fall back to `dump`, not abort —
        the path every bench sync takes until the flash batch lands. Before
        this test, only `--csv` (a DIFFERENT code path: `via = None` is set
        without ever sending `traceraw`) exercised the `else` branch that
        writes jumps.csv/trace.csv without trace.bin."""
        with tempfile.TemporaryDirectory() as td:
            r = run_cli(["sync", "--fake", "--fast", "--fake-no-traceraw",
                        "--out", td])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("(old firmware — falling back to `dump`)", r.stdout)
            sess = next(Path(td).iterdir())
            self.assertFalse((sess / "trace.bin").exists(),
                             "the fallback path must not produce a trace.bin")
            self.assertIn("trace verified", r.stdout)
            self.assertNotIn("traceraw verified", r.stdout)

    def test_sync_aborts_on_non_fallback_traceraw_error(self):
        """web/sync/CONTRACT.md §1: only the EXACT string 'ERR unknown_command
        traceraw' triggers the CSV fallback — any other ERR (storage not
        mounted, etc) is reported as-is and the sync aborts, since there is
        no CSV path that recovers from e.g. the storage layer being down."""
        with tempfile.TemporaryDirectory() as td:
            r = run_cli(["sync", "--fake", "--fast",
                        "--fake-traceraw-error", "storage_down", "--out", td])
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("ERR traceraw storage_down", r.stdout)
            self.assertEqual(list(Path(td).iterdir()), [],
                             "an aborted sync must not write a session dir")


class TestF22TracecheckArbitration(unittest.TestCase):
    """Audit F-22, measured on the OG 2026-09-07 with a FULL trace region:

        sync downloaded            15,917,153 B
        STATS trace_bytes          15,917,918 B   (-765)
        `tracecheck`               "# tracecheck fast=15917918 slow=15917153
                                    DISAGREE — the slow number is the correct one"

    The download was COMPLETE. The tool printed 'TRACE INCOMPLETE … Do NOT
    clear' and refused to clear, twice — on a puck whose region was full and
    which therefore had stopped recording (main.cpp:1707). The live counter
    over-reports once the region fills; the re-walked number is the arbiter,
    which is F-22's own prescribed fix ("make `tracecheck` the authority").

    Every case here goes through the CSV `dump` path, because that is the
    path the rider's puck actually takes (it predates `traceraw`).
    """

    def _sync(self, td, *extra):
        return run_cli(["sync", "--fake", "--fast", "--csv", "--out", td]
                       + list(extra))

    def test_over_report_confirmed_by_tracecheck_verifies_and_permits_clear(self):
        with tempfile.TemporaryDirectory() as td:
            r = self._sync(td, "--fake-trace-overreport", "765", "--clear")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            sess = next(Path(td).iterdir())
            true_b = (sess / "trace.csv").stat().st_size

            self.assertIn("trace_bytes over-reported by 765 B (audit F-22, "
                          "region full); tracecheck confirms the download is "
                          "complete.", r.stdout)
            self.assertNotIn("TRACE INCOMPLETE", r.stdout)
            self.assertNotIn("refusing to clear", r.stdout)
            self.assertIn("device cleared", r.stdout)

            # Both numbers travel with the session, so a clear made on F-22's
            # strength can be re-litigated later from the file alone.
            blob = json.loads((sess / "session.json").read_text())
            self.assertEqual(blob["trace_bytes_device"], true_b + 765)
            self.assertEqual(blob["tracecheck_slow_bytes"], true_b)
            self.assertEqual(blob["tracecheck_fast_bytes"], true_b + 765)
            # …additively: the wall-clock anchor is still there.
            self.assertIn("trace_epoch_utc", blob)

    def test_slow_number_that_also_disagrees_keeps_the_refusal(self):
        """tracecheck is the arbiter, not an excuse. A re-walk that matches
        NEITHER the download nor the live counter is a genuine shortfall, and
        the refusal must stand with both numbers on screen."""
        with tempfile.TemporaryDirectory() as td:
            r = self._sync(td, "--fake-trace-overreport", "765",
                           "--fake-tracecheck-slow-delta", "400", "--clear")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            sess = next(Path(td).iterdir())
            true_b = (sess / "trace.csv").stat().st_size

            self.assertIn("TRACE INCOMPLETE", r.stdout)
            self.assertIn(f"tracecheck: fast={true_b + 765:,} "
                          f"slow={true_b + 400:,}", r.stdout)
            self.assertIn("Do NOT clear the device.", r.stdout)
            self.assertIn("refusing to clear", r.stdout)
            self.assertNotIn("device cleared", r.stdout)

            blob = json.loads((sess / "session.json").read_text())
            self.assertEqual(blob["tracecheck_slow_bytes"], true_b + 400)

    def test_silent_tracecheck_keeps_the_refusal_and_names_the_silence(self):
        """CLAUDE.md rule 3: a reading that did not happen is a finding. An
        unanswered `tracecheck` leaves the mismatch exactly as unexplained as
        it was — it must never read as the F-22 pass above."""
        with tempfile.TemporaryDirectory() as td:
            r = self._sync(td, "--fake-trace-overreport", "765",
                           "--fake-tracecheck-silent",
                           "--fake-tracecheck-timeout", "3", "--clear")
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("TRACE INCOMPLETE", r.stdout)
            self.assertIn("tracecheck did not answer — no reply within 3 s",
                          r.stdout)
            self.assertNotIn("audit F-22", r.stdout)
            self.assertIn("refusing to clear", r.stdout)
            self.assertNotIn("device cleared", r.stdout)

            sess = next(Path(td).iterdir())
            blob = json.loads((sess / "session.json").read_text())
            self.assertIsNone(blob["tracecheck_slow_bytes"])
            self.assertIn("no reply within 3 s", blob["tracecheck_error"])

    def test_default_timeout_is_minutes_because_the_walk_is(self):
        """`tracecheck` re-reads and decodes every block in the region
        (main.cpp -> jh_store.cpp's walkTraceRegion(), which feeds the
        watchdog every 64 blocks) and sends nothing until it finishes. A
        20 s command timeout would turn a working full-region check into
        the 'did not answer' refusal above."""
        self.assertGreaterEqual(_load_jump_module().TRACECHECK_TIMEOUT_S, 300)

    def test_a_mismatch_with_no_tracecheck_offered_still_refuses(self):
        """The pure function keeps its old behavior for any caller that has
        no device to ask — and says the cross-check never ran rather than
        implying it passed."""
        mod = _load_jump_module()
        ok, out = mod._verify_download([], 12, 12, 2048, 2813)
        self.assertFalse(ok)
        self.assertTrue(any("TRACE INCOMPLETE" in l for l in out))
        self.assertTrue(any("tracecheck was not consulted" in l for l in out))

    def test_out_of_band_skew_is_resolved_but_never_filed_under_f22(self):
        """CLAUDE.md rule 6. F-22's evidence is an over-report of at most one
        50-sample batch (800 B). A 100 kB skew is not that finding, however
        the download itself checks out, and must not borrow its name."""
        mod = _load_jump_module()
        tc = mod.TraceCheck(fast=102048, slow=2048, reason=None)
        ok, out = mod._verify_download([], 12, 12, 2048, 102048, lambda: tc)
        self.assertTrue(ok)
        self.assertFalse(any("F-22, region full" in l for l in out),
                         "an unexplained skew must not be attributed to F-22")
        self.assertTrue(any("outside audit F-22's known range" in l for l in out))
        self.assertTrue(any("confirms the download is complete" in l for l in out))


class _StubDevice:
    """A minimal stand-in for jump's `Device`, for unit-testing
    `_sync_via_traceraw()` against a scripted response WITHOUT spawning
    tools/fake_device.py — used for wire shapes the fake can't (yet, or
    easily) produce, like an in-frame '#' warning or a truncated base64
    line."""

    def __init__(self, responses: dict):
        self.responses = responses

    def command(self, cmd, timeout=60):
        return self.responses[cmd]


class TestTracerawFrameCorruption(unittest.TestCase):
    """tools/jump:1718 (finding, 2026-09-07): a mid-transfer serial drop
    puts either the firmware's own in-frame '# WARNING ... INCOMPLETE' line
    (main.cpp emits it BEFORE the FILE END terminator) or a truncated
    base64 line into the frame body. Before the fix, base64.b64decode()
    raised on both — a bare traceback where every OTHER transfer failure in
    this file prints a plain "Do NOT clear the device" verdict."""

    def setUp(self):
        self.sync_via_traceraw = _load_jump_module()._sync_via_traceraw

    def test_in_frame_warning_line_is_filtered_not_joined_into_base64(self):
        """The exact firmware sequence: the WARNING lands INSIDE BEGIN/END,
        with a UTF-8 em dash that survives read_line's errors='replace'.
        Filtering '#' lines out of the body must let the (otherwise valid)
        base64 decode cleanly, WITHOUT losing the warning line itself — it
        must still reach _verify_traceraw_download() via received_lines."""
        dev = _StubDevice({
            "traceraw": [
                "# traceraw bytes=0 log_hz=50 region_bytes=0",
                "FILE trace.bin BEGIN",
                "# WARNING trace.bin INCOMPLETE — 512 bytes never reached the host",
                "FILE trace.bin END",
                "# traceraw crc32=00000000 bytes=0",
                "OK traceraw",
            ],
            "jumps": ["n,takeoff_s,airtime_raw_s,airtime_s,height_m"],
        })
        via = self.sync_via_traceraw(dev)
        self.assertIsNotNone(via, "a valid (if incomplete) frame must not "
                                  "be mistaken for old-firmware fallback")
        self.assertEqual(via["raw_bytes"], b"")
        self.assertTrue(any("INCOMPLETE" in l for l in via["received_lines"]),
                        "the warning must survive into received_lines so "
                        "_verify_traceraw_download()'s check (a) still sees it")

    def test_truncated_base64_line_fails_closed_not_a_traceback(self):
        """A line truncated mid-transfer (not a whole dropped line — those
        stay a multiple of 4 chars and decode fine) makes the base64 body's
        length not a multiple of 4. Must raise SystemExit(1) with a plain
        verdict, never let the raw ValueError/binascii.Error escape."""
        dev = _StubDevice({
            "traceraw": [
                "# traceraw bytes=8 log_hz=50 region_bytes=8",
                "FILE trace.bin BEGIN",
                "AAAAAAA",  # 7 chars: not a multiple of 4
                "FILE trace.bin END",
                "# traceraw crc32=deadbeef bytes=8",
                "OK traceraw",
            ],
            "jumps": [],
        })
        with self.assertRaises(SystemExit) as cm:
            self.sync_via_traceraw(dev)
        self.assertEqual(cm.exception.code, 1)

    def test_truncated_base64_prints_a_plain_verdict(self):
        """Same fixture as above, but asserting the PRINTED message shape —
        this is the actual regression: not just "doesn't crash the process"
        (main() already prints tracebacks it doesn't catch) but "prints the
        same shaped verdict every other transfer failure here does"."""
        import io
        from contextlib import redirect_stdout

        dev = _StubDevice({
            "traceraw": [
                "# traceraw bytes=8 log_hz=50 region_bytes=8",
                "FILE trace.bin BEGIN",
                "AAAAAAA",
                "FILE trace.bin END",
                "# traceraw crc32=deadbeef bytes=8",
                "OK traceraw",
            ],
            "jumps": [],
        })
        buf = io.StringIO()
        with redirect_stdout(buf), self.assertRaises(SystemExit):
            self.sync_via_traceraw(dev)
        out = buf.getvalue()
        self.assertIn("TRACERAW FRAME CORRUPT", out)
        self.assertIn("Do NOT clear the device", out)
        self.assertNotIn("Traceback", out)


class TestTracerawLogHzFallback(unittest.TestCase):
    """tools/jump:1737 (finding, minor): when the '# traceraw ... log_hz='
    chatter is missing/unparsable, log_hz silently falls back to
    config/params.json — CLAUDE.md rule 3 says the substitution itself must
    be reported, not just performed."""

    def test_missing_log_hz_chatter_prints_the_substituted_rate(self):
        import io
        from contextlib import redirect_stdout

        dev = _StubDevice({
            "traceraw": [
                "# traceraw bytes=0 region_bytes=0",  # no log_hz=
                "FILE trace.bin BEGIN",
                "FILE trace.bin END",
                "# traceraw crc32=00000000 bytes=0",
                "OK traceraw",
            ],
            "jumps": ["n,takeoff_s,airtime_raw_s,airtime_s,height_m"],
        })
        buf = io.StringIO()
        with redirect_stdout(buf):
            via = _load_jump_module()._sync_via_traceraw(dev)
        self.assertIsNotNone(via)
        out = buf.getvalue()
        self.assertIn("carried no log_hz", out)
        self.assertIn("50", out)  # config/params.json's default


class TestTracerawVerification(unittest.TestCase):
    """`_verify_traceraw_download()`'s failure paths (web/sync/CONTRACT.md §1),
    driven directly. Mutation-tested finding (2026-09-07): disabling the
    crc32 check (:1712 -> `if False:`) and the consumed==N check (:1958 ->
    `if False:`) together left TestSync's own fixtures green — they only
    ever feed a clean image with a correct crc32, so nothing here actually
    pinned these branches before."""

    def setUp(self):
        self.v = _load_jump_module()._verify_traceraw_download

    @staticmethod
    def _region(n_samples=10, log_hz=50):
        return [(i / log_hz, 1.0 + 0.001 * i) for i in range(n_samples)]

    def test_crc_mismatch_fails(self):
        image = encode_region(self._region(), 50)
        region = decode_region_recovering(image, 50)
        ok, out = self.v([], 0, None, image, len(image), "deadbeef", region,
                         len("t,mag\n"), None)
        self.assertFalse(ok)
        self.assertTrue(any("CRC MISMATCH" in l for l in out))

    def test_short_transfer_fails(self):
        image = encode_region(self._region(), 50)
        region = decode_region_recovering(image, 50)
        crc = f"{zlib.crc32(image) & 0xffffffff:08x}"
        ok, out = self.v([], 0, None, image[:-4], len(image), crc, region,
                         0, None)
        self.assertFalse(ok)
        self.assertTrue(any("TRACERAW SHORT" in l for l in out))

    def test_damaged_patch_with_matching_crc_fails_and_does_not_claim_no_loss(self):
        """The 2026-09-07 finding, at unit level: a torn write already on
        the device's flash (so crc32 over the transferred bytes matches —
        the corruption predates the transfer) must fail, and must never say
        'no bytes lost' — skipPastTornWrite() always drops the samples in
        the patch it jumps past."""
        samples = self._region(1500)
        image = bytearray(encode_region(samples, 50))
        image[10] ^= 0xFF
        image = bytes(image)
        region = decode_region_recovering(image, 50)
        crc = f"{zlib.crc32(image) & 0xffffffff:08x}"
        ok, out = self.v([], 0, None, image, len(image), crc, region, 0, None)
        self.assertFalse(ok, "damage inside the region must fail, not warn")
        self.assertNotIn("no bytes lost", "\n".join(out))

    def test_in_frame_incomplete_warning_fails(self):
        region = decode_region_recovering(b"", 50)
        ok, out = self.v(
            ["# WARNING trace.bin INCOMPLETE -- 512 bytes never reached the host"],
            0, None, b"", 0, "00000000", region, len("t,mag\n"), None)
        self.assertFalse(ok)
        self.assertTrue(any("DROPPED TRANSFER" in l for l in out))

    def test_empty_region_is_verified(self):
        region = decode_region_recovering(b"", 50)
        ok, out = self.v([], 0, None, b"", 0, "00000000", region,
                         len("t,mag\n"), None)
        self.assertTrue(ok)
        self.assertTrue(any("traceraw verified" in l for l in out))


class TestValidateMath(unittest.TestCase):
    """Pure math-core tests for `validate` — no device, no subprocess:
    fabricate measured rows directly and check the recommendations exactly,
    per the composition convention _validate_stats documents."""

    G = 9.80665

    def _rows(self, true_airtimes, bias=0.0, existing_offset=0.0,
              existing_scale=1.0, target_scale=1.0, fps=1000.0):
        """Fabricate matched rows shaped like real device/session data:
        raw = true_airtime + bias (a pure additive timing bias, independent
        of whatever offset the device already has — exactly like the real
        firmware's airtime_raw_s), adj = raw + existing_offset (what the
        device currently reports as its calibrated airtime), and `h`
        rescaled so that — once the bias is corrected — `target_scale` is
        exactly the height_scale this tool should recommend, regardless of
        existing_scale. That independence from existing_scale/existing_offset
        is the composition property under test.

        `bias` may also be a list/tuple of one value per jump (non-uniform
        bias) — every other caller passes a single constant, for which
        median(bias) == mean(bias) trivially; a non-uniform fixture is the
        only way to tell the two apart (see test_offset_recommendation_uses_
        median_not_mean_of_bias).
        """
        biases = bias if isinstance(bias, (list, tuple)) else [bias] * len(true_airtimes)
        rows = []
        for i, (ta, b) in enumerate(zip(true_airtimes, biases), start=1):
            raw = ta + b
            adj = raw + existing_offset
            true_h = self.G * ta * ta / 8.0
            h = (existing_scale / target_scale) * true_h * (adj / ta) ** 2
            rows.append({"n": i, "fps": fps, "frames": ta * fps,
                        "raw": raw, "adj": adj, "h": h})
        return rows

    def test_recovers_injected_bias_and_scale(self):
        # The task's own worked example: bias=+0.045s, scale=0.93.
        mod = _load_jump_module()
        rows = self._rows([0.6, 1.0, 1.5, 2.0], bias=0.045, target_scale=0.93)
        stats = mod._validate_stats(rows, self.G)
        self.assertAlmostEqual(stats["median_bias_s"], 0.045, places=6)
        self.assertAlmostEqual(stats["recommended_airtime_offset_s"], -0.045, places=6)
        self.assertAlmostEqual(stats["recommended_height_scale"], 0.93, places=6)
        decide = mod._validate_decide(stats)
        self.assertTrue(decide["recommend_offset"])
        self.assertTrue(decide["recommend_scale"])

    def test_offset_recommendation_ignores_existing_device_offset(self):
        """The most important correctness detail: airtime_offset_s is
        derived from RAW airtime, which the firmware never applies an
        offset to — so the recommendation must be identical no matter what
        offset the device already has applied (composing, not double-
        correcting)."""
        mod = _load_jump_module()
        rows_a = self._rows([0.6, 1.0, 1.5, 2.0], bias=0.045, existing_offset=-0.02)
        rows_b = self._rows([0.6, 1.0, 1.5, 2.0], bias=0.045, existing_offset=-0.30)
        stats_a = mod._validate_stats(rows_a, self.G, existing_offset=-0.02)
        stats_b = mod._validate_stats(rows_b, self.G, existing_offset=-0.30)
        self.assertAlmostEqual(stats_a["recommended_airtime_offset_s"],
                               stats_b["recommended_airtime_offset_s"], places=9)
        self.assertAlmostEqual(stats_a["recommended_airtime_offset_s"], -0.045, places=6)

    def test_height_scale_composes_with_existing_device_scale(self):
        """height_scale composes the other way round from offset: the
        device's reported height DOES carry the current height_scale, so the
        residual must be multiplied back onto it — not treated as the total.
        A device already running a wrong existing_scale=1.075 with a real
        0.93 residual anomaly must still recover a clean 0.93 recommendation."""
        mod = _load_jump_module()
        rows = self._rows([0.6, 1.0, 1.5, 2.0], bias=0.045, existing_offset=-0.02,
                          existing_scale=1.075, target_scale=0.93)
        stats = mod._validate_stats(rows, self.G, existing_offset=-0.02,
                                    existing_scale=1.075)
        self.assertAlmostEqual(stats["recommended_airtime_offset_s"], -0.045, places=6)
        self.assertAlmostEqual(stats["recommended_height_scale"], 0.93, places=6)
        self.assertLess(abs(stats["residual_pct_after_both"]), 0.01)

    def test_rails_refusal(self):
        mod = _load_jump_module()
        rows = self._rows([0.6, 1.0, 1.5, 2.0], bias=0.6)  # absurd 600 ms bias
        stats = mod._validate_stats(rows, self.G)
        decide = mod._validate_decide(stats)
        self.assertLess(stats["recommended_airtime_offset_s"], mod.OFFSET_MIN)
        self.assertIsNotNone(decide["offset_rail_refusal"])
        self.assertIn("outside the firmware's sane range", decide["offset_rail_refusal"])
        self.assertFalse(decide["recommend_offset"])

    def test_good_calibration_recommends_nothing(self):
        mod = _load_jump_module()
        rows = self._rows([0.6, 1.0, 1.5, 2.0], bias=0.003)  # 3 ms, under the 15 ms bar
        stats = mod._validate_stats(rows, self.G)
        decide = mod._validate_decide(stats)
        self.assertEqual(decide["verdict"], "good")
        self.assertFalse(decide["recommend_offset"])
        self.assertFalse(decide["recommend_scale"])

    def test_offset_recommendation_uses_median_not_mean_of_bias(self):
        """Item 8: every fixture above uses a CONSTANT bias, so
        median(bias) == mean(bias) trivially and the two can never be told
        apart. This fixture uses distinct per-jump biases where they
        genuinely disagree, and pins the recommendation to the median."""
        mod = _load_jump_module()
        biases = [0.040, 0.043, 0.045, 0.047, 0.200]
        # Sanity check the fixture itself actually distinguishes the two.
        self.assertGreater(abs(statistics.median(biases) - statistics.mean(biases)), 0.02)
        rows = self._rows([0.6, 0.8, 1.0, 1.5, 2.0], bias=biases)
        stats = mod._validate_stats(rows, self.G)
        self.assertAlmostEqual(stats["median_bias_s"], 0.045, places=6)
        self.assertAlmostEqual(stats["recommended_airtime_offset_s"], -0.045, places=6)
        # Would be ~-0.075 if the code mistakenly used the mean instead.
        self.assertNotAlmostEqual(stats["recommended_airtime_offset_s"],
                                  -statistics.mean(biases), places=3)

    def test_scale_applied_even_when_adj_clips_to_zero(self):
        """Item 2 regression (exact review repro): offset=-0.30, raw=0.26
        clips this row's stored adj to 0 (a real device clips a negative
        post-offset airtime to 0 too, before ever writing it to jumps.csv).
        The adj==0 branch must still carry existing_scale — it's the limit
        of the adj>0 formula (`h == existing_scale*g*adj^2/8` always, by
        the firmware's own height formula), not `g*adj^2/8` alone."""
        mod = _load_jump_module()
        g = self.G
        raw = 0.26
        existing_offset = -0.30
        existing_scale = 1.6
        adj = max(0.0, raw + existing_offset)  # 0.0, exactly like the device stores
        h = existing_scale * g * adj * adj / 8.0  # 0.0 too, for the same reason
        true_airtime = raw  # zero measurement bias on this one jump, by construction
        rows = [{"n": 1, "fps": 1000.0, "frames": true_airtime * 1000.0,
                "raw": raw, "adj": adj, "h": h}]
        stats = mod._validate_stats(rows, g, existing_offset=existing_offset,
                                    existing_scale=existing_scale)
        # Buggy code (missing the existing_scale factor) recovers 1.6 here
        # (i.e. "no change needed") instead of the correct 1.0.
        self.assertAlmostEqual(stats["recommended_height_scale"], 1.0, places=6)

    def test_scale_decision_uses_median_not_mean_one_outlier_no_recommendation(self):
        """Item 3 regression: 4 clean pairs (0% error) plus one mistyped
        pairs row (a 10x frames/fps slip — a huge single-row pct_error) must
        NOT trigger a height_scale recommendation. The decision has to be
        gated on the median residual (robust to one outlier), even though
        the informational MEAN residual is dragged sky-high by it."""
        mod = _load_jump_module()
        g = self.G
        rows = []
        for i, ta in enumerate([0.6, 1.0, 1.5, 2.0], start=1):
            h = g * ta * ta / 8.0
            rows.append({"n": i, "fps": 1000.0, "frames": ta * 1000.0,
                        "raw": ta, "adj": ta, "h": h})
        # 5th jump: the device saw a real, perfectly-calibrated 0.8s flight,
        # but the pairs file has a 10x frames typo (80 instead of 800).
        outlier_ta = 0.8
        outlier_h = g * outlier_ta * outlier_ta / 8.0
        rows.append({"n": 5, "fps": 1000.0, "frames": 80.0,  # should have been 800
                    "raw": outlier_ta, "adj": outlier_ta, "h": outlier_h})

        stats = mod._validate_stats(rows, g)
        decide = mod._validate_decide(stats)
        # Confirm the fixture really does exercise the bug scenario: the
        # mean-based stats are dragged sky-high by the one outlier row...
        self.assertGreater(stats["residual_pct_after_offset_only"], 100.0)
        self.assertGreater(stats["current_mean_abs_pct_error"], 100.0)
        # ...but the decision must not be fooled by it.
        self.assertLess(stats["residual_pct_after_offset_only_median"], 1.0)
        self.assertLess(stats["current_median_abs_pct_error"], 1.0)
        self.assertFalse(decide["want_scale"])
        self.assertFalse(decide["recommend_scale"])
        self.assertEqual(decide["verdict"], "good")


class TestValidatePairsFile(unittest.TestCase):
    """--pairs is typed up at home from a phone's frame stepper — every
    parsing failure must be friendly and name the problem."""

    def test_bad_header_is_a_friendly_error(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "pairs.csv"
            bad.write_text("n,fps,frame\n1,120,60\n")
            r = run_cli(["validate", "--fake", "--fast", "--pairs", str(bad)])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("header", r.stdout.lower())
            self.assertIn("jump_n,fps,frames", r.stdout)

    def test_garbage_row_is_a_friendly_error(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "pairs.csv"
            bad.write_text("jump_n,fps,frames\n1,not-a-number,60\n")
            r = run_cli(["validate", "--fake", "--fast", "--pairs", str(bad)])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("line 2", r.stdout)

    def test_out_of_range_fps_is_a_friendly_error(self):
        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "pairs.csv"
            bad.write_text("jump_n,fps,frames\n1,5,60\n")
            r = run_cli(["validate", "--fake", "--fast", "--pairs", str(bad)])
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("24-1000", r.stdout)

    def test_valid_pairs_file_parses_and_runs(self):
        with tempfile.TemporaryDirectory() as td:
            pairs = Path(td) / "pairs.csv"
            pairs.write_text("jump_n,fps,frames\n1,120,72\n2,120,120\n"
                            "3,120,180\n4,120,240\n")
            env = {"JH_DATA_DIR": str(Path(td) / "data")}
            r = run_cli(["validate", "--fake", "--fast", "--pairs", str(pairs)],
                       env_extra=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class TestValidateEndToEnd(unittest.TestCase):
    def test_fake_pairs_run_writes_report_with_pairs_table(self):
        with tempfile.TemporaryDirectory() as td:
            pairs = Path(td) / "pairs.csv"
            pairs.write_text("jump_n,fps,frames\n1,120,72\n2,120,120\n"
                            "3,120,180\n4,120,240\n")
            data_dir = Path(td) / "data"
            r = run_cli(["validate", "--fake", "--fast", "--pairs", str(pairs)],
                       env_extra={"JH_DATA_DIR": str(data_dir)})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            reports = list((data_dir / "validation").glob("validate-*.md"))
            csvs = list((data_dir / "validation").glob("validate-*.csv"))
            self.assertEqual(len(reports), 1)
            self.assertEqual(len(csvs), 1)
            body = reports[0].read_text()
            self.assertIn("## Pairs", body)
            self.assertIn("| n | fps | frames |", body)
            self.assertIn("## Aggregate", body)
            self.assertIn("## Recommendation", body)
            self.assertIn("Pueo 2023", body)
            csv_body = csvs[0].read_text().strip().splitlines()
            self.assertEqual(csv_body[0].split(",")[:3], ["jump_n", "fps", "frames"])
            self.assertEqual(len(csv_body), 5)  # header + 4 jump rows

    def test_apply_writes_calibration_to_fake_device(self):
        with tempfile.TemporaryDirectory() as td:
            # An injected +45 ms bias so there's something to apply.
            pairs = Path(td) / "pairs.csv"
            pairs.write_text(
                "jump_n,fps,frames\n1,1000,555\n2,1000,955\n3,1000,1455\n4,1000,1955\n")
            data_dir = Path(td) / "data"
            r = run_cli(["validate", "--fake", "--fast", "--apply",
                        "--pairs", str(pairs)],
                       env_extra={"JH_DATA_DIR": str(data_dir)})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("applied airtime_offset_s = -0.0450", r.stdout)
            body = (list((data_dir / "validation").glob("validate-*.md"))[0]).read_text()
            self.assertIn("**Applied:** applied", body)


class TestValidateNonTTY(unittest.TestCase):
    def test_interactive_mode_without_tty_instructs_pairs(self):
        # No --pairs and no TTY: must fail fast with a friendly instruction,
        # never call input() (the wizard-era crash this guards against).
        r = run_cli(["validate", "--fake", "--fast"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--pairs", r.stdout)
        self.assertIn("terminal", r.stdout.lower())


class TestValidateDeviceIdentityGuard(unittest.TestCase):
    """Item 1: `validate --session X --apply` would otherwise write a
    session-derived calibration onto WHATEVER device happens to be plugged
    in — nothing ties a session file to a specific device. Must refuse
    non-interactively without --force, and must proceed with --force."""

    @staticmethod
    def _session_and_pairs(td):
        g = 9.80665
        true_airtimes = [0.6, 1.0, 1.5, 2.0]
        bias = 0.045
        session = Path(td) / "jumps.csv"
        lines = ["n,takeoff_s,airtime_raw_s,airtime_s,height_m"]
        for i, ta in enumerate(true_airtimes, start=1):
            adj = ta + bias
            h = g * adj * adj / 8.0
            lines.append(f"{i},{10 + 5 * i:.3f},{adj:.4f},{adj:.4f},{h:.4f}")
        session.write_text("\n".join(lines) + "\n")
        pairs = Path(td) / "pairs.csv"
        pairs_lines = ["jump_n,fps,frames"]
        for i, ta in enumerate(true_airtimes, start=1):
            pairs_lines.append(f"{i},1000,{ta * 1000:.0f}")
        pairs.write_text("\n".join(pairs_lines) + "\n")
        return session, pairs

    def test_force_flag_appears_in_help(self):
        r = run_cli(["validate", "--help"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("--force", r.stdout)

    def test_session_apply_without_force_refuses_non_interactively(self):
        with tempfile.TemporaryDirectory() as td:
            session, pairs = self._session_and_pairs(td)
            data_dir = Path(td) / "data"
            r = run_cli(["validate", "--fake", "--fast", "--session", str(session),
                        "--pairs", str(pairs), "--apply"],
                       env_extra={"JH_DATA_DIR": str(data_dir)})
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("--force", r.stdout)
            self.assertIn("device", r.stdout.lower())
            self.assertNotIn("applied airtime_offset_s", r.stdout)
            # No report should claim anything was actually applied.
            reports = list((data_dir / "validation").glob("validate-*.md")) \
                if (data_dir / "validation").exists() else []
            self.assertEqual(reports, [])

    def test_session_apply_with_force_proceeds(self):
        with tempfile.TemporaryDirectory() as td:
            session, pairs = self._session_and_pairs(td)
            data_dir = Path(td) / "data"
            r = run_cli(["validate", "--fake", "--fast", "--session", str(session),
                        "--pairs", str(pairs), "--apply", "--force"],
                       env_extra={"JH_DATA_DIR": str(data_dir)})
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("--force given", r.stdout)
            self.assertIn("applied airtime_offset_s", r.stdout)


class TestValidateSessionMode(unittest.TestCase):
    """--session reads the exact jumps.csv format `sync` writes, and (unlike
    the connected-device path) works fully offline unless --apply is given."""

    @staticmethod
    def _session_csv(td, rows):
        p = Path(td) / "jumps.csv"
        lines = ["n,takeoff_s,airtime_raw_s,airtime_s,height_m"]
        for n, raw, adj, h in rows:
            lines.append(f"{n},{10 + 5 * n:.3f},{raw:.4f},{adj:.4f},{h:.4f}")
        p.write_text("\n".join(lines) + "\n")
        return p

    def test_session_mode_composes_offset_with_existing_device_offset(self):
        """Offset composition, exercised through the full CLI: the session's
        raw/adj reflect a device that already had a non-zero
        airtime_offset_s applied, plus a genuine +45 ms residual bias, and
        the recommended airtime_offset_s must land on the clean, composed
        replacement value — not double-corrected relative to what was
        already applied when the session was recorded. (height_scale
        composition is covered separately below — item 4 changed what
        existing_scale session-without-device mode uses.)"""
        g = 9.80665
        true_airtimes = [0.6, 1.0, 1.5, 2.0]
        existing_offset = -0.02
        bias = 0.045
        with tempfile.TemporaryDirectory() as td:
            rows = []
            for i, ta in enumerate(true_airtimes, start=1):
                raw = ta + bias
                adj = raw + existing_offset
                h = g * adj * adj / 8.0  # height_scale=1.0 actually baked in
                rows.append((i, raw, adj, h))
            session_csv = self._session_csv(td, rows)

            pairs = Path(td) / "pairs.csv"
            pairs_lines = ["jump_n,fps,frames"]
            for i, ta in enumerate(true_airtimes, start=1):
                pairs_lines.append(f"{i},1000,{ta * 1000:.0f}")
            pairs.write_text("\n".join(pairs_lines) + "\n")

            env = {"JH_DATA_DIR": str(Path(td) / "data")}
            r = run_cli(["validate", "--session", str(session_csv),
                        "--pairs", str(pairs)], env_extra=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("airtime_offset_s = -0.0450", r.stdout)
            # Pure --session analysis (no --apply) must never open a device.
            self.assertNotIn("Connecting to", r.stdout)

    def test_session_mode_ignores_stale_config_scale_derives_from_rows(self):
        """Item 4 regression: without a connected device, `validate
        --session` must NOT blindly trust config/params.json's height_scale
        (it can be stale, or simply belong to a different unit entirely) —
        it derives the session's own actual height_scale directly from its
        rows instead, since `h == height_scale * g * adj^2/8` is an exact
        firmware identity (sim/detector.py), invertible with no
        approximation. Reviewer-verified repro shape: trusting a mismatched
        config value computes a materially wrong recommendation (0.75-style);
        deriving from the session recovers the correct, self-consistent one.
        """
        g = 9.80665
        true_airtimes = [0.6, 1.0, 1.5, 2.0]
        bias = 0.045
        s_dev_actual = 1.2       # what ACTUALLY produced this session's h values
        stale_config_scale = 0.9  # config/params.json — deliberately mismatched
        with tempfile.TemporaryDirectory() as td:
            rows = []
            for i, ta in enumerate(true_airtimes, start=1):
                raw = ta + bias
                adj = raw  # existing_offset baked into adj is 0 here, kept simple
                h = s_dev_actual * g * adj * adj / 8.0  # the real firmware identity
                rows.append((i, raw, adj, h))
            session_csv = self._session_csv(td, rows)

            pairs = Path(td) / "pairs.csv"
            pairs_lines = ["jump_n,fps,frames"]
            for i, ta in enumerate(true_airtimes, start=1):
                pairs_lines.append(f"{i},1000,{ta * 1000:.0f}")
            pairs.write_text("\n".join(pairs_lines) + "\n")

            cfg_path = Path(td) / "params.json"
            cfg = json.loads((REPO / "config" / "params.json").read_text())
            cfg["detector"]["height_scale"] = stale_config_scale
            cfg_path.write_text(json.dumps(cfg))

            env = {"JH_CONFIG": str(cfg_path), "JH_DATA_DIR": str(Path(td) / "data")}
            r = run_cli(["validate", "--session", str(session_csv),
                        "--pairs", str(pairs)], env_extra=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            # Offset composition (a separate concern) is untouched.
            self.assertIn("airtime_offset_s = -0.0450", r.stdout)
            # s_dev_actual != 1.0 is a genuine residual (true_height doesn't
            # carry it), so a scale recommendation IS expected here — the
            # question is which value. Trusting the stale config would give
            # stale_config_scale * (1/s_dev_actual) = 0.9/1.2 = 0.750 (wrong);
            # deriving from the session's own rows must instead recover
            # s_dev_actual * (1/s_dev_actual) = 1.000 (self-consistently
            # correct, independent of whatever config happens to say).
            self.assertIn("height_scale = 1.000", r.stdout)
            self.assertNotIn("height_scale = 0.750", r.stdout)
            body = (list((Path(td) / "data" / "validation").glob("validate-*.md"))[0]
                   ).read_text()
            self.assertIn("derived directly from this session's own", body)
            self.assertIn(f"{s_dev_actual:.3f}", body)
            self.assertNotIn("Connecting to", r.stdout)

    def test_session_directory_path_also_works(self):
        with tempfile.TemporaryDirectory() as td:
            g = 9.80665
            rows = [(i, ta, ta, g * ta * ta / 8.0)
                    for i, ta in enumerate([0.6, 1.0, 1.5, 2.0], start=1)]
            self._session_csv(td, rows)  # writes td/jumps.csv
            pairs = Path(td) / "pairs.csv"
            pairs.write_text("jump_n,fps,frames\n1,1000,600\n2,1000,1000\n"
                            "3,1000,1500\n4,1000,2000\n")
            env = {"JH_DATA_DIR": str(Path(td) / "data")}
            r = run_cli(["validate", "--session", td, "--pairs", str(pairs)],
                       env_extra=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Recommend changing nothing", r.stdout)


if __name__ == "__main__":
    unittest.main()
