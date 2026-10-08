// Host harness for bootloader_arm.h (spec 2026-10-07 §8.1 T-U1). Each case
// scripts the SoftDevice/register accessors and prints what arm() decided
// and which accessors it touched; tools/tests/test_bootloader_arm.py asserts.
//
// Usage: bootloader_arm_harness <sd 0|1> <rc_clr> <rc_set> <rc_get>
//                               <get_value> <reg_readback> <magic> [unsupported]
//
// SPDX-License-Identifier: MIT

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "bootloader_arm.h"

namespace {
struct Script {
  bool sd;
  uint32_t rc_clr, rc_set, rc_get, get_value, reg_readback;
  int sd_calls = 0, reg_calls = 0;
  uint32_t set_arg = 0, reg_written = 0;
};
bool sdEnabled(void* c) { return ((Script*)c)->sd; }
uint32_t sdClr(void* c, uint32_t) { ((Script*)c)->sd_calls++; return ((Script*)c)->rc_clr; }
uint32_t sdSet(void* c, uint32_t v) { Script* s = (Script*)c; s->sd_calls++; s->set_arg = v; return s->rc_set; }
uint32_t sdGet(void* c, uint32_t* v) { Script* s = (Script*)c; s->sd_calls++; *v = s->get_value; return s->rc_get; }
void regWrite(void* c, uint32_t v) { Script* s = (Script*)c; s->reg_calls++; s->reg_written = v; }
uint32_t regRead(void* c) { Script* s = (Script*)c; s->reg_calls++; return s->reg_readback; }
}  // namespace

int main(int argc, char** argv) {
  if (argc < 8) { std::fprintf(stderr, "usage\n"); return 2; }
  Script s;
  s.sd = std::atoi(argv[1]) != 0;
  s.rc_clr = (uint32_t)std::strtoul(argv[2], nullptr, 0);
  s.rc_set = (uint32_t)std::strtoul(argv[3], nullptr, 0);
  s.rc_get = (uint32_t)std::strtoul(argv[4], nullptr, 0);
  s.get_value = (uint32_t)std::strtoul(argv[5], nullptr, 0);
  s.reg_readback = (uint32_t)std::strtoul(argv[6], nullptr, 0);
  const uint8_t magic = (uint8_t)std::strtoul(argv[7], nullptr, 0);
  const bool unsupported = argc > 8 && std::strcmp(argv[8], "unsupported") == 0;
  jh_boot::ArmOps ops = {&s, sdEnabled, sdClr, sdSet, sdGet, regWrite, regRead};
  if (unsupported) ops.sd_enabled = nullptr;
  const jh_boot::ArmResult r = jh_boot::arm(ops, magic);
  const char* st = r.status == jh_boot::ArmStatus::OK ? "OK"
                 : r.status == jh_boot::ArmStatus::FAILED ? "FAILED" : "UNSUPPORTED";
  std::printf("status=%s sd=%d rc=%u/%u/%u readback=0x%x sd_calls=%d reg_calls=%d "
              "set_arg=0x%x reg_written=0x%x\n", st, r.sd_enabled ? 1 : 0, r.rc_clr, r.rc_set,
              r.rc_get, r.readback, s.sd_calls, s.reg_calls, s.set_arg, s.reg_written);
  return 0;
}
