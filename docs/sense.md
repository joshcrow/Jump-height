# XIAO nRF52840 Sense — the v2 puck *(port spec & gap analysis)*

**Status:** ALL-IN (owner, 2026-07-28). Board + 500 mAh battery ordered,
arriving within days. Written before first power-on: this is the honest
list of what the plan had NOT covered, plus the bring-up sequence — items
marked **VERIFY** get answered on the bench and this doc gets edited.
Build-ahead, same day: the pre-arrival software is already DONE — firmware
0.4.3 split the core behind platform seams (§3.9), and the complete nRF52
layer now compiles clean (Bluefruit link with library-source citations,
LSM6DS3 driver, raw QSPI store with binary trace v2 + codec parity tests,
watchdog); CI publishes a drag-and-drop `.uf2`. Every runtime assumption
that only hardware can prove is numbered in
**[../firmware/SENSE_FIRST_BOOT.md](../firmware/SENSE_FIRST_BOOT.md)**
(21 items) — day one is flash + work down that list, i.e. milestone S0
and most of S1 already have prebuilt answers waiting.

**Product statement.** The Sense puck is a **tiny Garmin data-field
adder**: it rides the board, measures, and feeds the watch live — phone
(Bluefy) as the second screen — with new data points (time on foil,
carve G, spins…) layered on over time as new keys on the same protocol.
Smallest + most efficient wins every tie.

**What does NOT change:** water day still runs on the FireBeetle rig —
it is validated, calibrated, and cased. The Sense must pass the same
gauntlet (desk test → drop calibration → bucket → water) before it takes
over. Nothing here blocks Phase 2.

## 1. The board, verified (2026-07-28)

| Fact | Value | Note |
|---|---|---|
| MCU | nRF52840 — 64 MHz Cortex-M4F, 256 KB RAM, 1 MB internal flash | app shares the 1 MB with the BLE stack (SoftDevice S140) + bootloader |
| Data storage | 2 MB external QSPI flash (P25Q16H) | sessions live here, not in internal flash |
| IMU | LSM6DS3TR-C 6-axis accel+gyro, onboard | ±2/4/8/16 g — ships at ±16 g (0.488 mg/LSB; research.md §2/§6 — the ESP32 build stays ±8 g); power-gated by pin P1.08 |
| Battery | **NO connector — bare BAT +/− solder pads (underside)** | solder the JST pigtail (already ordered) to them |
| Charger | BQ25101: **50 mA default, 100 mA** with P0.13 driven low | 500 mAh ⇒ ~11 h vs ~5–6 h full charge |
| Battery readout | divider on P0.31 (AIN7), enabled by driving P0.14 low | disabled by default — no silent drain |
| LEDs | user RGB (P0.26 red / P0.30 green / P0.06 blue) + red charge LED (P0.17) | first build with an always-visible status light |
| Radio | BLE 5 only — **no WiFi** | NUS (our protocol's transport) is native here |
| Bootloader | Adafruit nRF52 UF2: double-tap reset → `XIAO-SENSE` USB drive appears, drag one file | PlatformIO can also flash over USB (adafruit-nrfutil) |
| Standby | < 5 µA (System OFF, per Seeed) | vs the ESP32's ~50 mA awake |
| Size | 21 × 17.8 mm | 500 mAh cell ≈ 36 × 29 × 5 mm sits beside/under it |

## 2. What carries over unchanged

- **`jump_detector.h`** — dependency-free C++; compiles as-is. The
  Python twin and the parity suite keep gating it. The crown jewel is
  untouched.
- **The line protocol** — JUMP/STATS/INFO/CAL/… text over the Nordic
  UART Service. The Sense's stack implements NUS natively (Bluefruit
  `BLEUart`), so Bluefy, the web app, the CLI, and the Garmin field
  connect without knowing the chip changed.
- **The web app + bench flows** — toss test, 3-step drop calibration,
  sync, autopsy: all protocol-level.
- **The Garmin scaffold** (`garmin/`) — scans for the NUS UUID;
  chip-agnostic by construction. The watch never knows.
- **The wizard/CLI concepts** — untethered testing, stored-rows
  baselining, stale-port recovery. (Port names change, §3.11.)
- **`config/params.json` → generated header** discipline; sim; tests;
  gravity-baseline normalization (built for exactly this moment — new
  sensor, new scale, zero drama).

## 3. The gap list — what we had NOT planned for

Ordered by how much they change the plan.

### 3.1 The BLE layer is a rewrite (the one real one)

