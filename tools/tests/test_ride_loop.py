"""Tests for tools/ride_loop.py -- the ride watcher on the OWNER's Mac
(docs/sync-agent-plan.md's Nick-side agent has tools/puckd + its own tests;
this is the counterpart on the owner's side, reading what puckd published
to Nick's shared Drive folder).

Three fakes, same technique tools/tests/test_puckd_daemon.py already
established for the sibling agent:

  * a fake rclone (`_write_fake_rclone`) backed by two local directories
    (FAKE_RCLONE_RO_STORE / FAKE_RCLONE_RW_STORE) standing in for the
    read-only "shared with me" remote and the owner's write-capable one --
    same idea as test_puckd_daemon.py's own fake, extended to two stores
    and two remote names since ride_loop.py, unlike puckd, talks to both.
  * a fake `./tools/jump` (`_write_fake_jump`) whose `ingest`/`score`
    subcommands read a small JSON "plan" instead of running the real
    detector/analysis stack -- ride_loop.py's own contract with `ingest` is
    just "read the printed session path back out of stdout", and with
    `score` is just "tolerate whatever exit code comes back and check for
    score.md after" -- neither needs a real bundle or a real trace.
  * a fake `tools/fitread.py` (`_write_fake_fitread`) that reads a JSON
    sidecar next to the "fit zip" instead of decoding a real FIT -- this
    file's contract with fitread is exactly `<path> --out <dir>` ->
    `fit-summary.json` with `start_utc`/`end_utc`; nothing here needs a real
    Garmin binary to test that contract.

Never edits tools/jump, tools/fitread.py, or anything under tools/puckd/ --
CLAUDE.md's "never edit a file you were not assigned"; never lets a test
reach the real ~/Library/LaunchAgents or a real launchctl (TestInstall
patches Path.home AND injects a recording run_launchctl -- belt and
braces); never authorizes or touches a real rclone remote.

Run via: python3 -m pytest tools/tests/test_ride_loop.py -q
"""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools"))

import ride_loop  # noqa: E402


# ------------------------------------------------------------- fake rclone

_FAKE_RCLONE_SRC = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    # Fake rclone for tools/tests/test_ride_loop.py. Understands exactly the
    # vocabulary ride_loop.py issues: listremotes, lsjson, copyto (either
    # direction). Two backing stores on local disk (FAKE_RCLONE_RO_STORE /
    # _RW_STORE) stand in for the read-only shared remote and the owner's
    # write-capable one; FAKE_RCLONE_REMOTES controls which remote names
    # "exist" at all (simulating gdrive-ro not yet authorized);
    # FAKE_RCLONE_LSJSON_FAIL names remote-dir paths whose lsjson should
    # fail outright, distinct from a real, merely-empty directory.
    import datetime, hashlib, json, os, sys
    from pathlib import Path

    RO_NAME = os.environ.get("FAKE_RCLONE_RO_NAME", "gdrive-ro")
    RW_NAME = os.environ.get("FAKE_RCLONE_RW_NAME", "gdrive")
    RO_STORE = os.environ.get("FAKE_RCLONE_RO_STORE")
    RW_STORE = os.environ.get("FAKE_RCLONE_RW_STORE")
    REMOTES = [r for r in os.environ.get(
        "FAKE_RCLONE_REMOTES", RO_NAME + "," + RW_NAME).split(",") if r]
    LSJSON_FAIL = {x for x in os.environ.get("FAKE_RCLONE_LSJSON_FAIL", "").split(",") if x}


    def store_for(remote):
        if remote == RO_NAME:
            return RO_STORE
        if remote == RW_NAME:
            return RW_STORE
        return None


    def split_spec(spec):
        if ":" not in spec:
            return None, spec
        remote, _, rel = spec.partition(":")
        return remote, rel


    def entry_for(store_root, p):
        st = p.stat()
        rel = str(p.relative_to(store_root))
        return {
            "Name": p.name,
            "Size": st.st_size,
            "IsDir": p.is_dir(),
            "ModTime": datetime.datetime.fromtimestamp(
                st.st_mtime, tz=datetime.timezone.utc).isoformat(),
            "ID": hashlib.md5(rel.encode()).hexdigest()[:16],
        }


    def main():
        args = sys.argv[1:]
        cmd = args[0] if args else ""

        if cmd == "listremotes":
            for r in REMOTES:
                print(f"{r}:")
            return 0

        if cmd == "lsjson":
            spec = args[1]
            remote, rel = split_spec(spec)
            if remote not in REMOTES:
                print(f"remote {remote!r} not configured", file=sys.stderr)
                return 1
            if rel in LSJSON_FAIL or spec in LSJSON_FAIL:
                print("simulated lsjson failure", file=sys.stderr)
                return 1
            store = store_for(remote)
            if not store:
                print(f"no backing store for {remote!r}", file=sys.stderr)
                return 1
            d = Path(store) / rel
            if not d.is_dir():
                # Real rclone's own answer, MEASURED with v1.75.1 on
                # 2026-09-14 (`rclone lsjson <remote>:<missing dir>`):
                #   ERROR : error listing: directory not found
                #   exit 3
                # NOT an empty list and exit 0. This fake used to print "[]"
                # and return 0, which made check_prerequisites() look like it
                # tolerated a folder Nick's app had not created yet when the
                # real binary gates the whole cycle on it -- a test asserting
                # the opposite of the thing it was protecting (CLAUDE.md
                # rule 3).
                print("error listing: directory not found", file=sys.stderr)
                return 3
            entries = [entry_for(Path(store), p) for p in sorted(d.iterdir())]
            print(json.dumps(entries))
            return 0

        if cmd == "copyto":
            src, dst = args[1], args[2]
            src_remote, src_rel = split_spec(src)
            dst_remote, dst_rel = split_spec(dst)
            if src_remote in REMOTES and store_for(src_remote):
                source_path = Path(store_for(src_remote)) / src_rel
                dest_path = Path(dst)
                if not source_path.is_file():
                    print(f"source not found: {source_path}", file=sys.stderr)
                    return 1
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(source_path.read_bytes())
                return 0
            if dst_remote in REMOTES and store_for(dst_remote):
                source_path = Path(src)
                dest_path = Path(store_for(dst_remote)) / dst_rel
                if not source_path.is_file():
                    print(f"source not found: {source_path}", file=sys.stderr)
                    return 1
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(source_path.read_bytes())
                return 0
            print("copyto: no known remote in either argument", file=sys.stderr)
            return 1

        print(f"unknown command {cmd!r}", file=sys.stderr)
        return 1


    if __name__ == "__main__":
        raise SystemExit(main())
    """
)


def _write_executable(path: Path, src: str) -> None:
    path.write_text(src)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


# --------------------------------------------------------------- fake jump

_FAKE_JUMP_SRC = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    # Fake ./tools/jump for tools/tests/test_ride_loop.py. `ingest <zip>`
    # reads a "fake_ingest.json" plan out of the zip instead of decoding a
    # real bundle; `score <session>` reads a ".score_plan.json" the fake
    # ingest step leaves behind instead of running sim/score.py.
    import json, os, sys, zipfile
    from pathlib import Path


    def cmd_ingest(zip_path):
        with zipfile.ZipFile(zip_path) as zf:
            plan = json.loads(zf.read("fake_ingest.json"))
        exit_code = plan.get("exit_code", 0)
        if exit_code:
            print(plan.get("message", "simulated ingest failure"))
            return exit_code
        sessions_dir = Path(os.environ["FAKE_JUMP_SESSIONS_DIR"])
        sess = sessions_dir / plan["session_name"]
        sess.mkdir(parents=True, exist_ok=True)
        (sess / "jumps.csv").write_text(plan["jumps_csv"])
        (sess / "trace.csv").write_text(plan["trace_csv"])
        (sess / "session.json").write_text(json.dumps(plan["session_json"]))
        if plan.get("surfr_json") is not None:
            (sess / "surfr.json").write_text(json.dumps(plan["surfr_json"]))
        if plan.get("score_plan") is not None:
            (sess / ".score_plan.json").write_text(json.dumps(plan["score_plan"]))
        if plan.get("print_no_marker"):
            print("ingested (deliberately no marker line for this test)")
            return 0
        print(f"\\nsome analysis output\\n\\U0001F4E6 Session written to: {sess}")
        return 0


    def cmd_score(session_dir):
        sess = Path(session_dir)
        try:
            plan = json.loads((sess / ".score_plan.json").read_text())
        except OSError:
            plan = {}
        md = plan.get("score_md")
        if md is not None:
            (sess / "score.md").write_text(md)
        print(f"scored {sess.name}")
        return plan.get("exit_code", 0)


    def main(argv):
        if not argv:
            return 2
        if argv[0] == "ingest":
            return cmd_ingest(argv[1])
        if argv[0] == "score":
            return cmd_score(argv[1])
        print(f"unknown subcommand {argv[0]!r}", file=sys.stderr)
        return 2


    if __name__ == "__main__":
        raise SystemExit(main(sys.argv[1:]))
    """
)


