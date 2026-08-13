// twim_bounded.h
//
// Bounded I2C master for the Sense's internal IMU bus — SENSE_FIRST_BOOT
// item 16d, "do before water day," built 2026-08-13 (the night after the
// second sensor stopped answering).
//
// WHY THIS EXISTS: the core's Wire_nRF52.cpp waits on TWIM events with
// unbounded `while (!EVENTS_...)` spins. A held bus line therefore hangs the
// caller forever; on this firmware that means the watchdog converts a sick
// SENSOR into a REBOOTING DEVICE — mid-session, on the water, the session is
// lost (16c's boot loop was exactly this, at boot). This class performs the
// same TWIM transactions with an explicit time bound on every wait: a held
// bus costs one timeout (~2 ms) and returns an error, and the sample loop's
// existing "no reading this tick" path handles the rest. No probes, no bus
// interrogation — the 16e/16f lesson stands: we do not ask "is the bus
// safe?", we make the answer not matter.
//
// Scope: master-only, 7-bit address, polled (no ISR — transactions are
// hundreds of microseconds at 400 kHz; blocking the sample loop for that is
// exactly what the Wire path already did, minus the unbounded tail). Talks
// to NRF_TWIM1 — the instance the variant assigns to Wire1 (the internal
// IMU bus). Wire1 itself must NOT be begun while this class owns the
// peripheral; jh_imu.cpp is the only user of either.
//
// EasyDMA note: TXD/RXD pointers must be RAM addresses (the peripheral
// cannot read flash). Callers pass stack/static buffers — both are RAM on
// this platform; nothing here takes a pointer that could be a flash
// literal (write() copies through a small RAM staging buffer to make that
// impossible to get wrong).
//
// Register facts from the nRF52840 Product Specification's TWIM chapter
// (EVENTS_STOPPED/EVENTS_ERROR semantics, ERRORSRC write-1-to-clear,
// SHORTS LASTTX_STARTRX/LASTTX_STOP/LASTRX_STOP, PSEL, FREQUENCY K400) via
// the SDK headers this core ships (<nrf52840.h>/<nrf52840_bitfields.h> —
// included through Arduino.h).
//
// SPDX-License-Identifier: MIT

#pragma once

#include <Arduino.h>

class TwimBounded {
 public:
  enum Result : uint8_t {
    OK = 0,
    NACK,      // address or data byte not acknowledged — device absent/asleep
    TIMEOUT,   // bus held / clock never completed inside the bound
    BUSERR,    // controller error other than NACK (overrun)
  };

  // Per-transaction wait bound. Our longest transaction is 1+6 bytes at
  // 400 kHz (~200 us on a healthy bus); the nominal bound is milliseconds
  // while staying far under the 3.5 s watchdog.
  //
  // GRANULARITY (night-review finding): this core's micros() is
  // tick-derived (~977 us steps — DWT cycle counting is never enabled, and
  // enabling it here would shrink micros()'s wrap period to ~67 s and
  // break jh_clock's wrap arithmetic, so it stays off). All micros()-based
  // bounds below are therefore honest only to +/- one tick, and every wait
  // additionally carries an ITERATION cap — a preemption-proof, clock-free
  // hard bound (the counter doesn't advance while an ISR runs, so
  // preemption stretches wall time but can never make the loop infinite).
  static const uint32_t TIMEOUT_US = 4000;       // ~4-5 ms real, >=4 ticks
  static const uint32_t SPIN_CAP   = 400000;     // ~40-200 ms absolute worst
  static const uint32_t STOP_CAP   = 20000;      // bounded STOP-wait courtesy

  // Configure pins + peripheral. Arduino pin numbers (mapped to nRF pins
  // internally). Pin config mirrors Wire_nRF52.cpp begin() exactly: input
  // buffer connected, internal pull-up, S0D1 (open-drain) drive.
  void begin(uint8_t sda_arduino, uint8_t scl_arduino) {
    sda_ = g_ADigitalPinMap[sda_arduino];
    scl_ = g_ADigitalPinMap[scl_arduino];
    pinCfg(scl_);
    pinCfg(sda_);
    NRF_TWIM1->PSEL.SCL  = scl_;
    NRF_TWIM1->PSEL.SDA  = sda_;
    NRF_TWIM1->FREQUENCY = TWIM_FREQUENCY_FREQUENCY_K400;
    NRF_TWIM1->SHORTS    = 0;
    NRF_TWIM1->INTEN     = 0;  // polled — no ISR, no NVIC involvement
    NRF_TWIM1->ENABLE    = TWIM_ENABLE_ENABLE_Enabled << TWIM_ENABLE_ENABLE_Pos;
    begun_ = true;
  }

