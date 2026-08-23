// Jump Height — chip-agnostic firmware core (see docs/sense.md §3.9: shared
// core + platform/<chip> seams — jh_imu/jh_store/jh_link/jh_persist). Built
// today for the FireBeetle 2 ESP32-E field board (platform/esp32); a second
// platform is a build-time swap of the four seam implementations, no changes
// to this file (except platformio.ini).
//
// What it does (see DECISIONS.md / BUILD.md):
//   * Samples the IMU at JH_SAMPLE_HZ (raw register driver, tolerant of
//     clone chips — see the jh_imu seam) and runs the airtime jump detector
//     live.
//   * Motion gate: only detects/logs while the board is actually moving.
//   * Logs to on-device storage (the jh_store seam):
//       jumps.csv — one line per jump (n,takeoff_s,airtime_raw_s,airtime_s,
//                    height_m,med_a_g,med_w_dps,med_acorr_g,n_air)
//       trace.csv — JH_LOG_HZ "t,mag" trace while moving, for offline re-tuning
//   * Power-on self-test with plain-English fix hints; a wiring failure does
//     NOT brick the session — fix the wires and type `selftest` to recover.
//
// Protocol (115200 baud on USB serial) — designed for the ./tools/jump CLI but
// human readable. Lines starting with `#` are chatter. Machine lines:
//   SELFTEST BEGIN / SELFTEST <name> PASS|WARN|FAIL|SKIP detail=<v> / SELFTEST END result=...
//   READY                      — boot complete
//   STATE recording|idle       — motion gate transitions
//   JUMP n=.. airtime_raw_s=.. airtime_s=.. height_m=.. height_ft=.. best_m=..
//   STATS session_jumps=.. session_best_m=.. session_best_airtime_s=.. stored_jumps=.. stored_best_m=.. trace_bytes=..
//   INFO fw=.. sample_hz=.. log_hz=.. ble=1 / PARAMS <key=value ...>
//   FILE <name> BEGIN ... FILE <name> END
//   OK <cmd> | ERR <detail>    — every typed command finishes with one of these
// Commands: help stats jumps trace dump clear selftest info
//
// BLE (added in v0.3.0): the SAME protocol is mirrored over a Nordic UART
// Service so a phone/laptop can read jumps and send commands wirelessly. Since
// v0.4.2, TWO BLE centrals can be connected and subscribed at once (e.g. a
// Garmin watch on the rider + a phone on the beach, per
// docs/garmin-datafield.md §7) — every line above goes out on BOTH USB serial
// and (when at least one BLE client is subscribed) the BLE TX characteristic,
// via the emit layer below; a single notify() reaches every subscribed client,
// so this file never has to loop over connections itself. The BLE stack lives
// behind the jh_link seam (include/platform/jh_link.h; ESP32 implementation
// in src/platform/esp32/jh_link.cpp). A BLE failure is reported by the
// self-test's `ble` row but never blocks jump detection — v1 still reads over
// USB.
//
// All tunables come from config/params.json via the generated params.gen.h.
//
// SPDX-License-Identifier: MIT

#include <Arduino.h>
#include <stdarg.h>
#include <string.h>
#include "params.gen.h"
#include "build.gen.h"
#include "gyro_bias.h"
#include "jump_detector.h"
#include "lever_arm.h"
#include "platform/jh_clock.h"
#include "platform/jh_imu.h"
#include "platform/jh_link.h"
#include "platform/jh_persist.h"
#include "platform/jh_power.h"
#include "platform/jh_store.h"

// FW_VERSION is a human-facing product version and is NOT the build identity:
// it has read "0.4.3" through every fix this project has shipped, including
// the four-day drive-strength crisis. The identity is JH_BUILD_SRC (build.gen.h)
// — a hash of the firmware sources — which is what `src=` on INFO reports and
// what makes the freeze protocol checkable. See tools/gen_build.py.
#define FW_VERSION "0.4.3"

// Fast charge: 100 mA instead of 50 mA while USB is charging (0.4C on the
// 250 mAh cell, against a 0.5C industry-standard rate). Set to 0 to revert
// to the charger's default. See jh_power.cpp's PIN_HICHG comment.
// (JH_FAST_CHARGE_ENABLED now lives in platform/jh_power.h — the consumer's
// own header — after the cross-TU define here left the feature compiled out
// of jh_power.cpp entirely. See the header's comment for the full story.)

// Self-arming spin correction: OFF for the one-shot water session so the data
// comes from the detector that was actually validated. See the commit site.
#ifndef JH_SPIN_SELFARM_ENABLED
#define JH_SPIN_SELFARM_ENABLED 0
#endif

static const float    G                  = JH_G;
static const uint32_t SAMPLE_INTERVAL_US = 1000000UL / JH_SAMPLE_HZ;
static const int      LOG_DECIMATE       = JH_SAMPLE_HZ / JH_LOG_HZ;
static const uint32_t IDLE_TIMEOUT_MS    = (uint32_t)JH_IDLE_TIMEOUT_S * 1000UL;
// Session-boundary threshold for the storage-lifecycle auto-clear. Chosen to
// sit well above any in-session pause (sitting on the board, a beach break)
// and well below the gap between outings. Only ever consulted when the trace
// is already full — see the rule in loop().
static const uint32_t AUTO_CLEAR_IDLE_MS = 3600UL * 1000UL;  // 1 h

jump::Detector detector;
jump::GyroBias gyro_bias;
jump::LeverArm lever_arm;

// Sensor-read failure counters. Both reads on the sample hot path degrade
// SILENTLY by design — a failed accel read skips the sample, a failed gyro
// read falls back to the accel-only detector path — and silence is the right
// behaviour for the transient I2C hiccup they were written for.
//
// It is the WRONG behaviour for a sensor that has actually died. An IMU that
// stops answering mid-session records nothing and says nothing; a gyro that
// stops answering quietly reverts to a detector that reads spun jumps low.
// Either would void an expensive water session invisibly, and the loss would
// only surface later as "the algorithm seems wrong" rather than "the sensor
// was down". Count them, and put the count where the session data is.
uint32_t accel_fail_count = 0;
uint32_t gyro_fail_count  = 0;
bool     sensor_warned    = false;
// Latched once at boot: does this board have a gyro at all? Without it, the
// v1 boards' deliberate "no gyro" (esp32/jh_imu.cpp returns false always)
// would be counted as a fault on every sample — a capability difference
// dressed up as a failure, which is the very thing these counters exist to
// stop.
bool     gyro_present     = false;

static bool sensor_ok = false;
static bool fs_ok     = false;
// Auto-remount state (see the rule in loop()). store_guard_was_latched
// records what setup() found: a latched guard means the previous mount
// attempt never returned, which is exactly the case that must NOT be retried
// automatically.
static bool     store_guard_was_latched = false;
static uint8_t  remount_tries           = 0;
static const uint8_t kRemountMaxTries   = 3;
// A real no-op rather than nullptr: try_mount now null-guards, but passing a
// function keeps this call site correct regardless of what the seam does.
static void quietAnnounce(const char*) {}
static bool ble_ok    = false;  // BLE stack came up; reported by the self-test

// Session stats (since this power-up) + stored stats (across power-ups)
static uint32_t session_jumps = 0;
static float    session_best  = 0.0f;
static float    session_best_airtime = 0.0f;  // best airtime this session — on
                                              // STATS so the watch can reseed it
                                              // after a dropout (FIT parse of
                                              // 2026-08-18 found best_jump
                                              // reconciled but best_airtime
                                              // stuck at the live-seen max)
static uint32_t stored_jumps  = 0;
static float    stored_best   = 0.0f;
// 64-bit microsecond timebase: 32-bit micros() wraps at ~71.6 min, which is
// shorter than a wing session and would reset t mid-file (and could eat a
// jump in flight at the wrap instant). jh_clock::micros64() never wraps.
static int64_t  t0_us         = 0;

// Motion gate. motion_seen keeps the gate idle from power-on until the first
// real over-threshold sample — without it, (now_ms - 0) < timeout reads as
// "active" at boot and the desktest shake step can never see a transition.
static uint32_t last_motion_ms = 0;
static bool     motion_seen    = false;
static bool     active         = false;

// Gravity baseline, measured by the boot self-test with the device at rest.
// Cheap sensors can be mis-scaled (a real field unit reads 0.824 g sitting
// still — genuine 0x68 chip, superbly quiet, just 18% low). Every sample is
// divided by this, so "1.0" always means "this sensor's own resting gravity":
// the motion gate can settle to idle, free-fall still reads ~0, and heights
// are untouched (the airtime method measures time, not amplitude). Updated
// only when a self-test sees a plausible, quiet resting measurement.
static float    g_baseline     = 1.0f;

// Trace buffering (keeps slow flash writes off the sampling path). Byte
// count, cap-full state, and the CSV headers now live behind jh_store.
static String   trace_buf;
static int      decimate_ctr  = 0;
static uint32_t last_flush_ms = 0;

// Non-blocking serial command assembly
static String cmd_buf;

// ---------------- Protocol output (emit layer) ----------------
// Single choke point for ALL protocol output. Every line goes to USB serial and,
// when any BLE client is subscribed, to the Nordic UART TX characteristic — same
// bytes on both transports, and the same bytes reach every subscribed BLE
// client (jh_link::write()/notify() broadcast; see jh_link.h). Chatter (`#`),
// hints, machine lines, and FILE dumps all pass through here. Nothing else in
// this file should call Serial.print* directly. Only ever called from
// loop()/setup() (never a link-implementation callback), so the BLE notify
// path has no cross-task contention (see jh_link.h).
// ---- reliable-export mode ------------------------------------------------
//
// MEASURED FAILURE, 2026-08-14: downloading the SAME stored trace twice gave
// two different files — one lost 0.58 s of samples mid-file, the other 0.42 s
// at a different place, and both truncated the final line. Cause: the drop
// above. `Serial.availableForWrite()` is momentarily short during a long
// export (the host drains in bursts), and a dropped 240-byte block is a
// silently missing slice of the session with nothing in the output to mark it.
//
// Dropping is the right policy for CHATTER — a wedged terminal must never
// cost the device. It is the wrong policy for a FILE, where the whole point
// is to get every byte off the puck exactly once. So file exports set
// s_serial_must_not_drop and we WAIT for room instead, bounded, feeding the
// watchdog. If the host is genuinely gone we give up, count the bytes, and
// the caller reports the export as incomplete — loudly, never silently.
static bool     s_serial_must_not_drop  = false;
static uint32_t s_serial_dropped_bytes  = 0;

// Bounded wait for CDC buffer room. Returns false if the host never drains.
static bool waitForSerialRoom(size_t len) {
  const uint32_t deadline_ms = millis() + 2000;  // generous; host bursts are ms
  while ((size_t)Serial.availableForWrite() < len) {
    if ((int32_t)(millis() - deadline_ms) >= 0) return false;
    jh_link::watchdog_feed();
    delay(1);
  }
  return true;
}

