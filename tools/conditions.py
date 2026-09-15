#!/usr/bin/env python3
"""conditions.py — external wind/wave conditions for a session's window.

`docs/accuracy-plan.md`'s corpus table lists `wind.json` as "forecast/obs,
when captured" but nothing wrote one until this. This derives WHERE and WHEN
a session happened, then pulls what free, keyless instruments say the air
and water were doing over that window, and writes `<session>/wind.json`.

PLACE AND TIME
---------------
When `garmin.fit` exists, both come from it: the position centroid of every
`record` that carries `position_lat`/`position_long`, and the first/last
STAMPED record timestamp (`tools/fitread.py` is the repo's one FIT reader —
this does not parse FIT a second time, it calls `fitread.read_fit_bytes` +
`fitread.scan`, the same as `sim/score.py`'s `load_garmin`).

Without `garmin.fit` there is no position, only `session.json`'s
`trace_epoch_utc` plus `trace.csv`'s first and last `t` — a time window with
nowhere to send it. Open-Meteo needs a lat/lon; NDBC needs a lat/lon to pick
a station. So the pull is skipped, and the reason is written into wind.json
rather than guessed at with a fixed venue coordinate (CLAUDE.md section 2:
no verdict — here, no reading — without an actual measurement backing it).

SOURCES (all free, keyless)
----------------------------
  open-meteo weather archive  archive-api.open-meteo.com/v1/archive
      hourly wind_speed_10m/wind_gusts_10m/wind_direction_10m/temperature_2m/
      surface_pressure, wind_speed_unit=kn. Queried for the whole day(s) the
      window falls in, then filtered to the session's hours plus one hour
      either side.
  open-meteo marine           marine-api.open-meteo.com/v1/marine
      hourly wave_height/wave_period/wave_direction. Always attempted when a
      position exists — this repo's sessions are all Outer Banks water, so a
      coastline lookup would be one more thing to get wrong for no benefit —
      but never trusted blindly: the response carries the GRID point it
      actually used, and if that point is more than a few km from the ride
      (Roanoke Sound is a narrow sheltered sound; Open-Meteo's marine grid is
      coarse and can snap to open ocean instead) or the row is null, wind.json
      says so instead of presenting a number as venue truth.
  NOAA NDBC realtime2         ndbc.noaa.gov/data/realtime2/<STATION>.txt
      WDIR/WSPD/GST (+ATMP) parsed for the exact session window. Station
      chosen from a small built-in Outer Banks table by great-circle distance,
      only within NDBC_MAX_KM; NDBC_STATIONS is not a general station
      directory (CLAUDE.md section 4 — an identifier without a lookup entry
      is a rediscovery waiting to happen: a session outside this pocket of
      coastline would need its own table entries, not a guess).

Every network call goes through one `fetch(url) -> bytes` seam (default
`default_fetch`, real urllib) so tests can inject a fixture-backed fake and
never touch the network. No source function raises past its own boundary: a
failed fetch, bad JSON, or an all-null response becomes a field in the
returned dict, never an exception — a reading that did not happen is a
finding (CLAUDE.md section 2.3), not a crashed command, and one dead source
must never stop the other two or the session after it in `--all`.

TLS
---
This Mac's framework Python ships no CA bundle, so a bare `urlopen` against
any of these hosts fails `CERTIFICATE_VERIFY_FAILED` (measured directly
against archive-api.open-meteo.com while writing this). `tools/puckd/netctx.py`
already carries the fix (certifi's bundle, falling back to the platform
default) for puckd's own HTTPS calls; `_ssl_context()` below is the same
four lines rather than an import into a module other agents are editing
concurrently this session.

Usage:
    python3 tools/conditions.py data/sessions/<id>
    python3 tools/conditions.py --all [--force]

Run via `python3 -m pytest tools/tests/test_conditions.py -q -p no:warnings`.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import ssl
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

UTC = dt.timezone.utc
FETCH_TIMEOUT_S = 20.0
USER_AGENT = "jump-height-bench/1.0 (tools/conditions.py)"

FT_PER_M = 3.280839895
KN_PER_MS = 1.9438444924406

Fetch = Callable[[str], bytes]

# A run cannot bridge a recording gap when reading trace.csv's tail; not used
# for detection here (sim/score.py owns that), only to find the last `t`.
_TAIL_CHUNK = 8192


def _ssl_context() -> ssl.SSLContext:
    """certifi's CA bundle when available, else the platform default.

    Same fix as tools/puckd/netctx.py:ssl_context() — see module docstring
    ("TLS") for why a bare urlopen fails on this machine.
    """
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 -- no certifi: fall back to the platform store
        return ssl.create_default_context()


def default_fetch(url: str, timeout: float = FETCH_TIMEOUT_S) -> bytes:
    """The one real network call. Every source takes a `fetch` parameter
    defaulting to this, so a test can pass a fixture-backed fake instead."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as r:
        return r.read()


