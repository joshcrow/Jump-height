"""Tests for tools/puckd/flash.py — the puck's own software update
(docs/sync-agent-plan.md:41-58's step 8, gate G2 at :62).

Two things are exercised for real, never mocked at the Python level:
  * latest_manifest()'s HTTP fetch — against a real local HTTP server
    (`_ManifestServer`) or a genuinely-closed local port (connection
    refused), never a monkeypatched urlopen.
  * flash()'s sha256 gate — against a real .uf2-shaped file on disk, hashed
    with real hashlib.

Everything flash() would otherwise need real hardware for (the serial
device, diskutil, the filesystem copy, wall-clock waits) is supplied
through its injectable keyword arguments: `_FakeDevice` stands in for
tools/jump's Device, `_FakeClock` replaces time.sleep/monotonic so a 30s or
60s wait pins in well under a second of real time, and small closures play
diskutil's list/mount and the file copy (including the
"Device not configured" copy error that IS the success signature —
docs/serial-parity-2026-09-09.md:336-340).

Run via: python3 -m pytest tools/tests/test_puckd_flash.py -q
"""

from __future__ import annotations

import errno
import hashlib
import http.server
import os
import json
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools"))

from puckd import flash  # noqa: E402

# A net under every test in this file: flash.py's default log sink is
# PUCKD_HOME/daemon.log (flash._default_log), the file Josh reads over the
# phone. A test that forgets to inject `log=` would otherwise append to the
# REAL one — which happened once while these seams were being written.
_LOG_SANDBOX = None


def setUpModule():  # noqa: N802 -- unittest's own spelling
    global _LOG_SANDBOX
    _LOG_SANDBOX = tempfile.TemporaryDirectory()
    os.environ["PUCKD_HOME"] = _LOG_SANDBOX.name


def tearDownModule():  # noqa: N802
    os.environ.pop("PUCKD_HOME", None)
    if _LOG_SANDBOX is not None:
        _LOG_SANDBOX.cleanup()


# --------------------------------------------------------------------------
# A real local HTTP server for latest_manifest() — no monkeypatching of
# urllib inside the module under test.
# --------------------------------------------------------------------------

class _ManifestServer:
    """Serves a single fixed (status, body) at MANIFEST_PATH; anything else
    404s. Runs in a background thread on 127.0.0.1 with an OS-assigned
    port, so parallel test runs never collide on a fixed port number."""

    MANIFEST_PATH = "/firmware/latest.json"

    def __init__(self, status: int = 200, body: bytes = b"{}",
                 content_type: str = "application/json"):
        self._status = status
        self._body = body
        self._content_type = content_type
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path != outer.MANIFEST_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(outer._status)
                self.send_header("Content-Type", outer._content_type)
                self.end_headers()
                self.wfile.write(outer._body)

            def log_message(self, *a):  # silence: tests should be quiet
                pass

        self._httpd = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                         daemon=True)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        port = self._httpd.server_address[1]
        return f"http://127.0.0.1:{port}"


class _HangingServer:
    """Accepts one TCP connection and then sends nothing, ever — the real
    way to provoke a genuine socket timeout in urlopen without waiting out
    flash.py's real 10s default (tests shrink _MANIFEST_TIMEOUT_S first)."""

    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(1)
        self._stop = False
        self._thread = threading.Thread(target=self._accept_and_stall,
                                         daemon=True)

    def _accept_and_stall(self):
        self._sock.settimeout(5)
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        try:
            while not self._stop:
                conn.settimeout(0.2)
                try:
                    if not conn.recv(4096):
                        break
                except socket.timeout:
                    continue
                except OSError:
                    break
        finally:
            conn.close()

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop = True
        self._sock.close()
        self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._sock.getsockname()[1]}"


def _closed_port_url() -> str:
    """A local port nothing is listening on: bind, learn the port, close —
    connecting there afterward gets a real, fast ECONNREFUSED."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


REAL_MANIFEST = {
    "src": "54c6826d",
    "file": "jumpheight-54c6826d.uf2",
    "bytes": 312832,
    "blocks": 611,
    "sha256": "7457a1325fe8de21d2575745fd5fe79fa64d1d94eb904a482bed597de4905517",
    "family": "0xADA52840",
    "built_utc": "2026-09-11",
    "note": "Flashed and verified on JumpHeight-8673 before publishing.",
}


class TestLatestManifest(unittest.TestCase):
    def test_valid_manifest_comes_back_unchanged(self):
        with _ManifestServer(200, json.dumps(REAL_MANIFEST).encode()) as srv:
            got = flash.latest_manifest(srv.url)
        self.assertEqual(got, REAL_MANIFEST)

    def test_valid_manifest_trailing_slash_on_site_url(self):
        with _ManifestServer(200, json.dumps(REAL_MANIFEST).encode()) as srv:
            got = flash.latest_manifest(srv.url + "/")
        self.assertEqual(got, REAL_MANIFEST)

    def test_404_is_no_update_not_an_exception(self):
        with _ManifestServer(404, b"not found") as srv:
            self.assertIsNone(flash.latest_manifest(srv.url))

    def test_500_is_no_update(self):
        with _ManifestServer(500, b"server error") as srv:
            self.assertIsNone(flash.latest_manifest(srv.url))

    def test_not_json_is_no_update(self):
        with _ManifestServer(200, b"<html>not json</html>",
                              content_type="text/html") as srv:
            self.assertIsNone(flash.latest_manifest(srv.url))

    def test_json_array_not_object_is_no_update(self):
        with _ManifestServer(200, b"[1, 2, 3]") as srv:
            self.assertIsNone(flash.latest_manifest(srv.url))

    def test_src_not_hex_is_no_update(self):
        """sync.js:2378's own shape check: src must be 4-40 hex chars."""
        bad = dict(REAL_MANIFEST, src="not-hex!!")
        with _ManifestServer(200, json.dumps(bad).encode()) as srv:
            self.assertIsNone(flash.latest_manifest(srv.url))

    def test_file_with_path_traversal_is_no_update(self):
        """sync.js:2379: `file` is pasted somewhere path-shaped downstream
        (here: joined onto the mounted volume path), so a '..' or a slash
        must never survive the shape check."""
        bad = dict(REAL_MANIFEST, file="../../etc/passwd.uf2")
        with _ManifestServer(200, json.dumps(bad).encode()) as srv:
            self.assertIsNone(flash.latest_manifest(srv.url))

    def test_file_not_uf2_is_no_update(self):
        bad = dict(REAL_MANIFEST, file="jumpheight-54c6826d.exe")
        with _ManifestServer(200, json.dumps(bad).encode()) as srv:
            self.assertIsNone(flash.latest_manifest(srv.url))

    def test_missing_src_field_is_no_update(self):
        bad = {k: v for k, v in REAL_MANIFEST.items() if k != "src"}
        with _ManifestServer(200, json.dumps(bad).encode()) as srv:
            self.assertIsNone(flash.latest_manifest(srv.url))

    def test_connection_refused_is_no_update_never_raises(self):
        self.assertIsNone(flash.latest_manifest(_closed_port_url()))

    def test_socket_timeout_is_no_update_never_raises(self):
        with _HangingServer() as srv:
            with patch.object(flash, "_MANIFEST_TIMEOUT_S", 0.3):
                self.assertIsNone(flash.latest_manifest(srv.url))

    def test_manifest_missing_sha256_still_returned_shape_ok(self):
        """latest_manifest() only shape-checks src/file (matching sync.js);
        a manifest with a valid src/file but no sha256 is NOT rejected
        here — flash()'s G2 gate is where that failure surfaces, so the
        two distinct problems ("no update available" vs. "update offered
        but unsafe to apply") never collapse into the same None."""
        bad = {k: v for k, v in REAL_MANIFEST.items() if k != "sha256"}
        with _ManifestServer(200, json.dumps(bad).encode()) as srv:
            got = flash.latest_manifest(srv.url)
        self.assertEqual(got, bad)
        self.assertNotIn("sha256", got)


