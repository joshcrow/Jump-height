// mock_flash.cpp — implementation of the RAM-backed mock declared in
// mock_flash.h. See that file's header comment for the full rationale;
// this file is deliberately the ONLY translation unit that touches the
// actual backing bytes (everything else — jh_store.cpp, the harness — goes
// through Adafruit_SPIFlashBase's public methods or the mock_flash_test
// hooks), so there is exactly one place that has to get flash semantics
// right.
//
// SPDX-License-Identifier: MIT

#include "mock_flash.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>

namespace {

// The "chip": one process-wide backing image, matching there being exactly
// one QSPI flash chip on the real board and exactly one Adafruit_SPIFlashBase
// instance in jh_store.cpp. A vector<uint8_t> would work equally well; a
// raw buffer keeps this file free of any container semantics that could
// blur "this is literally the chip's bytes."
uint8_t* g_ram              = nullptr;
uint32_t g_total_size       = 0;
std::FILE* g_backing_file   = nullptr;  // nullptr => transient, no persistence

bool     g_fault_armed      = false;
uint32_t g_fault_remaining  = 0;

// Non-fatal failure injection (review-store.md finding #1 — see mock_flash.h's
// "NON-FATAL FAILURE INJECTION" note): distinct from g_fault_armed above,
// which tears the whole process down; these two return cleanly instead, so
// the SAME process/power-cycle can keep issuing commands afterward.
bool     g_write_short_armed = false;
uint32_t g_write_short_n     = 0;
bool     g_erase_fail_armed  = false;

// Writes through [addr, addr+len) to the backing file, if one is open, and
// flushes immediately — a real power cut can land right after this, so
// nothing here should be left sitting in a buffer this process still owns.
void persistRange(uint32_t addr, uint32_t len) {
  if (!g_backing_file || len == 0) return;
  std::fseek(g_backing_file, (long)addr, SEEK_SET);
  std::fwrite(g_ram + addr, 1, len, g_backing_file);
  std::fflush(g_backing_file);
}

// Aborts loudly on an out-of-bounds [address, address+len) request
// (review-store.md finding #4 — see mock_flash.h's own note on why this is
// deliberately STRICTER than real hardware). len==0 is always in-bounds
// regardless of address (a vacuous, no-op request).
void abortIfOutOfBounds(const char* who, uint32_t address, uint32_t len) {
  if (len == 0) return;
  if (address >= g_total_size || len > g_total_size - address) {
    std::fprintf(stderr,
                 "mock_flash: OOB %s address=%u len=%u total_size=%u\n",
                 who, address, len, g_total_size);
    std::fflush(stderr);
    std::abort();
  }
}

}  // namespace

Adafruit_SPIFlashBase::Adafruit_SPIFlashBase(Adafruit_FlashTransport_QSPI* transport)
    : transport_(transport), total_size_(0) {}

