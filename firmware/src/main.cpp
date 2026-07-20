// Jump Height — ESP32 firmware (FireBeetle build)
//
// Behavior (matches the v1 design in DECISIONS.md):
//   * Samples an MPU-6050 at 200 Hz over I2C.
//   * Runs the airtime jump detector live (jump_detector.h) and prints each
//     jump over USB serial in plain English.
//   * "Motion gate": only records while the board is actually moving, so the
//     log doesn't fill with sitting-in-the-car time.
//   * Logs to the ESP32's built-in flash (LittleFS):
//       - jumps.csv : one line per detected jump (always kept — tiny)
//       - trace.csv : a compact 50 Hz trace of |acceleration| while moving,
//                     so you can re-tune the detector offline with sim/run.py
//                     and even see jumps the live detector missed.
//   * On land, open the capsule, plug in USB, and read the result:
//       - it prints a session summary on connect
//       - serial commands:  stats | trace | jumps | dump | clear | help
//
// This is written against the Arduino-ESP32 core + Adafruit MPU6050 lib
// (see platformio.ini). It has NOT been compiled on hardware yet — the desk
// test (BUILD.md, step 1) is where we shake it out.
//
// SPDX-License-Identifier: MIT

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <FS.h>
#include <LittleFS.h>
#include "jump_detector.h"

// ---------------- Configuration ----------------
#define I2C_SDA        21     // FireBeetle SDA — verify against your board's silkscreen
#define I2C_SCL        22     // FireBeetle SCL
#define SAMPLE_HZ      200    // live sampling + detector rate
#define LOG_HZ         50     // decimated rate written to trace.csv
#define ENABLE_LOGGING 1      // 0 = detect + serial only (handy for the first desk test)
#define ENABLE_BLE     0      // reserved for Phase 3 (live phone readout)

// Motion gate: the board is "in use" if we've felt a bump this recently.
static const float    MOTION_THRESH_G = 0.12f;    // |a|-1g bigger than this = motion
static const uint32_t IDLE_TIMEOUT_MS = 20000;    // go idle after this long calm

// Storage
static const char*    TRACE_PATH      = "/trace.csv";
static const char*    JUMPS_PATH      = "/jumps.csv";
static const uint32_t TRACE_MAX_BYTES = 1200000;  // ~30 min of moving time; protects flash

static const float    G                  = 9.80665f;
static const uint32_t SAMPLE_INTERVAL_US = 1000000UL / SAMPLE_HZ;
static const int      LOG_DECIMATE       = SAMPLE_HZ / LOG_HZ;

Adafruit_MPU6050 mpu;
jump::Detector   detector;

// Session stats (since this power-up)
static uint32_t jump_count  = 0;
static float    best_height = 0.0f;
static uint32_t t0_us       = 0;

// Motion gate state
static uint32_t last_motion_ms = 0;
static bool     active         = false;

// Trace write buffering (keeps slow flash writes off the 200 Hz sampling path)
static String   trace_buf;
static uint32_t trace_bytes   = 0;
static bool     trace_full    = false;
static bool     trace_header  = false;
static bool     jumps_header  = false;
static int      decimate_ctr  = 0;
static uint32_t last_flush_ms = 0;

// ---------------- Storage helpers ----------------
#if ENABLE_LOGGING
static void flushTrace() {
  if (trace_buf.length() == 0) return;
  File f = LittleFS.open(TRACE_PATH, FILE_APPEND);
  if (f) {
    if (!trace_header) { f.print("t,mag\n"); trace_header = true; }  // header for sim/run.py
    f.print(trace_buf);
    f.close();
  }
  trace_buf = "";
}

static void logJump(const jump::JumpEvent& ev) {
  File f = LittleFS.open(JUMPS_PATH, FILE_APPEND);
  if (f) {
    if (!jumps_header) { f.print("n,takeoff_s,airtime_s,height_m\n"); jumps_header = true; }
    f.printf("%lu,%.3f,%.3f,%.3f\n", (unsigned long)jump_count,
             ev.takeoff_time_s, ev.airtime_s, ev.height_m);
    f.close();
  }
}

static void printFileRaw(const char* path) {
  File f = LittleFS.open(path, FILE_READ);
  if (!f) { Serial.println("# (no data)"); return; }
  while (f.available()) Serial.write(f.read());
  f.close();
}

static void scanExistingJumps() {
  // On boot, summarize whatever is already stored so you see the history.
  File f = LittleFS.open(JUMPS_PATH, FILE_READ);
  if (!f) return;
  uint32_t n = 0; float best = 0.0f;
  f.readStringUntil('\n');  // skip header
  while (f.available()) {
    String line = f.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) continue;
    int c3 = line.lastIndexOf(',');
    if (c3 < 0) continue;
    float h = line.substring(c3 + 1).toFloat();
    n++; if (h > best) best = h;
  }
  f.close();
  if (n > 0) {
    Serial.printf("# Stored history: %lu jumps, best %.2f m (%.1f ft).\n",
                  (unsigned long)n, best, best * 3.28084f);
    Serial.println("#   send 'dump' to export, 'clear' to erase and start fresh.");
  }
}
#endif  // ENABLE_LOGGING

