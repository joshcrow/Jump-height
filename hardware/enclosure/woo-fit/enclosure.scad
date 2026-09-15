// =====================================================================
// woo-fit — waterproof puck enclosure for the XIAO nRF52840 Sense
// Target: drops into a WOO Sports 65240-7203 adhesive cradle.
//
// House rule (CLAUDE.md §2.2): no number without a source. Every
// dimension below carries its source in the comment. Where a number is
// UNMEASURED it says so and is a parameter, not a constant.
//
// rev 2 — 2026-09-15, after the adversarial review in REVIEW.md.
// 21 refuted items applied. The three that changed the machine:
//   * board retention did not exist (ledges missed the board by 0.25 mm
//     per side). Rebuilt as a located channel + seat + ramped snap tab.
//   * the port seal was built around a counterbore the datasheet does
//     not have. Rebuilt from the vendor's own panel section, re-read at
//     900 dpi: a stepped hole and a FLAT inner land, 0.30 mm of
//     specified compression.
//   * the 22 x 17 envelope is not a quoted fact about the WOO sensor
//     body. It may be sensor + cradle. It is now a WOO? parameter.
//
// *** THE TWO THINGS TO READ BEFORE PRINTING ***
//
// 1. The envelope is a GUESS, not a measurement.  The retailer page says
//    "The sensor and mount together weigh about as much as an AA
//    battery. Dimensions: 5.4 x 2.2 x 1.7 cm."  The dimensions sentence
//    follows "the sensor and mount TOGETHER". If that is what it means,
//    a 22.0 mm-wide puck cannot enter the cradle at all. BUILD.md's own
//    note concedes "the mount itself is still unstated". So env_w/env_h
//    are tagged WOO? and nothing here may be called "fits the WOO
//    envelope" until the cradle is calipered.
//
// 2. The installed LP502030 is 20.5 mm wide at max tolerance. A shell
//    22.0 mm wide cannot contain it: 20.5 + 0.2 swell + 0.4 clearance
//    + 2 x 1.2 wall = 23.7 mm. The two presets are the two honest
//    answers; pick one deliberately.
//      preset = "woo22"    -> 22.0 mm wide, matches the 2.2 cm figure.
//                            REQUIRES a cell <= 18.8 x 33.8 x 5.3 mm
//                            (not the one we own). Default.
//      preset = "lp502030" -> 23.7 mm wide. Holds the cell we own today.
//                            1.7 mm (7.7%) wider than the 2.2 cm figure.
//
// SOURCES (cited where used)
//  [WOO]   easy-surfshop WOO 4.0 kit page, fetched 2026-09-15, verbatim:
//          "The sensor and mount together weigh about as much as an AA
//           battery. Dimensions: 5.4 x 2.2 x 1.7 cm."
//          https://easy-surfshop.com/do/item/WOO-65240-7200/
//          NOTE: the page does NOT say "sensor body". See warning 1.
//  [XIAO]  Seeed wiki, "Ultra Small Size: 21 x 17.8mm"
//          https://wiki.seeedstudio.com/XIAO_BLE/
//          (also archived docs/sense.md §1 table, "Size | 21 x 17.8 mm")
//  [SCH]   Seeed Studio XIAO nRF52840 v1.1 schematic (official PDF)
//          https://files.seeedstudio.com/wiki/XIAO-BLE/Seeed-Studio-XIAO-nRF52840-Sense-v1.1.pdf
//  [CELL]  LiPol Battery LP502030 datasheet, Dwg. FD_2120_10, 20.12.2023
//          https://www.lipolbattery.com/LiPo-Battery-Datahseet/LiPo_Battery_LP502030.pdf
//  [UJ31]  Same Sky UJ31-CH-3-MSMT-TR-67 datasheet, rev 1.07, 09/12/2024
//          https://www.sameskydevices.com/product/resource/uj31-ch-3-msmt-tr-67.pdf
//          Pages 2 and 4 were rendered at 400-900 dpi and read directly
//          on 2026-09-15; every [UJ31] number below is off that render.
// =====================================================================

// ---------------------------------------------------------------- part
// body | cap | plug | assembly | cutaway | displacement
part   = "assembly";

// woo22 | lp502030   (see header)
preset = "woo22";

// fdm | sla | mjf | mold   — drives clearances and draft, not nominals
process = "mjf";

$fn = 48;

// ============================================================ ENVELOPE
// [WOO] 5.4 x 2.2 x 1.7 cm.  *** WOO? — the source attributes this to
// "the sensor and mount together", not to the sensor body. Treat as a
// target, not a fit. Recheck all three with calipers on the real cradle.
env_l = 54.0;                                   // WOO? 5.4 cm
env_h = 17.0;                                   // WOO? 1.7 cm

// Width comes from the preset. See header for the arithmetic.
env_w = (preset == "woo22") ? 22.0              // WOO? 2.2 cm
                            : 23.7;             // = 20.5 + 0.2 + 0.4 + 2.4

// *** UNMEASURED — the WOO cradle itself (65240-7203) is on order and
// has never been in hand. Everything tagged CRADLE? must be re-checked
// with calipers against the real cradle before committing to a mould.
// BUILD.md "WOO mount"; docs/STATUS.md "Nick's board / mount".
// These are no longer decorative: the tether-groove assertion below
// reads them, so the guess is enforced and will fire the moment the
// real numbers go in.
cradle_band_fwd  = 13.0;   // CRADLE? O-ring band 1 centre, mm aft of nose
cradle_band_aft  = 41.0;   // CRADLE? O-ring band 2 centre, mm aft of nose
cradle_band_w    =  4.0;   // CRADLE? band width (a rubber O-ring loop)
cradle_lip_h     =  3.0;   // CRADLE? height of the cradle side lips
cradle_floor_w   = 22.0;   // CRADLE? clear width between the lips

// ============================================================== WALLS
wall      = 1.2;   // structural side/floor/top wall. 1.2 mm is the
                   // thinnest wall that moulds reliably in PC/PA and
                   // prints watertight in MJF/SLA. Do not thin it.
nose_wall = 1.6;   // thicker: it takes the direct water hit and the
                   // impact load (see ORIENTATION below). It is LOCALLY
                   // thinned to 0.90 at the port — see PORT below, that
                   // is a datasheet requirement, not a saving.

// Derived interior ("the bore"). The shell is a one-piece tube closed at
// the nose and open at the tail — see SEALING SCHEME below for why.
bore_w = env_w - 2*wall;
bore_h = env_h - 2*wall;

// ============================================================== BOARD
// [XIAO] 21 x 17.8 mm. Seeed publishes no mounting holes and none are
// visible on the board outline, so the board is retained by a moulded
// CHANNEL (locates it in Y), a SEAT (carries it in Z) and two ramped
// SNAP TABS per side (holds it down). See board_support() for the
// geometry and for what rev 1 got wrong.
board_l  = 21.0;   // [XIAO]
board_w  = 17.8;   // [XIAO]

// UNMEASURED: Seeed publishes no PCB thickness and no overall height
// (the wiki was re-fetched 2026-09-15 and gives neither). Distributor
// listings give 3.5 mm overall (secondary, unverified). CALIPER BOTH
// before cutting a mould; both are parameters for exactly that reason.
board_pcb_t   = 1.2;   // UNMEASURED — PCB only
board_stack_h = 3.5;   // UNMEASURED — PCB + tallest top component.
                       // The tallest top component is the XIAO's OWN
                       // USB-C receptacle, which this design does not
                       // use. Desoldering it reclaims ~2.3 mm of height
                       // but is irreversible on a board we own 3 of.