# --------------------------------------------------------------------------
# place and time


@dataclass
class Window:
    ok: bool
    start_utc: Optional[dt.datetime] = None
    end_utc: Optional[dt.datetime] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    position_count: int = 0
    time_source: str = ""
    position_source: str = ""
    error: Optional[str] = None
    notes: list = field(default_factory=list)


def _last_line(path: Path, chunk: int = _TAIL_CHUNK) -> Optional[bytes]:
    """The last non-blank line of a file, read from the tail — trace.csv can
    be 10 MB+ and only its last row is wanted here."""
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        data = b""
        pos = size
        while pos > 0:
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
            if data.count(b"\n") >= 2 or pos == 0:
                break
        lines = [ln for ln in data.splitlines() if ln.strip()]
        return lines[-1] if lines else None


def trace_time_range(trace_path: Path) -> Optional[tuple[float, float]]:
    """(first t, last t) in trace.csv, without loading the whole file."""
    if not trace_path.exists():
        return None
    with trace_path.open("r", newline="") as f:
        f.readline()  # header: t,mag
        first = f.readline()
    if not first.strip():
        return None
    try:
        t_start = float(first.split(",")[0])
    except ValueError:
        return None
    last = _last_line(trace_path)
    if last is None:
        return None
    try:
        t_end = float(last.decode().split(",")[0])
    except ValueError:
        return None
    return (t_start, t_end)


def load_garmin_window(sess: Path) -> tuple[Optional[dict], str]:
    """Position centroid + first/last stamped record timestamp from
    garmin.fit, read through tools/fitread.py (the repo's one FIT reader).

    Returns (data, reason). data is None when garmin.fit is absent,
    unreadable, or has no timestamped record — the caller falls back to the
    trace window and the reason goes in wind.json's notes, never silently.
    """
    p = sess / "garmin.fit"
    if not p.exists():
        return None, "no garmin.fit"
    try:
        import fitread  # tools/fitread.py
    except ImportError as exc:
        return None, f"fitread unavailable ({exc})"
    try:
        blob, label = fitread.read_fit_bytes(p)
        scanned = fitread.scan(blob, label)
    except SystemExit as exc:  # fitread.die() on an unreadable file
        return None, f"garmin.fit unreadable ({exc})"
    except Exception as exc:  # noqa: BLE001 -- never crash the tool over one bad file
        return None, f"garmin.fit read failed ({type(exc).__name__}: {exc})"

    stamped = scanned["stamped"]
    if not stamped:
        return None, "garmin.fit has no timestamped records"

    lats, lons = [], []
    for r in stamped:
        lat, lon = r["position_lat"], r["position_long"]
        if lat is not None and lon is not None:
            lats.append(lat * fitread.SEMICIRCLE_TO_DEG)
            lons.append(lon * fitread.SEMICIRCLE_TO_DEG)

    data = {
        "start_utc": stamped[0]["timestamp"].astimezone(UTC),
        "end_utc": stamped[-1]["timestamp"].astimezone(UTC),
        "record_count": len(stamped),
        "position_count": len(lats),
        "lat": (sum(lats) / len(lats)) if lats else None,
        "lon": (sum(lons) / len(lons)) if lons else None,
    }
    return data, "garmin.fit"


