// Host-side encode harness for the binary trace v2 codec's parity test.
//
// Mirrors host_test.cpp's pattern exactly (see that file): reads a capture
// CSV on stdin ("t,mag", one header row, blank/# lines skipped — the same
// format sim/generate.py and the firmware's own trace.csv use), and instead
// of running the jump detector, feeds each row through trace_codec::Encoder,
// writing the raw encoded block bytes to stdout. tools/tests/test_trace_codec.py
// builds this with g++ (exactly how ./tools/jump simtest builds host_test.cpp)
// and diffs a Python decode (sim/trace_codec.py) of this program's output
// against a reference CSV.
//
// Block-closing policy (the encoder itself has no clock/policy — see
// trace_codec.h): start a new block whenever floor(t_s) advances into a new
// integer second, or the current block is full (255 samples), matching the
// "nominal one-second blocks" behavior firmware/src/platform/nrf52/jh_store.cpp
// implements for real. A sample whose t_s is LOWER than the block's second
// (a reboot restart, or any other backwards jump) also starts a new block —
// floor(t_s) simply differs, so no special-casing is needed; this is the
// same reasoning documented in trace_codec.h's file comment.
//
// Build (done automatically by tools/tests/test_trace_codec.py):
//   g++ -std=c++14 -Wall -Wextra -I firmware/include
//       firmware/test/trace_codec_harness.cpp -o trace_codec_harness
//
// SPDX-License-Identifier: MIT

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "trace_codec.h"

int main() {
  std::string line;
  bool header_skipped = false;
  std::vector<std::pair<float, float>> rows;  // (t_s, mag_g)

  while (std::getline(std::cin, line)) {
    if (line.empty() || line[0] == '#') continue;
    if (!header_skipped) { header_skipped = true; continue; }  // CSV header row

    std::stringstream ss(line);
    std::string cell;
    std::vector<double> vals;
    bool bad = false;
    while (std::getline(ss, cell, ',')) {
      char* end = nullptr;
      double v = std::strtod(cell.c_str(), &end);
      if (end == cell.c_str()) { bad = true; break; }
      vals.push_back(v);
    }
    if (bad || vals.size() < 2) continue;
    rows.emplace_back((float)vals[0], (float)vals[1]);
  }

  trace_codec::Encoder enc;
  bool block_open = false;
  long cur_sec = 0;

  auto flush_block = [&]() {
    if (!block_open || enc.count() == 0) return;
    uint8_t buf[trace_codec::block_size(trace_codec::MAX_SAMPLES_PER_BLOCK)];
    size_t n = enc.finish(buf, sizeof(buf));
    if (n > 0) std::fwrite(buf, 1, n, stdout);
    block_open = false;
  };

  for (const auto& row : rows) {
    const float t_s = row.first;
    const float mag_g = row.second;
    const long this_sec = (long)std::floor(t_s);

    if (!block_open || this_sec != cur_sec || enc.full()) {
      flush_block();
      enc.begin((uint32_t)std::lround((double)t_s * 1000.0));
      block_open = true;
      cur_sec = this_sec;
    }
    enc.add_sample(mag_g);
  }
  flush_block();

  std::fflush(stdout);
  return 0;
}
