// jh_clock.cpp — ESP32 implementation of the jh_clock seam
// (firmware/include/platform/jh_clock.h).
//
// SPDX-License-Identifier: MIT

#include "platform/jh_clock.h"

#include <esp_timer.h>

namespace jh_clock {

int64_t micros64() {
  return esp_timer_get_time();
}

}  // namespace jh_clock
