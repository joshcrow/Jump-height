# STATUS — what is true now

**This file wins.** If any other document disagrees about status, this one is
right and that one is stale. Fix the other one.

Rewritten 2026-08-23, from 2,507 lines to this. The old file was 33 dated
entries across ~190 sections — a log, kept in prose, beside a git history that
already stored it perfectly. It failed the one job it had: on 2026-08-20 a
fact written correctly *inside it* was missed anyway, because nothing could
reach it at the moment of use. The chronology lives at
`archive/docs-2026-08-23`; the commits are the log. **Do not rebuild the log
here.**

Every claim below cites a `file:line`, a commit, or a measurement — never
another document. Docs are the thing under suspicion.

---

## READ THIS FIRST

| Question | Answer | Authority |
|---|---|---|
| Which boards exist, and which has a **battery**? | **Only the OG (`JumpHeight-E2C4`).** The spare (`45ED`) and the Puck (`8673`) are USB-only; their battery readings are a floating divider — noise. | `docs/bench-playbook.md` §1 |
| Which board can measure power or run untethered? | **The OG only.** Drain, endurance and DC/DC numbers are meaningless elsewhere. | same |
| Why did my BLE reading change between calls? | **Three boards can advertise.** Unpinned tools answer from whichever replies first. Always `--name`. | `tools/blepin.py` |
| Can I trust a "dead board" verdict? | **No — four have been wrong.** Nothing was ever damaged. Establish the board's *configuration* first. | `docs/xiao-hardware-truth.md` |
| **Where is the OG?** | **At Nick's, from 2026-09-07.** Synced (two SHA-256-identical copies), `clear`ed, calibration intact, battery full, before it left. Reset button pressed before it left (owner, 2026-09-08) — so `session_jumps` starts at 0 for the loan; not machine-verified, Nick's first watch count confirms it. Nick has no admin path: `mount`, `clear`, `dfu` all need a laptop. | this session, 2026-09-07 |
| What firmware is on the OG? | **`src=5c80a436`** — matched the tree at the 2026-09-07 live read (`jump sync`: "device is running THIS source tree"). **The tree has since moved** (this merge: `traceraw`, the sync page, `jump ingest`; `build.gen.h` now `ae67dc8d`) and the OG has **NOT** been reflashed — every client falls back to CSV `trace`, by design. Confirm with `stats`; never infer from a commit date or a hash in a doc. | live read, 2026-09-07 |
| Are the OG's heights trustworthy today? | **Bench-calibrated, yes** — drop ritual re-run 2026-08-24: 8 drops from 101.6 cm, bias −19 ms ±9, `airtime_offset_s=0.0192`, `off_src=device`, survived a reflash. `height_scale` remains defaults *by design* until the on-water video calibration. | live read, below |
| How does the app reach the rider's watch? | **Connect IQ store, and it is APPROVED (2026-08-25).** Install from the Connect IQ phone app; sideloading is impossible on the Instinct 3. | `docs/watch.md` |
| How does the rider get the data to me? | **A sync page → a zip → `./tools/jump ingest`. Two ways in: the USB cable in Chrome on his Intel MacBook (recommended) or Bluetooth from his phone** — `manifest.json`'s `transfer.transport` records which one ran (`"usb"` or `"ble"`). No repo, no toolchain, no bench needed for the normal flow (an emergency remote-guided CLI session is the documented fallback if the page ever fails, DECISION #42). This is NOT the retired browser app coming back — it is a one-way export surface, like `tools/jump`, not a user interface; the watch remains the product's only UI. Built 2026-09-07, **not yet run on real hardware** — see the dated section below. **Deployed 2026-09-09 14:09 UTC and verified in a real Chrome load — footer `page version 2026-09-09b` (Remote diagnostics, below). Never yet connected to a real port.** The FIT export + a text line is the channel that already works (8 zips, August). | `docs/rider-sync.md`, `web/sync/`, DECISION #42 |
| When is the water day? | **No date exists anywhere in this repo.** The freeze is *defined* as ≥4 days before it, so there is no freeze window. | — |

---

## The OG, last read before handover — 2026-09-07 17:00 EDT

```
INFO fw=0.4.3 sample_hz=200 log_hz=50 motion_thresh_g=0.12 idle_timeout_s=20
     ble=1 vbat_mv=4097 batt_pct=99 chg=0 src=5c80a436
CAL  airtime_offset_s=0.0192 height_scale=1.000
     source=device off_src=device scale_src=defaults vbat_src=defaults
SELFTEST i2c / whoami / accel / noise / ble / flash — 6/6 PASS
STATS session_jumps=19 stored_jumps=0 trace_bytes=0 uptime_s=292180  (after clear;
      session_jumps is boot-scoped — F-30 — and only the reset button zeroes it)
```

The drop calibration (2026-08-24, `off_src=device`) survived the 09-07
`clear`: calibration and the store guards are separate NVS keys
(`jh_persist.h:42-56`). The session it left behind is
`data/sessions/20260907-163005` plus a SHA-256-identical copy in
`~/JumpHeight-session-backups/` (the 09-06 convention; `data/sessions/` is
gitignored), labelled (`labels.csv`, from
`data/notes/2026-09-07-beach.txt`).

---

## Open gates before the water day

Ordered by what blocks what.

