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
  float landing_settle_s    = JH_LANDING_SETTLE_S;    // this long at ordinary g => flight over
  float min_airtime_s       = JH_MIN_AIRTIME_S;       // reject shorter (chop/noise)
  float max_airtime_s       = JH_MAX_AIRTIME_S;       // reject longer: physically absurd
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
  // Why the last confirmed flight did NOT count as a jump. Set on the single
  // sample where the rejection happens (and cleared on the next update), so a
  // caller can narrate near-misses — "0.19s of air, under the 0.25s minimum"
  // beats silent nothing when a human is desk-testing. Mirrored in detector.py.
  enum class Reject { NONE, TOO_SHORT, NO_LANDING };

  Detector() : Detector(Params()) {}
  explicit Detector(const Params& p) : p_(p) {}

  // Feed one sample: t_s = timestamp in seconds, accel_mag_g = |acceleration| in g.
  // Returns true exactly on the sample that completes a valid jump, filling `out`.
  bool update(float t_s, float accel_mag_g, JumpEvent& out) {
    last_reject_ = Reject::NONE;
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
          last_low_time_ = t_s;
        }
        break;

      case State::AIRBORNE:
        if (accel_mag_g < p_.freefall_enter_g) last_low_time_ = t_s;  // still falling
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
          last_reject_         = raw < p_.min_airtime_s ? Reject::TOO_SHORT
                                                        : Reject::NO_LANDING;
          last_reject_airtime_ = raw;
        } else if (t_s - last_low_time_ >= p_.landing_settle_s) {
          // Ordinary (non-free-fall) readings for a while: the flight is over
          // but the landing never spiked past the threshold. Release NOW — a
          // detector stuck "airborne" is deaf to the next takeoff, and the
          // eventual stray spike would close the stale flight as a monster
          // jump (a real desk session stored a "57 m" jump exactly this way).
          state_ = State::RIDING;
          last_reject_         = Reject::NO_LANDING;
          last_reject_airtime_ = t_s - takeoff_time_;
        } else if (t_s - takeoff_time_ > p_.max_airtime_s) {
          state_ = State::RIDING;  // belt-and-suspenders: settle above fires first
          last_reject_         = Reject::NO_LANDING;
          last_reject_airtime_ = t_s - takeoff_time_;
        }
        break;
    }
    return false;
  }

  State state() const { return state_; }
  const Params& params() const { return p_; }
  // Runtime calibration hook: the two calibration terms are the only params
  // that may change after compile (NVS `set` command / a phone over BLE).
  void set_calibration(float airtime_offset_s, float height_scale) {
    p_.airtime_offset_s = airtime_offset_s;
    p_.height_scale     = height_scale;
  }
  Reject last_reject() const { return last_reject_; }
  float  last_reject_airtime() const { return last_reject_airtime_; }

 private:
  Params p_;
  State state_        = State::RIDING;
  float takeoff_time_ = 0.0f;
  float last_low_time_ = 0.0f;  // last sample that still read as free-fall
  Reject last_reject_         = Reject::NONE;
  float  last_reject_airtime_ = 0.0f;
};

}  // namespace jump