static void emitBytes(const char* data, size_t len) {
  // BOUNDED serial write. The core's CDC write spins forever when a host
  // holds DTR but stops draining (Adafruit_USBD_CDC.cpp write loop) — a
  // wedged serial monitor on the Mac was enough to starve loop(), trip the
  // watchdog, and (before watchdog_init moved first) strand the reboot in
  // the unprotected window. Debug output is NEVER worth the device: if the
  // host isn't draining, drop the serial copy and move on — BLE still
  // carries every line, and a stalled bench terminal was not reading
  // anyway.
  if ((size_t)Serial.availableForWrite() >= len) {
    Serial.write((const uint8_t*)data, len);
  } else if (s_serial_must_not_drop) {
    // FILE EXPORT: waiting beats dropping. See emitBytesReliable's comment.
    if (!waitForSerialRoom(len)) s_serial_dropped_bytes += len;
    else Serial.write((const uint8_t*)data, len);
  }
  jh_link::write(data, len);
}
static void emit(const char* s)     { emitBytes(s, strlen(s)); }
static void emitLine(const char* s) { emit(s); emitBytes("\n", 1); }  // like println
static void emitf(const char* fmt, ...) {                             // like printf
  char buf[256];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof(buf), fmt, ap);
  va_end(ap);
  if (n < 0) return;
  if (n > (int)sizeof(buf) - 1) n = (int)sizeof(buf) - 1;  // truncated: emit what fit
  emitBytes(buf, (size_t)n);
}

// A newly-subscribed BLE client gets the banner + READY so it knows the link is
// live. BLE-only on purpose (not through emit): it's a per-subscribe-event
// greeting, and re-emitting READY onto USB could confuse a serial session
// mid-command. Note for the two-central case: jh_link's notify() broadcasts
// to every currently-subscribed connection (see jh_link.h), so if client A is
// already live when client B subscribes, this second greet reaches BOTH — A
// sees an extra "# JumpHeight..."/READY mid-stream. Both lines are designed to
// be tolerated by a spec-compliant reader (`#` is chatter; docs/
// garmin-datafield.md §5.2 requires clients to skip unknown lines/keys), so
// this is treated as a harmless, acceptable side effect rather than a bug.
static void bleGreet() {
  static const char banner[] = "# JumpHeight fw v" FW_VERSION " src=" JH_BUILD_SRC "\n";
  jh_link::write(banner, sizeof(banner) - 1);
  jh_link::write("READY\n", 6);
}

// ---------------- Runtime calibration (jh_persist) ----------------
// `set airtime_offset_s 0.0153` persists via the jh_persist seam (ESP32:
// flash-backed NVS): it survives reboot AND reflash, so calibration never
// requires a rebuild. That makes a phone over BLE a complete calibration
// tool (and is the prerequisite for a future potted, cable-less build).
// Compiled params.json values stay the defaults; persisted values override
// them when present.
static bool cal_from_nvs = false;
// Per-key provenance (2026-08-21). The OR above hides a single key falling
// back to its compiled default while the others survive — which is exactly
// how the OG's lost AirtimeOffsetS could have hidden had VbatScale survived.
// The device REPORTS per key; the pass/fail POLICY lives host-side, because
// only the host knows whether this board is SUPPOSED to be calibrated (the
// registry does; a wiped device cannot distinguish "never calibrated" from
// "lost calibration" — any marker dies with the same storage).
static bool cal_src_off   = false;
static bool cal_src_scale = false;
static bool cal_src_vbat  = false;

static float cal_vbat_scale = 1.0f;

static void loadCalibration() {
  bool a = false, b = false, c = false;
  const float off   = jh_persist::load(jh_persist::Key::AirtimeOffsetS,
                                       JH_AIRTIME_OFFSET_S, &a);
  const float scale = jh_persist::load(jh_persist::Key::HeightScale,
                                       JH_HEIGHT_SCALE, &b);
  // vbat_scale's default is 1.0 — the identity — not a params.gen.h value.
  // It is a property of THIS board's divider resistors, so there is no
  // sensible compiled default other than "assume nominal".
  cal_vbat_scale = jh_persist::load(jh_persist::Key::VbatScale, 1.0f, &c);

  cal_from_nvs = a || b || c;
  cal_src_off = a; cal_src_scale = b; cal_src_vbat = c;
  detector.set_calibration(off, scale);
  jh_power::set_vbat_scale(cal_vbat_scale);
  if (cal_from_nvs) {
    emitf("# calibration from device memory: airtime_offset_s=%.4f "
          "height_scale=%.3f vbat_scale=%.4f\n", off, scale, cal_vbat_scale);
  }
}

// ---------------- Storage ----------------
static void flushTrace() {
  if (!fs_ok || trace_buf.length() == 0) return;
  // jh_store accounts bytes and enforces the cap internally so every flush
  // path (loop, idle transition, serial commands) counts — not just the
  // loop's; it returns true exactly on the append that newly crosses the cap.
  if (jh_store::trace_append(trace_buf.c_str(), trace_buf.length())) {
    emitLine("# trace log full — still counting jumps. `dump` then `clear` to reset.");
  }
  trace_buf = "";
}


// ---- flight physics accumulator -------------------------------------------
//
// THE MEASUREMENT THE PROJECT EXISTS TO MAKE. The thesis is that a wing jump
// is ballistic: once airborne, specific force is ~0 (predicted band 0-0.07 g).
// The 50 Hz trace cannot test it — it stores |a| only, and |a| carries the
// board's own rotation as omega^2*r, which for ordinary board pitch is several
// times the width of the band under test. The gyro is read every sample and
// was being discarded.
//
// So sample the airborne window at the FULL 200 Hz and keep three medians per
// jump: raw |a|, |omega|, and |a| with the rotation term removed. Medians, not
// means, because takeoff and landing edges are violent outliers.
//
// Capacity 256 samples = 1.28 s. A longer flight is truncated to its first
// 1.28 s and n_air records how many samples the medians actually used, so a
// truncated window can never be mistaken for a short flight.
static const uint16_t kAirCap = 256;
static uint16_t s_air_a_mg[kAirCap];
static uint16_t s_air_w_dps[kAirCap];
static uint16_t s_air_n = 0;

static void airReset() { s_air_n = 0; }

static void airObserve(float mag_g, float omega_dps) {
  if (s_air_n >= kAirCap) return;
  const float a = mag_g * 1000.0f;
  const float w = omega_dps;
  s_air_a_mg[s_air_n]  = (uint16_t)(a < 0 ? 0 : (a > 65535.0f ? 65535.0f : a));
  s_air_w_dps[s_air_n] = (uint16_t)(w < 0 ? 0 : (w > 65535.0f ? 65535.0f : w));
  s_air_n++;
}

// Median by insertion sort on a copy. n <= 256 and this runs ONCE per jump,
// inside the landing settle, so the cost is invisible; the sample loop is
// never asked to do it.
static uint16_t medianOf(const uint16_t* src, uint16_t n) {
  if (n == 0) return 0;
  static uint16_t tmp[kAirCap];
  for (uint16_t i = 0; i < n; ++i) tmp[i] = src[i];
  for (uint16_t i = 1; i < n; ++i) {
    const uint16_t v = tmp[i];
    int16_t j = (int16_t)i - 1;
    while (j >= 0 && tmp[j] > v) { tmp[j + 1] = tmp[j]; --j; }
    tmp[j + 1] = v;
  }
  return tmp[n / 2];
}

// One line per storage outage, not one per jump: a rider mid-session with a
// full region would otherwise get the same sentence after every single jump,
// which is how a real warning becomes noise the eye skips. Cleared by
// scanStoredJumps(), so a `mount`/`format`/`clear` re-arms it.
static bool store_refusal_reported = false;

// Returns true only if the record actually reached flash (F-10). The caller
// counts on that: stored_jumps used to increment whether or not the store
// kept anything.
static bool logJump(const jump::JumpEvent& ev, uint16_t med_a_mg,
                    uint16_t med_w_dps, uint16_t med_acorr_mg, uint16_t n_air) {
  if (!fs_ok) return false;
  // stored_jumps + 1 because the increment now happens only on success, and
  // the record's `n` field should still be its 1-based index in the file.
  const jh_store::AppendResult r =
      jh_store::jumps_append(stored_jumps + 1, ev.takeoff_time_s,
                             ev.airtime_raw_s, ev.airtime_s, ev.height_m,
                             med_a_mg, med_w_dps, med_acorr_mg, n_air);
  if (r == jh_store::AppendResult::OK) return true;
  if (!store_refusal_reported) {
    store_refusal_reported = true;
    switch (r) {
      case jh_store::AppendResult::REGION_FULL:
        emitLine("# jump NOT saved: storage region full. The ride still counts "
                 "on your watch; `dump` then `clear` to make room.");
        break;
      case jh_store::AppendResult::WRITE_FAILED:
        emitLine("# jump NOT saved: flash write failed. Watch totals are "
                 "unaffected; `dump` what is there before `format`.");
        break;
      case jh_store::AppendResult::FS_DOWN:
        emitLine("# jump NOT saved: storage down. `mount` retries, `format` "
                 "rebuilds (DESTROYS data).");
        break;
      case jh_store::AppendResult::OK:
        break;  // unreachable; kept so a new enum value fails the build here
    }
  }
  return false;
}

static void printFileFramed(jh_store::StoredFile which, const char* name) {
  s_serial_must_not_drop = true;          // a FILE must arrive whole
  s_serial_dropped_bytes = 0;
  emitf("FILE %s BEGIN\n", name);
  if (jh_store::open_read(which)) {
    // Read in blocks (not byte-by-byte): far fewer BLE notifications, and the
    // emit layer chunks each block to the MTU. Notify back-pressure/pacing is
    // handled inside jh_link::write, so a long BLE dump self-throttles.
    uint8_t block[240];
    size_t n;
    uint32_t blocks = 0;
    while ((n = jh_store::read_chunk(block, sizeof(block))) > 0) {
      emitBytes((const char*)block, n);
      // Feed the watchdog every few blocks (night-review finding #7): on a
      // serial-only session emitBytes DROPS when the CDC buffer is short
      // rather than blocking, so a full-trace export (~2 MB decode) is pure
      // CPU — long enough to starve the 3.5 s watchdog with no feed. BLE
      // sessions were already fed inside jh_link::write's drain branch;
      // this covers every path.
      if ((++blocks & 15) == 0) jh_link::watchdog_feed();
    }
    jh_store::close_read();
  }
  // Report truth about completeness. A silent short file is the failure this
  // whole mode exists to prevent, so say so in-band where every client
  // (CLI, web, watch) already parses lines.
  if (s_serial_dropped_bytes) {
    emitf("# WARNING %s INCOMPLETE — %lu bytes never reached the host; re-run the download\n",
          name, (unsigned long)s_serial_dropped_bytes);
  }
  s_serial_must_not_drop = false;
  emitf("FILE %s END\n", name);
}

static void scanStoredJumps() {
  stored_jumps = 0;
  stored_best  = 0.0f;
  // Re-arm the storage-refusal warning: every caller of this function has
  // just mounted, formatted or cleared, so the next refusal is NEW news and
  // the rider should hear it (F-10).
  store_refusal_reported = false;
  if (!fs_ok) return;
  jh_store::jumps_scan(stored_jumps, stored_best);
}

