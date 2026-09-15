# REVIEW — woo-fit enclosure, adversarial re-derivation

Reviewer: skeptical mechanical/electrical pass, 2026-09-15. Nothing in the
design was edited. Default verdict is REFUTED where a number could not be
reproduced.

**Method.** Every STL was recompiled locally (OpenSCAD 2026.09.12, all 48
combinations), every mesh re-measured (divergence theorem for volume, plane
slicing + ray casting for clearances), the UJ31 datasheet was fetched and its
drawing pages rendered at 600–700 dpi and read, the Seeed wiki and the WOO
retailer page were fetched, and every in-repo citation was grepped against the
file it names.

Scratch artifacts: `/private/tmp/claude-501/-Users-joshcrow-Jump-height/90f7006d-b107-49b1-bb31-9a124e3ce6d0/scratchpad/`
(`out/compile.log`, 48 STLs, `mesh.py`, `slice.py`, `uj31.txt`, rendered
datasheet crops).

**Summary: 12 CONFIRMED, 21 REFUTED, 3 UNMEASURABLE.** The build quality of the
*arithmetic the design shows its working for* is high — the buoyancy sum, the
mesh volumes and the z-stack all reproduce to the third decimal. The failures
are concentrated in three places: geometry that was asserted but never
measured (the board retention), a source that was cited for something it does
not say (the WOO envelope, BUILD.md's failure order, the UJ31 panel section),
and features that are described in prose but absent from the model (draft, the
O-ring lead-in).

Two findings are build-stopping: **#1** (the board and the carrier PCB are not
retained by anything) and **#2** (the 22 × 17 envelope may be the WOO sensor
*plus its cradle*, in which case the whole `woo22` preset is the wrong size).

---

## A. Build-stopping

### 1. The support ledges and snap tabs do not touch the board or the carrier PCB — REFUTED

**Claim.** README §4: *"the board … sits on ledges that project inward from the
side walls and is held down by four snap tabs… That is all six degrees of
freedom, rigidly"*; *"the board is clamped at **all four edges**"*; §4 width
budget *"bore 19.60 − 2 × 0.65 support ledge = 18.30 of clear span, for a 17.80
board + 0.40 clearance = 18.20. 0.10 mm spare"*. README §4 also places the
*"connector carrier PCB, 12.0 long × 14.5 wide, **on ledges**"*.

**Measurement.** Sliced `body_woo22_mjf.stl` at the ledge and tab stations and
ray-cast for the innermost material:

| feature | x | z | innermost material | board/carrier edge | result |
|---|---|---|---|---|---|
| board ledge | 24.0 | 8.60 | y = **9.150** | 8.90 | **misses by 0.25 mm/side** |
| snap tab | 16.1, 29.1 | 10.60 | y = **9.300** | 8.90 | **misses by 0.40 mm/side** |
| carrier ledge | 7.0 | 7.40 | y = **9.100** | 7.25 | **misses by 1.85 mm/side** |

Same measurement on `body_lp502030_mjf.stl`: ledge y = 9.900 (misses by
1.00 mm), tab y = 10.050 (misses by 1.15 mm) — the ledges are referenced to
`bore_w/2` so they move outward with the preset while the board stays 17.8 mm
wide, and the `lp502030` preset is worse.

**Why.** The assertion at `enclosure.scad:316` enforces
`board_w + board_clear_y <= bore_w - 2*board_ledge_w` — 18.20 ≤ 18.30. That is
the arithmetic for a board that drops **between** two ledges, i.e. one that
nothing supports in Z. For the board to *sit on* a ledge the ledge must project
inboard **past** the board edge, so the clear span must be **less** than 17.80,
not more. The two readings of "ledge" are mutually exclusive and the model
implements the one the README does not describe. The "0.10 mm spare" that §4
presents as the design being tight is in fact 0.10 mm of guaranteed *failure to
engage*.

The z placement is correct (ledge top at z = 9.00 = `z_board_bot`; tab underside
at 10.35, 0.15 above the 10.20 board top) — only the Y is wrong, which is why
nothing caught it. Genus, shell count and `manifold: NoError` cannot see it.

