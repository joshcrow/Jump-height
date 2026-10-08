"""Python mirror of firmware/include/event_format.h -- six-axis event pages.

The puck's event region (firmware batch 2, spec 2026-10-07 section 3.3) is a
run of 256-byte pages: SAMPLES (16 raw accel+gyro samples with per-sample
MCU time), BEGIN (trigger, provenance, units), END (counts, clipping, read
health, linked detector jumps) and TRIG (one summary entry per |a| floor
crossing, captured or not). This module decodes a region image (what the
`events` command streams, base64-framed) into events and trigger records,
and encodes pages for tests and tools/fake_device.py.

Units. Samples are the sensor's raw i16 registers. SI conversion uses the
constants recorded IN EACH BEGIN (accel_g_per_lsb, gyro_dps_per_lsb), not
constants remembered here; time is micros64 at the FreeRTOS tick
(time_quantum_ns = 976,563 in every BEGIN). Nothing is calibrated: the
measured +2.9 % accelerometer gain (docs/STATUS.md, bench 2026-10-04) is NOT
corrected, and no g_baseline division is applied -- both are recorded so an
analysis can apply them deliberately.

No silent passes (CLAUDE.md rule 3): a page that is neither erased nor
CRC-valid is DAMAGED and counted; an unknown format_version is an error, not
a guess; an event whose BEGIN or END is missing, whose sample_seq has gaps,
or that lost samples is marked incomplete with the reason.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass, field

PAGE_BYTES = 256
MAGIC = 0xE6
FORMAT_VERSION = 1
HDR_BYTES = 26
CRC_OFFSET = 252
SAMPLE_BYTES = 14
SAMPLES_PER_PAGE = 16
TRIG_ENTRY_BYTES = 16
TRIG_PER_PAGE = 14
MAX_LINKS = 4
REGION_BYTES = 528384
REGION_PAGES = REGION_BYTES // PAGE_BYTES   # 2064
RESERVE_PAGES = 8
FLAG_TIME_BREAK = 0x01
TIME_UNIT_HZ = 1_000_000
TIME_QUANTUM_NS = 976_563
ACCEL_G_PER_LSB = 0.000488
GYRO_DPS_PER_LSB = 0.070
G_MPS2 = 9.80665

PAGE_SAMPLES, PAGE_BEGIN, PAGE_END, PAGE_TRIG = 0, 1, 2, 3
PAGE_TYPE_NAMES = {0: "SAMPLES", 1: "BEGIN", 2: "END", 3: "TRIG"}

CAUSES = {1: "TIER_A", 2: "TIER_B", 3: "DETECTOR"}
CLOSE_REASONS = {1: "POST_EXPIRED", 2: "MAX_LEN", 3: "IDLE", 4: "COMMAND",
                 5: "REGION_FULL", 6: "RING_OVERRUN", 7: "DISABLED"}
DECISIONS = {1: "OPENED_A", 2: "OPENED_B", 3: "EXTENDED", 4: "REFUSED_BELOW_TIER",
             5: "REFUSED_BUDGET_A", 6: "REFUSED_BUDGET_B", 7: "REFUSED_FULL",
             8: "FORCED_DETECTOR", 9: "INSIDE_WINDOW", 10: "REFUSED_BUSY"}
TIER_HINTS = {0: "FLOOR", 1: "TIER_B", 2: "TIER_A", 3: "DETECTOR"}

HEADER_FMT = "<BBBBIHHIQH"
SAMPLE_FMT = "<H6h"
BEGIN_FMT = "<B8sIIQQIHBHHHIHHHff7sBhBffffBfff"
LINK_FMT = "IIdff"
END_FMT = "<IIIB6HHHHIhBBB" + LINK_FMT * MAX_LINKS + "IH"
TRIG_FMT = "<QHBBHH"

assert struct.calcsize(HEADER_FMT) == HDR_BYTES
assert struct.calcsize(SAMPLE_FMT) == SAMPLE_BYTES
assert struct.calcsize(BEGIN_FMT) == 104      # event_format.h BEGIN_PAYLOAD_BYTES
assert struct.calcsize(END_FMT) == 142        # event_format.h END_PAYLOAD_BYTES
assert struct.calcsize(TRIG_FMT) == TRIG_ENTRY_BYTES


def f32(x: float) -> float:
    """Round to float32 -- the firmware's float arithmetic, emulated."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def mg_from_g(g: float) -> int:
    """event_format.h mg_from_g(): the float32 g, times 1000 plus 0.5 in
    double (exact), truncated, saturating at 0 and 65535."""
    x = f32(g) * 1000.0 + 0.5
    if not x > 0.0:
        return 0
    if x >= 65535.0:
        return 65535
    return int(x)


