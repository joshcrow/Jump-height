// jh_imu.cpp — ESP32 implementation of the jh_imu seam
// (firmware/include/platform/jh_imu.h). See docs/sense.md §3.9.
//
// Thin wrapper over mpu6050_min.h's register-level MPU-6050 driver, plus the
// I2C bus bring-up main.cpp used to do inline in setup(). Moved out of
// main.cpp verbatim; see mpu6050_min.h for the register-level details this
// wrapper does not re-explain.
//
// SPDX-License-Identifier: MIT

#include "platform/jh_imu.h"

#include <Wire.h>

#include "mpu6050_min.h"
#include "params.gen.h"

namespace jh_imu {

static Mpu6050Min s_imu;

void init() {
  Wire.begin(JH_I2C_SDA, JH_I2C_SCL);
  Wire.setClock(400000);
}

bool probe(uint8_t addr) {
  return Mpu6050Min::probe(Wire, addr);
}

bool begin(uint8_t addr) {
  return s_imu.begin(Wire, addr);
}

uint8_t who_am_i() {
  return s_imu.whoAmI();
}

bool read_accel_g(float& ax, float& ay, float& az) {
  return s_imu.readAccelG(ax, ay, az);
}

}  // namespace jh_imu
