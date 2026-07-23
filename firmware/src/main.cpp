// Jump Height — ESP32 firmware (FireBeetle 2 ESP32-E field board)
//
// What it does (see DECISIONS.md / BUILD.md):
//   * Samples the MPU-6050 at JH_SAMPLE_HZ over I2C (raw register driver —
//     tolerant of clone chips) and runs the airtime jump detector live.
//   * Motion gate: only detects/logs while the board is actually moving.
//   * Logs to built-in flash (LittleFS):
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
// Service so a phone/laptop can read jumps and send commands wirelessly. Every
// line above goes out on BOTH USB serial and (when a client is subscribed) the
// BLE TX characteristic, via the emit layer below; the BLE stack lives in
// include/ble_link.h. A BLE failure is reported by the self-test's `ble` row but
// never blocks jump detection — v1 still reads over USB.
//
// All tunables come from config/params.json via the generated params.gen.h.
//
// SPDX-License-Identifier: MIT

#include <Arduino.h>
#include <Wire.h>
#include <FS.h>
#include <LittleFS.h>
#include <esp_timer.h>
#include <stdarg.h>
#include <string.h>
#include "params.gen.h"
#include "mpu6050_min.h"
#include "jump_detector.h"
#include "ble_link.h"

#define FW_VERSION "0.3.0"

static const float    G                  = JH_G;
static const uint32_t SAMPLE_INTERVAL_US = 1000000UL / JH_SAMPLE_HZ;
static const int      LOG_DECIMATE       = JH_SAMPLE_HZ / JH_LOG_HZ;
static const uint32_t IDLE_TIMEOUT_MS    = (uint32_t)JH_IDLE_TIMEOUT_S * 1000UL;

static const char* TRACE_PATH = "/trace.csv";
static const char* JUMPS_PATH = "/jumps.csv";

Mpu6050Min     imu;
jump::Detector detector;

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
// jump in flight at the wrap instant). esp_timer_get_time() never wraps.
static int64_t  t0_us         = 0;

// Motion gate. motion_seen keeps the gate idle from power-on until the first
// real over-threshold sample — without it, (now_ms - 0) < timeout reads as
// "active" at boot and the desktest shake step can never see a transition.
static uint32_t last_motion_ms = 0;
static bool     motion_seen    = false;
static bool     active         = false;

// Trace buffering (keeps slow flash writes off the sampling path)
static String   trace_buf;
static uint32_t trace_bytes   = 0;
static bool     trace_full    = false;
static bool     trace_header  = false;
static bool     jumps_header  = false;
static int      decimate_ctr  = 0;
static uint32_t last_flush_ms = 0;

// Non-blocking serial command assembly
static String cmd_buf;

// ---------------- Protocol output (emit layer) ----------------
// Single choke point for ALL protocol output. Every line goes to USB serial and,
// when a BLE client is subscribed, to the Nordic UART TX characteristic — same
// bytes on both transports. Chatter (`#`), hints, machine lines, and FILE dumps
// all pass through here. Nothing else in this file should call Serial.print*
// directly. Only ever called from loop()/setup() (never a NimBLE callback), so
// the BLE notify path has no cross-task contention (see ble_link.h).
static void emitBytes(const char* data, size_t len) {
  Serial.write((const uint8_t*)data, len);
  ble_link::write(data, len);
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
// live. BLE-only on purpose (not through emit): it's a per-connection greeting,
// and re-emitting READY onto USB could confuse a serial session mid-command.
static void bleGreet() {
  static const char banner[] = "# JumpHeight fw v" FW_VERSION "\n";
  ble_link::write(banner, sizeof(banner) - 1);
  ble_link::write("READY\n", 6);
}

// ---------------- Storage ----------------
static void flushTrace() {
  if (!fs_ok || trace_buf.length() == 0) return;
  File f = LittleFS.open(TRACE_PATH, FILE_APPEND);
  if (f) {
    if (!trace_header) {
      f.print("t,mag\n");
      trace_header = true;
      trace_bytes += 6;  // count the header too: STATS trace_bytes sizes the dump
    }
    f.print(trace_buf);
    f.close();
    // Account and enforce the cap here so every flush path (loop, idle
    // transition, serial commands) counts — not just the loop's.
    trace_bytes += trace_buf.length();
    if (trace_bytes >= JH_TRACE_MAX_BYTES && !trace_full) {
      trace_full = true;
      emitLine("# trace log full — still counting jumps. `dump` then `clear` to reset.");
    }
  }
  trace_buf = "";
}

static void logJump(const jump::JumpEvent& ev) {
  if (!fs_ok) return;
  File f = LittleFS.open(JUMPS_PATH, FILE_APPEND);
  if (f) {
    if (!jumps_header) { f.print("n,takeoff_s,airtime_raw_s,airtime_s,height_m\n"); jumps_header = true; }
    f.printf("%lu,%.3f,%.3f,%.3f,%.3f\n", (unsigned long)stored_jumps,
             ev.takeoff_time_s, ev.airtime_raw_s, ev.airtime_s, ev.height_m);
    f.close();
  }
}

static void printFileFramed(const char* path, const char* name) {
  emitf("FILE %s BEGIN\n", name);
  if (fs_ok) {
    File f = LittleFS.open(path, FILE_READ);
    if (f) {
      // Read in blocks (not byte-by-byte): far fewer BLE notifications, and the
      // emit layer chunks each block to the MTU. Notify back-pressure/pacing is
      // handled inside ble_link::write, so a long BLE dump self-throttles.
      uint8_t block[240];
      while (f.available()) {
        size_t n = f.read(block, sizeof(block));
        if (n == 0) break;
        emitBytes((const char*)block, n);
      }
      f.close();
    }
  }
  emitf("FILE %s END\n", name);
}

static void scanStoredJumps() {
  stored_jumps = 0;
  stored_best  = 0.0f;
  if (!fs_ok) return;
  File f = LittleFS.open(JUMPS_PATH, FILE_READ);
  if (!f) return;
  f.readStringUntil('\n');  // header
  while (f.available()) {
    String line = f.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) continue;
    int c = line.lastIndexOf(',');
    if (c < 0) continue;
    float h = line.substring(c + 1).toFloat();
    stored_jumps++;
    if (h > stored_best) stored_best = h;
  }
  f.close();
}