board_clear_y = 0.4;   // TOTAL side clearance inside the channel
board_seat    = 0.45;  // how far the seat underlaps the board, per side
board_tab_l   = 3.0;   // length of each snap tab along the board
board_tab_t   = 0.9;   // tab height
board_slot_slop = 0.15;  // + on PCB thickness under the tab

// ============================================================== CELL
// [CELL] LP502030 + PCM, Dwg FD_2120_10:
//   thickness 5.0 +0.3/-0.3  -> 5.3 max
//   width     20   +0.5/-0.5 -> 20.5 max
//   length    31   +1.0/-1.0 -> 32.0 max  (includes the PCM)
//   weight    "appr. 5.0g"    leads 50 +/-3 mm AWG28 UL1571
// This CONFIRMS the estimate BUILD.md was carrying ("treat as 5.2 x 20.5
// x 32 max until calipered") — it is now a specified figure, not a guess.
cell_t_max = 5.3;    // [CELL]
cell_l_max = 32.0;   // [CELL]
cell_w_max = (preset == "lp502030") ? 20.5   // [CELL] the cell we own
                                    : 18.8;  // the widest cell a 22.0 mm
                                             // shell can take ONCE plan
                                             // swell and clearance are
                                             // counted. rev 1 said 19.0
                                             // with zero margin and no
                                             // swell allowance at all.
// LiPo pouches grow with age and cycle count. Growth is predominantly
// through-thickness; in plan it is much smaller, and the +/-0.5 width
// tolerance already covers manufacture. Two allowances, not one — rev 1
// applied the thickness figure to thickness only and nothing to width.
cell_swell_t = 0.4;
cell_swell_w = 0.2;
cell_clear   = 0.4;  // total side clearance in the pocket
cell_fence_t = 2.2;  // fence height. rev 1 used 0.9 mm against a 5.3 mm
                     // cell — it would be climbed on the first landing.

// ========================================================== CONNECTOR
// [UJ31] Same Sky UJ31-CH-3-MSMT-TR-67 — IP67, mid-mount SMT, 24 contact
// USB-C carrying Dp1/Dn1 and Dp2/Dn2 (p3 pin table), so USB 2.0 data
// works in both plug orientations. Chosen over the "obvious"
// GCT USB4135-GF-A, which DigiKey lists as "24 (6+18 Dummy) - Power Only
// 6 Pin" — charge only, no data. See README for the full comparison.
//
// The connector sits on its OWN 0.60 mm carrier PCB [UJ31 p3: "PCB
// thickness: 0.60 mm"] and is wired to the XIAO with four wires. It
// cannot sit on the XIAO: mid-mount needs a 0.60 mm board, and the XIAO
// does not break out D+/D- anyway ([SCH]: J1 = VBUS/GND/3V3/P1.15/P1.14/
// P1.13/P1.12, J2 = P0.02/P0.03/P0.28/P0.29/P0.04/P0.05/P1.11 — D+ and
// D- appear only as nets between USB1 and the nRF52840).
//
// --- the panel, straight off the vendor's own section (p4, 900 dpi) ---
// The CASE PANEL carries a STEPPED through-hole:
//     (3.00) tall at the OUTER face, for (0.50) of depth
//     45 deg chamfer
//     (3.65) tall for the remaining (0.40), to the inner face
// so the vendor's reference panel is 0.50 + 0.40 = 0.90 mm thick, and
// the connector's nose (which stands 1.00 fwd of its flange, p2 side
// view) ends up recessed inside the hole. The SEAL RING sits on the
// connector's flange and is compressed against the panel's FLAT INNER
// FACE. There is no pocket in the panel.
// rev 1 read (3.65) as a counterbore for the ring, could not reconcile
// it with the 4.25 ring, and cut a pocket the vendor does not have.
conn_hole_h_out = 3.00;   // [UJ31 p4] outer step height  — panel dim,
                          //   cut at nominal, no clearance added
conn_hole_h_in  = 3.65;   // [UJ31 p4] inner step height  — ditto
conn_step_d     = 0.50;   // [UJ31 p4] depth of the outer step
conn_step_cham  = 0.10;   // the 45 deg blend between the two steps
nose_land_t     = 0.90;   // [UJ31 p4] = 0.50 + 0.40. The sealing land is
                          //   built to the vendor's own panel thickness.
                          //   The surrounding nose wall stays 1.60 and
                          //   the difference is taken as an external
                          //   recess (which also gives a cable overmould
                          //   the 1.45(REF) standoff p4 shows).

// Width: the datasheet NEVER dimensions the panel opening's width. It
// does dimension the connector's nose — the section that passes through
// the panel — on the p2 top view: 8.74, between the 9.20 body and the
// 9.80 flange. rev 1 used 9.00, which is an SMT pad span off DETAIL 2
// (a footprint dimension) and is not the outside of anything.
conn_nose_w     = 8.74;   // [UJ31 p2 top view] nose width
conn_flange_w   = 9.80;   // [UJ31 p2 top view] flange width
conn_body_w     = 9.20;   // [UJ31 p2 top view] body width
conn_nose_fit_w = 0.10;   // clearance on a CONNECTOR dimension (p2
                          //   tolerance block: X.XX +/-0.10). None is
                          //   added to the panel heights above.
conn_open_w     = conn_nose_w + conn_nose_fit_w;   // 8.84

// External recess around the port: takes the nose wall from 1.60 down to
// the 0.90 land, and clears a cable's overmould.
port_recess_d = nose_wall - nose_land_t;   // 0.70
port_recess_w = 13.0;
port_recess_h = 7.0;

// --- the seal ---
// OR-80 silicone ring [UJ31 p2]: outer 10.05 x 4.25, inner 8.05 x 2.25,
// section 0.80 / 0.95. It is captive on the connector's flange (p4
// FRONT/BACK ASSEMBLY shows it fitted before insertion), so the shell
// needs no feature to hold it — only a flat land to squeeze it against.
seal_ring_ol = 10.05;  // [UJ31 p2] outer, long axis
seal_ring_oh =  4.25;  // [UJ31 p2] outer, short axis
seal_ring_t  =  0.80;  // [UJ31 p2] the thinner of the two sections: the
                       //   one that governs, since it bottoms last
// [UJ31 p4] dimensions the compression itself: "0.30", labelled
// "(Shrinkage value of case panel to the seal ring)", with the note
// "Suggest the shrinkage value is the 40% ~ 50% diameter of the seal
// ring."  0.30 / 0.80 = 37.5%.  This is a SPECIFIED value, so the design
// delivers it rather than inventing a squeeze of its own.
seal_shrink  = 0.30;   // [UJ31 p4]
seal_gap     = seal_ring_t - seal_shrink;   // 0.50 — flange to land

