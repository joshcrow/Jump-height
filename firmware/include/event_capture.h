// event_capture.h — WHEN to record six-axis events, and the one-page-per-
// pass writer (firmware batch 2, spec 2026-10-07 §3).
//
// Header-only and dependency-free: it includes event_format.h and nothing
// else. It never sees the detector, the gyro bias, the lever arm, the trace
// or the motion gate — main.cpp hands it the raw sample the detector has
// ALREADY consumed, plus a few provenance values through Hooks, and it hands
// back pages through Hooks::write_page. tools/tests/test_event_codec.py
// compiles this file on its own (only event_format.h beside it) and greps it
// for detector/trace symbols, so that boundary is checked, not promised.
//
// sim/event_policy.py is a line-for-line Python mirror. The harness
// (firmware/test/event_capture_harness.cpp) runs the same scripted input
// through both and the test requires byte-identical pages, so any change
// here is a change there too.
//
// THE POLICY (spec §3.2, normative):
//   * Every sample with |a| >= floor_g that is not inside a crossing span
//     starts one: a span of refractory_s whose peak is tracked and which
//     ends in exactly one TRIG summary entry — every crossing is logged,
//     captured or not, so selection bias is measurable afterwards.
//   * With no window open, a span sample >= tier_a_g opens a TIER_A window
//     if projected_used + W <= allowA(m); otherwise a sample >= tier_b_g
//     opens TIER_B if projected_used + W <= allowB(m). m = gate-open time
//     since boot. Both allowances are stateless functions of (used, m).
//   * A detector JUMP forces a window (if none is open and the region has
//     room) and links the jump to the open window.
//   * An open window's end moves to t + post_s on every >= tier_b sample,
//     capped at start + max_len_s.
//   * A new window's pre-portion starts at max(trigger - pre_s, the first
//     sample not already given to a window): no sample is written twice and
//     merged windows are contiguous by sample_seq.
//   * Region full means stop. Never overwrite. Nothing is erased here.
//
// THE WRITER. Samples go into a RAM ring (RING_N = 1280, 6.4 s at 200 Hz) as
// they arrive; service() writes AT MOST ONE 256-byte page per call, and
// main.cpp calls it once per loop pass but never in the pass that flushed
// the trace (spec §2.4). The 5 s pre-trigger backlog (~63 pages) drains at
// 16 samples per pass against 1 arriving, so it catches up in ~0.3 s. If an
// unwritten sample is ever about to be overwritten, the window closes with
// RING_OVERRUN and the count goes on evstat — never silent.
//
// SPDX-License-Identifier: MIT

#pragma once

#include "event_format.h"

namespace jh_event {

struct Config {
  float    floor_g;
  float    tier_a_g;
  float    tier_b_g;
  uint32_t refractory_us;
  uint32_t pre_us;
  uint32_t post_us;
  uint32_t max_len_us;
  uint64_t session_target_ms;
  uint32_t tier_a_burst_pm;    // per mille
  uint32_t tier_b_reserve_pm;
  uint32_t tier_b_burst_pm;
  uint32_t sample_hz;          // for W, the default window size in pages
  uint32_t page_slack_us;      // a page write longer than this is counted
};

// What main.cpp (or the harness) provides. Every function pointer is
// required; ctx is passed back untouched.
struct Hooks {
  void* ctx;
  // Write one 256-byte page. 1 = written, 0 = the write FAILED but the page
  // is consumed (the store advanced past it; nothing is ever re-written on
  // top of torn bytes), -1 = deferred (nothing happened; try again later).
  int (*write_page)(void* ctx, const uint8_t* page);
  // Register readback, temperature, g_baseline, gyro bias, detector state,
  // calibration — read once, on the first service() after a window opens.
  void (*provenance)(void* ctx, Provenance* out);
  // Temperature again, for END. Returns false when it could not be read.
  bool (*read_temp)(void* ctx, int16_t* raw);
  // Clock for the page-write instrumentation (max_page_write_us).
  int64_t (*now_us)(void* ctx);
  // Keep the flash chip out of deep power-down while any window is in the
  // pipeline (true), and let it sleep again (false).
  void (*wake_hold)(void* ctx, bool hold);
};

static const uint32_t RING_N = 1280;
// TRIG pages may hold at most this share (per mille) of the region's
// usable pages, counted ACROSS boots (set_region()'s trig_pages comes from
// the store's mount scan). Review 2026-10-08 S2: unbudgeted, 2 h of 3 g
// crossings every 0.5 s wrote 1,029 TRIG pages (50 % of the region) and no
// extra window; at the 4/s refractory ceiling the region would fill in
// about 2 h and refuse tier A. 100 = 205 of 2,056 pages = 2,870 entries,
// about 31 h at the vest's measured ~92 crossings/h. Past it, crossings are
// still counted (crossings, trig_over_budget) but no longer stored.
// Mirrored by sim/event_policy.py, which reads it from this line.
static const uint32_t TRIG_BUDGET_PM = 100;
static const uint8_t  RF_GYRO_BAD = 0x01;
// Set on a sample whose predecessor (in push order) is more than pre_us
// older, or that has no predecessor. A pre-window never reaches past it —
// which is also what keeps the 32-bit timestamp arithmetic below from
// aliasing across a long idle gap.
static const uint8_t  RF_GAP      = 0x02;

class Capture {
 public:
  static const uint32_t kMaxWindows = 4;
  static const uint32_t kLateUs     = 10000;   // a poll this late counts as late

