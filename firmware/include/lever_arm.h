// lever_arm.h
//
// Self-calibrating mount lever arm for jump_detector.h's omega^2*r spin
// correction — the "mount-position calibration step" that DECISIONS.md #29,
// docs/gyro-sim-plan.md §6, docs/data-pipeline.md and docs/algorithm.md all
// name as a REQUIREMENT but none specifies a method for.
//
// The method. In flight the board is in free fall, so the specific force it
// experiences is ~0 and whatever the accelerometer reads IS the rotation's own
// centripetal term:
//
//     |a| = omega^2 * r / g        (|a| in g, omega in rad/s, r in m)
//  => r   = |a| * g / omega^2
//
// So the firmware can measure its own lever arm from any spun jump. That
// matters more than it sounds: r is NOT a property of where the puck is stuck.
// It is the distance from the ROTATION AXIS to the puck, and the axis is set by
// the trick and the rider's body position — so it cannot be fixed by choosing a
// mounting spot with a tape measure. Measuring it per-jump is the only honest
// way to know it.
//
// Accuracy matters here: g4 showed r mis-set by ±20% reopens the failures the
// correction exists to close (break-point back down to 350 dps at r=0.3). In
// simulation this estimator lands within 0.1-3.2%, and it gets BETTER as spin
// increases (error scales as 1/omega^2) — i.e. it is most accurate exactly on
// the jumps that need the correction most.
//
// Three design choices that are load-bearing:
//
//  1. RAW accelerometer magnitude in, never the corrected one. Feeding the
//     corrected value would be circular — solving for r using a number that
//     already had r subtracted out of it.
//
//  2. Median, not mean. The airborne window's first and last samples are
//     takeoff and landing transients where specific force is emphatically NOT
//     zero; a landing spike is a huge |a| that would drag a mean anywhere. A
//     handful of contaminated samples cannot move a median of dozens.
//
//  3. The estimate is used UNSHAVED (kSafetyFactor = 1.0), and the reasoning
//     behind that is the least obvious thing in this file.
//
//     The instinct — and the rule this file originally shipped with — was to
//     err SHORT, because g4's landing-erasure probe showed an OVER-estimated r
//     drives the correction's sqrt argument negative on the landing spike and
//     erases the touchdown. True, but only half the picture: the two errors
//     push in OPPOSITE directions, so there is no safe side.
//
//       over-estimate: free-fall samples clamp to 0 -> reads as free fall,
//                      the gate is happy. Landing spike shrinks -> at LARGE
//                      over-estimate (g4 used +20%) the landing is erased.
//       under-estimate: the landing survives, but free-fall leaves a residual
//                      of rot_g*sqrt(1-k^2), and the sqrt AMPLIFIES small
//                      errors — k=0.99 leaves 14% of rot_g. At r=0.5 m and
//                      600 dps, rot_g is 5.6 g, so that residual is 0.79 g:
//                      twice the 0.35 g free-fall gate. Takeoff gets re-pinned
//                      mid-flight and height reads ~-95% low.
//
//     Measured: a 5% shave broke 5 of 8 (lever x spin) cases outright; no
//     shave fixed all 8, at +0.0% height error. The estimator's own natural
//     bias is a slight OVER-estimate (k = 1.003-1.026 measured), which is the
//     benign direction here and nowhere near the +20% that erases landings.
//
//     So: aim unbiased, and never deliberately under-shoot.
//
// No Arduino dependency — compiles on host. 1:1 mirror of sim/lever_arm.py.
//
// SPDX-License-Identifier: MIT

#pragma once

#include <math.h>

namespace jump {

class LeverArm {
 public:
  // Keep the N highest-omega samples of a flight. High omega is where the
  // estimate is sharpest (error ~ 1/omega^2), so evicting the weakest sample
  // when full concentrates the median on the best data rather than merely the
  // earliest.
  static const int kSlots = 64;

  // Below this rate the 1/omega^2 division amplifies noise faster than it
  // yields signal, and a straight air legitimately has no rotation to measure.
  // A jump with nothing above this simply produces no estimate — which is
  // correct: a jump that never spun needs no correction.
  static constexpr float kMinDps = 150.0f;

  // See design note 3: UNBIASED, deliberately. Kept as a named constant
  // rather than deleted so that the next person to reach for a safety margin
  // finds the reasoning instead of the instinct. Lowering this BREAKS high-spin
  // jumps — it is not a free safety knob.
  static constexpr float kSafetyFactor = 1.0f;

  // Per-jump blend. The lever arm is a mount property that only changes when
  // the puck is re-strapped, so trust accumulated estimates over any single
  // noisy flight — but stay able to follow a genuine re-mount within a few
  // jumps.
  static constexpr float kBlend = 0.35f;

