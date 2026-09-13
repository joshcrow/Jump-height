"""P3 adversarial review (docs/sync-agent-plan.md:86) -- the gate checks the
P1/P2 test files did NOT defend, plus the one sync.js divergence found and
fixed in tools/puckd/serial_job.py.

Every test here was written because a MUTATION of the shipped code survived
the existing suite. Each one is named with the mutation that produced it, so
a later reader can reproduce the finding rather than take it on trust:

  * G5 (docs/sync-agent-plan.md:65) -- "the port is opened by exactly one
    process; the web page and the agent never fight (agent releases the port
    when idle)". THE ONLY GATE WITH NO TEST AT ALL. Replacing
    serial_job.run_job()'s and read_stats()'s `finally: dev.close()` with
    `finally: pass` left all 58 tests in test_puckd_serial_job.py +
    test_puckd_daemon.py green. A leaked handle is exactly what makes the
    agent and Chrome's Web Serial page fight over /dev/cu.usbmodem*, and it
    is invisible to every assertion those files make.

  * G3 (docs/sync-agent-plan.md:63) -- "the spool keeps the bundle".
    Adding `Path(result.bundle_path).unlink()` to daemon.run_job_cycle()'s
    ordinary `if not uploaded:` branch left all 31 tests in
    test_puckd_daemon.py green. Only the UNEXPECTED-exception path
    (test_an_unexpected_upload_exception_degrades_to_needs_you_not_a_crash)
    asserted the bundle survives; the ordinary failed-upload path -- the one
    that actually happens when Nick's wifi drops -- did not.

  * The in-frame '#' chatter split (web/sync/sync.js:629-646). Measured on
    this module before serial_job._csv_body() existed: a `jumps` frame two
    rows short, carrying the firmware's own "# WARNING jumps.csv INCOMPLETE"
    line (firmware/src/main.cpp:463-466), read as jump_rows=3 against
    stored_jumps=3 -- the warning became the missing third jump and
    verifyPull's check (b) could not fire. That is, verbatim, the failure
    sync.js:638-640 records having measured and fixed on the page. Check (a)
    still refused the bundle, so it was never a false clear -- but the
    cross-check on the ONE file with no crc and no byte count of its own was
    silenced, the CSV byte count ran 88 B over a 28 B device count (a SHORT
    trace reported as a surplus), and the bundle's own jumps.csv/trace.csv
    shipped a '#' row no other CONTRACT.md SS2.1 producer writes.

Never edits tools/fake_device.py, tools/jump, web/sync/, or another agent's
test file -- CLAUDE.md's "never edit a file you were not assigned".

Run via: python3 -m pytest tools/tests/test_puckd_gates_p3.py -q
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools"))

from puckd import daemon, serial_job  # noqa: E402

_jump = serial_job._jump()


# ------------------------------------------------------------ fake_device glue

def _spawn_fake(scenario="session", extra=None):
    """Same technique as test_puckd_serial_job.py's own _spawn_fake()."""
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


# ------------------------------------------------------------------- G5 seam

class _PortLedger:
    """A device_factory that wraps the REAL tools/jump.Device and keeps the
    open/close ledger G5 is about.

    `live` is the number of handles currently open on the port. G5 is two
    claims about that number, and this class is what makes both assertable:

        "opened by exactly one process"  -> live never exceeds 1
        "releases the port when idle"    -> live is back to 0 the moment any
                                            serial_job/daemon entry point
                                            returns

    It wraps the real Device rather than scripting the wire, so the ledger
    is counting real pyserial handles on a real pty -- the same "wrap the
    real thing" precedent test_puckd_daemon.py's _LyingClearWrapper sets.
    """

    def __init__(self, inner=None):
        self._inner = inner or _jump.Device
        self.opened = 0
        self.closed = 0
        self.max_live = 0

    @property
    def live(self) -> int:
        return self.opened - self.closed

    def __call__(self, port_path):
        real = self._inner(port_path)
        self.opened += 1
        self.max_live = max(self.max_live, self.live)
        ledger = self

        class _Tracked:
            def drain_boot(self, timeout=5.0):
                return real.drain_boot(timeout=timeout)

            def command(self, cmd, timeout=20.0):
                return real.command(cmd, timeout=timeout)

            def close(self):
                # close() is idempotent on tools/jump's Device; the ledger
                # must be too, or a double close would read as a negative
                # `live` and hide a genuine leak elsewhere.
                if not getattr(self, "_closed", False):
                    self._closed = True
                    ledger.closed += 1
                return real.close()

        return _Tracked()


