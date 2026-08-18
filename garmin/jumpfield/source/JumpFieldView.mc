// ERROR HANDLING NOTE (2026-08-14): every catch in this file is a BARE
// `catch (ex)`, never `catch (ex instanceof Lang.Exception)`. Connect IQ
// raises errors that are NOT Lang.Exception; a filtered catch lets those
// escape and kills the entire data field. This project has been bitten
// twice on silicon — utf8ArrayToString throwing 'Unexpected Type Error' on
// the first notification, and the first corruption gate throwing 'System
// Error: Failed invoking <symbol>' on the first line received — both with
// every simulator test green. On an unproven watch this is the difference
// between a field that degrades and a field that dies behind a splash.
// JumpFieldView.mc
//
// The DataField itself: owns Model/PuckLink/FitOut (the whole live object
// graph — spec's directory layout keeps their LOGIC in separate files, but
// something has to wire them together, and "the view that's alive as long
// as the field is installed on a screen" is the natural owner). Draws the
// three layouts of spec §4.1 by probing dc dimensions each onUpdate() (which
// of full/half/small slot we're in) and getObscurityFlags() (how far to
// inset from a clipped corner on the semi-octagon Instinct display, spec
// §5.1) — DC primitives only, no layout XML, no bitmaps (spec §5.6).
//
// Monochrome MIP has no dimming (spec §5.1): RECONNECTING does NOT gray the
// numbers out — it retains them at full contrast and relies on the hollow
// dot + "reconnecting" sub-text to carry the distinction. The new-jump
// flash is a true region INVERT (fill in foreground, draw the text in
// background), which spec says "MIP renders beautifully".
//
// compute() drives PuckLink.poll() and the vibrate/FIT-record trigger
// (never onUpdate(), which may not run at all while a different data
// screen is on-glass — see PuckLink.mc's header and FIRST_COMPILE.md).

using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.System;
using Toybox.Application;
using Toybox.Attention;
using Toybox.Math;
import Toybox.Lang;

class JumpFieldView extends WatchUi.DataField {

    // UI states (spec §4.2) — derived each draw from PuckLink.state() +
    // Model.hasData(), never stored: there is no BLE-awareness inside Model,
    // and no data-awareness inside PuckLink, on purpose (see Model.mc).
    const UI_SEARCHING = 0;
    const UI_CONNECTED = 1;
    const UI_RECONNECTING = 2;
    const UI_NO_BLE = 3;

    // Geometry constants and math live in Layout.mc (pure, unit-tested —
    // see LayoutTest.mc). This class draws; Layout computes.


    hidden var _model;
    hidden var _puckLink;
    hidden var _fitOut;          // null if FitContributor setup ever throws
    hidden var _puckName as String;
    hidden var _linkStarted = false;

    function initialize() {
        DataField.initialize();
        _model = new Model.State();
        _puckName = _readPuckName();
        _puckLink = new PuckLink(_model, _puckName);

        // FIT fields need the resolved display unit up front (spec §5.5's
        // units metadata is fixed at createField() time); a mid-session
        // system-unit change is an accepted, ignored edge case (UnitsFmt.mc).
        var feet = UnitsFmt.isFeet(_readUnitOverride());
        try {
            _fitOut = new FitOut(self, UnitsFmt.unitLabel(feet));
        } catch (ex) {
            _fitOut = null;  // FIT enrichment is a nicety (US4); the live
                              // glance (US1-US3) must not depend on it
        }
    }

    // ---- lifecycle, wired from JumpFieldApp.onStart()/onStop() ----

    function onAppStart() as Void {
        _ensureLinkStarted();
    }

    function onAppStop() as Void {
        _puckLink.stop();
        _linkStarted = false;  // a later activity start re-registers and rescans
    }

    // Idempotent, and called from BOTH the app lifecycle and compute().
    //
    // The app-lifecycle route alone is not trustworthy: onStart() fires before
    // getInitialView() exists (proven — see JumpFieldApp.mc), so the original
    // wiring never started the radio at all. Starting from getInitialView()
    // fixes that but does BLE registration during view construction, which is
    // a poor place for it. compute() is the honest answer: it is the field's
    // guaranteed ~1 Hz clock, it only runs once the field is genuinely live in
    // an activity, and reaching it means the whole object graph is built.
    // The guard makes the extra call free.
    hidden function _ensureLinkStarted() as Void {
        if (!_linkStarted) {
            _linkStarted = true;
            _puckLink.start();
        }
    }