`firmware/src/platform/esp32/jh_link.cpp` (formerly `ble_link.h`) is
written against NimBLE (ESP32-only). The Seeed/Adafruit
nRF52 core uses **Bluefruit**, whose `BLEUart` service IS the Nordic
UART Service — likely *less* code than we have now, but different
semantics: connection callbacks, per-connection MTU, TX buffering, and
how advertising resumes. The hard-won 0.4.2 lessons (two centrals =
watch + phone; min-MTU chunking; sampling never blocks on the radio)
must be **re-derived** against Bluefruit, not assumed ported. VERIFY:
`Bluefruit.begin(2, 0)` two-link behavior; `BLEUart` per-connection TX
FIFO and notify semantics; advertising auto-restart after the first
central connects; the 128-bit NUS UUID present in the advertisement
(the Garmin field's scan filter needs it) with the name in the scan
response.

### 3.2 Binary trace v2 is now launch-blocking, not an optimization

2 MB at today's CSV-on-flash format (~15 bytes/sample) is **~45
minutes** of moving time — a normal 1–2 h session doesn't fit. The
binary format already scoped in [ota.md §4.5](ota.md) (~2 bytes/sample)
makes the same 2 MB ≈ **5 hours**. So the Sense port ships binary
storage from day one; the wire format stays CSV (the device converts
while dumping — every client, tool, and test unchanged), and the
C++/Python parity suite gates the format change like everything else.
Net: the "smaller" flash actually holds ~6× more session than today's
board does.

### 3.3 The OTA plan mostly does not apply — Nordic DFU replaces it

[ota.md](ota.md) §§4.1–4.4 and 4.6 are ESP32-specific (esp_ota
partitions, WiFi doorway) — shelved for this board. The Sense path: the
Adafruit bootloader already speaks Nordic's **OTA DFU**; adding
Bluefruit's `BLEDfu` service lets any phone running Nordic's free **nRF
Connect** app install firmware wirelessly. Honest trade vs the ESP32
plan: this DFU is single-bank — a transfer that dies mid-way leaves the
device waiting in its bootloader (recoverable over BLE or USB, never
bricked) instead of still running the old firmware like the dual-slot
ESP32 scheme would. Mitigations: releases get bench-soaked first, and
UF2 drag-drop is a trivial cable recovery. CI grows a `.uf2` artifact
(hex → uf2conv) published on Pages next to the ESP32 binaries. VERIFY:
nRF Connect on iOS against the Adafruit bootloader end-to-end, twice in
a row. *(Researched 2026-07-28: DFU from our own web app is effectively
OFF the table with the stock bootloader — browsers blocklist the Nordic
legacy-DFU service UUID in Web Bluetooth, and the existing JS libraries
implement Secure DFU, a different protocol than the Adafruit
bootloader's legacy one. The nRF Connect app IS the wireless path; UF2
is the cable path. Revisit only if we ever swap bootloaders.)*

### 3.4 Battery plumbing we never built (firmware reports nothing today)

- **No connector on the board**: the JST pigtail gets soldered to the
  underside BAT pads. **JST battery polarity is NOT standardized —
  meter the battery leads before plugging** (red→+, black→−); a
  reversed cell kills the charger IC.
- **Charge current is a choice**: default 50 mA ≈ 11 h for the 500 mAh
  cell. Firmware drives P0.13 low → 100 mA ≈ 5–6 h. Plan on overnight
  charging; the red P0.17 LED shows charge-in-progress.
- **Battery telemetry**: today's firmware reports NOTHING about its
  battery. Add: VBAT sampling (enable P0.14 → read P0.31 → disable),
  `vbat=`/`batt_pct=` keys on INFO/STATS, a low-battery LED pattern,
  and a **low-voltage System OFF at ~3.45 V** so the cell can't be
  over-discharged. The app and the Garmin field display it — the puck
  is sealed, the number has to travel. Confirm the 500 mAh cell carries
  a protection PCB (Adafruit's do). VERIFY at S2.

### 3.5 Sleep/wake is a design, not a checkbox

The whole "most efficient" prize: idle timeout → **System OFF
(< 5 µA)**, wake on the IMU's hardware motion interrupt (its activity
detector runs at µA with the MCU fully off) or on charger attach.
Design deliberately: wake thresholds that ignore a car ride (wake, see
no sustained motion and no BLE central, re-sleep within seconds), the
first-jump-of-the-session wake latency, and "is it even on?" (a wake
blink). This replaces the ESP32-era "no power switch, it just idles at
50 mA" pain with "no power switch, it sleeps for months."

### 3.6 The QSPI flash leaks unless told to sleep

The 2 MB chip idles at tens of µA unless explicitly put into **deep
power-down** (~1 µA) between writes — the classic silent standby killer
on this exact board. The storage layer owns deep-power-down entry/exit
around every write burst. VERIFY the Adafruit SPIFlash API for it and
meter the delta at S2.

### 3.7 New IMU = new small driver, a power rail, and (later) a smarter architecture

- `lsm6ds3_min.h`, sibling of `mpu6050_min.h`: internal I2C bus
  (VERIFY pins — community says ~P0.07/P0.27 — and which `Wire`
  instance the Seeed variant maps there), address 0x6A, WHO_AM_I check,
  ±16 g (0.488 mg/LSB; research.md §2/§6 — the ESP32 build stays ±8 g),
  output rate ≥ our 200 Hz, plus the P1.08 power rail (cut in
  deep standby).
- The self-test carries, but its thresholds get re-validated on the new
  part, and **drop calibration must be redone on the Sense build** —
  our own standing rule: recalibrate when the sensor changes.
- Later (the real µA play, not v2.0): the IMU's hardware FIFO +
  interrupt batches samples while the CPU naps between drains, and
  hardware activity-detect replaces the software motion gate. The port
  starts as a plain poll loop — correctness before elegance.
- **Gyro policy**: off by default (~0.6 mA when running). Carve G is
  accel-only (a hard carve shows as sustained elevated |a|). Spins need
  the gyro → duty-cycle it on only while moving, when that metric
  ships. VERIFY the LSM6DS3TR-C FIFO size (4 KB-class per datasheet
  family) when the FIFO architecture lands.

### 3.8 Calibration persistence: NVS is ESP32-only

ESP32 `Preferences`/NVS → a small file on the internal-flash LittleFS
(`InternalFS` in this core). The `set` command, CAL line, sanity rails,
and the phone calibration flow are unchanged; only the storage shim
underneath differs. Same survival story: lives through app updates and
DFU, dies only with a full chip erase.

### 3.9 Toolchain/CI: second build target, one repo

PlatformIO env `xiaoblesense_adafruit` (nordicnrf52 platform, Adafruit
core = Bluefruit) added beside the ESP32 env. During the port (S1, not
as a speculative pre-refactor) the chip-agnostic core — command
dispatch, emit layer, detector glue, storage schema — gets pulled
behind four thin seams: **imu / store / link / persist**. ESP32 keeps
its current implementations; the Sense gets new ones; `main` logic
becomes shared. CI builds both targets and publishes `.bin` (ESP32) +
`.uf2` (Sense). VERIFY: the known PlatformIO USB/serial quirks on this
board; adafruit-nrfutil upload vs manual double-tap in the dev loop.

### 3.10 A status light, for the first time

A sealed puck with no serial cable has exactly one face: the RGB LED.
Define a tiny language — wake blink, advertising vs connected,
recording, low battery (charging is the hardware red LED). Duty-cycle
everything dim and rare (LEDs cost mA; 20 ms flashes every few seconds,
never solid).

### 3.11 Small but real

- **Watchdog**: a sealed puck that hangs is dead until the battery
  drains. The nRF watchdog goes in on day one of the port.
- **Antenna keep-out**: the antenna hangs off one end of the board — no
  metal near it, no foil tape, battery NOT stacked over it. Range-test
  puck-on-board → wrist at S4.
- **PDM microphone**: onboard, unused — confirm it stays unpowered
  (VERIFY its rail during the idle-current measurements).
- **Serial port names**: native USB shows up as `usbmodem…` on macOS,
  not `wchusbserial…` — widen the CLI's port filter. The
  port-vanishes-on-reset behavior is already handled by the stale-port
  logic built during bring-up week.
- **Clock semantics** unchanged: uptime resets each boot; the
  per-power-on trace segmentation logic carries as-is.

## 4. The "adder" architecture — layering new data points

The point of v2: the puck feeds the watch, and new measurements arrive
as **new keys on existing protocol lines**. Both parsers (web app,
Garmin `Protocol.mc`) already ignore unknown keys — old clients never
break, new clients grow tiles.

- **Rule 1**: every on-device metric is an incremental accumulator over
  the same 1 s windows the trace uses — integer-cheap, no big buffers.
- **Rule 2**: every on-device metric must be reproducible offline from
  the trace by `sim/` code. The C++/Python parity discipline extends to
  metrics; the trace stays ground truth and tuning fuel.
- **The ladder**: **v2.0** jump feed (parity with today) → **v2.1**
  time on foil (`foil_s=` on STATS; accel-only smoothness classifier,
  tuned on the first real water traces) → **v2.2** carve G (`carve_g=`
  window peak; accel-only) → **v2.3** spins (gyro, duty-cycled).
- The Garmin field renders what it recognizes; FIT developer fields
  grow alongside ([garmin-datafield.md §5.5](garmin-datafield.md)).

## 5. Power & runtime, honest *(estimates until S0/S2 measurements)*

| State | Estimate | 500 mAh means |
|---|---|---|
| Recording — naive poll loop, accel on, BLE connected, periodic writes | ~3–8 mA | **~60–160 h**: weekends per charge, not hours |
| + gyro on (duty-cycled, only once a gyro metric ships) | +~0.6 mA | still tens of hours |
| Awake idle (advertising, no motion) | ~1–3 mA | days |
| System OFF + IMU motion-watch + QSPI deep power-down | ~5–15 µA | **months** |

FireBeetle reference: ~50 mA flat → ~10 h of recording on a cell FIVE
times bigger. Even the lazy first port should outlast the v1 rig by an
order of magnitude. Measure at S0 (USB meter) and S2 (on battery, real
duty cycle) and replace this table with numbers.

## 6. Bring-up milestones

- **S0 — hello, board** *(day one, no soldering)*: UF2 blink; meter
  idle/active current; IMU WHO_AM_I + stream |a| over USB serial;
  Bluefruit BLEUart advertising as `JumpHeight`; Bluefy and the web app
  connect and see a greet line. Every §7 item answerable on day one,
  answered.
- **S1 — the port**: platform seams (imu/store/link/persist); detector +
  protocol + self-test running; **binary trace v2 storage with CSV on
  the wire**; desk test + autopsy through the wizard, untethered; parity
  replay on a Sense-recorded trace; drop calibration on the Sense build.
- **S2 — power + battery**: solder the pigtail (meter polarity first!);
  100 mA charge select; VBAT telemetry + low-voltage System OFF;
  sleep/wake on the IMU interrupt; QSPI deep power-down; LED language;
  measure everything and update §5.
- **S3 — update path**: BLEDfu + nRF Connect OTA proven from the
  iPhone; UF2 recovery drill written into BUILD.md; CI publishes `.uf2`.
- **S4 — the puck**: housing (Hammond 1551W-class), mount, antenna
  range check, bucket test — then the same water gauntlet v1 passed.
- **S5 — the metrics ladder** (§4) once real water traces exist to tune
  against.

## 7. VERIFY at bring-up (answer on the bench, then edit this doc)

1. Bluefruit two-central links: `begin(2, 0)`, per-connection MTU +
   notify, TX FIFO depth under our line rates.
2. NUS 128-bit UUID in the advertisement (Garmin scan filter), name in
   scan response.
3. Internal I2C pins / `Wire` instance for the IMU in the Seeed
   variant; LSM6DS3TR-C FIFO size *(4 KB confirmed from ST docs
   2026-07-28; register-map cross-check on the bench remains)*.
4. nRF Connect (iOS) DFU against the Adafruit bootloader, end-to-end,
   twice consecutively.
5. QSPI deep-power-down API in Adafruit SPIFlash + metered µA delta.
6. System OFF current with the IMU motion-watch armed; a wake threshold
   that ignores a car ride.
7. Charge LED behavior with no battery attached (bench use); P0.13
   actually selecting 100 mA.
8. The 500 mAh cell: protection PCB present; exact dimensions for the
   housing.
9. PlatformIO `xiaoblesense_adafruit` builds our tree; adafruit-nrfutil
   upload works; CLI port filter catches `usbmodem`.
10. PDM mic rail: confirm unpowered by default, or power it down
    explicitly.

## 8. Explicitly out of scope (for v2)

- Zephyr / nRF Connect SDK — Arduino + Bluefruit matches the rest of
  the fleet; revisit only if a hard wall appears.
- Custom PCB — the Sense IS the "real hardware" candidate; Phase 4's
  custom board happens only if the Sense proves the architecture and we
  still want smaller.
- Signed DFU, fleet management.
- WiFi anything — this chip has none; that is the point.

## 9. Jump-starts — existing tools, code, and papers *(researched
2026-07-28; expanded 2026-07-29 by the four-agent deep pass in
[research.md](research.md) — market map, literature verdicts on every
design choice, nautical-science transfers, and the OSS adoption list
with licenses verified)*