class TestNeedsUpdate(unittest.TestCase):
    def test_true_when_src_differs(self):
        self.assertTrue(flash.needs_update("5c80a436", REAL_MANIFEST))

    def test_false_when_src_matches(self):
        self.assertFalse(flash.needs_update("54c6826d", REAL_MANIFEST))

    def test_false_when_manifest_is_none(self):
        self.assertFalse(flash.needs_update("5c80a436", None))

    def test_false_when_puck_src_is_none(self):
        self.assertFalse(flash.needs_update(None, REAL_MANIFEST))

    def test_false_when_puck_src_is_empty_string(self):
        self.assertFalse(flash.needs_update("", REAL_MANIFEST))


# --------------------------------------------------------------------------
# flash() — fakes for every injectable seam.
# --------------------------------------------------------------------------

class _FakeDevice:
    """Stands in for tools/jump's Device: only .command()/.close() are ever
    called on it by flash.py. `script` maps a command's first word to
    either a list[str] of reply lines or an exception instance to raise."""

    def __init__(self, port, script, calls):
        self.port = port
        self._script = script
        self._calls = calls

    def command(self, cmd, timeout=None):
        self._calls.append(("command", self.port, cmd, timeout))
        action = self._script.get(cmd.split()[0], [])
        if isinstance(action, BaseException):
            raise action
        return list(action)

    def close(self):
        self._calls.append(("close", self.port))


def _device_factory(port_scripts, calls, unopenable=frozenset()):
    """port_scripts: {port: {cmd: lines_or_exception}}. A port not in
    port_scripts at all still opens with an empty script (every command
    returns [])."""
    def factory(port):
        if port in unopenable:
            raise OSError(f"[Errno 2] no such device: {port}")
        return _FakeDevice(port, port_scripts.get(port, {}), calls)
    return factory


class _FakeClock:
    """sleep() advances the same counter now() reads — every flash.py wait
    loop plays out its real logic with zero actual elapsed wall time."""

    def __init__(self, start: float = 0.0):
        self.t = start
        self.sleep_calls = 0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleep_calls += 1
        self.t += seconds


def _make_uf2(tmp: Path, content: bytes = b"fake uf2 bytes \x00\x01\x02" * 50) -> Path:
    p = tmp / "jumpheight-54c6826d.uf2"
    p.write_bytes(content)
    return p


