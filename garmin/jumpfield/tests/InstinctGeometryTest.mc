// InstinctGeometryTest.mc
//
// WHAT THIS IS FOR
// ----------------
// The Instinct 3 Solar is now the product's only screen on the water (owner
// decision 2026-08-20 — the brother rides). Its layout tiers have never been
// seen by a human on the real device, and the simulator screenshot path is
// blocked by a macOS Screen Recording permission this session cannot grant.
//
// So instead of pixels, this prints the EXACT geometry the real Layout code
// computes at the Instinct's real dimensions, for every tier. Two uses:
//
//   1. Tonight, hold the photographs next to these numbers. A mismatch means
//      the device disagrees with the code — far more diagnostic than "it
//      looked a bit off".
//   2. It is deterministic and reproducible, so it can be re-run after any
//      layout change without a watch or a screenshot.
//
// WHY ALL THREE TIERS MATTER ON THIS WATCH. The tier is chosen by the field's
// HEIGHT (Layout.tier: >=120 full, >=60 half, else small), and the height is
// decided by how many data fields the rider puts on the screen. The brother
// picks that himself, so all three are reachable:
//   1 field  -> ~176 px -> FULL
//   2 fields -> ~88 px  -> HALF
//   3+ fields-> ~59 px  -> SMALL
// If he builds a 4-field screen, our field renders SMALL and nobody has ever
// looked at SMALL on this device.
//
// This test ASSERTS the invariants that would ruin a session (text off-screen,
// a tier boundary landing wrong) and PRINTS the rest for eyeball comparison.

using Toybox.Test;
import Toybox.Lang;

// Instinct 3 Solar 45mm: 176x176 MIP, semi-octagon (NOT round — Layout's
// round-screen chord math must not apply here; JumpFieldView branches on
// screenShape).
const INSTINCT_W = 176;
const INSTINCT_H = 176;

function describeTier(t as Number) as String {
    if (t == Layout.TIER_FULL)  { return "FULL"; }
    if (t == Layout.TIER_HALF)  { return "HALF"; }
    return "SMALL";
}

(:test)
function testGeometry_instinctAllTiers(logger) as Boolean {
    // Heights the rider can actually produce by choosing a data-screen layout.
    var heights = [176, 88, 59];
    var labels  = ["1 field (full screen)", "2 fields (half)", "3+ fields (small)"];

    for (var i = 0; i < heights.size(); i += 1) {
        var h = heights[i];
        var t = Layout.tier(h);
        var dotR = Layout.dotRadius(h);

        var yHeader = Layout.rowY(0, h, Layout.HEADER_Y_FRAC);
        var ySub    = Layout.rowY(0, h, Layout.SUB_Y_FRAC);
        var yBig    = Layout.rowY(0, h, Layout.BIG_Y_FRAC);
        var yFooter = Layout.rowY(0, h, Layout.FOOTER_Y_FRAC);

        logger.debug("--- " + labels[i] + "  h=" + h + " -> " + describeTier(t));
        logger.debug("    dotRadius=" + dotR);
        logger.debug("    rows: header y=" + yHeader + "  sub y=" + ySub +
                     "  big y=" + yBig + "  footer y=" + yFooter);

        // INVARIANTS — these are the ones that ruin a session, not cosmetics.
        // Every row must land inside the field's own box.
        Test.assert(yHeader >= 0 && yHeader < h);
        Test.assert(ySub    >= 0 && ySub    < h);
        Test.assert(yBig    >= 0 && yBig    < h);
        Test.assert(yFooter >= 0 && yFooter < h);
        // Rows must stay in reading order, or the layout is scrambled.
        Test.assert(yHeader < ySub);
        Test.assert(ySub    < yBig);
        Test.assert(yBig    < yFooter);
        // A dot that outgrows its row is the classic overlap bug.
        Test.assert(dotR >= 0 && dotR * 2 < h / 4);
    }
    return true;
}

(:test)
function testGeometry_instinctTierBoundariesAreWhereWeThink(logger) as Boolean {
    // The exact heights at which the rider's screen choice flips our tier.
    // Pinning these means a layout refactor cannot silently move the boundary
    // and change what he sees mid-season.
    Test.assert(Layout.tier(120) == Layout.TIER_FULL);
    Test.assert(Layout.tier(119) == Layout.TIER_HALF);
    Test.assert(Layout.tier(60)  == Layout.TIER_HALF);
    Test.assert(Layout.tier(59)  == Layout.TIER_SMALL);

    // And the practical ones for a 176 px screen.
    logger.debug("176 px (1 field)  -> " + describeTier(Layout.tier(176)));
    logger.debug(" 88 px (2 fields) -> " + describeTier(Layout.tier(88)));
    logger.debug(" 59 px (3 fields) -> " + describeTier(Layout.tier(59)));
    Test.assert(Layout.tier(176) == Layout.TIER_FULL);
    Test.assert(Layout.tier(88)  == Layout.TIER_HALF);
    Test.assert(Layout.tier(59)  == Layout.TIER_SMALL);
    return true;
}

(:test)
function testGeometry_instinctIsNotTreatedAsRound(logger) as Boolean {
    // The Instinct's screen is a semi-octagon. Layout.safeHalfWidth applies
    // chord math for round screens; applying it here would needlessly narrow
    // every row. JumpFieldView branches on screenShape, so this test pins the
    // NON-round expectation: at mid-screen the usable half-width is the full
    // nominal half-width, not a chord-reduced one.
    var half = Layout.safeHalfWidth(INSTINCT_W, INSTINCT_H, INSTINCT_H / 2,
                                    0, 0,                 // insetL, insetR
                                    false,                // isRound
                                    INSTINCT_W,           // screenW
                                    false, false);        // atTop, atBottom
    logger.debug("non-round safeHalfWidth at mid-screen: " + half +
                 " (nominal " + (INSTINCT_W / 2) + ")");
    Test.assert(half == INSTINCT_W / 2);
    return true;
}
