// jh_power seam, host build — unsupported by default, scriptable for CI.
//
// Mirrors the other host seams' environment-variable pattern (see
// jh_imu.cpp's JH_IMU_SCRIPT): with no environment set, every accessor
// returns -1 and main.cpp emits no battery keys — the host build behaves
// exactly like the ESP32 build and every pre-battery test stays valid.
//
// Set JH_VBAT_MV (e.g. "3870") to make the host device report battery
// telemetry like a Sense would: vbat_mv() returns the value verbatim,
// batt_pct() derives from the SAME curve shape the nrf52 impl uses (kept
// deliberately simple here — linear 3300→0 .. 4160→100, the nrf52 curve's
// own two end anchors, matched here so a resting-full mock reads 100 the
// same way the real board does per SENSE_FIRST_BOOT.md item 24 — because
// what the host tests assert is key PRESENCE and plumbing, not curve
// calibration), and charging() reports JH_CHG ("0"/"1", default 0). This
// is what lets tools/tests/test_hostdev.py exercise the emit path in CI
// with no hardware anywhere.
//
// SPDX-License-Identifier: MIT

#include <cstdlib>
#include "platform/jh_power.h"

namespace jh_power {

namespace {
int env_int(const char* name, int fallback) {
  const char* v = std::getenv(name);
  return (v && v[0]) ? std::atoi(v) : fallback;
}
}  // namespace

void init() {}

int vbat_mv() { return env_int("JH_VBAT_MV", -1); }

int batt_pct() {
  const int mv = vbat_mv();
  if (mv < 0) return -1;
  if (mv >= 4160) return 100;
  if (mv <= 3300) return 0;
  return (mv - 3300) * 100 / (4160 - 3300);
}

int charging() {
  if (vbat_mv() < 0) return -1;
  return env_int("JH_CHG", 0) ? 1 : 0;
}

void update_charge_current() {}
uint32_t reset_reason() { return 0; }        // no retained reset register here
void breadcrumb_set(uint8_t) {}
uint8_t breadcrumb_last() { return 0; }
int fast_charge_state() { return -1; }       // no selectable charge current  // host: no selectable charge current

// The host mock reports JH_VBAT_MV verbatim; scaling it would make the
// env var lie about what it set. No-op on purpose.
void set_vbat_scale(float) {}

int vbat_mv_tacq(int tacq_code) {
  // There is no ADC here to have an acquisition time. Returning the SAME value
  // for every valid code is the honest mock: it models a board whose reading
  // does not depend on TACQ, which is one of the two real outcomes the sweep
  // is trying to distinguish — so a host run exercises the command and its
  // parsing without inventing a hardware effect that may not exist.
  if (tacq_code < 0 || tacq_code > 5) return -1;
  return vbat_mv();
}

bool system_off() {
  // Mirrors the capability rule main.cpp keys on (vbat_mv() >= 0 means
  // "battery platform" means "off is real"): with JH_VBAT_MV scripted the
  // host device IS emulating a Sense, so `off` must not return — the
  // process exits cleanly, and a harness sees exactly what a serial client
  // of the real board sees: farewell, OK, then silence/EOF. Unscripted
  // (v1-like) it stays unsupported and the command answers ERR — never a
  // mixed OK-then-ERR, which would corrupt client framing.
  if (vbat_mv() >= 0) {
    std::exit(0);
  }
  return false;
}

}  // namespace jh_power