def _sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class TestFlashSha256Gate(unittest.TestCase):
    """G2: "sha256 of the .uf2 matches latest.json" — checked BEFORE
    anything else. Neither the device nor the filesystem beyond the .uf2
    itself may be touched when this fails."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)

    def test_mismatched_sha256_refuses_before_touching_device(self):
        uf2 = _make_uf2(self.tmp)
        manifest = dict(REAL_MANIFEST, sha256="0" * 64)
        calls = []

        def factory(port):
            calls.append(port)
            raise AssertionError("device_factory must not be called (G2)")

        result = flash.flash(
            "/dev/cu.usbmodem1101", uf2, manifest,
            device_factory=factory,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_SHA256)
        self.assertIn("sha256 mismatch", result.error)
        self.assertEqual(calls, [])

    def test_missing_sha256_in_manifest_refuses(self):
        uf2 = _make_uf2(self.tmp)
        manifest = {k: v for k, v in REAL_MANIFEST.items() if k != "sha256"}

        def factory(port):
            raise AssertionError("device_factory must not be called (G2)")

        result = flash.flash("/dev/cu.usbmodem1101", uf2, manifest,
                              device_factory=factory)
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_SHA256)
        self.assertIn("no sha256", result.error)

    def test_correct_sha256_passes_the_gate(self):
        """Not a full run (no other seams supplied) — just proves the gate
        itself opens on a real match, using real hashlib against a real
        file, before anything downstream is reached."""
        uf2 = _make_uf2(self.tmp)
        manifest = dict(REAL_MANIFEST, sha256=_sha256_of(uf2))
        opened = []

        def factory(port):
            opened.append(port)
            raise RuntimeError("stop right here — gate passed is all we assert")

        result = flash.flash("/dev/cu.usbmodem1101", uf2, manifest,
                              device_factory=factory)
        self.assertEqual(opened, ["/dev/cu.usbmodem1101"])
        self.assertEqual(result.stage_reached, flash.STAGE_SEND_UF2)

    def test_unreadable_uf2_file_refuses_at_sha256_stage(self):
        missing = self.tmp / "does-not-exist.uf2"
        manifest = dict(REAL_MANIFEST, sha256="0" * 64)
        result = flash.flash("/dev/cu.usbmodem1101", missing, manifest)
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_SHA256)
        self.assertIn("couldn't read", result.error)


class TestFlashSequence(unittest.TestCase):
    """The full sequence past the sha256 gate: send uf2, wait for the
    volume, copy, wait for the port, read info back."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)
        self.uf2 = _make_uf2(self.tmp)
        self.manifest = dict(REAL_MANIFEST, sha256=_sha256_of(self.uf2))
        self.old_port = "/dev/cu.usbmodem1101"
        self.new_port = "/dev/cu.usbmodem2202"

    def _run(self, calls=None, **overrides):
        """Runs flash() past the sha256 gate with every seam defaulted to a
        happy-path fake; `overrides` replaces any of them. `calls` is the
        call-log list a caller's own device_factory (if it supplies one via
        `overrides["device_factory"]`) already closes over — passed straight
        through and returned unchanged, so the caller can still inspect it
        after the run instead of losing the reference to a second, unrelated
        list this helper would otherwise build."""
        if calls is None:
            calls = []
        if "device_factory" not in overrides:
            overrides["device_factory"] = _device_factory(
                {self.new_port: {"info": [f"INFO src={self.manifest['src']} "
                                          "fw=0.5.0 sample_hz=50"]}},
                calls,
            )
        kwargs = dict(
            scan_ports=lambda: [self.new_port],
            volume_exists=lambda: True,
            list_disks=lambda: "",
            mount_volume=lambda: None,
            copy_file=lambda src, dst: None,
            sleep=lambda s: None,
            now=lambda: 0.0,
        )
        kwargs.update(overrides)
        result = flash.flash(self.old_port, self.uf2, self.manifest, **kwargs)
        return result, calls

    def test_happy_path_end_to_end(self):
        calls = []
        clock = _FakeClock()
        volume = {"mounted": True}
        copies = []

        result = flash.flash(
            self.old_port, self.uf2, self.manifest,
            device_factory=_device_factory(
                {
                    self.old_port: {"uf2": ["OK uf2"]},
                    self.new_port: {"info": [
                        f"INFO src={self.manifest['src']} fw=0.5.0 sample_hz=50"]},
                },
                calls,
            ),
            scan_ports=lambda: [self.new_port],
            volume_exists=lambda: volume["mounted"],
            list_disks=lambda: "",
            mount_volume=lambda: None,
            copy_file=lambda src, dst: copies.append((src, dst)),
            sleep=clock.sleep,
            now=clock.now,
        )

        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.src_after, self.manifest["src"])
        self.assertEqual(result.stage_reached, flash.STAGE_DONE)
        self.assertIsNone(result.error)
        self.assertEqual(copies, [(str(self.uf2),
                                   f"/Volumes/XIAO-SENSE/{self.uf2.name}")])
        # both devices opened and closed — the port is never left held open.
        self.assertIn(("command", self.old_port, "uf2", flash._UF2_SEND_TIMEOUT_S),
                       calls)
        self.assertIn(("close", self.old_port), calls)
        self.assertIn(("close", self.new_port), calls)

    def test_present_but_unmounted_volume_gets_mounted(self):
        """docs/sync-agent-plan.md:52: "mount via diskutil if
        present-but-unmounted — measured 2026-09-11"."""
        calls = []
        clock = _FakeClock()
        state = {"mounted": False, "mount_calls": 0}

        def list_disks():
            return "   1: Windows_FAT_32 XIAO-SENSE   256.0 MB   disk4s1\n"

        def mount_volume():
            state["mount_calls"] += 1
            state["mounted"] = True

        result = flash.flash(
            self.old_port, self.uf2, self.manifest,
            device_factory=_device_factory(
                {self.new_port: {"info": [f"INFO src={self.manifest['src']}"]}},
                calls,
            ),
            scan_ports=lambda: [self.new_port],
            volume_exists=lambda: state["mounted"],
            list_disks=list_disks,
            mount_volume=mount_volume,
            copy_file=lambda src, dst: None,
            sleep=clock.sleep,
            now=clock.now,
        )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(state["mount_calls"], 1)

    def test_volume_never_appears_times_out(self):
        calls = []
        clock = _FakeClock()

        result = flash.flash(
            self.old_port, self.uf2, self.manifest,
            device_factory=_device_factory({}, calls),
            scan_ports=lambda: [],
            volume_exists=lambda: False,
            list_disks=lambda: "",  # never even enumerated
            mount_volume=lambda: (_ for _ in ()).throw(
                AssertionError("must not mount what diskutil never listed")),
            copy_file=lambda src, dst: (_ for _ in ()).throw(
                AssertionError("must not reach the copy stage")),
            sleep=clock.sleep,
            now=clock.now,
            volume_wait_s=5.0,
            poll_interval_s=1.0,
            # Injected, or this unit test shells out to the real `ioreg`
            # and appends a line to the real PUCKD_HOME/daemon.log — which
            # it did, once, before these seams existed.
            ioreg_fn=lambda: "",
            log=lambda msg: None,
            run_dfu=lambda argv, t: (_ for _ in ()).throw(
                AssertionError("no DFU without an identified bootloader")),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_VOLUME_WAIT)
        self.assertIsNone(result.src_after)
        self.assertGreaterEqual(clock.t, 5.0)
        # the wait loop must actually have ended, not spun forever
        self.assertLess(clock.sleep_calls, 20)

    def test_copy_device_not_configured_errno_is_success(self):
        """docs/serial-parity-2026-09-09.md:336-340: THAT MESSAGE IS THE
        SUCCESS SIGNATURE. Matched by errno here (ENODEV)."""
        calls = []
        result, _ = self._run(
            copy_file=lambda src, dst: (_ for _ in ()).throw(
                OSError(errno.ENODEV, "Device not configured")),
        )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.stage_reached, flash.STAGE_DONE)

    def test_copy_device_not_configured_by_message_is_success(self):
        """Same rule, matched by the exact wording rather than errno — real
        shutil/cp failures on a vanished mount are not guaranteed to carry
        errno 6 on every path (extended-attribute copy, cp subprocess...)."""
        result, _ = self._run(
            copy_file=lambda src, dst: (_ for _ in ()).throw(
                OSError("could not copy extended attributes to "
                        "/Volumes/XIAO-SENSE/x.uf2: Device not configured")),
        )
        self.assertTrue(result.ok, result.error)

    def test_copy_other_oserror_is_a_real_failure(self):
        result, _ = self._run(
            copy_file=lambda src, dst: (_ for _ in ()).throw(
                OSError(errno.ENOSPC, "No space left on device")),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_COPY)
        self.assertIn("No space left", result.error)

    def test_port_never_returns_times_out(self):
        calls = []
        clock = _FakeClock()
        result = flash.flash(
            self.old_port, self.uf2, self.manifest,
            device_factory=_device_factory({}, calls),
            scan_ports=lambda: [],
            volume_exists=lambda: True,
            list_disks=lambda: "",
            mount_volume=lambda: None,
            copy_file=lambda src, dst: None,
            sleep=clock.sleep,
            now=clock.now,
            port_wait_s=6.0,
            poll_interval_s=1.0,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_PORT_WAIT)
        self.assertGreaterEqual(clock.t, 6.0)

    def test_stranger_port_is_ignored_until_usbmodem_appears(self):
        """Only /dev/cu.usbmodem* counts — a phantom or unrelated adapter
        must never be mistaken for the puck coming back."""
        calls = []
        clock = _FakeClock()
        seq = [["/dev/cu.stranger"], ["/dev/cu.stranger"],
               ["/dev/cu.stranger", self.new_port]]
        state = {"n": 0}

        def scan():
            i = min(state["n"], len(seq) - 1)
            state["n"] += 1
            return seq[i]

        result = flash.flash(
            self.old_port, self.uf2, self.manifest,
            device_factory=_device_factory(
                {self.new_port: {"info": [f"INFO src={self.manifest['src']}"]}},
                calls,
            ),
            scan_ports=scan,
            volume_exists=lambda: True,
            list_disks=lambda: "",
            mount_volume=lambda: None,
            copy_file=lambda src, dst: None,
            sleep=clock.sleep,
            now=clock.now,
        )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.src_after, self.manifest["src"])

    def test_uf2_send_dropping_the_port_is_not_an_error(self):
        """docs/serial-parity-2026-09-09.md:328-330: the CDC port dropping
        right after `uf2` IS the reboot. dev.close() must still run."""
        calls = []
        result, calls = self._run(
            calls=calls,
            device_factory=_device_factory(
                {
                    self.old_port: {"uf2": TimeoutError(
                        "device went silent during 'uf2'")},
                    self.new_port: {"info": [
                        f"INFO src={self.manifest['src']}"]},
                },
                calls,
            ),
        )
        self.assertTrue(result.ok, result.error)
        self.assertIn(("close", self.old_port), calls)

    def test_cannot_open_port_for_uf2_stops_immediately(self):
        """A real failure to even open the port — as opposed to opening it
        and having the send/reboot drop it — must stop at send_uf2 and
        never touch the volume/copy/port-wait machinery."""
        calls = []
        result = flash.flash(
            self.old_port, self.uf2, self.manifest,
            device_factory=_device_factory({}, calls, unopenable={self.old_port}),
            scan_ports=lambda: (_ for _ in ()).throw(
                AssertionError("must not reach the port wait")),
            volume_exists=lambda: (_ for _ in ()).throw(
                AssertionError("must not reach the volume wait")),
            list_disks=lambda: "",
            mount_volume=lambda: None,
            copy_file=lambda src, dst: (_ for _ in ()).throw(
                AssertionError("must not reach the copy stage")),
            sleep=lambda s: None,
            now=lambda: 0.0,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_SEND_UF2)
        self.assertIn("couldn't open", result.error)

    def test_reopen_for_info_fails(self):
        calls = []
        result, _ = self._run(
            device_factory=_device_factory({}, calls, unopenable={self.new_port}),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_INFO)
        self.assertIn("couldn't reopen", result.error)

    def test_info_command_raises(self):
        calls = []
        result, _ = self._run(
            device_factory=_device_factory(
                {self.new_port: {"info": TimeoutError("silent")}}, calls),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_INFO)
        self.assertIn("didn't answer info", result.error)

    def test_info_with_no_src_field_fails(self):
        calls = []
        result, _ = self._run(
            device_factory=_device_factory(
                {self.new_port: {"info": ["INFO fw=0.5.0 sample_hz=50"]}},
                calls),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_INFO)
        self.assertIn("no src=", result.error)

    def test_src_mismatch_after_flash_fails_with_src_after_reported(self):
        calls = []
        result, _ = self._run(
            device_factory=_device_factory(
                {self.new_port: {"info": ["INFO src=deadbeef fw=0.5.0"]}},
                calls),
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_INFO)
        self.assertEqual(result.src_after, "deadbeef")
        self.assertIn("deadbeef", result.error)
        self.assertIn(self.manifest["src"], result.error)