class TestG5PortIsReleasedWhenIdle(unittest.TestCase):
    """docs/sync-agent-plan.md:65, G5. Undefended before this class existed:
    `finally: dev.close()` could be deleted from run_job() or read_stats()
    and the whole puckd suite stayed green."""

    def test_run_job_releases_the_port_before_it_returns(self):
        proc, port = _spawn_fake("session")
        ledger = _PortLedger()
        spool = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, spool, ignore_errors=True)
        try:
            result = serial_job.run_job(port, spool, device_factory=ledger)
        finally:
            _kill(proc)
        self.assertTrue(result.bundle_path.exists())
        self.assertEqual(ledger.opened, 1, "one handle for one job")
        self.assertEqual(
            ledger.live, 0,
            "G5: run_job() must release the port before it returns -- a "
            "handle still open here is the agent fighting the rider's page")

    def test_a_failed_pull_still_releases_the_port(self):
        """G3 and G5 at once: the pull that raises PullFailed is exactly the
        one an interrupted job produces, and it must leave the port free for
        the retry (and for Chrome) all the same."""
        proc, port = _spawn_fake("session", ["--traceraw-error", "storage_down"])
        ledger = _PortLedger()
        spool = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, spool, ignore_errors=True)
        try:
            with self.assertRaises(serial_job.PullFailed):
                serial_job.run_job(port, spool, device_factory=ledger)
        finally:
            _kill(proc)
        self.assertEqual(ledger.opened, 1)
        self.assertEqual(ledger.live, 0, "G5: released even on the failure path")

    def test_read_stats_releases_the_port_every_poll(self):
        """Step 10 polls every 60 s for as long as the puck is plugged in.
        One leaked handle per poll is one leaked handle per minute."""
        proc, port = _spawn_fake("session")
        ledger = _PortLedger()
        try:
            for _ in range(3):
                stats = serial_job.read_stats(port, device_factory=ledger)
                self.assertIsNone(stats["error"], stats)
                self.assertEqual(
                    ledger.live, 0,
                    "G5: read_stats() must release the port after every poll")
        finally:
            _kill(proc)
        self.assertEqual(ledger.opened, 3)
        self.assertEqual(ledger.closed, 3)

    def test_clear_puck_releases_the_port(self):
        proc, port = _spawn_fake("session")
        ledger = _PortLedger()
        try:
            result = serial_job.clear_puck(port, device_factory=ledger)
        finally:
            _kill(proc)
        self.assertTrue(result.ok, result)
        self.assertEqual(ledger.live, 0, "G5: clear_puck() releases the port")

    def test_never_two_handles_at_once_across_a_whole_attachment(self):
        """The G5 sentence's other half -- "opened by exactly one process".
        run_job_cycle() opens the port three separate times (run_job,
        clear_puck, then the attachment's first stats poll); none of those
        may overlap, and none may still be open when the loop goes idle and
        the rider might open the page."""
        proc, port = _spawn_fake("session")
        ledger = _PortLedger()
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        rclone = _write_fake_rclone(tmp)
        try:
            with patch.dict(os.environ, rclone):
                cfg = daemon.DaemonConfig(
                    home_dir=tmp / "home", spool_dir=tmp / "spool", site_url="",
                    notifier=lambda title, body: None,
                    garmin_module=_NoGarmin(), device_factory=ledger,
                    sleep=lambda s: None)
                daemon.run_forever(cfg, find_port=lambda: port, max_iterations=3)
        finally:
            _kill(proc)
        self.assertGreaterEqual(ledger.opened, 3, "the job really ran")
        self.assertEqual(ledger.max_live, 1,
                         "G5: never two handles on the port at once")
        self.assertEqual(ledger.live, 0,
                         "G5: the loop holds no handle between ticks")


