"""Tests for `./tools/fitread.py`, against real FIT files — no synthesised ones.

A FIT parser can only be wrong in ways a hand-built fixture would not show:
Garmin's own quirks (a `session.timestamp` that equals `start_time` instead of
the end; `local_timestamp` tagged UTC; 646 records with a null
`enhanced_speed`) are exactly what has to be handled. So every number asserted
here was MEASURED by running the tool on the file named, and pasted in. If a
value changes, either the parser changed or the file did.

Two corpora, and the difference between them is the point:
  * `data/fit/2026-09-06-13-33-59.fit` — the owner's Epix, tracked in git,
    carries all four developer fields.
  * `data/nick-sessions/` — the rider's eight Garmin Connect exports. These
    are gitignored user-local captures, so those tests SKIP (visibly, with the
    path in the reason) rather than pass, when the data is not present.

Run via ./tools/jump simtest, or directly:
    python3 -m pytest tools/tests/test_fitread.py -q
"""

from __future__ import annotations

import tempfile
import csv
import json
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
FITREAD = str(REPO / "tools" / "fitread.py")

EPIX = REPO / "data" / "fit" / "2026-09-06-13-33-59.fit"
NICK_FIT = REPO / "data" / "nick-sessions" / "fits" / "23904301445_ACTIVITY.fit"
NICK_BIG_FIT = REPO / "data" / "nick-sessions" / "fits" / "24004323561_ACTIVITY.fit"
NICK_BIG_ZIP = REPO / "data" / "nick-sessions" / "raw" / "24004323561.zip"

NICK_REASON = (
    f"rider capture not present ({NICK_FIT.parent} is gitignored, user-local) "
    "— this is a skip, not a pass"
)
HAVE_NICK = NICK_FIT.exists() and NICK_BIG_FIT.exists() and NICK_BIG_ZIP.exists()


def run(args, expect=0):
    proc = subprocess.run([sys.executable, FITREAD] + [str(a) for a in args],
                          capture_output=True, text=True, timeout=180,
                          cwd=str(REPO), stdin=subprocess.DEVNULL)
    assert proc.returncode == expect, (
        f"exit {proc.returncode} (wanted {expect})\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}")
    return proc


