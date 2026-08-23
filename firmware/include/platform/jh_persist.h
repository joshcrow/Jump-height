// jh_persist.h — calibration-persistence platform seam (see docs/sense.md §3.9,
// which explicitly calls out §3.8: "NVS is ESP32-only").
//
// `set airtime_offset_s 0.0153` (and height_scale) persist here so
// calibration survives reboot AND reflash without a rebuild — a phone over
// BLE becomes a complete calibration tool. Compiled params.json values
// (params.gen.h) stay the defaults; persisted values override them when
// present. The keys are the `Key` enum below: airtime_offset_s ("offset"),
// height_scale ("scale"), and — since 2026-08-11 — vbat_scale ("vbat"), the
// per-unit battery-divider correction.
//
// Units: airtime_offset_s in seconds, height_scale unitless (a multiplier) —
// same units as jump_detector.h's Params fields of the same names.
// vbat_scale is unitless, multiplying jh_power::vbat_mv()'s reading.
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

// The calibration keys. This was a `bool is_offset` until 2026-08-11 — a
// two-keys-forever shape that could not express a third. It had to grow
// because vbat_scale is a genuinely PER-UNIT number (this board's divider
// resistors read ~1.8% low; another board's differ), so it cannot live in
// params.json without making every other unit wrong.
//
// docs/data-pipeline.md's "calibration record (per physical unit)" already
// names more of these coming — gyro bias, gyro scale-factor, the mount
// lever arm. An enum takes them; a boolean never could.
enum class Key { AirtimeOffsetS, HeightScale, VbatScale,
                 // Not calibration: the sensor-probe crash guard (jh_imu,
                 // nrf52). 1.0 = a previous boot died inside the first bus
                 // touch; boots skip the sensor (sticky) until a manual
                 // `selftest` retries. Lives here because internal flash is
                 // the one store proven to survive watchdog resets AND the
                 // bootloader (GPREGRET gets sanitized; .noinit does not
                 // exist in this core's linker scripts — both measured).
                 ProbeGuard,
                 // Same guard byte, bit 1: the QSPI store mount crash guard
                 // (2026-08-12 evening: the mule's wedged flash chip hung
                 // jh_store::init before BLE ever started — the storage twin
                 // of the sensor-probe lesson, caught by the same
                 // neuter-and-bisect method). `format` is the retry path.
                 StoreGuard,
                 // Same guard byte, bit 2: the trace region is WEDGED — a
                 // trace_clear() erase failed, so a sector's contents are
                 // indeterminate and a stale island may sit above whatever
                 // append point a scan derives (F-07, audit 2026-08-22).
                 //
                 // This must persist, and that is the whole point of putting
                 // it here: jh_store's in-RAM flag dies on the watchdog reset
                 // that a wedged flash chip makes LIKELY, and the next boot's
                 // mount scan re-derives an append point with exactly the same
                 // blindness that caused the corruption. A fix that evaporates
                 // on reboot is not a fix. Cleared only by `format`/`clear`,
                 // which erase the region outright and so re-establish
                 // known-good geometry.
                 TraceGuard };

// Bring up the calibration store. Call once from setup(), before load().
void init();

// Load one persisted value, falling back to `def` if that key was never
// saved. Sets *from_store (when non-null) to whether the value came from
// storage rather than the default — main.cpp ORs these for the CAL line's
// `source=device|defaults` field and the boot banner.
float load(Key k, float def, bool* from_store = nullptr);

// Persist one calibration value, mirroring `set <key> <value>`.
// Returns true only if the value is durably stored.
//
// This was `void` with a "fire-and-forget ... no failure path to report"
// contract until F-10's sibling sweep (audit 2026-08-22). That was defensible
// while the only keys were calibration numbers a human had just typed and
// could retype. It stopped being defensible when the guard bits moved in:
// ProbeGuard, StoreGuard and TraceGuard are SAFETY flags whose whole job is
// to survive a reset, and a silent failure to store one re-arms exactly the
// crash or corruption the guard exists to prevent. writeRecord() had three
// swallowed failure paths (open, short write, rename).
bool save(Key k, float value);

// Revert one value to its compiled default: removes it from persisted
// storage, so the next load() falls back to the default again.
bool clear(Key k);

}  // namespace jh_persist
