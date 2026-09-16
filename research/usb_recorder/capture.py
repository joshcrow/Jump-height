#!/usr/bin/env python3
"""Pinned, evidence-preserving USB capture for the separate JH6 research firmware.

This program never flashes a board or opens a serial port at 1200 baud. Only
captures with valid CRCs, complete counters and real FIFO timestamps produce
imu.csv. Raw bytes and decoded integer samples survive unsuccessful captures.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import struct
import sys
import time
from typing import Any, Iterable
import zlib


DEFAULT_UID = "2513620E30AE413D"
DEFAULT_NAME = "JumpHeight-8673"
RECORDER = "jh6-usb-research"
MAGIC = b"JH6F"
HEADER = struct.Struct("<4sBBH")
SAMPLE = struct.Struct("<IIIhhhhhhHH")
RAW_FIFO = struct.Struct("<I18s")
CRC = struct.Struct("<I")
MAX_PAYLOAD = 4096
PERIOD_TOLERANCE = 0.25
G0 = 9.80665
RAW_COLUMNS = ("seq", "mcu_us", "sensor_ticks", "gx_raw", "gy_raw", "gz_raw",
               "ax_raw", "ay_raw", "az_raw", "flags", "fifo_words")
IMU_COLUMNS = ("t_s", "ax_mps2", "ay_mps2", "az_mps2", "gx_rps", "gy_rps", "gz_rps")
RECEIVE_COLUMNS = ("byte_start", "byte_end", "monotonic_before_ns", "monotonic_after_ns",
                   "realtime_before_ns", "realtime_after_ns")
RAW_FIFO_COLUMNS = ("seq", "raw_hex")


def _strict_json(payload: bytes) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"nonfinite JSON number {value}")

    def finite_float(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"nonfinite JSON number {value}")
        return result

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key {key}")
            result[key] = value
        return result

    result = json.loads(payload.decode("utf-8"), parse_constant=reject_constant, parse_float=finite_float,
                        object_pairs_hook=unique_object)
    if not isinstance(result, dict):
        raise ValueError("JSON payload must be an object")
    return result


def _number(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def normalize_uid(uid: str) -> str:
    uid = uid.upper()
    if len(uid) != 16 or any(c not in "0123456789ABCDEF" for c in uid):
        raise ValueError("UID must contain exactly 16 hexadecimal characters")
    return uid


def validate_info(info: dict[str, Any], expected_uid: str = DEFAULT_UID,
                  expected_name: str | None = DEFAULT_NAME) -> None:
    """Validate the protocol contract before sending a capture command."""
    if info.get("uid") != normalize_uid(expected_uid):
        raise ValueError(f"device UID mismatch: expected {expected_uid}")
    if expected_name is not None and info.get("name") != expected_name:
        raise ValueError(f"device name mismatch: expected {expected_name}")
    if not isinstance(info.get("name"), str) or not info["name"]:
        raise ValueError("device name is absent")
    if info.get("recorder") != RECORDER:
        raise ValueError("connected firmware is not the JH6 USB research recorder")
    if not isinstance(info.get("build"), str) or not info["build"]:
        raise ValueError("firmware build identifier is absent")
    if not _number(info.get("odr_hz")) or not 1 <= info["odr_hz"] <= 1000:
        raise ValueError("invalid ODR")
    for key, expected in (("accel_g_per_lsb", 0.000488),
                          ("gyro_dps_per_lsb", 0.070), ("timestamp_tick_us", 25)):
        if not _number(info.get(key)) or not math.isclose(info[key], expected, rel_tol=1e-9):
            raise ValueError(f"unexpected {key}; this capture version requires {expected}")
    if info.get("timestamp_mode") != "fifo" or info.get("timestamp_bits") != 24:
        raise ValueError("real 24-bit FIFO timestamps are required")
    if "raw_fifo_frames" in info and type(info["raw_fifo_frames"]) is not bool:
        raise ValueError("raw_fifo_frames metadata must be boolean")
    duration_limit_ms(info)


def duration_limit_ms(info: dict[str, Any]) -> int:
    """Old recorder builds predate an advertised limit and accept at most60s."""
    limit = info.get("max_duration_ms", 60000)
    if type(limit) is not int or not 100 <= limit <= 1200000:
        raise ValueError("invalid advertised max_duration_ms")
    return limit


def select_port(ports: Iterable[Any], expected_uid: str = DEFAULT_UID,
                requested_port: str | None = None) -> Any:
    """Select one exact USB serial identity; an explicit path cannot bypass pinning."""
    expected_uid = normalize_uid(expected_uid)
    matches = []
    for port in ports:
        serial_number = getattr(port, "serial_number", None)
        if (isinstance(serial_number, str) and serial_number.upper() == expected_uid
                and (requested_port is None or port.device == requested_port)):
            matches.append(port)
    if len(matches) != 1:
        suffix = f" on {requested_port}" if requested_port else ""
        raise ValueError(f"expected exactly one USB serial device with UID {expected_uid}{suffix}; "
                         f"found {len(matches)}")
    return matches[0]


def secure_serial_port(connection: Any) -> dict[str, Any]:
    """Prevent subsequent opens using the kernel terminal-exclusive mode.

    Pyserial's exclusive=True is only advisory flock on POSIX. TIOCEXCL also
    blocks native clients which ignore flock, but does not revoke handles that
    were open already. Existing ownership must be checked independently before
    capture; this function makes no claim to have done that check.
    """
    if os.name != "posix":
        raise RuntimeError("kernel serial exclusivity requires POSIX TIOCEXCL; refusing capture")
    import fcntl
    import termios

    if not hasattr(termios, "TIOCEXCL"):
        raise RuntimeError("TIOCEXCL unavailable; refusing capture without a kernel serial lock")
    descriptor = connection.fileno()
    if type(descriptor) is not int or descriptor < 0:
        raise RuntimeError("serial connection has no valid file descriptor")
    fcntl.ioctl(descriptor, termios.TIOCEXCL, 0)
    return {"kernel_exclusive": True, "method": "TIOCEXCL",
            "advisory_flock_requested": True, "existing_owner_verification": False,
            "limitation": "Prevents subsequent unprivileged opens; does not evict or detect handles already open. Existing serial ownership must be checked separately."}


@dataclass(frozen=True)
class Frame:
    kind: int
    payload: bytes


class FrameParser:
    """Incremental framed stream parser; corruption is never silently accepted."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.errors: list[str] = []
        self.frame_count = 0

    def feed(self, data: bytes) -> list[Frame]:
        self.buffer.extend(data)
        frames: list[Frame] = []
        while len(self.buffer) >= HEADER.size:
            if self.buffer[:4] != MAGIC:
                index = self.buffer.find(MAGIC, 1)
                count = index if index >= 0 else len(self.buffer) - 3
                self.errors.append(f"framing lost; discarded {count} bytes")
                del self.buffer[:count]
                continue
            _, version, kind, length = HEADER.unpack_from(self.buffer)
            if length > MAX_PAYLOAD:
                self.errors.append(f"payload length {length} exceeds maximum {MAX_PAYLOAD}")
                del self.buffer[:4]
                continue
            frame_size = HEADER.size + length + CRC.size
            if len(self.buffer) < frame_size:
                break
            encoded = bytes(self.buffer[:frame_size])
            del self.buffer[:frame_size]
            expected = CRC.unpack_from(encoded, frame_size - CRC.size)[0]
            if zlib.crc32(encoded[:-CRC.size]) & 0xFFFFFFFF != expected:
                self.errors.append("frame CRC32 mismatch")
                continue
            if version != 1:
                self.errors.append(f"unsupported frame version {version}")
                continue
            if kind not in (0, 1, 2, 3, 4, 5):
                self.errors.append(f"unknown frame type {kind}")
                continue
            if kind == 1 and length != SAMPLE.size:
                self.errors.append(f"sample payload is {length} bytes; expected {SAMPLE.size}")
                continue
            if kind == 5 and length != RAW_FIFO.size:
                self.errors.append(f"RAW_FIFO payload is {length} bytes; expected {RAW_FIFO.size}")
                continue
            self.frame_count += 1
            frames.append(Frame(kind, encoded[HEADER.size:-CRC.size]))
        return frames

    def finish(self) -> None:
        if self.buffer:
            self.errors.append(f"truncated stream: {len(self.buffer)} buffered bytes")
            self.buffer.clear()


