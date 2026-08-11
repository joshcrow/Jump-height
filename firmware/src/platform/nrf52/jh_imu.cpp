// jh_imu.cpp — nRF52 (Seeed XIAO nRF52840 Sense) implementation of the
// jh_imu seam (firmware/include/platform/jh_imu.h). See docs/sense.md §3.7/
// §3.9 and this platform's binding handoff notes.
//
// LSM6DS3TR-C over the SENSE'S INTERNAL I2C bus, wrapping the register-level
// driver in lsm6ds3_min.h (read that file for the register map this wrapper
// doesn't re-explain).
//
// Bus + pins: WIRE1 (NOT the default Wire — that's the exposed
// castellated-pad bus), on P0.07 (SDA) / P0.27 (SCL). Confirmed by reading
// the INSTALLED framework's own variant files after PlatformIO downloaded
// them for board id xiaoblesense_adafruit (see platformio.ini for why that
// id/platform source):
//   ~/.platformio/packages/framework-arduinoadafruitnrf52-seeed/variants/
//     Seeed_XIAO_nRF52840_Sense/variant.h:121-124
//       #define PIN_WIRE1_SDA (17)
//       #define PIN_WIRE1_SCL (16)
//       #define PIN_LSM6DS3TR_C_POWER (15)
//       #define PIN_LSM6DS3TR_C_INT1  (18)
//     Seeed_XIAO_nRF52840_Sense/variant.cpp:27-31 (g_ADigitalPinMap[], the
//     table those D-numbers above index into):
//       27,  // D16 is P0.27 (6D_I2C_SCL)
//        7,  // D17 is P0.07 (6D_I2C_SDA)
//       40,  // D15 is P1.08 (6D_PWR)
//       11,  // D18 is P0.11 (6D_INT1, unused by this poll-loop port)
//   and the Wire library itself instantiates Wire1 on exactly those two
//   pins (~/.platformio/packages/.../libraries/Wire/Wire_nRF52.cpp:425-426):
//       TwoWire Wire1(NRF_TWIM1, NRF_TWIS1, ..., PIN_WIRE1_SDA, PIN_WIRE1_SCL);
// This matches docs/sense.md §3.7's own "community says ~P0.07/P0.27"
// note — confirmed exactly, not just "close".
//
// Power rail: P1.08 (PIN_LSM6DS3TR_C_POWER) gates the sensor's power per
// docs/sense.md §1/§3.7 ("power-gated by pin P1.08"; cut in deep standby —
// that's a later, S2-milestone concern, not this port). Driven high once in
// init(), with a boot-settle delay before the bus is used — see
// firmware/SENSE_FIRST_BOOT.md for why the exact delay value is a VERIFY
// item (no datasheet "time to first valid I2C transaction after power-on"
// figure was available to confirm against in the authoring environment).
//
// Dual-probe self-test mapping: jh_imu.h's ADDR_PRIMARY/ADDR_SECONDARY
// (0x68/0x69) are MPU-6050 AD0-strapping candidates; this part's address is
// fixed at 0x6A by the board itself (Lsm6ds3Min::I2C_ADDR), which is
// NEITHER 0x68 nor 0x69. main.cpp's self-test loop (shared, unchanged —
// see jh_imu.h) tries ADDR_PRIMARY first, then ADDR_SECONDARY, and uses
// whichever one probe() answered true for. We designate ADDR_PRIMARY as
// "the slot that means this platform's IMU exists": probe(ADDR_PRIMARY)
// performs the REAL bus transaction against the real address (0x6A) and
// reports that result; probe(ADDR_SECONDARY) always misses. begin()/
// who_am_i()/read_accel_g() below all ignore the `addr` argument they're
// handed (it will only ever be jh_imu::ADDR_PRIMARY, i.e. 0x68 — a
// placeholder, never actually put on the wire) and always operate on the
// real device at 0x6A. The one visible side effect: the self-test's
// `SELFTEST i2c PASS detail=0x68` line will show the placeholder, not the
// true 0x6A — cosmetic only (main.cpp never uses the VALUE for anything
// but that log line and passing it back into begin()), documented in
// firmware/SENSE_FIRST_BOOT.md so it doesn't read as a bug on first boot.
//
// SPDX-License-Identifier: MIT

#include "platform/jh_imu.h"

#include <Arduino.h>
#include <Wire.h>

#include "lsm6ds3_min.h"

namespace jh_imu {

static Lsm6ds3Min s_imu;

void init() {
  // Power the sensor rail, then let it settle before the bus is touched.
  pinMode(PIN_LSM6DS3TR_C_POWER, OUTPUT);
  digitalWrite(PIN_LSM6DS3TR_C_POWER, HIGH);
  // Boot-settle margin (review-nrf52.md finding #5 / SENSE_FIRST_BOOT.md
  // item 7, now RESOLVED-BY-DATASHEET): the real LSM6DS3TR-C datasheet's
  // electrical characteristics table gives Ton (turn-on time, power-up to
  // first valid output) as 35 ms — this project's own authoring environment
  // couldn't fetch the datasheet PDF directly (see this platform's other
  // comments on that), but the figure was extracted from it directly for
  // this fix. The previous 20 ms was a conservative-sounding guess, not a
  // cited spec number, and sat BELOW the real 35 ms figure — masked so far
  // only by incidental boot ordering (other setup work already burning
  // enough wall-clock time before the bus gets touched). 40 ms restores a
  // deliberate margin above the datasheet number rather than trusting
  // "worked in practice" to keep holding as that ordering shifts.
  delay(40);

  Wire1.begin();
  Wire1.setClock(400000);
}

bool probe(uint8_t addr) {
  // Only the PRIMARY slot maps to this platform's (single, fixed-address)
  // IMU — see the file comment above.
  if (addr != ADDR_PRIMARY) return false;
  return Lsm6ds3Min::probe(Wire1, Lsm6ds3Min::I2C_ADDR);
}

bool begin(uint8_t addr) {
  (void)addr;  // always the real device at Lsm6ds3Min::I2C_ADDR — see above
  return s_imu.begin(Wire1, Lsm6ds3Min::I2C_ADDR);
}

uint8_t who_am_i() {
  return s_imu.whoAmI();
}

bool read_accel_g(float& ax, float& ay, float& az) {
  return s_imu.readAccelG(ax, ay, az);
}

bool read_gyro_dps(float& gx, float& gy, float& gz) {
  return s_imu.readGyroDps(gx, gy, gz);
}

}  // namespace jh_imu