class _NoGarmin:
    """The four calls daemon._run_garmin() makes; signed out, so the leg is
    a no-op that never reaches the network."""

    def is_signed_in(self):
        return False

    def fetch_new(self, since_iso, out_dir):
        return []

    def last_seen(self, store):
        return None

    def mark_seen(self, store, iso):
        pass


# ------------------------------------------------------------- fake rclone

_FAKE_RCLONE_SRC = '''\
#!/usr/bin/env python3
"""Fake rclone for tools/tests/test_puckd_gates_p3.py -- the four subcommands
upload.py issues, "gdrive:<path>" backed by $FAKE_RCLONE_STORE/<path>.
FAKE_RCLONE_FAIL_COPY makes `copy` exit 1 with nothing written, which is what
an upload failing outright (no network) looks like to upload.py. Written
fresh here rather than imported from a sibling test file -- CLAUDE.md's
"never edit a file you were not assigned" cuts both ways.
"""
import json, os, shutil, sys
from pathlib import Path

STORE = Path(os.environ["FAKE_RCLONE_STORE"])


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "config":
        print("{}")
        return 0
    if cmd == "listremotes":
        print("gdrive:")
        return 0
    if cmd == "copy":
        if os.environ.get("FAKE_RCLONE_FAIL_COPY"):
            print("Failed to copy: connection refused", file=sys.stderr)
            return 1
        src, dst = args[1], args[2]
        dest_dir = STORE / dst[len("gdrive:"):]
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_dir / Path(src).name)
        return 0
    if cmd == "lsjson":
        d = STORE / args[1][len("gdrive:"):]
        entries = []
        if d.is_dir():
            entries = [{"Name": p.name, "Size": p.stat().st_size, "IsDir": False}
                       for p in d.iterdir()]
        print(json.dumps(entries))
        return 0
    print("unknown command %r" % cmd, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _write_fake_rclone(tmp: Path, fail_copy: bool = False) -> dict:
    path = tmp / "fake_rclone.py"
    path.write_text(_FAKE_RCLONE_SRC)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    store = tmp / "drive"
    store.mkdir(exist_ok=True)
    env = {"PUCKD_RCLONE": str(path), "FAKE_RCLONE_STORE": str(store)}
    if fail_copy:
        env["FAKE_RCLONE_FAIL_COPY"] = "1"
    return env


# ----------------------------------------------- G3: the spool keeps the bundle

class TestG3SpoolKeepsTheBundle(unittest.TestCase):
    """docs/sync-agent-plan.md:63, G3: "a job interrupted anywhere leaves the
    puck recoverable: ... the spool keeps the bundle; the next plug-in
    retries". Undefended on the ORDINARY upload-failure path before this
    class existed -- only the unexpected-exception path asserted it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.spool = self.tmp / "spool"

    def _cfg(self, env):
        return daemon.DaemonConfig(
            home_dir=self.tmp / "home", spool_dir=self.spool, site_url="",
            notifier=lambda title, body: None, garmin_module=_NoGarmin(),
            sleep=lambda s: None)

    def test_a_copy_that_fails_outright_leaves_the_bundle_on_disk(self):
        proc, port = _spawn_fake("session")
        env = _write_fake_rclone(self.tmp, fail_copy=True)
        try:
            with patch.dict(os.environ, env):
                report = daemon.run_job_cycle(port, self._cfg(env))
                stats = serial_job.read_stats(port)
        finally:
            _kill(proc)

        self.assertTrue(report.pulled)
        self.assertFalse(report.uploaded)
        self.assertFalse(report.cleared)
        # The puck still holds the only other copy (G1/G3) ...
        self.assertEqual(stats["stored_jumps"], 4)
        # ... and the spool still holds this one. Both halves matter: the
        # bundle is what a retry would have to rebuild from scratch, and on
        # an UNVERIFIED ride it is the only artefact Josh ever gets.
        self.assertIsNotNone(report.bundle_path)
        self.assertTrue(report.bundle_path.exists(),
                        "G3: the spool keeps the bundle when the upload fails")
        self.assertEqual(
            [p.name for p in sorted(self.spool.glob("*.zip"))],
            [report.bundle_path.name],
            "exactly the one bundle this job wrote, still in the spool")
        # Readable, not a zero-byte stub: a kept bundle that cannot be opened
        # is the silent-failure shape CLAUDE.md rule 3 is about.
        with zipfile.ZipFile(report.bundle_path) as z:
            self.assertIn("manifest.json", z.namelist())
            self.assertTrue(json.loads(z.read("manifest.json"))["verified"])

    def test_a_second_pull_in_the_same_minute_does_not_clobber_the_first(self):
        """The collision the daemon's own recovery instruction causes.
        NEEDS_YOU_UPLOAD_ACTION tells Nick "Unplug the puck and plug it back
        in", which runs a second full pull -- inside the same MINUTE as the
        first, and the bundle name (web/sync/sync.js:1949-1951) has only
        minute resolution. Measured 2026-09-13 before _write_bundle() grew
        its suffix: three run_job_cycle() calls in one minute left exactly
        ONE zip in the spool.

        The page can name bundles this way because it writes to Downloads,
        where the browser de-duplicates and a person is watching. An
        unattended spool has neither.
        """
        proc, port = _spawn_fake("session")
        env = _write_fake_rclone(self.tmp, fail_copy=True)
        try:
            with patch.dict(os.environ, env):
                cfg = self._cfg(env)
                first = daemon.run_job_cycle(port, cfg)
                second = daemon.run_job_cycle(port, cfg)
                third = daemon.run_job_cycle(port, cfg)
        finally:
            _kill(proc)

        paths = [first.bundle_path, second.bundle_path, third.bundle_path]
        self.assertEqual(len(set(paths)), 3,
                         "three pulls, three bundles -- not one name reused")
        for p in paths:
            self.assertTrue(p.exists(), f"G3: {p.name} was clobbered")
        self.assertEqual(len(list(self.spool.glob("*.zip"))), 3)
        # The shape the page writes is still what the FIRST one gets; only a
        # collision adds anything (CONTRACT.md SS4.1).
        self.assertRegex(first.bundle_path.name,
                         r"^jumpheight-[0-9A-Za-z]{4}-\d{8}-\d{4}\.zip$")

    def test_an_unverified_ride_is_still_spooled(self):
        """The other interrupted shape: the pull completed structurally but
        the content did not check out. CONTRACT.md's own words -- "an
        unverified bundle is exactly the one Josh most wants to look at"."""
        proc, port = _spawn_fake(
            "session", ["--no-traceraw", "--trace-bytes-overreport", "5000"])
        env = _write_fake_rclone(self.tmp)
        try:
            with patch.dict(os.environ, env):
                report = daemon.run_job_cycle(port, self._cfg(env))
        finally:
            _kill(proc)

        self.assertTrue(report.pulled)
        self.assertFalse(report.verified, report.reasons)
        self.assertFalse(report.cleared, "G1: never clear over an unverified ride")
        self.assertTrue(report.bundle_path.exists(),
                        "G3: the spool keeps the unverified bundle too")


# ------------------------------ the sync.js in-frame chatter split (check (b))

class _ShortJumpsDevice:
    """A puck whose `jumps` and `trace` frames came up short, carrying the
    firmware's own in-frame warning (firmware/src/main.cpp:463-466,
    printFileFramed(): the "# WARNING <name> INCOMPLETE" line is printed
    AFTER the body and BEFORE "FILE <name> END").

    tools/fake_device.py has no knob for a short download -- there is no
    --incomplete flag and nothing in it drops bytes -- and it is not this
    file's to add one to. So the shape is scripted directly, the same
    precedent test_puckd_serial_job.py's own _LyingClearDevice sets for the
    one shape the reference device cannot produce.

    stored_jumps=3 against 2 real rows is the whole point: it is the exact
    off-by-one web/sync/sync.js:638-640 says it measured, where the warning
    line silently becomes the missing third jump.
    """

    def drain_boot(self, timeout=5.0):
        pass

    def command(self, cmd, timeout=20.0):
        c = cmd.split()[0]
        if c == "info":
            return ["INFO fw=0.5.0 src=5c80a436 log_hz=50", "# name=JumpHeight-E2C4"]
        if c == "stats":
            return ["STATS session_jumps=3 stored_jumps=3 stored_best_m=0.500 "
                    "trace_bytes=28 uptime_s=100.000 vbat_mv=3900 batt_pct=80 chg=0"]
        if c == "jumps":
            return ["FILE jumps.csv BEGIN",
                    "n,t_s,height_m",
                    "1,1.0,0.50",
                    "2,2.0,0.60",
                    "# WARNING jumps.csv INCOMPLETE — 18 bytes never reached "
                    "the host; re-run the download",
                    "FILE jumps.csv END"]
        if c == "traceraw":
            return ["ERR unknown_command traceraw"]
        if c == "trace":
            return ["FILE trace.csv BEGIN",
                    "t,mag",
                    "1.0,1.0",
                    "2.0,1.0",
                    "# WARNING trace.csv INCOMPLETE — 9 bytes never reached "
                    "the host; re-run the download",
                    "FILE trace.csv END"]
        if c == "selftest":
            return ["SELFTEST ok=1"]
        return [f"ERR unknown_command {c}"]

    def close(self):
        pass


class TestInFrameChatterIsNotABodyRow(unittest.TestCase):
    """web/sync/sync.js:629-646's onLine() split, which tools/jump's
    parse_file_sections() (tools/jump:1585-1596) does NOT make -- it keeps
    every in-frame line, chatter included. serial_job._csv_body() is the
    restatement; these tests are what fail if it is removed."""

    def setUp(self):
        self.spool = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.spool, ignore_errors=True)
        self.result = serial_job.run_job(
            "/dev/not-a-real-port", self.spool,
            device_factory=lambda p: _ShortJumpsDevice())

    def test_the_warning_line_is_not_counted_as_a_jump(self):
        self.assertEqual(
            self.result.jumps, 2,
            "2 real rows came across, not 3 -- the INCOMPLETE warning is "
            "chatter, not a jump (web/sync/sync.js:638-640)")

    def test_the_jump_row_cross_check_still_fires(self):
        """verifyPull check (b). With the warning counted as a row, 3 == 3
        and this check could not fire -- on the one file that has no crc and
        no byte count of its own."""
        self.assertFalse(self.result.verified)
        self.assertIn("Only 2 of the puck's 3 jumps came across.",
                      self.result.reasons)

    def test_the_puck_s_own_complaint_still_fires_too(self):
        """Check (a) reads device.log, which KEEPS the in-frame chatter
        (_log_lines()) -- the split moves the line, it does not drop it."""
        self.assertTrue(any("INCOMPLETE" in r for r in self.result.reasons),
                        self.result.reasons)

    def test_the_bundle_s_csv_files_carry_no_chatter_row(self):
        with zipfile.ZipFile(self.result.bundle_path) as z:
            jumps = z.read("jumps.csv").decode()
            trace = z.read("trace.csv").decode()
            log = z.read("device.log").decode()
        self.assertEqual(jumps, "n,t_s,height_m\n1,1.0,0.50\n2,2.0,0.60\n")
        self.assertEqual(trace, "t,mag\n1.0,1.0\n2.0,1.0\n")
        self.assertNotIn("#", jumps)
        self.assertNotIn("#", trace)
        # Not lost -- moved. Both warnings are in device.log, where
        # CONTRACT.md SS2 says the frame lines and all '#' chatter live.
        self.assertIn("# WARNING jumps.csv INCOMPLETE", log)
        self.assertIn("# WARNING trace.csv INCOMPLETE", log)

    def test_a_short_trace_reports_short_not_a_surplus(self):
        """The inflated byte count's other effect: 28 B of device count
        against a body the chatter pushed to 109 B read as "More ride data
        arrived than the puck says it has" -- the opposite of the truth, and
        the one arm verifyPull says it will never invent an explanation
        for."""
        with zipfile.ZipFile(self.result.bundle_path) as z:
            manifest = json.loads(z.read("manifest.json"))
        self.assertEqual(manifest["trace_bytes_got"], 22)   # "t,mag\n" + 2 x 8 B rows
        self.assertEqual(manifest["trace_bytes_device"], 28)
        self.assertFalse(any("does not add up" in r for r in self.result.reasons),
                          self.result.reasons)


if __name__ == "__main__":
    unittest.main()
