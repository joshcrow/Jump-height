"""Tests for sim/candidates_report.py — candidates.md / candidates.csv for one
session (spec sections 3.4, 6.7, 7.3). Synthetic session directories only.
"""

from __future__ import annotations

import csv
import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "sim"))

import candgen  # noqa: E402
import candidates_report as R  # noqa: E402

EPOCH = "2026-09-14T18:00:00Z"          # trace t=0 -> 14:00:00 local (UTC-4)


def _trace(hz, jumps_at, total_s=600.0, t0=10.0):
    """1.0 g riding with a pop/unload/impact at each time in `jumps_at`."""
    dt = 1.0 / hz
    n = int(total_s * hz)
    t = [round(t0 + k * dt, 3) for k in range(n)]
    m = [1.0] * n
    for a in jumps_at:
        for k in range(n):
            x = t[k] - a
            if 0.0 <= x < 0.1:
                m[k] = 3.0
            elif 0.1 <= x < 0.9:
                m[k] = 0.6
            elif 0.9 <= x < 1.0:
                m[k] = 6.0
    return t, m


def make_session(tmp_path, hz=50.0, jumps_at=(100.0, 200.0, 300.0), surfr=None,
                 mount=None, log_hz=None, name="20260914-180000-TEST"):
    d = tmp_path / name
    d.mkdir()
    t, m = _trace(hz, jumps_at)
    (d / "trace.csv").write_text("t,mag\n" + "".join(f"{a},{b}\n" for a, b in zip(t, m)))
    (d / "jumps.csv").write_text("n,takeoff_s,airtime_raw_s,airtime_s,height_m\n")
    (d / "session.json").write_text(json.dumps({
        "trace_epoch_utc": EPOCH,
        "manifest": {"tz_offset_min": -240, "log_hz": log_hz or hz, "trace_format": "csv"}}))
    if surfr is not None:
        (d / "surfr.json").write_text(json.dumps(surfr))
    if mount is not None:
        (d / "mount.json").write_text(json.dumps({"mount_id": mount, "source": "owner"}))
    return d


SURFR = {"session_start_local": "2026-09-14T14:01", "duration_s": 200, "jumps_total": 3,
         "rows": [{"n": 1, "height_ft": 4.0, "airtime_s": 2.0, "t_into_session": "1:00"}]}