  void init(const Config& cfg, const Hooks& hooks, uint32_t boot_id, int64_t t0_us,
            const char src8[8]) {
    // Field by field, never `*this = Capture()`: that temporary would be
    // ~23 KB on a 4 KB loop-task stack.
    resetState();
    cfg_ = cfg;
    hooks_ = hooks;
    boot_id_ = boot_id;
    t0_us_ = t0_us;
    memcpy(src_, src8, 8);
    const uint32_t win_samples = (uint32_t)(((uint64_t)(cfg.pre_us + cfg.post_us) * cfg.sample_hz) / 1000000u);
    W_ = (win_samples + SAMPLES_PER_PAGE - 1) / SAMPLES_PER_PAGE + 2;  // + BEGIN + END
  }

  // The region as the store sees it: pages already used (highest
  // non-erased + 1, from the mount scan) and its size. Called when capture
  // becomes enabled and after evclear/format.
  //
  // trig_pages = TRIG pages already in the region (jh_store::
  // events_trig_pages()), so the TRIG budget spans boots.
  void set_region(uint32_t used_pages, uint32_t total_pages, uint32_t trig_pages = 0) {
    used_ = used_pages;
    total_ = total_pages;
    P_ = total_pages > RESERVE_PAGES ? total_pages - RESERVE_PAGES : 0;
    trig_used_ = trig_pages;
    trig_cap_ = (uint32_t)((uint64_t)P_ * TRIG_BUDGET_PM / 1000u);
  }

  // Disabled = storage down or the events region unavailable. Anything in
  // the pipeline cannot be written and is dropped (counted).
  void set_enabled(bool en) {
    if (en == enabled_) return;
    enabled_ = en;
    if (!en) {
      for (uint32_t i = 0; i < nwin_; ++i) {
        Win& w = win(i);
        if (w.end_excl > w.write_seq) dropped_disabled_ += w.end_excl - w.write_seq;
      }
      nwin_ = 0;
      head_ = 0;
      if (trig_n_) { trig_dropped_ += trig_n_; trig_n_ = 0; }
      span_active_ = false;
      force_pending_ = false;
      releaseHold();
    }
  }
  bool enabled() const { return enabled_; }

  // Every successful accelerometer poll, active or not (main.cpp step 1).
  // Counters only: identical consecutive raw accel triples, and polls more
  // than kLateUs after the previous one.
  void note_poll(int64_t t_us, const int16_t accel[3]) {
    if (have_poll_) {
      if (accel[0] == last_poll_[0] && accel[1] == last_poll_[1] && accel[2] == last_poll_[2])
        ++dup_polls_;
      if (t_us - last_poll_t_ > (int64_t)kLateUs) ++late_polls_;
    }
    have_poll_ = true;
    last_poll_t_ = t_us;
    last_poll_[0] = accel[0]; last_poll_[1] = accel[1]; last_poll_[2] = accel[2];
  }

  // A detector JUMP, reported in the pass BEFORE this pass's on_sample().
  void link_jump(uint32_t session_n, uint32_t stored_n, double takeoff_s,
                 float airtime_raw_s, float height_m) {
    force_pending_ = true;
    pending_link_.session_n = session_n;
    pending_link_.stored_n = stored_n;
    pending_link_.takeoff_s = takeoff_s;
    pending_link_.airtime_raw_s = airtime_raw_s;
    pending_link_.height_m = height_m;
  }

