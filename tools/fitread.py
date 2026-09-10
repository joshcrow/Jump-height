#!/usr/bin/env python3
"""fitread.py — read a Garmin FIT the way this project needs to read one.

WHY THIS EXISTS
---------------
Nothing in this repo reads a FIT file. Everything it knows about the watch
side of a session was obtained by hand: the 2026-08-27 analysis of Nick's
eight rides and the 2026-09-06 beach session both ended as numbers a person
typed into a markdown table after running `fitdecode` at a prompt. Those
numbers cannot be re-derived without redoing the work, and one of them was
wrong for a day — `data/nick-sessions/notes.md` records that the
caption-to-file pairing was inverted and had to be corrected.

The rider's data now arrives as a Garmin Connect export — a zip holding one
`<id>_ACTIVITY.fit` — and it arrives repeatedly. This turns that zip into
one screen of facts, and with `--out` into two files a script can read.

WHAT IT REFUSES TO DO
---------------------
Print a number it did not read. A field the file does not carry prints
`absent`, never `0` (CLAUDE.md §2.3: a reading that did not happen is a
finding, never a pass). This is not pedantry here — it is the one thing the
tool is for. Nick's watch records the profile `Wing Foil` as sport
`generic` / sub_sport `track_me` and carries **no developer fields at all**,
so `jumps`, `best_jump`, `best_airtime` and `jump_height` are absent from
every one of his eight rides. The owner's Epix carries all four. A tool that
printed `jumps: 0` for Nick would be reporting the Jump Height data field as
having found nothing, when in fact it never ran.

TIME ON FOIL
------------
`docs/STATUS.md` (2026-09-06) says: "`enhanced_speed` above 2.5 m/s spans
14:08:49-14:09:13, **24 contiguous seconds and the only such window in 47
min**". This tool applies that same 2.5 m/s threshold to the `record`
messages and reports **17 s** for that flight, not 24 — the sample at
14:08:54 reads 2.482 m/s and splits the span into 14:08:49-14:08:51 (2 s,
under the 10 s floor) and 14:08:56-14:09:13 (17 s, kept). Same heuristic,
applied strictly to what is in the file. It is one window either way, and
still the only one.

A window also ends at any gap longer than `MAX_GAP_S` between consecutive
records. Garmin smart-recording leaves gaps up to 15 s in the nine files on
hand, but a *paused* activity leaves gaps up to 3,989 s (`23892159354`), and
no second inside a pause was measured above anything.

UNITS
-----
Columns are named exactly as the FIT names its fields, and carry the FIT's
own units, with one exception: `position_lat` / `position_long` are stored
as semicircles, so the CSV converts them and renames them
`position_lat_deg` / `position_long_deg`. The rule is: renamed if and only
if converted. `enhanced_speed` is m/s and `jump_height` is ft (the units the
developer field itself declares) — untouched, so unrenamed.

Usage:
    ./tools/fitread.py <file.fit | garmin-connect-export.zip> [--out DIR]

    --out DIR writes `fit-summary.json` (every number on the screen) and
    `fit-records.csv` (one row per `record` message).

Exit status: 0 on a file that was read, 2 on one that could not be.
"""
import argparse
import csv
import datetime
import io
import json
import sys
import zipfile
from pathlib import Path

try:
    import fitdecode
except ImportError:  # pragma: no cover - environment problem, not a data problem
    sys.exit("fitdecode is not installed — `pip3 install fitdecode`")  # noqa: TRY003

from fitdecode.types import DevField

# docs/STATUS.md 2026-09-06: "enhanced_speed above 2.5 m/s ... 24 contiguous
# seconds and the only such window in 47 min" — the time-on-foil heuristic.
FOIL_SPEED_MS = 2.5
FOIL_MIN_WINDOW_S = 10.0
# A window cannot bridge a recording gap. Smart recording gaps top out at 15 s
# across data/nick-sessions/fits/ and data/fit/; activity pauses reach 3,989 s.
MAX_GAP_S = 30.0

