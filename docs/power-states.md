# Power architecture — states, transitions, and the end-user story

> **IMPLEMENTATION STATUS (2026-08-14): NONE OF THIS IS BUILT YET.**
> Verified by reading the code, not by memory:
> - `INT1` (the IMU's motion-wake interrupt) is never configured as a wake
>   source — `jh_imu.cpp` only ever floats it. **No wake-on-motion.**
> - There is **no STANDBY tier**. The device has exactly two states: fully
>   on, or `off` (System OFF, manual command only).
> - The BLE advertisement carries flags, TX power, the NUS service UUID and
>   the name — **no battery level, no armed state**.
> - The 20 s idle timeout gates *recording only*; it does not change power.
> - There is no auto-off, no charger-aware behavior beyond reading `~CHG`.
>
> What IS measured and true: `off` performs the audited detach then System
> OFF, and does **not** wake from steady VBUS — it needs a VBUS edge or the
> reset button (§16j). That confirms the charger-wake design below works on
> real silicon, and it is the only part of this document with silicon
> behind it.
>
> This stays deliberately unbuilt until the water session (§6).
>
> **§0-§5b were rewritten 2026-08-14** after the drive-strength root
> cause and the schematic facts landed. The headline change: the product
> now performs **zero sensor-rail transitions**, because the arithmetic
> shows cutting the rail saves 3 µA against a ~10 µA floor dominated by
> battery self-discharge. The feared operation is designed out, not
> merely done carefully.


Drafted 2026-08-13, while the third board waited at the macOS Allow
prompt. Owner's framing, verbatim spirit: "we lack a comprehensive
power on / power off / power save design — we haven't thought through
an elegant solution for the end user at our destination state." This
document is that design. It is a DESIGN, deliberately not an
implementation plan to start tomorrow — see §6 for where it sits in
the roadmap.

## 0. The fear, answered with evidence (read this first)

The premise behind "power code killed our boards" was mine, and it was
wrong. What the record actually shows:

- **`off` shipped 2026-08-04** (54fa232) and was used for a week.
- **On 2026-08-11** — seven days and many `off` cycles later — that same
  board read gravity perfectly and produced the drop calibration
  (`airtime_offset_s = +0.0257`, a6e477d).
- **The real fault (GPIO drive strength) was present from the very first
  boot**, in `jh_imu::init()`, and explains every symptom on all three
  boards including the intermittency. Fixed 2026-08-14; both "dead"
  boards immediately read gravity again.

So `off` had a week of proven coexistence with a healthy sensor, and the
thing that actually broke everything was never in the power path at all.
**No board was damaged. No board has ever been damaged.**

That said, "the old theory was wrong" is not the same as "power code is
safe," and the fear points at something real. The schematic (§1 of
[xiao-hardware-truth.md](xiao-hardware-truth.md)) shows the genuine
hazard: R14/R15 (10k) tie SDA and SCL directly to the sensor rail, so
driving a bus line high while the rail is down back-powers the die
through those resistors. That is a real mechanism. It has never been
shown to have damaged anything here, and the design below makes it
unreachable rather than merely unlikely.

## 2. The fact that changes the whole design

**The GPIO pad IS the sensor's power supply.** P1.08 drives no regulator
and no FET; it feeds the LSM6DS3TR-C's VDD/VDDIO and both bus pull-ups
directly. Every "power state" question therefore reduces to one thing:
*do we ever stop driving that pin?*

And once you ask it that way, the arithmetic answers it.

## 3. The arithmetic that removes the dangerous operation

Datasheet and measured figures (sources in §11):

| Consumer | Current |
|---|---|
| LSM6DS3TR-C, **power-down** (registers alive, rail up) | **3 µA** |
| LSM6DS3TR-C, accel low-power 12.5 Hz + wake-on-motion | **9 µA** |
| nRF52840 **System OFF** (XIAO, measured) | **2.4 µA** |
| nRF52840 **System ON** idle + RTC (XIAO, measured) | **5.4 µA** |
| BLE advertising @2 s, added | **~10 µA** |
| **LiPo self-discharge, 250 mAh** (~2 %/month) | **~7 µA equivalent** |

Now the only question that matters:

> **What does cutting the sensor rail actually save? 3 µA.**

Shelf life with the rail left UP and the sensor in power-down:
`2.4 + 3 + 7 = 12.4 µA` → **~2.3 years**.
Shelf life with the rail CUT: `2.4 + 7 = 9.4 µA` → **~3.0 years**.

Both are far beyond the cell's practical life, and **both are dominated
by self-discharge, not by the sensor.** The rail cut buys a rounding
error, and it is the single most dangerous operation in the codebase —
the one that needs sequencing, that can back-power the die, and that has
consumed four days of this project's life.

**So we stop doing it.**

### THE RULE

> **The sensor rail is asserted once, at boot, with high drive, and is
> never changed again for the life of the power-up. The product performs
> ZERO rail transitions. A design that needs one is wrong.**

Everything the sensor needs — full-rate, low-power wake-on-motion, or
fully asleep at 3 µA — is reached by **register writes over a powered
bus**, which is an ordinary, safe, bounded I2C transaction. Power states
become software states. The electrically dangerous operation is deleted
from normal life entirely.

`revive()` (the one rail power-cycle) survives only as a **human-invoked
bench recovery command**. Nothing automatic may ever call it.

## 4. The state machine (rail-static)

```
                 motion on INT1                  stillness (2 min)
   STANDBY ──────────────────────▶  SESSION ──────────────────────▶ STANDBY
      │                                                                ▲
      │  `shelf` command, or N days with no session                    │
      ▼                                                                │
    SHELF ─────────────────────────────────────────────────────────────┘
              VBUS (charger) / reset / INT1 motion  →  cold boot

   In ALL THREE states P1.08 stays HIGH. The rail never moves.
   CHARGING is an overlay: the BQ25101 charges the cell in every state.
```

| | MCU | Sensor (via register writes only) | BLE | Total | Life on 250 mAh |
|---|---|---|---|---|---|
| **SESSION** | active | 208 Hz + gyro | connected | ~4 mA | a session day |
| **STANDBY** | System ON idle | accel LP 12.5 Hz, wake-on-motion | slow adv (2 s) | ~24 µA | **~11 months** |
| **SHELF** | System OFF | power-down (3 µA) | off | ~5.4 µA + self-disch. | **~2.3 years** |

Two deliberate choices worth stating:

1. **STANDBY keeps BLE advertising** even though System OFF would be
   cheaper (~11 µA vs ~24 µA). Eleven months of standby is already far
   more than the product needs, and the advertisement is what makes the
   puck *visible on the wrist at rest* — the direct answer to "am I sure
   it's on?" Spending 13 µA to remove that anxiety is the right trade.
2. **SHELF wakes on motion too**, not just the charger. The nRF52840's
   GPIO DETECT is a System OFF wake source, so INT1 still works. Waking
   from SHELF is a reset (cold boot), which is fine and is what already
   happens on every power-up.

## 5. Transitions — exactly what changes, and what never does

| Transition | What actually happens | Rail |
|---|---|---|
| boot → STANDBY | `nrf_gpio_cfg(P1.08, …H0H1…)`, set high, settle, configure IMU for wake-on-motion | **rises once, at boot** |
| STANDBY → SESSION | INT1 fires → reconfigure IMU registers to 208 Hz + gyro | untouched |
| SESSION → STANDBY | 2 min still → IMU registers back to LP wake-on-motion, flush storage | untouched |
| STANDBY → SHELF | IMU register write to power-down, announce, `sd_power_system_off()` | untouched |
| SHELF → boot | VBUS / reset / INT1 → cold boot (GPIOs reset to input, rail collapses and is re-asserted by `init()` — the bus is floating at reset too, so this is inherently safe) | one unavoidable, naturally-safe cycle |

The only rail movement in the entire product is the one that happens at
every cold boot, and at reset **all** GPIOs float simultaneously — bus
included — so the back-feed hazard cannot occur there by construction.

## 5a. REVIEW CORRECTIONS (2026-08-14, 47-agent adversarial pass)

36 findings confirmed, 7 refuted. The rail-static RULE survived — but
several numbers and two shipped code paths did not. Corrections, in
order of how badly they were wrong:

