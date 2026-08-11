// Harness for gyro_bias.h — the pre-takeoff stationary bias subtraction that
// docs/gyro-sim-plan.md §4 marks MANDATORY and DECISIONS.md #29 lists as part
// of the gyro configuration.
//
// Same shape as the other host harnesses (firmware/test/host_test.cpp,
// store_host/store_host_harness.cpp): a g++-built binary that prints one
// "PASS <name>" / "FAIL <name>" line per check and exits non-zero if any
// failed, driven from pytest (tools/tests/test_gyro_bias.py).
//
// Kept in C++ rather than mirrored into the simulator on purpose: bias is a
// PER-AXIS sensor property and the sim's detector is fed a scalar magnitude,
// so there is no meaningful Python twin to keep in parity with. The physics
// the simulator does own — the omega^2*r correction itself — is tested in
// tools/tests/test_spin_correction.py.
//
// SPDX-License-Identifier: MIT

#include <cmath>
#include <cstdio>

#include "gyro_bias.h"

static int g_fails = 0;

static void check(bool ok, const char* what) {
  std::printf("%s %s\n", ok ? "PASS" : "FAIL", what);
  if (!ok) g_fails++;
}

int main() {
  const float BX = 7.0f, BY = -3.0f, BZ = 2.0f;  // plausible ZRL, within ±10 dps

  // Seeds on the first riding sample instead of crawling up from zero. An EMA
  // starting at 0 would spend seconds insisting the bias is small — and that
  // window is exactly where a first jump lands.
  {
    jump::GyroBias gb;
    const float m = gb.update(BX, BY, BZ, true);
    check(std::fabs(m) < 0.01f, "seeds_immediately_biased_rest_reads_zero");
  }

  // Converges while riding, so a real rate on top of the bias reads truly.
  {
    jump::GyroBias gb;
    for (int i = 0; i < 20000; ++i) gb.update(BX, BY, BZ, true);
    const float m = gb.update(BX + 300.0f, BY, BZ, true);
    check(std::fabs(m - 300.0f) < 1.0f, "true_rate_reads_true_after_convergence");
  }

  // FROZEN in flight. This is the load-bearing half: if the baseline kept
  // updating while airborne, a spin would poison the very estimate used to
  // correct that spin, and the correction would eat itself.
  {
    jump::GyroBias gb;
    for (int i = 0; i < 20000; ++i) gb.update(BX, BY, BZ, true);
    const float before_x = gb.x(), before_y = gb.y(), before_z = gb.z();
    for (int i = 0; i < 5000; ++i) gb.update(BX + 600.0f, BY, BZ, false);
    const bool frozen = std::fabs(gb.x() - before_x) < 1e-6f &&
                        std::fabs(gb.y() - before_y) < 1e-6f &&
                        std::fabs(gb.z() - before_z) < 1e-6f;
    check(frozen, "baseline_frozen_while_airborne");
    const float m = gb.update(BX + 600.0f, BY, BZ, false);
    check(std::fabs(m - 600.0f) < 1.0f, "spin_still_reads_true_after_long_flight");
  }

  // Per-axis, not per-magnitude. A bias purely on one axis must vanish
  // entirely; a scalar correction on |g| could never do this.
  {
    jump::GyroBias gb;
    for (int i = 0; i < 20000; ++i) gb.update(0.0f, 0.0f, 9.0f, true);
    const float m = gb.update(0.0f, 0.0f, 9.0f, true);
    check(std::fabs(m) < 0.05f, "single_axis_bias_fully_removed");
  }

  // The cost this exists to avoid, stated as a number: uncorrected, the bias
  // enters the omega^2*r correction squared, via the 2*w*b cross-term.
  {
    const float w_true = 300.0f, b = 7.0f;
    const float sq_true = w_true * w_true;
    const float sq_biased = (w_true + b) * (w_true + b);
    const float err_pct = 100.0f * (sq_biased - sq_true) / sq_true;
    // ~4.7% error in the correction term from 7 dps of offset.
    check(err_pct > 4.0f && err_pct < 6.0f, "documents_uncorrected_bias_cost_pct");
    std::printf("# uncorrected %.0f dps bias at %.0f dps true rate = %.1f%% "
                "error in the omega^2 term\n", b, w_true, err_pct);
  }

  std::printf(g_fails ? "RESULT: FAIL\n" : "RESULT: PASS\n");
  return g_fails != 0;
}
