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
//       jumps.csv — one line per jump (n,takeoff_s,airtime_raw_s,airtime_s,height_m)
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
//   STATS session_jumps=.. session_best_m=.. stored_jumps=.. stored_best_m=.. trace_bytes=..
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
#include "gyro_bias.h"
#include "jump_detector.h"
#include "lever_arm.h"
#include "platform/jh_clock.h"
#include "platform/jh_imu.h"
#include "platform/jh_link.h"
#include "platform/jh_persist.h"
#include "platform/jh_power.h"
#include "platform/jh_store.h"

#define FW_VERSION "0.4.3"

static const float    G                  = JH_G;
static const uint32_t SAMPLE_INTERVAL_US = 1000000UL / JH_SAMPLE_HZ;
static const int      LOG_DECIMATE       = JH_SAMPLE_HZ / JH_LOG_HZ;
static const uint32_t IDLE_TIMEOUT_MS    = (uint32_t)JH_IDLE_TIMEOUT_S * 1000UL;

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
static bool ble_ok    = false;  // BLE stack came up; reported by the self-test

// Session stats (since this power-up) + stored stats (across power-ups)
static uint32_t session_jumps = 0;
static float    session_best  = 0.0f;
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
  static const char banner[] = "# JumpHeight fw v" FW_VERSION "\n";
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

static void logJump(const jump::JumpEvent& ev) {
  if (!fs_ok) return;
  jh_store::jumps_append(stored_jumps, ev.takeoff_time_s, ev.airtime_raw_s,
                          ev.airtime_s, ev.height_m);
}

static void printFileFramed(jh_store::StoredFile which, const char* name) {
  emitf("FILE %s BEGIN\n", name);
  if (jh_store::open_read(which)) {
    // Read in blocks (not byte-by-byte): far fewer BLE notifications, and the
    // emit layer chunks each block to the MTU. Notify back-pressure/pacing is
    // handled inside jh_link::write, so a long BLE dump self-throttles.
    uint8_t block[240];
    size_t n;
    while ((n = jh_store::read_chunk(block, sizeof(block))) > 0) {
      emitBytes((const char*)block, n);
    }
    jh_store::close_read();
  }
  emitf("FILE %s END\n", name);
}

