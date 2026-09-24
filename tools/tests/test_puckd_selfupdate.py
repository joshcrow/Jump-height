#!/usr/bin/env python3
"""tools/puckd/selfupdate.py, pinned end to end with no network, no zip, no
launchd, and — the part that matters — no way for a failure to eat the
installed bundle.

The app now replaces itself on a rider's Mac 300 miles away with nobody
watching. So the two tests this file exists for are the two that must never
regress:

  * a sha256 that does not match unpacks NOTHING and touches nothing;
  * a stage that fails half way leaves the installed bundle BYTE-IDENTICAL
    (asserted by hashing every file in it before and after, not by checking
    that the directory still exists).

Everything is driven through apply()'s injectable seams — the same pattern
flash.flash() (flash.py:253-267) uses — so this suite runs in under a second
on any machine.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from puckd import selfupdate  # noqa: E402


def _tree_digest(root: Path) -> str:
    """One hash over every relative path and every byte under root — what
    "byte-identical" is asserted with below, rather than the much weaker
    "the directory is still there"."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(str(p.relative_to(root)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def _make_bundle(path: Path, version: str, marker: str) -> Path:
    """A minimal .app: Contents/Info.plist with a version, plus a payload
    file so an "installed" and a "new" bundle are distinguishable by
    content and not just by name."""
    (path / "Contents" / "MacOS").mkdir(parents=True, exist_ok=True)
    (path / "Contents" / "Resources").mkdir(parents=True, exist_ok=True)
    with open(path / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleShortVersionString": version,
                       "CFBundleIdentifier": "com.jumpheight.puckd"}, f)
    (path / "Contents" / "MacOS" / "JumpHeight Sync").write_text(marker)
    return path


class VersionsCompareAsDottedIntegers(unittest.TestCase):
    def test_strictly_greater_is_the_only_update(self):
        m = {"version": "1.0.1", "url": "https://x/y.zip"}
        self.assertTrue(selfupdate.needs_update("1.0.0", m))
        self.assertFalse(selfupdate.needs_update("1.0.1", m))
        self.assertFalse(selfupdate.needs_update("1.0.2", m))

    def test_components_are_integers_not_strings(self):
        # The one every "compare versions as text" bug is made of.
        self.assertTrue(selfupdate.needs_update("1.0.9", {"version": "1.0.10"}))
        self.assertFalse(selfupdate.needs_update("1.0.10", {"version": "1.0.9"}))

    def test_short_and_long_versions_pad_with_zeros(self):
        self.assertFalse(selfupdate.needs_update("1.0", {"version": "1.0.0"}))
        self.assertTrue(selfupdate.needs_update("1.0", {"version": "1.0.1"}))

    def test_a_dev_checkout_is_below_every_release(self):
        self.assertTrue(selfupdate.needs_update(selfupdate.DEV_VERSION,
                                                {"version": "1.0.0"}))

    def test_no_manifest_or_a_junk_version_is_never_an_update(self):
        self.assertFalse(selfupdate.needs_update("1.0.0", None))
        self.assertFalse(selfupdate.needs_update("1.0.0", {}))
        self.assertFalse(selfupdate.needs_update("1.0.0", {"version": "latest"}))
        self.assertFalse(selfupdate.needs_update("1.0.0", {"version": 2}))


