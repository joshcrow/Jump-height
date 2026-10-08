// Host harness for event_capture.h + event_format.h (spec 2026-10-07 §8.1
// T-C1/T-C2). Runs a scripted op list from stdin through the REAL capture
// class into an in-memory sink, printing every page it consumes and the
// counters. tools/tests/test_event_codec.py runs the same script through
// sim/event_policy.py (the Python mirror) and requires byte-identical output,
// then decodes the pages with sim/event_codec.py to check the policy's
// semantics.
//
// Ops, one per line ('#' and blank lines ignored):
//   R <used> <total>        set_region
//   E <0|1>                 set_enabled
//   S <t_us> <ax> <ay> <az> <gx> <gy> <gz> <gyro_ok> <mag> <skip>
//                           note_poll + on_sample + service(skip)
//   P <t_us> <ax> <ay> <az> note_poll only (an idle poll)
//   V <skip>                service(skip) only (an idle pass)
//   J <session_n> <stored_n> <takeoff_s> <airtime_raw_s> <height_m>
//                           link_jump (before the next S)
//   C <reason>              close(reason)
//   D                       drain()
//   W ok | defer <n> | fail <n> | cost <us>
//                           sink behaviour: next n writes deferred / failed,
//                           or every write advances the fake clock by <us>
//   Q                       print the counters line
// Output:
//   PAGE <idx> <ok|fail> <512 hex chars>   one per consumed page
//   STAT key=value ...                     on Q and at EOF
//
// Build (done by the test):
//   g++ -std=c++14 -Wall -Wextra -I firmware/include \
//       firmware/test/event_capture_harness.cpp -o event_capture_harness
//
// SPDX-License-Identifier: MIT

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>

#include "event_config.h"

namespace {

struct Sink {
  int64_t clock_us = 0;
  int64_t cost_us = 0;
  int defer_n = 0;
  int fail_n = 0;
  int index = 0;
  int holds = 0;
};

Sink g_sink;

int writePage(void* ctx, const uint8_t* page) {
  Sink* s = (Sink*)ctx;
  if (s->defer_n > 0) { --s->defer_n; return -1; }
  s->clock_us += s->cost_us;
  const bool fail = s->fail_n > 0;
  if (fail) --s->fail_n;
  std::printf("PAGE %d %s ", s->index++, fail ? "fail" : "ok");
  for (uint32_t i = 0; i < jh_event::PAGE_BYTES; ++i) std::printf("%02x", page[i]);
  std::printf("\n");
  return fail ? 0 : 1;
}

void provenance(void*, jh_event::Provenance* p) {
  const uint8_t regs[7] = {0x54, 0x5C, 0x44, 0x02, 0x00, 0x00, 0x00};
  memcpy(p->regs, regs, 7);
  p->regs_ok = 1;
  p->temp_raw = 512;
  p->temp_ok = 1;
  p->g_baseline = 1.029f;
  p->gyro_bias[0] = 0.5f;
  p->gyro_bias[1] = -0.25f;
  p->gyro_bias[2] = 1.0f;
  p->detector_state = 0;
  p->spin_lever_m = 0.0f;
  p->airtime_offset_s = 0.0192f;
  p->height_scale = 1.0f;
}

bool readTemp(void*, int16_t* raw) { *raw = 768; return true; }
int64_t nowUs(void* ctx) { return ((Sink*)ctx)->clock_us; }
void wakeHold(void* ctx, bool hold) { ((Sink*)ctx)->holds += hold ? 1 : 0; }

jh_event::Capture g_cap;

void printStats() {
  const jh_event::Capture& c = g_cap;
  std::printf("STAT used=%u total=%u open=%d full=%d events_boot=%u crossings=%u "
              "refused_budget=%u refused_full=%u ring_overrun=%u write_fail=%u "
              "dup_polls=%u late_polls=%u max_page_write_us=%u pages_over_slack=%u "
              "trig_dropped=%u links_lost=%u pending=%d m_us=%llu W=%u\n",
              c.used_pages(), c.total_pages(), c.open() ? 1 : 0, c.full() ? 1 : 0,
              c.events_boot(), c.crossings(), c.refused_budget(), c.refused_full(),
              c.ring_overrun(), c.write_fail(), c.dup_polls(), c.late_polls(),
              c.max_page_write_us(), c.pages_over_slack(), c.trig_dropped(),
              c.links_lost(), c.pending() ? 1 : 0, (unsigned long long)c.m_us(),
              c.window_pages());
}

}  // namespace