def derive_window(sess: Path) -> Window:
    """The ride's place and time: Garmin when there is one, else the trace
    window with no position (CLAUDE.md section 2.3 — say why, don't guess)."""
    g, g_reason = load_garmin_window(sess)
    if g is not None:
        notes = []
        if g["position_count"] == 0:
            notes.append(
                "garmin.fit carries no positioned record — no centroid; "
                "external pull skipped")
        return Window(
            ok=True,
            start_utc=g["start_utc"], end_utc=g["end_utc"],
            lat=g["lat"], lon=g["lon"],
            position_count=g["position_count"],
            time_source=f"garmin.fit first/last of {g['record_count']} stamped record(s)",
            position_source=(f"garmin.fit position centroid ({g['position_count']} "
                              f"positioned record(s))" if g["position_count"] else ""),
            notes=notes,
        )

    sj_path = sess / "session.json"
    if not sj_path.exists():
        return Window(ok=False, error=(
            f"{g_reason}, and no session.json — cannot derive a UTC time window"))
    try:
        sj = json.loads(sj_path.read_text())
        epoch = dt.datetime.fromisoformat(sj["trace_epoch_utc"].replace("Z", "+00:00"))
        if epoch.tzinfo is None:
            epoch = epoch.replace(tzinfo=UTC)
    except Exception as exc:  # noqa: BLE001 -- malformed session.json is a finding, not a crash
        return Window(ok=False, error=(
            f"session.json unreadable or missing trace_epoch_utc ({type(exc).__name__}: {exc})"))

    tr = trace_time_range(sess / "trace.csv")
    if tr is None:
        return Window(ok=False, error="no trace.csv (or it has no data rows) — cannot derive a time window")
    t0, t1 = tr
    return Window(
        ok=True,
        start_utc=epoch + dt.timedelta(seconds=t0),
        end_utc=epoch + dt.timedelta(seconds=t1),
        lat=None, lon=None, position_count=0,
        time_source=f"session.json trace_epoch_utc + trace.csv t range ({g_reason})",
        position_source="",
        notes=[f"no position available ({g_reason}) — external pull skipped"],
    )


# --------------------------------------------------------------------------
# geometry


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


COMPASS_16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
              "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def compass(deg: Optional[float]) -> Optional[str]:
    if deg is None:
        return None
    return COMPASS_16[round((deg % 360) / 22.5) % 16]


def circular_mean_deg(degs) -> Optional[float]:
    vals = [d for d in degs if d is not None]
    if not vals:
        return None
    sx = sum(math.sin(math.radians(d)) for d in vals)
    cx = sum(math.cos(math.radians(d)) for d in vals)
    if abs(sx) < 1e-9 and abs(cx) < 1e-9:
        return None
    return math.degrees(math.atan2(sx, cx)) % 360


def c_to_f(c: Optional[float]) -> Optional[float]:
    return None if c is None else c * 9.0 / 5.0 + 32.0


def ms_to_kn(v: Optional[float]) -> Optional[float]:
    return None if v is None else v * KN_PER_MS


def m_to_ft(v: Optional[float]) -> Optional[float]:
    return None if v is None else v * FT_PER_M


def _stats(values) -> Optional[dict]:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return {"mean": sum(vals) / len(vals), "min": min(vals), "max": max(vals), "n": len(vals)}


def _at(lst, i):
    return lst[i] if lst and i < len(lst) else None


# --------------------------------------------------------------------------
# open-meteo


OPEN_METEO_WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"


def _build_url(base: str, params: dict) -> str:
    return base + "?" + urllib.parse.urlencode(params)


