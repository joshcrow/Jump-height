# Build guide — the hardware-day runbook

Your job is **hardware**: wires, glue, sealing, water. Everything else —
flashing, testing, calibrating, downloading data — is one command each, via
`./tools/jump`. This guide is the script for the day your parts arrive
(and for the day *before* they arrive: you can rehearse everything now).

Design decisions behind all of this: [`DECISIONS.md`](DECISIONS.md).

---

## Shopping list

**Have:** FireBeetle ESP32, small single-cell LiPo, MPU-6050 ×4 (ordered),
phone with 120–240 fps slow-mo, laptop.

**Still to get:**

| Item | Notes |
|------|-------|
| Waterproof screw-top **capsule** | Must **float**, fit the FireBeetle **and** battery, and clamp into a GoPro cradle (or zip-tie to a tray). Dive/camping "dry capsule" types work. |
| **GoPro adhesive mount** + cradle/tray | Plus zip ties as backup. |
| Short **leash / tether** | A failed mount must not cost you the puck. |
| **Jumper wires** (female-female) + a little solder | The MPU-6050 usually ships with its 4 header pins unsoldered. |
| **Rubbing alcohol** | Surface prep for the adhesive mount. |
| Multimeter *(optional, ~$10)* | To confirm battery polarity. |

---

## ⚠️ Safety (read once)

- **Battery polarity:** a LiPo plug can be wired **backwards** vs. the
  FireBeetle's connector even though it fits. Reversed = dead board instantly.
  Match `+`/`–` against the markings next to the board's battery connector;
  multimeter if unsure.
- **LiPo care:** charge only via the FireBeetle's USB, never unattended, don't
  crush the cell in the capsule.

---

## Day 0 — today, before the hardware arrives (~10 minutes)

```bash
./tools/jump setup      # one-time toolchain install
./tools/jump simtest    # full software test suite — should end PASS ✅
./tools/jump desktest --fake   # rehearse the desk test against a simulated device
./tools/jump drop --fake       # rehearse the calibration flow too
```

`--fake` runs the real tool against a simulated device, so you'll have seen
every screen before touching hardware. When the real thing behaves differently,
that difference *is* the diagnostic information.

## Day 1 — MPUs arrive: wire, flash, verify (~1 hour, mostly soldering)

**1. Solder + wire (the only manual skill needed today).** Solder the 4-pin
header onto an MPU-6050 breakout, then four jumper wires to the FireBeetle:

| MPU-6050 | FireBeetle |
|----------|------------|
| VCC | 3V3 |
| GND | GND |
| SDA | pin marked **SDA** (IO21) |
| SCL | pin marked **SCL** (IO22) |

No battery yet — run from USB. (Sensor mounting orientation never matters.)

**2. Plug into the laptop and run:**

```bash
./tools/jump flash
```

That regenerates settings, builds, uploads (first build downloads the compiler
— a few minutes, once), then **automatically self-tests the wiring** and prints
✅/❌ per check with a plain-English fix hint for anything wrong. Fix wires →
`./tools/jump selftest` → repeat until green. No re-flashing needed between
wiring fixes.

**3. Prove the whole pipeline:**

```bash
./tools/jump desktest
```

It walks you through a shake and 3 gentle tosses onto a cushion and verifies
detection end-to-end. `PASS` = your assembly works. (If a clone MPU turns out
to be a dud, the self-test says so — swap in a spare; you bought 4 for exactly
this.)

## Day 2 — calibration (~20 minutes)

The trick: a drop from a **measured height is perfect ground truth** — physics
fixes its free-fall time exactly (1.00 m ⇒ 0.452 s), so the tool can measure
the detector's timing bias and correct it, no video needed:

```bash
./tools/jump drop --height-cm 100
```

Hold the puck with its bottom exactly at 100 cm above a cushion, let go (don't
throw), 5 times. The tool computes the correction, saves it to
`config/params.json` with your consent, and then:

```bash
./tools/jump flash    # bake the calibration into the device
```

Don't drop from below ~50 cm — short falls are ignored by design
(`min_airtime_s`). The slow-mo video check stays in the plan for the *water*
session; this bench step just means you arrive at the water already close.

## Day 3+ — waterproof, mount, send it 🌊

1. **Capsule:** electronics out, dry tissue in, closed, 10 min in a bucket.
   Tissue dry = sealed. **Repeat this before every session.** Confirm it floats.
2. **Mount:** hard smooth patch near board center (not the soft foam pad),
   alcohol-wipe, press the GoPro mount on hard, **24 h cure** before water.
   Tether the capsule.
3. **Session:** charge → `./tools/jump sync --clear` (empties it) → seal near
   launch → ride. Have someone slow-mo a few jumps for the video cross-check.
4. **Back on land:**

```bash
./tools/jump sync
```

Downloads everything, re-analyzes the raw trace offline, cross-checks it
against the live detection, and writes `data/sessions/<date>/report.md` —
jump list, best height, and a flag if anything disagrees. The raw trace is
kept forever, so any session can be re-scored later with improved settings:

```bash
./tools/jump replay --csv data/sessions/<date>/trace.csv
```

**Video check:** count the frames your brother is airborne in the slow-mo,
`airtime = frames ÷ fps`, `true height = 9.81 × airtime² ÷ 8`. If the device
is consistently off by a percentage, set `height_scale` in
`config/params.json` and re-flash. After that, you trust the number.

---

## Command reference

| Command | What it does |
|---------|--------------|
| `./tools/jump setup` | one-time toolchain install |
| `./tools/jump simtest` | full software test suite (no hardware) |
| `./tools/jump flash` | settings → build → upload → self-test |
| `./tools/jump selftest` | wiring/sensor/storage check, fix hints |
| `./tools/jump desktest` | guided assembly verification (3 tosses) |
| `./tools/jump drop` | guided timing calibration from measured drops |
| `./tools/jump sync` | download session → analyze → report.md |
| `./tools/jump replay --csv f` | re-run the detector over any saved capture |
| `./tools/jump monitor` | raw serial console (type `help`) |
| `./tools/jump gen` | regenerate firmware settings from config/params.json |

Add `--fake` to selftest/desktest/drop/sync to rehearse without hardware, and
`--port /dev/ttyUSB0` anywhere if auto-detection picks the wrong port.

**Tuning:** every threshold lives in **`config/params.json`** — one file, used
by the firmware, the simulator, and the analysis identically. Edit → `flash`.

## Troubleshooting

Most problems are caught by `selftest`/`desktest`, which print their own fix
hints. Beyond those:

| Symptom | Fix |
|---------|-----|
| `flash` can't find the port | data-capable USB cable? (many are charge-only) Try `--port`. On Linux you may need to join the `dialout` group. |
| Real jumps missed on the water | in `config/params.json`: **raise** `freefall_enter_g` (takeoff dip not registering) or **lower** `landing_threshold_g` (landing spike missed); test against your synced trace with `replay`, then re-flash |
| False jumps from chop | raise `landing_threshold_g` or `min_airtime_s` (same loop) |
| `trace log full` during long session | `sync` then clear; ~30 min of *moving* time fits per session by design |
| Board won't charge / dead | battery polarity — see Safety |
