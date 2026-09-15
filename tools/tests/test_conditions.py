"""Tests for tools/conditions.py — session place/time derivation and the
external wind/wave pull into <session>/wind.json.

Two different fixture philosophies, on purpose:

  * Garmin reading reuses `data/fit/2026-09-06-13-33-59.fit`, the real,
    git-tracked Epix file `test_fitread.py` also uses ("real FIT files, no
    synthesised ones" — a hand-built fixture would not show Garmin's own
    quirks). This module does not parse FIT itself; it calls
    `tools/fitread.py`, already tested there, so here it is only checked
    that conditions.py turns fitread's scan into the right centroid/window.

  * Every external source (Open-Meteo weather/marine, NDBC) goes through the
    `fetch(url) -> bytes` seam conditions.py defines for exactly this reason,
    so those are exercised with small, hand-written fixture JSON/text below
    — no network, no monkeypatched urlopen.

Run via `python3 -m pytest tools/tests/test_conditions.py -q -p no:warnings`.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools"))

import conditions as c  # noqa: E402

UTC = dt.timezone.utc
EPIX_FIT = REPO / "data" / "fit" / "2026-09-06-13-33-59.fit"


# --------------------------------------------------------------------------
# fixtures: fake network responses (no network, no monkeypatched urlopen)


WEATHER_FIXTURE = json.dumps({
    "latitude": 35.9, "longitude": -75.66,
    "hourly_units": {"wind_speed_10m": "kn", "wind_gusts_10m": "kn",
                      "temperature_2m": "°C", "surface_pressure": "hPa"},
    "hourly": {
        "time": ["2026-09-14T19:00", "2026-09-14T20:00",
                 "2026-09-14T21:00", "2026-09-14T22:00"],
        "wind_speed_10m": [11.0, 13.0, 11.8, 11.9],
        "wind_gusts_10m": [27.6, 28.0, 27.6, 25.1],
        "wind_direction_10m": [16, 21, 30, 33],
        "temperature_2m": [28.05, 27.1, 26.3, 25.8],
        "surface_pressure": [1017.0, 1016.9, 1017.5, 1017.8],
    },
}).encode()

MARINE_FIXTURE_NONNULL = json.dumps({
    "latitude": 35.875, "longitude": -75.625,
    "hourly_units": {"wave_height": "m", "wave_period": "s", "wave_direction": "°"},
    "hourly": {
        "time": ["2026-09-14T19:00", "2026-09-14T20:00",
                 "2026-09-14T21:00", "2026-09-14T22:00"],
        "wave_height": [0.54, 0.6, 0.68, 0.72],
        "wave_period": [3.4, 3.55, 3.75, 3.85],
        "wave_direction": [38, 35, 31, 34],
    },
}).encode()

MARINE_FIXTURE_ALL_NULL = json.dumps({
    "latitude": 35.9, "longitude": -75.65,
    "hourly_units": {"wave_height": "m"},
    "hourly": {
        "time": ["2026-09-14T19:00", "2026-09-14T20:00"],
        "wave_height": [None, None],
        "wave_period": [None, None],
        "wave_direction": [None, None],
    },
}).encode()

NDBC_FIXTURE = (
    "#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE\n"
    "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft\n"
    "2026 09 14 22 54  50  7.2 10.3    MM    MM    MM  MM 1019.0  24.6  26.1    MM   MM   MM    MM\n"
    "2026 09 14 21 54  40  6.7  8.8    MM    MM    MM  MM 1018.3  25.1  26.1    MM   MM   MM    MM\n"
    "2026 09 14 20 18  30  8.8 10.8    MM    MM    MM  MM 1017.4    MM  26.1    MM   MM   MM    MM\n"
    "2026 09 14 19 00  30  7.2  9.8    MM    MM    MM  MM 1017.1  27.3  26.2    MM   MM +0.0    MM\n"
).encode()


def make_fake_fetch(weather=WEATHER_FIXTURE, marine=MARINE_FIXTURE_NONNULL,
                     ndbc=NDBC_FIXTURE, log=None):
    """A `fetch(url) -> bytes` that dispatches on host, like the real one
    would but entirely offline."""
    def fetch(url: str) -> bytes:
        if log is not None:
            log.append(url)
        if "archive-api.open-meteo.com" in url:
            return weather
        if "marine-api.open-meteo.com" in url:
            return marine
        if "ndbc.noaa.gov" in url:
            return ndbc
        raise AssertionError(f"unexpected URL: {url}")
    return fetch


def _raising_fetch(url: str) -> bytes:
    raise TimeoutError("simulated network failure")


# --------------------------------------------------------------------------
# small pure helpers


def test_haversine_known_pair():
    # Oregon Inlet Marina from the 2026-09-14 evening session centroid,
    # measured 2026-09-14 against NOAA's own station_table.txt.
    d = c.haversine_km(35.9112, -75.6597, 35.796, -75.548)
    assert 15.0 < d < 18.0


def test_haversine_zero_distance():
    assert c.haversine_km(35.9, -75.6, 35.9, -75.6) == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize("deg,expect", [
    (0, "N"), (22.5, "NNE"), (35, "NE"), (90, "E"), (180, "S"),
    (359, "N"), (360, "N"),
])
def test_compass(deg, expect):
    assert c.compass(deg) == expect


def test_compass_none_passthrough():
    assert c.compass(None) is None


def test_circular_mean_wraps_across_north():
    # 350 and 10 degrees average to 0 (not 180, the arithmetic mean's answer).
    m = c.circular_mean_deg([350, 10])
    assert m is not None
    assert m < 1.0 or m > 359.0


def test_circular_mean_empty_is_none():
    assert c.circular_mean_deg([None, None]) is None
    assert c.circular_mean_deg([]) is None


def test_circular_mean_opposing_directions_is_none():
    # N and S cancel exactly; there is no meaningful "prevailing" direction.
    assert c.circular_mean_deg([0, 180]) is None


def test_unit_conversions():
    assert c.c_to_f(0) == 32.0
    assert c.c_to_f(100) == 212.0
    assert c.c_to_f(None) is None
    assert c.ms_to_kn(1.0) == pytest.approx(1.9438444924406)
    assert c.ms_to_kn(None) is None
    assert c.m_to_ft(1.0) == pytest.approx(3.280839895)
    assert c.m_to_ft(None) is None


def test_stats_ignores_none_and_empty_is_none():
    assert c._stats([1.0, None, 3.0]) == {"mean": 2.0, "min": 1.0, "max": 3.0, "n": 2}
    assert c._stats([None, None]) is None
    assert c._stats([]) is None


def test_mm_missing_marker_is_none_not_zero():
    # NDBC's own missing-value marker must never become a silent 0.0
    # (CLAUDE.md: a reading that did not happen is a finding).
    assert c._mm("MM") is None
    assert c._mm("") is None
    assert c._mm("garbage") is None
    assert c._mm("7.2") == 7.2


# --------------------------------------------------------------------------
# NDBC station table


def test_nearest_station_is_oregon_inlet_for_manteo():
    st = c.nearest_ndbc_station(35.9112, -75.6597)
    assert st["id"] == "ORIN7"
    assert 15.0 < st["distance_km"] < 18.0


def test_nearest_station_far_away_exceeds_cutoff():
    # New York City: no Outer Banks station is anywhere close.
    st = c.nearest_ndbc_station(40.7128, -74.0060)
    assert st["distance_km"] > c.NDBC_MAX_KM


def test_fetch_ndbc_over_cutoff_never_calls_fetch():
    def boom(url):
        raise AssertionError("fetch must not be called past the distance cutoff")
    result = c.fetch_ndbc(40.7128, -74.0060,
                           dt.datetime(2026, 9, 14, 20, tzinfo=UTC),
                           dt.datetime(2026, 9, 14, 21, tzinfo=UTC),
                           fetch=boom)
    assert result["queried"] is False
    assert "km away" in result["reason"]
    assert result["nearest"]["id"] in c.NDBC_STATIONS


def test_parse_ndbc_realtime2_filters_window_and_missing_values():
    lo = dt.datetime(2026, 9, 14, 20, 18, tzinfo=UTC)
    hi = dt.datetime(2026, 9, 14, 21, 57, tzinfo=UTC)
    rows = c.parse_ndbc_realtime2(NDBC_FIXTURE.decode(), lo, hi)
    # 19:00 and 22:54 rows are outside [lo, hi]; 20:18 and 21:54 are inside.
    assert [r["time_utc"] for r in rows] == [
        "2026-09-14T20:18:00+00:00", "2026-09-14T21:54:00+00:00"]
    assert rows[0]["wspd_ms"] == 8.8
    assert rows[0]["gst_ms"] == 10.8
    assert rows[0]["atmp_c"] is None  # "MM" in the fixture
    assert rows[1]["atmp_c"] == 25.1


def test_fetch_ndbc_ok_path():
    fetch = make_fake_fetch()
    result = c.fetch_ndbc(35.9112, -75.6597,
                           dt.datetime(2026, 9, 14, 20, 18, tzinfo=UTC),
                           dt.datetime(2026, 9, 14, 21, 57, tzinfo=UTC),
                           fetch=fetch)
    assert result["ok"] is True
    assert result["station"]["id"] == "ORIN7"
    assert len(result["hours"]) == 2


def test_fetch_ndbc_network_failure_is_a_field_not_an_exception():
    result = c.fetch_ndbc(35.9112, -75.6597,
                           dt.datetime(2026, 9, 14, 20, tzinfo=UTC),
                           dt.datetime(2026, 9, 14, 21, tzinfo=UTC),
                           fetch=_raising_fetch)
    assert result["ok"] is False
    assert "TimeoutError" in result["error"]


# --------------------------------------------------------------------------
# open-meteo sources


def test_fetch_open_meteo_weather_filters_to_window_and_converts_grid():
    fetch = make_fake_fetch()
    lo = dt.datetime(2026, 9, 14, 19, tzinfo=UTC)
    hi = dt.datetime(2026, 9, 14, 22, tzinfo=UTC)
    result = c.fetch_open_meteo_weather(35.9112, -75.6597, lo, hi, fetch=fetch)
    assert result["ok"] is True
    assert len(result["hours"]) == 4
    assert result["hours"][0]["wind_speed_10m"] == 11.0
    assert result["grid_latitude"] == 35.9


def test_fetch_open_meteo_weather_partial_window_excludes_hours():
    fetch = make_fake_fetch()
    lo = dt.datetime(2026, 9, 14, 20, tzinfo=UTC)
    hi = dt.datetime(2026, 9, 14, 21, tzinfo=UTC)
    result = c.fetch_open_meteo_weather(35.9112, -75.6597, lo, hi, fetch=fetch)
    assert [r["time_utc"] for r in result["hours"]] == [
        "2026-09-14T20:00:00+00:00", "2026-09-14T21:00:00+00:00"]


def test_fetch_open_meteo_weather_bad_json_is_a_field_not_an_exception():
    result = c.fetch_open_meteo_weather(
        35.9, -75.6, dt.datetime(2026, 9, 14, tzinfo=UTC), dt.datetime(2026, 9, 14, 1, tzinfo=UTC),
        fetch=lambda url: b"not json")
    assert result["ok"] is False
    assert "error" in result


def test_fetch_open_meteo_weather_network_failure_is_a_field_not_an_exception():
    result = c.fetch_open_meteo_weather(
        35.9, -75.6, dt.datetime(2026, 9, 14, tzinfo=UTC), dt.datetime(2026, 9, 14, 1, tzinfo=UTC),
        fetch=_raising_fetch)
    assert result["ok"] is False
    assert "TimeoutError" in result["error"]


def test_fetch_open_meteo_marine_all_null_is_reported_honestly():
    """docs/accuracy-plan.md: the Roanoke Sound / Manteo NC marine grid may
    return nulls — that must show up as a note, never a fabricated number."""
    fetch = make_fake_fetch(marine=MARINE_FIXTURE_ALL_NULL)
    lo = dt.datetime(2026, 9, 14, 19, tzinfo=UTC)
    hi = dt.datetime(2026, 9, 14, 20, tzinfo=UTC)
    result = c.fetch_open_meteo_marine(35.9112, -75.6597, lo, hi, fetch=fetch)
    assert result["ok"] is True
    assert all(r["wave_height"] is None for r in result["hours"])
    assert "null" in result["note"]


def test_fetch_open_meteo_marine_distant_grid_point_is_flagged():
    fetch = make_fake_fetch()  # grid point is 35.875,-75.625, several km off
    lo = dt.datetime(2026, 9, 14, 19, tzinfo=UTC)
    hi = dt.datetime(2026, 9, 14, 22, tzinfo=UTC)
    result = c.fetch_open_meteo_marine(35.9112, -75.6597, lo, hi, fetch=fetch)
    assert result["ok"] is True
    assert result["grid_distance_km"] > 3.0
    assert "not venue truth" in result["note"]


def test_build_summary_combines_all_three_sources():
    fetch = make_fake_fetch()
    lo = dt.datetime(2026, 9, 14, 19, tzinfo=UTC)
    hi = dt.datetime(2026, 9, 14, 22, tzinfo=UTC)
    wx = c.fetch_open_meteo_weather(35.9112, -75.6597, lo, hi, fetch=fetch)
    mar = c.fetch_open_meteo_marine(35.9112, -75.6597, lo, hi, fetch=fetch)
    nd = c.fetch_ndbc(35.9112, -75.6597,
                       dt.datetime(2026, 9, 14, 20, 18, tzinfo=UTC),
                       dt.datetime(2026, 9, 14, 21, 57, tzinfo=UTC), fetch=fetch)
    s = c.build_summary(wx, mar, nd)
    assert s["open_meteo_weather"]["wind_speed_kn"]["min"] == 11.0
    assert s["open_meteo_weather"]["wind_direction_compass"] is not None
    assert s["open_meteo_marine"]["wave_height_ft"]["max"] == pytest.approx(0.72 * c.FT_PER_M)
    assert s["ndbc"]["station"]["id"] == "ORIN7"


def test_build_summary_handles_a_failed_source_without_raising():
    fetch = make_fake_fetch()
    lo = dt.datetime(2026, 9, 14, 19, tzinfo=UTC)
    hi = dt.datetime(2026, 9, 14, 22, tzinfo=UTC)
    wx = c.fetch_open_meteo_weather(35.9112, -75.6597, lo, hi, fetch=fetch)
    marine_fail = c.fetch_open_meteo_marine(35.9112, -75.6597, lo, hi, fetch=_raising_fetch)
    nd = c.fetch_ndbc(35.9112, -75.6597,
                       dt.datetime(2026, 9, 14, 20, 18, tzinfo=UTC),
                       dt.datetime(2026, 9, 14, 21, 57, tzinfo=UTC), fetch=fetch)
    s = c.build_summary(wx, marine_fail, nd)
    assert s["open_meteo_marine"] is None
    assert s["open_meteo_weather"] is not None  # the other two still work


# --------------------------------------------------------------------------
# trace.csv time range


def test_trace_time_range_reads_first_and_last(tmp_path):
    p = tmp_path / "trace.csv"
    p.write_text("t,mag\n108.467,1.041\n200.0,1.0\n25147.792,1.024\n")
    assert c.trace_time_range(p) == (108.467, 25147.792)


def test_trace_time_range_missing_file_is_none(tmp_path):
    assert c.trace_time_range(tmp_path / "nope.csv") is None


def test_trace_time_range_header_only_is_none(tmp_path):
    p = tmp_path / "trace.csv"
    p.write_text("t,mag\n")
    assert c.trace_time_range(p) is None


def test_trace_time_range_large_file_tail(tmp_path):
    """The tail reader must find the real last row, not truncate mid-line,
    across many chunk boundaries."""
    p = tmp_path / "trace.csv"
    rows = [f"{100.0 + i * 0.02:.3f},1.0{i % 10}" for i in range(5000)]
    p.write_text("t,mag\n" + "\n".join(rows) + "\n")
    lo, hi = c.trace_time_range(p)
    assert lo == pytest.approx(100.0)
    assert hi == pytest.approx(float(rows[-1].split(",")[0]))


# --------------------------------------------------------------------------
# window derivation


def _write_session(tmp_path, session=None, trace_rows=None, garmin_fit=False):
    d = tmp_path / "sess"
    d.mkdir(exist_ok=True)
    if session is not None:
        (d / "session.json").write_text(json.dumps(session))
    if trace_rows is not None:
        lines = ["t,mag"] + [f"{t},{m}" for t, m in trace_rows]
        (d / "trace.csv").write_text("\n".join(lines) + "\n")
    if garmin_fit:
        assert EPIX_FIT.exists(), f"tracked fixture missing: {EPIX_FIT}"
        shutil.copy(EPIX_FIT, d / "garmin.fit")
    return d


def test_derive_window_from_garmin_fit(tmp_path):
    d = _write_session(tmp_path, garmin_fit=True)
    w = c.derive_window(d)
    assert w.ok
    assert w.start_utc == dt.datetime(2026, 9, 6, 17, 33, 59, tzinfo=UTC)
    assert w.end_utc == dt.datetime(2026, 9, 6, 18, 31, 44, tzinfo=UTC)
    assert w.lat == pytest.approx(35.8973, abs=1e-3)
    assert w.lon == pytest.approx(-75.5883, abs=1e-3)
    assert w.position_count > 0
    assert "garmin.fit" in w.time_source
    assert not w.notes


def test_derive_window_falls_back_to_trace_when_no_garmin(tmp_path):
    d = _write_session(
        tmp_path,
        session={"trace_epoch_utc": "2026-09-14T18:07:29.427Z"},
        trace_rows=[(108.467, 1.041), (200.0, 1.0), (300.5, 0.99)],
    )
    w = c.derive_window(d)
    assert w.ok
    epoch = dt.datetime(2026, 9, 14, 18, 7, 29, 427000, tzinfo=UTC)
    assert w.start_utc == epoch + dt.timedelta(seconds=108.467)
    assert w.end_utc == epoch + dt.timedelta(seconds=300.5)
    assert w.lat is None and w.lon is None
    assert any("no position" in n for n in w.notes)


def test_derive_window_no_garmin_no_session_json_is_an_error(tmp_path):
    d = _write_session(tmp_path, trace_rows=[(10.0, 1.0), (20.0, 1.0)])
    w = c.derive_window(d)
    assert not w.ok
    assert "session.json" in w.error


def test_derive_window_session_json_without_epoch_is_an_error(tmp_path):
    d = _write_session(tmp_path, session={"unit": "JumpHeight-E2C4"},
                        trace_rows=[(10.0, 1.0), (20.0, 1.0)])
    w = c.derive_window(d)
    assert not w.ok
    assert "trace_epoch_utc" in w.error


def test_derive_window_no_trace_csv_is_an_error(tmp_path):
    d = tmp_path / "sess"
    d.mkdir()
    (d / "session.json").write_text(json.dumps({"trace_epoch_utc": "2026-09-14T18:07:29.427Z"}))
    w = c.derive_window(d)
    assert not w.ok
    assert "trace.csv" in w.error


def test_derive_window_garmin_with_no_positions_falls_through_to_no_centroid(tmp_path, monkeypatch):
    """garmin.fit can exist and be fully readable with zero positioned
    records (e.g. an indoor/GPS-off activity); the centroid must come back
    None with a note, not a crash or a fabricated (0, 0)."""
    d = _write_session(tmp_path, garmin_fit=True)

    def fake_load(_sess):
        return ({
            "start_utc": dt.datetime(2026, 9, 6, 17, 33, 59, tzinfo=UTC),
            "end_utc": dt.datetime(2026, 9, 6, 18, 31, 44, tzinfo=UTC),
            "record_count": 10,
            "position_count": 0,
            "lat": None,
            "lon": None,
        }, "garmin.fit")

    monkeypatch.setattr(c, "load_garmin_window", fake_load)
    w = c.derive_window(d)
    assert w.ok
    assert w.lat is None and w.lon is None
    assert any("no centroid" in n for n in w.notes)


# --------------------------------------------------------------------------
# end to end: gather_conditions / wind.json


def test_gather_conditions_full_pipeline_with_garmin(tmp_path):
    d = _write_session(tmp_path, garmin_fit=True)
    doc = c.gather_conditions(d, fetch=make_fake_fetch())
    assert doc["window"]["lat"] is not None
    assert doc["sources"]["open_meteo_weather"]["ok"] is True
    assert doc["sources"]["ndbc"]["ok"] is True
    assert doc["summary"]["open_meteo_weather"] is not None
    # round-trips through JSON cleanly (datetimes must already be strings)
    json.dumps(doc)


def test_gather_conditions_no_position_skips_the_pull(tmp_path):
    d = _write_session(
        tmp_path,
        session={"trace_epoch_utc": "2026-09-14T18:07:29.427Z"},
        trace_rows=[(100.0, 1.0), (200.0, 1.0)],
    )
    calls = []
    doc = c.gather_conditions(d, fetch=make_fake_fetch(log=calls))
    assert not calls, "no source should be fetched without a position"
    assert doc["sources"]["open_meteo_weather"]["queried"] is False
    assert doc["sources"]["open_meteo_marine"]["queried"] is False
    assert doc["sources"]["ndbc"]["queried"] is False
    assert "external pull skipped" in " ".join(doc["notes"])


def test_gather_conditions_no_derivable_window_is_an_error_not_a_crash(tmp_path):
    d = tmp_path / "sess"
    d.mkdir()
    doc = c.gather_conditions(d, fetch=make_fake_fetch())
    assert doc["window"] is None
    assert "error" in doc
    json.dumps(doc)  # still a well-formed document


def test_gather_conditions_one_dead_source_does_not_stop_the_others(tmp_path):
    d = _write_session(tmp_path, garmin_fit=True)

    def flaky(url):
        if "ndbc.noaa.gov" in url:
            raise ConnectionResetError("simulated")
        return make_fake_fetch()(url)

    doc = c.gather_conditions(d, fetch=flaky)
    assert doc["sources"]["ndbc"]["ok"] is False
    assert doc["sources"]["open_meteo_weather"]["ok"] is True
    assert doc["sources"]["open_meteo_marine"]["ok"] is True


def test_write_wind_json_round_trips(tmp_path):
    d = _write_session(tmp_path, garmin_fit=True)
    doc = c.gather_conditions(d, fetch=make_fake_fetch())
    path = c.write_wind_json(d, doc)
    assert path == d / "wind.json"
    reloaded = json.loads(path.read_text())
    assert reloaded["session"] == doc["session"]
    assert reloaded["window"]["lat"] == pytest.approx(doc["window"]["lat"])


def test_one_line_summary_error_case():
    line = c.one_line_summary({"session": "s1", "error": "no session.json"})
    assert line == "s1: no session.json"


def test_one_line_summary_full_case(tmp_path):
    d = _write_session(tmp_path, garmin_fit=True)
    doc = c.gather_conditions(d, fetch=make_fake_fetch())
    line = c.one_line_summary(doc)
    assert doc["session"] in line
    assert "kn" in line


# --------------------------------------------------------------------------
# CLI


def test_main_single_session_writes_wind_json_and_prints_summary(tmp_path, capsys):
    d = _write_session(tmp_path, garmin_fit=True)
    rc = c.main([str(d)], fetch=make_fake_fetch())
    assert rc == 0
    assert (d / "wind.json").exists()
    out = capsys.readouterr().out
    assert d.name in out
    assert "wrote" in out


def test_main_nonexistent_session_dir_is_exit_2(tmp_path, capsys):
    rc = c.main([str(tmp_path / "does-not-exist")], fetch=make_fake_fetch())
    assert rc == 2


def test_main_no_args_errors(monkeypatch):
    with pytest.raises(SystemExit):
        c.main([], fetch=make_fake_fetch())


def test_main_all_mode_skips_existing_unless_forced(tmp_path, monkeypatch):
    sessions_dir = tmp_path / "data" / "sessions"
    sessions_dir.mkdir(parents=True)
    monkeypatch.setattr(c, "REPO", tmp_path)

    # A real session dir always carries trace.csv alongside garmin.fit;
    # _session_dirs() uses trace.csv's presence to recognise a session.
    a = _write_session(sessions_dir, garmin_fit=True,
                        trace_rows=[(100.0, 1.0), (200.0, 1.0)])
    shutil.move(str(a), str(sessions_dir / "20260906-000000-TEST"))
    a = sessions_dir / "20260906-000000-TEST"

    calls = []
    rc = c.main(["--all"], fetch=make_fake_fetch(log=calls))
    assert rc == 0
    assert (a / "wind.json").exists()
    first_calls = len(calls)
    assert first_calls > 0

    # Second run without --force must not re-fetch.
    rc = c.main(["--all"], fetch=make_fake_fetch(log=calls))
    assert rc == 0
    assert len(calls) == first_calls, "existing wind.json must be skipped without --force"

    # --force re-fetches.
    rc = c.main(["--all", "--force"], fetch=make_fake_fetch(log=calls))
    assert rc == 0
    assert len(calls) > first_calls


def test_main_all_mode_handles_a_session_with_no_derivable_window(tmp_path, monkeypatch):
    """A session with trace.csv but no session.json (a real corpus case,
    e.g. data/sessions/20260731-092453) must not stop --all."""
    sessions_dir = tmp_path / "data" / "sessions"
    sessions_dir.mkdir(parents=True)
    monkeypatch.setattr(c, "REPO", tmp_path)

    broken = sessions_dir / "broken-session"
    broken.mkdir()
    (broken / "trace.csv").write_text("t,mag\n16.535,1.032\n633.982,1.027\n")

    good = _write_session(sessions_dir, garmin_fit=True,
                           trace_rows=[(100.0, 1.0), (200.0, 1.0)])
    shutil.move(str(good), str(sessions_dir / "20260906-000000-TEST"))

    rc = c.main(["--all"], fetch=make_fake_fetch())
    assert rc == 0
    assert (broken / "wind.json").exists()
    doc = json.loads((broken / "wind.json").read_text())
    assert "error" in doc
    assert (sessions_dir / "20260906-000000-TEST" / "wind.json").exists()


# --------------------------------------------------------------------------
# adversarial review 2026-09-15


def test_multi_boot_trace_is_a_finding_not_an_inverted_window(tmp_path):
    """trace.csv is a flash ring buffer: after a power cycle the LAST row's
    `t` can be an earlier uptime than the first row's.

    MEASURED on data/sessions/20260914-104207-E2C4 (no garmin.fit, so this
    is the path that runs): first t=76,608.848 s, last t=12.652 s. Taken at
    face value that is a window starting 2026-09-15T11:58:44Z and ENDING
    2026-09-14T14:42:07Z — inverted, a day out, and previously returned
    ok=True. Every hour filter downstream then matches nothing and the only
    symptom is "no hourly row fell inside the requested window".
    """
    d = _write_session(
        tmp_path,
        session={"trace_epoch_utc": "2026-09-14T14:41:55.281Z"},
        trace_rows=[(76608.848, 1.0), (287859.980, 1.0), (0.031, 1.0),
                    (12.652, 1.0)],
    )
    w = c.derive_window(d)
    assert not w.ok
    assert w.start_utc is None and w.end_utc is None
    assert "backwards" in w.error and "multi-boot" in w.error

    doc = c.gather_conditions(d, fetch=make_fake_fetch())
    assert doc["window"] is None
    assert "backwards" in doc["error"]
    assert doc["sources"] == {}


def test_open_meteo_response_not_in_utc_is_refused_not_relabelled():
    """Both fetchers send timezone=UTC and then parse naive `hourly.time`
    with `.replace(tzinfo=UTC)`. That stamp is an assertion; if the response
    ever comes back local, every hour in wind.json is silently shifted.

    MEASURED live 2026-09-15 against archive-api.open-meteo.com:
    utc_offset_seconds=0, timezone "GMT" — so the guard normally passes.
    """
    local = json.loads(WEATHER_FIXTURE)
    local["utc_offset_seconds"] = -14400
    local["timezone"] = "America/New_York"
    got = c.fetch_open_meteo_weather(
        35.9, -75.66,
        dt.datetime(2026, 9, 14, 19, tzinfo=UTC),
        dt.datetime(2026, 9, 14, 22, tzinfo=UTC),
        fetch=lambda url: json.dumps(local).encode())
    assert got["ok"] is False
    assert "utc_offset_seconds=-14400" in got["error"]
    assert "hours" not in got

    marine = json.loads(MARINE_FIXTURE_NONNULL)
    marine["utc_offset_seconds"] = 3600
    got_m = c.fetch_open_meteo_marine(
        35.9, -75.66,
        dt.datetime(2026, 9, 14, 19, tzinfo=UTC),
        dt.datetime(2026, 9, 14, 22, tzinfo=UTC),
        fetch=lambda url: json.dumps(marine).encode())
    assert got_m["ok"] is False
    assert "NOT in UTC" in got_m["error"]


def test_open_meteo_utc_response_still_passes_the_guard():
    """The guard must not reject the real thing: the live archive answers
    with utc_offset_seconds=0, and an older response omits the key."""
    for extra in ({}, {"utc_offset_seconds": 0, "timezone": "GMT"}):
        payload = json.loads(WEATHER_FIXTURE)
        payload.update(extra)
        got = c.fetch_open_meteo_weather(
            35.9, -75.66,
            dt.datetime(2026, 9, 14, 19, tzinfo=UTC),
            dt.datetime(2026, 9, 14, 22, tzinfo=UTC),
            fetch=lambda url: json.dumps(payload).encode())
        assert got["ok"] is True
        assert [h["time_utc"] for h in got["hours"]][0] == \
            "2026-09-14T19:00:00+00:00"


def test_garmin_window_uses_min_max_not_file_order(tmp_path, monkeypatch):
    """A FIT need not be in time order — fitread counts "record timestamp(s)
    go backwards" for exactly that reason. Reading stamped[0]/stamped[-1]
    would hand back an inverted window from a file fitread had already
    flagged, and those warnings were being dropped."""
    import fitread

    def fake_scan(blob, label):
        def rec(mins):
            return {"timestamp": dt.datetime(2026, 9, 14, 20, mins, tzinfo=UTC),
                    "position_lat": None, "position_long": None}
        return {"stamped": [rec(30), rec(10), rec(50), rec(20)],
                "records": [], "warnings": ["2 record timestamp(s) go backwards; "
                                             "windows assume file order"]}

    monkeypatch.setattr(fitread, "read_fit_bytes", lambda p: (b"", "fake"))
    monkeypatch.setattr(fitread, "scan", fake_scan)
    d = _write_session(tmp_path, garmin_fit=True)
    w = c.derive_window(d)
    assert w.ok
    assert w.start_utc == dt.datetime(2026, 9, 14, 20, 10, tzinfo=UTC)
    assert w.end_utc == dt.datetime(2026, 9, 14, 20, 50, tzinfo=UTC)
    assert any("go backwards" in n for n in w.notes), w.notes


def test_ndbc_station_table_matches_noaa(tmp_path):
    """Verified 2026-09-15 against NOAA's own
    https://www.ndbc.noaa.gov/data/stations/station_table.txt:
      orin7 35.796 N 75.548 W | dukn7 36.184 N 75.746 W | hcgn7 35.209 N 75.704 W
    An identifier without a correct lookup entry is a rediscovery waiting to
    happen (CLAUDE.md section 4)."""
    assert c.NDBC_STATIONS["ORIN7"]["lat"] == 35.796
    assert c.NDBC_STATIONS["ORIN7"]["lon"] == -75.548
    assert c.NDBC_STATIONS["DUKN7"]["lat"] == 36.184
    assert c.NDBC_STATIONS["DUKN7"]["lon"] == -75.746
    assert c.NDBC_STATIONS["HCGN7"]["lat"] == 35.209
    assert c.NDBC_STATIONS["HCGN7"]["lon"] == -75.704
    # The evening session's centroid, from its own wind.json window block.
    st = c.nearest_ndbc_station(35.911153956699785, -75.65974420437419)
    assert st["id"] == "ORIN7"
    assert st["distance_km"] == pytest.approx(16.29, abs=0.05)


def test_ndbc_columns_match_a_real_realtime2_file():
    """The live ORIN7 layout, fetched 2026-09-15: 19 whitespace columns,
    WDIR=5, WSPD=6, GST=7, PRES=12, ATMP=13. PTDY ("+2.0") sits at 17 and
    must not be mistaken for a temperature."""
    text = (
        "#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE\n"
        "#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft\n"
        "2026 09 15 12 00  60  6.2  8.2    MM    MM    MM  MM 1025.1  23.3  24.7    MM   MM +2.0    MM\n"
        "2026 09 15 11 48  60  6.2  8.2    MM    MM    MM  MM 1024.9    MM  24.8    MM   MM   MM    MM\n"
    )
    rows = c.parse_ndbc_realtime2(
        text, dt.datetime(2026, 9, 15, 11, tzinfo=UTC),
        dt.datetime(2026, 9, 15, 13, tzinfo=UTC))
    assert [r["time_utc"] for r in rows] == ["2026-09-15T11:48:00+00:00",
                                              "2026-09-15T12:00:00+00:00"]
    assert rows[1] == {"time_utc": "2026-09-15T12:00:00+00:00", "wdir": 60.0,
                       "wspd_ms": 6.2, "gst_ms": 8.2, "atmp_c": 23.3}
    assert rows[0]["atmp_c"] is None      # "MM", never 0.0 and never +2.0
    assert c.ms_to_kn(6.2) == pytest.approx(12.052, abs=0.001)
