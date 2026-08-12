# RCA — Sense IMU failure, 2026-08-11

**Verdict: hardware failure of the LSM6DS3TR-C (or its power path) on the
XIAO nRF52840 Sense module. Firmware is exonerated by direct experiment.**
Written 2026-08-12, the morning after; investigation was demanded and it was
owed — the failing part sits on a board whose firmware changed more on
2026-08-11 than on any day of the project, so "the firmware killed it" was
the correct null hypothesis. It is rejected below with evidence, not
assurance.

## 1. Symptom timeline (all times local, from session logs)

| Time (08-11) | Event |
|---|---|
| morning–17:00 | Drop calibration + jump recording on silicon. Sensor flawless: 61 session jumps recorded, best 1.495 m. Firmware through `a6e477d`. |
| ~18:25 | First flash of the evening (pacing fix + BLEDfu — `ad51a24` content). Sensor still working: `dfu`-triggered reboots at 18:39 boot clean. |
| ~19:20 | **First anomaly**: post-OTA boot is command-dead. Intermittent from here — healthy boots at 19:52 (serial flash) and 20:33 (OTA). |
| ~19:00–20:00 | (Concurrent: capsule/kapton-tape handling by owner; box opened and re-taped at least once.) |
| ~22:20 | Wedge becomes **persistent**: every boot hangs. Captured on serial: banner stops at `SELFTEST BEGIN`, watchdog loops the boot (~12 s cycles). |
| 23:26 | Bounded bit-bang probe ships (`cc4662c`): device boots commandable, reports `i2c FAIL no_device`. Sensor still absent. |
| 08-12 morning | **True cold start** (battery AND USB removed, waited). Sensor still absent. |

## 2. The exoneration experiment (the decisive one)

Flashed `a6e477d` — the exact commit whose binary recorded the 61 jumps that
same afternoon, predating every evening change — onto the cold-started
board, from a clean git worktree:

    # calibration from device memory: airtime_offset_s=0.0257 ...
    SELFTEST BEGIN
    (hang — identical signature to the 22:20 boot loop)

Same firmware + working sensor = worked for hours.
Same firmware + today's sensor = hangs at the first bus transaction.
The firmware is the controlled variable; the hardware is the free one.

## 3. Corroborating measurements (all post-cold-start)

| Measurement | Result | Meaning |
|---|---|---|
| Bit-bang ACK probe, pins verified against `variant.cpp` (D17=P0.07 SDA, D16=P0.27 SCL) | no ACK | not a wrong-pin artifact |
| Original Wire implementation (predates all changes) | hangs | second, independent witness |
| SDA and SCL levels with internal pull-ups | **both 0** | lines clamped through an unpowered/dead chip's ESD structures — a merely-wedged slave holds SDA only |
| Rail float test (P1.08 as input, 1 ms) | **0** | the sensor rail cannot hold charge — something sinks it hard |
| Battery+USB removed, waited | no change | rules out latched register state, SCR latch-up, anything volatile |

## 4. Firmware-change audit — can any 08-11 change physically kill a sensor?

Every firmware change of 08-11, audited for physical damage capability:

- **Pacing / BLEDfu / `dfu` / `uf2` commands, bootloader work** (`ad51a24`,
  `b8dc2ba`, `ec4f403`): radio, flash, and reset-domain code. No path to the
  sensor's power or pins. Cannot harm silicon.
- **Gyro config writes** (`d504c54`, and `3abb407`/`1c13c83` earlier):
  register writes to CTRL4_C/CTRL6_C/CTRL7_G. LSM6DS3TR-C has no
  self-destructive register, and all register state is volatile — the
  battery pull is the controlled experiment that clears it. Additionally the
  sensor ran for hours **after** this code first flashed.
- **Rail power-cycle + bus-low guard + bit-bang probe** (`cc4662c`, ~23:00):
  the only code that touches the sensor's power/pins — written **3.5 hours
  after the first failure**, as a response to it. Chronologically incapable
  of causing it. Electrically: GPIO-low on I2C lines and rail is within
  design (the rail pin exists to be switched — docs/sense.md §3.7 plans
  exactly this for deep standby).

No change has both a physical mechanism and a compatible timeline. Most have
neither.

## 5. Candidate causes, ranked

1. **Mechanical — solder/interconnect fatigue from deliberate impact
   testing.** This device's *job* is impacts, and 08-11 was its biggest
   impact day ever: ~61 recorded jumps (best 1.495 m ⇒ multi-g landings),
   plus handling. LGA sensor packages and module-level joints crack exactly
   this way, and the failure *arc* — perfect all day → intermittent for
   three hours → dead — is the textbook presentation of a crack opening
   under thermal/mechanical cycling. A cracked VDD joint also explains the
   rail short reading (flexed joint bridging) or an open that leaves the die
   clamping the bus.
2. **ESD during capsule/tape work.** Kapton is strongly triboelectric;
   peeling/cutting it generates kilovolts adjacent to the board, and the
   first anomaly lands inside the handling window. ESD damage classically
   presents as degrade-then-fail rather than instant death.
3. **Infant mortality / latent defect** — always on the list, never
   provable from outside.

Note these are not mutually exclusive, and 1 vs 2 cannot be separated in
software. Both have the same remediation.

