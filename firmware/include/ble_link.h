// ble_link.h
//
// Nordic UART Service (NUS) BLE link for the Jump Height firmware — a thin
// wrapper over NimBLE-Arduino that carries the EXACT same newline-terminated
// protocol as the USB serial console, so a phone/laptop (Web Bluetooth) can
// read jumps and send commands the same way ./tools/jump does over USB.
//
// Why NimBLE (not the stock ESP32 BLE / Bluedroid stack): Bluedroid's flash and
// RAM footprint is large; NimBLE's is a fraction of it, and that budget is what
// lets the app + a big LittleFS fit in our no-OTA partition map (partitions.csv).
//
// Threading model — the part that's easy to get wrong:
//   NimBLE runs its own host task. Every callback here (client writes, subscribe,
//   MTU change, disconnect) fires on THAT task, never on loop(). So:
//     * Received command bytes are pushed into a fixed ring buffer guarded by a
//       portMUX spinlock; loop() drains it and runs the SAME handleCommand() as
//       serial, so every command is handled exactly once, on the loop() task.
//     * Subscription / MTU state are plain scalars the host task writes and
//       loop() reads (single-writer/single-reader; a stale read is harmless).
//     * Notifications (device -> client) are only ever sent from loop() via the
//       emit layer in main.cpp, so the TX path has no cross-task contention.
//
// Only src/main.cpp includes this header, so the definitions live here inline
// (matching jump_detector.h / mpu6050_min.h). It pulls in <NimBLEDevice.h>, so
// it must never be included by the host-side detector parity test.
//
// API note: written against NimBLE-Arduino 1.4.x. The 2.x release changed the
// characteristic/server callback signatures (ble_gap_conn_desc* -> NimBLEConnInfo&);
// the signatures below are the 1.4.x ones.
//
// SPDX-License-Identifier: MIT

#pragma once

#include <Arduino.h>
#include <NimBLEDevice.h>

namespace ble_link {

// Nordic UART Service — the de-facto "serial port over BLE" profile.
static const char* SVC_UUID = "6E400001-B5A3-F393-E0A9-E50E24DCCA9E";
static const char* RX_UUID  = "6E400002-B5A3-F393-E0A9-E50E24DCCA9E";  // client writes cmds
static const char* TX_UUID  = "6E400003-B5A3-F393-E0A9-E50E24DCCA9E";  // device notifies output

// --- shared state (see threading model above) ---
static NimBLECharacteristic* s_tx = nullptr;
static volatile bool     s_subscribed    = false;  // is a client subscribed to TX?
static volatile bool     s_greet_pending = false;  // loop() owes a new client its banner
static volatile uint16_t s_mtu           = 23;     // negotiated ATT MTU (floor 23 => 20B payload)

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
class TxCallbacks : public NimBLECharacteristicCallbacks {
  void onSubscribe(NimBLECharacteristic* c, ble_gap_conn_desc* desc,
                   uint16_t subValue) override {
    (void)c; (void)desc;
    if (subValue > 0) {
      // Publish s_subscribed BEFORE the greet flag: loop() consumes the flag
      // (one-shot) and then calls write(), which no-ops unless s_subscribed —
      // the reverse order lets a preemption window eat the banner+READY.
      // The critical section's barrier orders this write ahead of the flag.
      s_subscribed = true;
      portENTER_CRITICAL(&s_mux);
      s_greet_pending = true;  // loop() sends the banner + READY
      portEXIT_CRITICAL(&s_mux);
    } else {
      s_subscribed = false;
    }
  }
};

class ServerCallbacks : public NimBLEServerCallbacks {
  void onDisconnect(NimBLEServer* pServer) override {
    (void)pServer;
    s_subscribed = false;
    s_mtu = 23;  // next client re-negotiates from the floor; never send a chunk
                 // bigger than a client's real MTU (that would truncate/corrupt)
    NimBLEDevice::startAdvertising();  // stay discoverable for the next connect
  }
  void onMTUChange(uint16_t mtu, ble_gap_conn_desc* desc) override {
    (void)desc;
    if (mtu >= 23) s_mtu = mtu;
  }
};

// Bring up the NUS server and start advertising as `name`. Returns false if any
// step of the NimBLE init fails — the caller reports it in the self-test but
// must keep running (BLE is optional; jump tracking works over USB regardless).
static bool begin(const char* name) {
  // Call init() in statement form and check getInitialized() rather than init()'s
  // return: across 1.4.x point releases init() has returned both void and bool,
  // and this form compiles against either.
  NimBLEDevice::init(name);
  if (!NimBLEDevice::getInitialized()) return false;
  NimBLEDevice::setMTU(247);  // request a large MTU; the client caps the real value

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
static bool takeGreetPending() {
  bool g = false;
  portENTER_CRITICAL(&s_mux);
  if (s_greet_pending) { s_greet_pending = false; g = true; }
  portEXIT_CRITICAL(&s_mux);
  return g;
}

// Notify the TX characteristic with `len` bytes, split across the negotiated
// MTU (payload = MTU - 3; the client reassembles on '\n'). No-op unless a client
// is subscribed. In NimBLE-Arduino 1.4.x notify() returns void (the bool return
// arrived in 2.x), so back-pressure can't be observed directly — instead each
// chunk is followed by a small fixed delay that lets the host task drain its
// buffer pool. That pacing bounds FILE dumps to roughly BLE's real throughput
// anyway; a congested/flaky link can still drop chunks silently, which is why
// the docs steer bulk session downloads to USB and treat BLE dumps as
// best-effort. Single protocol lines are one chunk, so live use costs ~3 ms.
static void write(const char* data, size_t len) {
  if (!s_subscribed || s_tx == nullptr) return;
  const size_t chunk = s_mtu > 3 ? (size_t)(s_mtu - 3) : 20;
  size_t off = 0;
  while (off < len) {
    size_t n = len - off;
    if (n > chunk) n = chunk;
    s_tx->setValue((const uint8_t*)data + off, n);
    s_tx->notify();
    delay(3);  // pace: let the NimBLE host task drain before the next chunk
    off += n;
  }
}

// Drain the RX ring, dispatching each completed command line through `handle`
// (the same handleCommand() serial uses). Call once per loop(); mirrors the
// serial pollSerial() line assembly (trim, ignore blanks, cap at 64 chars).
static void poll(void (*handle)(const String&)) {
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

}  // namespace ble_link
