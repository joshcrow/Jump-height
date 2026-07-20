# Hardware

Goal: a small, waterproof, battery-powered puck that mounts on the board, samples an
IMU, and runs the jump detector. Below is a phased BOM — start cheap on a breadboard,
upgrade as you go.

> **Building the actual v1?** This page is the general menu of options. The specific,
> decided build (FireBeetle ESP32 + MPU-6050, bought capsule, GoPro mount) with a
> step-by-step guide and shopping list is in **[../BUILD.md](../BUILD.md)**.

## Bill of materials

### Phase 1 — bench prototype (~US$15)

| Part | Suggested | Notes |
|------|-----------|-------|
| **MCU** | ESP32 dev board (ESP32-C3, ESP32-S3, or classic ESP32-WROOM) | BLE + WiFi, deep-sleep, Arduino/PlatformIO support. C3/S3 minis are tiny and low-power. |
| **IMU** | MPU-6050 breakout (GY-521) | 6-axis accel+gyro, I²C, ~US$2. Fine to prove the concept. |
| Wires | jumper wires + breadboard | I²C is just 4 wires. |
| Power | USB from your computer | for the bench you don't need a battery yet. |

### Phase 2+ — waterproof, battery-powered, on the water

| Part | Suggested | Notes |
|------|-----------|-------|
| **IMU (upgrade)** | ICM-20948, LSM6DSO(X), or BMI270 | lower noise, better bias stability, magnetometer (ICM) for heading. |
| **Battery** | 1S LiPo, 400–1000 mAh | a few hours; size to your enclosure. **Handle LiPo safely.** |
| **Charger** | TP4056 (USB-C) with protection, or an ESP32 board with charging built in | never charge LiPo unprotected. |
| **Power switch / MOSFET** | slide switch or soft-latch circuit | so you can turn it off between sessions. |
| **Storage (optional)** | microSD module, or use ESP32 internal flash (LittleFS) | log raw CSV for offline tuning. |
| **Enclosure** | small IP68 box or 3D-printed case + o-ring | see waterproofing below. |
| **Mount** | adhesive pad / strap / GoPro-style mount | must survive impacts and not come off. |

### Phase 4 — "real" device (optional)

- Custom PCB integrating ESP32 module + IMU + charger + fuel gauge (MAX17048).
- **GPS** (u-blox NEO-M9N / MAX-M10) for speed and distance.
- Barometer (BMP388) — *not* for jump height (too imprecise at this scale) but useful
  for other telemetry.
- Potted electronics for real waterproofing.

## Wiring (MPU-6050 ↔ ESP32, I²C)

| MPU-6050 pin | ESP32 pin | Notes |
|--------------|-----------|-------|
| VCC | 3V3 | **not** 5V for 3.3 V boards |
| GND | GND | |
| SDA | GPIO 21 (default; set in firmware) | I²C data |
| SCL | GPIO 22 (default; set in firmware) | I²C clock |
| INT | (optional) any GPIO | data-ready interrupt for precise timing later |

> ESP32-C3/S3 don't have fixed I²C pins — pick any two GPIOs and set them in
> `firmware/src/main.cpp` (`Wire.begin(SDA, SCL)`).

## IMU configuration that matters

- **Accelerometer range:** set **±8 g** (or ±16 g). The default ±2 g will **clip**
  landing spikes and hide the very signal we detect on. ±8 g keeps free-fall
  resolution good while capturing landings. (Set in firmware.)
- **Sample rate:** 100–200 Hz. Timing precision → height precision.
- **Low-pass filter:** enable the on-chip DLPF (~40–90 Hz) to tame vibration without
  smearing the landing spike.

## Power budget (rough)

| State | ESP32 current | Note |
|-------|---------------|------|
| Active (sampling + BLE) | ~40–120 mA | dominated by radio |
| Modem-sleep (BLE only, no WiFi) | lower | keep WiFi **off** on the water |
| Deep-sleep between sessions | ~10–150 µA | wake on motion (IMU interrupt) to save battery |

A 500 mAh LiPo at ~80 mA average gives ~5–6 h of sampling. Add wake-on-motion so it
sleeps in the car and the bag.

## Waterproofing — the part that actually kills these projects

Saltwater is relentless. Plan for it from the start:

1. **Seal the enclosure.** IP68 box with an o-ring, or a 3D print with a gasket
   groove. Test it empty (with a tissue inside) in a bucket overnight *before*
   trusting electronics to it.
2. **Conformal-coat the PCB** (acrylic/urethane) as a second line of defense; leave
   connectors/antenna clear.
3. **Consider potting** (epoxy/silicone) the electronics for a permanent build — but
   you lose repairability, and epoxy over the antenna hurts BLE range. Keep the
   antenna area clear.
4. **Charging without opening:** qi wireless charging or sealed pogo pins avoid a USB
   port (a classic leak point). Early on, just open the box to charge.
5. **BLE through water/plastic:** works fine through plastic in air; **water blocks
   2.4 GHz**, so you'll sync stats when the device is out of the water, not while
   submerged. Fine for post-session download; log to flash during the session.
6. **Mounting & impacts:** landings are violent. Strain-relieve wires, secure the
   battery so it can't shift, and use a mount that won't shear off.

## Safety

- LiPo cells can vent/ignite if punctured, over-charged, or shorted — use a
  protected charger, don't crush the cell in the enclosure, and don't leave charging
  unattended.
- Don't throw hardware near people to "test jumps."

## Licensing intent

Hardware design files (schematics, PCB, enclosure models) added later are intended
to be released under **CERN-OHL-S v2**; this documentation under **CC BY-SA 4.0**.