// [UJ31 p2/p3] carrier geometry
carrier_pcb_t   = 0.60;   // [UJ31 p3] "PCB thickness: 0.60 mm" — mandatory
carrier_l       = 12.0;   // carrier PCB length (insertion direction)
carrier_w       = 14.5;   // carrier PCB width; footprint is 12.15 [UJ31]
carrier_seat    = 0.60;   // seat underlap per side
carrier_clear_y = 0.4;    // total side clearance in its channel
conn_centre_off = 0.30;   // [UJ31 p2] "0.30 CENTER HEIGHT", above PCB top
conn_offset     = 1.40;   // [UJ31 p2] "1.40(OFFSET)", below PCB top
conn_body_l     = 9.25;   // [UJ31 p2 side view] overall length
conn_nose_proj  = 1.00;   // [UJ31 p2 side view] nose fwd of the flange
// UNMEASURED: where the carrier PCB's front edge sits relative to the
// flange face. p3's recommended layout is dimensioned to the pads, not
// to a board edge. Flush is the assumption; the carrier stop below is
// built from it, and it is shimmable at assembly.
flange_to_carrier_edge = 0.0;   // UNMEASURED
preload_pad_t   = 1.0;    // silicone pad, compressed to 0.7 (see below)
preload_interf  = 0.3;    // how much the column is built over-length

// ======================================================= SEALING SCHEME
// A radial bore seal on a tail cap, NOT a face gasket under a lid.
// Why: a face gasket needs a rim of land + groove + land on every side.
// The narrowest honest rim for a 1.0 mm cord is ~2.6 mm, which costs
// 5.2 mm of width. At 22 mm outside that leaves 16.8 mm of bore and the
// XIAO alone is 17.8 mm wide — the board would not fit, never mind the
// cell. A radial seal costs only the wall: 2 x 1.2 = 2.4 mm.
// Secondary benefit, and the real one: a radial O-ring seals on
// interference, not on bolt load. It does not care whether someone did
// the screws up evenly on a beach.
oring_cord    = 1.5;    // silicone cord, Shore 50-60A
oring_squeeze = 0.20;   // 20% — mid of the 15-30% band for a static
                        // radial seal
oring_fit     = 0.20;   // radial clearance, spigot to bore

// Gland maths, all derived so changing the cord re-solves the groove:
gland_depth  = oring_cord * (1 - oring_squeeze);  // 1.20 radial height
                                                  // (bore wall to groove floor)
groove_width = oring_cord * 1.30;                 // 1.95 -> fill 75.5%
groove_fill  = PI/4 * oring_cord*oring_cord / (groove_width * gland_depth);

cap_plate_t  = 2.0;   // solid tail plate
cap_spigot_l = 6.0;   // spigot engagement into the bore
cap_screw    = 1.6;   // 2x M1.6 A4 stainless, cap retention only
cap_screw_len = 5.0;  // M1.6 x 5. rev 1 specified x6 into a 3.99 mm hole
                      // — it bottomed 0.5 mm before the head seated.
cap_screw_thread_d = 4.0;   // blind tapped depth in the spigot

// LEAD-IN. A radial O-ring pushed into a square-edged bore rolls or
// shears; rev 1 had no chamfer anywhere and asked the cord to enter a
// square edge at 20% squeeze. The lead-in has to be on the BORE MOUTH,
// because by the time the cord reaches the mouth the spigot's own nose
// is already 4 mm inside — a spigot chamfer does nothing for the cord.
// 0.40 radial > the 0.30 the cord must give up, at 20 deg -> 1.10 axial.
// The wall thins to 0.80 at the extreme mouth edge only, and the cap
// plate covers it.
bore_mouth_cham   = 0.40;
bore_mouth_cham_a = 20;    // degrees
spigot_nose_cham  = 0.36;  // alignment only, on the spigot's leading end
spigot_nose_len   = 1.0;

// The cap is cored out as a pocket opening FORWARD, into the sealed air
// space. A solid cap is 2.3 cm3 of plastic and this puck's whole buoyancy
// margin is about 1 g, so this is worth ~0.7 g.
//
// It has to open forward, and that is the whole lesson here. Three
// versions were tried and measured:
//  - a closed internal void: saves the mass, but cannot be moulded (no
//    core can form it) and traps powder in MJF. Caught by the shell
//    count, not by "manifold: NoError", which it also passed.
//  - a dish opening AFT, out of the tail face: mouldable, but the dish
//    floods, so it gives back exactly as much displacement as it saves in
//    mass. Measured net gain for PA12: +0.01 g. Pointless.
//  - a pocket opening FORWARD into the sealed interior: the removed
//    plastic becomes sealed air. Displacement unchanged, mass down 0.7 g.
//
// The pocket wall is cap_core_t PLUS the groove depth, so that the wall
// under the O-ring groove is still cap_core_t. That also keeps it a
// straight pull: the section never widens as the core withdraws.
cap_lighten  = true;
cap_core_t   = 1.2;   // wall left around the core

// The retention screws enter through the TOP wall AFT of the O-ring
// groove, so their holes open into the annulus BEHIND the seal — the
// same property BUILD.md praises the Hammond 1551WHGY for ("stainless
// lid screws seated *outside* the gasket").
//
// HONEST LIMIT, and rev 1 claimed more than this: "no fastener crosses a
// sealing surface" is true AT REST only. The cord is carried on the
// spigot and travels the full length of the bore on assembly, so it
// passes over both holes every time the cap goes on. That is inherent to
// a radial seal with transverse fasteners in a 1.2 mm wall — there is no
// room outboard of the bore for a boss, and a screw forward of the seal
// would open into the dry volume, which is worse. Two mitigations:
//   * the holes are moved inboard to y = +/-5.0, off the bore's corner
//     arc and onto its flat top, where the cord is best supported. rev 1
//     put them at +/-7.6, breaking through the corner radius, which is
//     where a rounded-rectangle gland is already weakest.
//   * the bore-side edge of each hole is chamfered 0.40 mm.
// Grease the cord on assembly. This is a named residual risk, not a
// solved problem.
cap_screw_x_from_tail = 1.5;
cap_screw_y           = 5.0;
cap_screw_cham        = 0.40;

// ========================================================== TETHER
// DECISION #9: "Must float + be tethered." A moulded eye at this scale
// is a 1.2 x 1.5 mm plastic bar and will snap before the leash does. So
// the tether is a full circumferential groove: a Dyneema loop or a cable
// tie seats in it and the load goes into the shell as hoop stress, not
// into a boss. It also costs zero height, which a boss would not — and
// height is what the cradle's O-rings press on.
tether_groove_w = 2.2;    // fits a 2.5 mm cable tie or 2 mm cord
tether_groove_d = 0.5;    // NOT deeper: this groove is cut into the
                          // sealed boundary. 0.5 leaves 0.7 mm of wall.
                          // At 0.9 it left 0.3 mm and, once the process
                          // tolerance was added, broke through entirely.
tether_x        = 44.8;   // CRADLE? — moved aft from 44.0 in rev 1,
                          // which overlapped the guessed aft band. Now
                          // ENFORCED by an assertion against
                          // cradle_band_aft/cradle_band_w. The clearance
                          // is 0.70 mm against a GUESS; this is the
                          // first dimension to re-check when the cradle
                          // lands, and the assertion will fire if the
                          // real band is wider or further aft.
tether_enable   = true;