  // One gate-active sample the detector has already consumed. No I/O.
  void on_sample(int64_t t_us, const int16_t raw[6], bool gyro_ok, float mag) {
    // m: gate-open time since boot, summed over consecutive active samples
    // less than 100 ms apart. Accumulated even while disabled — it is a
    // property of the session, not of the store.
    if (have_prev_) {
      const int64_t d = t_us - prev_t_;
      if (d > 0 && d < 100000) m_us_ += (uint64_t)d;
    }
    have_prev_ = true;
    prev_t_ = t_us;
    if (!enabled_) { force_pending_ = false; return; }
    // GAP is measured against the previous PUSHED sample (the ring's own
    // predecessor), not the previous active one: while disabled nothing is
    // pushed, and the ring's last entry may be hours old.
    const bool gap = !have_pushed_ || t_us - last_push_t_ > (int64_t)cfg_.pre_us;
    have_pushed_ = true;
    last_push_t_ = t_us;

    // 1. An open window whose end has passed closes BEFORE this sample.
    Win* ow = openWin();
    if (ow && t_us > ow->end_t) closeWin(*ow, ow->capped ? CLOSE_MAX_LEN : CLOSE_POST_EXPIRED);

    // 2. Ring overrun: pushing seq_next_ overwrites seq_next_ - RING_N.
    if (seq_next_ >= RING_N && nwin_ > 0) {
      const uint32_t victim = seq_next_ - RING_N;
      const uint32_t oldest = oldestUnwritten();
      if (oldest != NONE && victim >= oldest && victim < next_unassigned_) ringOverrun();
    }

    // 3. Push.
    const uint32_t s = seq_next_;
    const uint32_t idx = s % RING_N;
    ring_t_lo_[idx] = (uint32_t)(uint64_t)t_us;
    for (int k = 0; k < 6; ++k) ring_v_[idx][k] = raw[k];
    ring_f_[idx] = (uint8_t)((gyro_ok ? 0 : RF_GYRO_BAD) | (gap ? RF_GAP : 0));
    newest_t_ = t_us;
    ++seq_next_;
    ow = openWin();
    if (ow) { ow->end_excl = seq_next_; next_unassigned_ = seq_next_; }

    // 4. Extension: any >= tier_b sample moves an open window's end.
    const bool ext = ow && mag >= cfg_.tier_b_g;
    if (ext) extend(*ow, t_us);

    // 5. Floor crossings.
    if (span_active_ && t_us - span_start_ >= (int64_t)cfg_.refractory_us) finalizeSpan();
    if (!span_active_ && mag >= cfg_.floor_g) {
      span_active_ = true;
      span_start_ = t_us;
      span_peak_ = mag;
      span_dec_ = 0;
      span_eid_ = 0;
      ++crossings_;
    }
    if (span_active_) {
      if (mag > span_peak_) span_peak_ = mag;
      ow = openWin();
      if (ow) {
        note(ext ? DEC_EXTENDED : DEC_INSIDE_WINDOW, ow->event_id);
      } else if (mag >= cfg_.tier_b_g) {
        const bool a = mag >= cfg_.tier_a_g;
        const uint32_t proj = projectedUsed();
        const uint64_t m_ms = m_us_ / 1000u;
        if (nwin_ >= kMaxWindows) {
          note(DEC_REFUSED_BUSY, 0);
        } else if (proj + W_ > P_) {
          note(DEC_REFUSED_FULL, 0);
        } else if (a && proj + W_ <= allowA(m_ms)) {
          note(DEC_OPENED_A, openWindow(CAUSE_TIER_A, t_us, s, mag, proj, m_ms));
        } else if (proj + W_ <= allowB(m_ms)) {
          note(DEC_OPENED_B, openWindow(CAUSE_TIER_B, t_us, s, mag, proj, m_ms));
        } else {
          note(a ? DEC_REFUSED_BUDGET_A : DEC_REFUSED_BUDGET_B, 0);
        }
      } else {
        note(DEC_REFUSED_BELOW_TIER, 0);
      }
    }

    // 6. A detector JUMP forces a window and links to it.
    if (force_pending_) {
      force_pending_ = false;
      ow = openWin();
      uint8_t dec;
      const uint32_t proj = projectedUsed();
      if (ow) {
        dec = DEC_FORCED_DETECTOR;
      } else if (nwin_ >= kMaxWindows) {
        dec = DEC_REFUSED_BUSY;
        ++refused_full_;
      } else if (proj + W_ > P_) {
        dec = DEC_REFUSED_FULL;
        ++refused_full_;
      } else {
        openWindow(CAUSE_DETECTOR, t_us, s, mag, proj, m_us_ / 1000u);
        dec = DEC_FORCED_DETECTOR;
      }
      ow = openWin();
      if (ow) addLink(*ow, pending_link_);
      else ++links_lost_;
      TrigEntry te;
      te.t_us = (uint64_t)t_us;
      te.peak_mg = mg_from_g(mag);
      te.decision = dec;
      te.tier_hint = HINT_DETECTOR;
      te.event_id = ow ? ow->event_id : 0;
      te.used_pages = (uint16_t)projectedUsed();
      queueTrig(te);
    }
  }

  // Close the open window (idle, a command). IDLE and COMMAND also end any
  // crossing span now, so its TRIG entry is not left waiting for a sample
  // that may be hours away.
  void close(uint8_t reason) {
    Win* ow = openWin();
    if (ow) closeWin(*ow, reason);
    if (span_active_ && (reason == CLOSE_IDLE || reason == CLOSE_COMMAND)) finalizeSpan();
  }

  // Write at most one page. `skip` = the trace was flushed this pass, so
  // the flash stays the trace's alone. Returns true when a page was
  // consumed (written or failed).
  bool service(bool skip) {
    if (!enabled_) return false;
    applyHold();
    for (uint32_t i = 0; i < nwin_; ++i) {
      Win& w = win(i);
      if (!w.prov_taken) {
        hooks_.provenance(hooks_.ctx, &w.prov);
        w.prov_taken = true;
      }
    }
    if (skip) return false;
    return writeOne(false);
  }

  // Close the open window, end the span and write EVERYTHING pending,
  // including a partial TRIG page — for the commands that read the region
  // or power the board off. Bounded: a sink that keeps deferring cannot
  // hang a command. Returns true when nothing is left pending.
  bool drain() {
    close(CLOSE_COMMAND);
    if (!enabled_) return true;
    applyHold();
    for (uint32_t i = 0; i < nwin_; ++i) {
      Win& w = win(i);
      if (!w.prov_taken) { hooks_.provenance(hooks_.ctx, &w.prov); w.prov_taken = true; }
    }
    uint32_t deferred = 0;
    while (nwin_ > 0 || trig_n_ > 0) {
      if (!writeOne(true)) {
        if (++deferred > 8) return false;
      }
    }
    return true;
  }

