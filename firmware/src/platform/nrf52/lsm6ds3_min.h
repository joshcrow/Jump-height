// lsm6ds3_min.h
//
// Minimal register-level LSM6DS3TR-C driver (I2C, Arduino Wire) — the
// Sense's onboard IMU sibling of the ESP32 platform's mpu6050_min.h
// (src/platform/esp32/mpu6050_min.h). Same reasoning applies: we only need
// ~5 registers, so we talk to them directly rather than pulling in a vendor
// library (a recorded project decision — see DECISIONS.md #13, extended to
// this chip by docs/sense.md §3.7's "new small driver... no vendor
// libraries" plan). Unlike the MPU-6050, this part's I2C address is fixed
// by the board's own wiring (not user-strappable), so there is no clone-ID
// leniency story here — see jh_imu.cpp for how the shared dual-address
// self-test loop (built for the MPU-6050's AD0-strapping) is mapped onto
// this single-address part.
//
// Register map facts used here, confirmed against ST's own official driver
// source (STMicroelectronics/lsm6ds3tr-c-pid, lsm6ds3tr-c_reg.h/.c on
// GitHub — the datasheet PDF itself was not directly fetchable in the
// authoring environment, so this citation is the driver header ST
// publishes, not the PDF's page number; SENSE_FIRST_BOOT.md marks the
// values below VERIFY on real silicon regardless):
//   0x0F WHO_AM_I     expected 0x6A for the "-C" (trimmed/tested) part
//                     (LSM6DS3TR_C_ID in ST's header) — NOTE the plain
//                     LSM6DS3 (no "-C") reports 0x69 instead; this board is
//                     the "-C" part (docs/sense.md §1).
//   0x10 CTRL1_XL     accel ODR + full-scale + anti-alias BW. Bit layout
//                     (ST's lsm6ds3tr_c_ctrl1_xl_t): [7:4] ODR_XL, [3:2]
//                     FS_XL, [1] LPF1_BW_SEL, [0] BW0_XL. We use
//                     ODR_XL=0101 (208 Hz, LSM6DS3TR_C_XL_ODR_208Hz=5) and
//                     FS_XL=01 (±16 g, LSM6DS3TR_C_16g=1 — ST's encoding
//                     is non-monotonic: 00=2g, 01=16g, 10=4g, 11=8g), BW
//                     bits left at the default (0b00) => CTRL1_XL = 0x54.
//                     ±16 g (vs the ESP32 build's ±8 g) is deliberate,
//                     research-backed (docs/research.md §2/§6): real
//                     board-sport landings peak 4.2-5.5+ g and marine
//                     impact literature runs 7-10 g+, so ±8 g clips the
//                     landing PEAKS our crash-severity analytics want.
//                     Detection is unaffected (thresholds live at 0.35 g
//                     and 2.5 g), and the binary trace's u16 milli-g
//                     format was given ±16 g headroom by design
//                     (ota.md §4.5). Resolution cost: 0.488 vs 0.244
//                     mg/LSB — noise floor, not signal, at our scales.
//   0x11 CTRL2_G      gyro ODR + full-scale. The gyro is now ON — 208 Hz
//                     (ODR_G=0101, matching the accel so both axes of the
//                     correction are sampled together) at ±2000 dps
//                     (FS_G=11) => CTRL2_G = 0x5C.
//
//                     THIS REVERSES docs/sense.md §3.7's original policy
//                     ("carve-G is accel-only, gyro is duty-cycled in later
//                     only when a spins metric ships"). That policy assumed
//                     the gyro was a trick-metric luxury. It is not:
//                     sim/experiments/g4_spin_detector.py showed the
//                     accel-only detector reads -93% low on height at 300
//                     dps peak spin, and real wing spins run 240-360 mean /
//                     500-900 peak. The gyro is a DETECTOR input — see
//                     jump_detector.h's correct_for_spin().
//
//                     ±2000 dps, not the ±500 the ESP32 build used: peaks
//                     of 500-900 dps would clip a ±500 range on exactly the
//                     jumps the correction exists to fix, and a clipped
//                     omega under-corrects silently.
//
//                     POWER, and an OPEN DECISION: the gyro costs ~0.9 mA
//                     (combo mode — the old 3.6 mA figure was the retired
//                     ESP32 MPU-6050). Always-on is what this does, because
//                     it is unconditionally CORRECT. Duty-cycling it awake
//                     only for flight would save that current, but the
//                     free-fall gate confirms in 80 ms (JH_FREEFALL_CONFIRM_S)
//                     and the LSM6DS3's gyro turn-on/settle is the same
//                     order — so a duty-cycled gyro risks being unsettled
//                     during the very window it is needed. Measure the
//                     settle time on silicon before attempting it.
//   0x13 CTRL4_C      LPF1_SEL_G (bit 1) = 1 — enable the gyro's digital
//                     LPF1. DECISIONS.md #29 / docs/gyro-sim-plan.md §4
//                     specify "digital LPF on"; this is the bit that does
//                     it. Bit positions taken from ST's own register driver
//                     (github.com/STMicroelectronics/lsm6ds3tr-c-pid,
//                     lsm6ds3tr-c_reg.h), NOT from memory. Note bit 2 is
//                     I2C_disable and MUST stay 0 — we are on I2C.
//                     => CTRL4_C = 0x02.
//   0x15 CTRL6_C      FTYPE[1:0] selects the LPF1 bandwidth. At our 208 Hz
//                     ODR every FTYPE option lands at ~67 Hz (ST AN5130
//                     Table 11 — the main datasheet's LPF1 table only lists
//                     833 Hz/1.6/3.3/6.6 kHz ODRs, which is why this looks
//                     unsupported at first glance; an ST moderator confirmed
//                     Table 11 covers 208 Hz). So the choice is immaterial
//                     here: FTYPE=00. ~67 Hz is a good place to sit anyway —
//                     far above spin dynamics (a few Hz), well under the
//                     104 Hz Nyquist. => CTRL6_C = 0x00.
//   0x16 CTRL7_G      G_HM_MODE (bit 7) = 0 => gyro HIGH-PERFORMANCE mode.
//                     Load-bearing, not decoration: AN5130 states LPF1 is
//                     BYPASSED in low-power/normal mode regardless of
//                     LPF1_SEL_G, so the LPF above only exists because of
//                     this. 0x00 is also the reset value, but written
//                     explicitly per this file's "configure every register
//                     you depend on" rule. => CTRL7_G = 0x00.
//   0x22 OUTX_L_G     gyro 6-byte burst, same little-endian-per-axis layout
//                     as the accel block below.
//   Sensitivity @ ±2000 dps: 70 mdps/LSB (ST's
//                     lsm6ds3tr_c_from_fs2000dps_to_mdps()).
//   0x12 CTRL3_C      BDU (bit 6, Block Data Update — a multi-byte read
//                     can't straddle a sensor update) and IF_INC (bit 2,
//                     auto-increment the register address across a burst
//                     read — required for the 6-byte accel burst below) =>
//                     CTRL3_C = 0x44. (IF_INC defaults to 1 out of reset
//                     per ST's own header, but we set it explicitly rather
//                     than depend on reset state, matching mpu6050_min.h's
//                     style of writing every register it configures.)
//   0x28 OUTX_L_XL    6-byte burst: X_L, X_H, Y_L, Y_H, Z_L, Z_H — NOTE this
//                     is LITTLE-endian per axis (low byte at the lower
//                     address), the OPPOSITE of the MPU-6050's big-endian
//                     ACCEL_XOUT_H burst in mpu6050_min.h. Getting this
//                     backwards silently halves-or-doubles readings in a
//                     way that can look plausible — called out here on
//                     purpose.
//   Sensitivity @ ±16 g: 0.488 mg/LSB (ST's lsm6ds3tr_c_from_fs16g_to_mg()).
//
// SPDX-License-Identifier: MIT

