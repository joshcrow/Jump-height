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
// The ONE place that on-disk difference shows through is the raw export
// (`traceraw`): its wire contract is trace_codec blocks, which this store
// doesn't keep. So open_read_raw() synthesizes a region image from the
// stored trace.csv, laid out exactly as the nRF52 store lays one out — see
// buildRawImage() below for what that means and why it is worth doing
// (it is what lets tools/tests/test_hostdev.py exercise main.cpp's real
// traceraw path natively, rather than only on silicon).
//
// There is no format-on-fail case on a host filesystem the way there is on
// first-ever-boot flash, so init() never needs to call `announce` — this
// mirrors the ESP32 code path taken on every boot AFTER the very first one
// (plain mount succeeds, no announcement).
//
// SPDX-License-Identifier: MIT

#include "platform/jh_store.h"

#include <cassert>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <sys/statvfs.h>

#include "host_paths.h"
#include "params.gen.h"
#include "trace_codec.h"

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

// ---- raw trace-region export (`traceraw`) ----
// The host store keeps CSV text, not trace_codec blocks, so there is no
// region image to stream — one is SYNTHESIZED from trace.csv at
// open_read_raw() time (buildRawImage() below). That is deliberate: it lets
// tools/tests/test_hostdev.py drive main.cpp's real traceraw path — framing,
// base64, CRC-32, the reliable-export bracket — natively, against a store
// whose contents the test also knows in plain text. It is NOT a claim that
// this platform stores binary trace.
std::vector<uint8_t> s_raw_image;
size_t s_raw_pos  = 0;
bool   s_raw_open = false;

std::string jumpsPath() { return jh_host::path(kJumpsName); }
std::string tracePath() { return jh_host::path(kTraceName); }

// Re-encode the whole of trace.csv into a trace-region IMAGE laid out the
// way firmware/src/platform/nrf52/jh_store.cpp lays one out, so that what
// main.cpp streams here is decodable by exactly the same client code that
// decodes a real puck's export (sim/trace_codec.py::decode_region_
// recovering): consecutive trace_codec blocks, one per nominal second of
// samples (the block policy feedSample() applies — a new block whenever
// floor(t) changes) or MAX_SAMPLES_PER_BLOCK, each block starting on a
// 4-byte boundary with the 0-3 pad bytes left at 0xFF (the erased-flash
// value the nRF52 writer never touches), and the image ending at align4 —
// which is what `traceraw` reports as bytes=.
//
// Rebuilt from scratch on every call: trace.csv on disk is the only state
// this store has, so there is nothing here that can drift out of date with
// it the way a cached image could.
void buildRawImage() {
  s_raw_image.clear();
  FILE* f = std::fopen(tracePath().c_str(), "rb");
  if (!f) return;  // no trace stored yet — an empty image, and bytes=0

  trace_codec::Encoder enc;
  bool block_open = false;
  long block_sec  = 0;
  uint8_t blk[trace_codec::block_size(trace_codec::MAX_SAMPLES_PER_BLOCK)];

  auto close_block = [&]() {
    if (!block_open || enc.count() == 0) { block_open = false; return; }
    block_open = false;
    const size_t n = enc.finish(blk, sizeof(blk));
    if (n == 0) return;
    s_raw_image.insert(s_raw_image.end(), blk, blk + n);
    while ((s_raw_image.size() & 3u) != 0) s_raw_image.push_back(0xFF);
  };

  char line[256];
  while (std::fgets(line, sizeof(line), f)) {
    // Skip anything that doesn't start a number — i.e. the "t,mag" header.
    // Testing the first character rather than "skip line 1" so a file
    // without a header (or with a blank line in it) is still read correctly
    // instead of silently losing its first sample.
    const char c = line[0];
    if (!((c >= '0' && c <= '9') || c == '-' || c == '+' || c == '.')) continue;
    const char* comma = std::strchr(line, ',');
    if (!comma) continue;
    // t_s stays double all the way to t0_ms_from_t_s(), the same rule
    // firmware/src/platform/nrf52/jh_store.cpp's feedSample() follows and
    // for the same reason (trace_codec.h's "Double precision, both
    // directions" note): narrowing here would move the anchor at multi-hour
    // timestamps and diverge from the Python mirror.
    const double t_s   = std::atof(line);
    const float  mag_g = (float)std::atof(comma + 1);
    if (!std::isfinite(t_s) || !std::isfinite(mag_g)) continue;

    const long sec = (long)std::floor(t_s);
    if (!block_open || sec != block_sec || enc.full()) {
      close_block();
      enc.begin(trace_codec::t0_ms_from_t_s(t_s));
      block_open = true;
      block_sec  = sec;
    }
    enc.add_sample(mag_g);
  }
  close_block();
  std::fclose(f);
}

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

