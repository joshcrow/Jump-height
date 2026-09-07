"""Tests for `./tools/jump ingest` (CONTRACT.md §2): importing a rider-synced
bundle (web/sync/'s zip, or its unzipped dir) into data/sessions/.

Bundles are built IN-TEST — zipfile + sim/trace_codec.encode_region() (the
same encoder tools/fake_device.py uses to build its 'session' scenario's
trace) + a manifest dict — rather than depending on a fixture file, so a
change to the wire contract shows up here immediately. Run via
./tools/jump simtest, or directly:
    python3 -m pytest tools/tests/test_ingest.py -q
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
JUMP = str(REPO / "tools" / "jump")
sys.path.insert(0, str(REPO / "sim"))

from trace_codec import decode_region_recovering, encode_region, region_to_csv  # noqa: E402

LOG_HZ = 50  # matches config/params.json's firmware.log_hz default


def run_cli(args, env_extra=None, timeout=60):
    env = dict(os.environ)
    env.update(env_extra or {})
    return subprocess.run([sys.executable, JUMP] + args, capture_output=True,
                          text=True, timeout=timeout, env=env, cwd=str(REPO),
                          stdin=subprocess.DEVNULL)


def _load_jump_module():
    """Load tools/jump (no .py extension) as an importable module, for
    direct unit-level access to its pure functions/helpers — same pattern
    as test_cli.py's helper of the same name."""
    import importlib.machinery
    import types

    loader = importlib.machinery.SourceFileLoader("jumpcli_ut_ingest", JUMP)
    mod = types.ModuleType("jumpcli_ut_ingest")
    mod.__file__ = JUMP
    loader.exec_module(mod)
    return mod


def make_manifest(**overrides) -> dict:
    """CONTRACT.md §2's manifest.json, with every key present (as a real
    bundle would have) and sane defaults a test overrides selectively."""
    m = {
        "bundle_version": 1,
        "page_version": "test-fixture",
        "puck_name": "JumpHeight-E2C4",
        "fw": "0.4.3",
        "src": "5c80a436",
        "synced_at_utc": "2026-09-13T18:32:07.123Z",
        "synced_at_local": "2026-09-13T14:32:07",
        "tz_offset_min": -240,
        "uptime_s": 12345.678,
        "trace_epoch_utc": "2026-09-13T15:06:21.445Z",
        "info_lines": ["INFO src=5c80a436 fw=0.4.3", "# name=JumpHeight-E2C4"],
        "cal": "CAL airtime_offset_s=0.0192 height_scale=1.000 source=device "
               "off_src=device scale_src=defaults vbat_src=defaults",
        "stats_before": "STATS session_jumps=0 stored_jumps=2 trace_bytes=0",
        "stats_after": "STATS session_jumps=0 stored_jumps=2 trace_bytes=0",
        "selftest_lines": ["SELFTEST BEGIN", "SELFTEST END result=PASS"],
        "trace_format": "jhtrace-v2-b64",
        "log_hz": LOG_HZ,
        "trace_bytes_device": 0,
        "trace_raw_bytes": 0,
        "trace_crc32": "00000000",
        "stored_jumps_device": 2,
        "jump_rows": 2,
        "verified": True,
        "cleared": False,
        "transfer": {"transport": "ble", "seconds": 5.0,
                    "bytes_received": 0, "mtu": None},
        "user_agent": "pytest",
    }
    m.update(overrides)
    return m


def trace_pairs(n_samples: int = 100, log_hz: int = LOG_HZ):
    """Evenly spaced at 1/log_hz from t=0 — trace_codec's own round-trip
    assumption (sim/trace_codec.py's module doc), same as
    tools/fake_device.py's demo session."""
    return [(i / log_hz, 1.0 + 0.001 * i) for i in range(n_samples)]


def jumps_csv_bytes(n_jumps: int = 2) -> bytes:
    rows = ["n,takeoff_s,airtime_raw_s,airtime_s,height_m"]
    for i in range(1, n_jumps + 1):
        rows.append(f"{i},{5.0 * i:.3f},0.500,0.520,0.300")
    return ("\n".join(rows) + "\n").encode()


