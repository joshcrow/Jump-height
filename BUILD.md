# Build guide — the hardware-day runbook

Your job is **hardware**: wires, glue, sealing, water. Everything else —
flashing, testing, calibrating, downloading data — is one command each, via
`./tools/jump`. This guide is the script for the day your parts arrive
(and for the day *before* they arrive: you can rehearse everything now).

Design decisions behind all of this: [`DECISIONS.md`](DECISIONS.md).

---

## Shopping list

**Have:** FireBeetle 2 ESP32-E (the USB-C FireBeetle, DFR0654), small
single-cell LiPo, Ximimark GY-521 MPU-6050
boards ×4 (headers unsoldered — see the soldering section), phone with
120–240 fps slow-mo, a Mac (any laptop works; the tooling is Mac-first).

**Still to get:**

| Item | Notes |
|------|-------|
| Waterproof screw-top **capsule** | Must **float**, fit the FireBeetle **and** battery, and clamp into a GoPro cradle (or zip-tie to a tray). Dive/camping "dry capsule" types work. If trying wireless charging (below), prefer a **thin flat bottom** with floor room for a ~5 cm coil — and buy the receiver first so you can size it. |
| **GoPro adhesive mount** + cradle/tray | Plus zip ties as backup. |
| Short **leash / tether** | A failed mount must not cost you the puck. |
| **Jumper wires** (female-female) + a little solder | The MPU-6050 usually ships with its 4 header pins unsoldered. |
| **Rubbing alcohol** | Surface prep for the adhesive mount. |
| Multimeter *(optional, ~$10)* | To confirm battery polarity. |
| Qi **wireless receiver, USB-C plug** *(optional, ~$10)* | Thin coil + captive USB-C tail ("wireless charging receiver USB C"; Nillkin or similar). Plugs into the board, coil taped to the capsule floor → charge the sealed capsule on a phone pad. Cheap receivers can be plug-orientation picky: no red LED, flip the plug. |
| Flat Qi **charging pad** *(optional, ~$12)* | Any reputable flat pad (not a stand), 5–10 W. A sealed puck parked on the pad also never self-drains — the wireless version of "leave it on USB". Works only if the capsule wall is thin (~few mm): that's the experiment. |

---

## ⚠️ Safety (read once)

- **Battery polarity:** a LiPo plug can be wired **backwards** vs. the
  FireBeetle's connector even though it fits. Reversed = dead board instantly.
  Match `+`/`–` against the markings next to the board's battery connector;
  multimeter if unsure.
- **LiPo care:** charge only via the FireBeetle's USB, never unattended, don't
  crush the cell in the capsule.
- **The red LED is the charge gauge:** quick-flashing = USB power, no battery
  connected (normal); solid = battery charging; off = fully charged (or no
  USB). Plug in USB-C with the battery connected and it charges automatically.
- **No power switch:** with a battery plugged in, the board (and its
  Bluetooth) is always on — v1 has no deep sleep yet. Between sessions keep
  it on USB (which also tops up the charge) or unplug the battery connector;
  a cell left on the shelf drains in roughly a day.

---

## The wizard: the whole bench phase is ONE command

```bash
./tools/jump wizard
```

Plug the FireBeetle into your Mac and run that. It walks you through
everything, in order, with a ✅ or a concrete fix at every step:

1. **Software check** — installs the toolchain if needed, runs the full
   software test suite.