**Consequence.** The XIAO drops through onto the cell pigtail; the carrier PCB
(and with it the connector that carries the port seal's preload) is supported
by nothing but the RTV bead of assembly step 3. The IMU is not coupled to the
shell at all, which is the one property the design says it exists to guarantee.

**Fix.** Ledges must underlap. Set `board_ledge_w` so that
`bore_w - 2*board_ledge_w < board_w` — e.g. ledge 1.35 mm gives a 16.90 clear
span and 0.45 mm of seat per side, with the board located laterally by the bore
(19.60 − 17.80 = 0.90 total, 0.45/side). Tabs must project further still. Then
**replace the assertion** with one that tests the real requirement, in two
parts: `bore_w - 2*board_ledge_w < board_w` (it seats) **and**
`board_w + board_clear_y <= bore_w` (it fits). Do the same for
`conn_carrier_support` against `carrier_w`, which needs a ledge of
`(bore_w - carrier_w)/2 + seat` ≈ 3.0 mm, or a wider carrier.

### 2. The 22 × 17 envelope is the WOO sensor **and mount together**, not the sensor body — REFUTED

**Claim.** `[WOO]` source line, README and `enclosure.scad:22-24`: *"WOO 4.0
sensor body '5.4 x 2.2 x 1.7 cm'"*. The whole design hangs on it: §1 *"the
22.0 is the binding dimension"*, §3 *"Total 17.00 mm = the WOO body height
exactly"*, the `woo22` preset, and the §1 verdict *"Fits the WOO envelope."*

**Measurement.** Fetched the cited page. It reads: *"The sensor and mount
together weigh about as much as an AA battery. **Dimensions: 5.4 x 2.2 x 1.7
cm.**"* The dimensions sentence follows directly from "the sensor and mount
together" and carries that subject; the page never says "sensor body". BUILD.md
line 37 already splits the two clauses the other way (*"WOO 4.0 sensor body:
5.4 × 2.2 × 1.7 cm, sensor + mount 'about as much as an AA battery'"*) and then
concedes *"the mount itself is still unstated"* — so the repo's own source note
records the attribution as an inference, and the design has promoted it to a
measured fact.

**Consequence.** If 22.0 × 17.0 is the outside of sensor + cradle, then a puck
built to 22.0 × 17.0 cannot enter that cradle at all — it is over by two cradle
wall thicknesses in width and one floor thickness in height. Both presets are
then wrong, and `lp502030` (23.5) is not "1.5 mm over", it is 1.5 mm over a
number that was already too big.

This is CLAUDE.md §2 rule 6 and rule 2 in one: a confident citation the source
does not support, converted into the verdict "Fits the WOO envelope" with no
measurement behind it.

**Fix.** Downgrade `env_w`/`env_h` to `WOO?` parameters in the same class as
`cradle_*`, strike "Fits the WOO envelope" from the preset table until the
cradle is calipered, and re-state the §1 arithmetic as conditional. The cradle
was ordered 2026-09-15; nothing about width is knowable until it lands.

---

## B. Assembly geometry

### 3. The cap rib interferes with the board by 0.60 mm and leaves no room for the preload pad — REFUTED

**Claim.** README §2 "How the port seal gets its preload": *"the column is
built **0.3 mm** long; a 1.0 mm silicone pad takes up the interference"*. BOM:
*"Preload pad … 1.0 mm silicone sheet, 8 × 12 mm … Between the cap rib and the
board's aft edge. Compresses to ~0.7 mm."*

**Measurement.** `cap()`'s rib (`enclosure.scad:563`) starts at
`x_spigot - 12.0` = **34.000**; confirmed by the mesh — `cap_woo22_mjf.stl`
bbox is `x[34.000, 54.000]`. The board's aft face is `x_board + board_l` =
13.6 + 21.0 = **34.600**. The rib and the board overlap **0.60 mm** in a shared
z band (rib z 9.00–10.35, board z 9.00–10.20).

With the 1.0 mm pad in place the rib face would have to sit at x = 35.60. It
sits at 34.00. The column is over-length by **1.60 mm**, not 0.30 mm, and the
pad would have to compress from 1.0 mm to −0.6 mm.

**Fix.** Put the rib face at `x_board + board_l + preload_pad_t` = 35.60 and
build the interference in by subtracting 0.30 from the *cap plate* seat, not by
burying the rib in the board. Add
`assert(x_spigot - rib_len >= x_board + board_l + preload_pad_t)`.

### 4. There is no O-ring lead-in chamfer on the spigot or the bore mouth — REFUTED (feature absent)

**Claim.** README §2 presents the radial gland as the whole sealing strategy and
lists gland depth, groove width and fill, but specifies no lead-in.

**Measurement.** `spigot_prism()` is a plain `rrect` with square ends;
`bore_solid()` is a plain `rrect` run 1 mm past `x_bore_end`, so the bore mouth
at x = 52 is a square edge. Neither part has a chamfer anywhere in the model.
The O-ring at x 47.00–48.95 must be pushed **5.05 mm** into a square-edged bore
at 20% squeeze.

A radial O-ring entering a square-edged bore rolls or shears. Standard practice
is a 15–20° lead-in of at least 1.5 × cord (≈2.3 mm here) on the bore mouth, or
on the spigot nose, or both.

**Fix.** Chamfer the bore mouth 20° × 2.5 mm, or taper the spigot's first 2 mm.

### 5. The two cap screw holes open into the O-ring's swept path — REFUTED

**Claim.** README §2: *"**No fastener crosses a sealing surface.** The two cap
screws enter through the top wall aft of the O-ring groove, so their holes open
into the annulus behind the seal."* `enclosure.scad:432`: *"Both lie AFT of the
O-ring groove, so neither crosses the seal."*

**Measurement.** True at rest, false during assembly. The Ø1.85 clearance holes
are at x = 50.50, y = ±7.60, and break through the bore ceiling at z ≈ 15.69
(this is 2 of the body's genus-3 through-holes — confirmed). The O-ring is
carried on the spigot and travels from the bore mouth at x = 52.00 forward to
x = 47.98 (groove centre) — **it passes directly over both holes**, at 20%
squeeze, across a sharp 1.85 mm edge, every time the cap is fitted. On a beach.

**Fix.** Move the screws outboard of the bore into solid wall, or blind them
from the tail face, or break the hole edges with a 0.3 mm chamfer on the bore
side — the last is the cheapest and still leaves a cord dragged over two holes.

### 6. The specified screw is 0.5 mm longer than the tapped hole is deep — REFUTED

**Claim.** BOM: *"Cap screws, 2, M1.6 × **6**, A4 / 316 stainless."*

**Measurement.** `cap_screw_holes(false)` runs from z = 17.50 down 6.20 to
z = **11.30**. The spigot's top surface at y = 7.60 is in the corner arc, at
z = **15.29**. Thread engagement available = 15.29 − 11.30 = **3.99 mm**. The
screw must first cross the body's top wall (1.20) and the spigot-to-bore
annulus (0.30) = 1.50 mm, leaving 6.00 − 1.50 = **4.50 mm** of shank to bury in
a 3.99 mm hole. The screw bottoms ~0.5 mm before the head seats.

**Fix.** M1.6 × 5, or increase the blind-hole `depth` from 4.5 to 5.2.

### 7. The cell has no forward stop and can foul the connector — REFUTED (claim of location)

**Claim.** README §10 step 4: *"Press the cell into its **pocket**"*; §4 lists
the cell at x 12.00–44.00.

**Measurement.** There is no pocket. The only cell feature in the model is
`cell_fence()`, a 1.4 × 19.2 × 0.9 rib at x 44.0–45.4 that stops **aft** motion
only. Nothing stops the cell moving forward. The connector body hangs 1.40 mm
below the carrier plane, i.e. to z = **6.40**; the cell top is z = **6.90**
(`z_floor_in` 1.2 + 5.3 + 0.4 swell). The two overlap in Z by 0.50 mm, and the
cell is free to slide the 10.4 mm forward that puts it there. The fence is also
only 0.90 mm tall against a 5.3 mm cell — it will be climbed under impact.

**Fix.** Add a forward fence at x = 11.5 of the same construction, and raise
both to ≥2.0 mm. Add `assert(z_cell_top <= z_carrier_bot - 1.40)` so the
connector-vs-cell clash is caught at compile time.

---

## C. The seal

### 8. O-ring squeeze is not the uniform 20% claimed — REFUTED

**Claim.** README §2 gland table and `enclosure.scad:191-199`: squeeze **20%**,
gland depth **1.20 mm**, groove fill **~74%**, each as a single value.

**Measurement.** The bore (19.60 × 14.60, r 3.00) and the groove floor
(17.20 × 12.20, r 2.10 at `mjf` — measured off `cap_woo22_mjf.stl` at x = 47.5)
are **not a constant offset**, because `spigot_prism()` builds the spigot at the
bore's own corner radius (`max(0.8, 3.0 - shrink)` with shrink = 0) while making
it 0.30/side smaller. Exact minimum-distance between the two contours:

| process | gland depth min → max | squeeze | groove fill |
|---|---|---|---|
| mold | 1.200 → 1.293 | 20.0% → **13.8%** | 75.5% → 70.1% |
| sla | 1.200 → 1.304 | 20.0% → **13.1%** | 75.5% → 69.5% |
| mjf | 1.200 → 1.324 | 20.0% → **11.7%** | 75.5% → 68.4% |
| fdm | 1.200 → 1.345 | 20.0% → **10.3%** | 75.5% → 67.4% |

20% holds only on the four flats. At the 45° diagonals the squeeze falls to
11.7% (`mjf`) and 10.3% (`fdm`) — the bottom of the 10–25% static radial band,
at the four corners, which is exactly where a rounded-rectangle gland with a
butt-spliced cord is already weakest.

Separately, the stated fill of "~74%" is **75.5%** on the flats by the model's
own numbers (π/4 × 1.5² ÷ (1.95 × 1.20) = 0.755).

**Fix.** Make the spigot a true offset of the bore: pass the corner radius
through as `3.0 - (oring_fit + proc_slop/2)` at shrink = 0 (2.70 at `mjf`), so
the groove floor becomes a genuine constant 1.20 offset and squeeze is uniform.
Then assert `min_gland_depth` rather than trusting the nominal.

### 9. The O-ring cord length is overstated by ~12% — REFUTED

**Claim.** BOM: *"Cap O-ring … ~**67 mm** developed length"*; README §6 uses
67 mm for the 0.14 g mass line.

**Measurement.** The cord's neutral axis sits 0.60 mm inboard of the bore, i.e.
on a rounded rect 18.40 × 13.40, r 2.40. Perimeter =
2(18.40−4.80) + 2(13.40−4.80) + 2π(2.40) = 27.20 + 17.20 + 15.08 =
**59.5 mm**. Even the bore perimeter itself is only 63.3 mm, so 67 mm is not
reachable by any plausible datum.

The mass error is negligible (0.127 g vs 0.142 g). The *assembly* error is not:
cutting 67 mm of cord for a 59.5 mm groove leaves 7.5 mm to bury in a groove
that is already 75% full. It will bunch at the splice.

**Fix.** Derive the length in the model from the groove-floor perimeter and
print it; specify 59.5 mm.

### 10. The datasheet "(3.65) vs 4.25 discrepancy" does not exist — REFUTED

**Claim.** README §9 item 7: *"The datasheet's p4 panel section dimensions it
[the panel counterbore] as (3.65), but the OR-80 it has to contain is 4.25
tall. **Those do not reconcile.**"* `enclosure.scad:161-166` repeats it as
`!! DISCREPANCY, unresolved` and sizes the counterbore from the ring instead.

**Measurement.** Rendered p4 at 600–700 dpi and read the CASE PANEL section
directly. The panel carries a **stepped through-hole**: (3.00) at the outer
face, (3.65) at the inner face, with a chamfer between them. (3.65) is the
inner step of the *hole* — the lead-in the connector's nose enters — **not a
pocket for the seal ring**. The SEAL RING leader on the same page points at the
ring sitting on the connector's own flange, forward of it; the FRONT/BACK
ASSEMBLY and PLUG INSERTION views both show it compressed between that flange
and the panel's **flat inner face**. The ring is never inside the panel.

There is nothing to reconcile: 3.65 is a hole, 4.25 is a ring OD, and the ring
is behind the hole. The design invented the discrepancy by assuming a pocket,
then responded to it by cutting one (10.25 × 4.45 × 0.455), which replaces the
vendor's flat-land face seal with a scheme the vendor does not show.

**Fix.** Reproduce the datasheet's geometry: stepped hole (3.00 outer / 3.65
inner, with the chamfer) and a flat inner land for the ring. Delete the
`DISCREPANCY` block and §9 item 7. The counterbore, if kept at all, should only
locate the ring laterally, not define the squeeze.

### 11. `conn_nose_fit = 0.30` enlarges a vendor-specified panel hole and eats the sealing land — REFUTED

**Claim.** `enclosure.scad:147`: `conn_nose_fit = 0.30; // clearance added to
the through-hole`.

**Measurement.** (3.00) on p4 is dimensioned on the **CASE PANEL**, i.e. it is
the finished panel hole, not a connector dimension needing clearance. The
drawing's tolerance block is X.XX ±0.10. The model cuts 3.30.

The land the OR-80 bears on runs from the hole edge to the ring OD: at (3.00),
(4.25 − 3.00)/2 = **0.625 mm** on the short axis. At 3.30 it is **0.475 mm** —
the clearance consumes 24% of the land, on a ring whose annulus is 1.00 mm
wide, of which 0.525 mm then bridges open hole with nothing behind it and will
extrude into it at 48% compression.

**Fix.** Cut the hole at 3.00 nominal. Clearance belongs on the connector's
nose, which the vendor has already allowed for; do not add it to a panel
dimension the vendor specified.

### 12. `conn_nose_w = 9.00` is cited to a drawing that does not dimension the panel opening — REFUTED (citation)

**Claim.** `enclosure.scad:146`: `conn_nose_w = 9.00; // [UJ31 p2 DETAIL 2]
nose width across`.

**Measurement.** 9.00 appears on p2 in the **DETAIL 2 recommended-PCB-layout
group**, alongside 12.15, 12−0.20 and 0.65 — footprint dimensions. The p4 CASE
PANEL section dimensions the hole's **height** only, (3.00)/(3.65); the
datasheet nowhere dimensions the panel opening's **width**. The connector's own
body is 9.20 wide and its flange 9.80 (p2 front view), both larger than 9.00,
so 9.00 cannot be an outside width of anything that passes through a panel.

The port cutout width therefore rests on a number taken from the wrong view.

**Fix.** Get the vendor STEP model (linked on every datasheet page as "3D
Model") and take the nose section from it. Until then mark `conn_nose_w`
UNMEASURED, as the design does elsewhere.

### 13. The nose wall is ~2× thicker than the datasheet's reference panel — UNMEASURABLE (but a missing unknown)

**Claim.** README §8: *"Uniform wall: 1.2 mm throughout, **1.6 mm at the
nose**"*; §9's unmeasured list does not mention panel thickness.

**Measurement.** The only two axial dimensions the datasheet gives for the case
panel are (0.50) and (0.40) on p4, both reference dimensions, spanning the
panel's thickness bands at the hole. Both are well under this design's nose
wall (1.60 mm, or **1.145 mm** residual under the 0.455 counterbore — itself
below the "1.2 mm throughout" the README states). p4 also dimensions
**1.45(REF)** as the plug's standoff from the panel's outer face.

I could not resolve the drawing's scale reliably enough to assert what a 1.145
mm panel does to plug seating — the section is a schematic reference view and
its horizontal and vertical scales do not agree. So: **UNMEASURABLE here.** But
the design assumes nose-wall thickness is free, and the datasheet's own panel
is of order half a millimetre. If the plug's overmould bottoms on the nose
before the contacts mate, the port does not work at all.

**Fix.** Add "panel thickness the UJ31 will tolerate, and plug seating depth
through a 1.6 mm wall" to §9. A printed nose section and the real connector
answer it in five minutes, per the same logic §9 item 7 already applies.

---

## D. Buoyancy

### 14. The buoyancy arithmetic reproduces exactly — CONFIRMED

Every figure in §6 was recomputed from freshly rendered meshes.

| quantity | README | measured | |
|---|---|---|---|
| body material, woo22 | 5.148 cm³ | **5.1477** | ✓ |
| cap material, woo22 | 1.643 cm³ | **1.6430** | ✓ |
| displaced, woo22 | 19.910 cm³ | **19.9098** | ✓ |
| body material, lp502030 | 5.369 cm³ | **5.3686** | ✓ |
| cap material, lp502030 | 1.729 cm³ | **1.7295** | ✓ |
| displaced, lp502030 | 21.283 cm³ | **21.2835** | ✓ |

Margins re-derived from those volumes, contents 11.28 g, ρ 0.998: MJF PA12
+1.731 / +2.791 g; PP +2.444 / +3.536; SLA +0.577 / +1.584; PC +0.441 / +1.442.
All match to ±0.01 g. Percentages (8.7%, 13.1%, 12.3%, 2.9%, 2.2%, 6.8%) match.
Contents sum to 11.28 exactly. Carrier PCB 0.193 g ✓; 0.020 g/mAh ✓; "87 mAh
eats the margin" = 1.731/0.020 = 86.5 ✓; the cap coring saving measured at
2.3068 − 1.6430 = 0.664 cm³ = **0.670 g** against the claimed "~0.7 g" ✓;
"a solid cap is 2.3 cm³" ✓. Seawater deltas: woo22 +0.538 (stated 0.54 ✓),
lp502030 +0.575 (stated 0.58, should be 0.57 — rounding only).

The honesty caveat in §6 about the 2.45 g of estimated mass is correct and
correctly applied to SLA and PC.

### 15. The displaced volume ignores every flooded void — REFUTED

**Claim.** §6's displaced volumes come from `part="displacement"`, described as
*"The water-displacing envelope of the ASSEMBLED puck"*.

**Measurement.** `part="displacement"` is `rrect(env_l, env_w, env_h, 2.0)`
minus only the tether groove and the orientation mark. It does not subtract:

- the **port cavity**, which floods by design — §11 test 2 exists precisely to
  prove the shell is watertight *with the port open to the sea*. A USB-C
  receptacle mouth (8.34 × 2.56 front opening, ~6.5 deep, less the tongue) is
  ≈ **0.10 cm³**;
- the **annulus behind the O-ring**, open to the sea through the cap-plate
  joint: perimeter ≈ 63.3 mm × 0.30 gap × 3.05 mm (48.95 → 52.00) ≈
  **0.058 cm³**;
- the two **screw clearance holes** and the unfilled bottom of their tapped
  holes, ≈ 0.01 cm³.

Total ≈ **0.17 cm³ ≈ 0.17 g** of buoyancy that does not exist. Against the
stated margins that is 39% of `woo22` PC (+0.44), 29% of `woo22` SLA (+0.58),
10% of `woo22` MJF. Add the MJF sealer that §7 tells you to budget (0.2–0.4 g)
but §6's table does not carry, and `woo22` MJF falls from +1.73 to ≈ +1.26 g.

It does not sink anything. But §6 is presented as the complete budget and it is
not, and the two materials §6 already flags as inside the uncertainty get worse.

**Fix.** Subtract the port cutout and the aft annulus in the `displacement`
part, and carry the MJF sealer as a line in the contents table rather than a
note in §7.

---

## E. Model vs prose

### 16. `draft = 0.75` is declared and never applied to any geometry — REFUTED

**Claim.** README §8: *"**Draft**: `draft = 0.75°` per side at
`process="mold"`. The body is a ~50 mm straight-pull core; less than that and
it will drag."*

**Measurement.** `grep -n draft enclosure.scad` returns three hits: line 280
(the declaration) and two occurrences of the English word "draft" in comments at
lines 102 and 506. The variable is read nowhere. `body_woo22_mold.stl` and
`body_woo22_mjf.stl` have identical triangle counts (3166) and the mold body is
0-draft: the bore is a constant-section prism.

**Fix.** Either apply it or delete it and move the sentence in §8 to §9. A
declared-but-unused parameter that the README describes as a feature is the
worst of both.

### 17. "The shell is all-plastic with no metal anywhere" — REFUTED

**Claim.** README §4: *"the design removes the need to know [the antenna
position]: **the shell is all-plastic with no metal anywhere**, so BLE passes
regardless of where the antenna sits."*

**Measurement.** The BOM lists, inside or through the sealed boundary: 2 × M1.6
A4 stainless screws; the UJ31, whose shell, mid plate and GND plate are all
stainless steel (datasheet p2 material table, confirmed verbatim); 4 × AWG30
wires; and **the LP502030 itself** — a 19–20.5 × 32 mm aluminium-laminate pouch
sitting 2.5 mm below the PCB across the board's entire footprint.

A metallised pouch that large, that close, parallel to the board, is the
single most likely thing in the assembly to detune or shadow a chip antenna.
The claim that the enclosure "removes the need to know" where the antenna sits
is therefore not supported — the cell's position relative to the antenna is
exactly the thing that matters, and the design has fixed it without knowing it.

**Fix.** Strike the sentence. Keep the honest version: BLE through an
all-plastic shell is likely fine, the cell underneath is an unknown, and §11
test 4 is what settles it. This one is cheap to de-risk — the board and cell
already exist; range-test the stack on the bench before printing anything.

### 18. BUILD.md does not say what it is cited as saying about foam and the IMU — REFUTED (citation)

**Claim**, three places. README §4: *"BUILD.md's 'realistic failure order' and
the IMU both want the board coupled to the shell and **not floating on
foam**."* `enclosure.scad:107-109`: *"BUILD.md 'realistic failure order': the
IMU must be rigidly coupled — **no foam under the board, ever**."* BOM, cell
pad: *"Never under the board - the IMU must be rigidly coupled (BUILD.md
'realistic failure order')."*

**Measurement.** BUILD.md's "realistic failure order" (line 358) is four ranked
items — pigtail/JST joint, adhesive mount or seal, large MLCCs, MCU/IMU
silicon — and says nothing about foam, rigid coupling, or board mounting. The
nearest statement in that section is the **opposite** in spirit: *"**Compliant
foam under the enclosure is the single best shock mitigation** — it lengthens
the deceleration pulse, which is what actually lowers peak g."* (That is foam
under the *enclosure*, not under the board, so it is not a direct
contradiction — but it is not the rule being quoted either.)