class TestFlashDefaultVolumeExists(unittest.TestCase):
    """volume_exists's real default (Path.is_dir()) — not the override used
    everywhere else above — pinned against a real temp directory standing
    in for /Volumes/XIAO-SENSE."""

    def test_default_volume_exists_checks_the_real_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            uf2 = _make_uf2(tmp)
            manifest = dict(REAL_MANIFEST, sha256=_sha256_of(uf2))
            volume_path = tmp / "XIAO-SENSE"  # does not exist yet
            new_port = "/dev/cu.usbmodem9"
            calls = []
            clock = _FakeClock()

            def mount_volume():
                volume_path.mkdir()  # diskutil mount, standing in for real

            result = flash.flash(
                "/dev/cu.usbmodem1", uf2, manifest,
                device_factory=_device_factory(
                    {new_port: {"info": [f"INFO src={manifest['src']}"]}},
                    calls),
                scan_ports=lambda: [new_port],
                volume_path=volume_path,
                # volume_exists intentionally NOT overridden
                list_disks=lambda: "XIAO-SENSE",
                mount_volume=mount_volume,
                copy_file=lambda src, dst: None,
                sleep=clock.sleep,
                now=clock.now,
            )
            self.assertTrue(result.ok, result.error)
            self.assertTrue(volume_path.is_dir())


class TestPortReturnPicksTheBoardItFlashed(unittest.TestCase):
    """Stage 5 waits for "a /dev/cu.usbmodem* port" and then reads src= off
    whatever it finds. On a bench with more than one board that is a coin
    toss, and the coin decides whether a flash that never landed reports
    PASS -- CLAUDE.md ss1: "Unpinned BLE tools answer from whichever replies
    first -- this has corrupted two analyses and flashed one wrong board."
    The port we sent `uf2` to is preferred whenever it comes back."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)

    def _flash_with_ports(self, ports, port_path="/dev/cu.usbmodem101"):
        uf2 = _make_uf2(self.tmp)
        manifest = dict(REAL_MANIFEST, sha256=_sha256_of(uf2))
        calls = []
        volume = self.tmp / "XIAO-SENSE"
        volume.mkdir()
        # Every port answers info with the NEW src, so a wrong pick cannot
        # be caught by the src check -- only by which port was opened.
        scripts = {p: {"info": [f"INFO src={manifest['src']}"]} for p in ports}
        scripts[port_path] = {"info": [f"INFO src={manifest['src']}"]}
        clock = _FakeClock()
        result = flash.flash(
            port_path, uf2, manifest,
            device_factory=_device_factory(scripts, calls),
            scan_ports=lambda: list(ports),
            volume_path=volume,
            list_disks=lambda: "",
            mount_volume=lambda: None,
            copy_file=lambda src, dst: None,
            sleep=clock.sleep, now=clock.now)
        self.assertTrue(result.ok, result.error)
        # calls records ("command", port, cmd, timeout) per _FakeDevice.
        info_ports = [c[1] for c in calls if c[0] == "command" and c[2] == "info"]
        return info_ports

    def test_the_original_port_wins_over_a_neighbour_that_sorts_first(self):
        ports = ["/dev/cu.usbmodem001", "/dev/cu.usbmodem101"]
        self.assertEqual(["/dev/cu.usbmodem101"], self._flash_with_ports(ports))

    def test_a_renumbered_port_still_works_and_is_deterministic(self):
        """macOS does sometimes hand back a different /dev/cu.usbmodemN.
        With the original absent the fallback must be the SORTED first, not
        scan order, so two runs on one bench never disagree."""
        ports = ["/dev/cu.usbmodem900", "/dev/cu.usbmodem200"]
        self.assertEqual(["/dev/cu.usbmodem200"], self._flash_with_ports(ports))


if __name__ == "__main__":
    unittest.main()


# ---- added by the post-faa08a9 adversarial review -------------------------


class TestTheRawCopyWritesEveryByte(unittest.TestCase):
    """_default_copy_file opens the destination with buffering=0. A raw
    file's write() is one write(2) and is ALLOWED to return short without
    raising -- there is no buffered layer left to finish the job. A short
    write puts a truncated .uf2 on the bootloader's volume: the image is
    ignored, the board comes back on the old src, and stage 6 reports a
    mismatch that looks like a bad build."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)

    def test_a_destination_that_takes_64_bytes_at_a_time_still_gets_all_of_it(self):
        src = self.tmp / "firmware.uf2"
        payload = bytes(range(256)) * 40          # 10240 bytes
        src.write_bytes(payload)
        dst = self.tmp / "XIAO-SENSE.img"
        written = bytearray()

        class _ShortWriter:
            def write(self, view):
                chunk = bytes(view[:64])
                written.extend(chunk)
                return len(chunk)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch("builtins.open", lambda *a, **k: _ShortWriter()):
            flash._default_copy_file(str(src), str(dst))

        self.assertEqual(bytes(written), payload,
                         "every byte of the image reached the volume")

    def test_a_write_that_makes_no_progress_is_a_failure_not_a_success(self):
        # errno.EIO/ENODEV/ENXIO are flash's SUCCESS signature (the board
        # rebooting mid-copy). A stalled write must not be raised as one of
        # them, or a copy that never happened reads as a flash that did.
        src = self.tmp / "firmware.uf2"
        src.write_bytes(b"x" * 100)

        class _Stalled:
            def write(self, view):
                return 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with patch("builtins.open", lambda *a, **k: _Stalled()):
            with self.assertRaises(OSError) as caught:
                flash._default_copy_file(str(src), str(self.tmp / "dst"))
        self.assertFalse(flash._is_device_not_configured(caught.exception),
                         "a stalled write must never read as the reboot")


class DiskutilOutputIsDecodedAsUtf8(unittest.TestCase):
    """Measured on the rider's Mac 2026-09-14: diskutil prints volume names
    wrapped in U+2068/U+2069, and a locale-decoded subprocess raised
    UnicodeDecodeError out of the flash leg on every job."""

    def test_default_list_disks_passes_an_explicit_encoding(self):
        import inspect
        src = inspect.getsource(flash._default_list_disks)
        self.assertIn('encoding="utf-8"', src)
        self.assertNotIn("text=True", src)

    def test_non_ascii_diskutil_output_does_not_raise(self):
        from unittest.mock import patch
        import subprocess as sp
        fake = sp.CompletedProcess(["diskutil", "list"], 0,
                                   "   1: APFS Volume \u2068JumpHeight Sync\u2069 274 MB disk3s1\n", "")
        with patch.object(flash.subprocess, "run", return_value=fake):
            out = flash._default_list_disks()
        self.assertIn("JumpHeight Sync", out)


