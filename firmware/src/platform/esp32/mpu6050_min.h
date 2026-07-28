// mpu6050_min.h
//
// Minimal register-level MPU-6050 driver (I2C, Arduino Wire).
//
// Why not a library? The popular drivers (e.g. Adafruit's) hard-fail unless
// the chip's WHO_AM_I register reads exactly 0x68 — and many cheap MPU-6050
// clone boards report other values (0x69, 0x70, 0x71, 0x98...) despite
// working fine. We only need ~5 registers, so we talk to them directly and
// treat an odd WHO_AM_I as a warning, not a failure.
//
// Register map facts used here (MPU-6000/6050 Register Map, rev 4.2):
//   0x19 SMPLRT_DIV    sample rate divider (internal rate 1 kHz with DLPF on)
//   0x1A CONFIG        DLPF_CFG[2:0]; 3 => accel LPF ~44 Hz
//   0x1B GYRO_CONFIG   FS_SEL[4:3];   1 => ±500 dps
//   0x1C ACCEL_CONFIG  AFS_SEL[4:3];  2 => ±8 g  (4096 LSB/g)
//   0x3B ACCEL_XOUT_H  6 bytes: X/Y/Z, big-endian int16
//   0x6B PWR_MGMT_1    bit6 SLEEP (set at power-up!); CLKSEL 1 => X-gyro PLL
//   0x75 WHO_AM_I      0x68 on genuine parts
//
// SPDX-License-Identifier: MIT

#pragma once

#include <Arduino.h>
#include <Wire.h>

class Mpu6050Min {
 public:
  static const uint8_t ADDR_PRIMARY   = 0x68;  // AD0 low (most breakouts)
  static const uint8_t ADDR_SECONDARY = 0x69;  // AD0 high

  // True if a device ACKs at this I2C address.
  static bool probe(TwoWire& wire, uint8_t addr) {
    wire.beginTransmission(addr);
    return wire.endTransmission() == 0;
  }

  // Wake the chip and configure ±8 g, ±500 dps, ~44 Hz DLPF.
  // Returns false only if the I2C writes fail (wiring problem).
  bool begin(TwoWire& wire, uint8_t addr) {
    wire_ = &wire;
    addr_ = addr;
    // Wake from the power-up SLEEP state; clock from X-gyro PLL (recommended
    // over the default internal oscillator for stable sampling).
    if (!writeReg(0x6B, 0x01)) return false;
    delay(10);  // clock settle
    bool ok = true;
    ok &= writeReg(0x19, 0x04);  // SMPLRT_DIV: 1kHz/(1+4) = 200 Hz internal
    ok &= writeReg(0x1A, 0x03);  // CONFIG: DLPF 44 Hz
    ok &= writeReg(0x1B, 0x08);  // GYRO_CONFIG: ±500 dps (unused in v1, set anyway)
    ok &= writeReg(0x1C, 0x10);  // ACCEL_CONFIG: ±8 g
    return ok;
  }

  // WHO_AM_I value, or 0x00 on read failure. 0x68 = genuine; clones vary.
  uint8_t whoAmI() {
    uint8_t v = 0;
    readRegs(0x75, &v, 1);
    return v;
  }

  // Read acceleration in g-units. False on I2C failure.
  bool readAccelG(float& ax, float& ay, float& az) {
    uint8_t b[6];
    if (!readRegs(0x3B, b, 6)) return false;
    const int16_t x = (int16_t)((b[0] << 8) | b[1]);
    const int16_t y = (int16_t)((b[2] << 8) | b[3]);
    const int16_t z = (int16_t)((b[4] << 8) | b[5]);
    const float lsb_per_g = 4096.0f;  // ±8 g range
    ax = x / lsb_per_g;
    ay = y / lsb_per_g;
    az = z / lsb_per_g;
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
  uint8_t  addr_ = ADDR_PRIMARY;
};
