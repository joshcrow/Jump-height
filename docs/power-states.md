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


Drafted 2026-08-13, while the third board waited at the macOS Allow
prompt. Owner's framing, verbatim spirit: "we lack a comprehensive
power on / power off / power save design — we haven't thought through
an elegant solution for the end user at our destination state." This
document is that design. It is a DESIGN, deliberately not an
implementation plan to start tomorrow — see §6 for where it sits in
the roadmap.

## 1. Why this document exists (the honest accounting)

The two dead sensors trace back to power-management code built ad hoc:

- `off` (54fa232) and the web power button (a3e4889) were written for
  the beach off-ritual — a real end-user need — as a single command,
  without a state design around them. The original sequence cut the
  sensor's rail with the bus energized; System OFF retains pin state;
  every sleep back-fed the sensor die for hours (16g). The mule's
  sensor stopped ACKing the day after `off` was proven on it.
- The diagnostic rail-cycling that killed the second board was itself
  chasing the mystery the first ad-hoc power code created.

The lesson is not "never sleep the device." It is: power transitions
are the most dangerous code in this project, so they must be FEW,
CENTRALIZED, and BORING. The state machine below is shaped by one
rule: **the everyday loop contains zero sensor-rail transitions.**
Rail edges happen only on rare, deliberate, announced transitions,
through the one audited sequencing pair (`bus_release`/`revive`,
DECISIONS #33).

## 2. The destination-state user story

A sealed, potted puck. No buttons, no visible ports, no ritual. The
watch is the primary UI. The user's entire power interface:

- **Pick the gear up → the puck is awake.** Carrying the board to the
  water is unmistakable motion; by the time the rider is on the water
  the session is armed. The watch finds it without being asked.
- **Leave it still → it goes quiet by itself.** Minutes of stillness
  end the session and drop to standby. Nothing to remember, nothing
  to press.
- **Between sessions it just waits.** Weeks on a shelf cost single-
  digit percent of battery. The watch can still find it (slow
  advertising) to show battery before a trip.
- **Storage or travel: tell it to sleep deeply, or let it decide.**
  An explicit "power off" from watch/app/web (flights, end of
  season), or automatically after N consecutive days without motion.
- **Waking from deep sleep = plug it into the charger.** One physical
  act, unambiguous, requires no button and no timing — VBUS is a
  hardware wake source for System OFF on the nRF52840.

Motion is the button. The charger is the deep-wake key. The watch is
the screen.

## 3. The state machine

```
            motion (INT1)                 sustained stillness
  STANDBY ────────────────▶ SESSION ────────────────────────▶ STANDBY
     │                                                          ▲
     │ `off` cmd, or N days still                               │
     ▼                                                          │
  DEEP OFF ────────────────────────────────────────────────────┘
                    VBUS attach (charger) → cold boot

  CHARGING is an overlay, not a state: the BQ25101 charges the cell
  autonomously in every state, MCU involved or not. VBUS also wakes
  DEEP OFF; ~CHG is telemetry.
```

### SESSION (today's "on")
- 200 Hz sampling, detector live, BLE connectable, trace logging.
- Budget: mA-class; a session day.
- Entry from STANDBY is purely register work: the rail is ALREADY UP.
  Reconfigure the IMU from activity-detect to 200 Hz over the bounded
  bus. No electrical transition at all.

### STANDBY (the missing tier — the important one)
- MCU: System ON idle, RAM retained, RTC running.
- IMU: POWERED, in its own hardware low-power activity-detect mode
  (accel at 1.6–12.5 Hz, wake interrupt on INT1 — µA-class per
  datasheet; exact figure is a §6b.2 pre-silicon check, not a guess
  to code against).
- BLE: slow advertising (~30 s interval) so a watch can find a
  resting puck, or off entirely — tunable after measurement.
- Wake: INT1 activity → SESSION in under a second.
- Budget target: tens of µA → months on the 250 mAh cell. The knee
  of the whole design: standby must be cheap enough that the rail
  NEVER needs to drop in normal life.
- **The rail stays up in STANDBY.** That is the point of STANDBY.

### DEEP OFF (today's `off`, correctly placed)
- The one rail transition in the machine: audited detach
  (`bus_release`) → rail down → System OFF. Sequencing fixed in
  77951ec; already shipped.
- ~µA-class; shelf life limited by LiPo self-discharge, not
  electronics.
- Wake: VBUS attach (charger) or reset. That's the contract: deep
  sleep is exited with the charging cable, nothing else.
- Entry: explicit command (watch/app/web), or auto after N days of
  STANDBY with no session (N ≈ 7, tunable), always announced over
  BLE before executing so a listening watch can log it.

## 4. Gap analysis (exists today vs. missing)

| Piece | Status |
|---|---|
| Motion gate (in-session, 20 s) | EXISTS — becomes the SESSION→STANDBY first stage |
| `off` → System OFF, safe sequencing | EXISTS (77951ec), wake-on-VBUS assumed, **off-current never measured** (item 25) |
| STANDBY tier (System ON idle + IMU activity-detect) | **MISSING — the big gap.** Today's choice is "fully on" or "deep off" |
| Wake-on-motion (INT1) | MISSING — INT1 unused by the poll-loop port; pin is wired (D18/P0.11) |
| Two-stage idle (pause recording → drop to STANDBY) | MISSING (only stage one exists) |
| Auto DEEP OFF after N days | MISSING |
| Charger-aware behavior (wake on dock, announce battery) | PARTIAL — ~CHG read exists as telemetry only |
| Tap-as-button (hardware double-tap detect) | NOT PLANNED — nice-to-have, the IMU supports it in hardware |
| Standby/off current measurements | MISSING — items 25/25c, need the meter or the overnight-delta method |
| Charging access on a sealed unit | **OPEN HARDWARE QUESTION** — potting vs. gasketed USB vs. pogo pads decides whether "plug in to deep-wake" survives the sealed design |

## 5. Safety rules this design inherits (non-negotiable)

1. Every rail/bus transition goes through the audited pair
   (`jh_imu::bus_release` / `revive`-shaped power-up). New states
   never reimplement sequencing (DECISIONS #33 rule 1).
2. IMU low-power/interrupt configuration is register work on a
   powered die — but it is NEW electrical-adjacent code, so:
   datasheet check before silicon (rule 2), first runs on the
   sacrificial board (rule 4), soaked before a keeper board sees it
   (hardware-protection §6).
3. STANDBY concentrates risk away from the rail: the more the puck
   lives in STANDBY, the fewer rail edges in its lifetime. Design
   success metric: a season's rail-transition count in the single
   digits.
4. Every auto-transition announces itself on the protocol (`STATE
   standby`, `# deep off in 60s — send any command to cancel`) so
   bench sessions and the watch never see a silent disappearance —
   the "board went dark" ambiguity this week is what a silent state
   machine costs.

## 6. Where this sits in the roadmap

NOT before the water milestone. Correction to an earlier framing:
drop calibration HAPPENED (a6e477d, airtime_offset_s = +0.0257
measured on silicon, on-device and in params.json — the owner went
ham drop-testing before the watch campaign). What has still never
happened is a labeled WATER session. The sequence:

1. Board #3 proven (soak ladder) — in progress. Re-run the drop
   ritual on it once healthy: the 0.0257 offset was measured on
   board #1 and is assumed transferable, not proven per-unit.
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