def jhtrace_bundle_files(n_jumps: int = 2, n_samples: int = 100,
                         corrupt_crc: bool = False,
                         stored_jumps_device: "int | None" = None) -> dict:
    """A CONTRACT.md §2 bundle's files, trace_format=jhtrace-v2-b64."""
    pairs = trace_pairs(n_samples)
    image = encode_region(pairs, LOG_HZ)
    crc = f"{zlib.crc32(image) & 0xffffffff:08x}"
    if corrupt_crc:
        crc = "deadbeef" if crc != "deadbeef" else "deadbeee"
    jumps_bytes = jumps_csv_bytes(n_jumps)
    if stored_jumps_device is None:
        stored_jumps_device = n_jumps
    manifest = make_manifest(
        trace_format="jhtrace-v2-b64", log_hz=LOG_HZ,
        trace_raw_bytes=len(image), trace_crc32=crc,
        trace_bytes_device=len("t,mag\n") + len(region_to_csv(image, LOG_HZ)),
        stored_jumps_device=stored_jumps_device, jump_rows=n_jumps)
    return {
        "manifest.json": json.dumps(manifest).encode(),
        "jumps.csv": jumps_bytes,
        "trace.bin": image,
        "notes.txt": b"# JumpHeight rider notes -- 2026-09-13T14:32:07\nflat\n",
        "device.log": b"# JumpHeight fw v0.4.3\nREADY\nOK jumps\nOK traceraw\n",
    }, manifest, image


def csv_bundle_files(n_jumps: int = 2, n_samples: int = 100,
                     bad_byte_count: bool = False) -> dict:
    """A CONTRACT.md §2 bundle's files, trace_format=csv (old-firmware
    fallback: the page never got `traceraw`, so it fell back to `trace`)."""
    pairs = trace_pairs(n_samples)
    csv_text = "t,mag\n" + "".join(f"{t:.3f},{m:.3f}\n" for t, m in pairs)
    csv_bytes = csv_text.encode()
    trace_bytes_device = len(csv_bytes) + (500 if bad_byte_count else 0)
    jumps_bytes = jumps_csv_bytes(n_jumps)
    manifest = make_manifest(
        trace_format="csv", log_hz=LOG_HZ,
        trace_raw_bytes=None, trace_crc32=None,
        trace_bytes_device=trace_bytes_device,
        stored_jumps_device=n_jumps, jump_rows=n_jumps)
    return {
        "manifest.json": json.dumps(manifest).encode(),
        "jumps.csv": jumps_bytes,
        "trace.csv": csv_bytes,
        "notes.txt": b"",
        "device.log": b"# JumpHeight fw v0.4.3\nREADY\n",
    }, manifest, csv_bytes


def demo_session_bundle_files() -> tuple:
    """A CONTRACT.md §2 bundle built from the SAME demo session
    tools/fake_device.py's 'session' scenario preloads (generate.DEMO_JUMPS
    run through the real sim/detector.Detector) — unlike jhtrace_bundle_
    files()'s flat 1.0 + 0.001*i ramp (trace_pairs(), used by every other
    fixture in this file), a ramp never crosses the detector's free-fall
    gate, so it can never tell an offline pass that found nothing apart
    from one that actually ran. CONTRACT.md §0 item 3's whole point — a
    phone bundle is 'scored exactly the way a bench sync is' — goes unpinned
    without a trace that actually contains jumps."""
    from detector import Detector, load_params
    from generate import DEMO_JUMPS, synth_session

    params = load_params()
    times, mag = synth_session(DEMO_JUMPS, fs_hz=float(LOG_HZ), seed=3)
    det = Detector(params)
    jumps_rows = ["n,takeoff_s,airtime_raw_s,airtime_s,height_m"]
    trace_pairs_: list = []
    n = 0
    for t, m in zip(times, mag):
        trace_pairs_.append((t, m))
        ev = det.update(t, m)
        if ev:
            n += 1
            jumps_rows.append(f"{n},{ev.takeoff_time_s:.3f},{ev.airtime_raw_s:.3f},"
                              f"{ev.airtime_s:.3f},{ev.height_m:.3f}")
    jumps_bytes = ("\n".join(jumps_rows) + "\n").encode()
    image = encode_region(trace_pairs_, LOG_HZ)
    crc = f"{zlib.crc32(image) & 0xffffffff:08x}"
    manifest = make_manifest(
        trace_format="jhtrace-v2-b64", log_hz=LOG_HZ,
        trace_raw_bytes=len(image), trace_crc32=crc,
        trace_bytes_device=len("t,mag\n") + len(region_to_csv(image, LOG_HZ)),
        stored_jumps_device=n, jump_rows=n)
    files = {
        "manifest.json": json.dumps(manifest).encode(),
        "jumps.csv": jumps_bytes,
        "trace.bin": image,
        "notes.txt": b"# JumpHeight rider notes -- 2026-09-13T14:32:07\nflat\n",
        "device.log": b"# JumpHeight fw v0.4.3\nREADY\nOK jumps\nOK traceraw\n",
    }
    return files, manifest, image


