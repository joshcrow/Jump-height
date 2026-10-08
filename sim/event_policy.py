"""Python reference of firmware/include/event_capture.h -- the six-axis
capture policy and its one-page-per-pass writer, line for line.

Why a second implementation: the policy decides which seconds of a ride are
ever recorded at full rate, and every number in it came from a replay of
vest data (spec 2026-10-07 section 3.2). A Python mirror lets the replay,
the tests and any future retune run the SAME rules as the firmware, and
tools/tests/test_event_codec.py proves they are the same by running one
scripted input through both and requiring byte-identical pages.

Floating point: the firmware compares float32 magnitudes against float32
thresholds; every comparison here goes through f32() so the two agree on
boundary values. Budget arithmetic is integer (ms, per mille) on both sides.

Config comes from config/params.json's `capture` section -- the same source
params.gen.h is generated from -- plus PAGE_SLACK_US, read from
firmware/include/event_config.h.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    from event_codec import (  # noqa: F401  (sim/ on sys.path)
        CLOSE_REASONS, PAGE_BYTES, REGION_PAGES, RESERVE_PAGES, SAMPLES_PER_PAGE,
        TRIG_PER_PAGE, MAX_LINKS, FLAG_TIME_BREAK, encode_begin_page, encode_end_page,
        encode_samples_page, encode_trig_page, f32, mg_from_g)
except ImportError:  # imported as sim.event_policy
    from sim.event_codec import (  # noqa: F401
        CLOSE_REASONS, PAGE_BYTES, REGION_PAGES, RESERVE_PAGES, SAMPLES_PER_PAGE,
        TRIG_PER_PAGE, MAX_LINKS, FLAG_TIME_BREAK, encode_begin_page, encode_end_page,
        encode_samples_page, encode_trig_page, f32, mg_from_g)

REPO = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO / "config" / "params.json"
EVENT_CONFIG_H = REPO / "firmware" / "include" / "event_config.h"

RING_N = 1280
RF_GYRO_BAD = 0x01
RF_GAP = 0x02
MAX_WINDOWS = 4
LATE_US = 10000

CAUSE_TIER_A, CAUSE_TIER_B, CAUSE_DETECTOR = 1, 2, 3
(CLOSE_POST_EXPIRED, CLOSE_MAX_LEN, CLOSE_IDLE, CLOSE_COMMAND, CLOSE_REGION_FULL,
 CLOSE_RING_OVERRUN, CLOSE_DISABLED) = range(1, 8)
(DEC_OPENED_A, DEC_OPENED_B, DEC_EXTENDED, DEC_REFUSED_BELOW_TIER,
 DEC_REFUSED_BUDGET_A, DEC_REFUSED_BUDGET_B, DEC_REFUSED_FULL, DEC_FORCED_DETECTOR,
 DEC_INSIDE_WINDOW, DEC_REFUSED_BUSY) = range(1, 11)
HINT_FLOOR, HINT_TIER_B, HINT_TIER_A, HINT_DETECTOR = 0, 1, 2, 3

_RANK = {DEC_OPENED_A: 10, DEC_OPENED_B: 9, DEC_EXTENDED: 8, DEC_INSIDE_WINDOW: 7,
         DEC_REFUSED_BUSY: 6, DEC_REFUSED_FULL: 5, DEC_REFUSED_BUDGET_A: 4,
         DEC_REFUSED_BUDGET_B: 3, DEC_REFUSED_BELOW_TIER: 2}

U32 = 0xFFFFFFFF


def page_slack_us(path: Path = EVENT_CONFIG_H) -> int:
    m = re.search(r"PAGE_SLACK_US\s*=\s*(\d+)", path.read_text())
    if not m:
        raise AssertionError(f"{path} has no PAGE_SLACK_US")
    return int(m.group(1))


@dataclass
class Config:
    floor_g: float
    tier_a_g: float
    tier_b_g: float
    refractory_us: int
    pre_us: int
    post_us: int
    max_len_us: int
    session_target_ms: int
    tier_a_burst_pm: int
    tier_b_reserve_pm: int
    tier_b_burst_pm: int
    sample_hz: int
    page_slack_us: int


def load_config(path: Path = CONFIG_PATH) -> Config:
    """The `capture` section, converted exactly as event_config.h does."""
    cfg = json.loads(Path(path).read_text())
    c = cfg["capture"]

    def us(x):
        return int(f32(x) * 1_000_000.0 + 0.5)

    def pm(x):
        return int(f32(x) * 1000.0 + 0.5)

    return Config(
        floor_g=f32(c["floor_g"]), tier_a_g=f32(c["tier_a_g"]), tier_b_g=f32(c["tier_b_g"]),
        refractory_us=us(c["refractory_s"]), pre_us=us(c["pre_s"]), post_us=us(c["post_s"]),
        max_len_us=us(c["max_len_s"]),
        session_target_ms=int(c["session_target_s"]) * 1000,
        tier_a_burst_pm=pm(c["tier_a_burst"]), tier_b_reserve_pm=pm(c["tier_b_reserve"]),
        tier_b_burst_pm=pm(c["tier_b_burst"]),
        sample_hz=int(cfg["firmware"]["sample_hz"]),
        page_slack_us=page_slack_us(),
    )


@dataclass
class Provenance:
    regs: bytes = b"\0" * 7
    regs_ok: int = 0
    temp_raw: int = 0
    temp_ok: int = 0
    g_baseline: float = 1.0
    gyro_bias: tuple = (0.0, 0.0, 0.0)
    detector_state: int = 0
    spin_lever_m: float = 0.0
    airtime_offset_s: float = 0.0
    height_scale: float = 1.0


@dataclass
class Win:
    event_id: int = 0
    cause: int = 0
    closed: bool = False
    close_reason: int = 0
    begin_written: bool = False
    prov_taken: bool = False
    capped: bool = False
    break_pending: bool = False
    start_seq: int = 0
    end_excl: int = 0
    write_seq: int = 0
    start_t: int = 0
    end_t: int = 0
    trig_t: int = 0
    trig_seq: int = 0
    trig_mg: int = 0
    used_at_open: int = 0
    allow_a: int = 0
    allow_b: int = 0
    m_s: int = 0
    prov: Provenance = field(default_factory=Provenance)
    page_seq: int = 0
    n_written: int = 0
    n_dropped: int = 0
    clip: list = field(default_factory=lambda: [0] * 6)
    gyro_bad: int = 0
    dup: int = 0
    late: int = 0
    max_dt: int = 0
    have_last: bool = False
    last_v: tuple = (0, 0, 0)
    last_t: int = 0
    last_written_t: int = 0
    max_write_us: int = 0
    over_slack: int = 0
    links: list = field(default_factory=list)
    links_overflow: int = 0


class Hooks:
    """Override in a subclass (the harness mirror does)."""

    def write_page(self, page: bytes) -> int:   # 1 ok, 0 failed (consumed), -1 deferred
        return 1

    def provenance(self) -> Provenance:
        return Provenance()

    def read_temp(self):                         # -> (ok, raw)
        return (False, 0)

    def now_us(self) -> int:
        return 0

    def wake_hold(self, hold: bool) -> None:
        pass


class Capture:
    def __init__(self, cfg: Config, hooks: Hooks, boot_id: int, t0_us: int, src: bytes):
        self.cfg = cfg
        self.hooks = hooks
        self.boot_id = boot_id & U32
        self.t0_us = t0_us
        self.src = src[:8].ljust(8, b"\0")
        win_samples = ((cfg.pre_us + cfg.post_us) * cfg.sample_hz) // 1_000_000
        self.W = (win_samples + SAMPLES_PER_PAGE - 1) // SAMPLES_PER_PAGE + 2
        self.P = self.total = self.used = 0
        self.enabled = False
        self.held = False
        self.hold_wanted = False
        self.ring_t_lo = [0] * RING_N
        self.ring_v = [(0,) * 6] * RING_N
        self.ring_f = [0] * RING_N
        self.seq_next = 0
        self.newest_t = 0
        self.have_prev = False
        self.prev_t = 0
        self.have_pushed = False
        self.last_push_t = 0
        self.m_us = 0
        self.next_unassigned = 0
        self.wins: list[Win] = []
        self.event_counter = 0
        self.span_active = False
        self.span_start = 0
        self.span_peak = 0.0
        self.span_dec = 0
        self.span_eid = 0
        self.force_pending = False
        self.pending_link = None
        self.trig: list[tuple] = []
        self.trig_page_seq = 0
        self.have_poll = False
        self.last_poll_t = 0
        self.last_poll = (0, 0, 0)
        self.events_boot = 0
        self.crossings = 0
        self.refused_budget = 0
        self.refused_full = 0
        self.ring_overrun = 0
        self.write_fail = 0
        self.dup_polls = 0
        self.late_polls = 0
        self.max_write_us = 0
        self.over_slack = 0
        self.trig_dropped = 0
        self.links_lost = 0
        self.dropped_disabled = 0

    # ---------------------------------------------------------------- public
    def set_region(self, used: int, total: int) -> None:
        self.used = used
        self.total = total
        self.P = total - RESERVE_PAGES if total > RESERVE_PAGES else 0

    def set_enabled(self, en: bool) -> None:
        if en == self.enabled:
            return
        self.enabled = en
        if not en:
            for w in self.wins:
                if w.end_excl > w.write_seq:
                    self.dropped_disabled += w.end_excl - w.write_seq
            self.wins = []
            if self.trig:
                self.trig_dropped += len(self.trig)
                self.trig = []
            self.span_active = False
            self.force_pending = False
            self._release_hold()

    def note_poll(self, t_us: int, accel) -> None:
        a = tuple(accel)
        if self.have_poll:
            if a == self.last_poll:
                self.dup_polls += 1
            if t_us - self.last_poll_t > LATE_US:
                self.late_polls += 1
        self.have_poll = True
        self.last_poll_t = t_us
        self.last_poll = a

    def link_jump(self, session_n, stored_n, takeoff_s, airtime_raw_s, height_m) -> None:
        self.force_pending = True
        self.pending_link = {"session_n": session_n, "stored_n": stored_n,
                             "takeoff_s": float(takeoff_s),
                             "airtime_raw_s": f32(airtime_raw_s), "height_m": f32(height_m)}

    def on_sample(self, t_us: int, raw, gyro_ok: bool, mag: float) -> None:
        cfg = self.cfg
        mag = f32(mag)
        if self.have_prev:
            d = t_us - self.prev_t
            if 0 < d < 100000:
                self.m_us += d
        self.have_prev = True
        self.prev_t = t_us
        if not self.enabled:
            self.force_pending = False
            return
        gap = (not self.have_pushed) or (t_us - self.last_push_t > cfg.pre_us)
        self.have_pushed = True
        self.last_push_t = t_us

        ow = self._open_win()
        if ow is not None and t_us > ow.end_t:
            self._close_win(ow, CLOSE_MAX_LEN if ow.capped else CLOSE_POST_EXPIRED)

        if self.seq_next >= RING_N and self.wins:
            victim = self.seq_next - RING_N
            oldest = self._oldest_unwritten()
            if oldest is not None and oldest <= victim < self.next_unassigned:
                self._ring_overrun()

        s = self.seq_next
        idx = s % RING_N
        self.ring_t_lo[idx] = t_us & U32
        self.ring_v[idx] = tuple(int(x) for x in raw)
        self.ring_f[idx] = (0 if gyro_ok else RF_GYRO_BAD) | (RF_GAP if gap else 0)
        self.newest_t = t_us
        self.seq_next += 1
        ow = self._open_win()
        if ow is not None:
            ow.end_excl = self.seq_next
            self.next_unassigned = self.seq_next

        ext = ow is not None and mag >= cfg.tier_b_g
        if ext:
            self._extend(ow, t_us)

        if self.span_active and t_us - self.span_start >= cfg.refractory_us:
            self._finalize_span()
        if not self.span_active and mag >= cfg.floor_g:
            self.span_active = True
            self.span_start = t_us
            self.span_peak = mag
            self.span_dec = 0
            self.span_eid = 0
            self.crossings += 1
        if self.span_active:
            if mag > self.span_peak:
                self.span_peak = mag
            ow = self._open_win()
            if ow is not None:
                self._note(DEC_EXTENDED if ext else DEC_INSIDE_WINDOW, ow.event_id)
            elif mag >= cfg.tier_b_g:
                a = mag >= cfg.tier_a_g
                proj = self._projected_used()
                m_ms = self.m_us // 1000
                if len(self.wins) >= MAX_WINDOWS:
                    self._note(DEC_REFUSED_BUSY, 0)
                elif proj + self.W > self.P:
                    self._note(DEC_REFUSED_FULL, 0)
                elif a and proj + self.W <= self.allow_a(m_ms):
                    self._note(DEC_OPENED_A, self._open_window(CAUSE_TIER_A, t_us, s, mag, proj, m_ms))
                elif proj + self.W <= self.allow_b(m_ms):
                    self._note(DEC_OPENED_B, self._open_window(CAUSE_TIER_B, t_us, s, mag, proj, m_ms))
                else:
                    self._note(DEC_REFUSED_BUDGET_A if a else DEC_REFUSED_BUDGET_B, 0)
            else:
                self._note(DEC_REFUSED_BELOW_TIER, 0)

        if self.force_pending:
            self.force_pending = False
            ow = self._open_win()
            proj = self._projected_used()
            if ow is not None:
                dec = DEC_FORCED_DETECTOR
            elif len(self.wins) >= MAX_WINDOWS:
                dec = DEC_REFUSED_BUSY
                self.refused_full += 1
            elif proj + self.W > self.P:
                dec = DEC_REFUSED_FULL
                self.refused_full += 1
            else:
                self._open_window(CAUSE_DETECTOR, t_us, s, mag, proj, self.m_us // 1000)
                dec = DEC_FORCED_DETECTOR
            ow = self._open_win()
            if ow is not None:
                self._add_link(ow, self.pending_link)
            else:
                self.links_lost += 1
            self._queue_trig((t_us, mg_from_g(mag), dec, HINT_DETECTOR,
                              ow.event_id if ow is not None else 0,
                              self._projected_used() & 0xFFFF))

    def close(self, reason: int) -> None:
        ow = self._open_win()
        if ow is not None:
            self._close_win(ow, reason)
        if self.span_active and reason in (CLOSE_IDLE, CLOSE_COMMAND):
            self._finalize_span()

    def service(self, skip: bool) -> bool:
        if not self.enabled:
            return False
        self._apply_hold()
        for w in self.wins:
            if not w.prov_taken:
                w.prov = self.hooks.provenance()
                w.prov_taken = True
        if skip:
            return False
        return self._write_one(False)

    def drain(self) -> bool:
        self.close(CLOSE_COMMAND)
        if not self.enabled:
            return True
        self._apply_hold()
        for w in self.wins:
            if not w.prov_taken:
                w.prov = self.hooks.provenance()
                w.prov_taken = True
        deferred = 0
        while self.wins or self.trig:
            if not self._write_one(True):
                deferred += 1
                if deferred > 8:
                    return False
        return True

    def pending(self) -> bool:
        return bool(self.wins or self.trig)

    def is_open(self) -> bool:
        return bool(self.wins) and not self.wins[-1].closed

    def full(self) -> bool:
        return self._projected_used() + self.W > self.P

    def allow_a(self, m_ms: int) -> int:
        S = self.cfg.session_target_ms
        num = self.cfg.tier_a_burst_pm * S + (1000 - self.cfg.tier_a_burst_pm) * m_ms
        den = 1000 * S
        if num >= den:
            return self.P
        return self.P * num // den

    def allow_b(self, m_ms: int) -> int:
        S = self.cfg.session_target_ms
        cap_pm = 1000 - self.cfg.tier_b_reserve_pm
        num = self.cfg.tier_b_burst_pm * S + (1000 - self.cfg.tier_b_burst_pm) * m_ms
        den = 1000 * S
        f = den if num >= den else num
        return self.P * cap_pm * f // (1000 * den)

    # --------------------------------------------------------------- private
    def _open_win(self):
        if self.wins and not self.wins[-1].closed:
            return self.wins[-1]
        return None

    def _t64(self, seq: int) -> int:
        newest = self.seq_next - 1
        d = (self.ring_t_lo[newest % RING_N] - self.ring_t_lo[seq % RING_N]) & U32
        return self.newest_t - d

    def _oldest_unwritten(self):
        for w in self.wins:
            if w.write_seq < w.end_excl:
                return w.write_seq
        return None

    def _projected_used(self) -> int:
        p = self.used
        for w in self.wins:
            if not w.begin_written:
                p += 1
            left = w.end_excl - w.write_seq if w.end_excl > w.write_seq else 0
            p += (left + SAMPLES_PER_PAGE - 1) // SAMPLES_PER_PAGE
            p += 1
        if self.trig:
            p += 1
        return p

    def _note(self, dec: int, eid: int) -> None:
        if _RANK.get(dec, 0) > _RANK.get(self.span_dec, 0):
            self.span_dec = dec
            self.span_eid = eid

    def _finalize_span(self) -> None:
        cfg = self.cfg
        self.span_active = False
        dec = self.span_dec or DEC_REFUSED_BELOW_TIER
        if dec in (DEC_REFUSED_BUDGET_A, DEC_REFUSED_BUDGET_B):
            self.refused_budget += 1
        if dec in (DEC_REFUSED_FULL, DEC_REFUSED_BUSY):
            self.refused_full += 1
        hint = (HINT_TIER_A if self.span_peak >= cfg.tier_a_g else
                HINT_TIER_B if self.span_peak >= cfg.tier_b_g else HINT_FLOOR)
        self._queue_trig((self.span_start, mg_from_g(self.span_peak), dec, hint,
                          self.span_eid, self._projected_used() & 0xFFFF))

    def _queue_trig(self, te: tuple) -> None:
        if len(self.trig) >= TRIG_PER_PAGE:
            self.trig_dropped += 1
            return
        self.trig.append(te)

    def _extend(self, w: Win, t: int) -> None:
        e = t + self.cfg.post_us
        cap = w.start_t + self.cfg.max_len_us
        if e >= cap:
            e = cap
            w.capped = True
        if e > w.end_t:
            w.end_t = e

    def _open_window(self, cause, t, s, mag, proj, m_ms) -> int:
        lo = self.next_unassigned
        if self.seq_next > RING_N and self.seq_next - RING_N > lo:
            lo = self.seq_next - RING_N
        first = s
        t_lo_s = self.ring_t_lo[s % RING_N]
        while first > lo:
            if self.ring_f[first % RING_N] & RF_GAP:
                break
            cand = first - 1
            if ((t_lo_s - self.ring_t_lo[cand % RING_N]) & U32) > self.cfg.pre_us:
                break
            first = cand
        self.event_counter = (self.event_counter + 1) & 0xFFFF
        w = Win(event_id=self.event_counter, cause=cause, start_seq=first, write_seq=first,
                end_excl=s + 1, start_t=self._t64(first), end_t=t, trig_t=t, trig_seq=s,
                trig_mg=mg_from_g(mag), used_at_open=proj & 0xFFFF,
                allow_a=self.allow_a(m_ms) & 0xFFFF, allow_b=self.allow_b(m_ms) & 0xFFFF,
                m_s=self.m_us // 1_000_000)
        w.last_written_t = w.start_t
        self._extend(w, t)
        if not self.wins:
            self.hold_wanted = True
        self.wins.append(w)
        self.next_unassigned = s + 1
        self.events_boot += 1
        return w.event_id

    def _close_win(self, w: Win, reason: int) -> None:
        w.closed = True
        w.close_reason = reason

    def _add_link(self, w: Win, link: dict) -> None:
        if len(w.links) < MAX_LINKS:
            w.links.append(link)
        elif w.links_overflow < 255:
            w.links_overflow += 1

    def _ring_overrun(self) -> None:
        self.ring_overrun += 1
        for w in self.wins:
            if w.end_excl > w.write_seq:
                w.n_dropped += w.end_excl - w.write_seq
                w.end_excl = w.write_seq
                w.closed = True
                w.close_reason = CLOSE_RING_OVERRUN

    def _apply_hold(self) -> None:
        if self.hold_wanted and not self.held:
            self.held = True
            self.hooks.wake_hold(True)

    def _release_hold(self) -> None:
        self.hold_wanted = False
        if self.held:
            self.held = False
            self.hooks.wake_hold(False)

    def _pop_win(self) -> None:
        self.wins.pop(0)
        if not self.wins:
            self._release_hold()

    def _write_page(self, page: bytes, w) -> int:
        a = self.hooks.now_us()
        r = self.hooks.write_page(page)
        b = self.hooks.now_us()
        if r < 0:
            return r
        self.used += 1
        if r == 0:
            self.write_fail += 1
        dt = b - a if b > a else 0
        dt &= U32
        self.max_write_us = max(self.max_write_us, dt)
        if dt > self.cfg.page_slack_us:
            self.over_slack += 1
        if w is not None:
            w.max_write_us = max(w.max_write_us, dt)
            if dt > self.cfg.page_slack_us and w.over_slack < 65535:
                w.over_slack += 1
        return r

    def _write_trig(self) -> bool:
        if self.used + 1 + 2 > self.total:
            self.trig_dropped += len(self.trig)
            self.trig = []
            return False
        page = encode_trig_page(boot_id=self.boot_id, page_seq=self.trig_page_seq & 0xFFFF,
                                sample_seq=self.seq_next & U32, entries=self.trig)
        r = self._write_page(page, None)
        if r < 0:
            return False
        self.trig_page_seq += 1
        self.trig = []
        return True

    def _write_one(self, flush: bool) -> bool:
        if len(self.trig) >= TRIG_PER_PAGE:
            return self._write_trig()
        if self.wins:
            w = self.wins[0]
            if not w.begin_written:
                if self.used + 2 > self.P:
                    if w.end_excl > w.write_seq:
                        w.n_dropped += w.end_excl - w.write_seq
                    w.end_excl = w.write_seq
                    self.refused_full += 1
                    if not w.closed:
                        self._close_win(w, CLOSE_REGION_FULL)
                    self._pop_win()
                    return False
                r = self._write_page(self._build_begin(w), w)
                if r < 0:
                    return False
                w.begin_written = True
                w.page_seq = 1
                return True
            if w.write_seq < w.end_excl:
                if self.used + 2 > self.P:
                    w.n_dropped += w.end_excl - w.write_seq
                    w.end_excl = w.write_seq
                    if not w.closed:
                        self._close_win(w, CLOSE_REGION_FULL)
                    else:
                        w.close_reason = CLOSE_REGION_FULL
                    return False
                avail = w.end_excl - w.write_seq
                if w.closed or avail >= SAMPLES_PER_PAGE or self._break_within(w, avail):
                    return self._write_samples(w)
            elif w.closed:
                if self.used + 1 > self.total:
                    self.write_fail += 1
                    self._pop_win()
                    return False
                r = self._write_page(self._build_end(w), w)
                if r < 0:
                    return False
                self._pop_win()
                return True
        if flush and self.trig:
            return self._write_trig()
        return False

    def _break_within(self, w: Win, avail: int) -> bool:
        for k in range(1, min(avail, SAMPLES_PER_PAGE)):
            seq = w.write_seq + k
            if self.ring_f[seq % RING_N] & RF_GAP:
                return True
            if self._t64(seq) - self._t64(seq - 1) > 65535:
                return True
        return False

    def _write_samples(self, w: Win) -> bool:
        first_is_break = bool(self.ring_f[w.write_seq % RING_N] & RF_GAP) or w.break_pending
        flags = FLAG_TIME_BREAK if (first_is_break and w.write_seq != w.start_seq) else 0
        n = 0
        brk = False
        prev = 0
        mask = 0
        while n < SAMPLES_PER_PAGE and w.write_seq + n < w.end_excl:
            seq = w.write_seq + n
            t = self._t64(seq)
            if n > 0 and ((self.ring_f[seq % RING_N] & RF_GAP) or t - prev > 65535):
                brk = True
                break
            if self.ring_f[seq % RING_N] & RF_GYRO_BAD:
                mask |= 1 << n
            prev = t
            n += 1
        samples = []
        prev = 0
        for i in range(n):
            seq = w.write_seq + i
            t = self._t64(seq)
            dt = 0 if i == 0 else (t - prev) & 0xFFFF
            samples.append((dt, self.ring_v[seq % RING_N]))
            prev = t
        page = encode_samples_page(boot_id=self.boot_id, event_id=w.event_id,
                                   page_seq=w.page_seq & 0xFFFF, sample_seq=w.write_seq & U32,
                                   t_first_us=self._t64(w.write_seq), samples=samples,
                                   flags=flags, gyro_bad_mask=mask)
        r = self._write_page(page, w)
        if r < 0:
            return False
        for i in range(n):
            seq = w.write_seq + i
            v = self.ring_v[seq % RING_N]
            t = self._t64(seq)
            for k in range(6):
                if v[k] in (32767, -32768) and w.clip[k] < 65535:
                    w.clip[k] += 1
            if (self.ring_f[seq % RING_N] & RF_GYRO_BAD) and w.gyro_bad < 65535:
                w.gyro_bad += 1
            if w.have_last:
                if v[:3] == w.last_v and w.dup < 65535:
                    w.dup += 1
                d = t - w.last_t
                if d > LATE_US and w.late < 65535:
                    w.late += 1
                if d > w.max_dt:
                    w.max_dt = d & U32
            w.have_last = True
            w.last_v = tuple(v[:3])
            w.last_t = t
            w.last_written_t = t
        if r == 1:
            w.n_written += n
        else:
            w.n_dropped += n
        w.write_seq += n
        w.break_pending = brk
        w.page_seq += 1
        return True

    def _build_begin(self, w: Win) -> bytes:
        p = w.prov
        return encode_begin_page(
            boot_id=self.boot_id, event_id=w.event_id, sample_seq=w.start_seq & U32,
            t_first_us=w.start_t, src=self.src, t0_us=self.t0_us, trigger_t_us=w.trig_t,
            trigger_sample_seq=w.trig_seq & U32, trigger_mag_mg=w.trig_mg, cause=w.cause,
            used_pages=w.used_at_open, allow_a=w.allow_a, allow_b=w.allow_b, m_s=w.m_s,
            pre_ms=self.cfg.pre_us // 1000, post_ms=self.cfg.post_us // 1000,
            max_ms=self.cfg.max_len_us // 1000, regs=p.regs, regs_ok=p.regs_ok,
            temp_raw=p.temp_raw, temp_ok=p.temp_ok, g_baseline=p.g_baseline,
            gyro_bias=p.gyro_bias, detector_state=p.detector_state,
            spin_lever_m=p.spin_lever_m, airtime_offset_s=p.airtime_offset_s,
            height_scale=p.height_scale)

    def _build_end(self, w: Win) -> bytes:
        ok, traw = self.hooks.read_temp()
        return encode_end_page(
            boot_id=self.boot_id, event_id=w.event_id, page_seq=w.page_seq & 0xFFFF,
            sample_seq=w.start_seq & U32, t_first_us=w.last_written_t,
            n_samples=w.n_written & U32, last_sample_seq=(w.end_excl - 1) & U32 if w.end_excl > 0 else 0,
            n_dropped=w.n_dropped & U32, close_reason=w.close_reason, clip=w.clip,
            gyro_bad=w.gyro_bad, dup_polls=w.dup, late_polls=w.late, max_dt_us=w.max_dt,
            temp_raw_end=traw if ok else 0, temp_ok=1 if ok else 0, links=w.links,
            links_overflow=w.links_overflow, max_page_write_us=w.max_write_us,
            pages_over_slack=w.over_slack)


# ------------------------------------------------- the harness, in Python

class _HarnessHooks(Hooks):
    """firmware/test/event_capture_harness.cpp's sink and fixed provenance."""

    def __init__(self, out: list[str]):
        self.out = out
        self.clock_us = 0
        self.cost_us = 0
        self.defer_n = 0
        self.fail_n = 0
        self.index = 0

    def write_page(self, page: bytes) -> int:
        if self.defer_n > 0:
            self.defer_n -= 1
            return -1
        self.clock_us += self.cost_us
        fail = self.fail_n > 0
        if fail:
            self.fail_n -= 1
        self.out.append(f"PAGE {self.index} {'fail' if fail else 'ok'} {page.hex()}")
        self.index += 1
        return 0 if fail else 1

    def provenance(self) -> Provenance:
        return Provenance(regs=bytes([0x54, 0x5C, 0x44, 0x02, 0x00, 0x00, 0x00]), regs_ok=1,
                          temp_raw=512, temp_ok=1, g_baseline=f32(1.029),
                          gyro_bias=(0.5, -0.25, 1.0), detector_state=0, spin_lever_m=0.0,
                          airtime_offset_s=f32(0.0192), height_scale=1.0)

    def read_temp(self):
        return (True, 768)

    def now_us(self) -> int:
        return self.clock_us


