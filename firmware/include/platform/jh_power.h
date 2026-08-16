// jh_power.h — battery telemetry platform seam (docs/sense.md §3.4).
//
// The sealed puck has no cable and no screen: if the firmware doesn't say
// what the battery is doing, nobody knows until it dies mid-session. This
// seam is the read-only half of the S2 battery milestone — voltage, a
// resting-voltage percent estimate, and charger state — surfaced as NEW
// keys on the existing INFO/STATS lines (the §4 "adder" architecture:
// clients that don't know a key skip it, so nothing anywhere else changes).
//
// Deliberately NOT here (they belong to the sleep/LED milestones, §3.5 and
// §3.10, which need design work of their own): the low-voltage System OFF
// cutoff, any LED language, and the P0.13 charge-current select — the
// BQ25101's 50 mA default is 0.2C for the 250 mAh cell actually installed
// (2026-08: the cell soldered on the bench is 250 mAh, not the 500 mAh the
// original §1 table planned for), which is the gentle, correct rate; there
// is nothing to switch.
//
// Semantics: every accessor returns "unsupported" (-1) on platforms with no
// battery sense — the ESP32/FireBeetle build (feature-frozen, decision #27;
// its board never had a sense divider wired) and, by default, the host
// build (which can script values via environment for CI — see
// src/platform/host/jh_power.cpp). main.cpp only appends the battery keys
// when vbat_mv() >= 0, so v1 clients and every existing test see the exact
// protocol they always did.
//
// Percent is a RESTING-voltage estimate from a small lookup curve — while
// the charger is on (charging()==1) the cell voltage floats high and pct
// reads optimistic; clients should show "charging" instead of trusting the
// number. Good enough to answer "should I charge before this session?",
// which is the whole product question.
//
// SPDX-License-Identifier: MIT

#pragma once

namespace jh_power {

// Configure pins/state. Call once from setup(), any time after Arduino
// core init; safe no-op on unsupported platforms.
void init();

// Battery voltage in millivolts, or -1 where unsupported. May perform a
// short blocking ADC read (tens of microseconds to ~a millisecond); only
// ever called from command handling, never the sample loop.
int vbat_mv();

// 0..100 resting-voltage estimate (see header comment), or -1.
int batt_pct();

// PER-UNIT correction multiplying vbat_mv()'s reading. Default 1.0.
//
// The divider resistors and the ADC reference both carry real tolerance, and
// the resulting error is a GAIN error — proportional, not an offset. On the
// first Sense it measured 1.8% low (4090 mV read vs 4165 measured), i.e. a
// scale of ~1.018. That number belongs to THAT BOARD: correcting it in the
// compiled constants would make every other unit wrong by its neighbour's
// error, which is why it is a persisted per-unit term (jh_persist::Key::
// VbatScale) rather than an edit to jh_power.cpp's divider maths.
//
// Deliberately NOT applied by vbat_mv_tacq(): that is the raw instrument for
// the acquisition-time sweep, and a calibration multiplier riding on it
// would quietly rescale a diagnostic meant to show the sensor as it is.
void set_vbat_scale(float scale);

// BENCH DIAGNOSTIC, SENSE_FIRST_BOOT item 24. Same measurement as vbat_mv()
// but with the ADC's acquisition time forced to a chosen setting:
//   0=3us 1=5us 2=10us 3=15us 4=20us 5=40us
// Returns -1 for an out-of-range code or where the platform cannot do it.
//
// Why the seam carries a diagnostic at all: two meter points proved vbat_mv()
// reads ~2.7% low but could not identify WHY, and the candidates need
// different fixes in different places. If the reading climbs with acquisition
// time, the ADC is not getting long enough to charge through the divider's
// ~340 kOhm source impedance — a firmware fix, correct for every unit. If it
// does not move, the error is in the divider resistors or the reference — a
// PER-UNIT calibration, which it would be actively wrong to bake into
// firmware. Sweeping this is the only way to tell the two apart.
int vbat_mv_tacq(int tacq_code);

// 1 = charger active (USB present, cell charging), 0 = not charging,
// -1 = unknown/unsupported.
int charging();

// Select the charger's fast (100 mA) current while USB is actually charging,
// and release it otherwise. Safe to call often. No-op on platforms without a
// selectable charge current, and compiled out entirely when
// JH_FAST_CHARGE_ENABLED is 0.
void update_charge_current();

// Why the chip remembers its own death: three unexplained reboots on
// 2026-08-16 (BLE `selftest`/`revive`, cause still open) were diagnosed
// blind because nobody reads the nRF52840's RESETREAS register — it
// distinguishes watchdog, CPU lockup, soft reset and pin reset. Captured
// once at init() (and cleared, per the PS: bits accumulate across resets
// until written), reported on INFO. 0 = clean power-on or unsupported
// platform.
uint32_t reset_reason();

// HICHG drive readback: 1 = driving the 100 mA select, 0 = released
// (50 mA), -1 = unknown/unsupported. Exists because fast charge shipped as
// `built-unverified` and the first span-timing measurement (2026-08-16)
// found the cell charging at the 50 mA rate with the driver code
// confirmed present — this answers the firmware half of that question
// from the device itself instead of by inference.
int fast_charge_state();

// Soft power-off — the `off` command's engine (docs/sense.md §3.5's
// smallest useful slice, built 2026-08-04 because a battery-powered board
// with no sleep otherwise runs until the cell is flat). On the Sense:
// cuts the IMU's power rail, then enters nRF System OFF (~µA) — wake is
// USB attach or a reset tap, charging works while off regardless (the
// BQ25101 needs no CPU). DOES NOT RETURN on platforms that support it.
// Returns false where unsupported (ESP32 — feature-frozen; host default —
// the fake/CI device shouldn't kill its own process): the caller answers
// ERR instead. The full sleep design (motion wake via the IMU interrupt,
// auto-off timers, low-voltage cutoff) remains the S2 milestone — this is
// only the manual switch.
bool system_off();

}  // namespace jh_power
