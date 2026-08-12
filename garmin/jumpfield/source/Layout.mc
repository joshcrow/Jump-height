// Layout.mc
//
// PURE layout geometry — every number that decides where something lands on
// the glass, extracted from JumpFieldView so it can be unit-tested with zero
// Graphics/WatchUi state (the Protocol.mc/Model.mc discipline, applied to
// pixels). JumpFieldView still does all drawing; this module only computes.
//
// Why this exists (2026-08-11, the first-sideload week): the original layout
// drew the header at x=20 on a 416px round Epix whose chord at that height
// starts at x=133 — "Jump Height" rendered as "eight", entirely off the
// glass, and nothing but a human eyeball could have caught it. These
// functions make that class of bug a TEST FAILURE instead: LayoutTest.mc
// asserts, for both real device geometries, that every row's text extent
// stays inside the visible glass.
//
// Scope note (Monkey C, learned on silicon): module-level functions called
// from a top-level class (JumpFieldView) resolve fine — that is exactly how
// Protocol.parseKV is used. What broke on-device was calling module helpers
// from a class NESTED IN the same module (Model.State -> Model._helper).
// Layout is consumed only from top-level classes and its own tests.

import Toybox.Lang;
using Toybox.Math;

module Layout {

    // Row placement as fractions of the field's height (spec §4.1's shape,
    // size-independent). Single source of truth — JumpFieldView reads these.
    const HEADER_Y_FRAC = 0.15f;
    const SUB_Y_FRAC    = 0.29f;
    const BIG_Y_FRAC    = 0.55f;
    const FOOTER_Y_FRAC = 0.88f;

    const EDGE_INSET_PX = 10;   // extra margin on an obscured (clipped) edge

    function rowY(top as Number, span as Number, frac as Float) as Number {
        return (top + span * frac).toNumber();
    }

    // Half of the horizontally usable width at local row `y` on a ROUND
    // display: the chord at vertical distance dy from the circle's centre is
    // 2*sqrt(R^2 - dy^2). Pure twin of the View's former _safeHalfWidth —
    // same rules:
    //   - non-round screens: the nominal half-width
    //   - a field that is not full-screen-width: nominal (its horizontal
    //     offset is unknowable, so guessing would be worse)
    //   - obscurity flags anchor the field against the circle: OBSCURE_TOP
    //     means local y IS absolute y; OBSCURE_BOTTOM mirrors; both means
    //     the field spans the screen; neither means a mid-screen strip
    //     where the chord is within a few percent of full width.
    //
    // isRound/screenW/atTop/atBottom arrive as plain values so no Toybox
    // device/System state is touched — the tests feed real device geometry.
    function safeHalfWidth(w as Number, h as Number, y as Number,
                           insetL as Number, insetR as Number,
                           isRound as Boolean, screenW as Number,
                           atTop as Boolean, atBottom as Boolean) as Number {
        var nominal = (w - insetL - insetR) / 2;
        if (!isRound) {
            return nominal;
        }
        if (w < screenW - 4) {
            return nominal;   // not full-width: position unknown, don't guess
        }
        var r = screenW / 2.0;
        var dy;
        if (atTop && atBottom) {
            dy = y - h / 2.0;
        } else if (atTop) {
            dy = y - r;
        } else if (atBottom) {
            dy = r - (h - y);
        } else {
            return nominal;
        }
        var inside = r * r - dy * dy;
        if (inside <= 1.0) {
            return 8;         // degenerate; keep something drawable
        }
        var chordHalf = Math.sqrt(inside).toNumber() - 4;
        return (chordHalf < nominal) ? chordHalf : nominal;
    }

    // Layout-tier decision (spec §4.1 full/half/small). Absolute on purpose:
    // "is there room for three rows" is a physical question.
    const FULL_MIN_H = 120;
    const HALF_MIN_H = 60;
    const TIER_FULL = 0;
    const TIER_HALF = 1;
    const TIER_SMALL = 2;

    function tier(h as Number) as Number {
        if (h >= FULL_MIN_H) { return TIER_FULL; }
        if (h >= HALF_MIN_H) { return TIER_HALF; }
        return TIER_SMALL;
    }

    // Dot radius scaled to field height, clamped to stay legible-but-modest.
    function dotRadius(h as Number) as Number {
        var r = h / 40;
        if (r < 4) { return 4; }
        if (r > 12) { return 12; }
        return r;
    }

    // Header geometry for the full tier, given measured text widths. Returns
    // [nameX, nameMaxWidth, countRightX] so the View can draw and the tests
    // can assert every extent is inside [hLeft, hRight].
    //
    // minGap is the enforced daylight between name and count — they are one
    // typographic row and must never collide (first Epix render did).
    function headerPlan(hLeft as Number, hRight as Number, dotR as Number,
                        gap as Number, minGap as Number,
                        countW as Number) as Array {
        var nameX = hLeft + dotR * 2 + gap;
        var nameMax = hRight - countW - minGap - nameX;
        if (nameMax < 0) { nameMax = 0; }
        return [nameX, nameMax, hRight];
    }

    // Width available to the DIGITS once the unit label sits beside them.
    //
    // The draw-time gap between digits and unit is digits-PROPORTIONAL
    // (digitsW/12 + 4, see bigGroupExtent/_drawBigValue), so the budget must
    // solve for it, not assume a constant:
    //     digits + digits/12 + 4 + unitW <= 2*half - pad
    //  => digits <= (2*half - pad - 4 - unitW) * 12/13
    // The first version budgeted a flat 10px and LayoutTest caught the
    // overflow immediately (group extent crossed the chord by 3px on wide
    // digits) — the whole reason this module has tests.
    function bigDigitsMax(halfWidth as Number, pad as Number,
                          unitW as Number) as Number {
        var avail = halfWidth * 2 - pad;
        if (unitW > 0) {
            var room = avail - 4 - unitW;
            if (room < 0) { room = 0; }
            return room * 12 / 13;
        }
        return (avail < 0) ? 0 : avail;
    }

    // The drawn extent [left, right] of the digits+unit group centred on cx —
    // mirrors JumpFieldView._drawBigValue's arithmetic exactly, so the tests
    // can prove the GROUP (not just the digits) stays on the glass.
    function bigGroupExtent(cx as Number, digitsW as Number,
                            unitW as Number) as Array {
        var gap = (unitW > 0) ? (digitsW / 12 + 4) : 0;
        var total = digitsW + gap + unitW;
        var x = cx - total / 2;
        return [x, x + total];
    }
}
