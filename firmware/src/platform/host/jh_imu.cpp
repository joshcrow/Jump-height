// jh_imu.cpp — HOST implementation of the jh_imu seam
// (firmware/include/platform/jh_imu.h). See firmware/platformio.ini's
// env:host.
//
// There's no real sensor, so this replays a SCRIPTED accelerometer timeline
// read from the plain-text file named by env JH_IMU_SCRIPT, one line per
// segment (blank lines and lines starting with '#' are ignored):
//
//   rest <seconds>              1 g + tiny noise
//   still <seconds> <g>         <g> + tiny noise (a mis-scaled-sensor stand-in)
//   jump <seconds>              free-fall for <seconds>, then a brief 4 g
//                                landing spike
//   chop <seconds> <amplitude>  +/-<amplitude> jitter around 1 g
//
// All magnitude is placed on the Z axis (ax=ay=0) — main.cpp only ever
// consumes sqrt(ax^2+ay^2+az^2), never individual axes, so this is
// indistinguishable to the core from a flat, still-oriented sensor and is
// far simpler than fabricating a 3-axis orientation.
//
// Playback is keyed off wall-clock time (std::chrono::steady_clock, the
// same clock family jh_clock.cpp itself uses), NOT a sample counter — so it
// never drifts against main.cpp's own timestamps regardless of exactly how
// often/fast read_accel_g() is actually called. Noise is drawn from a
// FIXED-SEED PRNG: same script -> byte-identical timeline every run.
//
// Once the script's segments are exhausted, playback loops the LAST
// segment's settled condition forever (never falls off the end): for
// `jump`, that's a resting condition (a jump always lands and settles);
// for every other segment type, it's that same segment's own steady
// condition, still jittering with fresh noise draws.
//
// If JH_IMU_SCRIPT is unset, unreadable, or empty, this simply behaves as
// an infinite `rest` — a plausible, quiet, always-on sensor.
//
// SPDX-License-Identifier: MIT

#include "platform/jh_imu.h"

#include <chrono>
#include <cstdlib>
#include <fstream>
#include <random>
#include <sstream>
#include <string>
#include <vector>

namespace jh_imu {

namespace {

enum class SegType { REST, STILL, JUMP, CHOP };

struct Segment {
  SegType type = SegType::REST;
  float duration_s = 0.0f;  // free-fall length for JUMP; steady length otherwise
  float param_g = 1.0f;     // STILL's resting g level
  float param_amp = 0.0f;   // REST/STILL's tiny noise, or CHOP's jitter amplitude
};

const float kTinyNoiseAmp = 0.004f;  // quiet enough for a clean SELFTEST noise PASS
const float kLandingSpikeS = 0.02f;  // wall-clock width of the simulated landing spike
const float kLandingG = 4.0f;
const float kFreefallNoiseAmp = 0.02f;   // still safely under JH_FREEFALL_ENTER_G
const float kLandingNoiseAmp = 0.05f;    // still safely over JH_LANDING_THRESHOLD_G

std::vector<Segment> g_segs;
std::mt19937 g_rng(12345);  // fixed seed: deterministic, reproducible playback
std::uniform_real_distribution<float> g_unit(-1.0f, 1.0f);
std::chrono::steady_clock::time_point g_t0;

float noise(float amplitude) { return g_unit(g_rng) * amplitude; }

std::vector<Segment> parseScript(const std::string& path) {
  std::vector<Segment> out;
  std::ifstream f(path);
  if (!f) return out;
  std::string line;
  while (std::getline(f, line)) {
    const size_t start = line.find_first_not_of(" \t\r\n");
    if (start == std::string::npos || line[start] == '#') continue;
    std::istringstream iss(line);
    std::string cmd;
    iss >> cmd;
    Segment s;
    if (cmd == "rest") {
      iss >> s.duration_s;
      s.type = SegType::REST;
      s.param_g = 1.0f;
      s.param_amp = kTinyNoiseAmp;
    } else if (cmd == "still") {
      iss >> s.duration_s >> s.param_g;
      s.type = SegType::STILL;
      s.param_amp = kTinyNoiseAmp;
    } else if (cmd == "jump") {
      iss >> s.duration_s;
      s.type = SegType::JUMP;
    } else if (cmd == "chop") {
      iss >> s.duration_s >> s.param_amp;
      s.type = SegType::CHOP;
      s.param_g = 1.0f;
    } else {
      continue;  // unrecognized line: ignore rather than abort the script
    }
    out.push_back(s);
  }
  return out;
}

// Wall-clock width this segment actually occupies in the timeline: a JUMP's
// landing spike is extra time on top of its scripted free-fall duration.
double effDuration(const Segment& s) {
  return (double)s.duration_s + (s.type == SegType::JUMP ? (double)kLandingSpikeS : 0.0);
}

float sampleWithin(const Segment& s, double local_t) {
  switch (s.type) {
    case SegType::REST:
      return 1.0f + noise(s.param_amp);
    case SegType::STILL:
      return s.param_g + noise(s.param_amp);
    case SegType::CHOP:
      return 1.0f + noise(s.param_amp);
    case SegType::JUMP:
      return local_t < (double)s.duration_s ? (0.0f + noise(kFreefallNoiseAmp))
                                             : (kLandingG + noise(kLandingNoiseAmp));
  }
  return 1.0f;
}

// "Loops last state when script ends": a JUMP always settles back to rest
// after landing; everything else just keeps repeating its own steady
// condition (with fresh noise draws, not a frozen value).
float sampleSettled(const Segment& s) {
  if (s.type == SegType::JUMP) return 1.0f + noise(kTinyNoiseAmp);
  return sampleWithin(s, 0.0);
}

float currentSample() {
  if (g_segs.empty()) return 1.0f + noise(kTinyNoiseAmp);  // no script: infinite rest
  const double elapsed_s =
      std::chrono::duration<double>(std::chrono::steady_clock::now() - g_t0).count();
  double acc = 0.0;
  for (const Segment& s : g_segs) {
    const double eff = effDuration(s);
    if (elapsed_s < acc + eff) return sampleWithin(s, elapsed_s - acc);
    acc += eff;
  }
  return sampleSettled(g_segs.back());
}

}  // namespace

void init() {
  g_t0 = std::chrono::steady_clock::now();
  const char* script_path = std::getenv("JH_IMU_SCRIPT");
  g_segs = script_path ? parseScript(script_path) : std::vector<Segment>{};
}

bool probe(uint8_t addr) { return addr == ADDR_PRIMARY; }

bool begin(uint8_t /*addr*/) { return true; }

uint8_t who_am_i() { return 0x68; }

bool read_accel_g(float& ax, float& ay, float& az) {
  ax = 0.0f;
  ay = 0.0f;
  az = currentSample();
  return true;
}

}  // namespace jh_imu