// ======================================================= ORIENTATION
// PORT END = NOSE (points at the board's nose). Three reasons:
//  1. The IMU's axes are fixed to the shell. An ambiguous mounting
//     orientation is a data bug, not a cosmetic one.
//  2. The port's seal is factory-made (the connector's own O-ring). The
//     cap's seal is made by hand, on a beach, by whoever last opened it.
//     Put the factory seal where the water hits — the nose — and the
//     hand-made one in the lee.
//  3. The tether runs AFT to a footstrap insert, so the tether groove
//     belongs at the tail; port and tether want opposite ends.
mark_enable = true;
mark_depth  = 0.3;   // recessed, so the cradle O-rings ride over it.
                     // NOT deeper: this is cut into the sealed top wall.

// ======================================================== TOLERANCES
// Applied to the CAP SPIGOT only — never to the bore.
// Enlarging the bore by the process tolerance is the obvious move and it
// is wrong: it eats the wall asymmetrically (the floor keeps its 1.2 mm,
// the ceiling loses the whole tolerance) and at process="fdm" it thinned
// the top wall to 0.9 mm, at which point the tether groove cut straight
// through the sealed boundary. Shrink the plug, not the hole.
proc_slop = (process == "fdm")  ? 0.30 :   // FDM: elephant-foot + squish
            (process == "sla")  ? 0.10 :   // SLA: tight, but brittle
            (process == "mjf")  ? 0.20 :   // MJF/SLS PA12: powder-bound
                                  0.05 ;   // injection mould

// ============================================================== DRAFT
// rev 1 declared draft = 0.75 and never read it: the mould and MJF
// bodies were byte-identical. It is now APPLIED, to the core only, and
// the value had to come down to pay for it.
//
// The body is a one-piece tube; the core pulls aft, so the bore must
// narrow toward the nose. Draft accumulates over the pull: at 0.75 deg
// it costs 42.4 * tan(0.75) = 0.555 mm PER SIDE at the nose, and the
// width budget — the binding dimension of this whole part — cannot pay
// it. 0.25 deg costs 0.185 mm per side, which it can. That buys a
// demanding core (polished, well vented, long) and that cost is stated
// in README §8 rather than hidden in a parameter nobody reads.
//
// The last 8 mm of the bore is a ZERO-DRAFT SEAL LAND: the O-ring gland
// must be a constant section, and it sits at the core's root where no
// draft has accumulated anyway.
//
// The cavity (outer form) is NOT drafted here. The outer section is the
// cradle interface and must be measured against the real cradle before
// anything is done to it — see README §9.
draft      = (process == "mold") ? 0.25 : 0.0;   // degrees per side
seal_land_x0 = 44.0;                             // bore is straight aft of here

// ===================================================== DERIVED Z STACK
// z = 0 is the OUTSIDE of the floor. Every row is sourced; see README
// for the same table in prose. rev 1's comments on these three lines
// stated 7.00 / 8.70 / 11.00, none of which is what the code computes.
z_floor_in   = wall;                                     // 1.20
z_cell_top   = z_floor_in + cell_t_max + cell_swell_t;   // 6.90
// The cell's pigtail is HAND-soldered to the XIAO's underside BAT pads
// (archived docs/sense.md 3.4: "No connector on the board: the JST pigtail
// gets soldered to the underside BAT pads"). A hand joint on AWG28 stands
// 1-1.5 mm proud of the board, and the wire has to turn. An earlier
// version of this stack-up allowed 0.5 mm here, which would have crushed
// the joint against the cell — and BUILD.md's "realistic failure order"
// puts that joint FIRST on the list of things that break.
solder_clear = 2.10;
z_board_bot  = z_cell_top + solder_clear;                //  9.00
z_board_top  = z_board_bot + board_pcb_t;                // 10.20 PCB top
z_tab_bot    = z_board_bot + board_pcb_t + board_slot_slop;  // 10.35
z_stack_top  = z_board_bot + board_stack_h;              // 12.50 tallest comp
z_ceiling    = env_h - wall;                             // 15.80
z_free_air   = z_ceiling - z_stack_top;                  //  3.30 float + silica

// The carrier PCB sits BELOW the main board (which is lifted by the
// pigtail solder clearance) and forward of the cell. Its height is
// chosen so the port centre lands on the nose face's own centre line.
z_carrier_bot = 7.60;
z_carrier_top = z_carrier_bot + carrier_pcb_t;              // 8.20
z_port_centre = z_carrier_top + conn_centre_off;            // 8.50 = env_h/2
z_conn_bot    = z_carrier_top - conn_offset;                // 6.80

// X stations, measured aft from the nose OUTER face.
// The chain starts at the seal, not at the wall, because the seal is
// what has to be right: land inner face -> compressed ring -> flange.
x_land_in     = nose_wall;                          //  1.60
x_flange      = x_land_in + seal_gap;               //  2.10
x_nose_tip    = x_flange - conn_nose_proj;          //  1.10 (inside the hole)
x_carrier     = x_flange + flange_to_carrier_edge;  //  2.10
x_conn_aft    = x_nose_tip + conn_body_l;           // 10.35
x_board       = x_carrier + carrier_l;              // 14.10
x_cell        = 12.2;     // clear of the connector body AND of its own
                          // forward fence — see the assertion below
x_bore_end    = env_l - cap_plate_t;                // 52.00
x_spigot      = x_bore_end - cap_spigot_l;          // 46.00
x_groove0     = x_spigot + 1.0;                     // 47.00
x_groove1     = x_groove0 + groove_width;           // 48.95
x_cap_rib     = x_board + board_l + (preload_pad_t - preload_interf);  // 35.80

// O-ring developed length, taken off the cord's own neutral axis
// (gland_depth/2 inboard of the bore). rev 1's BOM said 67 mm, which is
// longer than the bore perimeter itself and would bunch at the splice.
oring_na_w = bore_w - gland_depth;
oring_na_h = bore_h - gland_depth;
oring_na_r = 3.0 - gland_depth/2;
oring_len  = 2*(oring_na_w - 2*oring_na_r) + 2*(oring_na_h - 2*oring_na_r)
             + 2*PI*oring_na_r;

// Y stations of the retention features. These are referenced to the
// BOARD, not to the bore — which is the whole correction. rev 1 anchored
// them to bore_w/2, so they drifted outward with the preset while the
// board stayed 17.8 mm wide and missed it by 0.25 mm (woo22) or 1.00 mm
// (lp502030) per side.
board_ch_y     = (board_w + board_clear_y)/2;       // 9.10 - locates in Y
board_seat_y   = board_w/2 - board_seat;            // 8.45 - carries in Z
carrier_ch_y   = (carrier_w + carrier_clear_y)/2;   // 7.45 - locates in Y
carrier_seat_y = carrier_w/2 - carrier_seat;        // 6.65 - carries in Z

// ---- sanity assertions: these are the fit checks, run at compile time.
// Rule learned the hard way, three times: assert the thing actually
// REQUIRED, not a proxy for it. rev 1 asserted that the board fits
// BETWEEN the ledges, which is the arithmetic for a board nothing
// supports, and the model duly built ledges that missed it.

// -- board: it must both FIT in the channel and SEAT on the ledge --
// Two separate requirements, and rev 1 asserted only a proxy for the
// first. THE BOARD FITS:
assert(board_ch_y > board_w/2,
  "BOARD CHANNEL is not wider than the board: board_clear_y must be > 0.");
assert(board_ch_y - board_w/2 <= 0.35,
  "BOARD CHANNEL is so loose it does not locate the board. The IMU has to be rigidly coupled, so this is a data requirement, not a fit one.");