def fetch_open_meteo_weather(lat: float, lon: float, lo: dt.datetime, hi: dt.datetime,
                              fetch: Fetch = default_fetch) -> dict:
    """Hourly wind_speed_10m/wind_gusts_10m/wind_direction_10m/temperature_2m/
    surface_pressure, wind_speed_unit=kn, for the day(s) [lo, hi] spans,
    filtered to [lo, hi] itself (the session's hours plus one either side)."""
    params = {
        "latitude": f"{lat:.5f}",
        "longitude": f"{lon:.5f}",
        "start_date": lo.date().isoformat(),
        "end_date": hi.date().isoformat(),
        "hourly": ",".join(["wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
                             "temperature_2m", "surface_pressure"]),
        "wind_speed_unit": "kn",
        "timezone": "UTC",
    }
    url = _build_url(OPEN_METEO_WEATHER_URL, params)
    result = {"source": "open-meteo historical weather archive", "url": url, "queried": True}
    try:
        raw = fetch(url)
    except Exception as exc:  # noqa: BLE001 -- a failed source is a line in wind.json, not an exception
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["error"] = f"bad JSON from open-meteo weather: {exc}"
        return result
    hourly = data.get("hourly")
    if not hourly:
        result["ok"] = False
        result["error"] = data.get("reason") or "no 'hourly' block in the response"
        return result

    times = hourly.get("time", [])
    rows = []
    for i, t in enumerate(times):
        try:
            ts = dt.datetime.fromisoformat(t).replace(tzinfo=UTC)
        except ValueError:
            continue
        if not (lo <= ts <= hi):
            continue
        rows.append({
            "time_utc": ts.isoformat(),
            "wind_speed_10m": _at(hourly.get("wind_speed_10m"), i),
            "wind_gusts_10m": _at(hourly.get("wind_gusts_10m"), i),
            "wind_direction_10m": _at(hourly.get("wind_direction_10m"), i),
            "temperature_2m": _at(hourly.get("temperature_2m"), i),
            "surface_pressure": _at(hourly.get("surface_pressure"), i),
        })
    result.update(ok=True,
                  grid_latitude=data.get("latitude"),
                  grid_longitude=data.get("longitude"),
                  units=data.get("hourly_units", {}),
                  hours=rows)
    notes = []
    if not rows:
        notes.append("no hourly row fell inside the requested window")
    if result.get("grid_latitude") is not None and result.get("grid_longitude") is not None:
        d_km = haversine_km(lat, lon, result["grid_latitude"], result["grid_longitude"])
        result["grid_distance_km"] = d_km
        # MEASURED 2026-09-14: the archive's reanalysis grid snapped 14.2 km
        # from a Roanoke Sound query point — a real gap, not a rounding
        # artifact, so it is reported rather than presented as on-site.
        if d_km > 8.0:
            notes.append(
                f"grid point is {d_km:.1f} km from the session position — the "
                "archive's model resolution, not a local reading; treat as "
                "regional context, not venue truth")
    if notes:
        result["note"] = "; ".join(notes)
    return result


def fetch_open_meteo_marine(lat: float, lon: float, lo: dt.datetime, hi: dt.datetime,
                             fetch: Fetch = default_fetch) -> dict:
    """Hourly wave_height/wave_period/wave_direction. Always attempted (see
    module docstring); the response's own grid point decides how much to
    trust it, not a coastline check done here."""
    params = {
        "latitude": f"{lat:.5f}",
        "longitude": f"{lon:.5f}",
        "start_date": lo.date().isoformat(),
        "end_date": hi.date().isoformat(),
        "hourly": "wave_height,wave_period,wave_direction",
        "timezone": "UTC",
    }
    url = _build_url(OPEN_METEO_MARINE_URL, params)
    result = {"source": "open-meteo marine", "url": url, "queried": True}
    try:
        raw = fetch(url)
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    try:
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["error"] = f"bad JSON from open-meteo marine: {exc}"
        return result
    hourly = data.get("hourly")
    if not hourly:
        result["ok"] = False
        result["error"] = data.get("reason") or "no 'hourly' block in the response"
        return result

    times = hourly.get("time", [])
    rows = []
    for i, t in enumerate(times):
        try:
            ts = dt.datetime.fromisoformat(t).replace(tzinfo=UTC)
        except ValueError:
            continue
        if not (lo <= ts <= hi):
            continue
        rows.append({
            "time_utc": ts.isoformat(),
            "wave_height": _at(hourly.get("wave_height"), i),
            "wave_period": _at(hourly.get("wave_period"), i),
            "wave_direction": _at(hourly.get("wave_direction"), i),
        })
    result.update(ok=True,
                  grid_latitude=data.get("latitude"),
                  grid_longitude=data.get("longitude"),
                  units=data.get("hourly_units", {}),
                  hours=rows)

    notes = []
    if not rows:
        notes.append("no hourly row fell inside the requested window")
    elif all(r["wave_height"] is None for r in rows):
        notes.append(
            "marine grid returned nulls for every hour in this window — no wave "
            "model data at this point (docs/accuracy-plan.md: expected for "
            "Roanoke Sound / Manteo NC)")
    if result.get("grid_latitude") is not None and result.get("grid_longitude") is not None:
        d_km = haversine_km(lat, lon, result["grid_latitude"], result["grid_longitude"])
        result["grid_distance_km"] = d_km
        if d_km > 3.0:
            notes.append(
                f"grid point is {d_km:.1f} km from the session position — Open-Meteo "
                "marine's resolution can snap to open water instead of a sheltered "
                "sound; treat any non-null value here as offshore-reference, not "
                "venue truth")
    if notes:
        result["note"] = "; ".join(notes)
    return result


