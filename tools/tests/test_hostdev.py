"""Host-platform integration tests: the REAL C++ firmware core, running
natively on this machine (firmware/platformio.ini's env:host), driven over
its actual stdin/stdout protocol — no fake device, no simulator.

Every other test in this repo either drives tools/fake_device.py (a Python
stand-in for the protocol, tools/tests/test_cli.py) or the dependency-free
jump_detector.h in isolation (firmware/test/host_test.cpp). This file is the
first thing that ever runs the shared core's OWN command dispatch, self-test
orchestration, motion gate, and emit layer — the code in firmware/src/main.cpp
itself — off real hardware.

Requires PlatformIO (`pio`); every test class here is SKIPPED (not failed) if
it can't be found, since this machine's software test suite must still pass
with zero embedded toolchain installed (see tools/jump simtest's own g++
skip for the same policy applied to firmware/test/host_test.cpp).

All test classes are unittest.TestCase subclasses (not bare/pytest-fixture
classes): `./tools/jump simtest` drives this whole repo's Python tests via
`python -m unittest discover`, which silently never collects non-TestCase
classes at all — and a module-level `pytest.skip(allow_module_level=True)`
is a hard collection ERROR under plain `unittest`, not a skip. Moving the
"pio missing" skip into each class's setUpClass (raising unittest.SkipTest)
fixes both: it's a real skip under unittest AND under pytest (which defers
to unittest's own protocol for TestCase-derived tests), and simtest's
`python -m unittest discover` now actually runs these tests when pio IS
available, instead of quietly never collecting them.

Run via ./tools/jump simtest, or directly:
    python3 -m pytest tools/tests/test_hostdev.py -q
    python3 -m unittest discover -s tools/tests -p "test_hostdev.py"
"""

from __future__ import annotations

import base64
import itertools
import os
import re
import select
import shutil
import string
import subprocess
import sys
import tempfile
import time
import unittest
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
FIRMWARE_DIR = REPO / "firmware"
FW_VERSION = "0.4.3"  # firmware/src/main.cpp's FW_VERSION

# The `traceraw` tests below decode the device's raw export with the SAME
# module the phone page and `jump ingest` decode it with — the point of the
# exercise is that one decoder serves every client, so importing a second
# implementation here would test the wrong thing.
sys.path.insert(0, str(REPO / "sim"))

import trace_codec  # noqa: E402  (path insert must come first)


def _find_pio() -> list[str] | None:
    """argv that runs PlatformIO, or None — mirrors tools/jump's find_pio()
    (kept independent/self-contained here rather than imported, matching how
    tools/tests/test_trace_codec.py doesn't import tools/jump either)."""
    for cand in ("pio", "platformio"):
        p = shutil.which(cand)
        if p:
            return [p]
    candidates = [Path.home() / ".local" / "bin"]
    try:
        import sysconfig

        d = sysconfig.get_path("scripts", f"{os.name}_user")
        if d:
            candidates.append(Path(d))
    except (ImportError, KeyError):
        pass
    for d in candidates:
        for cand in ("pio", "platformio"):
            f = d / cand
            if f.exists():
                return [str(f)]
    penv = Path.home() / ".platformio" / "penv" / "bin" / "pio"
    if penv.exists():
        return [str(penv)]
    return None


PIO = _find_pio()  # module-level lookup only — no module-level skip here;
                    # see HostDevTestCase.setUpClass for the actual skip.

_host_binary_cache: Path | None = None


def _get_host_binary() -> Path:
    """Builds env:host once (pio run -e host) and returns the binary path,
    cached at module scope so every TestCase class shares a single build —
    the same "once per process" effect pytest's session-scoped fixture used
    to give, without depending on pytest's fixture machinery. Only called
    when PIO is known to be present; a build failure here is a real bug
    (asserted), not something to skip over."""
    global _host_binary_cache
    if _host_binary_cache is None:
        assert PIO is not None
        r = subprocess.run(PIO + ["run", "-d", str(FIRMWARE_DIR), "-e", "host"],
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, f"pio run -e host failed:\n{r.stdout}\n{r.stderr}"
        binp = FIRMWARE_DIR / ".pio" / "build" / "host" / "program"
        assert binp.exists(), f"expected host binary not found at {binp}"
        _host_binary_cache = binp
    return _host_binary_cache


# --------------------------------------------------------------- kv parsing


def parse_kv(line: str) -> dict:
    """'JUMP n=1 airtime_s=0.62' -> {'_tag': 'JUMP', 'n': '1', 'airtime_s': '0.62'}."""
    parts = line.split()
    out: dict = {"_tag": parts[0] if parts else ""}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k] = v
    return out


# ------------------------------------------------------------- the device