// AND IT SEATS — the seat's inboard face must be INSIDE the board edge:
assert(board_seat_y < board_w/2,
  "BOARD SEAT does not underlap the board: it would drop straight through onto the cell pigtail. This is what rev 1 shipped.");
assert(board_seat >= 0.35,
  "BOARD SEAT is too narrow to carry the board reliably.");
// AND THERE IS WALL LEFT TO BUILD THE CHANNEL OUT OF, at the narrowest
// (most drafted, most forward) station the board occupies:
assert(bore_hw(x_board) >= board_ch_y + 0.45,
  "BOARD CHANNEL walls have no material to stand on: widen env_w or cut the draft.");
// -- carrier PCB: the same two tests, which rev 1 failed by 1.85 mm --
assert(carrier_ch_y > carrier_w/2,
  "CARRIER CHANNEL is not wider than the carrier PCB.");
assert(carrier_seat_y < carrier_w/2,
  "CARRIER SEAT does not underlap the carrier PCB: nothing holds the connector that carries the port seal's preload.");
assert(carrier_seat >= 0.4,
  "CARRIER SEAT is too narrow.");
assert(bore_hw(x_carrier) >= carrier_ch_y + 0.45,
  "CARRIER CHANNEL walls have no material to stand on.");
// -- cell --
assert(cell_w_max + cell_swell_w + cell_clear <= bore_w,
  "CELL DOES NOT FIT: see the header. Use preset=\"lp502030\" or a narrower cell.");
assert(x_cell + cell_l_max + 1.4 <= x_spigot,
  "CELL TOO LONG: it collides with its aft fence or the cap spigot.");
assert(x_cell - 1.4 - 0.2 >= x_conn_aft,
  "CELL FORWARD FENCE fouls the connector body.");
assert(z_cell_top + 0.0 <= z_board_bot - solder_clear + 0.001,
  "CELL POCKET eats the pigtail solder clearance.");
// The cell and the connector overlap in Z (6.90 vs 6.80) and are kept
// apart in X. rev 1 had no forward stop at all, so the cell was free to
// slide 10.4 mm forward and foul the connector's underside.
assert(x_cell > x_conn_aft || z_cell_top <= z_conn_bot,
  "CELL fouls the connector: it is neither aft of it nor below it.");
// -- stack --
assert(z_stack_top < z_ceiling,
  "STACK TOO TALL: the board's top component hits the ceiling.");
// -- port --
assert(conn_hole_h_in + 2*wall < env_h,
  "PORT DOES NOT FIT the nose face.");
assert(nose_land_t == conn_step_d + 0.40,
  "NOSE LAND no longer matches the datasheet's 0.50 + 0.40 panel.");
assert(nose_land_t <= conn_nose_proj,
  "NOSE LAND is thicker than the connector's nose projection: the nose cannot reach the outer step.");
assert((seal_ring_oh - conn_hole_h_in)/2 >= 0.25,
  "SEALING LAND on the short axis is under 0.25 mm. Do not add clearance to the datasheet's panel heights.");
assert((seal_ring_ol - conn_open_w)/2 >= 0.40,
  "SEALING LAND on the long axis is under 0.40 mm.");
assert(conn_open_w < conn_flange_w - 0.6,
  "PORT OPENING is too wide for the flange to have a land to bear on.");
assert(z_port_centre - port_recess_h/2 > wall + 1.0 &&
       z_port_centre + port_recess_h/2 < env_h - wall - 1.0,
  "PORT RECESS runs off the nose face.");
assert(nose_wall - port_recess_d >= 0.85,
  "PORT RECESS leaves less than 0.85 mm of sealing land.");
// -- cap --
assert(x_cap_rib >= x_board + board_l + 0.5,
  "CAP RIB overlaps the board: there is no room for the preload pad. (rev 1 buried the rib 0.60 mm inside the board.)");
assert(x_cap_rib < x_spigot - 1.0,
  "CAP RIB collides with the spigot.");
assert(cap_screw_len <= (env_h - (wall + bore_h - (oring_fit + proc_slop/2)))
                        + cap_screw_thread_d - 0.3,
  "CAP SCREW is longer than the hole is deep: it bottoms before the head seats. (rev 1 specified M1.6x6 into 3.99 mm of thread.)");
assert(cap_screw_y + (cap_screw + 0.25)/2 + cap_screw_cham
       <= bore_w/2 - 3.0,
  "CAP SCREWS break through the bore's corner arc, where a rounded-rectangle gland with a butt-spliced cord is already weakest. Move them inboard. (rev 1 put them at y = +/-7.6, squarely on the arc.)");
assert(x_groove1 + 0.9 < env_l - cap_plate_t - cap_screw_x_from_tail
                          - (cap_screw + 0.25)/2,
  "CAP SCREW tapping hole breaks into the O-ring groove.");
assert(bore_mouth_cham > oring_cord * oring_squeeze,
  "BORE MOUTH CHAMFER is smaller than the squeeze the cord must give up on entry: the cord will roll or shear.");
assert(wall - bore_mouth_cham >= 0.75,
  "BORE MOUTH CHAMFER leaves under 0.75 mm of wall at the mouth edge.");
// -- shell boundary --
assert(!mark_enable || wall - mark_depth >= 0.8,
  "ORIENTATION MARK is cut too deep into the sealed top wall.");
assert(!tether_enable || wall - tether_groove_d >= 0.6,
  "TETHER GROOVE breaks the sealed wall: wall - tether_groove_d < 0.6 mm.");
assert(!tether_enable ||
       tether_x - tether_groove_w/2 > cradle_band_aft + cradle_band_w/2,
  "TETHER GROOVE lands under the cradle's aft O-ring band. This is a CRADLE? check against a GUESS — if it fires after the real cradle is measured, move the groove, not the assertion.");
assert(!tether_enable || tether_x + tether_groove_w/2 < x_groove0,
  "TETHER GROOVE thins the wall over the O-ring gland.");
// -- draft --
assert(bore_w/2 - (seal_land_x0 - x_board)*tan(draft)
       >= (board_w + board_clear_y)/2 + 0.45,
  "DRAFT has narrowed the bore below the board channel. Reduce draft or widen env_w.");

echo(str("part=", part, " preset=", preset, " process=", process));
echo(str("outside  = ", env_l, " x ", env_w, " x ", env_h, " mm"));
echo(str("bore     = ", bore_w, " x ", bore_h, " mm"));
echo(str("gland    depth ", gland_depth, "  width ", groove_width,
         "  fill ", round(groove_fill*1000)/10, "%"));
echo(str("O-ring cord length = ", round(oring_len*10)/10, " mm"));
echo(str("draft at the nose  = ",
         round((seal_land_x0 - nose_wall)*tan(draft)*1000)/1000, " mm/side"));
echo(str("cell envelope this preset accepts = ",
         cell_w_max, " x ", x_spigot - 1.4 - x_cell, " x ", cell_t_max, " mm"));

// =====================================================================
// MODULES
// =====================================================================

// Rounded prism, extruded along X, with the corners rounded in the Y-Z
// CROSS-SECTION — because that is where the rounding belongs: the shell
// is a tube and the 22 x 17 cross-section is what the cradle's O-rings
// wrap around. Spans x 0..l, y +/-w/2, z 0..h, for any l (an earlier
// version hulled cylinders along Z and blew out whenever l < 2r).
module rrect(l, w, h, r) {
  rr = min(r, w/2 - 0.01, h/2 - 0.01);
  hull() for (y = [-(w/2 - rr), w/2 - rr], z = [rr, h - rr])
    translate([0, y, z]) rotate([0, 90, 0]) cylinder(h = l, r = rr, $fn = 32);
}

