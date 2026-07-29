// jh_link.cpp — HOST implementation of the jh_link seam
// (firmware/include/platform/jh_link.h). See firmware/platformio.ini's
// env:host.
//
// A deliberate stub, per the host port's scope: this platform tests the
// REAL core's serial/command/detector/self-test/storage paths natively; it
// does not attempt to stand up a fake BLE stack. begin() reports failure
// (exactly like a real board with a dead radio — main.cpp's self-test
// already has a well-defined, non-fatal `ble FAIL` path for that, see
// runSelfTest()'s ble row), and poll()/pump()/write() are no-ops so the
// command loop's BLE calls cost nothing and never touch a second transport.
// takeGreetPending() stays false forever: nothing is ever subscribed.
//
// SPDX-License-Identifier: MIT

#include "platform/jh_link.h"

namespace jh_link {

bool begin(const char* /*name*/) { return false; }

void poll(void (*/*handle*/)(const String&)) {}

void pump() {}

void write(const char* /*data*/, size_t /*len*/) {}

bool takeGreetPending() { return false; }

}  // namespace jh_link