def temp_c(raw: int) -> float:
    """LSM6DS3TR-C OUT_TEMP: 25 degC + raw/256 (TIMEBASE_STUDY; ST allows a
    large absolute offset, so treat as relative)."""
    return 25.0 + raw / 256.0


# ------------------------------------------------------------------- encode

def _seal(page: bytearray) -> bytes:
    struct.pack_into("<I", page, CRC_OFFSET, zlib.crc32(bytes(page[:CRC_OFFSET])) & 0xFFFFFFFF)
    return bytes(page)


def page_header(type_: int, count: int, flags: int, boot_id: int, event_id: int,
                page_seq: int, sample_seq: int, t_first_us: int,
                gyro_bad_mask: int = 0) -> bytearray:
    page = bytearray(PAGE_BYTES)
    struct.pack_into(HEADER_FMT, page, 0, MAGIC, type_, count, flags, boot_id,
                     event_id, page_seq, sample_seq, t_first_us & 0xFFFFFFFFFFFFFFFF,
                     gyro_bad_mask)
    return page


def encode_samples_page(*, boot_id, event_id, page_seq, sample_seq, t_first_us,
                        samples, flags=0, gyro_bad_mask=0) -> bytes:
    """samples: [(dt_us, (ax, ay, az, gx, gy, gz)), ...] -- dt of the first
    is 0 by definition."""
    if len(samples) > SAMPLES_PER_PAGE:
        raise ValueError("at most 16 samples per page")
    page = page_header(PAGE_SAMPLES, len(samples), flags, boot_id, event_id,
                       page_seq, sample_seq, t_first_us, gyro_bad_mask)
    for i, (dt, v) in enumerate(samples):
        struct.pack_into(SAMPLE_FMT, page, HDR_BYTES + i * SAMPLE_BYTES, dt, *v)
    return _seal(page)


def encode_begin_page(*, boot_id, event_id, sample_seq, t_first_us, src: bytes,
                      t0_us, trigger_t_us, trigger_sample_seq, trigger_mag_mg,
                      cause, used_pages, allow_a, allow_b, m_s, pre_ms, post_ms,
                      max_ms, regs: bytes, regs_ok, temp_raw, temp_ok, g_baseline,
                      gyro_bias, detector_state, spin_lever_m, airtime_offset_s,
                      height_scale, format_version=FORMAT_VERSION) -> bytes:
    page = page_header(PAGE_BEGIN, 0, 0, boot_id, event_id, 0, sample_seq, t_first_us)
    struct.pack_into(BEGIN_FMT, page, HDR_BYTES, format_version, src[:8].ljust(8, b"\0"),
                     TIME_UNIT_HZ, TIME_QUANTUM_NS, t0_us, trigger_t_us,
                     trigger_sample_seq, trigger_mag_mg, cause, used_pages, allow_a,
                     allow_b, m_s, pre_ms, post_ms, max_ms, ACCEL_G_PER_LSB,
                     GYRO_DPS_PER_LSB, bytes(regs)[:7].ljust(7, b"\0"), regs_ok,
                     temp_raw, temp_ok, g_baseline, gyro_bias[0], gyro_bias[1],
                     gyro_bias[2], detector_state, spin_lever_m, airtime_offset_s,
                     height_scale)
    return _seal(page)


