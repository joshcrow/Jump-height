#!/usr/bin/env python3
"""obxwx — back-fill wind and sea state for any OBX session window.

The rider's venues are instrumented (researched 2026-08-27, docs/watch.md +
data/nick-sessions/): a wave buoy ~10 nmi off Jennette's Pier and two wind
stations flanking the sound. Nobody should hand-estimate what an instrument
nearby measured — E14 showed an accuracy number is uninterpretable without
sea state, and this makes recording it automatic: a session timestamp in,
the hour's measured conditions out.

Sources (all public, no keys):
  wind  NOAA CO-OPS 8652587  Oregon Inlet Marina — on the sound, 6-min data
        IEM ASOS archive KMQI Dare Co. airport (Manteo) — hourly METAR,
        full history, overland (expect some sheltering vs on-water)
  waves NDBC 44086 (off Jennette's Pier): realtime2 feed for the last
        ~45 days, monthly stdmet archives before that
        WAVES ARE OCEAN-SIDE ONLY. There is no wave sensor in the sound
        (confirmed negative); for sound sessions the wave row is labeled
        ocean-reference, not venue truth.

Usage:
  python3 tools/obxwx.py 2026-08-16T14:00 --hours 3
  python3 tools/obxwx.py 2026-08-16T14:00 --hours 3 --json
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import io
import json
import sys
import subprocess

# curl, not urllib: the framework Python on this Mac has no CA bundle wired
# up (CERTIFICATE_VERIFY_FAILED on every https host), while curl uses the
# system trust store and has already proven these exact hosts reachable.
def _get(url: str, timeout: int = 30) -> bytes:
    r = subprocess.run(["curl", "-sfL", "--max-time", str(timeout),
                        "-A", "jump-height-bench/1.0 (obxwx)", url],
                       capture_output=True, timeout=timeout + 10)
    if r.returncode != 0:
        raise RuntimeError(f"curl rc={r.returncode} for {url[:80]}")
    return r.stdout


def coops_wind(t0: dt.datetime, t1: dt.datetime) -> list[dict]:
    """Oregon Inlet Marina (8652587), 6-minute wind, m/s. GMT in/out."""
    url = ("https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
           f"?product=wind&station=8652587&time_zone=gmt&units=metric"
           f"&format=json&begin_date={t0:%Y%m%d %H:%M}"
           f"&end_date={t1:%Y%m%d %H:%M}").replace(" ", "%20")
    try:
        data = json.loads(_get(url))
    except Exception as e:
        return [{"error": f"co-ops: {e}"}]
    out = []
    for row in data.get("data", []):
        try:
            out.append({"t": row["t"], "wind_ms": float(row["s"]),
                        "gust_ms": float(row["g"]), "dir": float(row["d"])})
        except (KeyError, ValueError):
            continue
    return out


def iem_wind(t0: dt.datetime, t1: dt.datetime) -> list[dict]:
    """KMQI (Manteo airport) hourly METAR from the IEM archive. Knots in."""
    url = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
           f"?station=MQI&data=sknt&data=drct&data=gust"
           f"&year1={t0.year}&month1={t0.month}&day1={t0.day}"
           f"&year2={t1.year}&month2={t1.month}&day2={t1.day}"
           "&tz=Etc/UTC&format=onlycomma&latlon=no&missing=M&trace=T")
    try:
        txt = _get(url).decode()
    except Exception as e:
        return [{"error": f"iem: {e}"}]
    out = []
    for ln in txt.splitlines()[1:]:
        p = ln.split(",")
        if len(p) < 5:
            continue
        try:
            ts = dt.datetime.fromisoformat(p[1]).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        if not (t0 <= ts <= t1):
            continue
        try:
            kt = float(p[2])
        except ValueError:
            continue
        gust = None
        try:
            gust = float(p[4]) * 0.514444
        except (ValueError, IndexError):
            pass
        try:
            d = float(p[3])
        except ValueError:
            d = None
        out.append({"t": ts.isoformat(), "wind_ms": round(kt * 0.514444, 1),
                    "gust_ms": gust and round(gust, 1), "dir": d})
    return out


def ndbc_waves(t0: dt.datetime, t1: dt.datetime) -> list[dict]:
    """Buoy 44086 Hs/DPD for the window. Realtime feed covers ~45 days;
    older windows read the monthly archive. A missing month is REPORTED,
    never silently skipped."""
    now = dt.datetime.now(dt.timezone.utc)
    rows: list[dict] = []
    texts: list[str] = []
    if (now - t0).days <= 44:
        try:
            texts.append(_get(
                "https://www.ndbc.noaa.gov/data/realtime2/44086.txt"
            ).decode())
        except Exception as e:
            rows.append({"error": f"ndbc realtime: {e}"})
    else:
        mon = t0.strftime("%b")
        for url in (
            f"https://www.ndbc.noaa.gov/data/stdmet/{mon}/"
            f"44086{t0.month}{t0.year}.txt.gz",
            f"https://www.ndbc.noaa.gov/data/stdmet/{mon}/44086.txt",
        ):
            try:
                raw = _get(url)
                texts.append(gzip.decompress(raw).decode()
                             if url.endswith(".gz") else raw.decode())
                break
            except Exception:
                continue
        else:
            rows.append({"error": f"ndbc archive for {t0:%Y-%m} unreachable"})
    for txt in texts:
        for ln in txt.splitlines():
            if ln.startswith("#"):
                continue
            p = ln.split()
            if len(p) < 11:
                continue
            try:
                ts = dt.datetime(int(p[0]), int(p[1]), int(p[2]),
                                 int(p[3]), int(p[4]),
                                 tzinfo=dt.timezone.utc)
                hs, dpd = float(p[8]), float(p[9])
            except ValueError:
                continue
            if t0 <= ts <= t1 and hs < 90:
                rows.append({"t": ts.isoformat(), "hs_m": hs,
                             "dpd_s": dpd if dpd < 90 else None})
    return rows


def summarize(rows: list[dict], key: str):
    vals = [r[key] for r in rows if key in r and r.get(key) is not None]
    if not vals:
        return None
    vals.sort()
    return {"n": len(vals), "min": vals[0], "med": vals[len(vals) // 2],
            "max": vals[-1]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("start", help="session start, ISO, assumed UTC "
                                  "(e.g. 2026-08-16T14:00)")
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    t0 = dt.datetime.fromisoformat(args.start).replace(tzinfo=dt.timezone.utc)
    t1 = t0 + dt.timedelta(hours=args.hours)

    coops = coops_wind(t0, t1)
    iem = iem_wind(t0, t1)
    waves = ndbc_waves(t0, t1)
    report = {
        "window_utc": [t0.isoformat(), t1.isoformat()],
        "wind_sound_8652587_ms": summarize(coops, "wind_ms"),
        "gust_sound_8652587_ms": summarize(coops, "gust_ms"),
        "wind_kmqi_ms": summarize(iem, "wind_ms"),
        "waves_44086_hs_m": summarize(waves, "hs_m"),
        "waves_44086_dpd_s": summarize(waves, "dpd_s"),
        "errors": [r["error"] for r in coops + iem + waves if "error" in r],
        "note": "waves are the OCEAN buoy — venue truth ocean-side only; "
                "ocean-reference for sound sessions",
    }
    if args.json:
        print(json.dumps(report, indent=1))
    else:
        for k, v in report.items():
            print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
