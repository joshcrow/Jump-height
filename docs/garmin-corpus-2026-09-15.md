# What the rider's Garmin history tells us (2026-09-15)

Four reviewed lens passes over the rider's Garmin Connect export (338 zips) and
the puck sessions beside them. Sources, cited per line: **[wrist]**
`garmin-wrist.md`, **[dyn]** `garmin-dynamics.md`, **[alt]**
`garmin-altitude.md`, **[hist]** `garmin-history.md`. Every number is copied from
a reviewed report — none re-derived, rounded further, or extrapolated.


> **Hand-verified 2026-09-15 by the owner's session** (CLAUDE.md rule 5), against the
> FITs in `data/incoming/fits/`: finding 3's 09-14 dropout — the last `jump_height`
> value is at 20:58:31 Z and 1,688 of 2,877 records after it carry no developer
> slot at all (`scratchpad/devslot.py`); finding 7's `baro_alt_m` source —
> `JumpFieldView.mc:479-485` reads `info.altitude` into `baro_alt_m` and
> `info.rawAmbientPressure` into `baro_pa`, so the pressure channel is the real
> barometer and only the altitude channel duplicates the native stream; finding
> 10's picker — `tools/ride_loop.py` `pick_matching_fit` has no sport or duration
> filter and its docstring explicitly admits 0 s ties. Finding 2's fake session
> was verified and quarantined earlier the same day (commit fe8f545).

## 1. What we learned

1. **The watch is a second, wall-clocked witness to the puck's jumps.** 8 of the
   9 in-window puck jumps on 09-10 and 6 of 9 on 09-14 appear as `jump_height`
   value changes, each watch float the puck's `height_m / 0.3048` to float32
   (largest residual 2.2e-7 ft); lag after landing +3.01…+4.32 s (median +3.67 s)
   on 09-10, +1.42…+3.38 s (median +1.66 s) on 09-14. **[wrist]**
2. **The 09-12 wrist numbers are a stale carry-over.** `best_jump =
   11.131889343261719 ft` is bit-identical to 09-10's, `best_airtime = 0.0`, and
   `jump_height` is slotted into all 3,194 records and null in every one; the
   puck's `stored_jumps` reads 20 in the 09-11 sync and 20 in the 09-14 sync,
   both `"cleared": false`. The only "09-12 puck session" was
   `tools/fake_device.py` output (`src=fakedev0`), now quarantined. **[wrist]**
3. **The developer channel can vanish mid-ride.** On 09-14 a `record` definition
   with zero developer fields is emitted at 20:58:31 and never reversed — the
   slot is on 1,189 of 2,877 records (41.3 %), 485 non-null, then absent for
   59.1 min. The same drop on 09-10 reverses 2 m 52 s later (98 records) and
   costs exactly jump #12, 0.236 m, while the session field still says
   `jumps=9`. **[wrist]**
4. **Approach speed predicts nothing; the landing loss measures something.**
   N=17: r(approach, height) = 0.30, r(approach, airtime) = 0.32, t = 1.24 / 1.30
   on df = 15 (p ≈ 0.23 / 0.21), 09-10's Spearman −0.11, only 14 distinct
   approach windows in 17 rows — and the two r's are one measurement, since
   `h = g·t²/8` reproduces every stored height to ≤1.4 mm. Speed after landing is
   lower in 13 of 17 (5.19 → 4.59 m/s, p = 0.025); deduplicated 13 of 16,
   **p = 0.011**, Δ = −0.74 m/s. **[dyn]**
5. **What the FIT adds that the puck cannot.** Wall clock and GPS — it put the
   09-09 ride 7.78–8.00 km from the other four, whose pairwise spread is
   77–350 m; heart rate on every record of every ride (mean 148.8–159.9); a
   `gps_metadata` stream carrying `enhanced_speed` at **2.3–5.2×** the record
   rate (6,991–14,950 messages vs 2,383–3,398 records), no timestamp decoded;
   and 09-10's 15 laps, 14 `lap_trigger=manual`, leading the next takeoff by
   47–116 s — run markers, not per-jump presses. **[dyn]**
6. **`enhanced_altitude` carries no jump signature.** The only two jumps with a
   FIT record inside the flight — 2.81 m and 3.39 m apex — give in-flight
   residuals of **+0.13 m and −0.04 m** where the arc predicts +2.60 m and
   +3.36 m. The ±3 s excursion test agrees and is weak: 0.667 m over 9 jumps vs
   0.704 m over 5,000 controls, permutation **p = 0.36**, blind below ~2 m
   injected peak. **[alt]**
