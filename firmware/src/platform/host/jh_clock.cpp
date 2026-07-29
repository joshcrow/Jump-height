// jh_clock.cpp — HOST implementation of the jh_clock seam
// (firmware/include/platform/jh_clock.h).
//
// Built so the REAL chip-agnostic core (src/main.cpp) can run natively on a
// dev machine for testing (see firmware/platformio.ini's env:host) — no
// hardware, no chip-specific clock API to wrap.
//
// std::chrono::steady_clock: monotonic (never runs backward, immune to
// wall-clock adjustments), and a 64-bit microsecond count from a
// process-start epoch does not wrap within anything resembling a real test
// run — the same non-wrapping contract this seam documents for the ESP32's
// esp_timer_get_time() and the nRF52's wrap-tracked micros().
//
// SPDX-License-Identifier: MIT

#include "platform/jh_clock.h"

#include <chrono>

namespace jh_clock {

int64_t micros64() {
  static const std::chrono::steady_clock::time_point t0 =
      std::chrono::steady_clock::now();
  return std::chrono::duration_cast<std::chrono::microseconds>(
             std::chrono::steady_clock::now() - t0)
      .count();
}

}  // namespace jh_clock
