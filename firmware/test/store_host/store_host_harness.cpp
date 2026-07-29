// store_host_harness.cpp — host-side scenario runner for jh_store.cpp,
// mirroring firmware/test/trace_codec_harness.cpp's pattern (a small g++
// program driven by a Python test over stdin/stdout) one level up: instead
// of exercising trace_codec.h's encoder directly, this runs the REAL
// firmware/src/platform/nrf52/jh_store.cpp — the untested raw QSPI region
// manager itself — against firmware/test/store_host/mock_flash.h's
// real-semantics RAM/file-backed flash mock (see that file for exactly what
// is and isn't modeled).
//
// tools/tests/test_store_host.py builds this with g++ (same recipe as
// tools/tests/test_trace_codec.py builds trace_codec_harness.cpp) and
// drives it by writing a short scripted command list to stdin, one command
// per line, then parsing the printed result lines back out. A "reboot" is
// simply running this binary again as a fresh OS process against the SAME
// backing file (JH_MOCK_FLASH_FILE) — see mock_flash.h's header comment for
// why that's the right way to model it (jh_store.cpp's state is ordinary
// C++ statics; only a new process gives them their compile-time initial
// values back, exactly like a real MCU power-cycle resets RAM but not
// flash).
//
// Command language (blank lines and lines starting with '#' are skipped,
// matching trace_codec_harness.cpp's/host_test.cpp's own CSV-skipping
// convention). One command per line; each prints exactly one or more
// `TAG key=value ...` result lines (space-separated), or, for READ_ALL,
// raw bytes framed between literal marker lines. stdout is flushed after
// every command so a fault-injected crash (see FAULT_AFTER) never loses
// output from commands that already completed.
//
//   INIT                                    jh_store::init(); reports
//                                            ok=<0|1> elapsed_us=<n> (wall
//                                            clock around the call — this is
//                                            the number SENSE_FIRST_BOOT.md
//                                            item 11 asks the bench to
//                                            measure for real; here it's
//                                            order-of-magnitude host info
//                                            only, never a hardware timing
//                                            stand-in). Any announce() lines
//                                            print first, each as its own
//                                            `ANNOUNCE:<text>` line.
//   OK                                      jh_store::ok()
//   FREE_BYTES                              jh_store::free_bytes()
//   CHIP_SIZE                                mock_flash_test::chip_size()
//   JUMPS_APPEND n takeoff raw air height    jh_store::jumps_append(...)
//   JUMPS_SCAN                               jh_store::jumps_scan(...)
//   JUMPS_FILL count                         count synthetic appends in one
//                                            fast in-process loop (no
//                                            per-sample stdio) — for
//                                            marathon-scale scenarios.
//                                            Reports appended/count/best_m.
//   TRACE_APPEND t0,mag0;t1,mag1;...         one jh_store::trace_append()
//                                            call over the ';'-joined
//                                            samples (';' -> '\n', trailing
//                                            '\n' added) — mirrors
//                                            main.cpp's own ~1×/s batched
//                                            flush (see flushTrace()/
//                                            trace_buf in main.cpp).
//   TRACE_FILL max_seconds log_hz start_t mag0
//                                            up to max_seconds batches of
//                                            log_hz evenly-spaced samples
//                                            each (one jh_store::
//                                            trace_append() per simulated
//                                            second), stopping early once
//                                            trace_is_full() — for
//                                            marathon-scale scenarios.
//   TRACE_BYTES                              jh_store::trace_bytes()
//   TRACE_IS_FULL                            jh_store::trace_is_full()
//   OPEN_READ JUMPS|TRACE                    jh_store::open_read(...)
//   READ_ALL                                 drains read_chunk() to EOF,
//                                            raw bytes framed between
//                                            ===READ_ALL_BEGIN===/
//                                            ===READ_ALL_END=== marker
//                                            lines (see the parser note
//                                            below for the exact framing
//                                            contract).
//   CLOSE_READ                               jh_store::close_read()
//   CLEAR                                    jh_store::clear()
//   FAULT_AFTER n                            arms mock_flash_test::
//                                            arm_fault_after_bytes(n) —
//                                            the NEXT write-affecting
//                                            command that crosses n bytes
//                                            written (chip-wide countdown
//                                            from this point) ends this
//                                            process immediately via
//                                            mock_flash_test::kFaultExitCode
//                                            — simulated power loss. Any
//                                            commands after the one that
//                                            faults never run.
//
// READ_ALL framing contract (for the Python side): the payload is exactly
// the bytes between the END of the "===READ_ALL_BEGIN===\n" line and the
// START of the immediately-following "\n===READ_ALL_END===\n" — i.e. read
// everything up to (not including) that final '\n===READ_ALL_END===' marker;
// the one '\n' right before the marker is NOT part of the payload (it's
// unconditionally appended by this harness after the raw dump, whether or
// not the payload already ended in '\n' — jh_store's CSV rows always do, in
// practice, so there are two consecutive '\n's before the marker in
// practice, and the parser only need drop the outer one).
//
// Build (done automatically by tools/tests/test_store_host.py):
//   g++ -std=c++14 -Wall -Wextra
//       -I firmware/test/store_host/shim -I firmware/include
//       firmware/test/store_host/mock_flash.cpp
//       firmware/test/store_host/store_host_harness.cpp
//       firmware/src/platform/nrf52/jh_store.cpp
//       -o store_host_harness
//
// SPDX-License-Identifier: MIT

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <chrono>
#include <iostream>
#include <sstream>
#include <string>