SEMICIRCLE_TO_DEG = 180.0 / (2 ** 31)

ABSENT = "absent"

# Exit 2 means "this input could not be read", and the reason is on stderr.
# It is deliberately not 1: a caller looping over an inbox needs to tell a
# rejected file apart from a crash.
EXIT_BAD_INPUT = 2


def die(reason: str):
    print(reason, file=sys.stderr)
    sys.exit(EXIT_BAD_INPUT)


# --------------------------------------------------------------------------
# input


def read_fit_bytes(path: Path) -> tuple[bytes, str]:
    """Return (FIT payload, label) from a .fit, or from a Garmin Connect .zip.

    The label names the zip AND the member actually read, so a summary can
    never be traced back to the wrong export.

    Exits 2 with the reason on anything that is not exactly one activity.
    """
    if not path.exists():
        die(f"{path} does not exist")
    suffix = path.suffix.lower()
    if suffix == ".fit":
        return path.read_bytes(), path.name
    if suffix != ".zip":
        die(f"{path.name}: expected a .fit or a Garmin Connect .zip, got '{suffix or 'no extension'}'")
    try:
        with zipfile.ZipFile(path) as zf:
            names = [
                n for n in zf.namelist()
                if n.upper().endswith("_ACTIVITY.FIT") and not n.startswith("__MACOSX/")
            ]
            if len(names) != 1:
                found = ", ".join(names) if names else "none"
                die(
                    f"{path.name}: expected exactly one *_ACTIVITY.fit in the zip, "
                    f"found {len(names)} ({found})"
                )
            return zf.read(names[0]), f"{path.name} ({names[0]})"
    except zipfile.BadZipFile as exc:
        die(f"{path.name}: not a readable zip ({exc})")


# --------------------------------------------------------------------------
# scan


def _as_dict(frame):
    return {fld.name: fld.value for fld in frame.fields}


def integrity_warnings(session_count: int, records: list, stamped: list) -> list[str]:
    """Things about the file that make the numbers below mean less than they look.

    Kept separate from `scan` so it can be tested without a FIT that has the
    defect — none of the nine files on hand is multi-sport or has an unstamped
    record, and a guard that has never run is not a guard.
    """
    out = []
    if session_count > 1:
        out.append(f"{session_count} session messages (multi-sport file); every "
                   "SESSION number below is the FIRST session only")
    unstamped = len(records) - len(stamped)
    if unstamped:
        out.append(f"{unstamped} record message(s) carried no timestamp and are "
                   "excluded from timing")
    backsteps = sum(1 for a, b in zip(stamped, stamped[1:])
                    if b["timestamp"] < a["timestamp"])
    if backsteps:
        out.append(f"{backsteps} record timestamp(s) go backwards; windows assume "
                   "file order")
    return out


