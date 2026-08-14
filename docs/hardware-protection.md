# Hardware protection plan — how we never cook a board again

> ## ⚠️ SUPERSEDED IN PART — check [docs/STATUS.md](STATUS.md) first
>
> This file contains claims that were true when written and are now known to be
> WRONG. It is kept for its reasoning trail, not for its status. `STATUS.md` is
> the single source of truth; where they disagree, this file is stale.
> **Its founding premise is refuted.** This plan opens with "two IMUs went
> unresponsive... this project's own firmware electrically mistreating the sensor's
> switched power domain". No sensor was ever damaged, and there is no regulator on
> that net — the GPIO pad IS the supply (docs/xiao-hardware-truth.md). The sequencing
> discipline it teaches is still sound; its diagnosis and its mule verdict are not.



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
   carries BOTH a wall-clock bound (~4 ms nominal; micros() on this core
   is tick-granular, so ± ~1 ms) and a preemption-proof iteration cap. A
   held bus is an error return, not a hang; errors that race the
   self-STOP are decoded after STOPPED (not only before), and AMOUNT is
   cross-checked against the request so a short transfer can never
   report OK. No code is ever tempted to add "is the bus safe?" probes
   again (the probe family produced false negatives that convicted
   healthy hardware — DECISIONS #34, SENSE_FIRST_BOOT 16f).
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

## 4. The meter session — SUPERSEDED by the software meter (2026-08-13)

The owner ruled out a multimeter session, so the rail question was
answered in software instead: `railcheck` (branch `mule-railcheck`,
SENSE_FIRST_BOOT §16h) uses the module's own rail-powered I2C pull-ups
against weak internal pull-downs as the rail indicator, reads the EN
level back through the pin's own input buffer, and validates its method
against a known-good pin (P0.14) on the same board.

Mule verdict: `en=1 pin=0` — the EN net itself is stuck at ground. The
"rail ~0 V" outcome below, plus one level more specific: the fault is at
P1.08 or the net it drives, upstream of everything the original table
could distinguish.

Original interpretation table (kept for the Puck, still unmeasured):
- 6D rail ~0 V → sensor likely healthy, rail path damaged → bodge-wire
  recovery path, no new module needed.
- 6D rail ~3.3 V → die damaged → module replacement, onto FIXED firmware
  only, after the §5 gate passes.
- Mule 3V3 low → one board-level fault explains its IMU AND its QSPI.

## 5. The unseal gate — OVERRIDDEN by the owner 2026-08-13; executing as a verify-each-step ladder on board #3

The owner unsealed and plugged in the final spare (board #3) before the
gate could complete — the gate's soak requirement was circular anyway
(it needed a working sensor, and the only candidate working sensor IS
the spare). Replacement discipline, as executed:
1. Mechanism verdict: delivered by the software meter (§4) — the mule's
   fault is a hard EN-net-to-ground fault; the back-feed exposures in
   firmware were found and fixed independently (§2).
2. Pre-flash audit: a 10-agent adversarial review of the exact build
   before first flash (SENSE_FIRST_BOOT §16h) — no damage-capable
   defect; three real blockers fixed BEFORE flashing, including a
   cold-boot selftest artifact that would have painted the healthy
   board dead on arrival.
3. Quarantine held: board #3's first flash is clean main (`dca2985`),
   never the railcheck branch or any experimental electrical code.
4. The soak now runs ON board #3, re-ordered so every step verifies
   before the next risk is taken:
   a. `selftest` ×5 — no rail transitions at all; proves the bounded
      driver's healthy path on silicon.
   b. `revive` ×5 — one audited rail cycle per step, sensor verdict
      after EACH; stop at the first anomaly.
   c. `off`→wake ×5 tonight, ≥20 lifetime — the System OFF transition,
      selftest after every wake.
   Any FAIL stops the ladder immediately; no retry-and-hope on the
   final board.

## 6. Standing verifications (added to the bench list)

- After ANY change to jh_power/jh_imu/twim_bounded: re-run the §5.2 soak
  before the change is trusted on a non-sacrificial board.
- The morning-after rule: no "dead hardware" verdict without a meter
  measurement. Software instruments convicted healthy silicon twice.
