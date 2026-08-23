// Host-side parity test for the firmware's jump detector.
//
// Reads a capture CSV on stdin ("t,mag", "t,mag,gyro_dps" or "t_s,ax,ay,az" —
// the sim/run.py formats plus the gyro one), runs the exact detector the
// firmware uses
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

  // The spin lever is a CALIBRATION, not a sample, so it cannot ride in the
  // CSV. Both harnesses read it from the same env var (sim/golden.py does the
  // same) so a gyro parity run compares identical configurations. With the
  // lever at 0 — the default — correct_for_spin() is the identity and the
  // gyro column would change nothing, which is exactly the blind spot F-16
  // lived in.
  if (const char* lev = std::getenv("JH_SPIN_LEVER_M")) {
    det.set_spin_lever_m((float)std::atof(lev));
  }

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

    // t stays double all the way to update() (B3, glue-and-forget.md §3a):
    // jump_detector.h's t_s is now double specifically so an absolute,
    // ever-growing uptime never gets crushed through float32 first. Prior to
    // this fix this line narrowed to float — which mirrored what
    // firmware/src/main.cpp did before its own fix, so this program truly
    // mirrors the firmware's post-fix behavior, not a stale pre-fix one.
    const double t = vals[0];
    float mag;
    bool  have_gyro = false;
    float gyro_dps  = 0.0f;
    if (vals.size() >= 4) {
      const float ax = (float)vals[1], ay = (float)vals[2], az = (float)vals[3];
      mag = std::sqrt(ax * ax + ay * ay + az * az);
    } else {
      mag = (float)vals[1];
      // THREE columns means t,mag,gyro_dps — the gyro-aware path (F-16, audit
      // 2026-08-22). Until this existed, the parity harness only ever called
      // the accel-only update(), so every gyro-path divergence between C++ and
      // Python was invisible BY CONSTRUCTION. F-16 was exactly such a
      // divergence: the Python mirror was missing the rot_g > 16 g
      // anti-livelock guard, and no amount of running simtest could have shown
      // it. Two columns is t,mag; four is t,ax,ay,az; three was unused.
      if (vals.size() == 3) {
        have_gyro = true;
        gyro_dps  = (float)vals[2];
      }
    }

    jump::JumpEvent ev;
    if (have_gyro ? det.update(t, mag, gyro_dps, ev) : det.update(t, mag, ev)) {
      std::printf("JUMP takeoff=%.3f airtime_raw=%.3f airtime=%.3f height=%.3f\n",
                  ev.takeoff_time_s, ev.airtime_raw_s, ev.airtime_s, ev.height_m);
      found++;
    }
  }
  return found > 0 ? 0 : 1;
}