// ------------------------------------------------------------- the bore
// The interior cavity, drawn as a solid so it can be subtracted.
// Two sections: a DRAFTED run from the nose to the seal land, and a
// STRAIGHT seal land from there to the tail. At draft = 0 the hull
// degenerates to the same straight prism rev 1 had.
bore_draft = (seal_land_x0 - nose_wall) * tan(draft);   // per side, at the nose

module bore_solid(extra = 0) {
  union() {
    hull() {
      translate([nose_wall, 0, wall + bore_draft])
        rrect(0.02, bore_w - 2*bore_draft, bore_h - 2*bore_draft,
              max(0.8, 3.0 - bore_draft));
      translate([seal_land_x0 - 0.02, 0, wall]) rrect(0.02, bore_w, bore_h, 3.0);
    }
    translate([seal_land_x0 - 0.04, 0, wall])
      rrect(x_bore_end - seal_land_x0 + 0.04 + extra, bore_w, bore_h, 3.0);
  }
}

// Bore half-width at station x, accounting for draft.
function bore_hw(x) = bore_w/2 - (x < seal_land_x0
                                  ? (seal_land_x0 - x) * tan(draft) : 0);

// ----------------------------------------------------- the port opening
// Reproduces the vendor's CASE PANEL section (p4) rather than inventing
// a pocket: external recess down to the 0.90 land, then a stepped hole
// 3.00 -> chamfer -> 3.65, then a FLAT inner land for the seal ring.
module stadium_x(len, w, h, x0) {   // w spans Y, h spans Z, extruded in X
  translate([x0, 0, z_port_centre]) rotate([0, 90, 0])
    hull() for (y = [-(w - h)/2, (w - h)/2])
      translate([0, y, 0]) cylinder(h = len, d = h, $fn = 36);
}

module port_cutout() {
  // external recess: takes 1.60 of nose wall down to the vendor's 0.90
  // panel and gives a cable overmould the 1.45(REF) standoff of p4
  translate([-1, 0, z_port_centre - port_recess_h/2])
    rrect(1 + port_recess_d, port_recess_w, port_recess_h, 2.5);
  // outer step, 3.00 tall x 0.50 deep  [UJ31 p4]
  stadium_x(1 + port_recess_d + conn_step_d, conn_open_w, conn_hole_h_out, -1);
  // 45 deg blend between the steps  [UJ31 p4]
  hull() {
    stadium_x(0.01, conn_open_w, conn_hole_h_out, port_recess_d + conn_step_d);
    stadium_x(0.01, conn_open_w, conn_hole_h_in,
              port_recess_d + conn_step_d + conn_step_cham);
  }
  // inner step, 3.65 tall, to the inner face  [UJ31 p4]
  stadium_x(nose_wall - (port_recess_d + conn_step_d + conn_step_cham) + 0.01,
            conn_open_w, conn_hole_h_in,
            port_recess_d + conn_step_d + conn_step_cham);
}

// --------------------------------------------- located support features
// ADDED material, not a slot. No posts: the XIAO has no mounting holes
// (re-checked on the Seeed wiki 2026-09-15 — it lists none).
//
// Everything here is built by INTERSECTING the bore with a block that
// starts at a fixed Y and runs outward. That does three things at once:
// the feature always fuses to the wall (no free-floating shells), it
// follows the drafted bore automatically, and — the point rev 1 missed —
// its inboard face is at the Y THE BOARD NEEDS, not at an offset from a
// bore wall that moves with the preset. rev 1's ledges were anchored to
// bore_w/2 and so drifted outward with env_w while the board stayed
// 17.8 mm wide; at preset=lp502030 they missed by 1.00 mm per side.
module side_block(x0, x1, z0, z1, y_in, s) {
  translate([x0, s > 0 ? y_in : -y_in - env_w, z0])
    cube([x1 - x0, env_w, z1 - z0]);
}

module in_bore_band(x0, x1, z0, z1, y_in) {
  intersection() {
    bore_solid(0);
    union() for (s = [-1, 1]) side_block(x0, x1, z0, z1, y_in, s);
  }
}

// A ramped tab: full projection at its BOTTOM (the hold-down face),
// retracted to the channel wall at its TOP, so the part being retained
// slides down the ramp and snaps under it.
module in_bore_ramp(x0, x1, z0, z1, y_low, y_high) {
  for (s = [-1, 1])
    intersection() {
      bore_solid(0);
      hull() {
        side_block(x0, x1, z0, z0 + 0.01, y_low,  s);
        side_block(x0, x1, z1 - 0.01, z1, y_high, s);
      }
    }
}

module board_support() {
  // channel walls: the board's lateral location, 0.20 mm per side
  in_bore_band(x_board - 0.5, x_board + board_l + 0.5,
               z_board_bot, z_tab_bot, board_ch_y);
  // seat: underlaps the board by board_seat per side
  in_bore_band(x_board, x_board + board_l,
               z_board_bot - 0.8, z_board_bot, board_seat_y);
  // two ramped snap tabs per side
  for (fx = [x_board + 2.5, x_board + board_l - 5.5])
    in_bore_ramp(fx, fx + board_tab_l, z_tab_bot, z_tab_bot + board_tab_t,
                 board_seat_y, board_ch_y);
}

// ------------------------------------------- connector carrier support
// HOW THE PORT SEAL GETS ITS PRELOAD
// The OR-80 ring must be squeezed ALONG the connector's axis (+X) by
// 0.30 mm [UJ31 p4], and a screw through the flat carrier PCB pulls
// DOWN, not forward. So the whole interior is a compression column.
// Front to back:
//   nose land | OR-80 | connector flange | carrier PCB | XIAO |
//   silicone preload pad | cap rib | cap plate | body tail face
//
// The carrier's forward travel is limited by a hard STOP on the nose's
// inner face, placed outboard of the connector body, so the 0.30 mm is
// set by geometry rather than by how hard someone pushed. The cap plate
// bottoms on the body's tail end face and the column is built
// preload_interf (0.30 mm) long; the 1.0 mm silicone pad takes up that
// interference and holds the stack against the stop. The cap's two
// screws only stop the cap backing out; they carry no seal load.
module conn_carrier_support() {
  in_bore_band(x_carrier - 0.2, x_carrier + carrier_l,
               z_carrier_bot, z_carrier_top + 0.15, carrier_ch_y);
  in_bore_band(x_carrier, x_carrier + carrier_l,
               z_carrier_bot - 0.8, z_carrier_bot, carrier_seat_y);
  in_bore_ramp(x_carrier + carrier_l - 4.0, x_carrier + carrier_l - 1.0,
               z_carrier_top + 0.15, z_carrier_top + 0.95,
               carrier_seat_y, carrier_ch_y);
  // forward stop pads: the carrier's front edge butts on these. Placed
  // outboard of the connector body (9.20 wide -> 4.60 half) so they
  // touch only PCB.
  for (s = [-1, 1])
    translate([x_land_in, s > 0 ? 4.9 : -7.2, z_carrier_bot - 0.3])
      cube([x_carrier - x_land_in, 2.3, carrier_pcb_t + 0.6]);
}