# --------------------------------------------------------------------------
# NDBC


# Outer Banks pocket table (CLAUDE.md section 4: an identifier without a
# lookup entry is a rediscovery waiting to happen — a session outside this
# coastline needs its own entries, not a guessed nearest station). Positions
# from NOAA's station_table.txt, measured 2026-09-14.
NDBC_STATIONS = {
    "ORIN7": {"lat": 35.796, "lon": -75.548, "name": "Oregon Inlet Marina, NC"},
    "DUKN7": {"lat": 36.184, "lon": -75.746, "name": "Duck, NC (FRF pier)"},
    "HCGN7": {"lat": 35.209, "lon": -75.704, "name": "Hatteras, NC (USCG station)"},
}
NDBC_MAX_KM = 50.0
NDBC_REALTIME_URL = "https://www.ndbc.noaa.gov/data/realtime2/{station}.txt"


def nearest_ndbc_station(lat: float, lon: float) -> dict:
    """Closest table entry, always, with its distance. The caller applies
    the ~50 km cutoff — kept separate so 'how far' is itself inspectable."""
    sid, meta = min(NDBC_STATIONS.items(),
                     key=lambda kv: haversine_km(lat, lon, kv[1]["lat"], kv[1]["lon"]))
    return {"id": sid, "name": meta["name"], "lat": meta["lat"], "lon": meta["lon"],
            "distance_km": haversine_km(lat, lon, meta["lat"], meta["lon"])}


def _mm(s: str) -> Optional[float]:
    """NDBC's missing-value marker. 'MM' must never become 0.0."""
    if s in ("MM", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_ndbc_realtime2(text: str, lo: dt.datetime, hi: dt.datetime) -> list[dict]:
    """WDIR/WSPD/GST/ATMP for rows with [lo, hi], from the fixed realtime2
    met column layout:
    #YY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD PRES ATMP WTMP DEWP VIS PTDY TIDE
    """
    rows = []
    for ln in text.splitlines():
        if not ln.strip() or ln.startswith("#"):
            continue
        p = ln.split()
        if len(p) < 8:
            continue
        try:
            ts = dt.datetime(int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4]), tzinfo=UTC)
        except ValueError:
            continue
        if not (lo <= ts <= hi):
            continue
        rows.append({
            "time_utc": ts.isoformat(),
            "wdir": _mm(p[5]),
            "wspd_ms": _mm(p[6]),
            "gst_ms": _mm(p[7]),
            "atmp_c": _mm(p[13]) if len(p) > 13 else None,
        })
    rows.sort(key=lambda r: r["time_utc"])
    return rows


def fetch_ndbc(lat: float, lon: float, start_utc: dt.datetime, end_utc: dt.datetime,
                fetch: Fetch = default_fetch) -> dict:
    station = nearest_ndbc_station(lat, lon)
    if station["distance_km"] > NDBC_MAX_KM:
        return {
            "source": "NOAA NDBC realtime2", "queried": False, "nearest": station,
            "reason": (f"nearest station {station['id']} ({station['name']}) is "
                       f"{station['distance_km']:.1f} km away, over the "
                       f"{NDBC_MAX_KM:.0f} km cutoff"),
        }
    url = NDBC_REALTIME_URL.format(station=station["id"])
    result = {"source": "NOAA NDBC realtime2", "station": station, "url": url, "queried": True}
    try:
        raw = fetch(url)
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result
    try:
        text = raw.decode("latin-1")
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["error"] = f"decode failed: {type(exc).__name__}: {exc}"
        return result
    rows = parse_ndbc_realtime2(text, start_utc, end_utc)
    result["ok"] = True
    result["hours"] = rows
    if not rows:
        result["note"] = (
            "station has no row inside the session window (outside the ~45-day "
            "realtime2 range, or a data gap)")
    return result


# --------------------------------------------------------------------------
# summary


