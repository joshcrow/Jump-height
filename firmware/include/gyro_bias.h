// gyro_bias.h
//
// Pre-takeoff stationary bias subtraction for the gyro — the step
// docs/gyro-sim-plan.md §4 calls MANDATORY and DECISIONS.md #29 lists as
// part of the gyro configuration.
//
// Why it is not optional. A MEMS gyro's zero-rate level (ZRL) is spec'd at
// ±10 dps on this part, and it drifts with temperature. Two separate costs:
//
//   * For rotation COUNTING (the trick metrics this enables later), ±10 dps
//     integrated over a 3 s flight is ~30 degrees of pure fiction.
//   * For the omega^2*r height correction on the detector hot path
//     (jump_detector.h), the bias enters SQUARED. A true rate w with bias b
//     reads (w+b)^2 = w^2 + 2wb + b^2 — the 2wb cross-term is the one that
//     bites, and it scales with the spin you are trying to measure.
//
// The estimator: a slow exponential moving average of each axis, updated
// ONLY while the detector says we are riding, never in flight. That is the
// "planing baseline" the plan asks for — a rider carving around still
// averages to near-zero rate over seconds, while the sensor's own offset
// does not average away. Freezing it in flight is the important half: a
// spinning board would otherwise poison the very baseline used to correct
// the spin.
//
// Deliberately per-axis, not on the magnitude: bias is a per-axis offset,
// and |g| of a biased zero is a positive number that no scalar correction
// can remove.
//
// No Arduino dependency — compiles on host, same rule as jump_detector.h.
//
// SPDX-License-Identifier: MIT

#pragma once

#include <math.h>

namespace jump {

class GyroBias {
 public:
  // alpha: EMA coefficient per sample. The default gives a time constant of
  // roughly 1/(alpha*ODR) = ~4.8 s at 208 Hz — long enough that carving
  // averages out, short enough to follow thermal drift across a session.
  explicit GyroBias(float alpha = 0.001f) : alpha_(alpha) {}

  // Feed one raw gyro sample (deg/s per axis). `riding` must be true only
  // when the board is NOT airborne — pass detector.state() == State::RIDING.
  // Returns the bias-corrected angular rate MAGNITUDE in deg/s, which is
  // what jump_detector.h's gyro-aware update() wants.
  float update(float gx, float gy, float gz, bool riding) {
    if (riding) {
      if (!seeded_) {
        // Seed on the first riding sample rather than crawling up from zero:
        // an EMA started at 0 would spend seconds pretending the bias is
        // small, which is exactly the window a first jump lands in.
        bx_ = gx; by_ = gy; bz_ = gz;
        seeded_ = true;
      } else {
        bx_ += alpha_ * (gx - bx_);
        by_ += alpha_ * (gy - by_);
        bz_ += alpha_ * (gz - bz_);
      }
    }
    const float cx = gx - bx_;
    const float cy = gy - by_;
    const float cz = gz - bz_;
    return sqrtf(cx * cx + cy * cy + cz * cz);
  }

  // Current estimate, for telemetry / self-test narration.
  float x() const { return bx_; }
  float y() const { return by_; }
  float z() const { return bz_; }
  bool seeded() const { return seeded_; }

 private:
  float alpha_;
  float bx_ = 0.0f, by_ = 0.0f, bz_ = 0.0f;
  bool  seeded_ = false;
};

}  // namespace jump
