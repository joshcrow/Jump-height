// jh_power seam, Seeed XIAO nRF52840 Sense — real battery telemetry
// (docs/sense.md §3.4; seam contract in include/platform/jh_power.h).
//
// Pin facts, read out of the INSTALLED variant (variants/
// Seeed_XIAO_nRF52840_Sense/variant.cpp, the same source of truth
// jh_imu.cpp used for its pins):
//   D14 = P0.14 "READ_BAT"  — drive LOW to connect the VBAT divider
//                             (disabled/HIGH by default: no silent drain,
//                             exactly the behavior §1's table promises)
//   D32 = P0.31 "VBAT"      — the divider tap (SAADC AIN7); PIN_VBAT in
//                             variant.h
//   D23 = P0.17 "~CHG"      — BQ25101 charge-status output, open-drain,
//                             ACTIVE LOW while the cell is charging
//   D22 = P0.13 "HICHG"     — charge-current select; deliberately
//                             untouched (50 mA default = 0.2C for the
//                             250 mAh cell — see the header comment)
//
// Divider: VBAT — 1 MΩ — tap — 510 kΩ — GND (Seeed's published schematic
// value, corroborated by the community battery libraries for this exact
// board): vbat = tap × (1000+510)/510. ADC: 12-bit against the 2.4 V
// internal reference (AR_INTERNAL_2_4 — 0.6 V ref × 1/4 gain), the
// combination the Seeed wiki's own battery example uses.
//
// BENCH-VERIFY (SENSE_FIRST_BOOT.md item 24): the divider's source
// impedance (~340 kΩ Thevenin) is high for the SAADC's default
// acquisition time, which the Adafruit core does not expose per-read.
// Mitigations here: 1 ms settle after enabling the divider, one discarded
// throwaway read, then a 4-read average. Compare vbat_mv against a
// multimeter on the real cell once — if it reads low by more than ~2%,
// the fix is raising the SAADC acquisition time (core-level TACQ config),
// not tweaking the divider constants.
//
// SPDX-License-Identifier: MIT

#include <Arduino.h>
#include <nrf_soc.h>  // sd_power_system_off — the SoftDevice is up
                      // (Bluefruit), so the sd_ call is mandatory; the
                      // raw register write below is only the fallback.
#include "platform/jh_power.h"

namespace jh_power {

namespace {

const uint32_t PIN_DIVIDER_EN = 14;  // D14 / P0.14, LOW = divider on
const uint32_t PIN_VBAT_ADC   = 32;  // D32 / P0.31 (variant.h PIN_VBAT)
const uint32_t PIN_CHG_STATE  = 23;  // D23 / P0.17, LOW = charging

// Resting-voltage → percent, single-cell LiPo, small piecewise-linear
// table (interpolated). Coarse on purpose: the product question is
// "charge before this session?", not coulomb counting.
struct CurvePoint { uint16_t mv; uint8_t pct; };
const CurvePoint kCurve[] = {
    {4200, 100}, {4060, 90}, {3980, 80}, {3900, 65},
    {3820, 45},  {3770, 30}, {3700, 15}, {3550, 5}, {3300, 0},
};
const int kCurveLen = (int)(sizeof(kCurve) / sizeof(kCurve[0]));

}  // namespace

void init() {
  pinMode(PIN_DIVIDER_EN, OUTPUT);
  digitalWrite(PIN_DIVIDER_EN, HIGH);   // divider off until a read wants it
  pinMode(PIN_CHG_STATE, INPUT_PULLUP); // ~CHG is open-drain: pullup, LOW=charging
}

int vbat_mv() {
  digitalWrite(PIN_DIVIDER_EN, LOW);
  delay(1);  // divider + SAADC input settle (see header BENCH-VERIFY note)

  analogReference(AR_INTERNAL_2_4);
  analogReadResolution(12);
  (void)analogRead(PIN_VBAT_ADC);  // throwaway: first sample after mux/ref change
  uint32_t sum = 0;
  for (int i = 0; i < 4; ++i) sum += analogRead(PIN_VBAT_ADC);
  const uint32_t raw = sum / 4;

  digitalWrite(PIN_DIVIDER_EN, HIGH);  // divider back off — no idle drain

  // tap_mv = raw × 2400 / 4095; vbat = tap × 1510 / 510.
  const uint32_t tap_mv = (raw * 2400UL) / 4095UL;
  return (int)((tap_mv * 1510UL) / 510UL);
}

int batt_pct() {
  const int mv = vbat_mv();
  if (mv < 0) return -1;
  if (mv >= kCurve[0].mv) return 100;
  for (int i = 1; i < kCurveLen; ++i) {
    if (mv >= kCurve[i].mv) {
      const CurvePoint& hi = kCurve[i - 1];
      const CurvePoint& lo = kCurve[i];
      return lo.pct + (int)((uint32_t)(mv - lo.mv) * (hi.pct - lo.pct) /
                            (hi.mv - lo.mv));
    }
  }
  return 0;
}

int charging() { return digitalRead(PIN_CHG_STATE) == LOW ? 1 : 0; }

bool system_off() {
  // The LSM6DS3 is the one always-on consumer System OFF doesn't kill by
  // itself: its rail is power-gated by D15/P1.08 (jh_imu.cpp owns that pin
  // during normal life — read the pin fact there; overriding it here is
  // deliberate, this is the power domain's shutdown path and jh_imu is
  // about to lose power anyway). GPIO output states are RETAINED through
  // System OFF, so the rail stays cut while asleep. The VBAT divider is
  // already parked off (vbat_mv() always re-disables it), and the QSPI
  // flash sits in deep power-down between operations (jh_store's
  // wake/sleep bracketing) — nothing else needs telling.
  pinMode(15, OUTPUT);
  digitalWrite(15, LOW);
  delay(5);

  // Wake sources after this line: USB/VBUS attach or the reset button —
  // and the charger chip keeps charging a plugged cell with the CPU off.
  // BENCH-VERIFY (SENSE_FIRST_BOOT item 25): actual off-current, and
  // whether entry works with USB already attached (VBUS wake may fire
  // immediately — harmless either way, the cable case is bench-only).
  if (sd_power_system_off() != NRF_SUCCESS) {
    NRF_POWER->SYSTEMOFF = 1;  // SoftDevice not up after all — go direct
  }
  while (true) {}  // not reached (debug-emulated System OFF can return)
  return true;
}

}  // namespace jh_power