// ------------------------------------------------ cell fore/aft fences
// Ribs ADDED across the bore floor that stop the cell sliding either
// way. They were briefly a subtracted relief, which cut 1.0 mm into a
// 1.2 mm side wall on both sides — through the sealed boundary. Never
// subtract across the full bore width; the bore width IS the wall.
// rev 1 had an aft fence only, 0.9 mm tall.
module cell_fences() {
  for (fx = [x_cell - 1.4, x_cell + cell_l_max])
    translate([fx, -(bore_w - 0.4)/2, wall])
      cube([1.4, bore_w - 0.4, cell_fence_t]);
}

// ------------------------------------------------------ cap screw holes
// clearance=true  -> the hole through the BODY's top wall (Ø1.85)
// clearance=false -> the blind tapping hole in the CAP's spigot (Ø1.31)
// Both lie AFT of the O-ring groove, so at rest neither crosses the
// seal. See the note at cap_screw_y for what that does and does not buy.
module cap_screw_holes(clearance) {
  x = env_l - cap_plate_t - cap_screw_x_from_tail;
  spigot_top = wall + bore_h - (oring_fit + proc_slop/2);
  for (s = [-1, 1]) translate([x, s * cap_screw_y, 0]) {
    if (clearance) {
      translate([0, 0, env_h + 0.5]) rotate([180, 0, 0])
        cylinder(h = 2.5, d = cap_screw + 0.25);
      // break the bore-side edge, so the cord is not dragged over a
      // sharp Ø1.85 hole on every assembly
      translate([0, 0, z_ceiling - cap_screw_cham])
        cylinder(h = cap_screw_cham + 0.01,
                 d1 = cap_screw + 0.25 + 2*cap_screw_cham, d2 = cap_screw + 0.25);
    } else {
      translate([0, 0, spigot_top + 0.5]) rotate([180, 0, 0])
        cylinder(h = 0.5 + cap_screw_thread_d, d = cap_screw * 0.82);
    }
  }
}

// ------------------------------------------------------- tether groove
// Both prisms must be CONCENTRIC with the shell cross-section. An earlier
// version started the inner prism at z = -1 while the outer started at
// z = -1 too but was 2 mm taller, so the "groove" sheared the whole top
// of the body off above z = 14.2. It still compiled, still reported
// "manifold: NoError", and the handle count is what caught it.
module tether_groove() {
  if (tether_enable)
    difference() {
      translate([tether_x - tether_groove_w/2, 0, -1])
        rrect(tether_groove_w, env_w + 2, env_h + 2, 1.6);
      translate([tether_x - tether_groove_w/2 - 1, 0, tether_groove_d])
        rrect(tether_groove_w + 2, env_w - 2*tether_groove_d,
              env_h - 2*tether_groove_d, 1.6);
    }
}

// --------------------------------------------------- orientation mark
module orientation_mark() {
  if (mark_enable) {
    // arrow pointing at the nose (= the port end)
    translate([7.5, 0, env_h - mark_depth])
      linear_extrude(mark_depth + 0.01)
        polygon([[0,0],[4.5,3.2],[4.5,1.2],[10,1.2],[10,-1.2],[4.5,-1.2],[4.5,-3.2]]);
    translate([20.0, -2.6, env_h - mark_depth])
      linear_extrude(mark_depth + 0.01)
        text("NOSE", size = 4.4, halign = "left", valign = "baseline");
  }
}

// ------------------------------------------------------ bore mouth lead-in
module bore_mouth_leadin() {
  len = bore_mouth_cham / tan(bore_mouth_cham_a);
  hull() {
    translate([x_bore_end - len, 0, wall]) rrect(0.02, bore_w, bore_h, 3.0);
    translate([x_bore_end - 0.02, 0, wall - bore_mouth_cham])
      rrect(0.04 + 1, bore_w + 2*bore_mouth_cham, bore_h + 2*bore_mouth_cham,
            3.0 + bore_mouth_cham);
  }
}

// ================================================================ BODY
module body() {
  difference() {
    union() {
      difference() {
        rrect(env_l - cap_plate_t, env_w, env_h, 2.0);
        bore_solid(1);         // hollow it
        bore_mouth_leadin();   // O-ring lead-in at the mouth
      }
      conn_carrier_support();
      board_support();
      cell_fences();
    }
    port_cutout();          // the USB port, built to the vendor's panel
    cap_screw_holes(true);  // clearance holes through the top wall
    tether_groove();
    orientation_mark();
  }
}

// ================================================================= CAP
// Spigot cross-section, as a parametric prism so the groove floor is
// derived from the same origin as the spigot itself.
//
// The corner radius now carries the offset too. rev 1 built the spigot
// at the BORE's own 3.0 mm radius while making it smaller on the flats,
// so the gland was not a constant offset: the squeeze held 20% on the
// four flats and fell to 11.7% (mjf) / 10.3% (fdm) at the four 45 deg
// diagonals — the bottom of the static band, at the corners, which is
// where a rounded-rectangle gland with a butt splice is already weakest.
// A true offset makes the squeeze uniform everywhere.
spigot_off = oring_fit + proc_slop/2;
spigot_w   = bore_w - 2*spigot_off;
spigot_h   = bore_h - 2*spigot_off;
spigot_z   = wall + spigot_off;

module spigot_prism(len, shrink, x0) {
  translate([x0, 0, spigot_z + shrink])
    rrect(len, spigot_w - 2*shrink, spigot_h - 2*shrink,
          max(0.6, 3.0 - spigot_off - shrink));
}

groove_cut = gland_depth - spigot_off;      // cut into the spigot
x_core0 = x_spigot - 1;                     // open at the front
x_core1 = env_l - cap_plate_t/2 - cap_core_t;  // leave the tail solid

module cap() {
  union() {
    difference() {
      union() {
        // the plate, flush with the body's outer section
        translate([x_bore_end, 0, 0]) rrect(cap_plate_t, env_w, env_h, 2.0);
        // the spigot. It overlaps 0.1 mm into the plate: a spigot that
        // merely abuts the plate on a coincident plane renders as a
        // separate shell (this is the same trap as the board rib).
        spigot_prism(cap_spigot_l + 0.1, 0, x_spigot);
      }
      // leading-end chamfer on the spigot: alignment on entry
      difference() {
        translate([x_spigot - 0.5, 0, -1])
          rrect(spigot_nose_len + 0.5, env_w + 4, env_h + 4, 2.0);
        hull() {
          spigot_prism(0.02, spigot_nose_cham, x_spigot);
          spigot_prism(0.02, 0, x_spigot + spigot_nose_len);
        }
      }
      // O-ring groove, near the FRONT of the spigot so the retention
      // screws (aft of it) never break the seal.
      // Cutter = (oversize prism) minus (spigot shrunk by the groove cut).
      difference() {
        translate([x_groove0, 0, -1]) rrect(groove_width, env_w + 4, env_h + 4, 2.0);
        spigot_prism(groove_width + 2, groove_cut, x_groove0 - 1);
      }
      // core out the cap, keeping solid pillars for the screw threads
      if (cap_lighten)
        difference() {
          intersection() {
            translate([x_core0, 0, -1])
              rrect(x_core1 - x_core0, env_w + 4, env_h + 4, 2.0);
            spigot_prism(cap_spigot_l + cap_plate_t + 2,
                         cap_core_t + groove_cut, x_spigot);
          }
          // screw pillars: M1.6 needs more than a 1.2 mm wall to thread into
          for (sgn = [-1, 1])
            translate([env_l - cap_plate_t - cap_screw_x_from_tail,
                       sgn * cap_screw_y, -1])
              cylinder(h = env_h + 2, d = 4.4);
        }
      cap_screw_holes(false);   // blind tapping holes in the spigot
    }
    // rib that pushes the board forward against the carrier assembly:
    // this is what locks the board in X and makes the IMU rigid.
    // It runs all the way aft into the cap's solid tail, THROUGH the
    // cored pocket, and doubles as the pocket's stiffening spine. It
    // has to: opening the pocket forward ate the slice of spigot the
    // rib used to hang off, and the rib became a loose second shell.
    // Its FACE is at x_cap_rib, which leaves preload_pad_t minus
    // preload_interf of gap to the board's aft edge. rev 1 put the face
    // 0.60 mm INSIDE the board and still specified a 1.0 mm pad.
    translate([x_cap_rib, -4.0, z_board_bot])
      cube([(x_core1 + cap_core_t + 0.5) - x_cap_rib,
            8.0, board_pcb_t + board_slot_slop], center = false);
  }
}