## 6. What would falsify this RCA

- **Multimeter on the sensor rail** (6D_PWR net) with the board powered:
  3.3 V ⇒ the rail-short reading was wrong and the investigation reopens.
  ~0 V ⇒ confirmed short.
- **A replacement module running identical firmware.** If a fresh XIAO
  Sense shows the same failure under this repo's firmware, the RCA is wrong
  and firmware goes back under the lamp. (Prediction, on the record: it
  will run flawlessly.)

## 7. Impact and what survives

- **Lost**: the sensor — which on the XIAO Sense is on-module. Replacement
  means a new module (~US$15) and re-transplanting the puck.
- **Survives entirely**: every line of firmware and tooling (OTA DFU, the
  0.11.0 bootloader — which travels with the *old* board, a fresh module
  gets the same upgrade via `tools/otadfu.py`), the watch field, the web
  app, the calibration *procedure* (one command; the constant itself lives
  on the dead board's QSPI and is re-measured in minutes), and the entire
  test/diagnostic apparatus this failure forced into existence — the
  bounded probe alone converts any future recurrence from "mystery boot
  loop" into one honest FAIL row.

## 8. Prevention, carried into the build

- **Bounded first contact stays forever** (`cc4662c`): sensor death must
  never again cost the boot, the radio, or the diagnosis.
- **ESD discipline during capsule work**: touch ground before handling,
  no tape peeling directly over the board, board in the case before
  wrapping.
- **Mounting**: the module should be shock-mounted relative to the capsule
  (foam interface, not rigid contact) — the capsule's job is water, the
  mount's job is to keep board strain below joint-cracking levels while
  preserving the impact signal the detector needs. To be specified in
  docs/hardware.md before the next build.
- **`fakejump` bench command** (2026-08-12): the full client pipeline is
  testable against a sensor-dead puck, so hardware failure no longer blocks
  software validation.

---

# ADDENDUM — 2026-08-12: the falsifier FIRED. Verdict revised.

Section 6 promised that a replacement module running identical firmware
would falsify this RCA if it failed. **It failed** — the factory-fresh
board reported `i2c FAIL no_device` under the current firmware — and the
investigation that followed rewrote the story. This addendum supersedes
parts of the original; what stands and what falls is listed explicitly.

## What actually happened

The bounded-probe code added on the night of 08-11 (and two successors)
produced **false negatives against healthy sensors**:

1. The bit-banged ACK probe reported no ACK from a sensor that Wire,
   asked seconds later in the same boot, ACKed immediately
   (`probe-diag: sda=1 scl=1 bb6A=0 bb68=0 wire6A=0`).
2. A GPIO level-gate then read the bus lines nondeterministically —
   `1/1` one boot, `0/0` the next, same board, same code.
3. A TWIM register-level probe also false-negatived (root cause of #2/#3
   left unchased; a missing/mismatched pin configuration interaction is
   the leading suspect).

Every post-cold-start "confirmation" of the old board's death ran through
this code. **The `sda=0 scl=0 rail_float=0` mechanism evidence in §3 is
retracted** — those reads came from instruments now proven unreliable.

## What still stands, on independent evidence

The old board IS genuinely sick: `a6e477d` — pre-dating every probe — hangs
at its first **Wire** transaction on the old board (reproduced twice on
08-12, single-banner captures, while the same binary on the new board reads
the sensor perfectly at 0.970 g). A held bus on the old board is real;
only the *mechanism claims* about rail shorts are withdrawn. Cause remains
unresolved between hardware damage and something environmental/state-based
that survives cold starts; the board is retained as a bench radio and test
mule, not scrapped.

## The fix that shipped (fifth design, and the lesson)

Every outside-in "is the bus safe?" probe lied. The shipped design stops
asking: **crash-loop detection** — a magic+flag pair in `.noinit` RAM set
before the first Wire touch and cleared after. A held bus costs one
watchdog reset (~3.5 s), after which boots skip the sensor and come up
commandable with an honest FAIL row. The healthy path runs *zero* extra
bus operations — which is why it is the first design that cannot lie.
Verified on the new board: `i2c PASS`, accel 0.970 g, noise 0.0010 g,
5/5 consecutive.

## Lessons, earned twice now

- **A diagnostic that can produce false negatives is worse than none**:
  it converted a live sensor into a confident dead-hardware verdict.
- The falsifier section is the only reason this was caught in hours
  instead of after a $15 purchase and a rebuild. RCAs without falsifiers
  are stories.
- The failure `arc` argument (§5.1's fatigue narrative) fit the wrong
  facts equally well — arcs are weak evidence.

## Still open (tracked in SENSE_FIRST_BOOT)

- Old board: root cause of the genuinely held bus.
- New board: QSPI mount fails since an interrupted-format event
  (mounted and formatted fine on its first boots; item 21's scenario);
  0xAB wake and 0x66/0x99 JEDEC reset retries both insufficient.
  Storage-independent work proceeds meanwhile.
- Boot-time selftest reads accel 0.960 g / noise 0.0966 g deterministically
  vs 0.970 g / 0.0010 g on command — likely filter settle vs boot timing;
  benign-looking, unverified.
