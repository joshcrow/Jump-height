# Bench playbook — the bulletproof-pipeline doctrine

**Provenance:** distilled 2026-08-12 from three bench sessions (08-10 → 08-12)
that produced one wrong dead-hardware verdict, five diagnostic designs, ten
firmware commits, and the project's first fully wireless firmware pipeline.
This is the operational half of the doubt-list convention: FIRST_BOOT files
say what to *verify*; this file says how to *work* so verification can be
trusted. Read it before any bench session; amend it when it costs you an hour.

## 1. Board registry (keep current — identity mistakes flash the wrong board)

| Board | USB serial (our fw) | BLE addr (this Mac) | Bootloader BLE | Role |
|---|---|---|---|---|
| **"Puck"** — new Sense (2026-08-12, no battery yet) | `2513620E30AE413D` | `B96D14EA…` | (unrecorded) | **THE product board.** Sensor + gyro healthy (0.970 g / 0.0010 g noise). Gets the box, the battery, the calibration. |
| **"Mule"** — original Sense | `7ACE98D972CB56F8` | `185D88EE…` | `EB2503CC…` | Bench test mule. IMU bus held (cause open); radio/storage/bootloader all healthy; holds the 61-jump history. OTA-gate-proven. |

Factory-fresh boards report a *different* USB serial than our firmware does
(theirs is a Seeed string, ours is the chip ID) — a board's serial *changing*
after first flash is expected, not a swap.

**Rules the registry exists for:**
- Two boards on USB ⇒ **always** `--upload-port` explicitly.
- Two boards advertising `JumpHeight` ⇒ **always** `OTADFU_ADDR=` pin.
- After any flash, re-enumerate ports by serial number, never assume.

## 2. The instrument doctrine (what the wrong RCA taught)

1. **A diagnostic that can false-negative is worse than none.** Three probe
   designs each convicted a healthy sensor. If a check's failure mode is
   indistinguishable from the fault it checks for, it must not ship.
2. **Never diagnose through the code under suspicion.** Every post-cold-start
   "confirmation" of the dead sensor ran through the broken probe. Archaeology
   builds (worktree at a known-good commit) are cheap — use them as the
   control arm *on the same hardware*.
3. **Positive controls before verdicts.** A probe that has never been seen to
   PASS on a known-good target proves nothing when it fails. The fresh board
   was the positive control that unraveled everything; a $15 spare is
   cheaper than one wrong conclusion.
4. **Falsifiers in writing.** The RCA was caught in hours because it named,
   in advance, the experiment that would disprove it. Verdicts without
   falsifiers are stories.
5. **Failure arcs are weak evidence.** "Fine all day, intermittent, then
   dead" fit solder fatigue perfectly — and described a software race.

## 3. The transport doctrine (macOS is an unreliable witness)

- **Nothing counts as sent until its effect is observed.** BLE writes return
  success for commands that never execute; serial writes vanish into wedged
  CDC nodes. The `dfu` trigger requires seeing `OK dfu` back; treat every
  irreversible command the same way.
- **pio's uploader lies.** It prints `SUCCESS` over a failed
  `adafruit-nrfutil`. Trust only the literal `Device programmed.` line.
- **CoreBluetooth degrades with session length** (blind scans, dead
  notification paths, stale GATT caches that hide services). Remedies, in
  order: `blueutil -p 0 && sleep 3 && blueutil -p 1`; 12 s+ scan patience
  after; full re-pairing only if a *service* is missing from a GATT you know
  is there.
- **macOS CDC nodes wedge** after heavy replug/reset cycles (device present
  in `ioreg`, no `/dev/cu.*`). They usually self-heal; the board is rarely
  the problem. `ioreg` before blaming hardware.
- **Serial captures across reboots need reconnecting readers** — a plain
  read loop dies silently at the first re-enumeration and fakes a "hang".
  Count boot banners; one banner ≠ one boot unless the window says so.

## 4. Device power truths

- **A battery-backed board never truly resets.** Every wedge that survives
  "unplug it" survives because the battery kept the chips alive. True cold
  start = battery *and* USB removed. The battery-less puck-in-bring-up is the
  easy case: replug = power cycle — for the MCU *and* the QSPI flash and IMU.
- **In OTA-DFU mode the board has no USB at all.** The radio is the only way
  in; a failed transfer strands it dark until physical reset. Never enter DFU
  without a recovery plan.
- **The 1200-baud touch is an app feature.** Bootloaders and hung apps don't
  implement it. Bootloader UF2 (MSC) mode is entered by magic `0x57` (`uf2`
  command) — the 1200-touch enters *serial-only* DFU by design.
- **uhubctl doesn't work on the current hub.** A PPPS-capable hub (~$30)
  would make bench power fully software-controlled and retire half of this
  file's "ask a human to replug" cases.

## 5. Recovery ladder (try in order; each rung proven on silicon)

| Symptom | Remedy |
|---|---|
| App alive, commands work | `dfu` / `uf2` / `off` / `format` as needed |
| App advertising but command-dead | **BLEDfu control point** (subscribe, write `0x01`) — runs on the BLE task, survives a dead `loop()`; then `0x06` from the bootloader reboots clean |
| Bootloader stale mid-DFU | `otadfu.py` auto-recovers (0x07 probe → `0x06`) — with USB *out*; with USB in, `0x06` lands in UF2/serial mode: recover by serial flash |
| Boot loops at `SELFTEST BEGIN` | Already handled: the jh_persist sticky guard turns the second boot into an honest `i2c FAIL` + READY. `selftest` command = deliberate retry |
| Storage unmountable | `format` command (works with fs down). If the chip won't even probe: physical power-cycle (battery-less board: replug) |
| Dark on every interface | Physical: reset tap or power cycle. This is the case to engineer away, not accept |

## 6. Firmware invariants (hold these in review)

- **Boot must complete no matter what a peripheral does.** No unbounded
  first-contact on any bus; crash-guards must live in storage that survives
  the *whole* reset path — measured: `.noinit` doesn't exist in this core's
  linker scripts, GPREGRET2 is sanitized through the bootloader,
  **jh_persist (internal LittleFS) survives everything**.
- **Untrusted-input boundaries hold the line**: radio bytes (bare `catch` in
  the watch's ingest; corruption gate in its Model), flash state (mount
  failures degrade, never block), sensor presence (FAIL row, not a hang).
- **Every recovery path must be reachable over the air** — the sealed box has
  no other hands: `dfu` (reflash), `format` (storage), `selftest` (sensor
  retry), BLEDfu (wedged-app rescue). Anything new that can fail needs its
  radio-reachable recovery named in review.
- **Migrations never discard.** jh_persist v1→v2→v3 pattern: a shorter valid
  record is old, not corrupt.

## 7. Standing predictions (check on next bench contact)

1. The new board's QSPI mounts fine after its replug (the flash chip itself
   gets power-cycled; its stuck mode was the interrupted-format residue). If
   not: `format` command; if *that* fails, the chip state theory is wrong.
2. The new board's mid-afternoon dark-out recurs under the gyro hot path —
   watch its serial for the first hour after revival. If it recurs: hard
   fault in `d504c54`'s first real-sensor execution is the prime suspect.
3. The mule's sensor bus, probed by *meter* rather than code: either ~0 V on
   the rail (real hardware fault) or 3.3 V (and the mystery reopens with
   better instruments).
