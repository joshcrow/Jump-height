// trace_codec.h — binary trace v2 codec (docs/ota.md §4.5, docs/sense.md §3.2).
//
// Dependency-free, like jump_detector.h: no Arduino/platform includes, so
// this compiles equally well inside firmware/src/platform/nrf52/jh_store.cpp
// (device-side, encoding the live trace onto QSPI flash and decoding it back
// to the CSV wire format on a `trace`/`dump`) and in a host g++ test harness
// (firmware/test/trace_codec_harness.cpp), which
// tools/tests/test_trace_codec.py drives — mirroring the existing C++/Python
// detector parity pattern (firmware/test/host_test.cpp + sim/golden.py,
// wired up by ./tools/jump simtest; see sim/trace_codec.py for the Python
// mirror of the decoder below).
//
// ---------------------------------------------------------------------------
// Wire format (one block):
//   offset  size  field
//   0       4     t0_ms   little-endian u32; ms timestamp of this block's
//                         FIRST sample (same time base as jh_clock::micros64())
//   4       1     count   u8; number of samples in this block, 1..255
//                         (a block is never written with count==0 — see
//                         Encoder::finish())
//   5       2*N   samples N=count little-endian u16 values, magnitude in
//                         milli-g (1 count == 0.001 g; 0..65535 => 0..65.535 g,
//                         comfortably covers either platform's accel range —
//                         the ESP32 build's ±8 g and the Sense's ±16 g
//                         (SENSE_FIRST_BOOT.md item 20) both fit, with room
//                         to spare for landing spikes beyond either)
//   5+2*N   1     crc8    CRC-8 (poly 0x07, init 0x00, no input/output
//                         reflection, no final XOR — see crc8() below) over
//                         every preceding byte of THIS block (header+payload,
//                         i.e. the first 5+2*N bytes)
// Total block size = 6 + 2*count bytes (variable length — a reader must
// parse the 5-byte header first to learn how many payload+crc bytes follow;
// see block_size()/decode_one_block() below).
//
// Per-sample time reconstruction. Only the block's FIRST sample gets a
// stored timestamp; the other (count-1) samples are reconstructed assuming
// perfectly even spacing at 1/log_hz seconds apart:
//     t_s(i) = t0_ms / 1000.0 + i / log_hz          (i = 0 .. count-1)
// `log_hz` is the SAME decimation rate main.cpp already applies to the live
// trace before a sample ever reaches the jh_store seam (JH_LOG_HZ,
// config/params.json's firmware.log_hz) — pass it in explicitly rather than
// hardcoding it here, so this header stays free of any project-specific
// config include. This is why the format costs ~2 bytes/sample instead of
// ~6+ (docs/ota.md §4.5's whole point): real per-sample jitter (microseconds
// of I2C transaction time) is deliberately NOT preserved, only a once-per-
// block anchor. Documented trade-off, not a bug — nothing downstream (the
// jump detector replays whatever samples it's given; a future carve-G/
// foil-time classifier still operates on ~1 s windows) can tell the
// difference. The parity test therefore feeds already-evenly-spaced
// synthetic samples: that is the format's actual operating assumption, not
// a test artifact.
//
// Double precision, both directions (review-store.md finding #2 —
// CONFIRMED BY TEST, not theoretical): the encode-side t_s->t0_ms
// conversion (t0_ms_from_t_s() below — the ONE arithmetic path both
// jh_store.cpp's real encode site and firmware/test/trace_codec_harness.cpp's
// host harness call, rather than two independently-written copies that
// could drift apart) and the decode-side reconstruction (decode_one_block()
// below) both compute in double, and on_sample()'s t_s parameter is itself
// a double, all the way out to the final "%.3f" formatting. Doing this in
// float32 throughout used to diverge from Python's native-double math
// (sim/trace_codec.py) at multi-hour timestamps: float32's ~7-decimal-digit
// mantissa can no longer resolve 0.001 s steps once t exceeds ~8300 s (its
// ULP there already exceeds 1 ms) — measured divergence at t=8192.021s on
// the encode side, ~4.55h on the decode side, both comfortably inside this
// format's real, multi-hour operating range (docs/sense.md §3.2's ~5h
// capacity estimate). Computing the same expression in double and then
// narrowing the RESULT back to float32 for on_sample() does NOT fix this
// (verified): a single double-to-float32 rounding at these magnitudes can
// itself land on the wrong side of a "%.3f" rounding boundary regardless of
// how precisely the intermediate arithmetic was done — the fix has to keep
// t_s a double all the way to the formatted string, matching Python (whose
// floats are natively double everywhere), not merely during the arithmetic
// in between.
//
// Block boundaries are a POLICY decision that belongs to the caller (nominally
// once per second of wall-clock t, or immediately once a block is full, or
// on a natural discontinuity like an idle-gate transition) — this header has
// no clock of its own. Encoder::add_sample() simply refuses (returns false,
// stores nothing) once MAX_SAMPLES_PER_BLOCK is reached, so the caller knows
// to finish() and begin() a new block.
//
// Power-loss stance (docs/sense.md §3.6): decode_one_block() treats "not
// enough bytes left for the header's declared count" and "CRC mismatch"
// identically — an incomplete/corrupt block — and the multi-block decode()
// stops there without looking at anything after it. Erased QSPI flash reads
// as all-0xFF bytes; that pattern is additionally rejected outright (see the
// t0_ms==0xFFFFFFFF guard in decode_one_block()) rather than relying solely
// on the astronomically-unlikely chance that CRC-8(0xFF...) collides with a
// stray 0xFF "check byte" — belt and suspenders, and it gives the parity
// test a clean, deterministic corruption case to assert on. No separate
// magic number is needed at the per-block level for this reason (the
// jh_store seam's own superblock, sector 0, carries its own magic +
// format-version — a different, coarser-grained concern: "is this flash
// chip formatted for us at all", not "is this one block whole").
//
// SPDX-License-Identifier: MIT

