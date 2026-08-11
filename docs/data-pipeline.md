# Data pipeline — capture, label, evaluate, improve

Every open question now converges on one dependency: **real labeled water data.**
The arm-ceiling check ([wing-ballistic-sim.md](wing-ballistic-sim.md)), `height_scale`
calibration, landing-attitude direction, wing-rotation accuracy
([gyro-sim-plan.md](gyro-sim-plan.md)), and the whole riding-dynamics half
([riding-dynamics-map.md](riding-dynamics-map.md)) are all "needs real S0 traces."
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

### labels.csv

One row per ground-truth event, keyed to **trace time**. Header required.

```
event,t_start_s,t_end_s,height_m,rotation_deg,landing,notes
jump,12.34,,1.82,,flat,clean
jump,41.07,,2.65,,tail,slight tail-first
trick,58.9,60.4,,360,flat,backside 360
foil,70.0,138.5,,,,long foil run
carve,95.2,96.1,,,,hard bottom turn
```

- `event`: `jump | trick | foil | carve | pump | wave | crash | …` (extensible).
- `t_start_s`: trace time of the event — a jump's **takeoff**.
- `height_m`: video-derived TRUE apex — the accuracy/calibration truth. **Scored today.**
- `rotation_deg`, `landing` (`flat|nose|tail|rail`), `t_end_s`, `notes`: for the
  trick / landing / riding families as those labels accumulate. Blank is fine.

Only `event=jump` rows with a `height_m` are scored right now; the schema is
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
1. Power on; do a **sync marker** — one distinct, sharp event the sensor *and* the
   camera both catch (a firm triple-tap on the board, or a deliberate small hop).
   It gives video↔trace a shared `t=0`.
2. Ride. Film at **240 fps** for jumps (airtime to ±1 frame ≈ ±4 ms → height via
   `h = g·T²/8`); a wider, lower-fps shot is fine for riding/wave context.
3. Repeat the sync marker at the end. `jump sync` to pull the trace.

### Labeling
Align video to trace on the sync marker, then tag events into `labels.csv` at their
trace timestamps. For jumps: takeoff time + true height from counted airtime frames.
Start with a spreadsheet; a scrub-and-tag tool is only worth building once volume
justifies it.

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
  ([gyro-sim-plan.md](gyro-sim-plan.md) §4).
- gyro scale-factor — in-situ `mikoff/imu-calib` (MIT), cited in
  [gyro-prior-art.md](gyro-prior-art.md) §2.
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
