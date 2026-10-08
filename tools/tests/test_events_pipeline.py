"""Six-axis events through the Mac side: pull, bundle, daemon, ingest
(firmware batch 2, spec 2026-10-07 section 8.1 T-H1, T-H2, T-H3, T-H5).

  T-H1  serial_job.run_job() pulls `events` after the unchanged 1.0.7
        sequence; the bundle carries events.bin + evstat.txt + the manifest's
        events_* keys; a pre-batch-2 puck gives events_format null; an
        incomplete transfer is events_verified False while the TRACE stays
        verified; a page damaged after its CRC passes transport and counts
        as one damaged page.
  T-H2  The daemon runs `evclear` only after a confirmed upload AND a
        verified events pull, confirms it with evstat bytes=0, refuses to
        flash while events remain, pulls an otherwise-empty puck that still
        holds events, and flash.py stops on the device's own `ERR uf2_*`.
  T-H3  `jump ingest` decodes events.bin into events/ CSVs (SI = raw x the
        recorded constant, exactly), t_utc only for the bundle's own boot,
        a missing events.bin is a loud failure line, and events problems
        never refuse the trace.
  T-H5  The UNCHANGED 1.0.7 run_job + clear_puck (a frozen copy:
        tools/tests/fixtures/serial_job_1_0_7.py) against a batch-2 puck:
        verified, cleared, and the events still there afterwards.

All against tools/fake_device.py over a real pty, whose events are real
jhev1 pages made by sim/event_policy.py (the firmware's capture, mirrored).

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "sim"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from puckd import daemon, flash, serial_job  # noqa: E402
import event_codec  # noqa: E402
from test_puckd_daemon import _DaemonTestBase, _FakeFlashModule  # noqa: E402

JUMP = str(REPO / "tools" / "jump")


def _spawn_fake(scenario="session", extra=None):
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


def _run_job(extra, scenario="session"):
    proc, port = _spawn_fake(scenario, extra)
    spool = tempfile.mkdtemp()
    try:
        return serial_job.run_job(port, spool), spool
    finally:
        _kill(proc)


def _bundle(result) -> dict:
    with zipfile.ZipFile(result.bundle_path) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


# ------------------------------------------------------------------ T-H1

class TestRunJobPullsEvents(unittest.TestCase):
    def test_events_ride_in_the_bundle_with_their_manifest_keys(self):
        result, _ = _run_job(["--events", "2"])
        self.assertTrue(result.verified, result.reasons)
        self.assertEqual(result.events_format, "jhev1")
        self.assertTrue(result.events_verified, result.events_reasons)
        self.assertEqual(result.boot_id, "fa4eb007")
        files = _bundle(result)
        self.assertIn("events.bin", files)
        self.assertTrue(files["evstat.txt"].startswith(b"EVSTAT "))
        m = json.loads(files["manifest.json"])
        self.assertEqual(m["bundle_version"], 1, "the keys are additive")
        self.assertEqual(m["events_format"], "jhev1")
        self.assertEqual(m["events_bytes"], len(files["events.bin"]))
        self.assertEqual(m["events_region_bytes"], event_codec.REGION_BYTES)
        self.assertTrue(m["events_verified"])
        self.assertEqual(m["events_reasons"], [])
        self.assertEqual(m["events_damaged_pages"], 0)
        self.assertIs(m["events_cleared"], False)
        self.assertEqual(m["boot_id"], "fa4eb007")
        d = event_codec.decode_region(files["events.bin"])
        self.assertEqual(len(d.events), 2)
        self.assertTrue(all(e.complete for e in d.events))
        # device.log carries the chatter, never the base64 body.
        log = files["device.log"].decode()
        self.assertIn("# events bytes=", log)
        self.assertNotIn("FILE events.bin BEGIN\nAA", log)

    def test_an_incomplete_events_transfer_does_not_unverify_the_trace(self):
        result, _ = _run_job(["--events", "2", "--events-incomplete"])
        self.assertTrue(result.verified, "the trace verdict must not move")
        self.assertEqual(result.events_format, "jhev1")
        self.assertFalse(result.events_verified)
        self.assertTrue(any("incomplete" in r.lower() for r in result.events_reasons),
                        result.events_reasons)
        m = json.loads(_bundle(result)["manifest.json"])
        self.assertFalse(m["events_verified"])
        self.assertTrue(m["verified"])

    def test_a_page_damaged_after_its_crc_passes_transport_and_is_counted(self):
        result, _ = _run_job(["--events", "2", "--events-corrupt-page", "3"])
        self.assertTrue(result.events_verified, result.events_reasons)
        m = json.loads(_bundle(result)["manifest.json"])
        self.assertEqual(m["events_damaged_pages"], 1)

    def test_an_events_err_is_an_error_not_a_failed_ride(self):
        result, _ = _run_job(["--events", "2", "--events-error", "storage_down"])
        self.assertTrue(result.verified)
        self.assertEqual(result.events_format, "error")
        self.assertFalse(result.events_verified)
        self.assertIn("ERR events storage_down", " ".join(result.events_reasons))
        self.assertNotIn("events.bin", _bundle(result))

    def test_old_firmware_events_are_null_not_an_error(self):
        result, _ = _run_job(["--no-events"])
        self.assertTrue(result.verified)
        self.assertIsNone(result.events_format)
        self.assertEqual(result.events_reasons, [])


# ------------------------------------------------------------------ T-H5

def _load_serial_job_1_0_7():
    path = Path(__file__).resolve().parent / "fixtures" / "serial_job_1_0_7.py"
    spec = importlib.util.spec_from_file_location("serial_job_1_0_7", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod   # dataclasses resolve annotations through it
    spec.loader.exec_module(mod)
    mod.JUMP = JUMP          # the frozen file located tools/jump from its own path
    return mod


class TestApp107AgainstABatch2Puck(unittest.TestCase):
    def test_pull_verify_and_clear_succeed_and_the_events_survive(self):
        old = _load_serial_job_1_0_7()
        self.assertFalse(hasattr(old, "read_evstat"), "this must be the 1.0.7 module")
        proc, port = _spawn_fake("session", ["--events", "2"])
        spool = tempfile.mkdtemp()
        try:
            result = old.run_job(port, spool)
            self.assertTrue(result.verified, result.reasons)
            self.assertEqual(result.jumps, 4)
            with zipfile.ZipFile(result.bundle_path) as zf:
                self.assertEqual(set(zf.namelist()), {"manifest.json", "jumps.csv",
                                                      "trace.bin", "notes.txt", "device.log"})
            cleared = old.clear_puck(port)
            self.assertTrue(cleared.ok, cleared)
            self.assertEqual((cleared.stored_jumps_after, cleared.trace_bytes_after), (0, 0))
            ev = serial_job.read_evstat(port)
            self.assertTrue(ev.supported)
            self.assertGreater(ev.bytes, 0, "1.0.7's clear must not have erased the events")
        finally:
            _kill(proc)
            shutil.rmtree(spool, ignore_errors=True)


# ------------------------------------------------------------------ T-H2

class TestDaemonEvents(_DaemonTestBase):
    def _log(self) -> str:
        f = self.home / daemon.LOG_FILENAME
        return f.read_text() if f.exists() else ""

    def test_a_verified_sync_clears_events_and_confirms_it(self):
        proc, port = _spawn_fake("session", ["--events", "2"])
        try:
            with patch.dict(os.environ, self.rclone_env()):
                report = daemon.run_job_cycle(port, self.make_cfg())
                after = serial_job.read_evstat(port)
        finally:
            _kill(proc)
        self.assertTrue(report.cleared, report)
        self.assertIs(report.events_cleared, True)
        self.assertEqual(after.bytes, 0)
        drive = list((self.tmp / "drive").rglob("*.zip"))
        self.assertEqual(len(drive), 1)
        with zipfile.ZipFile(drive[0]) as zf:
            self.assertIn("events.bin", zf.namelist())

    def test_unverified_events_stay_on_the_puck_and_block_the_update(self):
        proc, port = _spawn_fake("session", ["--events", "2", "--events-incomplete"])
        fake = _FakeFlashModule(manifest={"src": "bbbb", "file": "x.uf2"}, needs=True)
        try:
            with patch.dict(os.environ, self.rclone_env()):
                cfg = self.make_cfg(site_url="http://example.invalid", flash_module=fake)
                report = daemon.run_job_cycle(port, cfg)
                after = serial_job.read_evstat(port)
        finally:
            _kill(proc)
        self.assertTrue(report.cleared, "the ride itself was verified and cleared")
        self.assertIs(report.events_cleared, False)
        self.assertGreater(after.bytes, 0, "unverified events must stay for the next sync")
        self.assertEqual(fake.latest_manifest_calls, [], "never flash while events remain")
        self.assertFalse(report.flashed)
        self.assertIn("events NOT cleared", self._log())
        self.assertEqual(self.recorder.titled("Needs you"), [],
                         "an events problem is Josh's log line, not Nick's notification")

    def test_an_empty_ride_with_events_on_the_puck_is_still_pulled(self):
        proc, port = _spawn_fake("ok", ["--events", "1"])
        try:
            with patch.dict(os.environ, self.rclone_env()):
                report = daemon.run_job_cycle(port, self.make_cfg())
                after = serial_job.read_evstat(port)
        finally:
            _kill(proc)
        self.assertFalse(report.empty)
        self.assertTrue(report.pulled)
        self.assertIs(report.events_cleared, True)
        self.assertEqual(after.bytes, 0)
        self.assertIn("holds", self._log())

    def test_an_empty_puck_with_no_events_is_still_silent_and_checks_for_updates(self):
        proc, port = _spawn_fake("ok")
        fake = _FakeFlashModule(manifest={"src": "aaaa", "file": "x.uf2"}, needs=False)
        try:
            report = daemon.run_job_cycle(
                port, self.make_cfg(site_url="http://example.invalid", flash_module=fake))
        finally:
            _kill(proc)
        self.assertTrue(report.empty)
        self.assertEqual(fake.latest_manifest_calls, ["http://example.invalid"])
        self.assertEqual(self.recorder.calls, [])

    def test_an_unanswered_evstat_is_unknown_and_blocks_the_update(self):
        cfg = self.make_cfg()
        ev = serial_job.EvStat(True, None, None, None, "the puck didn't answer 'evstat'")
        self.assertFalse(daemon._events_allow_flash(cfg, ev))
        self.assertIn("could not read the puck's event region", self._log())
        self.assertTrue(daemon._events_allow_flash(cfg, serial_job.EvStat(False, None, None, None, None)))
        self.assertTrue(daemon._events_allow_flash(cfg, serial_job.EvStat(True, 0, "0", "", None)))

    def test_store_down_reads_as_unknown_not_zero(self):
        st = serial_job._parse_evstat(["EVSTAT bytes=0 region_bytes=0 pages=0 disabled=store_down"])
        self.assertIsNone(st.bytes)


class TestFlashStopsOnTheDevicesOwnUf2Err(unittest.TestCase):
    def test_err_uf2_arm_failed_ends_the_flash_at_send_uf2(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        uf2 = tmp / "fw.uf2"
        uf2.write_bytes(b"\0" * 512)
        import hashlib
        manifest = {"src": "bbbbbbbb", "file": "fw.uf2",
                    "sha256": hashlib.sha256(uf2.read_bytes()).hexdigest()}
        logged = []
        volume_checks = []

        class Dev:
            def command(self, cmd, timeout=20.0):
                assert cmd == "uf2"
                return ["ERR uf2_arm_failed sd=1 rc=0/8/0 val=0x00"]

            def close(self):
                pass

        result = flash.flash("/dev/cu.usbmodem1", uf2, manifest,
                             device_factory=lambda p: Dev(), log=logged.append,
                             volume_exists=lambda: (volume_checks.append(1), False)[1],
                             list_disks=lambda: "", sleep=lambda s: None, now=lambda: 0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_SEND_UF2)
        self.assertEqual(result.error, "ERR uf2_arm_failed sd=1 rc=0/8/0 val=0x00")
        self.assertIn("uf2 reply: ERR uf2_arm_failed sd=1 rc=0/8/0 val=0x00", logged)
        self.assertEqual(volume_checks, [], "it must not wait for a drive that cannot appear")


# ------------------------------------------------------------------ T-H3

def _ingest(bundle: Path, out: Path, *extra):
    return subprocess.run([sys.executable, JUMP, "ingest", str(bundle), "--out", str(out)]
                          + list(extra), capture_output=True, text=True, timeout=180,
                          cwd=str(REPO), stdin=subprocess.DEVNULL)


def _rezip(src: Path, dst: Path, *, drop=(), manifest_update=None):
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w") as zo:
        for n in zi.namelist():
            if n in drop:
                continue
            data = zi.read(n)
            if n == "manifest.json" and manifest_update:
                m = json.loads(data)
                m.update(manifest_update)
                data = json.dumps(m).encode()
            zo.writestr(n, data)


class TestIngestDecodesEvents(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result, cls.spool = _run_job(["--events", "2"])

    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)

    def _session(self) -> Path:
        sess = [p for p in self.out.iterdir() if p.is_dir()]
        self.assertEqual(len(sess), 1)
        return sess[0]

    def test_events_become_csvs_in_si_units_from_the_raw_registers(self):
        r = _ingest(self.result.bundle_path, self.out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sess = self._session()
        sj = json.loads((sess / "session.json").read_text())
        self.assertTrue(sj["verified"], "the trace verdict is unchanged")
        self.assertTrue(sj["events_verification"]["verified"], sj["events_verification"])
        self.assertEqual(sj["events_verification"]["events"], 2)
        self.assertFalse((sess / "EVENTS-VERIFICATION-FAILED.txt").exists())
        self.assertTrue((sess / "events.bin").exists())
        idx = (sess / "events" / "index.csv").read_text().splitlines()
        self.assertEqual(len(idx), 3)
        self.assertTrue(idx[0].startswith("boot_id,event_id,t_start_s,t_trigger_s"))
        rows = (sess / "events" / "fa4eb007-1.csv").read_text().splitlines()
        hdr = rows[0].split(",")
        d = dict(zip(hdr, rows[1001].split(",")))
        lsb = event_codec.f32(event_codec.ACCEL_G_PER_LSB)
        self.assertEqual(float(d["az_mps2"]), round(int(d["az_raw"]) * lsb * 9.80665, 6))
        self.assertEqual(float(d["gx_rads"]),
                         round(int(d["gx_raw"]) * event_codec.f32(0.070) * math.pi / 180.0, 6))
        trig = (sess / "events" / "triggers.csv").read_text().splitlines()
        self.assertEqual(len(trig), 3)
        meta = json.loads((sess / "events" / "meta.json").read_text())
        self.assertIn("976.5625", meta["units"]["time_quantum"])

    def test_t_utc_only_for_the_bundles_own_boot(self):
        src = Path(self.result.bundle_path)
        same = self.out / "same.zip"
        _rezip(src, same, manifest_update={"trace_epoch_utc": "2026-10-07T12:00:00.000Z"})
        r = _ingest(same, self.out / "a")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = next((self.out / "a").iterdir()).joinpath("events", "fa4eb007-1.csv").read_text()
        first = dict(zip(rows.splitlines()[0].split(","), rows.splitlines()[1].split(",")))
        self.assertTrue(first["t_utc"].startswith("2026-10-07T12:00:0"), first)
        other = self.out / "other.zip"
        _rezip(src, other, manifest_update={"trace_epoch_utc": "2026-10-07T12:00:00.000Z",
                                            "boot_id": "00000001"})
        r = _ingest(other, self.out / "b")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rows = next((self.out / "b").iterdir()).joinpath("events", "fa4eb007-1.csv").read_text()
        first = dict(zip(rows.splitlines()[0].split(","), rows.splitlines()[1].split(",")))
        self.assertEqual(first["t_utc"], "", "another boot's events have no wall clock here")
        idx = next((self.out / "b").iterdir()).joinpath("events", "index.csv").read_text()
        row = dict(zip(idx.splitlines()[0].split(","), idx.splitlines()[1].split(",")))
        self.assertEqual(row["epoch_known"], "0")

    def test_a_missing_events_bin_is_loud_and_never_refuses_the_trace(self):
        bad = self.out / "missing.zip"
        _rezip(Path(self.result.bundle_path), bad, drop=("events.bin",))
        r = _ingest(bad, self.out / "s")
        self.assertEqual(r.returncode, 0, "events problems never refuse the trace ingest")
        self.assertIn("events.bin is not in the bundle", r.stdout)
        sess = next((self.out / "s").iterdir())
        self.assertTrue((sess / "EVENTS-VERIFICATION-FAILED.txt").exists())
        self.assertFalse((sess / "VERIFICATION-FAILED.txt").exists())
        sj = json.loads((sess / "session.json").read_text())
        self.assertTrue(sj["verified"])
        self.assertFalse(sj["events_verification"]["verified"])

    def test_a_crc_mismatch_is_a_failure_line(self):
        bad = self.out / "crc.zip"
        _rezip(Path(self.result.bundle_path), bad, manifest_update={"events_crc32": "deadbeef"})
        r = _ingest(bad, self.out / "c")
        self.assertEqual(r.returncode, 0)
        self.assertIn("does not match the manifest's deadbeef", r.stdout)

    def test_an_old_bundle_without_events_ingests_exactly_as_before(self):
        old = self.out / "old.zip"
        with zipfile.ZipFile(self.result.bundle_path) as zi:
            m = json.loads(zi.read("manifest.json"))
        for k in list(m):
            if k.startswith("events_") or k == "boot_id":
                del m[k]
        with zipfile.ZipFile(self.result.bundle_path) as zi, zipfile.ZipFile(old, "w") as zo:
            for n in zi.namelist():
                if n in ("events.bin", "evstat.txt"):
                    continue
                zo.writestr(n, json.dumps(m) if n == "manifest.json" else zi.read(n))
        r = _ingest(old, self.out / "o")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sess = next((self.out / "o").iterdir())
        sj = json.loads((sess / "session.json").read_text())
        self.assertNotIn("events_verification", sj)
        self.assertFalse((sess / "events").exists())


class TestJumpEventsBenchCommand(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, JUMP, "events"] + list(args),
                              capture_output=True, text=True, timeout=180, cwd=str(REPO),
                              stdin=subprocess.DEVNULL)

    def test_requires_a_name(self):
        r = self.run_cli("--fake")
        self.assertEqual(r.returncode, 2)
        self.assertIn("--name", r.stdout)

    def test_refuses_a_puck_with_another_name(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        r = self.run_cli("--fake", "--name", "JumpHeight-8673",
                         "--fake-puck-name", "JumpHeight-E2C4", "--out", out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("refusing to read it", r.stdout)
        self.assertEqual(list(Path(out).iterdir()), [])

    def test_pulls_and_decodes_a_named_puck(self):
        out = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, out, ignore_errors=True)
        r = self.run_cli("--fake", "--name", "JumpHeight-8673", "--out", out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        sess = next(Path(out).iterdir())
        self.assertTrue(sess.name.endswith("-8673-events"))
        self.assertEqual(len((sess / "events" / "index.csv").read_text().splitlines()), 3)


if __name__ == "__main__":
    unittest.main()
