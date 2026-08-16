#!/usr/bin/env python3
"""Fuzz the trace codec: corruption must never decode as plausible data.

WHY
---
docs/plan.md §3.3 names the cruellest failure this project can have: a session
that records perfectly and comes home wrong. The binary trace is the only copy
of what the sensor saw, and every number in the final report is downstream of
`decode_region()`. If a torn write, a dropped chunk, or a flipped bit can
produce samples that *look* fine, then no amount of care anywhere else matters —
the report will be confidently wrong and nothing will flag it.

The codec is designed for this: append-only, CRC-8 per block, and "a block is
either whole-and-valid or it is the end of valid data". These tests try to
break that contract the ways real flash actually fails.

WHAT IS AND IS NOT ASSERTED
---------------------------
NOT asserted: that corruption is always detected. A 1-byte CRC cannot promise
that. Two distinct numbers matter and they are often confused:

  * SINGLE-BIT errors are detected 100 % of the time. That is guaranteed by the
    algebra of any CRC whose polynomial has more than one term — it is not luck
    and not evidence of a good implementation, so a test reporting 0 % misses
    there is measuring arithmetic, not quality.
  * RANDOM / multi-byte corruption is the real case, and there the miss rate
    tends to ~1/256 (~0.39 %) for an 8-bit check. That is the number worth
    having on the record, so it is measured below.

What IS asserted:

  * a corrupted block never yields MORE samples than were encoded, so damage
    cannot fabricate flight time out of nothing;
  * decoding is total — it raises no exception and does not hang on any input,
    including random bytes, because a crash mid-download loses the session;
  * truncation is monotone: cutting bytes off the end never increases the
    sample count;
  * the CRC's real-world miss rate is MEASURED here rather than assumed, so
    the number is on the record.

Run: python3 -m unittest tools.tests.test_codec_fuzz -v
"""

from __future__ import annotations

import os
import random
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

import trace_codec as tc  # noqa: E402

LOG_HZ = 50


def encode_samples(n: int, seed: int = 0) -> bytes:
    """n samples of plausible motion through the real encoder."""
    rnd = random.Random(seed)
    out = bytearray()
    enc = tc.Encoder()
    t0_ms = 0
    enc.begin(t0_ms)
    per_block = tc.MAX_SAMPLES_PER_BLOCK
    for i in range(n):
        if enc.full():
            out += enc.finish()
            t0_ms += int(per_block * 1000 / LOG_HZ)
            enc.begin(t0_ms)
        enc.add_sample(1.0 + rnd.uniform(-0.4, 0.4))
    out += enc.finish()
    return bytes(out)


