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
// BENCH-VERIFIED 2026-08-11 (SENSE_FIRST_BOOT.md item 24) — the source
// impedance worry was RIGHT, and it was not the whole story.
//
// The prediction: the divider's ~340 kΩ Thevenin source is high for the
// SAADC's default acquisition time, which the Adafruit core does not expose
// per-read. Confirmed by sweeping TACQ directly (`vbatscan`): the reading
// climbs 4044 -> 4085 mV and flattens at 15 µs. So this file now drives the
// SAADC through raw registers at 15 µs instead of using analogRead().
//
// What that does NOT fix: at the plateau the reading is still ~75 mV (1.8%)
// below a multimeter (4085 vs 4160 measured). That residual is a GAIN error
// — divider resistor tolerance (a 1.043 MΩ top leg instead of 1 MΩ does it,
// inside 5% parts) or the internal reference's own spread. It is
// PER-UNIT, so it must NOT be corrected by editing the constants below:
// doing so would make every other board wrong. It belongs in the per-unit
// calibration record (docs/data-pipeline.md), and whether it is genuinely
// per-unit or systematic is settled by measuring a SECOND board.
//
// SPDX-License-Identifier: MIT

#include <Arduino.h>
#include <nrf_gpio.h>  // rail drive strength (DECISIONS #37)
#include <nrf_soc.h>  // sd_power_system_off — the SoftDevice is up
                      // (Bluefruit), so the sd_ call is mandatory; the
                      // raw register write below is only the fallback.
#include "platform/jh_imu.h"    // bus_release() — detach before rail cut
#include "platform/jh_power.h"

#include <nrf_sdm.h>
#include <nrf_soc.h>