#pragma once

#include <Arduino.h>

#include "twim_bounded.h"

class Lsm6ds3Min {
 public:
  static const uint8_t I2C_ADDR = 0x6A;  // fixed by the board (docs/sense.md §1)

  // True if a device ACKs at this I2C address on this bus. Bounded (16d):
  // writes the WHO_AM_I register pointer — a held bus times out in ~2 ms
  // and reports absent instead of hanging the caller into the watchdog.
  // (Wire's probe was an address-only zero-byte write; a one-byte register
  // pointer write is the same ACK question with no zero-MAXCNT corner.)
  static bool probe(TwimBounded& bus, uint8_t addr) {
    const uint8_t reg = 0x0F;
    return bus.write(addr, &reg, 1) == TwimBounded::OK;
  }

  // Configure ±16 g accel and ±2000 dps gyro, both @ 208 Hz, BDU+auto-increment
  // on. Returns false only if an I2C write fails (wiring problem).
  bool begin(TwimBounded& bus, uint8_t addr) {
    wire_ = &bus;
    addr_ = addr;
    bool ok = true;
    ok &= writeReg(0x12, 0x44);  // CTRL3_C:  BDU=1, IF_INC=1
    ok &= writeReg(0x11, 0x5C);  // CTRL2_G:  208 Hz, ±2000 dps (see file comment)
    ok &= writeReg(0x10, 0x54);  // CTRL1_XL: 208 Hz, ±16 g (see file comment)
    ok &= writeReg(0x16, 0x00);  // CTRL7_G:  gyro high-performance (LPF1 needs it)
    ok &= writeReg(0x15, 0x00);  // CTRL6_C:  FTYPE=00 (~67 Hz LPF1 @ 208 Hz ODR)
    ok &= writeReg(0x13, 0x02);  // CTRL4_C:  LPF1_SEL_G=1 — gyro digital LPF on
    return ok;
  }

