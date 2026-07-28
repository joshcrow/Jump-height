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
// "n,takeoff_s,airtime_raw_s,airtime_s,height_m\n"; trace.csv rows are
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

// True if storage is mounted (mirrors main.cpp's own fs_ok, for the
// self-test's flash PASS/FAIL row).
bool ok();

// Free bytes on the storage partition (self-test's flash detail=NNNB_free).
uint32_t free_bytes();

// ---- jumps.csv ----
// Append one row (writes the CSV header first time only). No-op if storage
// isn't mounted.
void jumps_append(uint32_t n, float takeoff_s, float airtime_raw_s,
                   float airtime_s, float height_m);
// Parse the stored file: row count and the max value of its last column
// (height_m). Both 0 if storage is down or the file is empty/missing.
void jumps_scan(uint32_t& count, float& best_m);

// ---- trace.csv ----
// Append raw "t,mag\n"-formatted text (writes the CSV header first time
// only), accounting bytes and enforcing the JH_TRACE_MAX_BYTES cap. Returns
// true exactly on the append that newly crosses the cap (main.cpp narrates
// that transition once).
bool trace_append(const char* data, size_t len);
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

}  // namespace jh_store