This is CLAUDE.md §2 rule 6 verbatim: *"On 2026-08-23 I attributed a rule to
this file that is not in it… a confident wrong citation is worse than no
citation."*

**Fix.** Either find the real source for "no foam under the board" or state it
as the design's own engineering judgement. Note that BUILD.md's actual advice
(compliant foam under the enclosure) argues for a compliant cradle interface,
which the design should at least acknowledge.

### 19. Three derived-value comments in `enclosure.scad` are stale — REFUTED

**Measurement.** The values are right; the comments claiming them are wrong.

| line | code | comment says | actual |
|---|---|---|---|
| 287 | `z_cell_top = 1.2 + 5.3 + 0.4` | `// 7.00` | **6.90** |
| 297 | `z_board_top = 9.00 + 1.2` | `// 8.70 PCB top` | **10.20** |
| 298 | `z_stack_top = 9.00 + 3.5` | `// 11.00 tallest comp` | **12.50** |

README §3's cumulative column (6.90, 10.20, 12.50) is correct throughout, so
the README is right and the model's comments disagree with the model's own
arithmetic. In a file whose stated discipline is "no number without a source",
three wrong numbers in the z-stack are the ones most likely to be read.

**Fix.** Recompute the comments, or drop them and let `echo()` print the stack.

### 20. `board_support()`'s comment describes an anchoring the code does not use — REFUTED (comment)

