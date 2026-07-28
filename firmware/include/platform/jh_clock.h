// jh_clock.h — monotonic-clock platform seam.
//
// NOT one of the four seams named in docs/sense.md §3.9 (imu/store/link/
// persist) — added because main.cpp's sample timestamping needs a 64-bit,
// non-wrapping microsecond counter, and that primitive is chip-specific
// (ESP32: esp_timer_get_time(), an ESP-IDF call) with no seam of its own to
// live behind. See this repo's refactor notes for why: keeping it inline
// would have left esp_timer.h (an ESP32-only header) included from main.cpp,
// which the port spec requires to be seam-only. Kept minimal on purpose:
// one function, mirroring exactly what main.cpp used to call inline.
//
// Units: microseconds, monotonic since boot. Arduino's standard micros() is
// only 32 bits and wraps at ~71.6 minutes — shorter than a wing session —
// which would reset main.cpp's sample timestamp mid-file (and could eat a
// jump in flight at the wrap instant). This seam exists so that never
// happens.
//
// ESP32: esp_timer_get_time() (src/platform/esp32/jh_clock.cpp) — an
// ESP-IDF call that in practice never wraps (a 64-bit microsecond counter
// overflows only after ~584,000 years). A future platform needs an
// equivalent 64-bit (or otherwise non-wrapping-within-a-session) monotonic
// microsecond source; VERIFY what the nRF52 core offers (e.g. a 32-bit
// hardware timer with its own extension scheme) — do not substitute the
// 32-bit micros() outright.
//
// SPDX-License-Identifier: MIT

#pragma once

#include <stdint.h>

namespace jh_clock {

// Microseconds since boot. 64-bit: does not wrap during a real session.
int64_t micros64();

}  // namespace jh_clock
