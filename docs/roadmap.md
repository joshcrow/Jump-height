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

- [x] FireBeetle ESP32 + MPU-6050 firmware (`firmware/`), ±8 g, 200 Hz, clone-tolerant
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

## Phase 3 — App & live stats

- [ ] BLE notify of jump events (to be added to the firmware)
- [ ] Web Bluetooth page or mobile app: live height, airtime, session best, count
- [ ] Session history / export

**Done when:** you can see jumps pop up live on a phone and review a session
afterward.

## Phase 4 — "Real" hardware

- [ ] Custom PCB: ESP32 module + IMU + LiPo charger + fuel gauge
- [ ] Better IMU (ICM-20948 / LSM6DSO); optional GPS for speed & distance
- [ ] Deep-sleep power management for multi-session battery life
- [ ] Potted, properly sealed, board-mountable enclosure
- [ ] Publish hardware files under CERN-OHL-S

**Done when:** it's a self-contained puck you charge, stick on, and forget.

---

## Suggested first three sessions of work

1. **Today:** run `sim/run.py`, read `docs/algorithm.md`, tweak a threshold, see it
   change. Internalize the airtime method.
2. **Order parts** (ESP32 + MPU-6050, ~US$15) and build the Phase 1 breadboard;
   get raw CSV streaming.
3. **Capture & replay:** record yourself doing hand "jumps" with the board, replay
   the CSV offline, and confirm the detector fires correctly. Everything after that
   is tuning and waterproofing.
