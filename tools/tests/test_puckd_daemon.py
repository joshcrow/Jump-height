"""Tests for tools/puckd/daemon.py -- the loop composing the six P1 modules
(docs/sync-agent-plan.md:41-58's job, :60-66's gates, :85's P2 build note:
"daemon.py composing P1; end-to-end test with fake device + fake rclone").

Three kinds of test, the same split test_puckd_serial_job.py and
test_puckd_flash.py already established in this repo:

  * End to end against a REAL tools/fake_device.py subprocess (a real pty,
    through tools/jump's own Device -- run_job_cycle()'s device_factory is
    left at its default) and a REAL rclone pointed at a small fake script on
    PATH via PUCKD_RCLONE (the same technique test_puckd_upload.py uses):
    the happy path, an upload that comes back short, a structural pull
    failure, and the two-plug-ins-one-upload-failing-one-not retry shape.

  * A `clear` that lies (wraps the real device rather than hand-scripting
    the whole protocol) for the one shape tools/fake_device.py's own `clear`
    cannot produce -- "nothing in it can lie the way step 7's gate must
    guard against" (test_puckd_serial_job.py's own words, restated here for
    the same reason).

  * G2's flash gate and the battery/charged poll driven directly with fake
    flash/garmin modules and a scripted stats-only Device stub -- the exact
    shapes test_puckd_flash.py and test_puckd_serial_job.py already use this
    technique for (a manifest, a stage, a STATS line) rather than running
    the real multi-stage flash() sequence a second time; that sequence is
    test_puckd_flash.py's own job, not this file's to duplicate.

Never edits tools/fake_device.py, tools/jump, or any sibling puckd module --
CLAUDE.md's "never edit a file you were not assigned".

Run via: python3 -m pytest tools/tests/test_puckd_daemon.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools"))

from puckd import daemon, flash, serial_job  # noqa: E402

_jump = serial_job._jump()  # tools/jump, loaded once (serial_job.py's own
# cached loader -- the same technique test_puckd_serial_job.py's own helpers
# reuse rather than re-deriving a second import shim).


# --------------------------------------------------------- fake_device glue

def _spawn_fake(scenario="session", extra=None):
    """Same technique as tools/tests/test_puckd_serial_job.py's own
    _spawn_fake(): spawn tools/fake_device.py directly, read its
    'PTY <path>' announcement. Caller must _kill(proc)."""
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "tools" / "fake_device.py"),
         "--scenario", scenario] + (extra or []),
        stdout=subprocess.PIPE, text=True)
    first = proc.stdout.readline().strip()
    if not first.startswith("PTY "):
        proc.terminate()
        raise RuntimeError(f"fake device failed to start: {first!r}")
    return proc, first.split(None, 1)[1]


def _kill(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


class _LyingClearWrapper:
    """Wraps a REAL jump.Device: forwards every command except `clear`,
    which it answers with a fake OK WITHOUT touching the real device -- so
    the confirming `stats` clear_puck() reads next still shows the puck's
    real, un-cleared contents. Exercises clear_puck()'s G4 refusal
    (docs/sync-agent-plan.md:64, "no stats -> no clear") without
    hand-scripting the rest of the wire protocol tools/fake_device.py
    already implements correctly -- the same "wrap the real thing for the
    one shape it can't produce" precedent test_puckd_serial_job.py's own
    module docstring sets out.
    """

    def __init__(self, real):
        self._real = real

    def drain_boot(self, timeout=5.0):
        return self._real.drain_boot(timeout=timeout)

    def command(self, cmd, timeout=20.0):
        if cmd == "clear":
            return ["# cleared stored data (lying)", "OK clear"]
        return self._real.command(cmd, timeout=timeout)

    def close(self):
        self._real.close()


def _lying_clear_factory(port_path):
    return _LyingClearWrapper(_jump.Device(port_path))


# ------------------------------------------------------------- fake rclone

_FAKE_RCLONE_SRC = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    # Fake rclone for tools/tests/test_puckd_daemon.py. Understands the four
    # subcommands upload.py issues. "gdrive:<path>" is backed by
    # $FAKE_RCLONE_STORE/<path> on local disk, so sizes are real UNLESS
    # FAKE_RCLONE_SHORT_FLAG names a file that currently exists, in which
    # case lsjson reports every size short by one byte -- a stand-in for a
    # copy that landed short or a flaky connection, toggled by a test simply
    # creating/deleting that flag file between two upload attempts.
    import json, os, shutil, sys
    from pathlib import Path

    STORE = Path(os.environ["FAKE_RCLONE_STORE"])
    SHORT_FLAG = os.environ.get("FAKE_RCLONE_SHORT_FLAG")


    def main():
        args = sys.argv[1:]
        cmd = args[0] if args else ""
        if cmd == "config":
            print("{}")
            return 0
        if cmd == "listremotes":
            print("gdrive:")
            return 0
        if cmd == "copy":
            src, dst = args[1], args[2]
            rel = dst[len("gdrive:"):]
            dest_dir = STORE / rel
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_dir / Path(src).name)
            return 0
        if cmd == "lsjson":
            rel = args[1][len("gdrive:"):]
            d = STORE / rel
            entries = []
            if d.is_dir():
                short = bool(SHORT_FLAG and os.path.exists(SHORT_FLAG))
                for p in d.iterdir():
                    size = p.stat().st_size
                    if short:
                        size = max(0, size - 1)
                    entries.append({"Name": p.name, "Size": size, "IsDir": False})
            print(json.dumps(entries))
            return 0
        print(f"unknown command {cmd!r}", file=sys.stderr)
        return 1


    if __name__ == "__main__":
        raise SystemExit(main())
    """
)


