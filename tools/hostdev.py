#!/usr/bin/env python3
"""hostdev — expose the HOST-platform firmware binary on a pty.

STRETCH utility (see the host-platform port notes): bridges a pty — the
same kind of device node a real board shows up as — to the REAL
firmware core (firmware/src/main.cpp, built for firmware/platformio.ini's
env:host via `pio run -e host`). Byte-for-byte the same core the esp32/nrf52
boards run; only the platform seams underneath it (firmware/src/platform/
host/**) differ. Lets any serial-speaking client (a terminal program,
pyserial, ./tools/jump's own low-level SerialPort) drive the real command
dispatch / self-test / motion gate / emit layer exactly as it would a real
board, with zero hardware.

Usage:
    python3 tools/hostdev.py &          # prints "PTY /dev/pts/N", then bridges
    # ... in another shell/script, open /dev/pts/N like any serial port ...

Options:
    --bin PATH        host binary (default: firmware/.pio/build/host/program,
                       i.e. what `pio run -d firmware -e host` produces)
    --host-dir PATH    JH_HOST_DIR for the child process (default: a fresh
                       temp directory)
    --script PATH      JH_IMU_SCRIPT for the child process (default: none —
                       jh_imu.cpp's own infinite-rest fallback)

NOTE ON ./tools/jump --port: tools/jump's own open_device() validates any
--port against scan_ports() (real USB devices only: pyserial's enumerated
VIDs, or /dev/ttyUSB*/ttyACM*/cu.* globs) before ever opening it, and
silently discards + re-autodetects otherwise — a bridged pty never matches
that list, so `./tools/jump <cmd> --port <this bridge's pty>` falls through
to autodetect and fails with no hardware attached. That gate lives in
tools/jump (out of this port's file list) and is unrelated to whether the
bridge itself works: open the printed pty directly (pyserial, or the same
POSIX-open+raw-termios dance tools/jump's own SerialPort class does) to
drive the real core through this bridge.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import argparse
import os
import select
import sys
import tempfile
import tty
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_BIN = REPO / "firmware" / ".pio" / "build" / "host" / "program"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bin", default=None, help=f"host binary (default: {DEFAULT_BIN})")
    ap.add_argument("--host-dir", default=None, help="JH_HOST_DIR (default: fresh temp dir)")
    ap.add_argument("--script", default=None, help="JH_IMU_SCRIPT (default: none)")
    args = ap.parse_args()

    binp = Path(args.bin) if args.bin else DEFAULT_BIN
    if not binp.exists():
        print(f"host binary not found at {binp} — build it first: "
              f"pio run -d firmware -e host", file=sys.stderr)
        return 1

    host_dir = args.host_dir or tempfile.mkdtemp(prefix="jh_hostdev_")

    env = dict(os.environ)
    env["JH_HOST_DIR"] = str(host_dir)
    if args.script:
        env["JH_IMU_SCRIPT"] = str(args.script)

    # Raw mode: without this the pty echoes bytes back to itself, exactly
    # the same feedback-loop trap tools/fake_device.py's own docstring warns
    # about — this bridge mirrors that file's pty setup for the same reason.
    master, slave = os.openpty()
    tty.setraw(slave)
    print(f"PTY {os.ttyname(slave)}", flush=True)

    import subprocess

    # The child must never outlive the bridge. A SIGTERM'd Python process
    # (how tests and shells tear this bridge down) skips finally: blocks
    # unless the signal becomes an exception first — which orphaned the host
    # binary (CPU-spinning, holding its pipes open, hanging any
    # capture_output caller downstream) on every bridge teardown. Convert
    # SIGTERM/SIGINT to SystemExit so the finally below always reaps it.
    import signal

    def _die(*_a):
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _die)
    signal.signal(signal.SIGINT, _die)

    proc = subprocess.Popen([str(binp)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
    assert proc.stdin is not None and proc.stdout is not None
    child_out_fd = proc.stdout.fileno()

    try:
        while proc.poll() is None:
            try:
                r, _, _ = select.select([master, child_out_fd], [], [], 0.5)
            except OSError:
                break
            if master in r:
                # Bytes a connected client wrote (a command) -> the real
                # core's stdin.
                try:
                    data = os.read(master, 4096)
                except OSError:
                    data = b""
                if data:
                    proc.stdin.write(data)
                    proc.stdin.flush()
            if child_out_fd in r:
                # Bytes the real core emitted -> back out through the pty,
                # to whoever has it open.
                data = os.read(child_out_fd, 4096)
                if data:
                    try:
                        os.write(master, data)
                    except OSError:
                        pass  # nobody has the slave open right now: drop it
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
