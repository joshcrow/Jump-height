// Jump Height — ESP32 firmware (FireBeetle v1 build)
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
// Serial protocol (115200 baud) — designed for the ./tools/jump CLI but human
// readable. Lines starting with `#` are chatter. Machine lines:
//   SELFTEST BEGIN / SELFTEST <name> PASS|WARN|FAIL|SKIP detail=<v> / SELFTEST END result=...
//   READY                      — boot complete
//   STATE recording|idle       — motion gate transitions
//   JUMP n=.. airtime_raw_s=.. airtime_s=.. height_m=.. height_ft=.. best_m=..
//   STATS session_jumps=.. session_best_m=.. stored_jumps=.. stored_best_m=..
//   INFO fw=.. sample_hz=.. / PARAMS <key=value ...>
//   FILE <name> BEGIN ... FILE <name> END
//   OK <cmd> | ERR <detail>    — every typed command finishes with one of these
// Commands: help stats jumps trace dump clear selftest info
//
// All tunables come from config/params.json via the generated params.gen.h.
//
// SPDX-License-Identifier: MIT

#include <Arduino.h>
#include <Wire.h>
#include <FS.h>
#include <LittleFS.h>
#include <esp_timer.h>
#include "params.gen.h"
#include "mpu6050_min.h"
#include "jump_detector.h"

#define FW_VERSION "0.2.0"

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