class Capture:
    """Decoded capture and quality decision, independent of serial hardware."""

    def __init__(self, expected_uid: str = DEFAULT_UID, expected_name: str | None = DEFAULT_NAME,
                 info: dict[str, Any] | None = None, duration_ms: int | None = None) -> None:
        self.expected_uid = normalize_uid(expected_uid)
        self.expected_name = expected_name
        self.info = info
        self.duration_ms = duration_ms
        self.parser = FrameParser()
        self.metadata: dict[str, Any] | None = None
        self.end: dict[str, Any] | None = None
        self.samples: list[tuple[int, ...]] = []
        self.errors: list[str] = []
        self.firmware_errors: list[dict[str, Any]] = []
        self.status: list[dict[str, Any]] = []
        self.fifo_raw: list[tuple[int, str]] = []
        self._raw_fifo_sequences: set[int] = set()
        self._pending_raw_fifo: tuple[int, bytes] | None = None
        self._finished = False
        self._quality: dict[str, Any] | None = None
        self.times_s: list[float] = []

    def feed(self, data: bytes) -> None:
        if self._finished:
            raise ValueError("capture has already been finalized")
        for frame in self.parser.feed(data):
            if self.end is not None:
                self.errors.append("frame received after END")
            if self._pending_raw_fifo is not None and frame.kind != 1:
                self.errors.append("RAW_FIFO frame not immediately followed by SAMPLE")
                self._pending_raw_fifo = None
            if frame.kind == 5:
                seq, raw = RAW_FIFO.unpack(frame.payload)
                self.fifo_raw.append((seq, raw.hex()))
                if self.metadata is None:
                    self.errors.append("RAW_FIFO received before metadata")
                if seq in self._raw_fifo_sequences:
                    self.errors.append(f"duplicate RAW_FIFO sequence {seq}")
                self._raw_fifo_sequences.add(seq)
                if seq != len(self.samples):
                    self.errors.append(f"RAW_FIFO sequence {seq} differs from next sample index {len(self.samples)}")
                self._pending_raw_fifo = (seq, raw)
                continue
            if frame.kind == 1:
                if self.metadata is None:
                    self.errors.append("sample received before metadata")
                sample = SAMPLE.unpack(frame.payload)
                if self._pending_raw_fifo is not None:
                    raw_seq, raw = self._pending_raw_fifo
                    if raw_seq != sample[0]:
                        self.errors.append(f"RAW_FIFO/SAMPLE sequence mismatch {raw_seq}/{sample[0]}")
                    if raw[:12] != frame.payload[12:24]:
                        self.errors.append(f"RAW_FIFO axes disagree with SAMPLE at sequence {sample[0]}")
                    decoded_ticks = raw[15] | (raw[12] << 8) | (raw[13] << 16)
                    if decoded_ticks != sample[2]:
                        self.errors.append(f"RAW_FIFO timestamp disagrees with SAMPLE at sequence {sample[0]}")
                    self._pending_raw_fifo = None
                elif self.metadata is not None and self.metadata.get("raw_fifo_frames") is True:
                    self.errors.append(f"SAMPLE sequence {sample[0]} has no preceding RAW_FIFO")
                self.samples.append(sample)
                continue
            try:
                payload = _strict_json(frame.payload)
            except (ValueError, UnicodeError) as exc:
                self.errors.append(f"invalid type {frame.kind} JSON: {exc}")
                continue
            if frame.kind == 0:
                if self.metadata is not None or self.samples:
                    self.errors.append("duplicate or late metadata frame")
                else:
                    self.metadata = payload
            elif frame.kind == 2:
                if self.end is not None:
                    self.errors.append("duplicate END frame")
                else:
                    self.end = payload
            elif frame.kind == 4:
                if self.metadata is None:
                    self.errors.append("status received before metadata")
                for key in ("seq_next", "mcu_us", "hfclkstat", "timer4_prescaler", "timer4_bitmode"):
                    if type(payload.get(key)) is not int or not 0 <= payload[key] <= 0xFFFFFFFF:
                        self.errors.append(f"STATUS {key} must be a uint32 integer")
                if payload.get("seq_next") != len(self.samples):
                    self.errors.append("STATUS seq_next differs from decoded sample count")
                if type(payload.get("temp_raw")) is not int or not -32768 <= payload["temp_raw"] <= 32767:
                    self.errors.append("STATUS temp_raw must be an int16 integer")
                if not _number(payload.get("temp_c")):
                    self.errors.append("STATUS temp_c must be finite")
                self.status.append(payload)
            else:
                self.firmware_errors.append(payload)
                self.errors.append(f"firmware error: {payload}")

    def finalize(self) -> dict[str, Any]:
        if self._quality is not None:
            return self._quality
        self._finished = True
        self.parser.finish()
        self.errors.extend(self.parser.errors)
        if self._pending_raw_fifo is not None:
            self.errors.append("unpaired RAW_FIFO at end of stream")
        metadata_valid = False
        if self.metadata is None:
            self.errors.append("missing metadata frame")
        else:
            try:
                validate_info(self.metadata, self.expected_uid, self.expected_name)
                metadata_valid = True
            except ValueError as exc:
                self.errors.append(str(exc))
            if self.info is not None:
                keys = ("uid", "name", "recorder", "build", "odr_hz", "accel_g_per_lsb",
                        "gyro_dps_per_lsb", "timestamp_tick_us", "timestamp_mode", "timestamp_bits",
                        "max_duration_ms", "raw_fifo_frames")
                if any(self.metadata.get(key) != self.info.get(key) for key in keys):
                    self.errors.append("capture metadata differs from pre-capture INFO")
            if self.metadata.get("raw_fifo_frames") is True and len(self.fifo_raw) != len(self.samples):
                self.errors.append(f"RAW_FIFO count={len(self.fifo_raw)}, SAMPLE count={len(self.samples)}")
        if self.end is None:
            self.errors.append("missing END frame")
        else:
            for key in ("sample_count", "dropped_frames", "i2c_errors", "fifo_overruns"):
                value = self.end.get(key)
                if type(value) is not int or value < 0:
                    self.errors.append(f"END {key} must be a nonnegative integer")
                elif key == "sample_count" and value != len(self.samples):
                    self.errors.append(f"END sample_count={value}, decoded={len(self.samples)}")
                elif key != "sample_count" and value:
                    self.errors.append(f"END {key}={value}")
        if len(self.samples) < 2:
            self.errors.append("fewer than two decoded samples")

        delta_ticks: list[int] = []
        cumulative_ticks = 0
        previous_ticks: int | None = None
        wraps = 0
        timestamp_decreases = 0
        timestamps_valid = metadata_valid and len(self.samples) >= 2
        fault_counts = {name: 0 for name in ("accel_x_rail", "accel_y_rail", "accel_z_rail",
                                             "gyro_x_rail", "gyro_y_rail", "gyro_z_rail",
                                             "fifo_overrun", "timestamp_missing", "unknown_flags")}
        for index, sample in enumerate(self.samples):
            seq, _, ticks = sample[:3]
            flags = sample[9]
            if seq != index:
                self.errors.append(f"sequence discontinuity at decoded sample {index}: seq={seq}")
            for bit, name in enumerate(tuple(fault_counts)[:7]):
                fault_counts[name] += bool(flags & (1 << bit))
            fault_counts["timestamp_missing"] += not bool(flags & 0x80)
            fault_counts["unknown_flags"] += bool(flags & ~0xFF)
            if ticks >= 1 << 24:
                self.errors.append(f"timestamp outside 24-bit range at sample {index}")
                timestamps_valid = False
            if previous_ticks is not None:
                delta = (ticks - previous_ticks) % (1 << 24)
                if ticks < previous_ticks:
                    timestamp_decreases += 1
                    if metadata_valid and ticks < (1 << 24) and previous_ticks < (1 << 24):
                        period = 1 / self.metadata["odr_hz"]
                        if period * (1 - PERIOD_TOLERANCE) <= delta * 25e-6 <= period * (1 + PERIOD_TOLERANCE):
                            wraps += 1
                delta_ticks.append(delta)
                cumulative_ticks += delta
            previous_ticks = ticks
            self.times_s.append(cumulative_ticks * 25e-6)
        for name, count in fault_counts.items():
            if count:
                self.errors.append(f"{name}: {count} samples")
        if fault_counts["timestamp_missing"]:
            timestamps_valid = False

        interval_stats = None
        if delta_ticks:
            intervals = [delta * 25e-6 for delta in delta_ticks]
            interval_stats = {
                "count": len(intervals), "min_s": min(intervals), "max_s": max(intervals),
                "mean_s": statistics.fmean(intervals), "median_s": statistics.median(intervals),
                "stdev_s": statistics.pstdev(intervals),
                "actual_rate_hz": (len(intervals) / sum(intervals)) if sum(intervals) else None,
            }
            if metadata_valid:
                period = 1 / self.metadata["odr_hz"]
                bad = [i + 1 for i, dt in enumerate(intervals)
                       if not period * (1 - PERIOD_TOLERANCE) <= dt <= period * (1 + PERIOD_TOLERANCE)]
                if bad:
                    self.errors.append(f"{len(bad)} invalid sensor intervals; first sample indices={bad[:10]}")
                    timestamps_valid = False
            interval_stats["timestamp_intervals_valid"] = timestamps_valid
            interval_stats["interpretation"] = "Modulo tick differences at nominal 25us; invalid differences remain visible for diagnosis. Invalid intervals cannot establish duration or sample rate."
            if not timestamps_valid:
                interval_stats["actual_rate_hz"] = None
        if metadata_valid and self.duration_ms is not None:
            expected = self.duration_ms * self.metadata["odr_hz"] / 1000
            allowance = max(2, expected * 0.05)
            if abs(len(self.samples) - expected) > allowance:
                self.errors.append(f"sample count inconsistent with requested duration: "
                                   f"{len(self.samples)} vs nominal {expected:.3f} (+/-{allowance:.3f})")
        self._quality = {
            "usable": not self.errors,
            "timebase_calibrated": False,
            "errors": self.errors,
            "decoded_sample_count": len(self.samples),
            "decoded_frame_count": self.parser.frame_count,
            "status_count": len(self.status),
            "raw_fifo_count": len(self.fifo_raw),
            "fault_counts": fault_counts,
            "timestamp_wraps": wraps,
            "timestamp_decrease_count": timestamp_decreases,
            "timestamp_intervals_valid": timestamps_valid,
            "sensor_duration_s": self.times_s[-1] if timestamps_valid else None,
            "interval_statistics": interval_stats,
            "guards": {
                "period_relative_tolerance": PERIOD_TOLERANCE,
                "duration_count_relative_tolerance": 0.05,
                "duration_count_boundary_allowance_samples": 2,
                "note": "Acquisition integrity heuristics; passing does not establish height accuracy.",
            },
        }
        return self._quality

    def si_rows(self) -> Iterable[tuple[float, ...]]:
        if not self.finalize()["usable"]:
            raise ValueError("unusable capture cannot produce estimator input")
        accel_scale = self.metadata["accel_g_per_lsb"] * G0
        gyro_scale = self.metadata["gyro_dps_per_lsb"] * math.pi / 180
        for t, row in zip(self.times_s, self.samples):
            yield (t, *(v * accel_scale for v in row[6:9]), *(v * gyro_scale for v in row[3:6]))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_outputs(output_dir: Path, capture: Capture, provenance: dict[str, Any]) -> dict[str, Any]:
    """Save decoded evidence, then write an estimator CSV only after passing quality."""
    quality = capture.finalize()
    raw_csv = output_dir / "raw.csv"
    with raw_csv.open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(RAW_COLUMNS)
        writer.writerows(capture.samples)
    with (output_dir / "status.json").open("x", encoding="utf-8") as stream:
        json.dump(capture.status, stream, indent=2, allow_nan=False)
        stream.write("\n")
    with (output_dir / "fifo_raw.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(RAW_FIFO_COLUMNS)
        writer.writerows(capture.fifo_raw)
    if quality["usable"]:
        with (output_dir / "imu.csv").open("x", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(IMU_COLUMNS)
            writer.writerows(capture.si_rows())
    files = {}
    for name in ("raw.bin", "raw.csv", "imu.csv", "status.json", "host_receive.csv", "fifo_raw.csv"):
        path = output_dir / name
        if path.exists():
            files[name] = {"bytes": path.stat().st_size, "sha256": _sha256(path)}
    manifest = {
        "schema": "jh6.usb_capture.v1", "research_only": True,
        "expected_uid": capture.expected_uid, "expected_name": capture.expected_name,
        "requested_duration_ms": capture.duration_ms,
        "info": capture.info, "metadata": capture.metadata, "end": capture.end,
        "quality": quality, "provenance": provenance, "files": files,
        "interpretation": {
            "imu_csv": "Uncalibrated nominal-SI readings; fit per-axis calibration before reconstruction.",
            "timestamp": "FIFO ticks unwrapped modulo 2^24 at NOMINAL 25 us/tick; clock scale is uncalibrated. Time is relative to first sample.",
            "mcu_us": "Service/readout timestamp only; never used as sample time.",
            "host_receive": "One row per serial read; half-open raw.bin byte offsets and host clock brackets. Userspace receipt is not exact USB arrival or sample time.",
            "status": "Original decoded type-4 device status objects; clock registers and temperature are diagnostics, not calibration.",
            "fifo_raw": "Original 18-byte sensor FIFO bursts from type-5 frames, paired by sequence with SAMPLE. No timestamp repair, sorting or substitution is performed.",
            "coordinates": "LSM6DS3TR-C sensor axes; no board, world, or gravity transformation applied.",
        },
    }
    with (output_dir / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, allow_nan=False)
        stream.write("\n")
    return manifest


def query_info(connection: Any, timeout_s: float = 3) -> dict[str, Any]:
    connection.write(b"info\n")
    connection.flush()
    deadline = time.monotonic() + timeout_s
    info = None
    partial = bytearray()
    while time.monotonic() < deadline:
        partial.extend(connection.readline(MAX_PAYLOAD + 1))
        if len(partial) > MAX_PAYLOAD:
            raise ValueError("oversized INFO response")
        if not partial.endswith(b"\n"):
            continue
        line = bytes(partial)
        partial.clear()
        if line.startswith(b"JH6 INFO "):
            if info is not None:
                raise ValueError("duplicate INFO response")
            info = _strict_json(line[len(b"JH6 INFO "):].strip())
        elif line.strip() == b"OK info":
            if info is None:
                raise ValueError("INFO terminator before INFO payload")
            return info
        elif line.strip().startswith((b"ERR", b"ERROR")):
            raise ValueError(f"INFO failed: {line.decode('utf-8', errors='replace').strip()}")
    raise ValueError("timed out waiting for complete JH6 INFO response")


def run_capture(args: argparse.Namespace) -> int:
    if not math.isfinite(args.seconds) or not 0.1 <= args.seconds <= 1200:
        raise ValueError("--seconds must be between 0.1 and 1200")
    duration_ms = round(args.seconds * 1000)
    expected_uid = normalize_uid(args.uid)
    expected_name = DEFAULT_NAME if expected_uid == DEFAULT_UID else None
    # Creating the directory atomically protects all evidence against accidental overwrite.
    args.output_dir.mkdir(parents=True, exist_ok=False)
    capture = Capture(expected_uid, expected_name, duration_ms=duration_ms)
    provenance: dict[str, Any] = {"host_start_utc": _utc_now(), "baud": 115200,
                                 "capture_script_sha256": _sha256(Path(__file__)),
                                 "timebase_calibrated": False,
                                 "host_monotonic_clock": vars(time.get_clock_info("monotonic")),
                                 "host_realtime_clock": vars(time.get_clock_info("time"))}
    raw_path = args.output_dir / "raw.bin"
    raw_path.touch(exist_ok=False)
    try:
        import serial
        from serial.tools import list_ports

        port = select_port(list_ports.comports(), expected_uid, args.port)
        provenance["usb"] = {key: getattr(port, key, None)
                              for key in ("device", "serial_number", "vid", "pid", "manufacturer", "product")}
        with serial.Serial(port.device, baudrate=115200, timeout=0.25, write_timeout=2,
                           exclusive=True) as connection:
            provenance["port_exclusivity"] = secure_serial_port(connection)
            capture.info = query_info(connection)
            validate_info(capture.info, expected_uid, expected_name)
            device_limit = duration_limit_ms(capture.info)
            provenance["device_capture_limit_ms"] = device_limit
            if duration_ms > device_limit:
                raise ValueError(f"requested {duration_ms}ms exceeds firmware capture limit {device_limit}ms; no capture command sent")
            provenance["capture_command_utc"] = _utc_now()
            capture_start = time.monotonic()
            connection.write(f"capture {duration_ms}\n".encode("ascii"))
            connection.flush()
            with raw_path.open("ab") as raw, (args.output_dir / "host_receive.csv").open("x", newline="", encoding="utf-8") as receives:
                receive_writer = csv.writer(receives)
                receive_writer.writerow(RECEIVE_COLUMNS)
                byte_offset = 0
                deadline = capture_start + args.seconds + 10
                while time.monotonic() < deadline:
                    read_size = max(1, min(connection.in_waiting, 65536))
                    mono_before = time.monotonic_ns()
                    real_before = time.time_ns()
                    data = connection.read(read_size)
                    real_after = time.time_ns()
                    mono_after = time.monotonic_ns()
                    receive_writer.writerow((byte_offset, byte_offset + len(data), mono_before,
                                             mono_after, real_before, real_after))
                    byte_offset += len(data)
                    if data:
                        raw.write(data)
                        capture.feed(data)
                    if capture.end is not None:
                        break
                else:
                    capture.errors.append("capture timed out before END")
            provenance["capture_wall_duration_s"] = time.monotonic() - capture_start
    except (Exception, KeyboardInterrupt) as exc:
        # Preserve the failure as evidence. A disconnected serial device must not look like a pass.
        capture.errors.append(f"capture failed: {type(exc).__name__}: {exc}")
    provenance["host_end_utc"] = _utc_now()
    manifest = write_outputs(args.output_dir, capture, provenance)
    if manifest["quality"]["usable"]:
        print(f"Saved {len(capture.samples)} samples to {args.output_dir / 'imu.csv'}")
        return 0
    print(f"Capture unusable; evidence retained in {args.output_dir}", file=sys.stderr)
    for error in manifest["quality"]["errors"][:20]:
        print(f"  {error}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    command = subcommands.add_parser("capture", help="record 0.1–1200 seconds from the pinned USB research board")
    command.add_argument("--seconds", type=float, required=True)
    command.add_argument("--output-dir", type=Path, required=True, help="new directory; existing paths are refused")
    command.add_argument("--uid", default=DEFAULT_UID, help=f"exact USB and firmware UID (default {DEFAULT_UID})")
    command.add_argument("--port", help="optional serial path; must still match --uid")
    args = parser.parse_args(argv)
    try:
        return run_capture(args)
    except (ValueError, OSError) as exc:
        print(f"jh6-capture: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
