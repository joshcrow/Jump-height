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
//
// Instinct coverage (2026-08-12) spans all three layout tiers -- full/half/
// small -- using the device's own real per-slot obscurity from
// instinct3solar45mm's simulator.json "datafields" block, not guessed
// insets (see the block comment above the Instinct full-tier test below).

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

// ---- Instinct 3 Solar 45mm, full/half/small, ground-truthed against the
// real device -- NOT guessed. ~/Library/Application Support/Garmin/
// ConnectIQ/Devices/instinct3solar45mm/{compiler,simulator}.json say:
// displayType "mip", deviceFamily "semioctagon-176x176", display.shape
// "semi-octagon" (SCREEN_SHAPE_ROUND is false), resolution 176x176. The
// simulator.json "datafields" block (layouts[0]) is the device's own
// per-arrangement obscurity truth -- exactly how the epix2 numbers in
// testLayout_tiersMatchTheThreeSpecArrangements were sourced, below:
//   "1 Field"   176x176 obscurity=[left,top,right,bottom]   -> TIER_FULL
//   "2 Fields"    99x72 obscurity=[left,top]                -> TIER_HALF
//               176x104 obscurity=[left,right,bottom]       -> TIER_HALF
//   "3 Fields A"  99x55 obscurity=[left,top]                -> TIER_SMALL
//               176x54 obscurity=[left,right,bottom]        -> TIER_SMALL
//   "4 Fields"  110x27 obscurity=[]                         -> TIER_SMALL

(:test)
function testLayout_instinctFullTierEveryRowFitsInsideTheInsetBox(logger) {
    // The "1 Field" slot: 176x176, all four edges obscured at once. Every
    // header/sub/big/footer row must land inside the inset box, never the
    // naive 0..176 span -- the same invariant epixEveryRowFitsInsideTheCircle
    // proves for the round device, here for the insets path instead.
    var w = 176;
    var h = 176;
    var insetL = Layout.EDGE_INSET_PX;
    var insetR = Layout.EDGE_INSET_PX;
    var top = Layout.EDGE_INSET_PX;
    var bottom = h - Layout.EDGE_INSET_PX;
    var span = bottom - top;
    var midX = (insetL + (w - insetR)) / 2;
    var fracs = [Layout.HEADER_Y_FRAC, Layout.SUB_Y_FRAC,
                 Layout.BIG_Y_FRAC, Layout.FOOTER_Y_FRAC];
    for (var i = 0; i < fracs.size(); i += 1) {
        var y = Layout.rowY(top, span, fracs[i]);
        Test.assert(y >= top);
        Test.assert(y <= bottom);            // vertically on-glass
        var half = Layout.safeHalfWidth(w, h, y, insetL, insetR, false, w, true, true);
        Test.assertEqual(half, (w - insetL - insetR) / 2);  // non-round: nominal, every row
        Test.assert(midX - half >= insetL);        // never draws into the clip
        Test.assert(midX + half <= w - insetR);
    }
    return true;
}

(:test)
function testLayout_instinctHalfTierRowsFitTheirRealSlots(logger) {
    // The "2 Fields" arrangement: a narrow top slot (99x72, obscured only
    // left+top -- its right/bottom border the OTHER field, not the device
    // edge) above a full-width bottom slot (176x104, obscured left+right+
    // bottom -- its top borders the other field). Both are TIER_HALF.
    Test.assertEqual(Layout.tier(72), Layout.TIER_HALF);
    Test.assertEqual(Layout.tier(104), Layout.TIER_HALF);

    // Top slot: narrow (99 < screenW 176) AND non-round -- either fact alone
    // is reason enough to decline chord math; nominal must come back, same
    // as halfWidthFieldNeverGuesses proves for Epix's 207px bottom pair.
    var topHalf = Layout.safeHalfWidth(99, 72, 30,
        Layout.EDGE_INSET_PX, 0, false, 176, true, false);
    Test.assertEqual(topHalf, (99 - Layout.EDGE_INSET_PX) / 2);
    Test.assert(topHalf >= 0);

    // Bottom slot: full-width but still non-round -- insets alone decide
    // it. Rows mirror JumpFieldView._drawHalf's two real fracs (0.40 big
    // value, 0.85 bottom line).
    var w = 176;
    var h = 104;
    var insetL = Layout.EDGE_INSET_PX;
    var insetR = Layout.EDGE_INSET_PX;
    var midX = (insetL + (w - insetR)) / 2;
    var span = h - Layout.EDGE_INSET_PX;   // top not obscured, bottom is
    var rows = [Layout.rowY(0, span, 0.40f), Layout.rowY(0, span, 0.85f)];
    for (var i = 0; i < rows.size(); i += 1) {
        var half = Layout.safeHalfWidth(w, h, rows[i], insetL, insetR, false, w, false, true);
        Test.assertEqual(half, (w - insetL - insetR) / 2);
        Test.assert(midX - half >= insetL);
        Test.assert(midX + half <= w - insetR);
    }
    return true;
}

(:test)
function testLayout_instinctSmallTierRowFitsInsideTheInsetBox(logger) {
    // The "4 Fields" bottom bar: 110x27, obscurity=[] -- an internal strip
    // touching no device edge. 27 is the field's smallest real slot, well
    // under HALF_MIN_H (60): TIER_SMALL, and the floor dotRadius clamps to.
    var w = 110;
    var h = 27;
    Test.assertEqual(Layout.tier(h), Layout.TIER_SMALL);
    Test.assertEqual(Layout.dotRadius(h), 4);

    var half = Layout.safeHalfWidth(w, h, h / 2, 0, 0, false, 176, false, false);
    Test.assertEqual(half, w / 2);        // no insets on this slot: full nominal
    var midX = w / 2;
    Test.assert(midX - half >= 0);
    Test.assert(midX + half <= w);
    return true;
}

(:test)
function testLayout_instinctBigValueGroupStaysInsideItsBudget(logger) {
    // Same formula as testLayout_bigValueGroupStaysInsideItsBudget, plugged
    // with Instinct's own inset-derived full-tier half-width (78, from
    // EDGE_INSET_PX on both sides of the 176px field) and its own centre
    // (88, not Epix's 208) instead of the round device's chord.
    var half = 78;
    var unitW = 20;   // "ft"/"m" at Instinct's smaller secondary font
    var digitsMax = Layout.bigDigitsMax(half, 8, unitW);
    var extent = Layout.bigGroupExtent(88, digitsMax, unitW);
    Test.assert(extent[0] >= 88 - half);
    Test.assert(extent[1] <= 88 + half + 2);   // +2: integer-division slack
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