def scan(blob: bytes, source_name: str) -> dict:
    """One pass over the FIT. Returns everything the screen and the JSON need."""
    session = None
    session_count = 0
    activity = None
    sport_msg = None
    file_id = None
    dev_decls = {}       # (dev_data_index, field_definition_number) -> declaration
    dev_counts = {}      # same key -> {"non_null": int, "messages": {name: int}}
    records = []
    laps = []
    warnings = []

    reader = fitdecode.FitReader(io.BytesIO(blob))
    try:
        for frame in reader:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue

            if frame.name == "field_description":
                d = _as_dict(frame)
                key = (d.get("developer_data_index"), d.get("field_definition_number"))
                dev_decls[key] = {
                    "name": d.get("field_name"),
                    "units": d.get("units"),
                    "native_mesg_num": d.get("native_mesg_num"),
                }
                dev_counts.setdefault(key, {"non_null": 0, "messages": {}})

            # Count developer-field values wherever they appear, not only in the
            # messages we happen to look at by name.
            for fld in frame.fields:
                if isinstance(fld.field, DevField):
                    key = (fld.field.dev_data_index, fld.field.def_num)
                    slot = dev_counts.setdefault(key, {"non_null": 0, "messages": {}})
                    slot["messages"][frame.name] = slot["messages"].get(frame.name, 0) + 1
                    if fld.value is not None:
                        slot["non_null"] += 1
                    dev_decls.setdefault(key, {
                        "name": fld.name,
                        "units": fld.units,
                        "native_mesg_num": None,
                    })

            if frame.name == "session":
                session_count += 1
                if session is None:
                    session = _as_dict(frame)
            elif frame.name == "activity" and activity is None:
                activity = _as_dict(frame)
            elif frame.name == "sport" and sport_msg is None:
                sport_msg = _as_dict(frame)
            elif frame.name == "file_id" and file_id is None:
                file_id = _as_dict(frame)
            elif frame.name == "lap":
                # THE GROUND-TRUTH CHANNEL. A rider pressing the lap button
                # after each jump stamps the FIT with a wall-clock time to the
                # second, using gear he already wears and no app. On the
                # 2026-09-09 ride nobody pressed it and the trace could only be
                # anchored to +-3.5 min by inference (docs/audit F-33/F-35);
                # one press per jump replaces all of that with a measurement.
                d = _as_dict(frame)
                laps.append({
                    "start_time": d.get("start_time"),
                    "timestamp": d.get("timestamp"),
                    "total_elapsed_time": d.get("total_elapsed_time"),
                    "trigger": d.get("lap_trigger"),
                })
            elif frame.name == "record":
                d = _as_dict(frame)
                records.append({
                    "timestamp": d.get("timestamp"),
                    "enhanced_speed": d.get("enhanced_speed"),
                    "jump_height": d.get("jump_height"),
                    "position_lat": d.get("position_lat"),
                    "position_long": d.get("position_long"),
                })
    except Exception as exc:  # a truncated or corrupt FIT
        die(f"{source_name}: FIT decode failed after {len(records)} record(s): {exc}")

    stamped = [r for r in records if r["timestamp"] is not None]
    warnings += integrity_warnings(session_count, records, stamped)

    return {
        "source": source_name,
        "laps": laps,
        "session": session,
        "activity": activity,
        "sport_msg": sport_msg,
        "file_id": file_id,
        "dev_decls": dev_decls,
        "dev_counts": dev_counts,
        "records": records,
        "stamped": stamped,
        "warnings": warnings,
    }


# --------------------------------------------------------------------------
# derive


def utc_offset(activity: dict | None) -> datetime.timedelta | None:
    """The activity's local-time offset, from `local_timestamp` - `timestamp`.

    fitdecode hands back `local_timestamp` tagged UTC, which it is not; the
    difference of the two naive values is the offset the watch was wearing.
    """
    if not activity:
        return None
    local = activity.get("local_timestamp")
    utc = activity.get("timestamp")
    if local is None or utc is None:
        return None
    delta = local.replace(tzinfo=None) - utc.replace(tzinfo=None)
    # Garmin writes whole seconds; offsets are whole minutes.
    return datetime.timedelta(minutes=round(delta.total_seconds() / 60.0))


def fmt_offset(off: datetime.timedelta | None) -> str:
    if off is None:
        return ABSENT
    total = int(off.total_seconds())
    sign = "-" if total < 0 else "+"
    total = abs(total)
    return f"{sign}{total // 3600:02d}:{(total % 3600) // 60:02d}"


def fmt_utc(dt: datetime.datetime | None) -> str:
    if dt is None:
        return ABSENT
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def fmt_local(dt: datetime.datetime | None, off: datetime.timedelta | None) -> str:
    if dt is None or off is None:
        return ABSENT
    return (dt.replace(tzinfo=None) + off).strftime("%Y-%m-%d %H:%M:%S") + " " + fmt_offset(off)


def fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return ABSENT
    s = int(round(seconds))
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def foil_windows(stamped: list[dict]) -> list[dict]:
    """Contiguous runs of enhanced_speed > FOIL_SPEED_MS lasting >= 10 s.

    A run is a maximal sequence of consecutive records that are all above the
    threshold and are never more than MAX_GAP_S apart. Its duration is
    last - first: the seconds between two measured samples, not an
    extrapolation past either end.
    """
    runs = []
    cur = None
    prev = None
    for rec in stamped:
        speed = rec["enhanced_speed"]
        t = rec["timestamp"]
        above = speed is not None and speed > FOIL_SPEED_MS
        if above and cur is not None and (t - prev).total_seconds() > MAX_GAP_S:
            runs.append(cur)
            cur = None
        if above:
            if cur is None:
                cur = {"start": t, "end": t}
            else:
                cur["end"] = t
        elif cur is not None:
            runs.append(cur)
            cur = None
        prev = t
    if cur is not None:
        runs.append(cur)

    kept = []
    for run in runs:
        dur = (run["end"] - run["start"]).total_seconds()
        if dur >= FOIL_MIN_WINDOW_S:
            kept.append({
                "start_utc": run["start"].astimezone(datetime.timezone.utc).isoformat(),
                "end_utc": run["end"].astimezone(datetime.timezone.utc).isoformat(),
                "seconds": dur,
            })
    return kept


def summarise(scanned: dict) -> dict:
    session = scanned["session"] or {}
    sport_msg = scanned["sport_msg"] or {}
    stamped = scanned["stamped"]

    profile = session.get("sport_profile_name") or sport_msg.get("name")
    sport = session.get("sport") or sport_msg.get("sport")
    sub_sport = session.get("sub_sport") or sport_msg.get("sub_sport")

    start = session.get("start_time")
    if start is None and stamped:
        start = stamped[0]["timestamp"]
    # session.timestamp is NOT the end on these Garmin files — on all nine it
    # equals start_time. The last record is the only measured end.
    end = stamped[-1]["timestamp"] if stamped else None
    span = (end - start).total_seconds() if (start and end) else None

    off = utc_offset(scanned["activity"])

    speeds = [r["enhanced_speed"] for r in scanned["records"] if r["enhanced_speed"] is not None]
    max_speed = max(speeds) if speeds else None

    dev = []
    for key, decl in sorted(scanned["dev_decls"].items(), key=lambda kv: (kv[0][0] or 0, kv[0][1] or 0)):
        counts = scanned["dev_counts"].get(key, {"non_null": 0, "messages": {}})
        dev.append({
            "name": decl["name"],
            "units": decl["units"],
            "native_mesg_num": decl["native_mesg_num"],
            "non_null_values": counts["non_null"],
            "seen_in_messages": counts["messages"],
        })

    # With no `record` messages — or no `enhanced_speed` in any of them, as an
    # older watch that writes only `speed` would give — there is nothing to
    # threshold, and "0 windows, 0 s" would read as a measured absence of
    # foiling rather than an absence of data. Report it absent (CLAUDE.md §2.3).
    if stamped and max_speed is None:
        scanned["warnings"].append(
            "no record carries enhanced_speed — time on foil could not be computed")
    if stamped and max_speed is not None:
        windows = foil_windows(stamped)
        total_foil = sum(w["seconds"] for w in windows)
    else:
        windows, total_foil = None, None

    file_id = scanned["file_id"] or {}

    return {
        "source": scanned["source"],
        # Not on the screen, but the one field that says WHOSE watch this is:
        # `instinct3_solar_45mm` is the rider's, `epix_gen2` the owner's.
        "device": {
            "manufacturer": file_id.get("manufacturer"),
            "product": file_id.get("garmin_product") or file_id.get("product"),
            "serial_number": file_id.get("serial_number"),
        },
        "profile_name": profile,
        "sport": sport,
        "sub_sport": sub_sport,
        "start_utc": start.astimezone(datetime.timezone.utc).isoformat() if start else None,
        "end_utc": end.astimezone(datetime.timezone.utc).isoformat() if end else None,
        "utc_offset": fmt_offset(off) if off is not None else None,
        "start_local": fmt_local(start, off) if off is not None else None,
        "end_local": fmt_local(end, off) if off is not None else None,
        "duration_s": span,
        "session_total_elapsed_time_s": session.get("total_elapsed_time"),
        "record_count": len(scanned["records"]),
        # A lap the RIDER pressed is ground truth; the one Garmin writes to
        # close the activity is not. Distinguish them by trigger:
        # lap_trigger "manual" is a button press, "session_end" is the wrap-up.
        "laps": [
            {
                "start_utc": (l["start_time"].astimezone(datetime.timezone.utc).isoformat()
                              if l.get("start_time") else None),
                "end_utc": (l["timestamp"].astimezone(datetime.timezone.utc).isoformat()
                            if l.get("timestamp") else None),
                "elapsed_s": l.get("total_elapsed_time"),
                "trigger": str(l.get("trigger")) if l.get("trigger") is not None else None,
            }
            for l in scanned.get("laps") or []
        ],
        "manual_laps": sum(1 for l in (scanned.get("laps") or []) if str(l.get("trigger")) == "manual"),
        "developer_fields_present": bool(dev),
        "developer_fields": dev,
        "session_jumps": session.get("jumps"),
        "session_best_jump": session.get("best_jump"),
        "session_best_jump_units": _units_of(scanned, "best_jump"),
        "session_best_airtime": session.get("best_airtime"),
        "max_enhanced_speed_ms": max_speed,
        "session_enhanced_max_speed_ms": session.get("enhanced_max_speed"),
        "foil": {
            "speed_threshold_ms": FOIL_SPEED_MS,
            "min_window_s": FOIL_MIN_WINDOW_S,
            "max_gap_s": MAX_GAP_S,
            "window_count": len(windows) if windows is not None else None,
            "total_seconds": total_foil,
            "percent_of_duration": (100.0 * total_foil / span) if (span and total_foil is not None) else None,
            "windows": windows,
        },
        "warnings": scanned["warnings"],
    }