**1. SHELF cannot be 3 µA and wake on motion. Physics, not opinion.**
In power-down (ODR_XL = 0) the accelerometer produces no samples, so the
wake engine has nothing to run on and INT1 never fires. §4 claimed both.
Pick one, and the honest numbers are:
- motion-wakeable SHELF: `2.4 + 9 + 7 = 18.4 µA` → **~1.5 years**
- charger/reset-only SHELF: `2.4 + 3 + 7 = 12.4 µA` → ~2.3 years
**Decision: take the 1.5 years and keep motion wake.** A puck that only
wakes on a cable is the Garmin-footpod silent-failure this whole design
exists to avoid, and 1.5 years already outlives the cell.
Note this *strengthens* THE RULE: an unpowered sensor cannot assert INT1
either, so cutting the rail would cost the wake feature outright.

**2. The advertising budget was off by an order of magnitude.**
`jh_link.cpp` advertises at **152.5 ms**, not the 2 s §4 assumed. The
advertiser alone blows the STANDBY budget. Slow-interval advertising has
to be configured explicitly; it is not free. **FIXED 2026-08-14 (commit
`216f75f`):** `Bluefruit.Advertising.setInterval(32, 1600)` — 20 ms fast
for 30 s, then 1000 ms idle (`firmware/src/platform/nrf52/jh_link.cpp:557`).
This is the advertising rate fix only; the STANDBY tier itself (the state
machine in §4) is still not built — see the status banner at the top of
this file.

**3. There is no low-power idle to build STANDBY on.** `loop()` is a
busy poll that returns early and is immediately re-entered — the CPU
never reaches the FreeRTOS idle task, so nothing sleeps. Worse,
**TWIM1 is left ENABLE=1 forever** (only `bus_release()` clears it), and
a serial peripheral left enabled is the classic nRF52 sleep killer —
hundreds of µA, 30-40× the entire STANDBY budget. STANDBY is a
main-loop restructure, not a feature to bolt on.

**4. The 3.5 s watchdog cannot be stopped and is configured to run
during sleep** (`CONFIG = SLEEP=Run`), so it caps every sleep at ~3.4 s.
The design's 2-5 minute standby tick is impossible until that is
addressed.

**5. The blue LED blinks at ~50 % duty the whole time the puck
advertises** — Bluefruit's `_led_conn`, and nothing ever calls
`Bluefruit.autoConnLed(false)`. A constant drain today, and an absurd
behavior for a sealed puck. **FIXED 2026-08-14 (commit `216f75f`):**
`Bluefruit.autoConnLed(false)` is called in `begin()`
(`firmware/src/platform/nrf52/jh_link.cpp:552`).

**6. The DC/DC converter is never enabled**, so session and advertising
currents are both ~1.7× the figures quoted here. **FIXED 2026-08-22
(audit F-05):** `jh_power::enable_dcdc()` now runs at every boot
(`firmware/src/main.cpp:1252`, after `jh_power::init()`), not only via
the manual `dcdc` console command — measured 1.39× endurance on a
same-board A/B (STATUS.md 2026-08-20). Confirm with `info`'s `dcdc=`
field before trusting a current figure on any given boot; it is volatile
and a watchdog reset used to silently revert it before this fix.

**7. Two shipped code paths violate THE RULE right now:**
- `jh_power::system_off()` still cuts the rail — and does it with
  `pinMode()`, the exact API DECISIONS #37 bans. **HALF-FIXED, HALF
  RECONSIDERED (2026-08-14, commit `859ad42`+):** the `pinMode()` half is
  fixed — `system_off()` now uses `nrf_gpio_cfg(...H0H1...)`
  (`firmware/src/platform/nrf52/jh_power.cpp:420`). The "stop cutting the
  rail" half was NOT done, and the code now argues explicitly against
  doing it: the same function's own comment calls this "the one
  sanctioned exception" to THE RULE — deliberate, human-invoked, rare,
  and safer than swapping a proven detach-then-cut sequence for a
  register write with no verification that could leave the sensor
  drawing ~0.9 mA indefinitely on a silent failure. Read that comment
  before changing this path; THE RULE as stated in §3 no longer matches
  what shipped.
