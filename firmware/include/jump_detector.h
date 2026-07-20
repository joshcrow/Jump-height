// jump_detector.h
//
// Portable, dependency-free jump-detection state machine for the airtime method.
// Feed it accelerometer-magnitude samples (in g) one at a time; it emits a jump
// with airtime and height whenever it sees takeoff (free-fall) followed by a
// landing spike.
//
// This header has NO Arduino/ESP32 dependencies, so it compiles on a host for
// unit testing and is a 1:1 mirror of sim/detector.py. Both take their tunable
// values from config/params.json — this file via the generated params.gen.h,
// the simulator by reading the JSON directly. Edit the JSON, not the defaults.
//
// Physics: h = height_scale * g * (airtime + airtime_offset)^2 / 8
// (see docs/algorithm.md; the two calibration terms default to off)
//
// SPDX-License-Identifier: MIT

#pragma once

#include "params.gen.h"

namespace jump {

// Tunable thresholds. Defaults come from config/params.json via params.gen.h.
struct Params {
  float g                   = JH_G;                   // gravity, m/s^2
  float freefall_enter_g    = JH_FREEFALL_ENTER_G;    // |a| below => possible takeoff
  float freefall_confirm_s  = JH_FREEFALL_CONFIRM_S;  // stay low this long => launch
  float landing_threshold_g = JH_LANDING_THRESHOLD_G; // |a| above while airborne => landing
  float min_airtime_s       = JH_MIN_AIRTIME_S;       // reject shorter (chop/noise)
  float max_airtime_s       = JH_MAX_AIRTIME_S;       // sanity cap; unsticks AIRBORNE
  float airtime_offset_s    = JH_AIRTIME_OFFSET_S;    // calibration: added to raw airtime
  float height_scale        = JH_HEIGHT_SCALE;        // calibration: multiplies height
};

struct JumpEvent {
  float takeoff_time_s;  // timestamp of takeoff (start of the free-fall dip)
  float airtime_raw_s;   // measured, uncorrected
  float airtime_s;       // after airtime_offset_s calibration
  float height_m;        // height_scale * g * airtime_s^2 / 8
};

enum class State { RIDING, CANDIDATE, AIRBORNE };

class Detector {
 public:
  Detector() : Detector(Params()) {}
  explicit Detector(const Params& p) : p_(p) {}

  // Feed one sample: t_s = timestamp in seconds, accel_mag_g = |acceleration| in g.
  // Returns true exactly on the sample that completes a valid jump, filling `out`.
  bool update(float t_s, float accel_mag_g, JumpEvent& out) {
    switch (state_) {
      case State::RIDING:
        if (accel_mag_g < p_.freefall_enter_g) {
          state_ = State::CANDIDATE;
          takeoff_time_ = t_s;  // pin takeoff to the start of the dip
        }
        break;

      case State::CANDIDATE:
        if (accel_mag_g >= p_.freefall_enter_g) {
          state_ = State::RIDING;  // popped back up: was just a bump
        } else if (t_s - takeoff_time_ >= p_.freefall_confirm_s) {
          state_ = State::AIRBORNE;  // sustained free-fall: real launch
        }
        break;

      case State::AIRBORNE:
        if (accel_mag_g > p_.landing_threshold_g) {
          const float raw = t_s - takeoff_time_;
          state_ = State::RIDING;
          // Validate on the raw (physical) airtime; report calibrated.
          if (raw >= p_.min_airtime_s && raw <= p_.max_airtime_s) {
            float cal = raw + p_.airtime_offset_s;
            if (cal < 0.0f) cal = 0.0f;
            out.takeoff_time_s = takeoff_time_;
            out.airtime_raw_s  = raw;
            out.airtime_s      = cal;
            out.height_m       = p_.height_scale * p_.g * cal * cal / 8.0f;
            return true;
          }
        } else if (t_s - takeoff_time_ > p_.max_airtime_s) {
          state_ = State::RIDING;  // safety: never saw a landing, reset
        }
        break;
    }
    return false;
  }

  State state() const { return state_; }
  const Params& params() const { return p_; }

 private:
  Params p_;
  State state_       = State::RIDING;
  float takeoff_time_ = 0.0f;
};

}  // namespace jump
