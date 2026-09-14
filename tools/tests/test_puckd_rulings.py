#!/usr/bin/env python3
"""The owner's rulings on the 2026-09-13 review, each pinned.

  1. A puck with no ride on it produces nothing: no pull, no upload, no
     notification. Nick charges it every night.
  2. "Ride synced" is said only after the clear confirms (spec step 9).
  3. One puck message: "check the puck". (test_puckd_daemon.py carries it.)
  4. An upload that fails is retried from the spool, quietly; Nick hears
     "reconnect Google Drive" only when the remote is gone or a ride has
     waited a day.
  5. Garmin is nagged only if he ever signed in.
  6. The site URL defaults to the live page.
  7. The app updates itself silently -- but only on a tick with no puck
     attached, because `launchctl kickstart -k` kills the process.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import json
import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from test_puckd_daemon import _DaemonTestBase, _FakeGarmin, _kill, _spawn_fake  # noqa: E402

sys.path.insert(0, str(HERE.parent))
from puckd import daemon  # noqa: E402


class EmptyPuckIsSilent(_DaemonTestBase):
    def test_second_plug_in_after_a_sync_does_nothing(self):
        proc, port = _spawn_fake("session")
        try:
            with patch.dict(os.environ, self.rclone_env()):
                cfg = self.make_cfg()
                first = daemon.run_job_cycle(port, cfg)
                store_after_first = sorted((self.tmp / "drive").rglob("*.zip"))
                second = daemon.run_job_cycle(port, cfg)
                store_after_second = sorted((self.tmp / "drive").rglob("*.zip"))
        finally:
            _kill(proc)

        self.assertTrue(first.cleared, first)
        self.assertTrue(second.empty)
        self.assertFalse(second.pulled)
        self.assertEqual(second.jumps, 0)
        self.assertEqual(store_after_first, store_after_second, "nothing uploaded twice")
        self.assertEqual(self.recorder.titled("Ride synced"), [("Ride synced · 4 jumps", None)],
                         "one ride, one notification -- the empty plug-in says nothing")
        self.assertEqual(len(daemon._pending_bundles(cfg)), 0)
        self.assertEqual(len(list((self.spool / daemon.SENT_DIRNAME).glob("*.zip"))), 1)

    def test_an_empty_puck_still_gets_the_update_check(self):
        proc, port = _spawn_fake("session")
        try:
            with patch.dict(os.environ, self.rclone_env()):
                cfg = self.make_cfg()
                daemon.run_job_cycle(port, cfg)           # empties it
                fake = _FakeFlash()
                cfg2 = self.make_cfg(site_url="http://example.invalid", flash_module=fake)
                report = daemon.run_job_cycle(port, cfg2)
        finally:
            _kill(proc)
        self.assertTrue(report.empty)
        self.assertEqual(fake.latest_manifest_calls, ["http://example.invalid"])
        self.assertIsNotNone(report.src, "src comes from a real `info` read")
        self.assertEqual(fake.needs_update_calls[0][0], report.src)


class _FakeFlash:
    def __init__(self):
        self.latest_manifest_calls = []
        self.needs_update_calls = []

    def latest_manifest(self, site_url):
        self.latest_manifest_calls.append(site_url)
        return None

    def needs_update(self, puck_src, manifest):
        self.needs_update_calls.append((puck_src, manifest))
        return False


class TheFolderIsSharedWithJosh(_DaemonTestBase):
    def test_shared_once_after_the_first_confirmed_upload(self):
        calls = []
        proc, port = _spawn_fake("session")
        try:
            with patch.dict(os.environ, self.rclone_env()):
                cfg = self.make_cfg(share_fn=lambda d: (calls.append(d), True)[1])
                daemon.run_job_cycle(port, cfg)
                daemon.retry_spool(cfg)            # a later upload path
        finally:
            _kill(proc)
        self.assertEqual(calls, ["JumpHeight"], "the top-level folder, once")


class SyncedIsSaidAfterTheClear(_DaemonTestBase):
    def test_notification_order_is_clear_then_synced(self):
        order = []
        real_clear = daemon.serial_job.clear_puck

        def spy_clear(*a, **k):
            order.append("clear")
            return real_clear(*a, **k)

        def recorder(title, body):
            order.append(title)

        proc, port = _spawn_fake("session")
        try:
            with patch.dict(os.environ, self.rclone_env()), \
                 patch.object(daemon.serial_job, "clear_puck", spy_clear):
                cfg = self.make_cfg(notifier=recorder)
                daemon.run_job_cycle(port, cfg)
        finally:
            _kill(proc)
        self.assertEqual(order, ["clear", "Ride synced · 4 jumps"])


class TheSpoolRetries(_DaemonTestBase):
    def _pending(self, name="jumpheight-xxxx-20260913-0100.zip", age_s=0.0):
        self.spool.mkdir(parents=True, exist_ok=True)
        p = self.spool / name
        p.write_bytes(b"PK\x05\x06" + b"\0" * 18)
        if age_s:
            t = time.time() - age_s
            os.utime(p, (t, t))
        return p

    def test_a_pending_bundle_is_uploaded_and_moved_to_sent(self):
        with patch.dict(os.environ, self.rclone_env()):
            cfg = self.make_cfg()
            p = self._pending()
            n = daemon.retry_spool(cfg)
        self.assertEqual(n, 1)
        self.assertFalse(p.exists())
        self.assertTrue((self.spool / daemon.SENT_DIRNAME / p.name).exists())
        self.assertTrue((self.tmp / "drive" / daemon.INBOX_DIR / p.name).exists())
        self.assertEqual(self.recorder.calls, [])

    def test_a_fresh_failure_with_drive_connected_says_nothing(self):
        marker = self.tmp / "short.flag"
        marker.write_text("down")
        with patch.dict(os.environ, self.rclone_env(short_flag=marker)):
            cfg = self.make_cfg()
            p = self._pending()
            n = daemon.retry_spool(cfg)
        self.assertEqual(n, 0)
        self.assertTrue(p.exists(), "still pending")
        self.assertEqual(self.recorder.calls, [])

    def test_drive_gone_says_reconnect_once(self):
        marker = self.tmp / "short.flag"
        marker.write_text("down")
        env = self.rclone_env(short_flag=marker)
        env["FAKE_RCLONE_NO_REMOTE"] = "1"
        with patch.dict(os.environ, env):
            cfg = self.make_cfg()
            (self.spool / daemon.SENT_DIRNAME).mkdir(parents=True)   # Drive WAS connected once
            self._pending()
            daemon.retry_spool(cfg)
            daemon.retry_spool(cfg)
        self.assertEqual(self.recorder.calls,
                         [("Needs you: reconnect Google Drive", "Open Set up in the menu bar.")])

    def test_drive_never_connected_is_setups_job_not_a_needs_you(self):
        marker = self.tmp / "short.flag"
        marker.write_text("down")
        env = self.rclone_env(short_flag=marker)
        env["FAKE_RCLONE_NO_REMOTE"] = "1"
        with patch.dict(os.environ, env):
            cfg = self.make_cfg()
            self._pending()
            daemon.retry_spool(cfg)
        self.assertEqual(self.recorder.calls, [], "the setup window is already asking")

    def test_a_ride_waiting_a_day_says_reconnect(self):
        marker = self.tmp / "short.flag"
        marker.write_text("down")
        with patch.dict(os.environ, self.rclone_env(short_flag=marker)):
            cfg = self.make_cfg()
            self._pending(age_s=daemon.PENDING_MAX_AGE_S + 60)
            daemon.retry_spool(cfg)
        self.assertEqual(len(self.recorder.titled("Needs you")), 1)

    def test_a_confirmed_upload_rearms_the_message(self):
        marker = self.tmp / "short.flag"
        marker.write_text("down")
        with patch.dict(os.environ, self.rclone_env(short_flag=marker)):
            cfg = self.make_cfg()
            self._pending(age_s=daemon.PENDING_MAX_AGE_S + 60)
            daemon.retry_spool(cfg)
            marker.unlink()                      # Drive is back
            daemon.retry_spool(cfg)
            self._pending(name="jumpheight-xxxx-20260914-0100.zip",
                          age_s=daemon.PENDING_MAX_AGE_S + 60)
            marker.write_text("down")
            daemon.retry_spool(cfg)
        self.assertEqual(len(self.recorder.titled("Needs you")), 2)

    def test_the_loop_retries_on_its_own_timer(self):
        clock = {"t": 1_000_000.0}
        with patch.dict(os.environ, self.rclone_env()):
            cfg = self.make_cfg(now=lambda: clock["t"])
            p = self._pending()

            def tick_find_port():
                clock["t"] += daemon.SPOOL_RETRY_INTERVAL_S / 2 + 1
                return None
            daemon.run_forever(cfg, find_port=tick_find_port, max_iterations=2)
        self.assertFalse(p.exists(), "retried without any puck being plugged in")


class ThePanelSaysWhatIsHappening(_DaemonTestBase):
    def test_phases_in_order_then_cleared(self):
        phases = []
        proc, port = _spawn_fake("session")
        try:
            with patch.dict(os.environ, self.rclone_env()):
                cfg = self.make_cfg(on_phase=phases.append)
                daemon.run_job_cycle(port, cfg)
        finally:
            _kill(proc)
        self.assertEqual(phases, ["reading", "uploading", "emptying", None])

    def test_glyph_and_words(self):
        from puckd import menubar
        self.assertEqual(menubar.glyph_state(False, None, False), "dormant")
        self.assertEqual(menubar.glyph_state(False, None, True), "idle")
        self.assertEqual(menubar.glyph_state(False, "uploading", True), "working")
        self.assertEqual(menubar.glyph_state(True, "uploading", True), "attention")
        self.assertEqual(menubar.format_puck_line(None, False, attached=False), "No puck")
        self.assertEqual(menubar.format_puck_line(80, True, phase="emptying"), "Emptying the puck\u2026")
        for state, path in menubar.ICON_FILES.items():
            self.assertTrue(path.is_file(), f"{state} glyph missing: {path}")


class TheLogCanNeverKillTheLoop(_DaemonTestBase):
    def test_an_em_dash_is_written_as_utf8(self):
        cfg = self.make_cfg()
        daemon._log(cfg, "device went silent during 'info' \u2014 is the right firmware flashed?")
        text = (self.home / daemon.LOG_FILENAME).read_bytes().decode("utf-8")
        self.assertIn("\u2014", text)

    def test_an_unwritable_log_is_swallowed(self):
        cfg = self.make_cfg()
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / daemon.LOG_FILENAME).mkdir()          # a directory where the file should be
        daemon._log(cfg, "must not raise")                 # OSError inside -> swallowed

    def test_the_loop_survives_a_tick_whose_error_message_has_an_em_dash(self):
        calls = []
        def find_port():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("port scan \u2014 exploded")
            return None
        cfg = self.make_cfg()
        daemon.run_forever(cfg, find_port=find_port, max_iterations=3)
        self.assertEqual(len(calls), 3, "the loop kept ticking after the failure")


class ClickingTheIconOpensTheWindow(_DaemonTestBase):
    def test_flag_is_consumed_once(self):
        cfg = self.make_cfg()
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / daemon.OPEN_SETUP_FLAG).write_text("open")
        self.assertTrue(daemon.consume_open_flag(cfg))
        self.assertFalse(daemon.consume_open_flag(cfg), "consumed")
        self.assertFalse((self.home / daemon.OPEN_SETUP_FLAG).exists())


class TheLogGoesToDrive(_DaemonTestBase):
    def test_after_a_job_the_log_and_status_land_in_the_shared_folder(self):
        proc, port = _spawn_fake("session")
        try:
            with patch.dict(os.environ, self.rclone_env()):
                cfg = self.make_cfg()
                daemon.run_forever(cfg, find_port=lambda: port, max_iterations=1)
        finally:
            _kill(proc)
        logdir = self.tmp / "drive" / daemon.LOG_DIR
        self.assertTrue((logdir / daemon.LOG_FILENAME).is_file(), "daemon.log on Drive")
        status = json.loads((logdir / daemon.STATUS_FILENAME).read_text())
        self.assertEqual(status["last_job"]["jumps"], 4)
        self.assertTrue(status["last_job"]["cleared"])
        self.assertIn("batt_pct", status["puck"])
        self.assertEqual(status["last_ride_jumps"], 4)


class GarminNagsOnlyTheSignedIn(_DaemonTestBase):
    def test_never_signed_in_is_never_asked(self):
        cfg = self.make_cfg(garmin_module=_FakeGarmin(signed_in=False, ever=False))
        daemon._run_garmin(cfg)
        self.assertEqual(self.recorder.calls, [])

    def test_expired_is_asked_once(self):
        cfg = self.make_cfg(garmin_module=_FakeGarmin(signed_in=False, ever=True))
        daemon._run_garmin(cfg)
        daemon._run_garmin(cfg)
        self.assertEqual(self.recorder.calls,
                         [("Needs you: sign in to Garmin again", "Open Set up in the menu bar.")])


class SiteUrlDefaultsToTheLivePage(unittest.TestCase):
    def test_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(daemon.SITE_URL_ENV, None)
            cfg = daemon.build_config(home_dir=Path("/tmp/x"))
        self.assertEqual(cfg.site_url, "https://joshcrow.github.io/Jump-height")

    def test_env_override(self):
        with patch.dict(os.environ, {daemon.SITE_URL_ENV: "http://127.0.0.1:9"}):
            cfg = daemon.build_config(home_dir=Path("/tmp/x"))
        self.assertEqual(cfg.site_url, "http://127.0.0.1:9")


class _FakeSelfupdate:
    """A stand-in for cfg.selfupdate_module. The daemon needs exactly four
    calls (current_version, latest_manifest, needs_update, apply) plus
    installed_bundle_path -- the real module's own download/sha256/rename
    sequence is tools/tests/test_puckd_selfupdate.py's job, not this
    file's to repeat."""

    def __init__(self, latest=None, ok=True):
        self.latest = latest
        self.ok = ok
        self.manifest_calls = []
        self.apply_calls = []

    def current_version(self):
        return "1.0.0"

    def installed_bundle_path(self):
        return Path("/Applications/JumpHeight Sync.app")

    def latest_manifest(self, site_url):
        self.manifest_calls.append(site_url)
        return {"version": self.latest, "url": "https://gh/x.zip",
                "sha256": "a" * 64} if self.latest else None

    def needs_update(self, current, manifest):
        return bool(manifest) and manifest.get("version", "") > current

    def apply(self, manifest, **kwargs):
        self.apply_calls.append((manifest, kwargs))
        return daemon.selfupdate.UpdateResult(self.ok, "restart", None,
                                              manifest.get("version"))


