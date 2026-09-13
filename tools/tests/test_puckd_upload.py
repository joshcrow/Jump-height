"""Tests for tools/puckd/upload.py — the Google Drive leg of the puck job
(docs/sync-agent-plan.md:41-58's step 6, gates G1/G4 at :61-64).

rclone is never mocked at the Python level: every test runs upload.py's
real subprocess path against a small fake `rclone` executable (a Python
script written fresh per test, chmod +x, on PATH or pointed to directly via
PUCKD_RCLONE) that records every invocation and backs "gdrive:" with a
plain local directory, so `copy` + `lsjson` behave like the real thing
(sizes come from files actually written to disk) unless a test asks the
fake to lie or fail — the same knobs the spec calls for: "fail the copy,
fail lsjson, or report a short remote size" (spec's upload.py line).

Run via: python3 -m pytest tools/tests/test_puckd_upload.py -q
"""

from __future__ import annotations

import contextlib
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools"))

from puckd import upload  # noqa: E402


_FAKE_RCLONE_SRC = textwrap.dedent(
    """\
    #!/usr/bin/env python3
    # Fake rclone for tools/tests/test_puckd_upload.py. Understands just the
    # four subcommands upload.py issues: config create, listremotes, copy,
    # lsjson. "gdrive:<path>" is backed by $FAKE_RCLONE_STORE/gdrive/<path>
    # on local disk so sizes are real unless a FAKE_RCLONE_* knob below says
    # to lie or fail.
    import json, os, shutil, sys
    from pathlib import Path

    LOG = os.environ.get("FAKE_RCLONE_LOG")
    STORE = os.environ.get("FAKE_RCLONE_STORE")


    def log_call(argv):
        if LOG:
            with open(LOG, "a") as f:
                f.write(json.dumps(argv) + "\\n")


    def fail(msg, code=1):
        print(msg, file=sys.stderr)
        sys.exit(code)


    def remotes_file():
        return Path(STORE) / ".remotes"


    def main():
        argv = sys.argv[1:]
        log_call(argv)
        if not argv:
            fail("fake rclone: no subcommand", 2)
        cmd = argv[0]

        if cmd == "config" and len(argv) >= 3 and argv[1] == "create":
            if os.environ.get("FAKE_RCLONE_FAIL_AUTHORIZE") == "1":
                fail("fake rclone: consent denied", 1)
            name = argv[2]
            rf = remotes_file()
            rf.parent.mkdir(parents=True, exist_ok=True)
            existing = set(rf.read_text().split()) if rf.exists() else set()
            existing.add(name)
            rf.write_text("\\n".join(sorted(existing)))
            sys.exit(0)

        if cmd == "listremotes":
            if os.environ.get("FAKE_RCLONE_FAIL_LISTREMOTES") == "1":
                fail("fake rclone: listremotes failed", 1)
            rf = remotes_file()
            names = rf.read_text().split() if rf.exists() else []
            for n in names:
                print(f"{n}:")
            sys.exit(0)

        if cmd == "copy" and len(argv) >= 3:
            if os.environ.get("FAKE_RCLONE_FAIL_COPY") == "1":
                fail("fake rclone: copy failed", 1)
            src, dst = argv[1], argv[2]
            remote_name, _, remote_path = dst.partition(":")
            dest_dir = Path(STORE) / remote_name / remote_path
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest_dir / Path(src).name)
            sys.exit(0)

        if cmd == "lsjson" and len(argv) >= 2:
            if os.environ.get("FAKE_RCLONE_FAIL_LSJSON") == "1":
                fail("fake rclone: lsjson failed", 1)
            if os.environ.get("FAKE_RCLONE_BAD_JSON") == "1":
                print("{not valid json")
                sys.exit(0)
            spec = argv[1]
            remote_name, _, remote_path = spec.partition(":")
            src_dir = Path(STORE) / remote_name / remote_path
            entries = []
            if (os.environ.get("FAKE_RCLONE_MISSING_FROM_LISTING") != "1"
                    and src_dir.is_dir()):
                for p in sorted(src_dir.iterdir()):
                    if not p.is_file():
                        continue
                    size = p.stat().st_size
                    if os.environ.get("FAKE_RCLONE_SHORT_SIZE") == "1":
                        size = max(0, size - 1)
                    entries.append({"Name": p.name, "Size": size, "IsDir": False})
            print(json.dumps(entries))
            sys.exit(0)

        fail(f"fake rclone: unhandled invocation {argv!r}", 2)


    if __name__ == "__main__":
        main()
    """
)