1. **Set the date.** Everything sequences off it, and no freeze window can
   begin without it.
1a. **Nick's Instinct 3 — installed, but the field could not be placed.**
   Owner, 2026-09-08: the Connect IQ phone app shows Jump Height installed on
   Nick's watch, but the owner "couldn't figure out how to get the data
   field to show up anywhere" — no data screen has it. The store listing is
   live (fetched 2026-09-07: "Jump Height", Josh, Data Field, free). No
   Instinct↔puck connection has ever been recorded. Mount for this loan is
   the **vest pocket** (owner, 2026-09-08), the one configuration ever run
   on water. Two things the brief now tells him
   that this repo measured: his profile is **"Wing Foil"** (sport=generic,
   sub_sport=track_me in 8/8 of his archived FITs, `data/nick-sessions/fits/`),
   not Windsurf; and **`NO REC` renders only in the single-field layout**
   (F-32). Until his first watch photo arrives, treat the watch side as
   UNKNOWN, not working.
1b. **Nick's data-return path is the FIT export + a text line** — the one
   that already worked in August (8 zips, `data/nick-sessions/raw/`). Nothing
   on `main` ingests a FIT (`grep fitdecode tools/ sim/` → 0 code hits);
   every FIT read so far was a one-off script. The trace comes home with
   the puck. An unmerged branch, `origin/claude/device-diagnostic-export-45ttyq`
   (`e5d6160`, 2026-09-07): a Web Serial/BLE sync page + `jump ingest` +
   firmware `traceraw`. **Not merged, not published (its rider URL was 404
   on 2026-09-07), and `traceraw` is not on the OG.** Merging is a decision,
   not a fact.
2. ~~Connect IQ store submission.~~ **APPROVED 2026-08-25, ~18 h after
   filing** (submitted 08-24 ~18:30, approval email 12:18 PM). Live at
   `apps.garmin.com/en-US/apps/7d0edbd4-24a7-45c2-a6b8-c0886ba34172`;
   Garmin says up to 24 h to appear for download. The approved build is the
   08-24 rebuild, 79,136 B, 4/4 variants. **This was the only route onto the
   rider's watch** — sideloading is architecturally impossible on the
   Instinct 3 — so the path now exists where none did. Next: install from
   the Connect IQ phone app on HIS watch, then the desk sequence in
   `docs/watch.md`. The `.iq` stays gitignored — **rebuild before any
   resubmission; size and mtime are the only handle on which build is on
   disk.**
3. ~~Re-run the drop calibration on the OG.~~ **DONE 2026-08-24** — 8 drops,
   101.6 cm, bias −19 ms ±9, saved to device NVS and `config/params.json`,
   baked into `src=76df4a83`, survived the reflash. The pending flash batch
   (F-25 help string + comment updates) landed in the same evening; tree and
   device agree and the session card's provenance gate is green.
4. **Week-0 items: four of five have not started.** Named 2026-08-20 as
   "latency-gated, begin regardless of era" — the saltwater adhesive coupon (a
   six-month soak needs six months of calendar), a temperature logger, and the
   µA-meter decision. Only the store package moved.
5. **Glue vs removable** — undecided, and the answer deletes or keeps an entire
   era of work.

## Open findings

`docs/audit-2026-08-22.md` carries the detail. F-01…F-21 are closed.

| | Severity | What |
|---|---|---|
| **F-22** | minor | `trace_bytes()` over-reports once the region fills; self-corrects at the next boot. **Hit live 2026-09-07:** `tracecheck fast=15917918 slow=15917153`; `jump sync` compares the download against the fast number and **refused to clear a complete file, twice, −765 both times.** `tracecheck`'s slow number is the arbiter — the tool should use it |
| **F-32** | minor | **`NO REC` renders only in the single-field layout.** `JumpFieldView.mc:209-211` sets it inside `_drawFull`; `_drawHalf` (`:284`) and `_drawSmall` (`:329`) never read `storageDown()`. A rider who shares the screen with speed/time is never told the puck stopped saving. The rider brief now says one field per screen; the fix is a rebuild + resubmission |
| **F-23** | minor | Full-chip mount is ~80× empty (74 ms vs 0.93 ms). The walk is the floor; no counter scheme fixes it |
| **F-24** | minor | Self-arm cannot bootstrap at a small lever arm. **Not reachable** — `JH_SPIN_SELFARM_ENABLED = 0` |
| **F-28** | minor | Phantoms self-identify by median airborne \|a\|. **Held on water 2026-09-06:** 6 phantoms at 0.50–1.53 g, 9 tosses at 0.04–0.23 g, no overlap. But the "phantoms are short and small" caveat is **retracted**: one water-entry fall read 0.78 s / 0.75 m and became the watch's best airtime |
| **F-29** | minor | `session_best_airtime` is only ever written in the `fakejump` path (`main.cpp:1332`); real jumps never update it, so `STATS` reports `session_best_airtime_s=0.000` and the watch-side reseed added 08-18 has nothing to reseed from. Seen live: 16 jumps, STATS airtime 0.000 |
| **F-30** | minor | ~~A jump was counted and not stored.~~ **REFUTED same day — no jump was lost.** `session_jumps` is BOOT-scoped and has no reset anywhere (`main.cpp:134` is its only assignment); `clear` zeroes `stored_jumps` alone (`main.cpp:736`). The OG booted 09-04 and was cleared 76 s after the 09-05 sync, so 1 pre-clear jump + 15 post-clear = the 16 the watch showed. **The real finding: the number on the rider's wrist counts from the last PUCK REBOOT, not from the activity or the last clear**, and nothing in the tooling says the two counters measure different spans |
| ~~F-31~~ | closed | **The suite did not read its own constants.** A 333-mutant overnight campaign found five gap classes, all the same shape: deliberately-chosen constants that nothing asserted. Worst two — `regression_check`'s failure paths were all flippable to pass, and `lever_arm`'s measured-wrong 5 % shave could be reinstated silently. **Suite 249 → 455; no behaviour changed.** All 333 mutants, all 11 modules. Worst three: a regression gate whose every failure path could be flipped to pass; a measured-wrong 5 % shave reinstatable in silence; and `while True` deletable from the board-slap generator with all 64 slap tests still green, because they assert ZERO jumps and removing the spikes only cleans the stream. `selfdiag` killed 11/11 — F-26's fix holding |
| ~~F-26~~ | closed | `sim/selfdiag.py` had no test at all — 11/11 mutants survived a 223-test run. Now 17 tests, 10/11 mutants killed |
| ~~F-27~~ | closed | `jump eval --split` was unguarded; inverting the filter passed the suite. Now killed by a partition property test |
| ~~F-25~~ | closed | `jump status` reported the help *string* as "commands in binary", hiding `gyro`/`pincensus`/`vbatscan`. Tool label fixed 08-23; help string shipped in the 08-24 flash (`42dbd59`, on-device at `src=76df4a83`) — `jump status` now shows 21 with no gap. **Residue (2026-09-07):** the regex at `tools/jump:2626` matches `cmd == "..."` only; `set` (`main.cpp:942`) and `fillstore` (`:793`) dispatch via `startsWith` and are counted nowhere — 23 reachable, 21 reported |

