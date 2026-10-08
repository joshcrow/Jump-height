// event_format.h — the on-flash format of six-axis event capture
// (firmware batch 2, spec 2026-10-07 §3.3).
//
// WHY THIS EXISTS. The trace stores one u16 |a| per sample
// (trace_codec.h). Direction and rotation are thrown away, so a board's
// trajectory cannot be reconstructed offline from anything this puck has
// ever recorded. An EVENT is a window of raw accelerometer + gyro registers
// around a plausible jump, with per-sample MCU time and the provenance a
// reconstruction needs (sensor config read back, temperature, gravity
// baseline, gyro bias, calibration, boot identity, the detector jump it
// covers). event_capture.h decides WHEN to record; this file decides only
// HOW the bytes look.
//
// Dependency-free and host-compilable on purpose, like trace_codec.h: the
// firmware, firmware/test/event_capture_harness.cpp and the Python mirror
// (sim/event_codec.py) must agree byte for byte, and
// tools/tests/test_event_codec.py checks that they do.
//
// PAGE LAYOUT. Every record is one 256-byte page, aligned to a QSPI program
// page, so every write is exactly one page program (the nRF52840 QSPI needs
// word-aligned flash addresses — jh_store.cpp's align4() comment; a page
// boundary is trivially aligned). All fields little-endian.
//
//   off  size  field
//     0     1  magic        0xE6
//     1     1  type         0 SAMPLES, 1 BEGIN, 2 END, 3 TRIG
//     2     1  count        samples in the page, or TRIG entries
//     3     1  flags        bit0 = first page after a time break
//     4     4  boot_id      random per boot (main.cpp)
//     8     2  event_id     per boot, from 1 (0 on TRIG pages)
//    10     2  page_seq     within the event (BEGIN = 0); TRIG pages count
//                           their own sequence per boot
//    12     4  sample_seq   boot-scoped index of the page's first sample
//    16     8  t_first_us   micros64 of the first sample, or the record time
//    24     2  gyro_bad_mask  SAMPLES only: bit i = sample i's gyro read failed
//    26   224  payload      16 x 14-byte samples, or a BEGIN/END/TRIG payload
//                           (BEGIN/END/TRIG may use up to 226 bytes, 26..251)
//   250     2  pad 0x00     (SAMPLES pages)
//   252     4  crc32        zlib CRC-32 over bytes 0..251
//
// SAMPLE (14 bytes): dt u16 (µs since the previous sample in the same page;
// 0 for the first; a delta over 65,535 µs starts a new page with an absolute
// t_first_us), then ax ay az gx gy gz as the sensor's raw i16 registers
// (0.488 mg/LSB at ±16 g, 70 mdps/LSB at ±2000 dps — lsm6ds3_min.h).
//
// TIME. Every timestamp is jh_clock::micros64(), the value main.cpp turns
// into the detector's `t`. Its resolution is the FreeRTOS tick, 976.5625 µs
// (spec §2.4), recorded in every BEGIN as time_quantum_ns so no decoder ever
// has to remember it.
//
// SPDX-License-Identifier: MIT

#pragma once

#include <stddef.h>
#include <stdint.h>
#include <string.h>