**Claim.** `enclosure.scad:379-380`: *"NB: these are anchored to the AS-CUT bore
surface (`bore_w + proc_slop`), not to the nominal bore. Anchoring to the
nominal leaves a proc_slop/2 air gap and the ledges render as free-floating
shells."*

**Measurement.** The code is `wall_y = bore_w/2;` — the nominal bore. There is
no `proc_slop` anywhere in `board_support()` or `conn_carrier_support()`. The
comment is harmless in effect (the bore is never enlarged by `proc_slop`; §
"TOLERANCES" is explicit that the tolerance goes on the spigot only), but it
describes a mechanism that is not there and would mislead the next edit.

**Fix.** Delete the NB, or restate it as the reason `proc_slop` is *not* applied
to the bore.

### 21. `woo22` is documented as taking a cell the model's own assertions reject — REFUTED

**Claim.** README §1 preset table: *"`woo22` … holds **a cell ≤ 19.0 × 42 ×
6.0 mm**"*. BOM: *"woo22 preset: any LiPo ≤ 19.0 × 42 × 6.0 mm with PCM."*

**Measurement.**
- **Length.** The cell runs from `x_cell` = 12.00 to `x_spigot` = 46.00 →
  **34.0 mm** available, and `assert(x_cell + cell_l_max <= x_spigot)` at line
  320 *fails* for anything over 34.0. A 42 mm cell does not compile.