    // ---- DataField overrides ----

    // info (Activity.Info) is intentionally untouched -- our data comes from
    // the puck over BLE, never from the activity/GPS/sensors Garmin already
    // records. Called ~1 Hz regardless of which data screen is on-glass
    // (see PuckLink.mc's header) -- this is the field's only clock.
    function compute(info) {
        _ensureLinkStarted();
        _puckLink.poll();

        var feet = UnitsFmt.isFeet(_readUnitOverride());

        // GUARD: only write SESSION fields once we actually have data.
        // FIT SESSION developer fields are last-write-wins at save time, and a
        // mid-activity restart (OOM, an uncaught error, a watch reboot) builds
        // a FRESH Model whose counters are all zero — which would then
        // overwrite a good pre-restart summary with 0/0.0/0.0 within one
        // second. best_airtime can never be recovered afterwards: there is no
        // wire field for it, so it exists only in this watch's own running max.
        // Two lines turn "a restart permanently zeroes the summary" into "a
        // restart keeps the last good summary until the puck reseeds".
        if (_fitOut != null && _model.hasData()) {
            _fitOut.updateSession(
                _model.jumpCount(),
                UnitsFmt.heightValue(_model.sessionBestM(), feet),
                _model.bestAirtimeS());
        }

        if (_model.consumeNewJump()) {
            if (_fitOut != null) {
                _fitOut.recordJump(UnitsFmt.heightValue(_model.lastHeightM(), feet));
            }
            _maybeVibrate();
        }

        return null;  // we draw everything ourselves in onUpdate(); nothing
                       // is bound through a layout value
    }

    function onUpdate(dc) as Void {
        var bg = getBackgroundColor();
        var fg = (bg == Graphics.COLOR_BLACK) ? Graphics.COLOR_WHITE : Graphics.COLOR_BLACK;
        dc.setColor(fg, bg);
        dc.clear();

        var w = dc.getWidth();
        var h = dc.getHeight();
        var insets = _edgeInsets();
        var feet = UnitsFmt.isFeet(_readUnitOverride());
        var uiState = _uiState();

        var t = Layout.tier(h);
        if (t == Layout.TIER_FULL) {
            _drawFull(dc, w, h, insets, fg, bg, feet, uiState);
        } else if (t == Layout.TIER_HALF) {
            _drawHalf(dc, w, h, insets, fg, bg, feet, uiState);
        } else {
            _drawSmall(dc, w, h, insets, fg, bg, feet, uiState);
        }
    }

    // ---------------------------------------------------------------- layouts