bool Adafruit_SPIFlashBase::begin(SPIFlash_Device_t const* flash_devs, size_t count) {
  (void)transport_;  // stored only for API-shape parity with the real ctor; unused otherwise
  if (flash_devs == nullptr || count == 0) return false;

  total_size_ = flash_devs[0].total_size;
  g_total_size = total_size_;

  delete[] g_ram;
  g_ram = new uint8_t[total_size_];

  // JH_MOCK_FLASH_FILE unset => transient chip: blank (erased) RAM image,
  // nothing survives this process exiting. This is the common case for
  // every single-invocation scenario (most of them).
  const char* path = std::getenv("JH_MOCK_FLASH_FILE");
  if (g_backing_file) { std::fclose(g_backing_file); g_backing_file = nullptr; }

  bool loaded_existing = false;
  if (path && path[0] != '\0') {
    // Try to reuse an existing, correctly-sized image first — this is the
    // "fresh instance over same RAM" path a simulated reboot relies on:
    // this process is brand new, but the bytes a PRIOR process instance
    // (or a fault this same test set up beforehand) left behind must come
    // back exactly as they were.
    std::FILE* existing = std::fopen(path, "r+b");
    if (existing) {
      std::fseek(existing, 0, SEEK_END);
      const long existing_size = std::ftell(existing);
      if (existing_size == (long)total_size_) {
        std::fseek(existing, 0, SEEK_SET);
        const size_t got = std::fread(g_ram, 1, total_size_, existing);
        loaded_existing = (got == total_size_);
      }
      if (loaded_existing) {
        g_backing_file = existing;
      } else {
        std::fclose(existing);
      }
    }
  }

  if (!loaded_existing) {
    // Factory-blank chip: every never-written NOR flash byte reads as 0xFF.
    std::memset(g_ram, 0xFF, total_size_);
    if (path && path[0] != '\0') {
      std::FILE* fresh = std::fopen(path, "w+b");
      if (fresh) {
        std::fwrite(g_ram, 1, total_size_, fresh);
        std::fflush(fresh);
        g_backing_file = fresh;
      }
    }
  }

  return true;
}

uint32_t Adafruit_SPIFlashBase::size(void) { return total_size_; }

// QSPI word-alignment modeling (learned from real silicon, 2026-07-31 —
// jh_store.cpp align4()'s comment has the full story): the nRF52840 QSPI
// peripheral silently DROPS the low 2 bits of the flash address on bulk
// READ/WRITE ops, and nothing in nrfx/the Adafruit transport compensates,
// so an unaligned transfer lands up to 3 bytes early. The real bench
// symptom was ~98% of byte-packed trace blocks reading back torn. Model
// that same round-down here (with a stderr note for instant diagnosis)
// so any future byte-packed access pattern corrupts in the harness the
// exact way it corrupts on the bench — pre-silicon, where it's cheap.
static uint32_t roundDownUnaligned(const char* who, uint32_t address) {
  if ((address & 3u) == 0) return address;
  std::fprintf(stderr,
               "mock_flash: %s at UNALIGNED flash address 0x%08x — real QSPI "
               "hardware silently rounds down to 0x%08x (see jh_store.cpp "
               "align4()); modeling that.\n",
               who, address, address & ~3u);
  return address & ~3u;
}

uint32_t Adafruit_SPIFlashBase::readBuffer(uint32_t address, uint8_t* buffer, uint32_t len) {
  if (!g_ram) return 0;
  address = roundDownUnaligned("readBuffer", address);
  abortIfOutOfBounds("readBuffer", address, len);
  std::memcpy(buffer, g_ram + address, len);
  return len;
}

