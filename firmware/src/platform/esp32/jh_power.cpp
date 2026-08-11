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
// No battery sensing on this board at all, so no acquisition-time question.
int  vbat_mv_tacq(int) { return -1; }

// Feature-frozen board (DECISIONS #27): no soft-off — deep sleep was an
// explicitly deferred v1 item and stays that way. `off` answers ERR.
bool system_off() { return false; }

}  // namespace jh_power