uint32_t trace_bytes_recomputed() {
  // The host store writes real CSV text, so there is no binary re-decode to
  // cross-check against — the file itself IS the ground truth.
  return trace_bytes();
}
uint32_t trace_bytes() { return s_trace_bytes; }
bool trace_is_full() { return s_trace_full; }

bool open_read(StoredFile which) {
  if (!s_fs_ok) return false;
  s_raw_open = false;  // one read slot, either mode (jh_store.h)
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
  s_raw_open = false;  // one read slot, either mode (jh_store.h)
}

// ---------------------------------------------- raw trace-region export
// DIVERGENCE FROM THE DEVICE, stated plainly: there is no fixed-size trace
// region here — the synthesized image (buildRawImage()) is exactly the used
// prefix, so trace_region_bytes() and trace_raw_bytes() return the same
// number on this platform. A client must not read the pair as "used out of
// capacity" against a host build; on the nRF52 they are genuinely
// independent (append offset vs ~1.93 MB of region).
// TEST SEAM, host build only — $JH_HOST_RAW_OVERREPORT (same idiom as this
// platform's JH_VBAT_MV/JH_CHG, jh_power.cpp): announce N bytes MORE than the
// image actually holds, without changing the image. It makes the store lie in
// exactly the way a lost open_read_raw() flush or a failed mid-stream read
// would, and it is the only way a test can reach main.cpp's
// `streamed != declared` arm — nothing else on this platform can come up
// short, so without it the arm that stands between the rider and a truncated
// export that self-verifies (CONTRACT.md §2's `verified`) would ship untested.
// Unset — every ordinary run, and every build that isn't env:host — this is 0
// and the store behaves exactly as before.
static uint32_t raw_overreport_bytes() {   // internal linkage: not seam API
  const char* v = std::getenv("JH_HOST_RAW_OVERREPORT");
  return (v && v[0]) ? (uint32_t)std::strtoul(v, nullptr, 10) : 0;
}

uint32_t trace_raw_bytes() {
  if (!s_fs_ok) return 0;
  // Don't rebuild under a reader's feet: while a raw export is open, the
  // image being streamed is the one this number must describe.
  if (!s_raw_open) buildRawImage();
  return (uint32_t)s_raw_image.size() + raw_overreport_bytes();
}

uint32_t trace_region_bytes() { return trace_raw_bytes(); }

bool open_read_raw() {
  if (!s_fs_ok) return false;
  // Taking the one read slot releases the CSV reader — including its open
  // FILE*, which would otherwise leak for the life of the process.
  if (s_read_file) {
    std::fclose(s_read_file);
    s_read_file = nullptr;
  }
  buildRawImage();
  s_raw_pos  = 0;
  s_raw_open = true;
  return true;
}

size_t read_raw_chunk(uint8_t* buf, size_t max_len) {
  if (!s_raw_open || buf == nullptr) return 0;
  if (s_raw_pos >= s_raw_image.size()) return 0;  // EOF
  // No word-alignment rule here (this reads RAM, not a QSPI peripheral), so
  // any max_len is servable — the nRF52 implementation's 4-byte floor is a
  // property of that bus, not of the seam's contract.
  //
  // But the PRECONDITION is the seam's, not that bus's (jh_store.h: max_len
  // must be >= 4 while bytes remain), so enforce it here too rather than
  // quietly serving a call that would come up short on silicon. This store
  // is the one a caller develops against — env:host never compiles the
  // nRF52 store, so its identical assert (nrf52/jh_store.cpp, under
  // `#if !defined(ARDUINO)`) cannot see this caller at all, and a violation
  // would first surface on the device as a truncated export that looks
  // complete. That is the shape CLAUDE.md rule 3 forbids, so: fail loudly,
  // here, where it is cheap.
  assert(max_len >= 4 &&
         "read_raw_chunk needs >= 4 bytes of buffer while data remains");
  if (max_len == 0) return 0;  // reads as EOF, like the nRF52 store's guard
  size_t n = s_raw_image.size() - s_raw_pos;
  if (n > max_len) n = max_len;
  std::memcpy(buf, s_raw_image.data() + s_raw_pos, n);
  s_raw_pos += n;
  return n;
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
