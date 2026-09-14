"""Tests for packaging/src/launcher.py -- the self-install the rider
performs by double-clicking the app.

Nothing here runs launchctl, copies anything into the real /Applications, or
writes the real ~/Library/LaunchAgents: APPLICATIONS_DIR is pointed at a
scratch directory, Path.home() at another, and `subprocess.run` is replaced
by a recorder, so the ORDER of operations -- the part that decides whether
opening the app a second time can destroy a working install -- is pinned on
any machine.

The three failures these exist for, all of them ordinary rider behaviour:

  * he opens the .dmg again while the installed copy is mid-job (the old
    code rmtree'd the bundle that copy was executing from, before copying),
  * the copy fails part-way (the old code had already deleted the install,
    so /Applications ended up with no app and a LaunchAgent pointing into
    nothing),
  * launchctl refuses every spelling (the old code returned True anyway and
    this process exited -- an install that started nothing at all, behind no
    menu bar, on a Mac 300 miles away).

Run via: python3 -m pytest tools/tests/test_puckd_launcher.py -q
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent.parent
_LAUNCHER = REPO / "packaging" / "src" / "launcher.py"

_spec = importlib.util.spec_from_file_location("puckd_launcher_under_test", _LAUNCHER)
launcher = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(launcher)


def _make_bundle(root: Path, name: str = "JumpHeight Sync.app") -> Path:
    """A minimally app-shaped directory: the executable the plist points at
    and a Resources tree, which is all launcher.py ever looks for."""
    bundle = root / name
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "Resources" / "tools").mkdir(parents=True)
    exe = bundle / "Contents" / "MacOS" / "JumpHeight Sync"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return bundle


class _LaunchctlRecorder:
    """Stands in for subprocess.run: records every launchctl argv and
    answers with a scripted return code per SUBCOMMAND."""

    def __init__(self, codes=None):
        self.calls = []
        self.codes = codes or {}

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        code = self.codes.get(argv[1], 0)
        return subprocess.CompletedProcess(argv, code, b"", b"")

    @property
    def subcommands(self):
        return [c[1] for c in self.calls]


class LauncherTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.apps = self.tmp / "Applications"
        self.apps.mkdir()
        self.home = self.tmp / "home"
        (self.home / "Library" / "LaunchAgents").mkdir(parents=True)
        self._patches = [
            patch.object(launcher, "APPLICATIONS_DIR", self.apps),
            patch.object(launcher.Path, "home", staticmethod(lambda: self.home)),
            patch.object(sys, "argv", ["JumpHeight Sync"]),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def plist_data(self):
        import plistlib
        path = self.home / "Library" / "LaunchAgents" / f"{launcher.LABEL}.plist"
        return plistlib.loads(path.read_bytes())


class TestCopyToApplications(LauncherTestCase):
    def test_an_existing_install_survives_a_copy_that_fails(self):
        installed = _make_bundle(self.apps)
        (installed / "Contents" / "marker").write_text("the working install")
        dmg = _make_bundle(self.tmp / "Volumes" / "JumpHeight",
                           name="JumpHeight Sync.app")

        with patch("shutil.copytree",
                   side_effect=OSError("No space left on device")):
            with self.assertRaises(OSError):
                launcher._copy_to_applications(dmg)

        self.assertTrue(installed.is_dir(), "the install was not deleted first")
        self.assertEqual("the working install",
                         (installed / "Contents" / "marker").read_text())

    def test_a_successful_copy_replaces_the_install_and_leaves_no_scratch(self):
        installed = _make_bundle(self.apps)
        (installed / "Contents" / "marker").write_text("old")
        dmg_root = self.tmp / "Volumes" / "JumpHeight"
        dmg = _make_bundle(dmg_root)
        (dmg / "Contents" / "marker").write_text("new")

        dest = launcher._copy_to_applications(dmg)

        self.assertEqual(dest, self.apps / "JumpHeight Sync.app")
        self.assertEqual("new", (dest / "Contents" / "marker").read_text())
        leftovers = [p.name for p in self.apps.iterdir()
                     if p.name.endswith((".new", ".old"))]
        self.assertEqual([], leftovers)


class TestInstallOrdering(LauncherTestCase):
    def test_the_running_copy_is_booted_out_before_the_bundle_is_touched(self):
        """Opening the app a second time while launchd's copy is mid-job:
        stop that copy FIRST, then replace the bundle it was running from."""
        _make_bundle(self.apps)
        dmg = _make_bundle(self.tmp / "Volumes" / "JumpHeight")
        recorder = _LaunchctlRecorder()
        copy_order = []

        def watching_copytree(src, dst, **kw):
            copy_order.append(("copy", len(recorder.calls)))
            Path(dst).mkdir(parents=True)
            (Path(dst) / "Contents" / "MacOS").mkdir(parents=True)
            (Path(dst) / "Contents" / "MacOS" / "JumpHeight Sync").write_text("x")
            return dst

        with patch("subprocess.run", recorder), \
             patch.object(launcher, "DMG_PREFIX", str(self.tmp / "Volumes")), \
             patch("shutil.copytree", watching_copytree):
            handed_off = launcher._install_and_hand_off(
                dmg / "Contents" / "Resources")

        self.assertTrue(handed_off)
        self.assertEqual("bootout", recorder.subcommands[0])
        self.assertEqual(1, copy_order[0][1],
                         "the copy happened AFTER the bootout, not before")

    def test_the_plist_carries_the_launchd_flag(self):
        """Without it the launchd copy self-installs again -- bootout,
        bootstrap, repeat, with KeepAlive putting it back every time."""
        bundle = _make_bundle(self.tmp / "Applications-real")
        with patch("subprocess.run", _LaunchctlRecorder()):
            launcher._install_and_hand_off(bundle / "Contents" / "Resources")
        data = self.plist_data()
        self.assertIn(launcher.LAUNCHD_FLAG, data["ProgramArguments"])
        self.assertEqual(launcher.LABEL, data["Label"])

    def test_the_plist_names_a_log_file_for_a_crash_before_our_own_logging(self):
        bundle = _make_bundle(self.tmp / "Applications-real")
        with patch("subprocess.run", _LaunchctlRecorder()):
            launcher._install_and_hand_off(bundle / "Contents" / "Resources")
        data = self.plist_data()
        self.assertTrue(data["StandardErrorPath"].endswith(".err.log"))
        self.assertTrue(data["StandardOutPath"].endswith(".out.log"))

    def test_a_copy_already_started_by_launchd_does_not_reinstall(self):
        bundle = _make_bundle(self.tmp / "Applications-real")
        recorder = _LaunchctlRecorder()
        with patch.object(sys, "argv", ["JumpHeight Sync", launcher.LAUNCHD_FLAG]), \
             patch("subprocess.run", recorder):
            self.assertFalse(
                launcher._install_and_hand_off(bundle / "Contents" / "Resources"))
        self.assertEqual([], recorder.calls, "no launchctl, no recursion")


class TestHandoffFailure(LauncherTestCase):
    def test_every_launchctl_spelling_failing_means_run_it_here(self):
        """The one outcome that must never happen quietly: this process
        exits believing launchd took over, and nothing is running."""
        bundle = _make_bundle(self.tmp / "Applications-real")
        recorder = _LaunchctlRecorder(
            codes={"bootstrap": 5, "load": 1, "kickstart": 3, "bootout": 3})
        with patch("subprocess.run", recorder):
            self.assertFalse(
                launcher._install_and_hand_off(bundle / "Contents" / "Resources"))
        self.assertEqual(["bootout", "bootstrap", "load", "kickstart"],
                         recorder.subcommands)

    def test_an_already_registered_service_is_kickstarted_not_abandoned(self):
        """`bootstrap` returns EALREADY when a bootout did not take; the old
        `load -w` spelling refuses for the same reason. kickstart -k is the
        one that restarts what is already there."""
        bundle = _make_bundle(self.tmp / "Applications-real")
        recorder = _LaunchctlRecorder(codes={"bootstrap": 5, "load": 1})
        with patch("subprocess.run", recorder):
            self.assertTrue(
                launcher._install_and_hand_off(bundle / "Contents" / "Resources"))
        self.assertIn("kickstart", recorder.subcommands)

    def test_the_gui_domain_is_used_not_user(self):
        """A menu-bar agent needs an Aqua session; user/<uid> has none."""
        bundle = _make_bundle(self.tmp / "Applications-real")
        recorder = _LaunchctlRecorder()
        with patch("subprocess.run", recorder):
            launcher._install_and_hand_off(bundle / "Contents" / "Resources")
        import os
        self.assertIn(f"gui/{os.getuid()}", " ".join(recorder.calls[0]))

    def test_launchctl_missing_entirely_is_not_a_traceback(self):
        bundle = _make_bundle(self.tmp / "Applications-real")
        with patch("subprocess.run", side_effect=OSError("no launchctl")):
            self.assertFalse(
                launcher._install_and_hand_off(bundle / "Contents" / "Resources"))


if __name__ == "__main__":
    unittest.main()