7. **Frozen on half the rides, noisy on the rest.** `enhanced_altitude` has
   exactly **1** distinct value on 09-09 (−15.6 m) and 09-14 (−29.2 m), against
   116 and 87 on 09-10/09-12. Where it moves the robust noise floor is
   **0.297 m** (MAD, both rides) against a median puck jump of **0.36 m (09-10)
   and 0.21 m (09-14)**. `baro_alt_m` reads `info.altitude`
   (`JumpFieldView.mc:480`) — that this is the member feeding native
   `enhanced_altitude` is an inference — and **0 of 338 zips** carry `baro_pa`,
   `baro_alt_m` or `baro_src`. **[alt]**
8. **Most flights are never sampled.** 5 of 18 real flights (27.8 %) contain any
   FIT record, against an exact expectation of 5.59 (31.0 %), P(K ≤ 5) = 0.49.
   Monte Carlo on each ride's own timestamps: P(hit) 0.343–0.395 at 0.8 s,
   **0.173–0.199 at 0.4 s**. **[alt]**
9. **18.3 months of ride history precede the first windsurfing-tagged file.**
   77±1 activities are ride-like, 73 reclassified from `generic`/`track_me`,
   spanning 2025-02-28 → 2026-09-03; `frac_1s` across rides is 0.217–0.511 and
   **none reaches 0.90**. Only **3 of 338** FITs carry any developer field
   (09-10, 09-12, 09-14): the sport tag switched on 09-09, the field began
   writing on 09-10 — two seams one day apart. **[hist]**
10. **`pick_matching_fit` (`tools/ride_loop.py:590-616`) mis-picks in measured
    ways.** No sport filter, and it scores shared seconds against a full-file
    trace window: on 08-22 it returns the 0.7-second, 1-record false-start
    `windsurfing` FIT on **0.0 s of shared time**; for `20260910-103108-E2C4`
    (81.3 h window) the correct 09-09 FIT is outside the window entirely and
    "most overlap" takes the longer 09-12 ride. 13 of 24 dated sessions overlap
    no FIT at all, one window ends 2026-09-17, 18 of 24 traces are
    non-monotonic, and one session directory has a `garmin.fit`. **[hist]**

## 2. What it changes

### Ask the rider for (watch side)

- **Data Recording = Every Second** — and ask what the menu *offers*, not only
  that he set it: from (8) a 0.4 s flight has 17–20 % odds of any sample, and
  from (9) no ride in 338 files exceeds `frac_1s = 0.511`. The ≈0.8 hit rate a
  1 s grid would give an 0.8 s flight is a projection from `FitContributor`
  semantics, not a measurement.
- **Elevation recording enabled** — from (7): the channel is one constant on 2 of
  4 rides and `baro_alt_m` reads the same member, so 1.0.1 buys nothing without it.
- **The Jump Height field on a data screen for both profiles**, one field per
  screen — from (9): `generic`/`track_me` for 18.3 months, `windsurfing` from
  09-09, and no developer channel at all on 09-09 or 09-03.
- **A lap press at each jump**, if he is willing — from (5): 14 manual presses on
  09-10, but 47–116 s ahead of a takeoff.

### `tools/ride_loop.py` FIT picking

- **Require strictly positive shared time and a non-zero-width candidate
  window** — `_windows_overlap` admits touching windows and a sole candidate
  beats `best is None`, which is how the 08-22 false start wins on 0.0 s (10).
- **Do not add a sport filter** — 73 of 77 rides are `generic`/`track_me` (9), so
  `sport=windsurfing` discards the corpus. Filter on ride shape: avg ≥ 2.0 m/s,
  30 s sustained ≥ 5.5 m/s, distance ≥ 8 km, elapsed ≥ 30 min.
- **Refuse, visibly, when two ride-shaped FITs sit inside one session window** —
  most-overlap resolves by ride duration, and on `20260910-103108-E2C4` the right
  FIT is not even a candidate (10). Flag a future-ending window or a
  non-monotonic trace the same way.
- **Verify a pick against the watch's own `jump_height` where the FIT has it** —
  value changes land +1.4…+4.3 s after the puck's landings, heights matching
  `height_m / 0.3048` to float32 (1). Only 3 of 338 files allow this check.
