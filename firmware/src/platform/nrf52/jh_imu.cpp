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
#include <Wire.h>      // bus_diag control: stock Wire1 A/B
#include <nrf_gpio.h>  // bus_diag rail-enable readback (no rail edge)
#include "platform/jh_persist.h"

#include "lsm6ds3_min.h"
#include "twim_bounded.h"

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
  // HIGH DRIVE (H0H1), NOT the Arduino default. THIS IS THE BUG THAT COST
  // THREE BOARDS AND FOUR DAYS (2026-08-14, proven on silicon):
  // pinMode(OUTPUT) configures S0S1 — standard drive, ~2 mA. P1.08 does not
  // merely enable a regulator on this module, it SOURCES the sensor domain
  // (sensor VDD + the on-module I2C pull-ups). At standard drive the pad
  // cannot supply that load: it sags to ground, the rail never comes up,
  // the bus sits dead, and the sensor never ACKs — which is indistinguishable
  // from a dead sensor and was twice diagnosed as exactly that. Measured on a
  // factory-fresh board: S0S1 -> pad reads LOW, SDA/SCL held low, every
  // transaction TIMEOUT; H0H1 (~10 mA) -> pad reads HIGH, bus idles high,
  // sensor ACKs at 0x6A on the first try. Same board, same second, one
  // register field apart. Never configure this pin with pinMode().
  {
    const uint32_t rail = g_ADigitalPinMap[PIN_LSM6DS3TR_C_POWER];
    nrf_gpio_cfg(rail, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_CONNECT,
                 NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
    nrf_gpio_pin_set(rail);
  }
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

  // The bus is deliberately NOT started here: probe() starts it on first
  // use. Bounded transactions (twim_bounded.h, 16d) make "started against a
  // wedged bus" survivable — a held line costs one ~2 ms timeout, never a
  // hang — but there is still no reason to touch the bus before the first
  // real question.
}

// ---- bounded transactions (16d) -------------------------------------------
//
// The core's Wire_nRF52.cpp spins on `while(!EVENTS_...)` with NO timeout —
// a held bus hangs the caller forever. That is how a wedged sensor turned
// into an unbootable device on 2026-08-11 (16c's boot loop), and how a
// mid-session wedge would turn into a lost session on the water. All bus
// traffic now goes through TwimBounded (twim_bounded.h): same TWIM
// peripheral, same transactions, explicit time bound on every wait. The
// bit-banged health-check/bus-clear helpers that used to live here are gone
// — they were the false-negative instruments the 16f addendum convicts, and
// bounded transactions make their question ("is the bus safe to touch?")
// unnecessary.

static TwimBounded s_bus;
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
  bus_release();
  delay(2);
  const uint32_t rail = g_ADigitalPinMap[PIN_LSM6DS3TR_C_POWER];
  // High drive here too — see init(). A standard-drive re-assert would
  // leave the rail down and make revive() look like a failed recovery.
  nrf_gpio_cfg(rail, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_CONNECT,
               NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
  nrf_gpio_pin_clear(rail);
  delay(600);
  nrf_gpio_pin_set(rail);
  delay(45);
  return true;
}

void bus_release() {
  // The power-down half of the 16g sequencing pair, in ONE audited place
  // (playbook 6b rule 1: copied, never improvised): disable TWIM, float
  // SDA/SCL (TwimBounded::end does both), float INT1. After this, no MCU
  // line can energize the sensor domain. Callers: revive() above and
  // jh_power::system_off() before it cuts the rail.
  s_bus.end();
  s_wire_started = false;
  pinMode(PIN_LSM6DS3TR_C_INT1, INPUT);  // no pull — truly floating
}

