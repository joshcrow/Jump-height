# Data pipeline — capture, label, evaluate, improve

> ## ⚠️ PARTLY SUPERSEDED — check [docs/STATUS.md](STATUS.md) first
>
> `STATUS.md` is the single source of truth; where they disagree, this file is
> stale.
>
> **The circular-labeling defect this banner used to warn about is FIXED
> (2026-08-15).** The old procedure derived "true height" from counted airborne
> frames via `h = g·T²/8` — the very formula under test — so it could only ever
> measure timing agreement and would pass whether or not wings are ballistic.
> *Labeling* below is rewritten around an independent ruler measurement, and
> the fix is now **enforced in code, not just documented**: `labels.csv` carries
> a `height_src` column and `sim/evaluate.py` excludes non-independent heights
> from RMSE rather than quietly scoring them.



Every open question now converges on one dependency: **real labeled water data.**
The arm-ceiling check (`wing-ballistic-sim.md`), `height_scale`
calibration, landing-attitude direction, wing-rotation accuracy
(`gyro-sim-plan.md`), and the whole riding-dynamics half
(`riding-dynamics-map.md`) are all "needs real S0 traces."
This doc is the loop that turns those traces into validated improvement.

**You already have ~70% of it.** `./tools/jump sync` captures, `drop` calibrates,
`replay`/`report` analyse, `simtest`+`golden.py` gate C++/Python parity, and
`config/params.json` is the single source of truth. The missing piece was
**ground truth** — everything to date was validated against *synthetic* truth
(`sim/generate.py`). This adds the real-data half: a labels schema and a corpus
evaluator (`sim/evaluate.py`, `./tools/jump eval`).

## The loop

```
capture ──▶ sync ──▶ label ──▶ eval ──▶ regression-gate ──▶ tune ──▶ deploy ──▶ (repeat)
 (ride)   (USB/BLE)  (video)  (score)   (block if worse)  (params)  (flash)
```

- **Per session:** capture ritual → `jump sync` → label from video → the session
  now scores in the corpus.
- **Per algorithm change:** edit `config/params.json` (or the detector) → `jump eval
  --baseline <saved>` over the whole corpus → block regressions → tune on the *train*
  split → confirm on the *test* split → `jump gen`/`flash`.

## Session layout

`./tools/jump sync` writes `data/sessions/<id>/` with `trace.csv` and `jumps.csv`.
Two files are added by hand (or a labeling tool) to make a session *scorable*:

```
data/sessions/<id>/
  trace.csv      t,mag                                          — |a| in g, ~50 Hz (the raw signal)
  jumps.csv      n,takeoff_s,airtime_raw_s,airtime_s,height_m   — the device's own detections
  labels.csv     ← NEW: video-derived ground truth (schema below)
  session.json   ← NEW: provenance + train/test split (schema below)
```

**Nesting is allowed, and since 2026-08-23 it is actually found.** A session is
any directory holding both `trace.csv` and `labels.csv`, at *any* depth —
`jitter-check/20260815-190012/`, `walk-overnight/pull-a/<id>/` and the flat
`<id>/` above are all discovered. The flat shape is the simple case, not the
only one.

> This is written down because the old scan was `root.glob("*")`, exactly one
> level, and the repo's only `labels.csv` sat two levels down. `jump eval`
> printed *"No labeled sessions found"* — which is the message for *"you have
> not labeled anything yet"* — for the entire eight days that session existed.
> A miss was indistinguishable from an absence (CLAUDE.md rule 3). The
> evaluator now also reports directories that hold a `trace.csv` but no
> `labels.csv`, by name, because *"14 traces, none labeled"* and *"nothing
> here"* are different facts.

### labels.csv

One row per ground-truth event, keyed to **trace time**. Header required.

```
event,t_start_s,t_end_s,height_m,height_src,rotation_deg,landing,notes
jump,12.34,,1.82,ruler,,flat,clean
jump,41.07,,2.65,ruler,,tail,slight tail-first
trick,58.9,60.4,,,360,flat,backside 360
foil,70.0,138.5,,,,,long foil run
carve,95.2,96.1,,,,,hard bottom turn
```

