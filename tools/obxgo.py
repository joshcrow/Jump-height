#!/usr/bin/env python3
"""obxgo — is today a wing day on Nick's water? (GO / MARGINAL / NO)

The morning Routine "Nick's riding conditions" runs this at 07:00 America/
New_York so Josh can text Nick to bring the OG (JumpHeight-E2C4, the only
board that can measure power) before he leaves. obxwx.py back-fills what an
instrument measured; this is the forward-looking half, and it is a forecast,
not a measurement — say so wherever the verdict is repeated.

Venue (E15, sim/experiments/e15_venues.py): Roanoke Sound at the Manteo /
Nags Head causeway, and the ocean side Coquina Beach -> Jennette's Pier.
One NWS gridpoint (MHX 91,117 at 35.91N 75.62W, Nags Head) covers both.

Verdict rule (stated, not derived — tune from labeled sessions once the
store has enough of them):
  GO        >= 3 consecutive daylight hours (08-19 local) at >= 15 mph
  MARGINAL  >= 3 consecutive daylight hours at >= 12 mph
  NO        otherwise. 2026-09-06 (big wing, ~9 mph forecast) never got on
            foil; that session is the floor this rule sits above.
  Any thunder in the window downgrades to NO. Gusts >= 40 mph downgrade GO
  to MARGINAL (the sound gets ugly, and the OG is not glued on).
Direction hint: W-quadrant wind is offshore on the ocean side -> sound.

A reading that did not happen is a finding: an unreachable forecast prints
UNKNOWN, never NO.

Usage:
  python3 tools/obxgo.py            # today, local
  python3 tools/obxgo.py --json
  python3 tools/obxgo.py --date 2026-09-09
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import zoneinfo

TZ = zoneinfo.ZoneInfo("America/New_York")
HOURLY = "https://api.weather.gov/gridpoints/MHX/91,117/forecast/hourly"
GRID = "https://api.weather.gov/gridpoints/MHX/91,117"
DAY_START, DAY_END = 8, 19          # local hours, inclusive start, exclusive end
GO_MPH, MARGINAL_MPH = 15, 12
MIN_RUN_H = 3
GUST_CAP_MPH = 40
OFFSHORE = {"W", "WNW", "WSW", "NW", "SW"}


def _get(url: str, timeout: int = 30) -> bytes:
    r = subprocess.run(["curl", "-sfL", "--max-time", str(timeout),
                        "-A", "jump-height-bench/1.0 (obxgo)",
                        "-H", "Accept: application/geo+json", url],
                       capture_output=True, timeout=timeout + 10)
    if r.returncode != 0:
        raise RuntimeError(f"curl rc={r.returncode} for {url}")
    return r.stdout


def _mph(s: str) -> float | None:
    """'10 to 15 mph' -> 15.0 (upper bound), '9 mph' -> 9.0."""
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s or "")]
    return max(nums) if nums else None


def hourly(day: dt.date) -> list[dict]:
    d = json.loads(_get(HOURLY))["properties"]
    rows = []
    for p in d["periods"]:
        t = dt.datetime.fromisoformat(p["startTime"]).astimezone(TZ)
        if t.date() != day or not (DAY_START <= t.hour < DAY_END):
            continue
        rows.append({"t": t.isoformat(), "hour": t.hour,
                     "mph": _mph(p["windSpeed"]), "dir": p["windDirection"],
                     "temp_f": p["temperature"],
                     "pop": (p.get("probabilityOfPrecipitation") or {}).get("value"),
                     "sky": p["shortForecast"]})
    return rows


def gusts(day: dt.date) -> dict[int, float]:
    """Hour -> gust mph from the raw gridpoint (km/h in). Best effort."""
    out: dict[int, float] = {}
    try:
        g = json.loads(_get(GRID))["properties"]["windGust"]["values"]
    except Exception:
        return out
    for v in g:
        try:
            start, dur = v["validTime"].split("/")
            t0 = dt.datetime.fromisoformat(start).astimezone(TZ)
            m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?", dur)
            hours = int(m.group(1) or 0) + (1 if m.group(2) else 0)
            kmh = float(v["value"])
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        for h in range(max(hours, 1)):
            t = t0 + dt.timedelta(hours=h)
            if t.date() == day:
                out[t.hour] = round(kmh / 1.609, 1)
    return out


def longest_run(rows: list[dict], mph: float) -> tuple[int, int | None]:
    best, best_start, run, start = 0, None, 0, None
    prev_hour = None
    for r in rows:
        ok = r["mph"] is not None and r["mph"] >= mph
        contiguous = prev_hour is not None and r["hour"] == prev_hour + 1
        if ok and contiguous and run:
            run += 1
        elif ok:
            run, start = 1, r["hour"]
        else:
            run, start = 0, None
        if run > best:
            best, best_start = run, start
        prev_hour = r["hour"]
    return best, best_start


def verdict(rows: list[dict], gust: dict[int, float]) -> dict:
    if not rows:
        return {"verdict": "UNKNOWN", "why": "no forecast hours for the window"}
    go_n, go_at = longest_run(rows, GO_MPH)
    mg_n, mg_at = longest_run(rows, MARGINAL_MPH)
    thunder = [r["hour"] for r in rows if "thunder" in r["sky"].lower()
               or "t-storm" in r["sky"].lower()]
    peak_gust = max((g for h, g in gust.items() if DAY_START <= h < DAY_END),
                    default=None)
    if go_n >= MIN_RUN_H:
        v, at, n = "GO", go_at, go_n
    elif mg_n >= MIN_RUN_H:
        v, at, n = "MARGINAL", mg_at, mg_n
    else:
        v, at, n = "NO", None, max(go_n, mg_n)
    why = []
    if at is not None:
        why.append(f"{n} h from {at:02d}:00 local at >= "
                   f"{GO_MPH if v == 'GO' else MARGINAL_MPH} mph")
    else:
        peak = max((r["mph"] or 0) for r in rows)
        why.append(f"peak {peak:g} mph; longest run >= {MARGINAL_MPH} mph "
                   f"is {mg_n} h (< {MIN_RUN_H})")
    if thunder:
        v = "NO"
        why.append(f"thunder forecast at {thunder}")
    if v == "GO" and peak_gust and peak_gust >= GUST_CAP_MPH:
        v = "MARGINAL"
        why.append(f"gusts to {peak_gust:g} mph")
    dirs = {r["dir"] for r in rows if r["mph"] and r["mph"] >= MARGINAL_MPH}
    if dirs and dirs <= OFFSHORE:
        side = "sound (W-quadrant is offshore on the ocean side)"
    elif dirs:
        side = "either side"
    else:
        side = "n/a"
    return {"verdict": v, "why": "; ".join(why), "venue_hint": side,
            "peak_gust_mph": peak_gust, "window_local": f"{DAY_START:02d}-{DAY_END:02d}"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="YYYY-MM-DD local; default today")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    day = (dt.date.fromisoformat(a.date) if a.date
           else dt.datetime.now(TZ).date())
    try:
        rows = hourly(day)
    except Exception as e:
        rep = {"date": day.isoformat(), "verdict": "UNKNOWN",
               "why": f"forecast unreachable: {e}", "hours": []}
        print(json.dumps(rep, indent=1) if a.json
              else f"{day} UNKNOWN — {rep['why']}")
        return 2
    g = gusts(day)
    rep = {"date": day.isoformat(), "source": HOURLY,
           **verdict(rows, g), "hours": rows}
    for r in rows:
        r["gust_mph"] = g.get(r["hour"])
    if a.json:
        print(json.dumps(rep, indent=1))
        return 0
    print(f"{day}  {rep['verdict']}  — {rep['why']}")
    print(f"venue: {rep['venue_hint']}   (forecast, not a measurement)")
    print(" hour  mph  gust  dir  sky")
    for r in rows:
        gs = f"{r['gust_mph']:>4g}" if r["gust_mph"] else "   -"
        print(f" {r['hour']:02d}   {r['mph']:>3g}  {gs}  {r['dir']:<3}  {r['sky']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
