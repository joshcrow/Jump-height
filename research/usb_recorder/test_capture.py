"""Hardware-free protocol, pinning, failure preservation and SI conversion tests."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
from pathlib import Path
import random
import struct
import tempfile
import termios
from types import SimpleNamespace
import unittest
from unittest import mock
import zlib

import capture


def info(**changes):
    result = {"uid": capture.DEFAULT_UID, "name": capture.DEFAULT_NAME,
              "recorder": capture.RECORDER, "build": "test-build", "odr_hz": 208,
              "accel_g_per_lsb": 0.000488, "gyro_dps_per_lsb": 0.070,
              "timestamp_tick_us": 25, "timestamp_bits": 24, "timestamp_mode": "fifo"}
    result.update(changes)
    return result


def frame(kind, payload, version=1):
    if not isinstance(payload, bytes):
        payload = json.dumps(payload, separators=(",", ":")).encode()
    encoded = capture.HEADER.pack(capture.MAGIC, version, kind, len(payload)) + payload
    return encoded + struct.pack("<I", zlib.crc32(encoded) & 0xFFFFFFFF)


def sample(seq, ticks=None, flags=0x80, axes=(100, -200, 300, 1000, -1500, 2049)):
    if ticks is None:
        ticks = round(seq * 1e6 / (208 * 25))
    return frame(1, capture.SAMPLE.pack(seq, 100000 + seq * 100, ticks,
                                       *axes, flags, 9))


def end(count=4, **changes):
    result = {"sample_count": count, "dropped_frames": 0, "i2c_errors": 0, "fifo_overruns": 0}
    result.update(changes)
    return frame(2, result)


def stream(count=4):
    return frame(0, info()) + b"".join(sample(i) for i in range(count)) + end(count)


def status(seq_next=0, **changes):
    payload = {"seq_next": seq_next, "mcu_us": 98765, "hfclkstat": 0x10001,
               "timer4_prescaler": 4, "timer4_bitmode": 3, "temp_raw": -128,
               "temp_c": 24.5}
    payload.update(changes)
    return frame(4, payload)


def raw_fifo(seq, ticks=None, axes=(100, -200, 300, 1000, -1500, 2049), raw=None):
    if ticks is None:
        ticks = round(seq * 1e6 / (208 * 25))
    if raw is None:
        raw = struct.pack("<hhhhhh", *axes) + bytes(((ticks >> 8) & 255,
              (ticks >> 16) & 255, 0xA1, ticks & 255, 0xB2, 0xC3))
    return frame(5, capture.RAW_FIFO.pack(seq, raw))


def decoded(data, **kwargs):
    result = capture.Capture(**kwargs)
    result.feed(data)
    result.finalize()
    return result


class ProtocolTests(unittest.TestCase):
    def assert_bad(self, result, fragment):
        quality = result.finalize()
        self.assertFalse(quality["usable"])
        self.assertTrue(any(fragment in error for error in quality["errors"]), quality)
        with self.assertRaisesRegex(ValueError, "unusable"):
            list(result.si_rows())

    def test_struct_sizes_match_wire_contract(self):
        self.assertEqual(capture.HEADER.size, 8)
        self.assertEqual(capture.SAMPLE.size, 28)
        self.assertEqual(capture.CRC.size, 4)
        self.assertEqual(capture.RAW_FIFO.size, 22)

    def test_raw_fifo_pairs_keep_all_bytes_and_match_axes_timestamp(self):
        first = bytes.fromhex("640038ff2c01e80324fa0108cdaba1efb2c3")
        wire = (frame(0, info(raw_fifo_frames=True)) + raw_fifo(0, raw=first)
                + sample(0, ticks=0xABCDEF) + raw_fifo(1, ticks=0xABCDEF + 192)
                + sample(1, ticks=0xABCDEF + 192) + end(2))
        result = capture.Capture()
        for byte in wire:
            result.feed(bytes([byte]))
        self.assertTrue(result.finalize()["usable"], result.errors)
        self.assertEqual(result.fifo_raw[0], (0, first.hex()))
        self.assertEqual(result.finalize()["raw_fifo_count"], 2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "raw.bin").write_bytes(wire)
            manifest = capture.write_outputs(path, result, {})
            with (path / "fifo_raw.csv").open() as source:
                reader = csv.reader(source)
                self.assertEqual(tuple(next(reader)), capture.RAW_FIFO_COLUMNS)
                rows = list(reader)
            self.assertEqual(rows[0], ["0", first.hex()])
            self.assertIn("fifo_raw.csv", manifest["files"])

    def test_advertised_raw_fifo_cannot_be_missing_or_duplicate(self):
        prefix = frame(0, info(raw_fifo_frames=True))
        self.assert_bad(decoded(prefix + sample(0) + raw_fifo(1) + sample(1) + end(2)),
                        "no preceding RAW_FIFO")
        self.assert_bad(decoded(prefix + raw_fifo(0) + raw_fifo(0) + sample(0)
                                + raw_fifo(1) + sample(1) + end(2)), "duplicate RAW_FIFO")

    def test_raw_fifo_mapping_faults_fail(self):
        prefix = frame(0, info(raw_fifo_frames=True))
        suffix = raw_fifo(1) + sample(1) + end(2)
        cases = ((raw_fifo(0, axes=(101, -200, 300, 1000, -1500, 2049)), "axes disagree"),
                 (raw_fifo(0, ticks=1), "timestamp disagrees"),
                 (raw_fifo(1, ticks=0), "sequence mismatch"))
        for bad, message in cases:
            self.assert_bad(decoded(prefix + bad + sample(0) + suffix), message)

    def test_raw_fifo_must_immediately_precede_sample(self):
        prefix = frame(0, info(raw_fifo_frames=True))
        wire = prefix + raw_fifo(0) + status(0) + sample(0) + raw_fifo(1) + sample(1) + end(2)
        self.assert_bad(decoded(wire), "not immediately followed")
        self.assert_bad(decoded(prefix + raw_fifo(0)), "unpaired RAW_FIFO")
        self.assert_bad(decoded(prefix + raw_fifo(0) + sample(0) + raw_fifo(1) + sample(1)
                                + raw_fifo(2) + end(2)), "RAW_FIFO count")

    def test_raw_fifo_truncated_or_wrong_length_fails(self):
        prefix = frame(0, info(raw_fifo_frames=True))
        self.assert_bad(decoded(prefix + raw_fifo(0)[:-1]), "truncated stream")
        self.assert_bad(decoded(prefix + frame(5, bytes(21)) + sample(0)
                                + raw_fifo(1) + sample(1) + end(2)), "RAW_FIFO payload")

    def test_raw_fifo_timestamp_regression_is_preserved_and_never_repaired(self):
        ticks = [772668, 772608, 773059]
        wire = frame(0, info(raw_fifo_frames=True))
        for seq, tick in enumerate(ticks):
            wire += raw_fifo(seq, ticks=tick) + sample(seq, ticks=tick)
        wire += end(3)
        result = decoded(wire)
        self.assert_bad(result, "invalid sensor intervals")
        self.assertEqual([sample[2] for sample in result.samples], ticks)
        self.assertEqual(len(result.fifo_raw), 3)
        quality = result.finalize()
        self.assertEqual(quality["timestamp_decrease_count"], 1)
        self.assertEqual(quality["timestamp_wraps"], 0)
        self.assertIsNone(quality["sensor_duration_s"])
        self.assertIsNone(quality["interval_statistics"]["actual_rate_hz"])
        self.assertFalse(quality["timestamp_intervals_valid"])

    def test_raw_fifo_flag_is_boolean_and_matches_handshake(self):
        with self.assertRaisesRegex(ValueError, "raw_fifo_frames"):
            capture.validate_info(info(raw_fifo_frames="true"))
        result = decoded(frame(0, info(raw_fifo_frames=True)) + raw_fifo(0) + sample(0)
                         + raw_fifo(1) + sample(1) + end(2), info=info())
        self.assert_bad(result, "differs from pre-capture INFO")
        # Legacy recordings legitimately have neither advertisement nor raw frames.
        self.assertTrue(decoded(stream()).finalize()["usable"])

    def test_valid_si_conversion_order_and_actual_sensor_time(self):
        result = decoded(stream())
        self.assertTrue(result.finalize()["usable"], result.errors)
        rows = list(result.si_rows())
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0][0], 0)
        self.assertAlmostEqual(rows[1][0], 192 * 25e-6)
        self.assertAlmostEqual(rows[0][1], 1000 * 0.000488 * 9.80665)
        self.assertAlmostEqual(rows[0][2], -1500 * 0.000488 * 9.80665)
        self.assertAlmostEqual(rows[0][3], 2049 * 0.000488 * 9.80665)
        self.assertAlmostEqual(rows[0][4], 100 * 0.070 * math.pi / 180)
        self.assertAlmostEqual(rows[0][5], -200 * 0.070 * math.pi / 180)
        self.assertAlmostEqual(rows[0][6], 300 * 0.070 * math.pi / 180)
        # MCU readouts differ by 100 us; those must never become 100-us sample times.
        self.assertNotEqual(rows[1][0], 100e-6)

    def test_every_two_chunk_boundary(self):
        wire = stream()
        for cut in range(len(wire) + 1):
            result = capture.Capture()
            result.feed(wire[:cut])
            result.feed(wire[cut:])
            self.assertTrue(result.finalize()["usable"], f"cut={cut}: {result.errors}")

    def test_byte_at_a_time_and_seeded_random_chunks(self):
        wire = stream(21)
        for sizes in ([1] * len(wire), [random.Random(i).randint(1, 80) for i in range(len(wire))]):
            result = capture.Capture(duration_ms=100)
            start = 0
            for size in sizes:
                result.feed(wire[start:start + size])
                start += size
                if start >= len(wire):
                    break
            self.assertTrue(result.finalize()["usable"], result.errors)

    def test_crc_corruption_never_passes(self):
        broken = bytearray(sample(1))
        broken[15] ^= 0x08
        result = decoded(frame(0, info()) + sample(0) + broken + sample(2) + sample(3) + end())
        self.assert_bad(result, "CRC32")

    def test_sequence_gap_and_duplicate_fail(self):
        for seqs in ((0, 1, 3, 4), (0, 1, 1, 2)):
            result = decoded(frame(0, info()) + b"".join(sample(i) for i in seqs) + end())
            self.assert_bad(result, "sequence discontinuity")

    def test_timestamp_wrap_unwraps(self):
        modulus = 1 << 24
        ticks = [modulus - 300, modulus - 108, 85, 277]
        result = decoded(frame(0, info()) + b"".join(sample(i, tick) for i, tick in enumerate(ticks)) + end())
        self.assertTrue(result.finalize()["usable"], result.errors)
        self.assertEqual(result.finalize()["timestamp_wraps"], 1)
        self.assertEqual(result.finalize()["timestamp_decrease_count"], 1)
        self.assertTrue(result.finalize()["timestamp_intervals_valid"])
        self.assertAlmostEqual(result.times_s[-1], 577 * 25e-6)

    def test_600_second_capture_crosses_sensor_wrap(self):
        result = capture.Capture(duration_ms=600000)
        result.feed(frame(0, info()))
        count = 600 * 208
        for start in range(0, count, 1000):
            result.feed(b"".join(sample(i, round(i * 1e6 / (208 * 25)) % (1 << 24))
                                 for i in range(start, min(start + 1000, count))))
        result.feed(end(count))
        self.assertTrue(result.finalize()["usable"], result.errors)
        self.assertEqual(result.finalize()["timestamp_wraps"], 1)
        self.assertAlmostEqual(result.times_s[-1], (count - 1) / 208, delta=25e-6)
        self.assertFalse(result.finalize()["timebase_calibrated"])

    def test_status_frames_are_retained_and_ordered(self):
        result = decoded(frame(0, info()) + status() + sample(0) + sample(1) + status(2) + end(2))
        self.assertTrue(result.finalize()["usable"], result.errors)
        self.assertEqual(result.finalize()["status_count"], 2)
        self.assertEqual(result.status[1]["seq_next"], 2)
        self.assertEqual(result.status[0]["temp_raw"], -128)
        self.assert_bad(decoded(frame(0, info()) + status(1) + sample(0) + sample(1) + end(2)),
                        "STATUS seq_next")
        self.assert_bad(decoded(frame(0, info()) + status(temp_c=None) + sample(0) + sample(1) + end(2)),
                        "STATUS temp_c")
        self.assert_bad(decoded(status() + stream()), "status received before metadata")

    def test_zero_duplicate_backward_and_gap_timestamps_fail(self):
        for ticks in ((0, 0, 0, 0), (0, 192, 192, 384), (1000, 800, 600, 400), (0, 192, 576, 768)):
            result = decoded(frame(0, info()) + b"".join(sample(i, tick) for i, tick in enumerate(ticks)) + end())
            self.assert_bad(result, "invalid sensor intervals")

    def test_timestamp_high_bits_fail(self):
        result = decoded(frame(0, info()) + sample(0, 1 << 24) + sample(1, (1 << 24) + 192) + end(2))
        self.assert_bad(result, "outside 24-bit")

    def test_rail_overrun_and_missing_timestamp_flags_fail(self):
        names = ("accel_x_rail", "accel_y_rail", "accel_z_rail", "gyro_x_rail",
                 "gyro_y_rail", "gyro_z_rail", "fifo_overrun")
        for bit, name in enumerate(names):
            result = decoded(frame(0, info()) + sample(0, flags=0x80 | 1 << bit) + sample(1) + end(2))
            self.assert_bad(result, name)
        self.assert_bad(decoded(frame(0, info()) + sample(0, flags=0) + sample(1) + end(2)),
                        "timestamp_missing")
        self.assert_bad(decoded(frame(0, info()) + sample(0, flags=0x180) + sample(1) + end(2)),
                        "unknown_flags")

    def test_missing_truncated_and_mismatched_end_fail(self):
        self.assert_bad(decoded(stream()[:-len(end())]), "missing END")
        self.assert_bad(decoded(stream()[:-3]), "truncated stream")
        self.assert_bad(decoded(frame(0, info()) + sample(0) + sample(1) + end(3)), "sample_count")

    def test_nonzero_or_absent_end_counters_fail(self):
        for key in ("dropped_frames", "i2c_errors", "fifo_overruns"):
            self.assert_bad(decoded(frame(0, info()) + sample(0) + sample(1) + end(2, **{key: 1})), key)
        self.assert_bad(decoded(frame(0, info()) + sample(0) + sample(1) + frame(2, {"sample_count": 2})),
                        "must be a nonnegative integer")

    def test_bad_order_duplicate_metadata_and_data_after_end_fail(self):
        for data, expected in ((sample(0) + frame(0, info()) + sample(1) + end(2), "sample received before"),
                               (frame(0, info()) + stream(), "duplicate or late metadata"),
                               (stream() + sample(4), "frame received after END"),
                               (stream() + end(), "duplicate END")):
            self.assert_bad(decoded(data), expected)

    def test_invalid_version_type_length_and_garbage_fail(self):
        variants = ((frame(1, b"\x00" * 28, version=2), "unsupported frame version"),
                    (frame(7, {}), "unknown frame type"),
                    (frame(1, b"\x00" * 27), "sample payload"),
                    (capture.HEADER.pack(capture.MAGIC, 1, 1, 5000), "exceeds maximum"),
                    (b"random bad input", "framing lost"))
        for invalid, expected in variants:
            self.assert_bad(decoded(invalid + stream()), expected)

    def test_firmware_error_frame_invalidates_capture(self):
        self.assert_bad(decoded(frame(3, {"error": "I2C read failed"}) + stream()), "firmware error")

    def test_strict_metadata_and_json_validation(self):
        for patch in ({"uid": "0000000000000000"}, {"name": "JumpHeight-E2C4"},
                      {"recorder": "production"}, {"build": ""}, {"odr_hz": False},
                      {"accel_g_per_lsb": 0.01}, {"gyro_dps_per_lsb": 70},
                      {"timestamp_tick_us": 1000}, {"timestamp_mode": "mcu"}, {"timestamp_bits": 32}):
            with self.assertRaises(ValueError):
                capture.validate_info(info(**patch))
        for payload in (b"[]", b'{"a":NaN}', b'{"a":1e999}', b'{"a":1,"a":2}', b"\xff"):
            self.assert_bad(decoded(frame(0, payload) + sample(0) + sample(1) + end(2)), "invalid type 0 JSON")

    def test_metadata_must_match_handshake(self):
        self.assert_bad(decoded(stream(), info=info(build="different-build")), "differs from pre-capture INFO")

    def test_requested_duration_count_guard(self):
        self.assert_bad(decoded(stream(), duration_ms=1000), "inconsistent with requested duration")
        self.assertTrue(decoded(stream(208), duration_ms=1000).finalize()["usable"])

    def test_finalization_is_idempotent(self):
        result = decoded(stream())
        times = list(result.times_s)
        result.finalize()
        self.assertEqual(result.times_s, times)
        with self.assertRaisesRegex(ValueError, "already been finalized"):
            result.feed(b"anything")


class FakeSerial:
    def __init__(self, wire=b"", response=None):
        self.wire = io.BytesIO(wire)
        self.lines = list(response if response is not None else
                          [b"JH6 INFO " + json.dumps(info()).encode() + b"\n", b"OK info\n"])
        self.writes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def write(self, data):
        self.writes.append(data)

    def flush(self):
        pass

    def readline(self, limit):
        return self.lines.pop(0) if self.lines else b""

    def read(self, size):
        return self.wire.read(size)

    @property
    def in_waiting(self):
        return len(self.wire.getvalue()) - self.wire.tell()


class PinningAndOutputTests(unittest.TestCase):
    def port(self, uid=capture.DEFAULT_UID, path="/dev/cu.test"):
        return SimpleNamespace(serial_number=uid, device=path, vid=0x2886, pid=0x0045)

    def test_exact_unique_usb_identity(self):
        right, wrong = self.port(), self.port("0000000000000000", "/dev/cu.wrong")
        self.assertIs(capture.select_port([wrong, right]), right)
        for ports, path in (([wrong], None), ([right, right], None), ([right, wrong], wrong.device),
                            ([self.port(None)], None)):
            with self.assertRaisesRegex(ValueError, "exactly one USB"):
                capture.select_port(ports, requested_port=path)

    def test_info_handshake_and_partial_lines(self):
        response = b"JH6 INFO " + json.dumps(info()).encode() + b"\n"
        connection = FakeSerial(response=[response[:10], response[10:40], response[40:], b"OK info\n"])
        self.assertEqual(capture.query_info(connection), info())
        self.assertEqual(connection.writes, [b"info\n"])

    def test_bad_info_terminator_duplicate_and_timeout(self):
        for response in ([b"OK info\n"],
                         [b"JH6 INFO {}\n", b"JH6 INFO {}\n"],
                         [b"ERR unknown command\n"], [b"x" * 4097]):
            with self.assertRaises(ValueError):
                capture.query_info(FakeSerial(response=response))
        with self.assertRaisesRegex(ValueError, "timed out"):
            capture.query_info(FakeSerial(response=[]), timeout_s=0.001)

    def test_success_outputs_and_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            wire = stream()
            (path / "raw.bin").write_bytes(wire)
            manifest = capture.write_outputs(path, decoded(wire), {"test": True})
            self.assertTrue(manifest["quality"]["usable"])
            self.assertEqual(manifest["files"]["raw.bin"]["sha256"], capture._sha256(path / "raw.bin"))
            with (path / "imu.csv").open() as handle:
                reader = csv.reader(handle)
                self.assertEqual(tuple(next(reader)), capture.IMU_COLUMNS)
                self.assertEqual(len(list(reader)), 4)
            self.assertIn("Uncalibrated", manifest["interpretation"]["imu_csv"])

    def test_failure_outputs_preserve_evidence_without_imu_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            wire = stream()[:-1]
            (path / "raw.bin").write_bytes(wire)
            manifest = capture.write_outputs(path, decoded(wire), {})
            self.assertFalse(manifest["quality"]["usable"])
            self.assertEqual((path / "raw.bin").read_bytes(), wire)
            self.assertTrue((path / "raw.csv").exists())
            self.assertTrue((path / "manifest.json").exists())
            self.assertFalse((path / "imu.csv").exists())

    def run_fake(self, path, connection, ports=None, lock_error=None, seconds=0.1):
        factory = mock.Mock(return_value=connection)
        modules = {
            "serial": SimpleNamespace(Serial=factory),
            "serial.tools": SimpleNamespace(list_ports=SimpleNamespace(comports=lambda: ports or [self.port()])),
        }
        args = argparse.Namespace(seconds=seconds, uid=capture.DEFAULT_UID, port=None, output_dir=path)
        with mock.patch.dict("sys.modules", modules), mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", io.StringIO()), mock.patch.object(capture, "secure_serial_port", return_value={"kernel_exclusive": True, "method": "test_fixture"}, side_effect=lock_error):
            status = capture.run_capture(args)
        return status, factory

    def test_kernel_exclusive_helper_uses_tiocexcl_without_claiming_existing_ownership(self):
        connection = SimpleNamespace(fileno=lambda: 123)
        with mock.patch("fcntl.ioctl") as ioctl:
            evidence = capture.secure_serial_port(connection)
        ioctl.assert_called_once_with(123, termios.TIOCEXCL, 0)
        self.assertTrue(evidence["kernel_exclusive"])
        self.assertFalse(evidence["existing_owner_verification"])

    def test_kernel_lock_failure_prevents_all_device_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new"
            connection = FakeSerial(stream(21))
            status, _ = self.run_fake(path, connection, lock_error=OSError("TIOCEXCL denied"))
            self.assertEqual(status, 2)
            self.assertEqual(connection.writes, [])
            self.assertFalse((path / "imu.csv").exists())
            manifest = json.loads((path / "manifest.json").read_text())
            self.assertFalse(manifest["quality"]["usable"])
            self.assertTrue(any("TIOCEXCL denied" in error for error in manifest["quality"]["errors"]))

    def test_oversized_capture_for_legacy_firmware_sends_only_info(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new"
            connection = FakeSerial()
            status, _ = self.run_fake(path, connection, seconds=61)
            self.assertEqual(status, 2)
            self.assertEqual(connection.writes, [b"info\n"])
            manifest = json.loads((path / "manifest.json").read_text())
            self.assertEqual(manifest["provenance"]["device_capture_limit_ms"], 60000)
            self.assertTrue(any("exceeds firmware capture limit" in error for error in manifest["quality"]["errors"]))

    def test_advertised_capture_limits_are_strict(self):
        self.assertEqual(capture.duration_limit_ms(info()), 60000)
        self.assertEqual(capture.duration_limit_ms(info(max_duration_ms=1200000)), 1200000)
        for invalid in (True, "1200000", 0, 99, 1200001):
            with self.assertRaisesRegex(ValueError, "max_duration_ms"):
                capture.validate_info(info(max_duration_ms=invalid))

    def test_invalid_serial_descriptor_is_rejected(self):
        with mock.patch("fcntl.ioctl") as ioctl:
            with self.assertRaisesRegex(RuntimeError, "valid file descriptor"):
                capture.secure_serial_port(SimpleNamespace(fileno=lambda: -1))
        ioctl.assert_not_called()

    def test_complete_serial_flow_never_1200_baud_and_byte_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new"
            wire = stream(21)
            connection = FakeSerial(wire)
            status, factory = self.run_fake(path, connection)
            self.assertEqual(status, 0)
            self.assertEqual(connection.writes, [b"info\n", b"capture 100\n"])
            self.assertEqual(factory.call_args.kwargs["baudrate"], 115200)
            self.assertTrue(factory.call_args.kwargs["exclusive"])
            self.assertEqual((path / "raw.bin").read_bytes(), wire)
            with (path / "host_receive.csv").open() as handle:
                reader = csv.DictReader(handle)
                self.assertEqual(tuple(reader.fieldnames), capture.RECEIVE_COLUMNS)
                rows = list(reader)
                self.assertEqual(int(rows[0]["byte_start"]), 0)
                self.assertEqual(int(rows[-1]["byte_end"]), len(wire))
                for row in rows:
                    self.assertLessEqual(int(row["monotonic_before_ns"]), int(row["monotonic_after_ns"]))
                    self.assertLessEqual(int(row["realtime_before_ns"]), int(row["realtime_after_ns"]))
            self.assertEqual(json.loads((path / "status.json").read_text()), [])

    def test_receive_log_records_empty_and_fragmented_reads_with_exact_offsets(self):
        wire = stream(21)

        class FragmentedSerial(FakeSerial):
            def __init__(self):
                super().__init__()
                self.chunks = [b"", wire[:17], wire[17:]]
                self.requested_sizes = []

            @property
            def in_waiting(self):
                return len(self.chunks[0]) if self.chunks else 0

            def read(self, size):
                self.requested_sizes.append(size)
                return self.chunks.pop(0) if self.chunks else b""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new"
            connection = FragmentedSerial()
            status, _ = self.run_fake(path, connection)
            self.assertEqual(status, 0)
            self.assertEqual(connection.requested_sizes, [1, 17, len(wire) - 17])
            self.assertEqual((path / "raw.bin").read_bytes(), wire)
            with (path / "host_receive.csv").open() as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([(int(row["byte_start"]), int(row["byte_end"])) for row in rows],
                             [(0, 0), (0, 17), (17, len(wire))])

    def test_production_firmware_gets_no_capture_command(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new"
            connection = FakeSerial(response=[b"JH6 INFO " + json.dumps(info(recorder="production")).encode() + b"\n", b"OK info\n"])
            status, _ = self.run_fake(path, connection)
            self.assertEqual(status, 2)
            self.assertEqual(connection.writes, [b"info\n"])
            self.assertFalse((path / "imu.csv").exists())
            self.assertTrue((path / "manifest.json").exists())

    def test_wrong_usb_device_is_never_opened(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new"
            status, factory = self.run_fake(path, FakeSerial(), [self.port("0000000000000000")])
            self.assertEqual(status, 2)
            factory.assert_not_called()
            self.assertEqual((path / "raw.bin").read_bytes(), b"")

    def test_existing_output_directory_is_not_modified(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            sentinel = path / "raw.bin"
            sentinel.write_bytes(b"preserve me")
            with self.assertRaises(FileExistsError):
                self.run_fake(path, FakeSerial(stream(21)))
            self.assertEqual(sentinel.read_bytes(), b"preserve me")
            self.assertEqual(list(path.iterdir()), [sentinel])

    def test_invalid_duration_before_output_or_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            for seconds in (float("nan"), float("inf"), -1, 0, 0.099, 1200.001):
                path = Path(directory) / "absent"
                args = argparse.Namespace(seconds=seconds, uid=capture.DEFAULT_UID, port=None, output_dir=path)
                with self.assertRaises(ValueError):
                    capture.run_capture(args)
                self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