  // Release the peripheral and float both lines (input, no pull) — the
  // power-down half of the 16g sequencing pair: nothing of ours may
  // energize the sensor domain once its rail is going away.
  void end() {
    NRF_TWIM1->ENABLE = TWIM_ENABLE_ENABLE_Disabled << TWIM_ENABLE_ENABLE_Pos;
    if (begun_) {
      pinFloat(scl_);
      pinFloat(sda_);
    }
    begun_ = false;
  }

  bool begun() const { return begun_; }

  // Write n bytes (n >= 1, n <= 8) then STOP.
  Result write(uint8_t addr7, const uint8_t* data, uint8_t n) {
    if (!begun_ || n == 0 || n > sizeof(txbuf_)) return BUSERR;
    for (uint8_t i = 0; i < n; ++i) txbuf_[i] = data[i];  // guaranteed-RAM staging
    NRF_TWIM1->ADDRESS    = addr7;
    NRF_TWIM1->TXD.PTR    = (uint32_t)txbuf_;
    NRF_TWIM1->TXD.MAXCNT = n;
    NRF_TWIM1->SHORTS     = TWIM_SHORTS_LASTTX_STOP_Msk;
    clearEvents();
    NRF_TWIM1->TASKS_STARTTX = 1;
    const Result r = await();
    // Second line of defence (night-review finding #2): a stale STOP from a
    // previous transaction, or any silently-short transfer, shows up as
    // AMOUNT != requested even when the event decode said OK.
    if (r == OK && NRF_TWIM1->TXD.AMOUNT != n) return BUSERR;
    return r;
  }

  // Write wn bytes, repeated-start, read rn bytes, STOP. The register-read
  // shape: wbuf is the register pointer, rbuf receives the burst.
  Result writeThenRead(uint8_t addr7, const uint8_t* wbuf, uint8_t wn,
                       uint8_t* rbuf, uint8_t rn) {
    if (!begun_ || wn == 0 || wn > sizeof(txbuf_) || rn == 0) return BUSERR;
    for (uint8_t i = 0; i < wn; ++i) txbuf_[i] = wbuf[i];
    NRF_TWIM1->ADDRESS    = addr7;
    NRF_TWIM1->TXD.PTR    = (uint32_t)txbuf_;
    NRF_TWIM1->TXD.MAXCNT = wn;
    NRF_TWIM1->RXD.PTR    = (uint32_t)rbuf;   // caller buffer: stack/static = RAM
    NRF_TWIM1->RXD.MAXCNT = rn;
    NRF_TWIM1->SHORTS     = TWIM_SHORTS_LASTTX_STARTRX_Msk |
                            TWIM_SHORTS_LASTRX_STOP_Msk;
    clearEvents();
    NRF_TWIM1->TASKS_STARTTX = 1;
    const Result r = await();
    // Same AMOUNT cross-check as write() — a short RX handed to the caller
    // as OK becomes a plausible-looking wrong sample in the detector.
    if (r == OK && (NRF_TWIM1->TXD.AMOUNT != wn || NRF_TWIM1->RXD.AMOUNT != rn)) {
      return BUSERR;
    }
    return r;
  }

 private:
  static void pinCfg(uint32_t nrf_pin) {
    NRF_GPIO_Type* port = (nrf_pin < 32) ? NRF_P0 : NRF_P1;
    port->PIN_CNF[nrf_pin & 31] =
        ((uint32_t)GPIO_PIN_CNF_DIR_Input       << GPIO_PIN_CNF_DIR_Pos) |
        ((uint32_t)GPIO_PIN_CNF_INPUT_Connect   << GPIO_PIN_CNF_INPUT_Pos) |
        ((uint32_t)GPIO_PIN_CNF_PULL_Pullup     << GPIO_PIN_CNF_PULL_Pos) |
        ((uint32_t)GPIO_PIN_CNF_DRIVE_S0D1      << GPIO_PIN_CNF_DRIVE_Pos) |
        ((uint32_t)GPIO_PIN_CNF_SENSE_Disabled  << GPIO_PIN_CNF_SENSE_Pos);
  }

