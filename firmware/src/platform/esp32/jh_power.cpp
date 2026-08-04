// jh_power seam, ESP32/FireBeetle — permanently unsupported stub.
//
// The FireBeetle rig never had a battery-sense divider wired (the board
// has one on-PCB but it isn't connected to the harness this project
// validated, and the build is feature-frozen — DECISIONS #27: bugfix-only
// until retirement). Returning -1 everywhere means main.cpp never emits
// the battery keys on this platform, so the v1 protocol stays
// byte-identical to what every shipped client and test already expects.
//
// SPDX-License-Identifier: MIT

#include "platform/jh_power.h"

namespace jh_power {

void init() {}
int  vbat_mv()  { return -1; }
int  batt_pct() { return -1; }
int  charging() { return -1; }

}  // namespace jh_power
