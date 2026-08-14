// jh_store.cpp — ESP32 implementation of the jh_store seam
// (firmware/include/platform/jh_store.h). See docs/sense.md §3.9.
//
// LittleFS on the ESP32's internal flash — moved out of main.cpp verbatim
// (same mount args/partition label, same file paths, same CSV formats, same
// cap/header bookkeeping). See partitions.csv for why the partition label
// is "littlefs" (not the ESP32 default "spiffs") and must be passed
// explicitly.
//
// SPDX-License-Identifier: MIT

#include "platform/jh_store.h"

#include <FS.h>
#include <LittleFS.h>

#include "params.gen.h"

namespace jh_store {

static const char* TRACE_PATH = "/trace.csv";
static const char* JUMPS_PATH = "/jumps.csv";

static bool     s_fs_ok        = false;
static uint32_t s_trace_bytes  = 0;
static bool     s_trace_full   = false;
static bool     s_trace_header = false;
static bool     s_jumps_header = false;

// Single-slot sequential reader (open_read/read_chunk/close_read) — matches
// how main.cpp actually uses this: one FILE-framed dump at a time, never two
// reads interleaved.
static File s_read_file;

bool init(void (*announce)(const char* line)) {
  // First boot ever formats the 2.4 MB partition — tens of seconds of
  // silence that reads as a hang unless announced (a real builder sat
  // through it): try the plain mount first, and only format (with a
  // heads-up) if it fails.
  s_fs_ok = LittleFS.begin(false, "/littlefs", 10, "littlefs");
  if (!s_fs_ok) {
    announce("# first boot: formatting storage — takes up to a minute, hang tight...");
    s_fs_ok = LittleFS.begin(true, "/littlefs", 10, "littlefs");  // format + mount
    announce(s_fs_ok ? "# storage ready" : "# storage format failed");
  }
  if (s_fs_ok) {
    File f = LittleFS.open(TRACE_PATH, FILE_READ);
    if (f) {
      s_trace_bytes  = f.size();
      s_trace_header = s_trace_bytes > 0;
      if (s_trace_bytes >= JH_TRACE_MAX_BYTES) s_trace_full = true;
      f.close();
    }
    File jf = LittleFS.open(JUMPS_PATH, FILE_READ);
    if (jf) { s_jumps_header = jf.size() > 0; jf.close(); }
  }
  return s_fs_ok;
}

bool try_mount(void (*announce)(const char* line)) {
  // Non-destructive twin of init(): mount only, never format-on-fail.
  s_fs_ok = LittleFS.begin(false, "/littlefs", 10, "littlefs");
  if (!s_fs_ok) {
    announce("# mount: failed — NOT formatting (`format`/init would)");
    return false;
  }
  File f = LittleFS.open(TRACE_PATH, FILE_READ);
  if (f) {
    s_trace_bytes  = f.size();
    s_trace_header = s_trace_bytes > 0;
    if (s_trace_bytes >= JH_TRACE_MAX_BYTES) s_trace_full = true;
    f.close();
  }
  File jf = LittleFS.open(JUMPS_PATH, FILE_READ);
  if (jf) { s_jumps_header = jf.size() > 0; jf.close(); }
  return s_fs_ok;
}

bool ok() { return s_fs_ok; }

uint32_t free_bytes() {
  return (uint32_t)(LittleFS.totalBytes() - LittleFS.usedBytes());
}

void jumps_append(uint32_t n, float takeoff_s, float airtime_raw_s,
                   float airtime_s, float height_m, uint16_t, uint16_t,
                   uint16_t, uint16_t) {
  // v1 ESP32 board: the extended flight-physics columns are a Sense-only
  // measurement (it has no gyro), so they are accepted and ignored here.
  if (!s_fs_ok) return;
  File f = LittleFS.open(JUMPS_PATH, FILE_APPEND);
  if (f) {
    if (!s_jumps_header) {
      f.print("n,takeoff_s,airtime_raw_s,airtime_s,height_m\n");
      s_jumps_header = true;
    }
    f.printf("%lu,%.3f,%.3f,%.3f,%.3f\n", (unsigned long)n,
              takeoff_s, airtime_raw_s, airtime_s, height_m);
    f.close();
  }
}

void jumps_scan(uint32_t& count, float& best_m) {
  count  = 0;
  best_m = 0.0f;
  if (!s_fs_ok) return;
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
    count++;
    if (h > best_m) best_m = h;
  }
  f.close();
}

bool trace_append(const char* data, size_t len) {
  if (!s_fs_ok) return false;
  File f = LittleFS.open(TRACE_PATH, FILE_APPEND);
  if (!f) return false;
  if (!s_trace_header) {
    f.print("t,mag\n");
    s_trace_header = true;
    s_trace_bytes += 6;  // count the header too: STATS trace_bytes sizes the dump
  }
  f.write((const uint8_t*)data, len);
  f.close();
  s_trace_bytes += (uint32_t)len;
  if (s_trace_bytes >= JH_TRACE_MAX_BYTES && !s_trace_full) {
    s_trace_full = true;
    return true;  // caller (main.cpp) narrates this transition once
  }
  return false;
}

uint32_t trace_bytes()   { return s_trace_bytes; }
bool     trace_is_full() { return s_trace_full; }

bool open_read(StoredFile which) {
  if (!s_fs_ok) return false;
  const char* path = (which == StoredFile::JUMPS) ? JUMPS_PATH : TRACE_PATH;
  s_read_file = LittleFS.open(path, FILE_READ);
  return (bool)s_read_file;
}

size_t read_chunk(uint8_t* buf, size_t max_len) {
  if (!s_read_file || !s_read_file.available()) return 0;
  return s_read_file.read(buf, max_len);
}

void close_read() {
  if (s_read_file) s_read_file.close();
}

void clear() {
  if (s_fs_ok) {
    LittleFS.remove(TRACE_PATH);
    LittleFS.remove(JUMPS_PATH);
  }
  s_trace_bytes  = 0;
  s_trace_full   = false;
  s_trace_header = false;
  s_jumps_header = false;
}

bool hard_format(void (*announce)(const char* line)) { announce("# hard format: not implemented on this platform"); return false; }

}  // namespace jh_store
