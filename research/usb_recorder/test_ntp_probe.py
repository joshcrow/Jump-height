"""Synthetic protocol checks; no network or clock configuration changes."""
import struct
import socket
import unittest
from unittest import mock

import ntp_probe


class NtpTests(unittest.TestCase):
    T1 = 1790000000 * 1_000_000_000

    def response(self, offset_ns=130_000_000, forward_ns=10_000_000,
                 reverse_ns=10_000_000, processing_ns=100_000):
        request = ntp_probe.make_request(self.T1)
        response = bytearray(48)
        response[0] = 0x24
        response[1] = 2
        response[3] = 0xEC  # 2^-20 seconds
        response[4:8] = struct.pack("!i", 655)  # ~10ms root delay
        response[8:12] = struct.pack("!I", 65)  # ~1ms root dispersion
        response[24:32] = request[40:48]
        t2 = self.T1 + forward_ns + offset_ns
        t3 = t2 + processing_ns
        response[32:40] = ntp_probe.encode_timestamp(t2)
        response[40:48] = ntp_probe.encode_timestamp(t3)
        t4 = self.T1 + forward_ns + reverse_ns + processing_ns
        return request, bytes(response), t4

    def parse(self, **kwargs):
        request, response, t4 = self.response(**kwargs)
        return ntp_probe.parse_response(response, request, self.T1, t4, t4-self.T1)

    def test_symmetric_delay_and_known_offset(self):
        result = self.parse()
        self.assertAlmostEqual(result["offset_s"], 0.130, places=8)
        self.assertAlmostEqual(result["delay_s"], 0.020, places=8)
        self.assertAlmostEqual(result["server_processing_s"], 0.0001, places=8)
        low, high = result["network_asymmetry_interval_s"]
        self.assertAlmostEqual(low, 0.12, places=8)
        self.assertAlmostEqual(high, 0.14, places=8)
        self.assertGreater(result["uncertainty_s"], 0.015)
        self.assertFalse(result["authenticated"])

    def test_asymmetric_delay_estimate_is_biased_but_interval_contains_truth(self):
        result = self.parse(forward_ns=18_000_000, reverse_ns=2_000_000)
        self.assertAlmostEqual(result["offset_s"], 0.138, places=8)
        low, high = result["network_asymmetry_interval_s"]
        self.assertLessEqual(low, 0.130)
        self.assertGreaterEqual(high, 0.130)

    def test_fraction_and_2036_era_wrap(self):
        for unix_ns in (self.T1 + 123456789, 2200000000 * 1_000_000_000 + 999999999):
            decoded = ntp_probe.decode_timestamp(ntp_probe.encode_timestamp(unix_ns), unix_ns)
            self.assertLessEqual(abs(decoded - unix_ns), 1)

    def test_origin_mode_version_leap_and_kod_rejected(self):
        request, response, t4 = self.response()
        variants = []
        for index, value in ((0, 0x23), (0, 0x14), (0, 0xE4), (1, 0), (1, 16), (24, 0)):
            malformed = bytearray(response)
            malformed[index] = value
            variants.append(bytes(malformed))
        variants += [response[:47], response[:32] + bytes(16)]
        for invalid in variants:
            with self.assertRaises(ValueError):
                ntp_probe.parse_response(invalid, request, self.T1, t4, t4-self.T1)

    def test_realtime_clock_step_rejected(self):
        request, response, t4 = self.response()
        with self.assertRaisesRegex(ValueError, "realtime/monotonic"):
            ntp_probe.parse_response(response, request, self.T1, t4, t4-self.T1+6_000_000)

    def test_negative_network_delay_rejected(self):
        request, response, t4 = self.response(processing_ns=50_000_000)
        with self.assertRaisesRegex(ValueError, "negative network delay"):
            ntp_probe.parse_response(response, request, self.T1, self.T1+1_000_000, 1_000_000)

    def test_failed_query_keeps_request_and_received_bytes(self):
        for received, expected_response in ((b"bad", b"bad".hex()), (TimeoutError("timeout"), None)):
            connection = mock.MagicMock()
            connection.__enter__.return_value = connection
            connection.getpeername.return_value = ("192.0.2.1", 123)
            if isinstance(received, Exception):
                connection.recv.side_effect = received
            else:
                connection.recv.return_value = received
            address = [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", ("192.0.2.1", 123))]
            with mock.patch.object(ntp_probe, "_resolve", return_value=address), mock.patch.object(ntp_probe.socket, "socket", return_value=connection):
                result = ntp_probe.probe("fixture.example")
            self.assertFalse(result["valid"])
            self.assertEqual(len(bytes.fromhex(result["request_hex"])), 48)
            self.assertEqual(result["response_hex"], expected_response)
            self.assertIn("error", result)
            self.assertIn("send_monotonic_before_ns", result)


if __name__ == "__main__":
    unittest.main()