def test_report_writes_md_and_csv_with_identity_columns(tmp_path):
    d = make_session(tmp_path, surfr=SURFR)
    rc, _ = R.run_session(d)
    md = (d / "candidates.md").read_text()
    pid_r = candgen.params_id(candgen.PRESETS["vest-R"])
    pid_l = candgen.params_id(candgen.PRESETS["vest-L"])
    assert f"gen cg-1, presets vest-R {pid_r} / vest-L {pid_l}" in md
    with open(d / "candidates.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == R.CSV_COLUMNS
    r_rows = [r for r in rows if r["preset"] == "vest-R"]
    assert len(r_rows) == 3
    assert {r["gen_version"] for r in rows} == {"cg-1"}
    assert [r["cand_id"] for r in r_rows] == ["1", "2", "3"]
    # the Surfr window is t=60..260: the first two jumps are in it, the
    # third is 'margin' (within 10 min) — never counted as outside
    assert [r["region"] for r in r_rows] == ["session", "session", "margin"]
    assert r_rows[0]["pop_local"] == "2026-09-14 14:01:40"
    assert r_rows[0]["mount_id"] == "UNKNOWN"
    assert rc == 0


def test_absent_inputs_are_named_never_zero(tmp_path):
    d = make_session(tmp_path, surfr=SURFR)
    R.run_session(d)
    md = (d / "candidates.md").read_text()
    assert "garmin.fit: ABSENT" in md
    assert "wind.json: ABSENT (no wind.json)" in md
    assert "## 4. Riding context" in md and "Did not run" in md
    with open(d / "candidates.csv", newline="") as f:
        r = next(csv.DictReader(f))
    assert r["kn_before"] == "" and r["rel_wind_deg"] == ""
    assert "kn_before: garmin.fit absent" in md
    for col in ("gyro_peak_dps", "rise_m"):
        assert r[col] == ""


def test_the_fixed_cannot_conclude_bullets_are_always_there(tmp_path):
    d = make_session(tmp_path)
    R.run_session(d)
    md = (d / "candidates.md").read_text()
    for b in R.CANNOT_CONCLUDE:
        assert b in md
    assert "The mount is UNKNOWN" in md
    assert "no height is computed" in md.lower()


def test_a_bad_surfr_row_is_a_finding_and_a_nonzero_exit(tmp_path):
    bad = dict(SURFR, rows=[{"n": 1, "t_into_session": "soon"}])
    d = make_session(tmp_path, surfr=bad)
    rc, text = R.run_session(d)
    assert rc == 1
    md = (d / "candidates.md").read_text()
    assert "FINDING" in md and "n=1" in md


def test_an_unreadable_surfr_json_is_a_finding(tmp_path):
    d = make_session(tmp_path)
    (d / "surfr.json").write_text("{not json")
    rc, _ = R.run_session(d)
    assert rc == 1
    assert "surfr.json is unreadable" in (d / "candidates.md").read_text()


def test_mount_registry_lookup(tmp_path):
    d = make_session(tmp_path, mount="board-nose-hammond-v1", name="a")
    R.run_session(d)
    assert "mount: board-nose-hammond-v1 (source: owner)" in (d / "candidates.md").read_text()
    d = make_session(tmp_path, mount="kite-strap", name="b")
    R.run_session(d)
    assert "UNKNOWN (id 'kite-strap' not in config/mounts.json)" in \
        (d / "candidates.md").read_text()


def test_mounts_registry_lists_the_known_mounts():
    reg = json.loads((REPO / "config" / "mounts.json").read_text())
    assert {"vest-pocket", "board-nose-hammond-v1"} <= set(reg)
    b = reg["board-nose-hammond-v1"]
    assert b["case"] == "Hammond 1551WHGY" and b["board"] == "2025 F-One Rocket Wing-S V4"


def test_a_100_hz_session_also_reports_its_2_to_1_decimation(tmp_path):
    d = make_session(tmp_path, hz=100.0, surfr=SURFR)
    R.run_session(d)
    md = (d / "candidates.md").read_text()
    assert "native / 2:1-decimated" in md
    assert "| 0.7 | 2 | " in md and " / " in md.split("| 0.7 | 2 | ")[1].split("\n")[0]


def test_no_window_counts_the_whole_trace_and_says_so(tmp_path):
    d = make_session(tmp_path)
    R.run_session(d)
    md = (d / "candidates.md").read_text()
    assert "no session window" in md
    assert "over the whole trace (no window)" in md
    assert "Surfr" in md and "did not run: no surfr.json" in md.lower()


def test_no_write_writes_nothing(tmp_path, capsys):
    d = make_session(tmp_path)
    assert R.main([str(d), "--no-write"]) == 0
    assert not (d / "candidates.md").exists() and not (d / "candidates.csv").exists()
    assert "# candidates.md" in capsys.readouterr().out


def test_missing_trace_is_a_nonzero_exit(tmp_path, capsys):
    d = tmp_path / "empty"
    d.mkdir()
    assert R.main([str(d)]) == 1
    assert "a finding, not a pass" in capsys.readouterr().out


def test_three_hour_100_hz_session_report_runs_inside_a_minute(tmp_path):
    # Spec section 6.6: target < 60 s per session at 100 Hz, enforced here.
    d = tmp_path / "long"
    d.mkdir()
    hz, n = 100.0, 1_080_000
    with open(d / "trace.csv", "w") as f:
        f.write("t,mag\n")
        for k in range(n):
            x = (k % 3000) / hz            # a jump every 30 s
            v = 3.0 if 26.0 <= x < 26.1 else 0.6 if 26.1 <= x < 26.9 else \
                6.0 if 26.9 <= x < 27.0 else 1.0
            f.write(f"{k / hz:.3f},{v}\n")
    (d / "session.json").write_text(json.dumps({
        "trace_epoch_utc": EPOCH, "manifest": {"log_hz": 100, "tz_offset_min": -240}}))
    t0 = time.time()
    rc, _ = R.run_session(d)
    took = time.time() - t0
    assert rc == 0
    with open(d / "candidates.csv", newline="") as f:
        assert sum(1 for r in csv.DictReader(f) if r["preset"] == "vest-R") == 360
    assert took < 60.0, f"{took:.1f} s"


def test_jump_cli_registers_candidates(tmp_path):
    import subprocess
    d = make_session(tmp_path)
    proc = subprocess.run([sys.executable, str(REPO / "tools" / "jump"), "candidates",
                           str(d)], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert (d / "candidates.md").is_file() and (d / "candidates.csv").is_file()


# ------------------------------------------- review S2, S3, S6

@pytest.mark.parametrize("field,value", [
    ("duration_s", "54 min"), ("tz_offset_min", "EDT"),
    ("session_start_local", "16:15"), ("jumps_total", "14 jumps")])
def test_s2_a_mistyped_surfr_header_is_a_finding_in_both_reports(tmp_path, field, value):
    import score
    d = make_session(tmp_path, surfr=dict(SURFR, **{field: value}))
    rc, _ = R.run_session(d)
    md = (d / "candidates.md").read_text()
    assert rc == 1
    assert "FINDING: surfr.json is malformed" in md and field in md
    assert "DID NOT RUN" in md
    # sim/score.py reads the same file: a FINDING, never a traceback
    assert score.main([str(d), "--no-write"]) == 0
    card = score.render_scorecard(score.score_session(d))
    assert "FINDING: surfr.json is malformed" in card and field in card


def test_s2_a_list_n_is_a_finding_not_a_crash(tmp_path):
    d = make_session(tmp_path, surfr=dict(SURFR, rows=[
        {"n": [1], "t_into_session": "1:00"}, {"n": 2, "t_into_session": "1:30"}]))
    rc, _ = R.run_session(d)
    assert rc == 1
    assert "has n=[1], not a number" in (d / "candidates.md").read_text()


def test_s3_the_utc_offset_and_its_source_are_printed(tmp_path):
    d = make_session(tmp_path, surfr=SURFR)
    R.run_session(d)
    md = (d / "candidates.md").read_text()
    line = ("UTC offset for local times and Surfr placement: -240 min "
            "(session.json manifest.tz_offset_min, the offset in force at SYNC)")
    sec0 = md.split("## 1.")[0]
    sec5 = md.split("## 5. Surfr")[1].split("## 6.")[0]
    assert line in sec0 and line in sec5


def test_s3_an_assumed_offset_placing_surfr_rows_is_a_finding(tmp_path):
    d = make_session(tmp_path, surfr=SURFR)
    sj = json.loads((d / "session.json").read_text())
    del sj["manifest"]["tz_offset_min"]
    (d / "session.json").write_text(json.dumps(sj))
    rc, _ = R.run_session(d)
    md = (d / "candidates.md").read_text()
    assert rc == 1
    assert "FINDING: the UTC offset that places the Surfr rows is ASSUMED (-240 min)" in md
    # with no Surfr start to place, the assumption is printed but not a finding
    d2 = make_session(tmp_path, name="nosurfr")
    (d2 / "session.json").write_text(json.dumps(sj))
    rc2, _ = R.run_session(d2)
    md2 = (d2 / "candidates.md").read_text()
    assert "ASSUMED -240 min" in md2 and "FINDING" not in md2 and rc2 == 0


def test_s3_a_dst_change_between_ride_and_sync_is_a_finding(tmp_path):
    d = make_session(tmp_path, surfr=SURFR)
    sj = json.loads((d / "session.json").read_text())
    sj["synced_at_utc"] = "2026-11-03T15:00:00Z"     # after the Nov 1 change
    (d / "session.json").write_text(json.dumps(sj))
    rc, _ = R.run_session(d)
    md = (d / "candidates.md").read_text()
    assert rc == 1
    assert "FINDING: a US daylight-saving change (2026-11-01)" in md
    sj["synced_at_utc"] = "2026-09-14T22:00:00Z"     # same day: no change between
    (d / "session.json").write_text(json.dumps(sj))
    R.run_session(d)
    assert "daylight-saving" not in (d / "candidates.md").read_text()


def test_s3_surfr_and_garmin_windows_an_hour_apart_are_a_finding():
    import candctx
    import datetime as dt
    base = dict(epoch_utc=dt.datetime(2026, 9, 14, 18, tzinfo=dt.timezone.utc),
                tz_offset_min=-240, tz_src="surfr.json tz_offset_min",
                garmin_reason="", garmin_window=(1000.0, 4000.0))
    ok = candctx.Context(**base, surfr_window=(813.0, 4021.0))   # Sep-14's -187 / +21
    candctx._tz_checks(ok, {}, {}, None)
    assert not any(ln.startswith("FINDING") for ln in ok.tz_lines)
    assert "Surfr starts -187 s and ends +21 s" in "\n".join(ok.tz_lines)
    off = candctx.Context(**base, surfr_window=(4600.0, 7600.0))  # +3600 s
    candctx._tz_checks(off, {}, {}, None)
    assert any(ln.startswith("FINDING: the Surfr window disagrees") for ln in off.tz_lines)


def test_s6_no_window_and_two_boots_count_the_same_everywhere(tmp_path):
    d = make_session(tmp_path, jumps_at=(100.0,))
    t, m = _trace(50.0, (100.0,))
    rows = list(zip(t, m)) + list(zip(t, m))          # boot 2 restarts t at 10 s
    (d / "trace.csv").write_text("t,mag\n" + "".join(f"{a},{b}\n" for a, b in rows))
    rep = R.analyse(d)
    assert rep.n_boots == 2 and rep.window is None
    md = R.render_md(rep)
    assert "vest-R: **2** over the whole trace (no window, all 2 boots summed)" in md
    grid_r = [n for p, n, _ in rep.grid if p == candgen.PRESETS["vest-R"]]
    assert grid_r == [2]
    assert "| 2 (R) |" in md or " 2 (R) " in md
    with open(tmp_path / "x.csv", "w"):
        pass
    R.write_csv(rep, tmp_path / "x.csv")
    with open(tmp_path / "x.csv", newline="") as f:
        r_rows = [r for r in csv.DictReader(f) if r["preset"] == "vest-R"]
    assert sorted(r["boot"] for r in r_rows) == ["earlier", "last"]
    assert {r["region"] for r in r_rows} == {"no_window"}


def test_s2_a_surfr_json_without_a_start_says_which_input_is_missing(tmp_path):
    d = make_session(tmp_path, surfr={"jumps_total": 12, "rows": []})
    R.run_session(d)
    assert ("Did not run: the Surfr start cannot be placed on the trace clock: "
            "surfr.json has no session_start_local.") in (d / "candidates.md").read_text()