static void scanStoredJumps() {
  stored_jumps = 0;
  stored_best  = 0.0f;
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
      if (who == 0x68) {
        emitf("SELFTEST whoami PASS detail=0x%02X\n", who);
      } else {
        // Clone chips report odd IDs but usually work fine — warn, don't fail.
        emitf("SELFTEST whoami WARN detail=0x%02X\n", who);
        emitLine("# hint: unexpected chip ID — likely a clone MPU-6050. Usually fine;");
        emitLine("# hint: the accel/noise checks below are what actually matter.");
      }
    }
  }

  // 2. Does the accelerometer read ~1 g sitting still?
  if (imu_up) {
    float sum = 0, sumsq = 0;
    int   good = 0;
    const int N = 100;
    for (int i = 0; i < N; ++i) {
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
  emitLine("# commands: help | stats | jumps | trace | dump | clear | selftest | revive | info | off | dfu | uf2 | fakejump | mount | format");
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
    if (vbat >= 0) {
      emitf("STATS session_jumps=%lu session_best_m=%.3f stored_jumps=%lu stored_best_m=%.3f trace_bytes=%lu vbat_mv=%d batt_pct=%d chg=%d%s%s\n",
            (unsigned long)session_jumps, session_best,
            (unsigned long)stored_jumps, stored_best, (unsigned long)jh_store::trace_bytes(),
            vbat, jh_power::batt_pct(), jh_power::charging(), fs_key, fail_key);
    } else {
      emitf("STATS session_jumps=%lu session_best_m=%.3f stored_jumps=%lu stored_best_m=%.3f trace_bytes=%lu%s%s\n",
            (unsigned long)session_jumps, session_best,
            (unsigned long)stored_jumps, stored_best, (unsigned long)jh_store::trace_bytes(),
            fs_key, fail_key);
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
  } else if (cmd == "dump") {
    flushTrace();
    printFileFramed(jh_store::StoredFile::JUMPS, "jumps.csv");
    printFileFramed(jh_store::StoredFile::TRACE, "trace.csv");
    emitLine("OK dump");
  } else if (cmd == "clear") {
    jh_store::clear();  // internally gated on fs_ok; resets byte count/cap/headers
    trace_buf = "";
    stored_jumps = 0; stored_best = 0.0f;
    emitLine("# cleared stored data");
    emitLine("OK clear");
  } else if (cmd == "selftest") {
    runSelfTest();
    emitLine("OK selftest");
  } else if (cmd == "revive") {
    // Clean sensor power-cycle (16g sequencing) then a full retry — the
    // recovery for power-up-corrupted-but-undamaged silicon. Bench command;
    // safe to repeat, ~0.7 s of deliberate delays inside.
    if (!jh_imu::revive()) {
      emitLine("ERR revive_unsupported no sensor rail on this platform");
    } else {
      emitLine("# rail cycled clean (bus floated first) — retrying selftest");
      runSelfTest();
      emitLine("OK revive");
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
    // ble=1 advertises the capability (this firmware speaks BLE); the runtime
    // health of the radio is the self-test's `ble` row, not this flag.
    // Battery keys appended only where measurable — same adder rule as STATS.
    const int vbat = jh_power::vbat_mv();
    if (vbat >= 0) {
      emitf("INFO fw=%s sample_hz=%d log_hz=%d motion_thresh_g=%.2f "
            "idle_timeout_s=%d ble=1 vbat_mv=%d batt_pct=%d chg=%d\n",
            FW_VERSION, JH_SAMPLE_HZ, JH_LOG_HZ,
            (double)JH_MOTION_THRESH_G, (int)JH_IDLE_TIMEOUT_S,
            vbat, jh_power::batt_pct(), jh_power::charging());
    } else {
      emitf("INFO fw=%s sample_hz=%d log_hz=%d motion_thresh_g=%.2f "
            "idle_timeout_s=%d ble=1\n", FW_VERSION, JH_SAMPLE_HZ, JH_LOG_HZ,
            (double)JH_MOTION_THRESH_G, (int)JH_IDLE_TIMEOUT_S);
    }
    emitLine("PARAMS " JH_PARAMS_SUMMARY);
    // Effective calibration (PARAMS above shows compiled defaults).
    // vbat_scale appended only when it is doing something (!= 1.0), keeping
    // the adder-key rule: a board with nominal divider resistors emits the
    // exact line every existing client already parses.
    if (cal_vbat_scale != 1.0f) {
      emitf("CAL airtime_offset_s=%.4f height_scale=%.3f source=%s vbat_scale=%.4f\n",
            detector.params().airtime_offset_s, detector.params().height_scale,
            cal_from_nvs ? "device" : "defaults", cal_vbat_scale);
    } else {
      emitf("CAL airtime_offset_s=%.4f height_scale=%.3f source=%s\n",
            detector.params().airtime_offset_s, detector.params().height_scale,
            cal_from_nvs ? "device" : "defaults");
    }
    emitLine("OK info");
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
    emitf("JUMP n=%lu airtime_raw_s=%.3f airtime_s=%.3f height_m=%.3f height_ft=%.1f best_m=%.3f\n",
          (unsigned long)session_jumps, at, at, h_m, h_m * 3.28084f, session_best);
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
  emitLine("# JumpHeight fw v" FW_VERSION);  // serial-only here: BLE isn't up yet

  jh_imu::init();
  jh_power::init();

  // Persist first — it's INTERNAL flash (no external bus, nothing to wedge)
  // and both crash guards live in it, so it must be readable before any
  // external-bus first contact.
  jh_persist::init();
  loadCalibration();

  // Mount storage — BRACKETED by the sticky store guard (2026-08-12: the
  // mule's wedged QSPI chip hung this call before BLE ever started; found
  // by the neuter-and-bisect method the sensor probe taught us). A hang
  // costs one watchdog reset; every boot after skips the mount and comes up
  // alive with an honest `flash FAIL` row. The `format` command is the
  // deliberate retry — and if THAT hangs, the same guard catches it again.
  if (jh_persist::load(jh_persist::Key::StoreGuard, 0.0f) > 0.5f) {
    emitLine("# storage: skipped (previous boot hung in the mount — `mount` retries safely, `format` rebuilds)");
    fs_ok = false;
  } else {
    jh_persist::save(jh_persist::Key::StoreGuard, 1.0f);
    fs_ok = jh_store::init(emitLine);
    jh_persist::save(jh_persist::Key::StoreGuard, 0.0f);
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
  if (jh_link::takeGreetPending()) bleGreet();  // greet a client that just subscribed
  if (!sensor_ok) { delay(10); return; }  // command loop still runs; sampling paused

  static int64_t next_us = jh_clock::micros64();
  const int64_t  now_us  = jh_clock::micros64();
  if (now_us < next_us) return;  // pace to SAMPLE_HZ
  next_us += SAMPLE_INTERVAL_US;
  // After a long stall (e.g. a 100 s serial dump) don't "catch up" with a
  // burst of thousands of back-to-back samples — resynchronize instead.
  if (now_us - next_us > 20 * (int64_t)SAMPLE_INTERVAL_US) next_us = now_us;

  float ax, ay, az;
  if (!jh_imu::read_accel_g(ax, ay, az)) {
    // Skipping the sample is right for a transient I2C hiccup; staying quiet
    // about a dead IMU is not. One warning, once, so a live client sees it
    // without the line repeating at the sample rate.
    if (++accel_fail_count == 200 && !sensor_warned) {  // ~1 s at 200 Hz
      sensor_warned = true;
      emitLine("# WARNING accelerometer not answering — this session is "
               "recording nothing. Check wiring, then `selftest`.");
    }
    return;
  }
  const float t   = (now_us - t0_us) * 1e-6f;
  // Orientation-independent, normalized to this unit's own measured gravity.
  const float mag = sqrtf(ax * ax + ay * ay + az * az) / g_baseline;

  // --- motion gate ---
  const uint32_t now_ms = millis();
  if (fabsf(mag - 1.0f) > JH_MOTION_THRESH_G) {
    last_motion_ms = now_ms;
    motion_seen    = true;
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
    if (was_airborne) lever_arm.observe(mag, omega_dps);

    jumped = detector.update(t, mag, omega_dps, ev);
  } else {
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
      if (lever_arm.commit()) detector.set_spin_lever_m(lever_arm.value());
    } else {
      lever_arm.discard();
    }
  }
  if (jumped) {
    session_jumps++;
    stored_jumps++;
    if (ev.height_m > session_best) session_best = ev.height_m;
    if (ev.height_m > stored_best)  stored_best  = ev.height_m;
    emitf("JUMP n=%lu airtime_raw_s=%.3f airtime_s=%.3f height_m=%.3f height_ft=%.1f best_m=%.3f\n",
          (unsigned long)session_jumps, ev.airtime_raw_s, ev.airtime_s,
          ev.height_m, ev.height_m * 3.28084f, session_best);
    logJump(ev);
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
