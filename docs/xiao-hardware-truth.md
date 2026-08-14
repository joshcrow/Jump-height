# XIAO nRF52840 Sense — hardware truth

What the board actually is, at schematic level, so this project never
again spends four days and three boards on a misunderstanding.

Written 2026-08-14, immediately after the drive-strength bug
(SENSE_FIRST_BOOT §16i) was found and fixed. Every claim here is
schematic-, datasheet-, or reference-implementation-grade, with the
source named. **If you are about to conclude "the hardware is dead,"
read this file first.**

## 1. The single most important fact

**P1.08 is not an enable pin. The GPIO pad IS the sensor's power
supply.**

Net `6D_PWR` (Seeed schematic v1.1, sheet 2) connects exactly:

| Node | What it is |
|---|---|
| U1 ball P2 | nRF52840 **P1.08** |
| U3 pad 8 | LSM6DS3TR-C **VDD** |
| U3 pad 5 | LSM6DS3TR-C **VDDIO** |
| U3 pad 12 | LSM6DS3TR-C **CS** (tied high = select I2C mode) |
| C1 | 100 nF decoupling to GND |
| R14 | **10 kΩ pull-up to INTERNAL_I2C_SCL** |
| R15 | **10 kΩ pull-up to INTERNAL_I2C_SDA** |

There is **no regulator, no load switch, no MOSFET** anywhere in that
path. The MCU pin sources the sensor and both bus pull-ups directly.

Consequences that follow immediately, and that cost this project days:

1. **Drive strength is not optional.** Load is ~0.9 mA (sensor) + ~0.66 mA
   (two 10 kΩ pull-ups) plus turn-on inrush into 100 nF. The nRF52840's
   **standard drive is specified at 0.5 mA**; high drive is 5 mA (9 mA
   max). `pinMode(pin, OUTPUT)` silently selects **standard** drive.
   Under-driven, the pad sags, the rail never reaches VIH, and
   everything downstream looks dead.
2. **Reading P1.08's IN register is a 1-bit voltmeter on the sensor
   rail**, threshold 0.7 × VDD ≈ 2.31 V — not a GPIO health check.
   `OUT=1, DIR=1, IN=0` means *the rail is sagging under load*, which is
   a power result, not a broken pin.
3. **All three "independent" symptoms are one symptom.** Rail down ⇒
   pull-ups unpowered ⇒ SDA and SCL both low ⇒ TWIM starts a transfer
   that can never complete (neither ACK nor NACK — a timeout). Treating
   them as three corroborating signs of dead silicon was the core
   reasoning error.
4. **Driving a bus line high while the rail is down back-powers the
   sensor** through R14/R15. Always `bus_release()` (float, pulls off)
   before any rail change. This is the schematic-level version of the
   §16g hazard.

## 2. Everyone else uses high drive. We were the only ones who didn't.

| Implementation | Configuration |
|---|---|
| **Seeed's own Arduino library** | Calls `pinMode(...)` and then **immediately overwrites** `NRF_P1->PIN_CNF[8]` with `DIR_OUTPUT \| INPUT_DISCONNECT \| NOPULL \| NRF_GPIO_PIN_H0H1 \| NOSENSE`, sets it high, `delay(10)`. The library author hard-coded a stronger driver over the framework default. |
| **Zephyr** (`xiao_ble_sense`) | Models it as `regulator-fixed`: `enable-gpios = <&gpio1 8 (NRF_GPIO_DRIVE_S0H1 \| GPIO_ACTIVE_HIGH)>`, `regulator-boot-on`, `startup-delay-us = <3000>`. High drive on the high side; 3 ms settle. |
| **CircuitPython** | Hit the exact failure: issue **#8093**, *"I2C hangs when power removed from bus on nrf52840"*, filed on this same board — *"IMU_PWR provides the power to both the LSM6DS3 chip and also the pullups to the I2C bus... this leaves both SDA and SCL low."* Fixed in PR #8094 with a pre-flight bus-sanity gate plus recovery. |
| **This project, until 2026-08-14** | `pinMode(OUTPUT)` = **S0S1 standard drive**. The only implementation using it, and the only one that "killed" boards. |

