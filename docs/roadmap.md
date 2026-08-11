# Roadmap

A phased plan that de-risks the hard parts early. Each phase has a concrete
**"done when"** so you know when to move on. You can get real value (and answer
"how high does my brother jump?") by the end of Phase 2.

## Phase 0 — Prove the algorithm, no hardware ✅ *(available now)*

Validate the whole concept in software before spending a cent: airtime →
height physics (`docs/algorithm.md`), the detection state machine
(`sim/detector.py`, mirrored in firmware), a synthetic IMU generator with
known jumps (`sim/generate.py`), and a test harness comparing detected vs
true height (`sim/run.py`).

**Done when:** `python3 sim/run.py` detects the synthetic jumps with small
height error — it does; this is the starting point.

## Phase 1 — Bench firmware ✅ COMPLETE *(hardware-validated 2026-07-25)*

Got the same algorithm running on real hardware on the desk — the runbook
for this phase is **[../BUILD.md](../BUILD.md)**. Delivered: FireBeetle 2
ESP32-E + MPU-6050 firmware (`firmware/`) at ±8 g / 200 Hz with a
clone-tolerant raw driver and a power-on self-test with fix hints;
one-command flash + wiring check (`./tools/jump flash` / `selftest`);
guided assembly verification (`./tools/jump desktest`, 3 tosses); trace
logging to flash with offline replay (`./tools/jump sync` / `replay`);
and, on hardware, a passed desk test on the real assembly (untethered
tosses) plus a drop-calibration run with the correction stored in device
memory (NVS).

**Done when:** `./tools/jump desktest` passes on the real device and a measured
drop reads correctly after calibration.

## Phase 2 — On the water 🌊

The real test — and where you get your answer.

- [ ] Battery power (charge over USB when the capsule is open)
- [ ] Waterproof capsule; **bucket-test it empty first**; floats; tethered
- [ ] Mount on the board (GoPro adhesive, center deck); capture a session with
      your brother wing foiling
- [ ] **Ground truth:** film some jumps at 120–240 fps; count airborne frames for
      true airtime
- [ ] Tune thresholds in **`config/params.json`** against the synced trace
      (`./tools/jump replay`), set `height_scale` from the video if needed, then
      `./tools/jump flash` — one file drives firmware, simulator, and analysis
- [ ] **Score it, don't eyeball it:** tag the video-derived truth into
      `labels.csv` and run **`./tools/jump eval`** (`sim/evaluate.py`) to score
      the detector against a held-out `test` split — schemas + workflow in
      **[data-pipeline.md](data-pipeline.md)**. The desk half is built and
      exercised; the **first labeled water session gates everything downstream**.

**Done when:** detected heights match video-derived heights within your accuracy
goal (aim for ~10%). Now you actually know how high he jumps.

## Phase 3 — App & live stats ✅ COMPLETE *(hardware-validated 2026-07)*

BLE in the firmware mirrors the exact serial protocol over a Nordic UART
Service (NimBLE; compiles clean, 42% of the new 1.5 MB partition). The
browser app (`web/`, served by `./tools/jump web`) shows live
height/airtime/best/count over **Web Bluetooth**, plus self-test +
session download + CSV export over **Web Serial**, Playwright-tested
against a mock device; session history/export lives in browser
localStorage with per-session CSV. A **zero-install browser flasher**
(ESP Web Tools) adds an Install button to the web app, with binaries
staged locally by `./tools/jump web` and built/published to GitHub Pages
by CI (`.github/workflows/build.yml`). On hardware: BLE validated
end-to-end (Bluefy on iPhone, live jumps, sync, bench flows), and GitHub
Pages is live at joshcrow.github.io/Jump-height with the board flashed
from the tooling.

**Done when:** you can see jumps pop up live on a phone and review a session
afterward — done, on both software and hardware.

## Phase 3.5 — WiFi sync mode *(RETIRED 2026-07-29, unbuilt)*

Fully scoped in 2026-07 (hotspot "beach sync" mode, WebSocket bridge,
station mode) as the answer to BLE's bulk-speed and iOS weaknesses.
Retired without building: the owner went all-in on the Sense
([sense.md](sense.md)), which has no WiFi at all, and every WiFi
justification dissolved on that path — binary trace + BLE cover sync
speed, the Garmin field + FIT cover live/auto-archive, and Nordic DFU
covers updates. The full scope lives in git history (any commit up to
`bf4d2aa`); revive only if an ESP32-class board ever returns.