- **Never read session-scope `jumps` / `best_jump` / `best_airtime` as this
  ride's data** — written once at save, and 09-12's carry over from 09-10 (2).

### What the baro fields can and cannot do

- **Can:** `baro_pa` reads `rawAmbientPressure`, a member profile elevation
  calibration never touches — the one 1.0.1 channel nothing here rules out; and
  `baro_src` names which API answered, so the first 1.0.1 activity is
  diagnosable from one file. Read `baro_src` first (7).
- **Cannot yet be evaluated at all** — 0 of 338 zips carry the three fields, and
  publishing 1.0.1 is not a ride. `baro_alt_m` reads the member frozen on 2 of 4
  rides; whether it feeds native `enhanced_altitude` needs one FIT with both (7).
- **Cannot resolve this rider's typical jump, or beat the sampling ceiling:**
  0.297 m noise against 0.36 m / 0.21 m median heights (7), the two largest jumps
  showed no in-flight excursion (6), and records are never written faster than
  1 Hz, so the 17–20 % odds at 0.4 s (8) are inherited.

### What the accuracy model should ingest next

- **The watch's `jump_height` value-change stream, per session, as a second
  clock** (1). Where it disagrees with `trace_epoch_utc + takeoff_s`, the FIT
  pick or the epoch is wrong.
- **Nothing that needs more than two aligned sessions.** Only 09-10 and 09-14 are
  placeable on a wall clock (1); 09-09 anchors to ±3.5 min — ~85× the 5 s window
  it needs — and 09-12 has no real puck bundle (2). Under `accuracy-plan.md`'s
  rule (free parameters ≤ sessions / 3), the one-parameter lift-fraction height
  model cannot yet be fitted honestly.
- **The landing-speed drop as a candidate feature** (13 of 16, p = 0.011,
  Δ = −0.74 m/s); approach speed carried, unweighted (4).
- **Heart rate, and each ride's own GPS centroid** — HR is on every record,
  never correlated with anything; the 8 km site separation (5) means wind and
  buoy context must key off the ride, not a fixed launch.
- **An attempt at time-anchoring `gps_metadata` by file order** — 2.3–5.2× the
  record density (5), named in `garmin-dynamics.md` as the single highest-value
  follow-up and untried by either pass.
- **The 73 reclassified rides as cadence context only** — no developer fields, no
  puck trace (9), so no jump content is recoverable from them.

## 3. What is still unmeasured

Verbatim from the four lens reports' Review sections.

### `garmin-wrist.md` — Still unmeasured after this review

- What removed the developer field from the record definition at 20:58:31 on
  09-14 (and restored it at 20:13:01 on 09-10). No crash log, no `device.log`
  entry, no second FIT.
- Whether the watch received jumps 8-10 on 09-14 at all.
- Whether the rider took any undetected jumps on 09-12.
- Why 09-03 carries no developer channel despite being on the profile the
  field was meant to be placed on.
- Why the wrist lag differs between 09-10 (+3.0…+4.3 s) and 09-14
  (+1.4…+3.4 s).
- **Loose thread, outside this file's questions**: the FIT's
  `developer_data_id.application_id` on all three rides is
  `7d0edbd4-24a7-45c2-a6b8-c0886ba34172`, which is **not**
  `manifest.xml`'s app id `873B577243574E27AB454C3FF165E7B4` that
  `FitOut.mc:52-61` says it is "a literal match" of. Measured, not chased.

### `garmin-dynamics.md` — Still unmeasured after review

- **Whether `gps_metadata` can be time-anchored.** fitdecode decodes only fields 3
  and 4 (`enhanced_altitude`, `enhanced_speed`) on these messages — no timestamp,
  no position. Anchoring by file order against surrounding `record` messages is
  plausible and untried. If it works, every 5 s window in §3 can be re-measured at
  2–5× the sample density, which is the single highest-value follow-up here.
- **What 09-12's `session.jumps = 10` counted.** Its `best_jump` is a bit-identical
  carry-over from 09-10 and its `best_airtime` is 0.0, so the counter is not
  trustworthy — but it is also not explained.