// ---------------- Self-test ----------------
// Prints machine-readable results plus plain-English hints, and (re)initializes
// the sensor. Safe to run repeatedly via the `selftest` command.
static bool runSelfTest() {
  emitLine("SELFTEST BEGIN");
  bool all_ok = true;

  // 1. Is anything answering on the I2C bus?
  uint8_t addr = 0;
  if (jh_imu::probe(jh_imu::ADDR_PRIMARY))        addr = jh_imu::ADDR_PRIMARY;
  else if (jh_imu::probe(jh_imu::ADDR_SECONDARY)) addr = jh_imu::ADDR_SECONDARY;

  bool imu_up = false;
  if (addr == 0) {
    emitLine("SELFTEST i2c FAIL detail=no_device");
    emitLine("# hint: no sensor found. Check the 4 wires: sensor VCC->3V3 (NOT the");
    emitLine("# hint: pin marked VCC — it carries ~4.7V), GND->GND, SDA->SDA,");
    emitLine("# hint: SCL->SCL. Swapped SDA/SCL is the #1 cause; loose jumper is #2.");
    all_ok = false;
  } else {
    emitf("SELFTEST i2c PASS detail=0x%02X\n", addr);
    imu_up = jh_imu::begin(addr);
    if (!imu_up) {
      emitLine("SELFTEST config FAIL detail=write_error");
      emitLine("# hint: device answers but register writes fail — usually a flaky");
      emitLine("# hint: wire or bad solder joint. Re-check connections.");
      all_ok = false;
    } else {
      const uint8_t who = jh_imu::who_am_i();
      // 0x68 = MPU-6050 (v1 ESP32 boards). 0x6A = LSM6DS3TR-C (the Sense).
      // BOTH are correct silicon. 0x6A was reported as "likely a clone
      // MPU-6050" for weeks on the very board it is the RIGHT answer for —
      // a self-test row that cries wolf about healthy hardware is exactly
      // how this project talked itself into two dead-hardware verdicts.
      if (who == 0x68 || who == 0x6A) {
        emitf("SELFTEST whoami PASS detail=0x%02X\n", who);
      } else {
        // Clone chips report odd IDs but usually work fine — warn, don't fail.
        emitf("SELFTEST whoami WARN detail=0x%02X\n", who);
        emitLine("# hint: unexpected chip ID — likely a clone. Usually fine;");
        emitLine("# hint: the accel/noise checks below are what actually matter.");
      }
    }
  }

  // 2. Does the accelerometer read ~1 g sitting still?
  if (imu_up) {
    // Wait out the accelerometer's FIRST conversion before sampling stats.
    // begin() wrote CTRL1_XL (power-down exit) moments ago; with BDU set,
    // the output registers hold their reset 0x0000 until the first
    // conversion lands (>= 1/ODR plus turn-on). One all-zero triple folded
    // into N=100 otherwise-clean samples puts sd at ~0.0995 — past the
    // 0.08 FAIL line — so a HEALTHY cold-booted sensor printed "noise
    // FAIL" deterministically (audit 2026-08-13; the archive's odd boot
    // rows 0.960 g/0.0966 are exactly the on-command 0.970 g plus one zero
    // sample). An exact 0/0/0 triple is unphysical for live silicon at
    // rest (gravity), so first-non-zero is a safe readiness gate; the loop
    // is bounded so a genuinely zero-stuck part still reaches the stats
    // below and fails there honestly.
    for (int i = 0; i < 30; ++i) {
      jh_link::watchdog_feed();  // sampling loops ran feed-less inside one
                                 // handler pass; on a degraded bus every read
                                 // maxes its bound and the sum can cross the
                                 // 3.5 s window (2026-08-16 reboots, cause
                                 // still open — this removes one candidate)
      float ax, ay, az;
      if (jh_imu::read_accel_g(ax, ay, az) &&
          (ax != 0.0f || ay != 0.0f || az != 0.0f)) break;
      delay(5);
    }
    float sum = 0, sumsq = 0;
    int   good = 0;
    const int N = 100;
    for (int i = 0; i < N; ++i) {
      jh_link::watchdog_feed();
      float ax, ay, az;
      if (jh_imu::read_accel_g(ax, ay, az)) {
        float m = sqrtf(ax * ax + ay * ay + az * az);
        sum += m; sumsq += m * m; good++;
      }
      delay(5);
    }
    if (good < N / 2) {
      emitLine("SELFTEST accel FAIL detail=read_errors");
      emitLine("# hint: reads are failing intermittently — flaky wiring.");
      all_ok = false;
    } else {
      const float mean = sum / good;
      const float var  = sumsq / good - mean * mean;
      const float sd   = var > 0 ? sqrtf(var) : 0;
      if (mean > 0.8f && mean < 1.2f) {
        emitf("SELFTEST accel PASS detail=%.3fg\n", mean);
      } else if (mean > 0.5f && mean < 1.5f) {
        // A real field unit read 0.824 g: mis-scaled, not broken. The gravity
        // baseline normalizes it, and heights come from airtime, not
        // amplitude — so this is a warning, never a dead end.
        emitf("SELFTEST accel WARN detail=%.3fg\n", mean);
        emitLine("# hint: gravity reads off-scale on this unit — auto-normalized;");
        emitLine("# hint: detection and heights are unaffected.");
      } else {
        emitf("SELFTEST accel FAIL detail=%.3fg\n", mean);
        emitLine("# hint: should read ~1.0g sitting still. Keep the device still on a");
        emitLine("# hint: table during self-test, and check VCC is on 3V3.");
        all_ok = false;
      }
      // Adopt this rest measurement as the gravity baseline when it's sane
      // (plausible magnitude, quiet enough to really be "at rest").
      if (mean > 0.5f && mean < 1.5f && sd < 0.08f) {
        g_baseline = mean;
        if (fabsf(mean - 1.0f) > 0.05f) {
          emitf("# gravity reads %.3fg at rest on this unit — auto-normalizing "
                "so motion math sees 1.000g\n", mean);
        }
      }
      if (sd < 0.03f) {
        emitf("SELFTEST noise PASS detail=%.4fg\n", sd);
      } else if (sd < 0.08f) {
        emitf("SELFTEST noise WARN detail=%.4fg\n", sd);
        emitLine("# hint: noisier than expected — vibration or a marginal clone.");
        emitLine("# hint: OK to proceed; watch for false jumps in the desk test.");
      } else {
        emitf("SELFTEST noise FAIL detail=%.4fg\n", sd);
        emitLine("# hint: far too noisy. Was the device moving? Re-run `selftest`");
        emitLine("# hint: with it resting on a table. If still failing, try another");
        emitLine("# hint: MPU board (you bought spares for exactly this).");
        all_ok = false;
      }
    }
  } else {
    emitLine("SELFTEST accel SKIP detail=no_sensor");
    emitLine("SELFTEST noise SKIP detail=no_sensor");
  }

  // 3. BLE link (v0.3.0). Reported honestly, but a BLE failure does NOT flip the
  // aggregate to FAIL: BLE is optional (v1 reads over USB), and the self-test's
  // result gates "is this device fit to track jumps?". Marking the whole test
  // FAIL over an optional radio would dead-end the wizard on a perfectly good
  // jump tracker — exactly the blocking the contract says must not happen.
  if (ble_ok) {
    emitLine("SELFTEST ble PASS detail=advertising");
  } else {
    emitLine("SELFTEST ble FAIL detail=init_error");
    emitLine("# hint: Bluetooth didn't start. Jump detection and the USB console");
    emitLine("# hint: still work fully — you can flash, test, and download over USB.");
    emitLine("# hint: Re-flash to retry; if it keeps failing the radio may be faulty.");
  }

  // 4. Storage.
  if (fs_ok) {
    emitf("SELFTEST flash PASS detail=%uB_free\n", (unsigned)jh_store::free_bytes());
  } else {
    emitLine("SELFTEST flash FAIL detail=mount_failed");
    emitLine("# hint: flash storage didn't mount; jumps will print live but won't be");
    emitLine("# hint: saved. Re-flash with `./tools/jump flash` (it formats storage).");
    all_ok = false;
  }

  // Sampling only needs a working IMU: a flash or accel-range failure still
  // leaves the device usable for live detection, and `selftest` can re-probe.
  sensor_ok = imu_up;
  emitf("SELFTEST END result=%s\n", all_ok ? "PASS" : "FAIL");
  return all_ok;
}

// ---------------- Commands ----------------
static void printHelp() {
  emitLine("# commands: help | stats | jumps | trace | tracecheck | dump | clear | selftest | revive | i2cdiag | dcdc | info | off | dfu | uf2 | fakejump | mount | format");
  emitLine("#           set <airtime_offset_s|height_scale|vbat_scale> <value|default>");
  emitLine("#           vbatscan  (bench: battery ADC vs acquisition time)");
  emitLine("#           gyro      (bench: raw + bias-corrected rate, 2 s)");
}

