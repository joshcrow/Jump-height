// jh_link.cpp — ESP32 implementation of the jh_link seam
// (firmware/include/platform/jh_link.h). See docs/sense.md §3.9.
//
// Formerly ble_link.h, moved here near-verbatim by the platform-seams
// refactor (git history follows the move) — namespace renamed ble_link ->
// jh_link and the 5 functions main.cpp calls given external linkage to match
// the seam header; everything else, including every comment below, is
// unchanged.
//
// Nordic UART Service (NUS) BLE link for the Jump Height firmware — a thin
// wrapper over NimBLE-Arduino that carries the EXACT same newline-terminated
// protocol as the USB serial console, so a phone/laptop (Web Bluetooth) AND a
// Garmin watch (Connect IQ BLE central) can read jumps and send commands the
// same way ./tools/jump does over USB — at the same time: this layer supports
// TWO concurrently connected BLE centrals (rider's watch + beach phone), per
// docs/garmin-datafield.md §7 (prerequisite for the M6 field trial).
//
// Why NimBLE (not the stock ESP32 BLE / Bluedroid stack): Bluedroid's flash and
// RAM footprint is large; NimBLE's is a fraction of it, and that budget is what
// lets the app + a big LittleFS fit in our no-OTA partition map (partitions.csv).
//
// Two centrals, mechanically: NimBLE-Arduino's own default
// CONFIG_BT_NIMBLE_MAX_CONNECTIONS is 3 (library src/nimconfig.h; no
// arduino-esp32 sdkconfig.h variant predefines it, so that default applies
// unmodified) — comfortably above the 2 we need, so no build_flags override
// is required. What DOES need help: NimBLE stops advertising the moment a
// central connects, and only auto-resumes advertising on a FAILED connect
// attempt or on a disconnect — never on a SUCCESSFUL connect (confirmed in
// NimBLEServer.cpp's handleGapEvent()). Left alone, the puck would go deaf to
// a second central the instant the first one paired. ServerCallbacks::
// onConnect() below is what restarts advertising while a slot remains.
//
// Threading model — the part that's easy to get wrong:
//   NimBLE runs its own host task. Every callback here (client writes, connect,
//   subscribe, MTU change, disconnect) fires on THAT task, never on loop(). So:
//     * Received command bytes are pushed into a fixed ring buffer guarded by a
//       portMUX spinlock; loop() drains it and runs the SAME handleCommand() as
//       serial, so every command is handled exactly once, on the loop() task.
//       This ring (and the line-assembly buffer downstream of it) is SHARED
//       across both centrals — by design, both speak the identical protocol
//       through the identical dispatcher. Two clients writing multi-fragment
//       commands in the same instant could in principle interleave bytes; in
//       practice commands are short (the watch writes one `stats` right after
//       connecting, per the Garmin spec; a human on the phone types
//       occasionally) and NimBLE serializes all host-task callbacks one at a
//       time, so this is a known, accepted narrow-window risk — not a silent
//       one — rather than something this change attempts to fix.
//     * Subscriber count / MTU / connection bookkeeping are plain scalars the
//       host task writes and loop() reads (single-writer/single-reader; a
//       stale read is harmless). s_sub_count replaces a single subscribed-bool
//       because either central can subscribe/unsubscribe independently of the
//       other (see TxCallbacks::onSubscribe). s_mtu tracks the MINIMUM MTU
//       seen across both centrals since connections last dropped to 0 (see
//       ServerCallbacks::onMTUChange), because notify() broadcasts one buffer
//       to every subscribed connection in a single call (confirmed in the
//       vendored library source, NimBLECharacteristic.cpp) — there is no
//       per-connection send, so a chunk size has to satisfy whichever
//       subscriber has the smallest MTU or that subscriber's stream corrupts.
//     * Notifications (device -> client) are only ever sent from loop() via the
//       emit layer in main.cpp, so the TX path has no cross-task contention.
//       One notify() call reaches every currently-subscribed central at once
//       (see above), so main.cpp never needs to loop over clients itself.
//
// This file is compiled only for the ESP32 platform (see platformio.ini's
// build_src_filter) and is never included by anything — main.cpp only sees
// firmware/include/platform/jh_link.h. It pulls in <NimBLEDevice.h>, so it
// must never be dragged into the host-side detector parity test (that test
// only ever builds jump_detector.h, which stays dependency-free).
//
// API note: written against NimBLE-Arduino 1.4.x (this repo pins ^1.4.2; 1.4.3
// is what's actually vendored under firmware/.pio/libdeps as of this writing).
// The 2.x release changed the characteristic/server callback signatures
// (ble_gap_conn_desc* -> NimBLEConnInfo&); the signatures below are the 1.4.x
// ones.
//
// SPDX-License-Identifier: MIT