- `event`: `jump | trick | foil | carve | pump | wave | crash | …` (extensible).
- `t_start_s`: trace time of the event — a jump's **takeoff**.
- `height_m`: video-derived TRUE apex — the accuracy/calibration truth. **Scored today.**
- `height_src`: **how that height was obtained. This decides whether it counts.**
  - `ruler` — apex measured against a known length in frame (see *Labeling*).
    Independent of the accelerometer and of `h = g·T²/8`. **Real truth.**
  - `sim` — a synthetic session, where the apex is an *input* to the generator
    rather than a re-derivation of the device's own output. Valid for testing
    the machinery; says nothing about real wings.
  - `timing` — frame-counted airtime put through `h = g·T²/8`. **Circular:
    that is the formula the firmware uses.** Kept for the record, excluded
    from RMSE by `sim/evaluate.py`.
  - blank — unknown provenance, treated as `timing`. Assuming the friendly
    reading is precisely how a circular number becomes a published accuracy
    claim.
- `rotation_deg`, `landing` (`flat|nose|tail|rail`), `t_end_s`, `notes`: for the
  trick / landing / riding families as those labels accumulate. Blank is fine.

Only `event=jump` rows with a `height_m` **and an independent `height_src`**
are scored for accuracy right now (detection is scored regardless); the schema is
deliberately one file so riding/trick labels grow in place.

### session.json (optional but recommended)

```json
{"unit":"sense-01","firmware":"0.4.3","params_sha":"<git sha of config/params.json>",
 "rider":"bro","gear":"6m wing / 1200 foil","conditions":"18 kt, chop","split":"train","notes":""}
```

`split` ∈ `{train, test}` drives held-out evaluation. **Never tune thresholds and
validate on the same sessions** — the repo's threshold-self-derivation rule
(research.md §8: "desk guesses would be fiction") only holds if you don't overfit
to three of your brother's sessions. Missing file → split `unknown`.

## Procedures

### Capture ritual (makes labeling possible)
1. Power on; do a **sync marker** — **three deliberate flat drops of the board
   onto a soft surface, ~2 s apart**, which the sensor and the camera both
   catch. Not a finger tap: a tap is 2-5 ms, and the stored trace is decimated
   to 50 Hz, so a tap may not appear in it **at all**. A flat drop is ~0.3 s of
   free-fall plus a spike — unmissable, auto-detectable, and it doubles as a
   zero-g reference.
2. **Also write down the wall-clock time and run `./tools/jump sync`**, which
   records `trace_epoch_utc` in `session.json`. The puck has no RTC; trace time
   is seconds since boot. That anchor is what makes 20-40 short kayak clips
   alignable at all — a single sync marker only aligns one continuous recording.
3. Ride. Film **1080p/120, not 4K/30** — see *frame rate* below; it is not a
   quality preference, it is the difference between measuring the effect and
   measuring the quantisation.
4. **Get the camera roughly abeam of the jump line**, and get the rider
   full-height in frame at apex. This is a briefing item for whoever is in the
   kayak, and it is the single thing that decides whether height is measurable.
5. Repeat the sync marker at the end. `jump sync` to pull the trace.

### Labeling

> **The old procedure here was circular and produced a number that could not
> fail.** It derived "true height" from counted airborne frames via
> `h = g·T²/8` — the formula the firmware uses. Scoring our `g·T²/8` against a
> label built from `g·T²/8` measures timing agreement and nothing else. It
> would report a small, confident RMSE whether or not wings are ballistic,
> which is the entire open question the water session exists to answer.
> Rewritten 2026-08-15; `sim/evaluate.py` now enforces it via `height_src`.