// Handles one command line from EITHER transport (serial pollSerial() or BLE
// jh_link::poll()). Both run on the loop() task, one at a time, so a command is
// processed exactly once; its output goes to both transports via the emit layer.
static void handleCommand(const String& cmd) {
  if (cmd == "help") {
    printHelp();
    emitLine("OK help");
  } else if (cmd == "stats") {
    flushTrace();  // so trace_bytes matches what a `dump` would actually deliver
    // Storage down is NOT the same answer as "you have no jumps", and this
    // line used to report them identically: jumps_scan() returns 0 when
    // s_fs_ok is false, so an unmounted device says stored_jumps=0
    // trace_bytes=0 — a stored session and a lost one look the same.
    //
    // Found the expensive way (2026-08-11): read too soon after a flash,
    // saw the zeros, and concluded the flash had wiped the board. It had
    // not — nine jumps were sitting there the whole time. A reading that
    // could not be taken must never be dressed up as a reading of zero.
    //
    // Emitted only when down, so the normal line is byte-identical for
    // every existing client (the same "adder key" rule the battery keys
    // follow), and the abnormal case is impossible to miss.
    if (!fs_ok) {
      emitLine("# storage NOT MOUNTED — the counts below are unknown, not zero");
    }
    // Battery keys are APPENDED, and only on platforms that can measure
    // (jh_power seam; docs/sense.md §3.4/§4 "adder" rule): absent keys mean
    // an unsupported platform, and clients that predate the keys skip them
    // — either direction, nothing else on this line may move.
    const int vbat = jh_power::vbat_mv();
    const char* fs_key = fs_ok ? "" : " fs=down";
    // Adder keys, present only when non-zero, so a healthy line is unchanged
    // for every existing client — and a session that quietly lost a sensor
    // cannot be mistaken for a session that simply had no jumps.
    char fail_key[48] = "";
    if (accel_fail_count || gyro_fail_count) {
      snprintf(fail_key, sizeof(fail_key), " accel_fail=%lu gyro_fail=%lu",
               (unsigned long)accel_fail_count, (unsigned long)gyro_fail_count);
    }
    // Link drops: appended only when non-zero, so a healthy session's STATS
    // line is unchanged and existing parsers keep working. Non-zero means a
    // client's LIVE view lost bytes; the RECORDED session is unaffected
    // (jh_store writes independently of the radio).
    // UPTIME — the anchor that makes every recorded timestamp convertible to
    // wall clock. Trace time is seconds since boot and the puck has no RTC,
    // so on its own a trace can never be aligned to video, to a written log,
    // or to anything else that happened in the real world. Report uptime and
    // the host can compute, once: wall_clock_of_trace_zero = now - uptime.
    // After that every sample and every jump has a real timestamp. This is
    // the cheapest fix available for both the kayak-video alignment problem
    // and for labelling ordinary bench sessions.
    char up_key[40];
    snprintf(up_key, sizeof(up_key), " uptime_s=%.3f",
             (double)(jh_clock::micros64() - t0_us) * 1e-6);
    char drop_key[32] = "";
    if (jh_link::tx_drops() > 0) {
      snprintf(drop_key, sizeof(drop_key), " tx_drops=%lu",
               (unsigned long)jh_link::tx_drops());
    }
    if (vbat >= 0) {
      emitf("STATS session_jumps=%lu session_best_m=%.3f session_best_airtime_s=%.3f stored_jumps=%lu stored_best_m=%.3f trace_bytes=%lu vbat_mv=%d batt_pct=%d chg=%d%s%s%s%s\n",
            (unsigned long)session_jumps, session_best, session_best_airtime,
            (unsigned long)stored_jumps, stored_best, (unsigned long)jh_store::trace_bytes(),
            vbat, jh_power::batt_pct(), jh_power::charging(), fs_key, fail_key, drop_key, up_key);
    } else {
      emitf("STATS session_jumps=%lu session_best_m=%.3f session_best_airtime_s=%.3f stored_jumps=%lu stored_best_m=%.3f trace_bytes=%lu%s%s%s%s\n",
            (unsigned long)session_jumps, session_best, session_best_airtime,
            (unsigned long)stored_jumps, stored_best, (unsigned long)jh_store::trace_bytes(),
            fs_key, fail_key, drop_key, up_key);
    }
    emitLine("OK stats");
  } else if (cmd == "jumps") {
    flushTrace();
    printFileFramed(jh_store::StoredFile::JUMPS, "jumps.csv");
    emitLine("OK jumps");
  } else if (cmd == "trace") {
    flushTrace();
    printFileFramed(jh_store::StoredFile::TRACE, "trace.csv");
    emitLine("OK trace");
  } else if (cmd == "tracecheck") {
    // F-08's cross-check. The mount-time byte counter computes each sample's
    // CSV length arithmetically instead of snprintf-ing it; this re-walks the
    // whole region measuring the old, slow way and compares. Deliberately a
    // command and not a boot step: it costs a full region read (~200 ms on a
    // full chip in the harness, longer here), which is the exact cost F-08
    // removed from every boot.
    flushTrace();
    const uint32_t fast = jh_store::trace_bytes();
    const uint32_t slow = jh_store::trace_bytes_recomputed();
    emitf("# tracecheck fast=%lu slow=%lu %s\n", (unsigned long)fast,
          (unsigned long)slow,
          fast == slow ? "agree"
                       : "DISAGREE — the slow number is the correct one");
    emitLine(fast == slow ? "OK tracecheck" : "ERR tracecheck mismatch");
  } else if (cmd == "dump") {
    flushTrace();
    printFileFramed(jh_store::StoredFile::JUMPS, "jumps.csv");
    printFileFramed(jh_store::StoredFile::TRACE, "trace.csv");
    emitLine("OK dump");
  } else if (cmd == "clear") {
    jh_store::clear();
    if (jh_store::ok()) {   // a fully-succeeded clear re-erased the trace region
      jh_store::set_trace_wedged(false);
      jh_persist::save(jh_persist::Key::TraceGuard, 0.0f);
    }
    if (fs_ok && !jh_store::ok()) {   // same mirror-refresh as trace_clear
      fs_ok = false;
      emitLine("# storage DOWN after clear — not recording. `mount` retries, `format` rebuilds.");
    }  // internally gated on fs_ok; resets byte count/cap/headers
    trace_buf = "";
    stored_jumps = 0; stored_best = 0.0f;
    emitLine("# cleared stored data");
    emitLine("OK clear");
  } else if (cmd == "selftest") {
    runSelfTest();
    emitLine("OK selftest");
  } else if (cmd == "pincensus") {
    // Validity check for every pin-level claim this project has made.
    // Prints pin:<pulldown><pullup> for each GPIO. A free pin reads 01.
    static char census[900];
    jh_imu::pin_census(census, sizeof(census));
    emitLine("# pincensus format pin:<val_with_pulldown><val_with_pullup>");
    emitLine("# a FREE pin reads 01. 00 = held low. 11 = held high.");
    // Chunked: one long line got silently dropped by the bounded emit path
    // (which discards rather than blocks — see emitBytes), and a report you
    // never see is worse than no report.
    for (int off = 0; census[off]; ) {
      char chunk[73];
      int k = 0;
      while (k < 72 && census[off]) chunk[k++] = census[off++];
      chunk[k] = 0;
      emitLine(chunk);
      jh_link::watchdog_feed();
    }
    // The census yanks weak pulls across the LIVE I2C bus and the sensor's
    // own supply pin. Measured consequence (2026-08-16): a census mid-sampling
    // left the sensor streaming garbage — the motion gate latched open and
    // recorded ~460 KB of noise before a reboot cleared it. The audited
    // recovery pair exists for exactly this, so run it unconditionally: a
    // diagnostic that silently poisons the instrument is worse than none.
    jh_link::watchdog_feed();
    emitLine("# census touched live sensor pins — running audited revive");
    const bool census_revived = jh_imu::revive();
    jh_link::watchdog_feed();
    if (!census_revived) {
      emitLine("# revive FAILED — sensor left down; reboot or `revive`");
      emitLine("OK pincensus");
    } else {
      // F-09 (audit 2026-08-22): revive() alone is NOT recovery.
      //
      // It opens with bus_release() (s_bus.end(), begun_=false), cycles the
      // rail, and returns true unconditionally — it never calls begin() on
      // the bus or the IMU. So this handler used to print "revive ok" while
      // leaving every subsequent read returning BUSERR, with sensor_ok still
      // true: a diagnostic that silently destroys the instrument and then
      // reports success. The `revive` COMMAND does not have this bug because
      // it follows revive() with runSelfTest(), which re-probes and re-begins
      // the bus. Do the same here, and report what it actually found rather
      // than a hardcoded "ok".
      emitLine("# rail cycled — re-running selftest to actually restore the bus");
      const bool ok = runSelfTest();
      jh_link::watchdog_feed();
      emitf("# revive %s\n", ok ? "ok — sensor answering"
                                 : "INCOMPLETE — selftest still failing, sensor is down");
      emitLine("OK pincensus");
    }
#if defined(NRF52840_XXAA)
  } else if (cmd.startsWith("fillstore")) {
    // BENCH ONLY — fill the trace region fast, to test the ONE storage path
    // no test has ever reached: findTraceAppendPoint() walking a nearly-full
    // region at boot. docs/plan.md §3.4 flags it as a session risk ("~19k
    // blocks on a full trace region... a well-used puck would reset there,
    // latch StoreGuard, and run the whole session storage-less"). Watchdog
    // feeds were added on 2026-08-14 and have never been exercised against a
    // full region, because filling one honestly takes ~5 h of recording.
    //
    // This writes synthetic-but-VALID trace lines through the real
    // trace_append() path — same encoder, same blocks, same CRCs — so the
    // boot scan afterwards faces exactly what a long session would leave.
    // Usage: `fillstore <kb>` (default 256). Feeds the watchdog every line.
    long kb = 256;
    const int sp = cmd.indexOf(' ');
    if (sp > 0) { const long v = cmd.substring(sp + 1).toInt(); if (v > 0) kb = v; }
    emitf("# fillstore: writing ~%ld KB of synthetic trace...\n", kb);
    char line[40];
    uint32_t written = 0;
    const uint32_t target = (uint32_t)kb * 1024u;
    float t = 1000.0f;
    bool full = false;
    while (written < target && !full) {
      t += 0.02f;
      const int n = snprintf(line, sizeof(line), "%.3f,%.3f\n",
                             (double)t, (double)(1.0f + 0.001f * (written % 100)));
      if (n <= 0) break;
      full = jh_store::trace_append(line, (size_t)n);
      written += (uint32_t)n;
      jh_link::watchdog_feed();
      if ((written % 65536u) < (uint32_t)n) {
        emitf("# fillstore: %lu KB, trace_bytes=%lu%s\n",
              (unsigned long)(written / 1024u),
              (unsigned long)jh_store::trace_bytes(), full ? " FULL" : "");
      }
    }
    emitf("# fillstore: wrote %lu bytes, trace_bytes=%lu, region_full=%d\n",
          (unsigned long)written, (unsigned long)jh_store::trace_bytes(), (int)full);
    emitLine("OK fillstore");
  } else if (cmd == "dcdc") {
    // BENCH EXPERIMENT, deliberately NOT at boot. The nRF52840's internal
    // DC/DC typically saves ~40% of MCU current, but it needs external
    // inductors on the DCC pins and it is NOT established that this board
    // has them (the web budget ran out before it could be confirmed, and
    // guessing is the habit that cost this project four days).
    //
    // If they are absent, enabling DC/DC browns out the regulator. DCDCEN
    // clears on reset, so a brownout costs one reboot and self-recovers —
    // UNLESS it is enabled at boot, which would turn that into a boot loop.
    // Hence: runtime only, on request. If the board answers after this, the
    // hardware supports it; if it silently reboots, it does not, and we have
    // our answer at the cost of nothing.
    emitLine("# dcdc: enabling internal DC/DC. If this board lacks the");
    emitLine("# inductors it will brown out and reboot — which is the answer,");
    emitLine("# and it recovers by itself (DCDCEN clears on reset).");
    emitLine("OK dcdc");
    delay(120);                       // let the lines above actually get out
    sd_power_dcdc_mode_set(NRF_POWER_DCDC_ENABLE);  // <nrf_soc.h> via Arduino.h on this target
    delay(50);
    emitLine("# dcdc: still alive — the hardware supports it. Measure now.");
#endif
  } else if (cmd == "i2cdiag") {
    // Bench diagnostic (see jh_imu.h): rail readback + bus idle levels +
    // bounded-driver ACK + stock-Wire1 ACK, in one pass, no rail edges.
    // The whole point is the last comparison: Wire1 read a factory-fresh
    // sensor at 0.970 g on 2026-08-12, before the bounded driver existed.
    jh_imu::BusDiag d;
    emitLine("# i2cdiag: begin (stage markers follow — the LAST line you see");
    emitLine("# is the stage that hung, which is itself the finding)");
    if (!jh_imu::bus_diag_rail(d)) {
      emitLine("ERR i2cdiag_unsupported no sensor bus on this platform");
    } else {
      emitf("I2CDIAG rail_pin=%u sda=%u scl=%u\n",
            d.rail_pin, d.sda_pulled_up, d.scl_pulled_up);
      {
        uint32_t o, dir, cnf, in;
        jh_imu::bus_rail_registers(o, dir, cnf, in);
        emitf("I2CDIAG railreg out=%lu dir=%lu in=%lu cnf=0x%08lX\n",
              (unsigned long)o, (unsigned long)dir, (unsigned long)in,
              (unsigned long)cnf);
      }
      // Polarity sweep: the "drive it HIGH" assumption has never actually
      // been verified to power anything, and a factory-fresh board says the
      // rail is down. Try both drive states and release.
      for (uint8_t st = 0; st < 4; ++st) {
        uint8_t sda = 0, scl = 0, pin = 0;
        jh_link::watchdog_feed();
        jh_imu::bus_rail_sweep(st, sda, scl, pin);
        // bit0 = level against internal pull-DOWN, bit1 = against pull-UP.
        emitf("I2CDIAG sweep en=%s pin=%u sda(dn/up)=%u/%u scl(dn/up)=%u/%u\n",
              st == 0 ? "LOW " : (st == 1 ? "HIGH" : (st == 2 ? "FLOAT" : "HIGH-HIDRIVE")), pin,
              sda & 1u, (sda >> 1) & 1u, scl & 1u, (scl >> 1) & 1u);
      }
      jh_link::watchdog_feed();
      emitLine("# stage: bounded-driver probe");
      jh_link::watchdog_feed();
      jh_imu::bus_diag_twim(d);
      emitf("I2CDIAG twim6A=%u twim6B=%u (0=OK 1=NACK 2=TIMEOUT 3=BUSERR)\n",
            d.twim_result, d.twim_result_alt);
      // Everything above is bounded and is now SAFELY OUT before the control
      // runs: stock Wire1 spins forever on a held bus, so if the next line
      // is the last thing you see, the bus is held hard enough to hang the
      // driver this project used all week — which is itself the answer.
      emitLine("# running stock-Wire1 control (unbounded — may hang here)");
      jh_link::watchdog_feed();
      jh_imu::bus_diag_wire(d);
      emitf("I2CDIAG wire6A=%u wire6B=%u whoami=0x%02X\n",
            d.wire_ack, d.wire_ack_alt, d.wire_whoami);
      if (!d.rail_pin) {
        emitLine("# verdict: rail enable reads LOW while driven — board-level");
        emitLine("# power fault (the mule's signature). Not a driver problem.");
      } else if (!d.sda_pulled_up || !d.scl_pulled_up) {
        emitLine("# verdict: EN high but the module pull-ups aren't holding the");
        emitLine("# bus up — rail path dead downstream of the enable.");
      } else if (d.wire_ack || d.wire_ack_alt) {
        if (d.twim_result != 0 && d.twim_result_alt != 0) {
          emitLine("# verdict: WIRE1 GETS AN ACK AND THE BOUNDED DRIVER DOES NOT.");
          emitLine("# The sensor is ALIVE; twim_bounded.h is the bug.");
        } else {
          emitLine("# verdict: both drivers see the sensor — bus healthy.");
        }
      } else {
        emitLine("# verdict: rail up, bus idle high, neither driver gets an ACK");
        emitLine("# — points at the sensor die, not at our code.");
      }
      emitLine("OK i2cdiag");
    }
  } else if (cmd == "revive") {
    // Clean sensor power-cycle (16g sequencing) then a full retry — the
    // recovery for power-up-corrupted-but-undamaged silicon. Bench command;
    // safe to repeat, ~0.7 s of deliberate delays inside.
    //
    // BREADCRUMBED (task #18): revive-over-BLE resets the board reproducibly
    // (2026-08-16 and 08-18) and RESETREAS is bootloader-consumed, so stage
    // stamps are the only witness. crumb=1 -> died inside jh_imu::revive();
    // crumb=2 -> died in runSelfTest(); crumb=3 -> died emitting the result.
    // Cleared on clean completion; read back as `crumb=` on the next INFO.
    jh_power::breadcrumb_set(1);
    if (!jh_imu::revive()) {
      jh_power::breadcrumb_set(0);
      emitLine("ERR revive_unsupported no sensor rail on this platform");
    } else {
      jh_power::breadcrumb_set(2);
      emitLine("# rail cycled clean (bus floated first) — retrying selftest");
      runSelfTest();
      jh_power::breadcrumb_set(3);
      emitLine("OK revive");
      jh_power::breadcrumb_set(0);
    }
  } else if (cmd.startsWith("set ")) {
    // set <airtime_offset_s|height_scale> <value|default> — runtime
    // calibration, persisted to NVS. Ranges are sanity rails, not tuning.
    const int sp = cmd.indexOf(' ', 4);
    String key = sp > 0 ? cmd.substring(4, sp) : "";
    String val = sp > 0 ? cmd.substring(sp + 1) : "";
    key.trim(); val.trim();
    const bool is_off   = key == "airtime_offset_s";
    const bool is_scale = key == "height_scale";
    // vbat_scale: the PER-UNIT battery-divider correction (jh_power.h). Its
    // rail is deliberately tight — resistor and reference tolerance produce a
    // few percent, so anything outside ±20% is a typo, not a calibration, and
    // accepting it would make the gauge confidently absurd.
    const bool is_vbat  = key == "vbat_scale";
    if (!is_off && !is_scale && !is_vbat) {
      emitLine("ERR set_unknown_key (airtime_offset_s | height_scale | vbat_scale)");
    } else {
      const jh_persist::Key k = is_off   ? jh_persist::Key::AirtimeOffsetS
                              : is_scale ? jh_persist::Key::HeightScale
                                         : jh_persist::Key::VbatScale;
      if (val == "default") {
        jh_persist::clear(k);
        loadCalibration();
        emitLine("# reverted to the compiled default");
        emitLine("OK set");
      } else {
        const float f = val.toFloat();
        const bool sane = is_off   ? (f >= -0.5f && f <= 0.5f)
                        : is_scale ? (f >= 0.5f && f <= 2.0f)
                                   : (f >= 0.8f && f <= 1.25f);
        if (!sane) {
          emitf("ERR set_out_of_range %s=%s\n", key.c_str(), val.c_str());
        } else {
          jh_persist::save(k, f);
          loadCalibration();
          emitLine("# saved to device memory — survives reboot and reflash");
          emitLine("OK set");
        }
      }
    }
  } else if (cmd == "info") {
    // `info` is a REQUESTED multi-line response, not chatter, and its three
    // lines are emitted back to back with no chance for the host to drain
    // between them. INFO + PARAMS together exceed the CDC buffer, so under the
    // default drop policy PARAMS was silently discarded on USB — which made
    // `jump selftest` report "device didn't report its params — old firmware?"
    // on a board that had just been flashed. (Found 2026-08-16, immediately
    // after `src=` lengthened INFO enough to make it deterministic.)
    //
    // Same reasoning as a file export: dropping is right for chatter, wrong
    // for something the operator asked for and a gate then parses. Bounded
    // wait, watchdog fed, and it self-clears below.
    s_serial_must_not_drop = true;
    // ble=1 advertises the capability (this firmware speaks BLE); the runtime
    // health of the radio is the self-test's `ble` row, not this flag.
    // Battery keys appended only where measurable — same adder rule as STATS.
    const int vbat = jh_power::vbat_mv();
    if (vbat >= 0) {
      emitf("INFO fw=%s sample_hz=%d log_hz=%d motion_thresh_g=%.2f "
            "idle_timeout_s=%d ble=1 vbat_mv=%d batt_pct=%d chg=%d src=%s\n",
            FW_VERSION, JH_SAMPLE_HZ, JH_LOG_HZ,
            (double)JH_MOTION_THRESH_G, (int)JH_IDLE_TIMEOUT_S,
            vbat, jh_power::batt_pct(), jh_power::charging(), JH_BUILD_SRC);
    } else {
      emitf("INFO fw=%s sample_hz=%d log_hz=%d motion_thresh_g=%.2f "
            "idle_timeout_s=%d ble=1 src=%s\n", FW_VERSION, JH_SAMPLE_HZ, JH_LOG_HZ,
            (double)JH_MOTION_THRESH_G, (int)JH_IDLE_TIMEOUT_S, JH_BUILD_SRC);
    }
    // Adder keys, per the rule above: reas= only when the last reset had a
    // recorded cause (0 = clean power-on or platform without the register),
    // hichg= only where charge-current select exists. reas made three blind
    // reboot diagnoses (2026-08-16) into a register read; hichg is the
    // firmware half of the failing fast-charge verification.
    if (jh_power::reset_reason() != 0)
      emitf("# reas=0x%08lX\n", (unsigned long)jh_power::reset_reason());
    if (jh_power::fast_charge_state() >= 0)
      emitf("# hichg=%d chg=%d\n", jh_power::fast_charge_state(), jh_power::charging());
    // dcdc= is an adder key (absent where the concept does not apply, e.g.
    // the host build). F-05: without this, a reverted DCDCEN was invisible.
    if (jh_power::dcdc_enabled() >= 0)
      emitf("# dcdc=%d\n", jh_power::dcdc_enabled());
    if (jh_link::local_name()[0])
      emitf("# name=%s\n", jh_link::local_name());  // WHICH puck — quiver world
    if (jh_power::breadcrumb_last() != 0)
      emitf("# crumb=%u\n", (unsigned)jh_power::breadcrumb_last());  // stage the last
                                                  // reset died in (task #18)
    emitLine("PARAMS " JH_PARAMS_SUMMARY);
    // Effective calibration (PARAMS above shows compiled defaults).
    // vbat_scale appended only when it is doing something (!= 1.0), keeping
    // the adder-key rule: a board with nominal divider resistors emits the
    // exact line every existing client already parses.
    if (cal_vbat_scale != 1.0f) {
      emitf("CAL airtime_offset_s=%.4f height_scale=%.3f source=%s vbat_scale=%.4f"
            " off_src=%s scale_src=%s vbat_src=%s\n",
            detector.params().airtime_offset_s, detector.params().height_scale,
            cal_from_nvs ? "device" : "defaults", cal_vbat_scale,
            cal_src_off ? "device" : "defaults",
            cal_src_scale ? "device" : "defaults",
            cal_src_vbat ? "device" : "defaults");
    } else {
      emitf("CAL airtime_offset_s=%.4f height_scale=%.3f source=%s"
            " off_src=%s scale_src=%s vbat_src=%s\n",
            detector.params().airtime_offset_s, detector.params().height_scale,
            cal_from_nvs ? "device" : "defaults",
            cal_src_off ? "device" : "defaults",
            cal_src_scale ? "device" : "defaults",
            cal_src_vbat ? "device" : "defaults");
    }
    emitLine("OK info");
    s_serial_must_not_drop = false;   // back to drop-is-fine for chatter
  } else if (cmd == "off") {
    // Soft power-off (jh_power seam; the S2 sleep design's manual slice).
    // Farewell BEFORE the attempt: on a supporting platform system_off()
    // never returns, and the sender — a phone at the beach, most likely —
    // deserves to know the silence that follows is intentional. The OK
    // terminator also goes first for the same reason (a client waiting on
    // OK/ERR would otherwise hang into its timeout on every clean off).
    if (jh_power::vbat_mv() >= 0) {  // supported platforms measure vbat too
      flushTrace();  // recording stops here — don't strand the open block
      emitLine("# powering down — plug in USB or tap reset to wake");
      emitLine("OK off");
      delay(250);            // let USB CDC + BLE actually push those bytes
      jh_power::system_off();  // does not return (seam contract: vbat
                               // support implies real off — nrf52 sleeps,
                               // host exits)
      return;  // contract violated? still never OK-then-ERR — just stop
    }
    emitLine("ERR off_unsupported this build has no soft-off");
  } else if (cmd == "dfu") {
    // Reboot into the bootloader's OTA-DFU mode (jh_link seam; nRF52 only).
    // Same farewell-first shape as `off` above, for the same reason: on a
    // supporting platform the call never returns, and the sender deserves to
    // know the disconnect that follows is intentional. After this, the puck
    // advertises as "AdaDFU" for nRF Connect until a transfer completes or
    // it is reset.
    flushTrace();  // recording stops here — don't strand the open block
    emitLine("# rebooting to DFU — use nRF Connect; reset/power-cycle to abort");
    emitLine("OK dfu");
    delay(250);              // let USB CDC + BLE actually push those bytes
    if (!jh_link::reboot_to_dfu()) {
      emitLine("ERR dfu_unsupported this build has no OTA bootloader");
    }
    return;
  } else if (cmd == "uf2") {
    // Reboot into the bootloader's UF2 drive (MSC). Bench use: bootloader
    // self-updates ship as update-*.uf2 and are MSC-only; the 1200-baud
    // touch can't reach MSC (serial-only magic by design).
    flushTrace();
    emitLine("# rebooting to UF2 drive — copy update-*.uf2 there; reset to abort");
    emitLine("OK uf2");
    delay(250);
    if (!jh_link::reboot_to_uf2()) {
      emitLine("ERR uf2_unsupported this build has no UF2 bootloader");
    }
    return;
  } else if (cmd == "mount") {
    // Non-destructive retry of a guard-skipped or failed mount — `format`
    // is the destructive one. Same guard bracket as boot: a hang costs one
    // watchdog reset and re-latches the skip; nothing is ever erased.
    if (fs_ok) {
      emitLine("OK mount already_mounted");
      return;
    }
    jh_persist::save(jh_persist::Key::StoreGuard, 1.0f);
    fs_ok = jh_store::try_mount(emitLine);
    jh_persist::save(jh_persist::Key::StoreGuard, 0.0f);
    if (fs_ok) {
      scanStoredJumps();
      emitf("# stored history: %lu jumps, best %.2f m — `dump` to export\n",
            (unsigned long)stored_jumps, (double)stored_best);
      emitLine("OK mount");
    } else {
      emitLine("ERR mount_failed storage still down — `format` rebuilds it (DESTROYS data)");
    }
  } else if (cmd == "format") {
    // Last-resort storage recovery — works when `clear` cannot (fs down).
    // Destroys stored jumps + trace; live detection unaffected either way.
    flushTrace();
    jh_persist::save(jh_persist::Key::StoreGuard, 1.0f);   // guard the retry too
    const bool fmt_ok = jh_store::hard_format(emitLine);
    jh_persist::save(jh_persist::Key::StoreGuard, 0.0f);
    if (fmt_ok) {
      fs_ok = true;
      // The whole chip was just erased, so the trace region's geometry is
      // known-good again. This is the recovery the wedge message promises;
      // without it that message would be a dead end.
      jh_store::set_trace_wedged(false);
      jh_persist::save(jh_persist::Key::TraceGuard, 0.0f);
      scanStoredJumps();
      emitLine("OK format");
    } else {
      emitLine("ERR format_failed see hints above");
    }
  } else if (cmd == "fakejump") {
    // BENCH ONLY: emit a synthetic JUMP through the real emit path — added
    // 2026-08-12, the morning the Sense's IMU was pronounced hardware-dead
    // (rail shorted; SENSE_FIRST_BOOT 16c resolution). A dead sensor stops
    // MEASUREMENT, not the radio: this lets the entire watch pipeline —
    // pacing, line reassembly, the corruption gate, FIT fields — be
    // exercised end-to-end against a puck that cannot jump. Uses the same
    // counters/formatting as a real jump so clients cannot tell the
    // difference; deliberately does NOT touch stored history.
    session_jumps++;
    const float h_m = 0.30f + 0.05f * (float)(session_jumps % 7);
    const float at  = sqrtf(8.0f * h_m / 9.80665f);
    if (h_m > session_best) session_best = h_m;
    if (at > session_best_airtime) session_best_airtime = at;
    emitf("JUMP n=%lu airtime_raw_s=%.3f airtime_s=%.3f height_m=%.3f height_ft=%.1f best_m=%.3f\n",
          (unsigned long)session_jumps, at, at, h_m, h_m * JH_M_TO_FT, session_best);
    emitLine("OK fakejump");
  } else if (cmd == "gyro") {
    // BENCH DIAGNOSTIC, SENSE_FIRST_BOOT item 26 step 1: has the gyro ever
    // been read on real silicon at all?
    //
    // This matters more than a spec-check. lever_arm.h SELF-ARMS the spin
    // correction — after one spinning jump it sets spin_lever_m above zero
    // and the correction goes live. So "ships inert" is only true until the
    // first spun jump, and everything downstream then rests on a sensor
    // nobody has looked at. Look at it.
    //
    // READ IT LIKE THIS: held still, |w| should settle near 0 dps once the
    // planing baseline converges (gyro_bias.h, ~5 s). Rotate the board by
    // hand and |w| should track — a slow turn is tens of dps, a brisk flick
    // hundreds. A byte-order error would NOT look broken here; it would look
    // like a plausible-but-wrong rate, so compare against something known.
    float gx, gy, gz;
    if (!jh_imu::read_gyro_dps(gx, gy, gz)) {
      emitLine("ERR gyro_unsupported no gyro on this build");
    } else {
      emitLine("# gyro: 20 samples @ 10 Hz — hold still, then rotate the board");
      for (int i = 0; i < 20; ++i) {
        jh_link::watchdog_feed();  // 20x delay(100) inside a command starves
                                    // the ~3.5s WDT with zero margin otherwise
        if (!jh_imu::read_gyro_dps(gx, gy, gz)) { emitLine("# read failed"); break; }
        const float raw_mag = sqrtf(gx * gx + gy * gy + gz * gz);
        // Report BOTH raw and bias-corrected: the pair is what shows whether
        // the baseline estimator is doing anything, and a raw magnitude that
        // never settles is a different fault from a bias that will not train.
        const float corr = gyro_bias.update(gx, gy, gz,
                                            detector.state() == jump::State::RIDING);
        emitf("GYRO n=%d x=%.1f y=%.1f z=%.1f raw_dps=%.1f corr_dps=%.1f "
              "bias=(%.1f,%.1f,%.1f)\n",
              i, gx, gy, gz, raw_mag, corr,
              gyro_bias.x(), gyro_bias.y(), gyro_bias.z());
        delay(100);
      }
      emitLine("OK gyro");
    }
  } else if (cmd == "vbatscan") {
    // BENCH DIAGNOSTIC, SENSE_FIRST_BOOT item 24. Reads the cell at every
    // ADC acquisition-time setting so the bench can see whether the known
    // ~2.7%-low error depends on acquisition time.
    //
    // READ IT LIKE THIS: if mv CLIMBS with tacq_us, the ADC was not getting
    // long enough to charge through the divider's ~340 kOhm source — a
    // firmware fix, correct for every unit. If mv is FLAT, the error is in
    // the divider resistors or the reference — a per-unit calibration, and
    // baking it into firmware would be wrong for other units.
    //
    // Take it on a RESTED cell (chg=0), since a charging one drifts under you.
    if (jh_power::vbat_mv_tacq(0) < 0) {
      emitLine("ERR vbatscan_unsupported this build has no battery ADC");
    } else {
      static const uint16_t kTacqUs[6] = {3, 5, 10, 15, 20, 40};
      emitf("# vbatscan chg=%d — mv rising with tacq_us => acquisition time; "
            "flat => divider/reference\n", jh_power::charging());
      for (int c = 0; c <= 5; ++c) {
        emitf("VBATSCAN tacq_us=%u mv=%d\n", kTacqUs[c], jh_power::vbat_mv_tacq(c));
      }
      emitLine("OK vbatscan");
    }
  } else {
    // Help BEFORE the ERR terminator: clients stop reading at OK/ERR, so
    // anything after it would sit in their buffer and corrupt the framing
    // of the NEXT command's response.
    printHelp();
    emitf("ERR unknown_command %s\n", cmd.c_str());
  }
}

