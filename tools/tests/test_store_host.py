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

NOTABLE FINDINGS from building this suite, BOTH SINCE FIXED in
jh_store.cpp (neither was a mock artifact — both reproduced, and now both
are verified fixed, against the real, unmodified-except-for-the-fix
source):

  1. A torn write used to permanently stick the append offset: resuming an
     append exactly where a torn write left off silently AND-merged new
     data against leftover garbage (real NOR flash can only clear bits),
     which the CRC check safely rejected but never advanced past — every
     later append could silently fail forever until clear(). Fixed by
     skipPastTornWrite()/isErasedBytes() in jh_store.cpp: a boot scan (and
     the read-back path) that finds damage (invalid AND not simply erased)
     skips to the next safe page boundary and keeps looking, rather than
     giving up. See test_power_cut_recovers_next_append_lands_cleanly_and_
     is_readable, test_power_cut_trace_block_recovers_and_is_readable, and
     the regression test test_multiple_torn_writes_all_recover_and_stay_
     readable below.
  2. trace_is_full() used to never trip when the remaining tail was smaller
     than one block but not literally zero — the flag could read false
     forever, the "# trace log full" narration might never fire, and
     trace_bytes()'s estimate kept climbing past what was truly stored.
     Fixed by treating "full" as "the remaining tail can't fit the LARGEST
     possible block" (kMaxTraceBlockBytes), not merely "not one more byte
     remains." See test_trace_is_full_flag_trips_before_the_tail_runs_out
     below.

