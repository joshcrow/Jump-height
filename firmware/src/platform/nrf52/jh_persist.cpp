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

const char* kPath    = "/jh_cal.bin";
// Atomic-save scratch path (review-nrf52.md finding #2) — see writeRecord()
// below for why this exists: never removed on its own by readRecord()/
// load(), only ever written-to-then-renamed-away by writeRecord().
const char* kTmpPath = "/jh_cal.bin.tmp";
// 'J','H','C','L' as a little-endian u32 — just a format sanity check, not
// a security boundary; a version byte lets us change the record shape
// later without misreading an old one as garbage.
const uint32_t kMagic       = 0x4C43484AUL;
// v1 = offset+scale. v2 adds vbat_scale (the per-unit battery-divider
// correction). readRecord() below MIGRATES v1 files rather than discarding
// them: a shorter file is a v1 record, not a corrupt one, and silently
// dropping a hard-won drop calibration on a firmware update would be a
// nasty way to learn that.
const uint8_t  kFormatVer   = 3;
const uint8_t  kFormatVerV2 = 2;
const uint8_t  kFormatVerV1 = 1;

#pragma pack(push, 1)
struct CalRecordV1 {
  uint32_t magic;
  uint8_t  format_version;
  uint8_t  has_offset;
  uint8_t  has_scale;
  uint8_t  _reserved;
  float    offset;
  float    scale;
};
struct CalRecordV2 {
  uint32_t magic;
  uint8_t  format_version;
  uint8_t  has_offset;
  uint8_t  has_scale;
  uint8_t  has_vbat;
  float    offset;
  float    scale;
  float    vbat;
};
struct CalRecord {
  uint32_t magic;
  uint8_t  format_version;
  uint8_t  has_offset;
  uint8_t  has_scale;
  uint8_t  has_vbat;
  uint8_t  guard;      // v3: jh_imu probe crash guard (0/1); NOT calibration
  float    offset;
  float    scale;
  float    vbat;
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
  uint8_t buf[sizeof(CalRecord)];  // v3 is the largest record shape
  memset(buf, 0, sizeof(buf));
  const int n = f.read(buf, sizeof(buf));
  f.close();
  if (n < (int)sizeof(CalRecordV1)) return false;

  uint32_t magic; uint8_t ver;
  memcpy(&magic, buf, sizeof(magic));
  memcpy(&ver, buf + sizeof(magic), sizeof(ver));
  if (magic != kMagic) return false;

  if (ver == kFormatVer && n >= (int)sizeof(CalRecord)) {
    memcpy(&rec, buf, sizeof(rec));
    return true;
  }
  if (ver == kFormatVerV2 && n >= (int)sizeof(CalRecordV2)) {
    // MIGRATE v2 -> v3: same fields plus the guard byte, which never
    // existed — clear. Same never-discard rule as the v1 path below.
    CalRecordV2 old;
    memcpy(&old, buf, sizeof(old));
    rec.magic          = old.magic;
    rec.format_version = kFormatVer;
    rec.has_offset     = old.has_offset;
    rec.has_scale      = old.has_scale;
    rec.has_vbat       = old.has_vbat;
    rec.guard          = 0;
    rec.offset         = old.offset;
    rec.scale          = old.scale;
    rec.vbat           = old.vbat;
    return true;
  }
  if (ver == kFormatVerV1) {
    // MIGRATE, don't discard. A v1 file is a shorter valid record, not a
    // corrupt one; treating it as "nothing saved" would throw away a drop
    // calibration on a routine firmware update.
    CalRecordV1 old;
    memcpy(&old, buf, sizeof(old));
    rec.magic          = old.magic;
    rec.format_version = kFormatVer;
    rec.has_offset     = old.has_offset;
    rec.has_scale      = old.has_scale;
    rec.has_vbat       = 0;          // never existed in v1
    rec.guard          = 0;          // nor did the guard
    rec.offset         = old.offset;
    rec.scale          = old.scale;
    rec.vbat           = 1.0f;
    return true;
  }
  return false;
}

