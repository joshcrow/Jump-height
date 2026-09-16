#!/usr/bin/env python3
"""Restore only USB bench board 8673 to the pinned c5eea285 application.

Default: package/USB/INFO preflight only. --restore permits one DFU-entry request
and at most one upload. No 1200-baud touch, format, clear, NVS command or retry.
An ACK is not bootloader entry: PID 0045 must subsequently be observed. If that
fails, stop; a person may need to double-tap RESET before a separate invocation.
Before either mode, pause JumpHeight Sync and independently verify that no process
owns the board's serial port. Kernel exclusivity cannot revoke existing handles.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any
import zipfile

from capture import query_info, secure_serial_port, validate_info

UID = "2513620E30AE413D"
NAME = "JumpHeight-8673"
VID, APP_PID, BOOT_PID = 0x2886, 0x8045, 0x0045
SOURCE = "c5eea285"
PACKAGE = Path(__file__).resolve().parents[2] / "web/firmware/jumpheight-c5eea285.zip"
PACKAGE_SHA256 = "a5cdd54711430167e506b8056efd4f0ad0335b4b595bdaadb35f3a23ef9eafd1"
MANUAL = "Stopped without retry. A person may need to double-tap RESET on 8673; then run preflight again."


class RestoreError(RuntimeError):
    pass


def validate_package(data: bytes) -> dict[str, Any]:
    """Both fixed image identity and application-only structure precede I/O."""
    if hashlib.sha256(data).hexdigest() != PACKAGE_SHA256:
        raise RestoreError("Rollback package SHA256 mismatch; no device action taken")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            if len(names) != 3 or set(names) != {"manifest.json", "firmware.bin", "firmware.dat"}:
                raise RestoreError("Rollback package must contain exactly the application files")
            document = json.loads(archive.read("manifest.json"))
            manifest = document["manifest"]
            if set(document) != {"manifest"} or set(manifest) != {"application", "dfu_version"}:
                raise RestoreError("Rollback package is not application-only")
            application = manifest["application"]
            if (application["bin_file"] != "firmware.bin"
                    or application["dat_file"] != "firmware.dat"
                    or not archive.read("firmware.bin") or not archive.read("firmware.dat")):
                raise RestoreError("Rollback application files are invalid")
            if archive.testzip() is not None:
                raise RestoreError("Rollback package CRC failure")
    except (KeyError, TypeError, ValueError, zipfile.BadZipFile) as exc:
        raise RestoreError(f"Invalid rollback manifest: {exc}") from exc
    return document


def pinned_port(ports: list[Any]) -> Any | None:
    matches = [p for p in ports if str(getattr(p, "serial_number", "")).upper() == UID]
    if len(matches) > 1:
        raise RestoreError("Ambiguous USB identity; no port selected")
    if not matches:
        return None
    port = matches[0]
    if port.vid != VID or port.pid not in (APP_PID, BOOT_PID):
        raise RestoreError("Pinned UID has unexpected VID/PID; refusing device")
    return port


class Runtime:
    """Hardware/process boundary; tests replace this entire object."""
    def ports(self) -> list[Any]:
        from serial.tools import list_ports
        return list(list_ports.comports())

    def connect(self, port: str) -> Any:
        import serial
        connection = serial.Serial(port, 115200, timeout=0.2, write_timeout=2, exclusive=True)
        try:
            secure_serial_port(connection)
        except (OSError, RuntimeError) as exc:
            connection.close()
            raise RestoreError(f"Cannot obtain kernel serial exclusivity: {exc}") from exc
        return connection

    def clock(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def uploader(self) -> str:
        path = shutil.which("adafruit-nrfutil")
        if not path:
            raise RestoreError("adafruit-nrfutil is unavailable; no DFU request sent")
        return path

    def upload(self, argv: list[str]) -> Any:
        return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, timeout=50, check=False)


def wait_pid(runtime: Any, pid: int, seconds: float = 10) -> Any:
    deadline = runtime.clock() + seconds
    while runtime.clock() < deadline:
        port = pinned_port(runtime.ports())
        if port is not None and port.pid == pid:
            return port
        runtime.sleep(0.1)
    raise RestoreError(f"Pinned board did not appear with PID {pid:04x}. {MANUAL}")


def read_lines(connection: Any, runtime: Any, terminal: bytes, seconds: float = 3) -> list[bytes]:
    deadline = runtime.clock() + seconds
    lines: list[bytes] = []
    partial = bytearray()
    total = 0
    while runtime.clock() < deadline:
        data = connection.readline(4097)
        total += len(data)
        partial.extend(data)
        if total > 16384 or len(partial) > 4096:
            raise RestoreError("Oversized command reply")
        if not partial.endswith(b"\n"):
            continue
        line = bytes(partial).strip()
        partial.clear()
        lines.append(line)
        if line == terminal:
            return lines
        if line.startswith((b"ERR", b"ERROR")):
            raise RestoreError(f"Device refused command: {line!r}")
    raise RestoreError(f"Missing exact {terminal!r} acknowledgment. {MANUAL}")


def verify_product(connection: Any, runtime: Any) -> list[str]:
    connection.write(b"info\n")
    connection.flush()
    lines = read_lines(connection, runtime, b"OK info", 5)
    infos = [line for line in lines if line.startswith(b"INFO ")]
    names = [line for line in lines if line.startswith(b"# name=")]
    if (len(infos) != 1 or f"src={SOURCE}".encode() not in infos[0].split()
            or names != [f"# name={NAME}".encode()]):
        raise RestoreError("Post-upload INFO does not prove the pinned product name and source")
    return [line.decode("utf-8", errors="replace") for line in lines]


def restore(*, perform: bool = False, runtime: Any | None = None) -> dict[str, Any]:
    # Snapshot the exact validated bytes. The uploader receives this same snapshot,
    # so a concurrent replacement of the repository ZIP cannot change the image.
    data = PACKAGE.read_bytes()
    package_manifest = validate_package(data)
    runtime = runtime or Runtime()
    port = pinned_port(runtime.ports())
    if port is None:
        raise RestoreError(f"USB bench board {NAME} / {UID} not found; no device action taken")
    result: dict[str, Any] = {"uid": UID, "name": NAME, "package": str(PACKAGE),
        "package_sha256": PACKAGE_SHA256, "package_manifest": package_manifest,
        "initial_pid": port.pid, "initial_port": port.device, "restored": False,
        "mode": "restore" if perform else "read_only_preflight"}
    executable = runtime.uploader() if perform else None
    if port.pid == APP_PID:
        # Reconfirm enumeration just before opening; never use a remembered path
        # alone as identity. Read INFO on the same connection used for the request.
        current = pinned_port(runtime.ports())
        if current is None or current.pid != APP_PID or current.device != port.device:
            raise RestoreError("USB identity changed before INFO; stopped")
        with runtime.connect(port.device) as connection:
            info = query_info(connection)
            validate_info(info, UID, NAME)
            if re.fullmatch(r"[0-9a-fA-F]{16}", info["build"]) is None:
                raise RestoreError("Research build identity is malformed")
            result["research_info"] = info
            if not perform:
                return result
            connection.write(b"dfu\n")
            connection.flush()
            result["dfu_reply"] = [line.decode() for line in
                                   read_lines(connection, runtime, b"OK dfu")]
        # The command ACK does not prove reset, retention magic, or USB enumeration.
        port = wait_pid(runtime, BOOT_PID)
    elif not perform:
        result["note"] = "Pinned board already in bootloader; INFO unavailable and not requested"
        return result

    # Fresh USB identity gate directly before the single upload attempt.
    current = pinned_port(runtime.ports())
    if current is None or current.pid != BOOT_PID or current.device != port.device:
        raise RestoreError(f"Bootloader USB identity changed before upload. {MANUAL}")
    with tempfile.TemporaryDirectory(prefix="jh6-restore-") as folder:
        snapshot = Path(folder) / PACKAGE.name
        snapshot.write_bytes(data)
        argv = [executable, "dfu", "serial", "--package", str(snapshot),
                "--port", current.device, "-b", "115200", "--singlebank"]
        try:
            upload = runtime.upload(argv)  # Exactly one call; no fallback transport.
        except subprocess.TimeoutExpired as exc:
            raise RestoreError(f"Single upload exceeded 50 seconds. {MANUAL}") from exc
    result["upload_returncode"] = upload.returncode
    result["upload_output"] = upload.stdout
    if "Device programmed." not in [line.strip() for line in upload.stdout.splitlines()]:
        raise RestoreError(f"Upload lacks literal 'Device programmed.' line. {MANUAL}")
    port = wait_pid(runtime, APP_PID, 15)
    current = pinned_port(runtime.ports())
    if current is None or current.pid != APP_PID or current.device != port.device:
        raise RestoreError("Application identity changed before verification")
    with runtime.connect(current.device) as connection:
        result["product_info"] = verify_product(connection, runtime)
    result["restored"] = True
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restore", action="store_true", help="permit the one pinned restoration attempt")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(restore(perform=args.restore), indent=2))
        return 0
    except (OSError, ValueError, RestoreError) as exc:
        print(f"Restoration stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