// ---------------- Self-test ----------------
// Prints machine-readable results plus plain-English hints, and (re)initializes
// the sensor. Safe to run repeatedly via the `selftest` command.
static bool runSelfTest() {
  emitLine("SELFTEST BEGIN");
  bool all_ok = true;

  // 1. Is anything answering on the I2C bus?
  uint8_t addr = 0;
  if (Mpu6050Min::probe(Wire, Mpu6050Min::ADDR_PRIMARY))        addr = Mpu6050Min::ADDR_PRIMARY;
  else if (Mpu6050Min::probe(Wire, Mpu6050Min::ADDR_SECONDARY)) addr = Mpu6050Min::ADDR_SECONDARY;

  bool imu_up = false;
  if (addr == 0) {
    emitLine("SELFTEST i2c FAIL detail=no_device");
    emitLine("# hint: no sensor found. Check the 4 wires: sensor VCC->3V3 (NOT the");
    emitLine("# hint: pin marked VCC — it carries ~4.7V), GND->GND, SDA->SDA,");
    emitLine("# hint: SCL->SCL. Swapped SDA/SCL is the #1 cause; loose jumper is #2.");
    all_ok = false;
  } else {
    emitf("SELFTEST i2c PASS detail=0x%02X\n", addr);
    imu_up = imu.begin(Wire, addr);
    if (!imu_up) {
      emitLine("SELFTEST config FAIL detail=write_error");
      emitLine("# hint: device answers but register writes fail — usually a flaky");
      emitLine("# hint: wire or bad solder joint. Re-check connections.");
      all_ok = false;
    } else {
      const uint8_t who = imu.whoAmI();
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
      if (imu.readAccelG(ax, ay, az)) {
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
      } else {
        emitf("SELFTEST accel FAIL detail=%.3fg\n", mean);
        emitLine("# hint: should read ~1.0g sitting still. Keep the device still on a");
        emitLine("# hint: table during self-test, and check VCC is on 3V3.");
        all_ok = false;
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
    emitf("SELFTEST flash PASS detail=%uB_free\n",
          (unsigned)(LittleFS.totalBytes() - LittleFS.usedBytes()));
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
  emitLine("# commands: help | stats | jumps | trace | dump | clear | selftest | info");
}

// Handles one command line from EITHER transport (serial pollSerial() or BLE
// ble_link::poll()). Both run on the loop() task, one at a time, so a command is
// processed exactly once; its output goes to both transports via the emit layer.
static void handleCommand(const String& cmd) {
  if (cmd == "help") {
    printHelp();
    emitLine("OK help");
  } else if (cmd == "stats") {
    flushTrace();  // so trace_bytes matches what a `dump` would actually deliver
    emitf("STATS session_jumps=%lu session_best_m=%.3f stored_jumps=%lu stored_best_m=%.3f trace_bytes=%lu\n",
          (unsigned long)session_jumps, session_best,
          (unsigned long)stored_jumps, stored_best, (unsigned long)trace_bytes);
    emitLine("OK stats");
  } else if (cmd == "jumps") {
    flushTrace();
    printFileFramed(JUMPS_PATH, "jumps.csv");
    emitLine("OK jumps");
  } else if (cmd == "trace") {
    flushTrace();
    printFileFramed(TRACE_PATH, "trace.csv");
    emitLine("OK trace");
  } else if (cmd == "dump") {
    flushTrace();
    printFileFramed(JUMPS_PATH, "jumps.csv");
    printFileFramed(TRACE_PATH, "trace.csv");
    emitLine("OK dump");
  } else if (cmd == "clear") {
    if (fs_ok) {
      LittleFS.remove(TRACE_PATH);
      LittleFS.remove(JUMPS_PATH);
    }
    trace_buf = ""; trace_bytes = 0; trace_full = false;
    trace_header = false; jumps_header = false;
    stored_jumps = 0; stored_best = 0.0f;
    emitLine("# cleared stored data");
    emitLine("OK clear");
  } else if (cmd == "selftest") {
    runSelfTest();
    emitLine("OK selftest");
  } else if (cmd == "info") {
    // ble=1 advertises the capability (this firmware speaks BLE); the runtime
    // health of the radio is the self-test's `ble` row, not this flag.
    emitf("INFO fw=%s sample_hz=%d log_hz=%d ble=1\n", FW_VERSION, JH_SAMPLE_HZ, JH_LOG_HZ);
    emitLine("PARAMS " JH_PARAMS_SUMMARY);
    emitLine("OK info");
  } else {
    emitf("ERR unknown_command %s\n", cmd.c_str());
    printHelp();
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
  Serial.begin(115200);
  delay(300);
  emitLine("# JumpHeight fw v" FW_VERSION);  // serial-only here: BLE isn't up yet

  Wire.begin(JH_I2C_SDA, JH_I2C_SCL);
  Wire.setClock(400000);

  // Mount the data partition. Our table (partitions.csv) names it "littlefs",
  // not the ESP32 default "spiffs", so the label must be passed explicitly or
  // the mount silently fails. Other args are the library defaults.
  // First boot ever formats the 2.4 MB partition — tens of seconds of silence
  // that reads as a hang unless announced (a real builder sat through it):
  // try the plain mount first, and only format (with a heads-up) if it fails.
  fs_ok = LittleFS.begin(false, "/littlefs", 10, "littlefs");
  if (!fs_ok) {
    emitLine("# first boot: formatting storage — takes up to a minute, hang tight...");
    fs_ok = LittleFS.begin(true, "/littlefs", 10, "littlefs");  // format + mount
    emitLine(fs_ok ? "# storage ready" : "# storage format failed");
  }
  if (fs_ok) {
    File f = LittleFS.open(TRACE_PATH, FILE_READ);
    if (f) {
      trace_bytes  = f.size();
      trace_header = trace_bytes > 0;
      if (trace_bytes >= JH_TRACE_MAX_BYTES) trace_full = true;
      f.close();
    }
    File jf = LittleFS.open(JUMPS_PATH, FILE_READ);
    if (jf) { jumps_header = jf.size() > 0; jf.close(); }
  }

  // Bring BLE up before the self-test so the `ble` row reflects the real result.
  // A failure is non-fatal: everything below (and jump detection) runs regardless.
  ble_ok = ble_link::begin("JumpHeight");

  runSelfTest();
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
  t0_us         = esp_timer_get_time();
  last_flush_ms = millis();
}

// ---------------- Loop ----------------
void loop() {
  pollSerial();
  ble_link::poll(handleCommand);  // BLE commands run through the same path as serial
  if (ble_link::takeGreetPending()) bleGreet();  // greet a client that just subscribed
  if (!sensor_ok) { delay(10); return; }  // command loop still runs; sampling paused

  static int64_t next_us = esp_timer_get_time();
  const int64_t  now_us  = esp_timer_get_time();
  if (now_us < next_us) return;  // pace to SAMPLE_HZ
  next_us += SAMPLE_INTERVAL_US;
  // After a long stall (e.g. a 100 s serial dump) don't "catch up" with a
  // burst of thousands of back-to-back samples — resynchronize instead.
  if (now_us - next_us > 20 * (int64_t)SAMPLE_INTERVAL_US) next_us = now_us;

  float ax, ay, az;
  if (!imu.readAccelG(ax, ay, az)) return;  // transient I2C hiccup: skip sample
  const float t   = (now_us - t0_us) * 1e-6f;
  const float mag = sqrtf(ax * ax + ay * ay + az * az);  // orientation-independent

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
  jump::JumpEvent ev;
  if (detector.update(t, mag, ev)) {
    session_jumps++;
    stored_jumps++;
    if (ev.height_m > session_best) session_best = ev.height_m;
    if (ev.height_m > stored_best)  stored_best  = ev.height_m;
    emitf("JUMP n=%lu airtime_raw_s=%.3f airtime_s=%.3f height_m=%.3f height_ft=%.1f best_m=%.3f\n",
          (unsigned long)session_jumps, ev.airtime_raw_s, ev.airtime_s,
          ev.height_m, ev.height_m * 3.28084f, session_best);
    logJump(ev);
  }

  // --- decimated trace logging, buffered; flushed ~once/second ---
  if (fs_ok && !trace_full && ++decimate_ctr >= LOG_DECIMATE) {
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