#include "mock_flash.h"
#include "platform/jh_store.h"

namespace {

void announce(const char* line) {
  std::printf("ANNOUNCE:%s\n", line);
  std::fflush(stdout);
}

jh_store::StoredFile parseWhich(const std::string& s) {
  return (s == "JUMPS") ? jh_store::StoredFile::JUMPS : jh_store::StoredFile::TRACE;
}

// Converts a ';'-joined "t,mag;t,mag;..." token into the '\n'-joined raw
// CSV body jh_store::trace_append() expects (see main.cpp's own trace_buf:
// one "t,mag\n" row per sample, concatenated, no header — the header is
// jh_store's own concern, added once on first read-back).
std::string sampleTokenToBody(const std::string& tok) {
  std::string body;
  body.reserve(tok.size() + 1);
  for (char c : tok) body += (c == ';') ? '\n' : c;
  if (body.empty() || body.back() != '\n') body += '\n';
  return body;
}

void cmdInit() {
  const auto t0 = std::chrono::steady_clock::now();
  const bool ok = jh_store::init(announce);
  const auto t1 = std::chrono::steady_clock::now();
  const long long us =
      std::chrono::duration_cast<std::chrono::microseconds>(t1 - t0).count();
  std::printf("INIT ok=%d elapsed_us=%lld\n", ok ? 1 : 0, us);
}

void cmdJumpsFill(std::istringstream& iss) {
  long count = 0;
  iss >> count;
  long appended = 0;
  for (long i = 0; i < count; ++i) {
    const float height = 0.5f + 0.001f * (float)(i % 500);
    jh_store::jumps_append((uint32_t)i, (float)i * 0.01f, 0.30f, 0.28f, height);
    ++appended;
  }
  uint32_t c = 0;
  float best = 0.0f;
  jh_store::jumps_scan(c, best);
  std::printf("JUMPS_FILL appended=%ld count=%u best_m=%.6f\n", appended, c, (double)best);
}

void cmdTraceFill(std::istringstream& iss) {
  long max_seconds = 0;
  long log_hz = 0;
  double start_t = 0.0;
  double mag0 = 1.0;
  iss >> max_seconds >> log_hz >> start_t >> mag0;

  long seconds_done = 0;
  bool full = jh_store::trace_is_full();
  static char batch[8192];
  for (long s = 0; s < max_seconds && !full; ++s) {
    size_t pos = 0;
    for (long i = 0; i < log_hz; ++i) {
      const double t = start_t + (double)s + (double)i / (double)log_hz;
      const double mag = mag0 + 0.001 * (double)(i % 50);
      const int n = std::snprintf(batch + pos, sizeof(batch) - pos, "%.3f,%.3f\n", t, mag);
      if (n <= 0 || pos + (size_t)n >= sizeof(batch)) break;
      pos += (size_t)n;
    }
    jh_store::trace_append(batch, pos);
    ++seconds_done;
    full = jh_store::trace_is_full();
  }
  std::printf("TRACE_FILL seconds_appended=%ld full=%d\n", seconds_done, full ? 1 : 0);
}

void cmdReadAll() {
  std::printf("===READ_ALL_BEGIN===\n");
  std::fflush(stdout);
  uint8_t buf[512];
  size_t n;
  while ((n = jh_store::read_chunk(buf, sizeof(buf))) > 0) {
    std::fwrite(buf, 1, n, stdout);
  }
  std::fflush(stdout);
  std::printf("\n===READ_ALL_END===\n");
}

}  // namespace

