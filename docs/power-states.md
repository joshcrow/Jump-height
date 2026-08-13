# Power architecture — states, transitions, and the end-user story

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
