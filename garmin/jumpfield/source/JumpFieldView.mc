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
using Toybox.Lang;

class JumpFieldView extends WatchUi.DataField {

    // UI states (spec §4.2) — derived each draw from PuckLink.state() +
    // Model.hasData(), never stored: there is no BLE-awareness inside Model,
    // and no data-awareness inside PuckLink, on purpose (see Model.mc).
    const UI_SEARCHING = 0;
    const UI_CONNECTED = 1;
    const UI_RECONNECTING = 2;
    const UI_NO_BLE = 3;

    // Layout-tier breakpoints (spec §4.1's full/half/small) — heuristic
    // pixel thresholds; verify against the real simulator preview for all
    // three field sizes on instinct3solar45mm (M3 AC, and one of this
    // task's "check first in the simulator" items — see FIRST_COMPILE.md).
    const FULL_MIN_H = 120;
    const HALF_MIN_H = 60;

    const EDGE_INSET_PX = 10;  // extra margin on an obscured (clipped) edge

    hidden var _model;
    hidden var _puckLink;
    hidden var _fitOut;          // null if FitContributor setup ever throws
    hidden var _puckName as String;

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
        } catch (ex instanceof Lang.Exception) {
            _fitOut = null;  // FIT enrichment is a nicety (US4); the live
                              // glance (US1-US3) must not depend on it
        }
    }

    // ---- lifecycle, wired from JumpFieldApp.onStart()/onStop() ----

    function onAppStart() as Void {
        _puckLink.start();
    }

    function onAppStop() as Void {
        _puckLink.stop();
    }

    // ---- DataField overrides ----

    // info (Activity.Info) is intentionally untouched -- our data comes from
    // the puck over BLE, never from the activity/GPS/sensors Garmin already
    // records. Called ~1 Hz regardless of which data screen is on-glass
    // (see PuckLink.mc's header) -- this is the field's only clock.
    function compute(info) {
        _puckLink.poll();

        var feet = UnitsFmt.isFeet(_readUnitOverride());

        if (_fitOut != null) {
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

        if (h >= FULL_MIN_H) {
            _drawFull(dc, w, h, insets, fg, bg, feet, uiState);
        } else if (h >= HALF_MIN_H) {
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
        var left = insets[2];
        var right = w - insets[3];
        var top = insets[0];
        var bottom = h - insets[1];
        var midX = (left + right) / 2;

        var headerY = top + 14;
        _drawDot(dc, left + 8, headerY, 6, uiState, fg, bg);
        dc.setColor(fg, bg);
        dc.drawText(left + 20, headerY, Graphics.FONT_XTINY, _puckName,
            Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);
        dc.drawText(right - 4, headerY, Graphics.FONT_XTINY, _model.jumpCount().toString() + " jumps",
            Graphics.TEXT_JUSTIFY_RIGHT | Graphics.TEXT_JUSTIFY_VCENTER);

        var subText = _subText(uiState);
        var bigY = (top + (bottom - top) * 0.52).toNumber();  // Dc coordinates
                                                                // want Numbers,
                                                                // not Floats
        if (!subText.equals("")) {
            dc.drawText(midX, top + 32, Graphics.FONT_XTINY, subText,
                Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
        }

        var bigText = _bigNumberText(uiState, feet);
        var fonts = [Graphics.FONT_NUMBER_HOT, Graphics.FONT_NUMBER_MEDIUM, Graphics.FONT_NUMBER_MILD];
        var font = _fitFont(dc, bigText, right - left - 8, fonts);
        _drawInvertible(dc, midX, bigY, bigText, font, fg, bg, uiState == UI_CONNECTED && _model.isFlashing());

        if (uiState == UI_CONNECTED || uiState == UI_RECONNECTING) {
            var footer = "best " + UnitsFmt.formatHeight(_model.sessionBestM(), feet)
                + " . air " + UnitsFmt.formatAirtime(_model.lastAirtimeS());
            dc.setColor(fg, bg);
            dc.drawText(midX, bottom - 12, Graphics.FONT_XTINY, footer,
                Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
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

        var bigText = _bigNumberText(uiState, feet);
        var bigY = (top + (bottom - top) * 0.38).toNumber();
        var fonts = [Graphics.FONT_NUMBER_MEDIUM, Graphics.FONT_NUMBER_MILD, Graphics.FONT_SMALL];
        var font = _fitFont(dc, bigText, right - left - 4, fonts);
        _drawInvertible(dc, midX, bigY, bigText, font, fg, bg, uiState == UI_CONNECTED && _model.isFlashing());

        var rowY = bottom - 12;
        dc.setColor(fg, bg);
        if (uiState == UI_CONNECTED || uiState == UI_RECONNECTING) {
            dc.drawText(left, rowY, Graphics.FONT_XTINY, "^" + UnitsFmt.formatHeight(_model.sessionBestM(), feet),
                Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);
            dc.drawText(midX, rowY, Graphics.FONT_XTINY, "n" + _model.jumpCount().toString(),
                Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
        } else {
            var subText = _subText(uiState);
            dc.drawText(left, rowY, Graphics.FONT_XTINY, subText,
                Graphics.TEXT_JUSTIFY_LEFT | Graphics.TEXT_JUSTIFY_VCENTER);
        }
        _drawDot(dc, right - 8, rowY, 5, uiState, fg, bg);
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
        return UnitsFmt.formatHeight(_model.lastHeightM(), feet);
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
            dc.drawText(cx, cy, font, text, Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
            dc.setColor(fg, bg);
        } else {
            dc.setColor(fg, bg);
            dc.drawText(cx, cy, font, text, Graphics.TEXT_JUSTIFY_CENTER | Graphics.TEXT_JUSTIFY_VCENTER);
        }
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
    hidden function _edgeInsets() as Array {
        var flags = 0;
        try {
            flags = getObscurityFlags();
        } catch (ex instanceof Lang.Exception) {
            flags = 0;
        }
        var top = 0;
        var bottom = 0;
        var left = 0;
        var right = 0;
        if ((flags & WatchUi.DataField.OBSCURE_TOP) != 0) { top = EDGE_INSET_PX; }
        if ((flags & WatchUi.DataField.OBSCURE_BOTTOM) != 0) { bottom = EDGE_INSET_PX; }
        if ((flags & WatchUi.DataField.OBSCURE_LEFT) != 0) { left = EDGE_INSET_PX; }
        if ((flags & WatchUi.DataField.OBSCURE_RIGHT) != 0) { right = EDGE_INSET_PX; }
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
        } catch (ex instanceof Lang.Exception) {
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
        } catch (ex instanceof Lang.Exception) {
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
        } catch (ex instanceof Lang.Exception) {
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
        } catch (ex instanceof Lang.Exception) {
        }
        return UnitsFmt.UNIT_AUTO;
    }
}