  bool pending() const { return nwin_ > 0 || trig_n_ > 0; }

  // ---- evstat ----
  uint32_t used_pages() const { return used_; }
  uint32_t total_pages() const { return total_; }
  bool     open() const { return nwin_ > 0 && !win(nwin_ - 1).closed; }
  bool     full() const { return projectedUsed() + W_ > P_; }
  uint32_t window_pages() const { return W_; }
  uint32_t events_boot() const { return events_boot_; }
  uint32_t crossings() const { return crossings_; }
  uint32_t refused_budget() const { return refused_budget_; }
  uint32_t refused_full() const { return refused_full_; }
  uint32_t ring_overrun() const { return ring_overrun_; }
  uint32_t write_fail() const { return write_fail_; }
  uint32_t dup_polls() const { return dup_polls_; }
  uint32_t late_polls() const { return late_polls_; }
  uint32_t max_page_write_us() const { return max_write_us_; }
  uint32_t pages_over_slack() const { return over_slack_; }
  uint32_t trig_dropped() const { return trig_dropped_; }
  uint32_t trig_pages() const { return trig_used_; }
  uint32_t trig_over_budget() const { return trig_over_budget_; }
  uint32_t links_lost() const { return links_lost_; }
  uint32_t dropped_disabled() const { return dropped_disabled_; }
  uint32_t boot_id() const { return boot_id_; }
  uint64_t m_us() const { return m_us_; }

  uint32_t allowA(uint64_t m_ms) const {
    const uint64_t S = cfg_.session_target_ms;
    const uint64_t num = (uint64_t)cfg_.tier_a_burst_pm * S + (uint64_t)(1000u - cfg_.tier_a_burst_pm) * m_ms;
    const uint64_t den = 1000ull * S;
    if (num >= den) return P_;
    return (uint32_t)((uint64_t)P_ * num / den);
  }
  uint32_t allowB(uint64_t m_ms) const {
    const uint64_t S = cfg_.session_target_ms;
    const uint64_t cap_pm = 1000u - cfg_.tier_b_reserve_pm;
    const uint64_t num = (uint64_t)cfg_.tier_b_burst_pm * S + (uint64_t)(1000u - cfg_.tier_b_burst_pm) * m_ms;
    const uint64_t den = 1000ull * S;
    const uint64_t f = num >= den ? den : num;
    return (uint32_t)((uint64_t)P_ * cap_pm * f / (1000ull * den));
  }

 private:
  static const uint32_t NONE = 0xFFFFFFFFu;

  struct Win {
    uint16_t event_id = 0;
    uint8_t  cause = 0;
    bool     closed = false;
    uint8_t  close_reason = 0;
    bool     begin_written = false;
    bool     prov_taken = false;
    bool     capped = false;
    bool     break_pending = false;
    uint32_t start_seq = 0;
    uint32_t end_excl = 0;      // one past the last sample assigned
    uint32_t write_seq = 0;     // next sample to write
    int64_t  start_t = 0;
    int64_t  end_t = 0;
    int64_t  trig_t = 0;
    uint32_t trig_seq = 0;
    uint16_t trig_mg = 0;
    uint16_t used_at_open = 0;
    uint16_t allow_a = 0;
    uint16_t allow_b = 0;
    uint32_t m_s = 0;
    Provenance prov = Provenance();
    uint16_t page_seq = 0;
    uint32_t n_written = 0;
    uint32_t n_dropped = 0;
    uint16_t clip[6] = {0, 0, 0, 0, 0, 0};
    uint16_t gyro_bad = 0;
    uint16_t dup = 0;
    uint16_t late = 0;
    uint32_t max_dt = 0;
    bool     have_last = false;
    int16_t  last_v[3] = {0, 0, 0};
    int64_t  last_t = 0;
    int64_t  last_written_t = 0;
    uint32_t max_write_us = 0;
    uint16_t over_slack = 0;
    uint8_t  n_links = 0;
    uint8_t  links_overflow = 0;
    JumpLink links[MAX_LINKS] = {};
  };

  Win& win(uint32_t i) { return wins_[(head_ + i) % kMaxWindows]; }
  const Win& win(uint32_t i) const { return wins_[(head_ + i) % kMaxWindows]; }
  Win* openWin() {
    if (nwin_ == 0) return nullptr;
    Win& w = win(nwin_ - 1);
    return w.closed ? nullptr : &w;
  }

  int64_t t64(uint32_t seq) const {
    const uint32_t newest = (newest_seq());
    const uint32_t d = ring_t_lo_[newest % RING_N] - ring_t_lo_[seq % RING_N];
    return newest_t_ - (int64_t)d;
  }
  uint32_t newest_seq() const { return seq_next_ - 1; }