class _StubReport:
    """What a patched run_job_cycle hands back -- enough for run_forever's
    own bookkeeping, nothing more."""

    port = "/dev/cu.usbmodemFAKE"
    pulled = False
    verified = None
    jumps = 0
    reasons = []
    uploaded = False
    cleared = False
    flashed = False
    needs_you = None
    bundle_path = None
    src = None
    empty = True
    stats = {}


class TheAppUpdatesItselfOnlyWhenIdle(_DaemonTestBase):
    """7. The app replaces itself silently -- but `launchctl kickstart -k`
    kills this process, so the check runs ONLY on a tick with no puck
    attached and no job running. A restart mid-sync is G3 broken from the
    app's own side, and the rider is 300 miles away when it happens."""

    def _drive(self, su, ports, *, interval=6 * 3600.0, ticks=None):
        """Run the loop over a scripted list of find_port() answers, with a
        clock that advances one POLL_INTERVAL_S per tick unless a port
        answer is the string "+interval" (which jumps the clock instead)."""
        clock = [1000.0]
        answers = list(ports)
        seen = []

        def now():
            return clock[0]

        def find_port():
            got = answers[seen.__len__()] if len(seen) < len(answers) else None
            seen.append(got)
            if got == "+interval":
                clock[0] += interval + 1.0
                return None
            clock[0] += daemon.POLL_INTERVAL_S
            return got

        cfg = self.make_cfg(selfupdate_module=su, selfupdate_interval_s=interval,
                            now=now, site_url="https://site.invalid")
        with patch.object(daemon, "run_job_cycle", lambda port, c: _StubReport()), \
             patch.object(daemon, "publish_log", lambda *a, **k: True), \
             patch.object(daemon, "_run_garmin", lambda c: None):
            daemon.run_forever(cfg, find_port=find_port,
                               max_iterations=ticks if ticks is not None else len(answers))
        return cfg

    def test_the_check_runs_on_the_very_first_idle_tick(self):
        su = _FakeSelfupdate()
        self._drive(su, [None])
        self.assertEqual(su.manifest_calls, ["https://site.invalid"],
                         "a build that shipped broken must not wait 6 h to fix itself")

    def test_it_does_not_run_again_before_the_interval(self):
        su = _FakeSelfupdate()
        self._drive(su, [None, None, None, None])
        self.assertEqual(len(su.manifest_calls), 1)

    def test_it_runs_again_once_the_interval_has_passed(self):
        su = _FakeSelfupdate()
        self._drive(su, [None, "+interval"])
        self.assertEqual(len(su.manifest_calls), 2)

    def test_a_puck_on_the_bench_defers_the_check_entirely(self):
        su = _FakeSelfupdate()
        # Tick 1: a puck arrives (a job runs). Tick 2: still attached.
        self._drive(su, ["/dev/cu.usbmodemFAKE", "/dev/cu.usbmodemFAKE"])
        self.assertEqual(su.manifest_calls, [],
                         "never restart mid-sync: the check does not even fetch")
        self.assertEqual(su.apply_calls, [])

    def test_the_deferred_check_happens_as_soon_as_the_puck_is_unplugged(self):
        su = _FakeSelfupdate()
        self._drive(su, ["/dev/cu.usbmodemFAKE", None])
        self.assertEqual(len(su.manifest_calls), 1)

    def test_a_newer_version_is_applied_and_logged(self):
        su = _FakeSelfupdate(latest="1.0.1")
        cfg = self._drive(su, [None])
        self.assertEqual(len(su.apply_calls), 1)
        _manifest, kwargs = su.apply_calls[0]
        self.assertEqual(kwargs["bundle_path"], Path("/Applications/JumpHeight Sync.app"))
        self.assertEqual(kwargs["download_dir"],
                         Path(cfg.home_dir) / daemon.UPDATES_CACHE_DIRNAME)
        log = (Path(cfg.home_dir) / daemon.LOG_FILENAME).read_text()
        self.assertIn("app update available: 1.0.0 -> 1.0.1", log)
        self.assertIn("app updated to 1.0.1", log)

    def test_the_same_version_applies_nothing(self):
        su = _FakeSelfupdate(latest="1.0.0")
        self._drive(su, [None])
        self.assertEqual(su.apply_calls, [])

    def test_a_selfupdate_module_that_explodes_never_kills_the_loop(self):
        class Boom:
            def current_version(self):
                raise RuntimeError("plist on fire")
        cfg = self._drive(Boom(), [None, None])
        log = (Path(cfg.home_dir) / daemon.LOG_FILENAME).read_text()
        self.assertIn("selfupdate raised", log)

    def test_status_json_carries_the_app_version(self):
        cfg = self.make_cfg(selfupdate_module=_FakeSelfupdate())
        Path(cfg.home_dir).mkdir(parents=True, exist_ok=True)
        with patch.dict(os.environ, self.rclone_env()):
            daemon.publish_log(cfg)
        status = json.loads((Path(cfg.home_dir) / daemon.STATUS_FILENAME).read_text())
        self.assertEqual(status["app_version"], "1.0.0")


if __name__ == "__main__":
    unittest.main()