- **Thickness.** `z_cell_top = wall + cell_t_max + cell_swell`; at 5.3 + 0.4
  that is 6.90, already 0.90 below the carrier at 7.80 and 0.50 above the
  connector's underside at 6.40 (see #7). A 6.0 mm cell puts the pocket top at
  7.60 and clashes with both. No assertion guards it.
- **Width.** 19.0 is right but has **zero** margin —
  `assert(cell_w_max + 0.6 <= bore_w)` is 19.60 ≤ 19.60 — and unlike thickness,
  width gets no `cell_swell` allowance at all, though pouches grow in plan too.

The buildable `woo22` cell is roughly **19.0 × 34 × 5.3**, not 19.0 × 42 × 6.0.
That is a materially smaller cell than the table advertises, which matters
because §1's whole recommendation is "buy a narrower cell".

**Fix.** Publish the envelope the assertions actually enforce, apply
`cell_swell` to width as well as thickness, and add
`assert(z_cell_top + 0.5 <= z_carrier_bot)`.

### 22. "5.6% of the face" — REFUTED

**Claim.** README §2: *"the UJ31 needs a 3.00 mm tall through-hole … that is
about 9.3 mm wide: **5.6% of the face**."*

**Measurement.** Nose face area = 22 × 17 less four r = 2.0 corners
= 374.0 − 3.434 = **370.57 mm²**. Hole as a stadium 9.30 × 3.00 =
3.00 × 6.30 + π(1.50)² = 18.90 + 7.07 = **25.97 mm²** → **7.0%**. As a plain
rectangle, 7.5%.

