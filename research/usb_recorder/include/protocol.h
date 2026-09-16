#pragma once
#include <stddef.h>
#include <stdint.h>

namespace jh6 {
constexpr uint8_t VERSION = 1;
constexpr uint8_t META = 0, SAMPLE = 1, END = 2, ERROR = 3, STATUS = 4, RAW_FIFO = 5;
constexpr uint16_t TIMESTAMP_VALID = 1u << 7;
constexpr uint16_t FIFO_OVERRUN = 1u << 6;

inline void u16(uint8_t* out, uint16_t value) {
  out[0] = value; out[1] = value >> 8;
}
inline void u32(uint8_t* out, uint32_t value) {
  for (uint8_t i = 0; i < 4; ++i) out[i] = value >> (8*i);
}
inline uint32_t crc32(const uint8_t* data, size_t size) {
  uint32_t crc = 0xFFFFFFFFu;
  for (size_t i = 0; i < size; ++i) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; ++bit)
      crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
  }
  return ~crc;
}
// AN5130 section 8.8: DS4 timestamp bytes are NOT ordinary little-endian.
inline uint32_t fifo_timestamp(const uint8_t* frame) {
  return uint32_t(frame[15]) | (uint32_t(frame[12]) << 8) |
         (uint32_t(frame[13]) << 16);
}
inline int16_t signed_word(const uint8_t* data) {
  return static_cast<int16_t>(uint16_t(data[0]) | (uint16_t(data[1]) << 8));
}
inline bool near_rail(int16_t value) { return value >= 32760 || value <= -32760; }
inline uint16_t rail_flags(const uint8_t* frame) {
  uint16_t flags = 0;
  for (uint8_t axis = 0; axis < 3; ++axis) {
    if (near_rail(signed_word(frame+6+2*axis))) flags |= 1u << axis;
    if (near_rail(signed_word(frame+2*axis))) flags |= 1u << (axis+3);
  }
  return flags;
}
}  // namespace jh6
