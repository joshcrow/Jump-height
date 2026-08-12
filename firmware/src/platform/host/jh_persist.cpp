// jh_persist.cpp — HOST implementation of the jh_persist seam
// (firmware/include/platform/jh_persist.h). See firmware/platformio.ini's
// env:host.
//
// ESP32 uses Preferences/NVS (a namespace + 2 named keys); this platform has
// no NVS, so the same 2 keys ("offset", "scale") live as "key=value" lines in
// one plain-text file, $JH_HOST_DIR/jumpcal.txt (JH_HOST_DIR defaults to
// /tmp — see host_paths.h). That's a REAL file on REAL disk, so `set
// airtime_offset_s <v>` genuinely survives a process restart with the same
// JH_HOST_DIR, exactly like NVS surviving a reboot on the real board.
//
// SPDX-License-Identifier: MIT

#include "platform/jh_persist.h"

#include <cstdio>
#include <cstdlib>
#include <map>
#include <string>

#include "host_paths.h"

namespace jh_persist {

namespace {

std::string filePath() { return jh_host::path("jumpcal.txt"); }

// Whole-file read: this store is at most 2 short lines, so read-modify-
// write-the-whole-file on every save()/clear() (mirroring how little state
// NVS itself holds here) is simpler than a real key/value database and
// costs nothing at this scale.
std::map<std::string, std::string> readAll() {
  std::map<std::string, std::string> kv;
  FILE* f = std::fopen(filePath().c_str(), "r");
  if (!f) return kv;
  char line[128];
  while (std::fgets(line, sizeof(line), f)) {
    std::string s(line);
    const size_t eq = s.find('=');
    if (eq == std::string::npos) continue;
    std::string key = s.substr(0, eq);
    std::string val = s.substr(eq + 1);
    while (!val.empty() && (val.back() == '\n' || val.back() == '\r')) val.pop_back();
    kv[key] = val;
  }
  std::fclose(f);
  return kv;
}

void writeAll(const std::map<std::string, std::string>& kv) {
  jh_host::ensure_dir();
  FILE* f = std::fopen(filePath().c_str(), "w");
  if (!f) return;
  for (const auto& entry : kv) {
    std::fprintf(f, "%s=%s\n", entry.first.c_str(), entry.second.c_str());
  }
  std::fclose(f);
}

}  // namespace

void init() { jh_host::ensure_dir(); }

namespace {
// File key names. "offset"/"scale" unchanged so an existing host cal file
// keeps loading; "vbat" is simply absent there and falls back to 1.0.
const char* keyName(Key k) {
  switch (k) {
    case Key::AirtimeOffsetS: return "offset";
    case Key::HeightScale:    return "scale";
    case Key::VbatScale:      return "vbat";
    case Key::ProbeGuard:     return "probe_guard";
    case Key::StoreGuard:     return "store_guard";
  }
  return "offset";
}
}  // namespace

float load(Key k, float def, bool* from_store) {
  const auto kv = readAll();
  const auto it = kv.find(keyName(k));
  const bool have = it != kv.end();
  if (from_store) *from_store = have;
  return have ? std::strtof(it->second.c_str(), nullptr) : def;
}

void save(Key k, float value) {
  auto kv = readAll();
  char buf[32];
  std::snprintf(buf, sizeof(buf), "%.6f", (double)value);
  kv[keyName(k)] = buf;
  writeAll(kv);
}

void clear(Key k) {
  auto kv = readAll();
  kv.erase(keyName(k));
  writeAll(kv);
}

}  // namespace jh_persist
