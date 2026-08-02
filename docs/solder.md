# Soldering & battery runbook — the day the iron arrives

Two soldering jobs exist in this whole project, and one of them has a lithium
cell on the other end. This is the procedure for both, plus the multimeter
checks that bracket them.

| Job | Board | When | Risk if wrong |
|---|---|---|---|
| **A — JST pigtail → BAT pads** | XIAO nRF52840 Sense (v2) | S2, [sense.md §3.4](sense.md) | Reversed polarity kills the BQ25101 charger IC; a solder bridge shorts a 500 mAh LiPo |
| **B — 8-pin header → GY-521** | MPU-6050 boards (v1 spares) | Before the wizard's flash step, [BUILD.md](../BUILD.md) | A cold joint reads as a dead sensor; `selftest` catches it |

Do **B first even if you only care about A** — the header pins are forgiving
practice, and the Sense's BAT pads are the smallest, least reworkable joints in
the project. They should not be your first joints of the day.

---

## 0. Bench setup (once)

- **Tip temperature:** 320–340 °C for leaded solder (Sn63Pb37 / Sn60Pb40),
  350–370 °C for lead-free. Hotter is not faster — it just lifts pads.
- **Tin the tip** as soon as it's hot, and re-tin before every joint. A dry
  brown tip transfers almost no heat, which is what makes people dwell too
  long, which is what lifts pads.
- **Flux:** a dab on the pad *and* the wire before you join them. Rosin /
  no-clean both fine. **Clean the residue off with isopropyl afterwards** —
  near a battery pad, flux residue is mildly conductive and hygroscopic, and
  this puck's whole future is inside a sealed humid capsule.
- **Ventilate.** Flux smoke is the fumes you're smelling, not the solder.
- Keep the LiPo **off the bench** while the iron is hot. It goes on when §2
  says it goes on.

## 1. The multimeter comes first

Three measurements, in this order. The whole point is that **JST wire colours
are not standardized** — the plug fits either way, and the board cannot tell
you it's about to die.

| # | What | Meter setting | How | Good result |
|---|---|---|---|---|
| 1 | Battery voltage | DC V (20 V range, or auto) | Probe tips into the battery's JST housing — red probe to the red-wire contact, black to the black | **3.6–4.0 V** (shipping storage charge). A positive number also confirms the cell's own colours are honest. |
| 2 | Which pigtail wire is **+** | DC V | Plug the battery into the pigtail. Hold the two bare pigtail ends **well apart**. Red probe on one end, black on the other. | A **positive** reading ⇒ the wire under the red probe is **+**. Negative ⇒ it's the other one. Mark it *immediately* with tape, then unplug the battery. |
| 3 | No bridge (after soldering, §2.7) | Continuity / Ω | Probes on BAT+ and BAT− pads, battery **unplugged** | **No beep.** A dead short (≈0 Ω, continuous beep) means a bridge — rework before anything else touches this board. |

> ⚠️ **The two bare pigtail ends must never touch each other.** A shorted
> 500 mAh LiPo is a fire, not a spark, and it gets there in seconds. Measure,
> label, unplug — don't leave a live pigtail lying on the bench.

Out-of-range readings on #1: **> 4.25 V** or **< 3.0 V** means stop and don't
use that cell. Same for any cell that is puffed, warm, dented, or has been
dropped hard.

## 2. Job A — JST pigtail → XIAO Sense BAT pads

**Preconditions:** board **not** plugged into USB. Battery **not** plugged
into the pigtail. Pigtail polarity known and labelled from §1.

1. **Find the pads.** Underside of the Sense, marked `BAT+` and `BAT−`
   ([sense.md §1](sense.md): the board ships with no battery connector at
   all). They sit close together — that adjacency is exactly why step 7
   exists.
2. **Trim the pigtail** to the length your housing plan wants (40–60 mm is
   plenty) before stripping. Strip ~2 mm.
3. **Tin the wire ends** — flux, then a thin coat of solder, twisted strands
   captured. No stray whiskers.
4. **Tin the pads** — flux, then one small dot of solder on each pad. Iron
   contact of 1–2 s. If the dot won't wet, add flux; do not add heat.
5. **Join `+` first, then `−`:** hold the tinned wire on the tinned pad,
   touch the iron to both for ~1 s until the two dots merge, remove the iron,
   hold still for 2 s while it freezes. A good joint is shiny and concave.
