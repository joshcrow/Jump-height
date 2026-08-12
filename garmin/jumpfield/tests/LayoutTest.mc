// LayoutTest.mc
//
// Golden geometry tests for Layout.mc against BOTH real device geometries:
//
//   epix2              416x416, ROUND, full-screen field has OBSCURE top+bottom
//   instinct3solar45mm 176x176, semi-octagon (NOT round), clipped corners
//                      arrive as obscurity insets instead of chord math
//
// The invariant under test is the one that actually shipped broken: every
// row's usable extent must stay inside the visible glass. On 2026-08-10 the
// header drew at x=20 on a circle whose chord at that height starts at
// x=133 — "Jump Height" rendered as "eight" and only a human eyeball caught
// it. These tests make that class of bug a red build instead.

import Toybox.Lang;
using Toybox.Test;
using Toybox.Math;

(:test)
function testLayout_epixHeaderChordIsTheRealConstraint(logger) {
    // Full-screen epix2 field: w=h=416, round, obscured top+bottom.
    var h = 416;
    var y = Layout.rowY(0, h, Layout.HEADER_Y_FRAC);   // 62
    var half = Layout.safeHalfWidth(416, h, y, 0, 0, true, 416, true, true);
    // Chord at dy=|62-208|=146 on r=208: sqrt(208^2-146^2)=~148 (minus margin).
    Test.assert(half > 100);
    Test.assert(half < 160);           // FAR less than the naive 208
    var left = 208 - half;
    Test.assert(left > 40);            // x=20 (the shipped bug) is OFF GLASS:
                                        // the chord's left edge starts far
                                        // right of it — this line is the
                                        // regression test for "eight"
    return true;
}

(:test)
function testLayout_epixEveryRowFitsInsideTheCircle(logger) {
    var h = 416;
    var r = 208.0;
    var fracs = [Layout.HEADER_Y_FRAC, Layout.SUB_Y_FRAC,
                 Layout.BIG_Y_FRAC, Layout.FOOTER_Y_FRAC];
    for (var i = 0; i < fracs.size(); i += 1) {
        var y = Layout.rowY(0, h, fracs[i]);
        var half = Layout.safeHalfWidth(416, h, y, 0, 0, true, 416, true, true);
        // The circle's own half-width at this row:
        var dy = (y - 208).abs();
        var trueHalf = Math.sqrt(r * r - dy * dy);
        Test.assert(half <= trueHalf);          // never claims more than exists
        Test.assert(half >= trueHalf - 6);      // never wastes more than margin
    }
    return true;
}

(:test)
function testLayout_halfWidthFieldNeverGuesses(logger) {
    // Epix's 207px-wide bottom-pair field: horizontal offset is unknowable,
    // so the chord math must decline and return the nominal width.
    var half = Layout.safeHalfWidth(207, 206, 30, 0, 0, true, 416, false, true);
    Test.assertEqual(half, 103);   // (207-0-0)/2 — nominal, no chord guess
    return true;
}

(:test)
function testLayout_instinctIsNotRoundSoInsetsRule(logger) {
    // Instinct: semi-octagon reported as non-round; the clipped corners come
    // in as obscurity insets. Chord math must stay OUT of the way and the
    // insets must be respected verbatim.
    var half = Layout.safeHalfWidth(176, 176, 26,
        Layout.EDGE_INSET_PX, Layout.EDGE_INSET_PX, false, 176, true, true);
    Test.assertEqual(half, (176 - 20) / 2);
    return true;
}

(:test)
function testLayout_midScreenStripUsesNominal(logger) {
    // A full-width strip touching neither top nor bottom sits mid-screen,
    // where the chord is within a few percent of full width.
    var half = Layout.safeHalfWidth(416, 132, 66, 0, 0, true, 416, false, false);
    Test.assertEqual(half, 208);
    return true;
}

(:test)
function testLayout_tiersMatchTheThreeSpecArrangements(logger) {
    // Real slot heights from the epix2 simulator.json: 416 (1-up), 207
    // (2-up), 132/146 (3-up), 103 (4-row), and Instinct's 176 full.
    Test.assertEqual(Layout.tier(416), Layout.TIER_FULL);
    Test.assertEqual(Layout.tier(207), Layout.TIER_FULL);
    Test.assertEqual(Layout.tier(176), Layout.TIER_FULL);
    Test.assertEqual(Layout.tier(146), Layout.TIER_FULL);
    Test.assertEqual(Layout.tier(132), Layout.TIER_FULL);
    Test.assertEqual(Layout.tier(103), Layout.TIER_HALF);
    Test.assertEqual(Layout.tier(59),  Layout.TIER_SMALL);
    return true;
}

(:test)
function testLayout_headerPlanNeverCollides(logger) {
    // Whatever widths arrive, name and count keep minGap of daylight and the
    // name's budget is never negative.
    var plan = Layout.headerPlan(60, 356, 10, 12, 26, 90);
    var nameX = plan[0]; var nameMax = plan[1]; var countRight = plan[2];
    Test.assert(nameX + nameMax + 26 + 90 <= countRight);
    // Pathological: a count wider than the row still yields a sane (0) budget.
    var tight = Layout.headerPlan(60, 200, 10, 12, 26, 400);
    Test.assertEqual(tight[1], 0);
    return true;
}

(:test)
function testLayout_bigValueGroupStaysInsideItsBudget(logger) {
    // The digits+unit GROUP, centred on midX, must fit the row that budgeted
    // it: bigDigitsMax hands out a digits budget, bigGroupExtent computes the
    // drawn extent — consistency between the two is the no-overflow proof.
    var half = 148;                       // epix BIG row chord half-width
    var unitW = 30;
    var digitsMax = Layout.bigDigitsMax(half, 8, unitW);
    var extent = Layout.bigGroupExtent(208, digitsMax, unitW);
    Test.assert(extent[0] >= 208 - half);
    Test.assert(extent[1] <= 208 + half + 2);   // +2: integer-division slack
    return true;
}

(:test)
function testLayout_dotRadiusScalesAndClamps(logger) {
    Test.assertEqual(Layout.dotRadius(416), 10);
    Test.assertEqual(Layout.dotRadius(176), 4);
    Test.assertEqual(Layout.dotRadius(59), 4);     // floor
    Test.assertEqual(Layout.dotRadius(2000), 12);  // ceiling
    return true;
}
