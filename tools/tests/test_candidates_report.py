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
