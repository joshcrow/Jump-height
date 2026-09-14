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

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