# ----------------------------------------------------------- fake fitread

_FAKE_FITREAD_SRC = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    # Fake tools/fitread.py for tools/tests/test_ride_loop.py. Reads
    # "<fit_zip>.window.json" (written by the test alongside the fixture
    # zip) instead of decoding a real FIT, and writes fit-summary.json in
    # the same shape ride_loop.fit_activity_window() reads (start_utc/end_utc).
    import json, os, sys
    from pathlib import Path


    def main(argv):
        fit_path = Path(argv[0])
        out_dir = Path(argv[argv.index("--out") + 1])
        # Keyed by basename in a FIXED directory (FAKE_FITREAD_WINDOW_DIR),
        # not colocated with fit_path itself: ride_loop.py reads the LOCAL
        # cached copy under data/incoming/fits/, which rclone's copyto
        # produces fresh each time (same basename, different directory than
        # wherever the test first staged the fixture) -- a real sidecar
        # FILE would need copying too, which is purely a test-fixture
        # concept the real Garmin zips have no equivalent of.
        window_dir = os.environ.get("FAKE_FITREAD_WINDOW_DIR")
        sidecar = (Path(window_dir) / (fit_path.name + ".window.json")) if window_dir \
            else fit_path.with_name(fit_path.name + ".window.json")
        try:
            window = json.loads(sidecar.read_text())
        except OSError:
            print(f"no window sidecar for {fit_path}", file=sys.stderr)
            return 2
        if window.get("fail"):
            print("simulated fitread failure", file=sys.stderr)
            return 2
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "fit-summary.json").write_text(json.dumps(window))
        return 0


    if __name__ == "__main__":
        raise SystemExit(main(sys.argv[1:]))
    """
)


# ------------------------------------------------------------------- base

class _RideLoopTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)
        self.repo_dir = self.tmp / "repo"
        self.data_dir = self.repo_dir / "data"
        self.ro_store = self.tmp / "ro_store"
        self.rw_store = self.tmp / "rw_store"
        for d in (self.repo_dir, self.ro_store, self.rw_store):
            d.mkdir(parents=True, exist_ok=True)

        self.fake_rclone = self.tmp / "fake_rclone.py"
        _write_executable(self.fake_rclone, _FAKE_RCLONE_SRC)
        self.fake_jump = self.tmp / "fake_jump.py"
        _write_executable(self.fake_jump, _FAKE_JUMP_SRC)
        self.fake_fitread = self.tmp / "fake_fitread.py"
        _write_executable(self.fake_fitread, _FAKE_FITREAD_SRC)

        self.sessions_dir = self.data_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

        self.notifications = []
        self.fit_windows_dir = self.tmp / "fit_windows"
        self.fit_windows_dir.mkdir()

        self.env = {
            "RIDE_LOOP_RCLONE": str(self.fake_rclone),
            "FAKE_RCLONE_RO_STORE": str(self.ro_store),
            "FAKE_RCLONE_RW_STORE": str(self.rw_store),
            "FAKE_JUMP_SESSIONS_DIR": str(self.sessions_dir),
            "FAKE_FITREAD_WINDOW_DIR": str(self.fit_windows_dir),
        }
        self._env_patch = patch.dict(os.environ, self.env)
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def _cleanup(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_cfg(self, **overrides) -> ride_loop.Config:
        cfg = ride_loop.default_config(self.repo_dir)
        cfg.jump_argv = [sys.executable, str(self.fake_jump)]
        cfg.fitread_argv = [sys.executable, str(self.fake_fitread)]
        cfg.notifier = lambda title, body: self.notifications.append((title, body))
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg

    # -------------------------------------------------- Drive-side fixtures

    def put_ro_file(self, remote_dir: str, name: str, content: bytes) -> Path:
        d = self.ro_store / remote_dir
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_bytes(content)
        return p

    def put_fit_zip(self, name: str, activity_id: str, start_utc: str, end_utc: str,
                    fail: bool = False) -> Path:
        buf_path = self.tmp / f"_stage_{name}"
        with zipfile.ZipFile(buf_path, "w") as zf:
            zf.writestr(f"{activity_id}_ACTIVITY.fit", b"not a real fit, fake-parsed")
        p = self.put_ro_file(ride_loop.default_config(self.repo_dir).fits_dir, name,
                             buf_path.read_bytes())
        window = {"fail": True} if fail else {"start_utc": start_utc, "end_utc": end_utc}
        (self.fit_windows_dir / (name + ".window.json")).write_text(json.dumps(window))
        return p

    def make_bundle_zip(self, name: str, session_name: str, jumps_csv: str,
                        trace_csv: str, session_json: dict, *,
                        surfr_json=None, score_plan=None, exit_code=0,
                        print_no_marker=False) -> Path:
        plan = {
            "session_name": session_name,
            "jumps_csv": jumps_csv,
            "trace_csv": trace_csv,
            "session_json": session_json,
            "surfr_json": surfr_json,
            "score_plan": score_plan,
            "exit_code": exit_code,
            "print_no_marker": print_no_marker,
        }
        buf_path = self.tmp / f"_stage_{name}"
        with zipfile.ZipFile(buf_path, "w") as zf:
            zf.writestr("fake_ingest.json", json.dumps(plan))
        return self.put_ro_file(ride_loop.default_config(self.repo_dir).inbox_dir,
                                name, buf_path.read_bytes())


JUMPS_CSV_HEADER = "n,takeoff_s,airtime_raw_s,airtime_s,height_m\n"


def jumps_csv(*heights: float) -> str:
    rows = [JUMPS_CSV_HEADER]
    for i, h in enumerate(heights, start=1):
        rows.append(f"{i},{i*10}.0,0.8,0.8,{h}\n")
    return "".join(rows)


def trace_csv(points: "list[tuple[float, float]]") -> str:
    lines = ["t,mag\n"]
    for t, mag in points:
        lines.append(f"{t},{mag}\n")
    return "".join(lines)


def session_json(trace_epoch_utc="2026-09-14T20:00:00Z", unit="JumpHeight-E2C4"):
    return {
        "unit": unit,
        "firmware": "0.4.3",
        "cal": "CAL airtime_offset_s=0.0192 height_scale=1.000 source=device",
        "synced_at_utc": "2026-09-14T23:00:00Z",
        "device_uptime_s_at_sync": 10800.0,
        "trace_epoch_utc": trace_epoch_utc,
    }


# ---------------------------------------------------------- rclone helpers

class TestRcloneHelpers(_RideLoopTestBase):
    def test_remote_authorized_true_when_listed(self):
        with patch.dict(os.environ, {"FAKE_RCLONE_REMOTES": "gdrive-ro,gdrive"}):
            self.assertTrue(ride_loop.remote_authorized("gdrive-ro"))

    def test_remote_authorized_false_when_not_yet_configured(self):
        with patch.dict(os.environ, {"FAKE_RCLONE_REMOTES": "gdrive"}):
            self.assertFalse(ride_loop.remote_authorized("gdrive-ro"))

    def test_remote_authorized_false_on_missing_binary(self):
        with patch.dict(os.environ, {"RIDE_LOOP_RCLONE": str(self.tmp / "nope")}):
            self.assertFalse(ride_loop.remote_authorized("gdrive-ro"))

    def test_list_shared_dir_returns_entries(self):
        self.put_ro_file("JumpHeight/inbox", "a.zip", b"x")
        entries = ride_loop.list_shared_dir("gdrive-ro", "JumpHeight/inbox")
        self.assertEqual([e["Name"] for e in entries], ["a.zip"])

    def test_list_shared_dir_missing_folder_reads_as_a_failed_listing(self):
        # MEASURED with rclone v1.75.1 (2026-09-14): lsjson on a directory
        # that does not exist exits 3 with "error listing: directory not
        # found" -- indistinguishable, from here, from an authorization
        # failure. So a folder Nick's app has not created yet (fits/ before
        # his first Garmin upload, log/ before his first publish_log())
        # arrives as None, and it is check_prerequisites()'s job -- not this
        # function's -- to decide which folders that may gate.
        entries = ride_loop.list_shared_dir("gdrive-ro", "JumpHeight/never-created")
        self.assertIsNone(entries)

    def test_list_shared_dir_none_on_forced_failure(self):
        with patch.dict(os.environ, {"FAKE_RCLONE_LSJSON_FAIL": "JumpHeight/inbox"}):
            entries = ride_loop.list_shared_dir("gdrive-ro", "JumpHeight/inbox")
        self.assertIsNone(entries)

    def test_list_shared_dir_none_on_unauthorized_remote(self):
        with patch.dict(os.environ, {"FAKE_RCLONE_REMOTES": "gdrive"}):
            entries = ride_loop.list_shared_dir("gdrive-ro", "JumpHeight/inbox")
        self.assertIsNone(entries)


# ----------------------------------------------------- check_prerequisites

class TestCheckPrerequisites(_RideLoopTestBase):
    def test_ok_lists_all_three_folders(self):
        self.put_ro_file("JumpHeight/inbox", "a.zip", b"x")
        cfg = self.make_cfg()
        ok, listings = ride_loop.check_prerequisites(cfg)
        self.assertTrue(ok)
        self.assertEqual(set(listings), {"inbox", "fits", "log"})
        self.assertEqual([e["Name"] for e in listings["inbox"]], ["a.zip"])

    def test_gdrive_ro_not_authorized_yet_gates_cleanly(self):
        with patch.dict(os.environ, {"FAKE_RCLONE_REMOTES": "gdrive"}):
            ok, listings = ride_loop.check_prerequisites(self.make_cfg())
        self.assertFalse(ok)
        self.assertEqual(listings, {})

    def test_a_folder_that_did_not_list_is_None_not_an_empty_list(self):
        # None is the whole point: it is what lets _run_cycle() say out loud
        # that a reading did not happen instead of treating it as "empty".
        # (log/ still lists here, so this is the SOME-failed case.)
        self.put_ro_file("JumpHeight/log", "daemon.log", b"hi\n")
        with patch.dict(os.environ, {"FAKE_RCLONE_LSJSON_FAIL": "JumpHeight/inbox"}):
            ok, listings = ride_loop.check_prerequisites(self.make_cfg())
        self.assertTrue(ok)
        self.assertIsNone(listings["inbox"])

    def test_a_configured_but_unconnected_remote_gates_the_cycle(self):
        """MEASURED on this Mac 2026-09-14: `gdrive-ro` is in `rclone
        listremotes` (remote_authorized() -> True) while every lsjson exits
        1 with "empty token found - please run rclone config reconnect".
        listremotes answers "configured", never "connected", so the only
        thing between a half-set-up remote and a cycle that reads Nick's
        Drive as simply empty is every-folder-failed."""
        with patch.dict(os.environ, {"FAKE_RCLONE_LSJSON_FAIL":
                                     "JumpHeight/inbox,JumpHeight/fits,JumpHeight/log"}):
            cfg = self.make_cfg()
            ok, listings = ride_loop.check_prerequisites(cfg)
            self.assertFalse(ok)
            self.assertEqual(listings, {})
            report = ride_loop.run_cycle(cfg)
        self.assertFalse(report.ready)
        self.assertEqual(self.notifications, [])
        self.assertIn("not ready", cfg.log_path.read_text())

    def test_a_never_created_fits_folder_does_not_gate_the_cycle(self):
        # THE ONE THAT BIT: puckd creates JumpHeight/fits/ only when a Garmin
        # FIT actually uploads, and a rider who skipped Garmin at setup never
        # uploads one (tools/puckd/daemon.py:747-750). Real rclone answers a
        # missing folder with exit 3, so gating the cycle on all three
        # folders listing meant every ride Nick ever sent sat unread in
        # inbox/ while this script logged "not ready yet" every ten minutes,
        # forever.
        self.make_bundle_zip("ride1.zip", "sessA", jumps_csv(1.5),
                             trace_csv([(0.0, 1.0)]), session_json())
        cfg = self.make_cfg()   # no JumpHeight/fits/ on the fake Drive at all
        report = ride_loop.run_cycle(cfg)
        self.assertTrue(report.ready)
        self.assertEqual([s.name for s in report.sessions], ["sessA"])
        self.assertIn("JumpHeight/fits", cfg.log_path.read_text())
        self.assertIn("JumpHeight/fits did not list", report.errors)


# -------------------------------------------------------- sync_new_entries

class TestSyncNewEntries(_RideLoopTestBase):
    def test_copies_a_new_zip_and_records_it_seen(self):
        self.put_ro_file("JumpHeight/inbox", "ride1.zip", b"ride-bytes")
        cfg = self.make_cfg()
        entries = ride_loop.list_shared_dir(cfg.ro_remote, cfg.inbox_dir)
        seen = {}
        new = ride_loop.sync_new_entries(cfg, cfg.inbox_dir, cfg.incoming_dir, entries, seen)
        self.assertEqual([p.name for p in new], ["ride1.zip"])
        self.assertEqual((cfg.incoming_dir / "ride1.zip").read_bytes(), b"ride-bytes")
        self.assertEqual(len(seen), 1)

    def test_already_seen_is_not_recopied(self):
        self.put_ro_file("JumpHeight/inbox", "ride1.zip", b"ride-bytes")
        cfg = self.make_cfg()
        entries = ride_loop.list_shared_dir(cfg.ro_remote, cfg.inbox_dir)
        seen = {}
        ride_loop.sync_new_entries(cfg, cfg.inbox_dir, cfg.incoming_dir, entries, seen)
        (cfg.incoming_dir / "ride1.zip").write_bytes(b"TAMPERED")  # prove it isn't touched again
        new_again = ride_loop.sync_new_entries(cfg, cfg.inbox_dir, cfg.incoming_dir, entries, seen)
        self.assertEqual(new_again, [])
        self.assertEqual((cfg.incoming_dir / "ride1.zip").read_bytes(), b"TAMPERED")

    def test_non_zip_entries_are_ignored(self):
        self.put_ro_file("JumpHeight/inbox", "readme.txt", b"not a bundle")
        cfg = self.make_cfg()
        entries = ride_loop.list_shared_dir(cfg.ro_remote, cfg.inbox_dir)
        new = ride_loop.sync_new_entries(cfg, cfg.inbox_dir, cfg.incoming_dir, entries, {})
        self.assertEqual(new, [])

    def test_self_heals_into_the_ledger_when_the_local_copy_already_matches(self):
        self.put_ro_file("JumpHeight/inbox", "ride1.zip", b"12345")
        cfg = self.make_cfg()
        cfg.incoming_dir.mkdir(parents=True, exist_ok=True)
        (cfg.incoming_dir / "ride1.zip").write_bytes(b"12345")  # same size, ledger lost
        entries = ride_loop.list_shared_dir(cfg.ro_remote, cfg.inbox_dir)
        seen = {}
        new = ride_loop.sync_new_entries(cfg, cfg.inbox_dir, cfg.incoming_dir, entries, seen)
        self.assertEqual(new, [])  # not re-copied
        self.assertEqual(len(seen), 1)  # but the ledger now knows about it

    def test_multiple_new_files_copy_oldest_modtime_first(self):
        p1 = self.put_ro_file("JumpHeight/inbox", "a.zip", b"a")
        p2 = self.put_ro_file("JumpHeight/inbox", "b.zip", b"b")
        os.utime(p1, (1000, 1000))
        os.utime(p2, (2000, 2000))
        cfg = self.make_cfg()
        entries = ride_loop.list_shared_dir(cfg.ro_remote, cfg.inbox_dir)
        new = ride_loop.sync_new_entries(cfg, cfg.inbox_dir, cfg.incoming_dir, entries, {})
        self.assertEqual([p.name for p in new], ["a.zip", "b.zip"])


# ---------------------------------------------------------- sync_rider_log

class TestSyncRiderLog(_RideLoopTestBase):
    def test_first_copy_is_reported_as_changed(self):
        self.put_ro_file("JumpHeight/log", "daemon.log", b"line one\n")
        self.put_ro_file("JumpHeight/log", "status.json", b'{"ok": true}')
        cfg = self.make_cfg()
        entries = ride_loop.list_shared_dir(cfg.ro_remote, cfg.log_dir)
        changed = ride_loop.sync_rider_log(cfg, entries)
        self.assertTrue(changed)
        self.assertEqual((cfg.rider_log_dir / "daemon.log").read_bytes(), b"line one\n")
        self.assertEqual((cfg.rider_log_dir / "status.json").read_bytes(), b'{"ok": true}')

    def test_unchanged_content_reports_false(self):
        self.put_ro_file("JumpHeight/log", "daemon.log", b"line one\n")
        self.put_ro_file("JumpHeight/log", "status.json", b'{"ok": true}')
        cfg = self.make_cfg()
        entries = ride_loop.list_shared_dir(cfg.ro_remote, cfg.log_dir)
        ride_loop.sync_rider_log(cfg, entries)  # first copy
        changed = ride_loop.sync_rider_log(cfg, entries)  # same bytes again
        self.assertFalse(changed)

    def test_a_changed_daemon_log_alone_reports_true(self):
        self.put_ro_file("JumpHeight/log", "daemon.log", b"line one\n")
        self.put_ro_file("JumpHeight/log", "status.json", b'{"ok": true}')
        cfg = self.make_cfg()
        entries = ride_loop.list_shared_dir(cfg.ro_remote, cfg.log_dir)
        ride_loop.sync_rider_log(cfg, entries)
        self.put_ro_file("JumpHeight/log", "daemon.log", b"line one\nline two\n")
        entries2 = ride_loop.list_shared_dir(cfg.ro_remote, cfg.log_dir)
        changed = ride_loop.sync_rider_log(cfg, entries2)
        self.assertTrue(changed)
        self.assertEqual((cfg.rider_log_dir / "daemon.log").read_bytes(),
                         b"line one\nline two\n")

    def test_a_status_json_heartbeat_alone_does_not_report_a_change(self):
        """THE NOTIFICATION-SPAM GUARD. puckd's publish_log() stamps
        status.json with `"written": datetime.now()` (daemon.py:466-468) and
        run_forever() calls publish_log() on every SPOOL_RETRY_INTERVAL_S
        tick (daemon.py:112 = 600 s) whether or not anything happened. This
        script's own interval is also 600 s, so if status.json's bytes voted
        here, "Nick: log updated" fired every ten minutes, forever."""
        self.put_ro_file("JumpHeight/log", "daemon.log", b"line one\n")
        self.put_ro_file("JumpHeight/log", "status.json",
                         b'{"written": "2026-09-14T23:00:00-04:00"}')
        cfg = self.make_cfg()
        entries = ride_loop.list_shared_dir(cfg.ro_remote, cfg.log_dir)
        ride_loop.sync_rider_log(cfg, entries)

        self.put_ro_file("JumpHeight/log", "status.json",
                         b'{"written": "2026-09-14T23:10:00-04:00"}')
        entries2 = ride_loop.list_shared_dir(cfg.ro_remote, cfg.log_dir)
        changed = ride_loop.sync_rider_log(cfg, entries2)
        self.assertFalse(changed, "a heartbeat timestamp is not news")
        # ... but the new bytes ARE still on disk: Josh reads status.json for
        # battery, version and last_job. Only the notification changed.
        self.assertIn(b"23:10:00", (cfg.rider_log_dir / "status.json").read_bytes())

    def test_a_log_not_published_yet_is_routine_not_a_crash(self):
        cfg = self.make_cfg()  # no JumpHeight/log/* on the fake Drive at all
        changed = ride_loop.sync_rider_log(cfg, [])
        self.assertFalse(changed)


# ------------------------------------------------------ session_trace_window

class TestSessionTraceWindow(_RideLoopTestBase):
    def _write_session(self, sess: Path, epoch: str, points):
        sess.mkdir(parents=True, exist_ok=True)
        (sess / "session.json").write_text(json.dumps(session_json(trace_epoch_utc=epoch)))
        (sess / "trace.csv").write_text(trace_csv(points))

    def test_window_from_epoch_plus_min_max_t(self):
        sess = self.tmp / "sess"
        self._write_session(sess, "2026-09-14T20:00:00Z", [(10.0, 1.0), (70.0, 1.0)])
        window = ride_loop.session_trace_window(sess)
        self.assertIsNotNone(window)
        start, end = window
        self.assertEqual(start, datetime(2026, 9, 14, 20, 0, 10, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 9, 14, 20, 1, 10, tzinfo=timezone.utc))

    def test_uses_min_max_not_first_last_row(self):
        # A boot-reset trace can log an early small t AFTER a later one in
        # file order; min/max still yields a window that CONTAINS the
        # activity rather than a narrower, wrong one (see the function's
        # own docstring).
        sess = self.tmp / "sess"
        self._write_session(sess, "2026-09-14T20:00:00Z", [(500.0, 1.0), (5.0, 1.0)])
        start, end = ride_loop.session_trace_window(sess)
        self.assertEqual(start, datetime(2026, 9, 14, 20, 0, 5, tzinfo=timezone.utc))
        self.assertEqual(end, datetime(2026, 9, 14, 20, 8, 20, tzinfo=timezone.utc))

    def test_missing_trace_epoch_is_none_not_a_guess(self):
        sess = self.tmp / "sess"
        sess.mkdir(parents=True, exist_ok=True)
        (sess / "session.json").write_text(json.dumps({"unit": "JumpHeight-E2C4"}))
        (sess / "trace.csv").write_text(trace_csv([(1.0, 1.0)]))
        self.assertIsNone(ride_loop.session_trace_window(sess))

    def test_missing_trace_csv_is_none(self):
        sess = self.tmp / "sess"
        sess.mkdir(parents=True, exist_ok=True)
        (sess / "session.json").write_text(json.dumps(session_json()))
        self.assertIsNone(ride_loop.session_trace_window(sess))


# --------------------------------------------------------------- Garmin fit

class TestGarminMatching(_RideLoopTestBase):
    def test_fit_activity_window_reads_the_fake_summary(self):
        cfg = self.make_cfg()
        fit = self.put_fit_zip("111_activity.zip", "111",
                               "2026-09-14T20:15:00Z", "2026-09-14T20:16:00Z")
        window = ride_loop.fit_activity_window(cfg, fit)
        self.assertEqual(window, (
            datetime(2026, 9, 14, 20, 15, tzinfo=timezone.utc),
            datetime(2026, 9, 14, 20, 16, tzinfo=timezone.utc)))

    def test_fit_activity_window_none_on_fitread_failure(self):
        cfg = self.make_cfg()
        fit = self.put_fit_zip("bad.zip", "222", "", "", fail=True)
        self.assertIsNone(ride_loop.fit_activity_window(cfg, fit))

    def test_pick_matching_fit_prefers_the_newest_overlapping_one(self):
        cfg = self.make_cfg()
        session_window = (datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc),
                          datetime(2026, 9, 14, 21, 0, tzinfo=timezone.utc))
        too_early = self.put_fit_zip("early.zip", "1",
                                     "2026-09-14T10:00:00Z", "2026-09-14T10:10:00Z")
        older_overlap = self.put_fit_zip("older.zip", "2",
                                         "2026-09-14T20:05:00Z", "2026-09-14T20:10:00Z")
        newer_overlap = self.put_fit_zip("newer.zip", "3",
                                         "2026-09-14T20:20:00Z", "2026-09-14T20:30:00Z")
        chosen = ride_loop.pick_matching_fit(
            cfg, session_window, [too_early, older_overlap, newer_overlap])
        self.assertEqual(chosen, newer_overlap)

    def test_pick_matching_fit_prefers_the_most_overlap_not_the_latest_ride(self):
        """The real shape of the problem, from the rider's own file: a puck
        session window is the whole day between the first and last opening of
        the motion gate — data/sessions/20260914-210637-E2C4/trace.csv spans
        25,039 s = 417 min (counted here, 648,808 rows) — while a Garmin
        activity is an hour or two. EVERY activity that day overlaps it, so
        "newest overlapping" attaches the evening walk to the morning's foil
        session and calls it ground truth."""
        session_window = (datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc),
                          datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc))
        cfg = self.make_cfg()
        the_ride = self.put_fit_zip("ride.zip", "1",
                                    "2026-09-14T10:00:00Z", "2026-09-14T12:00:00Z")
        evening_walk = self.put_fit_zip("walk.zip", "2",
                                        "2026-09-14T19:30:00Z", "2026-09-14T20:00:00Z")
        chosen = ride_loop.pick_matching_fit(cfg, session_window,
                                             [the_ride, evening_walk])
        self.assertEqual(chosen, the_ride)

    def test_pick_matching_fit_none_when_nothing_overlaps(self):
        cfg = self.make_cfg()
        session_window = (datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc),
                          datetime(2026, 9, 14, 21, 0, tzinfo=timezone.utc))
        far = self.put_fit_zip("far.zip", "9",
                               "2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z")
        self.assertIsNone(ride_loop.pick_matching_fit(cfg, session_window, [far]))

    def test_install_garmin_fit_unzips_the_single_activity_member(self):
        sess = self.tmp / "sess"
        sess.mkdir()
        zpath = self.tmp / "a.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("55_ACTIVITY.fit", b"fit-bytes")
        self.assertTrue(ride_loop.install_garmin_fit(sess, zpath))
        self.assertEqual((sess / "garmin.fit").read_bytes(), b"fit-bytes")

    def test_install_garmin_fit_false_when_not_exactly_one_activity_member(self):
        sess = self.tmp / "sess"
        sess.mkdir()
        zpath = self.tmp / "a.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("notes.txt", b"nope")
        self.assertFalse(ride_loop.install_garmin_fit(sess, zpath))
        self.assertFalse((sess / "garmin.fit").exists())


# ------------------------------------------------------------- run_ingest

class TestRunIngest(_RideLoopTestBase):
    def test_happy_path_returns_the_session_dir(self):
        cfg = self.make_cfg()
        zpath = self.make_bundle_zip(
            "ride.zip", "20260914-210600-E2C4", jumps_csv(1.1, 3.39),
            trace_csv([(0.0, 1.0), (60.0, 1.0)]), session_json())
        sess = ride_loop.run_ingest(cfg, zpath)
        self.assertEqual(sess, self.sessions_dir / "20260914-210600-E2C4")
        self.assertTrue((sess / "jumps.csv").is_file())

    def test_nonzero_exit_returns_none(self):
        cfg = self.make_cfg()
        zpath = self.make_bundle_zip(
            "ride.zip", "sess1", jumps_csv(), trace_csv([]), session_json(), exit_code=1)
        self.assertIsNone(ride_loop.run_ingest(cfg, zpath))

    def test_no_marker_line_is_treated_as_failure_not_a_guess(self):
        cfg = self.make_cfg()
        zpath = self.make_bundle_zip(
            "ride.zip", "sess1", jumps_csv(), trace_csv([]), session_json(),
            print_no_marker=True)
        self.assertIsNone(ride_loop.run_ingest(cfg, zpath))


# -------------------------------------------------------------- run_score

class TestRunScore(_RideLoopTestBase):
    def test_writes_score_md_on_success(self):
        cfg = self.make_cfg()
        sess = self.sessions_dir / "s1"
        sess.mkdir(parents=True)
        (sess / ".score_plan.json").write_text(json.dumps(
            {"score_md": "# score.md\n- FINDING: test\n", "exit_code": 0}))
        md = ride_loop.run_score(cfg, sess)
        self.assertEqual(md, sess / "score.md")
        self.assertIn("FINDING", md.read_text())

    def test_nonzero_exit_is_tolerated_and_score_md_stays_absent(self):
        cfg = self.make_cfg()
        sess = self.sessions_dir / "s1"
        sess.mkdir(parents=True)
        (sess / ".score_plan.json").write_text(json.dumps({"score_md": None, "exit_code": 1}))
        md = ride_loop.run_score(cfg, sess)
        self.assertIsNone(md)
        self.assertFalse((sess / "score.md").exists())

    def test_missing_subcommand_shape_is_also_tolerated(self):
        # No .score_plan.json at all -- the fake's stand-in for "`score`
        # isn't registered yet" (argparse would exit 2); still must not raise.
        cfg = self.make_cfg()
        sess = self.sessions_dir / "s1"
        sess.mkdir(parents=True)
        self.assertIsNone(ride_loop.run_score(cfg, sess))


# ------------------------------------------------------------------ corpus

class TestCorpus(_RideLoopTestBase):
    def test_corpus_line_includes_surfr_and_score_when_present(self):
        sess = self.sessions_dir / "20260914-210600-E2C4"
        sess.mkdir(parents=True)
        (sess / "session.json").write_text(json.dumps(session_json()))
        (sess / "jumps.csv").write_text(jumps_csv(1.11, 3.39, 0.5))
        (sess / "surfr.json").write_text(json.dumps({"jumps_total": 32}))
        (sess / "score.md").write_text("# score.md\n- FINDING — X: y\n")
        line = ride_loop.corpus_line(sess)
        self.assertIn("2026-09-14 21:06", line)
        self.assertIn("E2C4", line)
        self.assertIn("3 puck jumps, best 3.39 m", line)
        self.assertIn("Surfr 32 jumps", line)
        self.assertIn("score: FINDING", line)

    def test_corpus_line_omits_optional_fields_when_absent(self):
        sess = self.sessions_dir / "20260914-104207-E2C4"
        sess.mkdir(parents=True)
        (sess / "session.json").write_text(json.dumps(session_json()))
        (sess / "jumps.csv").write_text(jumps_csv(2.0))
        line = ride_loop.corpus_line(sess)
        self.assertNotIn("Surfr", line)
        self.assertNotIn("score:", line)

    def test_a_half_written_session_says_so_instead_of_reporting_zero_jumps(self):
        # An ingest that died between mkdir and jumps.csv leaves a directory
        # that looks, to the old line, exactly like a ride on which nothing
        # was detected: "0 puck jumps, best 0.00 m". In the corpus that
        # docs/accuracy-plan.md scores against, that is a manufactured
        # measurement (CLAUDE.md rule 3).
        sess = self.sessions_dir / "20260914-210600-E2C4"
        sess.mkdir(parents=True)
        (sess / "session.json").write_text(json.dumps(session_json()))
        line = ride_loop.corpus_line(sess)
        self.assertIn("no jumps.csv", line)
        self.assertNotIn("0 puck jumps", line)

    def test_write_corpus_covers_every_session_dir_sorted(self):
        for name in ("20260907-163005", "20260906-192422"):
            sess = self.sessions_dir / name
            sess.mkdir(parents=True)
            (sess / "session.json").write_text(json.dumps(session_json(unit="JumpHeight-8673")))
            (sess / "jumps.csv").write_text(jumps_csv(1.0))
        cfg = self.make_cfg()
        path = ride_loop.write_corpus(cfg)
        text = path.read_text()
        self.assertLess(text.index("2026-09-06"), text.index("2026-09-07"))

    def test_a_session_that_cannot_be_summarised_does_not_blank_the_corpus(self):
        good = self.sessions_dir / "20260907-163005"
        good.mkdir(parents=True)
        (good / "session.json").write_text(json.dumps(session_json()))
        (good / "jumps.csv").write_text(jumps_csv(1.0))
        # Valid JSON but not an object: session_json.get(...) inside
        # corpus_line() raises AttributeError on a list -- the shape
        # write_corpus()'s per-session try/except is there to survive.
        broken = self.sessions_dir / "20260908-000000"
        broken.mkdir(parents=True)
        (broken / "session.json").write_text("[]")
        (broken / "jumps.csv").write_text(jumps_csv(1.0))
        cfg = self.make_cfg()
        text = ride_loop.write_corpus(cfg).read_text()
        self.assertIn("2026-09-07", text)
        self.assertIn("20260908-000000", text)  # the broken one is still named, not dropped


# ------------------------------------------------------------ run_cycle e2e

class TestRunCycleEndToEnd(_RideLoopTestBase):
    def test_gated_cleanly_when_gdrive_ro_is_not_authorized(self):
        with patch.dict(os.environ, {"FAKE_RCLONE_REMOTES": "gdrive"}):
            report = ride_loop.run_cycle(self.make_cfg())
        self.assertFalse(report.ready)
        self.assertEqual(report.sessions, [])
        self.assertIsNone(report.notified)
        self.assertEqual(self.notifications, [])
        self.assertFalse(self.data_dir.joinpath("incoming", "seen.json").exists())

    def test_happy_path_ingests_matches_garmin_scores_and_notifies(self):
        self.make_bundle_zip(
            "ride1.zip", "20260914-210600-E2C4", jumps_csv(1.11, 3.39),
            trace_csv([(0.0, 1.0), (3600.0, 1.0)]),
            session_json(trace_epoch_utc="2026-09-14T20:00:00Z"),
            score_plan={"score_md": "# score.md\n- FINDING: clean session\n", "exit_code": 0})
        self.put_fit_zip("999_activity.zip", "999",
                         "2026-09-14T20:10:00Z", "2026-09-14T20:20:00Z")

        report = ride_loop.run_cycle(self.make_cfg())

        self.assertTrue(report.ready)
        self.assertEqual(len(report.sessions), 1)
        sess = report.sessions[0]
        self.assertEqual(sess.name, "20260914-210600-E2C4")
        self.assertEqual((sess / "garmin.fit").read_bytes(), b"not a real fit, fake-parsed")
        self.assertEqual(report.score_mds, [sess / "score.md"])

        self.assertTrue(self.data_dir.joinpath("corpus.md").exists())
        corpus_text = self.data_dir.joinpath("corpus.md").read_text()
        self.assertIn("2 puck jumps, best 3.39 m", corpus_text)

        self.assertEqual(len(self.notifications), 1)
        title, body = self.notifications[0]
        self.assertEqual(title, "Nick: 2 jumps, best 3.39 m")
        self.assertIsNone(body)

        # corpus.md and a session-qualified score.md landed in reports/.
        reports_dir = self.rw_store / "JumpHeight" / "reports"
        self.assertTrue((reports_dir / "corpus.md").is_file())
        self.assertTrue((reports_dir / "20260914-210600-E2C4-score.md").is_file())

    def test_second_cycle_with_nothing_new_notifies_nothing(self):
        self.make_bundle_zip(
            "ride1.zip", "sessA", jumps_csv(1.0), trace_csv([(0.0, 1.0)]), session_json())
        ride_loop.run_cycle(self.make_cfg())
        self.notifications.clear()

        report2 = ride_loop.run_cycle(self.make_cfg())
        self.assertTrue(report2.ready)
        self.assertEqual(report2.new_bundles, [])
        self.assertEqual(report2.sessions, [])
        self.assertIsNone(report2.notified)
        self.assertEqual(self.notifications, [])

    def test_log_only_change_notifies_log_updated(self):
        self.put_ro_file("JumpHeight/log", "daemon.log", b"first\n")
        ride_loop.run_cycle(self.make_cfg())
        self.notifications.clear()

        self.put_ro_file("JumpHeight/log", "daemon.log", b"first\nsecond\n")
        report = ride_loop.run_cycle(self.make_cfg())

        self.assertEqual(report.sessions, [])
        self.assertTrue(report.log_changed)
        self.assertEqual(self.notifications, [("Nick: log updated", None)])

    def test_a_bundle_with_zero_jumps_still_notifies(self):
        # "a puck with no ride on it produces nothing at all" (docs/sync-agent-plan.md)
        # is puckd's OWN gate on step 0 -- a bundle that made it to Drive at
        # all represents a real ride, even one with zero detected jumps, and
        # still gets ride_loop's notification.
        self.make_bundle_zip(
            "ride1.zip", "sessA", jumps_csv(), trace_csv([(0.0, 1.0)]), session_json())
        report = ride_loop.run_cycle(self.make_cfg())
        self.assertEqual(len(report.sessions), 1)
        self.assertEqual(self.notifications, [("Nick: 0 jumps, best 0.00 m", None)])

    def test_ingest_failure_is_logged_and_does_not_crash_the_cycle(self):
        self.make_bundle_zip("bad.zip", "sessA", jumps_csv(), trace_csv([]),
                             session_json(), exit_code=1)
        report = ride_loop.run_cycle(self.make_cfg())
        self.assertTrue(report.ready)
        self.assertEqual(report.sessions, [])
        self.assertTrue(any("ingest failed" in e for e in report.errors))

    def test_run_cycle_never_raises_even_when_a_step_blows_up(self):
        # At least one folder has to LIST for the cycle to get as far as
        # write_corpus(): with nothing on the fake Drive at all, every
        # lsjson fails the way real rclone fails on a missing directory
        # (exit 3, measured) and check_prerequisites() reads that as the
        # remote being unreachable, which is its own tested behaviour.
        self.put_ro_file("JumpHeight/log", "daemon.log", b"a line\n")
        cfg = self.make_cfg()
        with patch.object(ride_loop, "write_corpus", side_effect=RuntimeError("boom")):
            report = ride_loop.run_cycle(cfg)
        self.assertTrue(any("boom" in e for e in report.errors))


# -------------------------------------------------------------------- CLI

class TestCliOnce(_RideLoopTestBase):
    def test_once_prints_a_report_and_returns_zero(self):
        import io
        buf = io.StringIO()
        with patch.object(ride_loop, "default_config", return_value=self.make_cfg()), \
             patch("sys.stdout", buf):
            code = ride_loop.main(["--once"])
        self.assertEqual(code, 0)
        self.assertIn("ready:", buf.getvalue())

    def test_bare_invocation_is_quiet_on_stdout(self):
        import io
        buf = io.StringIO()
        with patch.object(ride_loop, "default_config", return_value=self.make_cfg()), \
             patch("sys.stdout", buf):
            code = ride_loop.main([])
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue(), "")


class TestInstall(_RideLoopTestBase):
    """Never touches the real ~/Library/LaunchAgents or a real launchctl:
    Path.home() is patched to a scratch directory AND run_launchctl is an
    injected recorder -- either alone would be enough; both together is
    the same "belt and braces" this file's own module docstring promises."""

    def test_render_plist_fills_in_both_placeholders(self):
        cfg = self.make_cfg()
        text = ride_loop.render_plist(cfg)
        self.assertNotIn("__PYTHON__", text)
        self.assertNotIn("__SCRIPT__", text)
        self.assertIn(sys.executable, text)
        self.assertIn(str(cfg.repo_dir / "tools" / "ride_loop.py"), text)

    def test_install_writes_the_plist_and_calls_bootout_then_bootstrap(self):
        fake_home = self.tmp / "fake_home"
        fake_home.mkdir()
        calls = []

        def recorder(argv, **kwargs):
            calls.append(argv)
            import subprocess as _sp
            return _sp.CompletedProcess(argv, 0, stdout="", stderr="")

        cfg = self.make_cfg()
        with patch.object(ride_loop.Path, "home", return_value=fake_home):
            ok = ride_loop.install(cfg, run_launchctl=recorder)

        self.assertTrue(ok)
        dest = fake_home / "Library" / "LaunchAgents" / "com.jumpheight.rideloop.plist"
        self.assertTrue(dest.is_file())
        self.assertIn(sys.executable, dest.read_text())
        self.assertEqual([c[1] for c in calls], ["bootout", "bootstrap"])
        self.assertTrue(all(str(dest) == c[-1] for c in calls))

    def test_install_reports_failure_when_bootstrap_exits_nonzero(self):
        fake_home = self.tmp / "fake_home"
        fake_home.mkdir()

        def recorder(argv, **kwargs):
            import subprocess as _sp
            rc = 1 if argv[1] == "bootstrap" else 0
            return _sp.CompletedProcess(argv, rc, stdout="", stderr="denied")

        cfg = self.make_cfg()
        with patch.object(ride_loop.Path, "home", return_value=fake_home):
            ok = ride_loop.install(cfg, run_launchctl=recorder)
        self.assertFalse(ok)