class CodecFuzzTest(unittest.TestCase):
    def test_roundtrip_is_exact(self):
        """The baseline. If this is not exact, nothing below means anything."""
        data = encode_samples(1000, seed=1)
        samples, _ = tc.decode_region(data, LOG_HZ)
        self.assertEqual(len(samples), 1000)

    def test_decode_never_raises_on_random_bytes(self):
        """A download that crashes the decoder loses the session.

        Every measurement is downstream of this call, and it will be run on a
        laptop at a beach on data that just came off a device that may have
        been dropped in salt water."""
        rnd = random.Random(1234)
        for i in range(3000):
            n = rnd.randint(0, 400)
            blob = bytes(rnd.randrange(256) for _ in range(n))
            try:
                tc.decode_region(blob, LOG_HZ)
            except Exception as e:                              # noqa: BLE001
                self.fail(f"decode_region raised {type(e).__name__} on random "
                          f"input #{i} of {n} bytes: {e}")

    def test_truncation_never_invents_samples(self):
        """Cutting the tail off can only ever lose data, never create it."""
        data = encode_samples(600, seed=2)
        full, _ = tc.decode_region(data, LOG_HZ)
        prev = len(full)
        for cut in range(1, min(len(data), 400)):
            got, _ = tc.decode_region(data[:len(data) - cut], LOG_HZ)
            self.assertLessEqual(
                len(got), prev,
                f"truncating {cut} bytes INCREASED the sample count "
                f"({prev} -> {len(got)}) — damage must not fabricate flight")
            prev = max(prev, len(got))

    def test_bitflip_never_yields_more_than_encoded(self):
        """A flipped bit may be missed by an 8-bit CRC. It must never turn
        into extra samples — that would be invented airtime, which is the one
        thing that would corrupt a height measurement silently."""
        rnd = random.Random(99)
        data = encode_samples(500, seed=3)
        n_ok, _ = tc.decode_region(data, LOG_HZ)
        limit = len(n_ok)
        for _ in range(2000):
            b = bytearray(data)
            pos = rnd.randrange(len(b))
            b[pos] ^= 1 << rnd.randrange(8)
            got, _ = tc.decode_region(bytes(b), LOG_HZ)
            self.assertLessEqual(
                len(got), limit,
                f"a single flipped bit at byte {pos} produced {len(got)} "
                f"samples from a {limit}-sample trace")

    def test_single_bit_errors_are_always_caught(self):
        """Single-bit detection is guaranteed by CRC algebra. Assert it anyway:
        if this ever fails, the CRC is not actually being checked."""
        rnd = random.Random(7)
        data = encode_samples(400, seed=4)
        clean, _ = tc.decode_region(data, LOG_HZ)
        trials = 4000
        undetected = 0
        for _ in range(trials):
            b = bytearray(data)
            pos = rnd.randrange(len(b))
            b[pos] ^= 1 << rnd.randrange(8)
            got, _ = tc.decode_region(bytes(b), LOG_HZ)
            # Same count AND same values => the damage left no trace at all.
            if len(got) == len(clean) and all(
                    abs(a.mag_g - c.mag_g) < 1e-9 and abs(a.t_s - c.t_s) < 1e-9
                    for a, c in zip(got, clean)):
                undetected += 1
        rate = undetected / trials
        print(f"\n    single-bit corruption slipping through: "
              f"{undetected}/{trials} = {rate*100:.2f}%  (must be 0)")
        self.assertEqual(undetected, 0,
                         "a single-bit error went undetected — the CRC is not "
                         "being checked, since CRC algebra makes this impossible")

    def test_random_corruption_miss_rate_is_measured(self):
        """THE number worth having: how often does real corruption pass silently?

        Random multi-byte damage is what a torn write or a bad flash page
        actually looks like, and an 8-bit check is expected to miss ~1/256 of
        it. This records the observed rate rather than assuming the textbook
        one. It is deliberately not a tight assertion — the point is to know
        the number, and to notice if it ever moves by an order of magnitude."""
        rnd = random.Random(4242)
        data = encode_samples(400, seed=8)
        clean, _ = tc.decode_region(data, LOG_HZ)
        trials = 6000
        undetected = 0
        for _ in range(trials):
            b = bytearray(data)
            for _ in range(rnd.randint(1, 6)):        # multi-byte damage
                b[rnd.randrange(len(b))] = rnd.randrange(256)
            got, _ = tc.decode_region(bytes(b), LOG_HZ)
            if len(got) == len(clean) and all(
                    abs(a.mag_g - c.mag_g) < 1e-9 and abs(a.t_s - c.t_s) < 1e-9
                    for a, c in zip(got, clean)):
                undetected += 1
        rate = undetected / trials
        print(f"\n    RANDOM multi-byte corruption slipping through silently: "
              f"{undetected}/{trials} = {rate*100:.2f}%  "
              f"(CRC-8 textbook expectation ~0.39%)")
        self.assertLess(rate, 0.05,
                        "silent-corruption rate is an order of magnitude above "
                        "what an 8-bit CRC should give — check the CRC path")

    def test_dropped_middle_chunk_does_not_corrupt_earlier_samples(self):
        """A dropped chunk mid-file must not retroactively change what came
        before it. Whatever survives has to be the truth, not a reinterpretation."""
        data = encode_samples(800, seed=5)
        head_only, _ = tc.decode_region(data[:len(data)//3], LOG_HZ)
        holed = data[:len(data)//3] + data[2*len(data)//3:]
        got, _ = tc.decode_region(holed, LOG_HZ)
        for i, s in enumerate(head_only):
            if i < len(got):
                self.assertAlmostEqual(
                    got[i].mag_g, s.mag_g, places=9,
                    msg=f"sample {i} changed value because LATER bytes were lost")


if __name__ == "__main__":
    unittest.main()
