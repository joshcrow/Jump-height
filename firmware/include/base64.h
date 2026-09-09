// base64.h — dependency-free base64 encoder (RFC 4648 standard alphabet).
//
// WHY THIS EXISTS: the whole protocol is line-oriented. Every client — the
// CLI's line reader, the watch, the phone page — splits the device's output
// on '\n' and treats anything it doesn't recognise as chatter. The
// `traceraw` command (firmware/src/main.cpp) has to ship the trace region's
// RAW bytes, and those are trace_codec blocks: they contain '\n', 0x00,
// 0xFF and everything in between, so they cannot travel as themselves
// without breaking the framing every existing parser depends on. Base64
// costs 33% and buys a body that is still just lines of text — still ~4.5x
// smaller on the wire than the ~17 bytes/sample CSV a `trace` sends, which
// is the point of the command.
//
// Encoder only. The device never decodes base64 (nothing sends it any), and
// an unused decoder is a maintenance liability, not a courtesy.
//
// Dependency-free, like trace_codec.h and jump_detector.h: no Arduino, no
// String, no allocation — so it compiles identically into the device image
// and into any host test build.
//
// SPDX-License-Identifier: MIT

#pragma once

#include <stddef.h>
#include <stdint.h>

namespace base64 {

// The standard alphabet (RFC 4648 §4), NOT the URL-safe one: the wire
// contract's decoders are Python's base64.b64decode and the browser's
// atob(), both of which take '+' and '/'.
inline const char* alphabet() {
  return "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
}

// Characters encode() will write for `len` input bytes — always a multiple
// of 4, because the output is '=' padded. constexpr so a caller can size a
// fixed buffer with it (main.cpp's traceraw does) instead of writing 76 and
// leaving the two numbers to drift apart.
constexpr size_t encoded_len(size_t len) { return ((len + 2) / 3) * 4; }

// Encodes `len` bytes into `out`, which must have room for
// encoded_len(len) characters. Returns the number of characters written, or
// 0 if `out` is too small (never a partial write — a truncated base64 group
// would decode to silently wrong bytes, and a caller that ignored the
// return value would ship them).
//
// Does NOT write a terminating NUL and does NOT insert line breaks: the
// caller owns line length, because that is a wire-format decision (traceraw
// emits 57 input bytes -> 76 characters per line).
inline size_t encode(const uint8_t* in, size_t len, char* out, size_t out_cap) {
  const size_t need = encoded_len(len);
  if (out == nullptr || out_cap < need) return 0;
  if (in == nullptr && len > 0) return 0;

  const char* tbl = alphabet();
  size_t o = 0;
  size_t i = 0;
  // Whole 3-byte groups -> 4 characters, no padding.
  for (; i + 3 <= len; i += 3) {
    const uint32_t v = ((uint32_t)in[i] << 16) | ((uint32_t)in[i + 1] << 8) |
                       (uint32_t)in[i + 2];
    out[o++] = tbl[(v >> 18) & 0x3F];
    out[o++] = tbl[(v >> 12) & 0x3F];
    out[o++] = tbl[(v >> 6) & 0x3F];
    out[o++] = tbl[v & 0x3F];
  }
  // The 1- or 2-byte tail: the missing bytes are encoded as zero bits, and
  // one '=' per missing byte records how many of them were fabricated.
  const size_t rem = len - i;
  if (rem == 1) {
    const uint32_t v = (uint32_t)in[i] << 16;
    out[o++] = tbl[(v >> 18) & 0x3F];
    out[o++] = tbl[(v >> 12) & 0x3F];
    out[o++] = '=';
    out[o++] = '=';
  } else if (rem == 2) {
    const uint32_t v = ((uint32_t)in[i] << 16) | ((uint32_t)in[i + 1] << 8);
    out[o++] = tbl[(v >> 18) & 0x3F];
    out[o++] = tbl[(v >> 12) & 0x3F];
    out[o++] = tbl[(v >> 6) & 0x3F];
    out[o++] = '=';
  }
  return o;
}

}  // namespace base64
