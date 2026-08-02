# The puck housing — sizing the v2 enclosure *(S4 working notes)*

Written 2026-08, before any enclosure was bought, to stop the obvious
mistake: shopping for a box before knowing what actually sets its size.
[sense.md §6](sense.md)'s S4 milestone names "Hammond 1551W-class"; this is
the arithmetic behind that, and the finding that the named part's smallest
size doesn't fit the cell we own.

**Do not buy an enclosure before S0/S1.** [BUILD.md](../BUILD.md)'s standing
line — the slick housing comes later, informed by what the rig teaches —
applies double here: the S4 antenna range test can change the required
clearance, and that changes the box.

## 1. The size driver is the cell, not the board

| Part | Footprint | Thickness |
|---|---|---|
| XIAO nRF52840 Sense | 21 × 17.8 mm | ~3.5 mm |
| PKCELL LP503035 (500 mAh, owned) | **35 × 30 mm** | 5 mm |

The battery is ~3× the board's footprint. Every dimension below is set by
the cell.

## 2. The layout is forced by the antenna

[sense.md §3.11](sense.md): the antenna hangs off one end of the board, no
metal near it — and a LiPo pouch **is** metal. So the compact
board-flat-on-battery stack is out. The board offsets so its antenna end
overhangs into free air (~6 mm), and the mated JST pair (~20 mm plus bend
radius) needs a corner (~10 mm).

| | 500 mAh (owned) | 250 mAh (recommended) |
|---|---|---|
| Packed assembly | ~50 × 30 × 11 mm | ~42 × 22 × 11 mm |
| **Internal needed** (+2 mm foam all round) | **≥ 54 × 34 × 15 mm** | **≥ 46 × 26 × 15 mm** |

## 3. The float rule

A big air-filled case (v1's Pelican-class capsule) floats trivially. A
tightly-packed small one may not: shrinking the box cuts displacement while
the payload mass stays constant. **Smallness and flotation are in direct
tension**, and BUILD.md requires float *and* tether, not either/or.

> **Floats if external volume (cm³) > total mass (g).**
> Seawater is 1.025 g/cm³, so "cm³ beats grams" is the break-even, with a
> few percent in hand.

Run it on every candidate before buying — case + cell + board + screws.
Margin thins fast below ~40 cm³. Closed-cell foam buys buoyancy for almost
no mass, but keep it out of the load path: the assembly must still move as
one rigid lump (BUILD.md), because anything that shifts internally is read
as signal.

## 4. No charge port, no Qi coil — the decision that unlocks smallness

BUILD.md contemplated a Qi receiver for v1. Its coil is ~40 mm across —
larger than this entire puck. **Rejected for v2.** The puck is opened to
charge over USB-C, which is only tolerable because [sense.md §5](sense.md)
projects months of standby once System OFF sleep lands: opening it is
seasonal, not per-session. Cost: a gasket cycle each time — silicone grease
on the seal, and the bucket test before every session (already doctrine).

## 5. The Hammond finding

The 1551W is the right *class* — genuine IP68, preformed silicone gasket,
polycarbonate (RF-transparent, which an aluminium capsule is not), moulded
PCB standoffs, flat bottom for a GoPro adhesive base. But **the smallest
1551W is 60 × 35 × 22 mm external**, leaving ~29 mm internal width after
~2.5 mm walls — and the 500 mAh cell is 30 mm wide. **It does not fit, in
either orientation.** With the owned cell the series jumps to ~80 × 40,
mostly full of battery.

## 6. Therefore: shrink the cell

[sense.md §5](sense.md) estimates 3–8 mA while recording. A session is 2–3
hours.

| Cell | Size | Recording time | Puck external |
|---|---|---|---|
| 500 mAh (owned) | 35 × 30 × 5 | 60–160 h | ~80 × 40 × 22 |
| **250 mAh** (502030) | **30 × 20 × 5** | **31–83 h** | **~55 × 32 × 20** |
| 150 mAh | 30 × 12 × 4 | 19–50 h | ~45 × 28 × 18 |

Even 150 mAh is a dozen sessions per charge *before* sleep mode exists. The
500 mAh cell buys runtime nobody will notice and costs volume on a thing
glued to a board. **Plan: 500 mAh for bench + first water sessions; a 250
mAh cell for the puck proper.** Verify a protection PCB is present — small
cells often omit it, and the low-voltage System OFF backstop
([sense.md §3.4](sense.md)) isn't built yet.

## 7. Rejected, with reasons

| Option | Why not |
|---|---|
| Soft dry-pouch / roll-top | Fails the rigid-lump rule — a pouch is all shift, and shift reads as signal |
| Aluminium screw-top capsule | A Faraday cage around the only radio |
| Anything IP65/IP66 | Splash, not immersion. IP67 minimum. "IP68" alone is meaningless — the standard makes the maker state depth *and* duration; read the spec, not the badge |
| Potting solid | Smallest of all, and DFU-over-BLE makes it nearly viable — but epoxy ~1.1 g/cm³ **sinks** by §3's rule, and it's a one-way door on the cell. Defer |
| Screw-top O-ring capsule | Not rejected — seals better than a 4-screw flat gasket under repeated opening (relevant given §4). Trade: round profile mounts flat poorly and wastes volume around a rectangular cell |

## 8. Acceptance, before it goes near the ocean

Same gauntlet v1 passed, in order — bucket **empty** with a paper towel
inside (the towel tells on leaks) → bucket **loaded** → phone still sees
`JumpHeight` through the closed case → confirm it floats loaded → antenna
range check puck-on-board to wrist ([sense.md §3.11](sense.md)). Then water.

Don't overtighten the lid screws: a warped lid leaks, and even gasket
compression beats hard compression.

---

Sizes verified 2026-08 against
[Hammond's 1551W page](https://www.hammfg.com/electronics/small-case/plastic/1551w)
and the [series announcement](https://www.dlpc.com.au/blogs/news/if-you-are-looking-for-a-miniature-ip68-sealed-enclosure-look-no-further-the-latest-extension-to-the-1551-family-by-hammond-manufacturing-the-1551w-fits-the-bill-perfectly)
(five sizes, 60 × 35 × 22 mm to 100 × 59 × 25 mm).