def encode_end_page(*, boot_id, event_id, page_seq, sample_seq, t_first_us,
                    n_samples, last_sample_seq, n_dropped, close_reason, clip,
                    gyro_bad, dup_polls, late_polls, max_dt_us, temp_raw_end,
                    temp_ok, links, links_overflow, max_page_write_us,
                    pages_over_slack) -> bytes:
    """links: up to 4 dicts {session_n, stored_n, takeoff_s, airtime_raw_s,
    height_m}."""
    page = page_header(PAGE_END, 0, 0, boot_id, event_id, page_seq, sample_seq, t_first_us)
    flat = []
    for i in range(MAX_LINKS):
        if i < len(links):
            l = links[i]
            flat += [l["session_n"], l["stored_n"], l["takeoff_s"], l["airtime_raw_s"], l["height_m"]]
        else:
            flat += [0, 0, 0.0, 0.0, 0.0]
    struct.pack_into(END_FMT, page, HDR_BYTES, n_samples, last_sample_seq, n_dropped,
                     close_reason, *clip, gyro_bad, dup_polls, late_polls, max_dt_us,
                     temp_raw_end, temp_ok, min(len(links), MAX_LINKS), links_overflow,
                     *flat, max_page_write_us, pages_over_slack)
    return _seal(page)


def encode_trig_page(*, boot_id, page_seq, sample_seq, entries) -> bytes:
    """entries: [(t_us, peak_mg, decision, tier_hint, event_id, used_pages)]"""
    if not entries or len(entries) > TRIG_PER_PAGE:
        raise ValueError("1..14 TRIG entries per page")
    page = page_header(PAGE_TRIG, len(entries), 0, boot_id, 0, page_seq, sample_seq,
                       entries[0][0])
    for i, e in enumerate(entries):
        struct.pack_into(TRIG_FMT, page, HDR_BYTES + i * TRIG_ENTRY_BYTES, *e)
    return _seal(page)


# ------------------------------------------------------------------- decode

def page_erased(page: bytes) -> bool:
    return page == b"\xff" * PAGE_BYTES


def page_valid(page: bytes) -> bool:
    return (len(page) == PAGE_BYTES and page[0] == MAGIC and
            struct.unpack_from("<I", page, CRC_OFFSET)[0] ==
            (zlib.crc32(page[:CRC_OFFSET]) & 0xFFFFFFFF))


@dataclass
class Header:
    type: int
    count: int
    flags: int
    boot_id: int
    event_id: int
    page_seq: int
    sample_seq: int
    t_first_us: int
    gyro_bad_mask: int


def parse_header(page: bytes) -> Header:
    (_magic, type_, count, flags, boot_id, event_id, page_seq, sample_seq,
     t_first_us, mask) = struct.unpack_from(HEADER_FMT, page, 0)
    return Header(type_, count, flags, boot_id, event_id, page_seq, sample_seq,
                  t_first_us, mask)


def parse_begin(page: bytes) -> dict:
    v = struct.unpack_from(BEGIN_FMT, page, HDR_BYTES)
    keys = ["format_version", "src", "time_unit_hz", "time_quantum_ns", "t0_us",
            "trigger_t_us", "trigger_sample_seq", "trigger_mag_mg", "cause",
            "used_pages", "allow_a", "allow_b", "m_s", "pre_ms", "post_ms", "max_ms",
            "accel_g_per_lsb", "gyro_dps_per_lsb", "regs", "regs_ok", "temp_raw",
            "temp_ok", "g_baseline", "gyro_bias_x", "gyro_bias_y", "gyro_bias_z",
            "detector_state", "spin_lever_m", "airtime_offset_s", "height_scale"]
    d = dict(zip(keys, v))
    d["src"] = d["src"].rstrip(b"\0").decode("ascii", errors="replace")
    d["regs"] = list(d["regs"])
    return d


def parse_end(page: bytes) -> dict:
    v = list(struct.unpack_from(END_FMT, page, HDR_BYTES))
    d = {
        "n_samples": v[0], "last_sample_seq": v[1], "n_dropped": v[2],
        "close_reason": v[3], "clip": v[4:10], "gyro_bad": v[10],
        "dup_polls": v[11], "late_polls": v[12], "max_dt_us": v[13],
        "temp_raw_end": v[14], "temp_ok": v[15], "n_links": v[16],
        "links_overflow": v[17],
    }
    links = []
    base = 18
    for i in range(MAX_LINKS):
        sn, st, to, ar, hm = v[base + 5 * i: base + 5 * i + 5]
        if i < d["n_links"]:
            links.append({"session_n": sn, "stored_n": st, "takeoff_s": to,
                          "airtime_raw_s": ar, "height_m": hm})
    d["links"] = links
    d["max_page_write_us"] = v[base + 5 * MAX_LINKS]
    d["pages_over_slack"] = v[base + 5 * MAX_LINKS + 1]
    return d


