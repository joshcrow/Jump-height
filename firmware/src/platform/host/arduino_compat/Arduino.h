// Arduino.h — minimal HOST-side Arduino compatibility shim.
//
// Exists ONLY so firmware/src/main.cpp (the chip-agnostic core) compiles
// and runs, completely unmodified, under platform=native (see
// firmware/platformio.ini's env:host). This directory is added to the
// include path for env:host ONLY (that env's build_flags) — the esp32 and
// nrf52 envs never see this file; they get the real framework's Arduino.h.
//
// Implements EXACTLY the Arduino surface main.cpp calls today (see the host
// port's own notes for the full inventory this was derived from) — nothing
// more:
//   * String — just enough of Arduino's to run main.cpp: the float/decimal-
//     places constructor, operator=(const char*), operator+=(String|char),
//     length(), c_str(), trim(), startsWith(), operator==(const char*),
//     indexOf(char, int), substring(int[, int]), toFloat(), reserve().
//   * Serial — begin()/available()/read()/write(), mapped to stdin/stdout
//     (definitions in Arduino.cpp): unbuffered, non-blocking, byte-at-a-
//     time — exactly what main.cpp's own pollSerial() already assumes.
//   * millis()/micros()/delay() — wall-clock based (Arduino.cpp).
//   * setup()/loop() — declared here (defined in main.cpp) so this shim's
//     own hidden main() (Arduino.cpp) can call them, exactly like a real
//     Arduino core's own (normally invisible) main.cpp does.
//
// SPDX-License-Identifier: MIT

#pragma once

#include <math.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <string>

// ------------------------------------------------------------------ String
// A thin wrapper over std::string with exactly the Arduino-shaped
// methods/signatures main.cpp calls. Not a general-purpose reimplementation
// of Arduino's String — anything main.cpp doesn't use is not here.
class String {
 public:
  String() = default;
  String(const char* s) : s_(s ? s : "") {}

  // Arduino's String(float, decimalPlaces) constructor: fixed-point
  // notation with exactly `decimalPlaces` digits after the point
  // (dtostrf-equivalent; snprintf's "%.*f" matches it for our purposes).
  String(float value, unsigned char decimalPlaces = 2) {
    char buf[64];
    snprintf(buf, sizeof(buf), "%.*f", (int)decimalPlaces, (double)value);
    s_ = buf;
  }

  String& operator=(const char* s) {
    s_ = s ? s : "";
    return *this;
  }
  String& operator+=(const String& o) {
    s_ += o.s_;
    return *this;
  }
  String& operator+=(char c) {
    s_ += c;
    return *this;
  }

  bool operator==(const char* s) const { return s_ == s; }

  size_t length() const { return s_.length(); }
  const char* c_str() const { return s_.c_str(); }

  void trim() {
    const size_t a = s_.find_first_not_of(" \t\r\n");
    if (a == std::string::npos) {
      s_.clear();
      return;
    }
    const size_t b = s_.find_last_not_of(" \t\r\n");
    s_ = s_.substr(a, b - a + 1);
  }

  bool startsWith(const char* prefix) const {
    const size_t n = strlen(prefix);
    return s_.compare(0, n, prefix) == 0;
  }

  // Arduino semantics: -1 when not found.
  int indexOf(char c, int fromIndex = 0) const {
    if (fromIndex < 0) fromIndex = 0;
    if ((size_t)fromIndex > s_.length()) return -1;
    const size_t p = s_.find(c, (size_t)fromIndex);
    return p == std::string::npos ? -1 : (int)p;
  }

  String substring(int from) const { return substring(from, (int)s_.length()); }
  String substring(int from, int to) const {
    if (from < 0) from = 0;
    if (to > (int)s_.length()) to = (int)s_.length();
    if (to < from) return String("");
    String out;
    out.s_ = s_.substr((size_t)from, (size_t)(to - from));
    return out;
  }

  float toFloat() const { return strtof(s_.c_str(), nullptr); }

  void reserve(size_t n) { s_.reserve(n); }

 private:
  std::string s_;
};

// ------------------------------------------------------------------ Serial
// USB serial mapped to stdin/stdout: unbuffered, byte-oriented, non-blocking
// reads (definitions in Arduino.cpp) — so main.cpp's own polling loop
// (pollSerial(), a `while (Serial.available())` loop) runs unmodified.
class HostSerial {
 public:
  void begin(unsigned long baud);
  int available();
  int read();
  size_t write(const uint8_t* buf, size_t len);
};
extern HostSerial Serial;

// -------------------------------------------------------- timing / entry point
// Real Arduino cores return `unsigned long` (32-bit on every Arduino target
// this firmware ships on); pinned here to an explicit 32-bit type instead —
// same width, no dependence on the host's own `long` size (64-bit on
// desktop Linux/macOS).
uint32_t millis();
uint32_t micros();
void delay(uint32_t ms);

// Defined in main.cpp; declared here so this shim's own hidden main()
// (Arduino.cpp) can call them — exactly like a real Arduino core's own
// hidden main.cpp does.
void setup();
void loop();
