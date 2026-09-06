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
| What firmware is on the OG? | **`src=5c80a436`** — matches the tree (`jump sync` 2026-09-06: "device is running THIS source tree"). Confirm with `stats`; never infer from a commit date. | live read, 2026-09-06 |
| Are the OG's heights trustworthy today? | **Bench-calibrated, yes** — drop ritual re-run 2026-08-24: 8 drops from 101.6 cm, bias −19 ms ±9, `airtime_offset_s=0.0192`, `off_src=device`, survived a reflash. `height_scale` remains defaults *by design* until the on-water video calibration. | live read, below |
| How does the app reach the rider's watch? | **Connect IQ store, and it is APPROVED (2026-08-25).** Install from the Connect IQ phone app; sideloading is impossible on the Instinct 3. | `docs/watch.md` |
| When is the water day? | **No date exists anywhere in this repo.** The freeze is *defined* as ≥4 days before it, so there is no freeze window. | — |

---

## The OG, read over USB on 2026-08-24

```
INFO fw=0.4.3 sample_hz=200 log_hz=50 motion_thresh_g=0.12 idle_timeout_s=20
     ble=1 vbat_mv=4094 batt_pct=93 chg=1 src=76df4a83
CAL  airtime_offset_s=0.0192 height_scale=1.000
     source=device off_src=device scale_src=defaults vbat_src=defaults
SELFTEST i2c / whoami / accel / noise / ble / flash — 6/6 PASS
STATS stored_jumps=0 (session synced to two copies, then cleared)
```

`dcdc=1` at every boot (audit F-05). The drop calibration was applied live,
then **baked and reflashed the same night, and survived the reflash in NVS**
— the flashed build and the tree agree at `src=76df4a83`. The pre-audit
session (18 jumps, 1.58 MB trace) is in `data/sessions/20260824-183054` and
a verified second copy.

---

## Open gates before the water day

Ordered by what blocks what.

1. **Set the date.** Everything sequences off it, and no freeze window can
   begin without it.
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
| **F-22** | minor | `trace_bytes()` over-reports once the region fills; self-corrects at the next boot |
| **F-23** | minor | Full-chip mount is ~80× empty (74 ms vs 0.93 ms). The walk is the floor; no counter scheme fixes it |
| **F-24** | minor | Self-arm cannot bootstrap at a small lever arm. **Not reachable** — `JH_SPIN_SELFARM_ENABLED = 0` |
| **F-28** | minor | Phantoms self-identify by median airborne \|a\|. **Held on water 2026-09-06:** 6 phantoms at 0.50–1.53 g, 9 tosses at 0.04–0.23 g, no overlap. But the "phantoms are short and small" caveat is **retracted**: one water-entry fall read 0.78 s / 0.75 m and became the watch's best airtime |
| **F-29** | minor | `session_best_airtime` is only ever written in the `fakejump` path (`main.cpp:1148`); real jumps never update it, so `STATS` reports `session_best_airtime_s=0.000` and the watch-side reseed added 08-18 has nothing to reseed from. Seen live: 16 jumps, STATS airtime 0.000 |
| **F-30** | minor | ~~A jump was counted and not stored.~~ **REFUTED same day — no jump was lost.** `session_jumps` is BOOT-scoped and has no reset anywhere (`main.cpp:134` is its only assignment); `clear` zeroes `stored_jumps` alone (`main.cpp:736`). The OG booted 09-04 and was cleared 76 s after the 09-05 sync, so 1 pre-clear jump + 15 post-clear = the 16 the watch showed. **The real finding: the number on the rider's wrist counts from the last PUCK REBOOT, not from the activity or the last clear**, and nothing in the tooling says the two counters measure different spans |
| ~~F-26~~ | closed | `sim/selfdiag.py` had no test at all — 11/11 mutants survived a 223-test run. Now 17 tests, 10/11 mutants killed |
| ~~F-27~~ | closed | `jump eval --split` was unguarded; inverting the filter passed the suite. Now killed by a partition property test |
| ~~F-25~~ | closed | `jump status` reported the help *string* as "commands in binary", hiding `gyro`/`pincensus`/`vbatscan`. Tool label fixed 08-23; help string shipped in the 08-24 flash (`42dbd59`, on-device at `src=76df4a83`) — `jump status` now shows 21 with no gap |

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

**The battery percentage gauge is now conclusively broken, with numbers:**

| gauge read | vbat | puck then ran |
|---|---|---|
| ≤20 % | 3733 mV | **38.9 more hours** |
| ≤5 % | 3564 mV | **28.1 more hours** |
| 0 % | 3307 mV | 5 more hours |

It sat at 0 % for the last five hours while answering every poll. **This
number is shown to the rider on the watch** (`docs/rider-brief.md` item 1),
so today the product displays a figure that was wrong by 39 hours. Committed
curve: `curve.csv` in the soak directory — this is the dataset the gauge
re-anchor needs.

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
  earned its place.
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
  in 2.8 h of car/handling). Offline replay at 50 Hz kept 3 of the 6. Every
  phantom sits under a labelled fall or pump-up: 13:44 entry fall (2), 13:47
  shore-break fall (1), 14:08 pumping onto foil (2). **This is a chest-mount
  number in surf; it says nothing about a board mount.**
- **The watch showed `jumps=16, best 0.95 m, best airtime 0.78 s` for a day
  with no jump.** All three numbers are now accounted for: 16 = 1 jump from
  09-04 (still counted, because `session_jumps` never resets) + today's 15;
  0.95 m is house toss #3, correctly reconciled; 0.78 s is phantom #9, a
  water-entry fall, and the only airtime the watch ever saw live because
  STATS carries none (F-29). **Nothing was lost — but nothing on that screen
  was a jump, and the count spanned three days.**
- **Median airborne |a| separates cleanly** (F-28): tosses 0.036–0.231 g,
  phantoms 0.502–1.533 g. Pooled with 08-29 and 09-05: **16 tosses
  0.036–0.255 g vs 11 phantoms 0.502–1.533 g, a 0.247 g gap, and a gate
  anywhere in 0.30–0.50 g kills 11/11 phantoms and eats 0/16 tosses.**
  It still must not ship — see F-28 for the spin-lever reason, which is
  mechanistic and not merely a small-sample caution.
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
  so the 14.4 MB region holds **4.7 h of water**. The card's ~5 h figure
  is now field-confirmed for water specifically, not just mixed use.
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
- The Instinct has never rendered a jump. All watch evidence is Epix.
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