    // ┌────────────────────────────┐
    // │ ● JumpHeight        3 jumps│   header: state dot + count
    // │         4.2 ft             │   LAST JUMP — largest font that fits
    // │   best 5.1 ft · air 1.02s  │   footer row
    // └────────────────────────────┘
    hidden function _drawFull(dc, w, h, insets, fg, bg, feet, uiState) as Void {
        var top = insets[0];
        var bottom = h - insets[1];
        var span = bottom - top;
        var midX = (insets[2] + (w - insets[3])) / 2;

        var labelFont = _labelFont(h);
        var dotR = _dotRadius(h);
        var gap = h / 40 + 2;  // dot-to-text gap, ~12px on Epix, ~6 on Instinct

        // Header. On a round display the usable width at this row is the
        // circle's CHORD, not w — at 15% of a 416px full-screen field that is
        // ~300px, not 416, which is exactly what clipped "Jump Height" down to
        // "eight" on the first Epix sideload.
        var headerY = (top + span * Layout.HEADER_Y_FRAC).toNumber();
        var hHalf = _safeHalfWidth(w, h, headerY, insets);
        var hLeft = midX - hHalf;
        var hRight = midX + hHalf;

        // Name and count are ONE typographic row: size them together, or they
        // render at different fonts and collide (which is exactly what the
        // first Epix render did). The header is secondary information — it
        // deliberately starts a tier below the status line so the glanceable
        // thing on the glass is the state, not the puck's name.
        var countText = _model.jumpCount().toString() + " jumps";
        // Corruption tally, P1 "show its own health": every line the gates
        // dropped, on the glass. The whole integrity design rests on "a
        // rejected line is dropped and counted, never rendered" — this is
        // where the COUNT becomes visible instead of dying in a hidden var.
        // Silent-when-zero: the marker appearing at all is the news.
        if (_model.rejectedCount() > 0) {
            countText += " !" + _model.rejectedCount().toString();
        }
        var nameX = hLeft + dotR * 2 + gap;
        var minGap = w / 16;
        var headerFont = _fitPair(dc, _puckName, countText,
            hRight - nameX - minGap, _fontLadder(_secondaryFont(h)));
        var countW = dc.getTextDimensions(countText, headerFont)[0];
        var nameMax = hRight - countW - minGap - nameX;

        _drawDot(dc, hLeft + dotR, headerY, dotR, uiState, fg, bg);
        dc.setColor(fg, bg);
        _drawVC(dc, nameX, headerY, headerFont,
            _ellipsize(dc, _puckName, headerFont, nameMax),
            Graphics.TEXT_JUSTIFY_LEFT, fg);
        _drawVC(dc, hRight, headerY, headerFont, countText,
            Graphics.TEXT_JUSTIFY_RIGHT, fg);

        var subText = _subText(uiState);
        if (!subText.equals("")) {
            var subY = (top + span * Layout.SUB_Y_FRAC).toNumber();
            var subFont = _fitFont(dc, subText,
                _safeHalfWidth(w, h, subY, insets) * 2 - 8, _fontLadder(labelFont));
            _drawVC(dc, midX, subY, subFont, subText, Graphics.TEXT_JUSTIFY_CENTER, fg);
        }

        // Dc coordinates want Numbers, not Floats.
        var bigY = (top + span * Layout.BIG_Y_FRAC).toNumber();
        var bigText = _bigNumberText(uiState, feet);
        var unitText = _bigUnitText(uiState, feet);
        var unitFont = _secondaryFont(h);
        var fonts = [Graphics.FONT_NUMBER_THAI_HOT, Graphics.FONT_NUMBER_HOT,
                     Graphics.FONT_NUMBER_MEDIUM, Graphics.FONT_NUMBER_MILD];
        var unitW = unitText.equals("") ? 0 : dc.getTextDimensions(unitText, unitFont)[0];
        var bigAvail = Layout.bigDigitsMax(_safeHalfWidth(w, h, bigY, insets), 8, unitW);
        var font = _fitFont(dc, bigText, bigAvail, fonts);
        _drawBigValue(dc, midX, bigY, bigText, unitText, font, unitFont, fg, bg,
            uiState == UI_CONNECTED && _model.isFlashing());

        if (uiState == UI_CONNECTED || uiState == UI_RECONNECTING) {
            var footerY = (top + span * Layout.FOOTER_Y_FRAC).toNumber();
            var footer = "best " + UnitsFmt.formatHeight(_model.sessionBestM(), feet)
                + " . air " + UnitsFmt.formatAirtime(_model.lastAirtimeS());
            var footFont = _fitFont(dc, footer,
                _safeHalfWidth(w, h, footerY, insets) * 2 - 8, _fontLadder(_secondaryFont(h)));
            dc.setColor(fg, bg);
            _drawVC(dc, midX, footerY, footFont, footer, Graphics.TEXT_JUSTIFY_CENTER, fg);
        }
    }

