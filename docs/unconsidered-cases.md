# The 30 unconsidered cases — and what addressing them actually costs

Produced 2026-08-21 by six independent lenses (the rider's day, the quiver,
the physical world, detector physics, data over months, other people) plus an
adversary hunting the seams between them. Owner's question afterwards: *"what
does the work to address the 30 look like."*

**Answer up front: it is not 30 pieces of work.** The cases cluster into six
themes, two of which are decisions rather than engineering, one of which is
already fixed, and two of which the adversary says to deliberately NOT build.
The genuinely new code is about a day.

---

## 1. What the 30 collapse into

| cluster | cases | what it actually is |
|---|---|---|
| **Per-board identity & calibration** | 8 | Mostly already designed ([puck-identity.md](puck-identity.md)). The NEW part is calibration being per-board, not per-project. |
| **Detector false-positives** | 8 | Mostly answered by the water day itself — but it needs a deliberate "what motion is NOT a jump" list, and labels for it. |
| **Charging cadence & who owns it** | 6 | **A decision between two people.** Not code. |
| **Staleness / health on the watch** | 5 | One small watch feature. |
| **Diagnostic honesty (provenance)** | 3 | One selftest row. |
| **Capsule reseal ritual** | 2 | A card zip-tied to the case. |
| *already fixed* | 1 | monotonic STATS reseed (`329c543`) |
| *do not build* | 2 | adversary rejected them — see §5 |

## 2. The work, in the order it should happen

### Now, before the water day — hours, not days

1. **Charging: name an owner and a cadence, in writing.** The single most
   dangerous scenario the adversary found starts with "the puck was last
   charged nine days ago". The honest cadence today — with no standby, no
   auto-off and no low-battery cutoff — is **charge it the night before every
   ride**, not "occasionally". And the person who can open the sealed case is
   not the person who has the board. This is a five-minute conversation that
   removes a session-ruining failure; no code will do it for you.
2. **Selftest row: fail when calibration came from defaults.** One
   `from_store` read (`jh_persist::load` already returns it; nothing in the
   tree passes it). This would have caught, the day it happened, that the
   OG's measured calibration was silently replaced by the compiled default
   after the battery death. Catches the whole "provenance" cluster.
3. **Rider-brief + session-card additions.** The brief currently tells the
   rider to ignore *every* anomaly uniformly, which is right for a 10-second
   dropout and wrong for a 20-minute one. Add: what a long dropout means,
   a low-battery line, and "reboot the puck before starting the activity".
4. **A card physically attached to the capsule** with the reseal ritual
   (dunk-test after every opening). A decision in `DECISIONS.md` will not be
   there in month four; a zip-tied card will.

### With the water day — costs nothing extra, must not be forgotten

5. **Label the non-jump motion.** The day already collects labels for
   time-on-foil. Add the negative cases while everyone is there: carrying the
   board up the beach, the drive home with it on the roof, dropping it on
   sand, the board bobbing in the shallows. These are the false-positive
   corpus, they are free on the day, and they are unobtainable afterwards.
6. **Sync between sessions, not just at the end.** The trace is a rolling
   ~5 h window; a second session can evict the first day's raw data.

### Era 2 — already planned, these cases just sharpen it

7. **Per-board identity** ([puck-identity.md](puck-identity.md)) — header id,
   bind-and-prefer, one central. Already scoped.
8. **Per-board calibration.** New, and it rides on (7): once the watch knows
   which puck it is talking to, per-board `airtime_offset_s` becomes
   addressable. Today one board's constant is applied to all three.
9. **Duration-aware staleness on the watch.** `Model.mc` already stores
   `_staleSinceMs` and never reads it — its own comment says it is kept "for
   a future stale-for-N-seconds readout". Turn a static "reconnecting" into
   an honest "no data — 12 min". Same calm dot, no alarm.
10. **Storage/diagnostic surfacing** — the `NO REC` row exists only on the
    full layout tier; a rider on a crowded data screen never sees it.

## 3. What is a decision, not work

- **Charging ownership and cadence** (§2.1).
- **Glue vs removable** — already open in
  [glue-and-forget.md](glue-and-forget.md) §2, and several of these cases
  (travel, selling a board, reseal risk, charging access) resolve differently
  depending on it.
- **Whether a third rider is ever in scope.** If no, several quiver cases
  stay theoretical.

## 4. What this does NOT change

Nothing here blocks the water day. One rider, one puck, one watch, one
session is unaffected by every case in the list — which is the reassuring
part: the failures cluster in *repetition*, *multiplicity* and *time*, none
of which the water day exercises.

## 5. Deliberately not building (adversary's verdict, kept so it is not
re-proposed)

- **Shock isolation for the puck.** Real board-sport landings are 2.7–5.5 g;
  the parts are qualified in the thousands. Worse, a compliant mount would
  mechanically low-pass the landing spike the detector triggers on — it would
  corrupt the measurement to protect against a non-problem. Instead, when the
  capsule is next open: foam-pack the cell so it cannot move, add a
  strain-relief loop on the pigtail, photograph the pack for comparison.
- **"Board B steals the link mid-ride."** Fails on geometry — B is 100 m away
  across water. The same hazard is real through a different door (the walk
  back up the beach with the activity running), and the monotonic-reseed fix
  already covers both.

## 6. The honest summary

Six clusters. Two are conversations. One is already fixed. Two are already on
the Era-2 plan and are now better specified. The genuinely new engineering is
a selftest row, a staleness readout, and per-board calibration — call it a
day's work, none of it urgent, none of it blocking the water.

The valuable output was not a backlog. It was learning that **the failures
live in repetition and multiplicity**, not in the single session everyone has
been designing for.