- **Why the 09-14 `jump_height` field died at 20:58:31** and never returned for the
  remaining 59 minutes, while the puck kept detecting (jumps #8–#10).
- **Whether the 10 stored jumps in `20260910-103108-E2C4` really are the 09-09
  ride's.** F-35 asserts it; no file in that directory stamps that boot, so it
  rests on the audit's assertion plus the 78.6 min jump span fitting an 85.7 min
  ride. Not independently established here.
- **`data/sessions/20260909-224641-8673/`** — the Puck board's own 09-09 sync: 0
  jumps, empty `jumps.csv`, `accel_fail=97935`. Relevant to the 09-09 ride,
  unexamined by the report and undiagnosed here.
- **Heart rate vs. jump output.** HR is measured on every record of every ride and
  was never correlated with anything.
- **No silicon was touched and nothing under `data/`, `tools/`, `sim/`, `docs/` or
  git was modified by this review.**

### `garmin-altitude.md` — Still unmeasured after review

- Whether `baro_pa` / `baro_alt_m` / `baro_src` behave differently from
  `enhanced_altitude`. No FIT carries them; re-verified across all 338 zips.
  Unchanged by 1.0.1 going live — publishing is not a ride.
- Whether `Activity.Info.altitude` and native `enhanced_altitude` are the
  same value. Needs one FIT carrying both.
- Whether Every Second yields a clean ~1 s grid on this hardware. The 0.8
  hit-probability figure remains a projection from documented semantics.
- GPS-vs-barometer provenance of the 09-10/09-12 wander. Now known to be
  answerable from this corpus; nobody has answered it.
- Whether the Garmin altitude pipeline low-pass filters hard enough to
  attenuate a 1–2 s arc on its own. This is the one live alternative
  explanation for the near-zero in-flight residuals — a frozen *member* and a
  working member behind a slow *filter* look identical in these four files.
  `baro_pa` would separate them.
- Which boot produced 09-09's 10 stored jumps. The implied epoch
  (2026-09-07 21:10–21:17 UTC) matches nothing on record.

### `garmin-history.md` — Still unmeasured after review

- **Why ride `record` streams are thinned.** Smart Recording is the obvious
  candidate, but this corpus cannot separate a user setting from an
  activity-type rule. Nothing here establishes the rider ever had an Every
  Second option on a ride to decline. Settling it needs the watch's own
  settings, not its files.
- **Whether the frozen `enhanced_speed` plateaus are a GPS-dropout hold, a
  firmware bug, or a Smart-Recording interpolation.** Measured that they
  exist and that `distance` disagrees with them; the mechanism is not
  established.
- **Whether `24054330994.zip` (08-20, 5.53 km) is a genuine short ride.**
  Unresolved, as originally stated.
- **The two `20260731`-era and the 09-10/09-14 trace-window anomalies.** No
  `sim/score.py` alignment check was run; the windows are reported as the
  files state them.
- **Physical identity of the three GPS clusters.** No map or address lookup
  was done, by either pass.
- **The 74 non-`windsurfing` rides' jump content.** They carry no developer
  fields, so nothing about jumps on those days is recoverable from FIT.

## Appendix — key tables, copied from the lens reports

### A1. Per-ride watch-side developer fields [wrist]

| ride (UTC start) | zip | dev fields declared | record `jump_height` writes (value-change events) | session `jumps` / `best_jump` / `best_airtime` (final) |
|---|---|---|---|---|
| 09-09 20:24:03 | `24302822289.zip` | **none** | 0 | absent / absent / absent |
| 09-10 19:55:52 | `24315031499.zip` | all 4 (jump_height declared twice) | **8** | **9** / **11.1319 ft (3.3930 m)** / **1.6640 s** |
| 09-12 20:25:57 | `24339421270.zip` | all 4 | **0** | **10** / **11.1319 ft (3.3930 m)** / **0.0 s** |
| 09-14 20:18:07 | `24364408252.zip` | jump_height only | **6** | absent / absent / absent |
| 09-03 20:11:39 | `24229004494.zip` (generic/track_me) | **none** | 0 | absent / absent / absent |

### A2. The `enhanced_altitude` channel, per ride [alt]

| ride | records | distinct alt. values | spread (m) | diff-std (m) | drift, start30→end30 (m) |
|---|---|---|---|---|---|
| 2026-09-09 | 2383 | **1** | 0.0 | 0.0 | 0.0 |
| 2026-09-10 | 3398 | 116 | 32.6 | 0.616 | −4.05 |
| 2026-09-12 | 3194 | 87 | 48.4 | 0.819 | −4.42 |
| 2026-09-14 | 2877 | **1** | 0.0 | 0.0 | 0.0 |