- `bus_release()` floats INT1, and `system_off()` calls it first, so the
  shutdown path **structurally cannot** have a motion wake. INT1 must be
  configured via GPIO SENSE/PORT (shared by the System ON and System OFF
  paths), never `attachInterrupt()`. **Still open 2026-08-23** — no
  wake-on-motion code exists yet (confirmed: no `STANDBY`/`SHELF`/
  `WAKE_UP_SRC`/`INACT_EN` in `firmware/src/`), consistent with the
  status banner at the top of this file.

**8. `i2cdiag` committed the back-feed itself** — it enabled internal
pull-ups on SDA/SCL while driving the rail LOW, pushing current into an
unpowered die through R14/R15, and it is reachable over BLE. **Fixed
2026-08-14**: the pull-up probe now runs only when the rail is asserted.
The lesson is the sharper one: *the diagnostic written to prove safety
was itself unsafe*, which is why rule 6 below (bench board first) is not
optional.

**9. A cold boot mid-activity permanently corrupts the Garmin FIT
record** — the real data-loss path, and it is about SHELF/reset
behavior, not about the rail. Any state that can reboot mid-session must
be reconciled on the watch side.

**Refuted** (the design was right): brownout does not create a back-feed
path; TWIM's internal pull-ups are not a standing hazard; the sensor
does get a real power-on reset at cold boot; the bootloader is not a
hole; STANDBY→SESSION does not manufacture phantom jumps; a latched
wake-up interrupt does not cause a boot loop.

**What this means for sequencing:** STANDBY is *not* the cheap first
increment it looked like. The cheap, safe, high-value first increments
were (a) `autoConnLed(false)`, (b) slow advertising, (c) enabling DC/DC,
(d) fixing `system_off()`'s drive strength. **UPDATE 2026-08-23: (a),
(b), (c) and the drive-strength half of (d) are all shipped** (see the
dated notes under findings 2/5/6/7 above); "stop cutting the rail"
specifically was reconsidered, not done — see finding 7. The sleep-tier
(STANDBY/SHELF state machine) work is what remains, and per the banner
at the top of this file, none of it exists yet.

## 5b. The safety rules any power change must satisfy

1. **Zero rail transitions in product code.** Only `jh_imu::init()`
   asserts it; only a human running `revive` may cycle it.
2. **Never energize the bus while the rail is down.** `bus_release()`
   first, always. (Naturally satisfied at reset.)