int main() {
  std::string line;
  while (std::getline(std::cin, line)) {
    if (line.empty() || line[0] == '#') continue;

    std::istringstream iss(line);
    std::string cmd;
    iss >> cmd;

    if (cmd == "INIT") {
      cmdInit();
    } else if (cmd == "OK") {
      std::printf("OK ok=%d\n", jh_store::ok() ? 1 : 0);
    } else if (cmd == "FREE_BYTES") {
      std::printf("FREE_BYTES n=%u\n", jh_store::free_bytes());
    } else if (cmd == "CHIP_SIZE") {
      std::printf("CHIP_SIZE n=%u\n", mock_flash_test::chip_size());
    } else if (cmd == "JUMPS_APPEND") {
      unsigned long n = 0;
      double takeoff = 0, araw = 0, air = 0, height = 0;
      iss >> n >> takeoff >> araw >> air >> height;
      jh_store::jumps_append((uint32_t)n, (float)takeoff, (float)araw, (float)air, (float)height);
      std::printf("JUMPS_APPEND done=1\n");
    } else if (cmd == "JUMPS_SCAN") {
      uint32_t count = 0;
      float best = 0.0f;
      jh_store::jumps_scan(count, best);
      std::printf("JUMPS_SCAN count=%u best_m=%.6f\n", count, (double)best);
    } else if (cmd == "JUMPS_FILL") {
      cmdJumpsFill(iss);
    } else if (cmd == "TRACE_APPEND") {
      std::string tok;
      iss >> tok;
      const std::string body = sampleTokenToBody(tok);
      const bool crossed = jh_store::trace_append(body.data(), body.size());
      std::printf("TRACE_APPEND crossed=%d\n", crossed ? 1 : 0);
    } else if (cmd == "TRACE_FILL") {
      cmdTraceFill(iss);
    } else if (cmd == "TRACE_BYTES") {
      std::printf("TRACE_BYTES n=%u\n", jh_store::trace_bytes());
    } else if (cmd == "TRACE_IS_FULL") {
      std::printf("TRACE_IS_FULL full=%d\n", jh_store::trace_is_full() ? 1 : 0);
    } else if (cmd == "OPEN_READ") {
      std::string which;
      iss >> which;
      const bool ok = jh_store::open_read(parseWhich(which));
      std::printf("OPEN_READ ok=%d\n", ok ? 1 : 0);
    } else if (cmd == "READ_ALL") {
      cmdReadAll();
    } else if (cmd == "CLOSE_READ") {
      jh_store::close_read();
      std::printf("CLOSE_READ ok=1\n");
    } else if (cmd == "CLEAR") {
      jh_store::clear();
      std::printf("CLEAR ok=1\n");
    } else if (cmd == "FAULT_AFTER") {
      unsigned long n = 0;
      iss >> n;
      mock_flash_test::arm_fault_after_bytes((uint32_t)n);
      std::printf("FAULT_AFTER armed=%lu\n", n);
    } else {
      std::printf("ERROR unknown_command=%s\n", cmd.c_str());
    }
    std::fflush(stdout);
  }
  return 0;
}