def parse_trig(page: bytes, count: int) -> list[dict]:
    out = []
    for i in range(count):
        t_us, peak, dec, hint, eid, used = struct.unpack_from(
            TRIG_FMT, page, HDR_BYTES + i * TRIG_ENTRY_BYTES)
        out.append({"t_us": t_us, "peak_mg": peak, "decision": dec,
                    "decision_name": DECISIONS.get(dec, f"UNKNOWN_{dec}"),
                    "tier_hint": hint, "event_id": eid, "used_pages": used})
    return out


def parse_samples(page: bytes, h: Header) -> list[tuple[int, int, tuple, bool]]:
    """[(sample_seq, t_us, (ax..gz), gyro_ok)] with absolute times rebuilt
    from t_first_us + the per-sample deltas."""
    out = []
    t = h.t_first_us
    for i in range(h.count):
        dt, *v = struct.unpack_from(SAMPLE_FMT, page, HDR_BYTES + i * SAMPLE_BYTES)
        if i > 0:
            t += dt
        out.append((h.sample_seq + i, t, tuple(v), not (h.gyro_bad_mask >> i) & 1))
    return out


@dataclass
class Event:
    boot_id: int
    event_id: int
    begin: dict | None = None
    end: dict | None = None
    samples: list = field(default_factory=list)   # (seq, t_us, raw6, gyro_ok)
    page_flags: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    begin_t_first_us: int | None = None

    @property
    def key(self) -> str:
        return f"{self.boot_id:08x}-{self.event_id}"

    @property
    def complete(self) -> bool:
        return not self.problems


@dataclass
class RegionDecode:
    events: list            # [Event], in region order of their first page
    triggers: list          # TRIG entries, each with boot_id added
    pages_total: int
    pages_erased: int
    damaged_pages: list     # page indices
    errors: list            # loud, structural problems (unknown version...)


def decode_region(data: bytes) -> RegionDecode:
    """Decode an `events` export (the region's bytes from page 0 up to the
    append point). Never raises on content: every problem is reported."""
    errors: list[str] = []
    if len(data) % PAGE_BYTES:
        errors.append(f"region is {len(data)} bytes, not a whole number of "
                      f"{PAGE_BYTES}-byte pages; the last "
                      f"{len(data) % PAGE_BYTES} bytes were ignored")
    n = len(data) // PAGE_BYTES
    events: dict[tuple[int, int], Event] = {}
    order: list[tuple[int, int]] = []
    triggers: list[dict] = []
    damaged: list[int] = []
    erased = 0
    for i in range(n):
        page = data[i * PAGE_BYTES:(i + 1) * PAGE_BYTES]
        if page_erased(page):
            erased += 1
            continue
        if not page_valid(page):
            damaged.append(i)
            continue
        h = parse_header(page)
        if h.type == PAGE_TRIG:
            for e in parse_trig(page, h.count):
                e["boot_id"] = h.boot_id
                triggers.append(e)
            continue
        if h.type not in (PAGE_SAMPLES, PAGE_BEGIN, PAGE_END):
            errors.append(f"page {i}: unknown page type {h.type}")
            continue
        k = (h.boot_id, h.event_id)
        ev = events.get(k)
        if ev is None:
            ev = events[k] = Event(h.boot_id, h.event_id)
            order.append(k)
        if h.type == PAGE_BEGIN:
            b = parse_begin(page)
            if b["format_version"] != FORMAT_VERSION:
                msg = (f"page {i}: event {ev.key} BEGIN has format_version="
                       f"{b['format_version']}; this decoder knows only "
                       f"{FORMAT_VERSION}")
                errors.append(msg)
                ev.problems.append(msg)
            if ev.begin is not None:
                ev.problems.append(f"page {i}: a second BEGIN")
            ev.begin = b
            ev.begin_t_first_us = h.t_first_us
        elif h.type == PAGE_END:
            if ev.end is not None:
                ev.problems.append(f"page {i}: a second END")
            ev.end = parse_end(page)
        else:
            ev.samples.extend(parse_samples(page, h))
            ev.page_flags.append(h.flags)
    out = []
    for k in order:
        ev = events[k]
        if ev.begin is None:
            ev.problems.append("no BEGIN page")
        if ev.end is None:
            ev.problems.append("no END page")
        seqs = [s[0] for s in ev.samples]
        for a, b in zip(seqs, seqs[1:]):
            if b != a + 1:
                ev.problems.append(f"sample_seq gap {a} -> {b}")
                break
        if ev.end is not None:
            if ev.end["n_dropped"]:
                ev.problems.append(f"{ev.end['n_dropped']} samples were assigned "
                                   "but never written ("
                                   + CLOSE_REASONS.get(ev.end["close_reason"], "?") + ")")
            if ev.end["n_samples"] != len(ev.samples):
                ev.problems.append(f"END says {ev.end['n_samples']} samples, "
                                   f"{len(ev.samples)} decoded")
        out.append(ev)
    if damaged:
        # A damaged page cannot be attributed to an event (its header is not
        # trustworthy), so every event is suspect only if it shows a gap or a
        # count mismatch -- which the checks above already catch.
        pass
    return RegionDecode(events=out, triggers=triggers, pages_total=n,
                        pages_erased=erased, damaged_pages=damaged, errors=errors)