uint32_t Adafruit_SPIFlashBase::writeBuffer(uint32_t address, uint8_t const* buffer, uint32_t len) {
  if (!g_ram) return 0;
  address = roundDownUnaligned("writeBuffer", address);
  abortIfOutOfBounds("writeBuffer", address, len);

  // Non-fatal short-write injection (mock_flash_test::arm_write_short_
  // return()) — a ONE-SHOT cap applied BEFORE the real chunking loop below,
  // so everything from here down (page chunking, AND-merge, persistence)
  // runs exactly as it would for a genuinely shorter request; the only
  // difference from a real short write is that we already know in advance
  // how short, whereas real hardware would just stop wherever the
  // transient failure happened to land — a distinction without a
  // difference for what jh_store.cpp can observe (a return value less
  // than what it asked for).
  uint32_t remain = len;
  if (g_write_short_armed) {
    g_write_short_armed = false;
    remain = std::min(remain, g_write_short_n);
  }

  uint32_t cursor = address;
  const uint8_t* src = buffer;
  uint32_t applied_total = 0;

  // Real Adafruit_SPIFlashBase::writeBuffer() chunks at 256-byte PAGE
  // boundaries ("write one page at a time and must not go over page
  // boundary" — its own source comment) — mirrored here because it's
  // exactly the granularity a real mid-write power cut interrupts at.
  while (remain > 0) {
    const uint32_t page_left = MOCK_SFLASH_PAGE_SIZE - (cursor % MOCK_SFLASH_PAGE_SIZE);
    const uint32_t to_write = std::min(remain, page_left);

    uint32_t allowed = to_write;
    bool will_fault = false;
    if (g_fault_armed) {
      if (g_fault_remaining <= to_write) {
        allowed = g_fault_remaining;
        will_fault = true;
      }
    }

    // NOR flash program semantics: a write can only clear bits (1 -> 0);
    // setting a bit back to 1 requires an erase. Modeled literally as an
    // AND-merge against whatever is already there — this is what makes
    // "write into a not-actually-erased region" behave like real hardware
    // instead of silently doing the wrong thing.
    for (uint32_t i = 0; i < allowed; ++i) {
      g_ram[cursor + i] = (uint8_t)(g_ram[cursor + i] & src[i]);
    }
    persistRange(cursor, allowed);
    applied_total += allowed;
    if (g_fault_armed) g_fault_remaining -= allowed;

    if (will_fault) {
      // Simulated power loss: identical in spirit to the real thing —
      // whatever the caller (jh_store.cpp) believes about this write
      // having completed is simply never seen again. No destructors, no
      // atexit handlers, no further script commands in this process.
      // Flush stdout first so the harness's PRIOR, already-completed
      // commands' output has actually reached the pipe the Python test
      // reads — this call itself never returns.
      g_fault_armed = false;
      std::fflush(stdout);
      std::_Exit(mock_flash_test::kFaultExitCode);
    }

    remain -= to_write;
    cursor += to_write;
    src += to_write;
  }

  return applied_total;
}

bool Adafruit_SPIFlashBase::eraseSector(uint32_t sectorNumber) {
  if (!g_ram) return false;
  // Non-fatal erase-failure injection (mock_flash_test::arm_erase_failure())
  // — checked BEFORE touching any bytes, so an injected failure leaves the
  // target sector completely untouched, matching a real erase that simply
  // didn't happen (no partial-erase shape is modeled — see mock_flash.h).
  if (g_erase_fail_armed) { g_erase_fail_armed = false; return false; }
  const uint32_t addr = sectorNumber * MOCK_SFLASH_SECTOR_SIZE;
  // Stricter than real hardware (review-store.md finding #4, same rationale
  // as abortIfOutOfBounds() above): a sector number landing past the
  // chip's own last sector aborts loudly rather than silently doing
  // nothing (the old `if (addr >= total_size_) return false;` masked this
  // the same way OOB reads/writes used to).
  abortIfOutOfBounds("eraseSector", addr, MOCK_SFLASH_SECTOR_SIZE);
  std::memset(g_ram + addr, 0xFF, MOCK_SFLASH_SECTOR_SIZE);
  persistRange(addr, MOCK_SFLASH_SECTOR_SIZE);
  return true;
}

bool Adafruit_SPIFlashBase::eraseChip(void) {
  if (!g_ram) return false;
  // Same non-fatal erase-failure injection as eraseSector() above.
  if (g_erase_fail_armed) { g_erase_fail_armed = false; return false; }
  std::memset(g_ram, 0xFF, total_size_);
  persistRange(0, total_size_);
  return true;
}

namespace mock_flash_test {

uint32_t chip_size() { return g_total_size; }

void arm_fault_after_bytes(uint32_t n) {
  if (n == 0) {
    g_fault_armed = false;
    g_fault_remaining = 0;
    return;
  }
  g_fault_armed = true;
  g_fault_remaining = n;
}

bool fault_is_armed() { return g_fault_armed; }

void arm_write_short_return(uint32_t n) {
  g_write_short_armed = true;
  g_write_short_n = n;
}

void arm_erase_failure() {
  g_erase_fail_armed = true;
}

}  // namespace mock_flash_test
