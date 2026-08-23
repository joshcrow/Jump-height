// jh_store.h — storage platform seam (see docs/sense.md §3.9).
//
// Everything main.cpp touches about the two on-device CSVs (jumps.csv,
// trace.csv): mount/format-on-fail, append, framed raw read-back (for the
// `jumps`/`trace`/`dump` commands), the trace byte-count + size-cap
// bookkeeping STATS reports, and clear. main.cpp still owns ITS OWN buffering
// (trace_buf — keeps slow flash writes off the sampling path) and the
// fs_ok gate; this seam only owns the actual file I/O.
//
// Units/format: jumps.csv rows are
// "n,takeoff_s,airtime_raw_s,airtime_s,height_m,med_a_g,med_w_dps,
// med_acorr_g,n_air\n" — NINE columns since the flight medians were added;
// this comment claimed five until F-20 (audit 2026-08-22), and a host-side
// reader that trusted it parsed n_air as the jump height. New columns are
// APPENDED, never inserted, so field indices are stable; read by index, never
// by counting back from the end. trace.csv rows are
// "t,mag\n" — both exactly the wire format the FILE-framed dump already
// sends, so a client never has to know the on-device format changed
// (docs/sense.md §3.2 plans an eventual binary trace with this same CSV
// wire contract preserved).
// Error returns: append/scan/read functions are no-ops (false/0/empty) when
// storage isn't mounted — callers may still gate on ok()/init()'s return
// first, matching current main.cpp behavior.
// Blocking: append/scan/read do a real filesystem write or read and may take
// milliseconds — same as today's direct LittleFS calls. They are only ever
// called from setup()/command handling/the once-a-second trace flush, never
// from a context where that would stall live sampling.
//
// ESP32: LittleFS on internal flash (src/platform/esp32/jh_store.cpp). A
// future platform may back this with different storage entirely (e.g. the
// Sense's external QSPI flash with a binary on-disk format, per
// docs/sense.md §3.2/§3.6) as long as the append/scan/read/clear semantics
// below hold and the CSV wire format above is what a framed dump sends.
//
// SPDX-License-Identifier: MIT

#pragma once

#include <stddef.h>
#include <stdint.h>