    // │       4.2 ft         │        last, large
    // │  ^5.1        n3   ●  │        best (^) . count . state dot
    //
    // Uses "^" rather than spec §4.1's "▲" glyph: Garmin's system fonts on a
    // monochrome MIP are not confirmed to include that Unicode glyph, and a
    // missing glyph is a worse failure than a plain caret -- see
    // FIRST_COMPILE.md. Meaning is unchanged ("best" marker).
    hidden function _drawHalf(dc, w, h, insets, fg, bg, feet, uiState) as Void {
        var left = insets[2];
        var right = w - insets[3];
        var top = insets[0];
        var bottom = h - insets[1];
        var midX = (left + right) / 2;

        var span = bottom - top;
        var labelFont = _labelFont(h);
        var dotR = _dotRadius(h);

        var bigText = _bigNumberText(uiState, feet);
        var unitText = _bigUnitText(uiState, feet);
        var unitFont = _secondaryFont(h);
        var bigY = (top + span * 0.40).toNumber();
        var fonts = [Graphics.FONT_NUMBER_HOT, Graphics.FONT_NUMBER_MEDIUM,
                     Graphics.FONT_NUMBER_MILD, Graphics.FONT_SMALL];
        var unitW = unitText.equals("") ? 0 : dc.getTextDimensions(unitText, unitFont)[0];
        var bigAvail = Layout.bigDigitsMax(_safeHalfWidth(w, h, bigY, insets), 6, unitW);
        var font = _fitFont(dc, bigText, bigAvail, fonts);
        _drawBigValue(dc, midX, bigY, bigText, unitText, font, unitFont, fg, bg,
            uiState == UI_CONNECTED && _model.isFlashing());

        // Bottom row rides the same chord rule as _drawFull's footer: on a
        // round face the last row is the narrowest line on the glass.
        var rowY = (top + span * 0.85).toNumber();
        var rHalf = _safeHalfWidth(w, h, rowY, insets);
        var rLeft = midX - rHalf;
        var rRight = midX + rHalf;

        dc.setColor(fg, bg);
        if (uiState == UI_CONNECTED || uiState == UI_RECONNECTING) {
            _drawVC(dc, rLeft, rowY, labelFont, "^" + UnitsFmt.formatHeight(_model.sessionBestM(), feet),
                Graphics.TEXT_JUSTIFY_LEFT, fg);
            _drawVC(dc, midX, rowY, labelFont, "n" + _model.jumpCount().toString(),
                Graphics.TEXT_JUSTIFY_CENTER, fg);
        } else {
            var subText = _subText(uiState);
            var subFont = _fitFont(dc, subText, rHalf * 2 - dotR * 3, [labelFont, Graphics.FONT_XTINY]);
            _drawVC(dc, rLeft, rowY, subFont, subText, Graphics.TEXT_JUSTIFY_LEFT, fg);
        }
        _drawDot(dc, rRight - dotR, rowY, dotR, uiState, fg, bg);
    }

    // `4.2^5.1` + state dot. Nothing else (spec §4.1's quarter slot).
    hidden function _drawSmall(dc, w, h, insets, fg, bg, feet, uiState) as Void {
        var left = insets[2];
        var right = w - insets[3];
        var top = insets[0];
        var bottom = h - insets[1];
        var midX = (left + right) / 2;
        var midY = (top + bottom) / 2;

        var text;
        if (uiState == UI_SEARCHING || uiState == UI_NO_BLE) {
            text = "--";
        } else {
            text = UnitsFmt.formatHeight(_model.lastHeightM(), feet) + "^"
                + UnitsFmt.formatHeight(_model.sessionBestM(), feet);
        }
        var fonts = [Graphics.FONT_SMALL, Graphics.FONT_XTINY];
        var font = _fitFont(dc, text, right - left - 14, fonts);
        _drawInvertible(dc, midX - 6, midY, text, font, fg, bg, uiState == UI_CONNECTED && _model.isFlashing());
        _drawDot(dc, right - 6, top + 6, 4, uiState, fg, bg);
    }

    // ---------------------------------------------------------------- helpers

    hidden function _uiState() as Number {
        var s = _puckLink.state();
        if (s == PuckLink.STATE_DEAD) {
            return UI_NO_BLE;
        }
        if (s == PuckLink.STATE_LIVE) {
            return UI_CONNECTED;
        }
        return _model.hasData() ? UI_RECONNECTING : UI_SEARCHING;
    }

    hidden function _subText(uiState as Number) as String {
        if (uiState == UI_SEARCHING) { return "finding puck"; }
        if (uiState == UI_RECONNECTING) { return "reconnecting"; }
        if (uiState == UI_NO_BLE) { return "BLE unavailable"; }
        // Connected: the puck's battery, if it reports one (Sense-class
        // firmware only — docs/sense.md §3.4: the puck is sealed, this line
        // and the phone are its only gauges). Doubles as live-link proof.
        var bp = _model.puckBattPct();
        if (bp != null) {
            if (_model.puckCharging()) { return "puck charging"; }
            return "puck " + bp + "%";
        }
        return "";
    }

