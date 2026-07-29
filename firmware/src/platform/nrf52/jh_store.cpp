// jh_store.cpp — nRF52 (Seeed XIAO nRF52840 Sense) implementation of the
// jh_store seam (firmware/include/platform/jh_store.h). See docs/sense.md
// §3.2/§3.6/§3.9 and this platform's binding handoff notes.
//
// RAW region management on the 2 MB QSPI chip (P25Q16H) — NO filesystem.
// Only jh_store.h's WIRE format is contractual (its own header comment:
// "on-disk representation is fully private"); everything below is this
// platform's private choice.
//
// Layout (byte offsets, computed from the detected flash's real size —
// s_flash.size() — not hardcoded, though on this board it will always be
// P25Q16H's 2 MiB / 2,097,152 bytes):
//   sector 0            superblock: magic + format version + region map + crc
//   64 KB                jumps region: fixed-size 32-byte binary records
//   remainder (~1.93 MB) trace region: binary trace v2 blocks (trace_codec.h),
//                        append-only
//
// Binary trace v2: this file owns the ENCODE side (parsing the incoming
// "t,mag\n" CSV text main.cpp already decimated and formatted, feeding it
// through trace_codec::Encoder, writing finished blocks to the trace
// region) and the DECODE side (read_chunk() streams stored blocks back out
// as that exact same CSV text — see trace_codec.h's decode_to_csv() and its
// "byte-identical" acceptance bar). trace_codec.h is the dependency-free,
// host-testable format definition (firmware/include/trace_codec.h,
// firmware/test/trace_codec_harness.cpp, tools/tests/test_trace_codec.py);
// this file is its only real (device-side) user.
//
// trace_bytes() contract (see the one sanctioned comment added to
// firmware/include/platform/jh_store.h): CSV-equivalent stream size
// ESTIMATE, not raw stored bytes. We track this EXACTLY (not merely
// estimated) by counting the length of the INCOMING CSV text at
// trace_append() time — identical to the ESP32 implementation's own
// `s_trace_bytes += (uint32_t)len` — which is what the decoded dump will
// reproduce byte-for-byte under normal operation (milli-g quantization
// only ever changes the last decimal digit, never the digit COUNT, so the
// formatted length is unaffected).
//
// DEVIATION from a naive "just copy the ESP32 semantics" reading, stated
// plainly (see the final report this port's commit references): the
// full-ness CAP is NOT config/params.json's JH_TRACE_MAX_BYTES. That
// constant (2,000,000) was sized for the ESP32's own ~2.4 MB CSV-on-flash
// partition, where 1 CSV byte ~= 1 stored byte. Binary trace v2 exists
// *because* it stores the same session in ~1/7th the bytes (docs/ota.md
// §4.5) — gating fullness on the SAME small CSV-equivalent constant would
// silently throw away 6 of the 7 hours docs/sense.md §3.2 promises. Instead,
// trace_is_full() here means "the trace REGION's actual physical bytes are
// exhausted" — a platform-appropriate reinterpretation of the same
// contract (jh_store.h: "as long as the append/scan/read/clear semantics
// below hold", not "as long as the same numeric cap applies").
//
// Power-loss stance (docs/sense.md §3.6): append-only, and a block/record
// is either whole-and-CRC-valid or it is treated as the end of valid data —
// see trace_codec.h's decode_one_block() and this file's own JumpRecord
// validity check. QSPI deep power-down (runCommand 0xB9 enter / 0xAB
// release — Adafruit_FlashTransport_QSPI exposes exactly this raw command
// path, confirmed to compile; see flashWake()/flashSleep() below) brackets
// every flash-touching seam call, so the chip sleeps between write/read
// bursts rather than idling awake — VERIFY the actual current draw and the
// wake-recovery delay on the bench (firmware/SENSE_FIRST_BOOT.md).
//
// SPDX-License-Identifier: MIT

#include "platform/jh_store.h"

#include <math.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <Adafruit_FlashTransport.h>
#include <Adafruit_SPIFlashBase.h>
#include <Arduino.h>

#include "params.gen.h"
#include "trace_codec.h"