3. **The rail is configured H0H1, in exactly one place.** Never
   `pinMode()` (DECISIONS #37).
4. **Every state change is announced** on the protocol (`STATE standby`,
   `# shelf in 60 s — send any command to cancel`) so nothing ever
   disappears silently. Silent state machines are why "the board is
   dead" was said three times.
5. **Measure before trusting.** Every current figure in §3 is a
   datasheet or third-party number. Not one has been measured on OUR
   board. No state ships until its current is measured (§6b).
6. **New power code runs on the backup board first** (#3), with
   `pincensus` + `i2cdiag` before and after, and a 20-cycle soak, before
   it touches the product board.

## 6b. What must be measured before any of this is trusted

- **Baseline off-current** on our board (item 25) — the overnight
  voltage-delta method needs no meter.
- **STANDBY current**, both flavors, to confirm the 24 µA / 11 µA
  estimates within 2x.
- **The IMU's wake-on-motion configuration on OUR part**: ST's app note
  AN4650's register map is WRONG for the TR-C variant (the activity
  enable moved into `TAP_CFG`), the inactivity interrupt CANNOT be
  latched while the wake-up interrupt can, and threshold resolution is
  full-scale dependent. Get this from the TR-C datasheet, not the app
  note.
- **A missed-interrupt backstop**: an RTC tick every few minutes that
  polls the accel and re-arms, so a lost interrupt costs minutes, not a
  session.

## 6. Where this sits in the roadmap

NOT before the water milestone. Correction to an earlier framing:
drop calibration HAPPENED (a6e477d, airtime_offset_s = +0.0257
measured on silicon, on-device and in params.json — the owner went
ham drop-testing before the watch campaign). What has still never
happened is a labeled WATER session. The sequence:

1. Board #3 proven (soak ladder) — **UPDATED 2026-08-23:** the
   `selftest ×5` / `revive ×5` legs are DONE, 5/5 PASS both
   (`firmware/SENSE_FIRST_BOOT.md` §16i durability table); the `off`→wake
   leg is still not run (bench can't automate it, see
   hardware-protection.md §5 item 4c). Re-run the drop ritual on it: the
   0.0257 offset was measured on board #1 (the OG) and is assumed
   transferable, not proven per-unit — still genuinely open. **Also
   note:** the OG's OWN drop calibration is no longer live either —
   `CAL … source=defaults` as of the 2026-08-23 flash (STATUS.md
   READ-THIS-FIRST table); the drop ritual needs re-running on the OG
   too before any height number from either board is trusted.
2. One labeled water session — the remaining real milestone.
3. THEN the STANDBY tier, built to §5's rules, measured as built
   (items 25/25c on the way through).

What this document buys now: the next time a power feature is needed
(and "the watch can't find the puck" or "battery died in the bag"
WILL come up), the answer is designed, sequenced, and safe — instead
of another single-evening command with an unexamined rail edge.

---

# Research addendum (2026-08-13): motion-wake reliability, observability, charging — with sources

Six web researchers (4-agent focused workflow + deep dives on
displays and solar/primary cells) ran against the owner's challenge:
"motion wake would be slick if bulletproof and infuriating if not;
charging is also under-designed." Findings below; full sourced
reports in the session archive.

## 7. Is motion wake actually reliable? YES — with three named engineering conditions

**The owner's fear is validated by direct prior art.** Garmin's
Running Dynamics Pod is the purest motion-wake-only product (coin
cell, no button, "give it a good shake") — and its forums document
the exact nightmare: pod asleep, watch still believes it's paired,
silent no-data run. WOO (our market) has the opposite failure:
manual button arming, documented missed sessions from forgetting to
press it, and users who don't trust its LED. Both extremes fail.
Nobody in the category ships motion wake + a trustworthy wrist-side
armed indicator. We already own the wrist.

**Condition 1 — use the right interrupt, on the right register map.**
Datasheet-grade traps found:
- ST's AN4650 register map is WRONG for our exact part: the
  LSM6DS3TR-C moved/expanded the activity-inactivity enable
  (INACT_EN[1:0] in TAP_CFG 0x58); following the app note literally
  configures the wrong register.
- The activity/inactivity interrupt CANNOT be latched — it pulses
  for exactly 1/ODR and deasserts (ST engineer, confirmed forum
  answer). A slow host misses it, period. The WAKE-UP interrupt CAN
  latch (LIR=1 in TAP_CFG; cleared by reading WAKE_UP_SRC). Use the
  wake-up path for MCU wake, never the inactivity pulse.
- Threshold LSB = FS/64: at ±2g that's 31.25 mg steps; configuring
  ±16g for shock tolerance silently coarsens wake sensitivity to
  250 mg steps. Wake config and session config are different FS
  regimes — reconfigure on each transition.
- Latency floor ≈ 2 sample periods: 160 ms at 12.5 Hz. Fine — board
  handling lasts seconds.
- No amplitude hysteresis exists; threshold near the 2-3 mg noise
  floor chatters. Set ≥2 LSB + WAKE_DUR ≥ 1.

**Condition 2 — belt and braces.** One unresolved field report of
spurious/missed wake behavior exists in this register family, so the
interrupt is primary, not sole: an RTC-tick backstop (every 2-5 min:
poll the accel, variance check, re-sleep) converts "missed interrupt"
from a lost session into minutes of delay. Both paths are µA-cheap.

**Condition 3 — asymmetric thresholds.** A false wake costs ~10 min
of idle current (then re-sleep); a missed wake costs a session. So
bias sensitive: wake on car-loading-grade motion, re-sleep only
after genuinely long stillness.

**The numbers (datasheet + measured-report grade):**
| Item | Current |
|---|---|
| LSM6DS3TR-C accel low-power @12.5 Hz (wake engine on) | 9 µA |
| nRF52840 System OFF + GPIO-DETECT wake (XIAO measured) | 2.4 µA |
| nRF52840 System ON idle + RTC (RAM retained) | 3.2-5.4 µA |
| BLE advertising @2 s, 0 dBm (derived from Nordic's formula) | ~9-10 µA |
| LiPo self-discharge equivalent (~2%/mo, folklore-grade) | ~7 µA |

Two standby flavors, both months-long on 250 mAh:
- **Dark standby** (System OFF + INT1 wake): ~11 µA electronics —
  ~1.6 years — but invisible to the watch until motion.
- **Visible standby** (System ON + slow advertising): ~25 µA —
  ~10 months — the watch sees the puck (and its battery) at rest.
Since any handling wakes the puck instantly, dark standby is
acceptable: the puck is visible whenever a human is near it. Pick
after measuring real board standby (§9 bench items).

## 8. The observability answer (the actual fix for "standing on the beach unsure")

**Put battery % and armed-state in the BLE advertisement payload
itself.** The watch/phone then shows "puck ✓ 78%" WITHOUT a
connection, the moment the puck advertises. Cost: zero hardware,
trivial firmware. This single change converts "is it on?" from faith
into a glance — it is the cheapest, highest-leverage finding of the
whole research pass.

The full feedback ladder:
1. Watch datafield shows puck presence + battery from the
   advertisement (no connection needed).
2. Recovery affordance is caveman-proof: shake it, glance again
   (~seconds).
3. In SESSION, an LED heartbeat blink through the potting (mA-class
   but session-only) gives a no-watch confirmation.
4. Every state transition announces on the protocol (`STATE ...`),
   so bench and apps never see a silent disappearance.

**Zero-power state displays: researched and REJECTED for a potted
puck.** E-paper holds an image at zero power but is 1 mm glass,
0-50 °C, UV-sensitive, moisture-sensitive, and every vendor warns
against pressure/encapsulation — no prior art exists for potting
one, and all real waterproof e-ink products use air-gap housings
with windows. Sharp Memory LCD needs continuous VCOM toggling (not
zero-power) and its own app note bans epoxy amine hardeners near the
polarizer. Flip-discs are truly zero-power but mechanical.
Electrochromic segments need a refresh every ~2 min and degrade in
UV. The one potting-compatible zero-power readout is NFC (flat coil,
no window): a phone tap reads state with the puck fully asleep —
and NFC field detect is also a System OFF WAKE source on the
nRF52840 (tap-to-wake). The XIAO exposes NFC antenna pads; needs
only a coil. Flagged as a v1.5 option, unverified through salt
water/potting thickness.

## 9. Charging and energy strategy

**The biggest lever is firmware, not chemistry.** Session current
today is ~4 mA (200 Hz reads + live BLE streaming) ≈ 8 mAh/session
→ ~31 sessions per charge. The LSM6DS3TR-C has an on-chip FIFO:
buffer samples, wake the MCU every 1-2 s instead of every 5 ms, and
upload at session end (the Garmin-speed-sensor/TPMS playbook) →
~0.5 mA session ≈ 8× the sessions per charge, before touching any
battery question. This also collapses charging-UX pressure from
weekly toward monthly. (Live watch display needs only the JUMP
lines, not the 200 Hz stream — the protocol already separates them.)

**Dock (destination product): WOO already ran our experiment.**
WOO 3.0 shipped exposed charging contacts → documented salt
corrosion complaints; WOO 4.0's fix is a magnetic wireless dock
marketed on waterproof integrity. Industry default for salt gear is
magnetic pogo (hard cobalt-gold ≥0.5 µm over nickel, recessed,
ideally power-gated pads) — and Garmin still collects corroded-pin
reports. Qi-receiver-in-potted-salt-puck is an undocumented niche:
BQ51013B-class RX ($2.4) feeds a charger IC exactly like USB VBUS
(so charger-wake works unchanged; debounce the slower Qi power-up),
but it needs carrier-board hardware. Verdict: current puck keeps
gasketed USB + fresh-water rinse discipline; the sealed destination
product wants the WOO-4.0-style inductive dock.

**Solar: measured verdict — real physics, wrong problem.** A
25×25 mm cell in real conditions harvests ~40-80 mWh/day in good
sun (whole session ≈ 30 mWh; standby ≈ 1.6 mWh/day), so it CAN
cover standby forever and even net-positive a sunny session. But it
does nothing for the actual failure mode (a dark gear bag is 0 lux),
feeding it into our BQ25101 hits real snags (10.8 h safety-timer
fault, 6.5 V OVP ceiling, a hardware charge LED that would eat
20-30% of the harvest), and it demands an optical window over
250 µm silicon in the one product whose virtue is being a monolithic
sealed lump. REJECTED for this hardware; noted for a future carrier
board only as the AEM10941 topology (solar + LiPo + automatic
primary-cell backstop — the only architecture found that
structurally guarantees "always answers").

**Primary cells: checked and rejected for this duty cycle.** At
today's 4 mA sessions a CR2032 lasts ~6 weeks, CR2477 ~5 months —
fantasy as products. AFTER the FIFO firmware change, a CR2477 shape
would genuinely run 3-4 years sealed — a real v2 option, but it
forfeits recharging entirely and our LiPo+dock path is better suited
to the session pattern. Kept as a documented alternative, not the
plan.

## 10. Round 2 (gaps closed): wet contacts and NFC

**Wet-contact auto-on — COMPLEMENT, not replacement.** The dive
industry split on purpose: Suunto uses wet contacts but only ANDed
with a depth threshold; Shearwater deliberately has none (pressure
only). Manufacturer-acknowledged failure modes: sweat/rain falsely
bridge the contacts and drain the battery (Suunto's own manuals),
and a dried SALT bridge holds the circuit "wet" after the session —
worse in seawater than the freshwater record. Every vendor makes
contact-rinsing mandatory maintenance. If ever added (v2): same-metal
gold-over-nickel pair, flush (no recess for salt crystals), pulsed/AC
sensing (Suunto's patent names this as the anti-electrolysis choice),
ANDed with motion — the two signals cancel each other's false-arm
modes. Not needed for v1: motion + watch presence covers the story.

**NFC — verified on our exact module; the surprise is which half
wins.** The XIAO exposes NFC1/NFC2 only as bare SMD test pads (not
castellations); no tuning caps on board; the core leaves the pins in
NFC mode, so no UICR change needed. Field-detect wake from System
OFF is real at ~100 nA — but it arrives as a RESET (boot path must
handle it), Nordic REQUIRES a series battery diode against strong-
field return current (unfixable after potting if skipped), and the
coil must be tuned WITH the potting in place (potting detunes; it
does not attenuate — and a salt-water film is only -0.14 dB, the
eddy-loss fear is imported from UHF/2.4 GHz and does not apply at
13.56 MHz). Design low-Q so wet and dry both work.

The stronger option for THIS project: a passive tag IC (NTAG I2C
plus, ~$4 class) on the I2C bus, own coil, status byte written to
its EEPROM before sleep. A phone then reads battery/state/fault
through the potting **with the puck flat, hung, or bricked** — the
only non-destructive diagnostic window a permanently sealed unit can
have, which this project's history argues for louder than any
convenience wake. One coil only (two 13.56 MHz loops in a puck
mutually detune — either/or): choose the tag; if tap-to-wake is
wanted later, take it from the tag's field-detect pin into a GPIO,
which also sidesteps the reset-on-wake constraint. Type 2/NFC-A =
iPhone background reading, no app. Caveats: tag-IC specifics need
datasheet confirmation (vendor sites unreachable this session);
sports/marine prior art unsearched (budget), physics-derived.

**Bench items this research adds** (all measure-first, §6b rules):
- Measure real XIAO standby in both flavors (System OFF + INT1 wake;
  System ON + slow adv). Community: 2.4 µA / 5.4 µA board-level; our
  board must be confirmed (P0.14 divider state matters, ~2.3 µA).
- Measure off-current with StoreGuard set: a forum report shows
  `sd_power_system_off` failing to reach low power when QSPI flash
  was never initialized — our guarded boot skips flash init; verify
  the off path still sleeps the QSPI chip (ties to item 25).
- Verify LSM6DS3TR-C part marking (TR-C vs older DS3: 9 µA vs 24 µA
  low-power — changes every budget above).