def write_zip(tmp: Path, name: str, files: dict) -> Path:
    p = tmp / name
    with zipfile.ZipFile(p, "w") as zf:
        for fname, data in files.items():
            zf.writestr(fname, data)
    return p


def write_dir(tmp: Path, name: str, files: dict) -> Path:
    d = tmp / name
    d.mkdir()
    for fname, data in files.items():
        (d / fname).write_bytes(data)
    return d


class TestIngestHappyPath(unittest.TestCase):
    """(a)/(b): a clean bundle, either trace format, ingests cleanly."""

    def test_jhtrace_bundle_ingests(self):
        files, manifest, image = jhtrace_bundle_files(n_jumps=2)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "jumpheight-E2C4-20260913-1432.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

            sessions = list(out_dir.iterdir())
            self.assertEqual(len(sessions), 1)
            sess = sessions[0]
            self.assertEqual(sess.name, "20260913-143207-E2C4")

            # trace.csv is EXACTLY the decode's own rendering.
            expected_csv = "t,mag\n" + region_to_csv(image, LOG_HZ)
            self.assertEqual((sess / "trace.csv").read_text(), expected_csv)
            # trace.bin is kept, byte for byte.
            self.assertEqual((sess / "trace.bin").read_bytes(), image)
            # jumps.csv passes through untouched.
            self.assertEqual((sess / "jumps.csv").read_bytes(), files["jumps.csv"])

            session_json = json.loads((sess / "session.json").read_text())
            # The wall-clock anchor is the MANIFEST's, never this machine's.
            self.assertEqual(session_json["trace_epoch_utc"],
                             manifest["trace_epoch_utc"])
            self.assertEqual(session_json["synced_at_utc"], manifest["synced_at_utc"])
            self.assertEqual(session_json["unit"], "JumpHeight-E2C4")
            self.assertEqual(session_json["source"], "bundle")
            self.assertEqual(session_json["bundle"], bundle.name)
            self.assertEqual(session_json["manifest"]["trace_crc32"],
                             manifest["trace_crc32"])

            info = (sess / "session-info.txt").read_text()
            self.assertIn("fw=0.4.3", info)
            self.assertIn("CAL airtime_offset_s=", info)

            report = (sess / "report.md").read_text()
            self.assertIn("2 jumps", report)

            self.assertIn("✅ trace.bin verified", r.stdout)
            self.assertIn("✅ jumps verified", r.stdout)
            self.assertIn(str(sess), r.stdout)
            self.assertIn("Copy the session folder somewhere else", r.stdout)

    def test_csv_bundle_ingests(self):
        """The old-firmware fallback bundle shape: trace.csv, no trace.bin."""
        files, manifest, csv_bytes = csv_bundle_files(n_jumps=3)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "jumpheight-E2C4-20260913-1432.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

            sess = next(out_dir.iterdir())
            self.assertFalse((sess / "trace.bin").exists())
            self.assertEqual((sess / "trace.csv").read_bytes(), csv_bytes)
            report = (sess / "report.md").read_text()
            self.assertIn("3 jumps", report)
            self.assertIn("✅ trace.csv verified", r.stdout)

    def test_ingest_of_already_unzipped_directory_works(self):
        """A rider (or Josh) may forward the unzipped folder instead of the
        .zip — CONTRACT.md §2's bundle shape, opened by hand."""
        files, manifest, image = jhtrace_bundle_files(n_jumps=1)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle_dir = write_dir(tmp, "jumpheight-E2C4-20260913-1432", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle_dir), "--out", str(out_dir)])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            sess = next(out_dir.iterdir())
            self.assertTrue((sess / "trace.bin").exists())
            self.assertIn("1 jumps", (sess / "report.md").read_text())


