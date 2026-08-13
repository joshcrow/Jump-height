# Hardware protection plan — how we never cook a board again

Written 2026-08-13, the night after the second sensor stopped answering.
Two XIAO nRF52840 Sense IMUs went unresponsive in three days. The common
factor was not static, not shipping damage, not coincidence: it was this
project's own firmware electrically mistreating the sensor's switched
power domain. This document is the standing plan that makes a third
occurrence impossible-by-process, not just unlikely-by-care.

Status of blame, honestly: the back-feed mechanism (§1) is DOCUMENTED for
this module and our code committed it; whether it is the confirmed killer
of each board is settled by the meter session (§4) and, if recovery
succeeds, by the soak (§5). This plan does not depend on that verdict —
every rule below is correct regardless.

## 1. The mechanism (what actually kills sensors)

On the XIAO Sense, pin P1.08 enables a small regulator that powers BOTH
the LSM6DS3TR-C's VDD AND the on-module I2C pull-up resistors. Nordic
DevZone documents the hazard for this exact module: **any MCU line into
the sensor domain that is energized while that rail is down back-drives
the sensor die through its pins** and corrupts its internal power-up
sequencing. A corrupted sensor does not ACK — from the bus side it is
indistinguishable from dead silicon, which is how this project produced
two wrong "dead hardware" verdicts before finding the real mechanism.

Our two exposures, both fixed:
- `off` → System OFF cut the rail with the TWIM peripheral still owning
  SDA/SCL (internal pull-ups energized), and System OFF *retains* pin
  state — hours of back-feed per sleep. Fixed: commit 77951ec.
- The (removed) probe-era rail power-cycle drove the rail LOW→HIGH with
  the bus energized — the same back-feed, transient edition.

## 2. Code-level defenses (shipped, with pointers)

1. **One audited detach, copied everywhere** — `jh_imu::bus_release()`
   (nrf52): TWIM disabled, SDA/SCL floated no-pull, INT1 floated. Called
   by `jh_power::system_off()` before the rail drops and by
   `jh_imu::revive()` before its power-cycle. No other code may touch
   rail or bus-pin state; new needs extend these functions, never
   reimplement them.
2. **The safe power-up pair** — `jh_imu::revive()`: detach → rail LOW →
   600 ms discharge → rail HIGH → 45 ms settle (3 ms regulator start +
   35 ms sensor Ton + margin) → only then bus traffic.
3. **Bounded bus transactions** — `twim_bounded.h` (16d): every I2C wait
   has a ~2 ms bound. A held bus is an error return, not a hang, so no
   code is ever tempted to add "is the bus safe?" probes again (the
   probe family produced false negatives that convicted healthy
   hardware — DECISIONS #34, SENSE_FIRST_BOOT 16f).
4. **Crash-loop guards stay** (ProbeGuard/StoreGuard, jh_persist) as the
   belt-and-braces layer under all of the above.

## 3. Process rules (bench-playbook §6b, restated as the law)

1. Sequencing pairs are **copied, never improvised**.
2. Any code touching a power/rail/bus pin gets a **datasheet-or-web
   sequencing check before silicon**. The §1 hazard was searchable the
   whole time; ten minutes would have prevented it.
3. **New-board quarantine**: a fresh board meets only the current audited
   build. Never experimental electrical code.
4. **Electrical experiments run on the sacrificial board only**
   (currently: the mule).
5. **Batch flashes**; seven-flash evenings are where mistakes compound.

## 4. The meter session (next physical step)

Protocol and interpretation table: SENSE_FIRST_BOOT §16g. Outcomes:
- 6D rail ~0 V → sensor likely healthy, rail path damaged → bodge-wire
  recovery path, no new module needed.
- 6D rail ~3.3 V → die damaged → module replacement, onto FIXED firmware
  only, after the §5 gate passes.
- Mule 3V3 low → one board-level fault explains its IMU AND its QSPI.

## 5. The unseal gate (the sealed spare board)

The spare Sense stays sealed until ALL of:
1. The meter session has produced a mechanism verdict (§4).
2. The off/sleep/wake cycle has been soaked on a board with a WORKING
   sensor: ≥20 `off`→wake cycles with `selftest` PASS after every wake.
   (Requires either a recovered sensor or the replacement module on the
   mule-as-carrier first.)
3. The revive/selftest path has passed on a healthy sensor ≥5
   consecutive times (the bounded driver's healthy path has never run on
   silicon — it is proven on the held-bus path only).
4. The quarantine rule (§3.3) is in effect: the spare's first flash is
   the audited build that passed 1–3, and nothing else.

## 6. Standing verifications (added to the bench list)

- After ANY change to jh_power/jh_imu/twim_bounded: re-run the §5.2 soak
  before the change is trusted on a non-sacrificial board.
- The morning-after rule: no "dead hardware" verdict without a meter
  measurement. Software instruments convicted healthy silicon twice.
