"""Restoration safety tests. All device/process boundaries are mocked."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

import restore_product as target


def port(pid=target.APP_PID, *, uid=target.UID, vid=target.VID, device="/dev/pinned"):
    return SimpleNamespace(serial_number=uid, vid=vid, pid=pid, device=device)


def research_info(**changes):
    info = dict(uid=target.UID, name=target.NAME, recorder="jh6-usb-research",
                build="0123456789abcdef", odr_hz=208, accel_g_per_lsb=0.000488,
                gyro_dps_per_lsb=0.070, timestamp_tick_us=25,
                timestamp_mode="fifo", timestamp_bits=24)
    info.update(changes)
    return [b"JH6 INFO " + json.dumps(info).encode() + b"\n", b"OK info\n"]


def product_info(source=target.SOURCE, name=target.NAME):
    return [f"INFO fw=0.4.3 src={source}\n".encode(),
            f"# name={name}\n".encode(), b"OK info\n"]


class Connection:
    def __init__(self, runtime, lines):
        self.runtime, self.lines = runtime, list(lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def write(self, data):
        self.runtime.writes.append(data)
        return len(data)

    def flush(self):
        pass

    def readline(self, size):
        self.runtime.now += 0.11
        return self.lines.pop(0) if self.lines else b""


class FakeRuntime:
    def __init__(self, inventories, replies=(), output="Device programmed.\n", returncode=0):
        self.inventories = list(inventories)
        self.replies = list(replies)
        self.output, self.returncode = output, returncode
        self.now = 0.0
        self.port_calls = 0
        self.connects, self.writes, self.uploads = [], [], []
        self.timeout = False

    def ports(self):
        self.port_calls += 1
        if len(self.inventories) > 1:
            return self.inventories.pop(0)
        return self.inventories[0]

    def connect(self, path):
        self.connects.append(path)
        return Connection(self, self.replies.pop(0))

    def uploader(self):
        return "/mock/adafruit-nrfutil"

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def upload(self, argv):
        self.uploads.append(argv)
        snapshot = Path(argv[argv.index("--package") + 1]).read_bytes()
        assert hashlib.sha256(snapshot).hexdigest() == target.PACKAGE_SHA256
        assert "--touch" not in argv
        assert argv[argv.index("-b") + 1] == "115200"
        if self.timeout:
            raise subprocess.TimeoutExpired(argv, 50)
        return SimpleNamespace(returncode=self.returncode, stdout=self.output)


def app_runtime(**kwargs):
    app, boot = port(), port(target.BOOT_PID)
    return FakeRuntime([[app], [app], [boot], [boot], [app], [app]],
                       [research_info() + [b"OK dfu\n"], product_info()], **kwargs)


class RestoreTests(unittest.TestCase):
    def test_serial_factory_obtains_kernel_exclusivity_before_returning(self):
        connection = Mock()
        serial = SimpleNamespace(Serial=Mock(return_value=connection))
        with patch.dict(sys.modules, {"serial": serial}), \
                patch.object(target, "secure_serial_port") as secure:
            self.assertIs(target.Runtime().connect("/dev/mock"), connection)
            secure.assert_called_once_with(connection)
            serial.Serial.assert_called_once_with("/dev/mock", 115200, timeout=0.2,
                                                  write_timeout=2, exclusive=True)
        connection.write.assert_not_called()

    def test_serial_factory_closes_and_fails_if_kernel_lock_fails(self):
        connection = Mock()
        serial = SimpleNamespace(Serial=Mock(return_value=connection))
        with patch.dict(sys.modules, {"serial": serial}), \
                patch.object(target, "secure_serial_port", side_effect=OSError("busy")):
            with self.assertRaisesRegex(target.RestoreError, "kernel serial exclusivity"):
                target.Runtime().connect("/dev/mock")
        connection.close.assert_called_once()
        connection.write.assert_not_called()

    def test_real_pinned_package_is_application_only(self):
        manifest = target.validate_package(target.PACKAGE.read_bytes())["manifest"]
        self.assertEqual(set(manifest), {"application", "dfu_version"})

    def test_default_preflight_only_sends_info(self):
        runtime = FakeRuntime([[port()]], [research_info()])
        result = target.restore(runtime=runtime)
        self.assertEqual(runtime.writes, [b"info\n"])
        self.assertEqual(runtime.uploads, [])
        self.assertFalse(result["restored"])

    def test_wrong_uid_never_opened_even_when_og_is_present(self):
        runtime = FakeRuntime([[port(uid="1111111111111111", device="/dev/OG")]])
        with self.assertRaisesRegex(target.RestoreError, "not found"):
            target.restore(perform=True, runtime=runtime)
        self.assertEqual(runtime.connects + runtime.uploads, [])

    def test_wrong_vid_or_pid_rejected_before_open(self):
        for candidate in (port(vid=0x1234), port(pid=0x1234)):
            with self.subTest(candidate=candidate):
                runtime = FakeRuntime([[candidate]])
                with self.assertRaisesRegex(target.RestoreError, "VID/PID"):
                    target.restore(perform=True, runtime=runtime)
                self.assertEqual(runtime.connects + runtime.uploads, [])

    def test_ambiguous_identity_rejected(self):
        runtime = FakeRuntime([[port(), port(device="/dev/other")]])
        with self.assertRaisesRegex(target.RestoreError, "Ambiguous"):
            target.restore(perform=True, runtime=runtime)
        self.assertEqual(runtime.connects, [])

    def test_bad_package_rejected_before_any_device_lookup(self):
        runtime = FakeRuntime([[port()]])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"bad.zip"
            path.write_bytes(b"not the pinned package")
            with patch.object(target, "PACKAGE", path):
                with self.assertRaisesRegex(target.RestoreError, "SHA256"):
                    target.restore(perform=True, runtime=runtime)
        self.assertEqual(runtime.port_calls, 0)

    def test_manifest_gate_rejects_bootloader_even_if_hash_gate_matches(self):
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as archive:
            archive.writestr("firmware.bin", b"image")
            archive.writestr("firmware.dat", b"init")
            archive.writestr("manifest.json", json.dumps({"manifest": {
                "application": {"bin_file": "firmware.bin", "dat_file": "firmware.dat"},
                "bootloader": {}, "dfu_version": 0.5}}))
        data = raw.getvalue()
        with patch.object(target, "PACKAGE_SHA256", hashlib.sha256(data).hexdigest()):
            with self.assertRaisesRegex(target.RestoreError, "application-only"):
                target.validate_package(data)

    def test_research_info_identity_and_build_checked_before_dfu(self):
        for change in ({"uid": "1111111111111111"}, {"name": "JumpHeight-E2C4"},
                       {"build": "unknown"}, {"recorder": "different"}):
            with self.subTest(change=change):
                runtime = FakeRuntime([[port()]], [research_info(**change)])
                with self.assertRaises((ValueError, target.RestoreError)):
                    target.restore(perform=True, runtime=runtime)
                self.assertEqual(runtime.writes, [b"info\n"])
                self.assertEqual(runtime.uploads, [])

    def test_bad_ack_never_uploads_or_retries(self):
        runtime = FakeRuntime([[port()]], [research_info() + [b"OK dfu extra\n"]])
        with self.assertRaisesRegex(target.RestoreError, "acknowledgment"):
            target.restore(perform=True, runtime=runtime)
        self.assertEqual(runtime.writes, [b"info\n", b"dfu\n"])
        self.assertEqual(runtime.uploads, [])

    def test_ack_without_bootloader_stops_with_manual_guidance(self):
        runtime = FakeRuntime([[port()]], [research_info() + [b"OK dfu\n"]])
        with self.assertRaisesRegex(target.RestoreError, "double-tap RESET"):
            target.restore(perform=True, runtime=runtime)
        self.assertEqual(runtime.writes.count(b"dfu\n"), 1)
        self.assertEqual(runtime.uploads, [])

    def test_success_requires_fresh_product_info(self):
        runtime = app_runtime()
        result = target.restore(perform=True, runtime=runtime)
        self.assertTrue(result["restored"])
        self.assertEqual(runtime.writes, [b"info\n", b"dfu\n", b"info\n"])
        self.assertEqual(len(runtime.uploads), 1)
        self.assertNotIn("--touch", runtime.uploads[0])

    def test_bootloader_preflight_does_not_open_serial(self):
        runtime = FakeRuntime([[port(target.BOOT_PID)]])
        self.assertFalse(target.restore(runtime=runtime)["restored"])
        self.assertEqual(runtime.connects + runtime.uploads, [])

    def test_already_bootloader_restore_does_not_send_dfu(self):
        app, boot = port(), port(target.BOOT_PID)
        runtime = FakeRuntime([[boot], [boot], [app], [app]], [product_info()])
        self.assertTrue(target.restore(perform=True, runtime=runtime)["restored"])
        self.assertEqual(runtime.writes, [b"info\n"])
        self.assertEqual(len(runtime.uploads), 1)

    def test_zero_exit_without_exact_marker_never_retries(self):
        for output in ("[SUCCESS]\n", "NOT Device programmed.\n"):
            with self.subTest(output=output):
                runtime = app_runtime(output=output)
                with self.assertRaisesRegex(target.RestoreError, "literal"):
                    target.restore(perform=True, runtime=runtime)
                self.assertEqual(len(runtime.uploads), 1)
                self.assertEqual(len(runtime.connects), 1)

    def test_upload_timeout_never_retries(self):
        runtime = app_runtime()
        runtime.timeout = True
        with self.assertRaisesRegex(target.RestoreError, "50 seconds"):
            target.restore(perform=True, runtime=runtime)
        self.assertEqual(len(runtime.uploads), 1)

    def test_wrong_postflash_source_or_name_is_not_success(self):
        for lines in (product_info(source="deadbeef"), product_info(name="JumpHeight-E2C4")):
            runtime = app_runtime()
            runtime.replies[-1] = lines
            with self.assertRaisesRegex(target.RestoreError, "does not prove"):
                target.restore(perform=True, runtime=runtime)
            self.assertEqual(len(runtime.uploads), 1)

    def test_usb_identity_change_before_upload_never_uploads(self):
        app, boot = port(), port(target.BOOT_PID)
        runtime = FakeRuntime([[app], [app], [boot], [port(uid="1111111111111111")]],
                              [research_info() + [b"OK dfu\n"]])
        with self.assertRaisesRegex(target.RestoreError, "identity changed"):
            target.restore(perform=True, runtime=runtime)
        self.assertEqual(runtime.uploads, [])


if __name__ == "__main__":
    unittest.main()
