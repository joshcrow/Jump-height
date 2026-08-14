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
// P1.08, which IS the supply pad — not a regulator enable; see
// docs/xiao-hardware-truth.md). Sequencing per SENSE_FIRST_BOOT 16g: bus lines
// floated FIRST (energized lines while the rail is down back-drive the
// sensor and corrupt its power-up — the documented failure the 2026-08-11
// rail cycle caused), long discharge, rail up, full settle. Returns false
// on platforms with no rail to cycle. Follow with probe()/begin().
bool revive();

// One-shot sensor-bus diagnostic (bench). Answers, in one pass and with NO
// rail transitions at all (read-only on the power pin — the quarantine-safe
// class of check): is the sensor rail enable actually high, are the module's
// rail-powered pull-ups holding the bus up, does the bounded driver see an
// ACK, and does the stock Arduino Wire1 driver — the one that read 0.970 g
// on factory-fresh silicon 2026-08-12, before the bounded driver existed —
// see the same thing? A disagreement between the last two convicts the
// driver, not the hardware. Fills `out` with a one-line-per-fact report.
// Returns lines written, 0 where there is no sensor bus.
struct BusDiag {
  uint8_t rail_pin;        // P1.08 level read back while driven (1 = up)
  uint8_t sda_pulled_up;   // bus idle level with our pulls off (1 = module
  uint8_t scl_pulled_up;   //     pull-ups powered ⇒ rail really is up)
  uint8_t twim_result;     // TwimBounded::Result at 0x6A
  uint8_t twim_result_alt; // ... at 0x6B (SA0 strap alternative)
  uint8_t wire_ack;        // stock Wire1 ACK at 0x6A (the control)
  uint8_t wire_ack_alt;    // ... at 0x6B
  uint8_t wire_whoami;     // WHO_AM_I via Wire1 if it ACKed (0x6A expected)
};
void bus_rail_registers(uint32_t& out_latch, uint32_t& dir, uint32_t& cnf,
                        uint32_t& in_level);
void bus_rail_sweep(uint8_t state, uint8_t& sda, uint8_t& scl, uint8_t& pin);
void pin_census(char* buf, int cap);
bool bus_diag_rail(BusDiag& out);
bool bus_diag_twim(BusDiag& out);
// The Wire1 control half — SEPARATE because stock Wire1 spins unbounded on
// a held bus (it hung board #3 and ate the whole report). Call only after
// the caller has already printed bus_diag()'s facts.
bool bus_diag_wire(BusDiag& out);

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