> **`jump x3` in your notes is a COUNT, not three timings — and the evaluator
> now refuses it.** `tools/label.py:116` expands `jump xN` into N rows that
> all carry the *same* `t_start_s`, deliberately: it means "roughly N jumps
> happened around here", which is a useful note and is not per-jump ground
> truth. It prints that caveat as it writes — to a terminal, on the day you
> ran it, in scrollback nobody re-reads. So the disqualification now lives in
> the file instead: `sim/evaluate.py` excludes any session whose `jump` rows
> share a takeoff instant, names it, and says why.
>
> Why refusing beats scoring: two takeoffs cannot occur at the same instant,
> so those rows can only ever produce `matched 0/N … spurious N` — and both
> this document (below) and `docs/session-card.md` tell the reader that
> signature means a video↔trace **sync** error and explicitly *not* a broken
> detector. The tool would have handed over a confident wrong diagnosis and
> sent someone to re-check a sync marker that was fine. Added 2026-08-23,
> after this was found to be the state of the only labeled session in the repo.

There are **two independent truth channels**, and they answer different
questions. Record both; never let one masquerade as the other.

#### Channel A — timing (validates the detector, NOT the height model)
Count airborne frames, divide by frame rate, get airtime `T`. Compare against
the device's `airtime_s`. This is a genuine, useful check: it proves the
detector finds takeoff and landing correctly on real water motion.

What it **cannot** do is validate height, because height is computed *from*
airtime. Put a timing-derived height in `labels.csv` if you like, but mark it
`height_src=timing` and it will be excluded from RMSE — deliberately.

#### Channel B — the ruler (validates the height model — this is the new part)
Measure apex displacement against a **known length that is in the same plane,
at the same distance, and roughly vertical**.

**Use rider height in gear as the ruler.** Not the mast:
- The rider is ~2× the mast, so the same pixel error is half the relative error.
- The rider is high-contrast against sky; a mast is dark against dark water.
- The rider is unambiguously vertical at apex; a mast is at whatever angle.

**Zero is the board's own position, NOT the horizon.** This one matters more
than it sounds:

> A camera 0.8 m above the water sees the water plane 0.8 m *below* its own
> level line — **at every distance**. Using the horizon as the zero therefore
> adds a fixed offset to every jump, and on a 1.5 m jump that is **+53 %**.
> It does not average out, it does not show up as scatter, and it survives
> every sanity check you would think to run. It just makes the device look
> like it reads low, forever.

So: take the board's underside in the **takeoff** frame as zero, the board's
underside in the **apex** frame as the height, and express the difference in
rider-heights. ±15 % is plenty — the failure this has to catch is a kite-like
**2.3× overshoot**, which is enormous. Record it as `height_src=ruler`.

#### Frame rate: 1080p/120, not 4K/30
At 30 fps a 1.0 s flight is quantised to ±1 frame = ±33 ms, which propagates
to height as `dh = g·T·dT/4 ≈ 8 cm` — **6.7 %**, the same size as the effect
under test. At 120 fps that falls to ~2 cm. Resolution buys you nothing here;
temporal resolution buys you the measurement. 240 fps is better still if the
light allows it, but 120 is the point at which quantisation stops mattering.

#### The strongest version: fit `g_eff` directly
With a known ruler and a known frame rate you have the board's vertical
position in every frame of the flight. Fit a parabola to it and you recover
`g_eff` — the vertical acceleration during flight — **directly from the
video**.

That is the same quantity the accelerometer measures, from a completely
different instrument. It turns the water session from "does our number look
about right?" into two independent measurements of the one physical quantity
this whole project turns on: *is a wing-foil jump ballistic?* The sim says
1.0-1.07× ballistic; a kite is 2.3×. Those are not close, and this measurement
separates them outright.

Start with a spreadsheet; a scrub-and-tag tool is only worth building once
volume justifies it.

#### And the primary result needs no video at all
Worth stating plainly, because it is the insurance policy: the airborne-|a|
check — median airborne |a| and |ω| per jump, now recorded in `JumpRecord` —
comes from the trace alone. **If the filming goes badly, the session still
answers its question.** Video is the secondary, height-scale check.

#### How good does the sync marker have to be? (measured, 2026-08-11)