5.6% is reproducible only as **3.00 / 54.0 = 5.56%** — the hole's height over
the puck's *length*, which is not a fraction of the nose face.

**Fix.** 7.0%. The argument survives intact either way.

### 23. The plug's lanyard cannot reach the tether groove, and the bung is 0.6 mm undersize — REFUTED

**Claim.** `enclosure.scad:585`: *"lanyard strap to the tether groove"*; BOM:
*"Silicone port plug … **captive on a 26 mm lanyard**"*; README §10 step 9.

**Measurement.** `plug_woo22_mjf.stl` bbox is
`x[0.000, 6.800] y[−27.700, 8.100] z[−2.500, 2.500]`. The lanyard extends
**27.7 mm in −Y** — sideways, past the puck's own side wall at y = −11.0, by
16.7 mm. The tether groove is at **x = 44.0**, i.e. 44 mm **aft** along a
different axis. A 26 mm strap cannot reach it, and it is pointed the wrong way.

The bung is `d = conn_nose_h − 0.3` = **2.70** into a hole cut at
`conn_nose_h + conn_nose_fit` = **3.30** — 0.60 mm undersize, 18%. A silicone
bung that loose falls out on the first rinse.

**Fix.** Route the lanyard aft along +X and make it ≥ 48 mm, or anchor it under
a cap screw. Size the bung to the connector's actual port cavity (the receptacle
mouth, ~8.34 × 2.56), not to the panel hole, with a light interference.

### 24. The shell's outside dimensions are exactly 54 × 22 × 17 — CONFIRMED (with one note)

**Measurement.** `body` bbox `x[0, 52] y[±11] z[0, 17]`; `cap` plate closes
x 52 → 54. Assembled outer envelope = **54.000 × 22.000 × 17.000**, and
`displacement` measures the same. `lp502030` = 54.000 × 23.500 × 17.000, i.e.
exactly the stated +1.5. `1.5 / 22.0 = 6.82%` ✓ matches the stated 6.8%.

