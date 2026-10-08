"""T-U1 (spec 2026-10-07 section 8.1): the `uf2`/`dfu` arm is verified before
anything says OK or resets.

Bench 2026-10-04 (docs/STATUS.md): `uf2` entered the bootloader 1 time in 3.
The old reboot_to_uf2() discarded sd_power_gpregret_set's return code, never
read GPREGRET back, and reset regardless -- after `OK uf2` had already gone
out. firmware/include/bootloader_arm.h is now the whole decision, as a pure
function over injected accessors; this drives every branch of it on the host.
The host half (no bootloader -> ERR, never `OK uf2` first) is in
test_hostdev.py; the device half is a bench gate (spec 8.3-6).

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
HARNESS = REPO / "firmware" / "test" / "bootloader_arm_harness.cpp"
MAIN_CPP = REPO / "firmware" / "src" / "main.cpp"


def _gxx() -> str:
    g = shutil.which("g++") or shutil.which("c++")
    if not g:
        raise unittest.SkipTest("no g++/c++ on this machine")
    return g


class TestArmDecision(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.mkdtemp(prefix="jh_bootarm_")
        cls.bin = str(Path(cls.tmp) / "bootloader_arm_harness")
        r = subprocess.run([_gxx(), "-std=c++11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(REPO / "firmware" / "include"), str(HARNESS),
                            "-o", cls.bin], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def arm(self, sd, rc_clr=0, rc_set=0, rc_get=0, get_value=0x57, reg=0x57,
            magic=0x57, unsupported=False) -> dict:
        args = [self.bin, str(int(sd)), str(rc_clr), str(rc_set), str(rc_get),
                hex(get_value), hex(reg), hex(magic)]
        if unsupported:
            args.append("unsupported")
        out = subprocess.run(args, capture_output=True, text=True, check=True).stdout
        return dict(kv.split("=", 1) for kv in out.split())

    def test_sd_path_ok_only_when_every_call_succeeds_and_the_readback_matches(self):
        r = self.arm(sd=True)
        self.assertEqual(r["status"], "OK")
        self.assertEqual((r["sd_calls"], r["reg_calls"]), ("3", "0"),
                         "with the SoftDevice up, NRF_POWER must never be touched "
                         "(a direct access hard-faults -- jh_power.cpp init())")
        self.assertEqual(r["set_arg"], "0x57")

    def test_sd_path_a_failed_set_is_failed(self):
        r = self.arm(sd=True, rc_set=8)              # NRF_ERROR_INVALID_STATE
        self.assertEqual(r["status"], "FAILED")
        self.assertEqual(r["rc"], "0/8/0")

    def test_sd_path_a_failed_clear_or_get_is_failed(self):
        self.assertEqual(self.arm(sd=True, rc_clr=1)["status"], "FAILED")
        self.assertEqual(self.arm(sd=True, rc_get=3)["status"], "FAILED")

    def test_sd_path_a_readback_that_did_not_stick_is_failed(self):
        r = self.arm(sd=True, get_value=0x00)
        self.assertEqual(r["status"], "FAILED")
        self.assertEqual(r["readback"], "0x0")

    def test_sd_path_compares_only_the_low_byte(self):
        self.assertEqual(self.arm(sd=True, get_value=0xFF57)["status"], "OK")

    def test_register_path_when_the_softdevice_is_off(self):
        r = self.arm(sd=False, reg=0xA8, magic=0xA8)
        self.assertEqual(r["status"], "OK")
        self.assertEqual((r["sd_calls"], r["reg_calls"]), ("0", "2"))
        self.assertEqual(r["reg_written"], "0xa8")
        self.assertEqual(self.arm(sd=False, reg=0x00, magic=0xA8)["status"], "FAILED")

    def test_no_bootloader_is_unsupported_and_touches_nothing(self):
        r = self.arm(sd=True, unsupported=True)
        self.assertEqual(r["status"], "UNSUPPORTED")
        self.assertEqual((r["sd_calls"], r["reg_calls"]), ("0", "0"))


class TestMainSaysOkOnlyAfterTheArm(unittest.TestCase):
    """Source order in main.cpp's dfu/uf2 arm: the arm and its status checks
    come before the OK line, and the reset comes after it."""

    def test_order(self):
        src = MAIN_CPP.read_text()
        start = src.index('} else if (cmd == "dfu" || cmd == "uf2") {')
        end = src.index('} else if (cmd == "events") {', start)
        arm = src[start:end]
        i_arm = arm.index("jh_link::arm_bootloader(")
        i_unsup = arm.index("ArmStatus::UNSUPPORTED")
        i_fail = arm.index("_arm_failed")
        i_ok = arm.index('"OK uf2"')
        i_reset = arm.index("jh_link::reset_now()")
        self.assertLess(i_arm, i_unsup)
        self.assertLess(i_unsup, i_ok)
        self.assertLess(i_fail, i_ok)
        self.assertLess(i_ok, i_reset)
        self.assertNotIn("reboot_to_uf2", src)
        self.assertNotIn("reboot_to_dfu", src)


if __name__ == "__main__":
    unittest.main()