def _units_of(scanned: dict, name: str):
    for decl in scanned["dev_decls"].values():
        if decl["name"] == name:
            return decl["units"]
    return None


# --------------------------------------------------------------------------
# output


def val(x, unit: str = "", fmt: str = "{}") -> str:
    """A number, or the word `absent`. Never a zero standing in for a silence."""
    if x is None:
        return ABSENT
    s = fmt.format(x)
    return f"{s} {unit}".strip()


def render(s: dict) -> str:
    out = []
    w = 22
    def row(label, value):
        out.append(f"  {label:<{w}}{value}")

    out.append(s["source"])
    out.append("")
    row("profile", s["profile_name"] or ABSENT)
    row("sport / sub_sport", f"{s['sport'] or ABSENT} / {s['sub_sport'] or ABSENT}")
    for label, iso, local in (("start", s["start_utc"], s["start_local"]),
                              ("end", s["end_utc"], s["end_local"])):
        row(label, ABSENT if iso is None
            else f"{fmt_utc(_dt(iso))} UTC   {local or ABSENT}")
    row("duration", ABSENT if s["duration_s"] is None
        else f"{fmt_duration(s['duration_s'])}  ({s['duration_s']:.0f} s, first to last record)")
    row("records", str(s["record_count"]))
    nlap = len(s.get("laps") or [])
    nman = s.get("manual_laps", 0)
    if nman:
        row("rider lap presses", f"{nman}  <- ground truth, one per press")
        for i, l in enumerate([l for l in (s.get("laps") or [])
                               if l.get("trigger") == "manual"], 1):
            row(f"  press {i}", l.get("end_utc") or "?")
    else:
        row("rider lap presses", f"none  ({nlap} lap message(s), all automatic)")

    out.append("")
    if not s["developer_fields_present"]:
        row("developer fields", f"{ABSENT} — no field_description in this file")
    else:
        row("developer fields", f"present ({len(s['developer_fields'])})")
        for f in s["developer_fields"]:
            where = ", ".join(f"{k} x{v}" for k, v in sorted(f["seen_in_messages"].items())) or "declared, never carried"
            units = f["units"] or "-"
            out.append(f"    {f['name']:<16}{units:<8}{f['non_null_values']:>6} non-null   [{where}]")

    out.append("")
    row("session jumps", val(s["session_jumps"]))
    row("session best_jump", val(s["session_best_jump"], s["session_best_jump_units"] or "", "{:.3f}"))
    row("session best_airtime", val(s["session_best_airtime"], "s", "{:.2f}"))
    row("max enhanced_speed", val(s["max_enhanced_speed_ms"], "m/s", "{:.3f}")
        + f"   (session says {val(s['session_enhanced_max_speed_ms'], 'm/s', '{:.3f}')})")

    f = s["foil"]
    out.append("")
    row("time on foil", f"enhanced_speed > {f['speed_threshold_ms']} m/s, contiguous >= "
        f"{f['min_window_s']:.0f} s, gap cap {f['max_gap_s']:.0f} s")
    row("  windows", val(f["window_count"]))
    pct = f"  ({f['percent_of_duration']:.2f} % of duration)" if f["percent_of_duration"] is not None else ""
    row("  total", val(f["total_seconds"], "s", "{:.0f}") + pct)

    for warn in s["warnings"]:
        out.append(f"  ! {warn}")
    return "\n".join(out)


