#!/usr/bin/env python3
"""label.py — turn human notes into labels.csv, using the session's own clock.

WHY THIS EXISTS
---------------
`sim/evaluate.py` has always scored sessions against `labels.csv`, and this
project has never had one — not a single labelled session, because labelling
was impossible in practice. The puck has no RTC, so trace time is
seconds-since-boot, and a note saying "14:32, three tosses" could not be
matched to anything in the data.

`jump sync` now writes `session.json` with `trace_epoch_utc` — the real-world
instant that trace time zero corresponds to. That makes wall-clock notes
convertible, so this reads notes the way a human actually writes them and
emits the schema the scorer already expects.

NOTES FORMAT (one per line; blank lines and #comments ignored)
--------------------------------------------------------------
    14:32-14:51  none      walking to the shops
    15:05        jump x3   deliberate flat tosses over a cushion
    15:20-15:40  none      driving
    16:02        jump      off the bottom step

  - time      : HH:MM or HH:MM:SS, local; or a HH:MM-HH:MM range
  - kind      : `jump`, `jump xN`, `none` (an explicit "nothing here", which
                is what makes a false-positive rate measurable), or a RIDING
                STATE — `foiling` / `notfoiling` / `riding` / `crash`.
                The riding states exist for the metrics roadmap
                (docs/future-metrics.md): time-on-foil is a CLASSIFICATION
                problem, and a classifier needs labelled regions. They cost
                nothing to write down during a session and cannot be
                recovered afterwards from memory.
  - the rest  : free-text notes

Ranges become one row spanning the period; instants become a row at that
second. `none` rows are written as `event=none`, which the scorer keeps and
reports but does not score as a jump — exactly what we want them for.

Usage:
    python3 tools/label.py <session-dir> <notes.txt> [--tz-offset-min N]
"""
import argparse
import csv
import datetime
import json
import re
import sys
from pathlib import Path


def load_epoch(session: Path) -> datetime.datetime:
    sj = session / "session.json"
    if not sj.exists():
        sys.exit(f"{sj} not found — re-run `jump sync` (it writes the clock anchor)")
    blob = json.loads(sj.read_text())
    ep = blob.get("trace_epoch_utc")
    if not ep:
        sys.exit("session.json has no trace_epoch_utc — the device predates `uptime_s`")
    return datetime.datetime.fromisoformat(ep)


def parse_clock(s: str, day: datetime.date) -> datetime.datetime:
    parts = [int(x) for x in s.split(":")]
    while len(parts) < 3:
        parts.append(0)
    return datetime.datetime(day.year, day.month, day.day, *parts,
                             tzinfo=datetime.datetime.now().astimezone().tzinfo)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("session")
    ap.add_argument("notes")
    args = ap.parse_args()

    session = Path(args.session)
    epoch = load_epoch(session)
    day = epoch.astimezone().date()

    rows = []
    for raw in Path(args.notes).read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^(\d{1,2}:\d{2}(?::\d{2})?)(?:\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?))?\s+(\S+)(?:\s+(.*))?$",
                     line)
        if not m:
            print(f"  ! skipped (unparseable): {raw}", file=sys.stderr)
            continue
        t0s, t1s, kind, note = m.group(1), m.group(2), m.group(3).lower(), (m.group(4) or "")

        # "jump x3" arrives as kind='jump' + note starting 'x3'
        count = 1
        cm = re.match(r"^x(\d+)\b\s*(.*)$", note)
        if cm:
            count, note = int(cm.group(1)), cm.group(2)

        start = (parse_clock(t0s, day) - epoch).total_seconds()
        end = (parse_clock(t1s, day) - epoch).total_seconds() if t1s else ""
        if start < 0:
            print(f"  ! skipped (before the session started): {raw}", file=sys.stderr)
            continue

        # Riding-state regions ride the same schema: they are not jumps, so
        # they never score as detections, but they carry their own label so a
        # future classifier has ground truth. Anything unrecognised still
        # lands as `none` (the conservative default that only ever measures
        # false positives).
        RIDING = ("foiling", "notfoiling", "riding", "crash")
        if kind.startswith("jump"):
            event = "jump"
        elif kind in RIDING:
            event = kind
        else:
            event = "none"
        for _ in range(count if event == "jump" else 1):
            rows.append({
                "event": event,
                "t_start_s": f"{start:.3f}",
                "t_end_s": f"{end:.3f}" if end != "" else "",
                "height_m": "",          # video-derived truth, filled in later
                "rotation_deg": "",
                "landing": "",
                "notes": note.strip(),
            })

    out = session / "labels.csv"
    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["event", "t_start_s", "t_end_s", "height_m",
                                          "rotation_deg", "landing", "notes"])
        w.writeheader()
        w.writerows(rows)
    import collections as _c
    kinds = _c.Counter(r["event"] for r in rows)
    jumps = kinds.get("jump", 0)
    summary = ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))
    print(f"wrote {out}  —  {len(rows)} rows ({summary})")
    if jumps:
        print()
        print("  NOTE ON WHAT THESE LABELS CAN AND CANNOT DO")
        print("  sim/evaluate.py matches a detection to a label only if the takeoff")
        print("  is within MATCH_WINDOW_S = 1.0 s. A time written down by a human")
        print("  from a phone screen is typically tens of seconds out, so:")
        print()
        print("   * `none` regions are the valuable part here. They measure the")
        print("     FALSE-POSITIVE rate — 'nothing happened in this window' — and")
        print("     coarse timing is completely fine for that. This is the dataset")
        print("     the project has never had.")
        print("   * `jump` rows will NOT reliably match unless the time is accurate")
        print("     to about a second. For real accuracy scoring the timing has to")
        print("     come from video, which is exactly what the water session is for.")
        print("     Treat jump rows here as 'roughly N jumps happened around here'.")
    print(f"trace epoch: {epoch.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("Next: ./tools/jump eval   (heights stay blank until video truth exists)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
