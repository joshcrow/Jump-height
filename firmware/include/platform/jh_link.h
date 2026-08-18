// jh_link.h — BLE link platform seam (see docs/sense.md §3.9).
//
// The wireless transport: mirrors the EXACT SAME newline-terminated protocol
// as the USB serial console over BLE, so a phone/laptop (Web Bluetooth) and
// a Garmin watch (Connect IQ BLE central) can read jumps and send commands
// wirelessly, the same way ./tools/jump does over USB — possibly with TWO
// concurrently connected centrals at once (rider's watch + beach phone), per
// docs/garmin-datafield.md §7.
//
// THE RULE, WRITTEN IN BLOOD (see the ESP32 implementation's TX-queue
// comments for the full reasoning this was learned from): nothing in this
// seam may block the sampling loop. write() only ever queues; pump() sends
// at most one paced chunk per call and must return in microseconds when
// idle. A synchronous send at a slow/negotiated-down MTU can stall sampling
// tens of milliseconds — long enough to swallow a landing spike. The one
// sanctioned exception is a bulk FILE dump from inside command handling,
// where sampling is already paused — write() may drain/pace inline there.
//
// ESP32: NimBLE-Arduino, exposing a Nordic UART Service
// (src/platform/esp32/jh_link.cpp — read its threading-model comment before
// touching it; the two-central mechanics there took real debugging to get
// right). A future platform's BLE stack (e.g. the Sense's Bluefruit
// BLEUart, itself already the Nordic UART Service — see docs/sense.md §3.1)
// must be RE-DERIVED against its own connection/subscribe/MTU semantics, not
// assumed to port 1:1 — docs/sense.md §3.1 lists exactly what to VERIFY.
//
// SPDX-License-Identifier: MIT

#pragma once

#include <Arduino.h>

namespace jh_link {

// Bring up the link and start advertising as `name`. Returns false if any
// init step fails; the caller reports it via the self-test's `ble` row but
// keeps running regardless — BLE is optional, jump tracking works over USB
// either way.
bool begin(const char* name);

// The name actually on the air — base plus a per-board suffix derived from
// factory silicon ID on platforms that have one ("" before begin()). Exists
// so INFO can tell a human WHICH puck they are talking to; the 2026-08-18
// impersonation incident is why that matters.
const char* local_name();

// Drain any received command bytes, dispatching each completed line through
// `handle` (the SAME dispatcher serial input uses, so a command is handled
// identically regardless of which transport it arrived on). Call once per
// loop() pass.
void poll(void (*handle)(const String& line));

// Send at most one paced output chunk if one is due. Call once per loop()
// pass; costs microseconds when idle or when there is nothing queued, so it
// never disturbs the sampling cadence.
void pump();

// Queue bytes for output, broadcast to every currently subscribed client in
// one call (callers never loop over connections themselves). No-op if
// nobody is subscribed. See the blocking rule above.
void write(const char* data, size_t len);

// True exactly once per new subscription: the caller sends a greet/banner so
// a freshly-subscribed client learns the link is alive.
bool takeGreetPending();

// Reboot into the bootloader's OTA-DFU mode. Returns FALSE if this platform
// has no such path (the caller reports ERR and carries on); on a platform
// that does, it never returns.
//
// The capability is expressed as a return value rather than an #ifdef,
// mirroring how `off` gates on jh_power::vbat_mv() >= 0 — main.cpp stays
// platform-neutral and the seam answers for itself.
bool reboot_to_dfu();

// Reboot into the bootloader's UF2 mass-storage mode (the drag-drop drive).
// Same contract as reboot_to_dfu(). Exists because the 1200-baud serial
// touch enters SERIAL-ONLY DFU by design (DFU_MAGIC_SERIAL_ONLY_RESET) —
// the UF2 drive is only offered under DFU_MAGIC_UF2_RESET, and bootloader
// self-update packages (update-*.uf2) are MSC-only.
bool reboot_to_uf2();

// Watchdog seam. ARM FIRST THING IN setup() — the 2026-08-12 dark-out hunt
// found the fatal window: jh_imu::init()/jh_store::init() used to run
// before the watchdog existed (it was armed inside begin()), so any hang
// there was PERMANENT — USB enumerates, CDC silent, BLE never starts,
// which is precisely the observed dark-board signature. Arming first
// requires every long setup operation to feed (see jh_store's chunked
// erase). No-ops on platforms without one.
void watchdog_init();
void watchdog_feed();

// Bytes the link gave up on after TX_RETRY_MAX attempts. Zero on a healthy
// session. Non-zero means the live stream lost data — the RECORDED session
// is unaffected (jh_store writes independently), but a client's view was
// incomplete, and that must be visible rather than silent.
uint32_t tx_drops();

}  // namespace jh_link
