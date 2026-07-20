// Host-side parity test for the firmware's jump detector.
//
// Reads a capture CSV on stdin (either "t,mag" or "t_s,ax,ay,az" — same
// formats sim/run.py accepts), runs the exact detector the firmware uses
// (jump_detector.h, with values baked from config/params.json via
// params.gen.h), and prints one line per jump in the same format as
// sim/golden.py. `./tools/jump simtest` diffs the two to prove the C++ and
// Python implementations agree.
//
// Build (done automatically by simtest):
//   g++ -std=c++14 -Wall -Wextra -I firmware/include firmware/test/host_test.cpp -o host_test
//
// SPDX-License-Identifier: MIT

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <iostream>
#include <sstream>

#include "jump_detector.h"

int main() {
  std::string line;
  bool header_skipped = false;
  jump::Detector det;
  int found = 0;

  while (std::getline(std::cin, line)) {
    if (line.empty() || line[0] == '#') continue;
    if (!header_skipped) { header_skipped = true; continue; }  // CSV header row

    std::vector<double> vals;
    std::stringstream ss(line);
    std::string cell;
    bool bad = false;
    while (std::getline(ss, cell, ',')) {
      char* end = nullptr;
      double v = std::strtod(cell.c_str(), &end);
      if (end == cell.c_str()) { bad = true; break; }
      vals.push_back(v);
    }
    if (bad || vals.size() < 2) continue;  // skip malformed rows

    const float t = (float)vals[0];
    float mag;
    if (vals.size() >= 4) {
      const float ax = (float)vals[1], ay = (float)vals[2], az = (float)vals[3];
      mag = std::sqrt(ax * ax + ay * ay + az * az);
    } else {
      mag = (float)vals[1];
    }

    jump::JumpEvent ev;
    if (det.update(t, mag, ev)) {
      std::printf("JUMP takeoff=%.3f airtime_raw=%.3f airtime=%.3f height=%.3f\n",
                  ev.takeoff_time_s, ev.airtime_raw_s, ev.airtime_s, ev.height_m);
      found++;
    }
  }
  return found > 0 ? 0 : 1;
}
