"""Parity test for the binary trace v2 codec (docs/ota.md §4.5, docs/sense.md
§3.2): encodes synthetic sample streams with the REAL firmware C++ encoder
(host-compiled, mirroring ./tools/jump simtest's C++/Python detector parity
pattern — see firmware/test/host_test.cpp), decodes the resulting bytes with
the Python mirror (sim/trace_codec.py), and diffs the result against a
reference CSV built directly from the same synthetic samples. Byte-identical
output against that reference is the acceptance bar (see trace_codec.h).

Run via ./tools/jump simtest, or directly:
    python3 -m pytest tools/tests/test_trace_codec.py -q
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

import trace_codec  # noqa: E402  (path insert must come first)

HARNESS_SRC = REPO / "firmware" / "test" / "trace_codec_harness.cpp"
LOG_HZ = 50  # matches config/params.json's firmware.log_hz default; the codec
             # itself takes this as a parameter (see trace_codec.h) rather
             # than hardcoding it, so the test states its own value explicitly.


def _gxx() -> str:
    gxx = shutil.which("g++") or shutil.which("c++")
    if not gxx:
        raise unittest.SkipTest("no g++/c++ on this machine")
    return gxx


def _build_harness(tmpdir: str) -> str:
    binp = str(Path(tmpdir) / "trace_codec_harness")
    r = subprocess.run(
        [_gxx(), "-std=c++14", "-Wall", "-Wextra", "-I", str(REPO / "firmware" / "include"),
         str(HARNESS_SRC), "-o", binp],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"trace_codec_harness failed to compile:\n{r.stderr}")
    return binp


def _encode(harness_bin: str, samples: list[tuple[float, float]]) -> bytes:
    """Run the C++ harness over `samples` ([(t_s, mag_g), ...]), returning
    the raw encoded bytes it wrote to stdout."""
    csv_in = "t,mag\n" + "".join(f"{t:.3f},{m:.3f}\n" for t, m in samples)
    r = subprocess.run([harness_bin], input=csv_in.encode(), capture_output=True)
    assert r.returncode == 0, f"harness exited {r.returncode}: {r.stderr!r}"
    return r.stdout


def _reference_csv(samples: list[tuple[float, float]]) -> str:
    """The wire CSV a client should see for exactly these samples — same
    3-decimal formatting the codec's decode_to_csv()/main.cpp's emit layer
    use. Samples are constructed at exact 0.001 g increments (see the
    generators below) so milli-g quantization is a lossless no-op and this
    reference is legitimately byte-exact, not merely "close"."""
    return "".join(f"{t:.3f},{m:.3f}\n" for t, m in samples)


def _evenly_spaced(t0: float, n: int, log_hz: int = LOG_HZ,
                   mag_fn=lambda i: 1.000) -> list[tuple[float, float]]:
    """n samples starting at t0, spaced exactly 1/log_hz apart — the format's
    actual operating assumption (see trace_codec.h)."""
    return [(round(t0 + i / log_hz, 3), round(mag_fn(i), 3)) for i in range(n)]


class TestTraceCodecParity(unittest.TestCase):
    """One shared compiled harness for every test in this class."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.mkdtemp(prefix="trace_codec_test_")
        cls.harness = _build_harness(cls._tmpdir)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _roundtrip(self, samples: list[tuple[float, float]]) -> None:
        encoded = _encode(self.harness, samples)
        decoded_csv = trace_codec.decode_to_csv(encoded, log_hz=LOG_HZ)
        self.assertEqual(decoded_csv, _reference_csv(samples))

    # ---------------------------------------------------------------- basics

    def test_single_short_block(self) -> None:
        """A handful of samples, well under one second — one block."""
        samples = _evenly_spaced(0.0, 10, mag_fn=lambda i: 1.0 + 0.001 * i)
        self._roundtrip(samples)

    def test_block_boundaries(self) -> None:
        """Several seconds of continuous samples span multiple 1 s blocks —
        the encoder's own block-closing policy (see trace_codec_harness.cpp),
        not just a single-block happy path."""
        samples = _evenly_spaced(0.0, LOG_HZ * 5, mag_fn=lambda i: 1.0 + 0.001 * (i % 7))
        encoded = _encode(self.harness, samples)
        # 5 whole seconds at LOG_HZ samples/s => 5 blocks of exactly LOG_HZ
        # samples each; confirm the byte count matches that arithmetic
        # exactly (not just "decodes to the right CSV") — pins block framing.
        expected_bytes = 5 * trace_codec.block_size(LOG_HZ)
        self.assertEqual(len(encoded), expected_bytes)
        decoded_csv = trace_codec.decode_to_csv(encoded, log_hz=LOG_HZ)
        self.assertEqual(decoded_csv, _reference_csv(samples))

    # ------------------------------------------------------------ edge cases

    def test_idle_gap(self) -> None:
        """A real recording gap (motion gate goes idle, then active again
        much later) — t keeps increasing but jumps by tens of seconds
        between bursts. No special-casing exists for this (see
        trace_codec.h / trace_codec_harness.cpp): it's just another
        "floor(t) changed" block boundary."""
        burst1 = _evenly_spaced(10.0, 20, mag_fn=lambda i: 1.0 + 0.001 * i)
        burst2 = _evenly_spaced(47.0, 20, mag_fn=lambda i: 0.9 + 0.001 * i)  # +37s gap
        samples = burst1 + burst2
        self._roundtrip(samples)

    def test_reboot_restarts_at_t_zero(self) -> None:
        """A device reboot mid-recording: t drops back down near 0 (jh_clock
        resets every power-on — see jh_clock.h). The trace region is
        append-only across boots, so the byte stream legitimately contains
        a block whose t0_ms is SMALLER than the previous block's. The codec
        must reproduce this faithfully (a higher layer — split_segments() in
        tools/jump and sim/run.py — is what turns "time went backwards" into
        separate sessions; the codec's job is just not to corrupt or drop
        data at that boundary)."""
        before_reboot = _evenly_spaced(118.0, 15, mag_fn=lambda i: 1.0 + 0.001 * i)
        after_reboot = _evenly_spaced(0.0, 15, mag_fn=lambda i: 1.0 + 0.002 * i)
        samples = before_reboot + after_reboot
        self._roundtrip(samples)

    def test_255_sample_cap_forces_new_block(self) -> None:
        """More than 255 samples inside one nominal second (an artificially
        high rate, but the cap is a hard wire-format limit — u8 count, see
        trace_codec.h) must split into multiple blocks, never overflow.

        Uses a local, higher log_hz (300) so the samples are still spaced
        exactly 1/log_hz apart — the format's real operating assumption
        (see trace_codec.h) — while still packing more than 255 of them
        into one nominal second; this isolates "does the count cap split
        blocks correctly" from "does time reconstruction hold" (covered by
        the other tests), rather than conflating the two.
        """
        hi_log_hz = 300
        samples = _evenly_spaced(0.0, hi_log_hz, log_hz=hi_log_hz,
                                 mag_fn=lambda i: 1.0 + 0.0005 * i)
        encoded = _encode(self.harness, samples)
        # First block: exactly the cap (255); second block: the remaining 45.
        expected_bytes = (trace_codec.block_size(255) +
                          trace_codec.block_size(hi_log_hz - 255))
        self.assertEqual(len(encoded), expected_bytes)
        decoded_csv = trace_codec.decode_to_csv(encoded, log_hz=hi_log_hz)
        self.assertEqual(decoded_csv, _reference_csv(samples))

    def test_partial_trailing_block_ignored(self) -> None:
        """Simulates a power-loss mid-write: the last block's bytes are cut
        off partway through. The decoder must recover every earlier whole
        block and stop cleanly at the torn one — never emit garbage, never
        raise."""
        good = _evenly_spaced(0.0, LOG_HZ * 2, mag_fn=lambda i: 1.0 + 0.001 * (i % 5))
        encoded = _encode(self.harness, good)
        # Chop off the last 10 bytes of the (necessarily multi-block) stream —
        # guaranteed to land inside the final block's payload/crc, never on
        # an exact block boundary, since each block here is well over 10 B.
        truncated = encoded[:-10]

        samples_out, consumed = trace_codec.decode(truncated, log_hz=LOG_HZ)
        # Exactly the whole blocks survive; the torn final block contributes
        # nothing, and decode() reports precisely how much it consumed.
        self.assertLess(consumed, len(truncated))
        expected_whole_blocks = len(truncated) // trace_codec.block_size(LOG_HZ)
        self.assertEqual(consumed, expected_whole_blocks * trace_codec.block_size(LOG_HZ))
        expected_csv = _reference_csv(good[:expected_whole_blocks * LOG_HZ])
        got_csv = "".join(f"{s.t_s:.3f},{s.mag_g:.3f}\n" for s in samples_out)
        self.assertEqual(got_csv, expected_csv)

    def test_corrupt_crc_ignored(self) -> None:
        """A single flipped bit inside an otherwise complete block (flash
        corruption, not truncation) must be caught by the CRC and treated
        the same way as a truncated block — stop, don't emit that block or
        anything after it."""
        good = _evenly_spaced(0.0, LOG_HZ * 3, mag_fn=lambda i: 1.0 + 0.001 * (i % 5))
        encoded = bytearray(_encode(self.harness, good))
        one_block = trace_codec.block_size(LOG_HZ)
        # Flip a bit in the middle of the SECOND block's payload.
        flip_at = one_block + 10
        encoded[flip_at] ^= 0x01
        samples_out, consumed = trace_codec.decode(bytes(encoded), log_hz=LOG_HZ)
        self.assertEqual(consumed, one_block)  # only the first block survives
        got_csv = "".join(f"{s.t_s:.3f},{s.mag_g:.3f}\n" for s in samples_out)
        self.assertEqual(got_csv, _reference_csv(good[:LOG_HZ]))

    def test_erased_flash_decodes_to_nothing(self) -> None:
        """A stretch of never-written QSPI flash reads back as all-0xFF —
        the decoder must recognize that as "no more data", not as a
        (nonsensical) 255-sample block at t0=4294967.295s."""
        erased = b"\xff" * 4096
        samples_out, consumed = trace_codec.decode(erased, log_hz=LOG_HZ)
        self.assertEqual(consumed, 0)
        self.assertEqual(samples_out, [])

    def test_crc8_matches_between_cpp_and_python(self) -> None:
        """Pin the CRC-8 variant itself (poly 0x07, init 0x00, no reflect,
        no final xor) — if this ever drifts between trace_codec.h and
        sim/trace_codec.py every other test in this file would still fail,
        but this one names the actual root cause directly."""
        # A block encoded by the C++ side must validate under the Python
        # crc8() re-derived from its own raw bytes — i.e. the two
        # implementations must agree on every byte fed through them.
        samples = _evenly_spaced(0.0, 30, mag_fn=lambda i: 1.0 + 0.0013 * i)
        encoded = _encode(self.harness, samples)
        body, stored_crc = encoded[:-1], encoded[-1]
        self.assertEqual(trace_codec.crc8(body), stored_crc)


if __name__ == "__main__":
    unittest.main()