namespace jh_store {

namespace {

// ---------------------------------------------------------------- geometry
const uint32_t SECTOR_BYTES       = 4096;
const uint32_t SUPERBLOCK_BYTES   = SECTOR_BYTES;  // sector 0
const uint32_t JUMPS_REGION_BYTES = 65536;         // 16 sectors, 2048 records
const uint32_t JUMP_RECORD_BYTES  = 32;
const uint32_t PAGE_BYTES         = 256;  // matches Adafruit_FlashTransport.h's
                                          // SFLASH_PAGE_SIZE — the physical QSPI
                                          // program-page size Adafruit_SPIFlashBase::
                                          // writeBuffer() itself chunks writes at
                                          // (see its own source: "write one page at
                                          // a time and must not go over page
                                          // boundary"). Used only by the torn-write
                                          // recovery below — see skipPastTornWrite().

const char* TRACE_HEADER = "t,mag\n";
const char* JUMPS_HEADER = "n,takeoff_s,airtime_raw_s,airtime_s,height_m\n";

// ------------------------------------------------------------- superblock
#pragma pack(push, 1)
struct Superblock {
  uint32_t magic;              // 'JHS2' — see kSuperblockMagic
  uint8_t  format_version;
  uint8_t  _reserved[3];
  uint32_t jumps_region_start;  // region map (design decision #3) — recorded
  uint32_t jumps_region_bytes;  // on-disk so a future firmware version can
  uint32_t trace_region_start;  // detect an OLDER layout instead of
  uint32_t trace_region_bytes;  // misreading it
  uint8_t  crc;                 // trace_codec::crc8 over every byte above
};
#pragma pack(pop)

const uint32_t kSuperblockMagic = 0x3253484AUL;  // "JHS2" little-endian
const uint8_t  kSuperblockVersion = 1;

// ------------------------------------------------------------- jump record
#pragma pack(push, 1)
struct JumpRecord {
  uint32_t n;
  float    takeoff_s;
  float    airtime_raw_s;
  float    airtime_s;
  float    height_m;
  uint8_t  crc;       // trace_codec::crc8 over the 20 bytes above
  uint8_t  _pad[11];  // -> 32 bytes total (JUMP_RECORD_BYTES)
};
#pragma pack(pop)

// ------------------------------------------------------------------ state
Adafruit_FlashTransport_QSPI s_transport;  // default ctor: board's own
                                           // PIN_QSPI_* (variant.h) — no
                                           // pins wired here on purpose.
Adafruit_SPIFlashBase s_flash(&s_transport);

bool     s_fs_ok             = false;
uint32_t s_flash_total_bytes = 0;
uint32_t s_jumps_region_start = 0;
uint32_t s_trace_region_start = 0;
uint32_t s_trace_region_bytes = 0;

uint32_t s_jumps_append_off = 0;  // bytes, relative to jumps region start
uint32_t s_jumps_count      = 0;
float    s_jumps_best_m     = 0.0f;

uint32_t s_trace_append_off  = 0;  // bytes, relative to trace region start
uint32_t s_trace_csv_bytes   = 0;  // see trace_bytes() contract, top comment
bool     s_trace_csv_header_counted = false;
bool     s_trace_full        = false;

// In-progress (not yet written to flash) trace block. See trace_append():
// closed and flushed to the trace region whenever the nominal second
// changes, the block hits trace_codec::MAX_SAMPLES_PER_BLOCK, or a
// `dump`/`trace`/`stats` read forces it out early (open_read()) so a client
// never misses the tail of the last active burst.
trace_codec::Encoder s_trace_enc;
bool s_trace_block_open = false;
long s_trace_block_sec  = 0;

// ---------------------------------------------------------- QSPI power
// Deep power-down between bursts (docs/sense.md §3.6). Bracket every public
// function below that actually touches the bus; free_bytes()/jumps_scan()
// don't (see their own comments) because they only ever read cached RAM
// state, never the flash itself, once boot has scanned it once.
void flashWake() {
  s_transport.runCommand(0xAB);  // Release from Deep Power-Down
  delayMicroseconds(100);        // recovery margin — VERIFY exact minimum
                                 // (firmware/SENSE_FIRST_BOOT.md); harmless
                                 // (and, per common SPI-NOR practice,
                                 // idempotent) to call even if the chip was
                                 // never put to sleep, e.g. on first boot.
}
void flashSleep() {
  s_transport.runCommand(0xB9);  // Deep Power-Down
}

// ------------------------------------------------------- record/CSV helpers
uint32_t csvLineLen(float t_s, float mag_g) {
  char buf[32];
  int n = snprintf(buf, sizeof(buf), "%.3f,%.3f\n", (double)t_s, (double)mag_g);
  return n > 0 ? (uint32_t)n : 0;
}

// True if `rec` (already read from flash) is a whole, uncorrupted, not-erased
// record. Mirrors trace_codec::decode_one_block()'s reasoning exactly (CRC
// check + an explicit erased-flash guard rather than relying solely on the
// astronomically-unlikely chance an all-0xFF run's CRC-8 collides with a
// stray 0xFF check byte).
bool jumpRecordValid(const JumpRecord& rec) {
  if (rec.n == 0xFFFFFFFFu) return false;  // erased flash
  const uint8_t want = trace_codec::crc8((const uint8_t*)&rec, offsetof(JumpRecord, crc));
  return want == rec.crc;
}

// ---------------------------------------------------- torn-write recovery
// True iff every byte of `buf` is the flash-erased fill value (0xFF) — i.e.
// genuinely blank, never-written flash, as opposed to bytes a write landed
// on (whether that write completed, was torn by a power cut partway
// through, or the result was corrupted afterward).
bool isErasedBytes(const uint8_t* buf, size_t len) {
  for (size_t i = 0; i < len; ++i) {
    if (buf[i] != 0xFF) return false;
  }
  return true;
}

// Called by both boot-time scans when they reach a record/block that fails
// validation. If the bytes sitting there are simply erased (0xFF), that's
// the ordinary, expected end-of-valid-data case — `off` is already a safe
// place to resume appending and is returned unchanged. If they are NOT
// erased, something was written there (a torn power-cut write or later
// corruption) — NOR flash writes can only ever CLEAR bits, never set one
// back to 1 without an erase, so resuming a fresh append exactly on top of
// those leftover bytes would AND-merge the new data against them and can
// silently and PERMANENTLY wedge that slot: every future append landing on
// the same not-fully-erased bytes has a real chance of getting corrupted in
// turn, failing its own CRC check in exactly the same way, so the append
// offset never advances past it again without a clear(). Proved by
// firmware/test/store_host's
// test_power_cut_recovers_next_append_lands_cleanly_and_is_readable (named
// for the fix below — it originally proved and was named after the bug,
// see tools/tests/test_store_host.py's module docstring for that history).
//
// The fix: skip `off` forward to the first page boundary beyond anything a
// write starting at `off` could possibly have touched — `worst_case_bytes`
// is the largest that write could legitimately have been (JUMP_RECORD_BYTES
// for a jump record, which never straddles a page; the largest possible
// trace block for the trace region, which can straddle several). That
// bound holds regardless of what the (possibly torn/corrupt) bytes
// themselves claim, so this is safe even when the tear lands inside the
// header fields those claims would otherwise come from. Clamped to
// `region_bytes` so a torn write near the very end of a region can't skip
// past it.
uint32_t skipPastTornWrite(uint32_t off, uint32_t worst_case_bytes, uint32_t region_bytes) {
  const uint32_t next_page =
      ((off + worst_case_bytes + PAGE_BYTES - 1) / PAGE_BYTES) * PAGE_BYTES;
  return (next_page < region_bytes) ? next_page : region_bytes;
}

// Scans the jumps region at boot: finds the true append offset AND the live
// count/best height in one linear pass.
//
// Design decision #3 suggests "binary search on sectors then linear in the
// last sector" as an acceptable boot-scan strategy, and that would speed up
// finding the append OFFSET alone — but jumps_scan()'s count/best_m (and,
// for the trace region below, trace_bytes()'s CSV-equivalent estimate) are
// AGGREGATES over every record/block ever written, not just "where does the
// valid data end". A binary search only tells you the boundary; it cannot
// tell you how many jumps or how many CSV bytes came before it without
// separately caching per-sector summaries we don't keep. So a full linear
// walk is required here regardless of how the append point is found, and a
// bespoke binary-search-to-narrow-the-start step would only add complexity
// (and a real bug surface) without actually skipping any work. The jumps
// region is small (2048 records, 64 KB) either way — this is a non-issue
// here. See findTraceAppendPoint() below for where the SAME reasoning
// applies to a much larger region, and firmware/SENSE_FIRST_BOOT.md for the
// resulting boot-time VERIFY item.
void findJumpsAppendPoint() {
  uint32_t off   = 0;
  uint32_t count = 0;
  float    best  = 0.0f;
  // A while-loop over byte offset, not a for-loop over record index: torn-
  // write recovery (below) can jump `off` forward by more than one record's
  // width, so a fixed per-iteration step no longer fits.
  while (off + JUMP_RECORD_BYTES <= JUMPS_REGION_BYTES) {
    JumpRecord rec;
    s_flash.readBuffer(s_jumps_region_start + off, (uint8_t*)&rec, sizeof(rec));
    if (!jumpRecordValid(rec)) {
      if (isErasedBytes((const uint8_t*)&rec, sizeof(rec))) break;  // genuine end of data
      // Torn-write recovery (see skipPastTornWrite()'s comment): a jump
      // record never straddles a page (JUMP_RECORD_BYTES divides PAGE_BYTES
      // evenly), so a write landing anywhere in it can only ever have
      // touched the one page `off` falls in. Skip past it and KEEP
      // scanning — a prior boot may already have resumed appending beyond
      // an earlier torn record (possibly more than once), so there can be
      // more genuinely valid data past this damaged patch; produceNextUnit()
      // below applies the identical skip on the read-back side so a dump
      // and this count never disagree. Proved by firmware/test/store_host's
      // test_power_cut_recovers_next_append_lands_cleanly_and_is_readable
      // and test_multiple_torn_writes_all_recover_and_stay_readable
      // (tools/tests/test_store_host.py).
      off = skipPastTornWrite(off, JUMP_RECORD_BYTES, JUMPS_REGION_BYTES);
      continue;
    }
    count++;
    if (rec.height_m > best) best = rec.height_m;
    off += JUMP_RECORD_BYTES;
  }
  s_jumps_append_off = off;
  s_jumps_count      = count;
  s_jumps_best_m     = best;
}

// Same idea for the trace region: also reconstructs the CSV-equivalent byte
// count (s_trace_csv_bytes) by decoding every recovered block's samples,
// since across a reboot we no longer have the original incoming text —
// only the compact binary form — to measure directly.
void csvByteCounter(void* ctx, float t_s, float mag_g) {
  uint32_t* total = (uint32_t*)ctx;
  *total += csvLineLen(t_s, mag_g);
}

// Largest a single trace block can ever legitimately be (header + the max
// sample count + crc) — unlike a jump record, a trace block CAN straddle
// several 256B pages, so torn-write recovery below has to skip past this
// much, not just one page (see skipPastTornWrite()).
const uint32_t kMaxTraceBlockBytes = trace_codec::block_size(trace_codec::MAX_SAMPLES_PER_BLOCK);

// Called at every point findTraceAppendPoint() finds a record that fails
// validation. If the bytes read there are simply erased (0xFF), that's the
// ordinary, expected end-of-valid-data case — returns false (nothing to
// skip; the caller should stop scanning). Otherwise something was written
// there and never validated (a torn power-cut write or later corruption) —
// advances `*off` past every page a block starting there could possibly
// have reached (see skipPastTornWrite()) and returns true, telling the
// caller to skip forward and KEEP scanning: a prior boot may already have
// resumed appending beyond an earlier torn block, so there can be more
// genuinely valid data past this one damaged patch. produceNextUnit()
// applies the identical skip on the read-back side so a dump and this
// scan's counts never disagree with each other. Proved by
// firmware/test/store_host's test_power_cut_trace_block_recovers_and_is_
// readable (tools/tests/test_store_host.py) — the trace-region sibling of
// findJumpsAppendPoint()'s identical jumps-region recovery above.
bool traceScanHitDamage(uint32_t* off, const uint8_t* buf, size_t len) {
  if (isErasedBytes(buf, len)) return false;
  *off = skipPastTornWrite(*off, kMaxTraceBlockBytes, s_trace_region_bytes);
  return true;
}

// Same reasoning as findJumpsAppendPoint() above (a linear pass is required
// regardless, since trace_bytes()'s CSV-equivalent estimate is an aggregate
// over every block, not just a boundary) — except this region can be up to
// ~1.93 MB, so a fully-used trace region means walking on the order of tens
// of thousands of blocks at boot. VERIFY actual boot time at realistic fill
// levels on the bench (firmware/SENSE_FIRST_BOOT.md); if it proves too
// slow, the fix is to persist a small periodic checkpoint (append offset +
// running CSV byte count, e.g. rewritten into the superblock every N
// blocks) so a reboot only has to replay the tail since the last
// checkpoint — deliberately not built here, to keep the on-disk format for
// this first compile-clean pass as simple as possible.
void findTraceAppendPoint() {
  uint32_t off = 0;
  uint32_t csv_total = 0;
  uint8_t block_buf[trace_codec::HEADER_BYTES];
  while (off + trace_codec::HEADER_BYTES <= s_trace_region_bytes) {
    s_flash.readBuffer(s_trace_region_start + off, block_buf, sizeof(block_buf));
    const uint32_t t0_ms = (uint32_t)block_buf[0] | ((uint32_t)block_buf[1] << 8) |
                          ((uint32_t)block_buf[2] << 16) | ((uint32_t)block_buf[3] << 24);
    const uint32_t count = block_buf[4];
    if (t0_ms == 0xFFFFFFFFu || count == 0 || count > trace_codec::MAX_SAMPLES_PER_BLOCK) {
      if (traceScanHitDamage(&off, block_buf, sizeof(block_buf))) continue;
      break;
    }

    const uint32_t need = trace_codec::block_size(count);
    if (off + need > s_trace_region_bytes) {
      if (traceScanHitDamage(&off, block_buf, sizeof(block_buf))) continue;
      break;
    }

    uint8_t full[trace_codec::block_size(trace_codec::MAX_SAMPLES_PER_BLOCK)];
    s_flash.readBuffer(s_trace_region_start + off, full, need);
    const trace_codec::BlockResult r =
        trace_codec::decode_one_block(full, need, JH_LOG_HZ, csvByteCounter, &csv_total);
    if (!r.ok) {
      if (traceScanHitDamage(&off, full, need)) continue;
      break;
    }
    off += r.bytes_consumed;
  }
  s_trace_append_off = off;
  s_trace_csv_bytes  = off > 0 ? csv_total + 6 /* "t,mag\n" header */ : 0;
  s_trace_csv_header_counted = off > 0;
  // Full means "no further block can ever fit" — not merely "not one more
  // byte remains." A block that arrives when less than kMaxTraceBlockBytes
  // remains is silently dropped by closeAndWriteBlock() without s_trace_full
  // ever crossing the >= region_bytes threshold on its own, so trace_is_full()
  // could read false forever after the region is, in every practical sense,
  // done accepting data — main.cpp's "# trace log full" narration would then
  // never fire and trace_bytes()'s estimate would keep climbing past what's
  // truly stored. Proved by firmware/test/store_host's
  // test_trace_is_full_flag_trips_before_the_tail_runs_out (named for the
  // fix below — it originally proved and was named after the bug, see
  // tools/tests/test_store_host.py's module docstring for that history).
  // Mirrored in closeAndWriteBlock()'s own newly_full check below.
  s_trace_full = (off >= s_trace_region_bytes) ||
                 (s_trace_region_bytes - off < kMaxTraceBlockBytes);
}

bool writeSuperblock() {
  Superblock sb;
  memset(&sb, 0, sizeof(sb));
  sb.magic              = kSuperblockMagic;
  sb.format_version      = kSuperblockVersion;
  sb.jumps_region_start  = s_jumps_region_start;
  sb.jumps_region_bytes  = JUMPS_REGION_BYTES;
  sb.trace_region_start  = s_trace_region_start;
  sb.trace_region_bytes  = s_trace_region_bytes;
  sb.crc = trace_codec::crc8((const uint8_t*)&sb, offsetof(Superblock, crc));
  return s_flash.writeBuffer(0, (const uint8_t*)&sb, sizeof(sb)) == sizeof(sb);
}

bool superblockValid() {
  Superblock sb;
  s_flash.readBuffer(0, (uint8_t*)&sb, sizeof(sb));
  if (sb.magic != kSuperblockMagic || sb.format_version != kSuperblockVersion) return false;
  if (trace_codec::crc8((const uint8_t*)&sb, offsetof(Superblock, crc)) != sb.crc) return false;
  // A stale/foreign superblock from a build with a different region split
  // would corrupt our own scanning below if we trusted it blindly — the
  // magic+version+crc triple above is what we rely on instead of also
  // cross-checking the recorded region map against our compiled-in
  // constants; a future firmware that changes the split bumps
  // kSuperblockVersion, which naturally fails this check and re-formats.
  return true;
}

// ------------------------------------------------------- read-back (dump)
StoredFile s_read_which        = StoredFile::JUMPS;
bool       s_read_open         = false;
bool       s_read_header_sent  = false;
uint32_t   s_read_src_cursor   = 0;  // bytes, relative to the region start
uint32_t   s_read_src_used     = 0;  // total valid bytes in that region

// Worst case one trace block's decoded CSV text: 255 samples * up to ~20
// bytes/line ("%.3f,%.3f\n" comfortably fits under 20 for our value ranges).
char   s_read_pending[6144];
size_t s_read_pending_len = 0;
size_t s_read_pending_pos = 0;

void appendCsvLineToPending(void* ctx, float t_s, float mag_g) {
  (void)ctx;
  if (s_read_pending_len >= sizeof(s_read_pending)) return;  // defensive; never hit in practice
  const size_t room = sizeof(s_read_pending) - s_read_pending_len;
  const int n = snprintf(s_read_pending + s_read_pending_len, room, "%.3f,%.3f\n",
                        (double)t_s, (double)mag_g);
  if (n > 0) s_read_pending_len += (size_t)((size_t)n < room ? n : room - 1);
}

// Decodes exactly the NEXT unit (one jump record, or one trace block) into
// s_read_pending, advancing s_read_src_cursor. Leaves s_read_src_cursor ==
// s_read_src_used with nothing produced once every valid unit has been
// delivered (EOF) — including stopping early on a corrupt/partial trailing
// one, matching the power-loss stance (nothing past it is ever surfaced to
// a client).
//
// A torn-write recovery boundary (see skipPastTornWrite()'s comment and
// findJumpsAppendPoint()/findTraceAppendPoint() above, which already
// resumed appending past exactly this kind of damage) is NOT such a
// trailing case: this loops past it internally rather than declaring EOF,
// so a dump surfaces every genuinely valid unit on both sides of a damaged
// patch — the same recovered data findJumpsAppendPoint()/
// findTraceAppendPoint() already counted. Without this, a client could get
// a `count` from STATS that disagrees with what an actual `dump` shows,
// which defeats the point of the fix. Proved by firmware/test/store_host's
// test_power_cut_recovers_next_append_lands_cleanly_and_is_readable and its
// siblings (tools/tests/test_store_host.py).
void produceNextUnit() {
  while (s_read_src_cursor < s_read_src_used) {
    if (s_read_which == StoredFile::JUMPS) {
      JumpRecord rec;
      s_flash.readBuffer(s_jumps_region_start + s_read_src_cursor, (uint8_t*)&rec, sizeof(rec));
      if (!jumpRecordValid(rec)) {
        if (isErasedBytes((const uint8_t*)&rec, sizeof(rec))) { s_read_src_cursor = s_read_src_used; return; }
        s_read_src_cursor = skipPastTornWrite(s_read_src_cursor, JUMP_RECORD_BYTES, s_read_src_used);
        continue;
      }
      const int n = snprintf(s_read_pending, sizeof(s_read_pending), "%lu,%.3f,%.3f,%.3f,%.3f\n",
                            (unsigned long)rec.n, (double)rec.takeoff_s, (double)rec.airtime_raw_s,
                            (double)rec.airtime_s, (double)rec.height_m);
      s_read_pending_len = n > 0 ? (size_t)n : 0;
      s_read_src_cursor += JUMP_RECORD_BYTES;
      return;
    } else {
      uint8_t hdr[trace_codec::HEADER_BYTES];
      s_flash.readBuffer(s_trace_region_start + s_read_src_cursor, hdr, sizeof(hdr));
      const uint32_t count = hdr[4];
      if (count == 0 || count > trace_codec::MAX_SAMPLES_PER_BLOCK) {
        if (isErasedBytes(hdr, sizeof(hdr))) { s_read_src_cursor = s_read_src_used; return; }
        s_read_src_cursor = skipPastTornWrite(s_read_src_cursor, kMaxTraceBlockBytes, s_read_src_used);
        continue;
      }
      const uint32_t need = trace_codec::block_size(count);
      if (s_read_src_cursor + need > s_read_src_used) {
        if (isErasedBytes(hdr, sizeof(hdr))) { s_read_src_cursor = s_read_src_used; return; }
        s_read_src_cursor = skipPastTornWrite(s_read_src_cursor, kMaxTraceBlockBytes, s_read_src_used);
        continue;
      }

      uint8_t full[trace_codec::block_size(trace_codec::MAX_SAMPLES_PER_BLOCK)];
      s_flash.readBuffer(s_trace_region_start + s_read_src_cursor, full, need);
      s_read_pending_len = 0;
      const trace_codec::BlockResult r =
          trace_codec::decode_one_block(full, need, JH_LOG_HZ, appendCsvLineToPending, nullptr);
      if (!r.ok) {
        if (isErasedBytes(full, need)) { s_read_src_cursor = s_read_src_used; return; }
        s_read_src_cursor = skipPastTornWrite(s_read_src_cursor, kMaxTraceBlockBytes, s_read_src_used);
        continue;
      }
      s_read_src_cursor += r.bytes_consumed;
      return;
    }
  }
}

}  // namespace

// ------------------------------------------------------------------- init
bool init(void (*announce)(const char* line)) {
  flashWake();

  static SPIFlash_Device_t s_devices[] = {P25Q16H};
  s_fs_ok = s_flash.begin(s_devices, 1);
  if (!s_fs_ok) return false;

  s_flash_total_bytes  = s_flash.size();  // P25Q16H: 2,097,152 (2 MiB)
  s_jumps_region_start = SUPERBLOCK_BYTES;
  s_trace_region_start = s_jumps_region_start + JUMPS_REGION_BYTES;
  s_trace_region_bytes = (s_flash_total_bytes > s_trace_region_start)
                             ? s_flash_total_bytes - s_trace_region_start
                             : 0;

  if (!superblockValid()) {
    announce("# first boot: formatting storage — takes up to a minute, hang tight...");
    const bool erased = s_flash.eraseChip();
    const bool wrote   = erased && writeSuperblock();
    announce(wrote ? "# storage ready" : "# storage format failed");
    if (!wrote) { s_fs_ok = false; flashSleep(); return false; }
  }

  findJumpsAppendPoint();
  findTraceAppendPoint();

  flashSleep();
  return s_fs_ok;
}

bool ok() { return s_fs_ok; }

uint32_t free_bytes() {
  // Cached RAM state only (see the state block's comment) — no flash
  // access, so no wake/sleep bracketing needed here.
  const uint32_t jumps_free = JUMPS_REGION_BYTES - s_jumps_append_off;
  const uint32_t trace_free = s_trace_region_bytes - s_trace_append_off;
  return jumps_free + trace_free;
}

// --------------------------------------------------------------- jumps.csv
void jumps_append(uint32_t n, float takeoff_s, float airtime_raw_s,
                  float airtime_s, float height_m) {
  if (!s_fs_ok) return;
  if (s_jumps_append_off + JUMP_RECORD_BYTES > JUMPS_REGION_BYTES) return;  // region full

  flashWake();
  JumpRecord rec;
  memset(&rec, 0xFF, sizeof(rec));
  rec.n = n;
  rec.takeoff_s = takeoff_s;
  rec.airtime_raw_s = airtime_raw_s;
  rec.airtime_s = airtime_s;
  rec.height_m = height_m;
  rec.crc = trace_codec::crc8((const uint8_t*)&rec, offsetof(JumpRecord, crc));
  s_flash.writeBuffer(s_jumps_region_start + s_jumps_append_off, (const uint8_t*)&rec, sizeof(rec));
  flashSleep();

  s_jumps_append_off += JUMP_RECORD_BYTES;
  s_jumps_count++;
  if (height_m > s_jumps_best_m) s_jumps_best_m = height_m;
}

void jumps_scan(uint32_t& count, float& best_m) {
  // Cached RAM state, kept correct incrementally by jumps_append() and
  // established at boot by findJumpsAppendPoint() — no re-scan needed.
  count  = s_fs_ok ? s_jumps_count  : 0;
  best_m = s_fs_ok ? s_jumps_best_m : 0.0f;
}

// --------------------------------------------------------------- trace.csv
namespace {

// Closes the in-progress block (if any samples were added to it) and
// writes it to the trace region, advancing the append offset and checking
// the physical-capacity cap. Returns true exactly on the write that newly
// crosses it. Caller holds flash awake already (trace_append()/open_read()
// bracket this).
bool closeAndWriteBlock() {
  if (!s_trace_block_open || s_trace_enc.count() == 0) {
    s_trace_block_open = false;
    return false;
  }
  uint8_t buf[trace_codec::block_size(trace_codec::MAX_SAMPLES_PER_BLOCK)];
  const size_t n = s_trace_enc.finish(buf, sizeof(buf));
  s_trace_block_open = false;
  if (n == 0) return false;
  if (s_trace_append_off + n > s_trace_region_bytes) {
    // Physically out of room for THIS block: drop it rather than write past
    // the region (into the next chip's worth of nothing). trace_is_full()
    // should already have stopped main.cpp from calling us by this point
    // (it gates on trace_is_full() before ever building up trace_buf) —
    // this is a last-ditch guard, not the primary mechanism. Nothing is
    // written here, so s_trace_append_off doesn't move and this can only
    // ever get MORE true from here — latch fullness now with the same
    // "no further block could ever fit" rule as below, so a caller that
    // keeps calling anyway (or a `dump` forcing a flush) doesn't have to
    // wait for some later write attempt to notice.
    const bool newly_full =
        !s_trace_full && (s_trace_region_bytes - s_trace_append_off < kMaxTraceBlockBytes);
    if (newly_full) s_trace_full = true;
    return newly_full;
  }
  s_flash.writeBuffer(s_trace_region_start + s_trace_append_off, buf, n);
  s_trace_append_off += (uint32_t)n;

  // "Full" means no further block could ever fit, not merely "this exact
  // byte is the last one" — checking only `>= s_trace_region_bytes` lets
  // trace_is_full() under-report forever once the remaining tail drops
  // below one block's worth but isn't literally zero (a block that doesn't
  // fit is simply dropped by the guard above, which never by itself crosses
  // that threshold). Proved by firmware/test/store_host's
  // test_trace_is_full_flag_trips_before_the_tail_runs_out
  // (tools/tests/test_store_host.py). Mirrored in findTraceAppendPoint()'s
  // own boot-time fullness check.
  const bool newly_full =
      !s_trace_full && (s_trace_region_bytes - s_trace_append_off < kMaxTraceBlockBytes);
  if (newly_full) s_trace_full = true;
  return newly_full;
}

// Parses one "t,mag" CSV line (no header, no trailing content beyond what
// main.cpp's emit layer ever produces — see jump_detector's trace_buf
// construction in main.cpp) and feeds it into the block encoder, opening/
// closing blocks per the policy documented in trace_codec.h's file comment
// and mirrored exactly in firmware/test/trace_codec_harness.cpp: a new
// block starts whenever floor(t) changes (covers idle gaps AND reboot
// restarts identically — see that file) or the current block is full.
// Returns true if closing a block (to make room for this sample) newly
// crossed the fullness cap.
bool feedSample(const char* line, size_t len) {
  // Manual float parse (avoids pulling in String/std::string here): find
  // the comma, strtod both halves. Malformed lines are skipped, same as
  // main.cpp's own tolerance elsewhere (e.g. host_test.cpp's CSV reader).
  const char* comma = (const char*)memchr(line, ',', len);
  if (!comma) return false;
  char t_buf[16];
  char m_buf[16];
  size_t t_len = (size_t)(comma - line);
  size_t m_len = len - t_len - 1;
  if (t_len >= sizeof(t_buf) || m_len >= sizeof(m_buf)) return false;
  memcpy(t_buf, line, t_len); t_buf[t_len] = '\0';
  memcpy(m_buf, comma + 1, m_len); m_buf[m_len] = '\0';
  const float t_s   = (float)atof(t_buf);
  const float mag_g = (float)atof(m_buf);

  bool crossed = false;
  const long this_sec = (long)floorf(t_s);
  if (!s_trace_block_open || this_sec != s_trace_block_sec || s_trace_enc.full()) {
    crossed = closeAndWriteBlock() || crossed;
    if (!s_trace_full) {
      s_trace_enc.begin((uint32_t)lroundf(t_s * 1000.0f));
      s_trace_block_open = true;
      s_trace_block_sec  = this_sec;
    }
  }
  if (s_trace_block_open) s_trace_enc.add_sample(mag_g);
  return crossed;
}

}  // namespace

bool trace_append(const char* data, size_t len) {
  if (!s_fs_ok || len == 0) return false;
  if (s_trace_full) return false;  // main.cpp already gates on trace_is_full()
                                   // before calling us; this is belt+suspenders.

  if (!s_trace_csv_header_counted) {
    s_trace_csv_bytes += 6;  // "t,mag\n" — counted once, matching the ESP32
                             // implementation's own header accounting
                             // (src/platform/esp32/jh_store.cpp:108).
    s_trace_csv_header_counted = true;
  }
  s_trace_csv_bytes += (uint32_t)len;

  flashWake();
  bool crossed = false;
  size_t line_start = 0;
  for (size_t i = 0; i < len; ++i) {
    if (data[i] == '\n') {
      if (i > line_start) crossed = feedSample(data + line_start, i - line_start) || crossed;
      line_start = i + 1;
    }
  }
  flashSleep();
  return crossed;
}

uint32_t trace_bytes()   { return s_trace_csv_bytes; }
bool     trace_is_full() { return s_trace_full; }

// ---------------------------------------------------- framed raw read-back
bool open_read(StoredFile which) {
  if (!s_fs_ok) return false;

  flashWake();  // stays awake for the whole read session; close_read() sleeps
  if (which == StoredFile::TRACE) {
    // Force out any tail still sitting in the in-progress block so a
    // `dump`/`trace` right after the last active burst (device still
    // powered, just idle — no NEW sample has arrived to naturally trigger
    // a block close) doesn't miss it. See the file's class-level comment.
    closeAndWriteBlock();
  }

  s_read_which       = which;
  s_read_header_sent = false;
  s_read_pending_len = 0;
  s_read_pending_pos = 0;
  s_read_src_cursor  = 0;
  s_read_src_used    = (which == StoredFile::JUMPS) ? s_jumps_append_off : s_trace_append_off;
  s_read_open        = true;
  return true;
}

size_t read_chunk(uint8_t* buf, size_t max_len) {
  if (!s_read_open || max_len == 0) return 0;

  if (s_read_pending_pos >= s_read_pending_len) {
    s_read_pending_pos = 0;
    s_read_pending_len = 0;
    if (!s_read_header_sent) {
      const char* hdr = (s_read_which == StoredFile::JUMPS) ? JUMPS_HEADER : TRACE_HEADER;
      s_read_pending_len = strlen(hdr);
      memcpy(s_read_pending, hdr, s_read_pending_len);
      s_read_header_sent = true;
    } else if (s_read_src_cursor < s_read_src_used) {
      produceNextUnit();
    } else {
      return 0;  // EOF
    }
  }

  const size_t avail = s_read_pending_len - s_read_pending_pos;
  const size_t n = (avail < max_len) ? avail : max_len;
  memcpy(buf, s_read_pending + s_read_pending_pos, n);
  s_read_pending_pos += n;
  return n;
}

void close_read() {
  s_read_open = false;
  flashSleep();
}

// ------------------------------------------------------------- housekeeping
void clear() {
  if (!s_fs_ok) return;
  flashWake();

  s_flash.eraseSector(0);  // superblock

  const uint32_t jumps_sectors_used =
      (s_jumps_append_off + SECTOR_BYTES - 1) / SECTOR_BYTES;
  for (uint32_t i = 0; i < jumps_sectors_used; ++i) {
    s_flash.eraseSector((s_jumps_region_start / SECTOR_BYTES) + i);
  }

  const uint32_t trace_sectors_used =
      (s_trace_append_off + SECTOR_BYTES - 1) / SECTOR_BYTES;
  for (uint32_t i = 0; i < trace_sectors_used; ++i) {
    s_flash.eraseSector((s_trace_region_start / SECTOR_BYTES) + i);
  }

  writeSuperblock();

  s_jumps_append_off = 0;
  s_jumps_count      = 0;
  s_jumps_best_m     = 0.0f;
  s_trace_append_off = 0;
  s_trace_csv_bytes  = 0;
  s_trace_csv_header_counted = false;
  s_trace_full       = false;
  s_trace_block_open = false;

  flashSleep();
}

}  // namespace jh_store