@contextlib.contextmanager
def _env(overrides: dict | None = None, unset=()):
    """Set/clear os.environ keys for the duration of the block, restoring
    the exact prior state (present-with-old-value, or absent) on exit —
    finer control than mock.patch.dict, which cannot express "this key must
    be absent" without clearing the whole environment."""
    overrides = overrides or {}
    sentinel = object()
    saved = {k: os.environ.get(k, sentinel) for k in set(overrides) | set(unset)}
    try:
        os.environ.update(overrides)
        for k in unset:
            os.environ.pop(k, None)
        yield
    finally:
        for k, v in saved.items():
            if v is sentinel:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class FakeRclone:
    """One fake-rclone rig: a temp dir holding the script, its backing
    "remote" store, and its call log. `env` is what a test hands to _env()
    to make upload.py's subprocess calls hit this rig via PUCKD_RCLONE."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.bin_dir = root / "bin"
        self.bin_dir.mkdir()
        self.bin_path = self.bin_dir / "rclone"
        self.bin_path.write_text(_FAKE_RCLONE_SRC)
        self.bin_path.chmod(self.bin_path.stat().st_mode | stat.S_IEXEC
                             | stat.S_IXGRP | stat.S_IXOTH)
        self.store = root / "store"
        self.store.mkdir()
        self.log_path = root / "calls.log"

    def base_env(self, **extra) -> dict:
        env = {
            upload.RCLONE_ENV: str(self.bin_path),
            "FAKE_RCLONE_STORE": str(self.store),
            "FAKE_RCLONE_LOG": str(self.log_path),
        }
        env.update(extra)
        return env

    def calls(self) -> list[list[str]]:
        if not self.log_path.exists():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines() if line]

    def remote_file_bytes(self, remote_dir: str, name: str) -> bytes:
        return (self.store / upload.REMOTE_NAME / remote_dir / name).read_bytes()


class UploadTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.rig = FakeRclone(Path(self._tmp.name) / "rig")
        self.work = Path(self._tmp.name) / "work"
        self.work.mkdir()

    def make_local_file(self, name: str, content: bytes) -> Path:
        p = self.work / name
        p.write_bytes(content)
        return p


class TestUploadHappyPath(UploadTestBase):
    def test_ok_when_remote_size_matches_local(self):
        local = self.make_local_file("ride.zip", b"x" * 4096)
        with _env(self.rig.base_env()):
            result = upload.upload(local, "JumpHeight/inbox")
        self.assertTrue(result.ok, result.error)
        self.assertIsNone(result.error)
        self.assertEqual(result.local_size, 4096)
        self.assertEqual(result.remote_size, 4096)
        self.assertEqual(self.rig.remote_file_bytes("JumpHeight/inbox", "ride.zip"),
                          b"x" * 4096)

    def test_strips_trailing_slash_from_remote_dir(self):
        """Spec's own example is 'gdrive:JumpHeight/inbox/' (docs/sync-agent-plan.md:49)
        — a trailing slash on remote_dir must land in the same place as without one."""
        local = self.make_local_file("ride.zip", b"y" * 10)
        with _env(self.rig.base_env()):
            result = upload.upload(local, "JumpHeight/inbox/")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(self.rig.remote_file_bytes("JumpHeight/inbox", "ride.zip"),
                          b"y" * 10)

    def test_empty_remote_dir_targets_remote_root(self):
        local = self.make_local_file("ride.zip", b"z" * 7)
        with _env(self.rig.base_env()):
            result = upload.upload(local, "")
        self.assertTrue(result.ok, result.error)
        calls = self.rig.calls()
        copy_call = next(c for c in calls if c[0] == "copy")
        self.assertEqual(copy_call[2], "gdrive:")

    def test_records_expected_rclone_invocations(self):
        local = self.make_local_file("ride.zip", b"a")
        with _env(self.rig.base_env()):
            upload.upload(local, "inbox")
        cmds = [c[0] for c in self.rig.calls()]
        self.assertEqual(cmds, ["copy", "lsjson"])


class TestUploadFailureModesReturnOkFalseWithReason(UploadTestBase):
    """docs/sync-agent-plan.md's upload.py line: fake rclone can 'fail the
    copy, fail lsjson, or report a short remote size; each must yield
    ok=False with the reason.'"""

    def test_copy_failure(self):
        local = self.make_local_file("ride.zip", b"a" * 100)
        with _env(self.rig.base_env(FAKE_RCLONE_FAIL_COPY="1")):
            result = upload.upload(local, "inbox")
        self.assertFalse(result.ok)
        self.assertIsNone(result.remote_size)
        self.assertIn("copy", result.error.lower())
        # lsjson must never even be attempted after a failed copy.
        self.assertEqual([c[0] for c in self.rig.calls()], ["copy"])

    def test_lsjson_failure(self):
        local = self.make_local_file("ride.zip", b"a" * 100)
        with _env(self.rig.base_env(FAKE_RCLONE_FAIL_LSJSON="1")):
            result = upload.upload(local, "inbox")
        self.assertFalse(result.ok)
        self.assertIsNone(result.remote_size)
        self.assertIn("lsjson", result.error.lower())
        # the copy DID happen — only the confirming read failed (G4).
        self.assertEqual(self.rig.remote_file_bytes("inbox", "ride.zip"), b"a" * 100)

    def test_short_remote_size(self):
        local = self.make_local_file("ride.zip", b"a" * 100)
        with _env(self.rig.base_env(FAKE_RCLONE_SHORT_SIZE="1")):
            result = upload.upload(local, "inbox")
        self.assertFalse(result.ok)
        self.assertEqual(result.local_size, 100)
        self.assertEqual(result.remote_size, 99)
        self.assertIn("99", result.error)
        self.assertIn("100", result.error)

    def test_copy_returns_zero_but_file_absent_from_listing_is_not_ok(self):
        """The gate's own wording: 'a copy that returned 0 but cannot be
        listed is NOT ok (gate G4)'."""
        local = self.make_local_file("ride.zip", b"a" * 50)
        with _env(self.rig.base_env(FAKE_RCLONE_MISSING_FROM_LISTING="1")):
            result = upload.upload(local, "inbox")
        self.assertFalse(result.ok)
        self.assertIsNone(result.remote_size)
        self.assertIn("ride.zip", result.error)
        self.assertIn("listing", result.error.lower())

    def test_lsjson_bad_json_is_not_ok(self):
        local = self.make_local_file("ride.zip", b"a" * 50)
        with _env(self.rig.base_env(FAKE_RCLONE_BAD_JSON="1")):
            result = upload.upload(local, "inbox")
        self.assertFalse(result.ok)
        self.assertIsNone(result.remote_size)
        self.assertIsNotNone(result.error)

    def test_local_file_missing_never_invokes_rclone(self):
        missing = self.work / "nope.zip"
        with _env(self.rig.base_env()):
            result = upload.upload(missing, "inbox")
        self.assertFalse(result.ok)
        self.assertIsNone(result.local_size)
        self.assertIsNone(result.remote_size)
        self.assertIn("nope.zip", result.error)
        # a reading that did not happen: no rclone call at all, not even one
        # that gets ignored.
        self.assertEqual(self.rig.calls(), [])