`evaluate.py`'s `MATCH_WINDOW_S = 1.0` — a detected takeoff within 1.0 s of a
labeled one is "the same jump". Dress-rehearsed end to end (8 sim jumps through
the real detector as `jumps.csv`, true apexes as `labels.csv`) with the labels
deliberately shifted:

| video↔trace error | jumps matched | what you see |
|---|---|---|
| ±0.5 s | 8/8 | identical RMSE — harmless |
| +0.8 s | 8/8 | still fine |
| **+1.0 s** | **5/8** | **partial — the dangerous one** |
| ≥1.2 s, or −1.0 s | 0/8 | total, and obvious |

**So: land the sync marker inside ~±0.8 s and nothing is lost.** A firm triple-tap
visible on both video and trace clears that easily. The window is asymmetric
(−1.0 s already fails while +1.0 s partly works) because detection lags takeoff
slightly, so a negative shift eats the window from the near side.

**THE DIAGNOSTIC THAT MATTERS — `missed ≈ spurious` means SYNC, not the detector.**
A sync error reports *both* "missed 8" *and* "spurious 8": every true jump goes
unmatched while every real detection is orphaned. A detector genuinely missing
jumps does **not** simultaneously invent an equal number of spurious ones. On
water-day evening the naive reading of "matched 0/8" is "the device doesn't
work" — panic — when it is a column offset in a spreadsheet.

The partial case is the trap: at 1.0 s you get 5/8 and a perfectly plausible RMSE
computed from a silent subset, with detection rate reading 0.625 and making the
detector look broken. Check missed-vs-spurious before believing any detection
rate.

Credit where due: `eval` reports `RMSE —` rather than a number when nothing
matched, so it never fabricates an accuracy figure out of zero samples.

### Evaluate + regression-gate
```
./tools/jump eval --verbose                 # score the whole labeled corpus
./tools/jump eval --split test              # held-out only
./tools/jump eval --save baselines/v0.4.3.json      # freeze a baseline
# after changing config/params.json or the detector:
./tools/jump eval --baseline baselines/v0.4.3.json  # exit 1 if RMSE/detection regress
```
The evaluator re-runs the detector on each `trace.csv` (so a `params.json` change is
seen corpus-wide without reflashing) and scores detection + height against
`labels.csv`. It reports against the Marčiš'21 video-validated benchmark (Surfr
0.51 m, WOO3 0.70 m RMSE) so the number has a competitive frame.

### Calibration record (per physical unit)
- `airtime_offset_s` — `jump drop` (bench drop tests). Already wired.
- `height_scale` — from the first video-labeled session (fit detected vs true height).
- gyro bias — pre-takeoff stationary bias subtraction
  (`gyro-sim-plan.md` §4).
- gyro scale-factor — in-situ `mikoff/imu-calib` (MIT), cited in
  `gyro-prior-art.md` §2.
- **mount lever-arm `r`** — from g4: the `ω²r` detector correction needs it, and a
  ±20% error reintroduces failures. A mount-position calibration step.

Store these per unit with the `params_sha` and firmware that produced each session,
so a trace always knows which calibration made it.

## Don't over-build

At zero labeled sessions the whole infrastructure is: the existing `data/sessions/`
tree + `labels.csv`/`session.json` + `jump eval`. Cloud storage, a database,
dashboards, a labeling UI — those come only with multiple units, other riders, or
hundreds of sessions. **The first ten labeled sessions matter more than any infra.**

## Why this is a continuation, not a rebuild

`sim/evaluate.py` is the real-data twin of `sim/run.py`'s `report_vs_truth`: the sim
proved the *physics* against synthetic ground truth; the labeled corpus tunes and
validates the *detection* against real ground truth. Same matching, same metrics —
you swap synthetic truth for video labels and the machinery is already there. The
one instrumented water session that validates the arm ceiling *also* calibrates
`height_scale`, labels landings, and seeds the riding-dynamics thresholds — they all
ride along on the same day on the water.
