// Dedicated USB bench recorder. No BLE, product detector, flash store or NVS.
// Register configuration: ST LSM6DS3TR-C datasheet + AN5130 sections 8.4/8.8.
#include <Arduino.h>
#include <Adafruit_TinyUSB.h>
#include <nrf_gpio.h>
#include "twim_bounded.h"  // Reuse audited bus, read-only in production tree.
#include "protocol.h"

#ifndef JH6_BUILD
#error Build identity must be supplied by build_identity.py
#endif

namespace {
TwimBounded bus;
constexpr uint8_t ADDR = 0x6A;
constexpr uint8_t FIFO_BYPASS = 0x00, FIFO_CONTINUOUS = 0x2E;
constexpr uint32_t MAX_DURATION_MS = 1200000; // 20 minutes; uint32 microseconds remain safe.
bool sensor_ok = false;
char sensor_error[80] = "not_initialized";
char uid[17], name[24];
uint32_t i2c_errors = 0;
// The core's loop task has a 1024-word (4096-byte) stack. Command processing is
// single-threaded; keep the framing/JSON scratch space off that stack.
char json[1200];
uint8_t frame_buffer[1212];

uint32_t timer_us() {
  NRF_TIMER4->TASKS_CAPTURE[0] = 1;
  return NRF_TIMER4->CC[0];
}
void init_timer() {
  NRF_TIMER4->TASKS_STOP = 1;
  NRF_TIMER4->MODE = TIMER_MODE_MODE_Timer;
  NRF_TIMER4->BITMODE = TIMER_BITMODE_BITMODE_32Bit;
  NRF_TIMER4->PRESCALER = 4;  // 16MHz / 16 = 1MHz, independent of Arduino RTC ticks.
  NRF_TIMER4->SHORTS = 0;
  NRF_TIMER4->INTENCLR = 0xFFFFFFFFu;
  NRF_TIMER4->TASKS_CLEAR = 1;
  NRF_TIMER4->TASKS_START = 1;
}
bool write_reg(uint8_t reg, uint8_t value) {
  uint8_t packet[2] = {reg, value};
  if (bus.write(ADDR, packet, 2) == TwimBounded::OK) return true;
  ++i2c_errors; return false;
}
bool read_regs(uint8_t reg, uint8_t* data, uint8_t count) {
  if (bus.writeThenRead(ADDR, &reg, 1, data, count) == TwimBounded::OK) return true;
  ++i2c_errors; return false;
}
bool config(uint8_t reg, uint8_t value) {
  uint8_t readback = 0;
  if (write_reg(reg, value) && read_regs(reg, &readback, 1) && readback == value) return true;
  snprintf(sensor_error, sizeof(sensor_error), "register_0x%02x_expected_0x%02x_got_0x%02x", reg, value, readback);
  return false;
}

bool usb_write(const uint8_t* data, size_t size, uint32_t timeout_us = 2000000) {
  const uint32_t start = timer_us();
  size_t offset = 0;
  while (offset < size) {
    if (!Serial || uint32_t(timer_us()-start) >= timeout_us) return false;
    int available = Serial.availableForWrite();
    if (available > 0) {
      size_t chunk = min(size-offset, static_cast<size_t>(available));
      size_t written = Serial.write(data+offset, chunk);
      offset += written;
    } else {
      yield();
    }
  }
  Serial.flush();  // TinyUSB flush submits buffered data; not an unbounded drain.
  return true;
}
bool text(const char* str) { return usb_write(reinterpret_cast<const uint8_t*>(str), strlen(str)); }
bool frame(uint8_t type, const uint8_t* payload, uint16_t size, uint32_t timeout_us = 2000000) {
  if (size > 1200) return false;
  uint8_t* bytes = frame_buffer;
  memcpy(bytes, "JH6F", 4); bytes[4] = jh6::VERSION; bytes[5] = type;
  jh6::u16(bytes+6, size);
  memcpy(bytes+8, payload, size);
  jh6::u32(bytes+8+size, jh6::crc32(bytes, size+8));
  return usb_write(bytes, size+12, timeout_us);
}
bool json_frame(uint8_t type, const char* json) {
  return frame(type, reinterpret_cast<const uint8_t*>(json), strlen(json));
}
void metadata(char* out, size_t capacity, uint32_t duration_ms = 0) {
  snprintf(out, capacity,
    "{\"recorder\":\"jh6-usb-research\",\"uid\":\"%s\",\"name\":\"%s\",\"build\":\"%s\","
    "\"sensor_ok\":%s,\"sensor_error\":\"%s\",\"odr_hz\":208,"
    "\"accel_g_per_lsb\":0.000488,\"gyro_dps_per_lsb\":0.070,"
    "\"timestamp_tick_us\":25,\"timestamp_bits\":24,\"timestamp_mode\":\"fifo\","
    "\"mcu_timebase\":\"TIMER4_1MHz_service_time\",\"duration_ms\":%lu,"
    "\"fifo_words_per_sample\":9,\"discard_startup_samples\":1,"
    "\"rail_threshold_counts\":32760,\"gyro_lpf1\":false,\"usb_only\":true,"
    "\"max_duration_ms\":1200000,\"status_interval_us\":1000000,\"raw_fifo_frames\":true,"
    "\"hfclkstat\":%lu,\"timer4_prescaler\":%lu,\"timer4_bitmode\":%lu,"
    "\"timer4_mode\":%lu,\"temp_c_formula\":\"25+raw/256\"}",
    uid, name, JH6_BUILD, sensor_ok ? "true" : "false", sensor_error,
    static_cast<unsigned long>(duration_ms),
    (unsigned long)NRF_CLOCK->HFCLKSTAT, (unsigned long)NRF_TIMER4->PRESCALER,
    (unsigned long)NRF_TIMER4->BITMODE, (unsigned long)NRF_TIMER4->MODE);
}
// Telemetry is independent of FIFO samples. Its timestamp brackets the die
// temperature read; seq_next ties the status to the surrounding sample stream.
bool status_frame(uint32_t seq_next) {
  uint8_t raw[2];
  const uint32_t before = timer_us();
  if (!read_regs(0x20, raw, sizeof(raw))) return false;
  const uint32_t after = timer_us();
  const int16_t temp_raw = jh6::signed_word(raw);
  snprintf(json, sizeof(json),
    "{\"seq_next\":%lu,\"mcu_us\":%lu,\"temp_read_before_us\":%lu,"
    "\"hfclkstat\":%lu,\"timer4_prescaler\":%lu,\"timer4_bitmode\":%lu,"
    "\"timer4_mode\":%lu,\"temp_raw\":%d,\"temp_c\":%.6f}",
    (unsigned long)seq_next, (unsigned long)after, (unsigned long)before,
    (unsigned long)NRF_CLOCK->HFCLKSTAT, (unsigned long)NRF_TIMER4->PRESCALER,
    (unsigned long)NRF_TIMER4->BITMODE, (unsigned long)NRF_TIMER4->MODE,
    (int)temp_raw, 25.0 + double(temp_raw)/256.0);
  return json_frame(jh6::STATUS, json);
}
bool init_sensor() {
  const uint32_t power = g_ADigitalPinMap[PIN_LSM6DS3TR_C_POWER];
  nrf_gpio_cfg(power, NRF_GPIO_PIN_DIR_OUTPUT, NRF_GPIO_PIN_INPUT_CONNECT,
               NRF_GPIO_PIN_NOPULL, NRF_GPIO_PIN_H0H1, NRF_GPIO_PIN_NOSENSE);
  nrf_gpio_pin_set(power);  // Do not cycle the supply through a weak GPIO drive.
  delay(120);
  bus.begin(PIN_WIRE1_SDA, PIN_WIRE1_SCL);
  uint8_t who = 0;
  if (!read_regs(0x0F, &who, 1) || who != 0x6A) {
    snprintf(sensor_error, sizeof(sensor_error), "whoami_0x%02x", who); return false;
  }
  if (!write_reg(0x12, 0x01)) return false;
  const uint32_t reset_start = timer_us();
  uint8_t control = 1;
  do {
    if (!read_regs(0x12, &control, 1)) return false;
    if (uint32_t(timer_us()-reset_start) > 100000) {
      strcpy(sensor_error, "software_reset_timeout"); return false;
    }
  } while (control & 1);
  const uint8_t registers[][2] = {
    {0x12,0x44}, // BDU + address auto-increment.
    {0x15,0x00}, {0x16,0x00}, {0x17,0x00}, // High-performance, no HP/LPF2.
    {0x13,0x00}, // Gyro LPF1 disabled: explicit research configuration.
    {0x10,0x54}, {0x11,0x5C}, // Accel16g + gyro2000dps, both208Hz.
    {0x5C,0x10}, {0x19,0x24}, // High-resolution25us timestamp + FUNC_EN.
    {0x1A,0x00}, // FIFO trigger from gyro/accel data-ready.
    {0x06,0x48}, {0x07,0x80}, //72-word watermark; timestamp to DS4, no pedometer trigger.
    {0x08,0x09}, {0x09,0x08}, // Gyro, accel and DS4 all undecimated.
    {0x0A,FIFO_BYPASS}
  };
  for (const auto& item : registers) if (!config(item[0], item[1])) return false;
  delay(1000);  // Settle sensors while FIFO is BYPASS; do not overrun a waiting FIFO.
  strcpy(sensor_error, "none");
  return true;
}

void capture(uint32_t duration_ms) {
  metadata(json, sizeof(json), duration_ms);
  if (!json_frame(jh6::META, json)) return;
  if (!sensor_ok) {
    snprintf(json, sizeof(json), "{\"error\":\"sensor_unavailable\",\"detail\":\"%s\"}", sensor_error);
    json_frame(jh6::ERROR, json); return;
  }
  i2c_errors = 0;
  if (!config(0x0A, FIFO_BYPASS) || !write_reg(0x42, 0xAA) || !config(0x0A, FIFO_CONTINUOUS)) {
    json_frame(jh6::ERROR, "{\"error\":\"capture_start_failed\"}"); return;
  }
  const uint32_t started = timer_us();
  uint32_t last_status = started;
  uint32_t count = 0, dropped = 0, overruns = 0, discarded = 0;
  uint32_t first_ticks = 0, last_ticks = 0, first_service = 0, last_service = 0;
  uint16_t max_words = 0;
  const char* failure = "none";
  if (!status_frame(0)) failure = "status_start_failed";
  while (strcmp(failure, "none") == 0 && uint32_t(timer_us()-started) < duration_ms*1000u) {
    if (uint32_t(timer_us()-last_status) >= 1000000) {
      if (!status_frame(count)) { failure = "status_periodic_failed"; break; }
      last_status = timer_us();
    }
    uint8_t status[4];
    if (!read_regs(0x3A, status, 4)) { failure = "fifo_status_i2c"; break; }
    const uint16_t words = uint16_t(status[0]) | (uint16_t(status[1]&0x07) << 8);
    max_words = max(max_words, words);
    if (status[1] & 0x40) { ++overruns; failure = "fifo_overrun"; break; }
    if (words < 9) { yield(); continue; }
    const uint16_t pattern = uint16_t(status[2]) | (uint16_t(status[3]&3) << 8);
    if (pattern != 0) { failure = "fifo_pattern_misaligned"; break; }
    uint8_t raw[18];
    // IF_INC auto-wraps 0x3E/0x3F for FIFO multi-byte bursts (AN5130 8.4).
    if (!read_regs(0x3E, raw, sizeof(raw))) { failure = "fifo_read_i2c"; break; }
    const uint32_t serviced = timer_us();
    if (!discarded) { ++discarded; continue; } // First frame after bypass transition.
    const uint32_t ticks = jh6::fifo_timestamp(raw);
    if (count == 0) { first_ticks = ticks; first_service = serviced; }
    last_ticks = ticks; last_service = serviced;
    uint8_t payload[28];
    jh6::u32(payload, count);
    jh6::u32(payload+4, serviced);
    jh6::u32(payload+8, ticks);
    memcpy(payload+12, raw, 12); // GxGyGz AxAyAz, original signed int16.
    jh6::u16(payload+24, jh6::rail_flags(raw) | jh6::TIMESTAMP_VALID);
    jh6::u16(payload+26, words);
    // Preserve every original FIFO byte, including DS4 padding/step fields.
    // This evidence is needed to diagnose the observed one-record tick reversal.
    uint8_t fifo_payload[22];
    jh6::u32(fifo_payload, count);
    memcpy(fifo_payload+4, raw, sizeof(raw));
    if (!frame(jh6::RAW_FIFO, fifo_payload, sizeof(fifo_payload), 50000)) {
      ++dropped; failure = "usb_raw_fifo_backpressure"; break;
    }
    ++count;
    if (!frame(jh6::SAMPLE, payload, sizeof(payload), 50000)) {
      ++dropped; failure = "usb_backpressure"; break;
    }
  }
  const uint32_t elapsed_us = timer_us()-started;
  if (!config(0x0A, FIFO_BYPASS)) failure = "fifo_stop_i2c";
  if (!status_frame(count)) failure = "status_end_failed";
  snprintf(json, sizeof(json),
    "{\"sample_count\":%lu,\"dropped_frames\":%lu,\"i2c_errors\":%lu,\"fifo_overruns\":%lu,"
    "\"discarded_startup_samples\":%lu,\"duration_us\":%lu,\"first_ticks\":%lu,\"last_ticks\":%lu,"
    "\"first_service_us\":%lu,\"last_service_us\":%lu,\"max_fifo_words\":%u,\"failure\":\"%s\"}",
    (unsigned long)count, (unsigned long)dropped, (unsigned long)i2c_errors,
    (unsigned long)overruns, (unsigned long)discarded, (unsigned long)elapsed_us,
    (unsigned long)first_ticks, (unsigned long)last_ticks,
    (unsigned long)first_service, (unsigned long)last_service, max_words, failure);
  if (strcmp(failure, "none") != 0) json_frame(jh6::ERROR, json);
  json_frame(jh6::END, json);
}
void command(const char* line) {
  if (strcmp(line, "info") == 0) {
    metadata(json, sizeof(json));
    text("JH6 INFO "); text(json); text("\nOK info\n");
  } else if (strcmp(line, "dfu") == 0) {
    text("OK dfu\n"); delay(100); enterSerialDfu();
  } else if (strncmp(line, "capture ", 8) == 0) {
    char* end = nullptr;
    unsigned long duration = strtoul(line+8, &end, 10);
    if (!end || *end || duration < 100 || duration > MAX_DURATION_MS) {
      text("ERR capture duration_ms must be100..1200000\n"); return;
    }
    capture(duration);
  } else if (strcmp(line, "help") == 0) {
    text("info | capture <100..1200000 ms> | dfu\nOK help\n");
  } else {
    text("ERR unknown_command\n");
  }
}
} // namespace

void setup() {
  init_timer();
  snprintf(uid, sizeof(uid), "%08lX%08lX", (unsigned long)NRF_FICR->DEVICEID[1], (unsigned long)NRF_FICR->DEVICEID[0]);
  snprintf(name, sizeof(name), "JumpHeight-%04X", unsigned(NRF_FICR->DEVICEADDR[0]&0xFFFFu));
  Serial.begin(115200);
  sensor_ok = init_sensor();
}
void loop() {
  static char line[80];
  static uint8_t used = 0;
  while (Serial.available()) {
    int ch = Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      line[used] = 0;
      if (used) command(line);
      used = 0;
    } else if (ch >= 0 && used < sizeof(line)-1) {
      line[used++] = char(ch);
    } else {
      used = 0; text("ERR command_too_long\n");
    }
  }
  delay(1);
}