class TestAuthorize(UploadTestBase):
    def test_authorize_success_then_is_authorized_true(self):
        with _env(self.rig.base_env()):
            self.assertFalse(upload.is_authorized())
            ok = upload.authorize()
            self.assertTrue(ok)
            self.assertTrue(upload.is_authorized())
        create_calls = [c for c in self.rig.calls() if c[:2] == ["config", "create"]]
        self.assertEqual(len(create_calls), 1)
        self.assertEqual(create_calls[0][2], upload.REMOTE_NAME)

    def test_authorize_failure_leaves_not_authorized(self):
        with _env(self.rig.base_env(FAKE_RCLONE_FAIL_AUTHORIZE="1")):
            ok = upload.authorize()
            self.assertFalse(ok)
            self.assertFalse(upload.is_authorized())

    def test_is_authorized_false_when_listremotes_itself_fails(self):
        """Even if 'gdrive' was created earlier, a listremotes call that
        fails must read as not-authorized, not as a stale-but-true guess —
        no verdict without a (successful) measurement."""
        with _env(self.rig.base_env()):
            self.assertTrue(upload.authorize())
        with _env(self.rig.base_env(FAKE_RCLONE_FAIL_LISTREMOTES="1")):
            self.assertFalse(upload.is_authorized())


class TestRcloneBinaryResolution(UploadTestBase):
    def test_missing_rclone_is_ok_false_not_an_exception(self):
        empty_dir = self.work / "empty_path"
        empty_dir.mkdir()
        with _env({"PATH": str(empty_dir)}, unset=(upload.RCLONE_ENV,)):
            self.assertFalse(upload.is_authorized())
            self.assertFalse(upload.authorize())
            local = self.make_local_file("ride.zip", b"a")
            result = upload.upload(local, "inbox")
        self.assertFalse(result.ok)
        self.assertIn("rclone", result.error.lower())

    def test_path_fallback_used_when_env_var_unset(self):
        """PUCKD_RCLONE takes precedence when set (every other test here
        proves that); this proves the documented fallback — PATH — is real,
        by putting ONLY the fake rclone's directory ahead of the ambient
        PATH (which may have a real rclone on this machine) and confirming
        the fake one, not a real Drive, receives the call."""
        local = self.make_local_file("ride.zip", b"q" * 12)
        new_path = f"{self.rig.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"
        with _env({"PATH": new_path, **self.rig.base_env()}, unset=(upload.RCLONE_ENV,)):
            result = upload.upload(local, "inbox")
        self.assertTrue(result.ok, result.error)
        self.assertEqual(self.rig.remote_file_bytes("inbox", "ride.zip"), b"q" * 12)


if __name__ == "__main__":
    unittest.main()