static void pollSerial() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      cmd_buf.trim();
      if (cmd_buf.length() > 0) handleCommand(cmd_buf);
      cmd_buf = "";
    } else if (cmd_buf.length() < 64) {
      cmd_buf += c;
    }
  }
}

// ---------------- Setup ----------------
void setup() {
  jh_link::watchdog_init();   // FIRST — no pre-watchdog hang window, ever
  Serial.begin(115200);
  delay(300);
  emitLine("# JumpHeight fw v" FW_VERSION " src=" JH_BUILD_SRC);  // serial-only here: BLE isn't up yet

  jh_imu::init();
  jh_power::init();

  // Persist first — it's INTERNAL flash (no external bus, nothing to wedge)
  // and both crash guards live in it, so it must be readable before any
  // external-bus first contact.
  // F-05 (audit 2026-08-22): enable the internal DC/DC at every boot.
  //
  // Measured 1.39x endurance on this project's own same-board A/B
  // (STATUS 2026-08-20). It used to be reachable only from the `dcdc` console
  // command, so every boot ran on the LDO — and because DCDCEN is volatile,
  // any watchdog reset silently reverted a hand-typed enable with nothing on
  // the wire to reveal it. STATUS:527's gate ("earns a place in boot only
  // after the A/B") was satisfied on 2026-08-20; this is that place.
  //
  // After jh_power::init() so the SoftDevice-state check inside the seam has
  // run once already, and before anything power-hungry starts.
  jh_power::enable_dcdc();

  jh_persist::init();
  loadCalibration();
  // Seed the advertised battery before BLE first advertises (loop() refreshes it).
  jh_link::publish_battery(jh_power::batt_pct(), jh_power::charging());

  // Mount storage — BRACKETED by the sticky store guard (2026-08-12: the
  // mule's wedged QSPI chip hung this call before BLE ever started; found
  // by the neuter-and-bisect method the sensor probe taught us). A hang
  // costs one watchdog reset; every boot after skips the mount and comes up
  // alive with an honest `flash FAIL` row. The `format` command is the
  // deliberate retry — and if THAT hangs, the same guard catches it again.
  if (jh_persist::load(jh_persist::Key::StoreGuard, 0.0f) > 0.5f) {
    emitLine("# storage: skipped (previous boot hung in the mount — `mount` retries safely, `format` rebuilds)");
    fs_ok = false;
    // Remember it for loop(): a latched guard means the LAST attempt never
    // returned, so the 30 s auto-remount must not retry it — that is how a
    // wedged chip turns into a reset every ~33 s for the whole session.
    // The `mount` command stays available as the deliberate human retry.
    store_guard_was_latched = true;
  } else {
    jh_persist::save(jh_persist::Key::StoreGuard, 1.0f);
    fs_ok = jh_store::init(emitLine);
    jh_persist::save(jh_persist::Key::StoreGuard, 0.0f);
  }

  // F-07: re-arm the trace wedge across the reboot. jh_store's flag is RAM,
  // and the mount above just re-derived an append point by scanning — the
  // same scan that cannot see a stale island. Without this, one watchdog
  // reset (which a failing flash chip makes likely, not hypothetical) would
  // silently re-enable the exact append the wedge exists to prevent.
  if (jh_persist::load(jh_persist::Key::TraceGuard, 0.0f) > 0.5f) {
    jh_store::set_trace_wedged(true);
    emitLine("# trace: WEDGED from a previous failed erase — raw sample "
             "recording is OFF until `format`. Jumps still record.");
  }

  // Bring BLE up before the self-test so the `ble` row reflects the real result.
  // A failure is non-fatal: everything below (and jump detection) runs regardless.
  ble_ok = jh_link::begin("JumpHeight");
  runSelfTest();

  // Latch gyro presence AFTER runSelfTest(), because that is what calls
  // jh_imu::begin() — the only thing that gives the driver its bus handle.
  // Reading before it is not "an early read that returns nothing"; it is a
  // null dereference and a boot loop. jh_imu::init() at the top of setup()
  // only opens the I2C bus, which is easy to mistake for "the IMU is ready".
  {
    float gx, gy, gz;
    gyro_present = jh_imu::read_gyro_dps(gx, gy, gz);
  }
  scanStoredJumps();
  if (stored_jumps > 0) {
    emitf("# stored history: %lu jumps, best %.2f m — `dump` to export, `clear` to reset\n",
          (unsigned long)stored_jumps, stored_best);
  }
  if (!sensor_ok) {
    emitLine("# sensor not working — fix wiring, then type `selftest` (no re-flash needed)");
  }
  printHelp();
  emitLine("READY");

  trace_buf.reserve(2048);
  t0_us         = jh_clock::micros64();
  last_flush_ms = millis();
}

