// jh_store.cpp — HOST implementation of the jh_store seam
// (firmware/include/platform/jh_store.h). See firmware/platformio.ini's
// env:host.
//
// Simplest correct mirror of the ESP32 implementation's contract
// (src/platform/esp32/jh_store.cpp): jumps.csv/trace.csv as plain CSV files
// on real disk, under $JH_HOST_DIR (default /tmp — see host_paths.h), same
// wire format, same header-once/byte-count/cap bookkeeping, resumed at
// init() by inspecting whatever's already on disk (mirrors the ESP32 side's
// own boot-time resume of trace byte count / header-written flags from the
// existing files) so state survives a process restart with the same
// JH_HOST_DIR exactly like the ESP32 side survives a reboot.
//
// Deliberately NOT the nRF52 platform's binary-trace/region-file approach:
// the task calls for CSV storage here (simplest correct implementation),
// and jh_store.h's contract only requires the append/scan/read/clear
// semantics + the CSV wire format a framed dump sends — not any particular
// on-disk representation.
//
// There is no format-on-fail case on a host filesystem the way there is on
// first-ever-boot flash, so init() never needs to call `announce` — this
// mirrors the ESP32 code path taken on every boot AFTER the very first one
// (plain mount succeeds, no announcement).
//
// SPDX-License-Identifier: MIT

#include "platform/jh_store.h"

#include <cstdio>
#include <cstdlib>
#include <string>

#include <sys/statvfs.h>

#include "host_paths.h"
#include "params.gen.h"

namespace jh_store {

namespace {

const char* kJumpsName = "jumps.csv";
const char* kTraceName = "trace.csv";
const char* kJumpsHeader = "n,takeoff_s,airtime_raw_s,airtime_s,height_m\n";
const char* kTraceHeader = "t,mag\n";

bool s_fs_ok = false;
uint32_t s_trace_bytes = 0;
bool s_trace_full = false;
bool s_trace_header = false;
bool s_jumps_header = false;

FILE* s_read_file = nullptr;

std::string jumpsPath() { return jh_host::path(kJumpsName); }
std::string tracePath() { return jh_host::path(kTraceName); }

// -1 if the file doesn't exist (or can't be opened).
long fileSize(const std::string& p) {
  FILE* f = std::fopen(p.c_str(), "rb");
  if (!f) return -1;
  std::fseek(f, 0, SEEK_END);
  const long n = std::ftell(f);
  std::fclose(f);
  return n;
}

}  // namespace

bool init(void (*/*announce*/)(const char* line)) {
  jh_host::ensure_dir();
  s_fs_ok = true;

  // Resume trace.csv's byte count / header-written / cap state from
  // whatever is already on disk (a prior process run with this same
  // JH_HOST_DIR) — mirrors the ESP32 side's own boot-time resume exactly
  // (src/platform/esp32/jh_store.cpp's init(): s_trace_bytes = f.size()).
  const long tb = fileSize(tracePath());
  s_trace_bytes = tb > 0 ? (uint32_t)tb : 0;
  s_trace_header = tb > 0;
  if (s_trace_bytes >= JH_TRACE_MAX_BYTES) s_trace_full = true;

  const long jb = fileSize(jumpsPath());
  s_jumps_header = jb > 0;

  return s_fs_ok;
}

bool ok() { return s_fs_ok; }

uint32_t free_bytes() {
  struct statvfs sv;
  if (statvfs(jh_host::host_dir().c_str(), &sv) == 0) {
    return (uint32_t)((uint64_t)sv.f_bavail * (uint64_t)sv.f_frsize);
  }
  return 0;
}

void jumps_append(uint32_t n, float takeoff_s, float airtime_raw_s, float airtime_s,
                   float height_m) {
  if (!s_fs_ok) return;
  FILE* f = std::fopen(jumpsPath().c_str(), "a");
  if (!f) return;
  if (!s_jumps_header) {
    std::fputs(kJumpsHeader, f);
    s_jumps_header = true;
  }
  std::fprintf(f, "%lu,%.3f,%.3f,%.3f,%.3f\n", (unsigned long)n, (double)takeoff_s,
               (double)airtime_raw_s, (double)airtime_s, (double)height_m);
  std::fclose(f);
}

void jumps_scan(uint32_t& count, float& best_m) {
  count = 0;
  best_m = 0.0f;
  if (!s_fs_ok) return;
  FILE* f = std::fopen(jumpsPath().c_str(), "r");
  if (!f) return;
  char line[256];
  bool first = true;
  while (std::fgets(line, sizeof(line), f)) {
    if (first) {
      first = false;
      continue;  // header
    }
    std::string s(line);
    while (!s.empty() && (s.back() == '\n' || s.back() == '\r')) s.pop_back();
    if (s.empty()) continue;
    const size_t c = s.find_last_of(',');
    if (c == std::string::npos) continue;
    const float h = std::strtof(s.c_str() + c + 1, nullptr);
    count++;
    if (h > best_m) best_m = h;
  }
  std::fclose(f);
}

bool trace_append(const char* data, size_t len) {
  if (!s_fs_ok) return false;
  FILE* f = std::fopen(tracePath().c_str(), "a");
  if (!f) return false;
  if (!s_trace_header) {
    std::fputs(kTraceHeader, f);
    s_trace_header = true;
    s_trace_bytes += 6;  // count the header too, like the ESP32 side does
  }
  std::fwrite(data, 1, len, f);
  std::fclose(f);
  s_trace_bytes += (uint32_t)len;
  if (s_trace_bytes >= JH_TRACE_MAX_BYTES && !s_trace_full) {
    s_trace_full = true;
    return true;  // caller (main.cpp) narrates this transition once
  }
  return false;
}

uint32_t trace_bytes() { return s_trace_bytes; }
bool trace_is_full() { return s_trace_full; }

bool open_read(StoredFile which) {
  if (!s_fs_ok) return false;
  const std::string p = (which == StoredFile::JUMPS) ? jumpsPath() : tracePath();
  s_read_file = std::fopen(p.c_str(), "rb");
  return s_read_file != nullptr;
}

size_t read_chunk(uint8_t* buf, size_t max_len) {
  if (!s_read_file) return 0;
  return std::fread(buf, 1, max_len, s_read_file);
}

void close_read() {
  if (s_read_file) {
    std::fclose(s_read_file);
    s_read_file = nullptr;
  }
}

void clear() {
  if (s_fs_ok) {
    std::remove(jumpsPath().c_str());
    std::remove(tracePath().c_str());
  }
  s_trace_bytes = 0;
  s_trace_full = false;
  s_trace_header = false;
  s_jumps_header = false;
}

bool hard_format(void (*announce)(const char* line)) { announce("# hard format: host is a no-op"); return true; }

}  // namespace jh_store