## Closed recently — do not re-open

- **The float32 timebase** (`glue-and-forget.md` §3a), the six-month
  silent-jump-dropping failure. `jump_detector.h:62` and `:152` are `double`;
  `jh_store.cpp:1001` no longer re-narrows `atof`; `trace_codec.h:224` uses
  `llround` with an explicit int32 bound. Falsifier passes:
  `tools/tests/test_timebase_falsifier.py:48`. Commit `37394ae` also caught a
  hazard *inside the fix* — an abort that would have killed a puck at 24.9 days.
- **`jump eval` discovered sessions only one level deep**, so the repo's only
  `labels.csv` was invisible for the eight days it existed, and placeholder
  labels were scored as truth. Fixed in `e9fa917`: discovery is recursive, and
  inadmissible ground truth is refused with a stated reason.
- **Cold-boot selftest false FAIL** — `main.cpp:494-516`, DECISION #35.
- **The detector gate question, open since 2026-08-15, is CLOSED** — DECISION
  #41. E7/E8 recommended `freefall_enter_g` 0.26; E11 measured what nobody
  had, at 400,000 paired jumps: the recommendation misses **48** where the
  shipped gate misses **3**, 45 of them missed by it alone. E12 then swept
  five gates over 200,000 jumps and found the curve **strictly monotonic** —
  no intermediate value rejects E7's slap without buying misses. It stays at
  **0.35 / 0.25**, and the 2026-08-24 drop calibration measured at those
  gates therefore stands — no re-drop.

## Retracted — do not resurrect

A number nobody measured is not a number. These were published, then withdrawn:

- **"100% detected"** in the wing ballistic sim — undersampling. The 200k rerun
  found 5 silent misses, all at the 0.35 g free-fall gate; RMSE is **4.6 cm**
  (not the earlier 4.2 cm) and the >1.20× tail was noise.
- **16.3 mA** gauge figure, **11.6 mA** walk figure, **"~15 h endurance"**.
- **"OTA on a glued device is proven"** — bench-only. A mid-transfer
  disconnect leaves a dark bootloader that only a physical reset recovers, and
  the `dfu` trigger is **unauthenticated**: any BLE peer in range can command a
  puck into its bootloader.
- **The "124 KB vs 32 KB" watch-memory fear** — that was PRG *file size*.
  Measured static memory is 12,417 B of 32,768 B.
- **The old ~7–11 mA / 25.7–34 h power figures** are superseded, not merely
  pending: see the measured 57.1 h below.

## Measured 2026-08-27 — endurance, and the gauge convicted

**57.1 h idle on one charge, DC/DC enabled** (`data/soaks/dcdc-deathrun-
20260824-192240/`). This is a real death, not a stopped run: the puck fell
from 3179 mV to 2617 mV in the final 90 minutes and stopped advertising. The
old ≥25.7 h figure was a *floor* — that run was ended by hand — so the honest
comparison is **2.2× the endurance**, same board, same method.

The curve is flat then cliffs: ~10–15 mV/h for fifty hours, then gone in
three polls. **Do not extrapolate a remaining-time estimate from the flat
middle** — that is precisely what makes voltage-percentage gauges lie.