class TheVersionComesFromTheInstalledBundle(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_reads_cfbundleshortversionstring(self):
        b = _make_bundle(self.tmp / "JumpHeight Sync.app", "1.2.3", "installed")
        self.assertEqual(selfupdate.current_version(bundle_path=b), "1.2.3")

    def test_an_unreadable_plist_is_dev_not_a_guess(self):
        b = self.tmp / "Broken.app"
        (b / "Contents").mkdir(parents=True)
        (b / "Contents" / "Info.plist").write_text("not a plist")
        self.assertEqual(selfupdate.current_version(bundle_path=b),
                         selfupdate.DEV_VERSION)

    def test_a_source_checkout_is_dev(self):
        with patch.object(selfupdate, "is_frozen", lambda: False):
            self.assertEqual(selfupdate.current_version(), selfupdate.DEV_VERSION)

    def test_bundle_path_is_derived_from_resourcepath(self):
        rp = self.tmp / "JumpHeight Sync.app" / "Contents" / "Resources"
        rp.mkdir(parents=True)
        with patch.dict(os.environ, {"RESOURCEPATH": str(rp)}):
            self.assertEqual(selfupdate.installed_bundle_path(),
                             (self.tmp / "JumpHeight Sync.app").resolve())

    def test_no_resourcepath_is_no_bundle(self):
        env = {k: v for k, v in os.environ.items() if k != "RESOURCEPATH"}
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(selfupdate.installed_bundle_path())


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class AManifestThatCannotBeTrustedIsNoUpdate(unittest.TestCase):
    """latest_manifest() NEVER RAISES — flash.latest_manifest()'s posture,
    restated here because a self-updater that throws on a bad byte takes the
    whole poll loop with it."""

    def _fetch(self, body, status=200):
        def fake_urlopen(url, timeout=None, context=None):
            self.url = url
            return _FakeResponse(body, status)
        with patch("urllib.request.urlopen", fake_urlopen):
            return selfupdate.latest_manifest("https://site.invalid")

    def test_the_path_is_app_latest_json(self):
        self._fetch(json.dumps({"version": "1.0.1",
                                "url": "https://github.com/joshcrow/Jump-height/releases/download/v9/x.zip"}).encode())
        self.assertEqual(self.url, "https://site.invalid/app/latest.json")

    def test_a_good_manifest_parses(self):
        m = self._fetch(json.dumps({"version": "1.0.1", "url": "https://github.com/joshcrow/Jump-height/releases/download/v9/x.zip",
                                    "sha256": "a" * 64}).encode())
        self.assertEqual(m["version"], "1.0.1")

    def test_non_200_is_none(self):
        self.assertIsNone(self._fetch(b'{"version":"9.9.9","url":"https://github.com/joshcrow/Jump-height/releases/download/v9/x"}', 404))

    def test_not_json_is_none(self):
        self.assertIsNone(self._fetch(b"<html>404</html>"))

    def test_not_an_object_is_none(self):
        self.assertIsNone(self._fetch(b'["1.0.1"]'))

    def test_a_bad_version_is_none(self):
        self.assertIsNone(self._fetch(b'{"version":"latest","url":"https://github.com/joshcrow/Jump-height/releases/download/v9/x.zip"}'))

    def test_a_non_https_url_is_none(self):
        self.assertIsNone(self._fetch(b'{"version":"1.0.1","url":"http://g/x.zip"}'))

    def test_offline_is_none_not_an_exception(self):
        def boom(*a, **k):
            raise OSError("no route to host")
        with patch("urllib.request.urlopen", boom):
            self.assertIsNone(selfupdate.latest_manifest("https://site.invalid"))

    def test_a_missing_sha256_is_passed_through_for_the_gate_to_refuse(self):
        # flash.py:140-148's rule: "cannot verify" must be reported by the
        # gate, not collapse into the same silence "no manifest" produces.
        m = self._fetch(b'{"version":"1.0.1","url":"https://github.com/joshcrow/Jump-height/releases/download/v9/x.zip"}')
        self.assertIsNotNone(m)
        self.assertIsNone(m.get("sha256"))


class _ApplyHarness(unittest.TestCase):
    """An installed bundle, a new bundle, and a zip of the new one — plus
    recording fakes for every seam apply() takes."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.apps = self.tmp / "Applications"
        self.apps.mkdir()
        self.installed = _make_bundle(self.apps / "JumpHeight Sync.app",
                                      "1.0.0", "the installed app")
        self.before = _tree_digest(self.installed)

        self.new_src = _make_bundle(self.tmp / "new" / "JumpHeight Sync.app",
                                    "1.0.1", "the NEW app")
        self.zip_src = self.tmp / "payload.zip"
        with zipfile.ZipFile(self.zip_src, "w") as zf:
            for p in sorted(self.new_src.rglob("*")):
                if p.is_file():
                    zf.write(p, Path("JumpHeight Sync.app") /
                             p.relative_to(self.new_src))
        self.payload = self.zip_src.read_bytes()
        self.sha = hashlib.sha256(self.payload).hexdigest()

        self.downloads = self.tmp / "puckd_home" / "updates"
        self.log_lines = []
        self.kicked = []
        self.order = []

    def manifest(self, **over):
        m = {"version": "1.0.1", "url": "https://github.com/joshcrow/Jump-height/releases/download/v9/JumpHeight-Sync-1.0.1.zip",
             "sha256": self.sha, "bytes": len(self.payload)}
        m.update(over)
        return m

    def download(self, body=None):
        def fn(url, dest):
            self.order.append("download")
            Path(dest).write_bytes(self.payload if body is None else body)
        return fn

    def unpack(self, fail=False):
        def fn(zip_path, dest_dir):
            self.order.append("unpack")
            if fail:
                raise OSError("corrupt archive")
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(dest_dir)
        return fn

    def kickstart(self, ok=True):
        def fn():
            self.order.append("kickstart")
            self.kicked.append(True)
            return ok
        return fn

    def apply(self, manifest=None, **over):
        kwargs = dict(
            bundle_path=self.installed,
            download_dir=self.downloads,
            log=self.log_lines.append,
            frozen=True,
            download_fn=self.download(),
            unpack_fn=self.unpack(),
            kickstart_fn=self.kickstart(),
        )
        kwargs.update(over)
        return selfupdate.apply(manifest or self.manifest(), **kwargs)


class TheHappyPath(_ApplyHarness):
    def test_the_new_bundle_is_in_place_the_old_is_gone_and_kickstart_is_last(self):
        result = self.apply()
        self.assertTrue(result.ok, result)
        self.assertEqual(result.stage, selfupdate.STAGE_RESTART)
        self.assertIsNone(result.error)
        self.assertEqual(
            (self.installed / "Contents" / "MacOS" / "JumpHeight Sync").read_text(),
            "the NEW app")
        self.assertEqual(selfupdate.current_version(bundle_path=self.installed), "1.0.1")
        self.assertFalse((self.apps / "JumpHeight Sync.app.old").exists(),
                         ".old must be deleted, never left to confuse the next run")
        self.assertFalse((self.apps / "JumpHeight Sync.app.new").exists())
        self.assertEqual(self.order[-1], "kickstart",
                         "the restart is the LAST thing: it kills this process")
        self.assertEqual(self.order, ["download", "unpack", "kickstart"])

    def test_a_kickstart_that_did_not_fire_is_reported_not_swallowed(self):
        # CLAUDE.md rule 3: the swap landed, the restart did not — that must
        # be visible, not a clean ok with nothing said.
        result = self.apply(kickstart_fn=self.kickstart(ok=False))
        self.assertTrue(result.ok)
        self.assertIsNotNone(result.error)
        self.assertTrue(any("next launch" in l for l in self.log_lines), self.log_lines)


class TheSha256IsAGate(_ApplyHarness):
    def test_a_mismatch_unpacks_nothing_and_touches_nothing(self):
        called = []
        result = self.apply(
            self.manifest(sha256="b" * 64),
            unpack_fn=lambda z, d: called.append("unpacked"))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, selfupdate.STAGE_SHA256)
        self.assertEqual(called, [], "NOTHING may be unpacked before the hash matches")
        self.assertEqual(self.kicked, [])
        self.assertEqual(_tree_digest(self.installed), self.before)
        self.assertFalse((self.apps / "JumpHeight Sync.app.new").exists())
        self.assertFalse((self.downloads / "1.0.1.zip").exists(),
                         "a zip whose bytes are wrong is not kept to be retried as-is")

    def test_a_truncated_download_fails_the_gate(self):
        result = self.apply(download_fn=self.download(body=self.payload[:100]))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, selfupdate.STAGE_SHA256)
        self.assertEqual(_tree_digest(self.installed), self.before)

    def test_a_manifest_with_no_sha256_refuses(self):
        called = []
        m = self.manifest()
        del m["sha256"]
        result = self.apply(m, unpack_fn=lambda z, d: called.append("unpacked"))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, selfupdate.STAGE_SHA256)
        self.assertEqual(called, [])
        self.assertEqual(_tree_digest(self.installed), self.before)

    def test_a_download_that_fails_stops_there(self):
        def boom(url, dest):
            raise OSError("connection reset")
        result = self.apply(download_fn=boom)
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, selfupdate.STAGE_DOWNLOAD)
        self.assertEqual(_tree_digest(self.installed), self.before)


class AFailureLeavesTheInstalledBundleAlone(_ApplyHarness):
    def test_an_unpack_failure_never_reaches_the_bundle(self):
        result = self.apply(unpack_fn=self.unpack(fail=True))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, selfupdate.STAGE_UNPACK)
        self.assertEqual(_tree_digest(self.installed), self.before)
        self.assertEqual(self.kicked, [])

    def test_a_zip_with_no_app_at_its_root_is_refused(self):
        def unpack_junk(zip_path, dest_dir):
            Path(dest_dir).mkdir(parents=True, exist_ok=True)
            (Path(dest_dir) / "readme.txt").write_text("nope")
        result = self.apply(unpack_fn=unpack_junk)
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, selfupdate.STAGE_UNPACK)
        self.assertEqual(_tree_digest(self.installed), self.before)

    def test_a_staging_copy_failure_leaves_the_bundle_byte_identical(self):
        def copy_boom(src, dst):
            raise OSError("No space left on device")
        result = self.apply(copytree_fn=copy_boom)
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, selfupdate.STAGE_STAGE)
        self.assertEqual(_tree_digest(self.installed), self.before)
        self.assertTrue(self.installed.is_dir())
        self.assertFalse((self.apps / "JumpHeight Sync.app.new").exists())
        self.assertEqual(self.kicked, [])

    def test_a_failed_swap_rolls_the_installed_bundle_back(self):
        """The dangerous window: installed has ALREADY been renamed to .old
        when the second rename fails. The app must come back."""
        real_rename = os.rename
        calls = []

        def rename(src, dst):
            calls.append((Path(src).name, Path(dst).name))
            if len(calls) == 2:            # installed->.old worked; this is .new->installed
                raise OSError("Operation not permitted")
            real_rename(src, dst)

        result = self.apply(rename_fn=rename)
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, selfupdate.STAGE_STAGE)
        self.assertTrue(self.installed.is_dir(), "the app must be back where it was")
        self.assertEqual(_tree_digest(self.installed), self.before)
        self.assertEqual(
            (self.installed / "Contents" / "MacOS" / "JumpHeight Sync").read_text(),
            "the installed app")
        self.assertFalse((self.apps / "JumpHeight Sync.app.old").exists())
        self.assertEqual(self.kicked, [])

    def test_the_installed_bundle_still_exists_at_every_point_of_a_failed_swap(self):
        """Stronger than the test above: at NO moment during a failing apply()
        may /Applications be without the app for longer than one rename. This
        checks the invariant the other way round — after the failure, the
        bundle is there and nothing named .new or .old is left behind."""
        real_rename = os.rename

        def rename(src, dst):
            if Path(src).name.endswith(".app.new"):
                raise OSError("Operation not permitted")
            real_rename(src, dst)

        self.apply(rename_fn=rename)
        leftovers = [p.name for p in self.apps.iterdir()
                     if p.name.endswith(".new") or p.name.endswith(".old")]
        self.assertEqual(leftovers, [])
        self.assertEqual(_tree_digest(self.installed), self.before)


class ADevCheckoutIsNeverReplaced(_ApplyHarness):
    def test_not_frozen_refuses_before_it_downloads_anything(self):
        called = []
        result = self.apply(frozen=False,
                            download_fn=lambda u, d: called.append("downloaded"))
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, selfupdate.STAGE_FROZEN)
        self.assertEqual(called, [], "a dev checkout does not even download")
        self.assertEqual(_tree_digest(self.installed), self.before)
        self.assertEqual(self.kicked, [])

    def test_frozen_defaults_to_is_frozen_so_a_caller_cannot_forget(self):
        with patch.object(selfupdate, "is_frozen", lambda: False):
            result = selfupdate.apply(self.manifest(), bundle_path=self.installed,
                                      download_dir=self.downloads,
                                      download_fn=self.download(),
                                      kickstart_fn=self.kickstart())
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, selfupdate.STAGE_FROZEN)
        self.assertEqual(self.kicked, [])

    def test_a_missing_bundle_path_refuses(self):
        result = self.apply(bundle_path=None)
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, selfupdate.STAGE_FROZEN)


class ApplyNeverRaises(_ApplyHarness):
    def test_every_seam_blowing_up_at_once_is_still_a_result(self):
        def boom(*a, **k):
            raise RuntimeError("everything is on fire")
        for seam in ("download_fn", "unpack_fn", "copytree_fn", "kickstart_fn"):
            with self.subTest(seam=seam):
                result = self.apply(**{seam: boom})
                self.assertIsInstance(result, selfupdate.UpdateResult)


class TheRealUnpackHandlesARealZip(_ApplyHarness):
    def test_default_unpack_finds_the_app_at_the_zips_root(self):
        # No unpack_fn override: exercises _default_unpack (ditto, or its
        # zipfile fallback) against the zip built the way build.sh builds it.
        result = self.apply(unpack_fn=selfupdate._default_unpack)
        self.assertTrue(result.ok, (result, self.log_lines))
        self.assertEqual(
            (self.installed / "Contents" / "MacOS" / "JumpHeight Sync").read_text(),
            "the NEW app")


if __name__ == "__main__":
    unittest.main()


# ---- added by the post-faa08a9 adversarial review -------------------------


class TheStagedBundleIsInspectedBeforeItIsSwappedIn(_ApplyHarness):
    """The sha256 gate proves the bytes are the bytes the manifest named.
    It proves nothing about whether they are a LAUNCHABLE app, or about
    whether the build inside is the version the manifest advertises. Both
    failures are unrecoverable from 300 miles away."""

    def _zip_of(self, app_dir: Path):
        """Re-zip `app_dir` as the payload, with the manifest's sha updated."""
        zp = self.tmp / "payload2.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            for p in sorted(app_dir.rglob("*")):
                if p.is_file():
                    zf.write(p, Path("JumpHeight Sync.app") / p.relative_to(app_dir))
        self.payload = zp.read_bytes()
        self.sha = hashlib.sha256(self.payload).hexdigest()

    def test_a_bundle_with_no_executable_is_refused(self):
        broken = self.tmp / "broken" / "JumpHeight Sync.app"
        (broken / "Contents" / "MacOS").mkdir(parents=True)
        with open(broken / "Contents" / "Info.plist", "wb") as f:
            plistlib.dump({"CFBundleShortVersionString": "1.0.1"}, f)
        self._zip_of(broken)          # no file under Contents/MacOS at all
        result = self.apply()
        self.assertFalse(result.ok, result)
        self.assertEqual(result.stage, selfupdate.STAGE_UNPACK)
        self.assertIn("Contents/MacOS", result.error)
        self.assertEqual(_tree_digest(self.installed), self.before,
                         "the working app must be untouched")
        self.assertEqual(self.kicked, [])

    def test_a_bundle_whose_version_disagrees_with_the_manifest_is_refused(self):
        # release.sh's APP_VERSION bump silently not taking: a manifest that
        # says 1.0.1 carrying a bundle that is still 1.0.0. Installing it
        # leaves needs_update() true forever -- a rider's Mac kickstarting
        # itself every six hours for good.
        stale = _make_bundle(self.tmp / "stale" / "JumpHeight Sync.app",
                             "1.0.0", "a build whose version bump did not take")
        self._zip_of(stale)
        result = self.apply()
        self.assertFalse(result.ok, result)
        self.assertEqual(result.stage, selfupdate.STAGE_UNPACK)
        self.assertIn("does not match the version it is published as", result.error)
        self.assertEqual(_tree_digest(self.installed), self.before)
        self.assertEqual(self.kicked, [])


class ASwapThatLandedAndARestartThatDidNot(_ApplyHarness):
    """apply() can return ok=True with the new bundle in place and the
    process still running the old code. Two things must hold across that
    gap, or the rider silently keeps running the old build."""

    def test_running_version_is_the_one_read_first_not_the_one_on_disk(self):
        before = selfupdate._RUNNING_VERSION
        self.addCleanup(setattr, selfupdate, "_RUNNING_VERSION", before)
        selfupdate._RUNNING_VERSION = None
        with patch.object(selfupdate, "current_version", lambda **k: "1.0.0"):
            first = selfupdate.running_version()
            self.assertEqual(first, "1.0.0")
        # The plist on disk now says 1.0.1; the running process has not changed.
        with patch.object(selfupdate, "current_version", lambda **k: "1.0.1"):
            self.assertEqual(selfupdate.running_version(), "1.0.0")
            self.assertTrue(selfupdate.needs_update(selfupdate.running_version(),
                                                    {"version": "1.0.1"}),
                            "the restart it still owes must stay visible")

    def test_a_second_attempt_restarts_instead_of_downloading_110mb_again(self):
        self.assertTrue(self.apply().ok)                 # 1.0.1 is now installed
        self.order.clear()
        result = self.apply()                            # same manifest again
        self.assertTrue(result.ok, result)
        self.assertEqual(result.stage, selfupdate.STAGE_RESTART)
        self.assertEqual(self.order, ["kickstart"],
                         "nothing is downloaded or unpacked a second time")


class OnlyOneAgentMaySwapTheBundle(_ApplyHarness):
    def test_a_second_apply_while_one_holds_the_lock_refuses(self):
        held = selfupdate._update_lock(self.downloads)
        if held is None:
            self.skipTest("no fcntl on this platform")
        try:
            result = self.apply()
        finally:
            held.close()
        self.assertFalse(result.ok, result)
        self.assertIn("another agent", result.error)
        self.assertEqual(_tree_digest(self.installed), self.before)
        self.assertEqual(self.order, [], "nothing was even downloaded")

    def test_the_lock_is_released_when_apply_returns(self):
        self.assertTrue(self.apply().ok)
        again = selfupdate._update_lock(self.downloads)
        self.assertIsNotNone(again)
        again.close()


class TheUpdatesDirectoryDoesNotGrowForever(_ApplyHarness):
    def test_a_previous_versions_leftovers_are_pruned(self):
        self.downloads.mkdir(parents=True, exist_ok=True)
        stale_zip = self.downloads / "0.9.9.zip"
        stale_zip.write_bytes(b"x" * 1000)
        stale_part = self.downloads / "0.9.8.zip.part"
        stale_part.write_bytes(b"y" * 1000)
        stale_dir = self.downloads / "0.9.9"
        stale_dir.mkdir()
        (stale_dir / "junk").write_text("an unpack that never installed")

        self.assertTrue(self.apply().ok)

        self.assertFalse(stale_zip.exists())
        self.assertFalse(stale_part.exists())
        self.assertFalse(stale_dir.exists())


class TheDownloadHostIsPinned(unittest.TestCase):
    """A manifest carries its own sha256, so a manifest-only compromise could
    name any file and its hash; the host is the one thing it cannot choose."""

    def test_a_foreign_host_is_refused_by_the_manifest_parser(self):
        import json
        from unittest.mock import patch
        body = json.dumps({"version": "9.9.9", "url": "https://evil.example/JumpHeight-Sync-9.9.9.zip",
                           "sha256": "ab" * 32, "bytes": 1}).encode()
        class R:
            status = 200
            def read(self): return body
            def __enter__(self): return self
            def __exit__(self, *a): return False
        with patch.object(selfupdate.urllib.request, "urlopen", return_value=R()):
            self.assertIsNone(selfupdate.latest_manifest("https://site.invalid"))

    def test_the_pinned_prefix_is_this_repositorys_releases(self):
        self.assertEqual(selfupdate.RELEASE_URL_PREFIX,
                         "https://github.com/joshcrow/Jump-height/releases/download/")


class NotEnoughDisk(_ApplyHarness):
    """The rider's Mac was full (measured 2026-09-14): the download and then
    the unpack failed every six hours. Now the check refuses up front, names
    the numbers, and downloads nothing."""

    def test_refuses_before_downloading_when_the_disk_is_short(self):
        calls = []
        manifest = dict(self.manifest()); manifest["bytes"] = 100_000_000
        r = self.apply(manifest, download_fn=lambda u, d: calls.append(u),
                       free_bytes_fn=lambda p: 50_000_000)
        self.assertFalse(r.ok)
        self.assertEqual(r.stage, selfupdate.STAGE_DOWNLOAD)
        self.assertIn("not enough free disk", r.error)
        self.assertIn("400 MB needed", r.error)
        self.assertEqual(calls, [], "nothing was downloaded")

    def test_space_needed_is_four_zips(self):
        self.assertEqual(selfupdate._space_needed({"bytes": 10}), 40)
        self.assertEqual(selfupdate._space_needed({}), 480_000_000)


class LatestManifestSaysWhyItFailed(unittest.TestCase):
    """latest_manifest() keeps its contract (never raises, None on any
    failure) and records which failure for the daemon's log."""

    def test_an_unreachable_site_records_the_error(self):
        self.assertIsNone(selfupdate.latest_manifest("http://127.0.0.1:1"))
        why = selfupdate.last_manifest_error()
        self.assertIsNotNone(why)
        self.assertIn("127.0.0.1:1", why)

