// shim/Arduino.h — stand-in for the Arduino core header, providing exactly
// the one symbol jh_store.cpp actually uses from it: delayMicroseconds(),
// called from flashWake()'s post-wake recovery margin. Confirmed by
// grepping the real source for HIGH/LOW/digitalWrite/millis/String/
// pinMode/analogRead — none appear outside of comments, so nothing else
// needs stubbing here. See firmware/test/store_host/mock_flash.h for the
// rest of this host-test shim's rationale.
//
// A real delay would only slow the test suite down for no benefit (the
// mock has no bus timing to respect), so this is a no-op — deliberately
// NOT trying to model the "actual current draw / wake-recovery delay"
// SENSE_FIRST_BOOT.md item 8 flags as bench-only VERIFY work.
//
// SPDX-License-Identifier: MIT

#pragma once

inline void delayMicroseconds(unsigned int) {}