#include "platform/jh_link.h"

#include <Arduino.h>
#include <NimBLEDevice.h>

namespace jh_link {

// Nordic UART Service — the de-facto "serial port over BLE" profile.
static const char* SVC_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E";
static const char* RX_UUID  = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E";  // client writes cmds
static const char* TX_UUID  = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E";  // device notifies output

// --- shared state (see threading model above) ---
static NimBLECharacteristic* s_tx = nullptr;
static volatile uint8_t  s_sub_count    = 0;      // how many centrals are subscribed to TX
static volatile bool     s_greet_pending = false;  // loop() owes a new client its banner
static volatile uint16_t s_mtu           = 23;     // MIN ATT MTU across connected centrals
                                                    // (floor 23 => 20B payload; see
                                                    // ServerCallbacks::onMTUChange)
static volatile bool     s_mtu_valid     = false;  // has a real MTU report landed since the
                                                    // last time connections hit 0?

// RX ring buffer: host task writes (rxPush), loop() drains (rxPop). The spinlock
// gives cross-core mutual exclusion between the two tasks.
static const size_t RX_CAP = 256;
static uint8_t          s_rx[RX_CAP];
static volatile size_t  s_rx_head = 0;
static volatile size_t  s_rx_tail = 0;
static portMUX_TYPE     s_mux = portMUX_INITIALIZER_UNLOCKED;

// Command-line assembly for the BLE side (loop() task only — mirrors main.cpp's
// serial cmd_buf; kept separate so serial and BLE bytes never interleave).
static String s_line;

static void rxPush(const uint8_t* data, size_t len) {
  portENTER_CRITICAL(&s_mux);
  for (size_t i = 0; i < len; ++i) {
    size_t next = (s_rx_head + 1) % RX_CAP;
    if (next == s_rx_tail) break;  // full: drop the rest, like a UART overrun
    s_rx[s_rx_head] = data[i];
    s_rx_head = next;
  }
  portEXIT_CRITICAL(&s_mux);
}

static bool rxPop(uint8_t& out) {
  bool got = false;
  portENTER_CRITICAL(&s_mux);
  if (s_rx_tail != s_rx_head) {
    out = s_rx[s_rx_tail];
    s_rx_tail = (s_rx_tail + 1) % RX_CAP;
    got = true;
  }
  portEXIT_CRITICAL(&s_mux);
  return got;
}

// Client wrote to RX (a command). Runs on the NimBLE host task: do the minimum
// — copy the bytes into the ring — and let loop() parse and dispatch them.
class RxCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* c) override {
    auto v = c->getValue();
    if (v.length()) rxPush((const uint8_t*)v.data(), v.length());
  }
};

// Client subscribed/unsubscribed to TX notifications. subValue bit0 = notify.
// Fires once per CONNECTION per actual transition — NimBLE only raises this
// when the subscription flags really change (ble_gatts_subscribe_event() in
// the library XORs old vs new flags before emitting anything), so a count
// that increments/decrements here in lockstep can never drift out of sync
// with reality.
//
// Critically, this ALSO fires with subValue==0 for a connection that
// disconnects while still subscribed, and it does so per-connection, not as
// a blanket reset: NimBLE's ble_gatts_connection_broken() (nimble/nimble/
// host/src/ble_gatts.c) walks that one connection's still-live CCCD state and
// synthesizes exactly this callback (reason TERM) for each characteristic it
// had subscribed to — called from ble_gap_conn_broken() (.../src/ble_gap.c)
// strictly BEFORE the BLE_GAP_EVENT_DISCONNECT that drives
// ServerCallbacks::onDisconnect below. So by the time onDisconnect runs,
// s_sub_count has ALREADY been decremented for the connection that's leaving
// — onDisconnect must not touch it again (see the comment there).
class TxCallbacks : public NimBLECharacteristicCallbacks {
  void onSubscribe(NimBLECharacteristic* c, ble_gap_conn_desc* desc,
                   uint16_t subValue) override {
    (void)c; (void)desc;
    if (subValue > 0) {
      // Publish the incremented count BEFORE the greet flag: loop() consumes
      // the flag (one-shot) and then calls write(), which no-ops unless
      // s_sub_count > 0 — the reverse order lets a preemption window eat the
      // banner+READY. The critical section's barrier orders this write ahead
      // of the flag.
      s_sub_count++;
      portENTER_CRITICAL(&s_mux);
      s_greet_pending = true;  // loop() sends the banner + READY
      portEXIT_CRITICAL(&s_mux);
    } else if (s_sub_count > 0) {
      s_sub_count--;
    }
  }
};