// ---------------- Storage ----------------
static void flushTrace() {
  if (!fs_ok || trace_buf.length() == 0) return;
  File f = LittleFS.open(TRACE_PATH, FILE_APPEND);
  if (f) {
    if (!trace_header) { f.print("t,mag\n"); trace_header = true; }
    f.print(trace_buf);
    f.close();
    // Account and enforce the cap here so every flush path (loop, idle
    // transition, serial commands) counts — not just the loop's.
    trace_bytes += trace_buf.length();
    if (trace_bytes >= JH_TRACE_MAX_BYTES && !trace_full) {
      trace_full = true;
      Serial.println("# trace log full — still counting jumps. `dump` then `clear` to reset.");
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
  Serial.printf("FILE %s BEGIN\n", name);
  if (fs_ok) {
    File f = LittleFS.open(path, FILE_READ);
    if (f) {
      while (f.available()) Serial.write(f.read());
      f.close();
    }
  }
  Serial.printf("FILE %s END\n", name);
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
  Serial.println("SELFTEST BEGIN");
  bool all_ok = true;

  // 1. Is anything answering on the I2C bus?
  uint8_t addr = 0;
  if (Mpu6050Min::probe(Wire, Mpu6050Min::ADDR_PRIMARY))        addr = Mpu6050Min::ADDR_PRIMARY;
  else if (Mpu6050Min::probe(Wire, Mpu6050Min::ADDR_SECONDARY)) addr = Mpu6050Min::ADDR_SECONDARY;

  bool imu_up = false;
  if (addr == 0) {
    Serial.println("SELFTEST i2c FAIL detail=no_device");
    Serial.println("# hint: no sensor found. Check the 4 wires: VCC->3V3 (NOT 5V pin if");
    Serial.println("# hint: unsure), GND->GND, SDA->SDA, SCL->SCL. Swapped SDA/SCL is the");
    Serial.println("# hint: #1 cause. Loose breadboard/jumper contact is #2.");
    all_ok = false;
  } else {
    Serial.printf("SELFTEST i2c PASS detail=0x%02X\n", addr);
    imu_up = imu.begin(Wire, addr);
    if (!imu_up) {
      Serial.println("SELFTEST config FAIL detail=write_error");
      Serial.println("# hint: device answers but register writes fail — usually a flaky");
      Serial.println("# hint: wire or bad solder joint. Re-check connections.");
      all_ok = false;
    } else {
      const uint8_t who = imu.whoAmI();
      if (who == 0x68) {
        Serial.printf("SELFTEST whoami PASS detail=0x%02X\n", who);
      } else {
        // Clone chips report odd IDs but usually work fine — warn, don't fail.
        Serial.printf("SELFTEST whoami WARN detail=0x%02X\n", who);
        Serial.println("# hint: unexpected chip ID — likely a clone MPU-6050. Usually fine;");
        Serial.println("# hint: the accel/noise checks below are what actually matter.");
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
      Serial.println("SELFTEST accel FAIL detail=read_errors");
      Serial.println("# hint: reads are failing intermittently — flaky wiring.");
      all_ok = false;
    } else {
      const float mean = sum / good;
      const float var  = sumsq / good - mean * mean;
      const float sd   = var > 0 ? sqrtf(var) : 0;
      if (mean > 0.8f && mean < 1.2f) {
        Serial.printf("SELFTEST accel PASS detail=%.3fg\n", mean);
      } else {
        Serial.printf("SELFTEST accel FAIL detail=%.3fg\n", mean);
        Serial.println("# hint: should read ~1.0g sitting still. Keep the device still on a");
        Serial.println("# hint: table during self-test, and check VCC is on 3V3.");
        all_ok = false;
      }
      if (sd < 0.03f) {
        Serial.printf("SELFTEST noise PASS detail=%.4fg\n", sd);
      } else if (sd < 0.08f) {
        Serial.printf("SELFTEST noise WARN detail=%.4fg\n", sd);
        Serial.println("# hint: noisier than expected — vibration or a marginal clone.");
        Serial.println("# hint: OK to proceed; watch for false jumps in the desk test.");
      } else {
        Serial.printf("SELFTEST noise FAIL detail=%.4fg\n", sd);
        Serial.println("# hint: far too noisy. Was the device moving? Re-run `selftest`");
        Serial.println("# hint: with it resting on a table. If still failing, try another");
        Serial.println("# hint: MPU board (you bought spares for exactly this).");
        all_ok = false;
      }
    }
  } else {
    Serial.println("SELFTEST accel SKIP detail=no_sensor");
    Serial.println("SELFTEST noise SKIP detail=no_sensor");
  }

  // 3. Storage.
  if (fs_ok) {
    Serial.printf("SELFTEST flash PASS detail=%uB_free\n",
                  (unsigned)(LittleFS.totalBytes() - LittleFS.usedBytes()));
  } else {
    Serial.println("SELFTEST flash FAIL detail=mount_failed");
    Serial.println("# hint: flash storage didn't mount; jumps will print live but won't be");
    Serial.println("# hint: saved. Re-flash with `./tools/jump flash` (it formats storage).");
    all_ok = false;
  }

  // Sampling only needs a working IMU: a flash or accel-range failure still
  // leaves the device usable for live detection, and `selftest` can re-probe.
  sensor_ok = imu_up;
  Serial.printf("SELFTEST END result=%s\n", all_ok ? "PASS" : "FAIL");
  return all_ok;
}

// ---------------- Commands ----------------
static void printHelp() {
  Serial.println("# commands: help | stats | jumps | trace | dump | clear | selftest | info");
}

static void handleCommand(const String& cmd) {
  if (cmd == "help") {
    printHelp();
    Serial.println("OK help");
  } else if (cmd == "stats") {
    Serial.printf("STATS session_jumps=%lu session_best_m=%.3f stored_jumps=%lu stored_best_m=%.3f\n",
                  (unsigned long)session_jumps, session_best,
                  (unsigned long)stored_jumps, stored_best);
    Serial.println("OK stats");
  } else if (cmd == "jumps") {
    flushTrace();
    printFileFramed(JUMPS_PATH, "jumps.csv");
    Serial.println("OK jumps");
  } else if (cmd == "trace") {
    flushTrace();
    printFileFramed(TRACE_PATH, "trace.csv");
    Serial.println("OK trace");
  } else if (cmd == "dump") {
    flushTrace();
    printFileFramed(JUMPS_PATH, "jumps.csv");
    printFileFramed(TRACE_PATH, "trace.csv");
    Serial.println("OK dump");
  } else if (cmd == "clear") {
    if (fs_ok) {
      LittleFS.remove(TRACE_PATH);
      LittleFS.remove(JUMPS_PATH);
    }
    trace_buf = ""; trace_bytes = 0; trace_full = false;
    trace_header = false; jumps_header = false;
    stored_jumps = 0; stored_best = 0.0f;
    Serial.println("# cleared stored data");
    Serial.println("OK clear");
  } else if (cmd == "selftest") {
    runSelfTest();
    Serial.println("OK selftest");
  } else if (cmd == "info") {
    Serial.printf("INFO fw=%s sample_hz=%d log_hz=%d\n", FW_VERSION, JH_SAMPLE_HZ, JH_LOG_HZ);
    Serial.println("PARAMS " JH_PARAMS_SUMMARY);
    Serial.println("OK info");
  } else {
    Serial.printf("ERR unknown_command %s\n", cmd.c_str());
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
  Serial.println("# JumpHeight fw v" FW_VERSION);

  Wire.begin(JH_I2C_SDA, JH_I2C_SCL);
  Wire.setClock(400000);

  fs_ok = LittleFS.begin(true);  // format on first use
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

  runSelfTest();
  scanStoredJumps();
  if (stored_jumps > 0) {
    Serial.printf("# stored history: %lu jumps, best %.2f m — `dump` to export, `clear` to reset\n",
                  (unsigned long)stored_jumps, stored_best);
  }
  if (!sensor_ok) {
    Serial.println("# sensor not working — fix wiring, then type `selftest` (no re-flash needed)");
  }
  printHelp();
  Serial.println("READY");

  trace_buf.reserve(2048);
  t0_us         = esp_timer_get_time();
  last_flush_ms = millis();
}

// ---------------- Loop ----------------
void loop() {
  pollSerial();
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
  if (active && !was_active) Serial.println("STATE recording");
  if (!active && was_active) {
    Serial.println("STATE idle");
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
    Serial.printf("JUMP n=%lu airtime_raw_s=%.3f airtime_s=%.3f height_m=%.3f height_ft=%.1f best_m=%.3f\n",
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
