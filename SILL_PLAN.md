# Sill 🪟👂

**A XIAO nRF52840 Sense on the windowsill at 25th & Broad that listens to and
feels the neighborhood — and turns it into a clean, labeled event stream.**

Trains working through Shockoe Valley. The bells at St. John's. The dawn
chorus. Sirens on Broad. Rain on the glass. One board classifies all of it
on-device and logs *events, not audio* — a tiny, private, timestamped
vocabulary of the corner.

> **Scope of this plan: data collection and classification only.**
> Displays, art pieces, and everything downstream are deliberately out of
> scope. If the event stream is good, everything later is easy; if it isn't,
> nothing later matters. The deliverable of this project is a board that has
> been running unattended for two weeks whose event log matches your own ears.

---

## The product of this project

A continuously appended event log, synced over BLE (or USB), one record per
acoustic/vibration event:

```json
{"ts": "2026-09-14T06:41:22-04:00", "class": "train", "dur_s": 142.0,
 "conf": 0.94, "peak_level": 0.71, "vib_energy": 0.88}
```

That's it. Everything else in this repo exists to make that record trustworthy.

### Event vocabulary, v1

| Class | Primary channel | Notes |
|---|---|---|
| `train` | vibration + audio | The star. Sustained rumble (minutes), fused: vibration envelope AND train-like audio. |
| `train_horn` | audio | Distinctive, delightful, easy to classify. Separate from `train` because horns happen without passes being near. |
| `bell` | audio | St. John's, next door. Scheduled → self-labeling ground truth. |
| `siren` | audio | Broad Street provides. |
| `bird` | audio | One class, not species. Dawn chorus is the seasonal payload. |
| `rain` | audio + vibration | On-glass rain is loud in both channels. |
| `wind` | vibration + audio | Buffeting; mostly a confuser to be modeled so it doesn't pollute `train`. |
| `road` | audio + vibration | Buses/trucks/cars lumped. Exists mainly so the model has somewhere to put them that isn't `train`. |
| `other/quiet` | — | Everything else, including all human sounds. |

