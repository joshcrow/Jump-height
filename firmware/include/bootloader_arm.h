// bootloader_arm.h — arm the bootloader's GPREGRET magic and PROVE it took,
// before anyone says OK or resets (firmware batch 2, spec 2026-10-07 §5).
//
// WHY. Bench 2026-10-04 (docs/STATUS.md): `uf2` entered the bootloader 1
// time in 3 on src=5c80a436. reboot_to_uf2() called sd_power_gpregret_clr/
// _set, ignored both return codes, never read the register back, and reset
// regardless — after main.cpp had already sent `OK uf2`. A magic that did
// not stick therefore looked exactly like success, and the board quietly
// rebooted into its app. This file is the arming step alone, as a pure
// function over injected register accessors, so the host can test every
// branch (tools/tests/test_bootloader_arm.py) and the device runs the same
// code (jh_link.cpp supplies the real SoftDevice/register calls).
//
// Contract: arm() never resets. The caller resets only on ArmStatus::OK.
// SD enabled  -> sd_power_gpregret_clr(0, 0xFF), _set(0, magic), _get(0,&v):
//                OK only if all three return 0 (NRF_SUCCESS) and (v & 0xFF)
//                == magic.
// SD disabled -> NRF_POWER->GPREGRET = magic, read back: OK only if equal.
//                With the SD enabled that register is SD-owned and a direct
//                access hard-faults (jh_power.cpp's init() comment), which is
//                why the branch exists at all.
//
// What this does NOT fix (spec §5): the bootloader entered and then returned
// to the app within ~8 s on 2026-10-04. The leading hypothesis (H-WDT, the
// app's ~3.5 s watchdog surviving the soft reset) is UNVERIFIED and is a
// bench measurement, not something arming can change.
//
// SPDX-License-Identifier: MIT

#pragma once

#include <stdint.h>

namespace jh_boot {

enum class ArmStatus : uint8_t { OK = 0, UNSUPPORTED, FAILED };

struct ArmResult {
  ArmStatus status;
  bool      sd_enabled;
  uint32_t  rc_clr;     // SD path only; 0 on the register path
  uint32_t  rc_set;
  uint32_t  rc_get;
  uint32_t  readback;   // what the register held after arming
};

struct ArmOps {
  void* ctx;
  // nullptr sd_enabled means "this platform has no bootloader to arm".
  bool     (*sd_enabled)(void* ctx);
  uint32_t (*sd_clr)(void* ctx, uint32_t mask);
  uint32_t (*sd_set)(void* ctx, uint32_t value);
  uint32_t (*sd_get)(void* ctx, uint32_t* value);
  void     (*reg_write)(void* ctx, uint32_t value);
  uint32_t (*reg_read)(void* ctx);
};

// DFU_MAGIC_UF2_RESET and DFU_MAGIC_OTA_RESET, cores/nRF5/wiring.c:27-28.
static const uint8_t MAGIC_UF2 = 0x57;
static const uint8_t MAGIC_OTA = 0xA8;

inline ArmResult arm(const ArmOps& ops, uint8_t magic) {
  ArmResult r = {ArmStatus::UNSUPPORTED, false, 0, 0, 0, 0};
  if (ops.sd_enabled == nullptr) return r;
  r.sd_enabled = ops.sd_enabled(ops.ctx);
  if (r.sd_enabled) {
    r.rc_clr = ops.sd_clr(ops.ctx, 0xFF);
    r.rc_set = ops.sd_set(ops.ctx, magic);
    uint32_t v = 0;
    r.rc_get = ops.sd_get(ops.ctx, &v);
    r.readback = v;
    const bool ok = r.rc_clr == 0 && r.rc_set == 0 && r.rc_get == 0 && (v & 0xFFu) == magic;
    r.status = ok ? ArmStatus::OK : ArmStatus::FAILED;
  } else {
    ops.reg_write(ops.ctx, magic);
    r.readback = ops.reg_read(ops.ctx);
    r.status = (r.readback & 0xFFu) == magic ? ArmStatus::OK : ArmStatus::FAILED;
  }
  return r;
}

}  // namespace jh_boot
