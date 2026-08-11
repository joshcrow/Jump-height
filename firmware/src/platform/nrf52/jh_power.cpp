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

int vbat_mv_tacq(int tacq_code) {
  // BENCH DIAGNOSTIC for SENSE_FIRST_BOOT item 24. Two meter points proved
  // vbat_mv reads ~2.7% low (3490 vs 3390, then 4160 vs 4050), but they could
  // NOT tell apart the candidate causes — the models' predictions differ by
  // ~19 mV and the meter's own resolution is comparable. This can:
  //
  //   * SAADC acquisition time too short for the divider's ~340 kOhm source
  //     (the standing theory) -> the reading RISES as TACQ increases.
  //   * Divider resistor tolerance, or internal-reference tolerance
  //     -> the reading does NOT move with TACQ.
  //
  // That distinction decides WHERE the fix belongs: acquisition time is a
  // firmware fix correct for every unit, while resistor tolerance is a
  // per-unit calibration that would be actively WRONG to bake into firmware.
  //
  // Raw registers rather than analogRead() because the Adafruit core does not
  // expose TACQ per-read — which is exactly why the theory went untested this
  // long. Every field position below is from the vendor's own
  // nrf52840_bitfields.h, not from memory.
  //
  // tacq_code: 0=3us 1=5us 2=10us 3=15us 4=20us 5=40us (SAADC_CH_CONFIG_TACQ_*)
  if (tacq_code < 0 || tacq_code > 5) return -1;

  digitalWrite(PIN_DIVIDER_EN, LOW);
  delay(1);

  NRF_SAADC->ENABLE = (SAADC_ENABLE_ENABLE_Enabled << SAADC_ENABLE_ENABLE_Pos);
  NRF_SAADC->RESOLUTION = SAADC_RESOLUTION_VAL_12bit;
  NRF_SAADC->CH[0].PSELP = SAADC_CH_PSELP_PSELP_AnalogInput7;  // AIN7 = P0.31
  NRF_SAADC->CH[0].PSELN = SAADC_CH_PSELN_PSELN_NC;
  NRF_SAADC->CH[0].CONFIG =
      (SAADC_CH_CONFIG_RESP_Bypass   << SAADC_CH_CONFIG_RESP_Pos)   |
      (SAADC_CH_CONFIG_RESN_Bypass   << SAADC_CH_CONFIG_RESN_Pos)   |
      (SAADC_CH_CONFIG_GAIN_Gain1_4  << SAADC_CH_CONFIG_GAIN_Pos)   |
      (SAADC_CH_CONFIG_REFSEL_Internal << SAADC_CH_CONFIG_REFSEL_Pos) |
      ((uint32_t)tacq_code           << SAADC_CH_CONFIG_TACQ_Pos)   |
      (SAADC_CH_CONFIG_MODE_SE       << SAADC_CH_CONFIG_MODE_Pos)   |
      (SAADC_CH_CONFIG_BURST_Disabled << SAADC_CH_CONFIG_BURST_Pos);

  int32_t sum = 0;
  const int kReads = 8;
  volatile int16_t buf = 0;
  for (int i = 0; i < kReads + 1; ++i) {  // +1: first sample is a throwaway
    NRF_SAADC->RESULT.PTR = (uint32_t)&buf;
    NRF_SAADC->RESULT.MAXCNT = 1;
    NRF_SAADC->EVENTS_END = 0;
    NRF_SAADC->TASKS_START = 1;
    while (!NRF_SAADC->EVENTS_STARTED) {}
    NRF_SAADC->EVENTS_STARTED = 0;
    NRF_SAADC->TASKS_SAMPLE = 1;
    while (!NRF_SAADC->EVENTS_END) {}
    NRF_SAADC->EVENTS_END = 0;
    NRF_SAADC->TASKS_STOP = 1;
    while (!NRF_SAADC->EVENTS_STOPPED) {}
    NRF_SAADC->EVENTS_STOPPED = 0;
    if (i > 0) sum += (buf < 0) ? 0 : buf;  // SAADC can report small negatives
  }
  NRF_SAADC->ENABLE = 0;  // leave it off so the next analogRead() re-inits cleanly

  digitalWrite(PIN_DIVIDER_EN, HIGH);

  const uint32_t raw = (uint32_t)(sum / kReads);
  const uint32_t tap_mv = (raw * 2400UL) / 4095UL;   // same recipe as vbat_mv()
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
