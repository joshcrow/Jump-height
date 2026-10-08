// jh_link.h — BLE link platform seam (see docs/sense.md §3.9).
//
// The wireless transport: mirrors the EXACT SAME newline-terminated protocol
// as the USB serial console over BLE, so a phone/laptop (Web Bluetooth) and
// a Garmin watch (Connect IQ BLE central) can read jumps and send commands
// wirelessly, the same way ./tools/jump does over USB — possibly with TWO
// concurrently connected centrals at once (rider's watch + beach phone) —
// see docs/watch.md#ble-link-dependability for the standing one-central rule.
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

#include "bootloader_arm.h"

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

// Bootloader entry, split in two so nothing resets on an unverified arm
// (firmware batch 2, spec 2026-10-07 §5; bench 2026-10-04: `uf2` entered
// the bootloader 1 time in 3 because the old reboot_to_uf2() discarded the
// SoftDevice's return codes and reset regardless).
//
// arm_bootloader(magic) writes GPREGRET through the SD-aware path and reads
// it back (bootloader_arm.h has the exact rule). It NEVER resets. Status
// UNSUPPORTED on a platform with no bootloader (the host), FAILED when a
// call errored or the readback differs. The magics: jh_boot::MAGIC_UF2
// (0x57, the UF2 drive — the 1200-baud touch reaches serial DFU only, and
// bootloader self-updates are MSC-only) and jh_boot::MAGIC_OTA (0xA8, OTA
// DFU for nRF Connect).
jh_boot::ArmResult arm_bootloader(uint8_t magic);
// Best-effort undo after a FAILED arm, so a later unrelated reset (the
// watchdog) cannot drop the board into a bootloader nobody asked for.
void disarm_bootloader();
// Flush what is queued for BLE, then reset. Call only after an OK arm.
// Never returns on a platform that has a bootloader; returns on the host.
void reset_now();

// Watchdog seam. ARM FIRST THING IN setup() — the 2026-08-12 dark-out hunt
// found the fatal window: jh_imu::init()/jh_store::init() used to run
// before the watchdog existed (it was armed inside begin()), so any hang
// there was PERMANENT — USB enumerates, CDC silent, BLE never starts,
// which is precisely the observed dark-board signature. Arming first
// requires every long setup operation to feed (see jh_store's chunked
// erase). No-ops on platforms without one.
void watchdog_init();
void watchdog_feed();

// Publish a battery snapshot for the advertisement payload. MUST be called
// from the loop() task only. The BLE connect callback runs on Bluefruit's
// AdaCallback task at TASK_PRIO_NORMAL, which PREEMPTS the loop task at
// TASK_PRIO_LOW — so the callback must never read the SAADC itself (see
// refreshAdvPayload in jh_link.cpp for the memory-corruption mechanism).
void publish_battery(int pct, int chg);

// Bytes the link gave up on after TX_RETRY_MAX attempts. Zero on a healthy
// session. Non-zero means the live stream lost data — the RECORDED session
// is unaffected (jh_store writes independently), but a client's view was
// incomplete, and that must be visible rather than silent.
uint32_t tx_drops();

}  // namespace jh_link
