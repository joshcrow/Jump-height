"""Independent wire/clock fixtures; no hardware and no simulator dependency."""
import csv
import json
import math
from pathlib import Path
import random
import struct
import tempfile
import unittest
import zlib

import analyze_clock as clock


def frame(kind, payload):
    if isinstance(payload, dict):
        payload = json.dumps(payload).encode()
    raw = struct.pack("<4sBBH", b"JH6F", 1, kind, len(payload))+payload
    return raw+struct.pack("<I", zlib.crc32(raw)&0xffffffff)


def fixture(path, count=2501, sample_hz=208, fifo_scale=1.0278,
            mcu_scale=1.000025, host_scale=1.0, batch_samples=5,
            wrap=False, drift=0.0):
    """Generate true-time events and independently quantized clock observations."""
    metadata = {"odr_hz": 208, "timestamp_tick_us": 25, "build": "fixture"}
    data = bytearray(frame(0, metadata))
    samples, reads = [], []
    previous_end = 0
    last_time = -1
    for seq in range(count):
        true_time = seq/sample_hz
        origin_ticks = (1 << 24)-1000 if wrap else 0
        origin_mcu = (1 << 32)-100000 if wrap else 50000
        ticks = (origin_ticks+round(true_time*fifo_scale/25e-6)) % (1 << 24)
        mcu = (origin_mcu+round((true_time*mcu_scale+drift*true_time**2)*1e6)) % (1 << 32)
        row = (seq, mcu, ticks, 1, 2, 3, 4, 5, 6, 128, 9)
        samples.append(dict(zip(clock.RAW_COLUMNS, row)))
        data.extend(frame(1, struct.pack("<IIIhhhhhhHH", *row)))
        if seq % 101 == 0:
            data.extend(frame(4, {"seq_next": seq+1, "mcu_us": mcu,
                                  "temp_c": 25+true_time/1000, "hfclkstat": 0x10001,
                                  "timer4_prescaler": 4, "timer4_bitmode": 3}))
        if seq % batch_samples == batch_samples-1 or seq == count-1:
            # Last sample plus smooth, correlated delivery latency. Read time
            # never serves as a bound on any earlier sample's actual arrival.
            latency = .001+.0002*math.sin(true_time/12)
            time = round((true_time*host_scale+latency+100)*1e9)
            assert time > last_time
            reads.append({"byte_start": previous_end, "byte_end": len(data),
                "monotonic_before_ns": time-10000, "monotonic_after_ns": time,
                "realtime_before_ns": time+100000000000-10000,
                "realtime_after_ns": time+100000000000})
            previous_end, last_time = len(data), time
    data.extend(frame(2, {"sample_count": count, "dropped_frames": 0,
                          "i2c_errors": 0, "fifo_overruns": 0}))
    reads.append({"byte_start": previous_end, "byte_end": len(data),
        "monotonic_before_ns": last_time+1000000, "monotonic_after_ns": last_time+1010000,
        "realtime_before_ns": last_time+100001000000, "realtime_after_ns": last_time+100001010000})
    (path/"raw.bin").write_bytes(data)
    with (path/"raw.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, clock.RAW_COLUMNS)
        writer.writeheader(); writer.writerows(samples)
    with (path/"host_receive.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, list(reads[0]))
        writer.writeheader(); writer.writerows(reads)
    return bytes(data), reads


class ClockTests(unittest.TestCase):
    def test_ntp_offset_change_uncertainty_and_server_agreement_are_separate(self):
        def probe(time_s, offset, delay=.02, valid=True):
            ns = round(time_s*1e9)
            return {"schema": "jh6.ntp_probe.v1", "queries": [{
                "valid": valid, "server": server,
                "send_realtime_ns": ns, "receive_realtime_ns": ns+20000000,
                "send_monotonic_before_ns": ns, "send_monotonic_after_ns": ns+1000,
                "receive_monotonic_before_ns": ns+20000000,
                "receive_monotonic_after_ns": ns+20001000,
                "measurement": {"offset_s": offset+extra, "delay_s": delay,
                                "uncertainty_s": .011}}
                for server, extra in (("first", 0), ("second", .001))]}
        with tempfile.TemporaryDirectory() as directory:
            before, after = Path(directory)/"before.json", Path(directory)/"after.json"
            before.write_text(json.dumps(probe(100, .1)))
            after.write_text(json.dumps(probe(160, .106)))
            report = clock.ntp_reference(before, after)
        self.assertTrue(report["available"])
        self.assertFalse(report["absolute_timebase_certified"])
        self.assertEqual(len(report["per_server"]), 2)
        self.assertAlmostEqual(report["per_server"][0]["host_realtime_correction_ppm"], 100)
        self.assertAlmostEqual(report["per_server"][0]["conditional_uncertainty_halfwidth_ppm"], .022/60*1e6)
        self.assertTrue(report["server_scenario_intervals_overlap"])
        self.assertAlmostEqual(report["server_point_correction_spread_ppm"], 0)

    def test_blocked_ntp_does_not_become_zero_uncertainty(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"probe.json"
            path.write_text(json.dumps({"schema": "jh6.ntp_probe.v1", "queries": [
                {"valid": False, "error": "timeout", "server": "test"}]}))
            result = clock.ntp_reference(path, path)
        self.assertFalse(result["available"])
        self.assertIn("unmeasured", result["reason"])
        self.assertFalse(clock.ntp_reference(None, None)["available"])

    def test_relative_clocks_recover_known_scales_without_assuming_nominal_odr(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            fixture(path, count=26001)
            report, rows = clock.analyze(path, window_s=10)
        self.assertTrue(report["gates"]["binary_integrity"], report["integrity_issues"])
        fits = report["fits"]
        self.assertAlmostEqual(fits["mcu_seconds_per_nominal_fifo_second"]["slope"], 1.000025/1.0278, delta=1e-7)
        self.assertAlmostEqual(fits["host_monotonic_seconds_per_nominal_mcu_second"]["slope"], 1/1.000025, delta=4e-6)
        self.assertAlmostEqual(report["rates"]["samples_per_host_monotonic_second"], 208, delta=.001)
        self.assertEqual(report["rates"]["sample_intervals"], 26000)
        self.assertEqual(report["rates"]["host_selected_sample_intervals"], 25996)
        self.assertEqual(report["host"]["sample_batches"], 5201)
        self.assertEqual(sum("host_batch_index" in r for r in rows), 5201)
        self.assertFalse(report["timebase_calibrated"])
        self.assertFalse(report["gates"]["absolute_timebase_validation"])
        self.assertFalse(report["gates"]["long_capture_at_least_600_host_seconds"])
        self.assertIn("ONE", report["interval_diagnostics"]["fifo_interpretation"])
        self.assertEqual(report["interval_diagnostics"]["samples_with_more_than_nine_fifo_words"], 0)

    def test_both_timestamp_wraps_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            fixture(path, wrap=True)
            report, _ = clock.analyze(path)
        self.assertEqual(report["interval_diagnostics"]["fifo_wraps"], 1)
        self.assertEqual(report["interval_diagnostics"]["mcu_wraps"], 1)
        self.assertTrue(report["gates"]["binary_integrity"])

    def test_twenty_minutes_of_synthetic_ticks_handle_multiple_fifo_wraps(self):
        true_ticks = [(1<<24)-1000+round(i/208*1.0278/25e-6) for i in range(249601)]
        observed = [v % (1<<24) for v in true_ticks]
        recovered, wraps = clock.unwrap(observed, 24, "FIFO")
        self.assertEqual(wraps, 3)
        self.assertEqual(recovered, true_ticks)

    def test_raw_fifo_frame_is_crc_checked_and_preserved(self):
        data = frame(5, struct.pack("<I18s", 42, bytes(range(18))))
        decoded, issues = clock.parse_frames(data)
        self.assertFalse(issues)
        self.assertEqual(decoded[0]["data"], {"seq": 42, "raw_hex": bytes(range(18)).hex()})

    def test_ntp_monotonic_anchor_survives_host_wall_step(self):
        def probe(mono_s, wall_s, true_server_s):
            m, w, s = [round(v*1e9) for v in (mono_s, wall_s, true_server_s)]
            return {"schema": "jh6.ntp_probe.v1", "queries": [{"valid": True, "server": "reference",
                "send_realtime_ns": w-10000000, "receive_realtime_ns": w+10000000,
                "send_monotonic_before_ns": m-10000000, "send_monotonic_after_ns": m-10000000,
                "receive_monotonic_before_ns": m+10000000, "receive_monotonic_after_ns": m+10000000,
                "measurement": {"offset_s": (s-w)*1e-9, "delay_s": .02, "uncertainty_s": .01,
                                "t2_unix_ns": s, "t3_unix_ns": s}}]}
        with tempfile.TemporaryDirectory() as directory:
            before, after = Path(directory)/"before.json", Path(directory)/"after.json"
            before.write_text(json.dumps(probe(100, 100, 100.1)))
            after.write_text(json.dumps(probe(160, 159.8, 160.1006)))
            result = clock.ntp_reference(before, after)
        pair = result["per_server"][0]
        self.assertAlmostEqual(pair["host_monotonic_correction_ppm"], 10, places=5)
        self.assertGreater(pair["host_realtime_correction_ppm"], 3000)
        self.assertAlmostEqual(pair["direct_vs_offset_server_span_difference_s"], 0, places=10)

    def test_backward_fifo_tick_fails_full_capture_without_inventing_a_wrap(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            raw, _ = fixture(path, count=501)
            frames, _ = clock.parse_frames(raw)
            sample_frames = [f for f in frames if f["type"] == 1]
            current, previous = sample_frames[100], sample_frames[99]
            data = dict(current["data"])
            data["sensor_ticks"] = previous["data"]["sensor_ticks"]-60
            replacement = frame(1, struct.pack("<IIIhhhhhhHH", *(data[k] for k in clock.RAW_COLUMNS)))
            (path/"raw.bin").write_bytes(raw[:current["start"]]+replacement+raw[current["end"]:])
            # Keep the decoded evidence consistent; corruption is in the
            # sensor's emitted timestamp, not on the transport.
            with (path/"raw.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            rows[100]["sensor_ticks"] = str(data["sensor_ticks"])
            with (path/"raw.csv").open("w", newline="") as stream:
                writer = csv.DictWriter(stream, clock.RAW_COLUMNS)
                writer.writeheader(); writer.writerows(rows)
            result, _ = clock.analyze(path)
        self.assertFalse(result["gates"]["binary_integrity"])
        self.assertEqual(result["analysis_scope"]["raw_decoded_sample_count"], 501)
        self.assertEqual(result["analysis_scope"]["selected_sample_count"], 100)
        self.assertEqual(result["invalid_clock_intervals"]["fifo"][0]["signed_raw_delta"], -60)
        self.assertEqual(len(result["invalid_clock_intervals"]["fifo"]), 2)
        self.assertEqual(result["interval_diagnostics"]["fifo_wraps"], 0)

    def test_split_frame_maps_to_completion_batch_and_only_last_per_batch(self):
        samples = [{"end": 40}, {"end": 80}, {"end": 120}, {"end": 160}]
        reads = [{"byte_start": 0, "byte_end": 20},
                 {"byte_start": 20, "byte_end": 85},
                 {"byte_start": 85, "byte_end": 85},
                 {"byte_start": 85, "byte_end": 160}]
        mapped = clock.map_host(samples, reads)
        self.assertEqual([(r["sample_index"], r["batch_index"]) for r in mapped], [(1,1), (3,3)])

    def test_bad_crc_and_csv_disagreement_fail_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            data, _ = fixture(path, count=51)
            frames, _ = clock.parse_frames(data)
            first_sample = next(f for f in frames if f["type"] == 1)
            broken = bytearray(data)
            broken[first_sample["start"]+15] ^= 0xff
            (path/"raw.bin").write_bytes(broken)
            report, _ = clock.analyze(path)
        self.assertFalse(report["gates"]["binary_integrity"])
        self.assertTrue(any("CRC" in issue for issue in report["integrity_issues"]))
        self.assertTrue(any("raw.csv" in issue for issue in report["integrity_issues"]))

    def test_nonmonotonic_clock_not_repaired_by_sorting(self):
        for values in ([1,1], [100,90], [0, 1<<23]):
            with self.assertRaises(ValueError):
                clock.unwrap(values, 24, "test")

    def test_latency_envelope_is_explicitly_conditional_not_formal_se(self):
        x = [i*.1 for i in range(1001)]
        y = [1.01*v+.0002*math.sin(v) for v in x]
        unbounded, _ = clock.compare(x, y, 10)
        bounded, _ = clock.compare(x, y, 10, .002)
        self.assertFalse(unbounded["conditional_endpoint_latency_envelope"]["available"])
        envelope = bounded["conditional_endpoint_latency_envelope"]
        self.assertAlmostEqual(envelope["halfwidth_ppm"], 20)
        self.assertIn("assumption", envelope)
        self.assertTrue(bounded["moving_block_bootstrap"]["available"])

    def test_drift_shows_local_slope_change_despite_zero_global_residual_trend(self):
        x = [i*.1 for i in range(3001)]
        y = [v+1e-6*v*v for v in x]
        fit, _ = clock.compare(x, y, 20)
        self.assertGreater(fit["window_slope_change_first_to_last_ppm"], 500)
        self.assertGreater(fit["residual_lag1"], .99)
        self.assertIn("zero by construction", fit["residual_trend_note"])

    def test_missing_host_data_is_explicit_and_does_not_invent_absolute_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            fixture(path, count=51)
            (path/"host_receive.csv").unlink()
            report, _ = clock.analyze(path)
        self.assertFalse(report["host"]["available"])
        self.assertFalse(report["gates"]["host_observations"])
        self.assertEqual(len(report["fits"]), 1)
        self.assertTrue(report["host"]["notes"])

    def test_host_offsets_must_cover_exact_raw_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            raw, _ = fixture(path, count=51)
            with self.assertRaisesRegex(ValueError, "raw.bin"):
                clock.read_host(path/"host_receive.csv", len(raw)+1)

    def test_bootstrap_is_reproducible_and_not_available_for_few_windows(self):
        slopes = [1+.001*math.sin(i) for i in range(15)]
        self.assertEqual(clock.block_bootstrap(slopes), clock.block_bootstrap(slopes))
        self.assertFalse(clock.block_bootstrap(slopes[:4])["available"])


if __name__ == "__main__":
    unittest.main()
