// jh_clock.cpp — nRF52 (Seeed XIAO nRF52840 Sense) implementation of the
// jh_clock seam (firmware/include/platform/jh_clock.h). See docs/sense.md
// §3.9 and the refactor's handoff notes (this platform's binding spec).
//
// The Adafruit nRF52 core's Arduino micros() is, like the ESP32's, only
// 32 bits (it reads a free-running hardware timer and returns it directly —
// see cores/nRF5/wiring_analog_nRF52.c and delay.c in the installed
// framework-arduinoadafruitnrf52-seeed package for the underlying
// NRF_RTC1-driven implementation) and wraps at the same ~71.6 minutes
// (2^32 us) as any other 32-bit microsecond counter. That is shorter than a
// real wing session, so per the refactor's handoff note we do NOT use it
// alone: this wraps-tracking wrapper turns it into a 64-bit, effectively
// non-wrapping (2^64 us ~= 584,942 years) counter, mirroring the contract
// jh_clock.h documents for the ESP32's esp_timer_get_time().
//
// How: compare each raw micros() reading to the previous one. If it went
// DOWN, exactly one 32-bit wrap happened since the last call, so bump a
// wrap counter and fold it into the high bits. This correctly detects at
// most one wrap per call — which is exactly why it's safe here and would
// NOT be in general: jh_clock::micros64() is only ever called from
// main.cpp's loop() (once per pace-check, i.e. at least JH_SAMPLE_HZ =
// 200 times/second — every 5 ms) and setup(), so consecutive calls are
// always milliseconds apart, never anywhere close to the 71.6-minute wrap
// period. A caller that stopped invoking this for over an hour would break
// the assumption; nothing in this codebase does.
//
// SPDX-License-Identifier: MIT

#include "platform/jh_clock.h"

#include <Arduino.h>

namespace jh_clock {

int64_t micros64() {
  static uint32_t s_last  = 0;
  static uint64_t s_wraps = 0;
  static bool     s_init  = false;

  const uint32_t now = micros();
  if (!s_init) {
    s_last = now;
    s_init = true;
  } else if (now < s_last) {
    s_wraps++;  // exactly one wrap since the previous call — see file comment
  }
  s_last = now;

  return (int64_t)(((uint64_t)s_wraps << 32) | (uint64_t)now);
}

}  // namespace jh_clock