def _write_fake_rclone(path: Path) -> None:
    path.write_text(_FAKE_RCLONE_SRC)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# ------------------------------------------------------------- fake garmin

class _FakeGarmin:
    """A stand-in module for cfg.garmin_module -- the daemon needs exactly
    the four calls garmin.py exports for the Garmin leg (is_signed_in,
    fetch_new, last_seen, mark_seen); garth-level mocking is
    test_puckd_garmin.py's own job, not this file's to repeat."""

    def __init__(self, signed_in=True):
        self.signed_in = signed_in
        self.fetch_calls = []
        self.marked = []

    def is_signed_in(self):
        return self.signed_in

    def fetch_new(self, since_iso, out_dir):
        self.fetch_calls.append((since_iso, out_dir))
        return []

    def last_seen(self, store):
        return "1970-01-01T00:00:00Z"

    def mark_seen(self, store, iso):
        self.marked.append(iso)


# ---------------------------------------------------------- fake flash module

class _FakeFlashModule:
    """A stand-in for cfg.flash_module -- records exactly what
    run_job_cycle()/_maybe_flash() decided to do, without running the real
    multi-stage flash() sequence a second time (test_puckd_flash.py's own
    job)."""

    def __init__(self, manifest=None, needs=False, flash_result=None):
        self._manifest = manifest
        self._needs = needs
        self._flash_result = flash_result
        self.latest_manifest_calls = []
        self.needs_update_calls = []
        self.flash_calls = []

    def latest_manifest(self, site_url):
        self.latest_manifest_calls.append(site_url)
        return self._manifest

    def needs_update(self, puck_src, manifest):
        self.needs_update_calls.append((puck_src, manifest))
        return self._needs

    def flash(self, port_path, uf2_path, manifest, device_factory=None):
        self.flash_calls.append((port_path, uf2_path, manifest))
        return self._flash_result


# ---------------------------------------------------------------- recorder