6. **Inspect** under your phone camera at max zoom — cheaper than a loupe and
   already in your pocket. Look for: a bridge between the pads, a dull grey
   blobby joint (cold — reflow it with flux), or solder that's sitting *on*
   the pad like a bead rather than wetting into it.
7. **Meter for a bridge** — §1 measurement #3. This is not optional; it is the
   one check standing between a slip of the iron and a shorted cell.
8. **Strain relief.** Tape or a dot of hot glue anchoring the pigtail to the
   board, so a tug lands on the anchor and not on the joints. **Lifted pads
   are the number one way these boards die**, and a lifted BAT pad is a
   board-level repair, not a resolder.
9. **Now plug the battery in.** Watch and feel for the first 30 seconds: no
   warmth, no smell, no smoke. Then attach USB and confirm the red charge LED
   (P0.17) behaves — [sense.md §7 item 7](sense.md) wants that behaviour
   recorded anyway.
10. **First power-on proper** is a different document: work
    [firmware/SENSE_FIRST_BOOT.md](../firmware/SENSE_FIRST_BOOT.md) top to
    bottom, and the S0 milestone in [sense.md §6](sense.md).

**While you're routing wire:** keep the cell off the antenna end of the board
([sense.md §3.11](sense.md) — no metal in the keep-out). Decide that now,
while the pigtail length is still a choice.

**Charge rate, for expectation-setting:** the BQ25101 defaults to 50 mA, so a
500 mAh cell is ~11 h from flat. Firmware can drive P0.13 low for 100 mA
(~5–6 h), but that's an S2 code change, not a soldering one. Plan on charging
overnight.

## 3. Job B — 8-pin header → GY-521 MPU-6050

The Ximimark boards ship with the header strip loose. Four of the eight pins
are all this project ever uses ([BUILD.md](../BUILD.md) has the pin table),
but solder all eight — a partially-populated header rocks under the iron.

1. Push the header's **long pins** into a breadboard, short pins up. The
   breadboard is the jig that holds everything square.
2. Sit the GY-521 on the short pins.
3. **Solder one corner pin only.** Then look from the side: is the board flat
   and square? If not, reheat that single joint and nudge it true — this is
   the only moment that correction is free.
4. Solder the remaining seven. Flux, iron on pad+pin ~2 s, feed solder into
   the joint (not onto the iron), remove, hold still.
5. Clean the flux residue off with isopropyl.

Then wire it per BUILD.md's table — **VCC → 3V3**, not the pin marked VCC
(that one carries ~4.7 V). If a board turns out to be a genuine dud, the
self-test says so; you have four for exactly this reason.

## 4. Before power, every time

- [ ] Polarity confirmed by meter, not by wire colour
- [ ] No bridge between BAT+ and BAT− (continuity mode, battery unplugged)
- [ ] No stray solder whiskers or clipped-lead debris on the board
- [ ] Flux residue cleaned with isopropyl
- [ ] Strain relief on the pigtail
- [ ] Battery not puffed, warm, or dented; 3.6–4.0 V at rest

## 5. When it goes wrong

| Symptom | Fix |
|---|---|
| **Bridge between pads** | Add flux, then drag a clean hot tip across the gap, or wick it with desoldering braid. Don't try to "flick" it off. |
| **Cold joint** (dull, grey, blobby, or the wire moves) | Add flux, reflow with a touch of *fresh* solder — fresh solder carries the flux that makes it wet. |
| **Solder won't wet the pad** | Flux, and clean the tip. Not more heat, and not more solder. |
| **Lifted BAT pad** | Board-level repair (a wire to the nearest point on that net) — prevention via §2.8 is the real answer. |
| **Board hangs before ever printing `READY`** | Suspect the QSPI flash joints, not your firmware — see [SENSE_FIRST_BOOT.md item 18](../firmware/SENSE_FIRST_BOOT.md), which documents this exact silent-hang signature. |
| **Cell puffed / punctured / dropped hard** | Retire it. Don't charge it, don't "just try it once". |

---

*The FireBeetle rig's own battery-polarity warning lives in
[BUILD.md's safety section](../BUILD.md) — same hazard, different connector:
a LiPo plug can be wired backwards versus the board's connector even though it
fits. Meter first there too.*