bool bus_diag_rail(BusDiag& out) {
  // Contract and safety argument in jh_imu.h. Order matters: read-only
  // facts first, bounded driver next, and the stock-Wire1 control LAST
  // (Wire1 spins unbounded on a held bus — if it hangs, the watchdog
  // reboots us and everything above has already been reported).
  const uint32_t rail_nrf = g_ADigitalPinMap[PIN_LSM6DS3TR_C_POWER];
  // Read the rail enable back through its own input buffer WITHOUT
  // changing what it is driving — init() already drove it HIGH; adding
  // INPUT_CONNECT to an output is not a rail transition.
  nrf_gpio_cfg(rail_nrf, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_CONNECT,
               NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_S0S1, NRF_GPIO_PIN_NOSENSE);
  out.rail_pin = (uint8_t)nrf_gpio_pin_read(rail_nrf);

  // Bus idle levels with OUR pulls removed: whatever holds the lines high
  // now can only be the module's own pull-ups, which sit on the switched
  // rail. This is the rail indicator, and it costs no rail edge.
  s_bus.end();
  s_wire_started = false;
  pinMode(PIN_WIRE1_SDA, INPUT);
  pinMode(PIN_WIRE1_SCL, INPUT);
  delay(2);
  out.sda_pulled_up = (uint8_t)digitalRead(PIN_WIRE1_SDA);
  out.scl_pulled_up = (uint8_t)digitalRead(PIN_WIRE1_SCL);

  return true;
}

void bus_rail_registers(uint32_t& out_latch, uint32_t& dir, uint32_t& cnf,
                        uint32_t& in_level) {
  // RAW register truth for the sensor rail enable (P1.08). Every earlier
  // rail conclusion rested on nrf_gpio_pin_read() alone; this reports the
  // OUT latch, DIR, PIN_CNF and IN separately, so "we are driving it high
  // and the pad is low" can be told apart from "we never drove it at all".
  const uint32_t nrf_pin = g_ADigitalPinMap[PIN_LSM6DS3TR_C_POWER];
  NRF_GPIO_Type* port = (nrf_pin < 32) ? NRF_P0 : NRF_P1;
  const uint32_t bit = nrf_pin & 31;
  out_latch = (port->OUT >> bit) & 1u;
  dir       = (port->DIR >> bit) & 1u;
  cnf       = port->PIN_CNF[bit];
  in_level  = (port->IN >> bit) & 1u;
}

void bus_rail_sweep(uint8_t state, uint8_t& sda, uint8_t& scl, uint8_t& pin) {
  // One step of a rail-polarity sweep. state: 0=drive LOW, 1=drive HIGH,
  // 2=release (input, no pull — lets any external pull decide). Bus lines
  // are floated by the audited detach first and read against weak internal
  // pull-DOWNS, which can only sink toward ground and therefore cannot
  // back-feed an unpowered die (the 16g hazard is pull-UPS into a dead
  // rail). Exists because "drive it HIGH and hope" was never actually
  // verified to power anything: a factory-fresh board reports the rail
  // down, so the assumption itself is now under test.
  bus_release();
  pinMode(PIN_WIRE1_SDA, INPUT_PULLDOWN);
  pinMode(PIN_WIRE1_SCL, INPUT_PULLDOWN);
  const uint32_t nrf_pin = g_ADigitalPinMap[PIN_LSM6DS3TR_C_POWER];
  if (state == 2) {
    nrf_gpio_cfg(nrf_pin, NRF_GPIO_PIN_DIR_INPUT, NRF_GPIO_PIN_INPUT_CONNECT,
                 NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_S0S1, NRF_GPIO_PIN_NOSENSE);
  } else {
    // HIGH DRIVE (H0H1) for state 3, standard (S0S1) otherwise. This pin
    // does not merely enable a regulator — the project's own RCA records
    // it SOURCING the sensor rail ("inrush exceeds what the pin sources
    // cleanly"). A standard-drive pad (~2 mA) loaded by the sensor plus
    // its bus pull-ups sags to ground and reads back LOW, which is exactly
    // what all three boards report. H0H1 raises that to ~10 mA.
    const nrf_gpio_pin_drive_t drive =
        (state == 3) ? NRF_GPIO_PIN_H0H1 : NRF_GPIO_PIN_S0S1;
    nrf_gpio_cfg(nrf_pin, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_CONNECT,
                 NRF_GPIO_PIN_NOPULL, drive, NRF_GPIO_PIN_NOSENSE);
    if (state) nrf_gpio_pin_set(nrf_pin); else nrf_gpio_pin_clear(nrf_pin);
  }
  delay(120);  // regulator start + sensor Ton + margin
  pin = (uint8_t)nrf_gpio_pin_read(nrf_pin);
  sda = (uint8_t)digitalRead(PIN_WIRE1_SDA);
  scl = (uint8_t)digitalRead(PIN_WIRE1_SCL);
  // SECOND reading with internal PULL-UPS. This is the one that matters:
  // pull-downs only prove "no powered external pull-up is winning", which
  // is ALSO what you see on a board that simply has no external pull-ups —
  // so a pull-down-only reading cannot tell a dead rail from a normal
  // design, and two boards were convicted on exactly that ambiguity. With
  // ~13k internal pull-ups engaged, a line that STILL reads 0 is genuinely
  // held low by something; a line that reads 1 means the bus is free and
  // any transaction timeout is ours, not the hardware's.
  pinMode(PIN_WIRE1_SDA, INPUT_PULLUP);
  pinMode(PIN_WIRE1_SCL, INPUT_PULLUP);
  delay(5);
  sda = (uint8_t)(sda | (digitalRead(PIN_WIRE1_SDA) << 1));
  scl = (uint8_t)(scl | (digitalRead(PIN_WIRE1_SCL) << 1));
  pinMode(PIN_WIRE1_SDA, INPUT);
  pinMode(PIN_WIRE1_SCL, INPUT);
}

