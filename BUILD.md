# Build guide — the hardware-day runbook

> ## ⚠️ SUPERSEDED IN PART — check [docs/STATUS.md](docs/STATUS.md) first
>
> This file contains claims that were true when written and are now known to be
> WRONG. It is kept for its reasoning trail, not for its status. `STATUS.md` is
> the single source of truth; where they disagree, this file is stale.
> **This runbook describes the FROZEN ESP32/FireBeetle v1 build**, not the board the
> README tells you to build (Seeed XIAO nRF52840 Sense). No Sense runbook exists yet.



Your job is **hardware**: wires, glue, sealing, water. Everything else —
flashing, testing, calibrating, downloading data — is one command each, via
`./tools/jump`. This guide is the script for the day your parts arrive
(and for the day *before* they arrive: you can rehearse everything now).

Design decisions behind all of this: [`DECISIONS.md`](DECISIONS.md).

---

## Shopping list

**Have:** FireBeetle 2 ESP32-E (the USB-C FireBeetle, DFR0654), a **2500 mAh**
single-cell LiPo (model 785060 — 61 × 51 × 8 mm, 46 g, protection circuit
built in), Ximimark GY-521 MPU-6050
boards ×4 (headers unsoldered — see the soldering section), phone with
120–240 fps slow-mo, a Mac (any laptop works; the tooling is Mac-first).

**Still to get:**

| Item | Notes |
|------|-------|
| ~~Small waterproof **hard case**, Pelican-1010-class~~ **BOUGHT 2026-08-11: Hammond 1551WHGY** (Digi-Key `164-1551WHGY-ND`) | **60 × 35 × 22 mm, IP68**, polycarbonate, replaceable silicone gasket, stainless lid screws seated *outside* the gasket. Better than the Pelican-clone plan this row used to describe: a rated seal instead of a hopeful O-ring. Must still **float** loaded — the bucket test tells. Thin walls should pass Qi. |
| ⚠️ **The sizing this row used to give was ESP32-era and ~2× too big** | It said "interior ≥65 × 55 mm, battery 61 × 51 × 8 mm, board 60 × 25 mm". Those are the **FireBeetle** rig's numbers. For the **Sense** the board is **21 × 17.8 mm** (docs/sense.md §2) and the installed cell is **250 mAh** — both far smaller. Anyone shopping off the old row buys a case twice the size they need. Board fit is not in question in a 60 × 35 mm shell; **the cell is the only item worth measuring against the interior.** |
| **GoPro surfboard mount kit** (~$12) | Purpose-built "stay on a board in waves" kit: flat adhesive bases + tether anchor + leash. Use **two bases**, fore + aft of the case. Alcohol-prep and stick **24 h before** water day — cure time is real. A failed mount must not cost you the puck: leash always. |
| Heavy **zip ties** + Velcro cinch straps | Case cinches flat through the bases' GoPro fingers and its own lanyard holes. Zip ties = the lock; Velcro = quick beach removal. |
| **Silica gel packet** (free in any shipment box) | Goes inside the case — hot beach → cold water means condensation fog on the electronics otherwise. |
| **Jumper wires** (female-female) + a little solder | The MPU-6050 usually ships with its 4 header pins unsoldered. |
| **Rubbing alcohol** | Surface prep for the adhesive mount. |
| Multimeter *(~$10)* | **Required.** To confirm battery polarity: the Sense's JST pigtail is soldered by hand and JST polarity is not standardized ([`docs/solder.md`](docs/solder.md) §1). (It was optional on the retired FireBeetle, which is why older notes hedge.) |
| Soldering iron, solder, flux | Headers on the GY-521s; the pigtail on the Sense. Procedure: [`docs/solder.md`](docs/solder.md). |
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
  the 2500 mAh cell left on the shelf drains in about two days. A full charge
  from the board's USB-C takes roughly an overnight (~6 h).
