# Roadmap

A phased plan that de-risks the hard parts early. Each phase has a concrete
**"done when"** so you know when to move on. You can get real value (and answer
"how high does my brother jump?") by the end of Phase 2.

## Phase 0 — Prove the algorithm, no hardware ✅ *(available now)*

Validate the whole concept in software before spending a cent.

- [x] Airtime → height physics (`docs/algorithm.md`)
- [x] Detection state machine (`sim/detector.py`, mirrored in firmware)
- [x] Synthetic IMU generator with known jumps (`sim/generate.py`)
- [x] Test harness comparing detected vs true height (`sim/run.py`)

**Done when:** `python3 sim/run.py` detects the synthetic jumps with small height
error. *(It does — this is the starting point.)*

## Phase 1 — Bench firmware ✅ *(written; validate on hardware via BUILD.md)*

Get the same algorithm running on real hardware on your desk. The firmware and
tooling exist — the runbook for executing this phase is **[../BUILD.md](../BUILD.md)**.

- [x] FireBeetle 2 ESP32-E + MPU-6050 firmware (`firmware/`), ±8 g, 200 Hz, clone-tolerant
      raw driver, power-on self-test with fix hints
- [x] One-command flash + wiring check: `./tools/jump flash` / `selftest`
- [x] Guided assembly verification: `./tools/jump desktest` (3 tosses)
- [x] Trace logging to flash + offline replay: `./tools/jump sync` / `replay`
- [ ] **On hardware:** desk test passes on the real assembly (BUILD.md Day 1)
- [ ] **On hardware:** drop calibration run and baked in (`./tools/jump drop`, Day 2)

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

**Done when:** detected heights match video-derived heights within your accuracy
goal (aim for ~10%). Now you actually know how high he jumps.

## Phase 3 — App & live stats ✅ *(built; validate on hardware)*

- [x] BLE in the firmware: the exact serial protocol mirrored over a Nordic
      UART Service (NimBLE; compiles clean, 42% of the new 1.5 MB partition)
- [x] Browser app (`web/`, served by `./tools/jump web`): live height/airtime/
      best/count over **Web Bluetooth**, self-test + session download + CSV
      export over **Web Serial**, Playwright-tested against a mock device
- [x] Session history/export (browser localStorage + per-session CSV)
- [x] **Zero-install browser flasher** (ESP Web Tools): Install button on the
      web app; binaries staged by `./tools/jump web` locally and built/published
      to GitHub Pages by CI (`.github/workflows/build.yml`)
- [ ] **On hardware:** BLE advertises + a phone sees live jumps (first real
      board is also the first real BLE radio test)
- [ ] Enable GitHub Pages (Settings → Pages → GitHub Actions) and flash a board
      from the hosted page

**Done when:** you can see jumps pop up live on a phone and review a session
afterward. *(Software side is done and tested; the two unchecked boxes need the
physical board.)*

## Phase 3.5 — WiFi sync mode *(scoped; build after the first water session)*

WiFi is the answer to BLE's two weaknesses — bulk-transfer speed (seconds vs
minutes for a full trace) and iPhones (no Web Bluetooth on iOS, but every phone
can join a WiFi network). Zero new hardware; it's all firmware + serving.

- [ ] **Hotspot ("beach sync") mode:** device broadcasts a `JumpHeight` WPA2
      network on demand and serves the web app itself from LittleFS at
      `http://jump.local` — live stats + sync on ANY phone, no internet, no
      app store. (The device must serve its own app here: an https-hosted page
      isn't allowed to talk to a local http device.)
- [ ] Entered with one tap from the app (BLE/USB command) or automatically
      after N minutes still on land; strictly time-boxed auto-off — WiFi draws
      ~10× BLE's power, so it's a sync window, not an all-day mode.
- [ ] Radio **modes**, not coexistence: BLE by default, WiFi while syncing.
      (Classic-ESP32 BLE+WiFi concurrency is possible but flaky and RAM-hungry;
      sequential modes sidestep it.)
- [ ] WebSocket bridge carrying the same line protocol (the app's transport
      abstraction gets a third implementation next to BLE/Serial/Mock).
- [ ] Phase 4 follow-on: **station mode** — device joins home WiFi (provisioned
      via the app over USB/BLE), announces itself via mDNS, and sessions
      auto-archive to the laptop: the board syncs itself from the garage.

**Done when:** an iPhone with no special browser joins the board's network and
syncs a session in seconds.

## Phase 4 — "Real" hardware

- [ ] Custom PCB: ESP32 module + IMU + LiPo charger + fuel gauge
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
Run-time ladder: today ≈ 10 h per 500 mAh charge; + deep sleep ≈ 1½–2 weeks
of riding per charge (≈ 2-month shelf); + solar ≈ indefinite, battery just
bridges dark weeks.

Prerequisites potting forces (in order — each is useful on its own):

1. **Deep sleep + wake-on-motion**: MPU-6050's low-power motion interrupt
   (tens of µA) wakes the ESP32 — needs the currently-unconnected **INT pin
   wired** to a GPIO (a 2-minute job later; the capsule stays openable).
2. **Calibration out of the binary**: today `drop` bakes `airtime_offset_s`
   in by *re-flashing* — impossible once potted. Params move to NVS-stored
   settings writable over BLE.
3. **OTA updates back in the partition map** (a potted board never sees USB
   again). Costs FS space on 4 MB (~0.8 MB trace) — or an 8/16 MB module on
   the Phase-4 PCB makes it free.
4. **A real solar charge path**: bare panel → board USB charger brownout-loops;
   use a small MPPT LiPo charger (CN3791-class) or harvesting IC (BQ25504)
   with an NTC temp cutoff potted against the cell (no charging > ~45 °C).

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