class ReplacesGate(unittest.TestCase):
    """A manifest that names the builds it replaces never flashes over any
    other build (a bench puck on a newer dev build was downgraded by the old
    src != latest.src rule, 2026-09-14)."""

    def test_listed_old_build_is_updated(self):
        m = {"src": "c5eea285", "replaces": ["5c80a436", "54c6826d"]}
        self.assertTrue(flash.needs_update("5c80a436", m))
        self.assertTrue(flash.needs_update("54c6826d", m))

    def test_unlisted_build_is_left_alone(self):
        m = {"src": "c5eea285", "replaces": ["5c80a436", "54c6826d"]}
        self.assertFalse(flash.needs_update("0304ab97", m), "a dev build newer than the site")
        self.assertFalse(flash.needs_update("c5eea285", m), "already current")

    def test_a_manifest_without_replaces_keeps_the_old_rule(self):
        self.assertTrue(flash.needs_update("anything", {"src": "c5eea285"}))
        self.assertFalse(flash.needs_update("c5eea285", {"src": "c5eea285"}))


# ==========================================================================
# THE POST-`uf2` "SILENT PUCK" (docs/STATUS.md, 2026-09-15) and the
# serial-DFU fallback out of it.
#
# The two ioreg fixtures below are REAL captures from the bench Puck on
# 2026-09-15, trimmed to the XIAO's own device block: one with the app
# running, one with the bootloader. Note what is IDENTICAL in them -- the
# product string, the locationID, and kUSBSerialNumberString. Only
# idProduct differs (32837 = 0x8045 app, 69 = 0x0045 bootloader). That is
# the whole finding, and a fabricated fixture would have hidden it.
# ==========================================================================

_IOREG_HEAD = """+-o Root  <class IORegistryEntry, id 0x100000100, retain 20>
  +-o AppleT6000USBXHCI@01000000  <class AppleT6000USBXHCI, id 0x10001a1f0, registered, matched, active, busy 0, retain 41>
      {
        "idProduct" = 33267
        "USB Product Name" = "USB3.1 Hub"
      }
"""

_IOREG_XIAO_APP = """    +-o XIAO nRF52840 Sense@00100000  <class IOUSBHostDevice, id 0x10001b583, registered, matched, active, busy 0 (258 ms), retain 31>
        {
          "sessionID" = 6159595498528
          "idProduct" = 32837
          "USB Product Name" = "XIAO nRF52840 Sense"
          "locationID" = 1048576
          "kUSBSerialNumberString" = "2513620E30AE413D"
          "idVendor" = 10374
        }
"""

_IOREG_XIAO_BOOTLOADER = """    +-o XIAO nRF52840 Sense@00100000  <class IOUSBHostDevice, id 0x10001abca, registered, matched, active, busy 0 (2300 ms), retain 37>
        {
          "sessionID" = 6151661472547
          "idProduct" = 69
          "USB Product Name" = "XIAO nRF52840 Sense"
          "locationID" = 1048576
          "kUSBSerialNumberString" = "2513620E30AE413D"
          "idVendor" = 10374
        }
"""

# A SECOND board, at another USB location -- CLAUDE.md §1's "three boards
# can advertise at once", the collision that has already flashed one wrong
# board. locationID 0x14200000 -> /dev/cu.usbmodem142<NN>.
_IOREG_XIAO_SECOND_APP = """    +-o XIAO nRF52840 Sense@14200000  <class IOUSBHostDevice, id 0x10001c001, registered, matched, active, busy 0 (240 ms), retain 31>
        {
          "sessionID" = 7159595498528
          "idProduct" = 32837
          "USB Product Name" = "XIAO nRF52840 Sense"
          "locationID" = 337641472
          "kUSBSerialNumberString" = "9999999999999999"
          "idVendor" = 10374
        }
"""


class TestUsbProductId(unittest.TestCase):
    """idProduct is the ONLY thing that separates a puck sitting in its
    bootloader from a puck that ignored `uf2` -- same port name, same USB
    serial, same product string."""

    def test_app_reads_0x8045(self):
        text = _IOREG_HEAD + _IOREG_XIAO_APP
        self.assertEqual(
            flash.usb_product_id("/dev/cu.usbmodem101", ioreg_fn=lambda: text),
            flash.PID_APP)
        self.assertEqual(flash.PID_APP, 0x8045)

    def test_bootloader_reads_0x0045(self):
        text = _IOREG_HEAD + _IOREG_XIAO_BOOTLOADER
        self.assertEqual(
            flash.usb_product_id("/dev/cu.usbmodem101", ioreg_fn=lambda: text),
            flash.PID_BOOTLOADER)
        self.assertEqual(flash.PID_BOOTLOADER, 0x0045)

    def test_no_xiao_on_usb_is_none(self):
        self.assertIsNone(
            flash.usb_product_id("/dev/cu.usbmodem101",
                                 ioreg_fn=lambda: _IOREG_HEAD))

    def test_unreadable_ioreg_is_none_not_a_raise(self):
        self.assertIsNone(
            flash.usb_product_id("/dev/cu.usbmodem101", ioreg_fn=lambda: ""))

    def test_the_port_picks_between_two_boards(self):
        """One board in its bootloader at 0x00100000, another running the
        app at 0x14200000: the answer must be the board whose port we were
        given, not whichever ioreg printed first."""
        text = _IOREG_HEAD + _IOREG_XIAO_BOOTLOADER + _IOREG_XIAO_SECOND_APP
        self.assertEqual(
            flash.usb_product_id("/dev/cu.usbmodem101", ioreg_fn=lambda: text),
            flash.PID_BOOTLOADER)
        self.assertEqual(
            flash.usb_product_id("/dev/cu.usbmodem14201", ioreg_fn=lambda: text),
            flash.PID_APP)

    def test_two_disagreeing_boards_and_no_port_match_is_none(self):
        """Never guess between boards. None here means "could not tell",
        which flash() reports as such -- it does NOT mean 0x0045, and so it
        can never start a serial DFU into an unidentified board."""
        text = _IOREG_HEAD + _IOREG_XIAO_BOOTLOADER + _IOREG_XIAO_SECOND_APP
        self.assertIsNone(
            flash.usb_product_id("/dev/cu.usbmodem9999", ioreg_fn=lambda: text))

    def test_two_agreeing_boards_and_no_port_match_still_answers(self):
        text = _IOREG_HEAD + _IOREG_XIAO_APP + _IOREG_XIAO_SECOND_APP
        self.assertEqual(
            flash.usb_product_id("/dev/cu.usbmodem9999", ioreg_fn=lambda: text),
            flash.PID_APP)

    def test_port_prefix_from_location_id(self):
        """Measured: locationID 0x00100000 -> /dev/cu.usbmodem101."""
        self.assertEqual(flash._port_prefix_for_location("00100000"),
                          "/dev/cu.usbmodem1")
        self.assertEqual(flash._port_prefix_for_location("14200000"),
                          "/dev/cu.usbmodem142")
        self.assertTrue("/dev/cu.usbmodem101".startswith(
            flash._port_prefix_for_location("00100000")))

    def test_the_real_ioreg_default_is_not_used_when_injected(self):
        """A unit test must never shell out. If ioreg_fn is honoured, a
        fixture that names no XIAO answers None even on a bench where a
        real puck is plugged in."""
        self.assertIsNone(flash.usb_product_id("/dev/cu.usbmodem101",
                                                ioreg_fn=lambda: "nothing here"))


# A manifest that also publishes the serial-DFU package.
DFU_MANIFEST_EXTRA = {
    "dfu_file": "jumpheight-54c6826d.zip",
    "dfu_bytes": 157102,
    "dfu_sha256": "0" * 64,   # replaced per-test with the real hash
}