  uint32_t oldestUnwritten() const {
    for (uint32_t i = 0; i < nwin_; ++i) {
      const Win& w = win(i);
      if (w.write_seq < w.end_excl) return w.write_seq;
    }
    return NONE;
  }

  uint32_t projectedUsed() const {
    uint32_t p = used_;
    for (uint32_t i = 0; i < nwin_; ++i) {
      const Win& w = win(i);
      if (!w.begin_written) ++p;
      const uint32_t left = w.end_excl > w.write_seq ? w.end_excl - w.write_seq : 0;
      p += (left + SAMPLES_PER_PAGE - 1) / SAMPLES_PER_PAGE;
      ++p;  // END
    }
    if (trig_n_) ++p;
    return p;
  }

  void note(uint8_t dec, uint16_t eid) {
    if (rank(dec) > rank(span_dec_)) { span_dec_ = dec; span_eid_ = eid; }
  }
  static uint8_t rank(uint8_t d) {
    switch (d) {
      case DEC_OPENED_A: return 10;
      case DEC_OPENED_B: return 9;
      case DEC_EXTENDED: return 8;
      case DEC_INSIDE_WINDOW: return 7;
      case DEC_REFUSED_BUSY: return 6;
      case DEC_REFUSED_FULL: return 5;
      case DEC_REFUSED_BUDGET_A: return 4;
      case DEC_REFUSED_BUDGET_B: return 3;
      case DEC_REFUSED_BELOW_TIER: return 2;
      default: return 0;
    }
  }

  void finalizeSpan() {
    span_active_ = false;
    const uint8_t dec = span_dec_ ? span_dec_ : DEC_REFUSED_BELOW_TIER;
    if (dec == DEC_REFUSED_BUDGET_A || dec == DEC_REFUSED_BUDGET_B) ++refused_budget_;
    if (dec == DEC_REFUSED_FULL || dec == DEC_REFUSED_BUSY) ++refused_full_;
    TrigEntry te;
    te.t_us = (uint64_t)span_start_;
    te.peak_mg = mg_from_g(span_peak_);
    te.decision = dec;
    te.tier_hint = span_peak_ >= cfg_.tier_a_g ? HINT_TIER_A
                 : span_peak_ >= cfg_.tier_b_g ? HINT_TIER_B : HINT_FLOOR;
    te.event_id = span_eid_;
    te.used_pages = (uint16_t)projectedUsed();
    queueTrig(te);
  }

  void queueTrig(const TrigEntry& te) {
    if (trig_used_ >= trig_cap_) { ++trig_over_budget_; return; }
    if (trig_n_ >= TRIG_PER_PAGE) { ++trig_dropped_; return; }
    trig_[trig_n_++] = te;
  }

  void extend(Win& w, int64_t t) {
    int64_t e = t + (int64_t)cfg_.post_us;
    const int64_t cap = w.start_t + (int64_t)cfg_.max_len_us;
    if (e >= cap) { e = cap; w.capped = true; }
    if (e > w.end_t) w.end_t = e;
  }

  uint16_t openWindow(uint8_t cause, int64_t t, uint32_t s, float mag, uint32_t proj,
                      uint64_t m_ms) {
    // Pre-portion: walk back from the trigger while still within pre_us,
    // never past a GAP sample, never onto a sample another window owns,
    // never off the end of the ring.
    uint32_t lo = next_unassigned_;
    if (seq_next_ > RING_N && seq_next_ - RING_N > lo) lo = seq_next_ - RING_N;
    uint32_t first = s;
    const uint32_t t_lo_s = ring_t_lo_[s % RING_N];
    while (first > lo) {
      if (ring_f_[first % RING_N] & RF_GAP) break;
      const uint32_t cand = first - 1;
      if ((uint32_t)(t_lo_s - ring_t_lo_[cand % RING_N]) > cfg_.pre_us) break;
      first = cand;
    }
    Win& w = wins_[(head_ + nwin_) % kMaxWindows];
    w = Win();
    w.event_id = ++event_counter_;
    w.cause = cause;
    w.start_seq = first;
    w.write_seq = first;
    w.end_excl = s + 1;
    w.start_t = t64(first);
    w.end_t = t;
    w.trig_t = t;
    w.trig_seq = s;
    w.trig_mg = mg_from_g(mag);
    w.used_at_open = (uint16_t)proj;
    w.allow_a = (uint16_t)allowA(m_ms);
    w.allow_b = (uint16_t)allowB(m_ms);
    w.m_s = (uint32_t)(m_us_ / 1000000u);
    w.last_written_t = w.start_t;
    extend(w, t);
    if (nwin_ == 0) acquireHold();
    ++nwin_;
    next_unassigned_ = s + 1;
    ++events_boot_;
    return w.event_id;
  }

  void closeWin(Win& w, uint8_t reason) {
    w.closed = true;
    w.close_reason = reason;
  }

  void addLink(Win& w, const JumpLink& l) {
    if (w.n_links < MAX_LINKS) w.links[w.n_links++] = l;
    else if (w.links_overflow < 255) ++w.links_overflow;
  }