class TestIngestRefusesBadBundles(unittest.TestCase):
    """(c)/(d)/(e): re-verification failures refuse the import (exit 1, a
    plain reason, nothing clear-worthy written) unless --force."""

    def test_bad_crc32_refuses_without_force(self):
        files, _manifest, _image = jhtrace_bundle_files(n_jumps=2, corrupt_crc=True)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "bad-crc.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("CRC MISMATCH", r.stdout)
            self.assertFalse(out_dir.exists(),
                             "a refused ingest must not write ANY session")

    def test_bad_crc32_with_force_writes_anyway(self):
        files, _manifest, image = jhtrace_bundle_files(n_jumps=2, corrupt_crc=True)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "bad-crc.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir), "--force"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("CRC MISMATCH", r.stdout)
            self.assertIn("UNVERIFIED", r.stdout)
            sess = next(out_dir.iterdir())
            self.assertEqual((sess / "trace.bin").read_bytes(), image)
            # The verdict must travel WITH the session, not just scroll past
            # in the terminal — a folder copied elsewhere is the only
            # remaining record once the puck may have been cleared.
            session_json = json.loads((sess / "session.json").read_text())
            self.assertFalse(session_json["verified"])
            self.assertTrue(session_json["forced"])
            self.assertTrue(any("CRC MISMATCH" in l for l in session_json["verification"]))
            failed_txt = (sess / "VERIFICATION-FAILED.txt").read_text()
            self.assertIn("CRC MISMATCH", failed_txt)

    def test_jump_row_mismatch_refuses_without_force(self):
        """jumps.csv holds 2 rows but the manifest's stored_jumps_device
        (what the puck itself reported) says 3 — CONTRACT.md §2's third hard
        check, reusing the exact _verify_jumps_rows() wording sync uses."""
        files, _manifest, _image = jhtrace_bundle_files(
            n_jumps=2, stored_jumps_device=3)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "jump-mismatch.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("JUMPS FILE SHORT", r.stdout)
            self.assertFalse(out_dir.exists())

    def test_jump_row_mismatch_with_force_writes_anyway(self):
        files, _manifest, _image = jhtrace_bundle_files(
            n_jumps=2, stored_jumps_device=3)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "jump-mismatch.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir), "--force"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            sess = next(out_dir.iterdir())
            self.assertTrue((sess / "jumps.csv").exists())

    def test_csv_byte_count_mismatch_refuses(self):
        files, _manifest, _csv = csv_bundle_files(n_jumps=2, bad_byte_count=True)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "csv-short.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("TRACE.CSV SHORT", r.stdout)
            self.assertFalse(out_dir.exists())

    def test_missing_manifest_is_an_error_naming_the_file(self):
        files, _manifest, _image = jhtrace_bundle_files(n_jumps=2)
        del files["manifest.json"]
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "no-manifest.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("manifest.json", r.stdout)
            self.assertFalse(out_dir.exists())

    def test_missing_bundle_path_is_an_error(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "does-not-exist.zip"
            r = run_cli(["ingest", str(missing)])
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            # A weaker version of this test (return code only) would pass on
            # ANY exit-1 failure — an unrelated crash, a bad argparse call —
            # and never actually see the message a human reads.
            self.assertIn("bundle not found", r.stdout)
            self.assertIn(missing.name, r.stdout)


class TestIngestOfflineReDetection(unittest.TestCase):
    """CONTRACT.md §0 item 3: a bundle must be scored exactly the way a
    bench sync is. TestIngestHappyPath's fixtures all use trace_pairs()'s
    flat ramp, which the detector never fires on — "0 jumps" in the report
    is therefore indistinguishable from "the offline pass never ran at
    all". This uses the SAME demo session tools/fake_device.py's 'session'
    scenario preloads, so there is something for the offline re-detection
    to actually find."""

    def test_offline_redetection_finds_the_same_jumps_as_the_device(self):
        files, manifest, image = demo_session_bundle_files()
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "jumpheight-E2C4-20260913-1432.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            n = manifest["stored_jumps_device"]
            self.assertGreater(n, 0, "fixture sanity: the demo session must "
                                     "actually contain jumps")
            sess = next(out_dir.iterdir())
            report = (sess / "report.md").read_text()
            self.assertIn(f"**Offline re-analysis:** {n} jumps", report)
            self.assertIn("✅ live and offline detection agree", report)
            self.assertIn(f"**Device:** {n} jumps", report)


class TestVerifyIngestBundleUnit(unittest.TestCase):
    """`_verify_ingest_bundle()`'s trace.bin checks, driven directly —
    mirrors test_cli.py's TestTracerawVerification so the ingest and sync
    verifiers are held to the same bar. Mutation/manual testing (2026-09-07)
    found the null-manifest-field path unverified: a corrupt trace.bin with
    trace_raw_bytes=null and trace_crc32=null printed a ✅ 'matches the
    manifest' line and imported with no --force needed."""

    def setUp(self):
        self.v = _load_jump_module()._verify_ingest_bundle

    @staticmethod
    def _jumps_lines(n=0):
        lines = ["n,takeoff_s,airtime_raw_s,airtime_s,height_m"]
        lines += [f"{i},{5.0*i:.3f},0.500,0.520,0.300" for i in range(1, n + 1)]
        return lines

    def test_crc_mismatch_fails(self):
        image = encode_region(trace_pairs(10), LOG_HZ)
        region = decode_region_recovering(image, LOG_HZ)
        manifest = make_manifest(trace_raw_bytes=len(image),
                                 trace_crc32="deadbeef", stored_jumps_device=0)
        ok, out = self.v([], self._jumps_lines(0), "jhtrace-v2-b64", image,
                         region, None, manifest)
        self.assertFalse(ok)
        self.assertTrue(any("CRC MISMATCH" in l for l in out))

    def test_null_crc_and_null_length_never_print_verified(self):
        """The exact repro from the finding: a corrupt trace.bin with BOTH
        manifest fields null must never claim a match against a crc32
        nobody supplied, and must not pass without --force."""
        image = bytearray(encode_region(trace_pairs(10), LOG_HZ))
        image[-1] ^= 0xFF  # corrupt it — a real bundle in this state is bad
        image = bytes(image)
        region = decode_region_recovering(image, LOG_HZ)
        manifest = make_manifest(trace_raw_bytes=None, trace_crc32=None,
                                 stored_jumps_device=0)
        ok, out = self.v([], self._jumps_lines(0), "jhtrace-v2-b64", image,
                         region, None, manifest)
        self.assertFalse(ok, "nothing was checkable — must not read as a pass")
        joined = "\n".join(out)
        self.assertNotIn("✅ trace.bin verified", joined)

    def test_damaged_patch_with_matching_crc_fails(self):
        samples = trace_pairs(1500)
        image = bytearray(encode_region(samples, LOG_HZ))
        image[10] ^= 0xFF
        image = bytes(image)
        region = decode_region_recovering(image, LOG_HZ)
        crc = f"{zlib.crc32(image) & 0xffffffff:08x}"
        manifest = make_manifest(trace_raw_bytes=len(image), trace_crc32=crc,
                                 stored_jumps_device=0)
        ok, out = self.v([], self._jumps_lines(0), "jhtrace-v2-b64", image,
                         region, None, manifest)
        self.assertFalse(ok, "damage inside the region must fail, not warn")
        self.assertNotIn("no bytes lost", "\n".join(out))

    def test_clean_bundle_is_verified(self):
        image = encode_region(trace_pairs(10), LOG_HZ)
        region = decode_region_recovering(image, LOG_HZ)
        crc = f"{zlib.crc32(image) & 0xffffffff:08x}"
        manifest = make_manifest(trace_raw_bytes=len(image), trace_crc32=crc,
                                 stored_jumps_device=0)
        ok, out = self.v([], self._jumps_lines(0), "jhtrace-v2-b64", image,
                         region, None, manifest)
        self.assertTrue(ok)
        self.assertTrue(any("✅ trace.bin verified" in l for l in out))


class TestIngestSessionDirName(unittest.TestCase):
    """_ingest_session_dir_name()'s docstring says it raises rather than
    falling back to datetime.now() — "exactly the kind of unlabeled guess
    CLAUDE.md rule 3 exists to prevent" — but nothing pinned either failure
    mode before this."""

    def setUp(self):
        self.fn = _load_jump_module()._ingest_session_dir_name

    def test_missing_synced_at_local_raises(self):
        with self.assertRaises(ValueError) as cm:
            self.fn({"puck_name": "JumpHeight-E2C4"})
        self.assertIn("synced_at_local", str(cm.exception))

    def test_unparseable_synced_at_local_raises(self):
        with self.assertRaises(ValueError) as cm:
            self.fn({"synced_at_local": "whenever", "puck_name": "JumpHeight-E2C4"})
        self.assertIn("synced_at_local", str(cm.exception))

    def test_never_falls_back_to_now(self):
        for bad_manifest in ({}, {"synced_at_local": "not-a-date"}):
            with self.assertRaises(ValueError):
                self.fn(bad_manifest)


class TestPuck4(unittest.TestCase):
    def setUp(self):
        self.fn = _load_jump_module()._puck4

    def test_last_dash_segment(self):
        self.assertEqual(self.fn("JumpHeight-E2C4"), "E2C4")

    def test_no_dash_is_unknown(self):
        self.assertEqual(self.fn("NoDashName"), "UNKN")

    def test_empty_is_unknown(self):
        self.assertEqual(self.fn(""), "UNKN")


class TestIngestUnverifiableManifest(unittest.TestCase):
    """`_verify_ingest_bundle()` returns ok=None (never silently upgraded to
    True) when nothing in the manifest was checkable — a csv-format bundle
    with stored_jumps_device=0 and no trace_bytes_device, say. cmd_ingest
    must proceed (there is nothing to refuse) but say so plainly, the way
    cmd_sync already does for an old-firmware download it can't verify."""

    def test_nothing_checkable_imports_but_says_unverified(self):
        files, _manifest, _csv = csv_bundle_files(n_jumps=0)
        manifest = json.loads(files["manifest.json"])
        manifest["stored_jumps_device"] = 0
        manifest["trace_bytes_device"] = None
        files["manifest.json"] = json.dumps(manifest).encode()
        files["jumps.csv"] = jumps_csv_bytes(0)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "jumpheight-E2C4-20260913-1432.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("could not be verified", r.stdout)
            sess = next(out_dir.iterdir())
            session_json = json.loads((sess / "session.json").read_text())
            self.assertFalse(session_json["verified"])
            self.assertFalse(session_json["forced"])
            self.assertTrue((sess / "VERIFICATION-FAILED.txt").exists())


class TestIngestOverwriteGuard(unittest.TestCase):
    """tools/jump:2122 (finding, minor): re-ingesting the same bundle (or
    any bundle that maps to the same session dir name) used to silently
    overwrite jumps.csv/notes.txt/trace.csv/report.md, including over a
    labels.csv a human had already added by hand."""

    def test_reingesting_refuses_without_force(self):
        files, _manifest, _image = jhtrace_bundle_files(n_jumps=2)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "jumpheight-E2C4-20260913-1432.zip", files)
            out_dir = tmp / "sessions"
            r1 = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r1.returncode, 0, r1.stdout + r1.stderr)
            sess = next(out_dir.iterdir())
            (sess / "labels.csv").write_text("n,label\n1,good\n")

            r2 = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r2.returncode, 1, r2.stdout + r2.stderr)
            self.assertIn("already exists", r2.stdout)
            # The hand-added file must survive the refused re-ingest.
            self.assertEqual((sess / "labels.csv").read_text(), "n,label\n1,good\n")

    def test_reingesting_with_force_overwrites(self):
        files, _manifest, _image = jhtrace_bundle_files(n_jumps=2)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "jumpheight-E2C4-20260913-1432.zip", files)
            out_dir = tmp / "sessions"
            r1 = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r1.returncode, 0)
            r2 = run_cli(["ingest", str(bundle), "--out", str(out_dir), "--force"])
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)