def _dt(iso: str | None):
    return datetime.datetime.fromisoformat(iso) if iso else None


def write_out(outdir: Path, summary: dict, records: list[dict]) -> list[Path]:
    outdir.mkdir(parents=True, exist_ok=True)

    has_jump_height = any(r["jump_height"] is not None for r in records)
    cols = ["timestamp_utc", "enhanced_speed"]
    if has_jump_height:
        cols.append("jump_height")
    cols += ["position_lat_deg", "position_long_deg"]

    summary = dict(summary)
    summary["csv_columns"] = cols
    summary["csv_column_units"] = {
        "timestamp_utc": "ISO 8601 UTC",
        "enhanced_speed": "m/s",
        "jump_height": _units_from_summary(summary, "jump_height"),
        "position_lat_deg": "degrees (FIT position_lat, semicircles, converted)",
        "position_long_deg": "degrees (FIT position_long, semicircles, converted)",
    }

    jpath = outdir / "fit-summary.json"
    jpath.write_text(json.dumps(summary, indent=2, sort_keys=False) + "\n")

    cpath = outdir / "fit-records.csv"
    with cpath.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(cols)
        for r in records:
            ts = r["timestamp"]
            row = [
                ts.astimezone(datetime.timezone.utc).isoformat() if ts else "",
                "" if r["enhanced_speed"] is None else r["enhanced_speed"],
            ]
            if has_jump_height:
                row.append("" if r["jump_height"] is None else r["jump_height"])
            row.append("" if r["position_lat"] is None else r["position_lat"] * SEMICIRCLE_TO_DEG)
            row.append("" if r["position_long"] is None else r["position_long"] * SEMICIRCLE_TO_DEG)
            writer.writerow(row)
    return [jpath, cpath]


def _units_from_summary(summary: dict, name: str):
    for f in summary.get("developer_fields", []):
        if f["name"] == name:
            return f["units"]
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Read a Garmin FIT (or a Garmin Connect zip) and print what it actually contains.")
    ap.add_argument("path", type=Path, help="a .fit, or a Garmin Connect export .zip")
    ap.add_argument("--out", type=Path, metavar="DIR",
                    help="write fit-summary.json and fit-records.csv into DIR")
    args = ap.parse_args(argv)

    blob, label = read_fit_bytes(args.path)
    scanned = scan(blob, label)
    summary = summarise(scanned)
    print(render(summary))

    if args.out:
        written = write_out(args.out, summary, scanned["records"])
        print("")
        for p in written:
            print(f"  wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