  // WHO_AM_I value, or 0x00 on read failure. 0x6A = genuine LSM6DS3TR-C.
  // Passed through HONESTLY (not remapped to 0x68) so main.cpp's self-test
  // hint text is at least numerically truthful even though its wording
  // ("likely a clone MPU-6050") doesn't literally apply on this chip — see
  // jh_imu.cpp and firmware/SENSE_FIRST_BOOT.md.
  uint8_t whoAmI() {
    uint8_t v = 0;
    readRegs(0x0F, &v, 1);
    return v;
  }

  // Read acceleration in g-units. False on I2C failure.
  bool readAccelG(float& ax, float& ay, float& az) {
    uint8_t b[6];
    if (!readRegs(0x28, b, 6)) return false;
    // Little-endian per axis (see the file comment) — low byte first.
    const int16_t x = (int16_t)((b[1] << 8) | b[0]);
    const int16_t y = (int16_t)((b[3] << 8) | b[2]);
    const int16_t z = (int16_t)((b[5] << 8) | b[4]);
    const float g_per_lsb = 0.000488f;  // ±16 g range, 0.488 mg/LSB
    ax = x * g_per_lsb;
    ay = y * g_per_lsb;
    az = z * g_per_lsb;
    return true;
  }

  // Read angular rate in deg/s. False on I2C failure.
  //
  // Same little-endian-per-axis trap as readAccelG — and worth restating,
  // because a byte-swapped gyro does not look broken, it looks like a
  // slightly wrong spin rate, which the correction then quietly turns into a
  // slightly wrong height.
  bool readGyroDps(float& gx, float& gy, float& gz) {
    uint8_t b[6];
    if (!readRegs(0x22, b, 6)) return false;  // OUTX_L_G
    const int16_t x = (int16_t)((b[1] << 8) | b[0]);
    const int16_t y = (int16_t)((b[3] << 8) | b[2]);
    const int16_t z = (int16_t)((b[5] << 8) | b[4]);
    const float dps_per_lsb = 0.070f;  // ±2000 dps range, 70 mdps/LSB
    gx = x * dps_per_lsb;
    gy = y * dps_per_lsb;
    gz = z * dps_per_lsb;
    return true;
  }

  uint8_t address() const { return addr_; }

 private:
  bool writeReg(uint8_t reg, uint8_t val) {
    if (!wire_) return false;  // same null-before-begin() hazard as readRegs
    const uint8_t d[2] = {reg, val};
    return wire_->write(addr_, d, 2) == TwimBounded::OK;
  }

  bool readRegs(uint8_t reg, uint8_t* buf, uint8_t n) {
    // wire_ is null until begin() runs, and EVERY public read lands here.
    // Without this guard a read before begin() dereferences null — a hard
    // fault, which on this part means a boot loop: the crash lands a few
    // lines after BLE starts, so the puck advertises for milliseconds per
    // cycle and USB never finishes re-enumerating. It presents as "board is
    // simply dead", which is a long way from the actual cause.
    //
    // Cost me a bricked board on 2026-08-11 by calling read_gyro_dps() in
    // setup() five lines before runSelfTest() got around to begin(). Returning
    // false is the honest answer — "no reading available" — and every caller
    // already handles it.
    if (!wire_) return false;
    // Register pointer + repeated-start burst read, both time-bounded (16d):
    // a bus that wedges MID-SESSION now costs one ~2 ms timeout per tick —
    // "no reading available", the detector's existing skip path — instead of
    // a watchdog reset that ends the session.
    return wire_->writeThenRead(addr_, &reg, 1, buf, n) == TwimBounded::OK;
  }

  TwimBounded* wire_ = nullptr;
  uint8_t     addr_ = I2C_ADDR;
};
