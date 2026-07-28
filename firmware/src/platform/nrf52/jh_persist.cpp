// jh_persist.cpp — nRF52 (Seeed XIAO nRF52840 Sense) implementation of the
// jh_persist seam (firmware/include/platform/jh_persist.h). See
// docs/sense.md §3.8/§3.9, which names this exact approach: "a small file
// on internal-flash LittleFS (InternalFS in this core)".
//
// InternalFS (Adafruit_LittleFS on the nRF52840's own internal flash,
// bundled with the Adafruit-fork core — no extra lib_deps needed; see
// platformio.ini) replaces the ESP32's Preferences/NVS. Same two-value
// contract as the ESP32 implementation (src/platform/esp32/jh_persist.cpp):
// airtime_offset_s ("offset") and height_scale ("scale"), with a per-key
// device-vs-defaults distinction — here tracked with two `has_*` flags in
// one small fixed-format record file, rather than two separate NVS keys,
// because InternalFS gives us a plain byte-addressable file instead of a
// key/value store. Same survival story either way: lives through app
// updates and reflash/DFU, dies only with a full chip erase (or
// `InternalFS.format()`, which nothing here calls).
//
// One whole-record rewrite per save()/clear() call is deliberately simple:
// calibration is written a handful of times per device's life (an
// interactive `set` command or the phone/web calibration flow — see
// jh_persist.h), never from the sampling path, so the small extra flash
// wear of rewriting ~12 bytes instead of a smaller diff is irrelevant.
//
// File-class note: Adafruit_LittleFS already avoids the Arduino File-class
// shadowing the ESP32 handoff note warned about — its File type lives in
// `Adafruit_LittleFS_Namespace` specifically so it doesn't collide with
// other filesystem libraries' own File types
// (~/.platformio/packages/framework-arduinoadafruitnrf52-seeed/libraries/
// Adafruit_LittleFS/src/Adafruit_LittleFS_File.h:31-41). We still fully
// qualify it below rather than pull the namespace in wholesale, to keep
// that boundary explicit.
//
// SPDX-License-Identifier: MIT

#include "platform/jh_persist.h"

#include <string.h>

#include <Adafruit_LittleFS.h>
#include <InternalFileSystem.h>

namespace jh_persist {

namespace {

const char* kPath = "/jh_cal.bin";
// 'J','H','C','L' as a little-endian u32 — just a format sanity check, not
// a security boundary; a version byte lets us change the record shape
// later without misreading an old one as garbage.
const uint32_t kMagic       = 0x4C43484AUL;
const uint8_t  kFormatVer   = 1;

#pragma pack(push, 1)
struct CalRecord {
  uint32_t magic;
  uint8_t  format_version;
  uint8_t  has_offset;
  uint8_t  has_scale;
  uint8_t  _reserved;
  float    offset;
  float    scale;
};
#pragma pack(pop)

// Reads the record file. Returns false (rec left zeroed) if it's missing,
// short, or the magic/version don't match — load() below treats that
// identically to "nothing saved yet" for both keys, matching jh_persist.h's
// "load() always succeeds" contract.
bool readRecord(CalRecord& rec) {
  memset(&rec, 0, sizeof(rec));
  Adafruit_LittleFS_Namespace::File f =
      InternalFS.open(kPath, Adafruit_LittleFS_Namespace::FILE_O_READ);
  if (!f) return false;
  const int n = f.read(&rec, sizeof(rec));
  f.close();
  if (n != (int)sizeof(rec)) return false;
  if (rec.magic != kMagic || rec.format_version != kFormatVer) return false;
  return true;
}

void writeRecord(const CalRecord& rec) {
  // FILE_O_WRITE does NOT truncate: Adafruit_LittleFS opens it
  // read/write + create, then seeks to the CURRENT end of file
  // (~/.platformio/packages/framework-arduinoadafruitnrf52-seeed/libraries/
  // Adafruit_LittleFS/src/Adafruit_LittleFS_File.cpp:53-73) — i.e. it's an
  // APPEND mode, the same convention Arduino's own FILE_WRITE uses
  // elsewhere. Writing without first clearing the file would keep
  // appending stale copies of this small record forever (and readRecord()
  // always reads from offset 0, so it would never see the new one).
  // Removing first guarantees a clean, single-copy file every time; saves
  // happen a handful of times per device's life (an interactive `set` or
  // the phone calibration flow), so the extra erase/rewrite cost is moot.
  InternalFS.remove(kPath);
  Adafruit_LittleFS_Namespace::File f =
      InternalFS.open(kPath, Adafruit_LittleFS_Namespace::FILE_O_WRITE);
  if (!f) return;  // fire-and-forget, matching jh_persist.h's save()/clear()
  f.write((const uint8_t*)&rec, sizeof(rec));
  f.close();
}

}  // namespace

void init() {
  InternalFS.begin();  // self-formats on a failed first mount (first boot
                       // ever) — see InternalFileSystem::begin()'s own
                       // implementation; no announce callback in this
                       // seam's contract (jh_persist.h), unlike jh_store's.
}

bool load(float default_offset_s, float default_scale,
          float& out_offset_s, float& out_scale) {
  CalRecord rec;
  const bool have_file = readRecord(rec);
  const bool has_offset = have_file && rec.has_offset;
  const bool has_scale  = have_file && rec.has_scale;

  out_offset_s = has_offset ? rec.offset : default_offset_s;
  out_scale    = has_scale  ? rec.scale  : default_scale;
  return has_offset || has_scale;
}

void save(bool is_offset, float value) {
  CalRecord rec;
  if (!readRecord(rec)) {
    memset(&rec, 0, sizeof(rec));
    rec.magic          = kMagic;
    rec.format_version = kFormatVer;
  }
  if (is_offset) { rec.offset = value; rec.has_offset = 1; }
  else           { rec.scale  = value; rec.has_scale  = 1; }
  writeRecord(rec);
}

void clear(bool is_offset) {
  CalRecord rec;
  if (!readRecord(rec)) return;  // nothing saved: already "cleared"
  if (is_offset) rec.has_offset = 0;
  else           rec.has_scale  = 0;
  writeRecord(rec);
}

}  // namespace jh_persist
