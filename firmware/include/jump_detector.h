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
// SPIN: a board-mounted accelerometer that is rotating reads its own
// centripetal term, omega^2*r, on top of the specific force it is there to
// measure. That is not a trick-metric nicety — it breaks THIS state machine
// in two distinct ways (sim/experiments/g4_spin_detector.py, which mapped
// both): at 300-500 dps the rotation lifts |a| back above freefall_enter_g
// and re-pins takeoff mid-flight, and above ~518 dps (at r=0.3 m) it crosses
// landing_threshold_g outright and fires a FALSE LANDING. Measured cost:
// -93% height error at 300 dps peak, and real wing spins run 240-360 mean /
// 500-900 peak. Straight airs are unaffected, which is exactly why this hid.
//
// The fix is one line of algebra applied BEFORE the state machine sees the
// sample (see correct_for_spin below), which is why it lives here rather
// than in a caller: a detector fed uncorrected magnitudes is wrong, and
// nothing downstream can undo it.
//
// SPDX-License-Identifier: MIT

#pragma once

#include <math.h>  // sqrtf — still no Arduino/ESP32 dependency; compiles on host

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
  float spin_lever_m        = JH_SPIN_LEVER_M;        // calibration: mount lever arm, 0 = off
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

  // Remove the rotation's own centripetal contribution from an accelerometer
  // magnitude, in quadrature:
  //
  //     a_corr = sqrt(max(0, |a|^2 - (omega^2 * r / g)^2))
  //
  // omega comes from the gyro (dps -> rad/s), r is the mount lever arm. The
  // quadrature form (not a plain subtraction) is what the sim validated: the
  // centripetal vector is perpendicular to the specific force it contaminates,
  // so magnitudes add in quadrature, not linearly.
  //
  // The max(0,...) clamp is load-bearing and two-sided (g4's landing-erasure
  // probe): an OVER-estimated r drives the argument negative on the landing
  // spike itself, clamps it to zero, and the detector never sees the
  // touchdown — the jump runs away or is lost entirely.
  //
  // CORRECTION (2026-08-10): this comment used to end "so err SHORT," which is
  // WRONG and was measured wrong. Under-estimating is not the safe direction —
  // it leaves a free-fall residual of rot_g*sqrt(1-k^2), and the sqrt amplifies
  // small errors badly: k=0.99 leaves 14% of rot_g. At r=0.5 m and 600 dps
  // that is 0.79 g against this 0.35 g gate, so takeoff is re-pinned
  // mid-flight and height reads ~-95% low. A deliberate 5% short-shave broke 5
  // of 8 lever x spin cases; removing it fixed all 8 at +0.0% error.
  //
  // The two errors push in opposite directions and there is no safe side. Aim
  // UNBIASED. See lever_arm.h design note 3 and tools/tests/test_lever_arm.py.
  //
  // The precision this demands is the real constraint, and it is steep: at
  // r=0.5 m / 600 dps the residual stays under the gate only for k > 0.998.
  // That is why the lever arm is measured per-jump from flight data rather
  // than entered by hand — no tape measure reaches 0.2%.
  //
  // spin_lever_m = 0 (the default) makes this exactly the identity, so an
  // uncalibrated device behaves precisely as it did before.
  static float correct_for_spin(float accel_mag_g, float gyro_mag_dps,
                                float spin_lever_m, float g) {
    if (spin_lever_m <= 0.0f) return accel_mag_g;  // correction off
    const float w_rad_s = gyro_mag_dps * 0.017453292519943295f;  // pi/180
    const float rot_g   = (w_rad_s * w_rad_s * spin_lever_m) / g;
    // Uncorrectable-sample guard (2026-08-12 gyro-crash-hunt, confirmed by
    // repro): if the centripetal term exceeds anything the +-16 g accel can
    // even read, this sample cannot be corrected — subtracting it just
    // MANUFACTURES free-fall. A railed gyro with the lever armed used to
    // pin the detector in a CANDIDATE->AIRBORNE->reject livelock this way,
    // erase real landing spikes, and fabricate jumps once the lever
    // collapsed. Pass the raw magnitude through instead: wrong-but-bounded
    // beats confidently zero.
    if (rot_g > 16.0f) return accel_mag_g;
    const float sq      = accel_mag_g * accel_mag_g - rot_g * rot_g;
    return sq > 0.0f ? sqrtf(sq) : 0.0f;
  }

  // Gyro-aware feed. Same contract as the accel-only update() below, but the
  // sample is spin-corrected first. This is the overload a 6-axis board
  // (the Sense's LSM6DS3) should call on every sample; the accel-only one
  // remains correct for straight airs and is all a 3-axis v1 board can do.
  bool update(float t_s, float accel_mag_g, float gyro_mag_dps, JumpEvent& out) {
    return update(t_s,
                  correct_for_spin(accel_mag_g, gyro_mag_dps, p_.spin_lever_m, p_.g),
                  out);
  }

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
  // The lever arm is a third calibration term, but a MOUNT property rather
  // than a motion one: it changes when the puck is strapped somewhere new,
  // not when the rider improves. Separate setter so re-calibrating a mount
  // never disturbs a hard-won airtime offset / height scale.
  void set_spin_lever_m(float spin_lever_m) { p_.spin_lever_m = spin_lever_m; }
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