#pragma once

#include <assert.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

namespace trace_codec {

static const uint32_t MAX_SAMPLES_PER_BLOCK = 255;
static const uint32_t HEADER_BYTES          = 5;  // t0_ms(4) + count(1)
static const uint32_t CRC_BYTES             = 1;

// Total on-wire/on-flash size of a block holding `count` samples.
inline uint32_t block_size(uint32_t count) {
  return HEADER_BYTES + count * 2u + CRC_BYTES;
}

// CRC-8, polynomial 0x07 (x^8+x^2+x+1), init 0x00, no input/output
// reflection, no final XOR — the plain "textbook" CRC-8, chosen only because
// it round-trips identically and trivially in both C++ and Python (see
// sim/trace_codec.py); it is a corruption/truncation check for our own
// on-flash format, not a cryptographic or wire-interop requirement.
inline uint8_t crc8(const uint8_t* data, size_t len) {
  uint8_t crc = 0x00;
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (int b = 0; b < 8; ++b) {
      crc = (crc & 0x80) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
    }
  }
  return crc;
}

// Magnitude <-> milli-g. Encoder rounding is round-half-up (mag_g is always
// >= 0: it's a vector magnitude) and clamps to the u16 range — both ends
// exposed so the host test harness and jh_store.cpp share one rounding rule
// rather than risking two subtly different ones.
//
// Non-finite guard (review-store.md finding #3): a NaN mag_g reaching the
// cast below is undefined behavior in C++ (observed: 0 on this project's
// host test compiler, but ARM is UNSPECIFIED, not merely "also 0" — a real
// correctness gap, not a style nit). +-inf would otherwise sail through the
// clamps below in an inconsistent way (an inf fails `< 0.0f`, but a
// subsequent inf*1000+0.5 is still inf, which DOES trip the `v > 65535.0f`
// clamp — so +inf was accidentally survivable already, -inf was not) — the
// simpler, deliberate contract here is that ANY non-finite input, NaN or
// +-inf alike, maps to 0, not "0 for NaN/-inf, 65535 for +inf" as an
// accident of clamp ordering. Reachable via a garbled trace_append() line
// parsing to atof("nan")/atof("inf") — see jh_store.cpp's feedSample(),
// which now also screens the parsed value at the source (belt+suspenders,
// not the only guard). Must match sim/trace_codec.py::milli_g_from_g() bit
// for bit.
inline uint16_t milli_g_from_g(float mag_g) {
  if (!isfinite(mag_g)) return 0;
  if (mag_g < 0.0f) mag_g = 0.0f;
  float v = mag_g * 1000.0f + 0.5f;
  if (v > 65535.0f) v = 65535.0f;
  return (uint16_t)v;
}
inline float g_from_milli_g(uint16_t milli_g) { return (float)milli_g / 1000.0f; }

