# USB recorder: first hardware check

Measured 2026-09-15 evening EDT (2026-09-16 UTC). **Acquisition works on the
connected USB-only board. Time, axis calibration and height accuracy remain
unvalidated.** This is a recorder readiness check, not a jump-height result.

**Follow-up:** the [external-reference clock study](TIMEBASE_STUDY.md) now gates
all pose calibration and supported-motion work. The source has a prepared
instrumented build; the board still runs the original build below because its
software bootloader-entry attempt failed. This document preserves the original
measurements; it does not establish TIMER4 as the reference clock.

## Device and deployment

- Board: **JumpHeight-8673**, USB UID `2513620E30AE413D`; no battery.
- Replaced application `src=c5eea285` with research recorder build
  **`9c9289b69af5b76a`** in one serial DFU upload. The uploader reported
  `Device programmed.`; the application returned the expected UID, name,
  build and `sensor_ok=true` afterward.
- The research application has no BLE, product jump detector or flash logger.
  Existing production source was not edited. This board now needs the research
  capture script; it does not speak the normal product protocol.
- Build passed; RAM 9,780 bytes, flash 44,532 bytes. All **30 recorder tests**
  passed, including corrupt/truncated streams, device pinning, raw preservation,
  SI conversion and the actual C++ codec helpers.
- Uploaded ZIP SHA-256:
  `1e974122c087825ffbdccb016f61d52552d0abbc8eb1c11e0c30bf87183a9f4b`.
- Verified rollback application: `web/firmware/jumpheight-c5eea285.zip`, SHA-256
  `a5cdd54711430167e506b8056efd4f0ad0335b4b595bdaadb35f3a23ef9eafd1`.
  A copy of the uploaded research ZIP is retained with deployment evidence.

Local deployment evidence: [preflight](captures/deployment-20260915/preflight.json),
[upload transcript](captures/deployment-20260915/flash-transcript.txt),
[post-flash identity](captures/deployment-20260915/postflash-info.json), and
[diagnostic values](captures/deployment-20260915/diagnostics.json).
Capture folders are intentionally git-ignored; preserve them separately when
sharing this report. Paths below refer to this checkout's local evidence.

## Actual captures

The board's physical pose and movement were not independently observed or
labelled. Neither recording is a stationary calibration or known-motion test.

| Measurement | Initial check | Longer check |
|---|---:|---:|
| Requested duration, MCU clock | 5 s | 30 s |
| Complete six-axis samples | 1,052 | 6,320 |
| CRC/framing/sequence errors | 0 | 0 |
| Reported I²C errors / dropped frames / FIFO overruns | 0 / 0 / 0 | 0 / 0 / 0 |
| Samples with near-rail axis flags | 0 | 0 |
| Startup samples discarded | 1 | 1 |
| Maximum FIFO occupancy | 9 words | 9 words |
| First-to-last MCU service span | 4.988597 s | 29.993199 s |
| First-to-last sensor span, nominal 25 µs/tick | 5.127050 s | 30.828125 s |
| Sensor/MCU slope, all-sample linear fit | 1.02775866 | 1.02784465 |
| Implied sensor tick relative to MCU | 24.32478 µs | 24.32274 µs |
| Host command-to-END wall duration | 5.03390 s | 30.25697 s |

The nominal sensor intervals were 4.85–4.90 ms in both runs. All six raw channels
varied; readings were retained without bias correction or normalization.
Mean acceleration magnitude was about 0.9621 g. With no independently verified
stationarity/orientation, that number alone cannot identify a calibration error.

Evidence: [5-second manifest](captures/20260915-initial-5s/manifest.json),
[30-second manifest](captures/20260915-clock-check-30s/manifest.json).
Each folder contains the received binary bytes, decoded raw integers, nominal
SI CSV and file hashes. These original manifests predate the host's explicit
`timebase_calibrated: false` field; their time conversion is equally uncalibrated.

## Accuracy finding: timebase needs attention

Using 25 µs as an exact tick length makes sensor elapsed time about **2.78%
longer than MCU elapsed time**. The agreement across two durations makes this
worth investigating before trajectory reconstruction. It is not explained by
USB sample-arrival jitter: the sensor timestamps come from FIFO records, and
MCU timestamps are taken at sensor readout before USB transmission.

The installed TinyUSB implementation requests the high-accuracy HF clock and
waits for it before connecting USB (`dcd_nrf5x.c`, `hfclk_enable` and
`USB_EVT_READY`). This suggests a crystal clock is intended; direct register
readback and an external comparison are still required. The old host wall timing
includes command/USB buffering and a fixed-size read with a 250 ms timeout.
Its endpoint overhead depends on stream/read phase, not solely on a fixed
constant. Two endpoint ratios cannot distinguish host buffering from clock
rate error. The follow-up logs every read against both host clocks and NTP.

Do not substitute service timestamps as sample times or silently force 208 Hz.
Estimate sensor clock scale over a long record, retain both original clocks,
validate on a separate record, and check stability across warm-up and power
cycles. The fitted values above are diagnostics, not an applied correction.
No firmware reflash was done to chase this finding.

For intuition only, a uniform 2.78% time stretch changes a zero-endpoint,
fixed-acceleration reconstruction's height by approximately
`1.0278² − 1 = 5.64%`. That would be about 17 cm at 3 m, already comparable to
the user's ±15.24 cm target. Actual six-axis reconstruction also changes attitude
integration, so this is not the error measured on a real jump.

## Next operator session

1. Keep the board USB-powered. Secure it to a small rigid nonconductive block
   with six flat faces; anchor the cable and leave slack. No battery soldering
   is needed for these measurements.
2. Follow the [six-pose procedure](README.md#first-usb-bench-session): settle,
   record each axis upward/downward, then reposition and repeat independently.
   Fit the first round; use the second to assess repeatability and drift.
3. Establish clock scale and stationary gyro/accelerometer calibration before
   processing controlled motion. Static poses cannot calibrate gyro scale.
4. Record supported vertical motion with externally measured stops and
   independently known initial tilt and timing. Include repeated durations and
   changing orientation. Only then compare reconstructed apex heights with the
   reference, including the reference's uncertainty.

**Current gate:** resolve timebase first. Calibration and motion experiments
remain pending; ±0.5 ft wing-foiling jump accuracy has not been established.

## Sensor configuration references

Register layout, nominal timestamp tick and FIFO timestamp ordering were checked
against ST's [LSM6DS3TR-C datasheet](https://www.st.com/resource/en/datasheet/lsm6ds3tr-c.pdf)
and [AN5130](https://www.st.com/content/ccc/resource/technical/document/application_note/group0/90/41/09/82/24/d9/4f/29/DM00472670/files/DM00472670.pdf/jcr:content/translations/en.DM00472670.pdf),
sections 6.5, 8.4 and 8.8. The timestamp byte order is also covered by the C++
codec test. FIFO co-batching does not certify identical accelerometer/gyro
filter delays.