class _Recorder:
    """cfg.notifier: records every (title, body) notify.notify() sends,
    exactly the shape notify.render() produces -- so a test can assert on
    the real, final strings without a real osascript call anywhere."""

    def __init__(self):
        self.calls = []

    def __call__(self, title, body):
        self.calls.append((title, body))

    def titled(self, prefix):
        return [c for c in self.calls if c[0].startswith(prefix)]


# ------------------------------------------------------------------- base

class _DaemonTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.home = self.tmp / "home"
        self.spool = self.tmp / "spool"
        self.recorder = _Recorder()

    def make_cfg(self, **overrides):
        kwargs = dict(
            home_dir=self.home,
            spool_dir=self.spool,
            site_url="",
            notifier=self.recorder,
            garmin_module=_FakeGarmin(),
            sleep=lambda s: None,
        )
        kwargs.update(overrides)
        return daemon.DaemonConfig(**kwargs)

    def rclone_env(self, short_flag: "Path | None" = None):
        """Point PUCKD_RCLONE at a freshly-written fake rclone and back
        "gdrive:" with a scratch directory -- same knobs test_puckd_upload.py's
        own fake uses, written fresh here per CLAUDE.md's "never edit a file
        you were not assigned" (no import from that file's private fixture)."""
        rclone_path = self.tmp / "fake_rclone.py"
        _write_fake_rclone(rclone_path)
        store = self.tmp / "drive"
        store.mkdir(exist_ok=True)
        env = {
            "PUCKD_RCLONE": str(rclone_path),
            "FAKE_RCLONE_STORE": str(store),
        }
        if short_flag is not None:
            env["FAKE_RCLONE_SHORT_FLAG"] = str(short_flag)
        return env


# ------------------------------------------------------------ find_puck_port

class TestFindPuckPort(unittest.TestCase):
    def test_prefers_seeed_vid_over_a_bare_usbmodem_entry(self):
        port = daemon.find_puck_port(lambda: [
            ("/dev/cu.usbmodem999", None),
            ("/dev/cu.usbmodem101", daemon.SEEED_VID),
        ])
        self.assertEqual(port, "/dev/cu.usbmodem101")

    def test_falls_back_to_usbmodem_glob_shape_when_no_seeed_vid_present(self):
        port = daemon.find_puck_port(lambda: [("/dev/cu.usbmodem202", None)])
        self.assertEqual(port, "/dev/cu.usbmodem202")

    def test_no_candidates_is_none(self):
        self.assertIsNone(daemon.find_puck_port(lambda: []))

    def test_a_non_puck_serial_device_is_not_picked(self):
        # e.g. a USB-serial debug adapter with a real vid, wrong pattern.
        self.assertIsNone(daemon.find_puck_port(lambda: [("/dev/cu.wchusbserial1", 4292)]))

    def test_multiple_seeed_candidates_resolve_deterministically(self):
        port = daemon.find_puck_port(lambda: [
            ("/dev/cu.usbmodem2", daemon.SEEED_VID),
            ("/dev/cu.usbmodem1", daemon.SEEED_VID),
        ])
        self.assertEqual(port, "/dev/cu.usbmodem1")


# ------------------------------------------------------------------ copy

class TestNeedsYouCopy(unittest.TestCase):
    """Pins the exact strings this module fires -- docs/sync-agent-plan.md's
    own literal example (line 13) and literal title (line 39), so a later
    edit that drifts the wording fails here first."""

    def test_puck_reset_action_is_the_spec_literal_example(self):
        from puckd import notify
        title, body = notify.render(
            "needs_you", line=daemon.PUCK_RESET_LINE, action=daemon.PUCK_RESET_ACTION)
        self.assertEqual(title, "Needs you: reset the puck")
        self.assertEqual(body, "Press the small button on the puck twice.")

    def test_garmin_reauth_title_matches_the_spec_literal(self):
        from puckd import notify
        title, _body = notify.render(
            "needs_you", line=daemon.NEEDS_YOU_GARMIN_LINE, action=daemon.NEEDS_YOU_GARMIN_ACTION)
        self.assertEqual(title, "Needs you: sign in to Garmin again")