void pin_census(char* buf, int cap) {
  // Read EVERY GPIO twice — once against a weak internal pull-DOWN, once
  // against a weak internal pull-UP — and report the pair per pin.
  //
  // WHY: three pins on a factory-fresh board (P1.08 rail enable, P0.07 SDA,
  // P0.27 SCL) read LOW even when driven or pulled high. Either those pins
  // are genuinely held to ground, or our read path is lying. A census
  // settles it without a meter: an unconnected pin MUST follow its pull
  // (0 with pull-down, 1 with pull-up). If most pins follow and only ours
  // do not, the finding is real hardware. If NOTHING follows, the
  // instrument is broken and every rail conclusion built on it is void —
  // which is the failure mode that has already produced two wrong verdicts.
  //
  // Safety: inputs and weak pulls only, never an output. QSPI pins are
  // skipped so a pull cannot disturb the flash mid-operation.
  const uint32_t kSkip[] = {21, 25, 20, 24, 22, 23};  // QSPI SCK/CS/IO0-3
  int n = 0;
  for (uint32_t pin = 0; pin < 48 && n < cap - 24; ++pin) {
    if (pin >= 32 && pin < 32) continue;
    bool skip = false;
    for (unsigned k = 0; k < sizeof(kSkip) / sizeof(kSkip[0]); ++k) {
      if (pin == kSkip[k]) skip = true;
    }
    if (skip) continue;
    NRF_GPIO_Type* port = (pin < 32) ? NRF_P0 : NRF_P1;
    const uint32_t bit = pin & 31;
    const uint32_t saved = port->PIN_CNF[bit];
    // pull-down read
    port->PIN_CNF[bit] = (GPIO_PIN_CNF_DIR_Input << GPIO_PIN_CNF_DIR_Pos) |
                         (GPIO_PIN_CNF_INPUT_Connect << GPIO_PIN_CNF_INPUT_Pos) |
                         (GPIO_PIN_CNF_PULL_Pulldown << GPIO_PIN_CNF_PULL_Pos);
    delayMicroseconds(200);
    const uint32_t dn = (port->IN >> bit) & 1u;
    port->PIN_CNF[bit] = (GPIO_PIN_CNF_DIR_Input << GPIO_PIN_CNF_DIR_Pos) |
                         (GPIO_PIN_CNF_INPUT_Connect << GPIO_PIN_CNF_INPUT_Pos) |
                         (GPIO_PIN_CNF_PULL_Pullup << GPIO_PIN_CNF_PULL_Pos);
    delayMicroseconds(200);
    const uint32_t up = (port->IN >> bit) & 1u;
    port->PIN_CNF[bit] = saved;  // leave every pin exactly as found
    n += snprintf(buf + n, cap - n, "%lu.%02lu:%lu%lu ",
                  (unsigned long)(pin / 32), (unsigned long)(pin % 32),
                  (unsigned long)dn, (unsigned long)up);
  }
  if (n < cap) buf[n] = 0;
}