static void printHelp() {
  Serial.println("# commands: stats | trace | jumps | dump | clear | help");
}

static void handleSerial() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd.length() == 0) return;
  if (cmd == "stats") {
    Serial.printf("# This session: %lu jumps, best %.2f m (%.1f ft).\n",
                  (unsigned long)jump_count, best_height, best_height * 3.28084f);
  }
#if ENABLE_LOGGING
  else if (cmd == "trace") { flushTrace(); printFileRaw(TRACE_PATH); }
  else if (cmd == "jumps") { printFileRaw(JUMPS_PATH); }
  else if (cmd == "dump") {
    flushTrace();
    Serial.println("===== jumps.csv =====");
    printFileRaw(JUMPS_PATH);
    Serial.println("===== trace.csv =====");
    printFileRaw(TRACE_PATH);
    Serial.println("===== end =====");
  }
  else if (cmd == "clear") {
    LittleFS.remove(TRACE_PATH);
    LittleFS.remove(JUMPS_PATH);
    trace_bytes = 0; trace_full = false; trace_header = false; jumps_header = false;
    Serial.println("# Cleared stored data.");
  }
#endif
  else { printHelp(); }
}

// ---------------- Setup ----------------
void setup() {
  Serial.begin(115200);
  Serial.setTimeout(50);  // don't let a partial serial line stall sampling
  delay(300);

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000);
  if (!mpu.begin()) {
    Serial.println("MPU6050 not found — check wiring and I2C address (0x68/0x69).");
    while (true) delay(1000);
  }
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);   // capture landings without clipping
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_44_HZ);

#if ENABLE_LOGGING
  if (!LittleFS.begin(true)) {  // format on first use
    Serial.println("# LittleFS mount failed — logging disabled this run.");
  } else {
    File f = LittleFS.open(TRACE_PATH, FILE_READ);
    if (f) {
      trace_bytes  = f.size();
      trace_header = trace_bytes > 0;
      if (trace_bytes >= TRACE_MAX_BYTES) trace_full = true;
      f.close();
    }
    File jf = LittleFS.open(JUMPS_PATH, FILE_READ);
    if (jf) { jumps_header = jf.size() > 0; jf.close(); }
    scanExistingJumps();
  }
  trace_buf.reserve(2048);
#endif

  Serial.println("# Jump Height ready. Send it! 🌊  (idle until it feels motion)");
  printHelp();
  t0_us = micros();
  last_flush_ms = millis();
}

// ---------------- Loop ----------------
void loop() {
  handleSerial();

  static uint32_t next_us = micros();
  const uint32_t  now_us  = micros();
  if ((int32_t)(now_us - next_us) < 0) return;  // pace to SAMPLE_HZ (handles wraparound)
  next_us += SAMPLE_INTERVAL_US;

  sensors_event_t a, gyro, temp;
  mpu.getEvent(&a, &gyro, &temp);
  const float ax  = a.acceleration.x / G;
  const float ay  = a.acceleration.y / G;
  const float az  = a.acceleration.z / G;
  const float t   = (now_us - t0_us) * 1e-6f;
  const float mag = sqrtf(ax * ax + ay * ay + az * az);  // orientation-independent

  // --- motion gate ---
  const uint32_t now_ms = millis();
  if (fabsf(mag - 1.0f) > MOTION_THRESH_G) last_motion_ms = now_ms;
  const bool was_active = active;
  active = (now_ms - last_motion_ms) < IDLE_TIMEOUT_MS;
  if (active && !was_active) Serial.println("# ...motion — recording");
  if (!active && was_active) {
    Serial.println("# ...idle — paused");
#if ENABLE_LOGGING
    flushTrace();
#endif
  }
  if (!active) return;  // idle: don't detect or log

  // --- live jump detection (200 Hz) ---
  jump::JumpEvent ev;
  if (detector.update(t, mag, ev)) {
    jump_count++;
    if (ev.height_m > best_height) best_height = ev.height_m;
    Serial.printf("JUMP #%lu  airtime=%.2fs  height=%.2fm (%.1fft)  best=%.2fm\n",
                  (unsigned long)jump_count, ev.airtime_s, ev.height_m,
                  ev.height_m * 3.28084f, best_height);
#if ENABLE_LOGGING
    logJump(ev);
#endif
  }

  // --- decimated trace logging (50 Hz), buffered and flushed ~once/second ---
#if ENABLE_LOGGING
  if (!trace_full && ++decimate_ctr >= LOG_DECIMATE) {
    decimate_ctr = 0;
    trace_buf += String(t, 3);
    trace_buf += ',';
    trace_buf += String(mag, 3);
    trace_buf += '\n';
    if (now_ms - last_flush_ms > 1000) {
      trace_bytes += trace_buf.length();
      flushTrace();
      last_flush_ms = now_ms;
      if (trace_bytes >= TRACE_MAX_BYTES) {
        trace_full = true;
        Serial.println("# trace log full — still counting jumps. 'dump' then 'clear' to reset.");
      }
    }
  }
#endif
}
