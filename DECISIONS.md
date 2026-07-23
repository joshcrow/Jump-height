# v1 design decisions

The decisions behind the v1 build, and *why* — so future-you (and anyone else
building this) knows what was chosen on purpose vs. what's just incidental.

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | **What v1 is** | A puck that **records** a session on one board; read the jumps on land afterward. Personal, one board. Open-source the files as you go. | Fastest path to the real unknown — *does the airtime method work on foil jumps?* Don't let "product for others" gate the first number. |
| 2 | **How you read it** | Review afterward over USB. No live/on-water display. | You can't usefully read anything mid-wing anyway; Woo itself just syncs on land. |
| 3 | **Accuracy target** | Within ~10%, proven **once** against slow-mo video, then bake in a correction factor. | 10% is invisible in real life; the method's own physics (drag, wave landings) limits you there regardless. Accuracy comes from the video check, not an expensive sensor. |
| 4 | **Sensor** | MPU-6050 (bought 4). Only the accelerometer is used. | Cheapest, best-documented, clears the 10% target. Using only \|acceleration\| makes mounting orientation irrelevant. |
| 5 | **The maths** | Airtime method: `height = g × airtime² ÷ 8`. Detect takeoff by free-fall (\|a\|≈0), landing by the spike. | Avoids double-integration drift, which is unusable on cheap IMUs. |
| 6 | **Board + power** | DFRobot **FireBeetle 2 ESP32-E** (DFR0654: USB-C, built-in LiPo charging, 4 MB WROOM-32E module) + a **2500 mAh** single-cell LiPo (785060, PCM-protected). Charge & download over USB when the capsule is open. | Already owned; built-in charging removes a whole class of wiring/safety mistakes; the FireBeetle 2 is one of the lowest-power ESP32 boards for the later sleep upgrade. *(Identified as the 2/E variant during bring-up — USB-C + CH340 + the 4 MB chip all match DFR0654, not the original DFR0478.)* |
| 7 | **On/off** | Motion-activated recording. Simple "record only while moving" now; real low-power deep-sleep later. | No hole needed in a sealed capsule. Simple version keeps logs clean; deep-sleep (for battery life) is a later optimization. |
| 8 | **What it saves** | Continuous **50 Hz trace of \|a\| while moving** + a per-jump list, on built-in flash. | Re-tunable offline against the same sim we already tested, and it captures **missed** jumps too (a plain per-jump clip wouldn't). Memory card is the escape hatch if flash runs short. |
| 9 | **Waterproofing** | Bought screw-top waterproof **capsule**. Bucket-test empty every time. Must float + be tethered. | Buying beats building for speed; one purchase settles waterproofing, charging, data offload, and storage at once. |
| 10 | **Mounting** | Adhesive **GoPro-style mount**, hard smooth patch of center deck. Alcohol-prep, 24 h cure, tethered. | Center = cleanest signal. Adhesive = no drilling. Tether + float because a lost puck is the classic ending. |
| 11 | **Test plan** | **Desk → dry land (filmed) → water (filmed).** | Each step fails cheaply and catches its own class of bug *before* the ocean, where mistakes are expensive. |

## Added for the "hardware is my only job" build-out

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 12 | **One-command tooling** | Everything software-side runs through `./tools/jump` (flash, self-test, desk test, calibration, session sync). | The builder's job is hardware only; each step of the runbook is a single command with PASS/FAIL output and fix hints. |
| 13 | **Raw MPU-6050 driver** | Minimal register-level driver instead of the Adafruit library. | Cheap Amazon clone chips often report an unexpected WHO_AM_I and the popular libraries refuse to start. Ours warns and continues — what matters is the accel actually reading ~1 g, which the self-test checks directly. |
| 14 | **Power-on self-test** | Firmware checks I2C, chip ID, gravity, noise floor, and storage at every boot, with plain-English fix hints; a wiring failure never bricks the session (`selftest` re-probes without re-flashing). | "Did I wire it right?" gets answered in 5 seconds, by the device itself. |
| 15 | **Single source of truth for settings** | `config/params.json` drives the firmware (via a generated header), the simulator, and the analysis. | Eliminates the "tuned Python but forgot the C++" failure mode entirely. |
| 16 | **Bench calibration by measured drops** | `./tools/jump drop`: physics fixes the free-fall time of a measured drop exactly, so timing bias is measured and corrected as an additive `airtime_offset_s` — no video needed on the bench. | Detection latency is constant in *time*, so an additive correction generalizes across jump sizes better than a height multiplier. Video ground truth remains for the on-water check (`height_scale`). |
| 17 | **Fake device for rehearsal + CI** | `tools/fake_device.py` emulates the firmware's serial protocol on a pty; every CLI flow runs against it (`--fake`) and the whole stack is integration-tested by `./tools/jump simtest`. | The entire test/calibration experience can be rehearsed before the hardware exists, and every software change is regression-tested without a board on the desk. |
| 18 | **Wizard + diagnostic bundle** | `./tools/jump wizard` is the front door: one resumable guided flow (software → find board → flash → desk test → calibrate) with a ✅ or a concrete fix at every step. Every command logs its terminal output *and* raw serial traffic to `data/logs/`; `./tools/jump report` bundles system info, config, wizard progress, a live device self-test, and recent logs into one file to hand to Claude. | The builder experience should be "plug it into the MacBook and follow along" — and when something does go wrong, the evidence is already collected, so remote troubleshooting starts from data instead of memory. |

## Added in Phase 3 (live stats + browser app + zero-install flashing)

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 19 | **BLE = the same protocol, wireless** | The firmware mirrors its exact serial line protocol over a Nordic UART Service (NimBLE stack, not the stock Bluedroid one), device name `JumpHeight`. A phone subscribes and sees the very same `JUMP ...` lines the CLI does. | One protocol everywhere — CLI, fake device, web, BLE — means nothing can drift, and every existing tool/test stays valid. NimBLE because Bluedroid's flash/RAM footprint wouldn't leave room for a big log partition. |
| 20 | **Browser app, no build step** | `web/` is one static vanilla-JS page: connect over Web Bluetooth (live jumps) or Web Serial (self-test, session download → history + CSV), plus an ESP Web Tools install button for in-browser flashing. Served locally by `./tools/jump web` (localhost is a secure context) or by GitHub Pages. | Zero install for the user, zero build tooling for contributors, testable end-to-end in CI (Playwright drives the real page against a mock device). iPhone caveat: iOS Safari has no Web Bluetooth — use the free Bluefy browser. |
| 21 | **Partition map + CI binaries** | Custom no-OTA partition table (app 1.5 MB, logs 2.4 MB — trace cap raised to ~45 min). Flasher binaries are **not** committed: `./tools/jump web` stages them from a local build, and the GitHub Action builds them fresh and publishes `web/` + binaries to Pages. | No OTA slots needed when flashing is over USB/web; committing binaries would bloat and stale the repo. **Upgrade note:** the new partition table reformats stored data on first boot — `sync` before upgrading a device that has sessions on it. |

## Deliberately deferred to later phases

- **Real deep-sleep** power management (multi-session battery life). v1 charges after each outing. *(Also the gate for the potted-puck + solar idea — energy math and prerequisites studied in the roadmap's Phase 4 backlog note.)*
- **Live in-session readout on the water.** BLE + the web app now show jumps live, but 2.4 GHz doesn't travel through water and you can't read a phone mid-wing — live stats are for the beach between runs; the session record remains the product.
- **microSD card** for unlimited logging. v1 uses built-in flash (~45 min of moving-time trace, capped; grew with the Phase 3 partition map).
- **Better IMU / GPS / custom PCB.** All Phase 4.
- **Air-drag / wave-landing correction** beyond a single global factor.

## Known v1 limitations (accepted on purpose)

- Simple motion-gating keeps logs clean but doesn't save power yet → **seal near launch, charge after**.
- Trace logging caps at ~45 min of *moving* time to protect flash; the jump list is always kept.
- A drop off a ledge reads as a "jump" (it's really fall height) — fine for this use.