class ServerCallbacks : public NimBLEServerCallbacks {
  // A successful connect does NOT resume advertising on its own in 1.4.x —
  // NimBLEServer::handleGapEvent()'s BLE_GAP_EVENT_CONNECT case only restarts
  // advertising on a FAILED connect attempt; on success it does nothing. So
  // without this override the puck would advertise exactly once and then go
  // silent forever after the first central connects — exactly the bug this
  // milestone exists to fix. getConnectedCount() already reflects the NEW
  // connection by the time this fires (the library pushes the conn handle
  // onto its internal vector before calling back), so "< 2" here really does
  // mean "a second slot is still free."
  void onConnect(NimBLEServer* pServer) override {
    if (pServer->getConnectedCount() < 2) {
      NimBLEDevice::startAdvertising();  // keep the 2nd slot discoverable
    }
  }

  void onDisconnect(NimBLEServer* pServer) override {
    // Do NOT clear s_sub_count here. TxCallbacks::onSubscribe(subValue==0)
    // has ALREADY fired for this specific connection's own subscription (if
    // it had one) by the time this runs — NimBLE fires it per-connection
    // ahead of the disconnect event (verified in the library source; see the
    // long comment on TxCallbacks). Zeroing/decrementing again here would
    // double-count, and the OLD single-client logic (unconditionally
    // clearing a bool) would have killed the OTHER still-connected client's
    // stream the moment either one disconnected — the exact bug this
    // milestone fixes.
    //
    // getConnectedCount() has already been decremented for the disconnecting
    // peer by the time this runs (the library erases it from its internal
    // vector before calling back), so "== 0" here means "well and truly
    // nobody is connected" — safe to drop back to the MTU floor.
    if (pServer->getConnectedCount() == 0) {
      s_mtu = 23;
      s_mtu_valid = false;
    }
    NimBLEDevice::startAdvertising();  // stay discoverable for the next connect
  }

  // MTU is per-connection but s_mtu is one scalar, and notify() (confirmed in
  // NimBLECharacteristic.cpp — see the threading-model comment at the top of
  // this file) broadcasts the SAME chunk to every subscribed connection in
  // one call — the library only WARNS ("Truncating to N bytes") when a chunk
  // is oversized for a given peer, it does not actually enforce a smaller
  // size, so an oversized chunk really does reach and corrupt that peer's
  // stream. We therefore track the MINIMUM MTU reported since connections
  // last dropped to 0, rather than a per-connection table:
  //   * s_mtu_valid distinguishes "no report yet since the last reset" (take
  //     this report as-is) from "at least one report already landed" (only
  //     ever shrink from here). This keeps the common one-client case
  //     behaving exactly as before — a single client's negotiated MTU is
  //     adopted outright, not floored against the 23 reset sentinel.
  //   * Tradeoff (by design, per docs/garmin-datafield.md §7): once a
  //     small-MTU client has been seen, a simultaneously-connected big-MTU
  //     client is chunked down to match it until BOTH disconnect — slower
  //     for the big-MTU client, but never wrong for the small one.
  void onMTUChange(uint16_t mtu, ble_gap_conn_desc* desc) override {
    (void)desc;
    if (mtu < 23) return;
    if (!s_mtu_valid || mtu < s_mtu) s_mtu = mtu;
    s_mtu_valid = true;
  }
};

// Bring up the NUS server and start advertising as `name`. Returns false if any
// step of the NimBLE init fails — the caller reports it in the self-test but
// must keep running (BLE is optional; jump tracking works over USB regardless).
bool begin(const char* name) {
  // Call init() in statement form and check getInitialized() rather than init()'s
  // return: across 1.4.x point releases init() has returned both void and bool,
  // and this form compiles against either.
  NimBLEDevice::init(name);
  if (!NimBLEDevice::getInitialized()) return false;
  NimBLEDevice::setMTU(247);  // request a large MTU; each client caps its own real value
                              // (independently — see ServerCallbacks::onMTUChange)

  NimBLEServer* server = NimBLEDevice::createServer();
  if (!server) return false;
  server->setCallbacks(new ServerCallbacks());

  NimBLEService* svc = server->createService(SVC_UUID);
  if (!svc) return false;

  s_tx = svc->createCharacteristic(TX_UUID, NIMBLE_PROPERTY::NOTIFY);
  if (!s_tx) return false;
  s_tx->setCallbacks(new TxCallbacks());

  NimBLECharacteristic* rx = svc->createCharacteristic(
      RX_UUID, NIMBLE_PROPERTY::WRITE | NIMBLE_PROPERTY::WRITE_NR);
  if (!rx) return false;
  rx->setCallbacks(new RxCallbacks());

  svc->start();

  NimBLEAdvertising* adv = NimBLEDevice::getAdvertising();
  if (!adv) return false;
  adv->addServiceUUID(SVC_UUID);
  adv->setScanResponse(true);  // carry the name in the scan response (the 128-bit
                               // service UUID nearly fills the primary adv packet)
  return NimBLEDevice::startAdvertising();
}