HARNESS_BOOT_ID = 0xA1B2C3D4
HARNESS_T0_US = 1_000_000
HARNESS_SRC = b"harness0"


def run_harness_script(script: str, cfg: Config | None = None) -> list[str]:
    """The harness's op language (see event_capture_harness.cpp), executed
    by the Python mirror. Returns the output lines the C++ harness prints."""
    out: list[str] = []
    hooks = _HarnessHooks(out)
    cap = Capture(cfg or load_config(), hooks, HARNESS_BOOT_ID, HARNESS_T0_US, HARNESS_SRC)
    cap.set_region(0, REGION_PAGES)
    cap.set_enabled(True)

    def stats():
        out.append(
            f"STAT used={cap.used} total={cap.total} open={int(cap.is_open())} "
            f"full={int(cap.full())} events_boot={cap.events_boot} crossings={cap.crossings} "
            f"refused_budget={cap.refused_budget} refused_full={cap.refused_full} "
            f"ring_overrun={cap.ring_overrun} write_fail={cap.write_fail} "
            f"dup_polls={cap.dup_polls} late_polls={cap.late_polls} "
            f"max_page_write_us={cap.max_write_us} pages_over_slack={cap.over_slack} "
            f"trig_dropped={cap.trig_dropped} links_lost={cap.links_lost} "
            f"pending={int(cap.pending())} m_us={cap.m_us} W={cap.W}")

    for line in script.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        p = line.split()
        op = p[0]
        if op == "R":
            cap.set_region(int(p[1]), int(p[2]))
        elif op == "E":
            cap.set_enabled(int(p[1]) != 0)
        elif op == "S":
            t = int(p[1])
            raw = [int(x) for x in p[2:8]]
            hooks.clock_us = t
            cap.note_poll(t, raw[:3])
            cap.on_sample(t, raw, int(p[8]) != 0, float(p[9]))
            cap.service(int(p[10]) != 0)
        elif op == "P":
            t = int(p[1])
            hooks.clock_us = t
            cap.note_poll(t, [int(x) for x in p[2:5]])
        elif op == "V":
            cap.service(int(p[1]) != 0)
        elif op == "J":
            cap.link_jump(int(p[1]), int(p[2]), float(p[3]), float(p[4]), float(p[5]))
        elif op == "C":
            cap.close(int(p[1]))
        elif op == "D":
            out.append(f"DRAIN ok={int(cap.drain())}")
        elif op == "W":
            mode = p[1]
            n = int(p[2]) if len(p) > 2 else 0
            if mode == "ok":
                hooks.defer_n = 0
                hooks.fail_n = 0
            elif mode == "defer":
                hooks.defer_n = n
            elif mode == "fail":
                hooks.fail_n = n
            elif mode == "cost":
                hooks.cost_us = n
        elif op == "Q":
            stats()
        else:
            out.append(f"ERROR unknown_op={op}")
    stats()
    return out
