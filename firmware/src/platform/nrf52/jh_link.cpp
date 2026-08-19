// jh_link.cpp — nRF52 (Seeed XIAO nRF52840 Sense) implementation of the
// jh_link seam (firmware/include/platform/jh_link.h). See docs/sense.md
// §3.1/§3.9 and this platform's binding handoff notes.
//
// Bluefruit (Adafruit's nRF52 BLE stack) + BLEUart — which the framework's
// own package name states outright IS the Nordic UART Service (same UUIDs
// as the ESP32's NimBLE implementation), so Bluefy/the web app/Garmin
// connect unchanged. This file's threading-model reasoning was RE-DERIVED
// against Bluefruit's real source (not assumed to port from the ESP32/
// NimBLE version) by reading the INSTALLED framework package —
// ~/.platformio/packages/framework-arduinoadafruitnrf52-seeed/libraries/
// Bluefruit52Lib/ — which a byte-diff against the upstream
// github.com/adafruit/Adafruit_nRF52_Arduino confirmed is an unmodified
// mirror of BLEUart.cpp/.h (the two files this seam's semantics rest on
// most) and only trivially different elsewhere (an unrelated indicate()
// feature, LED-timer bookkeeping). Every claim below cites a file + line in
// that installed copy.
//
// ===========================================================================
// THE CARDINAL RULE (jh_link.h): write()/pump() must never block sampling.
// What Bluefruit's own primitives actually do, established by reading them:
//
// 1. BLEUart::write(conn_hdl, data, len) — services/BLEUart.cpp:238-278.
//    Constructed with `_tx_buffered = false` by default (BLEUart.cpp:75;
//    nothing here ever calls bufferTXD(true) — see point 2) — so it
//    forwards STRAIGHT to `_txd.notify(conn_hdl, content, len)`
//    (BLEUart.cpp:254), synchronously, on whatever task calls it.
//
// 2. Why we do NOT use BLEUart's own opt-in TX buffering (bufferTXD(true)):
//    its `_tx_fifo` is ONE fifo shared by the WHOLE BLEUart instance
//    (BLEUart.h:107-108), and write(content,len) without an explicit
//    conn_hdl always targets `Bluefruit.connHandle()` — i.e. whichever
//    connection is "current" (BLEUart.cpp:230,240) — not a broadcast to
//    every subscriber. With two independently-subscribed centrals (watch +
//    phone, docs/garmin-datafield.md §7) that's wrong by construction: the
//    same bytes need to reach BOTH, at each one's OWN MTU. The doc comment
//    at BLEUart.cpp:259 references "the TXD timer handler" sending buffered
//    data later, but no such timer exists anywhere in this library (grepped
//    the whole Bluefruit52Lib tree) — so relying on it would risk data
//    silently never flushing. We therefore re-implement the byte-queue +
//    paced pump pattern from the ESP32 side (below), adapted to fan a
//    single dequeued chunk out to every subscribed connection ourselves.
//
// 3. Does notify() block? BLECharacteristic::notify() — BLECharacteristic.cpp
//    :707-760 — chunks by THAT connection's own MTU (line 722) and, per
//    chunk, calls `conn->getHvnPacket()` (line 728) before the SoftDevice
//    call `sd_ble_gatts_hvx()` (line 742). getHvnPacket() —
//    BLEConnection.cpp:232-234 — is
//        `xSemaphoreTake(_hvn_sem, ms2tick(BLE_GENERIC_TIMEOUT))`
//    i.e. a REAL FreeRTOS blocking wait, bounded at
//    `BLE_GENERIC_TIMEOUT = 100` (bluefruit_common.h:47) milliseconds. The
//    semaphore is a counting one sized `hvn_qsize`, which defaults to
//    `BLE_GATTS_HVN_TX_QUEUE_SIZE_DEFAULT = 1`
//    (.../nordic/softdevice/s140_nrf52_7.3.0_API/include/ble_gatts.h:198) —
//    only ONE notification may be in flight per connection at a time — and
//    is only replenished when the SoftDevice reports the previous one
//    actually left the radio, `BLE_GATTS_EVT_HVN_TX_COMPLETE`
//    (BLEConnection.cpp:418-419). So: **yes, this can genuinely block, up
//    to 100 ms, per connection, per call** — a materially different
//    (worse, but honestly disclosed rather than assumed-safe) semantic than
//    NimBLE's void-returning notify() on the ESP32 side. Under healthy link
//    conditions the single HVN credit frees up well within a connection
//    interval (typically single-digit-to-tens of ms), so in practice this
//    should read as "occasionally a few ms of blocking", not "regularly
//    100 ms" — but a stalled/out-of-range central makes the worst case
//    real. VERIFY on the bench (firmware/SENSE_FIRST_BOOT.md): confirm the
//    recorded trace has no multi-sample gaps during a `dump` with two
//    centrals connected.
//
// 4. Given (3), we keep the EXACT SAME architecture the ESP32 side uses and
//    for the same reason: write() only ever queues bytes into a small ring
//    buffer; pump() (called once per loop() pass) sends AT MOST one paced
//    chunk, fanned out to every subscribed connection, so any blocking is
//    confined to pump()'s own call — never to write()'s caller (main.cpp's
//    emit layer, called inline from loop()/setup()). The one sanctioned
//    exception is the same as the ESP32's: a bulk FILE dump drains the
//    queue inline (paced) from inside handleCommand(), where sampling is
//    already paused.
//
// 5. No broadcast primitive exists here (unlike NimBLECharacteristic::
//    notify(), which loops its own subscriber list in one call — see the
//    ESP32 jh_link.cpp) — Bluefruit's notify()/write() always target ONE
//    conn_hdl. So sendOneChunk() below loops over
//    Bluefruit.getConnectedHandles() (bluefruit.h:167) itself, checking
//    bleuart.notifyEnabled(hdl) (services/BLEUart.h:64) per connection, and
//    calls bleuart.write(hdl, ...) once per subscriber — chunked to the
//    SMALLEST currently-subscribed connection's own MTU
//    (BLEConnection::getMtu(), BLEConnection.h:83, queried fresh each time —
//    no cached-callback MTU tracking needed, unlike the ESP32/NimBLE side,
//    because Bluefruit exposes it as a plain synchronous accessor).
//
// ===========================================================================
// RX path — genuinely simpler than the ESP32/NimBLE version, and why:
// BLEUart's own `_rx_fifo` (an Adafruit_FIFO) is ALREADY safe for the
// producer (the BLE stack's callback, see below) and consumer (our
// poll(), called from loop()) to be different tasks: Adafruit_FIFO guards
// every read()/write() with its own FreeRTOS mutex
// (cores/nRF5/utility/adafruit_fifo.h:53-57 — `_mutex_lock`/`_mutex_unlock`
// via `xSemaphoreTake(_mutex, portMAX_DELAY)`/`xSemaphoreGive`, a short,
// bounded hold — copying a few bytes — never a stall risk). BLEUart::begin()
// wires the producer side itself (services/BLEUart.cpp:174-175:
// `_rxd.setWriteCallback(BLEUart::bleuart_rxd_cb, true)`), so poll() below
// just calls bleuart.available()/read() directly — no custom ring buffer,
// no critical section, unlike jh_link.h's ESP32 sibling.
//
// Two centrals share this ONE rx_fifo and ONE line-assembly buffer below —
// by the SAME design and for the SAME reason the ESP32 implementation
// documents: both speak the identical protocol through the identical
// dispatcher, and two clients writing multi-fragment commands in the same
// instant could in principle interleave bytes. Accepted, narrow-window
// risk, not something this port attempts to fix (see jh_link.h's ESP32
// citation for the full reasoning — it applies here verbatim).
//
// ===========================================================================
// Subscribe (CCCD write) callback threading: BLEUart::setNotifyCallback()
// registers with `useAdaCallback=true` by default (services/BLEUart.h:68,
// wired at services/BLEUart.cpp:129), meaning it is NOT called directly
// from the BLE stack's own event context — it's deferred onto a SEPARATE
// FreeRTOS task (BLECharacteristic.cpp:556-557's `ada_callback(...)` call;
// the task itself is `adafruit_callback_task` in
// cores/nRF5/utility/AdaCallback.c). So — like the ESP32's NimBLE host
// task — our onNotify() callback runs on neither loop() nor an ISR, and
// the greet-pending flag it sets needs to be handed off safely to
// takeGreetPending() (called from loop()). Single core here (unlike the
// ESP32's two), so a short `taskENTER_CRITICAL()/taskEXIT_CRITICAL()`
// (FreeRTOS's own critical section, just interrupt masking on this target —
// pulled in transitively via Arduino.h -> rtos.h) is the right-sized
// primitive, playing the same role the ESP32 side's portMUX spinlock does.
//
// ===========================================================================
// Advertising auto-restart while a slot remains — RE-DERIVED, not assumed:
// BLEAdvertising::_eventHandler()'s BLE_GAP_EVT_CONNECTED case
// (BLEAdvertising.cpp:406-417) never restarts advertising on its own. Its
// BLE_GAP_EVT_DISCONNECTED case (BLEAdvertising.cpp:419-426) only restarts
// when `Bluefruit.Periph.connected() == 0` — i.e. built-in
// restartOnDisconnect(true) (the default, BLEAdvertising.cpp:255) handles
// "everybody left", never "one of two left, one remains". Confirmed by a
// SECOND, independent source: Adafruit's own official multi-peripheral
// example (examples/Peripheral/bleuart_multi/bleuart_multi.ino) has its
// connect_callback() explicitly call `Bluefruit.Advertising.start(0)` when
// `connection_count < MAX_PRPH_CONNECTION` (lines ~144-148) — proving the
// library itself doesn't do this — while its disconnect_callback() (lines
// ~156-165) does NOT restart advertising at all, meaning even Adafruit's
// own canonical two-peripheral example has exactly the gap this port needs
// to close for real (a rider's watch staying connected while a beach
// phone's earlier connection drops must not leave the puck deaf to a new
// phone). Our onConnect()/onDisconnect() below both explicitly call
// `Bluefruit.Advertising.start(0)` whenever `Bluefruit.Periph.connected() <
// 2`, matching the ESP32 NimBLE side's own "stay discoverable" pattern.
//
// SPDX-License-Identifier: MIT

