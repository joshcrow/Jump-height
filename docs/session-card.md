# Water session card — print this, take it to the beach

One page. One shot. Everything below exists because of a specific way this
session could be wasted; the reasons are in [data-pipeline.md](data-pipeline.md)
and `plan.md`, not here.

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
- [ ] **`stats` does NOT say `fs=down`.** If it does, the flash did not
      mount and the puck will record nothing. The watch does show this as
      `NO REC`, so it is not silent — but catching it here costs a minute and
      catching it on the water costs the session. Send it `mount`
      (non-destructive; proven 2026-08-27 after a full battery death, all
      data intact). **Never `format` to fix this** — it destroys data that is
      almost certainly fine (DECISION #31). Expect this after any deep
      discharge: the puck refuses to auto-remount by design when a previous
      mount was interrupted.
- [ ] **No `⚠️ CALIBRATION PROVENANCE` warning.** If it appears, this board's
      measured drop calibration has been replaced by compiled defaults (a
      battery death can do this silently — it happened to the OG on
      2026-08-19). Re-run the drop ritual before trusting a single height.
- [ ] `./tools/jump sync` the previous session, **copy it twice**, then
      `clear`. Never clear anything that exists in only one place.
      (Capacity from a cleared start: ~5 h of *moving* time — covers every
      session on record. **Two outings in one day with a >1 h break:** the
      puck auto-clears the morning's raw trace when the afternoon starts, to
      make room — jumps are never touched. If the morning's raw trace
      matters, sync during the break.)
- [ ] Battery ~100 %. Budget ~21 h of recording; a session is ~2 h.
- [ ] Puck mounted **≥24 h ago** (the mount needs a full cure).
- [ ] **With the puck mounted, check you can still reach the reset
      button.** Press it once now, before the day, not for the first time
      at the beach. If the mount or capsule blocks it, fix that now — see
      the reboot ritual below for why this matters.
- [ ] Capsule bucket-tested, loaded, and floating.
- [ ] Camera: **1080p/120**. Not 4K/30. Check it is actually set.
- [ ] Phone or paper for notes, with the real time visible.
- [ ] **The field is installed on the RIDER's watch, and he has put it on a
      data screen once, at home.** This is not a beach task. It is also not
      guaranteed to be possible: the Instinct 3 (fw 15.18) **deletes a
      sideloaded `.prg` on the next USB disconnect** — pushed, read back by
      file id and exact byte count, gone after a reboot, `Restore` empty. So
      **Connect IQ store approval is the only route onto that watch**, and it
      has an external review queue you do not control. If approval has not
      landed, that is not a reason to cancel — see below.
- [ ] Hand the rider [rider-brief.md](rider-brief.md). One page, his job only.

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
4. Confirm the watch shows the puck connected before anyone gets wet — and
   that the rider **started his activity**, which is the one step of his that
   actually matters (no activity = the ride never reaches his watch).

   **If there is no watch app, run the day anyway.** The puck stores
   everything on its own flash and the session's headline number — median
   airborne |a| per jump — comes off the trace with no watch involved at all.
   A missing watch costs you the live display and the FIT, not the session.

5. **Once he is on the water, nobody else connects to the puck.** No phone,
   no laptop, no `dump`, not "just to check". A second central alongside the
   riding watch is precisely the 2026-08-11 configuration that corrupted a
   capture, and a `dump` mid-session blocks recording outright. The single
   exception is if `dualcentral.py` has been run and passed beforehand —
   which, unless you did it, it has not.

## In the water — the two things that decide whether this works

**1. Camera roughly ABEAM of the jump line** — and mind the RANGE: his
usual sound riding box starts ~325 m off the Manteo waterfront, beyond
phone-camera reach (~250–300 m for full-height ID at 1080p/120). Either
brief the rider to send jumps on the west edge near the waterfront, bring a
real zoom, or put the camera on a kayak/SUP ~100–200 m abeam. Not behind, not head-on. The
height measurement is a comparison against the rider's own height in frame,
so the rider has to be side-on and full-height at apex.

**2. Get the whole rider in frame at apex.** If the rider is cropped, that
jump has no height truth. Better to shoot wide and lose detail.

Then just ride. The puck stores everything; nothing depends on the watch
staying connected.

## Notes to take (ranges, not instants)

Write segments: `14:32-14:51 riding`, `15:05-15:12 nothing, sat on the board`,
`15:20 big one, felt like the highest`.

The hands-free way to do this: **message Claude** as events happen — messages
are timestamped, and `tools/label.py` converts them into labels the same way.

- **Know what accuracy to expect from the water you picked** (E15, 200k
  jumps on these exact venues, the rider's own speeds): flat ±6.5 cm ·
  light-wind sound ±8 · typical ocean ±9 · 18 kt sound ±10 · 25 kt sound
  ±14. **Short chop is the enemy, long swell is nearly benign** — so a windy
  sound day is the worst calibration water, not the ocean. Watch
  "session best" reads ~1–2 cm generous everywhere; median is the honest
  number.
- **Write down the SEA STATE, once per session, in plain words.** Rough
  wave height and whether it is short chop or long swell — "flat, glassy",
  "1 ft chop", "waist-high swell, long period". Guessing is fine; the point
  is that it is recorded at all.
  E14 (240,000 simulated jumps) is why: chop does **not** bias the average —
  that assumption held — but it more than doubles the per-jump spread, from
  4.6 cm RMSE on flat water to 10.2 cm in 1.5 m swell. **So an accuracy
  number from this session is uninterpretable without knowing what the water
  was doing**, and this line costs ten seconds and cannot be recovered later.
- **"Nothing happened here" is as valuable as "3 jumps here."** It is the
  false-positive rate, and there is no other way to get it.
- **Note when you were ON THE FOIL vs not** — `14:00-14:20 foiling`,
  `14:20-14:25 notfoiling`. This is the training data for time-on-foil and
  every riding metric after it (docs/future-metrics.md). It costs a line in
  your notes during the session and **cannot be recovered afterwards** — no
  amount of offline cleverness reconstructs what you were doing at 14:07.
  `tools/label.py` understands `foiling` / `notfoiling` / `riding` / `crash`.
- Note anything odd: a crash, a dropped board, a swim, a knock on the rocks.
- **Frame-count airtime for every filmed jump (Channel A) — this is not
  optional.** E16: the bench-measured timing correction may be wrong on
  water by up to ~100 ms (foil-exit edge shape), which is ~25 cm on a big
  jump. Frame-counted airtime vs the device's raw airtime measures the water
  offset directly; one session of it settles the question forever.
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

**Read "session best" with a pinch of salt.** It is a *maximum* over jumps
that each carry measurement noise, and a max over noisy readings lands on
whichever jump the noise flattered most — E14 puts that at **~3 cm of
inflation in every sea state, including flat water.** It is not a bug and no
calibration removes it; it is what taking a maximum does. The median and the
per-jump list are the honest numbers.

**The one number this session exists to produce needs no video at all:**
median airborne |a| per jump, straight from the trace. If the filming is a
disaster, the session still answers its question.
