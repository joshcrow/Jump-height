> **SUPERSEDED 2026-09-15 (evening).** The owner decided to keep the Hammond 1551WHGY and add a factory CA-USBW1 capped USB-C extension plugged into the Sense's native socket — no carrier PCB, no desoldering. This design (Same Sky UJ31 on a carrier, WOO-body envelope) is kept as a study. Its adversarial review (REVIEW.md, 21 refuted items) was never applied; do not print from it. Decision record: BUILD.md enclosure row; `/Users/joshcrow/Jump-height-race-openai/hardware/enclosure/HANDOFF-2026-09-15.md`.

# woo-fit — a waterproof puck that drops into a WOO cradle

A parametric enclosure for the XIAO nRF52840 Sense puck, sized to the WOO 4.0
sensor body so it can ride in a WOO Sports adhesive cradle. `enclosure.scad`
is the model; this file is the spec sheet and the argument.

**Compiled clean** with OpenSCAD 2026.09.12. All 48 combinations of
{`body`, `cap`, `plug`, `assembly`, `cutaway`, `displacement`} × {`woo22`,
`lp502030`} × {`fdm`, `sla`, `mjf`, `mold`} render with no errors, no warnings
and no failed assertions. `body.stl`, `cap.stl` and `plug.stl` are each a
single closed shell with zero non-manifold edges; `body` has exactly three
through-holes, which are the three it is meant to have (the USB port and the
two cap screws). Previews: `assembly.png`, `cutaway.png`, `body.png`,
`cap.png`, `plug.png`.

Every volume quoted below was **measured off the rendered mesh** (divergence
theorem over the STL) and every wall thickness by ray-casting through it — not
estimated from nominal dimensions. That was not pedantry: it caught four
defects that `manifold: NoError` had passed, including a board slot that left
0.1 mm of wall and a tether groove that sheared the top off the body. A shell
that compiles is not a shell that seals.

---

## 1. The two things to read before anything else

### The cell is the blocker

**The LP502030 we have installed does not fit inside a 22 mm-wide sealed
shell.** The arithmetic, with no judgement in it:

| | mm |
|---|---|
| LP502030 width, max tolerance (20 +0.5) [CELL] | 20.5 |
| side clearance, 0.3 each side | 0.6 |
| wall, 1.2 each side | 2.4 |
| **required outside width** | **23.5** |
| WOO 4.0 body width [WOO] | 22.0 |
| **over** | **1.5** |

There is no wall thickness that recovers this. At 22.0 mm outside, the bore is
19.6 mm and the cell needs 21.1 mm. Thinning the wall to 0.75 mm to close the
gap gives up the sealed boundary, which is the entire point of the part.

Note what is *not* the problem: the board. The XIAO is 17.8 mm wide [XIAO] and
fits a 22 mm shell with 0.1 mm to spare once the support ledges are counted
(§4). It is only the cell.

So the model ships two presets, and you pick one deliberately:

| preset | outside | holds | status |
|---|---|---|---|
| `woo22` (default) | 54.0 × 22.0 × 17.0 | a cell ≤ 19.0 × 42 × 6.0 mm | Fits the WOO envelope. **Needs a cell we do not own.** |
| `lp502030` | 54.0 × 23.5 × 17.0 | the LP502030 installed today | Buildable now. **1.5 mm (6.8%) wider than the WOO body.** |

The buoyancy sums below say `lp502030` actually floats *better* (more hull, same
contents). And the cradle it has to fit is **unmeasured** — see §9 — so 1.5 mm
of overhang may be free or may be fatal. Nobody knows yet, and this document
will not pretend otherwise.

**If you buy a narrower cell, do not upsize the capacity.** The LP502030 is
5.0 g for 250 mAh [CELL] — 0.020 g per mAh. The `woo22` buoyancy margin is
1.73 g, so 87 mAh of extra cell eats all of it. And there is no need: the puck
measured **57.1 h idle on one charge** with DC/DC enabled (docs/STATUS.md,
2026-08-27, a real death not a stopped run). A 150 mAh cell would still give
roughly a day and a half.

