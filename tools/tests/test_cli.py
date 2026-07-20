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
    return subprocess.run([sys.executable, JUMP] + args, capture_output=True,
                          text=True, timeout=timeout, env=env, cwd=str(REPO))


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


if __name__ == "__main__":
    unittest.main()