**Note.** The claim covers the shell only. Fitted, the plug's flange and lanyard
root stand **2.6 mm** proud of the nose (bung 4.2 + flange 1.4 + strap 1.2, less
the 4.2 of insertion), so the installed length is 56.6 mm. Probably free, given
the cradle grips the middle — but it is not in the envelope table.

### 25. The tether groove's station is never checked against the cradle bands — REFUTED (missing check)

**Claim.** README §9 item 1: *"**Check first that the tether groove at x = 44
does not land under an O-ring band.**"* `enclosure.scad:63-66` declares
`cradle_band_fwd = 13.0` and `cradle_band_aft = 41.0`.

**Measurement.** `grep -n cradle_ enclosure.scad` returns only the four
declarations. Nothing reads them — README §9 concedes this (*"nothing in the
model depends on them yet"*). Meanwhile the groove is 2.2 mm wide centred at
44.0, so it spans **42.9 – 45.1**, and the model's own guessed aft band sits at
41.0. Any band wider than 3.8 mm overlaps the groove *on the design's own
guess*, and a WOO cradle band is a rubber O-ring of roughly that order.

Also: §4's plan-view table lists the groove as a single station "44.00" while
every other row is a range. It is a 2.2 mm range.

**Fix.** Add `assert(!tether_enable || tether_x - tether_groove_w/2 >
cradle_band_aft + cradle_band_w/2)` with a `cradle_band_w` parameter, so the
guess is enforced rather than noted, and the assertion fires the moment the real
cradle numbers go in.

### 26. `docs/solder.md` does not exist and is cited without the archive form — REFUTED (citation)

**Claim.** README §10 step 1: *"JST polarity is not standardised and a reversed
cell kills the charger IC (`docs/solder.md` §1; CLAUDE.md)."*

**Measurement.** `find . -name "solder*"` returns nothing. CLAUDE.md §5 says
deleted docs are a deliberate lookup entry and prescribes the resolution form
`git show archive/docs-2026-08-23:<path>`; §3's table does not list solder.md.
The README cites `docs/sense.md` correctly as *"archived `docs/sense.md`"*
twice, so the convention is understood — step 1 just does not follow it. Whether
the file is in the archive tag was not checked (no git, per the review brief).

Separately, "CLAUDE.md" is cited for the JST polarity rule. CLAUDE.md contains
no such rule; its §2 rule 1 is about not declaring hardware dead, and its §1 is
the board/battery table.

**Fix.** Mark it `archived docs/solder.md`, and drop the CLAUDE.md citation or
point it at the section that actually carries the claim.

---

## F. What held up

### 27. OpenSCAD compiles clean, 48/48 — CONFIRMED

All 48 combinations of {body, cap, plug, assembly, cutaway, displacement} ×
{woo22, lp502030} × {fdm, sla, mjf, mold} rendered with `Status: NoError` and
**zero** warnings, errors, deprecations or failed assertions. Full log:
`scratchpad/out/compile.log`. Verbatim: the only non-structural lines in 48
runs are `Status:     NoError`, the genus line, and cache counts.

### 28. Single closed shells, zero non-manifold edges, body genus 3 — CONFIRMED

Independently measured by quantised-vertex union-find over each mesh:
`body` 1 shell / 0 non-manifold edges / **genus 3** (the port + two screw
holes, as claimed); `cap` 1 / 0 / genus 0; `plug` 1 / 0 / genus 0;
`displacement` 1 / 0 / genus 0. Genus is 3 for `body` in all 8 preset × process
combinations, and 0 for `cap` and `plug` in all 8. The claim is exactly right.

### 29. UJ31 electrical and mechanical claims — CONFIRMED

From the fetched datasheet (rev 1.07, 09/12/2024):
- **p3 pin table**: A6 Dp1, A7 Dn1, B6 Dp2, B7 Dn2 — verbatim. USB 2.0 data in
  both plug orientations ✓, and the README's "p3 pin table" citation is right.
- **p3**: *"PCB thickness: 0.60 mm"* — verbatim; the 0.60 carrier is mandatory ✓.
- **p1**: *"IP level — IP67"*, with **no** mated/unmated qualifier — §9 item 6's
  honesty about this is fully justified ✓.
- **p1**: USB 3.2 Gen 2, 24 contacts ✓. **p2**: OR-80 silicone ✓, outer
  10.05 × 4.25, inner 8.05 × 2.25, 0.80/0.95 ✓ (and 0.80/0.95 is indeed the
  ring's *axial* thickness, so compressing it axially is the right direction).
- **p4**: *"Suggest the shrinkage value is the 40% ~ 50% diameter of the seal
  ring"* — verbatim; `seal_squeeze = 0.48` is inside the band ✓.
- **No mass anywhere in the datasheet** — §9 item 5 is correct ✓.
- **rev 1.03** *"moved o-rings to be included in vacuum packaging"* — the BOM
  quotes it verbatim ✓.

### 30. GCT USB4135-GF-A is power-only — CONFIRMED

DigiKey 16036137, Number of Contacts, verbatim: *"24 (6+18 Dummy) - Power Only
6 Pin"*, with *"Power Only, No Data"* in the features. The rejection of (a′) is
correct and well-sourced — and it is the most valuable single fact in the
document, because it is the part everyone reaches for.

### 31. D+/D- are on no XIAO pad — CONFIRMED

The Seeed wiki lists the pin inventory and exposes no USB data pads; fetched
directly, it reports **"No USB D+/D- pins are exposed"** as a standard feature,
and the back side carries battery pads only. The design's §1 conclusion — that
any external connector needs a 0.5 mm-pitch microsolder tap, and that this cost
is identical across options (a)/(b)/(c) and so does not choose between them —
is sound, and the instruction to meter the tap points before soldering is the
right call given the schematic gives nets, not pad locations.

### 32. XIAO 21 × 17.8 mm — CONFIRMED

Seeed wiki, verbatim: *"Ultra Small Size: 21 x 17.8mm"*. Note that some
secondary listings say 21 × 17.5; the design cites the official figure, and
17.8 is the conservative one. PCB thickness and overall height are indeed
unpublished — the wiki gives neither — so §9 item 3 is correct to flag them.

### 33. In-repo citations that check out — CONFIRMED

- **DECISION #9** — DECISIONS.md line 16: *"Must float + be tethered"* ✓.
- **Hammond 1551WHGY** — BUILD.md line 34: *"stainless lid screws seated
  *outside* the gasket"* ✓ verbatim.
- **57.1 h idle, DC/DC enabled** — docs/STATUS.md line 247 ✓.
- **Failure order item 1** — BUILD.md line 361: the LiPo pigtail / JST joint is
  first, and *"Glue-dot the connector and anchor the wires"* is verbatim ✓.
- **Silica packet** — BUILD.md line 39 ✓.
- **Domed front deck / straight-edge first** — BUILD.md line 37 and
  docs/STATUS.md line 28 ✓.
- **Three boards can advertise; pin by name** — CLAUDE.md §1 ✓, and §11 test 4
  applies it correctly.

### 34. Option (b) is dead by arithmetic, and (d) does not seal — CONFIRMED

(b): (17.0 − 16.0)/2 = **0.50 mm** of wall, less once the 2.0 mm corner radius
is counted. Correct, and correctly characterised as arithmetic rather than
taste. (d): the leak path through an unsealed receptacle's open back is real
and the reasoning about trading a leak for galvanic corrosion on live contacts
is sound. The four-option comparison is the strongest section of the document.

### 35. The orientation mark exists and points the right way — CONFIRMED

`orientation_mark()` cuts an arrow at x 7.5–17.5 whose apex is the `[0,0]`
polygon vertex, i.e. pointing toward **−x = the nose = the port end** ✓, plus
`NOSE` in 4.4 mm text at x = 20. Both are recessed 0.3 mm, and
`assert(wall - mark_depth >= 0.8)` guards the sealed top wall ✓. The reasoning
in §5 — that an ambiguous mounting orientation is a data bug because the IMU's
axes are fixed to the shell — is correct and worth keeping.

### 36. The tether is a groove, not an eye — CONFIRMED (deliberate deviation)

The brief asked for a tether eye. The design ships a full circumferential groove
at x = 44 (2.2 × 0.5), states the deviation openly, and justifies it: a moulded
eye at this scale is a ~1.2 × 1.5 mm bar. That is right — and `tether_groove_d`
is guarded by `assert(wall - tether_groove_d >= 0.6)` ✓, leaving 0.7 mm of
sealed wall, measured. Accepted as a deviation, not a defect. (Its *station* is
unchecked — see #25 — and the plug's lanyard cannot reach it — see #23.)

### 37. "Shrink the plug, not the hole" — CONFIRMED, and it is the right call

`proc_slop` is applied only to the spigot (`enclosure.scad:508-510`); the bore
is never enlarged. Verified across all four processes: the bore measures
19.600 × 14.600 in every one, and only the spigot changes. The stated reason —
that enlarging the bore eats the wall asymmetrically and at `fdm` thinned the
top wall to 0.9 mm until the tether groove broke through — is sound, and it is
the discipline that the corner-radius bug in #8 slipped past rather than a
failure of it.

---

## Fix list, ranked

1. **#1** Ledges must underlap the board and the carrier; replace the width
   assertion with a two-part one that tests seating *and* fit. Nothing else
   matters until this is done — the current model has no board retention.
2. **#2** Stop asserting the WOO envelope. 22 × 17 may be sensor + cradle.
   Demote `env_w`/`env_h` to `WOO?`, strike "Fits the WOO envelope".
3. **#3** Move the cap rib aft by 1.6 mm so the preload pad has room.
4. **#10, #11, #12** Rebuild the port from the vendor STEP model: stepped hole
   3.00/3.65, flat inner land, no added clearance on a panel dimension.
5. **#8** Make the spigot a true offset of the bore (corner radius
   `3.0 - clearance`) so squeeze is uniform; assert the minimum gland depth.
6. **#4, #5** Add an O-ring lead-in chamfer; get the screw holes out of the
   seal's swept path.
7. **#15** Subtract the flooded voids from `displacement`; carry the MJF
   sealer in the contents table.
8. **#18, #26, #22, #19, #20** Citation and comment hygiene — CLAUDE.md §2
   rule 6. Each is cheap; together they are what makes the rest trustworthy.
9. **#7, #21, #23, #6, #16, #9, #25, #17** The rest, in any order.

## One structural note

The document's own discipline is what found most of this: the honesty of §9
("what is unmeasured") and the volume-measured-off-the-mesh method are both
genuinely good, and §14 above shows the arithmetic is sound wherever the design
showed its working. The failures cluster where prose was written *about*
geometry instead of *from* it — the ledges, the rib, the draft, the lanyard, the
corner squeeze. Every one of those would have been caught by a compile-time
assertion on the thing actually required, which is the same lesson the file
already learned three times (the genus count, the shell count, the handle
count) and then stopped applying one step short.
