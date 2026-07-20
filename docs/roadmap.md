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

## Phase 1 — Bench firmware

Get the same algorithm running on real hardware on your desk.

- [ ] ESP32 + MPU-6050 on a breadboard, I²C talking (`firmware/`)
- [ ] Set accel range to ±8 g, sample at ~200 Hz with precise timestamps
- [ ] Run `jump_detector.h`, print detected jumps over USB serial
- [ ] Add a **raw CSV streaming mode** (`t,ax,ay,az`) for capturing data
- [ ] Sanity test: gentle controlled drops / tosses onto a soft surface; compare
      reported height to a tape measure or slow-mo video

**Done when:** the board reliably reports a plausible height for a controlled drop,
and can stream raw CSV you can replay through `sim/run.py --csv`.

## Phase 2 — On the water 🌊

The real test — and where you get your answer.

- [ ] Battery power + wake-on-motion (see `docs/hardware.md`)
- [ ] Waterproof enclosure; **bucket-test it empty first**
- [ ] Log raw CSV to flash/SD for a full session
- [ ] Mount on the board; capture a session with your brother wing foiling
- [ ] **Ground truth:** film some jumps at 120–240 fps; count airborne frames for
      true airtime
- [ ] Tune `Params` in `sim/detector.py` against the captured data + video, then
      copy the tuned values into `firmware/include/jump_detector.h`

**Done when:** detected heights match video-derived heights within your accuracy
goal (aim for ~10%). Now you actually know how high he jumps.

## Phase 3 — App & live stats

- [ ] BLE notify of jump events (firmware already has a hook for this)
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