# ------------------------------------------------------------------ SI rows

def sample_rows(ev: Event):
    """Per-sample SI rows for one event. Sensor frame, uncalibrated: raw x
    the BEGIN's own scale, no g_baseline division, no gain correction."""
    b = ev.begin or {}
    t0 = b.get("t0_us")
    a_lsb = b.get("accel_g_per_lsb", f32(ACCEL_G_PER_LSB))
    g_lsb = b.get("gyro_dps_per_lsb", f32(GYRO_DPS_PER_LSB))
    prev_t = None
    prev_v = None
    for seq, t_us, v, gyro_ok in ev.samples:
        ax, ay, az, gx, gy, gz = v
        clip_mask = 0
        for k, x in enumerate(v):
            if x in (32767, -32768):
                clip_mask |= 1 << k
        yield {
            "sample_seq": seq,
            "t_s": (t_us - t0) * 1e-6 if t0 is not None else None,
            "t_us": t_us,
            "dt_s": (t_us - prev_t) * 1e-6 if prev_t is not None else None,
            "ax_mps2": ax * a_lsb * G_MPS2, "ay_mps2": ay * a_lsb * G_MPS2,
            "az_mps2": az * a_lsb * G_MPS2,
            "gx_rads": gx * g_lsb * math.pi / 180.0,
            "gy_rads": gy * g_lsb * math.pi / 180.0,
            "gz_rads": gz * g_lsb * math.pi / 180.0,
            "raw": v, "gyro_ok": gyro_ok,
            "dup": prev_v is not None and v == prev_v,
            "clip_mask": clip_mask,
        }
        prev_t = t_us
        prev_v = v


def decode_ctrl_regs(regs: list[int]) -> dict:
    """CTRL1_XL..CTRL7_G as read back, with the fields this firmware sets
    (lsm6ds3_min.h) decoded."""
    if len(regs) < 7:
        return {}
    odr_codes = {0: 0, 1: 12.5, 2: 26, 3: 52, 4: 104, 5: 208, 6: 416, 7: 833,
                 8: 1660, 9: 3330, 10: 6660}
    xl_fs = {0: 2, 1: 16, 2: 4, 3: 8}
    g_fs = {0: 250, 1: 500, 2: 1000, 3: 2000}
    c1, c2, c3, c4, c5, c6, c7 = regs[:7]
    return {
        "ctrl1_xl": f"0x{c1:02x}", "accel_odr_hz": odr_codes.get(c1 >> 4),
        "accel_fs_g": xl_fs[(c1 >> 2) & 3],
        "ctrl2_g": f"0x{c2:02x}", "gyro_odr_hz": odr_codes.get(c2 >> 4),
        "gyro_fs_dps": 125 if (c2 >> 1) & 1 else g_fs[(c2 >> 2) & 3],
        "ctrl3_c": f"0x{c3:02x}", "bdu": (c3 >> 6) & 1, "if_inc": (c3 >> 2) & 1,
        "ctrl4_c": f"0x{c4:02x}", "lpf1_sel_g": (c4 >> 1) & 1,
        "ctrl5_c": f"0x{c5:02x}", "ctrl6_c": f"0x{c6:02x}",
        "ctrl7_g": f"0x{c7:02x}", "g_hm_mode": (c7 >> 7) & 1,
    }