  void ringOverrun() {
    ++ring_overrun_;
    for (uint32_t i = 0; i < nwin_; ++i) {
      Win& w = win(i);
      if (w.end_excl > w.write_seq) {
        w.n_dropped += w.end_excl - w.write_seq;
        w.end_excl = w.write_seq;
        w.closed = true;
        w.close_reason = CLOSE_RING_OVERRUN;
      }
    }
  }

  // on_sample() does no I/O, and waking the flash is I/O: opening a window
  // only asks for the hold; service() takes it.
  void acquireHold() { hold_wanted_ = true; }
  void applyHold() {
    if (hold_wanted_ && !held_) { held_ = true; hooks_.wake_hold(hooks_.ctx, true); }
  }
  void releaseHold() {
    hold_wanted_ = false;
    if (held_) { held_ = false; hooks_.wake_hold(hooks_.ctx, false); }
  }

  void resetState() {
    W_ = P_ = total_ = used_ = 0;
    trig_used_ = trig_cap_ = trig_over_budget_ = 0;
    enabled_ = held_ = hold_wanted_ = false;
    seq_next_ = 0;
    newest_t_ = 0;
    have_prev_ = have_pushed_ = false;
    prev_t_ = last_push_t_ = 0;
    m_us_ = 0;
    next_unassigned_ = 0;
    head_ = nwin_ = 0;
    event_counter_ = 0;
    span_active_ = false;
    span_start_ = 0;
    span_peak_ = 0.0f;
    span_dec_ = 0;
    span_eid_ = 0;
    force_pending_ = false;
    trig_n_ = 0;
    trig_page_seq_ = 0;
    have_poll_ = false;
    last_poll_t_ = 0;
    events_boot_ = crossings_ = refused_budget_ = refused_full_ = 0;
    ring_overrun_ = write_fail_ = dup_polls_ = late_polls_ = 0;
    max_write_us_ = over_slack_ = trig_dropped_ = links_lost_ = dropped_disabled_ = 0;
  }

  void popWin() {
    head_ = (head_ + 1) % kMaxWindows;
    --nwin_;
    if (nwin_ == 0) releaseHold();
  }

  // Returns -1 deferred, 0 failed (consumed), 1 written. Instruments time.
  //
  // What the timing CAN see (review 2026-10-08 S4): only the write_page()
  // call. On the nRF52 that is Adafruit SPIFlash's writeBuffer(), which
  // waits for the PREVIOUS program to finish, issues this page's program and
  // returns without waiting for it (Adafruit_SPIFlashBase.cpp:494-509 in
  // 5.1.1), so this page's program time lands on the NEXT flash operation
  // and is attributed to nothing. now_us is also quantized to the 976.5625 us
  // FreeRTOS tick. max_page_write_us / pages_over_slack are therefore NOT
  // the page-program time; the bench gate judges the detector loop by
  // late_polls / dup_polls inside windows (END) against outside them.
  int writePage(Win* w) {
    const int64_t a = hooks_.now_us(hooks_.ctx);
    const int r = hooks_.write_page(hooks_.ctx, page_);
    const int64_t b = hooks_.now_us(hooks_.ctx);
    if (r < 0) return r;
    ++used_;
    if (r == 0) ++write_fail_;
    const uint32_t dt = b > a ? (uint32_t)(b - a) : 0;
    if (dt > max_write_us_) max_write_us_ = dt;
    if (dt > cfg_.page_slack_us) ++over_slack_;
    if (w) {
      if (dt > w->max_write_us) w->max_write_us = dt;
      if (dt > cfg_.page_slack_us && w->over_slack < 65535) ++w->over_slack;
    }
    return r;
  }

  bool writeTrig() {
    // Over the TRIG budget (only reachable when set_region() raised the
    // count under queued entries): counted, not stored.
    if (trig_used_ >= trig_cap_) {
      trig_over_budget_ += trig_n_;
      trig_n_ = 0;
      return false;
    }
    // TRIG pages may use the reserve, but always leave 2 pages for ENDs.
    if (used_ + 1 + 2 > total_) {
      trig_dropped_ += trig_n_;
      trig_n_ = 0;
      return false;
    }
    PageHeader h = PageHeader();
    h.type = PAGE_TRIG;
    h.count = (uint8_t)trig_n_;
    h.boot_id = boot_id_;
    h.event_id = 0;
    h.page_seq = trig_page_seq_;
    h.sample_seq = seq_next_;
    h.t_first_us = trig_[0].t_us;
    begin_page(page_, h);
    for (uint32_t i = 0; i < trig_n_; ++i) put_trig_entry(page_, i, trig_[i]);
    seal(page_);
    const int r = writePage(nullptr);
    if (r < 0) return false;
    ++trig_used_;
    ++trig_page_seq_;
    trig_n_ = 0;
    return true;
  }