    // NO BLE always shows placeholders, even if data was retained from
    // earlier in the activity (spec §4.2 table) -- unlike RECONNECTING,
    // there is no path back this session, so stale numbers would mislead
    // indefinitely rather than briefly.
    hidden function _bigNumberText(uiState as Number, feet as Boolean) as String {
        if (uiState == UI_SEARCHING || uiState == UI_NO_BLE) {
            return "--";
        }
        return UnitsFmt.heightDigits(_model.lastHeightM(), feet);
    }

    // The unit is drawn separately, in a text font — see UnitsFmt.heightDigits.
    // Placeholders carry no unit: "-- ft" claims a measurement we don't have.
    hidden function _bigUnitText(uiState as Number, feet as Boolean) as String {
        if (uiState == UI_SEARCHING || uiState == UI_NO_BLE) {
            return "";
        }
        return UnitsFmt.unitLabel(feet);
    }

    // Draws the big value as a GROUP — digits in a FONT_NUMBER_* face, unit in
    // a text face beside it, the pair centred together on cx.
    hidden function _drawBigValue(dc, cx, cy, digits as String, unit as String,
                                  numFont, unitFont, fg, bg, flashing as Boolean) as Void {
        var nd = dc.getTextDimensions(digits, numFont);
        var ud = null;
        var uw = 0;
        if (!unit.equals("")) {
            ud = dc.getTextDimensions(unit, unitFont);
            uw = ud[0];
        }
        // Geometry from Layout so the draw can never drift from the tested
        // math (LayoutTest proves the extent stays inside the row's budget).
        var extent = Layout.bigGroupExtent(cx, nd[0], uw);
        var x = extent[0];
        var gap = (uw > 0) ? (nd[0] / 12 + 4) : 0;
        var total = extent[1] - extent[0];

        var textColor = fg;
        if (flashing) {
            var pad = 6;
            dc.setColor(fg, fg);
            dc.fillRectangle(x - pad, cy - nd[1] / 2 - pad / 2, total + pad * 2, nd[1] + pad);
            textColor = bg;  // text reads out of the inverted block
        }
        _drawVC(dc, x, cy, numFont, digits, Graphics.TEXT_JUSTIFY_LEFT, textColor);
        if (ud != null) {
            // Sit the unit low against the digits rather than centred on them:
            // a short lowercase word centred against tall digits reads as
            // floating in the middle of the number.
            var unitY = cy + nd[1] / 2 - ud[1] / 2 - nd[1] / 8;
            _drawVC(dc, x + nd[0] + gap, unitY, unitFont, unit,
                Graphics.TEXT_JUSTIFY_LEFT, textColor);
        }
        dc.setColor(fg, bg);
    }

    hidden function _drawDot(dc, cx, cy, r, uiState as Number, fg, bg) as Void {
        dc.setColor(fg, bg);
        dc.setPenWidth(2);
        if (uiState == UI_CONNECTED) {
            dc.fillCircle(cx, cy, r);
        } else if (uiState == UI_NO_BLE) {
            dc.drawLine(cx - r, cy - r, cx + r, cy + r);
            dc.drawLine(cx - r, cy + r, cx + r, cy - r);
        } else {
            dc.drawCircle(cx, cy, r);  // hollow: SEARCHING or RECONNECTING
        }
    }

    // Draws text either normally, or -- for the ~5 s after a new JUMP --
    // as a true inverted region (fill in fg, text in bg): spec §4.2's flash,
    // and spec §5.1's note that MIP renders inversion "beautifully" instead
    // of the dimming this display can't do.
    hidden function _drawInvertible(dc, cx, cy, text as String, font, fg, bg, flashing as Boolean) as Void {
        if (flashing) {
            var dims = dc.getTextDimensions(text, font);
            var pad = 6;
            dc.setColor(fg, fg);
            dc.fillRectangle(cx - dims[0] / 2 - pad, cy - dims[1] / 2 - pad / 2,
                dims[0] + pad * 2, dims[1] + pad);
            dc.setColor(bg, fg);
            _drawVC(dc, cx, cy, font, text, Graphics.TEXT_JUSTIFY_CENTER, bg);
            dc.setColor(fg, bg);
        } else {
            _drawVC(dc, cx, cy, font, text, Graphics.TEXT_JUSTIFY_CENTER, fg);
        }
    }

