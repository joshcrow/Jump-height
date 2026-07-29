// mock_flash.h — host-side stand-in for the exact slice of the Adafruit
// SPIFlash API that firmware/src/platform/nrf52/jh_store.cpp calls, backed
// by a real-semantics RAM image of a QSPI NOR flash chip.
//
// WHY THIS EXISTS: jh_store.cpp has never run against real silicon (the
// board "arrives within days" — see firmware/SENSE_FIRST_BOOT.md's own
// framing). Its actual LOGIC — superblock format/validate, append-point
// recovery, the fullness cap, framed read-back, clear() — has nothing to do
// with the QSPI bus itself and everything to do with byte-level flash
// semantics that are perfectly host-testable if something plays the role of
// Adafruit_FlashTransport_QSPI / Adafruit_SPIFlashBase. That's this file.
//
// SCOPE / FIDELITY, stated plainly:
//   - This mirrors the exact call surface jh_store.cpp uses (confirmed by
//     grepping the real source): Adafruit_SPIFlashBase::begin/size/
//     readBuffer/writeBuffer/eraseSector/eraseChip, and
//     Adafruit_FlashTransport_QSPI::runCommand. Nothing else is implemented
//     because jh_store.cpp never calls anything else — see the comment
//     block above Adafruit_SPIFlashBase below for the full enumeration.
//   - Real flash semantics that DO matter to this file's power-loss logic
//     are modeled faithfully: erase sets every byte to 0xFF; a write can
//     only ever CLEAR bits (new = old & incoming — this is physically what
//     NOR flash program operations do), never set one back to 1 without an
//     erase; writes are chunked at 256-byte PAGE boundaries exactly like
//     the real Adafruit_SPIFlashBase::writeBuffer() (see its source, which
//     this mirrors: "write one page at a time and must not go over page
//     boundary"); erases operate on 4096-byte sectors.
//   - Real flash semantics that do NOT matter here and are deliberately NOT
//     modeled: JEDEC ID auto-detection (this mock's begin() just trusts the
//     one SPIFlash_Device_t it's handed — jh_store.cpp only ever hands it
//     exactly one, P25Q16H, so there is no "wrong chip" path to exercise),
//     realistic timing/current draw (that's the bench's job per
//     SENSE_FIRST_BOOT.md items 8/11/13 — this harness reports wall-clock
//     durations for order-of-magnitude sanity only, never asserts on them
//     as a hardware timing proxy), and interrupted ERASE operations (see
//     the fault-injection comment on writeBuffer() below — only writes are
//     interruptible, matching what was actually asked for).
//   - Deliberately NOT a base-class hierarchy: the real library has an
//     abstract Adafruit_FlashTransport so Adafruit_SPIFlashBase can sit on
//     top of either a QSPI or plain-SPI transport. jh_store.cpp only ever
//     constructs Adafruit_FlashTransport_QSPI and only ever hands
//     Adafruit_SPIFlashBase a pointer to THAT concrete type — so this mock
//     skips the polymorphic base entirely and types the constructor
//     directly against Adafruit_FlashTransport_QSPI*. A real port swap would
//     need the real hierarchy; a host test of THIS file does not.
//
// PERSISTENCE ACROSS "REBOOTS": jh_store.cpp's actual state (append
// offsets, counts, cached CSV byte total, ...) lives in ordinary C++
// namespace-scope statics inside its own anonymous namespace — there is no
// API to reset them short of a fresh process image, exactly like a real
// power-cycle resets the MCU's RAM. So "reboot" in the test harness means
// "run the harness binary again as a brand-new OS process" — and the only
// thing that needs to survive that process boundary is the flash CONTENTS,
// not any C++ object. This mock supports that by optionally backing its RAM
// image with a plain file on disk (set JH_MOCK_FLASH_FILE before calling
// jh_store::init(); every applied byte is written through immediately, so a
// process that dies mid-operation — real fault injection or a genuine crash
// — leaves the file in exactly the state real flash would be in). Leave the
// env var unset for a single-shot scenario that never needs to survive a
// process boundary: the chip is then a transient, blank (0xFF) RAM image
// that vanishes when the process exits.
//
// FAULT INJECTION: mock_flash_test::arm_fault_after_bytes(n) (declared at
// the bottom of this file, for the harness's own use — jh_store.cpp neither
// calls nor knows about it) arms a countdown, in bytes, across every
// subsequent writeBuffer() call (chip-wide, not per-call). When the
// countdown reaches zero PARTWAY through a page-chunked write, exactly the
// bytes up to that point are applied (and persisted, if a backing file is
// set) and the process is then torn down immediately via _Exit() — no
// destructors, no atexit handlers, no further script commands — the same
// abruptness a real power cut would impose. Whatever the calling code
// (jh_store.cpp) believed about that write having succeeded is simply never
// seen again by anyone; only the next fresh process's boot-time scan of the
// resulting bytes decides what was really and validly stored.
//
// SPDX-License-Identifier: MIT

#pragma once

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

// ---------------------------------------------------------------- geometry
// Mirrors the real library's enum (Adafruit_FlashTransport.h) — jh_store.cpp
// doesn't reference these names directly, but the mock's own implementation
// (mock_flash.cpp) does, and a test harness sizing fault-injection byte
// counts around page boundaries benefits from these being named constants
// rather than magic numbers repeated in two places.
enum {
  MOCK_SFLASH_SECTOR_SIZE = 4 * 1024,
  MOCK_SFLASH_PAGE_SIZE   = 256,
};

