"""Host-verification suite for firmware/src/platform/nrf52/jh_store.cpp — the
raw QSPI-flash region manager (superblock + jumps region + binary-trace
region) that, per firmware/SENSE_FIRST_BOOT.md, has never run against real
silicon (the board arrives within days of this test's writing). Its LOGIC
(superblock format/validate, boot-time append-point recovery, the fullness
cap, framed read-back, clear()) has nothing to do with the QSPI bus itself
and is fully host-testable once something plays the role of
Adafruit_SPIFlashBase/Adafruit_FlashTransport_QSPI — see
firmware/test/store_host/mock_flash.h for that mock (real erase/write/page/
sector semantics + fault injection) and firmware/test/store_host/
store_host_harness.cpp for the small scripted-command runner built on top of
it, both new, neither editing jh_store.cpp itself (confirmed by this file's
own build step compiling the real, unmodified source).

Mirrors tools/tests/test_trace_codec.py's pattern one level up: a g++-built
host harness driven by a scripted command list over stdin, output parsed
back out, run via ./tools/jump simtest or directly:
    python3 -m pytest tools/tests/test_store_host.py -q
    python3 -m pytest tools/tests/test_store_host.py -q -s   (see printed
        boot-scan-duration line for the near-full scenario)

A "reboot" is simply running the compiled harness again as a fresh OS
process pointed at the same JH_MOCK_FLASH_FILE backing file — jh_store.cpp's
state is ordinary C++ statics with no reset API, so a new process is the
only thing that genuinely reproduces "RAM cleared, flash unchanged," exactly
like a real power-cycle. See mock_flash.h's header comment for the full
rationale.

NOTABLE FINDINGS from building this suite (see the two tests named
`test_power_cut_can_permanently_stick_the_append_slot_until_clear` and
`test_trace_is_full_flag_can_under_report_at_the_tail` below for the
mechanics) — surfaced here, not fixed: jh_store.cpp does not re-erase a torn
write's slot before resuming appends after a reboot, and it never counts a
NEAR-exhausted trace region (less than one more block's worth of physical
room, but not literally zero) as "full." Neither is a mock artifact; both
reproduce against the real, unmodified jh_store.cpp source.

SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

import trace_codec  # noqa: E402  (path insert must come first)

STORE_HOST_DIR = REPO / "firmware" / "test" / "store_host"
JH_STORE_CPP = REPO / "firmware" / "src" / "platform" / "nrf52" / "jh_store.cpp"

# JH_LOG_HZ from config/params.json, mirrored the same way
# tools/tests/test_trace_codec.py states its own LOG_HZ explicitly rather
# than parsing params.gen.h.
LOG_HZ = 50

# ---------------------------------------------------------------------------
# jh_store.cpp's own private on-flash geometry (its file header: "on-disk
# representation is fully private" — everything below is this platform's own
# choice, NOT part of the jh_store.h contract). Mirrored here, deliberately,
# ONLY for the one white-box cross-language byte check
# (test_trace_bytes_matches_python_decode_of_raw_region below) that has no
# other way to locate the raw trace region inside the mock's backing file.
# Every other test in this suite goes through jh_store's public API only
# (via the harness) and never needs these. Source: jh_store.cpp's own
# top-of-file layout comment and its SECTOR_BYTES/SUPERBLOCK_BYTES/
# JUMPS_REGION_BYTES constants; also restated in SENSE_FIRST_BOOT.md item 19
# ("JUMPS_REGION_BYTES = 65536"). If jh_store.cpp's geometry ever changes,
# this constant must change with it.
SUPERBLOCK_BYTES = 4096
JUMPS_REGION_BYTES = 65536
JUMP_RECORD_BYTES = 32
TRACE_REGION_START = SUPERBLOCK_BYTES + JUMPS_REGION_BYTES  # 69632

# P25Q16H, matching flash_devices.h's real macro and mock_flash.h's P25Q16H
# shim (both (1 << 21)) — the chip jh_store.cpp always asks for by name.
CHIP_SIZE = 1 << 21
TRACE_REGION_BYTES = CHIP_SIZE - TRACE_REGION_START  # 2,027,520

# Process exit code mock_flash.cpp uses for a simulated power cut — mirrors
# mock_flash.h's mock_flash_test::kFaultExitCode (kept in sync by hand; a
# mismatch here would only ever make a fault-path assertion too strict, not
# silently pass, so this is safe to duplicate rather than parse out of the
# header).
FAULT_EXIT_CODE = 90


def _gxx() -> str:
    gxx = shutil.which("g++") or shutil.which("c++")
    if not gxx:
        raise unittest.SkipTest("no g++/c++ on this machine")
    return gxx


def _build_harness(tmpdir: str) -> str:
    binp = str(Path(tmpdir) / "store_host_harness")
    r = subprocess.run(
        [_gxx(), "-std=c++14", "-Wall", "-Wextra",
         "-I", str(STORE_HOST_DIR / "shim"),
         "-I", str(REPO / "firmware" / "include"),
         str(STORE_HOST_DIR / "mock_flash.cpp"),
         str(STORE_HOST_DIR / "store_host_harness.cpp"),
         str(JH_STORE_CPP),
         "-o", binp],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"store_host_harness failed to compile:\n{r.stderr}")
    return binp


# --------------------------------------------------------------- run/parse


@dataclasses.dataclass
class RunResult:
    returncode: int
    events: list[dict]       # one dict per KEY/ANNOUNCE line, in order
    read_alls: list[str]     # one entry per READ_ALL command, in order
    raw_stdout: str


def _extract_read_alls(text: str) -> tuple[list[str], str]:
    """Pulls every READ_ALL payload out of the harness's raw stdout (see
    store_host_harness.cpp's framing contract: payload sits between a
    "===READ_ALL_BEGIN===\\n" line and a following "\\n===READ_ALL_END===\\n"
    marker), returning (payloads_in_order, leftover_text_with_them_removed)
    so plain KEY=VALUE line parsing can run on what's left without tripping
    over embedded CSV newlines."""
    begin_tag = "===READ_ALL_BEGIN===\n"
    end_tag = "\n===READ_ALL_END===\n"
    payloads: list[str] = []
    out_parts: list[str] = []
    pos = 0
    while True:
        b = text.find(begin_tag, pos)
        if b == -1:
            out_parts.append(text[pos:])
            break
        out_parts.append(text[pos:b])
        payload_start = b + len(begin_tag)
        e = text.find(end_tag, payload_start)
        assert e != -1, "unterminated READ_ALL block in harness output"
        payloads.append(text[payload_start:e])
        pos = e + len(end_tag)
    return payloads, "".join(out_parts)


def _parse_kv_lines(text: str) -> list[dict]:
    events: list[dict] = []
    for line in text.splitlines():
        if not line:
            continue
        if line.startswith("ANNOUNCE:"):
            events.append({"tag": "ANNOUNCE", "text": line[len("ANNOUNCE:"):]})
            continue
        parts = line.split()
        if not parts:
            continue
        kv: dict = {"tag": parts[0]}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
                kv[k] = v
        events.append(kv)
    return events


def run_harness(binary: str, commands: list[str], backing: Path | None = None) -> RunResult:
    """Runs one scripted session against the harness. `backing`, if given,
    is the flash image file this session's mock chip persists to — pass the
    SAME path across two calls to simulate a reboot (fresh OS process, same
    flash contents); omit it for a single-shot, transient (never-persisted)
    chip."""
    env = os.environ.copy()
    if backing is not None:
        env["JH_MOCK_FLASH_FILE"] = str(backing)
    else:
        env.pop("JH_MOCK_FLASH_FILE", None)
    script = "\n".join(commands) + "\n"
    r = subprocess.run([binary], input=script.encode(), capture_output=True, env=env)
    text = r.stdout.decode(errors="replace")
    read_alls, remaining = _extract_read_alls(text)
    events = _parse_kv_lines(remaining)
    return RunResult(r.returncode, events, read_alls, text)


def last(events: list[dict], tag: str) -> dict:
    matches = [e for e in events if e["tag"] == tag]
    assert matches, f"no {tag!r} line in harness output: {events!r}"
    return matches[-1]


def all_of(events: list[dict], tag: str) -> list[dict]:
    return [e for e in events if e["tag"] == tag]


# ------------------------------------------------------------- test values


class TestStoreHost(unittest.TestCase):
    """One shared compiled harness for every test in this class (mirrors
    tools/tests/test_trace_codec.py's TestTraceCodecParity)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmpdir = tempfile.mkdtemp(prefix="store_host_test_")
        cls.harness = _build_harness(cls._tmpdir)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _backing(self, name: str) -> Path:
        """A fresh, not-yet-existing backing-file path for one test (or one
        step of a multi-reboot scenario) — a nonexistent path makes
        Adafruit_SPIFlashBase::begin() treat it as a genuinely blank chip,
        matching a real never-written QSPI part."""
        return Path(self._tmpdir) / name

    # ------------------------------------------------------------- basics

    def test_fresh_chip_format(self) -> None:
        """A brand-new (never-written) chip: init() must format it (the
        SAME announce lines main.cpp's setup() used to emit inline — see
        jh_store.h's init() doc comment), come up ok(), and start every
        counter at zero/empty. A second init() call in the SAME process
        (not a reboot) must NOT re-format — the superblock it just wrote
        should already validate."""
        backing = self._backing("fresh.bin")
        r = run_harness(self.harness, [
            "INIT",
            "OK",
            "FREE_BYTES",
            "JUMPS_SCAN",
            "TRACE_BYTES",
            "TRACE_IS_FULL",
            "INIT",  # same process, not a reboot — must skip re-formatting
        ], backing=backing)
        self.assertEqual(r.returncode, 0)

        announces = [e["text"] for e in all_of(r.events, "ANNOUNCE")]
        self.assertEqual(len(announces), 2, f"expected exactly one format's worth of "
                                             f"announce lines, got {announces!r}")
        self.assertIn("formatting storage", announces[0])
        self.assertIn("storage ready", announces[1])

        inits = all_of(r.events, "INIT")
        self.assertEqual(len(inits), 2)
        self.assertEqual(inits[0]["ok"], "1")
        self.assertEqual(inits[1]["ok"], "1")

        self.assertEqual(last(r.events, "OK")["ok"], "1")
        self.assertEqual(int(last(r.events, "FREE_BYTES")["n"]),
                          JUMPS_REGION_BYTES + TRACE_REGION_BYTES)
        scan = last(r.events, "JUMPS_SCAN")
        self.assertEqual(int(scan["count"]), 0)
        self.assertAlmostEqual(float(scan["best_m"]), 0.0, places=3)
        self.assertEqual(int(last(r.events, "TRACE_BYTES")["n"]), 0)
        self.assertEqual(last(r.events, "TRACE_IS_FULL")["full"], "0")

    # -------------------------------------------------------------- reboot

    def test_reboot_continuity_jumps_and_trace(self) -> None:
        """Append jumps + trace, force the trace tail flushed, then run the
        harness AGAIN as a fresh process over the same backing file
        (a simulated reboot) and confirm counts/bytes/append-point
        continuity — then append more and confirm a THIRD instance still
        sees everything from both prior generations."""
        backing = self._backing("reboot.bin")

        # Two trace blocks (floor(t) advances 0 -> 1, closing the first),
        # then a forced flush of the second so nothing is left sitting in
        # the in-progress encoder when this process exits (an open block is
        # correctly NOT durable — see test_power_cut_mid_write_jump_record_
        # ignored_on_reboot's sibling reasoning; forcing it out here is what
        # main.cpp's own `dump`/`trace` commands do, via open_read()).
        r1 = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 1 1.000 0.300 0.280 0.550",
            "JUMPS_APPEND 2 2.000 0.310 0.290 0.610",
            "TRACE_APPEND 0.020,1.001;0.040,1.002;0.060,0.998",
            "TRACE_APPEND 1.020,1.010",
            "OPEN_READ TRACE",
            "CLOSE_READ",
            "JUMPS_SCAN",
            "TRACE_BYTES",
            "FREE_BYTES",
        ], backing=backing)
        self.assertEqual(r1.returncode, 0)
        scan1 = last(r1.events, "JUMPS_SCAN")
        bytes1 = int(last(r1.events, "TRACE_BYTES")["n"])
        free1 = int(last(r1.events, "FREE_BYTES")["n"])
        self.assertEqual(int(scan1["count"]), 2)
        self.assertAlmostEqual(float(scan1["best_m"]), 0.610, places=3)
        # header(6) + block1(3 samples) + block2(1 sample), all flushed.
        expected_csv_bytes1 = 6 + 3 * len("0.020,1.001\n") + 1 * len("1.020,1.010\n")
        self.assertEqual(bytes1, expected_csv_bytes1)

        # --- reboot: fresh OS process, same backing file ---
        r2 = run_harness(self.harness, [
            "INIT",
            "JUMPS_SCAN",
            "TRACE_BYTES",
            "FREE_BYTES",
        ], backing=backing)
        self.assertEqual(r2.returncode, 0)
        self.assertEqual(all_of(r2.events, "ANNOUNCE"), [],
                         "a warm reboot over a valid superblock must not re-format")
        scan2 = last(r2.events, "JUMPS_SCAN")
        self.assertEqual(int(scan2["count"]), int(scan1["count"]))
        self.assertAlmostEqual(float(scan2["best_m"]), float(scan1["best_m"]), places=3)
        self.assertEqual(int(last(r2.events, "TRACE_BYTES")["n"]), bytes1)
        self.assertEqual(int(last(r2.events, "FREE_BYTES")["n"]), free1)

        # --- append more post-reboot; append-point continuity (no overwrite,
        # no gap) means count/free_bytes move by exactly one record's worth ---
        r3 = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 3 3.000 0.320 0.300 0.700",
            "TRACE_APPEND 5.000,2.000;5.020,2.001",
            "OPEN_READ TRACE",
            "CLOSE_READ",
            "JUMPS_SCAN",
            "TRACE_BYTES",
            "FREE_BYTES",
            "OPEN_READ JUMPS",
            "READ_ALL",
            "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(r3.returncode, 0)
        scan3 = last(r3.events, "JUMPS_SCAN")
        self.assertEqual(int(scan3["count"]), 3)
        self.assertAlmostEqual(float(scan3["best_m"]), 0.700, places=3)
        bytes3 = int(last(r3.events, "TRACE_BYTES")["n"])
        self.assertEqual(bytes3, bytes1 + 2 * len("5.000,2.000\n"))
        free3 = int(last(r3.events, "FREE_BYTES")["n"])
        # free_bytes() tracks PHYSICAL flash consumed, not the CSV-equivalent
        # estimate — the whole point of binary trace v2 (see jh_store.cpp's
        # own DEVIATION comment) is that these two differ: one new 2-sample
        # binary block (trace_codec.block_size(2) bytes) plus one new 32-byte
        # jump record, regardless of how many CSV bytes that block decodes to.
        self.assertEqual(free1 - free3, JUMP_RECORD_BYTES + trace_codec.block_size(2))
        # The dump must show ALL THREE jumps in order, not just the new one —
        # proof the append landed after, not instead of, the first two.
        dump = r3.read_alls[0]
        rows = [ln for ln in dump.splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0].startswith("1,"))
        self.assertTrue(rows[1].startswith("2,"))
        self.assertTrue(rows[2].startswith("3,"))

        # --- a THIRD instance still sees both generations' worth ---
        r4 = run_harness(self.harness, ["INIT", "JUMPS_SCAN", "TRACE_BYTES"], backing=backing)
        self.assertEqual(int(last(r4.events, "JUMPS_SCAN")["count"]), 3)
        self.assertEqual(int(last(r4.events, "TRACE_BYTES")["n"]), bytes3)

    # ------------------------------------------------------- cap semantics

    def test_trace_region_cap_stops_trace_but_jumps_continue(self) -> None:
        """Marathon-fill the trace region to true physical exhaustion (~19.1k
        one-second blocks at JH_LOG_HZ=50 into a ~1.93 MB region — see the
        module-level TRACE_REGION_BYTES/block_size(LOG_HZ) arithmetic).
        jh_store.h's cap semantics: jumps_append() and trace_append() are
        independent — trace filling up must not stop jumps from appending.
        Also prints the boot-scan duration on a reboot over this near-full
        chip (SENSE_FIRST_BOOT.md item 11's own open question — order-of-
        magnitude host info only, never a hardware timing stand-in; run
        with `-s` to see the printed line).

        Also verifies (and documents, since it's a genuine finding from
        building this suite, not a mock artifact) that trace_is_full() can
        stay 0 even once the region has no room left for another block —
        see test_trace_is_full_flag_can_under_report_at_the_tail below for
        the focused version of this same finding. This test's OWN
        correctness bar is narrower and unaffected by it: no bytes are ever
        written past the region (checked via reboot-stable trace_bytes()),
        and jumps keep appending regardless of what the trace flag reports.
        """
        backing = self._backing("marathon.bin")

        exact_blocks = TRACE_REGION_BYTES // trace_codec.block_size(LOG_HZ)
        # A little headroom past the exact fit so the tail (a partial,
        # doesn't-fit block) is genuinely exercised, without looping
        # needlessly long past it.
        seconds_to_try = exact_blocks + 50

        r1 = run_harness(self.harness, [
            "INIT",
            f"TRACE_FILL {seconds_to_try} {LOG_HZ} 0.0 1.0",
            "TRACE_BYTES",
            "FREE_BYTES",
            "JUMPS_APPEND 1 1.0 0.30 0.28 0.50",
            "JUMPS_APPEND 2 2.0 0.30 0.28 0.90",
            "JUMPS_SCAN",
        ], backing=backing)
        self.assertEqual(r1.returncode, 0)

        free_after_fill = int(last(r1.events, "FREE_BYTES")["n"])
        # Physical trace headroom left is under one block's worth (the exact
        # arithmetic the region was sized against) — i.e. genuinely,
        # physically full for all practical purposes, whatever the flag says.
        trace_free = free_after_fill - JUMPS_REGION_BYTES
        self.assertGreaterEqual(trace_free, 0)
        self.assertLess(trace_free, trace_codec.block_size(LOG_HZ))

        # Jumps region is entirely unaffected by how full trace is.
        scan = last(r1.events, "JUMPS_SCAN")
        self.assertEqual(int(scan["count"]), 2)
        self.assertAlmostEqual(float(scan["best_m"]), 0.90, places=3)

        # No bytes are ever written past the region: a reboot's re-scan
        # reports the exact same trace_bytes() as this process's own optimistic
        # running total ONLY if every attempted block genuinely fit; assert
        # instead that the reboot's (truthful, re-derived-from-storage) value
        # is stable across a SECOND reboot — i.e. whatever got durably
        # written has stopped growing, which is the actual safety property,
        # independent of the flag/estimate discussion above.
        t_before_reboot = time.time()
        r2 = run_harness(self.harness, ["INIT", "TRACE_BYTES", "JUMPS_SCAN"], backing=backing)
        scan_duration_hint_s = time.time() - t_before_reboot
        self.assertEqual(r2.returncode, 0)
        bytes_after_reboot = int(last(r2.events, "TRACE_BYTES")["n"])
        self.assertEqual(int(last(r2.events, "JUMPS_SCAN")["count"]), 2)

        r3 = run_harness(self.harness, ["INIT", "TRACE_BYTES"], backing=backing)
        self.assertEqual(int(last(r3.events, "TRACE_BYTES")["n"]), bytes_after_reboot,
                         "durable trace byte count must be stable across repeated reboots "
                         "once nothing more is actually landing on flash")

        boot_scan_us = int(last(r2.events, "INIT")["elapsed_us"])
        print(f"\n[store_host] near-full ({exact_blocks} blocks, "
              f"{bytes_after_reboot:,} CSV-equivalent bytes) boot scan: "
              f"{boot_scan_us / 1000.0:.1f} ms host time "
              f"(wall-clock incl. process overhead: {scan_duration_hint_s * 1000:.1f} ms) "
              f"— order-of-magnitude only, see SENSE_FIRST_BOOT.md item 11 for the real check")
        # Sanity bound only (order-of-magnitude info, not a perf gate) —
        # catches a genuine hang/quadratic-blowup, nothing tighter.
        self.assertLess(boot_scan_us, 30_000_000)

    def test_trace_is_full_flag_can_under_report_at_the_tail(self) -> None:
        """FINDING (not fixed here — see this file's module docstring and
        the final report): trace_is_full() is only ever set true inside
        closeAndWriteBlock() at the exact moment a write makes
        s_trace_append_off >= s_trace_region_bytes. A block that instead
        arrives when LESS than one block's worth of room remains (the
        overwhelmingly likely case, since block sizes rarely divide the
        region evenly) is silently dropped WITHOUT crossing that threshold —
        so trace_is_full() can read false forever after, even though no
        further block will ever again fit. main.cpp's own gate
        (`if (fs_ok && !jh_store::trace_is_full() ...)`) would therefore
        keep calling trace_append() indefinitely, each call harmlessly (no
        corruption — closeAndWriteBlock()'s own bounds check still refuses
        to write past the region) but pointlessly wasting the call and the
        RAM-side trace_bytes() estimate would keep climbing forever, never
        matching what is truly, durably stored, and the "# trace log full"
        narration main.cpp prints on the true-crossing transition may never
        fire for a real session that happens to land in this (common) gap.
        """
        backing = self._backing("full_flag.bin")
        exact_blocks = TRACE_REGION_BYTES // trace_codec.block_size(LOG_HZ)
        remainder = TRACE_REGION_BYTES % trace_codec.block_size(LOG_HZ)
        self.assertGreater(remainder, 0, "this finding only manifests when the region "
                                         "doesn't divide evenly by the block size — true "
                                         "for JH_LOG_HZ=50 against this chip size today; "
                                         "if that ever changes this assertion will say so")

        r = run_harness(self.harness, [
            "INIT",
            f"TRACE_FILL {exact_blocks + 20} {LOG_HZ} 0.0 1.0",
            "TRACE_IS_FULL",
            "TRACE_BYTES",
            "FREE_BYTES",
        ], backing=backing)
        self.assertEqual(r.returncode, 0)

        trace_free = int(last(r.events, "FREE_BYTES")["n"]) - JUMPS_REGION_BYTES
        self.assertLess(trace_free, trace_codec.block_size(LOG_HZ),
                        "physically no room for another block")
        self.assertGreater(trace_free, 0, "but not exactly zero either")

        # The actual finding: the flag does not reflect that reality.
        self.assertEqual(last(r.events, "TRACE_IS_FULL")["full"], "0",
                         "trace_is_full() under-reports at the tail (see this test's "
                         "docstring) — if this ever starts asserting '1' instead, "
                         "jh_store.cpp's fullness bookkeeping changed and this whole "
                         "finding (and the accompanying report language) is stale")

        # And the RAM-side estimate keeps climbing on every further call in
        # THIS SAME session, each one silently a no-op on the durable side —
        # rerun with one more TRACE_APPEND appended to the same script so
        # the before/after comparison is within a single process/chip.
        r_plus_one = run_harness(self.harness, [
            "INIT",
            f"TRACE_FILL {exact_blocks + 20} {LOG_HZ} 0.0 1.0",
            "TRACE_BYTES",
            "TRACE_APPEND 999999.000,1.000",
            "TRACE_BYTES",
        ], backing=self._backing("full_flag_plus_one.bin"))
        bytes_events = all_of(r_plus_one.events, "TRACE_BYTES")
        self.assertGreater(int(bytes_events[1]["n"]), int(bytes_events[0]["n"]),
                           "the CSV-equivalent estimate keeps growing even once nothing "
                           "more can physically be written")

    # -------------------------------------------------------- clear/reuse

    def test_clear_then_reuse(self) -> None:
        """clear() resets every counter (byte count, cap-full flag,
        header-written flags — jh_store.h's own doc comment) unconditionally,
        and the region is fully reusable afterward — appends start again
        from byte/record zero, not wherever the previous session left off."""
        backing = self._backing("clear.bin")
        r1 = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 1 1.0 0.3 0.28 0.5",
            "JUMPS_APPEND 2 2.0 0.3 0.28 0.6",
            "TRACE_APPEND 0.020,1.001;0.040,1.002",
            "OPEN_READ TRACE", "CLOSE_READ",
            "CLEAR",
            "JUMPS_SCAN",
            "TRACE_BYTES",
            "TRACE_IS_FULL",
            "FREE_BYTES",
            "JUMPS_APPEND 1 10.0 0.3 0.28 0.42",
            "TRACE_APPEND 0.020,3.000",
            "OPEN_READ TRACE", "CLOSE_READ",
            "JUMPS_SCAN",
            "TRACE_BYTES",
            "OPEN_READ JUMPS", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(r1.returncode, 0)

        scans = all_of(r1.events, "JUMPS_SCAN")
        self.assertEqual(int(scans[0]["count"]), 0, "clear() must zero the jump count")
        trace_bytes_events = all_of(r1.events, "TRACE_BYTES")
        self.assertEqual(int(trace_bytes_events[0]["n"]), 0, "clear() must zero trace bytes")
        self.assertEqual(last(r1.events, "TRACE_IS_FULL")["full"], "0")
        self.assertEqual(int(last(r1.events, "FREE_BYTES")["n"]),
                         JUMPS_REGION_BYTES + TRACE_REGION_BYTES,
                         "clear() must give back all the space the pre-clear session used")

        self.assertEqual(int(scans[1]["count"]), 1, "reused region starts counting from zero")
        self.assertEqual(int(trace_bytes_events[1]["n"]), 6 + len("0.020,3.000\n"))

        dump = r1.read_alls[0]
        rows = [ln for ln in dump.splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(len(rows), 1, "no trace of the pre-clear jumps should survive")
        self.assertTrue(rows[0].startswith("1,10.000,"))

        # And clear() survives a reboot too (it isn't just an in-RAM reset).
        r2 = run_harness(self.harness, ["INIT", "JUMPS_SCAN", "TRACE_BYTES"], backing=backing)
        self.assertEqual(int(last(r2.events, "JUMPS_SCAN")["count"]), 1)
        self.assertEqual(int(last(r2.events, "TRACE_BYTES")["n"]), 6 + len("0.020,3.000\n"))

    # --------------------------------------------------------- power loss

    def test_power_cut_mid_write_jump_record_ignored_on_reboot(self) -> None:
        """The literal scenario asked for: a write is cut after N bytes
        (simulating power loss mid-page), and the partial record must be
        ignored on the next boot — not counted, not surfaced by a dump —
        while the append offset correctly rewinds to right before it (not
        after), so the NEXT attempted append lands there rather than past
        a phantom record."""
        backing = self._backing("power_cut_jumps.bin")

        r1 = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 1 1.000 0.300 0.280 0.550",
            "JUMPS_SCAN",
            # Cut the SECOND record's write after 12 of its 32 bytes — deep
            # enough to land inside real payload (past `n`, into
            # takeoff_s/airtime_raw_s), short of the crc byte (offset 20) —
            # a genuine "mid-page, not at a field boundary" tear.
            "FAULT_AFTER 12",
            "JUMPS_APPEND 2 2.000 0.300 0.280 0.999",
        ], backing=backing)
        # The fault must actually have fired — otherwise this test would
        # trivially "pass" having exercised nothing.
        self.assertEqual(r1.returncode, FAULT_EXIT_CODE,
                         "expected the armed fault to terminate the process")
        self.assertEqual(int(last(r1.events, "JUMPS_SCAN")["count"]), 1,
                         "record #1 (clean, pre-fault) must already be counted")

        # Reboot: the torn record #2 must be invisible to both the cached
        # scan AND an actual dump.
        r2 = run_harness(self.harness, [
            "INIT",
            "JUMPS_SCAN",
            "OPEN_READ JUMPS", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(r2.returncode, 0)
        self.assertEqual(int(last(r2.events, "JUMPS_SCAN")["count"]), 1,
                         "torn record must not be counted after reboot")
        rows = [ln for ln in r2.read_alls[0].splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(len(rows), 1, "torn record must not be surfaced by a dump either")
        self.assertTrue(rows[0].startswith("1,1.000,"))

    def test_power_cut_can_permanently_stick_the_append_slot_until_clear(self) -> None:
        """FINDING (not fixed here — see this file's module docstring and
        the final report): jh_store.cpp resumes appending at the exact
        flash offset a torn write left behind WITHOUT re-erasing it first.
        Real NOR flash writes can only clear bits (never set one back to 1
        without an erase — modeled faithfully in mock_flash.h), so the next
        write attempt at that same offset ANDs its intended bytes against
        whatever the torn attempt already left there. If that corrupts even
        one bit the new record's own stored CRC (computed over the
        INTENDED bytes before the corrupting AND) will no longer match the
        ACTUAL stored bytes — so the corruption is always safely caught by
        the very same CRC check (nothing corrupt is ever surfaced as valid)
        — but the practical result is that the append offset never advances
        past that dead slot, so it can keep failing forever, silently
        losing every jump logged after a mid-write power cut, until clear()
        is called. This reproduces on the FIRST retry with the values below
        (not a contrived adversarial search); whether any given real-world
        retry collides is data-dependent, but this demonstrates it is a
        real, easily-reached outcome rather than a theoretical one.
        """
        backing = self._backing("stuck_slot.bin")

        r1 = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 1 1.000 0.300 0.280 0.550",
            "FAULT_AFTER 12",
            "JUMPS_APPEND 2 2.000 0.300 0.280 0.999",
        ], backing=backing)
        self.assertEqual(r1.returncode, FAULT_EXIT_CODE)

        # Reboot once: confirm the clean baseline (count=1), then retry a
        # DIFFERENT record at the same (now-dead) offset.
        r2 = run_harness(self.harness, [
            "INIT",
            "JUMPS_SCAN",
            "JUMPS_APPEND 99 3.000 0.300 0.280 0.650",
            "JUMPS_SCAN",  # optimistic in-RAM count — bumps regardless of validity
            "OPEN_READ JUMPS", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(r2.returncode, 0)
        scans = all_of(r2.events, "JUMPS_SCAN")
        self.assertEqual(int(scans[0]["count"]), 1)
        optimistic_count = int(scans[1]["count"])
        rows = [ln for ln in r2.read_alls[0].splitlines() if ln and not ln.startswith("n,")]
        # The finding: the in-RAM count optimistically advanced, but the
        # dump — which re-validates every record's CRC — did not.
        self.assertEqual(optimistic_count, 2, "jumps_append() advances its cached count "
                         "unconditionally; this is the optimistic side of the finding")
        self.assertEqual(len(rows), 1, "the retry did not actually produce a valid, "
                         "readable second record — the dead-slot finding")

        # Reboot AGAIN: the discrepancy self-corrects on the *cached* side
        # (a fresh scan re-derives truth from storage)... but the slot is
        # still dead, so it happens again, forever, without clear().
        r3 = run_harness(self.harness, [
            "INIT",
            "JUMPS_SCAN",
            "JUMPS_APPEND 100 4.000 0.300 0.280 0.700",
            "OPEN_READ JUMPS", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(int(last(r3.events, "JUMPS_SCAN")["count"]), 1,
                         "a fresh boot re-derives the true (still just 1 valid record) count")
        rows3 = [ln for ln in r3.read_alls[0].splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(len(rows3), 1, "the slot is still dead on a second retry, "
                         "without an intervening clear()")

        # clear() is the documented, confirmed way out.
        r4 = run_harness(self.harness, [
            "INIT",
            "CLEAR",
            "JUMPS_APPEND 1 5.000 0.300 0.280 0.800",
            "JUMPS_SCAN",
            "OPEN_READ JUMPS", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(int(last(r4.events, "JUMPS_SCAN")["count"]), 1)
        rows4 = [ln for ln in r4.read_alls[0].splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(len(rows4), 1)
        self.assertTrue(rows4[0].startswith("1,5.000,"), "clear() fully un-sticks the region")

    # --------------------------------------------------------- corruption

    def test_crc_corrupted_trace_block_skipped_without_derailing_scan(self) -> None:
        """A single flipped bit inside an otherwise complete, previously-
        valid trace block (bit rot / unrelated flash corruption — NOT a
        torn write) must be caught by decode_one_block()'s CRC check on the
        next boot scan, which must stop there cleanly (no crash, no hang,
        no garbage emitted) — recovering everything before it and nothing
        at or after it, exactly mirroring tools/tests/test_trace_codec.py's
        own test_corrupt_crc_ignored, one layer up (through the actual
        on-flash region, not just the codec in isolation)."""
        backing = self._backing("crc_corrupt.bin")

        # Three distinct one-second blocks: 3, 2, and 4 samples respectively.
        r1 = run_harness(self.harness, [
            "INIT",
            "TRACE_APPEND 0.020,1.001;0.040,1.002;0.060,1.003",
            "TRACE_APPEND 1.020,1.010;1.040,1.011",
            "TRACE_APPEND 2.020,1.020;2.040,1.021;2.060,1.022;2.080,1.023",
            "OPEN_READ TRACE", "CLOSE_READ",
            "TRACE_BYTES",
        ], backing=backing)
        self.assertEqual(r1.returncode, 0)
        block1_size = trace_codec.block_size(3)
        bytes_all_three = int(last(r1.events, "TRACE_BYTES")["n"])

        # Flip one bit inside block 2's header (its t0_ms field, byte offset
        # 2 of that block — mirrors test_trace_codec.py's own
        # `flip_at = one_block + 10` in spirit: land solidly inside the
        # target block, clear of either neighboring block's boundary).
        flip_at = TRACE_REGION_START + block1_size + 2
        with open(backing, "r+b") as f:
            f.seek(flip_at)
            byte = f.read(1)
            f.seek(flip_at)
            f.write(bytes([byte[0] ^ 0x01]))

        r2 = run_harness(self.harness, [
            "INIT",
            "TRACE_BYTES",
            "OPEN_READ TRACE", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(r2.returncode, 0, "a corrupted block must not crash the boot scan")

        # Only block 1 survives — block 2's corruption stops the scan cold,
        # and block 3 (otherwise perfectly valid) is never even reached,
        # exactly like trace_codec.h's own decode()/decode_one_block()
        # contract (stop at the first bad block, never look past it).
        expected_bytes = 6 + 3 * len("0.020,1.001\n")
        self.assertEqual(int(last(r2.events, "TRACE_BYTES")["n"]), expected_bytes)
        self.assertLess(int(last(r2.events, "TRACE_BYTES")["n"]), bytes_all_three)

        csv = r2.read_alls[0]
        rows = [ln for ln in csv.splitlines() if ln and ln != "t,mag"]
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertTrue(row.startswith("0.0"), f"unexpected surviving row: {row!r}")

    # ------------------------------------------------- cross-language check

    def test_trace_bytes_matches_python_decode_of_raw_region(self) -> None:
        """trace_bytes()'s CSV-equivalent estimate must equal the byte
        length of sim/trace_codec.py independently decoding the SAME raw
        on-flash bytes to CSV — the cross-language check this whole binary
        trace format exists to satisfy (see trace_codec.h's file comment
        and tools/tests/test_trace_codec.py, one layer down from here).
        Reads the mock's backing file directly rather than adding any new
        harness command — the file IS the chip's bytes."""
        backing = self._backing("cross_lang.bin")
        r = run_harness(self.harness, [
            "INIT",
            "TRACE_APPEND 0.020,1.001;0.040,1.002;0.060,1.003",
            "TRACE_APPEND 1.020,1.010;1.040,1.011",
            "TRACE_APPEND 2.020,1.777",
            "OPEN_READ TRACE", "CLOSE_READ",  # force the last block's flush
            "TRACE_BYTES",
        ], backing=backing)
        self.assertEqual(r.returncode, 0)
        reported = int(last(r.events, "TRACE_BYTES")["n"])

        raw = backing.read_bytes()
        self.assertEqual(len(raw), CHIP_SIZE)
        raw_trace_region = raw[TRACE_REGION_START:]
        csv = trace_codec.decode_to_csv(raw_trace_region, log_hz=LOG_HZ)

        # trace_bytes() additionally counts the 6-byte "t,mag\n" CSV header
        # (added once — see jh_store.cpp's trace_append(), s_trace_csv_
        # header_counted) that a framed dump prepends but the raw BINARY
        # region itself never stores — decode_to_csv() of the raw region is
        # exactly the sample rows, header-free.
        self.assertEqual(len(csv) + 6, reported)
        # And, for good measure, the actual content matches what a `dump`
        # would stream (minus jh_store's own CSV header, which trace_bytes()
        # counts but the raw region itself never stores). A fresh process
        # needs its own INIT first (storage starts unmounted).
        r2 = run_harness(self.harness, ["INIT", "OPEN_READ TRACE", "READ_ALL", "CLOSE_READ"],
                         backing=backing)
        dumped = r2.read_alls[0]
        self.assertEqual(dumped, "t,mag\n" + csv)