// ================================================================ PLUG
// Contamination cover, NOT the seal — the connector is sealed with or
// without it (that is the whole point of choosing a rear-sealed part,
// and the bucket test is run with the plug OFF to prove it). The plug
// keeps sand and salt off the contacts between sessions.
//
// REFERENCE GEOMETRY. The production answer is a stock silicone USB-C
// dust plug with a lanyard; this exists so the mould/TPU print has a
// shape and so the lanyard length is checked against the shell.
// rev 1 sized the bung to the PANEL HOLE (3.30) rather than to the
// receptacle mouth — 0.60 mm undersize, 18%, it would fall out on the
// first rinse — and routed the lanyard 27.7 mm SIDEWAYS, past the
// puck's own side wall, at a groove 44 mm away on a different axis.
mouth_w   = 8.34;   // [UJ31 p2] receptacle mouth, +0.06/-0.02
mouth_h   = 2.56;   // [UJ31 p2] receptacle mouth, +/-0.04
mouth_d   = 6.20;   // UNMEASURED — the datasheet does not dimension the
                    // cavity depth. 6.20 is the USB-C plug engagement.
plug_interf = 0.10; // silicone, light interference
tongue_t  = 0.75;   // UNMEASURED — clearance slot for the receptacle
tongue_w  = 6.60;   //   tongue, so the bung can actually enter
lanyard_len = tether_x + 4.0;   // reaches the tether groove, +X, and
                                // wraps. rev 1 shipped 26 mm pointed at
                                // the wrong axis.

module plug() {
  // bung, sized to the receptacle mouth with a light interference
  difference() {
    translate([0, 0, 0]) rotate([0, 90, 0])
      hull() for (y = [-(mouth_w + plug_interf - mouth_h)/2,
                        (mouth_w + plug_interf - mouth_h)/2])
        translate([0, y, 0])
          cylinder(h = mouth_d - 1.2, d = mouth_h + plug_interf, $fn = 24);
    // slot for the receptacle tongue
    translate([-0.01, -tongue_w/2, -tongue_t/2])
      cube([mouth_d - 2.2, tongue_w, tongue_t]);
  }
  // flange, seats in the port recess against the 0.90 land's outer face
  translate([mouth_d - 1.2, 0, 0]) rotate([0, 90, 0])
    hull() for (y = [-(port_recess_w - 4.0)/2, (port_recess_w - 4.0)/2])
      translate([0, y, 0]) cylinder(h = port_recess_d, d = port_recess_h - 1.0, $fn = 24);
  // lanyard, routed AFT along +X to the tether groove, ending in an eye
  translate([mouth_d - 1.2 + port_recess_d - 1.0, 0, 0]) rotate([0, 0, 0])
    linear_extrude(1.2, center = true) hull() {
      circle(d = 3.0, $fn = 20);
      translate([lanyard_len, 0]) circle(d = 3.4, $fn = 20);
    }
}

// ================================================ STAND-INS (view only)
module ghost_board() {
  color("green", 0.55)
    translate([x_board, -board_w/2, z_board_bot]) cube([board_l, board_w, board_pcb_t]);
  color("silver", 0.55)  // the XIAO's own (unused) USB-C receptacle
    translate([x_board, -4.6, z_board_top]) cube([7.5, 9.2, 3.2]);
}
module ghost_cell() {
  color("dimgray", 0.6)
    translate([x_cell, -cell_w_max/2, z_floor_in]) cube([cell_l_max, cell_w_max, cell_t_max]);
}
module ghost_conn() {
  color("gold", 0.8)
    translate([x_carrier, -carrier_w/2, z_carrier_bot]) cube([carrier_l, carrier_w, carrier_pcb_t]);
  color("lightsteelblue", 0.8)
    translate([x_nose_tip, -conn_body_w/2, z_conn_bot])
      cube([conn_body_l, conn_body_w, conn_offset + conn_centre_off + conn_hole_h_in/2]);
}

// ======================================================= FLOODED VOIDS
// Everything inside the outer envelope that is open to the sea when the
// puck is assembled and in the water. It does NOT displace, so it must
// come off the buoyancy sum. rev 1's displacement part subtracted only
// the tether groove and the orientation mark, and so over-counted by
// about 0.17 cm3 — 39% of the moulded-PC margin.
module flooded_voids() {
  // the port: recess, stepped hole, and the receptacle cavity behind it.
  // §11 test 2 exists precisely to prove the shell is watertight with
  // this open to the sea.
  port_cutout();
  translate([x_land_in - 0.01, 0, z_port_centre]) rotate([0, 90, 0])
    difference() {
      hull() for (y = [-(mouth_w - mouth_h)/2, (mouth_w - mouth_h)/2])
        translate([0, y, 0]) cylinder(h = mouth_d, d = mouth_h, $fn = 36);
      translate([-tongue_w/2, -tongue_t/2, 0.5]) cube([tongue_w, tongue_t, mouth_d]);
    }
  // the annulus behind the O-ring, open to the sea through the
  // cap-plate joint
  difference() {
    intersection() {
      bore_solid(0);
      translate([x_groove1, -env_w, -1])
        cube([x_bore_end - x_groove1, 2*env_w, env_h + 2]);
    }
    spigot_prism(cap_spigot_l + 2, 0, x_spigot);
  }
  // the two screw clearance holes and the unfilled tail of their
  // tapped holes
  cap_screw_holes(true);
}

// ================================================================ MAIN
module assembly() {
  color("gainsboro") body();
  color("lightgray")  cap();
  ghost_board(); ghost_cell(); ghost_conn();
}

if      (part == "body")     body();
else if (part == "cap")      cap();
else if (part == "plug")     plug();
else if (part == "assembly") assembly();
else if (part == "displacement")
  // The water-displacing envelope of the ASSEMBLED puck, for the
  // buoyancy sum. Not a printable part.
  difference() {
    rrect(env_l, env_w, env_h, 2.0);
    tether_groove(); orientation_mark(); flooded_voids();
  }
else if (part == "cutaway")
  difference() { assembly(); translate([-2, 0, -2]) cube([env_l + 4, env_w, env_h + 4]); }
else assembly();