2. **Find your board** — watches for the serial port to appear when you plug
   in (and tells you about charge-only cables, the #1 gotcha, if it doesn't).
3. **Flash + wiring self-test** — builds, uploads, then the device tests its
   own wiring and prints per-check results with fix hints.
4. **Desk test** — a shake and 3 gentle tosses onto a cushion prove the whole
   detection pipeline.
5. **Calibration** — guided measured drops; physics gives exact ground truth
   (1.00 m ⇒ 0.452 s of free-fall), the timing correction is computed, saved,
   and baked back into the device automatically.

It's **resumable**: quit anytime, run it again, it continues where you left
off (`--restart` starts over). Every run — wizard or any other command — also
writes a full session log (everything on screen *plus* raw serial traffic)
under `data/logs/`.

**Today, before the hardware arrives:** rehearse the whole thing against a
simulated device, end to end:

```bash
./tools/jump wizard --fake
```

**If anything ever gets stuck:**

```bash
./tools/jump report
```

writes one file (`data/diagnostics/report-*.txt`) containing your system info,
tool versions, config, wizard progress, visible ports, a live self-test of the
device if connected, and the recent logs — paste it to Claude and it has
everything needed to troubleshoot remotely.

## The one manual skill: solder + wire (Day 1, before the wizard's flash step)

Your sensors are Ximimark **GY-521** MPU-6050 boards: they arrive with the
8-pin header strip loose, so solder the header to the board first (8 joints,
any orientation of board vs. capsule is fine). Then four female-female jumper
wires to the FireBeetle — the other 4 header pins stay empty:

| GY-521 pin | FireBeetle | |
|------------|------------|---|
| VCC | 3V3 | 3.3 V is right — **not** the pin marked VCC (that one carries ~4.7 V) |
| GND | GND | |
| SDA | pin marked **SDA** (IO21) | |
| SCL | pin marked **SCL** (IO22) | |
| XDA, XCL, AD0, INT | *not connected* | normal — they're unused here |

No battery yet — run from USB. Sensor mounting orientation never matters. At
this price these are likely clone chips: the firmware is built for that (an
odd chip ID is a warning, not a failure — what matters is the gravity/noise
check, which the self-test does directly). If one board is a genuine dud,
the self-test will say so; swap in a spare — you bought 4 for exactly this.

## Calibration notes (the wizard handles the mechanics)

Hold the puck with its **bottom** exactly at your measured height above a
cushion and let go — don't throw. Don't drop from below ~50 cm (short falls
are ignored by design, `min_airtime_s`). The slow-mo video check stays in the
plan for the *water* session; this bench step just means you arrive at the
water already close.

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

## Phase 3: live stats in a browser + zero-install flashing

The device now speaks Bluetooth (same protocol as USB, wireless), and there's
a browser app for it:

```bash
./tools/jump web      # serves the app at http://localhost:8765 — open in Chrome/Edge
```

What the app does:

- **Live** (Bluetooth): connect to `JumpHeight` and watch jumps pop up in real
  time — big glare-readable numbers (feet first; one tap swaps to meters),
  session best, count, and a growing bar strip. The screen stays awake while
  connected. Built sunlight-first: high-contrast light theme by default, with
  an Auto/Light/Dark toggle in the header. *Phones:* Android Chrome works out
  of the box; **iPhone Safari has no Web Bluetooth — install the free
  "Bluefy" browser and use that.** (And water blocks Bluetooth — live stats
  are for on land, by physics.)
- **Sync**: when a connected device is holding jumps, a banner offers one
  button — **Sync**. It shows real progress, saves the session into the
  browser, opens it immediately (stats + per-jump bar chart), and only after
  a verified save offers to clear the device for the next session. USB syncs
  fastest; Bluetooth works but is slow for big sessions.
- **Sessions**: history with all-time best, per-jump charts, **Share** (a
  clean share-card image of the session via your phone's share sheet), CSV
  export per session, and **Back up all / Restore** (a JSON file) so browser
  storage is never the only copy. The laptop's `./tools/jump sync` remains
  the archival path into `data/sessions/`.
- **Install**: flash a brand-new board from the web page (ESP Web Tools) —
  no toolchain, no terminal. `./tools/jump web` stages binaries from your
  local build; CI builds them for the hosted page.

**Hosted version (for sharing the project):** the GitHub Action builds the
firmware and publishes the app + flasher binaries to GitHub Pages. One-time
setup: repo **Settings → Pages → Source: "GitHub Actions"**. After that,
anyone can open your Pages URL and flash a board from the browser.

**⚠️ Upgrading a device that has sessions on it:** Phase 3 changes the flash
partition layout, which reformats stored data on first boot after the new
firmware. Run `./tools/jump sync` (and confirm the report looks right)
**before** flashing the upgrade.

---

## Command reference

| Command | What it does |
|---------|--------------|
| **`./tools/jump wizard`** | **the guided end-to-end flow above (resumable; `--fake` to rehearse)** |
| **`./tools/jump report`** | **diagnostic bundle to send to Claude when stuck** |
| `./tools/jump setup` | one-time toolchain install |
| `./tools/jump simtest` | full software test suite (no hardware) |
| `./tools/jump flash` | settings → build → upload → self-test |
| `./tools/jump selftest` | wiring/sensor/storage check, fix hints |
| `./tools/jump desktest` | guided assembly verification (3 tosses) |
| `./tools/jump drop` | guided timing calibration from measured drops |
| `./tools/jump sync` | download session → analyze → report.md |
| `./tools/jump web` | serve the browser app (live BLE stats, sessions, flasher) |
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
| `trace log full` during long session | `sync` then clear; ~45 min of *moving* time fits per session by design (grew with the Phase 3 partition map) |
| Board won't charge / dead | battery polarity — see Safety |