// ---------------- Loop ----------------
void loop() {
  pollSerial();
  jh_link::poll(handleCommand);  // BLE commands run through the same path as serial
  jh_link::pump();               // send at most one paced BLE chunk — never blocks
  // SELF-HEALING STORAGE. Measured 2026-08-19: a puck that reboots on a
  // nearly-flat cell comes up with the QSPI flash unmounted — the chip needs
  // more supply than the MCU and radio do — and then looks PERFECTLY healthy
  // (BLE up, sensor up, watch connected) while recording nothing. The manual
  // `mount` command already recovers it: on the OG, one `mount` after the
  // supply came back restored all 3 jumps and 150,034 trace bytes, nothing
  // lost. But nobody types `mount` at a beach, and the only other symptom is
  // one self-test row.
  //
  // So retry it automatically, slowly (every 30 s) and only while down. The
  // same StoreGuard bracket as boot and the `mount` command, so a hang costs
  // one watchdog reset and re-latches the skip rather than repeating forever.
  // Retry bounded and guard-aware. Two defects found by the pre-flight review
  // (2026-08-20) and fixed here:
  //
  //  * the first version passed try_mount(nullptr) for silence. try_mount
  //    calls announce() on BOTH failure paths with no null check — a jump to
  //    address 0, i.e. HardFault, i.e. NVIC_SystemReset. It fired only on
  //    FAILURE, which is the one path this feature exists for: a puck with
  //    unmountable storage would have reset every 30 s, dropping the watch
  //    link and the live session each time. Strictly worse than the silent
  //    no-record symptom it was written to cure. (jh_store now null-guards
  //    announce as well — belt and braces, since the header never forbade it.)
  //
  //  * it wrote StoreGuard around the attempt but never READ it, so its own
  //    comment claimed a property the code did not have. A try_mount that
  //    HANGS costs a watchdog reset, boot then skips the mount because the
  //    guard is latched — and the old retry ignored the latch and hung again,
  //    ~every 33 s, forever.
  //
  // So: if the guard was latched at boot, the last attempt never returned.
  // Do not auto-retry that; leave it to the human `mount` command, which is
  // what the guard's design always intended. And give up after a few clean
  // failures rather than retrying for the whole session.
  // Publish the battery snapshot the advertisement uses. Done HERE, on the
  // loop() task, because the BLE connect callback runs at a higher FreeRTOS
  // priority and must never touch the SAADC itself (jh_link.cpp explains the
  // cross-task EasyDMA hazard). Once a minute: the payload is only read when
  // advertising re-arms, and each read costs a conversion.
  {
    static uint32_t last_batt_pub_ms = 0;
    const uint32_t now_bp = millis();
    if (last_batt_pub_ms == 0 || now_bp - last_batt_pub_ms >= 60000UL) {
      last_batt_pub_ms = now_bp ? now_bp : 1;
      jh_link::publish_battery(jh_power::batt_pct(), jh_power::charging());
    }
  }

  if ((!fs_ok || !jh_store::ok()) && !store_guard_was_latched &&
      remount_tries < kRemountMaxTries) {
    static uint32_t last_remount_ms = 0;
    const uint32_t now_rm = millis();
    if (now_rm - last_remount_ms > 30000UL) {
      last_remount_ms = now_rm;
      ++remount_tries;
      jh_persist::save(jh_persist::Key::StoreGuard, 1.0f);
      fs_ok = jh_store::try_mount(quietAnnounce);
      jh_persist::save(jh_persist::Key::StoreGuard, 0.0f);
      if (fs_ok) {
        scanStoredJumps();
        remount_tries = 0;
        emitf("# storage RECOVERED automatically: %lu jumps, best %.2f m\n",
              (unsigned long)stored_jumps, (double)stored_best);
      } else if (remount_tries >= kRemountMaxTries) {
        emitLine("# storage still down after retries — giving up. `mount` to "
                 "retry by hand, `format` to rebuild (DESTROYS data).");
      }
    }
  }


  if (jh_link::takeGreetPending()) bleGreet();  // greet a client that just subscribed
  if (!sensor_ok) { delay(10); return; }  // command loop still runs; sampling paused

  // Charger current select, once a second (see jh_power::update_charge_current).
  {
    static uint32_t last_chg_ms = 0;
    const uint32_t now_ms_chg = millis();
    if (now_ms_chg - last_chg_ms >= 1000) {
      last_chg_ms = now_ms_chg;
      jh_power::update_charge_current();
    }
  }

  static int64_t next_us = jh_clock::micros64();
  const int64_t  now_us  = jh_clock::micros64();
  if (now_us < next_us) {
    // SLEEP the leftover time instead of spinning it away (2026-08-15).
    //
    // This `return` used to hand straight back to the Arduino/FreeRTOS loop
    // task, which called loop() again immediately — so the task never
    // blocked, the RTOS idle task never ran, and the core never slept. At
    // 200 Hz the real work (one I2C burst, the detector, an occasional flash
    // block) is a few hundred microseconds out of 5 ms, so the CPU spent
    // ~90% of its life re-reading a clock at 64 MHz. That is the dominant
    // term in the device's idle draw — against a plan that had assumed ~4 mA
    // from an idle that did not exist.
    //
    // F-21 (audit 2026-08-22): this comment used to cite "the MEASURED
    // 11.6 mA". That figure is RETRACTED (docs/battery-measurement.md:11,
    // STATUS:497) — it came from a batt_pct extrapolation through a region
    // nobody had measured. The honest baseline is the conservation bound
    // **<=9.7 mA** (250 mAh datasheet capacity / 25.7 h measured runtime).
    // Rule 2 exists because retracted numbers come back: this audit's own
    // first pass repeated 11.6 mA after reading it HERE, which is the proof
    // of harm. If you need a current figure, take it from
    // docs/battery-measurement.md, never from a comment.
    //
    // delay() here is vTaskDelay: it yields to the idle task, which sleeps
    // the core until the next ~0.98 ms tick. The 1200 us guard band means we
    // can never oversleep a deadline — one tick still leaves >200 us for the
    // final tight approach, so the sample cadence stays precise where it
    // matters.
    //
    // Jitter budget, because AIRTIME IS THE MEASUREMENT: the pacer keeps
    // long-run rate exact (next_us += INTERVAL), so only individual samples
    // shift, by at most ~1 ms. Height error goes as dh = g*T*dT/4, i.e.
    // 2.5 mm at a 1 s flight — negligible against 0.8-1.6 m jumps.
    // Falsifier on record: if post-change sample deltas run >2 ms off
    // cadence, or desk-test airtimes shift systematically, revert.
    // F-06 (audit 2026-08-22): the guard was 1200 us, which meant that below
    // 1200 us remaining we RETURNED WITHOUT DELAYING — loop() spun at 64 MHz
    // for the "final tight approach". Measured ~24% of wall-clock.
    //
    // That approach bought nothing. This core's micros() is TICK-DERIVED at
    // 976.5625 us steps (DWT cycle counting is off), so a spin between ticks
    // cannot observe time passing at all: it re-reads the same value until
    // the tick rolls, then exits. It burned current to wait for the very
    // event delay(1) waits for, only without sleeping.
    //
    // WHY NOT THE OTHER FIX. The audit's preferred option was to enable the
    // DWT cycle counter so micros() becomes precise and the band could shrink
    // honestly. This repo already prohibits that, for a reason recorded in
    // twim_bounded.h:57 — enabling DWT shrinks micros()'s wrap period to
    // ~67 s and BREAKS jh_clock's wrap arithmetic, which every timestamp in
    // the system depends on. That fix would trade a power bug for a
    // correctness bug across the whole measurement. DWT stays off.
    //
    // So: one tick (977 us) is the smallest honest guard on this clock.
    // Below it there is nothing to approach — the next tick IS the resolution.
    // Jitter cost is unchanged from the analysis above (at most ~1 ms on an
    // individual sample; long-run rate stays exact because next_us advances
    // by INTERVAL regardless), and the falsifier on record is unchanged:
    // if post-change sample deltas run >2 ms off cadence, or desk-test
    // airtimes shift systematically, REVERT rather than rationalise.
    if (next_us - now_us > 977) delay(1);
    return;
  }
  next_us += SAMPLE_INTERVAL_US;
  // After a long stall (e.g. a 100 s serial dump) don't "catch up" with a
  // burst of thousands of back-to-back samples — resynchronize instead.
  if (now_us - next_us > 20 * (int64_t)SAMPLE_INTERVAL_US) next_us = now_us;

  float ax, ay, az;
  if (!jh_imu::read_accel_g(ax, ay, az)) {
    // Skipping the sample is right for a transient I2C hiccup; staying quiet
    // about a dead IMU is not. One warning, once, so a live client sees it
    // without the line repeating at the sample rate.
    // F-09 sibling sweep: this was `== 200`, a strict-equality one-shot. Any
    // earlier transient that walked the counter past 200 consumed the warning
    // forever — the sensor could then die permanently and never say so. `>=`
    // with the existing latch is the same one-shot behaviour without the
    // pre-consumption hole (CLAUDE.md rule 3: a skipped warning is not a
    // passing one).
    if (++accel_fail_count >= 200 && !sensor_warned) {  // ~1 s at 200 Hz
      sensor_warned = true;
      emitLine("# WARNING accelerometer not answering — this session is "
               "recording nothing. Check wiring, then `selftest`.");
    }
    return;
  }
  // double (B3, glue-and-forget.md §3a): this is elapsed seconds since boot,
  // an ABSOLUTE value that grows without bound for as long as the device
  // stays awake — float32 loses sub-ms resolution starting at 18.2 h of
  // uptime and silently drops ~12% of real jumps by 6 months. See
  // jump_detector.h's update() for the full failure-mode writeup and the
  // permanent falsifier (tools/tests/test_timebase_falsifier.py).
  const double t  = (now_us - t0_us) * 1e-6;
  // Orientation-independent, normalized to this unit's own measured gravity.
  const float mag = sqrtf(ax * ax + ay * ay + az * az) / g_baseline;

  // --- motion gate ---
  const uint32_t now_ms = millis();
  const bool     moved_now = fabsf(mag - 1.0f) > JH_MOTION_THRESH_G;
  // How long the board was STILL, captured before last_motion_ms is updated —
  // the auto-clear rule below needs the idle span that just ended, and after
  // the update it would always read zero.
  const uint32_t idle_ms = now_ms - last_motion_ms;
  if (moved_now) {
    last_motion_ms = now_ms;
    motion_seen    = true;
  }
  // ---- STORAGE LIFECYCLE: reclaim a dead trace at a session boundary ----
  // The problem (docs/garmin-only.md §3): the trace region is append-only and
  // holds ~5 h. Once full it records NOTHING, forever, with no symptom the
  // watch can show — jumps keep flowing and jumps are all the watch sees. A
  // user who never opens a laptop would silently stop keeping raw data.
  //
  // The rule is deliberately narrow, because auto-clearing is auto-DELETING.
  // All three must hold:
  //   1. the trace is ALREADY FULL — so it is recording nothing, and clearing
  //      it cannot make the present worse. This is what makes the whole thing
  //      safe: we never delete a trace that is still doing its job.
  //   2. the board has been still for AUTO_CLEAR_IDLE_MS — a session boundary,
  //      not a pause. An hour is far longer than sitting on the board between
  //      runs and far shorter than the gap between outings.
  //   3. motion has just resumed — a new session is actually starting, so the
  //      space is about to be needed.
  // And it clears the TRACE ONLY: jumps are the user's history and the watch's
  // reconnect source (jh_store::trace_clear()).
  //
  // The trade it makes, stated plainly: OLD raw data is sacrificed so the
  // CURRENT session records. Losing the session you are actually riding is
  // worse than losing one you already had a chance to sync. Sync first (phone
  // or laptop) if the old trace matters.
  static uint32_t auto_clears = 0;
  if (moved_now && motion_seen && idle_ms >= AUTO_CLEAR_IDLE_MS &&
      jh_store::ok() && jh_store::trace_is_full()) {
    emitLine("# trace region was FULL and a new session is starting —");
    emitLine("# clearing the trace to make room. Stored jumps are untouched.");
    jh_store::trace_clear();
    // F-07: a failed erase leaves the region unappendable. Say so — silence
    // here is how a puck records nothing while looking healthy.
    if (jh_store::trace_wedged()) {
      // Persist BEFORE reporting: if the next thing to happen is the reset
      // that a sick flash chip tends to cause, the flag must already be down.
      // And check it — an unstored guard bit is a guard that is not there,
      // and the rider should know the protection did not take (F-10 sweep).
      if (!jh_persist::save(jh_persist::Key::TraceGuard, 1.0f))
        emitLine("# WARNING: could not persist the trace-wedge flag — a reboot "
                 "will re-enable raw recording into a bad region. Run `format`.");
      emitLine("ERR trace_clear — erase failed; raw trace recording is OFF "
               "until a full `format`. Jumps still record.");
    }
    // Re-read the store's own verdict. main's fs_ok is a MIRROR, and a mirror
    // that never refreshes is how "records nothing, reports healthy" happens:
    // every writer gates on this copy, and `stats` prints fs=down from it.
    if (fs_ok && !jh_store::ok()) {
      fs_ok = false;
      emitLine("# storage DOWN after trace clear — not recording. `mount` retries, `format` rebuilds.");
    }
    ++auto_clears;
    emitf("# trace cleared (auto #%lu) — recording resumes\n",
          (unsigned long)auto_clears);
  }

  const bool was_active = active;
  active = motion_seen && (now_ms - last_motion_ms) < IDLE_TIMEOUT_MS;
  if (active && !was_active) emitLine("STATE recording");
  if (!active && was_active) {
    emitLine("STATE idle");
    flushTrace();
  }
  if (!active) return;

  // --- live jump detection ---
  // Spin correction: a rotating board reads its own omega^2*r on top of the
  // specific force, which breaks BOTH detector gates above ~300 dps (see
  // jump_detector.h's correct_for_spin). Feed the gyro-aware overload where
  // the hardware has a gyro; read_gyro_dps() returns false on the 3-axis v1
  // boards, and the accel-only path below is what those have always used.
  // With spin_lever_m uncalibrated (0, the default) the two paths are
  // identical anyway — this costs nothing until a mount is measured.
  // The gyro's zero-rate level is spec'd at ±10 dps and drifts with
  // temperature, and the correction squares omega — so a raw magnitude would
  // feed a 2*w*b cross-term straight into the height. gyro_bias.h keeps a
  // planing baseline (updated only while RIDING, frozen in flight) and
  // returns the corrected magnitude. Mandatory per docs/gyro-sim-plan.md §4.
  jump::JumpEvent ev;
  float gx, gy, gz;
  const bool have_gyro = jh_imu::read_gyro_dps(gx, gy, gz);
  // Only counted on hardware that HAS a gyro. The v1 boards answer false on
  // every call by design (esp32/jh_imu.cpp), and counting that would turn a
  // deliberate capability difference into a fault indication.
  if (!have_gyro && gyro_present) gyro_fail_count++;
  // Transition tracking is hoisted OUT of the have_gyro branch (2026-08-12
  // gyro-crash-hunt, confirmed by repro): a flight whose LANDING sample hits
  // a transient gyro read failure used to exit AIRBORNE through the
  // accel-only branch, skip commit entirely, and leave up to 64 stale
  // observations pending — which then merged into the NEXT flight's median
  // and walked spin_lever_m to a blend no real flight would produce.
  const bool was_airborne = detector.state() == jump::State::AIRBORNE;
  bool jumped;
  if (have_gyro) {
    const bool riding = detector.state() == jump::State::RIDING;
    const float omega_dps = gyro_bias.update(gx, gy, gz, riding);

    // Self-calibrating lever arm (lever_arm.h). In flight the board is in free
    // fall, so `mag` — the RAW, uncorrected magnitude — IS the rotation's own
    // omega^2*r term, and r falls out of it. Feeding the raw value is essential:
    // the corrected one would be circular.
    if (was_airborne) {
      lever_arm.observe(mag, omega_dps);
      airObserve(mag, omega_dps);   // full-rate flight physics — see airObserve
    }

    jumped = detector.update(t, mag, omega_dps, ev);
  } else {
    // No gyro on this platform: still record |a| so the band can be looked at,
    // with omega recorded as 0 rather than pretended-away.
    if (was_airborne) airObserve(mag, 0.0f);
    jumped = detector.update(t, mag, ev);
  }
  // Flight over. The old rule — "a rejected flight still carries perfectly
  // good rotation data" — was the ROOT of a confirmed calibration-corruption
  // path: phantom flights (max-airtime rejects manufactured by a railed gyro)
  // are all rejects, and committing them walked a converged lever arm off by
  // ~50x in minutes. Commit ONLY flights that ended in a validated jump;
  // every other AIRBORNE exit discards its observations.
  if (was_airborne && detector.state() != jump::State::AIRBORNE) {
    if (jumped) {
      // SELF-ARM GATED FOR THE ONE-SHOT WATER SESSION (review 2026-08-14).
      //
      // correct_for_spin() is applied to the magnitude FED INTO the detector's
      // gates, not to the height afterwards — so once this arms it changes
      // which jumps are detected at all and what airtimes they get. It is not
      // a post-hoc scale factor and it is NOT reversible offline. It needs only
      // 8 airborne observations at 200 Hz (~0.04 s) to commit, and wing riders
      // rotate, so it WILL arm early in a real session. Meanwhile the value has
      // no persistence key (a battery blip re-arms it to a different number)
      // and appears on no protocol line, so afterwards nobody can tell which
      // jumps were corrected or by how much. The correction has zero silicon
      // time (STATUS.md).
      //
      // For the session we want the exact detector that was validated in sim.
      // Re-enable deliberately once there is water data to validate against,
      // and give it a persistence key and a wire field at the same time.
      if (JH_SPIN_SELFARM_ENABLED) {
        if (lever_arm.commit()) detector.set_spin_lever_m(lever_arm.value());
      } else {
        lever_arm.discard();
      }
    } else {
      lever_arm.discard();
      airReset();          // rejected flight: its samples must not leak forward
    }
  }
  if (jumped) {
    session_jumps++;
    if (ev.height_m > session_best) session_best = ev.height_m;
    // stored_jumps/stored_best move only if the store actually kept it —
    // updated after logJump() below (F-10).
    emitf("JUMP n=%lu airtime_raw_s=%.3f airtime_s=%.3f height_m=%.3f height_ft=%.1f best_m=%.3f\n",
          (unsigned long)session_jumps, ev.airtime_raw_s, ev.airtime_s,
          ev.height_m, ev.height_m * JH_M_TO_FT, session_best);
    // Medians over the flight just ended. The corrected value subtracts the
    // rotation term the detector itself uses, so it is directly comparable to
    // the sim's predicted 0-0.07 g band; the raw one is kept because it is
    // what the trace shows and the two disagreeing is itself informative.
    const uint16_t med_a  = medianOf(s_air_a_mg, s_air_n);
    const uint16_t med_w  = medianOf(s_air_w_dps, s_air_n);
    const float    r_m    = detector.spin_lever_m();
    const float    w_rad  = med_w * 0.017453293f;
    const float    corr_g = (w_rad * w_rad * r_m) / 9.80665f;   // omega^2*r
    float          acorr  = med_a / 1000.0f - corr_g;
    if (acorr < 0.0f) acorr = 0.0f;
    if (logJump(ev, med_a, med_w, (uint16_t)(acorr * 1000.0f + 0.5f), s_air_n)) {
      stored_jumps++;
      if (ev.height_m > stored_best) stored_best = ev.height_m;
    }
    emitf("# flight n=%lu med_a=%.3fg med_w=%udps med_acorr=%.3fg n_air=%u\n",
          (unsigned long)session_jumps, med_a / 1000.0, (unsigned)med_w,
          (double)acorr, (unsigned)s_air_n);
    airReset();
  } else if (detector.last_reject() == jump::Detector::Reject::TOO_SHORT) {
    // Narrate near-misses: silence during a desk test is undebuggable.
    emitf("# almost a jump: %.2fs of air — under the %.2fs minimum. Toss higher.\n",
          detector.last_reject_airtime(), (double)JH_MIN_AIRTIME_S);
  } else if (detector.last_reject() == jump::Detector::Reject::NO_LANDING) {
    emitf("# free-fall seen but no landing spike over %.1fg — landing too soft, "
          "or caught mid-air?\n", (double)JH_LANDING_THRESHOLD_G);
  }

  // --- decimated trace logging, buffered; flushed ~once/second ---
  if (fs_ok && !jh_store::trace_is_full() && ++decimate_ctr >= LOG_DECIMATE) {
    decimate_ctr = 0;
    trace_buf += String(t, 3);
    trace_buf += ',';
    trace_buf += String(mag, 3);
    trace_buf += '\n';
    if (now_ms - last_flush_ms > 1000) {
      flushTrace();  // does the byte accounting + cap check
      last_flush_ms = now_ms;
    }
  }
}
