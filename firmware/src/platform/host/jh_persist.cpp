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

bool load(float default_offset_s, float default_scale, float& out_offset_s,
          float& out_scale) {
  const auto kv = readAll();
  bool any = false;

  const auto off_it = kv.find("offset");
  if (off_it != kv.end()) {
    out_offset_s = std::strtof(off_it->second.c_str(), nullptr);
    any = true;
  } else {
    out_offset_s = default_offset_s;
  }

  const auto scale_it = kv.find("scale");
  if (scale_it != kv.end()) {
    out_scale = std::strtof(scale_it->second.c_str(), nullptr);
    any = true;
  } else {
    out_scale = default_scale;
  }

  return any;  // true if EITHER value came from the persisted file
}

void save(bool is_offset, float value) {
  auto kv = readAll();
  char buf[32];
  std::snprintf(buf, sizeof(buf), "%.6f", (double)value);
  kv[is_offset ? "offset" : "scale"] = buf;
  writeAll(kv);
}

void clear(bool is_offset) {
  auto kv = readAll();
  kv.erase(is_offset ? "offset" : "scale");
  writeAll(kv);
}

}  // namespace jh_persist