class HostDevice:
    """A running instance of the host-platform binary, talking the real
    firmware protocol over its stdin/stdout — the host-build equivalent of
    tools/jump's own Device class, minus the pyserial/pty plumbing (plain
    pipes suffice: the C++ side's Serial shim is unbuffered stdin/stdout)."""

    def __init__(self, binp: Path, host_dir: Path, script_path: Path,
                 extra_env: dict | None = None):
        host_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["JH_HOST_DIR"] = str(host_dir)
        env["JH_IMU_SCRIPT"] = str(script_path)
        if extra_env:
            env.update(extra_env)
        self.proc = subprocess.Popen(
            [str(binp)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            cwd=str(REPO), env=env)
        self._buf = b""
        # Everything the device has said, in order. wait_for() drops
        # non-matching lines on the floor, which makes narration lines
        # BETWEEN two tagged lines unassertable without this.
        self.seen: list[str] = []

    def write_line(self, s: str) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((s + "\n").encode())
        self.proc.stdin.flush()

    def read_line(self, timeout: float) -> str | None:
        """Next complete line (without the newline), or None on timeout/EOF."""
        assert self.proc.stdout is not None
        deadline = time.monotonic() + timeout
        while True:
            nl = self._buf.find(b"\n")
            if nl >= 0:
                line = self._buf[:nl]
                self._buf = self._buf[nl + 1:]
                text = line.decode(errors="replace")
                self.seen.append(text)
                return text
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            r, _, _ = select.select([self.proc.stdout], [], [], min(remaining, 0.2))
            if r:
                chunk = os.read(self.proc.stdout.fileno(), 4096)
                if not chunk:
                    return None  # EOF: process exited
                self._buf += chunk

    def drain_boot(self, timeout: float = 8.0) -> list[str]:
        """Read the boot banner/SELFTEST/READY sequence."""
        lines: list[str] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.read_line(deadline - time.monotonic())
            if line is None:
                break
            lines.append(line)
            if line.strip() == "READY":
                break
        return lines

    def command(self, cmd: str, timeout: float = 10.0) -> list[str]:
        """Send a command, return every line up to its OK/ERR terminator."""
        verb = cmd.split()[0] if cmd.split() else cmd
        self.write_line(cmd)
        lines: list[str] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.read_line(deadline - time.monotonic())
            if line is None:
                raise TimeoutError(f"device went silent during {cmd!r}")
            lines.append(line)
            if line.strip() == f"OK {verb}" or line.startswith("ERR"):
                return lines
        raise TimeoutError(f"timed out waiting for {cmd!r} to finish")

    def wait_for(self, tag: str, timeout: float = 10.0) -> str | None:
        """Next machine line with the given tag (e.g. 'JUMP'), or None."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.read_line(deadline - time.monotonic())
            if line is None:
                return None
            if line.startswith(tag + " ") or line.strip() == tag:
                return line
        return None

    def close(self) -> None:
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except Exception:
            try:
                self.proc.kill()
                self.proc.wait(timeout=5)
            except Exception:
                pass


def write_script(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def _load_jump_module():
    """Load tools/jump (no .py extension) as an importable module — same
    technique tools/tests/test_cli.py uses, kept local here so this file
    stays self-contained."""
    import importlib.machinery
    import types

    jump_path = str(REPO / "tools" / "jump")
    loader = importlib.machinery.SourceFileLoader("jumpcli_ut_hostdev", jump_path)
    mod = types.ModuleType("jumpcli_ut_hostdev")
    mod.__file__ = jump_path
    loader.exec_module(mod)
    return mod


# ------------------------------------------------------------ base TestCase


class HostDevTestCase(unittest.TestCase):
    """Shared setUpClass/setUp for every TestCase below: skip the whole class
    if PlatformIO isn't installed (both `python -m pytest` and `python -m
    unittest discover` must exit clean in that case — the latter is what
    `./tools/jump simtest` actually runs), else build env:host once (cached
    at module scope) and hand out a fresh tmp_path-equivalent per test,
    since plain unittest has no `tmp_path` fixture of its own."""

    host_binary: Path

    @classmethod
    def setUpClass(cls) -> None:
        if PIO is None:
            raise unittest.SkipTest(
                "PlatformIO (pio) is not available on this machine — "
                "skipping host-platform integration tests")
        cls.host_binary = _get_host_binary()

    def setUp(self) -> None:
        self.tmp_path = Path(tempfile.mkdtemp(prefix="jh_hostdev_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_path, ignore_errors=True)


# --------------------------------------------------------------------- tests


class TestBootAndInfo(HostDevTestCase):
    def test_boot_sequence(self) -> None:
        script = write_script(self.tmp_path / "script.txt", "rest 5.0\n")
        dev = HostDevice(self.host_binary, self.tmp_path / "hostdir", script)
        try:
            lines = dev.drain_boot()
            joined = "\n".join(lines)
            self.assertIn(f"# JumpHeight fw v{FW_VERSION}", joined)
            self.assertIn("SELFTEST BEGIN", joined)
            self.assertIn("SELFTEST END result=PASS", joined)
            self.assertEqual(lines[-1].strip(), "READY")
        finally:
            dev.close()

    def test_info_reports_fw_and_expected_keys(self) -> None:
        script = write_script(self.tmp_path / "script.txt", "rest 5.0\n")
        dev = HostDevice(self.host_binary, self.tmp_path / "hostdir", script)
        try:
            dev.drain_boot()
            lines = dev.command("info")
            info_line = next((ln for ln in lines if ln.startswith("INFO ")), None)
            self.assertIsNotNone(info_line, f"no INFO line in {lines}")
            kv = parse_kv(info_line)
            self.assertEqual(kv["fw"], FW_VERSION)
            for key in ("sample_hz", "log_hz", "motion_thresh_g", "idle_timeout_s", "ble"):
                self.assertIn(key, kv, f"INFO line missing {key!r}: {info_line}")
            self.assertTrue(any(ln.strip() == "OK info" for ln in lines))
        finally:
            dev.close()


class TestCalibrationPersistence(HostDevTestCase):
    def test_set_persists_across_process_restart(self) -> None:
        host_dir = self.tmp_path / "hostdir"
        script = write_script(self.tmp_path / "script.txt", "rest 5.0\n")

        dev1 = HostDevice(self.host_binary, host_dir, script)
        try:
            dev1.drain_boot()
            resp = dev1.command("set airtime_offset_s 0.02")
            self.assertFalse(any(ln.startswith("ERR") for ln in resp), resp)
        finally:
            dev1.close()

        # A brand-new process, same JH_HOST_DIR: the calibration must have
        # survived the restart, exactly like NVS surviving a reboot.
        dev2 = HostDevice(self.host_binary, host_dir, script)
        try:
            dev2.drain_boot()
            lines = dev2.command("info")
            cal_line = next((ln for ln in lines if ln.startswith("CAL ")), None)
            self.assertIsNotNone(cal_line, f"no CAL line in {lines}")
            kv = parse_kv(cal_line)
            self.assertEqual(kv["source"], "device")
            self.assertLess(abs(float(kv["airtime_offset_s"]) - 0.02), 1e-4)
        finally:
            dev2.close()


class TestJumpDetectionAndStorage(HostDevTestCase):
    def test_scripted_jump_detected_and_stored(self) -> None:
        # rest -> a toss-style free-fall -> rest, per the script mini-language
        # in firmware/src/platform/host/jh_imu.cpp.
        freefall_s = 0.65
        script = write_script(self.tmp_path / "script.txt",
                              f"rest 2.0\njump {freefall_s}\nrest 2.0\n")
        host_dir = self.tmp_path / "hostdir"
        dev = HostDevice(self.host_binary, host_dir, script)
        try:
            boot = dev.drain_boot()
            self.assertTrue(boot and boot[-1].strip() == "READY")

            jump_line = dev.wait_for("JUMP", timeout=10.0)
            self.assertIsNotNone(jump_line, "no JUMP line seen from the scripted toss")
            kv = parse_kv(jump_line)
            airtime_raw = float(kv["airtime_raw_s"])
            self.assertLessEqual(
                abs(airtime_raw - freefall_s), 0.1,
                f"airtime_raw_s={airtime_raw} not within 0.1s of scripted {freefall_s}")
            # F-20: `> 0.0` was too loose to catch the host store parsing the
            # WRONG COLUMN, because n_air (an in-air sample count, ~114 here) is
            # also positive. Pin the height to the physics instead: for a
            # ballistic flight h = g*t^2/8, so a 0.676 s airtime is ~0.56 m.
            # 0.56 vs 114 is the discriminator the old assert threw away.
            height = float(kv["height_m"])
            airtime = float(kv["airtime_s"])
            expected_h = 9.80665 * airtime * airtime / 8.0
            self.assertLessEqual(
                abs(height - expected_h), 0.01,
                f"height_m={height} does not match g*t^2/8={expected_h:.3f} "
                f"for airtime_s={airtime}")

            lines = dev.command("jumps")
            self.assertTrue(any(ln.startswith("FILE jumps.csv BEGIN") for ln in lines))
            self.assertTrue(any(ln.startswith("FILE jumps.csv END") for ln in lines))
            data_rows = [ln for ln in lines if "," in ln and not ln.startswith(("n,", "#", "FILE"))]
            self.assertEqual(len(data_rows), 1, f"expected exactly one stored jump row, got {lines}")
            cells = data_rows[0].split(",")
            self.assertEqual(len(cells), 9,
                             "jumps.csv is a NINE-column schema; a reader that "
                             "counts back from the end breaks when a column is "
                             "appended (F-20): " + data_rows[0])
            self.assertEqual(cells[0], "1")  # n
            self.assertLessEqual(abs(float(cells[2]) - freefall_s), 0.1)  # airtime_raw_s
            self.assertLessEqual(abs(float(cells[4]) - height), 0.001,
                                 "field 4 must be height_m: " + data_rows[0])

        finally:
            dev.close()

    def test_stored_best_survives_restart_as_a_height_not_a_sample_count(self) -> None:
        """F-20: the host jumps_scan() took find_last_of(',') and parsed the
        TAIL as height_m — true only while the schema had five columns. It has
        had nine since the flight medians were appended, so "best" on the host
        was n_air, an in-air SAMPLE COUNT (~114 here), reported in metres.

        This needs a process RESTART to see. main.cpp keeps stored_best in RAM
        and updates it from the live JumpEvent; jumps_scan() only runs at boot
        (and on mount/clear), so an in-process `stats` reads the RAM copy and
        the parser is never exercised. An earlier version of this test asserted
        in-process and passed against the known-broken parser.
        """
        freefall_s = 0.65
        script = write_script(self.tmp_path / "script.txt",
                              f"rest 2.0\njump {freefall_s}\nrest 2.0\n")
        host_dir = self.tmp_path / "hostdir"

        dev = HostDevice(self.host_binary, host_dir, script)
        try:
            self.assertTrue(dev.drain_boot())
            jump_line = dev.wait_for("JUMP", timeout=10.0)
            self.assertIsNotNone(jump_line, "no JUMP line from the scripted toss")
            height = float(parse_kv(jump_line)["height_m"])
            rows = [ln for ln in dev.command("jumps")
                    if "," in ln and not ln.startswith(("n,", "#", "FILE"))]
            self.assertEqual(len(rows), 1, rows)
            n_air = float(rows[0].split(",")[8])
        finally:
            dev.close()

        # The discriminator: these two must be far apart or the test proves
        # nothing about WHICH column was read.
        self.assertGreater(n_air, 10.0,
                           f"n_air={n_air} too close to height={height} for this "
                           f"test to distinguish the columns: {rows[0]}")

        # Fresh process, same store directory: boot re-derives stored_best by
        # PARSING the file, which is the code path under test.
        dev2 = HostDevice(self.host_binary, host_dir, script)
        try:
            self.assertTrue(dev2.drain_boot())
            stats_line = next((ln for ln in dev2.command("stats")
                               if ln.startswith("STATS ")), None)
            self.assertIsNotNone(stats_line)
            kv = parse_kv(stats_line)
            self.assertEqual(int(kv["stored_jumps"]), 1, stats_line)
            best = float(kv["stored_best_m"])
            self.assertLessEqual(
                abs(best - height), 0.001,
                f"stored_best_m={best} after restart should be the jump height "
                f"{height}, not n_air={n_air} — the host store is parsing the "
                f"wrong column (F-20)")
        finally:
            dev2.close()


class TestPtyBridge(HostDevTestCase):
    """STRETCH: tools/hostdev.py bridges the real host-platform core onto a
    pty. One smoke assertion: drive it through tools/jump's OWN low-level
    transport (SerialPort/Device — the exact classes a real board's
    ./tools/jump session uses to talk over USB serial), proving the bridge
    is indistinguishable from real hardware at the byte level.

    Deliberately NOT routed through `./tools/jump selftest --port <pty>`
    itself: that command's open_device() validates any --port against
    scan_ports() (real USB devices only — pyserial-enumerated VIDs, or
    /dev/ttyUSB*/ttyACM*/cu.* globs) before ever opening it, discarding
    anything else and falling back to autodetection; a bridged pty can never
    appear in that list, so the literal CLI invocation fails with no real
    hardware attached. That gate lives in tools/jump itself (outside this
    port's file list — new files + the platformio.ini env addition only)
    and is orthogonal to whether the bridge/transport actually works — see
    tools/hostdev.py's own docstring for the full story.
    """

    def test_selftest_over_the_bridged_pty(self) -> None:
        script = write_script(self.tmp_path / "script.txt", "rest 5.0\n")
        bridge = subprocess.Popen(
            [sys.executable, str(REPO / "tools" / "hostdev.py"),
             "--bin", str(self.host_binary), "--host-dir", str(self.tmp_path / "hostdir"),
             "--script", str(script)],
            stdout=subprocess.PIPE, text=True)
        try:
            first = bridge.stdout.readline().strip()
            self.assertTrue(first.startswith("PTY "), f"bridge failed to start: {first!r}")
            port = first.split(None, 1)[1]

            mod = _load_jump_module()
            dev = mod.Device(port)  # the real transport a real board uses
            try:
                dev.drain_boot(timeout=5.0)
                lines = dev.command("selftest", timeout=15)
                ok = mod.render_selftest(lines)
                self.assertTrue(ok, f"selftest over the bridged pty did not PASS: {lines}")
            finally:
                dev.close()
        finally:
            bridge.terminate()
            try:
                bridge.wait(timeout=5)
            except Exception:
                bridge.kill()


class TestBatteryTelemetry(HostDevTestCase):
    """The jh_power seam's adder-rule contract (docs/sense.md §3.4):
    battery keys appear on INFO/STATS exactly when the platform can
    measure — scripted here via JH_VBAT_MV/JH_CHG (the host seam's env
    hooks, src/platform/host/jh_power.cpp) — and are BYTE-ABSENT
    otherwise, keeping the pre-battery protocol untouched for the ESP32
    build and every v1 client."""

    def _line(self, dev: HostDevice, cmd: str, prefix: str) -> str:
        lines = dev.command(cmd)
        line = next((ln for ln in lines if ln.startswith(prefix)), None)
        self.assertIsNotNone(line, f"no {prefix!r} line in {lines}")
        return line

    def test_keys_absent_by_default(self) -> None:
        script = write_script(self.tmp_path / "script.txt", "rest 5.0\n")
        dev = HostDevice(self.host_binary, self.tmp_path / "hostdir", script)
        try:
            dev.drain_boot()
            for cmd, prefix in (("stats", "STATS "), ("info", "INFO ")):
                kv = parse_kv(self._line(dev, cmd, prefix))
                for key in ("vbat_mv", "batt_pct", "chg"):
                    self.assertNotIn(key, kv,
                                     f"{prefix.strip()} grew {key!r} without battery support")
        finally:
            dev.close()

    def test_keys_present_and_sane_when_scripted(self) -> None:
        script = write_script(self.tmp_path / "script.txt", "rest 5.0\n")
        dev = HostDevice(self.host_binary, self.tmp_path / "hostdir", script,
                         extra_env={"JH_VBAT_MV": "3870", "JH_CHG": "1"})
        try:
            dev.drain_boot()
            for cmd, prefix in (("stats", "STATS "), ("info", "INFO ")):
                kv = parse_kv(self._line(dev, cmd, prefix))
                self.assertEqual(kv["vbat_mv"], "3870")
                # Host curve is linear 3300→0 .. 4160→100 (see the host
                # seam's own comment — 4160 is the nrf52 curve's
                # rested-full top anchor, SENSE_FIRST_BOOT.md item 24):
                # 3870 ⇒ 66. Pinned exactly so an accidental
                # curve/plumbing change fails loudly.
                self.assertEqual(kv["batt_pct"], "66")
                self.assertEqual(kv["chg"], "1")
        finally:
            dev.close()


class TestOffCommand(HostDevTestCase):
    """`off` (jh_power::system_off): on a battery platform it must farewell,
    terminate with OK, then go silent — never return; on a v1-like platform
    it answers a clean ERR. Never a mixed OK-then-ERR (client framing)."""

    def test_off_unsupported_answers_err(self) -> None:
        script = write_script(self.tmp_path / "script.txt", "rest 5.0\n")
        dev = HostDevice(self.host_binary, self.tmp_path / "hostdir", script)
        try:
            dev.drain_boot()
            lines = dev.command("off")
            self.assertTrue(any(ln.startswith("ERR off_unsupported") for ln in lines),
                            f"expected ERR off_unsupported, got {lines}")
            self.assertFalse(any(ln.startswith("OK off") for ln in lines),
                             f"OK and ERR must never both appear: {lines}")
        finally:
            dev.close()

    def test_off_on_battery_platform_farewells_then_exits(self) -> None:
        script = write_script(self.tmp_path / "script.txt", "rest 5.0\n")
        dev = HostDevice(self.host_binary, self.tmp_path / "hostdir", script,
                         extra_env={"JH_VBAT_MV": "3900"})
        try:
            dev.drain_boot()
            dev.write_line("off")
            got: list[str] = []
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                ln = dev.read_line(timeout=0.5)
                if ln is None:
                    if dev.proc.poll() is not None:
                        break  # EOF + process gone: the intended silence
                    continue
                got.append(ln)
                if ln.strip() == "OK off":
                    break
            joined = "\n".join(got)
            self.assertIn("powering down", joined)
            self.assertTrue(any(ln.strip() == "OK off" for ln in got), joined)
            dev.proc.wait(timeout=5)
            self.assertEqual(dev.proc.returncode, 0,
                             "off must end the host device cleanly (exit 0)")
        finally:
            dev.close()


if __name__ == "__main__":
    unittest.main()


class TestStorageRefusalIsVisible(HostDevTestCase):
    """F-10: jumps_append() refusals were bare returns and main.cpp incremented
    stored_jumps regardless.

    Driven through a read-only store directory, which is the one refusal path
    the host platform can actually produce (its init() cannot fail, and it has
    no region cap — the REGION_FULL path is device-only and is covered against
    the real nrf52 store in tools/tests/test_store_host.py).
    """

    def test_write_failure_is_reported_once_and_not_counted(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("root ignores directory permissions, so no write can fail")

        host_dir = self.tmp_path / "readonly_store"
        host_dir.mkdir()
        os.chmod(host_dir, 0o500)   # readable + traversable, NOT writable

        script = write_script(self.tmp_path / "script.txt",
                              "rest 2.0\njump 0.65\nrest 2.0\njump 0.65\nrest 2.0\n")
        dev = HostDevice(self.host_binary, host_dir, script)
        try:
            boot = dev.drain_boot()
            self.assertTrue(boot and boot[-1].strip() == "READY")

            first = dev.wait_for("JUMP", timeout=10.0)
            self.assertIsNotNone(first, "the detector must still report the jump")
            second = dev.wait_for("JUMP", timeout=10.0)
            self.assertIsNotNone(second, "second scripted toss never detected")

            stats = dev.command("stats")
            stats_line = next((ln for ln in stats if ln.startswith("STATS ")), None)
            self.assertIsNotNone(stats_line, f"no STATS line: {stats}")
            kv = parse_kv(stats_line)

            # The jumps happened, so the SESSION count must advance — the puck
            # is not allowed to under-report the ride just because its flash is
            # unwritable.
            self.assertEqual(int(kv["session_jumps"]), 2, stats_line)

            # But nothing reached storage, so the STORED count must not move.
            # This is the actual F-10 defect: it used to read 2.
            self.assertEqual(int(kv["stored_jumps"]), 0,
                             "stored_jumps counted records the store refused: " + stats_line)

            # And the rider is told once, not once per jump — two jumps
            # both refused must not produce two identical warnings.
            transcript = "\n".join(dev.seen)
            n_warnings = transcript.count("# jump NOT saved")
            self.assertEqual(n_warnings, 1,
                             f"expected exactly one refusal warning, got {n_warnings}:\n{transcript}")
            self.assertIn("flash write failed", transcript,
                          "the warning must name the actual reason")
        finally:
            dev.close()
            os.chmod(host_dir, 0o700)   # before tearDown's rmtree


class TestTraceRawExport(HostDevTestCase):
    """`traceraw` — the same stored trace `trace` sends, streamed as the
    store's own binary blocks in base64 instead of ~17-byte CSV rows.

    It exists for the rider-sync path: a phone pulling a full session over
    BLE waits on the wire, and the binary form is roughly 2.7 bytes/sample
    (~2 stored, +33% for base64) against the CSV's ~17. So the bar here is
    not "it produces output" — it is that a client can VERIFY what it got
    (byte count + CRC-32) and decode it back to exactly the rows `trace`
    would have sent.

    This drives the real firmware path natively (framing, base64, the
    streaming CRC-32, the reliable-export bracket in
    firmware/src/main.cpp's printTraceRawFramed). The host store keeps CSV
    text rather than trace_codec blocks, so its open_read_raw() synthesizes a
    region image laid out exactly the way the nRF52 store lays one out
    (firmware/src/platform/host/jh_store.cpp) — what goes over the wire is a
    genuine trace region, decoded here by the same sim/trace_codec.py the
    phone page and `jump ingest` use.
    """

    # The wire contract, as regexes rather than prose: any drift in the
    # chatter lines breaks the page's progress bar and its verification, so
    # these are deliberately anchored and exact.
    CHATTER_RE = re.compile(r"^# traceraw bytes=(\d+) log_hz=(\d+) region_bytes=(\d+)$")
    CRC_RE = re.compile(r"^# traceraw crc32=([0-9a-f]{8}) bytes=(\d+)$")
    B64_CHARS = set(string.ascii_letters + string.digits + "+/")

    def _parse_traceraw(self, lines: list[str]) -> dict:
        """Parse one `traceraw` response STRICTLY per the wire contract, and
        return {raw, bytes, log_hz, region_bytes, crc32}.

        Strict on purpose. Every check below is something a client depends
        on: `tools/jump`'s parse_file_sections matches FILE lines by
        `startswith("FILE ")` + `endswith(" BEGIN"/" END")`, so an extra
        token on those lines silently changes the file NAME it thinks it is
        reading; a body line that isn't a whole number of base64 groups
        decodes to different bytes; '=' padding anywhere but the very end
        means the stream was cut and re-padded mid-file.
        """
        text = [ln.rstrip("\r") for ln in lines]

        chatter = [m for m in (self.CHATTER_RE.match(ln) for ln in text) if m]
        self.assertEqual(len(chatter), 1,
                         f"expected exactly one traceraw header line:\n" + "\n".join(text))
        crc_m = [m for m in (self.CRC_RE.match(ln) for ln in text) if m]
        self.assertEqual(len(crc_m), 1,
                         f"expected exactly one traceraw crc32 line:\n" + "\n".join(text))

        file_lines = [(i, ln) for i, ln in enumerate(text) if ln.startswith("FILE ")]
        self.assertEqual([ln for _, ln in file_lines],
                         ["FILE trace.bin BEGIN", "FILE trace.bin END"],
                         "the FILE frame lines must carry NO extra tokens — "
                         "every existing parser matches them by prefix/suffix")
        begin_i, end_i = file_lines[0][0], file_lines[1][0]

        self.assertLess(text.index(chatter[0].string), begin_i,
                        "bytes= must arrive before the frame: it is what a "
                        "client sizes its progress bar and its check with")
        self.assertGreater(text.index(crc_m[0].string), end_i,
                           "crc32= is computed over the streamed bytes, so it "
                           "can only be honest after the frame closes")
        self.assertEqual(text[-1].strip(), "OK traceraw",
                         "the response must end on its OK terminator")
        self.assertFalse([ln for ln in text if "INCOMPLETE" in ln],
                         "the device reported an incomplete export:\n" + "\n".join(text))

        body = text[begin_i + 1:end_i]
        for i, ln in enumerate(body):
            last = i == len(body) - 1
            if last:
                self.assertTrue(0 < len(ln) <= 76,
                                f"final body line has length {len(ln)}: {ln!r}")
            else:
                self.assertEqual(len(ln), 76,
                                 f"body line {i} is {len(ln)} chars, not 76: {ln!r}")
            self.assertEqual(len(ln) % 4, 0,
                             f"body line {i} is not a whole number of base64 groups")
            stripped = ln.rstrip("=")
            self.assertLessEqual(len(ln) - len(stripped), 2,
                                 f"body line {i} has more than two '=' pad chars")
            if not last:
                self.assertEqual(stripped, ln,
                                 f"'=' padding on non-final body line {i}: {ln!r}")
            self.assertTrue(set(stripped) <= self.B64_CHARS,
                            f"non-base64 characters in body line {i}: {ln!r}")

        raw = base64.b64decode("".join(body), validate=True)
        declared = int(chatter[0].group(1))
        crc_hex, crc_bytes = crc_m[0].group(1), int(crc_m[0].group(2))
        self.assertEqual(len(raw), declared,
                         "decoded byte count must equal the announced bytes=")
        self.assertEqual(crc_bytes, declared,
                         "the two bytes= values (before and after the frame) "
                         "must agree, or a client cannot tell which to trust")
        self.assertEqual(f"{zlib.crc32(raw) & 0xFFFFFFFF:08x}", crc_hex,
                         "crc32 must be CRC-32/ISO-HDLC over the raw bytes — "
                         "exactly Python's zlib.crc32")
        return {
            "raw": raw,
            "bytes": declared,
            "log_hz": int(chatter[0].group(2)),
            "region_bytes": int(chatter[0].group(3)),
            "crc32": crc_hex,
        }

    @staticmethod
    def _file_body(lines: list[str], name: str) -> list[str]:
        """The rows inside a `FILE <name> BEGIN/END` frame, header dropped."""
        begin = lines.index(f"FILE {name} BEGIN")
        end = lines.index(f"FILE {name} END")
        return [ln for ln in lines[begin + 1:end] if ln and ln != "t,mag"]

    def test_traceraw_streams_a_verifiable_trace_that_decodes_to_the_csv(self) -> None:
        script = write_script(self.tmp_path / "script.txt",
                              "rest 2.0\njump 0.65\nrest 2.0\n")
        dev = HostDevice(self.host_binary, self.tmp_path / "hostdir", script)
        try:
            self.assertTrue(dev.drain_boot())
            self.assertIsNotNone(dev.wait_for("JUMP", timeout=10.0),
                                 "no JUMP from the scripted toss — no trace to export")
            # Let a few whole seconds of trace accumulate before exporting.
            # MEASURED NECESSITY, not padding of the test: exporting straight
            # after the JUMP gave a region of two PARTIAL blocks, and a block
            # is 6 + 2*count bytes — odd counts land on a 4-byte boundary by
            # themselves. Deleting the image builder's align4 padding
            # entirely still passed this test in that state. Full seconds at
            # 50 Hz give even counts, which need the padding; the assertion
            # below then fails loudly if a run somehow gets none anyway,
            # rather than going quietly green on a fixture that cannot see
            # the bug. (The motion gate stays open for JH_IDLE_TIMEOUT_S
            # after the last movement, so the trace keeps growing here and
            # the device emits nothing that could fill the pipe.)
            time.sleep(3.0)

            info = next(ln for ln in dev.command("info") if ln.startswith("INFO "))
            info_log_hz = int(parse_kv(info)["log_hz"])

            got = self._parse_traceraw(dev.command("traceraw"))
            self.assertGreater(got["bytes"], 0,
                               "the scripted ride must have stored a trace")
            self.assertEqual(got["log_hz"], info_log_hz,
                             "traceraw's log_hz is what the client reconstructs "
                             "sample times with; it must match INFO's")
            # Capacity can never be less than what is stored. (On the host
            # store the two are equal — the synthesized image IS the used
            # prefix; on the nRF52 they are genuinely independent.)
            self.assertGreaterEqual(got["region_bytes"], got["bytes"])

            res = trace_codec.decode_region_recovering(got["raw"], log_hz=got["log_hz"])
            self.assertEqual(res.consumed, got["bytes"],
                             "the decoder must consume every exported byte — "
                             "stopping early is how a session loses its tail")
            self.assertEqual(res.damaged_patches, 0,
                             "nothing tore here; damage would mean the image is malformed")

            # --- and it is the SAME trace `trace` returns ---
            # Sampling continues between the two commands, so `trace` sees the
            # raw export's rows plus whatever arrived after it; compare on the
            # common prefix.
            rendered = trace_codec.region_to_csv(got["raw"], log_hz=got["log_hz"]).splitlines()
            trace_rows = self._file_body(dev.command("trace"), "trace.csv")
            self.assertGreater(len(rendered), 0)
            self.assertGreaterEqual(len(trace_rows), len(rendered))
            prefix = trace_rows[:len(rendered)]

            # Magnitudes are preserved EXACTLY (milli-g quantization cannot
            # move a 3-decimal value), so compare that column byte for byte.
            self.assertEqual([r.split(",")[1] for r in rendered],
                             [r.split(",")[1] for r in prefix],
                             "the magnitude column must survive the round trip intact")

            # Timestamps are not compared row-for-row against `trace`, and
            # that is the format's documented trade, not a defect: a block
            # stores ONE t0 and the decoder rebuilds the rest at exactly
            # 1/log_hz spacing (trace_codec.h, "Per-sample time
            # reconstruction"). The host build samples off a real clock, so
            # its rows carry real jitter — measured while writing this test,
            # on this machine: 3 of 135 rows differed from the reconstructed
            # time by 1-8 ms (e.g. logged 1.643, reconstructed 1.640), and a
            # separate run logged two consecutive rows both rounding to
            # 2.400 s. A row-for-row timestamp comparison would therefore be
            # flaky here and would be testing the clock. What must hold is
            # that the export is those same rows put through that same
            # transform, so re-encode them here with the Python mirror and
            # require the bytes to match. That pins the host store's image
            # synthesis against sim/trace_codec.py at the same time.
            pairs = [(float(a), float(b))
                     for a, b in (r.split(",") for r in prefix)]
            blocks = [list(group) for _, group
                      in itertools.groupby(pairs, key=lambda p: int(p[0]))]
            expected = b"".join(
                trace_codec.encode_region(
                    block, got["log_hz"],
                    samples_per_block=trace_codec.MAX_SAMPLES_PER_BLOCK)
                for block in blocks)
            # The fixture must actually contain a block that needs align4
            # padding, or this comparison cannot see a missing-padding bug at
            # all (see the sleep above for how that was found). A block is
            # 6 + 2*count bytes, so only EVEN sample counts need padding.
            padding = len(expected) - sum(trace_codec.block_size(len(b)) for b in blocks)
            self.assertGreater(
                padding, 0,
                f"no block in this run needed align4 padding (block sample "
                f"counts {[len(b) for b in blocks]}) — the fixture is too "
                f"small or too jittery to test the region layout")
            self.assertEqual(got["raw"], expected,
                             "the exported region does not match the same trace "
                             "rows re-encoded by sim/trace_codec.py")
            self.assertEqual("\n".join(rendered) + "\n",
                             trace_codec.region_to_csv(expected, log_hz=got["log_hz"]))
        finally:
            dev.close()

    def test_traceraw_short_export_cannot_self_verify_and_says_so_outside_the_frame(self) -> None:
        """The store comes up SHORT of what it announced — and the response
        must make that impossible to miss.

        Reached with $JH_HOST_RAW_OVERREPORT (firmware/src/platform/host/
        jh_store.cpp): the store announces 8 bytes more than its image holds,
        the way a lost open_read_raw() flush or a failed mid-stream read
        would. Nothing else on this platform can come up short, so without
        this knob main.cpp's `streamed != declared` arm ships untested.

        Three things are pinned here, all of them shapes CLAUDE.md rule 3
        forbids (a silent failure that looks like a pass):

        1. The base64 body stays PURE. A `#` warning line inside the frame is
           not a note — tools/jump's parse_file_sections and web/sync's
           sync.js copy everything between BEGIN and END into the file, and
           b64decode/atob then throw on it (the em dash isn't even ASCII), so
           the page reports "unreadable data" for a cause the puck named.
        2. The warning is OUTSIDE the frame, as `#` chatter. That is where
           both scanners look, and web/sync/CONTRACT.md §2 keeps FILE bodies out of the
           device.log Josh receives — so inside the frame is exactly where
           `verified` criterion (a)'s only evidence would be discarded.
        3. BOTH `bytes=` values are N, the announced count. If the trailing
           one carried what actually streamed, the pair (crc32 over the short
           body + a byte count that matches it) would be internally
           consistent, and a client checking the count next to the crc would
           call a truncated trace verified — and web/sync/CONTRACT.md §2 then permits
           offering `clear`."""
        script = write_script(self.tmp_path / "script.txt",
                              "rest 2.0\njump 0.65\nrest 2.0\n")
        dev = HostDevice(self.host_binary, self.tmp_path / "hostdir", script,
                         extra_env={"JH_HOST_RAW_OVERREPORT": "8"})
        try:
            self.assertTrue(dev.drain_boot())
            self.assertIsNotNone(dev.wait_for("JUMP", timeout=10.0),
                                 "no JUMP from the scripted toss — no trace to export")
            time.sleep(3.0)   # let whole seconds accumulate, as above
            lines = [ln.rstrip("\r") for ln in dev.command("traceraw")]

            begin = lines.index("FILE trace.bin BEGIN")
            end = lines.index("FILE trace.bin END")
            body = lines[begin + 1:end]

            # (1) the body is base64 and nothing else.
            self.assertTrue(body, "the scripted ride must have stored a trace")
            try:
                raw = base64.b64decode("".join(body), validate=True)
            except ValueError as e:
                self.fail(
                    f"the FILE body is not pure base64 ({e}) — something else "
                    f"was emitted between BEGIN and END, and every client "
                    f"decodes those lines straight into trace.bin. Last body "
                    f"lines: {body[-2:]!r}")

            # (2) the complaint rides outside the frame, as chatter.
            warnings = [i for i, ln in enumerate(lines) if "INCOMPLETE" in ln]
            self.assertTrue(warnings,
                            "the store came up short and the device said "
                            "nothing:\n" + "\n".join(lines))
            for i in warnings:
                self.assertTrue(lines[i].startswith("#"),
                                f"warning is not a chatter line: {lines[i]!r}")
                self.assertFalse(begin < i < end,
                                 f"warning sits INSIDE the base64 body (line "
                                 f"{i}, frame {begin}..{end}): {lines[i]!r}")
            self.assertTrue(
                any("streamed" in lines[i] for i in warnings),
                "the shortfall warning must name what streamed vs what was "
                "announced:\n" + "\n".join(lines[i] for i in warnings))

            # (3) both bytes= are N, and N is not what arrived.
            declared = int(self.CHATTER_RE.match(
                next(ln for ln in lines if self.CHATTER_RE.match(ln))).group(1))
            crc_m = next(self.CRC_RE.match(ln) for ln in lines
                         if self.CRC_RE.match(ln))
            self.assertEqual(int(crc_m.group(2)), declared,
                             "the trailing bytes= must be the SAME N the "
                             "header announced — printing what actually "
                             "streamed lets a short export self-verify")
            self.assertEqual(len(raw), declared - 8,
                             "the knob is what makes this scenario short; if "
                             "the body matched the announcement there is "
                             "nothing here to detect")
            # The crc IS honest about what went out, so the receiver's own
            # check fails on the count, not on a corrupt-looking payload.
            self.assertEqual(f"{zlib.crc32(raw) & 0xFFFFFFFF:08x}", crc_m.group(1))
            self.assertNotEqual(len(raw), declared,
                                "a receiver comparing received bytes to "
                                "bytes= must be able to see the shortfall")
        finally:
            dev.close()

    def test_traceraw_with_no_trace_is_a_well_formed_empty_export(self) -> None:
        """A puck that has recorded nothing (here: a script that never moves,
        so the motion gate never opens) must answer with the empty framing —
        bytes=0, no body lines, crc32 of nothing — NOT an error. This is the
        state a just-cleared puck hands the phone page, and "empty" and
        "failed" have to be distinguishable there: one is fine, the other
        means don't offer to clear."""
        script = write_script(self.tmp_path / "script.txt", "rest 5.0\n")
        dev = HostDevice(self.host_binary, self.tmp_path / "hostdir", script)
        try:
            self.assertTrue(dev.drain_boot())
            lines = dev.command("traceraw")
            got = self._parse_traceraw(lines)
            self.assertEqual(got["bytes"], 0)
            self.assertEqual(got["raw"], b"")
            # zlib.crc32(b"") — the receiver's own check has to agree on the
            # empty case too, or every empty sync looks corrupt.
            self.assertEqual(got["crc32"], "00000000")
            begin = lines.index("FILE trace.bin BEGIN")
            self.assertEqual(lines[begin + 1], "FILE trace.bin END",
                             "an empty export must have NO body lines at all")
        finally:
            dev.close()