**Companion — ESP32 OTA scope ([ota.md](ota.md))**: likewise retired to a
tombstone; its one shipped survivor is the §4.5 binary trace format, live
on the Sense (`trace_codec.h`, DECISIONS #24). Updates on the v2 board are
Nordic DFU ([sense.md](sense.md) §3.3).

## Backlog study — what else the same hardware can measure *(thought through 2026-07)*

The pipeline (50 Hz trace → sync → offline analysis → report) makes most new
metrics pure software over data already being recorded. Tune everything
against the first real water-session trace + video — desk guesses about what
foiling "feels like" to the sensor would be fiction. Literature context for
several of the tiers below (time on foil, chop meter, crash counter, pop
strength, clip-that, landing quality) is gathered in
[research.md §8](research.md); not restated per item here.

**Tier A — trace mining, zero firmware change** *(build after water session 1;
detector-vs-truth scoring already automated via `./tools/jump eval` — see
[data-pipeline.md](data-pipeline.md), `sim/evaluate.py`)*
- **Time on foil**: classify 1 s windows by vibration signature (foil flight
  is smooth, taxiing choppy, bobbing still) → % on foil, longest flight.
- **Crash counter**: big spike with no preceding free-fall = impact; count,
  rank ("gnarliest 6.2 g at 41 min").
- **Landing quality**: smooth riding after the landing spike = stomped;
  quiet/floaty = swam. Per-jump verdict.
- **Session rhythm**: jumps per 10 min, rest gaps, intensity curve.
- **Pop strength**: peak g in the ~0.3 s before free-fall (takeoff loading).

**Tier B — small firmware additions, same sensor**
- **"Clip that" gesture**: three deliberate board-slaps = device tags a
  timestamp; marks sync out and line up with camera footage (self-indexing
  highlight reel).
- **Conditions fingerprint**: coarse chop-meter per session (glassy → rough)
  so day-to-day comparisons normalize.

**Tier C — wake the LSM6DS3TR-C's unused gyroscope** (the Sense's 6-axis IMU;
the ESP32-era MPU-6050 path was pruned, DECISIONS #27). Fully scoped + desk-
validated in **[gyro-sim-plan.md](gyro-sim-plan.md)** and **[gyro-prior-art.md](gyro-prior-art.md)**.
- **Spin detection**: integrate |ω| during airtime → 180/360 labels per jump.
  Adopt quaternion AHRS (dlaidig/vqf MIT, or vendored xio Fusion); rotation
  counting is published to ±8.18°/1.42% (Merz/Gorges 2025).
- **Carve analytics**: jibe/tack count, hardest-carve g (see the broader
  riding-metric map, [riding-dynamics-map.md](riding-dynamics-map.md)).
- **Detector hardening**: rotation is exactly what hides free-fall from the
  accel-only detector (bench toss 3 proved it; the **g4 sim** now quantifies it —
  the raw detector fails at ~300 dps peak spin, and inlining a per-sample ω²r
  subtraction into the detector recovers it, so the gyro is a **detector
  hot-path input**, not a recording-only extra).
- Cost note: gyro adds ~**0.9 mA** combined (accel+gyro, high-performance) /
  ~0.45 mA normal — NOT the ~3.6 mA once assumed (that was the ESP32-era
  MPU-6050). Config: ±2000 dps, ODR ≥208 Hz + LPF, with a mandatory pre-takeoff
  bias subtraction. Enable during recording only.

**Tier D — needs Phase 4 hardware**: GPS speed/distance/runs (the other half
of the Woo/Surfr feature set); barometer stays rejected for jump height.

**Prior-art check — WOO's "The WOO Way" whitepaper (v1.3, 2024)**, read
and digested 2026-07: their empirical data independently confirms our
board mount (takeoff/landing detection fails from chest/wrist; wrists
are cheatable; chest reportedly read ~60% different on inverted tricks —
a 2026-07-29 research pass could not re-verify that figure; see below)
and our rigid-packing rule ("any wiggle room leads to inaccurate
results"). Their sensor arms race (±32 g, 32 kHz, timing crystal, 6-axis
factory cal, Kalman fusion) is the price of DOUBLE-INTEGRATING height for
kites, whose jumps are not ballistic (their data: 15.5 m at 5.9 s airtime
— free-fall math would say 42 m). Wing jumps are short and near-ballistic
(simulation now backs this: realistic wing overshoot **1.00–1.07×** vs a
kite's **2.31×**, the arm-force ceiling capping mid-air lift —
[wing-ballistic-sim.md](wing-ballistic-sim.md), pending real-water
confirmation), and the airtime method never integrates — which is why a $2
mis-scaled sensor passes our bench. Adopted notes: (1) the ESP32 build's ±8 g range
clips landing PEAKS (detection at 2.5 g unaffected) — the Sense already
ships ±16 g from day one, research-backed ([research.md](research.md)
§2/§6); crash-severity analytics remain future work on both platforms,
independent of range; (2) their users complain "reads too low"
95% of the time and WOO resolves doubt upward (wave-trough baselines) —
when video-calibrating height_scale, keep the honest number; that
pressure will arrive. Strategically, the whitepaper read as a kite-only
retreat in 2024, conceding that simpler tools suffice for personal
measurement. The 2026-07-29 deep research pass revisited both of those
points — WOO's site again markets to wingfoilers, and the chest-mount
figure above is downgraded to unverified — which sharpens rather than
breaks the thesis: full corrected figures, the market thesis, and every
citation are in [research.md](research.md), not restated here.

**New output surface — Garmin watch data field**: fully scoped with user
stories, interaction design, architecture, milestones, and acceptance
criteria in **[garmin-datafield.md](garmin-datafield.md)** — written to be
executed by a cheaper build agent. STATUS 2026-07: the complete scaffold
is authored in `garmin/` (source, tests, manifest, README,
FIRST_COMPILE.md with 14 verify-at-compile entries) by a Sonnet agent and
integrated after review; firmware 0.4.2 shipped the two-central BLE
prerequisite. **M0 met 2026-08-04**: first compile on the owner's Mac
succeeded — SDK 9.2.0, `BUILD SUCCESSFUL`, all 24 sim unit tests PASS
(`garmin/FIRST_COMPILE.md`). **On-watch install + live BLE link achieved
2026-08-11** — sideloaded to the owner's **Epix Gen 2** (not the Instinct,
which is his brother's; `epix2` added to the manifest ahead of M5), and the
full chain scan → pair → discover → subscribe → decode → render is proven
on silicon, with a real toss registering (`session_jumps` 0 → 1,
`stored_jumps` 7 → 8). **Not yet signed off as M1:** values on the glass
are corrupt with a second BLE central subscribed (count and best wrong,
airtime absent) — open bug, evidence and ruled-out causes in
`garmin/FIRST_COMPILE.md`; the single-central control run is the next
thing to do. Layout was also rebuilt for a 416x416 round AMOLED (the
original absolute offsets were sized for Instinct's 176px MIP and drew the
header off the glass entirely). The custom "Wing Foil activity" app remains a deliberate later
decision (§8 there). Precedent: Surfr ships a Garmin companion.

## Phase 4 — "Real" hardware

- [ ] Custom PCB: ESP32 module + IMU + LiPo charger + fuel gauge — OR the
      off-the-shelf shortcut: integrated ESP32+IMU boards exist (M5StickC
      Plus2 / AtomS3 / M5Capsule with MPU6886/BMI270-class 6-axis;
      Waveshare S3 + QMI8658; and the non-ESP32 wildcard XIAO nRF52840
      Sense with µA sleep for the solar/potted dream). Trade: tiny
      built-in batteries vs our 2500 mAh. The detector doesn't care —
      new board = one small driver file, same wizard.
      **North star (owner, 2026-07): smallest + most efficient.**
      **ALL-IN (owner, 2026-07-28): the XIAO nRF52840 Sense IS the v2
      board** — hardware + 500 mAh cell ordered. Full port spec, gap
      analysis, and bring-up plan: **[sense.md](sense.md)** (BLE layer
      rewrite NimBLE→Bluefruit, binary trace v2 now launch-blocking,
      Nordic DFU replaces the ESP32 OTA plan, battery telemetry +
      System OFF sleep, metrics-layering architecture feeding the
      Garmin field). The FireBeetle stays the water-day rig until the
      Sense passes the same bench → bucket → water gauntlet. Past
      compile-only now: **S0 bench bring-up done 2026-07-31** — first
      power-on fixed two QSPI silicon bugs (deep-power-down mount +
      word-alignment), answered the `firmware/SENSE_FIRST_BOOT.md`
      first-boot checklist on real silicon, and detected its first jumps.
      Next on the Sense: battery solder-up, drop calibration, and the
      two-central test on-board.
      Middle option if the WiFi decision ever revives: XIAO ESP32-C6
      (same thumbnail size, WiFi 6 + BLE, better efficiency than classic
      ESP32) — but NO onboard IMU (external sensor + wiring stays), and
      it needs the newer Arduino-core/NimBLE-2.x toolchain generation.
- [ ] Better IMU (ICM-20948 / LSM6DSO); optional GPS for speed & distance
- [ ] Deep-sleep power management for multi-session battery life
- [ ] Potted, properly sealed, board-mountable enclosure
- [ ] Publish hardware files under CERN-OHL-S

**Done when:** it's a self-contained puck you charge, stick on, and forget.

### Backlog study — epoxy-potted puck + solar top-up *(thought through 2026-07)*

Verdict: **viable, and the energy math is comfortably on our side — but only
after deep sleep exists.** An awake ESP32 (~50 mA ≈ 4.4 Wh/day) out-eats any
puck-sized panel (~1 Wh/day); asleep between sessions the whole device needs
~1.3 Wh/**week** (3 × 2 h sessions + sub-mA idle), which a capsule-lid-sized
~0.5 W panel covers 4–7× over even flat-mounted, salty, and half-clouded.
Run-time ladder with the real cell (2500 mAh / 9.4 Wh, model 785060): today's
always-on firmware ≈ 2 days per charge; + deep sleep ≈ ~7 weeks of riding per
charge (shelf ≈ a year); + solar ≈ indefinite — the cell alone bridges nearly
two sunless months.

Prerequisites potting forces (in order — each is useful on its own):

1. **Deep sleep + wake-on-motion**: MPU-6050's low-power motion interrupt
   (tens of µA) wakes the ESP32 — needs the currently-unconnected **INT pin
   wired** to a GPIO (a 2-minute job later; the capsule stays openable).
2. **Calibration out of the binary** *(✅ done 2026-07: `set` command +
   NVS persistence + phone-only calibration flow in the web app)*.
3. **OTA updates back in the partition map** (a potted board never sees USB
   again). Costs FS space on 4 MB (~0.8 MB trace) — or an 8/16 MB module on
   the Phase-4 PCB makes it free.
4. **A real solar charge path**: bare panel → board USB charger brownout-loops;
   use a small MPPT LiPo charger (CN3791-class) or harvesting IC (BQ25504)
   with an NTC temp cutoff potted against the cell (this cell's spec allows
   charging only at 0–40 °C — a black block in the sun exceeds that).

Potting traps (all solvable, all mandatory):
- **The cell is the hazard**: pouch LiPo swells — never rigid-encase it. Soft
  silicone cavity inside the epoxy shell, or switch to **LiFePO4** (safer
  chemistry, temp-tolerant, 3.2 V still fine).
- **Epoxy sinks** (~1.15 g/cm³): "must float" needs a syntactic-foam
  (glass-microballoon) layer or a foam jacket. Mass also affects board feel.
- Pour thin layers (cure is exothermic — a thick pour can cook the cell),
  keep epoxy thin or windowed over the PCB antenna, use clear resin over the
  charge LED, and **flash final firmware + calibrate before the pour**.
- Boring-but-reliable alternative to solar: a potted **Qi receiver coil**
  (charges at night, no deck real estate, but needs a human to dock it).

Sequence: deep-sleep firmware → NVS params + OTA → solar trickle experiment
on the *openable* capsule → pot as the v3 appliance once water-validated.

---

## Suggested first three sessions of work

1. **Today:** run `sim/run.py`, read `docs/algorithm.md`, tweak a threshold, see it
   change. Internalize the airtime method.
2. **Order parts** (ESP32 + MPU-6050, ~US$15) and build the Phase 1 breadboard;
   get raw CSV streaming.
3. **Capture & replay:** record yourself doing hand "jumps" with the board, replay
   the CSV offline, and confirm the detector fires correctly. Everything after that
   is tuning and waterproofing.