  static void pinFloat(uint32_t nrf_pin) {
    NRF_GPIO_Type* port = (nrf_pin < 32) ? NRF_P0 : NRF_P1;
    port->PIN_CNF[nrf_pin & 31] =
        ((uint32_t)GPIO_PIN_CNF_DIR_Input       << GPIO_PIN_CNF_DIR_Pos) |
        ((uint32_t)GPIO_PIN_CNF_INPUT_Disconnect<< GPIO_PIN_CNF_INPUT_Pos) |
        ((uint32_t)GPIO_PIN_CNF_PULL_Disabled   << GPIO_PIN_CNF_PULL_Pos);
  }

  void clearEvents() {
    NRF_TWIM1->EVENTS_STOPPED = 0;
    NRF_TWIM1->EVENTS_ERROR   = 0;
    NRF_TWIM1->ERRORSRC       = 0xFFFFFFFF;  // write-1-to-clear, all sources
  }

  // Bounded STOP-then-quiesce: try TASKS_STOP (courtesy — STOP cannot
  // complete on a clamped SCL), and if the peripheral will not stop,
  // force-disable/re-enable it, which abandons the transaction
  // unconditionally. EVERY non-OK exit of await() funnels through here, so
  // no exit can leave a STOP in flight to pollute the next transaction
  // (night-review finding #2/#6).
  void quiesce() {
    NRF_TWIM1->TASKS_STOP = 1;
    for (uint32_t n = 0; n < STOP_CAP && !NRF_TWIM1->EVENTS_STOPPED; ++n) {}
    if (!NRF_TWIM1->EVENTS_STOPPED) {
      NRF_TWIM1->ENABLE = TWIM_ENABLE_ENABLE_Disabled << TWIM_ENABLE_ENABLE_Pos;
      delayMicroseconds(10);
      NRF_TWIM1->ENABLE = TWIM_ENABLE_ENABLE_Enabled << TWIM_ENABLE_ENABLE_Pos;
    }
    clearEvents();
  }

  // The one loop Wire never bounded. Every exit path leaves the peripheral
  // stopped (or disabled+re-enabled if it would not stop) and events clear.
  //
  // ORDERING (night-review finding #1, the important one): both transaction
  // shapes self-stop via SHORTS, so EVENTS_ERROR and EVENTS_STOPPED land
  // microseconds apart and STOPPED can win the race — an ISR preempting
  // this loop in that window used to make it return OK over a latched
  // NACK/OVERRUN, silently converting "config write never happened" into
  // success. Wire_nRF52.cpp's own completion path checks EVENTS_ERROR
  // unconditionally AFTER seeing STOPPED; so does this now, plus an
  // AMOUNT-vs-expected cross-check in the callers as a second line.
  Result await() {
    const uint32_t t0 = micros();
    uint32_t n = 0;
    while (!NRF_TWIM1->EVENTS_STOPPED && !NRF_TWIM1->EVENTS_ERROR) {
      if (((micros() - t0) >= TIMEOUT_US) || (++n >= SPIN_CAP)) {
        // Re-check ONCE before declaring death: the transaction may have
        // completed during preemption between the reads above and this
        // branch (finding #4 — a valid buffer must not be reported TIMEOUT).
        if (NRF_TWIM1->EVENTS_STOPPED || NRF_TWIM1->EVENTS_ERROR) break;
        quiesce();
        return TIMEOUT;
      }
    }
    // Something fired. Give a raced-in STOP a bounded beat to land, then
    // decode ERROR regardless of which event won the poll.
    if (NRF_TWIM1->EVENTS_ERROR) {
      for (uint32_t s = 0; s < STOP_CAP && !NRF_TWIM1->EVENTS_STOPPED; ++s) {}
      const uint32_t src = NRF_TWIM1->ERRORSRC;
      quiesce();  // also clears events; forced-disable only if STOP failed
      if (src & (TWIM_ERRORSRC_ANACK_Msk | TWIM_ERRORSRC_DNACK_Msk)) {
        return NACK;
      }
      return BUSERR;
    }
    clearEvents();
    return OK;
  }

  uint32_t sda_ = 0, scl_ = 0;
  bool begun_ = false;
  uint8_t txbuf_[8];
};