Our fix uses `H0H1` (matching Seeed's library) via `nrf_gpio_cfg` in
`jh_imu::init()` and `jh_imu::revive()`. `S0H1` (Zephyr's choice) is
equally valid — only the high side needs the strength.

## 3. Rails: what is powered by what

| Peripheral | Supply | Implication |
|---|---|---|
| LSM6DS3TR-C IMU | **P1.08 GPIO** (`6D_PWR`) | Dead unless firmware drives P1.08 high, hard |
| I2C pull-ups R14/R15 | **P1.08** (same net) | Bus cannot idle high unless the sensor rail is up |
| PDM microphone | **P1.10** (`MIC_PWR`) | Separate GPIO-sourced rail; same drive-strength question applies — **untested, assume it needs high drive too** |
| QSPI flash | **always-on 3V3** (LDO U6, EN tied to VIN) | *"The flash answers" says NOTHING about the IMU rail.* Different rail entirely. We mis-read this as evidence more than once. |

## 4. Measurements that are INVALID on this board

Recorded because each one produced a confident wrong answer here.

- ❌ **Reading SDA/SCL against internal pull-downs to infer rail state.**
  With 10 kΩ external pull-ups against the nRF's 11–16 kΩ internal
  pull-down, the pad sits at 1.73–2.03 V — **below the 2.31 V input-high
  threshold even on a perfectly healthy board.** The test returns the
  same answer for healthy and broken hardware, so it is not evidence.
  This is what the `railcheck` "EN net stuck at ground" verdict was
  built on, and that verdict was wrong.
- ❌ **Any bus-side probe as a health check for the sensor.** Every
  instrument that samples only inside the sensor domain inherits the
  fault and confirms whatever story you brought.
- ❌ **"QSPI works, so the board's power is fine."** Different rail.
- ✅ **`pincensus`** — read every GPIO twice (internal pull-down, then
  pull-up). A free pin reads `01`. Unrelated pins behaving correctly
  prove the *instrument*; the suspect pins reading `00` prove the
  *load*. This is the measurement that finally broke the case.
- ✅ **Pin readback while driving** (`OUT`/`DIR`/`IN` separately). On
  P1.08 specifically, remember it is a rail voltmeter, not a pin test.

## 5. Pin map (verified against the installed variant, index-by-index)

`g_ADigitalPinMap` in `variants/Seeed_XIAO_nRF52840_Sense/variant.cpp`:

| Arduino | nRF | Function |
|---|---|---|
| 14 | P0.14 | `READ_BAT` (drive LOW to connect the divider) |
| **15** | **P1.08** | **`6D_PWR` — the IMU supply. HIGH DRIVE ONLY.** |
| 16 | P0.27 | `6D_I2C_SCL` |
| 17 | P0.07 | `6D_I2C_SDA` |
| 18 | P0.11 | `6D_INT1` |
| 19 | P1.10 | `MIC_PWR` (also GPIO-sourced) |
| 22 / 23 | P0.13 / P0.17 | `HICHG` / `~CHG` (BQ25101) |
| 24–29 | P0.21/25/20/24/22/23 | QSPI SCK/CSN/IO0–IO3 |
| 30 / 31 | P0.09 / P0.10 | NFC1 / NFC2 (NFC mode by default) |
| 32 | P0.31 | `VBAT` divider tap (SAADC AIN7) |

Package is **aQFN73 (QIAA)** — P1.08 is ball P2. All of P0.00–P0.31 and
P1.00–P1.15 are bonded, so "the pin doesn't exist" is never the
explanation.

## 6. The rules this file exists to enforce

1. **Never configure a current-carrying pin with `pinMode()`.** Use
   `nrf_gpio_cfg(..., NRF_GPIO_PIN_H0H1, ...)`. Applies to P1.08 today
   and P1.10 (mic) the day it is used.
2. **Read the pin back before believing you drove it.** `OUT=1, IN=0` is
   a complete, printable diagnosis and was available on day one.
3. **Any pin-level claim requires control pins in the same pass**
   (`pincensus`). No single-pin verdicts.
4. **Two failed "dead hardware" verdicts means the instrument is
   suspect, not the hardware.** Stop testing the board; start testing
   the measurement, and pull the vendor schematic before spending
   another board.
5. **Pull the schematic FIRST for any new peripheral on this module.**
   Seeed publishes it (`Seeed-Studio-XIAO-nRF52840-Sense-v1.1.pdf`) and
   the KiCad source. Ten minutes there would have saved all of this — as
   it would have for the §16g back-feed hazard, which was also sitting
   in public documentation the whole time.
6. **Check how Seeed's own library, Zephyr, and CircuitPython drive it**
   before writing our own register code. All three had this right.

## Sources

- Seeed XIAO nRF52840 Sense schematic v1.1 (sheets 2–3) and the official
  KiCad project netlist — R14/R15, `6D_PWR` node list, LDO U6.
- Nordic nRF52840 Product Specification — GPIO drive specs (0.5 mA
  standard / 5 mA high), `VIH = 0.7 × VDD`, pull resistor range
  11/13/16 kΩ, package pin tables 145–147.
- Seeed_Arduino_LSM6DS3 (`beginCore()` raw `PIN_CNF[8]` H0H1 write).
- Zephyr `xiao_ble_nrf52840_sense` devicetree (`regulator-fixed`,
  `S0H1`, `startup-delay-us = 3000`).
- CircuitPython issues #8093 / PR #8094 — same board, same symptom.
