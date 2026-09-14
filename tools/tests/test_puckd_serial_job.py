"""Tests for tools/puckd/serial_job.py -- the puck job's serial half
(docs/sync-agent-plan.md:41-58's steps 1-5 and 7).

Two kinds of test, deliberately kept apart:

  * Against a REAL tools/fake_device.py subprocess, over a real pty, through
    tools/jump's own Device -- the happy path, the traceraw->CSV fallback,
    an F-22 shortfall, a short transfer outside F-22's band, a non-fallback
    traceraw ERR, and a genuine `clear`. These exercise the actual wire.

  * verify_pull() and read_stats()'s trace_full/fs=down handling, driven
    DIRECTLY with a hand-built PullContext or a tiny scripted Device stub --
    for shapes tools/fake_device.py's real protocol cannot produce at all:
    its `trace` command never sends the empty-region's 6-byte header (its
    `send_file()` skips the header line entirely when there are no rows,
    unlike a real nrf52 puck -- CONTRACT.md SS2.5b), it has no background
    recording to grow trace_bytes between two `stats` reads (CONTRACT.md
    SS2.5b's growth window), it never emits '# name=' or the STATS
    `trace_full=1` adder key (firmware/src/main.cpp:850-856, landed with
    F-36's fix -- CONTRACT.md Appendix B2 records the '# name=' gap; no
    board on any bench has filled a region against a build that emits
    trace_full at all), and its `clear` command always genuinely empties
    (nothing in it can lie the way step 7's gate must guard against).
    tools/tests/test_ingest.py's own TestIngestF22CsvBand and
    TestTraceBinChecks establish this exact precedent in this repo: driving
    the pure verification function directly, not through a device that
    cannot produce the shape.

Neither approach ever edits tools/fake_device.py -- CLAUDE.md's "never edit
a file you were not assigned."

Run via: python3 -m pytest tools/tests/test_puckd_serial_job.py -q
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
JUMP = str(REPO / "tools" / "jump")
sys.path.insert(0, str(REPO / "tools"))

from puckd import serial_job  # noqa: E402


def run_cli(args, timeout=90):
    return subprocess.run([sys.executable, JUMP] + args, capture_output=True,
                          text=True, timeout=timeout, cwd=str(REPO),
                          stdin=subprocess.DEVNULL)


def _spawn_fake(scenario="session", extra=None):
    """Same technique as tools/tests/test_cli.py's TestSelftest et al.: spawn
    tools/fake_device.py directly (not through the `jump` CLI), read its
    'PTY <path>' announcement, and return (proc, port). Caller must
    proc.terminate() + proc.wait()."""
    proc = subprocess.Popen(
        [sys.executable, str(REPO / "tools" / "fake_device.py"),
         "--scenario", scenario] + (extra or []),
        stdout=subprocess.PIPE, text=True)
    first = proc.stdout.readline().strip()
    if not first.startswith("PTY "):
        proc.terminate()
        raise RuntimeError(f"fake device failed to start: {first!r}")
    return proc, first.split(None, 1)[1]


def _kill(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# --------------------------------------------------------- verify_pull: raw

class TestVerifyPullRawPath(unittest.TestCase):
    def ctx(self, **kw):
        base = dict(trace_format="jhtrace-v2-b64", jump_rows=0)
        base.update(kw)
        return serial_job.PullContext(**base)

    def test_clean_transfer_verifies(self):
        r = serial_job.verify_pull(self.ctx(
            expected_raw=10, got_raw_len=10, crc_expected="ab", crc_actual="ab"))
        self.assertTrue(r.verified)
        self.assertEqual(r.reasons, [])

    def test_short_raw_transfer_does_not_verify(self):
        """The exact shape a truncated cable pull produces: fewer bytes than
        announced. Must never verify -- CONTRACT.md SS2.4 (c)."""
        r = serial_job.verify_pull(self.ctx(
            expected_raw=1000, got_raw_len=500, crc_expected="ab", crc_actual="cd"))
        self.assertFalse(r.verified)
        self.assertTrue(any("Only 500" in x and "1,000" in x for x in r.reasons))

    def test_crc_mismatch_alone_fails_even_at_the_right_length(self):
        r = serial_job.verify_pull(self.ctx(
            expected_raw=10, got_raw_len=10, crc_expected="ab", crc_actual="zz"))
        self.assertFalse(r.verified)
        self.assertTrue(any("damaged" in x for x in r.reasons))

    def test_missing_expected_bytes_is_unchecked_not_a_pass(self):
        r = serial_job.verify_pull(self.ctx(
            expected_raw=None, got_raw_len=10, crc_expected="ab", crc_actual="ab"))
        self.assertFalse(r.verified)

    def test_b64_error_is_its_own_reason(self):
        r = serial_job.verify_pull(self.ctx(
            expected_raw=10, got_raw_len=10, crc_expected="ab", crc_actual="ab",
            b64_error="part of the ride data was unreadable"))
        self.assertFalse(r.verified)
        self.assertIn("part of the ride data was unreadable.", r.reasons)


# --------------------------------------------------------- verify_pull: csv

class TestVerifyPullCsvPath(unittest.TestCase):
    def ctx(self, **kw):
        base = dict(trace_format="csv", jump_rows=0)
        base.update(kw)
        return serial_job.PullContext(**base)

    def test_exact_byte_match_verifies(self):
        r = serial_job.verify_pull(self.ctx(trace_bytes_device=1000, got_csv_bytes=1000))
        self.assertTrue(r.verified)
        self.assertFalse(r.f22_band_applied)

    def test_f22_band_minus_765_verifies_and_names_f22(self):
        """The measured OG reading (docs/audit-2026-08-22.md, CONTRACT.md
        SS2.5a): tracecheck fast=15917918 slow=15917153, a COMPLETE download
        the pre-band code refused. tools/fake_device.py's own
        --trace-bytes-overreport knob can only skew STATS trace_bytes, not
        the CSV body a `trace` command actually sends, so this exact
        arithmetic is exercised directly (test_ingest.py's own
        test_minus_765_ingests_clean_and_says_why does the same thing for
        _verify_ingest_bundle)."""
        r = serial_job.verify_pull(self.ctx(
            trace_bytes_device=15917918, got_csv_bytes=15917918 - 765))
        self.assertTrue(r.verified)
        self.assertTrue(r.f22_band_applied)
        self.assertIn("765", r.f22_note)

    def test_f22_band_edge_800_verifies(self):
        r = serial_job.verify_pull(self.ctx(trace_bytes_device=10000, got_csv_bytes=10000 - 800))
        self.assertTrue(r.verified)
        self.assertTrue(r.f22_band_applied)

    def test_801_just_outside_the_band_refuses(self):
        r = serial_job.verify_pull(self.ctx(trace_bytes_device=10000, got_csv_bytes=10000 - 801))
        self.assertFalse(r.verified)
        self.assertFalse(r.f22_band_applied)

    def test_a_surplus_is_never_forgiven_as_f22(self):
        """F-22 only ever runs the device counter HIGH -- more bytes arriving
        than the puck claims to hold is never this finding (CLAUDE.md rule
        6: a wrong citation is worse than none)."""
        r = serial_job.verify_pull(self.ctx(trace_bytes_device=10000, got_csv_bytes=10001))
        self.assertFalse(r.verified)
        self.assertFalse(r.f22_band_applied)

    def test_header_only_region_is_an_empty_puck_not_a_surplus(self):
        """A real nrf52 puck always emits 't,mag\\n' (6 B) even with nothing
        stored (firmware/src/platform/nrf52/jh_store.cpp:1119-1126);
        trace_bytes only starts counting on the first append. tools/
        fake_device.py's `send_file()` sends NO header at all when rows are
        empty, so this shape cannot be produced over its wire -- driven
        directly, exactly as test_ingest.py's
        test_header_only_region_is_an_empty_puck_not_a_surplus does for
        _verify_ingest_bundle()."""
        r = serial_job.verify_pull(self.ctx(trace_bytes_device=0, got_csv_bytes=6))
        self.assertTrue(r.verified)

    def test_seven_bytes_against_zero_is_still_a_refusal(self):
        """Bounded at exactly 6: this forgives the header and nothing else."""
        r = serial_job.verify_pull(self.ctx(trace_bytes_device=0, got_csv_bytes=7))
        self.assertFalse(r.verified)

    def test_growth_within_the_after_window_verifies(self):
        """The puck keeps recording between the two `stats` reads
        (CONTRACT.md SS2.5b) -- tools/fake_device.py has no background
        recording to reproduce this, so it is driven directly."""
        r = serial_job.verify_pull(self.ctx(
            trace_bytes_device=1000, trace_bytes_after=1300, got_csv_bytes=1200))
        self.assertTrue(r.verified)
        self.assertIn("extra bytes", r.growth_note)

    def test_growth_past_the_after_window_is_still_a_refusal(self):
        r = serial_job.verify_pull(self.ctx(
            trace_bytes_device=1000, trace_bytes_after=1100, got_csv_bytes=1200))
        self.assertFalse(r.verified)

    def test_growth_with_no_after_reading_is_still_a_refusal(self):
        r = serial_job.verify_pull(self.ctx(trace_bytes_device=1000, got_csv_bytes=1200))
        self.assertFalse(r.verified)

    def test_missing_trace_bytes_device_is_unchecked_not_a_pass(self):
        r = serial_job.verify_pull(self.ctx(trace_bytes_device=None, got_csv_bytes=10))
        self.assertFalse(r.verified)

    def test_no_trace_data_at_all(self):
        r = serial_job.verify_pull(serial_job.PullContext(trace_format=None, jump_rows=0))
        self.assertFalse(r.verified)
        self.assertIn("No ride data arrived at all.", r.reasons)


# ------------------------------------------------------ verify_pull: shared

class TestVerifyPullSharedChecks(unittest.TestCase):
    def test_storage_down_refuses_even_when_the_arithmetic_matches(self):
        ctx = serial_job.PullContext(storage_down=True, trace_format="csv",
                                     trace_bytes_device=0, got_csv_bytes=0, jump_rows=0)
        self.assertFalse(serial_job.verify_pull(ctx).verified)

    def test_incomplete_chatter_outranks_matching_byte_counts(self):
        ctx = serial_job.PullContext(
            incomplete_text="# WARNING trace.bin INCOMPLETE -- 12 bytes never "
                           "reached the host",
            trace_format="csv", trace_bytes_device=10, got_csv_bytes=10, jump_rows=0)
        self.assertFalse(serial_job.verify_pull(ctx).verified)

    def test_jump_row_shortfall_refuses_when_stored_jumps_positive(self):
        ctx = serial_job.PullContext(stored_jumps_device=3, jump_rows=2, trace_format="csv",
                                     trace_bytes_device=0, got_csv_bytes=0)
        self.assertFalse(serial_job.verify_pull(ctx).verified)

    def test_zero_stored_jumps_skips_the_row_check(self):
        """0 legitimately means an empty puck (CONTRACT.md SS2.4 (b))."""
        ctx = serial_job.PullContext(stored_jumps_device=0, jump_rows=0, trace_format="csv",
                                     trace_bytes_device=0, got_csv_bytes=0)
        self.assertTrue(serial_job.verify_pull(ctx).verified)


# ------------------------------------------------------------ _decode_raw_body

class TestDecodeRawBody(unittest.TestCase):
    def test_clean_body_decodes_exactly(self):
        raw = b"hello world, this is a fake trace region of raw bytes"
        b64 = base64.b64encode(raw).decode()
        lines = [b64[i:i + 8] for i in range(0, len(b64), 8)]
        got, err = serial_job._decode_raw_body(lines)
        self.assertIsNone(err)
        self.assertEqual(got, raw)

    def test_corrupt_chunk_is_named_and_prior_bytes_are_kept(self):
        raw = b"0123456789abcdef"
        b64 = base64.b64encode(raw).decode()
        got, err = serial_job._decode_raw_body([b64, "!!!!not-base64!!!!"])
        self.assertEqual(err, "part of the ride data was unreadable")
        self.assertEqual(got, raw)

    def test_truncated_final_chunk_is_named(self):
        got, err = serial_job._decode_raw_body(["abc"])  # not a multiple of 4, no '='
        self.assertEqual(err, "the ride data stopped part-way through a chunk")
        self.assertEqual(got, b"")


# ----------------------------------------------------------------- helpers

class TestSmallHelpers(unittest.TestCase):
    def test_jump_row_count_excludes_the_header(self):
        self.assertEqual(serial_job._jump_row_count(
            ["n,takeoff_s,airtime_raw_s,airtime_s,height_m", "1,..", "2,.."]), 2)

    def test_jump_row_count_with_no_header_counts_every_row(self):
        self.assertEqual(serial_job._jump_row_count(["1,..", "2,.."]), 2)

    def test_jump_row_count_empty_is_zero(self):
        self.assertEqual(serial_job._jump_row_count([]), 0)

    def test_puck4_extracts_from_a_real_name(self):
        self.assertEqual(serial_job._puck4("JumpHeight-E2C4"), "E2C4")

    def test_puck4_falls_back_to_xxxx(self):
        self.assertEqual(serial_job._puck4(None), "xxxx")
        self.assertEqual(serial_job._puck4("Puck-7"), "xxxx")

    def test_extract_puck_name_last_match_wins(self):
        lines = ["# name=Old", "INFO src=abc", "# name=JumpHeight-E2C4"]
        self.assertEqual(serial_job._extract_puck_name(lines), "JumpHeight-E2C4")

    def test_extract_puck_name_absent_is_none(self):
        self.assertIsNone(serial_job._extract_puck_name(["INFO src=abc"]))

    def test_log_lines_drops_file_bodies_but_keeps_in_frame_chatter(self):
        raw = ["INFO src=abc", "FILE trace.csv BEGIN", "t,mag", "1.0,1.0",
               "# WARNING trace.csv INCOMPLETE -- 4 bytes never reached the host",
               "FILE trace.csv END", "STATS stored_jumps=0"]
        out = serial_job._log_lines(raw)
        self.assertEqual(out, ["INFO src=abc", "FILE trace.csv BEGIN",
                               "# WARNING trace.csv INCOMPLETE -- 4 bytes never "
                               "reached the host",
                               "FILE trace.csv END", "STATS stored_jumps=0"])


# ------------------------------------------------------------ _build_manifest

class TestBuildManifestUptimePrecision(unittest.TestCase):
    """tools/fake_device.py's STATS never carries uptime_s at all (CONTRACT.md
    Appendix B1), so this exact field can only be driven directly."""

    def _manifest(self, uptime_s_str):
        from datetime import datetime, timezone
        synced_at = datetime(2026, 9, 13, 14, 32, 7, tzinfo=timezone.utc)
        verify = serial_job.VerifyResult(reasons=[])
        return serial_job._build_manifest(
            puck_name="JumpHeight-E2C4", info_kv={"fw": "0.4.3", "src": "abc123"},
            cal_line="CAL airtime_offset_s=0.0192 height_scale=1.000",
            synced_at=synced_at, stats_before_line="STATS stored_jumps=0",
            stats_after_line="STATS stored_jumps=0",
            stats_before_kv={"uptime_s": uptime_s_str, "stored_jumps": "0"},
            stats_after_kv={"stored_jumps": "0"}, info_lines=[], selftest_lines=[],
            trace_format="csv", log_hz=50, trace_raw_len=None, trace_crc_hex=None,
            verify=verify, jump_rows=0, transport_bytes=0, transport_seconds=1.0)

    def test_uptime_seconds_keeps_its_fraction_not_truncated_to_an_int(self):
        """A caught bug: reusing the integer-only _int_or_none() here rounded
        12345.678 down to 12345, silently mis-dating trace_epoch_utc by up
        to a second under a green checkmark -- CLAUDE.md rule 3's shape."""
        m = self._manifest("12345.678")
        self.assertEqual(m["uptime_s"], 12345.678)

    def test_trace_epoch_utc_is_synced_at_minus_uptime_to_the_millisecond(self):
        m = self._manifest("12345.678")
        # synced_at is 2026-09-13T14:32:07.000Z; uptime_s=12345.678s back.
        self.assertEqual(m["trace_epoch_utc"], "2026-09-13T11:06:21.322Z")

    def test_missing_uptime_leaves_both_fields_null(self):
        m = self._manifest(None)
        self.assertIsNone(m["uptime_s"])
        self.assertIsNone(m["trace_epoch_utc"])