class _DfuHarness(unittest.TestCase):
    """Shared wiring for the fallback: the volume NEVER appears, so every
    test here goes down the stage-4b path (or is refused before it)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.addCleanup(self._tmpdir.cleanup)
        self.uf2 = _make_uf2(self.tmp)
        self.zip_bytes = b"PK\x03\x04 not really a zip, but a real sha256" * 40
        self.zip_name = "jumpheight-54c6826d.zip"
        self.zip_sha = hashlib.sha256(self.zip_bytes).hexdigest()
        self.manifest = dict(REAL_MANIFEST,
                             sha256=_sha256_of(self.uf2),
                             dfu_file=self.zip_name,
                             dfu_bytes=len(self.zip_bytes),
                             dfu_sha256=self.zip_sha)
        self.port = "/dev/cu.usbmodem101"
        self.logs = []
        self.dfu_runs = []

    def _ioreg(self, pid_block):
        return lambda: _IOREG_HEAD + pid_block

    def _run_dfu(self, output, code=0):
        def runner(argv, timeout_s):
            self.dfu_runs.append((list(argv), timeout_s))
            return code, output
        return runner

    def _cache_the_zip(self):
        (self.tmp / self.zip_name).write_bytes(self.zip_bytes)

    def _flash(self, **overrides):
        calls = []
        clock = _FakeClock()
        kwargs = dict(
            device_factory=_device_factory(
                {self.port: {"info": [
                    f"INFO src={self.manifest['src']} fw=0.5.0 sample_hz=50"]}},
                calls),
            scan_ports=lambda: [self.port],
            volume_exists=lambda: False,
            list_disks=lambda: "",
            mount_volume=lambda: None,
            copy_file=lambda src, dst: self.fail(
                "the copy stage must not run when no volume appeared"),
            sleep=clock.sleep,
            now=clock.now,
            volume_wait_s=5.0,
            poll_interval_s=1.0,
            ioreg_fn=self._ioreg(_IOREG_XIAO_BOOTLOADER),
            dfu_cache_dir=self.tmp,
            site_url="http://127.0.0.1:1",
            fetch_fn=lambda site, name, dest: self.fail(
                "unexpected fetch of " + name),
            run_dfu=self._run_dfu("nothing useful"),
            nrfutil_path="/fake/adafruit-nrfutil",
            log=self.logs.append,
        )
        kwargs.update(overrides)
        return flash.flash(self.port, self.uf2, self.manifest, **kwargs), calls


class TestVolumeWaitNamesTheFailure(_DfuHarness):
    """Part 1: three faults that looked identical now say which they are."""

    def test_app_still_running_says_it_did_not_enter_update_mode(self):
        result, _ = self._flash(
            ioreg_fn=self._ioreg(_IOREG_XIAO_APP),
            run_dfu=lambda argv, t: self.fail("no DFU from the app state"))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_VOLUME_WAIT)
        self.assertIn("the puck did not enter update mode", result.error)
        self.assertIn("0x8045", result.error)
        self.assertTrue(any("did not enter update mode" in line
                            for line in self.logs), self.logs)

    def test_bootloader_says_this_mac_made_no_disk(self):
        self._cache_the_zip()
        result, _ = self._flash(
            run_dfu=self._run_dfu("Failed to upgrade target. Error is: nope"))
        self.assertFalse(result.ok)
        self.assertIn("the puck is in update mode but this Mac made no disk",
                       result.error)
        self.assertIn("0x0045", result.error)
        self.assertTrue(any("made no disk" in line for line in self.logs),
                        self.logs)

    def test_nothing_on_usb_says_puck_not_found(self):
        result, _ = self._flash(
            ioreg_fn=lambda: _IOREG_HEAD,
            run_dfu=lambda argv, t: self.fail("no DFU into an unknown board"))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_VOLUME_WAIT)
        self.assertIn("puck not found on USB", result.error)
        self.assertIn("none", result.error)

    def test_an_ioreg_that_raises_is_a_finding_not_a_crash(self):
        def boom(_port):
            raise OSError("ioreg exploded")
        result, _ = self._flash(
            usb_product_id_fn=boom,
            run_dfu=lambda argv, t: self.fail("no DFU without an identity"))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_VOLUME_WAIT)
        self.assertIn("puck not found on USB", result.error)
        self.assertTrue(any("could not read idProduct" in line
                            for line in self.logs), self.logs)

    def test_the_timeout_sentence_survives(self):
        """The new diagnosis is added to the old message, not instead of
        it: a reader still learns the volume never appeared and how long
        flash() waited."""
        result, _ = self._flash(
            ioreg_fn=self._ioreg(_IOREG_XIAO_APP),
            run_dfu=lambda argv, t: self.fail("no DFU"))
        self.assertIn("/Volumes/XIAO-SENSE never appeared within 5s",
                       result.error)


class TestRefusedCopyFallsBackToSerialDfu(_DfuHarness):
    """The OTHER way to be stuck in a bootloader with nothing flashed.

    MEASURED on the rider's Mac 2026-09-20 16:09: the disk appeared, the
    copy was refused -- `[Errno 13] Permission denied:
    '/Volumes/XIAO-SENSE/jumpheight-c5eea285.uf2'` -- after the
    _COPY_SETTLE_S retry was exhausted, and 1.0.6 returned STAGE_COPY and
    fired "needs you: check the puck". The puck stayed a USB drive for two
    days. The bootloader's CDC node was live the whole time.
    """

    def setUp(self):
        super().setUp()
        # The recovery ships DISABLED (flash.SERIAL_DFU_ON_REFUSED_COPY) until
        # a bench reading exists -- see that constant. These tests are what
        # makes turning it on a one-line change rather than a rewrite, so
        # they enable it explicitly and put it back afterwards.
        self._gate = flash.SERIAL_DFU_ON_REFUSED_COPY
        flash.SERIAL_DFU_ON_REFUSED_COPY = True
        self.addCleanup(setattr, flash, "SERIAL_DFU_ON_REFUSED_COPY", self._gate)

    def test_the_gate_ships_off(self):
        """If this ever fails, read SERIAL_DFU_ON_REFUSED_COPY's comment
        before changing it: the reading it waits for is in docs/STATUS.md."""
        self.assertFalse(self._gate,
                         "the refused-copy fallback must ship disabled")

    def test_with_the_gate_off_a_refused_copy_still_gives_up(self):
        flash.SERIAL_DFU_ON_REFUSED_COPY = False
        self._cache_the_zip()
        result, _ = self._flash_refused(
            run_dfu=lambda argv, t: self.fail("the gate is off"))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_COPY)
        self.assertIn("Permission denied", result.error)
        self.assertEqual(self.dfu_runs, [])

    def _refuse(self, errno_=13, msg="Permission denied"):
        def copy_file(src, dst):
            raise PermissionError(errno_, msg, dst)
        return copy_file

    def _flash_refused(self, **overrides):
        kwargs = dict(volume_exists=lambda: True, copy_file=self._refuse())
        kwargs.update(overrides)
        return self._flash(**kwargs)

    def test_a_refused_copy_recovers_over_serial_dfu(self):
        self._cache_the_zip()
        result, calls = self._flash_refused(
            run_dfu=self._run_dfu("Device programmed."))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.stage_reached, flash.STAGE_DONE)
        self.assertEqual(result.src_after, self.manifest["src"])
        self.assertEqual(len(self.dfu_runs), 1)
        self.assertIn(("command", self.port, "info", flash._INFO_TIMEOUT_S),
                      calls)

    def test_the_refusal_is_logged_before_the_fallback_runs(self):
        self._cache_the_zip()
        self._flash_refused(run_dfu=self._run_dfu("Device programmed."))
        joined = "\n".join(self.logs)
        self.assertIn("Permission denied", joined)
        self.assertIn("the disk refused the write", joined)

    def test_an_app_mode_board_is_never_dfu_uploaded_into(self):
        """CLAUDE.md #1. A refused copy is not a licence to upload into a
        board we have not identified as sitting in its bootloader."""
        self._cache_the_zip()
        result, _ = self._flash_refused(
            ioreg_fn=self._ioreg(_IOREG_XIAO_APP),
            run_dfu=lambda argv, t: self.fail("no DFU from the app state"))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_COPY)
        self.assertIn("Permission denied", result.error)
        self.assertEqual(self.dfu_runs, [])

    def test_both_failures_survive_into_the_error_when_dfu_also_fails(self):
        self._cache_the_zip()
        result, _ = self._flash_refused(
            run_dfu=self._run_dfu("Failed to upgrade target.", code=0))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_DFU_SERIAL)
        self.assertIn("Permission denied", result.error)
        self.assertIn("serial DFU fallback", result.error)

    def test_no_dfu_package_in_the_manifest_keeps_the_copy_verdict(self):
        for k in ("dfu_file", "dfu_bytes", "dfu_sha256"):
            self.manifest.pop(k, None)
        result, _ = self._flash_refused(
            run_dfu=lambda argv, t: self.fail("no package, no upload"))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_COPY)
        self.assertIn("Permission denied", result.error)

    def test_device_not_configured_is_still_the_success_signature(self):
        """A regression guard: ENODEV on the copy MEANS the board took the
        image and rebooted. It must not be diverted into a DFU upload."""
        self._cache_the_zip()
        result, _ = self._flash_refused(
            copy_file=self._refuse(errno_=6, msg="Device not configured"),
            run_dfu=lambda argv, t: self.fail(
                "ENODEV is success, not a reason to DFU"))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.stage_reached, flash.STAGE_DONE)
        self.assertEqual(self.dfu_runs, [])


class TestSerialDfuFallback(_DfuHarness):
    """Part 2: the way out of the wedge."""

    def test_marker_line_carries_on_to_port_wait_and_info(self):
        self._cache_the_zip()
        result, calls = self._flash(
            run_dfu=self._run_dfu(
                "########\nActivating new firmware\nDevice programmed.\n"))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(result.stage_reached, flash.STAGE_DONE)
        self.assertEqual(result.src_after, self.manifest["src"])
        # It really did run the uploader, and really did read info back.
        self.assertEqual(len(self.dfu_runs), 1)
        self.assertIn(("command", self.port, "info", flash._INFO_TIMEOUT_S),
                       calls)

    def test_the_exact_command_line(self):
        """--singlebank, 115200, and NO --touch: the board is already in
        DFU, PlatformIO's own uploader passes no touch for this board, and
        the 1200-baud touch was measured doing nothing to this bootloader."""
        self._cache_the_zip()
        self._flash(run_dfu=self._run_dfu("Device programmed."))
        argv, timeout_s = self.dfu_runs[0]
        self.assertEqual(argv[:3], ["/fake/adafruit-nrfutil", "dfu", "serial"])
        self.assertIn("--package", argv)
        self.assertEqual(argv[argv.index("--package") + 1],
                          str(self.tmp / self.zip_name))
        self.assertEqual(argv[argv.index("--port") + 1], self.port)
        self.assertEqual(argv[argv.index("-b") + 1], "115200")
        self.assertIn("--singlebank", argv)
        self.assertNotIn("--touch", argv)
        self.assertEqual(timeout_s, 180.0)

    def test_rc_zero_without_the_marker_is_a_failure(self):
        """bench-playbook.md:150-152. Measured again 2026-09-15: a serial
        DFU that died at packet 23 with "No data received on serial port"
        still exited 0. The return code is not the verdict."""
        self._cache_the_zip()
        result, _ = self._flash(
            run_dfu=self._run_dfu(
                "#######################\n"
                "Failed to upgrade target. Error is: No data received on "
                "serial port. Not able to proceed.\n", code=0))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_DFU_SERIAL)
        self.assertIn("No data received", result.error)
        self.assertIn("Device programmed.", result.error)

    def test_a_marker_in_a_timeout_message_is_not_invented(self):
        self._cache_the_zip()
        result, _ = self._flash(
            run_dfu=self._run_dfu(
                "adafruit-nrfutil did not finish within 180s", code=124))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_DFU_SERIAL)
        self.assertIn("did not finish", result.error)

    def test_a_programmed_board_that_never_answers_is_still_a_failure(self):
        """MEASURED ON THE BENCH 2026-09-15: `Device programmed.` and the
        puck stayed in its bootloader. Programmed is not "came back", and
        only stage 6 may say ok=True."""
        self._cache_the_zip()

        def scan_ports():
            # The bootloader's CDC node is there for the upload; nothing
            # comes back after it.
            return [] if self.dfu_runs else [self.port]

        result, _ = self._flash(
            run_dfu=self._run_dfu("Device programmed."),
            scan_ports=scan_ports,
            port_wait_s=5.0)
        self.assertEqual(len(self.dfu_runs), 1, "the upload must have run")
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_PORT_WAIT)

    def test_the_fallback_waits_for_the_bootloader_cdc_node(self):
        """MEASURED 2026-09-15: idProduct flipped to 0x0045 at 0.68 s and
        /dev/cu.usbmodem101 only came back at 0.91 s. A fallback fired at
        0.68 s died on "could not open port … [Errno 2]" — our impatience
        reading as "serial DFU does not work on this Mac"."""
        self._cache_the_zip()
        appearances = {"n": 0}

        def scan_ports():
            appearances["n"] += 1
            return [self.port] if appearances["n"] > 3 else []

        result, _ = self._flash(run_dfu=self._run_dfu("Device programmed."),
                                 scan_ports=scan_ports)
        self.assertTrue(result.ok, result.error)
        self.assertEqual(len(self.dfu_runs), 1)

    def test_a_node_that_never_comes_back_does_not_upload_into_nothing(self):
        self._cache_the_zip()
        result, _ = self._flash(
            scan_ports=lambda: [],
            run_dfu=lambda argv, t: self.fail("no port, no upload"))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_VOLUME_WAIT)
        self.assertTrue(any("never came back as a bootloader port" in l
                            for l in self.logs), self.logs)

    def test_the_zip_is_fetched_when_it_is_not_cached(self):
        fetched = []

        def fetch(site, name, dest):
            fetched.append((site, name, Path(dest)))
            p = Path(dest) / name
            p.write_bytes(self.zip_bytes)
            return p

        result, _ = self._flash(fetch_fn=fetch,
                                 run_dfu=self._run_dfu("Device programmed."))
        self.assertTrue(result.ok, result.error)
        self.assertEqual(fetched,
                          [("http://127.0.0.1:1", self.zip_name, self.tmp)])

    def test_a_cached_zip_is_not_fetched_again(self):
        self._cache_the_zip()
        result, _ = self._flash(run_dfu=self._run_dfu("Device programmed."))
        self.assertTrue(result.ok, result.error)   # fetch_fn would self.fail()


class TestSerialDfuIsGated(_DfuHarness):
    """G2's sha256 clause, applied to the second image."""

    def test_a_zip_whose_sha256_is_wrong_never_runs_and_is_discarded(self):
        self._cache_the_zip()
        bad = dict(self.manifest, dfu_sha256="f" * 64)
        calls = []
        clock = _FakeClock()
        result = flash.flash(
            self.port, self.uf2, bad,
            device_factory=_device_factory({}, calls),
            scan_ports=lambda: [],
            volume_exists=lambda: False,
            list_disks=lambda: "",
            mount_volume=lambda: None,
            copy_file=lambda s, d: self.fail("no copy"),
            sleep=clock.sleep, now=clock.now,
            volume_wait_s=5.0, poll_interval_s=1.0,
            ioreg_fn=self._ioreg(_IOREG_XIAO_BOOTLOADER),
            dfu_cache_dir=self.tmp,
            site_url="http://127.0.0.1:1",
            fetch_fn=lambda site, name, dest: self.fail("no fetch"),
            run_dfu=lambda argv, t: self.fail("must not upload an unverified zip"),
            nrfutil_path="/fake/adafruit-nrfutil",
            log=self.logs.append,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_VOLUME_WAIT)
        self.assertTrue(any("refusing the serial-DFU fallback (G2)" in line
                            for line in self.logs), self.logs)
        self.assertFalse((self.tmp / self.zip_name).exists(),
                          "a zip that failed the gate must not stay cached")

    def test_a_manifest_without_dfu_file_skips_quietly(self):
        plain = dict(REAL_MANIFEST, sha256=_sha256_of(self.uf2))
        calls = []
        clock = _FakeClock()
        result = flash.flash(
            self.port, self.uf2, plain,
            device_factory=_device_factory({}, calls),
            scan_ports=lambda: [],
            volume_exists=lambda: False,
            list_disks=lambda: "",
            mount_volume=lambda: None,
            copy_file=lambda s, d: self.fail("no copy"),
            sleep=clock.sleep, now=clock.now,
            volume_wait_s=5.0, poll_interval_s=1.0,
            ioreg_fn=self._ioreg(_IOREG_XIAO_BOOTLOADER),
            dfu_cache_dir=self.tmp,
            fetch_fn=lambda site, name, dest: self.fail("no fetch"),
            run_dfu=lambda argv, t: self.fail("nothing to upload"),
            log=self.logs.append,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_VOLUME_WAIT)
        self.assertIn("this Mac made no disk", result.error)
        self.assertEqual(
            [l for l in self.logs if "no dfu_file" in l or "names no dfu_file" in l],
            ["flash: manifest names no dfu_file — no serial-DFU fallback to try"])

    def test_a_manifest_without_dfu_sha256_refuses(self):
        self._cache_the_zip()
        no_hash = dict(self.manifest)
        no_hash.pop("dfu_sha256")
        got = flash.dfu_package(no_hash, self.tmp, "http://x",
                                 lambda *a: self.fail("no fetch"),
                                 self.logs.append)
        self.assertIsNone(got)
        self.assertTrue(any("no dfu_sha256" in l for l in self.logs), self.logs)

    def test_a_dfu_file_that_is_not_a_bare_zip_name_is_refused(self):
        for bad_name in ("../secrets.zip", "/etc/passwd", "firmware.uf2",
                          "a/b.zip"):
            logs = []
            got = flash.dfu_package(dict(self.manifest, dfu_file=bad_name),
                                     self.tmp, "http://x",
                                     lambda *a: self.fail("no fetch"),
                                     logs.append)
            self.assertIsNone(got, bad_name)

    def test_an_unfetchable_zip_leaves_the_volume_wait_verdict(self):
        result, _ = self._flash(
            fetch_fn=lambda site, name, dest: None,
            run_dfu=lambda argv, t: self.fail("nothing to upload"))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage_reached, flash.STAGE_VOLUME_WAIT)
        self.assertTrue(any("could not fetch" in l for l in self.logs), self.logs)


