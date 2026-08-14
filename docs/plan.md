# The plan — sequencing, priority, and what we are deliberately not doing

Written 2026-08-14 by the engineering side, for the owner, after a
four-day hardware incident produced a large backlog and not much clarity.
This document exists to make the next two weeks obvious.

## Where we actually are

**The hardware crisis is over and it was never hardware.** Both boards
pass every self-test row. The root cause (GPIO drive strength) is fixed,
documented, and can't recur — it's encoded in DECISIONS #37-39 and
`xiao-hardware-truth.md`. That chapter is closed.

**But the product's central question is still unanswered.** Three weeks
in, we have never measured a real jump on water. Everything on the
backlog — BLE reliability, power states, sealed enclosures, NFC — is
polish on a hypothesis nobody has tested yet. Roadmap Phase 2 has said
this in bold the whole time.

## The filter that sequences everything

> **Does this block getting trustworthy data from one water session?**

Run the backlog through it and almost nothing survives, because of one
fact worth stating plainly:

**The BLE bug cannot corrupt recorded data.** `main.cpp:1018` calls
`logJump(ev)` — the flash write — independently of the `emitf()` that
feeds the radio. The puck records to storage no matter what the link
does; the watch is a *view*, and the session is downloaded over USB
afterward. So the corruption bug threatens the live display, not the
science.

That single fact moves BLE work off the critical path.

## The sequence

### P0 — Get the water data (this week)

The only work item that can invalidate everything else.

| Step | Who | Time |
|---|---|---|
| `./tools/jump desktest` on the OG board — 3 untethered tosses | **owner** | 10 min |
| Charge, capsule, mount, pair the WATCH ONLY | owner | — |
| Water session, filmed for ground truth | owner | one session |
| Download + label + `./tools/jump eval` | engineering | same day |

**Why the desk test is a hard gate:** it is the only thing that proves a
jump actually *survives to storage* on this board. The self-test proves
the filesystem mounts; `fakejump` deliberately skips storage. If
persistence is broken, the session records nothing and we learn nothing.
Ten minutes now prevents a wasted trip.

### P1 — The 90-minute batch, in ONE flash, before the water

Small, safe, independently verifiable. Batched deliberately: seven-flash
evenings are where mistakes compound (playbook rule 5).

1. **`system_off()` stops cutting the rail** — removes the one shipped
   contradiction of the rail-static rule and the last `pinMode()` on a
   power pin. ~15 min.
2. **`Bluefruit.autoConnLed(false)`** — the blue LED currently blinks at
   50 % duty forever. Wasteful and absurd in a sealed puck. ~5 min.
3. **Slow the advertising interval** — 152 ms is a session-grade rate for
   a device that sits idle. ~10 min.

Then **stop touching the board** until after the water session.

### P2 — Make the live experience honest (after data)

Only worth doing once we know the numbers themselves are right.

1. Honor the transmit return + `tx_drops` counter — kills the silent
   byte-loss path (`ble-dependability.md` §3 layer 1).
2. Watch-side corruption gate — reject implausible lines instead of
   rendering them.
3. Battery + armed state in the advertisement — the "is it on?" fix.
4. Per-line checksum + sequence — makes loss *detectable*, not just rare.

### P3 — Power UX (the big one, deliberately last)

`power-states.md` §5a is honest about the size: there is no low-power
idle to build on, the main loop never sleeps, the I2C peripheral is left
enabled, and the watchdog caps sleep at 3.4 s. This is a main-loop
restructure, not a feature.

**It is also not urgent.** The device runs ~60 hours on a charge today.
A session is two hours. Standby matters for the *sealed product*, not
for the next month of testing.

### P4 — The sealed product

Charging access (gasketed USB now, inductive dock later), enclosure,
and the v2 hardware conversation. Everything here depends on decisions
that real water data will inform.

## What we are explicitly NOT doing, and why

| Deferred | Why |
|---|---|
| Two-central BLE support | Product is one central (the watch). Fix the *cause*, keep the capability, don't chase the feature. |
| Instinct 3 Solar on-watch work | It's your brother's watch. Simulator tests pass; wait for hardware. |
| NFC, solar, primary cells | v2 hardware conversations. Researched and documented; no action until the product shape is settled. |
| Standby tier | P3. Nothing before the water session needs it. |
| Bonding / BLE security | Adds a pairing ritual to a device with no display, for a threat model that doesn't justify it. Revisit only if `dfu` stays open. |

## The one thing that could still bite us

`dfu` is an unauthenticated BLE command — anyone in range can reboot the
puck into its bootloader, and in DFU mode it has no USB at all. That's a
bench annoyance today and a real problem the day this leaves the bench.
Gate it in P2.

## Decisions needed from the owner

1. **Water session date** — everything sequences off it.
2. **P1 batch: yes or skip?** It's 90 minutes and one flash. My
   recommendation: do it, because it removes the last shipped rule
   violation before the board goes somewhere wet.
3. **After the water session, which comes first: P2 (trustworthy live
   display) or P3 (power)?** My recommendation: P2 — it's smaller, and a
   wrong number on the wrist erodes trust in the product faster than a
   flat battery does.

## The standing rule this project earned the hard way

No verdicts without measurements. No "dead hardware" without a
`pincensus`. No new power code without the bench board first. Those
aren't ceremony — each one was bought with a day we don't get back.