namespace jh_power {

namespace {

const uint32_t PIN_DIVIDER_EN = 14;  // D14 / P0.14, LOW = divider on
const uint32_t PIN_VBAT_ADC   = 32;  // D32 / P0.31 (variant.h PIN_VBAT)
const uint32_t PIN_CHG_STATE  = 23;  // D23 / P0.17, LOW = charging
const uint32_t PIN_HICHG      = 22;  // D22 / P0.13, LOW = 100 mA charge

// FAST CHARGE (2026-08-15). The BQ25101 selects between two preset charge
// currents on HICHG: released = 50 mA, driven LOW = 100 mA (docs/sense.md
// §2). For the installed 250 mAh cell that is 0.2C vs 0.4C — and 0.5C is
// the industry-standard "standard charge" rate for LiPo pouch cells, so
// 0.4C is still conservative, not aggressive.
//
// Why it matters more than the raw numbers suggest: THE DEVICE IS AWAKE
// WHILE IT CHARGES and draws ~6-12 mA of the charger's output, so at the
// 50 mA setting a quarter of the charge current never reaches the cell.
// Net charge rate goes from ~38 mA to ~94 mA, i.e. roughly 6 h -> under 3 h.
//
// DRIVEN ONLY WHILE CHARGING, released otherwise. That is deliberate and
// it is about a thing we do NOT know: whether HICHG has an internal
// pull-up and what it is referenced to. If it were referenced to the
// battery rail, holding the pin low would sink current forever — tens of
// microamps, which is more than the entire standby budget the power design
// is aiming at. Driving it only while USB is present makes the question
// moot: any pull-up current comes from the charger's own input, and when
// unplugged the pin is high-Z with no path at all. Correct under either
// answer, which is the right way to treat an unverified datasheet detail.
bool s_fast_charge_on = false;

// Resting-voltage → percent, single-cell LiPo, small piecewise-linear
// table (interpolated). Coarse on purpose: the product question is
// "charge before this session?", not coulomb counting.
//
// TOP ANCHOR 4160, NOT 4200 (SENSE_FIRST_BOOT.md item 24). 4200 mV is the
// CHARGING voltage (the BQ25101's regulated float), never what a rested
// cell reads — the item's own 08-11 meter point put a rested-full cell at
// 4160 mV. Anchoring 100% at 4200 meant even a perfect ADC topped out
// ~98%, on top of the acquisition-time error item 24 already fixed
// (SAADC TACQ 15 µs, see the file header). With that measurement fix in,
// the remaining "never quite full" gap was the anchor, not the sensor —
// this is the anchor move item 24 called for.
struct CurvePoint { uint16_t mv; uint8_t pct; };
const CurvePoint kCurve[] = {
    {4160, 100}, {4060, 90}, {3980, 80}, {3900, 65},
    {3820, 45},  {3770, 30}, {3700, 15}, {3550, 5}, {3300, 0},
};
const int kCurveLen = (int)(sizeof(kCurve) / sizeof(kCurve[0]));

}  // namespace

static uint32_t s_reset_reas = 0;
static uint8_t  s_crumb_last = 0;

void init() {
  // Capture-and-clear FIRST: RESETREAS bits accumulate across resets until
  // written (nRF52840 PS), so a stale bit from last week would otherwise be
  // indistinguishable from this boot's cause.
  //
  // MUST be SoftDevice-aware. POWER is an SD-owned peripheral: with the SD
  // enabled, direct NRF_POWER access hard-faults. The first build that did it
  // directly (src=1c72f10f, 2026-08-17) died before its own boot banner and
  // parked the board in the bootloader — while watchdog_init's direct NRF_WDT
  // writes kept working, because WDT is not SD-owned. Ask the SD first; fall
  // back to the register only when it is genuinely off.
  uint8_t sd_en = 0;
  sd_softdevice_is_enabled(&sd_en);
  if (sd_en) {
    sd_power_reset_reason_get(&s_reset_reas);
    sd_power_reset_reason_clr(0xFFFFFFFF);
  } else {
    s_reset_reas = NRF_POWER->RESETREAS;
    NRF_POWER->RESETREAS = 0xFFFFFFFF;
  }
  // Breadcrumb capture-and-clear — SD-AWARE, unconditionally. The evening of
  // 2026-08-18 proved the "init runs pre-SD so direct access is legal"
  // assumption WRONG on this core: a direct GPREGRET2 RMW here killed the
  // boot before its own banner (build de849fbb, dark on a healthy board that
  // booted its predecessor minutes earlier), exactly as the direct RESETREAS
  // write had killed 1c72f10f. The SoftDevice is live earlier than setup()
  // on this core; POWER is SD-owned; there is no legal direct path. This is
  // also why reas= reads 0: the sd_ getters run, not the raw register.
  {
    uint8_t sd_en2 = 0;
    sd_softdevice_is_enabled(&sd_en2);
    if (sd_en2) {
      uint32_t g2 = 0;
      sd_power_gpregret_get(1, &g2);
      s_crumb_last = (uint8_t)((g2 >> 4) & 0x7u);
      sd_power_gpregret_clr(1, 0x70u);
    } else {
      s_crumb_last = (uint8_t)((NRF_POWER->GPREGRET2 >> 4) & 0x7u);
      NRF_POWER->GPREGRET2 &= ~0x70u;
    }
  }
  pinMode(PIN_DIVIDER_EN, OUTPUT);
  digitalWrite(PIN_DIVIDER_EN, HIGH);   // divider off until a read wants it
  pinMode(PIN_CHG_STATE, INPUT_PULLUP); // ~CHG is open-drain: pullup, LOW=charging
  pinMode(PIN_HICHG, INPUT);            // released = 50 mA until we see charging
  s_fast_charge_on = false;
}

void update_charge_current() {
  // Cheap enough to call at any rate: one digitalRead, and a pinMode only on
  // an actual transition. See PIN_HICHG's comment for why this is gated on
  // charging rather than simply set once at boot.
#if JH_FAST_CHARGE_ENABLED
  const bool charging_now = digitalRead(PIN_CHG_STATE) == LOW;
  if (charging_now == s_fast_charge_on) return;          // no change
  if (charging_now) {
    pinMode(PIN_HICHG, OUTPUT);
    digitalWrite(PIN_HICHG, LOW);                        // 100 mA
  } else {
    pinMode(PIN_HICHG, INPUT);                           // release -> 50 mA
  }
  s_fast_charge_on = charging_now;
#endif
}

// Acquisition time for the production read: 15 µs (SAADC_CH_CONFIG_TACQ_15us).
//
// MEASURED, not chosen (SENSE_FIRST_BOOT item 24, `vbatscan` on a rested
// cell): 3 µs 4044 | 5 µs 4056 | 10 µs 4077 | 15 µs 4082 | 20 µs 4082 |
// 40 µs 4085. The reading climbs and then FLATTENS at 15 µs — that is the
// SAADC finally getting long enough to charge through the divider's ~340 kΩ
// source impedance, which the file header predicted and nothing had tested
// because the Adafruit core does not expose TACQ per-read.
//
// 15 µs rather than 40 µs deliberately: the curve is flat from 15 on (40 µs
// buys 3 mV, inside the noise), and this runs on battery. Taking the knee,
// not the extreme.
const int kTacqDefault = 3;  // index into the sweep: 0=3µs 1=5µs 2=10µs 3=15µs

float s_vbat_scale = 1.0f;  // per-unit; overridden from NVS at boot

void set_vbat_scale(float scale) {
  // Guard the range: a scale far from 1.0 is a typo or a bad calibration,
  // and silently accepting 10x would make the gauge confidently absurd.
  if (scale > 0.8f && scale < 1.25f) s_vbat_scale = scale;
}

int vbat_mv() {
  // One implementation, shared with the diagnostic below, so the number this
  // returns in the field can never drift from the number the sweep measured.
  const int raw = vbat_mv_tacq(kTacqDefault);
  if (raw < 0) return -1;
  return (int)(raw * s_vbat_scale + 0.5f);
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

  // Every wait below is BOUNDED. The first cut of this used bare
  // `while (!EVENTS_x) {}`, and that wedged the board on real silicon: the
  // radio still accepted BLE connections (the SoftDevice runs under us) while
  // loop() sat spinning forever, so it looked alive and answered nothing.
  //
  // vbat_mv() is on the `stats`/`info` path, so ANY unbounded spin here is a
  // whole-device hang, not a bad reading. A conversion is ~2 µs + TACQ; 5 ms
  // is four orders of magnitude of headroom and still imperceptible. Bailing
  // out returns -1, which every caller already handles as "unsupported".
  const uint32_t kSpinTimeoutUs = 5000;
  int32_t sum = 0;
  const int kReads = 8;
  volatile int16_t buf = 0;
  bool timed_out = false;

  // Clear stale events BEFORE the first start, not just between iterations —
  // a leftover EVENTS_STARTED would let the first wait fall straight through.
  NRF_SAADC->EVENTS_STARTED = 0;
  NRF_SAADC->EVENTS_END = 0;
  NRF_SAADC->EVENTS_STOPPED = 0;

  for (int i = 0; i < kReads + 1 && !timed_out; ++i) {  // +1: throwaway first
    NRF_SAADC->RESULT.PTR = (uint32_t)&buf;
    NRF_SAADC->RESULT.MAXCNT = 1;

    NRF_SAADC->EVENTS_STARTED = 0;
    NRF_SAADC->EVENTS_END = 0;
    NRF_SAADC->TASKS_START = 1;
    uint32_t t0 = micros();
    while (!NRF_SAADC->EVENTS_STARTED) {
      if (micros() - t0 > kSpinTimeoutUs) { timed_out = true; break; }
    }
    if (timed_out) break;

    NRF_SAADC->TASKS_SAMPLE = 1;
    t0 = micros();
    while (!NRF_SAADC->EVENTS_END) {
      if (micros() - t0 > kSpinTimeoutUs) { timed_out = true; break; }
    }
    if (timed_out) break;

    NRF_SAADC->EVENTS_STOPPED = 0;
    NRF_SAADC->TASKS_STOP = 1;
    t0 = micros();
    while (!NRF_SAADC->EVENTS_STOPPED) {
      if (micros() - t0 > kSpinTimeoutUs) { timed_out = true; break; }
    }
    if (timed_out) break;

    if (i > 0) sum += (buf < 0) ? 0 : buf;  // SAADC can report small negatives
  }
  NRF_SAADC->ENABLE = 0;  // leave it off so the next analogRead() re-inits cleanly

  digitalWrite(PIN_DIVIDER_EN, HIGH);  // divider back off — no idle drain

  // A timed-out conversion is NOT a reading. Report -1 ("unsupported"), which
  // callers already handle, rather than an average of however many samples
  // happened to land — the same rule the rest of this session kept relearning:
  // a measurement that did not happen must never be dressed up as a value.
  if (timed_out) return -1;

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

uint32_t reset_reason() { return s_reset_reas; }

void breadcrumb_set(uint8_t stage) {
  // SD-aware, same lesson as reset_reason(): POWER is SD-owned; direct
  // register writes with the SoftDevice live hard-fault before the banner.
  const uint32_t mask = 0x70u;                    // bits 4-6
  const uint32_t val  = ((uint32_t)stage & 0x7u) << 4;
  uint8_t sd_en = 0;
  sd_softdevice_is_enabled(&sd_en);
  if (sd_en) {
    sd_power_gpregret_clr(1, mask);
    if (val) sd_power_gpregret_set(1, val);
  } else {
    NRF_POWER->GPREGRET2 = (NRF_POWER->GPREGRET2 & ~mask) | val;
  }
}

uint8_t breadcrumb_last() { return s_crumb_last; }

int fast_charge_state() {
  // Read the actual register state, not s_fast_charge_on — the point is to
  // catch the cases where the mirror and the silicon disagree.
  const uint32_t pin = g_ADigitalPinMap[PIN_HICHG];   // D22 -> P0.13
  NRF_GPIO_Type* port = (pin < 32) ? NRF_P0 : NRF_P1;
  const uint32_t bit = pin & 31;
  const bool is_output =
      ((port->PIN_CNF[bit] >> GPIO_PIN_CNF_DIR_Pos) & 1) == GPIO_PIN_CNF_DIR_Output;
  if (!is_output) return 0;                            // released = 50 mA
  return ((port->OUT >> bit) & 1) ? 0 : 1;             // driving LOW = 100 mA
}

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
  // RELEASE THE BUS BEFORE CUTTING THE RAIL (2026-08-13, the night the
  // second sensor died). Nordic DevZone, on this exact module: SDA/SCL
  // energized while the 6D rail is down BACK-DRIVE the sensor die through
  // its ESD structures and corrupt its power-up sequencing. The original
  // sequence here cut the rail with Wire1's TWIM still owning the pins —
  // and System OFF RETAINS pin state, so the sensor spent every sleep
  // being phantom-fed through its bus pins. `off` was exercised on the
  // mule the day before its sensor stopped ACKing; suspected contributor
  // (SENSE_FIRST_BOOT 16f web-findings addendum). Order now: bus pins to
  // input-no-pull FIRST, settle, then rail down. One audited implementation
  // of that detach lives in jh_imu::bus_release() (playbook 6b rule 1:
  // sequencing pairs are copied, never improvised) — it disables the TWIM
  // peripheral (whose begin sets internal pull-ups on both lines, alive
  // regardless of the 6D rail) and floats SDA/SCL/INT1 with pulls off.
  jh_imu::bus_release();
  delay(1);
  // DRIVE STRENGTH, NOT pinMode (DECISIONS #37). P1.08 is the sensor's
  // SUPPLY, not a regulator enable, and pinMode() silently selects standard
  // drive — the exact mistake that cost four days and two wrong
  // dead-hardware verdicts. High drive here too, so the pad can actually
  // sink the rail (100 nF plus the sensor) instead of coasting down.
  //
  // WHY THE RAIL STILL DROPS HERE, when the power design says the product
  // performs zero rail transitions: this is the one sanctioned exception,
  // and it is deliberate. `off` is human-invoked, announced, rare, and runs
  // the audited sequence (bus floated FIRST, so nothing can back-feed
  // through R14/R15). The alternative — leaving the rail up and putting the
  // sensor to sleep with a register write — swaps a proven-safe operation
  // for a FALLIBLE I2C write with no verification, and if that write failed
  // the sensor would sit at ~0.9 mA and flatten the cell in days. The rule
  // is about the everyday loop, not about a deliberate shutdown.
  const uint32_t rail = g_ADigitalPinMap[PIN_LSM6DS3TR_C_POWER];
  nrf_gpio_cfg(rail, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_CONNECT,
               NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
  nrf_gpio_pin_clear(rail);
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
