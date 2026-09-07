#!/usr/bin/env python3
"""Three codec contracts the existing tests do not reach.

WHY THIS EXISTS
---------------
`sim/trace_codec.py` came out of the 2026-09-06 mutation campaign the
healthiest module measured — 26 of 35 mutants killed, against 12 of 29 for
the detector. Its fuzz test and its C++ parity harness do real work. But
three of the nine survivors are reachable and land on contracts the file
states in prose:

  1. `return min(v, 65535)` -> 65536 survived. The function's own docstring
     says it "must match trace_codec.h::milli_g_from_g() bit for bit", and
     65535 is the uint16 saturation point. Reaching it needs |a| >= 65.5 g,
     which the +-16 g part cannot produce, so the fuzz test never gets there
     — but a cross-language bit-for-bit claim should not depend on the input
     distribution to hold.

  2. `if count == 0 or count > MAX_SAMPLES_PER_BLOCK:` -> `and` survived.
     With `and`, a zero-count block stops being rejected on its own. The
     encoder never emits one (`finish()` returns b"" at count 0), so the
     only source is corruption — which is the entire job of the decoder this
     guard sits in.

  3. `add_sample`'s `return False` when full, and `return True` on success,
     each flipped and survived. Callers use that boolean to know whether a
     sample was stored; inverting it means a dropped sample reports as
     stored, which is the "silent failure looks like a pass" shape CLAUDE.md
     rule 3 names.

The other six survivors were triaged as unreachable or behaviourally
equivalent: three `<` -> `<=` flips whose alternate branch rejects the same
input anyway, and the loop bounds, where an off == len(data) iteration
decodes an empty slice and is refused by the header-length check.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "sim"))

import trace_codec as tc  # noqa: E402

# Written as literals on purpose — a test that reads the constant it pins
# moves with it. See tools/tests/test_lever_arm_invariants.py.
UINT16_MAX = 65535


EXPECTED_MAX_SAMPLES = 255
EXPECTED_HEADER_BYTES = 5
LOG_HZ = 50  # config/params.json firmware.log_hz; decode_one_block needs a rate


def _enc():
    """Encoder() takes no constructor args; begin(t0_ms) starts a block."""
    e = tc.Encoder()
    e.begin(0)
    return e


class MilliGSaturation(unittest.TestCase):
    """Contract 1: the uint16 cap, declared bit-for-bit with the C++ twin."""

    def test_saturates_at_uint16_max(self) -> None:
        self.assertEqual(
            tc.milli_g_from_g(100.0), UINT16_MAX,
            "100 g must saturate to 65535. The value is packed into a uint16; "
            "returning 65536 overflows it, and this function is declared "
            "bit-for-bit identical to trace_codec.h::milli_g_from_g().")

    def test_just_below_saturation_is_not_clamped(self) -> None:
        """Pins the cap from below, so LOWERING it also fails."""
        self.assertEqual(
            tc.milli_g_from_g(65.0), 65000,
            "65 g is inside uint16 range and must pass through unclamped.")

    def test_the_saturation_point_itself(self) -> None:
        self.assertEqual(tc.milli_g_from_g(65.535), UINT16_MAX)
        self.assertEqual(tc.milli_g_from_g(65.536), UINT16_MAX,
                         "one milli-g past the cap must still clamp, not wrap")

    def test_non_finite_and_negative_map_to_zero(self) -> None:
        """Already covered elsewhere; asserted here so the contract reads
        whole in one place."""
        self.assertEqual(tc.milli_g_from_g(float("nan")), 0)
        self.assertEqual(tc.milli_g_from_g(float("-inf")), 0)
        self.assertEqual(tc.milli_g_from_g(-1.0), 0)


class ZeroCountBlockIsRefused(unittest.TestCase):
    """Contract 2: a zero-count block is corrupt, on its own."""

    def _block(self, t0_ms: int, count: int, payload: bytes = b"") -> bytes:
        """A block whose CRC is VALID, so only the guard under test can reject it.

        This matters, and it caught a bad test on 2026-09-06: the first draft
        appended zero bytes as the checksum, so every crafted block failed
        the CRC check FIRST and was refused whichever way the guard went. The
        assertions passed for the wrong reason, and the mutations they were
        written to kill survived. A negative test has to fail for exactly the
        reason it claims.
        """
        header = struct.pack("<IB", t0_ms, count)
        body = header + payload
        return body + bytes([tc.crc8(body)])

    def test_a_valid_block_really_is_accepted(self) -> None:
        """The control. Without it, every rejection below could be an artefact
        of a malformed fixture rather than the guard doing its job."""
        payload = struct.pack("<H", 1000)      # one sample, 1.000 g
        res = tc.decode_one_block(self._block(1234, 1, payload), LOG_HZ)
        self.assertTrue(
            res.ok,
            "the fixture builder must produce a block the decoder accepts, "
            "or the negative tests below prove nothing.")
        self.assertEqual(res.count, 1)

    def test_zero_count_is_rejected(self) -> None:
        res = tc.decode_one_block(self._block(1000, 0), LOG_HZ)
        self.assertFalse(
            res.ok,
            "a block declaring zero samples must be refused ON THE COUNT, "
            "even with a valid CRC. The encoder never emits one, so its only "
            "source is corruption — and this decoder exists to catch it.")
        self.assertEqual(res.bytes_consumed, 0)

    def test_a_count_over_the_maximum_is_rejected(self) -> None:
        """The other half of the same condition.

        MAX_SAMPLES_PER_BLOCK is 255 and count is a single byte, so the
        over-max branch is unreachable through a real header — it exists for
        the C++ twin, where count is wider. Asserted through the boundary
        instead: 255 with a matching payload must be ACCEPTED, which fails if
        the maximum is lowered.
        """
        payload = struct.pack("<H", 1000) * EXPECTED_MAX_SAMPLES
        res = tc.decode_one_block(
            self._block(1000, EXPECTED_MAX_SAMPLES, payload), LOG_HZ)
        self.assertTrue(
            res.ok,
            f"a full block of {EXPECTED_MAX_SAMPLES} samples with a valid CRC "
            "must decode. If this fails, MAX_SAMPLES_PER_BLOCK was lowered.")
        self.assertEqual(res.count, EXPECTED_MAX_SAMPLES)

    def test_short_payload_for_the_declared_count_is_rejected(self) -> None:
        header = struct.pack("<IB", 1000, EXPECTED_MAX_SAMPLES)
        data = header + b"\x00" * 4          # nowhere near 255 samples
        self.assertFalse(
            tc.decode_one_block(data, LOG_HZ).ok,
            "a declared count the buffer cannot hold must be refused.")

    def test_erased_flash_header_is_rejected(self) -> None:
        """Valid CRC on purpose, so only the t0_ms guard can refuse it."""
        payload = struct.pack("<H", 1000) * 3
        res = tc.decode_one_block(self._block(0xFFFFFFFF, 3, payload), LOG_HZ)
        self.assertFalse(
            res.ok,
            "t0_ms == 0xFFFFFFFF is erased QSPI fill, not a timestamp, and "
            "must be refused even when the block is otherwise well formed. "
            "Without a valid CRC here this test passed for the wrong reason.")

    def test_too_short_for_a_header_is_rejected(self) -> None:
        self.assertFalse(tc.decode_one_block(b"\x00" * (EXPECTED_HEADER_BYTES - 1), LOG_HZ).ok)

    def test_a_corrupted_crc_is_rejected(self) -> None:
        payload = struct.pack("<H", 1000)
        good = self._block(1234, 1, payload)
        bad = good[:-1] + bytes([good[-1] ^ 0xFF])
        self.assertFalse(
            tc.decode_one_block(bad, LOG_HZ).ok,
            "a flipped CRC byte must be refused — this is the check the other "
            "negative tests must NOT be relying on.")


class AddSampleReturnContract(unittest.TestCase):
    """Contract 3: the boolean says whether the sample was stored."""

    def test_returns_true_while_there_is_room(self) -> None:
        enc = _enc()
        self.assertTrue(
            enc.add_sample(1.0),
            "add_sample must report True when the sample was stored; callers "
            "use this to know whether data was kept.")
        self.assertEqual(enc.count(), 1)

    def test_returns_false_once_full_and_stores_nothing(self) -> None:
        enc = _enc()
        for _ in range(EXPECTED_MAX_SAMPLES):
            self.assertTrue(enc.add_sample(1.0))
        self.assertTrue(enc.full())
        before = enc.count()
        self.assertFalse(
            enc.add_sample(1.0),
            "add_sample must report False when the block is full. Reporting "
            "True would make a DROPPED sample look stored, which is exactly "
            "the silent failure CLAUDE.md rule 3 forbids.")
        self.assertEqual(
            enc.count(), before,
            "a refused sample must not be appended either.")

    def test_full_is_reached_at_the_documented_capacity(self) -> None:
        enc = _enc()
        for i in range(EXPECTED_MAX_SAMPLES - 1):
            enc.add_sample(1.0)
        self.assertFalse(
            enc.full(),
            f"a block must not be full at {EXPECTED_MAX_SAMPLES - 1} samples")
        enc.add_sample(1.0)
        self.assertTrue(
            enc.full(),
            f"a block must be full at exactly {EXPECTED_MAX_SAMPLES}")

    def test_empty_block_serializes_to_nothing(self) -> None:
        self.assertEqual(
            _enc().finish(), b"",
            "finish() at count 0 must emit b'' — matching "
            "trace_codec.h::Encoder::finish(), and the reason a zero-count "
            "block can only ever be corruption.")


if __name__ == "__main__":
    unittest.main()
