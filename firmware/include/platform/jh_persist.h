// jh_persist.h — calibration-persistence platform seam (see docs/sense.md §3.9,
// which explicitly calls out §3.8: "NVS is ESP32-only").
//
// `set airtime_offset_s 0.0153` (and height_scale) persist here so
// calibration survives reboot AND reflash without a rebuild — a phone over
// BLE becomes a complete calibration tool. Compiled params.json values
// (params.gen.h) stay the defaults; persisted values override them when
// present. Exactly two keys exist: airtime_offset_s ("offset") and
// height_scale ("scale") — the same two, and only two, the `set` command and
// the CAL protocol line ever touch.
//
// Units: airtime_offset_s in seconds, height_scale unitless (a multiplier) —
// same units as jump_detector.h's Params fields of the same names.
// Error returns: load() always succeeds (falls back to the given compiled
// defaults for a key that was never saved); save()/clear() are fire-and-
// forget, matching today's Preferences calls (main.cpp already range-checks
// values before calling save(), so there is no failure path to report).
//
// ESP32: Preferences/NVS, namespace "jumpcal", keys "offset"/"scale"
// (src/platform/esp32/jh_persist.cpp). A future platform stores the same two
// keys in whatever survives reflash/DFU there (docs/sense.md §3.8 names
// InternalFS — a small file on internal-flash LittleFS — as the likely
// Sense equivalent); same survival story either way: lives through app
// updates and reflash/DFU, dies only with a full chip erase.
//
// SPDX-License-Identifier: MIT

#pragma once

namespace jh_persist {

// Bring up the calibration store. Call once from setup(), before load().
void init();

// Load persisted calibration, falling back to the given compiled defaults
// for whichever key was never saved. Returns true if EITHER value came from
// persisted storage (vs. both being compiled defaults) — main.cpp uses this
// for the CAL `source=device|defaults` field and the boot-time banner.
bool load(float default_offset_s, float default_scale,
          float& out_offset_s, float& out_scale);

// Persist one calibration value. `is_offset` selects airtime_offset_s
// (true) vs height_scale (false), mirroring the `set <key> <value>`
// command's own key selection.
void save(bool is_offset, float value);

// Revert one calibration value to its compiled default: removes it from
// persisted storage, so the next load() falls back to the default again.
void clear(bool is_offset);

}  // namespace jh_persist