// True exactly once per subscribe: loop() uses it to send the banner + READY.
bool takeGreetPending() {
  bool g = false;
  portENTER_CRITICAL(&s_mux);
  if (s_greet_pending) { s_greet_pending = false; g = true; }
  portEXIT_CRITICAL(&s_mux);
  return g;
}

// --- TX queue (loop task only) ---
// Notifications are paced (the NimBLE host task needs ~3 ms per chunk to drain
// its buffer pool; notify() returns void in 1.4.x so back-pressure can't be
// observed), but the sampling loop must NEVER wait on the radio: a JUMP line
// relayed synchronously at the 23-byte MTU floor stalls sampling ~15-20 ms —
// long enough to swallow the landing spike of a quick follow-up jump. So
// write() only queues; pump(), called every loop() pass, sends at most one
// paced chunk between samples. The queue only fills up during FILE dumps,
// where write() drains it inline (paced) — sampling is already paused inside
// handleCommand there, so blocking is free. A flaky link can still drop
// chunks silently; bulk downloads remain best-effort vs USB.
static const size_t TX_CAP = 1024;
static uint8_t  s_txq[TX_CAP];
static size_t   s_txq_head = 0, s_txq_tail = 0;  // loop() task only
static uint32_t s_last_chunk_us = 0;
static const uint32_t CHUNK_GAP_US = 2500;

static size_t txqSize() { return (s_txq_head + TX_CAP - s_txq_tail) % TX_CAP; }

static void sendOneChunk() {
  size_t avail = txqSize();
  if (avail == 0 || s_tx == nullptr) return;
  const size_t payload = s_mtu > 3 ? (size_t)(s_mtu - 3) : 20;
  uint8_t buf[244];
  size_t n = avail < payload ? avail : payload;
  if (n > sizeof(buf)) n = sizeof(buf);
  for (size_t i = 0; i < n; ++i) buf[i] = s_txq[(s_txq_tail + i) % TX_CAP];
  s_tx->setValue(buf, n);
  s_tx->notify();  // 1.4.x: returns void, and BROADCASTS this exact buffer to
                   // every currently-subscribed connection in this one call
                   // (NimBLECharacteristic::notify() loops its own internal
                   // subscriber list — confirmed in the vendored library
                   // source). There is no per-connection send to hook, which
                   // is exactly why chunk size must respect the SMALLEST
                   // subscribed MTU (s_mtu) rather than any one client's.
  s_txq_tail = (s_txq_tail + n) % TX_CAP;
  s_last_chunk_us = micros();
}

// Send one paced chunk if one is due. Called once per loop() pass; costs
// microseconds when idle, so it never disturbs the 200 Hz sampling cadence.
void pump() {
  if (s_sub_count == 0 || s_tx == nullptr) {
    s_txq_tail = s_txq_head;  // no listener: drop pending output
    return;
  }
  if (txqSize() > 0 && (uint32_t)(micros() - s_last_chunk_us) >= CHUNK_GAP_US) {
    sendOneChunk();
  }
}

void write(const char* data, size_t len) {
  if (s_sub_count == 0 || s_tx == nullptr) return;
  for (size_t i = 0; i < len; ++i) {
    size_t next = (s_txq_head + 1) % TX_CAP;
    while (next == s_txq_tail) {
      // Queue full: bulk output (a FILE dump) from inside handleCommand —
      // sampling is paused there anyway, so drain inline, paced.
      while ((uint32_t)(micros() - s_last_chunk_us) < CHUNK_GAP_US) delay(1);
      sendOneChunk();
    }
    s_txq[s_txq_head] = data[i];
    s_txq_head = next;
  }
}

// Drain the RX ring, dispatching each completed command line through `handle`
// (the same handleCommand() serial uses). Call once per loop(); mirrors the
// serial pollSerial() line assembly (trim, ignore blanks, cap at 64 chars).
// This ring and the line buffer below are shared across BOTH connected
// centrals (see the threading-model comment at the top of this file).
void poll(void (*handle)(const String&)) {
  uint8_t c;
  while (rxPop(c)) {
    if (c == '\n' || c == '\r') {
      s_line.trim();
      if (s_line.length() > 0) handle(s_line);
      s_line = "";
    } else if (s_line.length() < 64) {
      s_line += (char)c;
    }
  }
}

}  // namespace jh_link