### D+ and D- are on no XIAO pad

From the official schematic [SCH], the two castellated rows are:

- **J1**: VBUS, GND, 3V3, P1.15/MOSI, P1.14/MISO, P1.13/SCK, P1.12/RX
- **J2**: P0.02, P0.03, P0.28, P0.29, P0.04, P0.05, P1.11/TX

`USB_D+` and `USB_D-` appear only as nets between the on-board receptacle
`USB1` (pins A6/DP1, A7/DN1, B6/DP2, B7/DN2) and the nRF52840's `D+`/`D-`
pins, through two series components next to the MCU.

This matters because data is not optional here: the Mac app syncs over USB
serial and firmware goes in through the USB bootloader (UF2 mass storage and
Nordic serial DFU). So **any** external connector — options (a), (b) and (c)
alike — has to be tapped off the XIAO's own receptacle pins or those two
series components. 0.5 mm pitch, by hand, on a board we own three of.

Because that cost is identical for every external-connector option, it does
not choose between them. It is simply the price of a sealed USB port on this
board, and it should be budgeted rather than discovered. **Confirm the tap
points with a continuity meter before soldering** — the schematic gives the
nets, not the pad locations.

---

## 2. The connector: what was evaluated, and the numbers

**Chosen: Same Sky UJ31-CH-3-MSMT-TR-67**, IP67, mid-mount SMT, 24 contacts.
[Datasheet](https://www.sameskydevices.com/product/resource/uj31-ch-3-msmt-tr-67.pdf)
(09/12/2024) · DigiKey 9564430 · $3.82 @1, $2.40 @700.

It wins on the only axis that is actually tight — the **end face**. The nose of
this puck is 22 × 17 mm and the UJ31 needs a **3.00 mm tall through-hole**
(datasheet p4, "FRONT/BACK ASSEMBLY") that is about 9.3 mm wide: 5.6% of the
face. Its 12.15 × 9.50 mm footprint sits on a 0.60 mm carrier PCB (p3), and it
is pushed *from behind* through the wall, with an OR-80 silicone ring squeezed
between its flange and a counterbore on the inside face. Crucially it carries
**Dp1/Dn1 and Dp2/Dn2** (p3 pin table), so USB 2.0 works in both plug
orientations.

| option | verdict | the number that decided it |
|---|---|---|
| **(a) UJ31-CH-3-MSMT-TR-67**, rear-sealed board-mount | **chosen** | 3.00 × 9.3 mm hole in a 22 × 17 face. Seal is a factory O-ring, not a hand joint. |
| (a′) GCT USB4135-GF-A | **rejected** | DigiKey: "24 (6+18 Dummy) — **Power Only 6 Pin**". No D+/D-. It is the part everyone reaches for and it cannot carry data. |
| **(b) 16 mm panel-mount IP67 bulkhead** (GT Contact class) | **rejected** | The face is 17.0 mm tall. A 16 mm cutout leaves (17.0−16.0)/2 = **0.50 mm** of wall above and below — and less once the 2.0 mm corner radius is counted. There is no material for a bulkhead nut or a face seal. Dead by arithmetic, not by taste. |
| **(c) potted USB-C female pigtail** | **fallback** | Overmould ≈ 15 × 8 mm: it fits, and it needs **no carrier PCB**, which is its one real advantage. Rejected as primary because the seal becomes a hand-made potting joint whose quality is invisible, and the port cavity floods — seawater sits on live contacts after every session. |
| **(d) the XIAO's own receptacle through the wall** | **does not seal** | See below. |

**Why (d) does not seal.** The XIAO's `USB1` is a standard, unsealed
receptacle. A gasket around the *outside* of its shell does not close the leak
path, because the path runs **through the connector**: water entering the port
passes between the contacts and the insulator and exits the open back onto the
PCB. You can make it seal by potting behind the receptacle with the port cavity
masked — but that is a one-way, hand-made operation on a board we own three of,
and the port still floods, so you have traded a leak for galvanic corrosion on
live contacts. It is the cheapest option and it is the wrong one.

### The sealing scheme, stated plainly

Two seals, and neither depends on the plug being in:

1. **The port** — the UJ31's own OR-80 ring, factory-moulded, compressed
   40–50% (the datasheet's own figure) by a 0.455 mm counterbore in the nose
   wall. The silicone plug is a **contamination cover**, not a seal. This is
   why the acceptance test in §10 runs the bucket **with the plug off**.
2. **The cap** — a 1.5 mm silicone cord in a radial gland on the cap spigot.

**Why a radial bore seal and not a lid with a face gasket.** A face gasket
needs land + groove + land on every side. The narrowest honest rim for a 1.0 mm
cord is about 2.6 mm, which costs 5.2 mm of width. At 22 mm outside that leaves
a 16.8 mm bore — and the XIAO alone is 17.8 mm wide. The board would not fit,
never mind the cell. A radial seal costs only the wall: 2 × 1.2 = 2.4 mm. The
second reason is better than the first: a radial O-ring seals on interference,
not on bolt load, so it does not care whether the screws were done up evenly on
a beach.

**Gland maths** (all derived in the model, so changing the cord re-solves it):

| | value | rule |
|---|---|---|
| cord | 1.5 mm | — |
| squeeze | 20% | mid of the 15–30% band for a static radial seal |
| gland depth, bore wall to groove floor | 1.20 mm | cord × (1 − squeeze) |
| groove width | 1.95 mm | cord × 1.30 |
| groove fill | ~74% | target 60–85% |
| spigot-to-bore radial clearance | 0.20 mm | + the process tolerance |

**No fastener crosses a sealing surface.** The two cap screws enter through the
top wall *aft* of the O-ring groove, so their holes open into the annulus
behind the seal. This is the property BUILD.md praises the Hammond 1551WHGY for
("stainless lid screws seated *outside* the gasket"), kept.

### How the port seal gets its preload

The OR-80 must be squeezed **along** the connector axis, and a screw through the
flat carrier PCB pulls *down*, not forward. So the interior is a compression
column:

```
nose counterbore │ OR-80 │ connector flange │ carrier PCB │ XIAO │
                 silicone preload pad │ cap rib │ cap plate │ body tail face
```

The cap plate bottoms on the body's tail face and the column is built 0.3 mm
long; a 1.0 mm silicone pad takes up the interference and sets a repeatable
squeeze without crushing the seal ring. The cap screws only stop the cap backing
out — they carry no seal load.

---

## 3. Stack-up, z from the outside of the floor

| # | row | mm | cumulative | source |
|---|---|---|---|---|
| 1 | floor wall | 1.20 | 1.20 | `wall`, this model — thinnest that moulds in PC/PA and prints watertight |
| 2 | cell pocket (5.3 max + 0.4 swell allowance) | 5.70 | 6.90 | [CELL] thickness 5.0 **+0.3**/−0.3 |
| 3 | pigtail solder clearance | 2.10 | 9.00 | the cell is wired to the board's **underside** BAT pads (archived `docs/sense.md` §3.4); a hand joint on AWG28 stands 1–1.5 mm proud |
| 4 | PCB | 1.20 | 10.20 | **UNMEASURED** — Seeed publishes no thickness |
| 5 | tallest top component above the PCB | 2.30 | 12.50 | **UNMEASURED** — from a 3.5 mm overall figure (secondary source) minus row 4. It is the XIAO's *own* USB-C receptacle, which this design does not use |
| 6 | free air — flotation + silica packet | 3.30 | 15.80 | the balance |
| 7 | top wall | 1.20 | **17.00** | `wall` |

**Total 17.00 mm = the WOO body height exactly** [WOO].

Row 5 is worth a second look: removing the XIAO's own receptacle would reclaim
~2.3 mm of height. The model has a parameter for it. It is not the default,
because it is irreversible on a board we own three of, and because height is not
the binding dimension here — width is.

## 4. Plan view, x aft from the nose face

| x, mm | what |
|---|---|
| 0.00 – 1.60 | nose wall, with the port through-hole and the OR-80 counterbore |
| 1.60 – 13.60 | connector carrier PCB, 12.0 long × 14.5 wide, on ledges |
| 13.60 – 34.60 | the XIAO, 21.0 × 17.8 [XIAO] |
| 12.00 – 44.00 | the cell, underneath, 32.0 max long [CELL] |
| 44.00 – 45.40 | cell aft fence |
| **44.00** | **tether groove**, 2.2 wide × 0.5 deep, full circumference |
| 46.00 – 52.00 | cap spigot engagement |
| 47.00 – 48.95 | O-ring groove |
| 50.50 | 2 × cap retention screws |
| 52.00 – 54.00 | cap plate |

**Width budget** (`woo22`): bore 19.60 − 2 × 0.65 support ledge = **18.30** of
clear span, for a 17.80 board + 0.40 clearance = **18.20**. 0.10 mm spare. It
is tight and it is checked by a compile-time assertion.

**Board retention.** The XIAO has no mounting holes, so it sits on ledges that
project inward from the side walls and is held down by four snap tabs, fore and
aft on each side; the cap rib pushes it forward against the carrier stack. That
is all six degrees of freedom, rigidly — which is the requirement, because
BUILD.md's "realistic failure order" and the IMU both want the board coupled to
the shell and **not floating on foam**. The only compliant part in the whole
stack is the pad under the *cell*.

Note what the ledges are *not*: a slot cut into the wall. The side wall is
1.2 mm, and a slot deep enough to hold a PCB would leave ~0.1 mm — a leak path
straight through the sealed boundary. The first draft did exactly that.

**Antenna and IMU.** Seeed publishes neither position; the schematic shows a
discrete chip antenna `ANT1` but no board location, and the wiki's back-side
pinout does not place the LSM6DS3TR-C. Rather than guess, the design removes the
need to know: **the shell is all-plastic with no metal anywhere**, so BLE passes
regardless of where the antenna sits, and the board is clamped at **all four
edges**, so the IMU is rigid wherever it is. Confirm both by eye on the real
board before committing to a mould.

## 5. Orientation — the port end is the NOSE

A moulded arrow and the word `NOSE` are recessed 0.3 mm into the top face,
pointing at the port. Three reasons, in order of how much they matter:

1. **The IMU's axes are fixed to the shell.** An ambiguous mounting orientation
   is a data bug, not a cosmetic one.
2. **Put the factory seal where the water hits.** The port's seal is made in a
   factory; the cap's seal is made by hand, on a beach, by whoever last opened
   it. Water flows nose-to-tail over the deck, so the nose takes the direct hit
   and the hand-made seal sits in the lee.
3. The tether runs **aft** to a footstrap insert, so the tether groove belongs
   at the tail — port and tether want opposite ends.

**The tether is a groove, not an eye.** A moulded eye at this scale is a
1.2 × 1.5 mm plastic bar and will snap before the leash does. A full
circumferential groove takes a Dyneema loop or a cable tie and puts the load
into the shell as hoop stress. It also costs zero height, which a boss would
not — and height is exactly what the cradle's O-rings press on.

## 6. Buoyancy

DECISION #9: *"Must float + be tethered."* So this is a calculation that has to
be done, not a hope.

**Measured volumes** (divergence theorem over the rendered meshes):

| | `woo22` | `lp502030` |
|---|---|---|
| body material | 5.148 cm³ | 5.369 cm³ |
| cap material | 1.643 cm³ | 1.729 cm³ |
| **shell total** | **6.791 cm³** | **7.098 cm³** |
| **displaced volume** | **19.910 cm³** | **21.283 cm³** |

**Contents, 11.28 g:**

| item | g | basis |
|---|---|---|
| cell + PCM + leads | 5.00 | [CELL] "Weight ▸ appr. 5.0g" |
| XIAO nRF52840 Sense | 3.00 | distributor **gross** weight incl. packaging — an upper bound. Seeed publishes no board weight |
| carrier PCB | 0.19 | 12 × 14.5 × 0.6 FR4 @ 1.85 g/cm³ |
| UJ31 connector | 1.00 | **estimate** — the datasheet gives no mass |
| 2 × M1.6×6 A4 screws | 0.25 | estimate |
| O-ring cord, 67 mm of 1.5 mm | 0.14 | π/4 × 1.5² × 67 @ 1.2 g/cm³ |
| 4 × 40 mm AWG30 wire | 0.20 | estimate |
| pads + RTV | 0.40 | estimate |
| silicone plug + lanyard | 0.60 | estimate |
| silica gel packet | 0.50 | BUILD.md |

**Result, in fresh water (ρ 0.998 — the bucket test, and the harder case):**

| material | ρ g/cm³ | `woo22` margin | `lp502030` margin |
|---|---|---|---|
| MJF PA12 | 1.01 | **+1.73 g** (8.7%) | **+2.79 g** (13.1%) |
| moulded PP | 0.905 | +2.45 g (12.3%) | +3.54 g (16.7%) |
| SLA resin | 1.18 | +0.58 g (2.9%) | +1.59 g (7.5%) |
| moulded PC | 1.20 | +0.44 g (2.2%) | +1.44 g (6.8%) |

In seawater (ρ 1.025) add 0.54 g (`woo22`) or 0.58 g (`lp502030`).

**Read this honestly.** It floats in every material considered — but 2.45 g of
the 11.28 g contents figure is *estimated*, not weighed, and the plausible
error on that is comfortably ±1 g. **For SLA and PC that is larger than the
margin.** So: PA12 and PP float with margin outside the uncertainty; SLA and PC
are inside it and **must not be called a pass until the bucket test says so**.
Per house rule: no verdict without a measurement.

Two levers are already spent in the model. The cap is cored out as a pocket
opening **forward into the sealed air space**, worth ~0.7 g. That direction is
load-bearing: a version that cored the cap from the *tail* face was measured and
gave back exactly as much displacement as it saved in mass — net gain for PA12,
+0.01 g. Pointless. A closed internal void saves the same mass but cannot be
moulded and traps powder in MJF.

The remaining lever is the cell, and it points the wrong way: **0.020 g per
mAh**. Do not upsize it.

## 7. Print recommendations

**What FDM cannot do.** Not a watertight part at 1.2 mm wall, whatever the
slicer claims. Layer adhesion on a thin vertical wall is the leak path, and the
part is pressurised from outside by depth and by impact. FDM is for **fit
checks only** — printing the body at `process="fdm"` to try the cradle, the
board and the cell is genuinely useful and costs an hour. Do not bucket-test it
and conclude anything.

**SLA** gives the best dimensional fidelity for the O-ring gland and is
watertight as printed. It is also brittle: a 1.2 mm wall in standard resin will
crack on a hard landing. Use a tough/ABS-like resin, and note it sits in the
buoyancy band that the estimate error can swallow.

**MJF / SLS PA12 — the recommendation for a functional prototype.** Tough, not
brittle, dimensionally honest at these wall thicknesses, and the lightest of the
realistic options. **It is POROUS as printed and will wick water.** An unsealed
MJF part is not a waterproof part. Post-process: bead blast, then seal by
cyanoacrylate impregnation, an epoxy wash, or vapour smoothing. Budget
0.2–0.4 g of sealer against the margin in §6, and re-weigh before the bucket
test.

For every process: `process=` sets the spigot clearance only (fdm 0.30, sla
0.10, mjf 0.20, mold 0.05). It is deliberately applied to the **spigot, not the
bore** — enlarging the bore by the tolerance eats the wall asymmetrically, and
at `fdm` it thinned the top wall to 0.9 mm, at which point the tether groove cut
straight through the sealed boundary. Shrink the plug, not the hole.

## 8. Injection-moulding notes

- **Draft**: `draft = 0.75°` per side at `process="mold"`. The body is a ~50 mm
  straight-pull core; less than that and it will drag.
- **Uniform wall**: 1.2 mm throughout, 1.6 mm at the nose. The ledges, tabs,
  fence and rib are all ≤ 0.9 mm proud, so no sink marks over them.
- **Parting line**: around the body's outer section, clear of both the bore and
  the O-ring gland. Nothing that seals should sit on a parting line.
- **Cores**: body — one core from the tail. Cap — one core from the front for
  the lightening pocket, forked around the board rib. The pocket wall is
  `cap_core_t + groove_cut` thick so the section never widens as the core
  withdraws (no undercut) and the wall under the O-ring groove is still 1.2 mm.
- **Gasket groove tolerance**: the gland depth sets the squeeze directly. Hold
  ±0.05 mm on it; ±0.10 on everything else is fine.
- **Material**: PC for toughness and clarity of moulding, PA12 for toughness and
  the lower density. Both need a **UV stabiliser** — this part lives on a deck
  in the sun. PP is the flotation-optimal choice (ρ 0.905) and is excellent in
  salt and UV, but creeps under the sustained O-ring load and bonds poorly;
  treat it as a considered option, not a default.
- **Gate**: on the tail end face of the body, away from the seal and the port.

## 9. What is unmeasured

House rule: a reading that did not happen is a finding.

1. **The WOO cradle (65240-7203) — all of it.** Ordered 2026-09-15, never in
   hand. Inside width, lip height, O-ring band positions and span, floor
   curvature: every one is a guess. The `cradle_*` parameters exist so the guess
   is visible and replaceable, and nothing in the model depends on them yet.
   **Check first that the tether groove at x = 44 does not land under an O-ring
   band.** (BUILD.md "WOO mount"; docs/STATUS.md "Nick's board / mount".)
2. **The real LP502030 as built** — calipered, including the PCM tab and the
   heat-shrink. This model uses the datasheet maxima (5.3 × 20.5 × 32.0).
3. **The XIAO's PCB thickness and overall height**, and the overall height
   *with the pigtail soldered to the underside BAT pads*. Seeed publishes
   neither. Rows 4 and 5 of the stack-up are parameters for this reason.
4. **The D+/D- tap points** on the XIAO. The schematic gives the nets, not the
   pads. Meter them.
5. **The UJ31's mass.** The datasheet gives none, and it is the largest single
   estimate in the buoyancy sum.
6. **Whether the UJ31's IP67 is rated mated, unmated, or both.** The datasheet
   says only "IP level — IP67" and DigiKey says "IP67 — Dust Tight,
   Waterproof". The p4 assembly drawing shows the seal ring at the
   connector-to-panel joint, which is the joint this design relies on; it says
   nothing about the path front-to-back through the connector. **The whole
   "sealed with the plug off" claim rests on this**, which is exactly why the
   acceptance test in §10 tests it directly rather than believing the label.
7. **The panel counterbore height.** The datasheet's p4 panel section
   dimensions it as (3.65), but the OR-80 it has to contain is 4.25 tall. Those
   do not reconcile. This model sizes the counterbore from the **ring**, since
   that is the part that must fit, and flags it. Confirm against the vendor STEP
   model before cutting a mould; a printed prototype answers it in five minutes.
8. **The antenna and IMU positions.** Designed around, not resolved — see §4.

## 10. Assembly order

1. Solder the cell pigtail to the XIAO's underside BAT pads. **Meter the cell
   leads first** — JST polarity is not standardised and a reversed cell kills
   the charger IC (`docs/solder.md` §1; CLAUDE.md). Glue-dot and strain-relieve
   the joint: BUILD.md puts it first on the failure list.
2. Solder four wires to the carrier PCB, then to the XIAO's USB tap points.
   Continuity-check D+/D- before and after.
3. Seat the OR-80 ring in the nose counterbore. Push the connector+carrier in
   from behind until the flange bottoms. Locate the carrier on its ledges.
4. Press the cell into its pocket on the EVA pad; route the pigtail up the side,
   not under the board.
5. Snap the XIAO down onto its ledges, past the four tabs.
6. Drop in the silica packet, aft of the board.
7. Fit the cord O-ring to the cap spigot. Stick the preload pad to the cap rib.
8. Slide the cap home until the plate bottoms on the body's tail face; fit the
   two M1.6 × 6 A4 screws. Snug, not tight — they carry no seal load.
9. Fit the tether in its groove. Fit the port plug.

## 11. Acceptance tests

In order, per BUILD.md's Day 3+ sequence, with one addition:

1. **Bucket, empty, plug ON.** Sealed shell, dry tissue inside, 10 minutes
   submerged. Tissue dry = sealed. BUILD.md: *"Repeat this before every
   session."*
2. **Bucket, empty, plug OFF.** *The addition.* If the connector is genuinely
   rear-sealed, the shell is watertight with the port open to the sea; the plug
   is only keeping sand off the contacts. If the tissue comes out wet, item 6 of
   §9 has been answered the hard way and the plug has just become
   safety-critical — say so in STATUS.md and change the rinse discipline.
3. **Bucket, loaded.** With the electronics in. Then confirm it **floats**, and
   record the freeboard: that is the real buoyancy measurement, and it retires
   the estimate in §6 either way.
4. **BLE through the closed shell.** Confirm the phone sees
   `JumpHeight-XXXX` — pinned by name, always (CLAUDE.md §1: three boards can
   advertise).
5. **USB through the sealed port.** `./tools/jump sync` over the cable, and a
   UF2 or DFU flash. This is the test that says the D+/D- tap actually works.
6. **Cradle fit.** Only possible once the mount arrives. Straight-edge the
   intended deck spot first — F-One calls the Rocket Wing-S front deck *domed*,
   and a rigid 54 mm plate on a dome contacts at the crown only (BUILD.md).
7. **Salt rinse discipline.** Fresh water over the port, plug off, then dry
   before the plug goes back on. A plug fitted over a wet port traps salt
   against the contacts, which is worse than leaving it open.

---

### Sources

- **[WOO]** WOO 4.0 sensor body "5.4 x 2.2 x 1.7 cm" — [easy-surfshop WOO 4.0 kit page](https://easy-surfshop.com/do/item/WOO-65240-7200/Kiteboard-sensor-WOO-4.0--charger-and-mount), via BUILD.md's "WOO mount" row.
- **[XIAO]** "Ultra Small Size: 21 x 17.8mm" — [Seeed wiki, XIAO BLE](https://wiki.seeedstudio.com/XIAO_BLE/). The same figure is in the archived `docs/sense.md`, but in its **§1 board table**, not §2 (§2 is "What carries over unchanged").
- **[SCH]** [Seeed Studio XIAO nRF52840 v1.1 schematic](https://files.seeedstudio.com/wiki/XIAO-BLE/Seeed-Studio-XIAO-nRF52840-Sense-v1.1.pdf) — J1/J2 pad nets, USB1, ANT1.
- **[CELL]** [LiPol Battery LP502030 + PCM datasheet](https://www.lipolbattery.com/LiPo-Battery-Datahseet/LiPo_Battery_LP502030.pdf), Dwg. FD_2120_10, 20.12.2023 — 5.0 +0.3/−0.3 × 20 ±0.5 × 31 ±1.0 mm, "appr. 5.0g", 250 mAh min. This **confirms** the estimate BUILD.md was carrying ("treat as 5.2 × 20.5 × 32 max until calipered") and turns it into a specified figure.
- **[UJ31]** [Same Sky UJ31-CH-3-MSMT-TR-67 datasheet](https://www.sameskydevices.com/product/resource/uj31-ch-3-msmt-tr-67.pdf), 09/12/2024.
- GCT USB4135-GF-A contact count — [DigiKey 16036137](https://www.digikey.com/en/products/detail/gct/USB4135-GF-A/16036137).
- Endurance 57.1 h idle, DC/DC enabled — `docs/STATUS.md`, measured 2026-08-27.
- Float + tether requirement — `DECISIONS.md` #9. Failure order, potting rule, bucket sequence, silica packet — `BUILD.md`.
