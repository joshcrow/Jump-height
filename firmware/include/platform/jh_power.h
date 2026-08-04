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

// 1 = charger active (USB present, cell charging), 0 = not charging,
// -1 = unknown/unsupported.
int charging();

}  // namespace jh_power