int main() {
  jh_event::Hooks h;
  h.ctx = &g_sink;
  h.write_page = writePage;
  h.provenance = provenance;
  h.read_temp = readTemp;
  h.now_us = nowUs;
  h.wake_hold = wakeHold;
  const char src[8] = {'h', 'a', 'r', 'n', 'e', 's', 's', '0'};
  g_cap.init(jh_event::config_from_params(), h, 0xA1B2C3D4u, 1000000, src);
  g_cap.set_region(0, jh_event::REGION_PAGES);
  g_cap.set_enabled(true);

  std::string line;
  while (std::getline(std::cin, line)) {
    if (line.empty() || line[0] == '#') continue;
    std::istringstream iss(line);
    std::string op;
    iss >> op;
    if (op == "R") {
      unsigned long used = 0, total = 0;
      iss >> used >> total;
      g_cap.set_region((uint32_t)used, (uint32_t)total);
    } else if (op == "E") {
      int en = 1;
      iss >> en;
      g_cap.set_enabled(en != 0);
    } else if (op == "S") {
      long long t = 0;
      int v[6] = {0, 0, 0, 0, 0, 0};
      int gyro_ok = 1, skip = 0;
      std::string mag_s;
      iss >> t >> v[0] >> v[1] >> v[2] >> v[3] >> v[4] >> v[5] >> gyro_ok >> mag_s >> skip;
      const float mag = std::strtof(mag_s.c_str(), nullptr);
      int16_t raw[6];
      for (int k = 0; k < 6; ++k) raw[k] = (int16_t)v[k];
      g_sink.clock_us = t;
      g_cap.note_poll(t, raw);
      g_cap.on_sample(t, raw, gyro_ok != 0, mag);
      g_cap.service(skip != 0);
    } else if (op == "P") {
      long long t = 0;
      int a[3] = {0, 0, 0};
      iss >> t >> a[0] >> a[1] >> a[2];
      int16_t raw[3] = {(int16_t)a[0], (int16_t)a[1], (int16_t)a[2]};
      g_sink.clock_us = t;
      g_cap.note_poll(t, raw);
    } else if (op == "V") {
      int skip = 0;
      iss >> skip;
      g_cap.service(skip != 0);
    } else if (op == "J") {
      unsigned long sn = 0, st = 0;
      std::string to, ar, hm;
      iss >> sn >> st >> to >> ar >> hm;
      g_cap.link_jump((uint32_t)sn, (uint32_t)st, std::strtod(to.c_str(), nullptr),
                      std::strtof(ar.c_str(), nullptr), std::strtof(hm.c_str(), nullptr));
    } else if (op == "C") {
      int reason = jh_event::CLOSE_COMMAND;
      iss >> reason;
      g_cap.close((uint8_t)reason);
    } else if (op == "D") {
      const bool ok = g_cap.drain();
      std::printf("DRAIN ok=%d\n", ok ? 1 : 0);
    } else if (op == "W") {
      std::string mode;
      long long n = 0;
      iss >> mode >> n;
      if (mode == "ok") { g_sink.defer_n = 0; g_sink.fail_n = 0; }
      else if (mode == "defer") g_sink.defer_n = (int)n;
      else if (mode == "fail") g_sink.fail_n = (int)n;
      else if (mode == "cost") g_sink.cost_us = n;
    } else if (op == "Q") {
      printStats();
    } else {
      std::printf("ERROR unknown_op=%s\n", op.c_str());
    }
  }
  printStats();
  return 0;
}