# --------------------------------------------------------------- state I/O

class TestStateIO(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_missing_state_reads_as_empty_dict_not_an_error(self):
        self.assertEqual(daemon.load_state(self.tmp), {})

    def test_round_trips(self):
        daemon.save_state(self.tmp, {"last_ride_jumps": 12})
        self.assertEqual(daemon.load_state(self.tmp), {"last_ride_jumps": 12})

    def test_corrupt_state_file_reads_as_empty_not_a_crash(self):
        (self.tmp / daemon.STATE_FILENAME).write_text("{not json")
        self.assertEqual(daemon.load_state(self.tmp), {})


# ------------------------------------------------------- run_job_cycle: e2e

class TestRunJobCycleHappyPath(_DaemonTestBase):
    def test_exactly_one_synced_then_cleared_and_no_flash_offered(self):
        proc, port = _spawn_fake("session")
        try:
            env = self.rclone_env()
            with patch.dict(os.environ, env):
                cfg = self.make_cfg()
                report = daemon.run_job_cycle(port, cfg)
        finally:
            _kill(proc)

        self.assertTrue(report.pulled)
        self.assertTrue(report.verified, report.reasons)
        self.assertEqual(report.jumps, 4)  # tools/fake_device.py's demo session
        self.assertTrue(report.uploaded)
        self.assertTrue(report.cleared)
        self.assertFalse(report.flashed)  # site_url="" -- G2 never reachable
        self.assertIsNone(report.needs_you)

        synced = self.recorder.titled("Ride synced")
        self.assertEqual(len(synced), 1, self.recorder.calls)
        self.assertEqual(synced[0], ("Ride synced · 4 jumps", None))
        self.assertEqual(self.recorder.titled("Needs you"), [])

    def test_records_the_ride_for_the_menu_bar(self):
        proc, port = _spawn_fake("session")
        try:
            with patch.dict(os.environ, self.rclone_env()):
                cfg = self.make_cfg()
                report = daemon.run_job_cycle(port, cfg)
                daemon._record_ride(cfg, report.jumps, report.bundle_path)
        finally:
            _kill(proc)

        state = daemon.load_state(self.home)
        self.assertEqual(state["last_ride_jumps"], 4)
        self.assertIn("T", state["last_ride_iso"])  # a real ISO local timestamp


class TestRunJobCycleUploadFailure(_DaemonTestBase):
    def test_upload_short_fires_one_needs_you_and_never_clears(self):
        proc, port = _spawn_fake("session")
        marker = self.tmp / "short.flag"
        marker.write_text("down")
        try:
            with patch.dict(os.environ, self.rclone_env(short_flag=marker)):
                cfg = self.make_cfg()
                report = daemon.run_job_cycle(port, cfg)
                # The puck must still hold its data -- `clear` was never sent.
                stats = serial_job.read_stats(port)
        finally:
            _kill(proc)

        self.assertTrue(report.pulled)
        self.assertTrue(report.verified, report.reasons)
        self.assertFalse(report.uploaded)
        self.assertFalse(report.cleared)
        self.assertEqual(report.needs_you,
                         (daemon.NEEDS_YOU_UPLOAD_LINE, daemon.NEEDS_YOU_UPLOAD_ACTION))
        self.assertEqual(stats["stored_jumps"], 4)  # untouched

        self.assertEqual(self.recorder.titled("Ride synced"), [])
        needs_you = self.recorder.titled("Needs you")
        self.assertEqual(len(needs_you), 1, self.recorder.calls)
        self.assertEqual(needs_you[0],
                         ("Needs you: reconnect the puck", "Unplug the puck and plug it back in."))

    def test_an_unexpected_upload_exception_degrades_to_needs_you_not_a_crash(self):
        """Reproduces a real, verified upload.py gap: PUCKD_RCLONE naming a
        binary that does not exist raises FileNotFoundError straight
        through upload.upload() (its own two try/excepts catch
        RcloneNotFound and TimeoutExpired, not this) -- run_job_cycle() must
        contain that, not let it take the daemon down (G3: the daemon
        itself staying up is part of "recoverable"). The bundle is already
        on disk by this point, so nothing is lost."""
        proc, port = _spawn_fake("session")
        try:
            with patch.dict(os.environ, {"PUCKD_RCLONE": "/no/such/rclone-binary"}):
                cfg = self.make_cfg()
                report = daemon.run_job_cycle(port, cfg)  # must not raise
        finally:
            _kill(proc)

        self.assertTrue(report.pulled)
        self.assertTrue(report.bundle_path.exists())  # nothing lost
        self.assertFalse(report.uploaded)
        self.assertFalse(report.cleared)
        self.assertEqual(report.needs_you,
                         (daemon.NEEDS_YOU_UPLOAD_LINE, daemon.NEEDS_YOU_UPLOAD_ACTION))
        self.assertEqual(len(self.recorder.titled("Needs you")), 1)


class TestRunJobCyclePullFailed(_DaemonTestBase):
    def test_structural_pull_failure_fires_one_needs_you_reset_the_puck(self):
        proc, port = _spawn_fake("session", ["--traceraw-error", "storage_down"])
        try:
            cfg = self.make_cfg()
            report = daemon.run_job_cycle(port, cfg)
        finally:
            _kill(proc)

        self.assertFalse(report.pulled)
        self.assertIsNone(report.verified)
        self.assertFalse(report.uploaded)
        self.assertFalse(report.cleared)
        self.assertEqual(report.needs_you, (daemon.PUCK_RESET_LINE, daemon.PUCK_RESET_ACTION))
        self.assertEqual(self.recorder.calls,
                         [("Needs you: reset the puck", "Press the small button on the puck twice.")])


class TestRunJobCycleUnverifiedButUploaded(_DaemonTestBase):
    def test_unverified_upload_is_silent_and_uncleared(self):
        """CONTRACT.md's own words for this exact bundle: 'an unverified
        bundle is exactly the one Josh most wants to look at' -- nothing is
        actionable for Nick, so nothing is said to him, and G1 keeps the
        puck from being cleared over a question mark."""
        proc, port = _spawn_fake("session", ["--no-traceraw",
                                             "--trace-bytes-overreport", "5000"])
        try:
            with patch.dict(os.environ, self.rclone_env()):
                cfg = self.make_cfg()
                report = daemon.run_job_cycle(port, cfg)
        finally:
            _kill(proc)

        self.assertTrue(report.pulled)
        self.assertFalse(report.verified)
        self.assertTrue(report.uploaded)  # it IS safe on Drive
        self.assertFalse(report.cleared)  # but G1 refuses to clear over it
        self.assertIsNone(report.needs_you)
        self.assertEqual(self.recorder.calls, [])


class TestClearDoesNotConfirm(_DaemonTestBase):
    def test_a_lying_clear_refuses_and_fires_needs_you_not_synced_first(self):
        proc, port = _spawn_fake("session")
        try:
            with patch.dict(os.environ, self.rclone_env()):
                cfg = self.make_cfg(device_factory=_lying_clear_factory)
                report = daemon.run_job_cycle(port, cfg)
        finally:
            _kill(proc)

        self.assertTrue(report.uploaded)
        self.assertTrue(report.verified)
        self.assertFalse(report.cleared)
        self.assertEqual(report.needs_you, (daemon.PUCK_RESET_LINE, daemon.PUCK_RESET_ACTION))
        # "Ride synced" still fires -- the ride itself DID sync; it is the
        # separate clear step that failed to confirm.
        self.assertEqual(self.recorder.titled("Ride synced"), [("Ride synced · 4 jumps", None)])
        self.assertEqual(len(self.recorder.titled("Needs you")), 1)

    def test_flash_is_never_offered_when_clear_did_not_confirm(self):
        """G2 depends on a clear-confirmed-empty puck; a clear that failed
        to confirm must never let a flash attempt through underneath it."""
        proc, port = _spawn_fake("session")
        fake_flash = _FakeFlashModule(manifest={"src": "aaaa", "file": "x.uf2"}, needs=True)
        try:
            with patch.dict(os.environ, self.rclone_env()):
                cfg = self.make_cfg(device_factory=_lying_clear_factory,
                                    site_url="http://example.invalid",
                                    flash_module=fake_flash)
                daemon.run_job_cycle(port, cfg)
        finally:
            _kill(proc)

        self.assertEqual(fake_flash.latest_manifest_calls, [])
        self.assertEqual(fake_flash.flash_calls, [])


# ------------------------------------------------------------------ G2: flash

class TestMaybeFlashGate(_DaemonTestBase):
    def test_no_site_url_never_fetches_a_manifest(self):
        fake = _FakeFlashModule()
        cfg = self.make_cfg(site_url="", flash_module=fake)
        self.assertEqual(daemon._maybe_flash("/dev/x", "aaaa", cfg), (False, None))
        self.assertEqual(fake.latest_manifest_calls, [])

    def test_needs_update_false_never_calls_flash(self):
        # fetch_uf2_fn is a fake that WOULD hand back a usable path if ever
        # called -- so this test fails for the right reason if the
        # needs_update() gate is ever bypassed, rather than passing by
        # accident because the real default fetcher can't reach a bogus
        # host either.
        uf2 = self.tmp / "x.uf2"
        uf2.write_bytes(b"firmware")
        fetch_calls = []
        fake = _FakeFlashModule(manifest={"src": "aaaa", "file": "x.uf2"}, needs=False)
        cfg = self.make_cfg(site_url="http://example.invalid", flash_module=fake,
                            fetch_uf2_fn=lambda *a, **k: (fetch_calls.append(a), uf2)[1])
        self.assertEqual(daemon._maybe_flash("/dev/x", "aaaa", cfg), (False, None))
        self.assertEqual(fetch_calls, [])  # never even reached for the uf2 file
        self.assertEqual(fake.flash_calls, [])

    def test_unreachable_uf2_download_skips_silently(self):
        fake = _FakeFlashModule(manifest={"src": "bbbb", "file": "x.uf2"}, needs=True)
        cfg = self.make_cfg(site_url="http://example.invalid", flash_module=fake,
                            fetch_uf2_fn=lambda *a, **k: None)
        self.assertEqual(daemon._maybe_flash("/dev/x", "aaaa", cfg), (False, None))
        self.assertEqual(fake.flash_calls, [])
        self.assertEqual(self.recorder.calls, [])  # no needs_you for a network blip

    def test_empty_and_older_offers_the_update_and_notifies_puck_updated(self):
        uf2 = self.tmp / "x.uf2"
        uf2.write_bytes(b"firmware")
        manifest = {"src": "bbbb", "file": "x.uf2", "sha256": "irrelevant-to-the-fake"}
        result = flash.FlashResult(ok=True, src_after="bbbb",
                                   stage_reached=flash.STAGE_DONE, error=None)
        fake = _FakeFlashModule(manifest=manifest, needs=True, flash_result=result)
        cfg = self.make_cfg(site_url="http://example.invalid", flash_module=fake,
                            fetch_uf2_fn=lambda *a, **k: uf2)
        self.assertEqual(daemon._maybe_flash("/dev/x", "aaaa", cfg), (True, None))
        self.assertEqual(len(fake.flash_calls), 1)
        self.assertEqual(self.recorder.calls, [("Puck updated", None)])

    def test_flash_failure_after_touching_the_device_fires_needs_you(self):
        uf2 = self.tmp / "x.uf2"
        uf2.write_bytes(b"firmware")
        manifest = {"src": "bbbb", "file": "x.uf2", "sha256": "x"}
        result = flash.FlashResult(ok=False, src_after=None,
                                   stage_reached=flash.STAGE_PORT_WAIT, error="no port returned")
        fake = _FakeFlashModule(manifest=manifest, needs=True, flash_result=result)
        cfg = self.make_cfg(site_url="http://example.invalid", flash_module=fake,
                            fetch_uf2_fn=lambda *a, **k: uf2)
        self.assertEqual(
            daemon._maybe_flash("/dev/x", "aaaa", cfg),
            (False, (daemon.PUCK_RESET_LINE, daemon.PUCK_RESET_ACTION)))
        self.assertEqual(self.recorder.calls,
                         [("Needs you: reset the puck", "Press the small button on the puck twice.")])

    def test_sha256_refusal_never_touched_the_device_so_no_needs_you(self):
        uf2 = self.tmp / "x.uf2"
        uf2.write_bytes(b"firmware")
        manifest = {"src": "bbbb", "file": "x.uf2", "sha256": "wrong"}
        result = flash.FlashResult(ok=False, src_after=None,
                                   stage_reached=flash.STAGE_SHA256, error="sha256 mismatch")
        fake = _FakeFlashModule(manifest=manifest, needs=True, flash_result=result)
        cfg = self.make_cfg(site_url="http://example.invalid", flash_module=fake,
                            fetch_uf2_fn=lambda *a, **k: uf2)
        self.assertEqual(daemon._maybe_flash("/dev/x", "aaaa", cfg), (False, None))
        self.assertEqual(self.recorder.calls, [])


# ------------------------------------------------------- battery / charged

class _ScriptedStatsDevice:
    """Answers `stats` with one fixed line each time it's popped off a
    script; every other command gets a bare OK. Same shape as
    test_puckd_serial_job.py's own _ScriptedStatsDevice, written fresh here
    (CLAUDE.md: never edit/import a file you were not assigned)."""

    def __init__(self, lines):
        self._lines = list(lines)

    def drain_boot(self, timeout=5.0):
        pass

    def command(self, cmd, timeout=20.0):
        if cmd == "stats":
            return [self._lines.pop(0)]
        return [f"OK {cmd}"]

    def close(self):
        pass


class TestChargedFiresExactlyOnce(_DaemonTestBase):
    def test_charged_fires_once_per_attachment_not_on_every_poll(self):
        lines = [
            "STATS session_jumps=0 session_best_m=0.000 stored_jumps=0 stored_best_m=0.000 "
            "trace_bytes=0 vbat_mv=4100 batt_pct=88 chg=1",
            "STATS session_jumps=0 session_best_m=0.000 stored_jumps=0 stored_best_m=0.000 "
            "trace_bytes=0 vbat_mv=4150 batt_pct=97 chg=0",
            "STATS session_jumps=0 session_best_m=0.000 stored_jumps=0 stored_best_m=0.000 "
            "trace_bytes=0 vbat_mv=4150 batt_pct=97 chg=0",
        ]
        dev = _ScriptedStatsDevice(lines)
        cfg = self.make_cfg(device_factory=lambda p: dev)
        session = daemon.AttachmentSession(port="/dev/x")

        daemon.poll_attached("/dev/x", cfg, session)   # charging, 88%
        self.assertEqual(self.recorder.calls, [])

        daemon.poll_attached("/dev/x", cfg, session)   # 1 -> 0 at 97%: charged
        self.assertEqual(self.recorder.calls, [("Puck charged", None)])

        daemon.poll_attached("/dev/x", cfg, session)   # still done charging: silent
        self.assertEqual(self.recorder.calls, [("Puck charged", None)])

    def test_below_95_percent_does_not_fire_even_on_the_1_to_0_edge(self):
        lines = [
            "STATS session_jumps=0 session_best_m=0.000 stored_jumps=0 stored_best_m=0.000 "
            "trace_bytes=0 vbat_mv=3900 batt_pct=70 chg=1",
            "STATS session_jumps=0 session_best_m=0.000 stored_jumps=0 stored_best_m=0.000 "
            "trace_bytes=0 vbat_mv=3900 batt_pct=70 chg=0",
        ]
        dev = _ScriptedStatsDevice(lines)
        cfg = self.make_cfg(device_factory=lambda p: dev)
        session = daemon.AttachmentSession(port="/dev/x")
        daemon.poll_attached("/dev/x", cfg, session)
        daemon.poll_attached("/dev/x", cfg, session)
        self.assertEqual(self.recorder.calls, [])


# --------------------------------------------------------------- Garmin leg

class TestGarminLeg(_DaemonTestBase):
    def test_signed_out_fires_needs_you_once_not_on_every_tick(self):
        cfg = self.make_cfg(garmin_module=_FakeGarmin(signed_in=False))
        daemon._run_garmin(cfg)
        daemon._run_garmin(cfg)
        daemon._run_garmin(cfg)
        self.assertEqual(self.recorder.calls,
                         [("Needs you: sign in to Garmin again",
                           "Sign in again from Set up in the menu bar.")])

    def test_signed_in_fetches_and_never_raises_into_the_caller(self):
        g = _FakeGarmin(signed_in=True)
        cfg = self.make_cfg(garmin_module=g)
        daemon._run_garmin(cfg)  # must not raise
        self.assertEqual(len(g.fetch_calls), 1)
        self.assertEqual(self.recorder.calls, [])

    def test_a_raising_garmin_module_never_reaches_the_caller(self):
        class _Boom:
            def is_signed_in(self):
                raise RuntimeError("garth is down")
        cfg = self.make_cfg(garmin_module=_Boom())
        daemon._run_garmin(cfg)  # "never blocks the puck job" -- must not raise


# ----------------------------------------------------- run_forever: retry

class TestRunForeverRetryFromSpool(_DaemonTestBase):
    def test_second_plugin_after_a_failed_upload_eventually_syncs(self):
        proc, port = _spawn_fake("session")
        marker = self.tmp / "short.flag"
        marker.write_text("down")
        env = self.rclone_env(short_flag=marker)
        calls = {"n": 0}

        def scripted_find_port():
            calls["n"] += 1
            if calls["n"] == 1:
                return port                 # first plug-in
            if calls["n"] == 2:
                marker.unlink()             # "the wifi comes back" before he
                return None                 # replugs it -- detach first
            return port                     # second plug-in

        reports = []
        try:
            with patch.dict(os.environ, env):
                cfg = self.make_cfg()
                daemon.run_forever(cfg, find_port=scripted_find_port,
                                   on_cycle=reports.append, max_iterations=3)
        finally:
            _kill(proc)

        self.assertEqual(len(reports), 2, "exactly two plug-in events across 3 ticks")
        first, second = reports
        self.assertFalse(first.uploaded)
        self.assertFalse(first.cleared)
        self.assertTrue(second.uploaded)
        self.assertTrue(second.verified)
        self.assertTrue(second.cleared)

        self.assertEqual(len(self.recorder.titled("Ride synced")), 1)
        self.assertEqual(len(self.recorder.titled("Needs you")), 1)

    def test_the_same_attachment_never_reruns_the_job_on_later_ticks(self):
        proc, port = _spawn_fake("session")
        try:
            with patch.dict(os.environ, self.rclone_env()):
                cfg = self.make_cfg()
                reports = []
                daemon.run_forever(cfg, find_port=lambda: port,
                                   on_cycle=reports.append, max_iterations=4)
        finally:
            _kill(proc)

        self.assertEqual(len(reports), 1, "one job for one continuous attachment")
        self.assertEqual(len(self.recorder.titled("Ride synced")), 1)


if __name__ == "__main__":
    unittest.main()