**The battery percentage gauge WAS broken, with numbers — and was re-anchored
the same day.** The table below is the OLD table's error. `ea270e7` refitted
`kCurve[]` to this discharge (`jh_power.cpp:90-123`, worst error 2.0 h of
57.1, pinned by `tools/tests/test_batt_curve.py`) and `8cec162` flashed it —
it is in `src=5c80a436`, on the OG now. Remaining limits, from the source:
15 % of the cell's life sits inside 35 mV around 3515–3550 (coarse in the
middle), and it is calibrated at idle so it reads pessimistic under
recording load. The rider brief's rule is "charge after every ride", not a
number. Corrected 2026-09-07; this section had read as current for eleven days.

| OLD gauge read | vbat | puck then ran |
|---|---|---|
| ≤20 % | 3733 mV | **38.9 more hours** |
| ≤5 % | 3564 mV | **28.1 more hours** |
| 0 % | 3307 mV | 5 more hours |

It sat at 0 % for the last five hours while answering every poll. Committed
curve: `curve.csv` in the soak directory — the dataset the re-anchor used.

## After a deep discharge, the flash does not mount on the recovery boot

Observed 2026-08-27, immediately after the 57.1 h run-to-death. Plugged in at
**3102 mV** (right at the cell's cutoff), the OG booted and reported
**`fs=down`** — the QSPI flash had not initialised. A single non-destructive
`mount` at 3191 mV brought it straight back with **everything intact**: 8
stored jumps, 812 KB of trace, and the drop calibration still `off_src=device`.

**Nothing was damaged and nothing was lost**, and — corrected 2026-08-27
after checking rather than assuming — **this is the firmware working as
designed, not a gap:**

- `main.cpp:1279` deliberately does NOT auto-remount when the StoreGuard was
  found latched, because the previous mount never returned. Retrying it is
  how "a wedged chip turns into a reset every ~33 s for the whole session."
  A 30 s auto-remount does exist (`main.cpp:1380`) for the un-latched case.
- It is **not** a silent failure either. `fs=down` ships as a STATS adder key
  (`main.cpp:656`), `Model.mc:342` parses it, and `JumpFieldView.mc:210`
  renders **`NO REC`** on the watch. The rider is told.
- The deliberate human retry is `mount`, exactly as the firmware's own error
  text says.

What WAS wrong was the rider brief, which lumped `NO REC` in with the
cosmetic `!` marker and told the rider both were "not a problem — keep
riding either way." `NO REC` means the session is recording nothing. Fixed:
it is now the one thing worth coming in for.

**Water-day consequence:** if the puck is ever run flat and then charged, do
not trust "it powered on". Check `stats` for `fs=down` before the session, and
`mount` if you see it. Do **NOT** `format` — DECISION #31 exists precisely
because an unreadable superblock is not an invitation to destroy the data
underneath, and here the data was perfectly fine.

Not yet established: whether this is purely voltage (likely — it mounted 89 mV
higher) or something about the deep-discharge recovery specifically. One
repeat at the next flat battery would settle it.

## Field-measured 2026-08-29 — the pocket-carry protocol (49 h, one run)

First real-motion exposure, deliberately bracketed by two toss-triplets:

- **Detection: 6/6 deliberate tosses found, 0 missed** — and today's triplet
  timestamped within 2 min of the user's stated wall clock.
- **False positives: 0.9/h of motion** (4 phantoms in ~4.4 h incl. a 5-mile
  run — adversarial motion). Budget (<1/h) met, barely. All 4 phantoms
  self-identify at `med_a` ≥ 0.64 g vs tosses ≤ 0.26 → F-28 filed. Zero
  phantoms overnight on the shelf.
- **Recording draw ≈ idle draw** (~1.5 %/h mixed vs 1.75 %/h idle) — the
  57 h endurance figure appears to hold for recording too. Estimate only:
  the endpoint was read after charging began.
- **Trace capacity field-confirmed:** 12.6 of 14.4 MB used (~87%) — the
  ~5 h moving-time figure is real, and the session card's two-outing note
  earned its place. (Both are CSV-equivalent `trace_bytes` figures; the
  physical region is 2,027,520 B and the CSV number at exhaustion is not a
  constant — see 2026-09-07.)
- **The eval pipeline scored its first real, admissible labeled session**
  end-to-end: `matched 6/6, spurious 6` with the placeholder session still
  correctly refused alongside. The grading path is no longer unrehearsed.

## Field-measured 2026-09-06 — first water session: vest mount, zero real jumps

The rider (Instinct 3, field not yet on a data screen) wore the OG and the
owner's Epix in a flotation-vest pocket at chest height — **not on the
board**, no glue. Nags Head ocean side, light wind, disorganised chop,
waist-high shore break. 58 min activity, ~47 min in the water, nine falls,
one ~20 s flight, **no jump**. Session folder `data/sessions/20260906-192422`
(+ two verified copies), FIT `data/fit/2026-09-06-13-33-59.fit`, labels from
`data/notes/2026-09-06-beach.txt`.

- **Detection: 9/9 deliberate tosses found** (three triplets: house 10:46,
  beach 13:33, base 14:31), live and offline agree on all nine.
- **False positives on water, vest mount: 5 in ~47 min ≈ 6/h live** (plus one
  in 2.8 h of car/handling). Every phantom sits under a labelled fall or under
  the takeoff onto foil: 13:44 entry fall (2), 13:47 shore-break fall (1),
  14:08 the foil takeoff itself (2, at GPS 2.5–2.9 m/s and rising). **This is
  a chest-mount number in surf; it says nothing about a board mount.**
- **The offline pass is stricter than the device, and that hides the problem.**
  The device (200 Hz) found 15; `jump replay` on the 50 Hz logged trace found
  12, rejecting exactly three — the car phantom, the 14:08 foil-takeoff
  phantom, and **the 0.75 m water-entry phantom that became the watch's best
  airtime.** `jump eval` scores the offline set, so it reported 3 spurious
  where the rider's wrist saw 6. **Every phantom rate computed from a trace
  understates what the rider is shown.** Nothing in the tooling says so.
- **The watch showed `jumps=16, best 0.95 m, best airtime 0.78 s` for a day
  with no jump.** All three numbers are now accounted for: 16 = 1 jump from
  09-04 (still counted, because `session_jumps` never resets) + today's 15;
  0.95 m is house toss #3, correctly reconciled; 0.78 s is phantom #9, a
  water-entry fall, and the only airtime the watch ever saw live because
  STATS carries none (F-29). **Nothing was lost — but nothing on that screen
  was a jump, and the count spanned three days.**
- **Median airborne |a| separates cleanly** (F-28): tosses 0.036–0.231 g,
  phantoms 0.502–1.533 g. **Pooled over every documented-deliberate session
  (41 real vs 13 spurious): the margin is 0.017 g.** Highest real is
  `20260824-183054` n=18 at 0.238 g (in the 08-24 drop log, which calls it a
  valid-looking jump); lowest spurious is `20260829-110356` n=2 at 0.255 g, a
  bounce 0.5 s after a labelled toss. **The only gate that kills 13/13 at
  zero real cost sits in 0.239–0.2549 g — a 17 mg window.** The 0.5 g gate
  under discussion kills 12/13. Label-strict (the two sessions with a
  labels.csv) the figure is 0.024 g over 15 real; the fuller corpus is the
  honest one. It must not ship — see F-28: the uncorrected spin term spans
  30–500 mg, one to thirty times the entire window.
- **The one flight is in the GPS, not the accelerometer.** `enhanced_speed`
  above 2.5 m/s spans 14:08:49–14:09:13, **24 contiguous seconds and the
  only such window in 47 min** — 0.84 % time-on-foil. Accelerometer
  variance does NOT find it: 258 of 1418 ten-second windows are quieter
  than the flight, because floating still is quieter than foiling. A
  quiet-window classifier would have picked 14:28, when he was walking up
  the beach. **For time-on-foil the watch is the better sensor and the puck
  alone is not sufficient.**
- **Water logs at a continuous 50.00 Hz** (142,250 samples in 2,845 s,
  max gap 0.1 s) where transport averaged 27 Hz with 405 s gaps — the
  motion gate never closes on water. At 16.97 B/sample that is 3.05 MB/h,
  so the region holds **~5 h of water** — measured full on 2026-09-07 at
  937,644 samples = **5.2 h of logged time** (`data/sessions/20260907-163005`).
  The "14.4 MB" here was the 08-29 CSV-equivalent fill, not the capacity;
  the 09-07 fill read 15.9 MB for the same physical 2,027,520 B.
- **FIT developer fields are present and correct** (fitdecode): SESSION
  `jumps/best_jump/best_airtime`, RECORD `jump_height` in 1,213/1,404 records,
  7 distinct live values matching the trace within ~3 s. **Garmin Connect's
  phone app showed none of them in Overview/Stats/Charts** — the archive
  has the data; the rider's screen does not. Strava, as documented, shows
  nothing.
- **BLE through a wet vest:** 7 of the 15 stored JUMP lines arrived live; the
  rest were absorbed by STATS reconcile on reconnect (count and best correct,
  per-jump record lost). `tx_drops=20` is **20 BYTES, not 20 drops**
  (`jh_link.cpp:360` adds the chunk length) — one chunk given up after 8
  retries in 2.5 days of uptime. First non-zero `tx_drops` since the
  2026-08-11 fix. The corruption gate held.
- **Procedure lessons:** the beach toss triplet landed 50 s *before* the
  activity started, so it exists on the puck and not in the FIT — start the
  activity first. The puck was **not rebooted** (uptime 2.5 days at sync), so
  the session count included the morning's house tosses and a car phantom.
  `label.py` assumed the notes' day was the boot day; `--date` added.

## Field-measured 2026-09-07 — pocket walk, the region fills, and the F-28 margin goes negative

Owner's pocket, walk to the beach and back, no water. Three deliberate tosses
at the house (the sync-marker ritual, owner-confirmed), then 1.83 h of walking.
Session `data/sessions/20260907-163005` + backup copy; labels from
`data/notes/2026-09-07-beach.txt` (times trace-derived, exact to the second).

- **Detection: 3/3 tosses found, 0 phantoms in 1.83 h of walking** (11:42–13:32
  EDT). First clean walking false-positive number: **0/h**, against the
  0.9/h of the 08-29 run-included carry.
- **Toss #18 spun** (`med_w` 1017 dps) and read **`med_a` 0.352 g** — a real,
  owner-labelled jump above the lowest recorded phantom (0.255 g). **F-28's
  pooled 17 mg window is now negative**, by exactly the spin mechanism STATUS
  already gave as the reason it must not ship. Pooled corpus is now 44 real
  vs 13 spurious; no |a|-only gate separates them at zero cost.
- **The trace region filled at t=279,711 s (13:33 EDT) and recorded nothing
  for the next 3.2 h** — the walk home, the car, the bench — while `stats`
  answered normally and nothing on any surface said so. STATS carries no
  fullness key (`main.cpp:686-693`); the one-shot `# trace log full` serial
  line fires once per boot. **Had it gone to Nick like this, his first ride
  would have auto-cleared the region (`main.cpp:1549`) and the day's data
  with it.** Physical region: 2,027,520 B (2 MiB − 4 KB superblock − 64 KB
  jumps, `jh_store.cpp:107`); when full the firmware **stops, never wraps**
  (`main.cpp:1707`, `jh_store.cpp:1045`).
- **F-22 hit the tooling.** `tracecheck fast=15917918 slow=15917153`; sync
  compared its download against `fast` and refused to clear a byte-complete
  file twice. The −765 B is F-22's dropped-block residue. `tracecheck` is
  the arbiter; `jump sync` should call it before calling a file SHORT.
- **Region cleared 17:00 EDT** after two verified copies. `session_jumps=19`
  survives the clear (F-30) and only the reset button zeroes it.

## Remote diagnostics — built 2026-09-07, reviewed and corrected 2026-09-09, NOT yet run on hardware

The rider (Nick) is taking the OG home. He has an older Intel MacBook (no
repo, no toolchain on it) and a phone — no bench either way. Four pieces
exist to get his data back without a bench:

- **Firmware `traceraw`** — `firmware/src/main.cpp` (command dispatch),
  `firmware/include/base64.h` (dependency-free base64 encoder). Streams the
  trace region's raw bytes instead of CSV; falls back cleanly (`ERR
  unknown_command traceraw`) on firmware that predates it.
- **The sync page** — `web/sync/` (`index.html`, `sync.css`, `sync.js`).
  Connects over the USB cable (Web Serial, Chrome on his Mac — recommended)
  or over Bluetooth (Web Bluetooth, phone or Mac), pulls
  info/stats/jumps/traceraw(→trace)/selftest, builds a zip, hands it to his
  Downloads or share sheet. Which transport ran is recorded in
  `manifest.json`'s `transfer.transport` (`"usb"` or `"ble"`).
- **`./tools/jump ingest`** — unpacks a bundle into a `data/sessions/<id>/`
  folder and runs the same analysis `jump sync` does.
- **Docs** — this row, `docs/rider-sync.md` (Nick's page), `docs/watch.md`,
  DECISIONS #42/#43, `docs/bench-playbook.md` §1 (clone-board placeholder).

**Reviewed 2026-09-09 before it reached `main`** (four Opus reviews, each
blocking finding refuted or confirmed by a second agent, then verified by
hand — CLAUDE.md rule 5). What changed, all pinned by tests, suite 523 →
592 passed, 1 xfailed, Playwright driving the real page for 28:
- **`web/sync/CONTRACT.md` did not exist.** Cited 28+ times across 12 files
  (including `jump ingest --help`), in no commit on any ref — CLAUDE.md §4's
  identifier-without-a-lookup-entry. Reconstructed from what the code does,
  every rule citing its implementing line; disagreements recorded in its
  appendices, not harmonised.
- **F-22 on every path the rider can reach.** The page and `jump ingest`
  hard-compared the download against STATS `trace_bytes` — the counter
  measured 765 B high on the OG 2026-09-07 — so a full puck could never
  verify and every such bundle needed `--force`. Now: a 1..800 B shortfall
  is accepted with a visible note (`sync.js` csv arm; `tools/jump`
  `_verify_ingest_bundle`); the bench `jump sync` asks the device
  (`tracecheck`, 300 s) instead. The band is the whole arbiter on the csv
  path — no crc32 there — and is one-sided and narrow for that reason.
- **Two real-puck shapes a byte match refuses:** a cleared region emits the
  6-byte CSV header against `trace_bytes=0` (`jh_store.cpp:1119-1126`) —
  the first bundle of the loan; and the puck keeps logging while handled,
  so `got` runs past the connect-time count. The page re-reads `stats`
  after the dump and ships `trace_bytes_after`; page and ingest accept up
  to it.
- **"Empty the puck" is hidden** behind `?allowclear=1` (`ALLOW_CLEAR`,
  gates the button, the unhide and `doClear()`): the brief says the rider
  never clears, and "delivered" only ever meant `a.click()` returned.
- A pull that died mid-frame no longer poisons the retry (`fileSection`
  reset in `doPull`); a puck that never answered `stats` cannot be pulled
  (the manifest would ship `trace_epoch_utc` null and `label.py` would
  misdiagnose a live puck); "reload this page" on link loss.
- `docs/rider-sync.md` said the puck keeps everything until emptied — the
  firmware wipes a full region at the next ride. Fixed, with Wing Foil for
  Windsurf, share-sheet-before-Downloads (measured on the owner's Mac, not
  Nick's), no accessory prompt on an Intel Mac, and the full-puck Bluetooth
  estimate (~20-30 min).
- `tools/fitread.py` — the first thing on `main` that reads a FIT (41 tests
  on all nine real files). `docs/serial-parity-2026-09-09.md` — the page's
  serial assumptions vs the firmware vs `tools/jump`, the checklist for the
  first real-port test.
- Page version `2026-09-09b`. **Deploy status at merge time:** `/sync/` was
  **404** and the root served the retired 08-23 app; Pages source is
  already "GitHub Actions" (`gh api …/pages` → `build_type: workflow`); the
  `pages` job needs the `test` job, which now installs Playwright + Chromium.
  **First CI run ever, PR #3, 2026-09-09: FAILED** — `fitdecode` was not in
  the workflow's pip line (6 failed, 23 errors, all `test_fitread`). Fixed
  in `f3d2a9a`; **re-run green: 580 passed, 12 skipped, 1 xfailed.** The 12
  skips are the `HAVE_NICK` classes in `test_fitread.py` — they read
  `data/nick-sessions/`, which is gitignored, so CI cannot see those files;
  locally the same suite is 592 passed, 0 skipped. Chromium launched in CI
  and drove the page. **Merged and deployed 2026-09-09 14:09 UTC** (PR #3,
  merge `aa9d86e`, run `34361217565`: test / firmware / pages all success).
  Verified live, not assumed: `/`, `/sync/`, `/sync/sync.js`,
  `/sync/CONTRACT.md` all HTTP 200 with that last-modified; the served
  `sync.js` carried `PAGE_VERSION = '2026-09-09b'`; a real Chrome load
  shows the footer, ends at "You're finished. Josh empties the puck.", and
  has no step 4;
  `?allowclear=1` shows step 4 and the button; zero console messages over
  two fresh loads. The root now serves the landing page, not the retired
  08-23 app. **2026-09-09: a real port, at last.** The owner
  attached the Puck (`JumpHeight-8673`, `src=15b2d468` — older than the OG,
  so it takes the same CSV fallback Nick's will) and picked it in Chrome's
  native sheet by hand. The page connected, fell back to CSV exactly as
  designed, and pulled **455 KB in 9.1 s, verified, zero console errors**.
  Browser throughput **64.9 KB/s** against **64.1 KB/s** measured
  independently from pyserial — so a full region's CSV is **~4 min on the
  cable**, the figure the rider docs had called unmeasured since the page
  shipped. Full assumption table: `docs/serial-parity-2026-09-09.md`.
  **It immediately caught a defect no fixture had:** 455 KB of ride data
  with zero detected jumps rendered as "Nothing was recorded on the puck" —
  the exact shape of the 09-06 water session, the most valuable capture this
  project has. Fixed to three outcomes (`2026-09-09d`).
  **Still unmeasured:** a multi-megabyte body through the browser (this was
  455 KB), `tracecheck` on a full region, and Send on Nick's own Mac.
- **Simplified 2026-09-09 to `2026-09-09c`, after the owner read it and called
  it confusing.** It was written for "phone or computer, Bluetooth or cable",
  and Chrome on a Mac reports BOTH transports — so the rider met two competing
  Connect buttons, two hints, a Bluetooth time estimate nobody has measured,
  phone advice during a cable copy, and a build hash. Now **one way in per
  device**: the cable wherever a serial port exists, Bluetooth only where one
  does not (`sync.js` init, `hasSerial`). Cut: the second button and hint, the
  Apple-silicon accessory prompt (Nick's Mac is Intel), the `Ride data waiting`
  byte count and `Puck software` build-hash rows, and the duplicate "don't
  empty the puck". The copying status now says "Leave the puck plugged in" on
  the cable. **Consequence, deliberate: Bluetooth is no longer reachable from
  a Mac at all** — `rider-sync.md` and DECISION #42 said it was, and were
  corrected in the same commit. Suite 592 → 593 (29 Playwright).

**How each part is verified today — all off real silicon:**
- The store side of `traceraw`: `firmware/test/store_host/` runs the real,
  unmodified `firmware/src/platform/nrf52/jh_store.cpp` against a
  real-semantics mock QSPI flash, driven by `tools/tests/test_store_host.py`.
- The command itself: `firmware/src/main.cpp` compiled and run natively
  (PlatformIO `env:host`), driven over its real stdin/stdout protocol by
  `tools/tests/test_hostdev.py`.
- The page: a real headless Chromium loads the actual `web/sync/index.html` +
  `sync.js` and plays a scripted fake puck through the page's own test seam,
  in `tools/tests/test_web_sync.py` — the Bluetooth-shaped and the
  cable-shaped flows, the verification gate, the old-firmware fallback,
  fs=down, the iPhone dead end, and the cable button/port-picker paths.
  **The Web Serial transport itself (`SerialTransport` in `sync.js`) has
  driven no port here** — only a real Chrome on a real cable exercises it.
- The CLI ingest path: `tools/tests/test_ingest.py` (bundle round-trip for
  both trace formats, directory ingest, the crc32/jump-row/csv-byte-count
  refusals and their `--force` overrides, and the two refusals that need no
  arithmetic — a puck whose store was not mounted (NO REC) and a bundle the
  page itself marked `verified=false`; bundles built in-test with
  `sim/trace_codec.encode_region`). The `traceraw`/`--csv` sync-side
  coverage lives separately, in `tools/tests/test_cli.py::TestSync`.

**What is UNMEASURED — say so plainly, none of this has run on a real puck:**
- **`traceraw` on silicon at all.** The OG is on `src=5c80a436` (row above),
  which predates the command — `stats`/`selftest` will confirm this before
  any claim otherwise. Until the OG is reflashed, both the page and
  `jump sync` fall back to CSV, which is the designed behaviour, not a bug.
- **Real BLE throughput.** The 10-16 KB/s figure in `docs/rider-sync.md` is
  an ESTIMATE from MTU × connection interval, not a measurement — nothing
  has been timed end to end yet.
- **USB transfer time.** Not measured, and no number is estimated either —
  `docs/rider-sync.md` says only that it's expected to be faster than
  Bluetooth, since it rides the same USB serial link `./tools/jump sync`
  already uses. The first real cable sync on his Mac settles it.
- **The cable path end to end.** Chrome's Web Serial against the puck's CDC
  port has not been tried by this project on any machine, let alone his
  Intel MacBook: the port picker's entry name, the macOS accessory prompt,
  and the stale-buffer drain in `SerialTransport.open` are all reasoning,
  not observation. One sync on the bench Mac before the handoff is the
  cheapest possible measurement of all three.
- **The 800 B band, the 6-byte header rule and `trace_bytes_after` growth**
  are read from source and played by a scripted puck — never observed off
  silicon. `tracecheck`'s walk time on a full region (the 300 s floor) is
  likewise untimed.
- **Nick's Chrome:** whether `navigator.canShare({files})` is true there
  (it is on the owner's Chrome 152 → share sheet first) and what the port
  picker names the CDC port.
- **Bluefy's share sheet on an actual iPhone.** `navigator.share({files})`
  behaviour there is untested by this project.
- **The Garmin two-central slowdown warning.** The page's "is your watch in
  an activity?" message is a design response to the documented no-second-
  central rule (`docs/watch.md`, BLE link dependability) — it has not been
  triggered and observed on real hardware.

**Flash-batch candidates — the owner's decision, none of this is done:**
`traceraw` itself, F-29 (`session_best_airtime` never set on real jumps, see
Open findings above), and gating `dfu` behind a required argument before any
OTA is ever pushed to the rider (DECISION #43). All three are candidates for
the *next* flash batch, not committed to one.

## Known-unmeasured

Stated plainly so an absence is never mistaken for a pass:

- The detector has seen water once (2026-09-06) **on a chest, with no jump in
  it.** No jump has ever been measured on water; every height number is
  bench or simulator, and the vest-mount phantom rate does not transfer to
  a board mount.
- **The water takeoff-edge offset is a NAMED unmeasured risk (E16).** The
  −19 ms drop-ritual latency is edge-shape latency; a foil leaving water is
  a different edge. If it unloads slowly, heights read up to ~25 cm low on a
  1 s jump even after correction. **Video Channel A (frame-counted airtime
  vs device airtime) measures it directly and is the day's most important
  video-derived number.** The additive-offset architecture is confirmed
  right (bias constant in T to ≤0.5 ms across 51 bench-matching shapes) —
  only the venue constant is in question.
- **Nothing has tested whether a 1.5 m reading means 1.5 m.** Only the water
  day can. **E13 supplies a prior: expect `height_scale` ≈ 0.99** (300,000
  jumps; fitted 0.9903, which independently reproduces DECISION #28's 1.0128x
  overshoot). Landing near it confirms the measurement chain end to end;
  landing far from it — 0.85, say — means something real is wrong that no
  bench test can see. `height_scale` is **1.000** today, by design, because
  it is calibrated on the water and nowhere else.
- **Per-jump accuracy expectations by venue are now simulated on his actual
  water** (E15, 200k jumps, his GPS speeds): flat ±6.5 cm, light sound ±8,
  measured-ocean ±8.6, 18 kt sound ±10.3, 25 kt sound ±13.7. Short chop
  hurts more than tall swell — wavelength vs jump-travel, not height, is
  the mechanism. Estimates for the sound (no buoy exists there); measured
  climatology for the ocean.
- Off-current has never been measured and is **unmeasurable** with the
  instruments this project owns — cell self-discharge is the same order as the
  signal.
- The Instinct has never rendered a jump. All watch evidence is Epix. **No
  evidence the field is even installed on Nick's Instinct** (open gate 1a).
- **The enclosure has never been bucket-tested, floated loaded, or
  BLE-range-checked closed** — every repo hit for "bucket" is an instruction,
  none is a record (`DECISIONS.md` #9, `session-card.md:48`). The 09-06 water
  session was a vest pocket; whether the puck itself got wet is unrecorded.
- **Whether a reset recovers `fs=down` after a flat battery.** `main.cpp:1279`
  skips the boot mount when the StoreGuard was found latched; the 08-27
  recovery used `mount`, and STATUS never recorded whether the guard was
  latched. So the brief's "charge, press reset, text a photo" is a hope with
  a fallback, not a fix.
- `jump monitor` and `setup` have no test coverage. (`jump drop` got its
  first real runs 2026-08-24.)

---

## Using this file

- **A state is a claim about the past, not a promise.** "Proven on hardware"
  means it worked on a date, on a build.
- `./tools/jump status` machine-checks what it can — the build, the suites and
  a live device — and marks the rest UNKNOWN rather than assuming.
- **No plan may list work without checking here first.** That one step would
  have prevented every rediscovery this project has paid for.
