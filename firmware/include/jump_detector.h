// jump_detector.h
//
// Portable, dependency-free jump-detection state machine for the airtime method.
// Feed it accelerometer-magnitude samples (in g) one at a time; it emits a jump
// with airtime and height whenever it sees takeoff (free-fall) followed by a
// landing spike.
//
// This header has NO Arduino/ESP32 dependencies, so it compiles on a host for
// unit testing and is a 1:1 mirror of sim/detector.py. Keep the two in sync.
//
// Physics: h = g * airtime^2 / 8   (see docs/algorithm.md)
//
// SPDX-License-Identifier: MIT

#pragma once

namespace jump {

// Tunable thresholds. Defaults are a reasonable starting point; tune against real
// captured data (see docs/algorithm.md and sim/). Keep in sync with sim/detector.py.
struct Params {
  float g                  = 9.80665f;  // gravity, m/s^2
  float freefall_enter_g   = 0.35f;     // |a| below this => possible takeoff
  float freefall_confirm_s = 0.08f;     // must stay low this long to confirm launch
  float landing_threshold_g= 2.50f;     // |a| above this while airborne => landing
  float min_airtime_s      = 0.25f;     // reject anything shorter (chop/noise)
  float max_airtime_s      = 8.00f;     // sanity cap; also unsticks AIRBORNE state
};

struct JumpEvent {
  float takeoff_time_s;  // timestamp of takeoff (start of the free-fall dip)
  float airtime_s;       // time in the air
  float height_m;        // g * airtime^2 / 8
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
          const float airtime = t_s - takeoff_time_;
          state_ = State::RIDING;
          if (airtime >= p_.min_airtime_s && airtime <= p_.max_airtime_s) {
            out.takeoff_time_s = takeoff_time_;
            out.airtime_s      = airtime;
            out.height_m       = p_.g * airtime * airtime / 8.0f;
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