  bool writeOne(bool flush) {
    if (trig_n_ >= TRIG_PER_PAGE) return writeTrig();
    if (nwin_ > 0) {
      Win& w = win(0);
      if (!w.begin_written) {
        if (used_ + 2 > P_) {
          // Not even BEGIN + END fit: the window never existed on flash.
          if (w.end_excl > w.write_seq) w.n_dropped += w.end_excl - w.write_seq;
          w.end_excl = w.write_seq;
          ++refused_full_;
          if (!w.closed) closeWin(w, CLOSE_REGION_FULL);
          popWin();
          return false;
        }
        buildBegin(w);
        const int r = writePage(&w);
        if (r < 0) return false;
        w.begin_written = true;
        w.page_seq = 1;
        return true;
      }
      if (w.write_seq < w.end_excl) {
        if (used_ + 2 > P_) {
          w.n_dropped += w.end_excl - w.write_seq;
          w.end_excl = w.write_seq;
          if (!w.closed) closeWin(w, CLOSE_REGION_FULL);
          else w.close_reason = CLOSE_REGION_FULL;
          // fall through to END below on the next call
          return false;
        }
        const uint32_t avail = w.end_excl - w.write_seq;
        if (w.closed || avail >= SAMPLES_PER_PAGE || breakWithin(w, avail)) return writeSamples(w);
        // An open window waiting for its next 16 samples.
      } else if (w.closed) {
        if (used_ + 1 > total_) {
          // Cannot happen with the reserve; if it does, the END is lost and
          // the window is still retired, never re-written.
          ++write_fail_;
          popWin();
          return false;
        }
        buildEnd(w);
        const int r = writePage(&w);
        if (r < 0) return false;
        popWin();
        return true;
      }
    }
    if (flush && trig_n_ > 0) return writeTrig();
    return false;
  }

  // True when a time break among the first `avail` unwritten samples would
  // end the page early anyway — then the short page can be written now.
  bool breakWithin(const Win& w, uint32_t avail) const {
    for (uint32_t k = 1; k < avail && k < SAMPLES_PER_PAGE; ++k) {
      const uint32_t seq = w.write_seq + k;
      if (ring_f_[seq % RING_N] & RF_GAP) return true;
      if (t64(seq) - t64(seq - 1) > 65535) return true;
    }
    return false;
  }

  bool writeSamples(Win& w) {
    PageHeader h = PageHeader();
    h.type = PAGE_SAMPLES;
    h.boot_id = boot_id_;
    h.event_id = w.event_id;
    h.page_seq = w.page_seq;
    h.sample_seq = w.write_seq;
    h.t_first_us = (uint64_t)t64(w.write_seq);
    const bool first_is_break = (ring_f_[w.write_seq % RING_N] & RF_GAP) || w.break_pending;
    h.flags = first_is_break && w.write_seq != w.start_seq ? FLAG_TIME_BREAK : 0;
    // Count the samples first: the header is written before the payload.
    uint32_t n = 0;
    bool brk = false;
    int64_t prev = 0;
    uint16_t mask = 0;
    while (n < SAMPLES_PER_PAGE && w.write_seq + n < w.end_excl) {
      const uint32_t seq = w.write_seq + n;
      const int64_t t = t64(seq);
      if (n > 0 && ((ring_f_[seq % RING_N] & RF_GAP) || t - prev > 65535)) { brk = true; break; }
      if (ring_f_[seq % RING_N] & RF_GYRO_BAD) mask |= (uint16_t)(1u << n);
      prev = t;
      ++n;
    }
    h.count = (uint8_t)n;
    h.gyro_bad_mask = mask;
    begin_page(page_, h);
    prev = 0;
    for (uint32_t i = 0; i < n; ++i) {
      const uint32_t seq = w.write_seq + i;
      const int64_t t = t64(seq);
      const uint16_t dt = i == 0 ? 0 : (uint16_t)(t - prev);
      put_sample(page_, i, dt, ring_v_[seq % RING_N]);
      prev = t;
    }
    seal(page_);
    const int r = writePage(&w);
    if (r < 0) return false;
    // Consumed: account the samples, written or not.
    for (uint32_t i = 0; i < n; ++i) {
      const uint32_t seq = w.write_seq + i;
      const int16_t* v = ring_v_[seq % RING_N];
      const int64_t t = t64(seq);
      for (int k = 0; k < 6; ++k)
        if ((v[k] == 32767 || v[k] == -32768) && w.clip[k] < 65535) ++w.clip[k];
      if ((ring_f_[seq % RING_N] & RF_GYRO_BAD) && w.gyro_bad < 65535) ++w.gyro_bad;
      if (w.have_last) {
        if (v[0] == w.last_v[0] && v[1] == w.last_v[1] && v[2] == w.last_v[2] && w.dup < 65535) ++w.dup;
        const int64_t d = t - w.last_t;
        if (d > (int64_t)kLateUs && w.late < 65535) ++w.late;
        if (d > (int64_t)w.max_dt) w.max_dt = (uint32_t)d;
      }
      w.have_last = true;
      w.last_v[0] = v[0]; w.last_v[1] = v[1]; w.last_v[2] = v[2];
      w.last_t = t;
      w.last_written_t = t;
    }
    if (r == 1) w.n_written += n;
    else w.n_dropped += n;
    w.write_seq += n;
    w.break_pending = brk;
    ++w.page_seq;
    return true;
  }