#include "platform/jh_link.h"

#include <Arduino.h>
#include <bluefruit.h>

#include "platform/jh_clock.h"
#include "platform/jh_power.h"  // batt_pct/charging for the advertised payload

namespace jh_link { static void refreshAdvPayload(); }  // defined near begin()

namespace jh_link {

namespace {

BLEUart s_bleuart;
BLEDfu  s_bledfu;   // OTA DFU control service — see begin()

const uint8_t kMaxPrphConnections = 2;

volatile bool s_greet_pending = false;  // set on the AdaCallback task, taken
                                        // on loop() — see file comment.

// ---------------------------------------------------- RX line assembly
// loop()-task only (poll() is only ever called from loop() — see
// jh_link.h), so no locking needed here, unlike the shared rx_fifo above.
String s_line;

void onConnect(uint16_t conn_hdl) {
  (void)conn_hdl;
  if (Bluefruit.Periph.connected() < kMaxPrphConnections) {
    refreshAdvPayload();             // fresh battery reading on every re-arm
    Bluefruit.Advertising.start(0);  // keep the 2nd slot discoverable — see
                                     // the file comment's advertising section
  }
}

void onDisconnect(uint16_t conn_hdl, uint8_t reason) {
  (void)conn_hdl;
  (void)reason;
  // Unconditionally re-arm advertising whenever a slot is free — covers
  // BOTH "everybody left" (which the library's own restartOnDisconnect
  // would also catch) AND "one of two left, one remains" (which it does
  // NOT — see the file comment). Harmless/idempotent if advertising is
  // already running.
  if (Bluefruit.Periph.connected() < kMaxPrphConnections) {
    Bluefruit.Advertising.start(0);
  }
}

void onNotify(uint16_t conn_hdl, bool enabled) {
  (void)conn_hdl;
  if (!enabled) return;
  taskENTER_CRITICAL();
  s_greet_pending = true;
  taskEXIT_CRITICAL();
}

// ------------------------------------------------------------- TX queue
// loop()-task only (write()/pump() are only ever called from loop()/setup()
// — see jh_link.h) — the SoftDevice/AdaCallback tasks never touch this
// queue, so (unlike the RX side) no locking is needed here either.
const size_t TX_CAP = 1024;
uint8_t  s_txq[TX_CAP];
size_t   s_txq_head = 0, s_txq_tail = 0;
uint32_t s_last_chunk_us = 0;
// Paced send interval. hvn_qsize defaults to 1 (see file comment) — only
// one notification per connection may be in flight at a time — so pacing
// slower than the negotiated BLE connection interval gives the previous
// chunk a realistic chance to have already been ACKed by the time we send
// the next one, keeping the getHvnPacket() wait in the OFTEN-EMPTY case
// rather than the 100 ms worst case. 15 ms is not arbitrary: Apple's
// "Accessory Design Guidelines for Apple Devices" (Bluetooth LE chapter,
// connection-parameter rules) requires peripheral-requested connection
// intervals to be multiples of 15 ms (floor 15 ms for non-HID
// accessories), so against an iPhone — Bluefy IS the primary client — the
// interval will be ≥15 ms and one chunk per interval is the physical
// ceiling anyway. Pacing faster buys nothing and risks the blocking path.
// VERIFY on the bench: log the interval Bluefy/Garmin actually negotiate
// and retune if the trace shows sample gaps (SENSE_FIRST_BOOT.md #3).
const uint32_t CHUNK_GAP_US = 15000;

// Pacing FLOOR, not the pacing itself. CHUNK_GAP_US was derived from Apple's
// Accessory Design Guidelines (>=15 ms for a non-HID accessory), i.e. from the
// iPhone case, and SENSE_FIRST_BOOT.md item 3 flagged it as unmeasured against
// a real Garmin: "log the interval Bluefy/Garmin actually negotiate and
// retune".
//
// MEASURED 2026-08-11, and the guess was wrong for this peer. A Connect IQ
// data field on an Epix Gen 2 negotiates ATT_MTU 23 — the bare minimum — so
// every 86-byte JUMP line needs FIVE notifications, and the watch was losing
// roughly a third of them. The watch's own diagnostic caught a line reading
//     height_m=1.623m=1.658
// i.e. exactly the 20 characters " height_ft=5.3 best_" — precisely one
// MTU-23 payload — missing from the middle. Lose any one of five packets and
// the whole line is corrupt, which is why nothing survived intact.
//
// Sending one chunk every 15 ms into a link that only drains once per
// connection interval overruns it whenever that interval is longer than
// 15 ms. So the gap is now taken from the link itself, per connection,
// refreshed every chunk (BLEConnection::getConnectionInterval() is kept
// current through BLE_GAP_EVT_CONN_PARAM_UPDATE), and we pace to the
// SLOWEST subscriber — the same "never wrong for the slow one" trade the
// MTU logic above already makes.
const uint32_t PACE_MAX_US = 100000;   // sanity cap: never stall a dump on a
                                        // pathological negotiated interval
uint32_t s_pace_us = CHUNK_GAP_US;      // recomputed in sendOneChunk()

// Per-connection delivery tracking for the chunk currently in flight, plus
// the visible drop counter. See sendOneChunk().
const uint8_t TX_RETRY_MAX = 8;         // ~8 pump passes before giving up
uint16_t s_pending_hdls[kMaxPrphConnections];
uint8_t  s_pending_n = 0;
uint8_t  s_pending_tries = 0;
uint32_t s_tx_drops = 0;

size_t txqSize() { return (s_txq_head + TX_CAP - s_txq_tail) % TX_CAP; }

// Currently connected AND subscribed handles (at most kMaxPrphConnections).
uint8_t subscribedHandles(uint16_t* out) {
  uint16_t conn_hdls[kMaxPrphConnections];
  const uint8_t n = Bluefruit.getConnectedHandles(conn_hdls, kMaxPrphConnections);
  uint8_t found = 0;
  for (uint8_t i = 0; i < n; ++i) {
    if (s_bleuart.notifyEnabled(conn_hdls[i])) out[found++] = conn_hdls[i];
  }
  return found;
}

void sendOneChunk() {
  const size_t avail = txqSize();
  if (avail == 0) return;

  uint16_t subs[kMaxPrphConnections];
  const uint8_t n_subs = subscribedHandles(subs);
  if (n_subs == 0) { s_txq_tail = s_txq_head; s_pending_n = 0; return; }  // no listener

  // Chunk to the SMALLEST subscribed connection's own MTU (queried fresh —
  // see file comment on why no cached MTU-change callback is needed here).
  uint16_t min_mtu = 0xFFFF;
  uint32_t pace_us = CHUNK_GAP_US;
  for (uint8_t i = 0; i < n_subs; ++i) {
    BLEConnection* c = Bluefruit.Connection(subs[i]);
    const uint16_t mtu = c ? c->getMtu() : 23;
    if (mtu < min_mtu) min_mtu = mtu;
    // getConnectionInterval() is in raw 1.25 ms units (the library's own log
    // line multiplies by 1.25f to print milliseconds).
    const uint32_t iv_us = c ? (uint32_t)c->getConnectionInterval() * 1250UL : 0;
    if (iv_us > pace_us) pace_us = iv_us;
  }
  s_pace_us = (pace_us > PACE_MAX_US) ? PACE_MAX_US : pace_us;
  const size_t payload = (min_mtu > 3) ? (size_t)(min_mtu - 3) : 20;

  // LATCH THE CHUNK LENGTH while a retry is in flight. Recomputing it each
  // pass is a silent-loss bug in its own right (found by review, 2026-08-14,
  // before it ever ran): `avail` GROWS between passes because loop() keeps
  // queueing while pump() drains, and min_mtu can SHRINK if a second central
  // joins. So a chunk sent as 6 bytes to central A could come back as 20
  // bytes for central B, and when B accepts, the tail advances 20 — A
  // silently loses the 14 it never saw. Exactly the failure this whole fix
  // exists to remove, reintroduced on the other connection. Single-central
  // is unaffected, but Mac+watch is the configuration the fix gets
  // re-verified in, so it would have muddied the very measurement.
  static size_t s_chunk_n = 0;
  uint8_t buf[244];
  size_t n;
  if (s_pending_n > 0) {
    n = s_chunk_n;                       // retry: same bytes, same length
  } else {
    n = (avail < payload) ? avail : payload;
    if (n > sizeof(buf)) n = sizeof(buf);
    s_chunk_n = n;
  }
  for (size_t i = 0; i < n; ++i) buf[i] = s_txq[(s_txq_tail + i) % TX_CAP];

  // NEVER DISCARD A BYTE SILENTLY (2026-08-14). BLEUart::write() returns 0
  // when the SoftDevice has no free TX buffers (BLEUart.cpp:254 —
  // `_txd.notify(...) ? len : 0`). The previous version ignored that and
  // advanced the tail regardless, so a rejected chunk vanished for that
  // connection while the other central got it: bytes missing mid-line, the
  // fragments re-gluing into a JUMP that still parses but carries wrong
  // numbers. That is the watch-corruption signature, and a single central
  // under load hits the same path.
  //
  // Retry is PER-CONNECTION, not all-or-nothing: re-sending a chunk to a
  // connection that already accepted it would duplicate bytes and corrupt
  // the stream a second way. s_pending_* remembers exactly who still owes
  // this chunk.
  if (s_pending_n == 0) {                       // new chunk: everyone owes it
    for (uint8_t i = 0; i < n_subs; ++i) s_pending_hdls[i] = subs[i];
    s_pending_n = n_subs;
    s_pending_tries = 0;
  }
  uint8_t still_pending = 0;
  for (uint8_t i = 0; i < s_pending_n; ++i) {
    const uint16_t h = s_pending_hdls[i];
    if (s_bleuart.write(h, buf, n) == n) continue;   // delivered
    s_pending_hdls[still_pending++] = h;             // owes it still
  }
  s_pending_n = still_pending;

  if (s_pending_n == 0) {
    s_txq_tail = (s_txq_tail + n) % TX_CAP;          // everyone got it
  } else if (++s_pending_tries >= TX_RETRY_MAX) {
    // Bounded: one wedged central must never stall output for the others,
    // and must never stall it forever. Give up on this chunk, COUNT it, and
    // move on — a drop we can see is a bug; a drop we cannot see is a wrong
    // number on someone's wrist. Surfaced as tx_drops= in `stats`.
    s_tx_drops += n;
    s_txq_tail = (s_txq_tail + n) % TX_CAP;
    s_pending_n = 0;
  }
  s_last_chunk_us = micros();
}

// ------------------------------------------------------------- watchdog
// nRF52840 WDT — direct register setup (design decision: no HAL wrapper
// needed for 4 registers). Register names/offsets and the reload magic
// value confirmed against the installed core's own Nordic device header
// (not just remembered): ~/.platformio/packages/
// framework-arduinoadafruitnrf52-seeed/cores/nRF5/nordic/nrfx/mdk/
// nrf52840.h:2046-2062 (NRF_WDT_Type: TASKS_START @0x000, CRV @0x504,
// RREN @0x508, CONFIG @0x50C, RR[8] @0x600) and
// nrf52840_bitfields.h:17402 (`WDT_RR_RR_Reload = 0x6E524635`). These
// match the nRF52840 Product Specification's own WDT chapter register map
// (peripheral base 0x40010000, NRF_WDT_BASE above) — VERIFY the watchdog
// actually resets the board on a deliberately-hung build, and that
// reloading from pump() (a per-loop()-pass call, i.e. far more often than
// needed) never itself gets starved by something else blocking loop()
// (firmware/SENSE_FIRST_BOOT.md).
void wdtInit() {
  NRF_WDT->CONFIG = (1 << 0);  // bit0 SLEEP=Run (keep counting through CPU
                               // sleep — this port doesn't yet use System
                               // OFF sleep, docs/sense.md §3.5 is a later
                               // milestone, so this is a forward-looking,
                               // safe default); bit3 HALT=Pause (default 0:
                               // don't fire while a debugger has the core
                               // halted).
  NRF_WDT->CRV   = (3500UL * 32768UL) / 1000UL - 1;  // ~3.5 s (32.768 kHz
                                                     // LFCLK-driven counter)
  NRF_WDT->RREN  = (1 << 0);   // enable reload channel RR[0]
  NRF_WDT->TASKS_START = 1;
}

void wdtFeed() {
  NRF_WDT->RR[0] = WDT_RR_RR_Reload;
}

}  // namespace

void watchdog_init() { wdtInit(); }
void watchdog_feed() { wdtFeed(); }

uint32_t tx_drops() { return s_tx_drops; }

static char s_adv_name[28] = "";
const char* local_name() { return s_adv_name; }

// ADVERTISED BATTERY + STATE — "puck 78%" on the watch WITHOUT connecting,
// the cheapest UX win in ble-dependability.md §5 (layer 5: the user must be
// able to tell states apart). Manufacturer-specific data, company ID 0xFFFF
// (the SIG's reserved-for-development ID — we have no assigned one, and an
// invented real ID would be squatting).
//
// Payload: [FF FF][batt_pct][flags]. batt_pct 0xFF = unmeasurable (v1 boards).
// flags bit0 = charging. Room for armed/recording in bit1 when main.cpp gets
// a setter; battery+charging is the part that needs no new seam surface.
//
// It lives in the SCAN RESPONSE, not the primary packet: the primary is
// already 24 of its 31 bytes (flags 3 + txPower 3 + 128-bit NUS UUID 18) and
// a failed advertising start is a dead puck, whereas the scan response has
// ~14 bytes free next to the name. Every scanner that reads the name at all
// does an active scan, so anything that can see us can see this.
//
// Refreshed at every advertising (re)start rather than on a timer: updating
// live means stop/clear/restart, which would disturb a client mid-connect.
// Every disconnect and re-arm therefore publishes a fresh reading.
static void refreshAdvPayload() {
  uint8_t mfr[4];
  mfr[0] = 0xFF;                       // company ID 0xFFFF, little-endian
  mfr[1] = 0xFF;
  const int pct = jh_power::batt_pct();
  mfr[2] = (pct < 0) ? 0xFF : (uint8_t)pct;
  mfr[3] = (uint8_t)(jh_power::charging() == 1 ? 0x01 : 0x00);
  Bluefruit.ScanResponse.clearData();
  Bluefruit.ScanResponse.addManufacturerData(mfr, sizeof(mfr));
  Bluefruit.ScanResponse.addName();
}

bool begin(const char* name) {
  wdtInit();  // idempotent-enough: TASKS_START on a running WDT is a no-op  // see the file comment: begin()/pump() are the only two
             // main.cpp call sites available to hook this from, since
             // main.cpp itself is unchanged.

  // Raise the MTU ceiling above the SoftDevice's own default of 23
  // (bluefruit.cpp:161's `_sd_cfg.prph.mtu_max = BLE_GATT_ATT_MTU_DEFAULT`)
  // before begin() — each client still negotiates its own real value
  // (queried fresh per send — see sendOneChunk()), same intent as the
  // ESP32 side's NimBLEDevice::setMTU(247).
  Bluefruit.configPrphConn(247, BLE_GAP_EVENT_LENGTH_MIN,
                          BLE_GATTS_HVN_TX_QUEUE_SIZE_DEFAULT,
                          BLE_GATTC_WRITE_CMD_TX_QUEUE_SIZE_DEFAULT);

  if (!Bluefruit.begin(kMaxPrphConnections, 0)) return false;
  // UNIQUE PER-BOARD NAME — "JumpHeight-3F2A", suffix from the factory-lasered
  // FICR device address. Proven necessary 2026-08-18: with two boards both
  // advertising bare "JumpHeight", the bench logger connected to the freshly
  // soaked spare and logged its floating-divider "97%" into the OG's
  // death-run record. In a quiver of pucks this is not an edge case, it is
  // Tuesday. Every client matches by PREFIX (or by NUS service), so old bare
  // names and new suffixed names coexist.
  snprintf(s_adv_name, sizeof(s_adv_name), "%s-%04X", name,
           (unsigned)(NRF_FICR->DEVICEADDR[0] & 0xFFFFu));
  Bluefruit.setName(s_adv_name);
  Bluefruit.Periph.setConnectCallback(onConnect);
  Bluefruit.Periph.setDisconnectCallback(onDisconnect);

  // OTA DFU — the ONLY firmware path once the capsule is sealed.
  //
  // The Adafruit bootloader already speaks Nordic's legacy OTA DFU
  // (docs/sense.md §3.3), but reaching it needed a physical double-tap on
  // RESET, which lives inside the box. Starting BLEDfu here publishes the
  // control characteristic while the app runs, so nRF Connect on a phone can
  // reboot the puck into its bootloader and flash it wirelessly — no cable,
  // no opening the capsule. Added 2026-08-11, deliberately BEFORE the box was
  // taped shut: sealing without it would have frozen the firmware for good.
  //
  // Trade, unchanged from §3.3: this DFU is single-bank, so a transfer that
  // dies mid-way leaves the device sitting in its bootloader — recoverable
  // over BLE or USB, never bricked — rather than falling back to the old
  // image. It is also unauthenticated: anyone in radio range with nRF Connect
  // can flash this puck. Accepted for a personal device; revisit if these are
  // ever handed out.
  //
  // Adafruit's own examples start BLEDfu FIRST, before other services, and
  // that ordering is kept here.
  if (s_bledfu.begin() != ERROR_NONE) return false;

  if (s_bleuart.begin() != ERROR_NONE) return false;
  s_bleuart.setNotifyCallback(onNotify);

  Bluefruit.Advertising.addFlags(BLE_GAP_ADV_FLAGS_LE_ONLY_GENERAL_DISC_MODE);
  Bluefruit.Advertising.addTxPower();
  Bluefruit.Advertising.addService(s_bleuart);  // 128-bit NUS UUID in the
                                                // primary advertising packet
                                                // (docs/sense.md §3.1)
  refreshAdvPayload();                          // battery+state, then the name
  // (kept below for the trail: the name lives in the scan response because —
                                                // no room left in the primary
                                                // packet once the 128-bit
                                                // service UUID is in it
                                                // (same reasoning Adafruit's
                                                // own bleuart_multi.ino uses)
  Bluefruit.Advertising.restartOnDisconnect(true);  // default already true;
                                                    // explicit for clarity —
                                                    // see the file comment
                                                    // for why we ALSO handle
                                                    // restart ourselves
  // The blue LED blinked at ~50% duty for as long as the puck advertised —
  // Bluefruit's _led_conn, on by default. Pointless drain, and absurd in a
  // sealed puck (2026-08-14 power review).
  Bluefruit.autoConnLed(false);
  // Fast 20 ms for the first 30 s (a watch looking for us finds us at once),
  // then 1 s idle. 152.5 ms was a session-grade rate for a device that
  // spends most of its life sitting still, and the advertiser alone
  // overran the whole standby budget.
  Bluefruit.Advertising.setInterval(32, 1600);  // fast 20 ms / slow 1000 ms
  Bluefruit.Advertising.setFastTimeout(30);
  const bool started = Bluefruit.Advertising.start(0);  // 0 = advertise forever

  // Feed right before handing control back to main.cpp's setup() (review-
  // nrf52.md finding #4): wdtInit() above starts the ~3.5s countdown, but
  // from here until loop() first runs pump() (its own feed), setup() still
  // has to run jh_persist::init()/loadCalibration()/runSelfTest() (>=500ms
  // just for the accel self-test's own 100x delay(5) loop) /
  // scanStoredJumps() — all feed-less against that budget. Comfortably
  // fits today (fragile, per the review), but feeding exactly here — rather
  // than relying on whatever's left over from wdtInit()'s own reload above —
  // is what keeps it that way as those calls grow. See SENSE_FIRST_BOOT.md
  // for the matching QSPI-hang-before-READY diagnostic this same finding
  // also identified (a hang in jh_store::init(), called BEFORE this
  // function and its wdtInit(), has no watchdog protection at all).
  wdtFeed();
  return started;
}

bool takeGreetPending() {
  bool g = false;
  taskENTER_CRITICAL();
  if (s_greet_pending) { s_greet_pending = false; g = true; }
  taskEXIT_CRITICAL();
  return g;
}

void pump() {
  wdtFeed();  // see wdtInit()'s comment: fed every loop() pass, i.e. far
             // more often than the ~3.5 s timeout, so this only matters if
             // something else actually hangs loop() itself.

  // Keep jh_clock's wrap-tracker alive even while sampling is paused
  // (review-nrf52.md finding #3): main.cpp's loop() calls jh_clock::
  // micros64() itself, but only AFTER an `if (!sensor_ok) { delay(10);
  // return; }` early return — so if the IMU is ever down (a wiring fault,
  // recovered later via `selftest`) for more than the underlying 32-bit
  // micros()'s ~71.6-minute wrap period, the wrap tracker misses a wrap
  // entirely and every subsequent timestamp this boot is off by ~71.6
  // minutes. pump() is called unconditionally, every loop() pass, BEFORE
  // that early return (verified against firmware/src/main.cpp's loop():
  // jh_link::pump() runs at line ~489, the `!sensor_ok` return at ~491) —
  // so calling it here keeps the tracker fed regardless of sensor_ok. The
  // return value is discarded: this call exists purely for its side effect
  // (advancing s_wraps in jh_clock.cpp's own statics), not to read the
  // time. nRF52-only fix (this platform's pump() only) — see the ESP32
  // sibling's jh_link.cpp, unaffected, since docs/sense.md's own refactor
  // notes already flagged 32-bit micros() wrap-tracking as chip-specific.
  (void)jh_clock::micros64();

  if (txqSize() > 0 && (uint32_t)(micros() - s_last_chunk_us) >= s_pace_us) {
    sendOneChunk();
  }
}

void write(const char* data, size_t len) {
  uint16_t subs[kMaxPrphConnections];
  if (subscribedHandles(subs) == 0) return;  // no-op if nobody is subscribed
  for (size_t i = 0; i < len; ++i) {
    size_t next = (s_txq_head + 1) % TX_CAP;
    while (next == s_txq_tail) {
      // Queue full: bulk output (a FILE dump) from inside handleCommand —
      // sampling is paused there anyway, so drain inline, paced — the ONE
      // sanctioned exception to "write() only ever queues" (jh_link.h).
      //
      // CRITICAL (review-nrf52.md finding #1): this loop runs entirely
      // outside loop()'s own per-pass wdtFeed() — pump() isn't called again
      // until this whole write() returns — and a big FILE dump (jumps.csv +
      // trace.csv, the `dump` command) can spend well over the ~3.5s WDT
      // window right here: measured budget exhaustion from ~4.6KB (23-B
      // MTU, worst case) to ~55KB (247-B MTU) of queued output, i.e. any
      // real session sync with a subscribed central. Feed every iteration
      // of this loop (each iteration paces ~CHUNK_GAP_US = 15ms, so this is
      // a cheap, frequent reload, not a rate-limited one) so a dump-over-
      // BLE never watchdog-resets mid-transfer.
      wdtFeed();
      while ((uint32_t)(micros() - s_last_chunk_us) < s_pace_us) delay(1);
      sendOneChunk();
    }
    s_txq[s_txq_head] = data[i];
    s_txq_head = next;
  }
}

void poll(void (*handle)(const String&)) {
  // Reads straight from BLEUart's own (already thread-safe — see file
  // comment) rx_fifo; both connected centrals share this ONE line-assembly
  // buffer, mirroring the ESP32 side's own accepted trade-off exactly.
  while (s_bleuart.available()) {
    const char c = (char)s_bleuart.read();
    if (c == '\n' || c == '\r') {
      s_line.trim();
      if (s_line.length() > 0) handle(s_line);
      s_line = "";
    } else if (s_line.length() < 64) {
      s_line += c;
    }
  }
}

// Reboot into OTA DFU. The magic byte is the Adafruit bootloader's own
// DFU_OTA_MAGIC = 0xB1, read from Bluefruit52Lib's BLEDfu.cpp rather than
// remembered — the bootloader reads GPREGRET on boot and stays in DFU
// instead of starting the app.
//
// BLEDfu's own handler jumps straight to the bootloader after preserving
// bonding keys; a plain reset is used here because this device does not
// bond, and a reset is the simpler, harder-to-get-wrong path.
//
// Why this exists ON TOP of the BLEDfu service: Web Bluetooth blocklists the
// Nordic DFU service UUID (docs/sense.md §3.3), so the browser app can never
// touch that characteristic. It CAN speak our own NUS line protocol — so
// routing the trigger through a plain `dfu` command means the web console,
// blecmd.py and a phone all reach it the same way, with no blocklist and no
// second protocol.
bool reboot_to_dfu() {
  // Uses the core's own enterOTADfu() (cores/nRF5/wiring.c:89) rather than a
  // hand-rolled GPREGRET write. First attempt used 0xB1 and REBOOTED STRAIGHT
  // BACK INTO THE APP — proven on silicon 2026-08-11 (sent `dfu` over BLE,
  // puck kept advertising as JumpHeight): 0xB1 is DFU_MAGIC_OTA_APPJUM, the
  // "app JUMPED here with the SoftDevice still live" handshake BLEDfu.cpp
  // uses with a direct bootloader_util_app_start() — through a full
  // NVIC_SystemReset() that promise is false and the bootloader just starts
  // the app. The RESET path wants DFU_MAGIC_OTA_RESET (0xA8), which is
  // exactly what enterOTADfu() writes.
  delay(50);          // let the caller's farewell bytes reach USB/BLE
  // NOT the core's enterOTADfu(): that helper writes NRF_POWER->GPREGRET
  // directly, and with the SoftDevice ENABLED that register is SD-owned —
  // the raw write lands only sometimes. Measured on silicon 2026-08-11:
  // three `dfu` commands entered the bootloader, then two in a row silently
  // rebooted back into the app, same binary. The SD-aware calls are the
  // reliable path; 0xA8 is DFU_MAGIC_OTA_RESET (wiring.c:28).
  uint32_t rc1 = sd_power_gpregret_clr(0, 0xFF);
  uint32_t rc2 = sd_power_gpregret_set(0, 0xA8);
  uint32_t back = 0;
  uint32_t rc3 = sd_power_gpregret_get(0, &back);
  // DIAG (temporary): prove whether the magic actually sticks before reset.
  char msg[64];
  snprintf(msg, sizeof(msg), "# gpregret rc=%lu/%lu/%lu val=0x%02lx\n",
           (unsigned long)rc1, (unsigned long)rc2, (unsigned long)rc3,
           (unsigned long)back);
  write(msg, strlen(msg));
  for (int i = 0; i < 40; ++i) { pump(); delay(15); }  // flush over BLE/USB
  NVIC_SystemReset();
  return true;        // not reached
}

bool reboot_to_uf2() {
  delay(50);
  sd_power_gpregret_clr(0, 0xFF);
  sd_power_gpregret_set(0, 0x57);   // DFU_MAGIC_UF2_RESET (wiring.c:27)
  NVIC_SystemReset();
  return true;  // not reached
}

}  // namespace jh_link