def build_summary(weather: dict, marine: dict, ndbc: dict) -> dict:
    summary: dict = {}

    if weather.get("ok"):
        rows = weather["hours"]
        dirs = [r["wind_direction_10m"] for r in rows]
        mean_dir = circular_mean_deg(dirs)
        temps_f = [c_to_f(r["temperature_2m"]) for r in rows if r["temperature_2m"] is not None]
        summary["open_meteo_weather"] = {
            "grid_latitude": weather.get("grid_latitude"),
            "grid_longitude": weather.get("grid_longitude"),
            "grid_distance_km": weather.get("grid_distance_km"),
            "wind_speed_kn": _stats(r["wind_speed_10m"] for r in rows),
            "wind_gust_kn": _stats(r["wind_gusts_10m"] for r in rows),
            "wind_direction_deg_mean": mean_dir,
            "wind_direction_compass": compass(mean_dir),
            "temperature_f": _stats(temps_f),
            "surface_pressure_hpa": _stats(r["surface_pressure"] for r in rows),
            "note": weather.get("note"),
        }
    else:
        summary["open_meteo_weather"] = None

    if marine.get("ok"):
        rows = marine["hours"]
        h_m = _stats(r["wave_height"] for r in rows)
        summary["open_meteo_marine"] = {
            "grid_latitude": marine.get("grid_latitude"),
            "grid_longitude": marine.get("grid_longitude"),
            "grid_distance_km": marine.get("grid_distance_km"),
            "wave_height_m": h_m,
            "wave_height_ft": ({k: (m_to_ft(v) if k != "n" else v) for k, v in h_m.items()}
                                if h_m else None),
            "wave_period_s": _stats(r["wave_period"] for r in rows),
            "note": marine.get("note"),
        }
    else:
        summary["open_meteo_marine"] = None

    if ndbc.get("ok"):
        rows = ndbc["hours"]
        mean_dir = circular_mean_deg(r["wdir"] for r in rows)
        temps_f = [c_to_f(r["atmp_c"]) for r in rows if r.get("atmp_c") is not None]
        summary["ndbc"] = {
            "station": ndbc.get("station"),
            "wind_speed_kn": _stats(ms_to_kn(r["wspd_ms"]) for r in rows),
            "wind_gust_kn": _stats(ms_to_kn(r["gst_ms"]) for r in rows),
            "wind_direction_deg_mean": mean_dir,
            "wind_direction_compass": compass(mean_dir),
            "air_temperature_f": _stats(temps_f),
            "note": ndbc.get("note"),
        }
    elif ndbc.get("nearest") is not None:
        summary["ndbc"] = {"station": None, "reason": ndbc.get("reason"), "nearest": ndbc.get("nearest")}
    else:
        summary["ndbc"] = None

    return summary


# --------------------------------------------------------------------------
# orchestration


def gather_conditions(sess: Path, fetch: Fetch = default_fetch) -> dict:
    """Everything wind.json needs, for one session. Never raises: every
    failure mode becomes a field in the returned dict."""
    doc: dict = {
        "session": sess.name,
        "generated_at_utc": dt.datetime.now(UTC).isoformat(),
        "window": None,
        "sources": {},
        "summary": {},
        "notes": [],
    }

    win = derive_window(sess)
    if not win.ok:
        doc["error"] = win.error
        doc["notes"].append(win.error)
        return doc

    doc["window"] = {
        "start_utc": win.start_utc.isoformat(),
        "end_utc": win.end_utc.isoformat(),
        "lat": win.lat,
        "lon": win.lon,
        "position_count": win.position_count,
        "time_source": win.time_source,
        "position_source": win.position_source,
    }
    doc["notes"].extend(win.notes)

    if win.lat is None or win.lon is None:
        reason = "no position for this session — external pull skipped"
        if reason not in doc["notes"]:
            doc["notes"].append(reason)
        doc["sources"] = {
            "open_meteo_weather": {"queried": False, "reason": reason},
            "open_meteo_marine": {"queried": False, "reason": reason},
            "ndbc": {"queried": False, "reason": reason},
        }
        return doc

    # The session's hours, plus one hour either side (task spec, weather
    # archive only — NDBC below uses the exact window).
    lo = win.start_utc.replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=1)
    hi = win.end_utc.replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)

    weather = fetch_open_meteo_weather(win.lat, win.lon, lo, hi, fetch=fetch)
    marine = fetch_open_meteo_marine(win.lat, win.lon, lo, hi, fetch=fetch)
    ndbc = fetch_ndbc(win.lat, win.lon, win.start_utc, win.end_utc, fetch=fetch)
    doc["sources"] = {"open_meteo_weather": weather, "open_meteo_marine": marine, "ndbc": ndbc}

    try:
        doc["summary"] = build_summary(weather, marine, ndbc)
    except Exception as exc:  # noqa: BLE001 -- a summary that can't be built is a note, not a crash
        doc["summary"] = {}
        doc["notes"].append(f"summary computation failed: {type(exc).__name__}: {exc}")

    return doc


