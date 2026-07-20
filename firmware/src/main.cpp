// Jump Height — ESP32 firmware
//
// Reads an MPU-6050 over I2C at a fixed rate, runs the airtime jump detector
// (jump_detector.h), and reports jumps over USB serial. Two optional modes:
//   * STREAM_RAW = 1  -> print raw CSV (t,ax,ay,az) for capturing sessions to
//                        replay/tune offline with sim/run.py --csv
//   * ENABLE_BLE = 1  -> also notify jump events over BLE (Nordic UART service)
//
// This is a starting skeleton: it compiles against the Arduino-ESP32 core +
// Adafruit MPU6050 lib (see platformio.ini). Validate on the bench (Phase 1).
//
// SPDX-License-Identifier: MIT

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include "jump_detector.h"

// ---------------- Configuration ----------------
#define I2C_SDA     21     // set to your board's pins (C3/S3 have no fixed I2C pins)
#define I2C_SCL     22
#define SAMPLE_HZ   200    // sampling rate; timing precision -> height precision
#define STREAM_RAW  0      // 1 = dump raw CSV instead of detecting (for capture)
#define ENABLE_BLE  0      // 1 = advertise + notify jumps over BLE (Phase 3)

static const float    G                  = 9.80665f;
static const uint32_t SAMPLE_INTERVAL_US = 1000000UL / SAMPLE_HZ;

Adafruit_MPU6050 mpu;
jump::Detector   detector;

// Session stats
static float    best_height = 0.0f;
static uint32_t jump_count  = 0;
static uint32_t t0_us       = 0;

// ---------------- Optional BLE ----------------
#if ENABLE_BLE
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>
// Nordic UART Service (widely supported by generic BLE apps / Web Bluetooth)
#define NUS_SVC_UUID "6E400001-B5A3-F393-E0A9-E50E24DCCA9E"
#define NUS_TX_UUID  "6E400003-B5A3-F393-E0A9-E50E24DCCA9E"  // device -> app (notify)

static BLECharacteristic* txChar     = nullptr;
static bool               bleConnected = false;

class ServerCB : public BLEServerCallbacks {
  void onConnect(BLEServer*) override { bleConnected = true; }
  void onDisconnect(BLEServer* s) override {
    bleConnected = false;
    s->getAdvertising()->start();  // allow reconnect
  }
};

static void bleInit() {
  BLEDevice::init("JumpHeight");
  BLEServer* server = BLEDevice::createServer();
  server->setCallbacks(new ServerCB());
  BLEService* svc = server->createService(NUS_SVC_UUID);
  txChar = svc->createCharacteristic(NUS_TX_UUID, BLECharacteristic::PROPERTY_NOTIFY);
  txChar->addDescriptor(new BLE2902());
  svc->start();
  BLEDevice::getAdvertising()->addServiceUUID(NUS_SVC_UUID);
  BLEDevice::startAdvertising();
}

static void bleNotify(const char* s) {
  if (bleConnected && txChar) {
    txChar->setValue((uint8_t*)s, strlen(s));
    txChar->notify();
  }
}
#endif  // ENABLE_BLE

// ---------------- Setup ----------------
void setup() {
  Serial.begin(115200);
  delay(200);

  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000);  // fast I2C

  if (!mpu.begin()) {
    Serial.println("MPU6050 not found — check wiring / I2C address (0x68/0x69).");
    while (true) delay(1000);
  }
  // ±8 g so landing spikes don't clip while free-fall resolution stays good.
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_44_HZ);  // tame vibration, keep landing spike

#if ENABLE_BLE
  bleInit();
#endif

#if STREAM_RAW
  Serial.println("t_s,ax,ay,az");  // CSV header — capture and replay with sim/run.py
#else
  Serial.println("# Jump Height ready. Send it! 🌊");
#endif

  t0_us = micros();
}

// ---------------- Loop ----------------
void loop() {
  static uint32_t next_us = micros();
  const uint32_t  now     = micros();
  // Pace the loop to SAMPLE_HZ. The int32_t cast handles micros() wraparound.
  if ((int32_t)(now - next_us) < 0) return;
  next_us += SAMPLE_INTERVAL_US;

  sensors_event_t a, gyro, temp;
  mpu.getEvent(&a, &gyro, &temp);

  const float ax = a.acceleration.x / G;  // m/s^2 -> g
  const float ay = a.acceleration.y / G;
  const float az = a.acceleration.z / G;
  const float t  = (now - t0_us) * 1e-6f;

#if STREAM_RAW
  Serial.printf("%.4f,%.4f,%.4f,%.4f\n", t, ax, ay, az);
#else
  const float mag = sqrtf(ax * ax + ay * ay + az * az);  // orientation-independent
  jump::JumpEvent ev;
  if (detector.update(t, mag, ev)) {
    jump_count++;
    if (ev.height_m > best_height) best_height = ev.height_m;
    Serial.printf("JUMP #%lu  airtime=%.2fs  height=%.2fm (%.1fft)  best=%.2fm\n",
                  (unsigned long)jump_count, ev.airtime_s, ev.height_m,
                  ev.height_m * 3.28084f, best_height);
#if ENABLE_BLE
    char buf[96];
    snprintf(buf, sizeof(buf),
             "{\"n\":%lu,\"airtime\":%.2f,\"height\":%.2f}",
             (unsigned long)jump_count, ev.airtime_s, ev.height_m);
    bleNotify(buf);
#endif
  }
#endif  // STREAM_RAW
}