  void buildBegin(const Win& w) {
    PageHeader h = PageHeader();
    h.type = PAGE_BEGIN;
    h.boot_id = boot_id_;
    h.event_id = w.event_id;
    h.page_seq = 0;
    h.sample_seq = w.start_seq;
    h.t_first_us = (uint64_t)w.start_t;
    begin_page(page_, h);
    BeginInfo b;
    memcpy(b.src, src_, 8);
    b.t0_us = (uint64_t)t0_us_;
    b.trigger_t_us = (uint64_t)w.trig_t;
    b.trigger_sample_seq = w.trig_seq;
    b.trigger_mag_mg = w.trig_mg;
    b.cause = w.cause;
    b.used_pages = w.used_at_open;
    b.allow_a = w.allow_a;
    b.allow_b = w.allow_b;
    b.m_s = w.m_s;
    b.pre_ms = (uint16_t)(cfg_.pre_us / 1000u);
    b.post_ms = (uint16_t)(cfg_.post_us / 1000u);
    b.max_ms = (uint16_t)(cfg_.max_len_us / 1000u);
    put_begin_payload(page_, b, w.prov);
    seal(page_);
  }

  void buildEnd(Win& w) {
    PageHeader h = PageHeader();
    h.type = PAGE_END;
    h.boot_id = boot_id_;
    h.event_id = w.event_id;
    h.page_seq = w.page_seq;
    h.sample_seq = w.start_seq;
    h.t_first_us = (uint64_t)w.last_written_t;
    begin_page(page_, h);
    EndInfo e = EndInfo();
    e.n_samples = w.n_written;
    e.last_sample_seq = w.end_excl > 0 ? w.end_excl - 1 : 0;
    e.n_dropped = w.n_dropped;
    e.close_reason = w.close_reason;
    for (int k = 0; k < 6; ++k) e.clip[k] = w.clip[k];
    e.gyro_bad = w.gyro_bad;
    e.dup_polls = w.dup;
    e.late_polls = w.late;
    e.max_dt_us = w.max_dt;
    int16_t traw = 0;
    const bool tok = hooks_.read_temp(hooks_.ctx, &traw);
    e.temp_raw_end = tok ? traw : 0;
    e.temp_ok = tok ? 1 : 0;
    e.n_links = w.n_links;
    e.links_overflow = w.links_overflow;
    for (uint32_t i = 0; i < MAX_LINKS; ++i) e.links[i] = w.links[i];
    e.max_page_write_us = w.max_write_us;
    e.pages_over_slack = w.over_slack;
    put_end_payload(page_, e);
    seal(page_);
  }

  // ---- state ----
  Config   cfg_ = Config();
  Hooks    hooks_ = Hooks();
  uint32_t boot_id_ = 0;
  int64_t  t0_us_ = 0;
  char     src_[8] = {0, 0, 0, 0, 0, 0, 0, 0};
  uint32_t W_ = 0;
  uint32_t P_ = 0;
  uint32_t total_ = 0;
  uint32_t used_ = 0;
  uint32_t trig_used_ = 0;     // TRIG pages in the region, across boots
  uint32_t trig_cap_ = 0;      // P_ * TRIG_BUDGET_PM / 1000
  bool     enabled_ = false;
  bool     held_ = false;
  bool     hold_wanted_ = false;

  uint32_t ring_t_lo_[RING_N];
  int16_t  ring_v_[RING_N][6];
  uint8_t  ring_f_[RING_N];
  uint32_t seq_next_ = 0;
  int64_t  newest_t_ = 0;
  bool     have_prev_ = false;
  int64_t  prev_t_ = 0;
  bool     have_pushed_ = false;
  int64_t  last_push_t_ = 0;
  uint64_t m_us_ = 0;
  uint32_t next_unassigned_ = 0;

  Win      wins_[kMaxWindows];
  uint32_t head_ = 0;
  uint32_t nwin_ = 0;
  uint16_t event_counter_ = 0;

  bool     span_active_ = false;
  int64_t  span_start_ = 0;
  float    span_peak_ = 0.0f;
  uint8_t  span_dec_ = 0;
  uint16_t span_eid_ = 0;

  bool     force_pending_ = false;
  JumpLink pending_link_ = JumpLink();

  TrigEntry trig_[TRIG_PER_PAGE];
  uint32_t trig_n_ = 0;
  uint16_t trig_page_seq_ = 0;

  uint8_t  page_[PAGE_BYTES];

  bool     have_poll_ = false;
  int64_t  last_poll_t_ = 0;
  int16_t  last_poll_[3] = {0, 0, 0};

  uint32_t events_boot_ = 0;
  uint32_t crossings_ = 0;
  uint32_t refused_budget_ = 0;
  uint32_t refused_full_ = 0;
  uint32_t ring_overrun_ = 0;
  uint32_t write_fail_ = 0;
  uint32_t dup_polls_ = 0;
  uint32_t late_polls_ = 0;
  uint32_t max_write_us_ = 0;
  uint32_t over_slack_ = 0;
  uint32_t trig_dropped_ = 0;
  uint32_t trig_over_budget_ = 0;
  uint32_t links_lost_ = 0;
  uint32_t dropped_disabled_ = 0;
};

}  // namespace jh_event
