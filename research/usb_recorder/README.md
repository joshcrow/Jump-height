# USB six-axis bench recorder

Separate research firmware and a host recorder for the **Seeed XIAO nRF52840
Sense**. It records accelerometer and gyro counts from the LSM6DS3TR-C FIFO at
nominally 208 Hz, with a hardware timestamp for each sample. It provides input
for the [offline estimator](../README.md); it does not estimate jump height.

**Installed-versus-prepared state:** 8673 still runs `9c9289b69af5b76a`, limited
to 60-second captures. The current source builds `53aac69c2b7e37a8`, which adds
1,200-second captures and clock/temperature telemetry; it is **not installed**.
Software bootloader entry failed. See [the clock study](TIMEBASE_STUDY.md) and
[board lookup/restoration](../../docs/bench-playbook.md#research-recorder-lookup-and-restoration--2026-09-15).

The default target is the USB-only **JumpHeight-8673**, device UID
`2513620E30AE413D`. No battery is needed. The host checks both the USB serial
identity and the firmware identity before requesting a recording. This firmware
has no product detector, BLE service or flash recorder. Installing it replaces
the device's current application; building or running the host script does not
install it.

Software verification: **87 tests pass**, including a host/device codec
comparison compiled from the actual C++ helpers. This does not establish sensor
calibration, correct physical mounting, or measured height accuracy. Hardware
results must be recorded separately with their capture manifests.

**Live bench result, 2026-09-15:** installed on 8673 and recorded 7,372 samples
across 5-second and 30-second checks without reported acquisition errors.
The sensor's nominal clock ran about **2.78% faster than the MCU clock**;
clock calibration remains open. See [measured results and evidence](BENCH_RESULTS.md).

## Build, test and capture

Run from the repository root. Building needs PlatformIO; capture needs Python
3.10+ and `pyserial`; the protocol test needs a C++ compiler. Use an environment
with those dependencies installed.

```sh
pio run -d research/usb_recorder
python3 -m unittest discover -s research/usb_recorder -p 'test_*.py' -v
python3 research/usb_recorder/capture.py capture --seconds 3 \
  --output-dir data/bench/usb-recorder/check-01
```

The build output is under
`research/usb_recorder/.pio/build/xiaoblesense_adafruit/`. Installation is a
separate, explicitly board-pinned operation; the capture command expects this
research firmware to be installed already. An ordinary product build will fail
the research-recorder handshake.

Capture duration is **0.1–1200 seconds** for the prepared firmware, and **0.1–60
seconds** for the installed original firmware. The host checks the device's
advertised limit before sending a capture command. Each output directory must be new: the
script refuses an existing path. `--port /dev/cu.…` can select a path but cannot
bypass UID checking. `--uid` explicitly selects another exact device identity;
leave the default when using 8673. Before capture, check `lsof` for an existing
port owner and stop local sync/serial clients. The local JumpHeight Sync launch
agent is paused for this study because it opened the research board concurrently.
The host requests an advisory lock and a kernel `TIOCEXCL` lock at 115200 baud;
the latter blocks subsequent unprivileged opens but cannot evict existing handles.
It never uses the 1200-baud reset mechanism or flashes a device.

Exit status `0` means the capture passed the acquisition integrity checks.
Status `2` means it did not; inspect `manifest.json`. Failed capture evidence is
retained. No `imu.csv` is produced for an unusable capture.
`quality.usable` is a transport/acquisition decision, **not a calibrated time,
acceleration or height result**.

## Output files

| File | Contents |
|---|---|
| `raw.bin` | Exact bytes received during the capture, including frames and CRCs. |
| `raw.csv` | Successfully decoded sample frames: integer counts, original timestamps, sequence, flags and FIFO occupancy. Corrupt frames are not invented or repaired. |
| `imu.csv` | Passing captures only: nominal SI values in the estimator's seven-column format. |
| `manifest.json` | Firmware identity and metadata, host/USB provenance, requested duration, end counters, quality errors, interval statistics and file SHA-256 hashes. |
| `host_receive.csv` | Half-open raw byte offsets for every read and before/after timestamps from host monotonic and realtime clocks. Empty reads are retained. |
| `status.json` | Exact decoded STATUS records, including hardware clock registers and die temperature when supported. Empty on the original firmware. |
| `fifo_raw.csv` | Sequence and all 18 original FIFO bytes per sample, when the prepared firmware advertises raw frames. |

`imu.csv` has exactly these columns:

```text
t_s,ax_mps2,ay_mps2,az_mps2,gx_rps,gy_rps,gz_rps
```

These are **uncalibrated sensor-axis readings**. Conversion uses
`accel_count × 0.000488 × 9.80665` for m/s² and
`gyro_count × 0.070 × π/180` for rad/s. Stationary gravity remains in the
accelerometer's specific-force measurement. No bias subtraction, world-frame
rotation, gravity subtraction, pose labelling or height reconstruction occurs.

`t_s` uses the 24-bit FIFO timestamp, unwrapped at 25 µs per tick and relative to
the first sample. `mcu_us` is a TIMER4 service/readout timestamp, retained only
as a diagnostic. It is never substituted for a sample timestamp. The firmware
discards one startup FIFO frame and reports that fact in its metadata/counters.

**The 25 µs tick duration is nominal, not a measured clock calibration.** A
constant sensor-clock scale error can pass the integrity gates. The reported
intervals and sample rate use this nominal conversion. Compare sensor elapsed
time with MCU service elapsed time as a diagnostic, and establish the clock
scale against an independent reference before treating `t_s` as calibrated SI
time. Neither clock is certified by successful USB transmission.

## First USB bench session

**Blocked by the clock study:** do not begin calibration or motion work until
Task 1 passes. The following is the later pose-acquisition procedure; retain a
second six-pose round and at least two oblique poses for validation, and repeat
over the required temperature span.

1. **Prepare the mount.** Attach the board firmly to a small rigid nonconductive
   block with six flat faces. Keep components and contacts clear of conductive
   objects. Secure the USB cable to the block, with a loose loop between the
   block and Mac so moving the block does not tug the connector. Keep the same
   mounting throughout the session.
2. **Warm up and check.** Leave it powered for 3–5 minutes as a starting
   procedure, then record the three-second check above while the block rests
   untouched. Inspect its manifest and confirm `quality.usable` is true. This
   warm-up time is not proof that temperature drift has settled.
3. **Identify six faces.** Mark the block `x+`, `x-`, `y+`, `y-`, `z+`, `z-` using
   the sensor/package-axis orientation. Cross-check with stationary readings:
   `x+` means sensor +x points upward and ax is near +g; `x-` means ax is near
   -g, and similarly for y and z. Use the known package orientation and flat
   faces to establish alignment; do not infer precise alignment from a fitted
   calibration or guess it from the USB connector.
4. **Record each pose.** Place the required face on a stable, level surface,
   release the block, allow two seconds to settle, then capture ten seconds.
   Do not hold it by hand during the stationary recording. Record all six
   orientations with separate, clearly named output directories.
5. **Repeat independently.** Reposition the block and repeat all six poses.
   Record the first pose once more at the end to reveal drift. Note the pose,
   repetition, mounting, start/end times and any cable movement. Keep failures
   and their notes rather than replacing their files.

Example for one already positioned pose:

```sh
python3 research/usb_recorder/capture.py capture --seconds 10 \
  --output-dir data/bench/usb-recorder/x-plus-01
```

Use corresponding names for the other five poses and `-02` for the second
round. A six-pose set fits per-axis accelerometer scale/offset and stationary
gyro offset. It does **not** establish gyro scale, cross-axis alignment or
dynamic performance. Build the estimator's `accel_poses` and `gyro_poses` JSON
from these labelled SI recordings as described in [its input contract](../README.md#six-pose-calibration-json);
this recorder does not yet assemble that JSON automatically. Use one complete
round for fitting and the other for an independent repeatability check.

After stationary recording quality and repeatability are reviewed, the next
experiment is supported vertical motion between independently measured stops,
with USB slack and repeated durations. Record the measured geometry separately;
the six-axis data does not supply an independent height reference. Do not treat
hand-held motion or an imposed endpoint closure as height ground truth.

## Wire protocol, version 1

Commands and replies use USB serial. Lines are ASCII, terminated by `\n`.

| Command | Reply |
|---|---|
| `info` | `JH6 INFO {JSON}` followed by `OK info`. |
| `capture <duration_ms>` | Binary frames: metadata, samples/status, optional error, then end. Prepared firmware accepts 100–1200000 ms; original accepts up to 60000 ms. Startup failure may emit an error without an end frame. |
| `help` | Command list and `OK help`. |
| `dfu` | `OK dfu`, followed by entering serial DFU. The host capture script never sends this command. |

Binary framing is little-endian:

```text
header   <4sBBH    magic=b'JH6F', version=1, type, payload_length
payload  N bytes
checksum <I       CRC32(header + payload), compatible with Python zlib.crc32
```

Frame types: `0` metadata JSON, `1` sample, `2` end JSON, `3` error JSON,
`4` status JSON and `5` raw FIFO bytes (prepared firmware).
The header is 8 bytes and the CRC is 4 bytes. Sample payloads are exactly
28 bytes, packed as `<IIIhhhhhhHH`:

| Field order | Type | Meaning |
|---|---|---|
| `seq`, `mcu_us`, `sensor_ticks` | Three uint32 | Zero-based sample sequence, service time, decoded 24-bit sensor time. |
| `gx`, `gy`, `gz`, `ax`, `ay`, `az` | Six int16 | Original sensor counts; gyro comes first on the wire. |
| `flags`, `fifo_words` | Two uint16 | Quality flags and FIFO occupancy before reading this sample. |

Flag bits 0–2 indicate accelerometer x/y/z near a rail; bits 3–5 indicate gyro
x/y/z near a rail; bit 6 indicates FIFO overrun; bit 7 indicates a valid sensor
timestamp. The device rail threshold is an absolute count of 32760. Other bits
are currently invalid. Firmware decodes the FIFO's unusual timestamp byte order
before sending the ordinary uint32 `sensor_ticks` field; see
[`protocol.h`](include/protocol.h) and the C++ protocol test.

Required identity/configuration metadata:
`recorder="jh6-usb-research"`, `uid`, `name`, nonempty `build`, `odr_hz`,
`accel_g_per_lsb=0.000488`, `gyro_dps_per_lsb=0.070`,
`timestamp_tick_us=25`, `timestamp_bits=24`, `timestamp_mode="fifo"`.
The build identifier hashes recorder sources/configuration and the reused
bounded I²C driver. Additional metadata describes sensor initialization,
startup discard, FIFO format, filters and capture duration.

Prepared firmware STATUS is emitted at capture start/end and approximately once
per MCU second. Fields: `seq_next`, `mcu_us`, `temp_read_before_us`, `hfclkstat`,
`timer4_prescaler`, `timer4_bitmode`, `timer4_mode`, `temp_raw`, `temp_c`.
Die temperature uses nominal `25 + raw/256` °C; its absolute offset is uncalibrated.
The samples remain unchanged nine-word FIFO records; a status record is not an
IMU sample. Counter readbacks preserve raw values rather than silently asserting
a crystal source.

Type 5 has payload `<I18s>`: sample sequence and all 18 original FIFO bytes. If
metadata says `raw_fifo_frames=true`, each sample must be immediately preceded
by exactly one matching raw frame. Sequence, axis bytes and decoded timestamp
must agree. Original bytes are retained on failure; this check cannot prove
the sensor/I²C data were correct before USB framing.

## External clock study

After installation and identity verification, record a reference bracket and
an 11-minute uninterrupted capture using fresh directories:

```sh
python3 research/usb_recorder/ntp_probe.py --output-dir data/bench/clock-before
caffeinate -i python3 research/usb_recorder/capture.py capture --seconds 660 \
  --output-dir data/bench/clock-run
python3 research/usb_recorder/ntp_probe.py --output-dir data/bench/clock-after
python3 research/usb_recorder/analyze_clock.py data/bench/clock-run \
  --ntp-before data/bench/clock-before/probe.json \
  --ntp-after data/bench/clock-after/probe.json
```

NTP probes are read-only and preserve packets, failures, offset/delay allowances
and host discipline evidence. They do not change clock configuration. Analysis
preserves nominal times and reports relative fits, window slopes, residuals,
autocorrelation and NTP offset-change uncertainty. It does not produce calibrated
IMU data or equate statistical fit uncertainty with an absolute accuracy bound.

End JSON requires nonnegative integer `sample_count`, `dropped_frames`,
`i2c_errors` and `fifo_overruns`. Firmware also reports elapsed time, startup
discards, endpoint sensor/service timestamps, maximum FIFO occupancy and failure
reason. There is no ASCII trailer after the end frame.

## Integrity gates and their limits

Acceptance requires valid framing/version/length/CRC, one initial metadata
frame matching the handshake, contiguous zero-based sequences, a complete end
record matching decoded count, zero loss/error/overrun counters, no rail/fault
flags, valid sensor timestamps and at least two samples. Unknown flags, error
frames, missing data and data after the end frame all fail the capture.

Every sensor interval must fall within **±25% of the nominal period**. The
sample count must be within `max(2, 5% of nominal count)` of requested duration
times ODR. These are explicit acquisition heuristics, not measurement-accuracy
limits. The manifest includes actual interval statistics and inferred sample
rate; the host never resamples a failing clock into a nominally regular stream.

A passing capture does not prove axis alignment, calibration, gravity
projection, analogue sensor bandwidth, exact accelerometer/gyro phase response,
event boundaries, sensor endpoint geometry or jump-height accuracy. Those need
the separate bench/reference experiments described in the
[accuracy review](../../docs/accuracy-review-2026-09-15.md).