class ThePlistFindsRclone(unittest.TestCase):
    """launchd's default PATH is /usr/bin:/bin:/usr/sbin:/sbin; rclone lives
    in /opt/homebrew/bin. Measured 2026-09-15: every launchd run logged
    "gdrive-ro not ready" while a shell run copied fine."""

    def test_plist_names_rclone_and_a_path_with_homebrew(self):
        import plistlib
        from pathlib import Path
        d = plistlib.loads((Path(__file__).resolve().parents[2] / "packaging" / "com.jumpheight.rideloop.plist").read_bytes())
        env = d.get("EnvironmentVariables", {})
        self.assertEqual(env.get("RIDE_LOOP_RCLONE"), "/opt/homebrew/bin/rclone")
        self.assertIn("/opt/homebrew/bin", env.get("PATH", ""), "launchd_default_path lacks homebrew")


# ---------------------------------------- adversarial review 2026-09-15

class NoRcloneBinaryIsNotAnAuthorizationProblem(_RideLoopTestBase):
    """MEASURED 2026-09-15 on this Mac. The LaunchAgent installed at
    04:05 UTC carried no EnvironmentVariables; launchd's default PATH is
    /usr/bin:/bin:/usr/sbin:/sbin and rclone lives in /opt/homebrew/bin. So
    every ten-minute cycle from 04:05 to 11:57 UTC wrote, to
    data/ride_loop.log and to the plist's StandardErrorPath:

        gdrive-ro not ready (not configured, or configured but not connected
        — `rclone config reconnect`, or the share is gone: nothing listed at
        all)

    while a shell run of the same three lsjson calls listed every folder.
    Eight hours of a line naming three causes, none of which was the real
    one — because remote_authorized() catches RcloneNotFound and answers
    False, the same answer a disconnected remote gives. The binary being
    absent has to say so."""

    def test_missing_binary_is_named_and_not_blamed_on_the_remote(self):
        empty = self.tmp / "empty_path"
        empty.mkdir()
        cfg = self.make_cfg()
        env = dict(os.environ)
        env.pop("RIDE_LOOP_RCLONE", None)
        env["PATH"] = str(empty)
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNotNone(ride_loop.rclone_missing_reason())
            report = ride_loop.run_cycle(cfg)
        text = cfg.log_path.read_text()
        self.assertIn("NO RCLONE BINARY", text)
        self.assertNotIn("config reconnect", text)
        self.assertIn("no rclone binary", report.errors)
        self.assertFalse(report.ready)
        self.assertEqual(self.notifications, [])

    def test_an_env_override_pointing_at_nothing_is_also_named(self):
        cfg = self.make_cfg()
        with patch.dict(os.environ, {"RIDE_LOOP_RCLONE": str(self.tmp / "nope")}):
            self.assertIn("not a runnable file", ride_loop.rclone_missing_reason())
            report = ride_loop.run_cycle(cfg)
        self.assertIn("NO RCLONE BINARY", cfg.log_path.read_text())
        self.assertIn("no rclone binary", report.errors)

    def test_a_real_binary_leaves_the_remote_gate_in_charge(self):
        """The guard must not shadow the case it is not about: with rclone
        present and the remote unconfigured, the message is still the
        remote's."""
        cfg = self.make_cfg()
        self.assertIsNone(ride_loop.rclone_missing_reason())
        with patch.dict(os.environ, {"FAKE_RCLONE_REMOTES": "gdrive"}):
            report = ride_loop.run_cycle(cfg)
        text = cfg.log_path.read_text()
        self.assertNotIn("NO RCLONE BINARY", text)
        self.assertIn("not ready", text)
        self.assertFalse(report.ready)


