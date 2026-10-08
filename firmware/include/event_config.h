// event_config.h — event_capture.h's Config, built from config/params.json's
// `capture` section (via the generated params.gen.h). Kept OUT of
// event_capture.h so that file stays dependency-free (it compiles with only
// event_format.h beside it — tools/tests/test_event_codec.py checks that).
// main.cpp and firmware/test/event_capture_harness.cpp both build their
// Config here, and sim/event_policy.py derives the identical numbers from
// the JSON (the harness parity test would fail on any drift).
//
// SPDX-License-Identifier: MIT

#pragma once

#include "event_capture.h"
#include "params.gen.h"

namespace jh_event {

// A page write longer than this is counted in pages_over_slack. 4.5 ms is
// the spec's "about 4.5 ms of slack per pass" at 200 Hz (5 ms per pass, the
// rest of the pass's work assumed to be ~0.5 ms) — a chosen threshold, not
// a measurement; the bench gate (spec §8.3-3) reads the count it produces.
// Mirrored by sim/event_policy.py, which reads it from this line.
static const uint32_t PAGE_SLACK_US = 4500;

inline Config config_from_params() {
  Config c;
  c.floor_g = JH_CAPTURE_FLOOR_G;
  c.tier_a_g = JH_CAPTURE_TIER_A_G;
  c.tier_b_g = JH_CAPTURE_TIER_B_G;
  c.refractory_us = (uint32_t)((double)JH_CAPTURE_REFRACTORY_S * 1000000.0 + 0.5);
  c.pre_us = (uint32_t)((double)JH_CAPTURE_PRE_S * 1000000.0 + 0.5);
  c.post_us = (uint32_t)((double)JH_CAPTURE_POST_S * 1000000.0 + 0.5);
  c.max_len_us = (uint32_t)((double)JH_CAPTURE_MAX_LEN_S * 1000000.0 + 0.5);
  c.session_target_ms = (uint64_t)JH_CAPTURE_SESSION_TARGET_S * 1000u;
  c.tier_a_burst_pm = (uint32_t)((double)JH_CAPTURE_TIER_A_BURST * 1000.0 + 0.5);
  c.tier_b_reserve_pm = (uint32_t)((double)JH_CAPTURE_TIER_B_RESERVE * 1000.0 + 0.5);
  c.tier_b_burst_pm = (uint32_t)((double)JH_CAPTURE_TIER_B_BURST * 1000.0 + 0.5);
  c.sample_hz = JH_SAMPLE_HZ;
  c.page_slack_us = PAGE_SLACK_US;
  return c;
}

}  // namespace jh_event