def write_wind_json(sess: Path, doc: dict) -> Path:
    path = sess / "wind.json"
    path.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n")
    return path


def _fmt_range(stats: Optional[dict], unit: str, digits: int = 0) -> str:
    if not stats:
        return f"no {unit}"
    return f"{stats['min']:.{digits}f}-{stats['max']:.{digits}f} {unit}"


def one_line_summary(doc: dict) -> str:
    sess = doc["session"]
    if doc.get("error"):
        return f"{sess}: {doc['error']}"

    w = doc["window"]
    bits = [f"{w['start_utc']}..{w['end_utc']}"]
    if w["lat"] is not None:
        bits.append(f"@ {w['lat']:.4f},{w['lon']:.4f}")
    else:
        bits.append("(no position — pull skipped)")

    s = doc.get("summary") or {}
    wx = s.get("open_meteo_weather")
    if wx:
        gust = f" gust to {wx['wind_gust_kn']['max']:.0f}" if wx.get("wind_gust_kn") else ""
        dirn = f" {wx['wind_direction_compass']}" if wx.get("wind_direction_compass") else ""
        bits.append(f"open-meteo wind {_fmt_range(wx.get('wind_speed_kn'), 'kn')}{gust} kn{dirn}")
        if wx.get("temperature_f"):
            bits.append(f"{_fmt_range(wx['temperature_f'], 'F')}")

    mar = s.get("open_meteo_marine")
    if mar and mar.get("wave_height_ft"):
        note = " (see note)" if mar.get("note") else ""
        bits.append(f"waves {_fmt_range(mar['wave_height_ft'], 'ft', 1)}{note}")
    elif mar and mar.get("note"):
        bits.append(f"marine: {mar['note']}")

    nd = s.get("ndbc")
    if nd and nd.get("station") and nd.get("wind_speed_kn"):
        st = nd["station"]
        dirn = f" {nd['wind_direction_compass']}" if nd.get("wind_direction_compass") else ""
        gust = f" gust to {nd['wind_gust_kn']['max']:.0f}" if nd.get("wind_gust_kn") else ""
        bits.append(f"NDBC {st['id']} ({st['distance_km']:.0f} km) "
                    f"{_fmt_range(nd.get('wind_speed_kn'), 'kn')}{gust} kn{dirn}")
    elif nd and nd.get("reason"):
        bits.append(f"NDBC: {nd['reason']}")

    return f"{sess}: " + "  ".join(bits)


# --------------------------------------------------------------------------
# CLI


def _session_dirs() -> list[Path]:
    base = REPO / "data" / "sessions"
    if not base.is_dir():
        return []
    return sorted(p for p in base.iterdir() if p.is_dir() and (p / "trace.csv").exists())


def main(argv=None, fetch: Fetch = default_fetch) -> int:
    ap = argparse.ArgumentParser(
        description="Derive a session's place/time and pull external wind/wave "
                     "conditions into <session>/wind.json.")
    ap.add_argument("session", nargs="?", type=Path, help="a data/sessions/<id> directory")
    ap.add_argument("--all", action="store_true", help="run over every data/sessions/* session")
    ap.add_argument("--force", action="store_true",
                     help="with --all, re-pull sessions that already have wind.json")
    args = ap.parse_args(argv)

    if args.all:
        rc = 0
        for sess in _session_dirs():
            wpath = sess / "wind.json"
            if wpath.exists() and not args.force:
                print(f"{sess.name}: skip (wind.json exists; --force to redo)")
                continue
            doc = gather_conditions(sess, fetch=fetch)
            write_wind_json(sess, doc)
            print(one_line_summary(doc))
        return rc

    if args.session is None:
        ap.error("give a session directory, or --all")
    sess = args.session
    if not sess.is_dir():
        print(f"{sess}: not a directory", file=sys.stderr)
        return 2

    doc = gather_conditions(sess, fetch=fetch)
    path = write_wind_json(sess, doc)
    print(one_line_summary(doc))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