bool bus_diag_twim(BusDiag& out) {
  // The bounded driver, at both SA0 strap addresses. Split out so the
  // caller can print the read-only rail facts FIRST — board #3 showed the
  // whole command dying silently, and a report you never see cannot tell
  // you where it died.
  s_bus.begin(PIN_WIRE1_SDA, PIN_WIRE1_SCL);
  s_wire_started = true;
  const uint8_t reg = 0x0F;  // WHO_AM_I pointer — a 1-byte write is the ACK test
  out.twim_result     = (uint8_t)s_bus.write(0x6A, &reg, 1);
  out.twim_result_alt = (uint8_t)s_bus.write(0x6B, &reg, 1);

  out.wire_ack = out.wire_ack_alt = out.wire_whoami = 0xFF;  // "not run"
  return true;
}

bool bus_diag_wire(BusDiag& out) {
  // The CONTROL half, split into its own call (2026-08-13, board #3): stock
  // Wire1 spins UNBOUNDED on a held bus, so running it in the same call as
  // the read-only facts meant a hang ate the whole report — 30 s of silence
  // and a watchdog reboot, with the rail data we actually needed never
  // printed. The caller now prints everything above BEFORE calling this, so
  // a hang here costs only the control row.
  //
  // Hand the peripheral over cleanly (both drivers own TWIM1, so the bounded
  // one must be fully disabled first) and let stock Wire1 ask the identical
  // question. The rail stays UP throughout — the 16g hazard is an energized
  // bus over a DEAD rail, which this is not.
  const uint8_t reg = 0x0F;
  s_bus.end();
  s_wire_started = false;
  Wire1.begin();
  Wire1.setClock(400000);
  Wire1.beginTransmission(0x6A);
  Wire1.write(reg);
  out.wire_ack = (Wire1.endTransmission() == 0) ? 1 : 0;
  Wire1.beginTransmission(0x6B);
  Wire1.write(reg);
  out.wire_ack_alt = (Wire1.endTransmission() == 0) ? 1 : 0;
  out.wire_whoami = 0;
  const uint8_t found_addr = out.wire_ack ? 0x6A : (out.wire_ack_alt ? 0x6B : 0);
  if (found_addr) {
    Wire1.beginTransmission(found_addr);
    Wire1.write(reg);
    if (Wire1.endTransmission(false) == 0 &&
        Wire1.requestFrom(found_addr, (uint8_t)1) == 1) {
      out.wire_whoami = (uint8_t)Wire1.read();
    }
  }
  Wire1.end();
  // Leave the bus back under the bounded driver, as every other path
  // expects to find it.
  s_bus.begin(PIN_WIRE1_SDA, PIN_WIRE1_SCL);
  s_wire_started = true;
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
    s_bus.begin(PIN_WIRE1_SDA, PIN_WIRE1_SCL);  // 400 kHz, bounded (16d)
    s_wire_started = true;
  }
  const bool found = Lsm6ds3Min::probe(s_bus, Lsm6ds3Min::I2C_ADDR);
  jh_persist::save(jh_persist::Key::ProbeGuard, 0.0f);  // survived: clear guard
  return found;
}

bool begin(uint8_t addr) {
  (void)addr;  // always the real device at Lsm6ds3Min::I2C_ADDR — see above
  return s_imu.begin(s_bus, Lsm6ds3Min::I2C_ADDR);
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