// Millisecond timestamp for a block's t0, from the (double) seconds value
// jh_store.cpp's sampling path hands us — the ONE arithmetic path both
// jh_store.cpp's feedSample() (the real, device-side encode site) and
// firmware/test/trace_codec_harness.cpp's host harness call (see this
// file's "Double precision, both directions" note above for why sharing
// this matters, not just computing it identically in two places).
//
// t_s is double, not float (B3, glue-and-forget.md §3a — the second of two
// unswept narrowing sites the attack found; the first was jh_store.cpp:960's
// `(float)atof()`, now fixed alongside this one). Multiplying in double
// avoids a double-rounding defect (one rounding from a float32 multiply
// itself, a second from the round-to-integer below) that measurably
// diverged from Python's (always-double) math at t=8192.021s and beyond —
// but that fix is worthless if the INPUT was already crushed through
// float32 before it got here, which is exactly what the unswept site did:
// it made this function's own effective resolution fictional starting at
// ~2.28h of uptime regardless of how carefully the arithmetic below is
// done. The parameter must stay double all the way from the caller.
//
// FINDING preserved (do not let this regress silently): the predecessor of
// the line below called lround(), not llround(). lround() returns a plain
// `long`, which is 32 bits on this project's ARM (Cortex-M4F) target but 64
// bits on the x86_64/ARM64 host that builds and runs ./tools/jump simtest —
// so the existing parity harness is STRUCTURALLY BLIND to this failure
// mode: a signed 32-bit `long` silently overflows (undefined behavior in C)
// once t_s exceeds INT32_MAX ms = 2,147,483.647 s =~ 24.9 days of uptime,
// and no host build could ever exercise that, because the host's own `long`
// never overflows there. llround() returns `long long` (64-bit on every
// target this project ships to), so the multiply-round itself no longer
// overflows — but this function's uint32_t return still needs the result to
// fit, and its historical callers all assumed the narrower, signed int32
// range lround() used to enforce implicitly. Check that explicitly, on
// every platform, rather than trust silence from a host suite that cannot
// see the gap.
inline uint32_t t0_ms_from_t_s(double t_s) {
  const long long ms = llround(t_s * 1000.0);
  assert(ms >= 0 && ms <= 2147483647LL);  // fits int32 — see FINDING above
  return (uint32_t)ms;
}

// ---------------------------------------------------------------- Encoder
// Builds one block at a time into caller-owned memory — no dynamic
// allocation, so it runs identically on-device and on a host test harness.
//   Encoder enc;
//   enc.begin(t0_ms);
//   while (more samples && enc.add_sample(mag_g)) { /* ... */ }
//   uint8_t buf[trace_codec::block_size(trace_codec::MAX_SAMPLES_PER_BLOCK)];
//   size_t n = enc.finish(buf, sizeof(buf));   // 0 if enc.count()==0
class Encoder {
 public:
  void begin(uint32_t t0_ms) { t0_ms_ = t0_ms; count_ = 0; }
  uint32_t count() const { return count_; }
  bool full() const { return count_ >= MAX_SAMPLES_PER_BLOCK; }

  // Adds one sample to the in-progress block. Returns false (storing
  // nothing) once full() — the caller must finish() and begin() again.
  bool add_sample(float mag_g) {
    if (full()) return false;
    mags_milli_g_[count_++] = milli_g_from_g(mag_g);
    return true;
  }

  // Bytes finish() would write right now (0 if count()==0) — callers that
  // need to size/fit a flash page write ahead of time use this.
  size_t encoded_size() const { return count_ ? block_size(count_) : 0; }

  // Serializes header+payload+crc into `out` (>= encoded_size() bytes).
  // Returns bytes written; 0 if count()==0 (an empty block is never
  // written — there is nothing to anchor a t0_ms to) or out_cap too small.
  // Does not reset internal state; call begin() again to reuse this encoder.
  size_t finish(uint8_t* out, size_t out_cap) const {
    const size_t need = encoded_size();
    if (need == 0 || out_cap < need) return 0;
    size_t p = 0;
    out[p++] = (uint8_t)(t0_ms_ & 0xFF);
    out[p++] = (uint8_t)((t0_ms_ >> 8) & 0xFF);
    out[p++] = (uint8_t)((t0_ms_ >> 16) & 0xFF);
    out[p++] = (uint8_t)((t0_ms_ >> 24) & 0xFF);
    out[p++] = (uint8_t)count_;
    for (uint32_t i = 0; i < count_; ++i) {
      out[p++] = (uint8_t)(mags_milli_g_[i] & 0xFF);
      out[p++] = (uint8_t)((mags_milli_g_[i] >> 8) & 0xFF);
    }
    out[p] = crc8(out, p);
    p += 1;
    return p;
  }

 private:
  uint32_t t0_ms_ = 0;
  uint32_t count_ = 0;
  uint16_t mags_milli_g_[MAX_SAMPLES_PER_BLOCK];
};

// ---------------------------------------------------------------- Decoder
struct BlockResult {
  bool     ok;              // false: incomplete/corrupt (see decode_one_block)
  uint32_t bytes_consumed;  // 0 when !ok
  uint32_t t0_ms;
  uint32_t count;
};

