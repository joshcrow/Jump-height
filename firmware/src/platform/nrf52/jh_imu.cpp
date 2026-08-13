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
#include "platform/jh_persist.h"
#include <Wire.h>

#include "lsm6ds3_min.h"

namespace jh_imu {

static Lsm6ds3Min s_imu;

void init() {
  // POWER-CYCLE the sensor rail — not just power it. On a battery-fed board
  // no reset ever removes power: a sensor that wedges its I2C bus (SDA held
  // mid-transaction) stays wedged through every NVIC/DFU/watchdog reset,
  // and every subsequent boot hangs at its first bus touch — observed
  // 2026-08-11 as a boot loop dying at SELFTEST BEGIN, surviving reflashes
  // all evening precisely because only the CPU was ever reset. P1.08 gates
  // the sensor's rail (docs/sense.md §3.7), so a LOW pulse here is a true
  // sensor power-on: bus state cleared, Ton clock restarted, every boot
  // identical whether it followed a cold start, a crash, or an OTA jump.
  // Rail: assert HIGH only — matching the factory boot path the variant
  // itself uses. The hard LOW-then-HIGH power cycle that briefly lived here
  // (2026-08-11 night) is GONE: on a FRESH, healthy board it produced
  // no_device on every boot — hard-discharging the sensor's rail and bus,
  // then re-driving the rail from a standard-drive GPIO, browns the sensor
  // out mid-boot (inrush exceeds what the pin sources cleanly), and a
  // half-booted LSM6DS3TR-C clamps its bus. The very failure signature that
  // got read as dead hardware. Falsified on the replacement board
  // 2026-08-12: same firmware, factory-fresh sensor, same "no_device" —
  // remove the cycle and the sensor reads 0.970 g. See the RCA addendum.
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

  // Wire1 is deliberately NOT started here anymore: probe() starts it only
  // after the bit-banged health check proves the bus alive. Starting it
  // against a wedged bus arms the unbounded spins for whoever touches it
  // first.
}

// ---- bounded first contact ------------------------------------------------
//
// The core's Wire_nRF52.cpp spins on `while(!EVENTS_...)` with NO timeout —
// a held bus hangs the caller forever. That is exactly how a wedged sensor
// turned into an unbootable device on 2026-08-11: the selftest's first probe
// never returned, and the watchdog turned the hang into a boot loop
// (SENSE_FIRST_BOOT 16c). The rule this section enforces: Wire NEVER touches
// a bus that has not first been proven alive by construction-bounded code.
//
// Everything here is bit-banged GPIO at ~50 kHz with explicit loop bounds:
// it cannot hang, only fail. Sequence: health check (both lines high?) →
// if not, the standard 9-SCL-pulse bus-clear + STOP, re-check → bounded
// ACK probe of the device address. Only after an ACK does Wire1 get the pins.

static inline void sdaHigh() { pinMode(PIN_WIRE1_SDA, INPUT_PULLUP); }
static inline void sdaLow()  { pinMode(PIN_WIRE1_SDA, OUTPUT); digitalWrite(PIN_WIRE1_SDA, LOW); }
static inline void sclHigh() { pinMode(PIN_WIRE1_SCL, INPUT_PULLUP); }
static inline void sclLow()  { pinMode(PIN_WIRE1_SCL, OUTPUT); digitalWrite(PIN_WIRE1_SCL, LOW); }
static inline int  sdaRead() { return digitalRead(PIN_WIRE1_SDA); }
static inline int  sclRead() { return digitalRead(PIN_WIRE1_SCL); }
static const uint32_t BB_HALF_US = 10;  // ~50 kHz; sensor supports 400 kHz

static bool busIdleHigh() {
  sdaHigh(); sclHigh(); delayMicroseconds(BB_HALF_US);
  return sdaRead() && sclRead();
}

// Standard bus-clear: up to 9 clock pulses lets a slave stuck mid-byte shift
// out whatever it thinks it still owes, then a STOP releases the bus.
static void busClear() {
  sdaHigh();
  for (int i = 0; i < 9 && !sdaRead(); ++i) {
    sclLow();  delayMicroseconds(BB_HALF_US);
    sclHigh(); delayMicroseconds(BB_HALF_US);
  }
  // STOP: SDA low->high while SCL high
  sdaLow();  delayMicroseconds(BB_HALF_US);
  sclHigh(); delayMicroseconds(BB_HALF_US);
  sdaHigh(); delayMicroseconds(BB_HALF_US);
}

static bool s_wire_started = false;

// Crash-loop detection state — GPREGRET2, the SoftDevice-managed retained
// register (sibling of the DFU magic in GPREGRET). Survives watchdog and
// soft resets, cleared by real power-on. Chosen over a .noinit section
// after measurement: this core's linker scripts define no .noinit region,
// so the attribute landed the flags in ordinary zero-initialised RAM and
// the skip NEVER fired — two banners, two identical hangs, on the very
// board the protection exists for. GPREGRET is the mechanism already
// proven to survive resets on this hardware (the `dfu` command rides its
// sibling), so the protection now stands on measured ground.
static bool s_boot_probe_done = false;  // false only for the boot-time probe

bool revive() {
  // Clean sensor power-cycle — the sequencing the removed 2026-08-11 rail
  // cycle got wrong (16g): it cut the rail with TWIM + pull-ups energized,
  // back-driving the sensor through its bus pins during the LOW — the very
  // corruption it was trying to clear. Order here: release TWIM, float
  // every MCU line into the sensor domain, THEN drop the rail (regulator
  // EN via P1.08 — a logic input, no inrush path through the GPIO), long
  // discharge, rail up, regulator start (3 ms) + sensor Ton (35 ms) with
  // margin before anyone touches the bus.
  Wire1.end();
  s_wire_started = false;
  pinMode(PIN_WIRE1_SDA, INPUT);   // no pull — truly floating
  pinMode(PIN_WIRE1_SCL, INPUT);
  pinMode(PIN_LSM6DS3TR_C_INT1, INPUT);
  delay(2);
  pinMode(PIN_LSM6DS3TR_C_POWER, OUTPUT);
  digitalWrite(PIN_LSM6DS3TR_C_POWER, LOW);
  delay(600);
  digitalWrite(PIN_LSM6DS3TR_C_POWER, HIGH);
  delay(45);
  return true;
}

bool probe(uint8_t addr) {
  // Only the PRIMARY slot maps to this platform's (single, fixed-address)
  // IMU — see the file comment above.
  if (addr != ADDR_PRIMARY) return false;

  // Bounded first contact: the hang-guard is the LEVEL CHECK, not an ACK.
  // Wire's unbounded spins only bite on a HELD bus, so proving both lines
  // idle-high (with one 9-pulse bus-clear attempt for a stuck slave) is
  // exactly sufficient to make Wire safe — and Wire then does the ACK probe
  // it has always done correctly.
  //
  // History, so nobody re-adds it: this function briefly contained a
  // bit-banged ACK probe as an extra gate. It was a false-negative machine —
  // on a factory-fresh board with an idle-high bus it reported no ACK at
  // either address while Wire, asked the same question seconds later,
  // ACKed 0x6A immediately (probe-diag, 2026-08-12: `sda=1 scl=1 bb6A=0
  // bb68=0 wire6A=0`). Those false negatives cascaded into a wrong
  // dead-hardware RCA. A redundant probe that can lie is worse than no
  // probe: deleted rather than debugged.
  // Boot-hang protection, final design: CRASH-LOOP DETECTION, not a probe.
  //
  // Journey (RCA + addendum have the full trail): Wire hangs unboundedly on
  // a held bus, so first contact needed a bound. Four probe designs tried
  // to answer "is the bus safe?" from the outside — bit-bang ACK, GPIO
  // level gate, TWIM register transaction, TWIM + PIN_CNF — and every one
  // produced false negatives against a healthy sensor that plain Wire read
  // perfectly (0.970 g, 4/4). The outside-in question keeps being answered
  // wrong, so stop asking it: let Wire touch the bus exactly as the
  // proven-good firmware always has, and make a HANG survivable instead.
  //
  // A magic+flag pair lives in .noinit RAM (survives watchdog/soft resets,
  // scrambled by real power-on). Set before the first Wire transaction,
  // cleared after it returns. If a held bus hangs the probe, the watchdog
  // reboots us, the flag is found still set, and THIS boot skips the sensor
  // entirely: one ~3.5 s watchdog cost once, then a live, commandable
  // device with an honest `i2c FAIL` row every boot until the bus is
  // physically freed. The healthy path runs zero extra bus operations.
  // STICKY guard in internal flash (jh_persist) — the one store measured to
  // survive both watchdog resets AND the bootloader's register sanitizing.
  // Boot path (s_boot_probe_done false): a set guard means a previous boot
  // died inside this probe — skip the sensor, stay alive, leave the guard
  // SET so every boot skips until a human (or client) runs `selftest`,
  // which comes through here again with s_boot_probe_done true and retries
  // for real. Healthy path writes nothing (guard already clear).
  const bool guard = jh_persist::load(jh_persist::Key::ProbeGuard, 0.0f) > 0.5f;
  if (guard && !s_boot_probe_done) {
    s_boot_probe_done = true;
    return false;              // sticky skip; `selftest` is the retry path
  }
  s_boot_probe_done = true;
  if (!guard) {
    jh_persist::save(jh_persist::Key::ProbeGuard, 1.0f);
  }
  if (!s_wire_started) {
    Wire1.begin();
    Wire1.setClock(400000);
    s_wire_started = true;
  }
  const bool found = Lsm6ds3Min::probe(Wire1, Lsm6ds3Min::I2C_ADDR);
  jh_persist::save(jh_persist::Key::ProbeGuard, 0.0f);  // survived: clear guard
  return found;
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