Three items below are the same recommendation research.md §7's adopt
list already makes with full license detail, so they're pointers here,
not restated: **ST's official driver examples** (research.md §7 — the
detail unique to this doc is that we keep our own minimal driver but
copy their wake_up/activity/free-fall/FIFO register sequences rather
than deriving them), **xio Fusion** (research.md §7, adopted whole once
spins/orientation land), and **Edge Impulse's XIAO Sense support**
(research.md §7's S5 classifier fallback — unique detail: data in via
their serial forwarder, deployed as an Arduino library, free tier, worth
it if time-on-foil ever outgrows the windowed-variance approach). The
sports-science flight-time literature below (MyJump vs. force-plate
validation, IMU timing-bias papers justifying `airtime_offset_s` as
additive) is likewise cited with hard numbers in research.md §2.

What's unique to this doc, standing on shoulders so nothing below gets
hand-rolled that doesn't need to be:

**Current measurement (S0/S2)**: Nordic's free [Online Power
Profiler](https://devzone.nordicsemi.com/power/) calculator models BLE
current for given connection parameters before you measure, as a sanity
check on §5's table. **Nordic Power Profiler Kit II (PPK2, ~$100)** —
the standard µA-level source-meter/logger for exactly this chip family;
a multimeter cannot see dynamic duty-cycle draw honestly. Recommended
purchase before S2. Nordic DevZone + Seeed forum threads on XIAO Sense
sleep current (QSPI deep-power-down, mic rail) mean §3.6's gotcha is
already community-documented — read them before chasing µA.

**Sleep/wake + IMU hardware features (S2)**:
[AN5130 — the LSM6DS3TR-C application note](https://www.st.com/resource/en/application_note/an5130-lsm6ds3trc-alwayson-3d-accelerometer-and-3d-gyroscope-stmicroelectronics.pdf)
— threshold math, per-mode current tables, FIFO (4 KB confirmed).
*(Careful: AN4987 is the LSM6DSM's — sibling part, wrong doc.)* The
Adafruit nRF52 core's FreeRTOS tickless idle already makes plain
`delay()` low-power, and `sd_power_system_off` examples ship in the core.

**Battery telemetry (S2)**: Adafruit's canonical nRF52 VBAT ADC snippet
(internal 0.6 V reference + gain + divider handling) is adapted, not
derived. Bluefruit ships a standard BLE Battery Service, `BLEBas`, in
one line, rendered natively by every phone (and nRF Connect) — adopted
ALONGSIDE the protocol's own `vbat=` key, genuine hand-roll avoided.
Published LiPo discharge curves supply the voltage→percent table, with
the honest under-load-sag caveat.

**OTA proof (S3)** — the client side already exists end-to-end:
adafruit-nrfutil builds packages, Nordic's free nRF Connect / nRF
Toolbox apps are the iOS DFU clients, `BLEDfu` ships in the core. Zero
client code to write. Browser DFU: dead with the stock bootloader (see
§3.3 note).

**Drop calibration + height validation (S1 / water)**:
[Woodman, "An introduction to inertial navigation" (free Cambridge tech
report)](https://www.cl.cam.ac.uk/techreports/UCAM-CL-TR-696.pdf) — the
standard primer; the drift math there is WHY this project never
integrates. Phyphox (free physics app) serves as an independent second
sensor for bench drop cross-checks.

**BLE pacing (S1 tuning)** — Apple's *Accessory Design Guidelines for
Apple Devices* (Bluetooth LE chapter) is the authoritative source for
iPhone connection-interval behavior; turns `CHUNK_GAP_US` tuning from
guesswork into arithmetic. Nordic's throughput app notes cover the
peripheral side.

**Storage fallback** — if the raw QSPI region manager misbehaves on the
bench, littlefs (power-loss-proven by design) is the drop-in fallback;
the swap hides entirely behind the `jh_store` seam.

**What rightly stays hand-rolled**: the detection thresholds and
wing-foil traces — no public vibration-signature prior art exists,
confirmed by the research pass ([research.md §6](research.md)) — the
line protocol, and the minimal clone-tolerant drivers (DECISIONS #13).

---

Sources verified 2026-07-28: [Seeed XIAO BLE wiki](https://wiki.seeedstudio.com/XIAO_BLE/)
(IMU part, charge currents + P0.13, VBAT pins, P25Q16H, LED pins,
standby figure, IMU power pin), [Seeed XIAO nRF52840 + PlatformIO
wiki](https://wiki.seeedstudio.com/xiao_nrf52840_with_platform_io/),
[UF2 bootloader flashing walk-through](https://mithundotdas.medium.com/xiao-firmware-update-uf2-e93a94fd499f),
[PlatformIO nordicnrf52 board-support PR](https://github.com/platformio/platform-nordicnrf52/pull/151).