if __name__ == "__main__":
    unittest.main()


class TestIngestRefusesNoRec(unittest.TestCase):
    """fs=down (the watch's NO REC): the store never mounted, so the puck's
    counts are unknown, not zero — and a csv bundle built from them passes
    every arithmetic check vacuously (an empty trace.csv equals
    trace_bytes_device=0; the jump cross-check is skipped at
    stored_jumps_device=0). Found by the web reviewer on 2026-09-07 and
    confirmed here: before the check, such a bundle printed "✅ trace.csv
    verified: 0 bytes" and imported with exit 0."""

    def _no_rec_files(self, *, via_stats=True, via_devlog=False):
        files, _manifest, _csv = csv_bundle_files(n_jumps=0)
        manifest = json.loads(files["manifest.json"])
        manifest["stored_jumps_device"] = 0
        manifest["trace_bytes_device"] = 0
        stats = "STATS session_jumps=0 stored_jumps=0 trace_bytes=0"
        manifest["stats_before"] = stats + (" fs=down" if via_stats else "")
        manifest["stats_after"] = manifest["stats_before"]
        manifest["verified"] = True   # a lying (or older) page: ingest must not rely on it
        files["manifest.json"] = json.dumps(manifest).encode()
        files["jumps.csv"] = b""
        files["trace.csv"] = b""
        devlog = ["INFO fw=0.4.3 src=5c80a436", stats]
        if via_devlog:
            devlog.insert(1, "# storage NOT MOUNTED — the counts below are unknown, not zero")
        files["device.log"] = ("\n".join(devlog) + "\n").encode()
        return files

    def test_fs_down_in_stats_refuses(self):
        files = self._no_rec_files(via_stats=True)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "no-rec.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("NOT MOUNTED", r.stdout)
            self.assertNotIn("✅ trace.csv verified", r.stdout)
            self.assertFalse(out_dir.exists(), "a NO REC bundle must not become a session")

    def test_storage_not_mounted_chatter_alone_refuses(self):
        """The firmware says it twice; either half must be enough."""
        files = self._no_rec_files(via_stats=False, via_devlog=True)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "no-rec-chatter.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("NOT MOUNTED", r.stdout)
            self.assertFalse(out_dir.exists())

    def test_fs_down_with_force_imports_as_unverified(self):
        files = self._no_rec_files(via_stats=True)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "no-rec.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir), "--force"])
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            sess = next(out_dir.iterdir())
            self.assertIn("NOT MOUNTED", (sess / "VERIFICATION-FAILED.txt").read_text())
            self.assertFalse(json.loads((sess / "session.json").read_text())["verified"])


class TestIngestHonoursThePagesRefusal(unittest.TestCase):
    """manifest.verified=false is the page saying it saw something wrong at
    pull time. Some of its reasons ("the puck never said how much ride data
    to expect") leave no other mark in the bundle, so ingest must refuse on
    the flag itself — while a `true` from the page still proves nothing
    (every other test in this file re-verifies regardless)."""

    def test_verified_false_refuses_without_force(self):
        files, _manifest, _image = jhtrace_bundle_files(n_jumps=2)
        manifest = json.loads(files["manifest.json"])
        manifest["verified"] = False
        files["manifest.json"] = json.dumps(manifest).encode()
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bundle = write_zip(tmp, "page-refused.zip", files)
            out_dir = tmp / "sessions"
            r = run_cli(["ingest", str(bundle), "--out", str(out_dir)])
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
            self.assertIn("verified=false", r.stdout)
            self.assertFalse(out_dir.exists())
            r2 = run_cli(["ingest", str(bundle), "--out", str(out_dir), "--force"])
            self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
            sess = next(out_dir.iterdir())
            self.assertTrue((sess / "VERIFICATION-FAILED.txt").exists())