class TestSerialDfuHelper(unittest.TestCase):
    """serial_dfu() on its own."""

    def test_missing_nrfutil_is_reported_not_raised(self):
        """A Mac with no adafruit-nrfutil (Nick's, today) must get a log
        line and a FlashResult, never a traceback — and must not try to
        run anything."""
        with patch.object(flash, "_default_nrfutil_argv0", return_value=None):
            ok, detail = flash.serial_dfu(
                "/dev/cu.usbmodem101", "/tmp/x.zip",
                run_dfu=lambda argv, t: self.fail("must not run anything"))
        self.assertFalse(ok)
        self.assertIn("not installed on this Mac", detail)

    def test_default_runner_reports_a_missing_binary(self):
        code, out = flash._default_run_dfu(
            ["/definitely/not/here/adafruit-nrfutil", "dfu"], 5.0)
        self.assertEqual(code, 127)
        self.assertIn("could not run", out)

    def test_marker_anywhere_in_the_output_counts(self):
        ok, detail = flash.serial_dfu(
            "/dev/cu.usbmodem101", "/tmp/x.zip",
            nrfutil_path="/fake/nrfutil",
            run_dfu=lambda argv, t: (0, "noise\nDevice programmed.\nmore noise"))
        self.assertTrue(ok)
        self.assertIn("Device programmed.", detail)