namespace jh_event {

// ------------------------------------------------------------- geometry
static const uint32_t PAGE_BYTES       = 256;
static const uint8_t  MAGIC            = 0xE6;
static const uint8_t  FORMAT_VERSION   = 1;
static const uint32_t HDR_BYTES        = 26;
static const uint32_t CRC_OFFSET       = 252;
static const uint32_t PAYLOAD_MAX      = CRC_OFFSET - HDR_BYTES;  // 226
static const uint32_t SAMPLE_BYTES     = 14;
static const uint32_t SAMPLES_PER_PAGE = 16;                      // 224 B
static const uint32_t TRIG_ENTRY_BYTES = 16;
static const uint32_t TRIG_PER_PAGE    = 14;                      // 224 B
static const uint32_t MAX_LINKS        = 4;

// The event region (spec §3.3): 129 sectors at the top of the 2 MiB chip.
static const uint32_t REGION_BYTES  = 528384;
static const uint32_t REGION_PAGES  = REGION_BYTES / PAGE_BYTES;  // 2064
// The last pages are kept for END and TRIG records, so a window that runs
// the region out can still say so (spec §3.2 rule 7).
static const uint32_t RESERVE_PAGES = 8;

static const uint8_t  FLAG_TIME_BREAK = 0x01;

// Units recorded in every BEGIN.
static const uint32_t TIME_UNIT_HZ     = 1000000;   // timestamps are µs
static const uint32_t TIME_QUANTUM_NS  = 976563;    // 1/1024 s FreeRTOS tick
static const float    ACCEL_G_PER_LSB  = 0.000488f; // lsm6ds3_min.h, ±16 g
static const float    GYRO_DPS_PER_LSB = 0.070f;    // lsm6ds3_min.h, ±2000 dps

static_assert(HDR_BYTES + SAMPLES_PER_PAGE * SAMPLE_BYTES <= CRC_OFFSET,
              "samples must fit before the CRC");
static_assert(HDR_BYTES + TRIG_PER_PAGE * TRIG_ENTRY_BYTES <= CRC_OFFSET,
              "TRIG entries must fit before the CRC");
static_assert(REGION_BYTES % 4096 == 0, "the event region is whole sectors");

enum PageType : uint8_t {
  PAGE_SAMPLES = 0,
  PAGE_BEGIN   = 1,
  PAGE_END     = 2,
  PAGE_TRIG    = 3,
};

// Why a window opened.
enum Cause : uint8_t {
  CAUSE_TIER_A   = 1,
  CAUSE_TIER_B   = 2,
  CAUSE_DETECTOR = 3,
};

// Why a window closed.
enum CloseReason : uint8_t {
  CLOSE_POST_EXPIRED = 1,  // post_s elapsed with no further >= tier_b sample
  CLOSE_MAX_LEN      = 2,  // extension hit max_len_s
  CLOSE_IDLE         = 3,  // the motion gate went idle
  CLOSE_COMMAND      = 4,  // events/evclear/off/dfu/uf2/format
  CLOSE_REGION_FULL  = 5,  // no room for another SAMPLES page
  CLOSE_RING_OVERRUN = 6,  // an unwritten sample was about to be overwritten
  CLOSE_DISABLED     = 7,  // storage went down mid-window (no END is written)
};

// What a floor crossing (or a detector jump) led to — one TRIG entry each.
enum Decision : uint8_t {
  DEC_OPENED_A           = 1,
  DEC_OPENED_B           = 2,
  DEC_EXTENDED           = 3,   // a window was open and a >= tier_b sample moved its end
  DEC_REFUSED_BELOW_TIER = 4,   // peak under tier_b, no window open
  DEC_REFUSED_BUDGET_A   = 5,   // tier A refused by pacing
  DEC_REFUSED_BUDGET_B   = 6,   // tier B refused by pacing
  DEC_REFUSED_FULL       = 7,   // no room in the region at all
  DEC_FORCED_DETECTOR    = 8,   // a detector JUMP forced or joined a window
  // Not in the spec's list, added because the list had no honest word for
  // it: a crossing whose samples WERE captured (a window was already open)
  // but which did not reach tier_b, so it did not extend the window.
  DEC_INSIDE_WINDOW      = 9,
  // A window wanted to open while the write pipeline already held its
  // maximum (event_capture.h kMaxWindows). Not reachable at 200 Hz with a
  // working sink; it exists so that if it ever happens it is counted.
  DEC_REFUSED_BUSY       = 10,
};

// TRIG tier_hint: the highest tier the crossing's peak reached.
enum TierHint : uint8_t {
  HINT_FLOOR    = 0,
  HINT_TIER_B   = 1,
  HINT_TIER_A   = 2,
  HINT_DETECTOR = 3,
};

// ------------------------------------------------------- CRC-32 (zlib)
// CRC-32/ISO-HDLC — byte for byte Python's zlib.crc32(); the same bitwise
// form main.cpp's crc32Update() uses for the traceraw export.
inline uint32_t crc32_update(uint32_t crc, const uint8_t* data, size_t len) {
  for (size_t i = 0; i < len; ++i) {
    crc ^= data[i];
    for (int b = 0; b < 8; ++b) crc = (crc & 1u) ? ((crc >> 1) ^ 0xEDB88320u) : (crc >> 1);
  }
  return crc;
}
inline uint32_t crc32(const uint8_t* data, size_t len) {
  return crc32_update(0xFFFFFFFFu, data, len) ^ 0xFFFFFFFFu;
}

// -------------------------------------------------- little-endian helpers
inline void put_u8(uint8_t* p, uint8_t v) { p[0] = v; }
inline void put_u16(uint8_t* p, uint16_t v) { p[0] = (uint8_t)v; p[1] = (uint8_t)(v >> 8); }
inline void put_i16(uint8_t* p, int16_t v) { put_u16(p, (uint16_t)v); }
inline void put_u32(uint8_t* p, uint32_t v) {
  for (int i = 0; i < 4; ++i) p[i] = (uint8_t)(v >> (8 * i));
}
inline void put_u64(uint8_t* p, uint64_t v) {
  for (int i = 0; i < 8; ++i) p[i] = (uint8_t)(v >> (8 * i));
}
inline void put_f32(uint8_t* p, float f) {
  uint32_t u;
  memcpy(&u, &f, 4);
  put_u32(p, u);
}
inline void put_f64(uint8_t* p, double d) {
  uint64_t u;
  memcpy(&u, &d, 8);
  put_u64(p, u);
}
inline uint16_t get_u16(const uint8_t* p) { return (uint16_t)(p[0] | (p[1] << 8)); }
inline uint32_t get_u32(const uint8_t* p) {
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

// A sequential payload writer: each put advances; overflow is impossible by
// construction for the fixed layouts below (static sizes checked by the
// Python mirror's struct.calcsize and by the harness).
struct Cursor {
  uint8_t* p;
  void u8(uint8_t v)   { put_u8(p, v);  p += 1; }
  void u16(uint16_t v) { put_u16(p, v); p += 2; }
  void i16(int16_t v)  { put_i16(p, v); p += 2; }
  void u32(uint32_t v) { put_u32(p, v); p += 4; }
  void u64(uint64_t v) { put_u64(p, v); p += 8; }
  void f32(float v)    { put_f32(p, v); p += 4; }
  void f64(double v)   { put_f64(p, v); p += 8; }
  void bytes(const void* src, size_t n) { memcpy(p, src, n); p += n; }
};

// ----------------------------------------------------------------- header
struct PageHeader {
  uint8_t  type;
  uint8_t  count;
  uint8_t  flags;
  uint32_t boot_id;
  uint16_t event_id;
  uint16_t page_seq;
  uint32_t sample_seq;
  uint64_t t_first_us;
  uint16_t gyro_bad_mask;
};

// Zero the page and write the header. The payload is written after this,
// then seal() computes the CRC last.
inline void begin_page(uint8_t* page, const PageHeader& h) {
  memset(page, 0, PAGE_BYTES);
  Cursor c{page};
  c.u8(MAGIC);
  c.u8(h.type);
  c.u8(h.count);
  c.u8(h.flags);
  c.u32(h.boot_id);
  c.u16(h.event_id);
  c.u16(h.page_seq);
  c.u32(h.sample_seq);
  c.u64(h.t_first_us);
  c.u16(h.gyro_bad_mask);
}

inline void seal(uint8_t* page) { put_u32(page + CRC_OFFSET, crc32(page, CRC_OFFSET)); }

inline bool page_erased(const uint8_t* page) {
  for (uint32_t i = 0; i < PAGE_BYTES; ++i)
    if (page[i] != 0xFF) return false;
  return true;
}

// Whole and ours: magic + CRC. A page that is neither this nor erased is
// damaged (a torn write, a failed write, or corruption) — the store counts
// those at mount and the decoder reports them; nothing ever writes on top.
inline bool page_valid(const uint8_t* page) {
  return page[0] == MAGIC && get_u32(page + CRC_OFFSET) == crc32(page, CRC_OFFSET);
}

// ------------------------------------------------------------ payloads
// Provenance read when a window opens (main.cpp supplies it through
// event_capture.h's Hooks; the harness supplies fixed values).
struct Provenance {
  uint8_t regs[7];        // burst read 0x10..0x16: CTRL1_XL .. CTRL7_G
  uint8_t regs_ok;
  int16_t temp_raw;       // OUT_TEMP 0x20/0x21; degC = 25 + raw/256
  uint8_t temp_ok;
  float   g_baseline;     // main.cpp: every |a| is divided by this
  float   gyro_bias[3];   // gyro_bias.h, deg/s
  uint8_t detector_state; // jump::State as an integer
  float   spin_lever_m;
  float   airtime_offset_s;
  float   height_scale;
};

struct BeginInfo {
  char     src[8];             // JH_BUILD_SRC, not NUL-terminated
  uint64_t t0_us;              // main.cpp's t0_us: the trace's time zero
  uint64_t trigger_t_us;
  uint32_t trigger_sample_seq;
  uint16_t trigger_mag_mg;
  uint8_t  cause;
  uint16_t used_pages;
  uint16_t allow_a;
  uint16_t allow_b;
  uint32_t m_s;                // gate-open seconds since boot at open
  uint16_t pre_ms;
  uint16_t post_ms;
  uint16_t max_ms;
};

// BEGIN payload: 104 bytes (sim/event_codec.py BEGIN_FMT).
inline void put_begin_payload(uint8_t* page, const BeginInfo& b, const Provenance& p) {
  Cursor c{page + HDR_BYTES};
  c.u8(FORMAT_VERSION);
  c.bytes(b.src, 8);
  c.u32(TIME_UNIT_HZ);
  c.u32(TIME_QUANTUM_NS);
  c.u64(b.t0_us);
  c.u64(b.trigger_t_us);
  c.u32(b.trigger_sample_seq);
  c.u16(b.trigger_mag_mg);
  c.u8(b.cause);
  c.u16(b.used_pages);
  c.u16(b.allow_a);
  c.u16(b.allow_b);
  c.u32(b.m_s);
  c.u16(b.pre_ms);
  c.u16(b.post_ms);
  c.u16(b.max_ms);
  c.f32(ACCEL_G_PER_LSB);
  c.f32(GYRO_DPS_PER_LSB);
  c.bytes(p.regs, 7);
  c.u8(p.regs_ok);
  c.i16(p.temp_raw);
  c.u8(p.temp_ok);
  c.f32(p.g_baseline);
  c.f32(p.gyro_bias[0]);
  c.f32(p.gyro_bias[1]);
  c.f32(p.gyro_bias[2]);
  c.u8(p.detector_state);
  c.f32(p.spin_lever_m);
  c.f32(p.airtime_offset_s);
  c.f32(p.height_scale);
}
static const uint32_t BEGIN_PAYLOAD_BYTES = 104;

struct JumpLink {
  uint32_t session_n;      // the detector's session count — the watch's n=
  uint32_t stored_n;       // the jumps.csv row n, 0 when the store refused it
  double   takeoff_s;      // detector time, same zero as trace.csv
  float    airtime_raw_s;
  float    height_m;
};

struct EndInfo {
  uint32_t n_samples;        // samples in SAMPLES pages that were written OK
  uint32_t last_sample_seq;  // last sample assigned to the window
  uint32_t n_dropped;        // assigned but never written (overrun, full, failed write)
  uint8_t  close_reason;
  uint16_t clip[6];          // samples at raw +32767 or -32768, ax ay az gx gy gz
  uint16_t gyro_bad;
  uint16_t dup_polls;        // identical consecutive accel triples
  uint16_t late_polls;       // dt > 10,000 µs
  uint32_t max_dt_us;
  int16_t  temp_raw_end;
  uint8_t  temp_ok;
  uint8_t  n_links;
  uint8_t  links_overflow;   // jumps that would not fit MAX_LINKS
  JumpLink links[MAX_LINKS];
  uint32_t max_page_write_us;
  uint16_t pages_over_slack;
};

// END payload: 142 bytes (sim/event_codec.py END_FMT).
inline void put_end_payload(uint8_t* page, const EndInfo& e) {
  Cursor c{page + HDR_BYTES};
  c.u32(e.n_samples);
  c.u32(e.last_sample_seq);
  c.u32(e.n_dropped);
  c.u8(e.close_reason);
  for (int i = 0; i < 6; ++i) c.u16(e.clip[i]);
  c.u16(e.gyro_bad);
  c.u16(e.dup_polls);
  c.u16(e.late_polls);
  c.u32(e.max_dt_us);
  c.i16(e.temp_raw_end);
  c.u8(e.temp_ok);
  c.u8(e.n_links);
  c.u8(e.links_overflow);
  for (uint32_t i = 0; i < MAX_LINKS; ++i) {
    c.u32(e.links[i].session_n);
    c.u32(e.links[i].stored_n);
    c.f64(e.links[i].takeoff_s);
    c.f32(e.links[i].airtime_raw_s);
    c.f32(e.links[i].height_m);
  }
  c.u32(e.max_page_write_us);
  c.u16(e.pages_over_slack);
}
static const uint32_t END_PAYLOAD_BYTES = 142;

struct TrigEntry {
  uint64_t t_us;
  uint16_t peak_mg;
  uint8_t  decision;
  uint8_t  tier_hint;
  uint16_t event_id;    // 0 = none
  uint16_t used_pages;
};

inline void put_trig_entry(uint8_t* page, uint32_t i, const TrigEntry& t) {
  Cursor c{page + HDR_BYTES + i * TRIG_ENTRY_BYTES};
  c.u64(t.t_us);
  c.u16(t.peak_mg);
  c.u8(t.decision);
  c.u8(t.tier_hint);
  c.u16(t.event_id);
  c.u16(t.used_pages);
}

inline void put_sample(uint8_t* page, uint32_t i, uint16_t dt_us, const int16_t v[6]) {
  Cursor c{page + HDR_BYTES + i * SAMPLE_BYTES};
  c.u16(dt_us);
  for (int k = 0; k < 6; ++k) c.i16(v[k]);
}

static_assert(BEGIN_PAYLOAD_BYTES <= PAYLOAD_MAX, "BEGIN payload fits");
static_assert(END_PAYLOAD_BYTES <= PAYLOAD_MAX, "END payload fits");

// g -> milli-g for TRIG/BEGIN, saturating. In DOUBLE on purpose: a float32
// times 1000 and plus 0.5 is exact in a double, so the result cannot depend
// on whether a compiler fuses the multiply-add (Apple clang on arm64 and
// GCC on the Cortex-M4F both may) — which is what lets sim/event_codec.py's
// mg_from_g() match it bit for bit. Called once per crossing, not per sample,
// so the M4F's software double costs nothing that matters.
inline uint16_t mg_from_g(float g) {
  const double x = (double)g * 1000.0 + 0.5;
  if (!(x > 0.0)) return 0;
  if (x >= 65535.0) return 65535;
  return (uint16_t)x;
}

}  // namespace jh_event
