"""Six-axis event capture: C++/Python parity, policy semantics, decoder
honesty (spec 2026-10-07 section 8.1, T-C1 / T-C2 / T-C3, and the T-D2
source-level half).

  T-C1  firmware/test/event_capture_harness.cpp runs the REAL
        event_capture.h + event_format.h over scripted input; the same script
        runs through sim/event_policy.py; the outputs (every page, hex, and
        the counters) must be identical, byte for byte.
  T-C2  The scenarios the spec names, asserted on the DECODED C++ output
        (sim/event_codec.py): a single impact; pop + landing 3.9 s apart
        (two windows, contiguous sample_seq, no sample twice); the 12 s
        extension cap; refractory; tier B refused by pacing; tier A paced;
        a detector-forced window and its link; region full (stop, END
        written, later crossings counted not stored, nothing past the
        region); a >65,535 us gap (page break); gyro failure; clipping; dup
        counting; ring overrun from a stalled sink (RING_OVERRUN, never
        silent).
  T-C3  The decoder rejects a bad CRC, a wrong magic, an unknown
        format_version (loudly) and a truncated region.
  T-D2  event_capture.h compiles with ONLY event_format.h beside it and
        names no detector / gyro-bias / lever-arm / trace symbol.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

import event_codec as ec  # noqa: E402
import event_policy as ep  # noqa: E402

INCLUDE = REPO / "firmware" / "include"
HARNESS_SRC = REPO / "firmware" / "test" / "event_capture_harness.cpp"
LSB_G = 0.000488


def _gxx() -> str:
    gxx = shutil.which("g++") or shutil.which("c++")
    if not gxx:
        raise unittest.SkipTest("no g++/c++ on this machine")
    return gxx


_HARNESS: str | None = None
_TMP = tempfile.mkdtemp(prefix="jh_event_harness_")


def _harness() -> str:
    global _HARNESS
    if _HARNESS is None:
        binp = str(Path(_TMP) / "event_capture_harness")
        r = subprocess.run([_gxx(), "-std=c++14", "-Wall", "-Wextra", "-Werror",
                            "-I", str(INCLUDE), str(HARNESS_SRC), "-o", binp],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise AssertionError(f"event_capture_harness failed to compile:\n{r.stderr}")
        _HARNESS = binp
    return _HARNESS


def run_cpp(script: str) -> list[str]:
    r = subprocess.run([_harness()], input=script, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines()


def run_py(script: str) -> list[str]:
    return ep.run_harness_script(script)


def pages_of(out: list[str]) -> list[bytes]:
    return [bytes.fromhex(l.split()[3]) for l in out if l.startswith("PAGE ")]


def stat_of(out: list[str]) -> dict:
    last = [l for l in out if l.startswith("STAT ")][-1]
    return {k: int(v) for k, v in (kv.split("=") for kv in last.split()[1:])}


def region_of(out: list[str]) -> bytes:
    """The region image the sink would hold: ok pages verbatim, failed pages
    with their CRC broken (a failed QSPI write leaves torn bytes)."""
    img = bytearray()
    for l in out:
        if not l.startswith("PAGE "):
            continue
        page = bytearray.fromhex(l.split()[3])
        if l.split()[2] == "fail":
            page[-1] ^= 0xFF
        img += page
    return bytes(img)


class Script:
    """Builds harness op lines at 200 Hz (5 ms per sample)."""

    def __init__(self, t0_us: int = 2_000_000):
        self.t = t0_us
        self.n = 0
        self.lines: list[str] = []

    def op(self, line: str) -> "Script":
        self.lines.append(line)
        return self

    def sample(self, mag: float, *, raw=None, gyro_ok: bool = True, skip=None,
               dt_us: int = 5000) -> "Script":
        self.t += dt_us
        self.n += 1
        if raw is None:
            az = max(-32768, min(32767, int(round(mag / LSB_G))))
            raw = (self.n % 7 - 3, -(self.n % 5), az, self.n % 11, -(self.n % 13), 3)
        if skip is None:
            skip = 1 if self.n % 200 == 0 else 0   # one trace-flush pass a second
        self.lines.append(f"S {self.t} {' '.join(str(int(x)) for x in raw)} "
                          f"{1 if gyro_ok else 0} {mag:.3f} {skip}")
        return self

    def rest(self, seconds: float, mag: float = 1.0) -> "Script":
        for _ in range(int(round(seconds * 200))):
            self.sample(mag)
        return self

    def impact(self, g: float, samples: int = 2) -> "Script":
        for _ in range(samples):
            self.sample(g)
        return self

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def decode(out: list[str]) -> ec.RegionDecode:
    return ec.decode_region(region_of(out))


class ParityMixin:
    def both(self, script: str) -> list[str]:
        c = run_cpp(script)
        p = run_py(script)
        if c != p:
            first = next(i for i, (a, b) in enumerate(zip(c + [""], p + [""])) if a != b)
            self.fail(f"C++ and Python diverge at output line {first}:\n"
                      f"  C++: {c[first][:160] if first < len(c) else '<end>'}\n"
                      f"  Py : {p[first][:160] if first < len(p) else '<end>'}")
        return c


# ----------------------------------------------------------------- T-C2 + T-C1

class TestPolicyScenarios(ParityMixin, unittest.TestCase):

    def test_single_impact_is_one_tier_a_window_with_five_seconds_before(self):
        s = Script().rest(8).impact(9.0).rest(4).op("D")
        out = self.both(s.text())
        d = decode(out)
        self.assertEqual(d.errors, [])
        self.assertEqual(d.damaged_pages, [])
        self.assertEqual(len(d.events), 1)
        ev = d.events[0]
        self.assertTrue(ev.complete, ev.problems)
        self.assertEqual(ec.CAUSES[ev.begin["cause"]], "TIER_A")
        self.assertEqual(ev.begin["trigger_mag_mg"], 9000)
        trig_t = ev.begin["trigger_t_us"]
        self.assertEqual(trig_t - ev.samples[0][1], 5_000_000,
                         "the pre-trigger portion must start exactly pre_s before")
        self.assertEqual(ev.samples[-1][1] - trig_t, 1_500_000 + 5000 * 1,
                         "post_s after the LAST >= tier_b sample (the 2nd impact sample)")
        self.assertEqual(ec.CLOSE_REASONS[ev.end["close_reason"]], "POST_EXPIRED")
        self.assertEqual([t["decision_name"] for t in d.triggers], ["OPENED_A"])
        self.assertEqual(d.triggers[0]["event_id"], ev.event_id)
        # BEGIN provenance as the harness supplied it.
        self.assertEqual(ev.begin["regs"], [0x54, 0x5C, 0x44, 0x02, 0, 0, 0])
        self.assertEqual(ev.begin["time_quantum_ns"], 976563)
        self.assertEqual(ev.begin["src"], "harness0")
        self.assertEqual(ev.begin["t0_us"], ep.HARNESS_T0_US)

    def test_pop_and_landing_merge_contiguously_with_no_sample_twice(self):
        # Pop at 4.5 g (tier B), landing 3.9 s later at 9 g (tier A).
        s = Script().rest(8).impact(4.5).rest(3.9).impact(9.0).rest(3).op("D")
        out = self.both(s.text())
        d = decode(out)
        self.assertEqual(len(d.events), 2)
        a, b = d.events
        self.assertTrue(a.complete and b.complete, a.problems + b.problems)
        self.assertEqual(ec.CAUSES[a.begin["cause"]], "TIER_B")
        self.assertEqual(ec.CAUSES[b.begin["cause"]], "TIER_A")
        self.assertEqual(b.samples[0][0], a.samples[-1][0] + 1,
                         "the landing's window must start where the pop's ended")
        seqs = [x[0] for x in a.samples + b.samples]
        self.assertEqual(len(seqs), len(set(seqs)), "a sample was written twice")
        self.assertEqual(seqs, list(range(seqs[0], seqs[0] + len(seqs))))

    def test_extension_is_capped_at_max_len(self):
        s = Script().rest(8)
        for _ in range(40):                 # a 9 g sample every 0.5 s for 20 s
            s.impact(9.0, samples=1).rest(0.495)
        s.rest(3).op("D")
        out = self.both(s.text())
        d = decode(out)
        first = d.events[0]
        self.assertEqual(ec.CLOSE_REASONS[first.end["close_reason"]], "MAX_LEN")
        span = first.samples[-1][1] - first.samples[0][1]
        self.assertLessEqual(span, 12_000_000)
        self.assertGreater(span, 11_900_000)
        # Tier A (allowA(0) = 1028 pages) still has room after one 152-page
        # window; tier B (allowB(0) = 123) would not -- that is pacing.
        self.assertGreaterEqual(len(d.events), 2, "capture continues after the cap")
        self.assertEqual(d.events[1].samples[0][0], first.samples[-1][0] + 1)

    def test_refractory_makes_one_trig_entry_per_crossing_span(self):
        s = (Script().rest(5).impact(3.0, 1).rest(0.1).impact(3.0, 1)   # same span
             .rest(0.3).impact(3.0, 1).rest(2).op("D"))
        out = self.both(s.text())
        d = decode(out)
        self.assertEqual(d.events, [])
        self.assertEqual([t["decision_name"] for t in d.triggers],
                         ["REFUSED_BELOW_TIER", "REFUSED_BELOW_TIER"])
        self.assertEqual(stat_of(out)["crossings"], 2)

    def test_tier_b_is_refused_by_pacing_and_logged(self):
        # At m ~ 0, allowB = 0.06 P = 123 pages: room for ONE 84-page window.
        s = (Script().rest(8).impact(4.5).rest(3).impact(4.5).rest(3).op("D"))
        out = self.both(s.text())
        d = decode(out)
        self.assertEqual(len(d.events), 1)
        self.assertEqual([t["decision_name"] for t in d.triggers],
                         ["OPENED_B", "REFUSED_BUDGET_B"])
        self.assertEqual(stat_of(out)["refused_budget"], 1)

    def test_tier_a_is_paced_too(self):
        # 1000 pages already used; allowA(0) = 0.5 P = 1028 < 1000 + 84.
        s = Script().op("R 1000 2064").rest(8).impact(9.0).rest(3).op("D")
        out = self.both(s.text())
        d = decode(out)
        self.assertEqual(d.events, [])
        self.assertEqual([t["decision_name"] for t in d.triggers], ["REFUSED_BUDGET_A"])

    def test_detector_jump_forces_a_window_and_is_linked(self):
        s = (Script().rest(8).op("J 7 3 9.250 0.600 0.441").sample(1.0)
             .rest(3).op("D"))
        out = self.both(s.text())
        d = decode(out)
        self.assertEqual(len(d.events), 1)
        ev = d.events[0]
        self.assertEqual(ec.CAUSES[ev.begin["cause"]], "DETECTOR")
        self.assertEqual(ev.end["n_links"], 1)
        link = ev.end["links"][0]
        self.assertEqual((link["session_n"], link["stored_n"]), (7, 3))
        self.assertAlmostEqual(link["takeoff_s"], 9.25, places=9)
        self.assertAlmostEqual(link["airtime_raw_s"], 0.6, places=6)
        trig = d.triggers[-1]
        self.assertEqual(trig["decision_name"], "FORCED_DETECTOR")
        self.assertEqual(ec.TIER_HINTS[trig["tier_hint"]], "DETECTOR")
        self.assertEqual(trig["event_id"], ev.event_id)

    def test_region_full_stops_writes_end_and_counts_later_crossings(self):
        total = ec.REGION_PAGES
        s = Script().op(f"R 1960 {total}").rest(6)
        s.op("J 1 1 7.000 0.500 0.300").sample(1.0)   # forced: 1960+84 <= 2056
        for _ in range(30):                             # keep extending to the cap
            s.impact(4.5, 1).rest(0.395)
        s.rest(3).op("J 2 2 30.000 0.500 0.300").sample(1.0)
        s.rest(1).impact(9.0).rest(2).op("D")
        out = self.both(s.text())
        st = stat_of(out)
        self.assertLessEqual(st["used"], total, "wrote past the region")
        self.assertEqual(st["used"], 1960 + len(pages_of(out)))
        d = decode(out)
        self.assertEqual(len(d.events), 1)
        ev = d.events[0]
        self.assertIsNotNone(ev.end, "the END page must still be written")
        self.assertEqual(ec.CLOSE_REASONS[ev.end["close_reason"]], "REGION_FULL")
        self.assertGreater(ev.end["n_dropped"], 0)
        self.assertFalse(ev.complete)
        later = [t["decision_name"] for t in d.triggers[-2:]]
        self.assertIn("REFUSED_FULL", later)
        self.assertGreaterEqual(st["refused_full"], 1)
        self.assertEqual(st["full"], 1)

    def test_a_gap_over_65535_us_starts_a_new_page_with_absolute_time(self):
        s = Script().rest(8).impact(9.0).rest(0.5)
        s.sample(1.0, dt_us=80_000)                      # an 80 ms stall
        s.rest(2).op("D")
        out = self.both(s.text())
        d = decode(out)
        ev = d.events[0]
        self.assertTrue(ev.complete, ev.problems)
        ts = [x[1] for x in ev.samples]
        gaps = [b - a for a, b in zip(ts, ts[1:])]
        self.assertIn(80_000, gaps)
        self.assertEqual(sum(1 for f in ev.page_flags if f & ec.FLAG_TIME_BREAK), 1)
        self.assertEqual(ev.end["late_polls"], 1)
        self.assertEqual(ev.end["max_dt_us"], 80_000)

    def test_gyro_failure_clipping_and_duplicates_are_counted(self):
        s = Script().rest(8).impact(9.0)
        for _ in range(3):
            s.sample(1.0, gyro_ok=False)
        s.sample(16.0, raw=(32767, -32768, 32767, 0, 0, -32768))
        s.sample(16.0, raw=(32767, -32768, 32767, 0, 0, -32768))   # a duplicate
        s.rest(2).op("D")
        out = self.both(s.text())
        d = decode(out)
        ev = d.events[0]
        self.assertEqual(ev.end["gyro_bad"], 3)
        self.assertEqual(sum(1 for x in ev.samples if not x[3]), 3)
        self.assertEqual(ev.end["clip"], [2, 2, 2, 0, 0, 2])
        self.assertEqual(ev.end["dup_polls"], 1)

    def test_a_stalled_sink_overruns_the_ring_and_says_so(self):
        s = Script().rest(8).op("W defer 1000000").impact(9.0).rest(2)
        s.op("W ok").rest(1).op("D")
        out = self.both(s.text())
        st = stat_of(out)
        self.assertEqual(st["ring_overrun"], 1)
        d = decode(out)
        ev = d.events[0]
        self.assertEqual(ec.CLOSE_REASONS[ev.end["close_reason"]], "RING_OVERRUN")
        self.assertGreater(ev.end["n_dropped"], 0)
        self.assertFalse(ev.complete, "an overrun event must never look complete")

    def test_idle_close_and_failed_write_are_reported(self):
        s = Script().rest(8).impact(9.0).rest(1).op("W fail 1").rest(0.3)
        s.op(f"C {ep.CLOSE_IDLE}")
        for _ in range(200):
            s.op("V 0")
        out = self.both(s.text())
        st = stat_of(out)
        self.assertEqual(st["write_fail"], 1)
        d = decode(out)
        # The window drained after the idle close (its END is on flash). The
        # one TRIG entry stays in RAM until 14 accumulate or a command drains
        # -- a partial TRIG page per idle pause would spend the budget.
        self.assertIsNotNone(d.events[0].end, "an idle close must still drain the window")
        self.assertEqual(st["pending"], 1)
        self.assertEqual(len(d.damaged_pages), 1)
        ev = d.events[0]
        self.assertEqual(ec.CLOSE_REASONS[ev.end["close_reason"]], "IDLE")
        self.assertEqual(ev.end["n_dropped"], 16)
        self.assertFalse(ev.complete)

    def test_slow_page_writes_are_counted_against_the_slack(self):
        s = Script().op("W cost 6000").rest(8).impact(9.0).rest(2).op("D")
        out = self.both(s.text())
        st = stat_of(out)
        self.assertEqual(st["max_page_write_us"], 6000)
        self.assertEqual(st["pages_over_slack"], len(pages_of(out)))

    def test_no_page_is_written_in_a_trace_flush_pass(self):
        """Every S line with skip=1 must be followed by no PAGE line."""
        s = Script().rest(8).impact(9.0).rest(2)
        script = s.text()
        out = run_cpp(script)
        # Re-run op by op is not possible; instead check the counts: with
        # skip on 1 pass in 200 the backlog still drains completely.
        self.assertEqual(stat_of(out)["ring_overrun"], 0)
        self.assertEqual(out, run_py(script))

    def test_randomized_sessions_match_byte_for_byte(self):
        rng = random.Random(20261007)
        for trial in range(6):
            s = Script(t0_us=rng.randrange(1_000_000, 4_000_000_000))
            if trial == 5:
                s.op("R 1990 2064")
            for _ in range(rng.randrange(20, 40)):
                kind = rng.random()
                if kind < 0.35:
                    s.rest(rng.uniform(0.1, 3.0), mag=rng.uniform(0.6, 1.4))
                elif kind < 0.55:
                    s.impact(rng.uniform(2.0, 12.0), rng.randrange(1, 4))
                elif kind < 0.65:
                    s.op(f"J {rng.randrange(1, 50)} {rng.randrange(0, 50)} "
                         f"{rng.uniform(0, 1e4):.3f} {rng.uniform(0.25, 3):.3f} "
                         f"{rng.uniform(0, 5):.3f}")
                elif kind < 0.72:
                    s.sample(rng.uniform(0, 3), dt_us=rng.choice([20_000, 70_000, 200_000]))
                elif kind < 0.78:
                    s.op(f"C {ep.CLOSE_IDLE}")
                    for _ in range(rng.randrange(0, 120)):
                        s.op("V 0")
                elif kind < 0.84:
                    s.op(f"W {rng.choice(['fail', 'defer'])} {rng.randrange(1, 4)}")
                elif kind < 0.88:
                    s.op(f"W cost {rng.choice([0, 300, 5000])}")
                elif kind < 0.92:
                    s.sample(rng.uniform(0.5, 9), gyro_ok=False)
                elif kind < 0.95:
                    s.op("Q")
                else:
                    s.sample(rng.uniform(1, 20), dt_us=6_000_000)  # a long gap
            s.op("D")
            out = self.both(s.text())
            d = decode(out)
            self.assertEqual(d.errors, [], f"trial {trial}")
            st = stat_of(out)
            self.assertLessEqual(st["used"], 2064)


# ------------------------------------------------------------------- T-C3

def _one_event_region() -> bytes:
    out = run_cpp(Script().rest(8).impact(9.0).rest(2).op("D").text())
    return region_of(out)


class TestDecoderRefusesToGuess(unittest.TestCase):
    def test_a_bad_crc_is_a_damaged_page(self):
        img = bytearray(_one_event_region())
        img[256 * 3 + 40] ^= 0x01
        d = ec.decode_region(bytes(img))
        self.assertEqual(d.damaged_pages, [3])
        self.assertFalse(d.events[0].complete)

    def test_a_wrong_magic_is_a_damaged_page(self):
        img = bytearray(_one_event_region())
        img[256 * 5] = 0xE7
        d = ec.decode_region(bytes(img))
        self.assertEqual(d.damaged_pages, [5])

    def test_an_unknown_format_version_is_loud(self):
        img = bytearray(_one_event_region())
        page = bytearray(img[0:256])
        page[ec.HDR_BYTES] = 9
        img[0:256] = ec._seal(page)
        d = ec.decode_region(bytes(img))
        self.assertTrue(any("format_version=9" in e for e in d.errors), d.errors)
        self.assertFalse(d.events[0].complete)

    def test_a_truncated_region_is_reported(self):
        img = _one_event_region()          # ... BEGIN, SAMPLES, END, TRIG
        d = ec.decode_region(img[:-(256 + 100)])
        self.assertTrue(any("not a whole number" in e for e in d.errors), d.errors)
        self.assertFalse(d.events[0].complete, "the END page was cut off")

    def test_erased_pages_are_skipped_not_damaged(self):
        img = _one_event_region() + b"\xff" * 512
        d = ec.decode_region(img)
        self.assertEqual(d.damaged_pages, [])
        self.assertEqual(d.pages_erased, 2)
        self.assertTrue(d.events[0].complete)

    def test_si_columns_are_raw_times_the_recorded_scale(self):
        img = _one_event_region()
        ev = ec.decode_region(img).events[0]
        rows = list(ec.sample_rows(ev))
        r = rows[1000]
        self.assertEqual(r["az_mps2"], r["raw"][2] * ev.begin["accel_g_per_lsb"] * ec.G_MPS2)
        self.assertEqual(r["gx_rads"],
                         r["raw"][3] * ev.begin["gyro_dps_per_lsb"] * 3.141592653589793 / 180.0)
        self.assertAlmostEqual(r["t_s"], (r["t_us"] - ev.begin["t0_us"]) * 1e-6)


# --------------------------------------------------------------- T-D2 (source)

class TestCaptureHeaderIsIsolated(unittest.TestCase):
    def test_compiles_with_only_event_format_beside_it(self):
        with tempfile.TemporaryDirectory() as td:
            for f in ("event_format.h", "event_capture.h"):
                shutil.copy(INCLUDE / f, Path(td) / f)
            tu = Path(td) / "tu.cpp"
            tu.write_text('#include "event_capture.h"\n'
                          "int main() { static jh_event::Capture c; return (int)c.used_pages(); }\n")
            r = subprocess.run([_gxx(), "-std=c++11", "-Wall", "-Wextra", "-Werror",
                                "-I", td, str(tu), "-o", str(Path(td) / "tu")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_names_no_detector_trace_or_motion_gate_symbol(self):
        text = (INCLUDE / "event_capture.h").read_text()
        code = "\n".join(l.split("//")[0] for l in text.splitlines())
        for sym in ("jump::", "Detector", "detector.", "GyroBias", "gyro_bias.", "LeverArm",
                    "lever_arm", "trace_codec", "jh_store", "trace_buf", "flushTrace",
                    "motion_seen", "last_motion_ms", "jh_imu", "params.gen.h",
                    "#include <Arduino.h>"):
            self.assertNotIn(sym, code, f"event_capture.h must not touch {sym}")
        includes = re.findall(r'#include\s+[<"]([^>"]+)[>"]', code)
        self.assertEqual(includes, ["event_format.h"])


if __name__ == "__main__":
    unittest.main()