// ------------------------------------------------------- SPIFlash_Device_t
// Minimal stand-in for the real flash_devices.h struct — only the one field
// jh_store.cpp's call path actually reads through (Adafruit_SPIFlashBase::
// size(), which the real library implements as `_flash_dev->total_size`) is
// modeled. P25Q16H below matches the real flash_devices.h's own P25Q16H
// macro's total_size value exactly ((1 << 21) == 2,097,152 == 2 MiB) — that
// equality is the one fact this whole mock's byte-for-byte fidelity depends
// on, so it's called out here rather than left to coincidence.
typedef struct {
  uint32_t total_size;
} SPIFlash_Device_t;

#define P25Q16H \
  { .total_size = (1UL << 21) /* 2 MiB, matches the real P25Q16H macro */ }

// ------------------------------------------------------ Adafruit_FlashTransport_QSPI
// jh_store.cpp's ENTIRE use of this type: a default-constructed instance,
// and two runCommand() calls (0xAB release-deep-power-down, 0xB9
// enter-deep-power-down) whose return value it ignores. That's it — see
// flashWake()/flashSleep() in jh_store.cpp. No pin configuration, no
// begin()/end(), no read/write/erase path — Adafruit_SPIFlashBase owns all
// of that in the real library, and so does this mock (below), independent
// of this class entirely.
class Adafruit_FlashTransport_QSPI {
 public:
  Adafruit_FlashTransport_QSPI() : last_command_(0), command_count_(0) {}

  bool runCommand(uint8_t command) {
    last_command_ = command;
    ++command_count_;
    return true;
  }

  // Test-only visibility (not part of the real API, not used by
  // jh_store.cpp) — lets the harness optionally note wake/sleep bracketing
  // without having to guess from side effects. Entirely unused today but
  // cheap to keep for whoever adds that assertion later.
  uint8_t last_command() const { return last_command_; }
  uint32_t command_count() const { return command_count_; }

 private:
  uint8_t last_command_;
  uint32_t command_count_;
};

// ------------------------------------------------------------- Adafruit_SPIFlashBase
// jh_store.cpp's ENTIRE use of this type (grepped from the real source,
// firmware/src/platform/nrf52/jh_store.cpp):
//   Adafruit_SPIFlashBase s_flash(&s_transport);   // ctor
//   s_flash.begin(s_devices, 1)
//   s_flash.size()
//   s_flash.eraseChip()
//   s_flash.readBuffer(addr, buf, len)
//   s_flash.writeBuffer(addr, buf, len)
//   s_flash.eraseSector(sectorNumber)
// No readStatus/writeEnable/isReady/numPages/pageSize/getJEDECID/erasePage/
// eraseBlock/read8/16/32 — those are internal plumbing the REAL library
// uses to implement the six calls above; jh_store.cpp never touches them
// directly, so this mock doesn't implement them at all.
class Adafruit_SPIFlashBase {
 public:
  explicit Adafruit_SPIFlashBase(Adafruit_FlashTransport_QSPI* transport);

  bool begin(SPIFlash_Device_t const* flash_devs, size_t count);

  uint32_t size(void);

  uint32_t readBuffer(uint32_t address, uint8_t* buffer, uint32_t len);
  uint32_t writeBuffer(uint32_t address, uint8_t const* buffer, uint32_t len);

  bool eraseSector(uint32_t sectorNumber);
  bool eraseChip(void);

 private:
  Adafruit_FlashTransport_QSPI* transport_;
  uint32_t total_size_;
};

// ------------------------------------------------------------ test-only hooks
// NOT part of the Adafruit API surface, NOT visible to jh_store.cpp (which
// never includes this header's declarations directly — jh_store.cpp only
// ever spells the two Arduino-named headers, see shim/). Only
// firmware/test/store_host/store_host_harness.cpp calls these, to drive
// scenarios from its own scripted commands.
namespace mock_flash_test {

// Process exit code used to simulate an abrupt power cut (see
// arm_fault_after_bytes() below) — chosen to be distinctive in a harness
// subprocess's returncode, not a signal number, not 0/1 (which the harness
// uses for ordinary success/parse-error). Namespace-scope constexpr has
// internal linkage in C++ by default, so including this header from more
// than one translation unit (mock_flash.cpp, store_host_harness.cpp) is
// safe — no ODR violation, every TU just gets its own copy of the value.
constexpr int kFaultExitCode = 90;

// Total chip size as configured by the most recent begin() (0 before any
// begin() call) — lets the harness report geometry without hardcoding it
// twice.
uint32_t chip_size();

// Arms a countdown, in bytes, across all subsequent writeBuffer() calls
// (cumulative, chip-wide — not scoped to one call). 0 disarms. See the file
// header comment above for exactly what happens when it reaches zero
// mid-write. Re-arming (calling this again before it fires) simply replaces
// the previous countdown.
void arm_fault_after_bytes(uint32_t n);

// True once a fault has been armed and not yet consumed/disarmed — lets the
// harness sanity-check its own scripts (e.g. warn if a scenario expected a
// fault to fire but the countdown never reached zero because the write it
// was armed for turned out shorter than expected).
bool fault_is_armed();

}  // namespace mock_flash_test