  static constexpr float kG = 9.80665f;

  // Accelerometer saturation guard. lsm6ds3_min.h configures CTRL1_XL for
  // ±16 g; a sample at or near that rail is CLIPPED, so its magnitude is a
  // floor rather than a measurement — and a floor makes r read LOW, which is
  // the direction that re-pins takeoff and destroys the height.
  //
  // Found by experiment g5 (sim/experiments/g5_lever_tolerance.py): at
  // r=0.8 m and 900 dps the true centripetal term is 20.1 g, well past the
  // rail, and the unguarded estimator returned k=0.849 — a 15% under-estimate,
  // the only case out of twelve that fell outside the usable band. Discarding
  // railed samples leaves the highest UNCLIPPED ones, which are still the
  // sharpest available (error ~ 1/omega^2).
  //
  // If the accel range ever changes, this must change with it.
  static constexpr float kClipGuardG = 15.5f;
  // Gyro-side twin of kClipGuardG (2026-08-12 hunt): a railed omega makes r
  // read catastrophically LOW — the direction that collapses a converged
  // lever. Same argument, opposite sensor.
  static constexpr float kGyroClipGuardDps = 1900.0f;

  // Feed one sample taken while AIRBORNE.
  //   accel_mag_g_raw : UNCORRECTED accelerometer magnitude, in g
  //   gyro_mag_dps    : bias-corrected angular rate magnitude, in deg/s
  void observe(float accel_mag_g_raw, float gyro_mag_dps) {
    if (gyro_mag_dps < kMinDps) return;
    if (gyro_mag_dps >= kGyroClipGuardDps) return;  // railed omega: r reads low
    if (accel_mag_g_raw >= kClipGuardG) return;  // railed: a floor, not a reading
    const float w = gyro_mag_dps * 0.017453292519943295f;  // rad/s
    const float r = (accel_mag_g_raw * kG) / (w * w);
    if (!(r > 0.0f) || r > 3.0f) return;  // NaN-safe; 3 m is absurd for a mount

    if (n_ < kSlots) {
      dps_[n_] = gyro_mag_dps;
      r_[n_]   = r;
      n_++;
      return;
    }
    // Full: replace the weakest sample, but only if this one beats it.
    int weakest = 0;
    for (int i = 1; i < kSlots; ++i)
      if (dps_[i] < dps_[weakest]) weakest = i;
    if (gyro_mag_dps > dps_[weakest]) {
      dps_[weakest] = gyro_mag_dps;
      r_[weakest]   = r;
    }
  }

  // Close out a flight. Folds this flight's median into the running estimate
  // and clears the per-flight buffer. Returns true if an estimate was produced.
  // Call this whenever the detector leaves AIRBORNE, jump or no jump — a
  // rejected flight still carries perfectly good rotation data.
  bool commit() {
    // Too few samples to trust a median: discard rather than guess.
    if (n_ < 8) { n_ = 0; return false; }

    // Insertion sort — n_ <= 64 and this runs once per flight, not per sample.
    float s[kSlots];
    for (int i = 0; i < n_; ++i) s[i] = r_[i];
    for (int i = 1; i < n_; ++i) {
      const float key = s[i];
      int j = i - 1;
      while (j >= 0 && s[j] > key) { s[j + 1] = s[j]; j--; }
      s[j + 1] = key;
    }
    const float median = (n_ % 2) ? s[n_ / 2]
                                  : 0.5f * (s[n_ / 2 - 1] + s[n_ / 2]);
    n_ = 0;

    const float shaved = median * kSafetyFactor;
    value_ = has_ ? value_ + kBlend * (shaved - value_) : shaved;
    has_ = true;
    return true;
  }

  // Best current estimate in metres, or 0 if none yet — which is exactly the
  // value that makes jump_detector.h's correction the identity, so an
  // un-calibrated device behaves as it always did.
  float value() const { return has_ ? value_ : 0.0f; }
  bool  has_estimate() const { return has_; }
  int   pending_samples() const { return n_; }

  // For a re-mount: throw away what was learned about the old position.
  void reset() { has_ = false; value_ = 0.0f; n_ = 0; }

  // Drop PENDING observations only — the converged value_ survives. This is
  // what a non-jump AIRBORNE exit calls: the flight's samples are suspect
  // (phantom flights are all rejects), but a calibration already earned from
  // real landed jumps must not die with them.
  void discard() { n_ = 0; }

 private:
  float dps_[kSlots] = {0};
  float r_[kSlots]   = {0};
  int   n_    = 0;
  float value_ = 0.0f;
  bool  has_   = false;
};

}  // namespace jump
