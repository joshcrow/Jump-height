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
   600 ms discharge → rail HIGH → **120 ms** settle → only then bus
   traffic. **CORRECTED 2026-08-23:** this used to read "45 ms (3 ms
   regulator start + 35 ms sensor Ton + margin)" — wrong on two counts.
   There is no regulator (§1's premise is refuted; see the banner above),
   and 45 ms was measured to fail: the mule NACKed its first post-revive
   config write 1 time in 6 at 45 ms, clean 12/12 at 120 ms, because the
   rail is fed through a GPIO pad and a 100 nF cap — a softer, slower rise
   than a regulator's. Shipped code and evidence:
   `firmware/src/platform/nrf52/jh_imu.cpp:198-205`; durability table in
   `firmware/SENSE_FIRST_BOOT.md` §16i.
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
   (currently: the mule). **STALE 2026-08-23:** "the mule" is a retired
   name for the OG (`JumpHeight-E2C4`), which is now the product board
   carrying the soldered battery pigtail and the only drop calibration —
   see `docs/bench-playbook.md` §1 row 1 and its retired-name row. Running
   electrical experiments on it would violate this exact rule, not follow
   it. No board is currently designated sacrificial in the registry; pick
   one deliberately (not the OG) before the next experimental change and
   name it in the registry per bench-playbook.md §1b.
5. **Batch flashes**; seven-flash evenings are where mistakes compound.

## 4. The meter session — SUPERSEDED by the software meter (2026-08-13)

The owner ruled out a multimeter session, so the rail question was
answered in software instead: `railcheck` (**pointer corrected
2026-08-23**: this lived on branch `mule-railcheck`, which no longer
exists — `main` is now the only branch. The code is archived as annotated
tag `archive/mule-railcheck` and is not present in `firmware/src/` on
`main`; recover it with `git show archive/mule-railcheck` or
`git show archive/mule-railcheck:firmware/src/platform/esp32/jh_imu.cpp`.
SENSE_FIRST_BOOT §16h) uses the module's own rail-powered I2C pull-ups
against weak internal pull-downs as the rail indicator, reads the EN
level back through the pin's own input buffer, and validates its method
against a known-good pin (P0.14) on the same board.

Mule verdict: `en=1 pin=0` — the EN net itself is stuck at ground. The
"rail ~0 V" outcome below, plus one level more specific: the fault is at
P1.08 or the net it drives, upstream of everything the original table
could distinguish. **RETRACTED 2026-08-14 (STATUS.md):** the pull-down
instrument reads LOW against a healthy rail too — with 10k pull-ups tying
SDA/SCL to the switched rail, an internal pull-down divider sits below the
input-high threshold whether the rail is up or down (DECISION #38,
xiao-hardware-truth.md:80-86). The mule's sensor later read
`accel PASS 1.029g` (SENSE_FIRST_BOOT.md:972) — the verdict below was
wrong, not just superseded.

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
      driver's healthy path on silicon. **RESULT (2026-08-14): 5/5 PASS**
      — SENSE_FIRST_BOOT.md §16i durability table.
   b. `revive` ×5 — one audited rail cycle per step, sensor verdict
      after EACH; stop at the first anomaly. **RESULT: 5/5 PASS** on
      board #3 (the mule needed a bounded retry after 2 first-contact
      failures in ~14, then ran 12/12 — same table).
   c. `off`→wake ×5 tonight, ≥20 lifetime — the System OFF transition,
      selftest after every wake. **STILL NOT RUN as specified, and not a
      silent gap**: SENSE_FIRST_BOOT.md §16j found 2026-08-14 that `off`
      does not wake on steady VBUS, only on a VBUS edge or the reset
      button — so this half of the ladder cannot be automated on this
      bench and needs a human replug per cycle. Do not read a/b's PASS as
      "the gate passed"; c is the untested third of it.
   Any FAIL stops the ladder immediately; no retry-and-hope on the
   final board.

## 6. Standing verifications (added to the bench list)

- After ANY change to jh_power/jh_imu/twim_bounded: re-run the §5 item 4
  soak ladder before the change is trusted on a non-sacrificial board.
  **Reference fixed 2026-08-23** — this pointed at "§5.2" (item 2 is the
  pre-flash audit, not the soak); the a/b/c ladder is item 4.
- The morning-after rule: no "dead hardware" verdict without a meter
  measurement. Software instruments convicted healthy silicon twice.
