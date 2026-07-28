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

bool load(float default_offset_s, float default_scale,
          float& out_offset_s, float& out_scale) {
  out_offset_s = s_prefs.getFloat("offset", default_offset_s);
  out_scale    = s_prefs.getFloat("scale",  default_scale);
  return s_prefs.isKey("offset") || s_prefs.isKey("scale");
}

void save(bool is_offset, float value) {
  s_prefs.putFloat(is_offset ? "offset" : "scale", value);
}

void clear(bool is_offset) {
  s_prefs.remove(is_offset ? "offset" : "scale");
}

}  // namespace jh_persist
