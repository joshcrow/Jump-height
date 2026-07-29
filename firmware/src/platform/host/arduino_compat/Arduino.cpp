// Arduino.cpp — definitions for the HOST Arduino compatibility shim
// (Arduino.h): Serial <-> stdin/stdout, millis/micros/delay, and the hidden
// main() that calls main.cpp's setup()/loop() — exactly the contract every
// real Arduino core's own (normally invisible) main.cpp implements, never
// otherwise seen from main.cpp itself.
//
// SPDX-License-Identifier: MIT

#include "Arduino.h"

#include <chrono>
#include <string>

#include <fcntl.h>
#include <unistd.h>

namespace {

using Clock = std::chrono::steady_clock;
const Clock::time_point kBoot = Clock::now();

// Small growable buffer fed by non-blocking reads of stdin; available()/
// read() serve it out one byte at a time — exactly the UART RX-buffer
// semantics main.cpp's own pollSerial() already assumes (a
// `while (Serial.available())` loop reading one char at a time).
std::string g_rx;
size_t g_rx_pos = 0;

void fillFromStdin() {
  char buf[4096];
  for (;;) {
    const ssize_t n = ::read(STDIN_FILENO, buf, sizeof(buf));
    if (n > 0) {
      g_rx.append(buf, (size_t)n);
      continue;
    }
    break;  // n == 0 (EOF) or n < 0 (EAGAIN/EWOULDBLOCK: nothing pending)
  }
  // Keep the buffer from growing unboundedly over a long session.
  if (g_rx_pos > 0 && g_rx_pos == g_rx.size()) {
    g_rx.clear();
    g_rx_pos = 0;
  } else if (g_rx_pos > (1u << 20)) {
    g_rx.erase(0, g_rx_pos);
    g_rx_pos = 0;
  }
}

}  // namespace

void HostSerial::begin(unsigned long /*baud*/) {
  // Unbuffered stdout: every emitted byte must reach the reader immediately
  // — main.cpp's protocol framing (OK/ERR terminators, FILE BEGIN/END)
  // depends on lines actually arriving promptly, not sitting in libc's
  // stdio buffer waiting for a flush that never comes.
  setvbuf(stdout, nullptr, _IONBF, 0);
  const int flags = fcntl(STDIN_FILENO, F_GETFL, 0);
  fcntl(STDIN_FILENO, F_SETFL, flags | O_NONBLOCK);
}

int HostSerial::available() {
  fillFromStdin();
  return (int)(g_rx.size() - g_rx_pos);
}

int HostSerial::read() {
  fillFromStdin();
  if (g_rx_pos >= g_rx.size()) return -1;
  return (unsigned char)g_rx[g_rx_pos++];
}

size_t HostSerial::write(const uint8_t* buf, size_t len) {
  size_t off = 0;
  while (off < len) {
    const ssize_t n = ::write(STDOUT_FILENO, buf + off, len - off);
    if (n <= 0) break;  // reader gone: drop the rest rather than block/spin
    off += (size_t)n;
  }
  return off;
}

HostSerial Serial;

uint32_t millis() {
  return (uint32_t)std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - kBoot)
      .count();
}

uint32_t micros() {
  return (uint32_t)std::chrono::duration_cast<std::chrono::microseconds>(Clock::now() - kBoot)
      .count();
}

void delay(uint32_t ms) { usleep((useconds_t)ms * 1000u); }

// The hidden Arduino core entry point: bring the sketch up once, then run
// its cooperative loop forever. The exact contract every real Arduino core
// provides via its own hidden main.cpp — never seen from main.cpp itself.
int main() {
  setup();
  for (;;) loop();
  return 0;
}