**Privacy stance (non-negotiable, bake into firmware):** after the training
phase, raw audio never leaves the board and is never stored on it. Inference
runs on ~1 s windows in RAM, windows are discarded, only class labels + levels
persist. There is deliberately **no** speech/voice class — human activity is
`other` and is not logged as such. During the training-capture phase audio IS
recorded (you can't train without it): tethered to your laptop, stored locally,
never committed to the repo, deleted when the dataset ships.

---

## Hardware & platform decisions

Seeded in the style of Jump-height's DECISIONS.md — copy this table into
`DECISIONS.md` in the new repo and keep appending.

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Board | XIAO nRF52840 **Sense** (onboard PDM mic + LSM6DS3TR-C IMU + 2 MB QSPI flash) | Everything needed is on one $16 board; no wiring for v1. |
| 2 | Power, v1 | **USB-powered** at the window. Battery/solar is a later phase, not a v1 problem. | Removes the entire power-engineering workstream while the real risk (classification quality) is retired. An outlet near a window is not scarce. |
| 3 | Placement, v1 | **Indoors, coupled to the window glass** (adhesive/suction mount pressing the board's face to the glass). | Glass is a great vibration diaphragm and a decent acoustic one; zero weatherproofing. Phase 2 includes an A/B/C placement experiment (inside-glass vs. cracked-window vs. sheltered-outside) before committing the final spot. |
| 4 | Firmware platform | Arduino via **Seeed nRF52 Boards core (the Adafruit-based, non-mbed one)** | PDM + LSM6DS3 + QSPI flash + BLE all have working libraries; Edge Impulse exports an Arduino library for exactly this board. Zephyr is the "if we outgrow it" path — noted, not chosen. |
| 5 | Audio capture | PDM mic, **16 kHz, 16-bit mono** | Standard rate for environmental-sound models; well inside the mic's usable band; 32 KB/s streams easily over USB CDC. |
| 6 | Vibration capture | LSM6DS3 accelerometer, **~416 Hz ODR, ±2 g, FIFO-buffered** | Train/traffic rumble lives under ~100 Hz; 416 Hz gives comfortable margin. FIFO lets the MCU sleep between drains later. Gyro off (nothing here rotates). |
| 7 | ML pipeline | **Edge Impulse**, audio classifier on mel-spectrogram windows, int8 export | The XIAO BLE Sense is an officially supported EI target; fastest path from labeled clips to on-device int8 model. Vibration joins by **fusion at the event level**, not inside the neural net (see #8). |
| 8 | Train detection = fusion rule | `train` requires sustained vibration envelope AND train-class audio within the same window. Audio-only → `road`/`other`; vibration-only → logged as `rumble` (unclassified). | The two channels fail differently (wind fools the mic, a heavy truck fools the accel). Requiring both makes the flagship class robust without inventing a multimodal net. |
| 9 | Clock | No battery RTC on board. Epoch is set on **every** USB/BLE connect; events stored as boot-relative ms + epoch anchors, resolved at sync time. | 32 kHz crystal drifts seconds/day — irrelevant if the sync tool re-anchors daily-ish. A `clock_drift` field in sync output keeps it honest. |
| 10 | Event storage | Binary ring log on the 2 MB QSPI flash, ~32 B/record → **~65,000 events** before wrap. | Even a loud week is a few thousand events. Storage is a non-problem; treat it as such. |
| 11 | One-command tooling | Everything runs through **`./tools/sill`** (selftest, monitor, capture, label, sync, simtest), every flow rehearsable against **`tools/fake_device.py --fake`**. | Proven pattern from Jump-height: the wizard/fake-device/one-command discipline is what made that project buildable. Steal it wholesale, including logging every session to `data/logs/`. |
| 12 | Dataset hygiene | Train/val/test split **by day**, never by clip. | Clips sliced from the same train pass are near-duplicates; splitting by clip leaks and inflates accuracy. This is the #1 way projects like this lie to themselves. |

### Repo layout

```
sill/
├── PLAN.md              # this file
├── DECISIONS.md         # table above, appended as decisions happen
├── config/params.json   # single source of truth: rates, thresholds, class list
├── firmware/            # Arduino sketch + modules (capture, detect, infer, log, ble)
├── model/               # Edge Impulse exports (int8 .zip → library), eval reports
├── tools/
│   ├── sill             # the CLI front door (Python)
│   ├── fake_device.py   # emulates the serial/BLE protocol on a pty
│   └── tests/
├── web/                 # static vanilla-JS: live monitor + clip labeler (Web Serial/Web Bluetooth)
├── data/                # gitignored: raw captures, labels; committed: tiny test fixtures
└── docs/
```

---

## Phases

Each phase has an exit criterion. Don't start the next one without it.

### Phase 0 — Bring-up (a weekend)

- Toolchain installs via `./tools/sill setup`; board flashes; serial console works.
- PDM mic streaming: clap test shows a clean spike; browser page renders a live
  spectrogram over Web Serial.
- IMU streaming: tapping the window shows a clean accel transient at 416 Hz.
- `./tools/sill selftest` — mic alive, IMU WHO_AM_I + noise floor, flash R/W,
  clock set — with plain-English fix hints (port of the Jump-height pattern).
- `fake_device.py` exists from day one and `./tools/sill simtest` passes in CI.

**Exit:** a screenshot-worthy live spectrogram of a real passing train, seen
from the window mount. (If a train doesn't show up in audio+vibration at this
stage, the placement conversation happens NOW, not after the model is built.)

### Phase 1 — Tethered recorder (a week of evenings)

The board becomes a dumb, reliable, dual-channel streamer; the laptop does the
thinking. Continuous 16 kHz audio + 416 Hz accel over USB to
`./tools/sill capture`, which keeps a rolling ring buffer (pre-roll lives on
the laptop — the board's 256 KB RAM doesn't need to hold it) and cuts clips:

- **Auto-triggered:** energy gates on either channel (audio band energy,
  vibration envelope) open a capture with ~10 s pre-roll, close on quiet.
- **Manually:** hotkey in the monitor page ("that's a horn — mark it").
- Every clip lands as `data/raw/<date>/<clipid>.wav + .accel.csv + .json`
  (trigger reason, levels, timestamps).

**Exit:** one full evening's session captures the freights you heard, with
synced audio+vibration, and nothing you heard is missing from the auto-trigger
log. Trigger thresholds land in `config/params.json`.

### Phase 2 — The data campaign (2–3 weeks, mostly passive)

The recorder runs whenever you're around; you label in batches.

- `./tools/sill label` serves `web/labeler`: plays a clip, shows spectrogram +
  vibration trace, one-keystroke class assignment, slice/trim, export to
  Edge Impulse format.
- **Self-labeling shortcuts:** St. John's bell times give near-free `bell`
  labels (confirm the schedule in week one and write it into
  `config/params.json`); dawn chorus is a daily standing appointment for
  `bird`; a rainy day is a `rain` harvest.
- **Placement A/B/C** (inside-glass / cracked-window / sheltered-outside):
  same 24 h weekday for each, compare per-class SNR and trigger counts, then
  commit to one placement and record the decision.

Collection targets (Edge Impulse rule of thumb is ~10 min/class minimum;
aim higher on the classes that matter):

| Class | Target | Feasibility at 25th & Broad |
|---|---|---|
| `train` | 60+ passes (≈2+ hrs) | CSX obliges daily; 2 weeks is plenty |
| `train_horn` | 30+ events | Comes with the territory |
| `bell` | 20+ ring events | Scheduled — trivial |
| `siren` | 20+ events | Broad St. provides |
| `bird` | 60+ min | Dawn chorus daily |
| `road` | 60+ min | Constant |
| `rain` | 30+ min | Opportunistic — grab every storm |
| `wind` | 30+ min | Opportunistic |
| `other/quiet` | 120+ min | Free; sample across all hours |

**Exit:** targets met, labels spot-checked, dataset split **by day** into
train/val/test, exported. Raw audio stays on the laptop, out of git.

### Phase 3 — The model (a week)

- Edge Impulse project: mel-spectrogram (or MFE) features on ~1 s windows,
  small conv net, int8 quantized, EON-compiled for the nRF52840.
- Hold out the test *days* until the very end. Report a confusion matrix in
  `model/EVAL.md`, not a single accuracy number.
- Quality bars to beat before going on-device: `train` recall > 0.9 (with the
  fusion rule it only needs to be good, not perfect), `bell` precision > 0.95
  (a false bell is embarrassing), `siren` recall > 0.8. `road` vs `train`
  audio confusion is expected and acceptable — fusion (#8) exists for exactly
  that.
- Budget check: model RAM arena + 1 s audio ring + BLE stack must fit 256 KB
  with slack; EI reports this per-build. If tight, shrink the net, not the
  sample rate.

**Exit:** confusion matrix on held-out days meets the bars; model exported as
an Arduino library into `model/`.

### Phase 4 — On-device inference + event assembly (a week)

- Inference on overlapping 1 s windows, continuous.
- **Event assembler:** hysteresis + merging turns window-level labels into
  events with start/end/duration/peak (a 2-minute train is one event, not 120
  window hits). Fusion rule from decision #8 applied here.
- Events → QSPI ring log. Audio path now terminates in RAM; recording code is
  compile-flagged out of production builds (privacy stance enforced by build,
  not by intention).
- `./tools/sill tail` shows live events over serial for eyeball-vs-ears QA.

**Exit:** an evening of `sill tail` next to an open ear: every train, bell,
and siren you personally hear appears as one correctly-labeled event, and the
log contains nothing you can't account for.

### Phase 5 — Untethered: BLE sync + soak (a week + a fortnight of patience)

- Nordic UART Service mirroring the serial protocol (the Jump-height pattern:
  one protocol everywhere — serial, BLE, fake device).
- `./tools/sill sync`: connect, set clock, report drift, pull new events as
  JSONL into `data/events/`, verify, ack. Web monitor gains a Web Bluetooth
  connect button for phone-based spot checks.
- Watchdog + brownout recovery + storage-wrap behavior. `sill report` bundles
  logs/config/selftest for remote debugging (port from Jump-height).
- **The soak:** 14 days unattended on the window. Daily `sill sync`. Keep a
  small human diary (heard the 6:40, heard bells at noon) and diff it against
  the log weekly.

**Exit — and the finish line for this whole plan:** a 14-day event log with
no gaps, no reboots-lost-data, clock drift accounted for, and >90% agreement
with your diary on trains/bells/sirens. That JSONL file is the foundation
everything else gets built on.

### Phase 6 (optional, later) — Power & weather

Battery + IMU-FIFO/PDM duty-cycling for months-per-charge, sheltered outdoor
enclosure, solar. Explicitly deferred: none of it de-risks classification,
and USB at a window is fine indefinitely.

---

## Risks & open questions

- **Glass placement muffles/colors outdoor audio.** The model must be trained
  on data from the *final* placement — hence the Phase 2 A/B/C experiment
  before mass labeling. Retraining after a placement change is a real cost;
  pick once.
- **Wind is the great impostor** (broadband rumble in both channels). It gets
  its own class and its own collection target for exactly this reason.
- **Seasonality:** a September-trained model meets January (bare trees, HVAC,
  no birds, different rain). Plan a small "top-up" labeling round each season;
  the tethered recorder never gets deleted, only retired to a drawer.
- **Class granularity temptation.** Bird *species*, bus-vs-truck, which-bell:
  all v2+. Every added class dilutes the training budget for `train`.
- **Bell schedule** needs confirming by ear in week one (hours? services?
  count of rings?) — it's both ground truth and a clock-sanity check.
- **Does the fusion rule need the horn?** If `train` audio recall
  disappoints, `train_horn` + vibration may carry detection alone. The event
  assembler should make swapping fusion rules a config change, not a rewrite.

## Name

Working name **Sill** (a Sense on the sill). Alternatives if it doesn't stick:
`overhear`, `cornerlog`, `broad-and-25th`. Rename before the repo exists, not
after.
