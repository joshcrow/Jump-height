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
const char* kJumpsHeader = "n,takeoff_s,airtime_raw_s,airtime_s,height_m,med_a_g,med_w_dps,med_acorr_g,n_air\n";
// Field index of height_m in kJumpsHeader, 0-based. Named because
// jumps_scan() reads it positionally, and a reader that finds this column by
// counting from the END breaks silently every time a column is appended
// (F-20, audit 2026-08-22 — it already did, when four were).
const size_t kHeightField = 4;
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

// Host mounts are directory creation — nothing to format, so the
// non-destructive retry is literally init().
bool try_mount(void (*announce)(const char* line)) { return init(announce); }

bool ok() { return s_fs_ok; }

uint32_t free_bytes() {
  struct statvfs sv;
  if (statvfs(jh_host::host_dir().c_str(), &sv) == 0) {
    return (uint32_t)((uint64_t)sv.f_bavail * (uint64_t)sv.f_frsize);
  }
  return 0;
}

AppendResult jumps_append(uint32_t n, float takeoff_s, float airtime_raw_s,
                          float airtime_s, float height_m, uint16_t med_a_mg,
                          uint16_t med_w_dps, uint16_t med_acorr_mg,
                          uint16_t n_air) {
  if (!s_fs_ok) return AppendResult::FS_DOWN;
  FILE* f = std::fopen(jumpsPath().c_str(), "a");
  // No REGION_FULL here: the host store is a plain file with no cap. That is
  // a real divergence from the device and it is why F-19's region-full test
  // must run against the nrf52 store in firmware/test/store_host/, not here.
  if (!f) return AppendResult::WRITE_FAILED;
  if (!s_jumps_header) {
    std::fputs(kJumpsHeader, f);
    s_jumps_header = true;
  }
  std::fprintf(f, "%lu,%.3f,%.3f,%.3f,%.3f,%.3f,%u,%.3f,%u\n", (unsigned long)n, (double)takeoff_s,
               (double)airtime_raw_s, (double)airtime_s, (double)height_m,
               med_a_mg / 1000.0, (unsigned)med_w_dps, med_acorr_mg / 1000.0,
               (unsigned)n_air);
  std::fclose(f);
  return AppendResult::OK;
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
    // height_m is field 4 (0-based). This used to take find_last_of(',') and
    // parse the TAIL, which WAS height_m — back when the schema had five
    // columns. It has had nine since med_a_g/med_w_dps/med_acorr_g/n_air were
    // appended, so the host store has been reporting n_air — an integer count
    // of in-air samples — as a height in metres. The nRF52 path reads a binary
    // struct field and was never affected, which is why this survived: the
    // divergence is invisible unless a test compares the two, and per F-03 CI
    // was not compiling env:host at all.
    float h = 0.0f;
    size_t start = 0, field = 0;
    bool have_height = false;
    for (size_t i = 0; i <= s.size(); ++i) {
      if (i != s.size() && s[i] != ',') continue;
      if (field == kHeightField) {
        h = std::strtof(s.substr(start, i - start).c_str(), nullptr);
        have_height = true;
        break;
      }
      ++field;
      start = i + 1;
    }
    if (!have_height) continue;  // short/malformed row: not a jump record
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

bool trace_wedged() { return false; }  // host store has no sector erase to fail
void set_trace_wedged(bool) {}

void trace_clear() {
  // Host store is CSV files; truncating trace.csv is the equivalent, and
  // jumps.csv is deliberately left alone.
  FILE* f = std::fopen(tracePath().c_str(), "wb");
  if (f) std::fclose(f);
  s_trace_bytes = 0;
  s_trace_header = false;
  s_trace_full = false;
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