class TestSiteUrlMatchesTheDaemon(unittest.TestCase):
    """CLAUDE.md §4: an identifier without a lookup entry is a rediscovery
    waiting to happen. flash.py restates four of daemon.py's/garmin.py's
    constants because it cannot import them (daemon imports flash). This
    test is the lookup entry: edit one side alone and it fails here."""

    def test_the_constants_agree(self):
        try:
            from puckd import daemon, garmin
        except Exception as exc:  # pragma: no cover - optional dependency
            self.skipTest(f"daemon/garmin not importable here: {exc!r}")
        self.assertEqual(flash.DEFAULT_SITE_URL, daemon.DEFAULT_SITE_URL)
        self.assertEqual(flash.SITE_URL_ENV, daemon.SITE_URL_ENV)
        self.assertEqual(flash._LOG_FILENAME, daemon.LOG_FILENAME)
        self.assertEqual(flash._DEFAULT_HOME, garmin.DEFAULT_HOME)
        self.assertEqual(flash._PUCKD_HOME_ENV, garmin.PUCKD_HOME_ENV)
        self.assertEqual(flash.puckd_home(), garmin.puckd_home())


class InvokedAsAModuleInsideTheBundle(unittest.TestCase):
    """The shipped app has no adafruit-nrfutil script; the nordicsemi package
    is vendored and run with the bundle's own interpreter."""

    def test_module_form_when_nordicsemi_imports(self):
        import sys
        argv0 = flash._default_nrfutil_argv0()
        self.assertIsNotNone(argv0)
        self.assertEqual(argv0[:1], [sys.executable])
        self.assertEqual(argv0[1:], ["-m", "nordicsemi"])

    def test_serial_dfu_builds_argv_from_the_module_form(self):
        seen = {}
        def run(argv, t):
            seen["argv"] = argv; return 0, "Device programmed."
        ok, _ = flash.serial_dfu("/dev/cu.usbmodemX", "/tmp/pkg.zip", run_dfu=run)
        self.assertTrue(ok)
        self.assertEqual(seen["argv"][1:5], ["-m", "nordicsemi", "dfu", "serial"])


class LatestManifestSaysWhyItFailed(unittest.TestCase):
    """latest_manifest() keeps its contract (never raises, None on any
    failure) and now also records which failure, for the daemon's log."""

    def test_an_unreachable_site_records_the_error(self):
        self.assertIsNone(flash.latest_manifest("http://127.0.0.1:1"))
        why = flash.last_manifest_error()
        self.assertIsNotNone(why)
        self.assertIn("127.0.0.1:1", why)