class CorpusStatesOnlyWhatItMeasured(_RideLoopTestBase):
    def test_a_bench_dir_is_not_diagnosed_as_a_failed_ingest(self):
        """data/sessions/ also holds bench artifacts that were never ingests
        — jitter-check/ and walk-overnight/ carry no jumps.csv AND no
        session.json. "ingest did not finish" is a CAUSE, and asserting it
        for a directory that no ingest ever wrote is a verdict without a
        measurement (CLAUDE.md rule 2)."""
        bench = self.sessions_dir / "walk-overnight"
        bench.mkdir(parents=True)
        (bench / "pull-a").mkdir()
        line = ride_loop.corpus_line(bench)
        self.assertIn("not an ingested session", line)
        self.assertNotIn("ingest did not finish", line)
        self.assertNotIn("0 puck jumps", line)

    def test_a_half_written_ingest_still_says_ingest_did_not_finish(self):
        """The other half of the same distinction: session.json present and
        jumps.csv missing IS an ingest that died part-way."""
        sess = self.sessions_dir / "20260914-210600-E2C4"
        sess.mkdir(parents=True)
        (sess / "session.json").write_text(json.dumps(session_json()))
        line = ride_loop.corpus_line(sess)
        self.assertIn("ingest did not finish", line)
        self.assertNotIn("0 puck jumps", line)


class OnePassAtATime(_RideLoopTestBase):
    """Measured 2026-09-15: a manual --once and a launchd pass overlapped."""

    def test_a_second_pass_yields_while_the_first_holds_the_lock(self):
        import fcntl
        cfg = self.make_cfg()
        lock = Path(cfg.data_dir) / ".ride_loop.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        holder = open(lock, "w")
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            report = ride_loop.run_cycle(cfg)
        finally:
            holder.close()
        self.assertIn("another pass running", report.errors)
        self.assertIn("yields", cfg.log_path.read_text())

    def test_the_lock_is_released_after_a_pass(self):
        import fcntl
        with patch.dict(os.environ, {"FAKE_RCLONE_REMOTES": "gdrive"}):
            ride_loop.run_cycle(self.make_cfg())
        lock = Path(self.make_cfg().data_dir) / ".ride_loop.lock"
        fh = open(lock, "w")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)   # would raise if still held
        fh.close()


if __name__ == "__main__":
    unittest.main()