Both tests keep their original "FINDING" framing front and center in their
docstrings (what was found, exactly how, and why it mattered) alongside
what the fix actually does — this suite is the regression guard for both
from here on.

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

    def test_trace_clear_preserves_jumps(self) -> None:
        """trace_clear() erases the trace and PRESERVES every stored jump.

        This is the contract the storage-lifecycle auto-clear rests on
        (main.cpp, 2026-08-20: when the trace region is full at a session
        boundary, reclaim it so the new session records). The dangerous half
        is not the erasing — it is whether the user's history survives. Jumps
        are what the watch reseeds from on every reconnect, and 2048 records
        is ~100 sessions; wiping them to make room for trace would trade the
        user's data for ours.

        Uses a genuinely FULL region (TRACE_FILL to the cap), because that is
        the only state the auto-clear ever acts in — a half-full trace is
        never touched.
        """
        backing = self._backing("trace_clear.bin")
        r = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 1 10.0 0.90 0.90 1.00",
            "JUMPS_APPEND 2 20.0 1.02 1.02 1.28",
            "TRACE_FILL 99999 50 0.0 1.0",   # fill to the cap
            "TRACE_IS_FULL",
            "JUMPS_SCAN",
            "TRACE_CLEAR",
            "TRACE_IS_FULL",
            "JUMPS_SCAN",
            "TRACE_BYTES",
            "OK",
            # the reclaimed region must actually be usable again, or the
            # auto-clear would "free" space into a dead store
            "TRACE_APPEND 1.0,1.001;1.02,1.002",
            "TRACE_BYTES",
        ], backing=backing)
        self.assertEqual(r.returncode, 0)

        full = all_of(r.events, "TRACE_IS_FULL")
        self.assertEqual(full[0]["full"], "1", "TRACE_FILL should reach the cap")
        self.assertEqual(full[1]["full"], "0", "trace_clear must clear the full flag")

        scans = all_of(r.events, "JUMPS_SCAN")
        self.assertEqual(scans[0]["count"], "2")
        self.assertEqual(scans[1]["count"], "2",
                         "JUMPS MUST SURVIVE trace_clear — this is the whole contract")
        self.assertEqual(scans[0]["best_m"], scans[1]["best_m"],
                         "best height must survive too (the watch reseeds from it)")

        tb = all_of(r.events, "TRACE_BYTES")
        self.assertEqual(tb[0]["n"], "0", "trace must be empty after trace_clear")
        self.assertNotEqual(tb[1]["n"], "0", "region must accept new writes again")
        self.assertEqual(all_of(r.events, "OK")[-1]["ok"], "1",
                         "storage must remain mounted through a trace_clear")

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
        # binary block (trace_codec.block_size(2) bytes, rounded up to the
        # next word boundary — the writer advances with align4() because the
        # QSPI peripheral requires word-aligned flash addresses; see
        # jh_store.cpp align4()'s comment) plus one new 32-byte jump record,
        # regardless of how many CSV bytes that block decodes to.
        align4 = lambda n: (n + 3) & ~3
        self.assertEqual(free1 - free3,
                         JUMP_RECORD_BYTES + align4(trace_codec.block_size(2)))
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

    # ---------------------------------------------------------- try_mount

    def test_try_mount_valid_store_resumes_everything(self) -> None:
        """try_mount() over a valid superblock behaves exactly like init():
        mounts, resumes append points, no announce lines (nothing formats).
        This is the mule-recovery happy path (jh_store.h's try_mount doc):
        StoreGuard skipped the boot mount, the `mount` command retries, the
        history is still there."""
        backing = self._backing("try_mount_ok.bin")
        r1 = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 1 1.000 0.300 0.280 0.550",
            "JUMPS_APPEND 2 2.000 0.310 0.290 0.610",
        ], backing=backing)
        self.assertEqual(r1.returncode, 0)

        r2 = run_harness(self.harness, [
            "TRY_MOUNT",
            "OK",
            "JUMPS_SCAN",
            "JUMPS_APPEND 3 3.000 0.320 0.300 0.700",  # append continuity
            "JUMPS_SCAN",
        ], backing=backing)
        self.assertEqual(r2.returncode, 0)
        self.assertEqual(last(r2.events, "TRY_MOUNT")["ok"], "1")
        self.assertEqual(last(r2.events, "OK")["ok"], "1")
        self.assertEqual(all_of(r2.events, "ANNOUNCE"), [],
                         "a clean try_mount must not announce (nothing formats)")
        scans = all_of(r2.events, "JUMPS_SCAN")
        self.assertEqual(int(scans[0]["count"]), 2)
        self.assertEqual(int(scans[1]["count"]), 3,
                         "append points must be fully resumed, exactly like init()")

    def test_try_mount_virgin_chip_refuses_to_format(self) -> None:
        """try_mount() on a never-written chip must REFUSE (init() is the
        only formatter): ok=0, storage not-ok, and — the actual contract —
        nothing gets written, proven by a later init() over the same backing
        still seeing a virgin chip and announcing a first-boot format."""
        backing = self._backing("try_mount_virgin.bin")
        r1 = run_harness(self.harness, [
            "TRY_MOUNT",
            "OK",
            "JUMPS_APPEND 1 1.000 0.300 0.280 0.550",  # must no-op: not ok
            "JUMPS_SCAN",
        ], backing=backing)
        self.assertEqual(r1.returncode, 0)
        self.assertEqual(last(r1.events, "TRY_MOUNT")["ok"], "0")
        self.assertEqual(last(r1.events, "OK")["ok"], "0")
        self.assertEqual(int(last(r1.events, "JUMPS_SCAN")["count"]), 0)
        announces = [e["text"] for e in all_of(r1.events, "ANNOUNCE")]
        self.assertTrue(any("NOT formatting" in a for a in announces),
                        f"expected the refuses-to-format announce, got {announces!r}")

        r2 = run_harness(self.harness, ["INIT", "OK"], backing=backing)
        announces2 = [e["text"] for e in all_of(r2.events, "ANNOUNCE")]
        self.assertTrue(any("formatting storage" in a for a in announces2),
                        "init() must still see a virgin chip — try_mount wrote NOTHING")
        self.assertEqual(last(r2.events, "OK")["ok"], "1")

    def test_try_mount_corrupt_superblock_never_touches_data(self) -> None:
        """THE reason try_mount() exists (jh_store.h doc): a chip that
        mounts but reads a bad superblock might be garbage reads over
        INTACT data — init() would auto-format and destroy it. try_mount()
        must refuse AND leave every byte of the chip untouched, so that
        once the superblock is repaired (here: restored; on real silicon:
        a power-cycle un-wedging the chip's read mode) the data is still
        there in full."""
        backing = self._backing("try_mount_corrupt_sb.bin")
        r1 = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 1 1.000 0.300 0.280 0.550",
            "JUMPS_APPEND 2 2.000 0.310 0.290 0.610",
        ], backing=backing)
        self.assertEqual(r1.returncode, 0)

        # Corrupt one bit of the superblock magic, remembering the original.
        with open(backing, "r+b") as f:
            original_byte = f.read(1)
            f.seek(0)
            f.write(bytes([original_byte[0] ^ 0x01]))
        snapshot = backing.read_bytes()

        r2 = run_harness(self.harness, [
            "TRY_MOUNT",
            "OK",
        ], backing=backing)
        self.assertEqual(r2.returncode, 0)
        self.assertEqual(last(r2.events, "TRY_MOUNT")["ok"], "0")
        self.assertEqual(last(r2.events, "OK")["ok"], "0")
        announces = [e["text"] for e in all_of(r2.events, "ANNOUNCE")]
        self.assertTrue(any("NOT formatting" in a for a in announces),
                        f"expected the refuses-to-format announce, got {announces!r}")
        self.assertEqual(backing.read_bytes(), snapshot,
                         "try_mount over a bad superblock must not change ONE byte — "
                         "an init() here would have chip-erased everything")

        # Repair the superblock: the data must still be there, in full.
        with open(backing, "r+b") as f:
            f.write(original_byte)
        r3 = run_harness(self.harness, [
            "TRY_MOUNT",
            "JUMPS_SCAN",
        ], backing=backing)
        self.assertEqual(last(r3.events, "TRY_MOUNT")["ok"], "1")
        scan = last(r3.events, "JUMPS_SCAN")
        self.assertEqual(int(scan["count"]), 2, "the data survived the whole episode")
        self.assertAlmostEqual(float(scan["best_m"]), 0.610, places=3)

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

        Also confirms trace_is_full() actually trips once the region is
        physically exhausted (see test_trace_is_full_flag_trips_before_the_
        tail_runs_out below for the focused version of this fix, and the
        module docstring for the pre-fix finding this guards against) — the
        fill loop here relies on that flag to know when to stop, so a
        regression back to the old under-reporting behavior would show up
        here too as `TRACE_FILL`'s own `full=` line never becoming 1.
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
            "TRACE_IS_FULL",
            "TRACE_BYTES",
            "FREE_BYTES",
            "JUMPS_APPEND 1 1.0 0.30 0.28 0.50",
            "JUMPS_APPEND 2 2.0 0.30 0.28 0.90",
            "JUMPS_SCAN",
        ], backing=backing)
        self.assertEqual(r1.returncode, 0)

        fill = last(r1.events, "TRACE_FILL")
        self.assertEqual(fill["full"], "1", "the fill loop's own stopping condition — "
                         "confirms trace_is_full() tripped instead of running the whole "
                         "requested seconds_to_try regardless (the pre-fix behavior)")
        self.assertLess(int(fill["seconds_appended"]), seconds_to_try,
                        "should have stopped early once full, not exhausted the whole ask")
        self.assertEqual(last(r1.events, "TRACE_IS_FULL")["full"], "1")

        free_after_fill = int(last(r1.events, "FREE_BYTES")["n"])
        # Physical trace headroom left is under the LARGEST possible block's
        # worth (kMaxTraceBlockBytes in jh_store.cpp — a conservative bound,
        # not the specific size of whatever block happened to be tried last)
        # — i.e. genuinely, physically full: no further block could ever fit.
        max_block_bytes = trace_codec.block_size(trace_codec.MAX_SAMPLES_PER_BLOCK)
        trace_free = free_after_fill - JUMPS_REGION_BYTES
        self.assertGreaterEqual(trace_free, 0)
        self.assertLess(trace_free, max_block_bytes)

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

    def test_trace_is_full_flag_trips_before_the_tail_runs_out(self) -> None:
        """FIXED BEHAVIOR — was a genuine pre-hardware FINDING (see this
        file's module docstring): trace_is_full() used to only ever get set
        true inside closeAndWriteBlock() at the exact moment a write made
        s_trace_append_off >= s_trace_region_bytes. A block that instead
        arrived when LESS than one block's worth of room remained (the
        overwhelmingly likely case, since block sizes rarely divide the
        region evenly) was silently dropped WITHOUT crossing that
        threshold — so the flag could read false forever after, even
        though no further block would ever fit again. main.cpp's own gate
        (`if (fs_ok && !jh_store::trace_is_full() ...)`) would therefore
        have kept calling trace_append() indefinitely (harmlessly — no
        corruption, closeAndWriteBlock()'s own bounds check already
        refused to write past the region — but pointlessly), the "# trace
        log full" narration might never have fired, and trace_bytes()'s
        estimate would have kept climbing past what was truly stored.

        THE FIX (jh_store.cpp's kMaxTraceBlockBytes, used in both
        findTraceAppendPoint()'s boot-time check and closeAndWriteBlock()'s
        runtime check): "full" now means "the remaining tail cannot fit
        the LARGEST block that could ever legitimately be written"
        (trace_codec::block_size(MAX_SAMPLES_PER_BLOCK) — a fixed,
        conservative upper bound), not merely "this specific block that
        just arrived didn't fit." This test drives the region to true
        physical exhaustion and confirms the flag trips there, and that
        trace_append() then correctly stops growing its byte estimate.
        """
        backing = self._backing("full_flag.bin")
        exact_blocks = TRACE_REGION_BYTES // trace_codec.block_size(LOG_HZ)
        max_block_bytes = trace_codec.block_size(trace_codec.MAX_SAMPLES_PER_BLOCK)

        r = run_harness(self.harness, [
            "INIT",
            f"TRACE_FILL {exact_blocks + 20} {LOG_HZ} 0.0 1.0",
            "TRACE_IS_FULL",
            "TRACE_BYTES",
            "FREE_BYTES",
            "TRACE_APPEND 999999.000,1.000",  # one more, after already at the tail
            "TRACE_BYTES",
        ], backing=backing)
        self.assertEqual(r.returncode, 0)

        trace_free = int(last(r.events, "FREE_BYTES")["n"]) - JUMPS_REGION_BYTES
        self.assertGreaterEqual(trace_free, 0)
        self.assertLess(trace_free, max_block_bytes,
                        "no further block could ever fit in what's left")

        # The fix: the flag reflects that reality instead of staying 0.
        self.assertEqual(last(r.events, "TRACE_IS_FULL")["full"], "1",
                         "trace_is_full() must trip once the tail can't fit the largest "
                         "possible block — if this ever asserts '0' again, the "
                         "under-report finding documented in this file's module "
                         "docstring has regressed")

        # And the RAM-side estimate correctly stops climbing once full,
        # since trace_append() itself gates on trace_is_full() before ever
        # touching s_trace_csv_bytes — both checked within ONE session so
        # the before/after comparison is on the same process/chip.
        bytes_events = all_of(r.events, "TRACE_BYTES")
        self.assertEqual(int(bytes_events[1]["n"]), int(bytes_events[0]["n"]),
                         "the CSV-equivalent estimate must stop growing once genuinely full")

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

    def test_power_cut_recovers_next_append_lands_cleanly_and_is_readable(self) -> None:
        """FIXED BEHAVIOR — was a genuine pre-hardware FINDING (see this
        file's module docstring): jh_store.cpp used to resume appending at
        the exact flash offset a torn write left behind WITHOUT re-erasing
        it first. Real NOR flash writes can only clear bits (never set one
        back to 1 without an erase — modeled faithfully in mock_flash.h),
        so the next write attempt at that same offset ANDed its intended
        bytes against whatever the torn attempt already left there. If that
        corrupted even one bit the new record's own stored CRC (computed
        over the INTENDED bytes before the corrupting AND) no longer
        matched the ACTUAL stored bytes — safely caught by the CRC check
        (nothing corrupt was ever surfaced as valid), but the append offset
        never advanced past that dead slot, so it could keep failing
        forever, silently losing every jump logged after a mid-write power
        cut, until clear() was called. This reproduced on the FIRST retry
        with the values below (not a contrived adversarial search).

        THE FIX (jh_store.cpp's skipPastTornWrite()/isErasedBytes(),
        applied in findJumpsAppendPoint() and produceNextUnit()): a boot
        scan that finds a record which fails validation AND isn't simply
        erased treats it as torn/corrupted, skips the resume point forward
        to the next page boundary beyond anything that write could have
        touched (never resuming on top of not-fully-erased bytes), and
        KEEPS scanning rather than giving up — the read-back path applies
        the identical skip, so what jumps_scan() counts and what a dump
        actually shows never disagree.
        """
        backing = self._backing("stuck_slot.bin")

        r1 = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 1 1.000 0.300 0.280 0.550",
            "FAULT_AFTER 12",
            "JUMPS_APPEND 2 2.000 0.300 0.280 0.999",
        ], backing=backing)
        self.assertEqual(r1.returncode, FAULT_EXIT_CODE,
                         "expected the armed fault to terminate the process")

        # Reboot: torn record #2 correctly excluded, then append a NEW,
        # different record at what is now a safely-skipped-to offset.
        r2 = run_harness(self.harness, [
            "INIT",
            "JUMPS_SCAN",
            "JUMPS_APPEND 99 3.000 0.300 0.280 0.650",
            "JUMPS_SCAN",
            "OPEN_READ JUMPS", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(r2.returncode, 0)
        scans = all_of(r2.events, "JUMPS_SCAN")
        self.assertEqual(int(scans[0]["count"]), 1, "the torn record must still be excluded")
        self.assertEqual(int(scans[1]["count"]), 2, "the fix: the retry is counted...")
        rows = [ln for ln in r2.read_alls[0].splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(len(rows), 2, "...AND the fix: the retry is actually READABLE, "
                         "not just optimistically counted — the pre-fix version of this "
                         "test asserted len(rows) == 1 here (see git history)")
        self.assertTrue(rows[0].startswith("1,1.000,"))
        self.assertTrue(rows[1].startswith("99,3.000,"))

        # Reboot AGAIN: the recovered state must be durable, not a one-
        # process fluke (before the fix, a second reboot's fresh re-scan
        # would have reverted the cached count back down to 1).
        r3 = run_harness(self.harness, [
            "INIT",
            "JUMPS_SCAN",
            "OPEN_READ JUMPS", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(int(last(r3.events, "JUMPS_SCAN")["count"]), 2)
        rows3 = [ln for ln in r3.read_alls[0].splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(len(rows3), 2)
        self.assertTrue(rows3[0].startswith("1,1.000,"))
        self.assertTrue(rows3[1].startswith("99,3.000,"))

        # And a further, entirely ordinary append lands right after both,
        # with no gap-related off-by-ones.
        r4 = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 100 4.000 0.300 0.280 0.700",
            "JUMPS_SCAN",
            "OPEN_READ JUMPS", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(int(last(r4.events, "JUMPS_SCAN")["count"]), 3)
        rows4 = [ln for ln in r4.read_alls[0].splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(len(rows4), 3)
        self.assertTrue(rows4[2].startswith("100,4.000,"))

        # clear() still works as the (now merely optional) full reset.
        r5 = run_harness(self.harness, [
            "INIT", "CLEAR",
            "JUMPS_APPEND 1 5.000 0.300 0.280 0.800",
            "JUMPS_SCAN",
            "OPEN_READ JUMPS", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(int(last(r5.events, "JUMPS_SCAN")["count"]), 1)
        rows5 = [ln for ln in r5.read_alls[0].splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(len(rows5), 1)
        self.assertTrue(rows5[0].startswith("1,5.000,"))

    def test_power_cut_trace_block_recovers_and_is_readable(self) -> None:
        """Trace-region sibling of test_power_cut_recovers_next_append_
        lands_cleanly_and_is_readable — the identical torn-write/AND-merge
        mechanism and fix apply here too (findTraceAppendPoint()/
        produceNextUnit()'s trace branch), but trace blocks are variable-
        size and can straddle multiple 256B pages (unlike a fixed 32-byte
        jump record, which never does), so this is a genuinely different
        code path worth its own regression coverage, not just a restatement
        of the jumps case."""
        backing = self._backing("stuck_slot_trace.bin")

        r1 = run_harness(self.harness, [
            "INIT",
            "TRACE_APPEND 0.020,1.001;0.040,1.002",  # block A: closes on the next line
            "TRACE_APPEND 1.020,1.010",               # block B: torn below
            "FAULT_AFTER 4",
            "OPEN_READ TRACE", "CLOSE_READ",          # forces block B's write — this tears
        ], backing=backing)
        self.assertEqual(r1.returncode, FAULT_EXIT_CODE)

        r2 = run_harness(self.harness, [
            "INIT",
            "TRACE_BYTES",
            "TRACE_APPEND 5.000,2.000;5.020,2.001",
            "TRACE_APPEND 9.000,3.000",
            "OPEN_READ TRACE", "CLOSE_READ",
            "TRACE_BYTES",
            "OPEN_READ TRACE", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(r2.returncode, 0)
        bytes_events = all_of(r2.events, "TRACE_BYTES")
        self.assertEqual(int(bytes_events[0]["n"]), 6 + 2 * len("0.020,1.001\n"),
                         "only block A survives the torn block B")
        self.assertGreater(int(bytes_events[1]["n"]), int(bytes_events[0]["n"]),
                           "the fix: the retry blocks are now counted too")
        dump = r2.read_alls[0]
        rows = [ln for ln in dump.splitlines() if ln and ln != "t,mag"]
        self.assertEqual(rows, ["0.020,1.001", "0.040,1.002", "5.000,2.000",
                                "5.020,2.001", "9.000,3.000"],
                         "the fix: every sample survives — the pre-fix block B's damage "
                         "would have hidden the retry blocks entirely")

        # Durable across a further reboot.
        r3 = run_harness(self.harness, ["INIT", "OPEN_READ TRACE", "READ_ALL", "CLOSE_READ"],
                         backing=backing)
        rows3 = [ln for ln in r3.read_alls[0].splitlines() if ln and ln != "t,mag"]
        self.assertEqual(rows3, rows)

    def test_multiple_torn_writes_all_recover_and_stay_readable(self) -> None:
        """Regression scenario for the fix's "keep scanning/reading past
        damage" behavior specifically (not just "skip past one gap"): TWO
        separate power cuts, each leaving its own torn record behind, with
        a successful append landing between them. All three genuinely
        valid records (one before each gap, one after both) must survive,
        stay correctly counted, and stay readable — including still being
        correctly ORDERED — across further reboots. This is exactly the
        shape of code a future refactor could silently regress (e.g.
        reintroducing a `break` where this test needs `continue`), so it's
        worth pinning on its own rather than only ever exercising a single
        gap."""
        backing = self._backing("multi_gap.bin")

        r1 = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 0 0.0 0.300 0.280 0.10",
            "FAULT_AFTER 12",
            "JUMPS_APPEND 1 1.0 0.300 0.280 0.20",  # torn: gap #1
        ], backing=backing)
        self.assertEqual(r1.returncode, FAULT_EXIT_CODE)

        r2 = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 10 10.0 0.300 0.280 0.30",  # lands cleanly past gap #1
            "FAULT_AFTER 12",
            "JUMPS_APPEND 11 11.0 0.300 0.280 0.40",  # torn: gap #2
        ], backing=backing)
        self.assertEqual(r2.returncode, FAULT_EXIT_CODE)

        r3 = run_harness(self.harness, [
            "INIT",
            "JUMPS_SCAN",
            "JUMPS_APPEND 20 20.0 0.300 0.280 0.50",  # lands cleanly past gap #2
            "JUMPS_SCAN",
            "OPEN_READ JUMPS", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(r3.returncode, 0)
        scans = all_of(r3.events, "JUMPS_SCAN")
        self.assertEqual(int(scans[0]["count"]), 2, "the two records either side of gap #1 "
                         "recovered by the PREVIOUS boot, before gap #2 even existed")
        self.assertEqual(int(scans[1]["count"]), 3)
        rows = [ln for ln in r3.read_alls[0].splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[0].startswith("0,0.000,"))
        self.assertTrue(rows[1].startswith("10,10.000,"))
        self.assertTrue(rows[2].startswith("20,20.000,"))

        # One more reboot: fully stable with 3 valid records spanning 2 gaps.
        r4 = run_harness(self.harness, ["INIT", "JUMPS_SCAN", "OPEN_READ JUMPS", "READ_ALL",
                                        "CLOSE_READ"], backing=backing)
        self.assertEqual(int(last(r4.events, "JUMPS_SCAN")["count"]), 3)
        rows4 = [ln for ln in r4.read_alls[0].splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(rows4, rows)

    # ------------------------------------------ non-fatal failure injection (F)
    # The tests above all model a POWER CUT (FAULT_AFTER — the process is torn
    # down via _Exit(), and the fix is only visible across a REBOOT). The two
    # tests below model the OTHER real failure shape review-store.md finding
    # #1 calls out: writeBuffer()/eraseSector()/eraseChip() returning short/
    # false on a transient bus failure WITHOUT resetting the MCU — the mock's
    # NEW FAIL_NEXT_WRITE/FAIL_NEXT_ERASE (mock_flash_test::
    # arm_write_short_return()/arm_erase_failure(), distinct from
    # arm_fault_after_bytes()'s process-ending _Exit()). Because the process
    # keeps running, these bugs were visible WITHIN THE SAME power cycle —
    # no reboot needed to see count/dump drift or resurrected data.

    def test_failed_jump_write_same_cycle_count_and_dump_agree(self) -> None:
        """FIX (review-store.md finding #1, the 'no reboot needed' half): a
        transient bus failure — writeBuffer() returning short WITHOUT a
        power cut — used to still advance jumps_append()'s offset/count
        unconditionally, so JUMPS_SCAN and an actual dump could disagree
        about the failed record WITHIN THE SAME power cycle. jh_store.cpp
        now checks writeBuffer()'s return value and, on a short/failed
        write, treats it exactly like a torn write found during a boot scan
        (skips the append offset past it via skipPastTornWrite(), does not
        count it) — entirely in-process, immediately, not waiting for a
        reboot's rescan to notice."""
        backing = self._backing("failed_write.bin")
        r = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 1 1.000 0.300 0.280 0.550",
            "FAIL_NEXT_WRITE 12",  # next writeBuffer applies 12 of 32 bytes, returns cleanly
            "JUMPS_APPEND 2 2.000 0.300 0.280 0.999",  # this write is the injected short one
            "JUMPS_SCAN",
            "OPEN_READ JUMPS", "READ_ALL", "CLOSE_READ",
            # Still the SAME process/power-cycle: a further, ordinary append
            # must land cleanly after the skipped-past damage.
            "JUMPS_APPEND 99 3.000 0.300 0.280 0.650",
            "JUMPS_SCAN",
            "OPEN_READ JUMPS", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(r.returncode, 0, "a short/failed write must NOT end the process "
                         "(that's FAULT_AFTER's job) — the MCU keeps running")

        scans = all_of(r.events, "JUMPS_SCAN")
        self.assertEqual(int(scans[0]["count"]), 1,
                         "the failed record must not be counted, in THIS SAME process, "
                         "with no reboot needed to notice")
        rows_before = [ln for ln in r.read_alls[0].splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(len(rows_before), 1, "and a dump must agree with that count exactly — "
                         "the bug this fix closes is count/dump drift WITHIN one power cycle")
        self.assertTrue(rows_before[0].startswith("1,1.000,"))

        self.assertEqual(int(scans[1]["count"]), 2, "a further append recovers, landing past "
                         "the skipped-over damaged slot")
        rows_after = [ln for ln in r.read_alls[1].splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(len(rows_after), 2)
        self.assertTrue(rows_after[1].startswith("99,3.000,"))

        # And durable across an actual reboot too, for good measure.
        r2 = run_harness(self.harness, ["INIT", "JUMPS_SCAN", "OPEN_READ JUMPS", "READ_ALL",
                                        "CLOSE_READ"], backing=backing)
        self.assertEqual(int(last(r2.events, "JUMPS_SCAN")["count"]), 2)
        rows2 = [ln for ln in r2.read_alls[0].splitlines() if ln and not ln.startswith("n,")]
        self.assertEqual(rows2, rows_after)

    def test_failed_clear_leaves_not_ok_and_no_resurrection(self) -> None:
        """FIX (review-store.md finding #1's clear() half): an erase
        failure during clear() (a transient bus failure, not a power cut —
        the mock's NEW arm_erase_failure()) used to be ignored, so clear()
        would happily rewrite the superblock and zero every counter as if
        the erase had genuinely happened, even though the OLD data was
        still physically sitting on flash underneath a now out-of-sync
        superblock. jh_store.cpp now tracks whether every erase (+ the
        final superblock rewrite) actually succeeded and, if not, leaves
        storage marked not-ok (jh_store::ok()) — jh_store.h's clear()
        contract is void, so this is the only channel available to report
        it — so jumps_append()/trace_append() both go silent (no-op) rather
        than writing on top of a region whose true state is now uncertain,
        and the RAM-side counters do not keep showing the pre-clear data as
        though clear() had silently succeeded."""
        backing = self._backing("failed_clear.bin")
        r = run_harness(self.harness, [
            "INIT",
            "JUMPS_APPEND 1 1.000 0.300 0.280 0.550",
            "JUMPS_APPEND 2 2.000 0.300 0.280 0.900",
            "TRACE_APPEND 0.020,1.001;0.040,1.002",
            "OPEN_READ TRACE", "CLOSE_READ",
            "JUMPS_SCAN", "TRACE_BYTES", "OK",
            "FAIL_NEXT_ERASE",
            "CLEAR",
            "OK",
            "JUMPS_SCAN",
            "TRACE_BYTES",
            # These must be no-ops: storage is no longer ok(), matching
            # jumps_append()/trace_append()'s own !s_fs_ok guard.
            "JUMPS_APPEND 3 3.000 0.300 0.280 0.700",
            "TRACE_APPEND 9.000,3.000",
            "JUMPS_SCAN",
            "TRACE_BYTES",
        ], backing=backing)
        self.assertEqual(r.returncode, 0, "an erase failure must NOT end the process — "
                         "the MCU keeps running, just no longer 'ok'")

        oks = all_of(r.events, "OK")
        self.assertEqual(oks[0]["ok"], "1", "storage was fine before the injected failure")
        self.assertEqual(oks[1]["ok"], "0", "the fix: a failed clear() leaves storage not-ok "
                         "— the only channel jh_store.h's void clear() contract allows")

        scans = all_of(r.events, "JUMPS_SCAN")
        bytes_events = all_of(r.events, "TRACE_BYTES")
        self.assertEqual(int(scans[0]["count"]), 2, "pre-clear state, for reference")
        self.assertGreater(int(bytes_events[0]["n"]), 0)

        self.assertEqual(int(scans[1]["count"]), 0, "no resurrection: the pre-clear jump "
                         "count must not keep reading as if clear() had silently failed AND "
                         "the old data were still normally readable")
        self.assertEqual(int(bytes_events[1]["n"]), 0)

        self.assertEqual(int(scans[2]["count"]), 0, "jumps_append() must no-op once not-ok")
        self.assertEqual(int(bytes_events[2]["n"]), 0, "trace_append() must no-op once not-ok")

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
        csv = trace_codec.decode_region_to_csv(raw_trace_region, log_hz=LOG_HZ)

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

    def test_multi_hour_timestamps_match_python_decode(self) -> None:
        """FIX (review-store.md finding #2 — CONFIRMED BY TEST): jh_store.cpp's
        encode-site arithmetic (feedSample()'s t_s->t0_ms conversion) used to
        be done in float32 (lroundf(t_s*1000.0f)), diverging from Python's
        native-double math (sim/trace_codec.py) once t grows large enough
        that float32's ~7-decimal-digit precision can no longer resolve
        0.001 s steps (measured divergence at t=8192.021s). Now shared via
        trace_codec::t0_ms_from_t_s(), computed in double. This test drives
        the REAL on-device encode path (trace_append()/feedSample(), not
        just the trace_codec_harness one layer down in
        tools/tests/test_trace_codec.py) with samples anchored at ~8200s and
        ~16400s — straddling both the encode-side's measured 8192.021s
        divergence and the decode-side's later ~16380s one — and checks the
        raw stored bytes decode, via the independent Python mirror, to an
        EXACT byte-for-byte match of the input CSV: the same cross-language
        bar test_trace_bytes_matches_python_decode_of_raw_region already
        holds at t=0, extended here to where the bug actually bit."""
        for t0 in (8200.0, 16400.0):
            backing = self._backing(f"multihour_{int(t0)}.bin")
            samples = [(t0 + i / LOG_HZ, 1.0 + 0.001 * (i % 5)) for i in range(LOG_HZ * 3)]
            seconds = sorted(set(int(t) for t, _ in samples))
            commands = ["INIT"]
            for sec in seconds:
                tokens = ";".join(f"{t:.3f},{m:.3f}" for t, m in samples if int(t) == sec)
                commands.append(f"TRACE_APPEND {tokens}")
            commands += ["OPEN_READ TRACE", "CLOSE_READ"]
            r = run_harness(self.harness, commands, backing=backing)
            self.assertEqual(r.returncode, 0)

            raw = backing.read_bytes()
            raw_trace_region = raw[TRACE_REGION_START:]
            csv = trace_codec.decode_region_to_csv(raw_trace_region, log_hz=LOG_HZ)
            expected = "".join(f"{t:.3f},{m:.3f}\n" for t, m in samples)
            self.assertEqual(csv, expected, f"multi-hour parity failed at t0={t0}")

    # ---------------------------------------------------- non-finite guard (H)

    def test_nan_inf_sample_skipped_without_crash(self) -> None:
        """FIX (review-store.md finding #3): jh_store.cpp's feedSample() now
        rejects a non-finite PARSED value (atof("nan")/atof("inf") both
        parse successfully per the C standard — this is not the existing
        malformed-line/memchr tolerance) before it can reach
        t0_ms_from_t_s()/Encoder::add_sample() — t0_ms_from_t_s() has no
        guard of its own (lround(NaN) is unspecified, not merely "also maps
        to 0"), and trace_codec.h's own milli_g_from_g() guards the
        magnitude column independently (see tools/tests/test_trace_codec.py's
        own NaN/inf test, one layer down). A 'nan'/'inf' token in EITHER
        column must be silently skipped — no crash (subprocess exit 0), no
        garbage sample surfacing in count or dump — while well-formed
        samples on either side of it still come through, in order.

        Expected rows below are NOT the input timestamps verbatim — that's
        deliberate, not a mistake: a skipped sample is dropped before it's
        ever added to the block, so the survivors simply become the block's
        2nd/3rd/4th stored sample and are RECONSTRUCTED at that index's
        evenly-spaced offset from the block's t0 (trace_codec.h's own
        documented format property — a block has no way to represent "a
        gap", only "how many samples, evenly spaced, from this anchor").
        The two non-finite lines land in the same nominal second as the
        still-open block (a skipped sample can't itself trigger a new
        block, since feedSample() returns before ever checking floor(t_s)),
        so all four survivors end up packed into ONE block."""
        backing = self._backing("nan_inf.bin")
        r = run_harness(self.harness, [
            "INIT",
            "TRACE_APPEND 0.020,1.001;0.040,nan;0.060,1.002;0.080,inf;0.100,1.003",
            "TRACE_APPEND nan,1.500;0.120,1.004",
            "OPEN_READ TRACE", "CLOSE_READ",
            "TRACE_BYTES",
            "OPEN_READ TRACE", "READ_ALL", "CLOSE_READ",
        ], backing=backing)
        self.assertEqual(r.returncode, 0, "a non-finite sample must never crash the process")

        rows = [ln for ln in r.read_alls[0].splitlines() if ln and ln != "t,mag"]
        self.assertEqual(rows, ["0.020,1.001", "0.040,1.002", "0.060,1.003", "0.080,1.004"],
                         "the 3 non-finite lines (mag=nan, mag=inf, t=nan) are dropped; "
                         "the 4 well-formed magnitudes (1.001/1.002/1.003/1.004) all "
                         "survive, in order, packed densely from the block's t0")

        # And a reboot's re-scan agrees exactly — the skip happens at parse
        # time, not as some read-side patch-up that could disagree with what
        # was actually, durably stored.
        r2 = run_harness(self.harness, ["INIT", "OPEN_READ TRACE", "READ_ALL", "CLOSE_READ"],
                         backing=backing)
        rows2 = [ln for ln in r2.read_alls[0].splitlines() if ln and ln != "t,mag"]
        self.assertEqual(rows2, rows)
