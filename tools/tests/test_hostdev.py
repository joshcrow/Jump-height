"""Host-platform integration tests: the REAL C++ firmware core, running
natively on this machine (firmware/platformio.ini's env:host), driven over
its actual stdin/stdout protocol — no fake device, no simulator.

Every other test in this repo either drives tools/fake_device.py (a Python
stand-in for the protocol, tools/tests/test_cli.py) or the dependency-free
jump_detector.h in isolation (firmware/test/host_test.cpp). This file is the
first thing that ever runs the shared core's OWN command dispatch, self-test
orchestration, motion gate, and emit layer — the code in firmware/src/main.cpp
itself — off real hardware.

Requires PlatformIO (`pio`); the whole module is skipped (not failed) if it
can't be found, since this machine's software test suite must still pass
with zero embedded toolchain installed (see tools/jump simtest's own g++
skip for the same policy applied to firmware/test/host_test.cpp).

Run via ./tools/jump simtest, or directly:
    python3 -m pytest tools/tests/test_hostdev.py -q
"""

from __future__ import annotations

import os
import select
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
FIRMWARE_DIR = REPO / "firmware"
FW_VERSION = "0.4.3"  # firmware/src/main.cpp's FW_VERSION


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


PIO = _find_pio()
if PIO is None:
    pytest.skip("PlatformIO (pio) is not available on this machine — "
                "skipping host-platform integration tests", allow_module_level=True)


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

    def __init__(self, binp: Path, host_dir: Path, script_path: Path):
        host_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["JH_HOST_DIR"] = str(host_dir)
        env["JH_IMU_SCRIPT"] = str(script_path)
        self.proc = subprocess.Popen(
            [str(binp)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            cwd=str(REPO), env=env)
        self._buf = b""

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
                return line.decode(errors="replace")
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


# ------------------------------------------------------------------ fixture


@pytest.fixture(scope="session")
def host_binary() -> Path:
    """Builds env:host once (pio run -e host) and returns the binary path."""
    r = subprocess.run(PIO + ["run", "-d", str(FIRMWARE_DIR), "-e", "host"],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, f"pio run -e host failed:\n{r.stdout}\n{r.stderr}"
    binp = FIRMWARE_DIR / ".pio" / "build" / "host" / "program"
    assert binp.exists(), f"expected host binary not found at {binp}"
    return binp


# --------------------------------------------------------------------- tests


class TestBootAndInfo:
    def test_boot_sequence(self, host_binary: Path, tmp_path: Path) -> None:
        script = write_script(tmp_path / "script.txt", "rest 5.0\n")
        dev = HostDevice(host_binary, tmp_path / "hostdir", script)
        try:
            lines = dev.drain_boot()
            joined = "\n".join(lines)
            assert f"# JumpHeight fw v{FW_VERSION}" in joined
            assert "SELFTEST BEGIN" in joined
            assert "SELFTEST END result=PASS" in joined
            assert lines[-1].strip() == "READY"
        finally:
            dev.close()

    def test_info_reports_fw_and_expected_keys(self, host_binary: Path,
                                               tmp_path: Path) -> None:
        script = write_script(tmp_path / "script.txt", "rest 5.0\n")
        dev = HostDevice(host_binary, tmp_path / "hostdir", script)
        try:
            dev.drain_boot()
            lines = dev.command("info")
            info_line = next((ln for ln in lines if ln.startswith("INFO ")), None)
            assert info_line is not None, f"no INFO line in {lines}"
            kv = parse_kv(info_line)
            assert kv["fw"] == FW_VERSION
            for key in ("sample_hz", "log_hz", "motion_thresh_g", "idle_timeout_s", "ble"):
                assert key in kv, f"INFO line missing {key!r}: {info_line}"
            assert any(ln.strip() == "OK info" for ln in lines)
        finally:
            dev.close()


class TestCalibrationPersistence:
    def test_set_persists_across_process_restart(self, host_binary: Path,
                                                  tmp_path: Path) -> None:
        host_dir = tmp_path / "hostdir"
        script = write_script(tmp_path / "script.txt", "rest 5.0\n")

        dev1 = HostDevice(host_binary, host_dir, script)
        try:
            dev1.drain_boot()
            resp = dev1.command("set airtime_offset_s 0.02")
            assert not any(ln.startswith("ERR") for ln in resp), resp
        finally:
            dev1.close()

        # A brand-new process, same JH_HOST_DIR: the calibration must have
        # survived the restart, exactly like NVS surviving a reboot.
        dev2 = HostDevice(host_binary, host_dir, script)
        try:
            dev2.drain_boot()
            lines = dev2.command("info")
            cal_line = next((ln for ln in lines if ln.startswith("CAL ")), None)
            assert cal_line is not None, f"no CAL line in {lines}"
            kv = parse_kv(cal_line)
            assert kv["source"] == "device"
            assert abs(float(kv["airtime_offset_s"]) - 0.02) < 1e-4
        finally:
            dev2.close()


class TestJumpDetectionAndStorage:
    def test_scripted_jump_detected_and_stored(self, host_binary: Path,
                                               tmp_path: Path) -> None:
        # rest -> a toss-style free-fall -> rest, per the script mini-language
        # in firmware/src/platform/host/jh_imu.cpp.
        freefall_s = 0.65
        script = write_script(tmp_path / "script.txt",
                              f"rest 2.0\njump {freefall_s}\nrest 2.0\n")
        host_dir = tmp_path / "hostdir"
        dev = HostDevice(host_binary, host_dir, script)
        try:
            boot = dev.drain_boot()
            assert boot and boot[-1].strip() == "READY"

            jump_line = dev.wait_for("JUMP", timeout=10.0)
            assert jump_line is not None, "no JUMP line seen from the scripted toss"
            kv = parse_kv(jump_line)
            airtime_raw = float(kv["airtime_raw_s"])
            assert abs(airtime_raw - freefall_s) <= 0.1, (
                f"airtime_raw_s={airtime_raw} not within 0.1s of scripted {freefall_s}")
            assert float(kv["height_m"]) > 0.0

            lines = dev.command("jumps")
            assert any(ln.startswith("FILE jumps.csv BEGIN") for ln in lines)
            assert any(ln.startswith("FILE jumps.csv END") for ln in lines)
            data_rows = [ln for ln in lines if "," in ln and not ln.startswith(("n,", "#", "FILE"))]
            assert len(data_rows) == 1, f"expected exactly one stored jump row, got {lines}"
            cells = data_rows[0].split(",")
            assert cells[0] == "1"  # n
            assert abs(float(cells[2]) - freefall_s) <= 0.1  # airtime_raw_s
        finally:
            dev.close()


class TestPtyBridge:
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

    def test_selftest_over_the_bridged_pty(self, host_binary: Path, tmp_path: Path) -> None:
        script = write_script(tmp_path / "script.txt", "rest 5.0\n")
        bridge = subprocess.Popen(
            [sys.executable, str(REPO / "tools" / "hostdev.py"),
             "--bin", str(host_binary), "--host-dir", str(tmp_path / "hostdir"),
             "--script", str(script)],
            stdout=subprocess.PIPE, text=True)
        try:
            first = bridge.stdout.readline().strip()
            assert first.startswith("PTY "), f"bridge failed to start: {first!r}"
            port = first.split(None, 1)[1]

            mod = _load_jump_module()
            dev = mod.Device(port)  # the real transport a real board uses
            try:
                dev.drain_boot(timeout=5.0)
                lines = dev.command("selftest", timeout=15)
                ok = mod.render_selftest(lines)
                assert ok, f"selftest over the bridged pty did not PASS: {lines}"
            finally:
                dev.close()
        finally:
            bridge.terminate()
            try:
                bridge.wait(timeout=5)
            except Exception:
                bridge.kill()
