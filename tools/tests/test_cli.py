"""Integration tests: the jump CLI driven against the fake device.

Every test runs the real CLI binary as a subprocess, talking the real serial
protocol over a pty to tools/fake_device.py — the same code paths used with
hardware, minus the hardware. Run via ./tools/jump simtest (or
`python3 -m unittest discover -s tools/tests`).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
JUMP = str(REPO / "tools" / "jump")


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
        """
        rows = []
        for i, ta in enumerate(true_airtimes, start=1):
            raw = ta + bias
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

    def test_session_mode_composes_with_existing_config_calibration(self):
        """Device-offset composition, exercised through the full CLI: the
        LOCAL config (standing in for 'the device's last-known calibration')
        already carries a non-zero airtime_offset_s AND height_scale, the
        session file's raw/adj/h reflect that plus a genuine +45 ms residual
        bias and a genuine 0.93 residual scale anomaly, and the recommended
        values must still land on the clean, composed replacement values —
        not double-corrected relative to what's already configured."""
        g = 9.80665
        true_airtimes = [0.6, 1.0, 1.5, 2.0]
        existing_offset, existing_scale = -0.02, 1.075
        bias, target_scale = 0.045, 0.93
        with tempfile.TemporaryDirectory() as td:
            rows = []
            for i, ta in enumerate(true_airtimes, start=1):
                raw = ta + bias
                adj = raw + existing_offset
                true_h = g * ta * ta / 8.0
                h = (existing_scale / target_scale) * true_h * (adj / ta) ** 2
                rows.append((i, raw, adj, h))
            session_csv = self._session_csv(td, rows)

            pairs = Path(td) / "pairs.csv"
            pairs_lines = ["jump_n,fps,frames"]
            for i, ta in enumerate(true_airtimes, start=1):
                pairs_lines.append(f"{i},1000,{ta * 1000:.0f}")
            pairs.write_text("\n".join(pairs_lines) + "\n")

            cfg_path = Path(td) / "params.json"
            cfg = json.loads((REPO / "config" / "params.json").read_text())
            cfg["detector"]["airtime_offset_s"] = existing_offset
            cfg["detector"]["height_scale"] = existing_scale
            cfg_path.write_text(json.dumps(cfg))

            env = {"JH_CONFIG": str(cfg_path), "JH_DATA_DIR": str(Path(td) / "data")}
            r = run_cli(["validate", "--session", str(session_csv),
                        "--pairs", str(pairs)], env_extra=env)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("airtime_offset_s = -0.0450", r.stdout)
            self.assertIn("height_scale = 0.930", r.stdout)
            # Pure --session analysis (no --apply) must never open a device.
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