    // Vertically-centred drawText, with a transparent cell background.
    //
    // The transparency is the load-bearing part; the manual centring is just
    // tidiness (TEXT_JUSTIFY_VCENTER was tested and does NOT clip descenders —
    // that was a wrong first guess at the "g" bug, disproven by drawing the
    // same string three ways in one frame).
    hidden function _drawVC(dc, x, y, font, text as String, hJust, textColor) as Void {
        // COLOR_TRANSPARENT is the whole point. Garmin's drawText paints the
        // glyph cell's BACKGROUND with the current background colour, so a
        // later row's cell silently ERASES whatever an earlier row put there.
        // The big number's cell overlaps the bottom of the sub-text row, and
        // its background fill was wiping out exactly the descenders — that is
        // what cut the "g" in "finding puck" on the real Epix. Nothing was
        // clipped and nothing was mispositioned; a neighbour painted over it.
        dc.setColor(textColor, Graphics.COLOR_TRANSPARENT);
        dc.drawText(x, y - Graphics.getFontHeight(font) / 2, font, text, hJust);
    }

    // Half of the horizontally usable width at local row `y`, honouring a
    // ROUND display's curvature: the chord at vertical distance dy from the
    // circle's centre is 2*sqrt(R^2 - dy^2), which near the top or bottom of
    // the glass is dramatically less than the field's nominal width.
    //
    // We can only place a row against the circle when we know where the field
    // sits on the screen, and the obscurity flags tell us exactly that: an
    // OBSCURE_TOP field's top edge IS the screen's top edge, so local y is
    // absolute y (mirrored for OBSCURE_BOTTOM). A strip touching neither is
    // mid-screen, where the chord is within a few percent of full width and
    // the nominal half-width is the honest answer.
    //
    // Only applied to full-width fields — a half-width field (Epix's 207px
    // bottom pair) is horizontally offset by an amount we cannot query, and
    // guessing would be worse than the small over-estimate of using w/2.
    hidden function _safeHalfWidth(w, h, y, insets) as Number {
        // Pure math in Layout.safeHalfWidth (unit-tested); this wrapper only
        // gathers the device facts it needs.
        var settings = System.getDeviceSettings();
        var isRound = (settings.screenShape == System.SCREEN_SHAPE_ROUND);
        var flags = _obscurityFlags();
        return Layout.safeHalfWidth(w, h, y, insets[2], insets[3],
            isRound, settings.screenWidth,
            (flags & WatchUi.DataField.OBSCURE_TOP) != 0,
            (flags & WatchUi.DataField.OBSCURE_BOTTOM) != 0);
    }

    // Label/sub-text font scaled to the field's height. FONT_XTINY on a 416px
    // Epix field is a rumour, not a readable line.
    hidden function _labelFont(h) as Number {
        if (h >= 300) { return Graphics.FONT_MEDIUM; }
        if (h >= 180) { return Graphics.FONT_SMALL; }
        if (h >= 110) { return Graphics.FONT_TINY; }
        return Graphics.FONT_XTINY;
    }

    // One tier below _labelFont: header/footer metadata, not the glance line.
    hidden function _secondaryFont(h) as Number {
        if (h >= 300) { return Graphics.FONT_SMALL; }
        if (h >= 180) { return Graphics.FONT_TINY; }
        return Graphics.FONT_XTINY;
    }

    // Descending ladder starting at `top`, so _fitFont/_fitPair can step down.
    hidden function _fontLadder(top as Number) as Array {
        var all = [Graphics.FONT_MEDIUM, Graphics.FONT_SMALL,
                   Graphics.FONT_TINY, Graphics.FONT_XTINY];
        var out = [];
        var started = false;
        for (var i = 0; i < all.size(); i += 1) {
            if (all[i] == top) { started = true; }
            if (started) { out.add(all[i]); }
        }
        return started ? out : [Graphics.FONT_XTINY];
    }

    // Largest font at which BOTH strings fit side by side in maxWidth. Sizing
    // them separately lets a short string keep a big font while its neighbour
    // shrinks, which reads as a typo rather than a hierarchy.
    hidden function _fitPair(dc, a as String, b as String, maxWidth as Number, fonts as Array) as Number {
        for (var i = 0; i < fonts.size(); i += 1) {
            var wa = dc.getTextDimensions(a, fonts[i])[0];
            var wb = dc.getTextDimensions(b, fonts[i])[0];
            if (wa + wb <= maxWidth) {
                return fonts[i];
            }
        }
        return fonts[fonts.size() - 1];
    }