- **Pin tips vs. the pouch:** soldered header pins are sharp; the battery is
  a soft pouch whose one big rule is *don't pierce*. Always keep foam or
  bubble wrap between the board's underside and the cell.

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

**Who does what — phone vs Mac:** once firmware is on the board, the phone
(Bluefy → the web app) is daily life: self-test, toss test, drop calibration
(saved into the device's own memory, surviving reboots *and* reflashes),
live jumps, sync, export, clear — all under *Connect → Bench: test &
calibrate* and *Sessions*. The Mac is the factory and the hospital: it
**flashes firmware** (first install + upgrades — no iOS browser can do
this), archives sessions as real files on disk, and produces the
`./tools/jump report` diagnostic bundle when something's genuinely weird.
If the firmware isn't changing, the puck can live for weeks without ever
meeting the computer.

**If anything ever gets stuck:**

```bash
./tools/jump report
```

writes one file (`data/diagnostics/report-*.txt`) containing your system info,
tool versions, config, wizard progress, visible ports, a live self-test of the
device if connected, and the recent logs — paste it to Claude and it has
everything needed to troubleshoot remotely.

## The one manual skill: solder + wire (Day 1, before the wizard's flash step)

> Iron technique, flux, temperatures, the multimeter checks, and the rework
> table live in **[`docs/solder.md`](docs/solder.md)** — read it before the
> first joint, and *especially* before soldering the JST pigtail onto the
> Sense's bare BAT pads, where a bridge shorts a lithium cell.

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

One-page visual of the hookup **and** the foam packing:
[`docs/img/bench-assembly.svg`](docs/img/bench-assembly.svg)

No battery yet — run from USB. Sensor mounting orientation never matters. At
this price these are likely clone chips: the firmware is built for that (an
odd chip ID is a warning, not a failure — what matters is the gravity/noise
check, which the self-test does directly). If one board is a genuine dud,
the self-test will say so; swap in a spare — you bought 4 for exactly this.

## The bench "housing": raid the kitchen (the slick case comes later)

For desk tests and calibration drops the assembly just needs to move as one
rigid lump: any small **hard-sided food container** works. Pack board,
battery, and sensor snug with foam or bubble wrap — nothing may shift or
rattle (the sensor reads every internal wobble as signal), with padding
between pin tips and the battery pouch. Crack the lid for the USB cable
during flashing and self-tests — but tosses and calibration drops are done
**unplugged**: the wizard tells you when to pull the cable, the board keeps
running on battery and records on its own, and when you plug back in the
wizard reads the results out of storage. No cable ever flies.
Orientation inside doesn't matter. The waterproof capsule from the shopping
list remains the container for anything near water — the kitchen box's
jurisdiction ends at the cushion.

## Water day one: the MVP rig

Two adhesive bases on the center deck, **clear of where knees and feet land**,
alcohol-wiped and stuck **24 hours before** water day. The case packs exactly
like the bench box (snug foam, pin-tip padding, silica packet), zip-tied flat
through both bases and its lanyard holes, leash to a footstrap insert or the
kit's tether anchor. Before any ocean, in order: bucket-test the sealed case
**empty** with a paper towel inside (the towel tells on leaks) → bucket-test
**loaded** → confirm the phone still sees "JumpHeight" through the closed
case. It floats and it's tethered, always. The slick custom housing comes
later, informed by what this rig teaches.

## Calibration notes (the wizard handles the mechanics)

**Once per build, and it sticks:** calibration measures the device's fixed
reaction time against gravity, and the result is stored in the device's own
memory — surviving reboots, reflashes, and battery swaps. Redo it only if
you swap the sensor board or change detection settings in
`config/params.json`. (The separate `height_scale` knob gets set once from
on-water slow-mo video — that's Phase 2's job, not the bench's.)

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

## Validate against video — the honest number

The manual video check above works by hand; `./tools/jump validate`
automates it and writes the honest error-bar report no commercial tracker
publishes. Two commands, done at different times:

```bash
# 1. On the water: film a handful of jumps in slow-mo (120-240 fps).
# 2. Back on land, after typing up what the video shows into pairs.csv:
./tools/jump validate --pairs pairs.csv
```

`pairs.csv` has one row per filmed jump — jump number, video fps, frames
airborne:

```
jump_n,fps,frames
3,120,74
7,240,168
```

`jump_n` matches the jump's number in the device's stored list (or a synced
session — add `--session data/sessions/<date>/jumps.csv` to validate one of
those instead). Count `frames` the same way every time: from the first frame
the board has fully left the water to the first frame it touches back
down — consistency beats philosophy. No `pairs.csv` yet? Run the command
without `--pairs` from a real terminal and it prompts you jump by jump
instead (fps, then either a frame count or takeoff/landing frame numbers).

The tool computes the true airtime and height from your frame counts,
compares them against what the device measured, and prints a plain-English
verdict: leave calibration alone, correct `airtime_offset_s` (a timing
bias), or — only if a height error survives that timing fix —
correct `height_scale` too. Nothing is written to the device unless you
pass `--apply` or answer yes at the prompt. Every run writes
`data/validation/validate-<date>.md`, a self-contained report you can hand
to anyone skeptical of the number, plus a `.csv` of the same data.

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
| `./tools/jump eval` | score the detector over labeled sessions ([docs/data-pipeline.md](docs/data-pipeline.md)) |
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

## Shock durability — what it can take, and what breaks first

Researched 2026-08-14. The headline: **the silicon is not the weak link.**

| Part | Rating | Source |
|---|---|---|
| LSM6DS3TR-C IMU | **10,000 g** for a 0.2 ms pulse (absolute max, i.e. a damage threshold) | ST datasheet DocID030071 Rev 3, Table 9 §4.5 p.29 |
| LiPo pouch cell | Qualified to **150 g / 6 ms half-sine**, 18 shocks, 3 axes | UN Manual of Tests & Criteria Rev.6 §38.3.4.4 (Test T.4) |
| nRF52840 | **No published shock rating at all** — Nordic gives only moisture-sensitivity (MSL 1/2) | nRF52840 PS v1.1, confirmed absence |
| P25Q16H flash | No shock rating published either | Puya datasheet V2.1 |

**What that means in practice.** A drop from 1–2 m onto water, or a hand
toss onto a cushion, is nowhere near 10,000 g. Even a bad drop onto concrete
is far more likely to *saturate the ±16 g reading* than to hurt the chip.
The ST datasheet does note the part is "sensitive to mechanical shock" and
that its number is a stress rating, not a promise — but the margin is large.

**The realistic failure order** (informed engineering judgement, not measured):

1. **The LiPo pigtail / JST joint**, from repeated flex fatigue — not one
   impact, but hundreds. **Glue-dot the connector and anchor the wires.**
2. **The adhesive mount or the enclosure seal.** Mounts bear leverage and
   torque that a small, low-mass PCB inside does not.
3. **Large MLCCs**, but mostly as a *potting* risk, not an impact one — see
   below.
4. The MCU/IMU silicon itself. Least likely by a wide margin.

**If you ever pot it:** use a soft urethane or silicone, **not rigid epoxy**.
Cure shrinkage plus CTE mismatch cracks MLCCs and can shift the IMU's zero
offset through package stress. Standard practice is to dam or mask the IMU
footprint rather than encapsulate directly over it. Compliant foam under the
enclosure is the single best shock mitigation — it lengthens the deceleration
pulse, which is what actually lowers peak g.

**What nobody has measured:** there are **no published accelerometer numbers
for kite/wing/surf landings**. PubMed and Scholar return nothing under any
term tried; this is genuinely off the map. Our own recorded traces so far peak
at **1.52 g** (desk handling only). The water session will be the first real
measurement — worth noting the ±16 g setting is a deliberate choice
(DECISIONS #25) and a hard landing may clip it, which is a data question, not
a damage one.
