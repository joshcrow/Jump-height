// jh_imu.h — IMU platform seam (see docs/sense.md §3.9).
//
// The accelerometer driver, reduced to exactly what main.cpp consumes today:
// bring up the sensor, probe/identify it for the self-test, and read raw
// per-axis acceleration in g-units. main.cpp computes the magnitude itself
// (sqrt(ax^2+ay^2+az^2)) and applies its own gravity-baseline normalization —
// this seam mirrors current usage exactly (raw axes, not a pre-computed
// magnitude).
//
// Units: g (1.0 == the sensor's own nominal 1 g, before any gravity-baseline
// normalization — that correction lives in main.cpp, not here).
// Blocking: probe()/begin()/read_accel_g() may block briefly on the sensor
// bus (I2C transaction time), same as today — they are only ever called from
// setup()/self-test/the paced sample loop, never from a context where that
// would stall something else.
//
// ESP32: MPU-6050 over I2C (src/platform/esp32/jh_imu.cpp, wrapping the
// register-level driver in mpu6050_min.h — read that file for the exact
// register map). The Sense's LSM6DS3TR-C now implements this same
// probe-by-address/begin/who_am_i/read_accel_g shape (src/platform/nrf52/
// jh_imu.cpp, wrapping lsm6ds3_min.h — see docs/sense.md §3.7).
//
// SPDX-License-Identifier: MIT

#pragma once

#include <stdint.h>

namespace jh_imu {

// Two candidate I2C addresses the self-test tries in order, mirroring the
// sensor's AD0-pin strapping options (primary = AD0 low, secondary = AD0
// high — see mpu6050_min.h).
static const uint8_t ADDR_PRIMARY   = 0x68;
static const uint8_t ADDR_SECONDARY = 0x69;

// Bring up the sensor bus. Call once from setup(), before probe()/begin().
void init();

// Detach every MCU line from the sensor's power domain (bus controller off,
// SDA/SCL/INT floated, no pulls) — the power-down half of the 16g sequencing
// pair, in one audited place. MUST be called before anything cuts the
// sensor's rail (jh_power::system_off does). No-op on platforms without a
// switched sensor domain.
void bus_release();

// Clean sensor power-cycle, where the platform has rail control (Sense:
// P1.08-enabled regulator). Sequencing per SENSE_FIRST_BOOT 16g: bus lines
// floated FIRST (energized lines while the rail is down back-drive the
// sensor and corrupt its power-up — the documented failure the 2026-08-11
// rail cycle caused), long discharge, rail up, full settle. Returns false
// on platforms with no rail to cycle. Follow with probe()/begin().
bool revive();

// Software meter (bench diagnostic, SENSE_FIRST_BOOT 16g): decide "sensor
// rail up or down" without a multimeter, using the module's own I2C pull-up
// resistors — they are powered by the same switched rail as the sensor, so
// with the MCU's bus controller detached and a weak internal PULL-DOWN on
// SDA/SCL, a line that still reads HIGH can only be held there by powered
// module pull-ups (rail UP). Three steps: rail as-is/EN high, EN low
// (long discharge), EN high again; each records both line levels. Never
// energizes the sensor domain (pull-downs and the EN logic input only —
// the 16g hazard is pull-UPS into a dead rail, deliberately not used).
// Returns steps written (3), or 0 where there is no rail to test.
// `pin` is the EN line read BACK through its own input buffer while driven:
// it separates "the MCU pin can no longer drive high / EN net shorted low"
// (pin != en) from "EN asserts fine but the rail still never comes up"
// (pin == en, lines low — regulator dead or die shorting the rail).
// `control` is the same readback applied to a known-good driven pin
// (P0.14, battery-divider EN, idles HIGH): 1 proves the readback method
// itself works on this board, so pin=0 readings are real.
struct RailcheckStep {
  uint8_t en; uint8_t pin; uint8_t sda; uint8_t scl; uint8_t control;
};
int railcheck(RailcheckStep out[3]);

// True if a sensor ACKs at this I2C address. Safe to call repeatedly — the
// `selftest` command re-probes on demand, exactly like today.
bool probe(uint8_t addr);

// Wake and configure the sensor found at this address (±8 g, ±500 dps,
// ~44 Hz DLPF — see mpu6050_min.h for why these values). Returns false only
// on an I2C write failure (a wiring problem, not a missing device).
bool begin(uint8_t addr);

// WHO_AM_I register value from the last successful begin() (0x68 = genuine
// silicon; clone chips report other values and are still usable — see the
// self-test's WARN-not-FAIL policy in main.cpp).
uint8_t who_am_i();

// Read acceleration in g-units, raw axes. False on a transient bus failure
// (the caller skips that sample and tries again next tick).
bool read_accel_g(float& ax, float& ay, float& az);

// Read angular rate in deg/s, raw axes. False on a transient bus failure, and
// ALSO false on hardware with no gyro wired up (the 3-axis v1 boards) — so a
// caller must treat false as "no spin information this tick" and fall back to
// the accel-only detector path, not as an error worth reporting.
//
// Why the seam carries this at all: the gyro is not a trick-metric extra, it
// is a detector input. A rotating board-mounted accelerometer reads its own
// omega^2*r, which breaks takeoff and landing detection outright above ~300
// dps — see jump_detector.h's correct_for_spin() and
// sim/experiments/g4_spin_detector.py.
bool read_gyro_dps(float& gx, float& gy, float& gz);

}  // namespace jh_imu