// Decode exactly one block from the front of `data` (up to `len` bytes
// available — NOT necessarily the whole stored region; callers streaming
// off flash a page at a time pass whatever they have). `on_sample` (may be
// NULL to just validate/measure) is invoked once per recovered sample, in
// order, with the reconstructed timestamp and magnitude in g.
//
// ok=false (bytes_consumed=0) covers, identically and deliberately:
//   - fewer than HEADER_BYTES available
//   - header's declared count is 0 or > MAX_SAMPLES_PER_BLOCK (never
//     legitimately written by Encoder::finish())
//   - fewer than block_size(count) bytes available (a torn/partial write)
//   - CRC-8 mismatch (corruption)
//   - t0_ms == 0xFFFFFFFF (erased QSPI flash reads as all-0xFF; this is the
//     belt-and-suspenders guard described in the file header comment)
// The caller's job is simply to stop at the first !ok block — this mirrors
// the append-only, partial-last-page-ignored power-loss stance.
inline BlockResult decode_one_block(const uint8_t* data, size_t len, uint32_t log_hz,
                                     void (*on_sample)(void* ctx, double t_s, float mag_g),
                                     void* ctx) {
  BlockResult r = {false, 0, 0, 0};
  if (len < HEADER_BYTES) return r;

  const uint32_t t0_ms = (uint32_t)data[0] | ((uint32_t)data[1] << 8) |
                          ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24);
  const uint32_t count = data[4];
  if (t0_ms == 0xFFFFFFFFu) return r;             // erased flash, see above
  if (count == 0 || count > MAX_SAMPLES_PER_BLOCK) return r;

  const uint32_t need = block_size(count);
  if ((uint32_t)len < need) return r;              // torn/partial write

  const uint8_t want_crc = crc8(data, need - 1);
  if (want_crc != data[need - 1]) return r;        // corrupt

  if (on_sample) {
    for (uint32_t i = 0; i < count; ++i) {
      const uint16_t milli_g = (uint16_t)data[5 + 2 * i] |
                                ((uint16_t)data[5 + 2 * i + 1] << 8);
      // Double precision throughout (see this file's "Double precision,
      // both directions" note) — NOT (double)((t0_ms/1000.0f) + (float)i /
      // (float)log_hz): narrowing to float32 anywhere in this expression
      // before handing t_s onward reintroduces exactly the multi-hour
      // divergence from Python this fixes.
      const double t_s   = (double)t0_ms / 1000.0 + (double)i / (double)log_hz;
      const float  mag_g = g_from_milli_g(milli_g);
      on_sample(ctx, t_s, mag_g);
    }
  }
  r.ok = true;
  r.bytes_consumed = need;
  r.t0_ms = t0_ms;
  r.count = count;
  return r;
}

// Decode every complete block in `data`, front to back, stopping at (and
// never reading past) the first incomplete/corrupt one. Returns total bytes
// consumed — == len iff every block in `data` was whole and valid.
inline size_t decode(const uint8_t* data, size_t len, uint32_t log_hz,
                     void (*on_sample)(void* ctx, double t_s, float mag_g), void* ctx) {
  size_t off = 0;
  while (off < len) {
    const BlockResult r = decode_one_block(data + off, len - off, log_hz, on_sample, ctx);
    if (!r.ok) break;
    off += r.bytes_consumed;
  }
  return off;
}

namespace detail {
struct CsvAdapter {
  void (*on_line)(void* ctx, const char* line, size_t n);
  void* user_ctx;
};
inline void emit_csv_sample(void* ctx, double t_s, float mag_g) {
  CsvAdapter* a = (CsvAdapter*)ctx;
  char buf[32];
  // Matches main.cpp's emit layer exactly: trace_buf += String(t,3) + ","
  // + String(mag,3) + "\n" — i.e. fixed 3-decimal-place text, one row per
  // sample. This equality (not just "close enough") is the acceptance bar
  // (see the file's top comment and tools/tests/test_trace_codec.py). t_s
  // is a double all the way to this snprintf (see the file's "Double
  // precision, both directions" note) — mag_g stays float, unaffected by
  // that finding (magnitudes never grow unboundedly the way a session's
  // elapsed time does).
  int n = snprintf(buf, sizeof(buf), "%.3f,%.3f\n", t_s, (double)mag_g);
  if (n <= 0) return;
  if (n > (int)sizeof(buf) - 1) n = (int)sizeof(buf) - 1;  // never happens at 3dp, belt+suspenders
  a->on_line(a->user_ctx, buf, (size_t)n);
}
}  // namespace detail

// Same as decode(), but formats each recovered sample as its wire CSV row
// ("t,mag\n", 3 decimal places — see jh_store.h) and delivers it a line at a
// time via `on_line`. This is what firmware/src/platform/nrf52/jh_store.cpp
// streams out for the `trace`/`dump` commands, and what
// tools/tests/test_trace_codec.py diffs against a reference CSV.
inline size_t decode_to_csv(const uint8_t* data, size_t len, uint32_t log_hz,
                            void (*on_line)(void* ctx, const char* line, size_t n),
                            void* user_ctx) {
  detail::CsvAdapter a{on_line, user_ctx};
  return decode(data, len, log_hz, detail::emit_csv_sample, &a);
}

}  // namespace trace_codec