# ------------------------------------------------- run_job, real fake wire

class TestRunJobAgainstFakeDevice(unittest.TestCase):
    def _run(self, scenario="session", extra=None):
        proc, port = _spawn_fake(scenario, extra)
        spool = tempfile.mkdtemp()
        try:
            return serial_job.run_job(port, spool), spool
        finally:
            _kill(proc)

    def test_happy_path_traceraw(self):
        result, spool = self._run("session")
        self.assertTrue(result.verified, result.reasons)
        self.assertEqual(result.jumps, 4)  # the demo session's 4 known jumps
        self.assertTrue(result.bundle_path.exists())
        self.assertEqual(result.src, "fakedev0")
        # tools/fake_device.py's `info` never emits '# name=' (CONTRACT.md
        # Appendix B2) -- documented gap, not a bug here.
        self.assertIsNone(result.puck_name)
        self.assertIsNotNone(result.stats_before)
        self.assertIsNotNone(result.stats_after)

        with zipfile.ZipFile(result.bundle_path) as zf:
            names = set(zf.namelist())
            self.assertEqual(names, {"manifest.json", "jumps.csv", "trace.bin",
                                     "notes.txt", "device.log"})
            manifest = json.loads(zf.read("manifest.json"))
            jumps_csv = zf.read("jumps.csv").decode()

        self.assertEqual(manifest["trace_format"], "jhtrace-v2-b64")
        self.assertTrue(manifest["verified"])
        self.assertEqual(manifest["cleared"], False)
        self.assertIsNotNone(manifest["trace_crc32"])
        self.assertGreater(manifest["trace_raw_bytes"], 0)
        self.assertIsNone(manifest["trace_bytes_got"])  # csv-path-only field
        self.assertEqual(manifest["transfer"]["transport"], "usb")
        self.assertTrue(jumps_csv.startswith("n,takeoff_s,airtime_raw_s,airtime_s,height_m"))
        # tools/fake_device.py's STATS never carries uptime_s (CONTRACT.md
        # Appendix B1) -- trace_epoch_utc is null, the documented gap.
        self.assertIsNone(manifest["uptime_s"])
        self.assertIsNone(manifest["trace_epoch_utc"])
        # synced_at_local must be exactly what tools/jump's
        # _ingest_session_dir_name() can datetime.fromisoformat() (CONTRACT.md SS4.3).
        from datetime import datetime as _dt
        _dt.fromisoformat(manifest["synced_at_local"])

    def test_bundle_passes_jump_ingest_unchanged(self):
        result, spool = self._run("session")
        with tempfile.TemporaryDirectory() as out:
            r = run_cli(["ingest", str(result.bundle_path), "--out", out])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            sessions = list(Path(out).iterdir())
            self.assertEqual(len(sessions), 1)
            self.assertTrue((sessions[0] / "session.json").exists())
            session_json = json.loads((sessions[0] / "session.json").read_text())
            self.assertTrue(session_json["verified"])

    def test_csv_fallback_when_traceraw_is_unknown(self):
        result, spool = self._run("session", ["--no-traceraw"])
        self.assertTrue(result.verified, result.reasons)
        with zipfile.ZipFile(result.bundle_path) as zf:
            names = set(zf.namelist())
            manifest = json.loads(zf.read("manifest.json"))
        self.assertIn("trace.csv", names)
        self.assertNotIn("trace.bin", names)
        self.assertEqual(manifest["trace_format"], "csv")
        self.assertTrue(manifest["verified"])
        self.assertEqual(manifest["trace_bytes_device"], manifest["trace_bytes_got"])

        # And it too must import unchanged.
        with tempfile.TemporaryDirectory() as out:
            r = run_cli(["ingest", str(result.bundle_path), "--out", out])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_f22_band_verifies_over_the_real_wire(self):
        """--trace-bytes-overreport skews STATS trace_bytes exactly the way a
        FULL region's live counter does (audit F-22) while the `trace`
        command still sends the true, unskewed body -- a real -765-shaped
        gap, driven end to end through the actual protocol."""
        result, spool = self._run("session", ["--no-traceraw",
                                              "--trace-bytes-overreport", "765"])
        self.assertTrue(result.verified, result.reasons)
        with zipfile.ZipFile(result.bundle_path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        self.assertTrue(manifest["f22_band_applied"])
        self.assertEqual(
            manifest["trace_bytes_device"] - manifest["trace_bytes_got"], 765)

    def test_short_transfer_outside_the_band_does_not_verify(self):
        """5,000 B is unambiguously outside F-22's 800 B band -- CONTRACT.md
        SS2.5a's own boundary. Still written: 'an unverified bundle is
        exactly the one Josh most wants to look at.'"""
        result, spool = self._run("session", ["--no-traceraw",
                                              "--trace-bytes-overreport", "5000"])
        self.assertFalse(result.verified)
        self.assertTrue(any("Only" in r and "bytes of ride data" in r
                            for r in result.reasons), result.reasons)
        self.assertTrue(result.bundle_path.exists())
        with zipfile.ZipFile(result.bundle_path) as zf:
            manifest = json.loads(zf.read("manifest.json"))
        self.assertFalse(manifest["verified"])
        self.assertFalse(manifest["f22_band_applied"])

    def test_non_fallback_traceraw_error_raises_and_writes_no_bundle(self):
        """CONTRACT.md SS1.4: 'storage_down' is reported as-is and is NOT a
        fallback trigger -- there is no CSV path that recovers from the
        storage layer being down. G3: the puck stays untouched and nothing
        partial is left behind for the next plug-in to trip over."""
        proc, port = _spawn_fake("session", ["--traceraw-error", "storage_down"])
        spool = tempfile.mkdtemp()
        try:
            with self.assertRaises(serial_job.PullFailed) as ctx:
                serial_job.run_job(port, spool)
            self.assertIn("storage_down", str(ctx.exception))
        finally:
            _kill(proc)
        self.assertEqual(list(Path(spool).iterdir()), [])


# --------------------------------------------------- clear_puck, real wire

class TestClearPuckAgainstFakeDevice(unittest.TestCase):
    def test_genuine_clear_reports_ok_and_tracecheck_agrees(self):
        proc, port = _spawn_fake("session")
        try:
            result = serial_job.clear_puck(port)
        finally:
            _kill(proc)
        self.assertTrue(result.ok)
        self.assertEqual(result.stored_jumps_after, 0)
        self.assertEqual(result.trace_bytes_after, 0)
        self.assertIsNotNone(result.tracecheck)
        self.assertIsNone(result.tracecheck.reason)
        self.assertEqual(result.tracecheck.fast, 0)
        self.assertEqual(result.tracecheck.slow, 0)

    def test_clearing_an_already_empty_puck_is_still_ok(self):
        proc, port = _spawn_fake("ok")  # no jumps, no trace, from boot
        try:
            result = serial_job.clear_puck(port)
        finally:
            _kill(proc)
        self.assertTrue(result.ok)
        self.assertEqual(result.stored_jumps_after, 0)
        self.assertEqual(result.trace_bytes_after, 0)


# ---------------------------------------- clear_puck: an injected stub device

class _LyingClearDevice:
    """Answers `clear` OK, but the very next `stats` still shows data --
    the ONE shape docs/sync-agent-plan.md's step 7 gate exists to catch, and
    the one tools/fake_device.py's real `clear` (which genuinely empties)
    cannot produce. Satisfies exactly the surface serial_job.py calls on a
    Device: .drain_boot()/.command()/.close() -- same seam shape as
    tools/puckd/flash.py's own injected _FakeDevice standing in for
    hardware."""

    def __init__(self, stats_after_clear):
        self._stats_after_clear = stats_after_clear
        self.sent = []

    def drain_boot(self, timeout=5.0):
        pass

    def command(self, cmd, timeout=20):
        self.sent.append(cmd)
        first = cmd.split()[0]
        if first == "clear":
            return ["# cleared stored data"]
        if first == "stats":
            return [self._stats_after_clear]
        return [f"ERR unknown_command {first}"]

    def close(self):
        pass


class TestClearPuckRefusesALie(unittest.TestCase):
    def test_refuses_when_stats_after_clear_still_shows_data(self):
        dev = _LyingClearDevice(
            "STATS session_jumps=0 stored_jumps=3 stored_best_m=0.500 trace_bytes=1500")
        result = serial_job.clear_puck(
            "/dev/not-a-real-port", device_factory=lambda p: dev)
        self.assertFalse(result.ok)
        self.assertEqual(result.stored_jumps_after, 3)
        self.assertEqual(result.trace_bytes_after, 1500)
        # A clear that lied is never asked to walk a region it claims is
        # already gone.
        self.assertIsNone(result.tracecheck)
        self.assertEqual(dev.sent, ["clear", "stats"])

    def test_refuses_when_only_jumps_are_left(self):
        dev = _LyingClearDevice(
            "STATS session_jumps=0 stored_jumps=1 stored_best_m=0.0 trace_bytes=0")
        result = serial_job.clear_puck(
            "/dev/not-a-real-port", device_factory=lambda p: dev)
        self.assertFalse(result.ok)

    def test_refuses_when_only_trace_bytes_are_left(self):
        dev = _LyingClearDevice(
            "STATS session_jumps=0 stored_jumps=0 stored_best_m=0.0 trace_bytes=42")
        result = serial_job.clear_puck(
            "/dev/not-a-real-port", device_factory=lambda p: dev)
        self.assertFalse(result.ok)


class _ClearThenLyingTracecheckDevice:
    """stats confirms 0/0, but a full re-walk (`tracecheck`) still finds
    bytes -- main.cpp's own wording for a fast/slow mismatch is 'the slow
    number is the correct one', so this must refuse too."""

    def command(self, cmd, timeout=20):
        first = cmd.split()[0]
        if first == "clear":
            return ["# cleared stored data"]
        if first == "stats":
            return ["STATS session_jumps=0 stored_jumps=0 trace_bytes=0"]
        if first == "tracecheck":
            return ["# tracecheck fast=0 slow=128 DISAGREE -- the slow number "
                    "is the correct one", "ERR tracecheck mismatch"]
        return [f"ERR unknown_command {first}"]

    def drain_boot(self, timeout=5.0):
        pass

    def close(self):
        pass


class TestClearPuckDistrustsAFastZeroTracecheckContradicts(unittest.TestCase):
    def test_a_full_rewalk_that_still_finds_bytes_refuses(self):
        result = serial_job.clear_puck(
            "/dev/not-a-real-port",
            device_factory=lambda p: _ClearThenLyingTracecheckDevice())
        self.assertFalse(result.ok)
        self.assertEqual(result.tracecheck.slow, 128)


# ---------------------------------------------------------- read_stats

class TestReadStatsAgainstFakeDevice(unittest.TestCase):
    def test_reads_battery_and_counts(self):
        proc, port = _spawn_fake("session", ["--vbat-mv", "3920", "--charging"])
        try:
            stats = serial_job.read_stats(port)
        finally:
            _kill(proc)
        self.assertIsNone(stats["error"])
        self.assertEqual(stats["vbat_mv"], 3920)
        self.assertEqual(stats["chg"], 1)
        self.assertEqual(stats["stored_jumps"], 4)
        self.assertGreater(stats["trace_bytes"], 0)
        # tools/fake_device.py never emits STATS trace_full=1 (firmware/src/
        # main.cpp:850-856, F-36's fix) -- False against the fake is the
        # documented gap, not a claim this covers a full region.
        self.assertFalse(stats["trace_full"])

    def test_no_battery_device_reports_none_not_a_false_zero(self):
        proc, port = _spawn_fake("ok", ["--no-battery"])
        try:
            stats = serial_job.read_stats(port)
        finally:
            _kill(proc)
        self.assertIsNone(stats["error"])
        self.assertIsNone(stats["vbat_mv"])
        self.assertIsNone(stats["batt_pct"])
        self.assertIsNone(stats["chg"])

    def test_never_raises_on_a_command_the_port_refuses(self):
        """A stub that only ever ERRs -- read_stats must come back with
        `error` set, never propagate an exception into a 60 s poll loop."""
        class _AlwaysErrDevice:
            def drain_boot(self, timeout=5.0):
                pass

            def command(self, cmd, timeout=20):
                return [f"ERR unknown_command {cmd.split()[0]}"]

            def close(self):
                pass

        stats = serial_job.read_stats(
            "/dev/not-a-real-port", device_factory=lambda p: _AlwaysErrDevice())
        self.assertIsNotNone(stats["error"])
        self.assertIsNone(stats["vbat_mv"])
        self.assertIsNone(stats["trace_full"])


class _ScriptedStatsDevice:
    """One canned STATS line, for the two shapes no fake_device.py scenario
    can produce: the real trace_full=1 adder key, and fs=down poisoning it
    alongside stored_jumps/trace_bytes."""

    def __init__(self, stats_line):
        self._line = stats_line

    def drain_boot(self, timeout=5.0):
        pass

    def command(self, cmd, timeout=20):
        if cmd.split()[0] == "stats":
            return [self._line]
        return [f"ERR unknown_command {cmd.split()[0]}"]

    def close(self):
        pass


class TestReadStatsTraceFullAndStorageDown(unittest.TestCase):
    def test_trace_full_key_present_reports_true(self):
        line = ("STATS session_jumps=0 stored_jumps=2 stored_best_m=1.0 "
                "trace_bytes=14093819 vbat_mv=3920 batt_pct=71 chg=0 trace_full=1")
        stats = serial_job.read_stats(
            "/dev/not-a-real-port", device_factory=lambda p: _ScriptedStatsDevice(line))
        self.assertTrue(stats["trace_full"])
        self.assertEqual(stats["trace_bytes"], 14093819)

    def test_trace_full_key_absent_reports_false_not_none(self):
        line = "STATS session_jumps=0 stored_jumps=0 trace_bytes=0 vbat_mv=3920"
        stats = serial_job.read_stats(
            "/dev/not-a-real-port", device_factory=lambda p: _ScriptedStatsDevice(line))
        self.assertFalse(stats["trace_full"])
        self.assertIsNone(stats["error"])

    def test_fs_down_poisons_counts_and_trace_full_but_not_battery(self):
        line = ("STATS session_jumps=0 stored_jumps=0 trace_bytes=0 fs=down "
                "vbat_mv=3920 batt_pct=71 chg=0")
        stats = serial_job.read_stats(
            "/dev/not-a-real-port", device_factory=lambda p: _ScriptedStatsDevice(line))
        self.assertIsNone(stats["stored_jumps"])
        self.assertIsNone(stats["trace_bytes"])
        self.assertIsNone(stats["trace_full"])
        self.assertEqual(stats["vbat_mv"], 3920)


# ------------------------------------------------- the port going away

class _VanishedDevice:
    """The cable pulled mid-job. pyserial answers a read on a device node
    that no longer exists with SerialException -- a subclass of OSError, and
    NOT the TimeoutError every command() call site in serial_job.py catches.
    Modelled with the exact errno macOS produces (ENODEV, "Device not
    configured"), raised from drain_boot() because that is the first thing
    every one of these entry points does after opening.

    Before 2026-09-13 each of the four tests below raised out of the
    function under test, through daemon.run_job_cycle(), through
    daemon.run_forever(), and off the end of the daemon THREAD -- the menu
    bar stayed up and syncing was dead with nothing saying so.
    """

    def __init__(self, port=None):
        pass

    def drain_boot(self, timeout=5.0):
        raise OSError(6, "Device not configured")

    def command(self, cmd, timeout=20.0):
        raise OSError(6, "Device not configured")

    def close(self):
        pass


class _UnopenableDevice:
    """The port is gone before it is even opened -- find_puck_port() saw it
    2 s ago, the rider unplugged it since."""

    def __init__(self, port=None):
        raise OSError(2, "No such file or directory")


class TestPortVanishesMidCall(unittest.TestCase):
    def test_read_stats_returns_an_error_reading_never_raises(self):
        stats = serial_job.read_stats("/dev/cu.usbmodemGONE",
                                      device_factory=_VanishedDevice)
        self.assertIsNotNone(stats["error"])
        self.assertIsNone(stats["batt_pct"])
        self.assertIsNone(stats["stored_jumps"])

    def test_read_src_is_none_never_raises(self):
        self.assertIsNone(serial_job.read_src("/dev/cu.usbmodemGONE",
                                              device_factory=_VanishedDevice))

    def test_clear_puck_refuses_never_raises(self):
        for factory in (_VanishedDevice, _UnopenableDevice):
            with self.subTest(factory=factory.__name__):
                result = serial_job.clear_puck("/dev/cu.usbmodemGONE",
                                               device_factory=factory)
                self.assertFalse(result.ok)
                self.assertIsNone(result.stored_jumps_after)

    def test_run_job_raises_pull_failed_marked_port_gone_not_a_bare_oserror(self):
        for factory in (_VanishedDevice, _UnopenableDevice):
            with self.subTest(factory=factory.__name__):
                with tempfile.TemporaryDirectory() as td:
                    with self.assertRaises(serial_job.PullFailed) as ctx:
                        serial_job.run_job("/dev/cu.usbmodemGONE", td,
                                           device_factory=factory)
                    self.assertTrue(ctx.exception.port_gone)
                    self.assertEqual([], list(Path(td).glob("*.zip")),
                                     "nothing is written for a pull that never started")

    def test_an_ordinary_pull_failure_is_not_marked_port_gone(self):
        """The flag must separate "he unplugged it" from "the puck refused",
        or the daemon's silence would swallow the failures Nick DOES need to
        hear about."""
        exc = serial_job.PullFailed("the puck refused 'jumps': ERR storage_down")
        self.assertFalse(exc.port_gone)


if __name__ == "__main__":
    unittest.main()