def summarise(path, tmp: Path):
    """Run the tool with --out and return (screen text, summary dict, csv rows)."""
    proc = run([path, "--out", tmp])
    summary = json.loads((tmp / "fit-summary.json").read_text())
    with (tmp / "fit-records.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    return proc.stdout, summary, rows


class TestEpixWithDeveloperFields(unittest.TestCase):
    """data/fit/2026-09-06-13-33-59.fit — the 09-06 beach session, all four
    developer fields present. Tracked in git, so this must never skip."""

    @classmethod
    def setUpClass(cls):
        assert EPIX.exists(), f"{EPIX} is tracked in git and must exist"
        cls.tmp = Path(__import__("tempfile").mkdtemp(prefix="fitread-epix-"))
        cls.screen, cls.s, cls.rows = summarise(EPIX, cls.tmp)

    def test_identity(self):
        self.assertEqual(self.s["source"], "2026-09-06-13-33-59.fit")
        self.assertEqual(self.s["profile_name"], "Windsurf")
        self.assertEqual(self.s["sport"], "windsurfing")
        self.assertEqual(self.s["sub_sport"], "generic")

    def test_clock(self):
        self.assertEqual(self.s["start_utc"], "2026-09-06T17:33:59+00:00")
        self.assertEqual(self.s["end_utc"], "2026-09-06T18:31:44+00:00")
        self.assertEqual(self.s["utc_offset"], "-04:00")
        self.assertEqual(self.s["start_local"], "2026-09-06 13:33:59 -04:00")
        self.assertEqual(self.s["end_local"], "2026-09-06 14:31:44 -04:00")
        self.assertEqual(self.s["duration_s"], 3465.0)
        # session.timestamp on this file equals start_time, so the end must
        # come from the last record — 3465 s, not 0.
        self.assertAlmostEqual(self.s["session_total_elapsed_time_s"], 3464.824, places=3)

    def test_record_count(self):
        self.assertEqual(self.s["record_count"], 1404)
        self.assertEqual(len(self.rows), 1404)

    def test_developer_fields(self):
        self.assertTrue(self.s["developer_fields_present"])
        got = [(f["name"], f["units"], f["non_null_values"]) for f in self.s["developer_fields"]]
        self.assertEqual(got, [
            ("jump_height", "ft", 1213),
            ("jumps", "count", 1),
            ("best_jump", "ft", 1),
            ("best_airtime", "s", 1),
        ])
        jh = self.s["developer_fields"][0]
        self.assertEqual(jh["seen_in_messages"], {"record": 1404})
        self.assertEqual(jh["native_mesg_num"], "record")

    def test_developer_field_count_matches_status_md(self):
        """docs/STATUS.md 2026-09-06 states the number this tool re-derives.

        If this fails, the doc and the parser disagree about the same file and
        one of them is wrong — do not silence it by editing the assertion.
        """
        status = (REPO / "docs" / "STATUS.md").read_text()
        self.assertIn("`jump_height` in 1,213/1,404 records", status)
        self.assertEqual(self.s["developer_fields"][0]["non_null_values"], 1213)
        self.assertEqual(self.s["record_count"], 1404)

    def test_session_metrics(self):
        self.assertEqual(self.s["session_jumps"], 16)
        self.assertAlmostEqual(self.s["session_best_jump"], 3.110236406326294, places=6)
        self.assertEqual(self.s["session_best_jump_units"], "ft")
        self.assertAlmostEqual(self.s["session_best_airtime"], 0.7799999713897705, places=6)

    def test_max_speed(self):
        self.assertAlmostEqual(self.s["max_enhanced_speed_ms"], 6.168, places=3)
        self.assertAlmostEqual(self.s["session_enhanced_max_speed_ms"], 6.513, places=3)

    def test_time_on_foil(self):
        """One window, 17 s. docs/STATUS.md says 24 s for the same flight; the
        tool's docstring explains the 2.482 m/s sample at 14:08:54 that splits
        it. Both agree it is the ONLY window in the session."""
        foil = self.s["foil"]
        self.assertEqual(foil["speed_threshold_ms"], 2.5)
        self.assertEqual(foil["window_count"], 1)
        self.assertEqual(foil["total_seconds"], 17.0)
        self.assertEqual(foil["windows"], [{
            "start_utc": "2026-09-06T18:08:56+00:00",
            "end_utc": "2026-09-06T18:09:13+00:00",
            "seconds": 17.0,
        }])
        self.assertAlmostEqual(foil["percent_of_duration"], 0.4906, places=4)

    def test_no_warnings(self):
        self.assertEqual(self.s["warnings"], [])

    def test_device_identity(self):
        """Not on the screen, but in the JSON: which watch wrote this."""
        self.assertEqual(self.s["device"]["manufacturer"], "garmin")
        self.assertEqual(self.s["device"]["product"], "epix_gen2")

    def test_csv_has_jump_height_column_and_1213_values(self):
        self.assertEqual(self.s["csv_columns"],
                         ["timestamp_utc", "enhanced_speed", "jump_height",
                          "position_lat_deg", "position_long_deg"])
        filled = sum(1 for r in self.rows if r["jump_height"] != "")
        self.assertEqual(filled, 1213)

    def test_csv_first_row(self):
        first = self.rows[0]
        self.assertEqual(first["timestamp_utc"], "2026-09-06T17:33:59+00:00")
        self.assertEqual(first["enhanced_speed"], "0.0")
        self.assertEqual(first["jump_height"], "")  # null at t=0, not 0.0
        # Semicircles converted to degrees: Nags Head, NC.
        self.assertAlmostEqual(float(first["position_lat_deg"]), 35.8984661847353, places=9)
        self.assertAlmostEqual(float(first["position_long_deg"]), -75.58927022852004, places=9)

    def test_csv_speed_gaps_are_blank_not_zero(self):
        """646 records carry no enhanced_speed. A 0.0 there would be a number
        the file does not contain."""
        blank = sum(1 for r in self.rows if r["enhanced_speed"] == "")
        self.assertEqual(blank, 646)

    def test_screen(self):
        for want in ("profile               Windsurf",
                     "sport / sub_sport     windsurfing / generic",
                     "records               1404",
                     "session jumps         16",
                     "session best_jump     3.110 ft",
                     "session best_airtime  0.78 s",
                     "max enhanced_speed    6.168 m/s"):
            self.assertIn(want, self.screen)
        self.assertIn("jump_height     ft        1213 non-null   [record x1404]", self.screen)


@unittest.skipUnless(HAVE_NICK, NICK_REASON)
class TestLapGroundTruth(unittest.TestCase):
    """The lap button is the cheapest ground truth this project can get.

    The 2026-09-09 ride could only be anchored to +-3.5 min, by inferring that
    all ten stored detections must fall inside the activity — which is why
    F-33/F-35 rest on a bracket rather than a measurement. A rider pressing the
    lap button after each jump stamps the FIT to the second, with gear he
    already wears. This pins that we can READ them, and that we never mistake
    Garmin's own activity-closing lap for a press.
    """

    def test_the_09_09_ride_has_no_rider_presses(self):
        """Nobody pressed it that day. The tool must say so plainly rather than
        counting the automatic wrap-up lap as ground truth."""
        screen, s, _ = summarise(NICK_FIT, Path(tempfile.mkdtemp(prefix="fitread-lap-")))
        self.assertEqual(s["manual_laps"], 0)
        self.assertEqual(len(s["laps"]), 1, "Garmin writes one lap to close the activity")
        self.assertNotEqual(s["laps"][0].get("trigger"), "manual")
        self.assertIn("rider lap presses", screen)
        self.assertIn("none", screen)

    def test_every_lap_carries_a_timestamp(self):
        """A press with no time on it would be useless as ground truth."""
        _, s, _ = summarise(NICK_FIT, Path(tempfile.mkdtemp(prefix="fitread-lap2-")))
        for l in s["laps"]:
            self.assertTrue(l["end_utc"], l)


@unittest.skipUnless(HAVE_NICK, NICK_REASON)
class TestNickNoDeveloperFields(unittest.TestCase):
    """data/nick-sessions/fits/23904301445_ACTIVITY.fit — the rider's watch.

    Profile `Wing Foil`, recorded as sport `generic` / sub_sport `track_me`,
    and carrying NO developer fields. Every jump number must read `absent`."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(__import__("tempfile").mkdtemp(prefix="fitread-nick-"))
        cls.screen, cls.s, cls.rows = summarise(NICK_FIT, cls.tmp)

    def test_identity(self):
        self.assertEqual(self.s["profile_name"], "Wing Foil")
        self.assertEqual(self.s["sport"], "generic")
        self.assertEqual(self.s["sub_sport"], "track_me")

    def test_clock(self):
        self.assertEqual(self.s["start_utc"], "2026-08-08T18:11:01+00:00")
        self.assertEqual(self.s["end_utc"], "2026-08-08T19:09:22+00:00")
        self.assertEqual(self.s["utc_offset"], "-04:00")
        self.assertEqual(self.s["start_local"], "2026-08-08 14:11:01 -04:00")
        self.assertEqual(self.s["duration_s"], 3501.0)

    def test_record_count(self):
        self.assertEqual(self.s["record_count"], 1807)
        self.assertEqual(len(self.rows), 1807)

    def test_developer_fields_absent(self):
        self.assertFalse(self.s["developer_fields_present"])
        self.assertEqual(self.s["developer_fields"], [])
        self.assertIsNone(self.s["session_jumps"])
        self.assertIsNone(self.s["session_best_jump"])
        self.assertIsNone(self.s["session_best_airtime"])

    def test_screen_says_absent_never_zero(self):
        """CLAUDE.md §2.3. `jumps: 0` would claim the data field ran and found
        nothing; it never ran on this watch."""
        for want in ("developer fields      absent — no field_description in this file",
                     "session jumps         absent",
                     "session best_jump     absent",
                     "session best_airtime  absent"):
            self.assertIn(want, self.screen)
        for never in ("session jumps         0",
                      "session best_jump     0",
                      "session best_airtime  0"):
            self.assertNotIn(never, self.screen)

    def test_csv_omits_jump_height_column(self):
        self.assertEqual(self.s["csv_columns"],
                         ["timestamp_utc", "enhanced_speed",
                          "position_lat_deg", "position_long_deg"])
        self.assertNotIn("jump_height", self.rows[0])

    def test_csv_blank_position_before_gps_lock(self):
        """The first record has no fix; blank, not 0.0 degrees off Africa."""
        self.assertEqual(self.rows[0]["position_lat_deg"], "")
        self.assertEqual(self.rows[0]["position_long_deg"], "")

    def test_device_identity(self):
        """The rider's watch, named in the file — Instinct 3 Solar 45mm."""
        self.assertEqual(self.s["device"]["manufacturer"], "garmin")
        self.assertEqual(self.s["device"]["product"], "instinct3_solar_45mm")

    def test_max_speed_and_foil(self):
        self.assertAlmostEqual(self.s["max_enhanced_speed_ms"], 8.566, places=3)
        self.assertAlmostEqual(self.s["session_enhanced_max_speed_ms"], 8.566, places=3)
        self.assertEqual(self.s["foil"]["window_count"], 39)
        self.assertEqual(self.s["foil"]["total_seconds"], 2281.0)
        self.assertEqual(self.s["foil"]["windows"][0], {
            "start_utc": "2026-08-08T18:13:09+00:00",
            "end_utc": "2026-08-08T18:13:36+00:00",
            "seconds": 27.0,
        })


@unittest.skipUnless(HAVE_NICK, NICK_REASON)
class TestGarminConnectZip(unittest.TestCase):
    """data/nick-sessions/raw/24004323561.zip — the shape the rider's export
    actually arrives in. It must read identically to the .fit inside it."""

    @classmethod
    def setUpClass(cls):
        tmp = Path(__import__("tempfile").mkdtemp(prefix="fitread-zip-"))
        cls.screen, cls.zs, cls.zrows = summarise(NICK_BIG_ZIP, tmp / "zip")
        _, cls.fs, cls.frows = summarise(NICK_BIG_FIT, tmp / "fit")

    def test_zip_names_the_member_it_read(self):
        self.assertEqual(self.zs["source"], "24004323561.zip (24004323561_ACTIVITY.fit)")
        self.assertEqual(self.fs["source"], "24004323561_ACTIVITY.fit")

    def test_zip_and_fit_agree_on_everything_else(self):
        a, b = dict(self.zs), dict(self.fs)
        a.pop("source"), b.pop("source")
        self.assertEqual(a, b)
        self.assertEqual(self.zrows, self.frows)

    def test_measured_values(self):
        self.assertEqual(self.zs["profile_name"], "Wing Foil")
        self.assertEqual(self.zs["start_utc"], "2026-08-16T20:18:07+00:00")
        self.assertEqual(self.zs["end_utc"], "2026-08-16T22:14:56+00:00")
        self.assertEqual(self.zs["duration_s"], 7009.0)
        self.assertEqual(self.zs["record_count"], 3225)
        self.assertFalse(self.zs["developer_fields_present"])
        self.assertAlmostEqual(self.zs["max_enhanced_speed_ms"], 8.855, places=3)
        self.assertEqual(self.zs["foil"]["window_count"], 58)
        self.assertEqual(self.zs["foil"]["total_seconds"], 5116.0)
        self.assertAlmostEqual(self.zs["foil"]["percent_of_duration"], 72.9919, places=4)


class TestFileWithNoRecords(unittest.TestCase):
    """A FIT that carries no `record` messages (a settings or monitoring file)
    must not report "0 windows, 0 s of foiling" — that reads as a measured
    absence of foiling rather than an absence of data. Exercised through the
    module rather than a file, because no such FIT is on hand to run against."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO / "tools"))
        import fitread
        cls.fitread = fitread
        cls.s = fitread.summarise(dict(
            source="settings.fit", session=None, activity=None, sport_msg=None,
            file_id=None, dev_decls={}, dev_counts={}, records=[], stamped=[],
            warnings=[]))

    def test_foil_is_absent_not_zero(self):
        foil = self.s["foil"]
        self.assertIsNone(foil["window_count"])
        self.assertIsNone(foil["total_seconds"])
        self.assertIsNone(foil["percent_of_duration"])
        self.assertIsNone(foil["windows"])

    def test_record_count_is_a_measured_zero(self):
        """`records 0` IS honest — the messages were counted and there were
        none. It is the derived numbers that must not be invented."""
        self.assertEqual(self.s["record_count"], 0)

    def test_screen_never_prints_a_fabricated_zero(self):
        screen = self.fitread.render(self.s)
        self.assertIn("records               0", screen)
        for line in ("start                 absent",
                     "end                   absent",
                     "duration              absent",
                     "max enhanced_speed    absent",
                     "windows             absent",
                     "total               absent"):
            self.assertIn(line, screen)
        self.assertNotIn("0 s  (", screen)


class TestIntegrityGuards(unittest.TestCase):
    """Guards for file shapes none of the nine files on hand has. A guard that
    has never run is not a guard, so they are exercised directly."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO / "tools"))
        import fitread
        cls.fitread = fitread

    @staticmethod
    def _rec(second, speed=1.0):
        import datetime
        return {"timestamp": datetime.datetime(2026, 9, 6, 17, 0, second,
                                               tzinfo=datetime.timezone.utc),
                "enhanced_speed": speed, "jump_height": None,
                "position_lat": None, "position_long": None}

    def test_clean_file_warns_about_nothing(self):
        recs = [self._rec(0), self._rec(1)]
        self.assertEqual(self.fitread.integrity_warnings(1, recs, recs), [])

    def test_multi_sport_file_says_which_session_it_reported(self):
        recs = [self._rec(0)]
        warns = self.fitread.integrity_warnings(3, recs, recs)
        self.assertEqual(len(warns), 1)
        self.assertIn("3 session messages", warns[0])
        self.assertIn("FIRST session only", warns[0])

    def test_unstamped_records_are_declared_excluded(self):
        recs = [self._rec(0), dict(self._rec(1), timestamp=None)]
        warns = self.fitread.integrity_warnings(1, recs, recs[:1])
        self.assertEqual(len(warns), 1)
        self.assertIn("1 record message(s) carried no timestamp", warns[0])

    def test_backwards_timestamps_are_declared(self):
        recs = [self._rec(5), self._rec(1), self._rec(9)]
        warns = self.fitread.integrity_warnings(1, recs, recs)
        self.assertEqual(len(warns), 1)
        self.assertIn("1 record timestamp(s) go backwards", warns[0])

    def test_records_without_enhanced_speed_do_not_report_zero_foiling(self):
        """An older watch writes `speed`, not `enhanced_speed`. Reporting
        "0 windows" there would claim the rider never got on foil."""
        recs = [dict(self._rec(i), enhanced_speed=None) for i in range(30)]
        s = self.fitread.summarise(dict(
            source="old.fit", session=None, activity=None, sport_msg=None,
            file_id=None, dev_decls={}, dev_counts={}, records=recs,
            stamped=recs, warnings=[]))
        self.assertIsNone(s["max_enhanced_speed_ms"])
        self.assertIsNone(s["foil"]["window_count"])
        self.assertIsNone(s["foil"]["total_seconds"])
        self.assertEqual(s["warnings"], [
            "no record carries enhanced_speed — time on foil could not be computed"])
        self.assertIn("windows             absent", self.fitread.render(s))

    def test_a_window_cannot_bridge_a_pause(self):
        """MAX_GAP_S. Two fast samples either side of a 40 min pause are not a
        40 min flight — data/nick-sessions/fits/23892159354 pauses for 3,989 s."""
        import datetime
        fast = [self._rec(0, 6.0), self._rec(20, 6.0)]
        later = dict(self._rec(0, 6.0))
        later["timestamp"] += datetime.timedelta(minutes=40)
        far = [self._rec(0, 6.0), later]
        self.assertEqual(len(self.fitread.foil_windows(fast)), 1)
        self.assertEqual(self.fitread.foil_windows(fast)[0]["seconds"], 20.0)
        self.assertEqual(self.fitread.foil_windows(far), [])


class TestRejections(unittest.TestCase):
    """Everything that cannot be read exits 2 with the reason on stderr — not
    0 with an empty screen, which would look like a session with no data."""

    def _reject(self, path):
        proc = run([path], expect=2)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.strip(), "exit 2 must say why")
        return proc.stderr

    def test_missing_file(self):
        err = self._reject(REPO / "data" / "fit" / "no-such-file.fit")
        self.assertIn("does not exist", err)

    def test_wrong_extension(self):
        err = self._reject(REPO / "CLAUDE.md")
        self.assertIn("expected a .fit or a Garmin Connect .zip", err)
        self.assertIn(".md", err)

    def test_zip_with_no_activity(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="fitread-bad-"))
        z = tmp / "empty.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("readme.txt", "no activity here")
        err = self._reject(z)
        self.assertIn("expected exactly one *_ACTIVITY.fit in the zip, found 0 (none)", err)

    def test_zip_with_two_activities(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="fitread-bad-"))
        z = tmp / "two.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("111_ACTIVITY.fit", "x")
            zf.writestr("222_ACTIVITY.fit", "y")
        err = self._reject(z)
        self.assertIn("found 2 (111_ACTIVITY.fit, 222_ACTIVITY.fit)", err)

    def test_not_a_zip(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="fitread-bad-"))
        z = tmp / "bad.zip"
        z.write_bytes(b"not a zip at all")
        err = self._reject(z)
        self.assertIn("not a readable zip", err)

    def test_truncated_fit(self):
        """A half-downloaded FIT must not summarise the half it got."""
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="fitread-bad-"))
        f = tmp / "trunc.fit"
        f.write_bytes(EPIX.read_bytes()[:4000])
        err = self._reject(f)
        self.assertIn("FIT decode failed", err)


if __name__ == "__main__":
    unittest.main()
