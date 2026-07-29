"""Binary trace v2 codec — Python mirror of firmware/include/trace_codec.h.

Read that header's top comment first; this module keeps the same field
names, constants, and semantics so the two stay easy to eyeball-diff. Used
by tools/tests/test_trace_codec.py to decode blocks a host-compiled C++
harness (firmware/test/trace_codec_harness.cpp) produced with the real
firmware encoder, and compare the result against a reference CSV — the
same C++/Python parity discipline jump_detector.h/sim/detector.py already
follow, extended to the on-flash trace format the Sense port (nRF52,
docs/sense.md §3.2) ships.

Wire format (one block) — see trace_codec.h for the full write-up:
    u32 t0_ms   little-endian, ms timestamp of the block's first sample
    u8  count   number of samples, 1..255
    count * u16 little-endian magnitude in milli-g
    u8  crc8    CRC-8 (poly 0x07, init 0x00, no reflect, no final xor) over
                every preceding byte of this block

Per-sample times are reconstructed assuming even spacing at 1/log_hz
seconds from t0_ms — only the block's first sample gets a real timestamp
(see trace_codec.h for why: ~2 bytes/sample instead of ~6+).

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

MAX_SAMPLES_PER_BLOCK = 255
HEADER_BYTES = 5  # t0_ms(4) + count(1)
CRC_BYTES = 1


def block_size(count: int) -> int:
    """Total on-wire size of a block holding `count` samples."""
    return HEADER_BYTES + count * 2 + CRC_BYTES


def crc8(data: bytes) -> int:
    """CRC-8, poly 0x07, init 0x00, no reflect, no final xor — must match
    trace_codec.h::crc8() bit for bit (see that file for why this one)."""
    crc = 0x00
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else (crc << 1) & 0xFF
    return crc


def milli_g_from_g(mag_g: float) -> int:
    """Encoder rounding rule: round-half-up (mag_g is a magnitude, >= 0),
    clamped to a u16 — must match trace_codec.h::milli_g_from_g().

    Non-finite guard (review-store.md finding #3): a bare `int(nan * 1000.0
    + 0.5)` RAISES (ValueError: cannot convert float NaN to integer) rather
    than the C++ side's undefined-behavior cast — a different failure mode,
    but still not the deliberate, cross-language "maps to 0" contract this
    format wants for both NaN and +-inf alike (Python's `int(inf)` raises
    OverflowError, so +inf isn't even accidentally survivable here the way
    it was in the old C++ code — see that function's own comment). Reachable
    from a garbled reading that ever reaches this encoder (e.g. Encoder.
    add_sample() on a corrupted upstream value); must match
    trace_codec.h::milli_g_from_g() bit for bit.
    """
    if not math.isfinite(mag_g):
        return 0
    if mag_g < 0.0:
        mag_g = 0.0
    v = int(mag_g * 1000.0 + 0.5)
    return min(v, 65535)


def g_from_milli_g(milli_g: int) -> float:
    return milli_g / 1000.0


class Encoder:
    """Mirror of trace_codec.h's Encoder — builds one block at a time.

    Not needed by the device (which only ever *decodes* on this platform;
    encoding happens in firmware/src/platform/nrf52/jh_store.cpp) or by the
    parity test (which encodes via the real C++ harness to test THAT code) —
    provided for symmetry/completeness and any future offline tooling that
    wants to synthesize a binary trace fixture in pure Python.
    """

    def __init__(self) -> None:
        self._t0_ms = 0
        self._mags_milli_g: list[int] = []

    def begin(self, t0_ms: int) -> None:
        self._t0_ms = t0_ms
        self._mags_milli_g = []

    def count(self) -> int:
        return len(self._mags_milli_g)

    def full(self) -> bool:
        return len(self._mags_milli_g) >= MAX_SAMPLES_PER_BLOCK

    def add_sample(self, mag_g: float) -> bool:
        if self.full():
            return False
        self._mags_milli_g.append(milli_g_from_g(mag_g))
        return True

    def finish(self) -> bytes:
        """Serialized header+payload+crc, or b"" if count()==0 (an empty
        block is never emitted — matches trace_codec.h::Encoder::finish())."""
        n = len(self._mags_milli_g)
        if n == 0:
            return b""
        body = struct.pack("<IB", self._t0_ms, n)
        body += struct.pack(f"<{n}H", *self._mags_milli_g)
        return body + bytes([crc8(body)])


@dataclass
class Sample:
    t_s: float
    mag_g: float


@dataclass
class BlockResult:
    ok: bool
    bytes_consumed: int
    t0_ms: int = 0
    count: int = 0
    samples: list[Sample] = field(default_factory=list)


def decode_one_block(data: bytes, log_hz: int) -> BlockResult:
    """Decode exactly one block from the FRONT of `data`.

    Mirrors trace_codec.h::decode_one_block() exactly, including which
    conditions count as "incomplete/corrupt" (see that function's doc
    comment): too few bytes for the header, an out-of-range count, too few
    bytes for the declared count, a CRC mismatch, or t0_ms==0xFFFFFFFF
    (erased QSPI flash's all-0xFF fill).
    """
    if len(data) < HEADER_BYTES:
        return BlockResult(ok=False, bytes_consumed=0)

    t0_ms, count = struct.unpack_from("<IB", data, 0)
    if t0_ms == 0xFFFFFFFF:
        return BlockResult(ok=False, bytes_consumed=0)
    if count == 0 or count > MAX_SAMPLES_PER_BLOCK:
        return BlockResult(ok=False, bytes_consumed=0)

    need = block_size(count)
    if len(data) < need:
        return BlockResult(ok=False, bytes_consumed=0)

    body = data[: need - 1]
    if crc8(body) != data[need - 1]:
        return BlockResult(ok=False, bytes_consumed=0)

    samples = []
    mags = struct.unpack_from(f"<{count}H", data, HEADER_BYTES)
    for i, milli_g in enumerate(mags):
        t_s = t0_ms / 1000.0 + i / log_hz
        samples.append(Sample(t_s=t_s, mag_g=g_from_milli_g(milli_g)))

    return BlockResult(ok=True, bytes_consumed=need, t0_ms=t0_ms, count=count,
                       samples=samples)


def decode(data: bytes, log_hz: int) -> tuple[list[Sample], int]:
    """Decode every complete block in `data`, front to back, stopping at
    (and never reading past) the first incomplete/corrupt one. Returns
    (samples, bytes_consumed) — bytes_consumed == len(data) iff every block
    was whole and valid."""
    samples: list[Sample] = []
    off = 0
    while off < len(data):
        r = decode_one_block(data[off:], log_hz)
        if not r.ok:
            break
        samples.extend(r.samples)
        off += r.bytes_consumed
    return samples, off


def decode_to_csv(data: bytes, log_hz: int) -> str:
    """Same as decode(), formatted as the wire CSV rows main.cpp's own
    String(t,3)+","+String(mag,3)+"\\n" produces — "%.3f,%.3f\\n" per
    sample, byte for byte. This is the parity test's acceptance bar."""
    samples, _consumed = decode(data, log_hz)
    return "".join(f"{s.t_s:.3f},{s.mag_g:.3f}\n" for s in samples)