// Atomic save (review-nrf52.md finding #2): the OLD approach here was
// InternalFS.remove(kPath) followed by re-creating it fresh — a real window
// exists between the remove() actually landing and the new write finishing
// where kPath simply does not exist; a power cut anywhere in that window
// (or even mid-write, before f.close()) leaves BOTH calibration values
// silently reverted to compiled defaults on the next boot (readRecord()
// treats a missing/short file identically to "nothing saved yet" — see its
// own comment) — the exact per-key-safety property the ESP32 sibling's NVS
// gets for free was quietly lost in this port. FIX: never remove-then-
// recreate kPath itself. Write the new record to a throwaway temp path
// first, then replace kPath with ONE atomic InternalFS.rename() call —
// LittleFS's own rename is power-loss-safe by construction (it's a single
// metadata-level move/commit, the whole reason a log-structured filesystem
// like littlefs exists — see lfs_rename() in the installed
// Adafruit_LittleFS/src/littlefs/lfs.c) — so at every instant either the
// OLD kPath is still intact, or the NEW one is, never neither.
void writeRecord(const CalRecord& rec) {
  // Clear out any stale tmp file a PRIOR crash (a power cut between THIS
  // remove and the rename below, on some earlier save() attempt) might
  // have left behind — this is the tmp path only, never kPath itself, so it
  // carries none of the atomicity risk the old remove-then-recreate
  // approach had (readRecord()/load() never read kTmpPath).
  InternalFS.remove(kTmpPath);
  Adafruit_LittleFS_Namespace::File f =
      InternalFS.open(kTmpPath, Adafruit_LittleFS_Namespace::FILE_O_WRITE);
  if (!f) return;  // couldn't even start the write; kPath is untouched
  const size_t n = f.write((const uint8_t*)&rec, sizeof(rec));
  f.close();
  if (n != sizeof(rec)) { InternalFS.remove(kTmpPath); return; }  // short write: don't
                                                                  // let a partial tmp
                                                                  // file get promoted

  // The one moment kPath's identity actually changes — atomic per the
  // reasoning above, so readRecord() can never observe kPath missing or
  // half-written, even across a power cut landing exactly here.
  InternalFS.rename(kTmpPath, kPath);  // fire-and-forget, matching
                                       // jh_persist.h's save()/clear()
                                       // contract (no failure path to
                                       // report) — see this function's
                                       // own comment for what's actually
                                       // guaranteed either way.
}

}  // namespace

void init() {
  InternalFS.begin();  // self-formats on a failed first mount (first boot
                       // ever) — see InternalFileSystem::begin()'s own
                       // implementation; no announce callback in this
                       // seam's contract (jh_persist.h), unlike jh_store's.
}

float load(Key k, float def, bool* from_store) {
  CalRecord rec;
  const bool have = readRecord(rec);
  bool has = false; float val = def;
  if (have) {
    switch (k) {
      case Key::AirtimeOffsetS: has = rec.has_offset; val = rec.offset; break;
      case Key::HeightScale:    has = rec.has_scale;  val = rec.scale;  break;
      case Key::VbatScale:      has = rec.has_vbat;   val = rec.vbat;   break;
      case Key::ProbeGuard:     has = 1;              val = rec.guard;  break;
    }
  }
  if (from_store) *from_store = has;
  return has ? val : def;
}

void save(Key k, float value) {
  CalRecord rec;
  if (!readRecord(rec)) {
    memset(&rec, 0, sizeof(rec));
    rec.magic          = kMagic;
    rec.format_version = kFormatVer;
  }
  switch (k) {
    case Key::AirtimeOffsetS: rec.offset = value; rec.has_offset = 1; break;
    case Key::HeightScale:    rec.scale  = value; rec.has_scale  = 1; break;
    case Key::VbatScale:      rec.vbat   = value; rec.has_vbat   = 1; break;
    case Key::ProbeGuard:     rec.guard  = (value > 0.5f) ? 1 : 0;    break;
  }
  writeRecord(rec);
}

void clear(Key k) {
  CalRecord rec;
  if (!readRecord(rec)) return;  // nothing saved: already "cleared"
  switch (k) {
    case Key::AirtimeOffsetS: rec.has_offset = 0; break;
    case Key::HeightScale:    rec.has_scale  = 0; break;
    case Key::VbatScale:      rec.has_vbat   = 0; break;
    case Key::ProbeGuard:     rec.guard      = 0; break;
  }
  writeRecord(rec);
}

}  // namespace jh_persist
