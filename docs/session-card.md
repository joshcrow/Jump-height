# Water session card — print this, take it to the beach

One page. One shot. Everything below exists because of a specific way this
session could be wasted; the reasons are in [data-pipeline.md](data-pipeline.md)
and [plan.md](plan.md), not here.

---

## Which puck are you taking?

**Only the OG (`JumpHeight-E2C4`) has a battery** — it is the only board that
can run untethered, so it is the only board that can go to the water. The
spare (`JumpHeight-45ED`) is USB-only and its battery readout is meaningless.
Confirm with `./tools/jump info` and check the advertised name, not the case.
Full registry: [bench-playbook.md §1](bench-playbook.md).

## Before you leave the house

- [ ] `./tools/jump selftest` — every row PASS, **and** it says
      `✅ device is running THIS source tree`. If it names two different
      hashes, you are about to measure a build you did not intend to.
- [ ] **No `⚠️ CALIBRATION PROVENANCE` warning.** If it appears, this board's
      measured drop calibration has been replaced by compiled defaults (a
      battery death can do this silently — it happened to the OG on
      2026-08-19). Re-run the drop ritual before trusting a single height.
- [ ] `./tools/jump sync` the previous session, **copy it twice**, then
      `clear`. Never clear anything that exists in only one place.
- [ ] Battery ~100 %. Budget ~21 h of recording; a session is ~2 h.
- [ ] Puck mounted **≥24 h ago** (the mount needs a full cure).
- [ ] **With the puck mounted, check you can still reach the reset
      button.** Press it once now, before the day, not for the first time
      at the beach. If the mount or capsule blocks it, fix that now — see
      the reboot ritual below for why this matters.
- [ ] Capsule bucket-tested, loaded, and floating.
- [ ] Camera: **1080p/120**. Not 4K/30. Check it is actually set.
- [ ] Phone or paper for notes, with the real time visible.

## Powering on (the ritual, in order)

1. **Reboot the puck — even if it already looks on and connected.**
   There is no single "reboot" command. Use one of these:
   - **Press the physical reset button on the board.** Always works,
     needs no laptop, and is the only option once you're away from the
     bench. (This is why you checked it's reachable, above.)
   - Or, if it's still on USB at the bench: send it `off` (it powers
     down cleanly), then unplug and replug the cable to wake it.
   Do this right before you start today's activity, **every time** —
   including if it was already sitting there powered on from an earlier
   test. Why this matters: [glue-and-forget.md §3b](glue-and-forget.md).
   Skipping it has already put someone else's desk tosses from hours
   earlier into a real ride's saved data.
2. **Sync marker: three deliberate flat drops of the board onto something
   soft, ~2 s apart.** Not a finger tap — a tap is 2-5 ms and the stored
   trace is 50 Hz, so it can be absent from the data entirely.
3. **Write down the wall-clock time, to the second.** Trace time is seconds
   since boot; this is the only thing that ties it to the real world.
4. Confirm the watch shows the puck connected before anyone gets wet.

## In the water — the two things that decide whether this works

**1. Camera roughly ABEAM of the jump line.** Not behind, not head-on. The
height measurement is a comparison against the rider's own height in frame,
so the rider has to be side-on and full-height at apex.

**2. Get the whole rider in frame at apex.** If the rider is cropped, that
jump has no height truth. Better to shoot wide and lose detail.

Then just ride. The puck stores everything; nothing depends on the watch
staying connected.

## Notes to take (ranges, not instants)

Write segments: `14:32-14:51 riding`, `15:05-15:12 nothing, sat on the board`,
`15:20 big one, felt like the highest`.

Two hands-free ways to do this: the web app's **Label tab** (load the page
before leaving — it works offline; tap Jump / Start-End quiet as things
happen, export `notes.txt` after) or just **message Claude** as events happen —
messages are timestamped and become labels the same way.

- **"Nothing happened here" is as valuable as "3 jumps here."** It is the
  false-positive rate, and there is no other way to get it.
- **Note when you were ON THE FOIL vs not** — `14:00-14:20 foiling`,
  `14:20-14:25 notfoiling`. This is the training data for time-on-foil and
  every riding metric after it (docs/future-metrics.md). It costs a line in
  your notes during the session and **cannot be recovered afterwards** — no
  amount of offline cleverness reconstructs what you were doing at 14:07.
  `tools/label.py` understands `foiling` / `notfoiling` / `riding` / `crash`.
- Note anything odd: a crash, a dropped board, a swim, a knock on the rocks.
- Don't try to time individual jumps by hand — you cannot get within the 1 s
  matching window from a phone screen, and video is what supplies per-jump
  timing anyway.

## Coming off the water

1. **Repeat the sync marker** — three flat drops.
2. Note the wall-clock time again.
3. Leave it running until it is plugged into the laptop. Do not `off`, do not
   `clear`, do not open the capsule in the wind.
4. `./tools/jump sync` → **copy the session folder somewhere else** → only
   then consider clearing.

## If something goes wrong

| Symptom | What it actually means |
|---|---|
| Watch shows nothing | Almost always the link, not the data. The puck is still recording. Keep riding. |
| `eval` says matched 0/N **and** spurious N | Video↔trace sync error, **not** a broken detector. A detector that misses jumps does not invent an equal number. |
| `eval` says matched 5/8 | The dangerous case: a plausible RMSE computed from a silent subset. Check missed-vs-spurious before believing any rate. |
| No RMSE at all, with a warning about `height_src` | Working as intended: the heights are timing-derived and therefore circular. Measure against the ruler instead. |
| Puck seems dead | Do not diagnose it at the beach. Bring it home. Two "dead hardware" verdicts in this project were wrong. |

## Afterwards

- Label from video: takeoff time, and apex measured in **rider-heights**,
  with the board's position at takeoff as zero — **not the horizon** (a
  horizon zero adds ~50 % and never looks like an error).
- Set `height_src=ruler` on those rows. Anything else is excluded from
  accuracy on purpose.
- `./tools/jump eval --verbose`.

**The one number this session exists to produce needs no video at all:**
median airborne |a| per jump, straight from the trace. If the filming is a
disaster, the session still answers its question.
