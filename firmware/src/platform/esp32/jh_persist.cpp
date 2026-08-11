// jh_persist.cpp — ESP32 implementation of the jh_persist seam
// (firmware/include/platform/jh_persist.h). See docs/sense.md §3.8/§3.9.
//
// Preferences/NVS, namespace "jumpcal", keys "offset"/"scale" — moved out of
// main.cpp verbatim (same namespace name, same keys, same Preferences calls).
//
// SPDX-License-Identifier: MIT

#include "platform/jh_persist.h"

#include <Preferences.h>

namespace jh_persist {

static Preferences s_prefs;

void init() {
  s_prefs.begin("jumpcal", false);
}

namespace {
// NVS key names. "offset"/"scale" are unchanged so existing calibration on a
// FireBeetle keeps loading; "vbat" is simply absent on those units and falls
// back to its default of 1.0.
const char* keyName(Key k) {
  switch (k) {
    case Key::AirtimeOffsetS: return "offset";
    case Key::HeightScale:    return "scale";
    case Key::VbatScale:      return "vbat";
  }
  return "offset";
}
}  // namespace

float load(Key k, float def, bool* from_store) {
  const char* n = keyName(k);
  if (from_store) *from_store = s_prefs.isKey(n);
  return s_prefs.getFloat(n, def);
}

void save(Key k, float value) { s_prefs.putFloat(keyName(k), value); }

void clear(Key k) { s_prefs.remove(keyName(k)); }

}  // namespace jh_persist