    hidden function _dotRadius(h) as Number {
        return Layout.dotRadius(h);
    }

    // Trim with a trailing "." until it fits: a long puck name should lose its
    // tail visibly rather than run off the round edge mid-word (which is how
    // "Jump Height" became "eight" on the Epix).
    hidden function _ellipsize(dc, text as String, font, maxWidth as Number) as String {
        if (maxWidth <= 0) { return ""; }
        if (dc.getTextDimensions(text, font)[0] <= maxWidth) { return text; }
        var s = text;
        while (s.length() > 1) {
            s = s.substring(0, s.length() - 1);
            if (dc.getTextDimensions(s + ".", font)[0] <= maxWidth) {
                return s + ".";
            }
        }
        return "";
    }

    // Largest font (by array order, biggest first) whose rendered width
    // fits maxWidth -- spec §4.1's "largest font that fits" literally.
    hidden function _fitFont(dc, text as String, maxWidth as Number, fonts as Array) as Number {
        for (var i = 0; i < fonts.size(); i += 1) {
            var dims = dc.getTextDimensions(text, fonts[i]);
            if (dims[0] <= maxWidth) {
                return fonts[i];
            }
        }
        return fonts[fonts.size() - 1];
    }

    // [top, bottom, left, right] pixel insets so nothing sits in a clipped
    // corner on the semi-octagon Instinct display (spec §5.1). Only valid to
    // read getObscurityFlags() during onUpdate() (per the DataField docs),
    // so this must be called from there, never from compute().
    // Only valid to read during onUpdate() (per the DataField docs), which is
    // the only place either caller runs.
    hidden function _obscurityFlags() as Number {
        try {
            return getObscurityFlags();
        } catch (ex) {
            return 0;
        }
    }

    hidden function _edgeInsets() as Array {
        var flags = _obscurityFlags();
        var top = 0;
        var bottom = 0;
        var left = 0;
        var right = 0;
        if ((flags & WatchUi.DataField.OBSCURE_TOP) != 0) { top = Layout.EDGE_INSET_PX; }
        if ((flags & WatchUi.DataField.OBSCURE_BOTTOM) != 0) { bottom = Layout.EDGE_INSET_PX; }
        if ((flags & WatchUi.DataField.OBSCURE_LEFT) != 0) { left = Layout.EDGE_INSET_PX; }
        if ((flags & WatchUi.DataField.OBSCURE_RIGHT) != 0) { right = Layout.EDGE_INSET_PX; }
        return [top, bottom, left, right];
    }

    // Guarded per spec §9.3/§4.4: (a) setting, (b) `has :vibrate` runtime
    // check (some devices/app-types don't expose it), (c) try/catch (some
    // may expose it but still refuse from a data field) -- any failure
    // degrades silently to just the invert-flash, never a crash.
    hidden function _maybeVibrate() as Void {
        var enabled = true;
        try {
            var v = Application.Properties.getValue("vibrateOnJump");
            if (v != null) {
                enabled = v;
            }
        } catch (ex) {
            enabled = true;  // default from properties.xml is true; a read
                              // failure shouldn't silently disable the nudge
        }
        if (!enabled) {
            return;
        }
        if (!(Attention has :vibrate)) {
            return;
        }
        try {
            Attention.vibrate([ new Attention.VibeProfile(50, 200) ]);
        } catch (ex) {
            // Forbidden on this device/app-type combo -- exactly the silent
            // degrade spec §9.3 calls for; the invert-flash remains the nudge.
        }
    }

    hidden function _readPuckName() as String {
        try {
            var v = Application.Properties.getValue("puckName");
            if (v != null && v.length() > 0) {
                return v;
            }
        } catch (ex) {
        }
        return "JumpHeight";  // properties.xml's own default, repeated here so
                              // a read failure still leaves the field usable
                              // (spec §10: every default must fully work)
    }

    hidden function _readUnitOverride() as Number {
        try {
            var v = Application.Properties.getValue("unitOverride");
            if (v != null) {
                return v;
            }
        } catch (ex) {
        }
        return UnitsFmt.UNIT_AUTO;
    }
}
