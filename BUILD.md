# Build guide (v1)

The specific, step-by-step build for the design in [`DECISIONS.md`](DECISIONS.md).
Written to be followed without an engineering background. Take it one section at a
time — don't skip the desk test.

---

## Shopping list

**Already have:** FireBeetle ESP32, small single-cell LiPo battery, MPU-6050 ×4
(ordered), a phone that shoots 120–240 fps slow-mo, a laptop with a USB port.

**Still to get:**

| Item | Notes |
|------|-------|
| Waterproof screw-top **capsule** | Must **float**, fit the FireBeetle **and** battery, and clamp into a GoPro cradle (or be zip-tie-able to one). Dive/camping "dry capsule" types work. |
| **GoPro adhesive mount** + cradle/tray | Flat or curved sticky mount + a way to hold the capsule. Zip ties as backup. |
| Short **leash / tether** | So a failed mount doesn't cost you the puck. |
| **Jumper wires** (female-female) + a little solder | The MPU-6050 usually needs its header pins soldered on (4 pins). |
| **Rubbing alcohol** (isopropyl) | Surface prep for the adhesive mount. |
| Multimeter *(optional, ~$10)* | To confirm battery polarity before you plug it in. |

---

## ⚠️ Safety first (read once)

- **Battery polarity:** the plug on your LiPo may be wired **backwards** vs. the
  FireBeetle's connector even though it fits. Reversed = dead board, instantly.
  Match `+`/`–` to the markings by the board's connector; multimeter if unsure.
- **LiPo care:** charge only via the FireBeetle's USB, never leave it charging
  unattended, don't crush or puncture the cell inside the capsule.

---

## Step 0 — Wire it up

Four wires, MPU-6050 → FireBeetle (it's the I²C bus):

| MPU-6050 pin | FireBeetle pin |
|--------------|----------------|
| VCC          | 3V3            |
| GND          | GND            |
| SDA          | pin labelled **SDA** (default in firmware: GPIO21) |
| SCL          | pin labelled **SCL** (default in firmware: GPIO22) |

If your FireBeetle's SDA/SCL land on different GPIO numbers, change `I2C_SDA` /
`I2C_SCL` at the top of [`firmware/src/main.cpp`](firmware/src/main.cpp) to match.
Leave the battery unplugged for now — run off USB while building.

---

## Step 1 — Flash the firmware and desk-test it

Install [PlatformIO](https://platformio.org/install) (the VS Code extension is the
friendly route), then from the `firmware/` folder:

```bash
pio run -t upload         # compile + flash the FireBeetle
pio device monitor        # watch the serial output (115200 baud)
```

You should see `# Jump Height ready...`. Now test the detection with your hand:

- Hold the board (or just the puck) still → nothing logged (it's "idle").
- Give it a shake → `# ...motion — recording`.
- **Toss it a few inches up off a cushion, or drop it a short measured distance.**
  A real free-fall + catch should print a `JUMP #… height=…` line.

**Pass = it prints plausible jumps.** A 20 cm drop is ~0.13 s of free-fall → tiny
height; a bigger toss reads bigger. You're just confirming the pipeline works.

> Tip: to desk-test without the flash logging, set `ENABLE_LOGGING 0` at the top of
> `main.cpp`. Turn it back to `1` before the water.

Serial commands (type into the monitor): `stats`, `jumps`, `trace`, `dump`, `clear`.

---

## Step 2 — Dry-land accuracy check (this sets your correction factor)

Goal: find out how the puck's number compares to reality, on land, where a mistake
costs nothing.

1. Firmware logging on (`ENABLE_LOGGING 1`), `clear` any old data.
2. Have someone **film in slow-mo (120–240 fps)** while you do a few clear jumps
   holding the board (or drop it from a measured height onto something soft).
3. Read the puck's heights: `stats` and `jumps`.
4. **Ground truth from the video:** count the frames the board is airborne,
   `airtime = frames ÷ fps`, then true `height = 9.81 × airtime² ÷ 8`.
5. Compare. If the puck reads consistently, say, 8% low, that's your **correction
   factor** — note it. (We can bake it into the firmware or apply it after.)

Also pull the raw trace and confirm the offline detector agrees:

```bash
# in the serial monitor, run:  trace
# copy the CSV output (the lines starting at "t,mag") into data/landtest.csv, then:
python3 sim/run.py --csv data/landtest.csv
```

---

## Step 3 — Waterproof it, then bucket-test (non-negotiable)

1. Put the FireBeetle + battery in the capsule, close it.
2. **Before it ever goes near the sea:** drop the *closed, empty-of-electronics*
   capsule (with a dry tissue inside) in a bucket of water for 10 minutes. Tissue
   dry = good. Do this **every** session — it's 10 minutes of insurance against a
   drowned build.
3. Confirm the sealed capsule **floats**. Add a scrap of foam if it doesn't.

---

## Step 4 — Mount it

1. Pick a **hard, smooth** spot near the **center** of the deck (not the soft foam
   pad — tape won't hold there).
2. Wipe with rubbing alcohol, let it dry.
3. Stick the GoPro mount down firmly and **leave it 24 hours before it gets wet.**
4. Clip the capsule in, and **tether** it to the board.

---

## Step 5 — Send it 🌊

1. Charge the puck, `clear` old data, seal it **near launch time** (remember: it
   records the whole time it's awake — battery covers a few hours).
2. Have someone film a few of his jumps in slow-mo for the final accuracy check.
3. After the session: open capsule, plug into laptop, run `stats` for the headline
   and `dump` to export everything.
4. Re-run the trace through `python3 sim/run.py --csv …` and, if needed, nudge the
   detector thresholds in `sim/detector.py`, confirm on the captured data, then copy
   the tuned numbers into `firmware/include/jump_detector.h` and re-flash.

That loop — capture, replay, tune, re-flash — is how you get from "roughly right" to
"trust it." After that, you actually know how high your brother jumps.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| `MPU6050 not found` | Check the 4 wires; try I²C address 0x69 (some boards); confirm 3V3 not 5V. |
| No `JUMP` lines | Motion detected but no free-fall + landing — you need an actual airborne moment. Lower `freefall_enter_g` / `landing_threshold_g` in the header if real jumps are missed. |
| Lots of false jumps | Raise `landing_threshold_g` or `min_airtime_s`. |
| Heights consistently off | Apply your Step 2 correction factor. |
| `trace log full` | ~30 min of moving-time logged; `dump` then `clear`. Consider a microSD later. |
| Board won't charge / died | **Battery polarity** — see Safety. |