namespace jh_store {

// The two files this seam manages. Named "StoredFile" (not "File") to avoid
// shadowing the Arduino/LittleFS File type inside platform implementations.
enum class StoredFile { JUMPS, TRACE };

// Mount storage, formatting on a failed first mount (first-boot-ever case).
// `announce` is called with the SAME progress lines main.cpp's setup() used
// to emit inline ("# first boot: formatting storage...", "# storage
// ready"/"# storage format failed") — passed in as a callback so this seam
// never depends on the emit layer. Also resumes any existing trace.csv /
// jumps.csv state (byte count, header-written flags, cap status) so behavior
// across a reboot is unchanged. Returns overall mounted-ok.
bool init(void (*announce)(const char* line));

// Non-destructive mount retry: the same mount ladder as init(), but it
// NEVER formats — an unreadable/foreign superblock is announced and
// reported as failure instead of being rebuilt over possibly-live data.
// Exists for the guard-skipped-mount case (StoreGuard latched, on-chip data
// state unknown — the mule's 61-jump history, 2026-08-12): `format` was the
// only retry and it destroys by design. A success fully resumes append
// state, exactly like init().
bool try_mount(void (*announce)(const char* line));

// True if storage is mounted (mirrors main.cpp's own fs_ok, for the
// self-test's flash PASS/FAIL row).
bool ok();

// LAST-RESORT recovery: full re-init -> chip erase -> fresh superblock,
// deliberately usable when ok() is false — the state every other API
// refuses to touch. Exists because an interrupted first-boot format
// (SENSE_FIRST_BOOT item 21, observed for real 2026-08-12) leaves the fs
// permanently unmountable with `clear` refusing to help, and on a sealed
// box "reflash and hope" is not a recovery plan. Destroys all stored data
// by definition. Returns true if the store is mounted and empty after.
bool hard_format(void (*announce)(const char* line));

// Free bytes on the storage partition (self-test's flash detail=NNNB_free).
uint32_t free_bytes();

// ---- jumps.csv ----
// Why this reports a status (F-10, audit 2026-08-22): jumps_append() was
// `void` with THREE bare-return refusal paths, and main.cpp incremented
// stored_jumps unconditionally afterwards. Nothing lied to the rider —
// jumps_scan() re-derives the count from flash — but the seam made
// "refused" and "stored" indistinguishable to every caller, which is the
// same shape as F-07 (a void trace_clear() whose failure had nowhere to go)
// and F-09 (a self-test that printed a verdict it never checked). Three
// bugs, one interface habit. A caller that cannot ask cannot report.
enum class AppendResult : unsigned char {
  OK = 0,
  FS_DOWN,       // storage not mounted — nothing was written
  REGION_FULL,   // jumps region has no room for another record
  WRITE_FAILED,  // short/failed write; the append offset was skipped forward
                 // past the torn bytes (never resumed on top of them)
};

// Append one row (writes the CSV header first time only). Returns OK only if
// the record is actually on flash; see AppendResult for the refusals.
AppendResult jumps_append(uint32_t n, float takeoff_s, float airtime_raw_s,
                          float airtime_s, float height_m,
                          uint16_t med_a_mg = 0, uint16_t med_w_dps = 0,
                          uint16_t med_acorr_mg = 0, uint16_t n_air = 0);
// Parse the stored file: row count and the max value of its last column
// (height_m). Both 0 if storage is down or the file is empty/missing.
void jumps_scan(uint32_t& count, float& best_m);

// ---- trace.csv ----
// Append raw "t,mag\n"-formatted text (writes the CSV header first time
// only), accounting bytes and enforcing the JH_TRACE_MAX_BYTES cap. Returns
// true exactly on the append that newly crosses the cap (main.cpp narrates
// that transition once).
bool trace_append(const char* data, size_t len);
// Bytes a trace dump will stream (equals stored bytes on platforms that store CSV).
uint32_t trace_bytes();
bool trace_is_full();

// ---- framed raw read-back (jumps/trace/dump commands) ----
// Single-slot sequential reader: open, repeatedly read_chunk() until it
// returns 0 (EOF), then close_read(). Mirrors the block-copy loop main.cpp
// used to run directly over a LittleFS File. open_read() returns false if
// storage isn't mounted or the file doesn't exist — callers still emit the
// FILE BEGIN/END frame either way (see main.cpp's printFileFramed).
bool open_read(StoredFile which);
size_t read_chunk(uint8_t* buf, size_t max_len);
void close_read();

// ---- housekeeping ----
// Remove both files (no-op if storage isn't mounted) and reset all counters
// above (byte count, cap-full flag, header-written flags) unconditionally.
void clear();

// Erase the TRACE region only, preserving every stored jump. The
// storage-lifecycle primitive: the trace fills in ~5 h and then records
// nothing forever, while jumps (2048 records, ~100 sessions) are the user's
// history and the watch's reconnect source. See the implementation comment
// and docs/garmin-only.md §3. No-op when storage is not mounted.
void trace_clear();

// F-07 (audit 2026-08-22): true when a trace_clear() erase FAILED and the
// region's layout became unknowable by scanning. trace_append() refuses while
// this is set, because a stale island may sit above the derived append point
// and NOR programming would AND-merge into it and report success. Query it
// rather than assuming trace_clear() worked — it used to report ok
// unconditionally, which is how the corruption stayed invisible.
bool trace_wedged();

// Restore the wedged flag from persistent storage at boot, or clear it after
// an operation that re-erased the region. jh_store does NOT reach into
// jh_persist itself — main.cpp owns that bracket, exactly as it already does
// for Key::StoreGuard around mount.
void set_trace_wedged(bool wedged);

}  // namespace jh_store
