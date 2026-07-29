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
//   0x11 CTRL2_G      gyro ODR + full-scale. Gyro stays OFF (power — see
//                     docs/sense.md §3.7's gyro policy: carve-G is
//                     accel-only, gyro is duty-cycled in later only when a
//                     spins metric ships) — ODR_G=0000 (power-down) => 0x00.
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
#include <Wire.h>

class Lsm6ds3Min {
 public:
  static const uint8_t I2C_ADDR = 0x6A;  // fixed by the board (docs/sense.md §1)

  // True if a device ACKs at this I2C address on this bus.
  static bool probe(TwoWire& wire, uint8_t addr) {
    wire.beginTransmission(addr);
    return wire.endTransmission() == 0;
  }

  // Configure ±16 g accel @ 208 Hz, BDU+auto-increment on, gyro powered down.
  // Returns false only if an I2C write fails (wiring problem).
  bool begin(TwoWire& wire, uint8_t addr) {
    wire_ = &wire;
    addr_ = addr;
    bool ok = true;
    ok &= writeReg(0x12, 0x44);  // CTRL3_C:  BDU=1, IF_INC=1
    ok &= writeReg(0x11, 0x00);  // CTRL2_G:  gyro power-down (policy: off)
    ok &= writeReg(0x10, 0x54);  // CTRL1_XL: 208 Hz, ±16 g (see file comment)
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

  uint8_t address() const { return addr_; }

 private:
  bool writeReg(uint8_t reg, uint8_t val) {
    wire_->beginTransmission(addr_);
    wire_->write(reg);
    wire_->write(val);
    return wire_->endTransmission() == 0;
  }

  bool readRegs(uint8_t reg, uint8_t* buf, uint8_t n) {
    wire_->beginTransmission(addr_);
    wire_->write(reg);
    if (wire_->endTransmission(false) != 0) return false;  // repeated start
    if (wire_->requestFrom(addr_, n) != n) return false;
    for (uint8_t i = 0; i < n; ++i) buf[i] = wire_->read();
    return true;
  }

  TwoWire* wire_ = nullptr;
  uint8_t  addr_ = I2C_ADDR;
};
