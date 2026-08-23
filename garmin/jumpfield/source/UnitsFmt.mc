// UnitsFmt.mc
//
// Unit selection + number formatting (spec §4.3). Setting override wins;
// "auto" follows the watch's own system unit preference — mirrors web/app.js
// choosing ft-vs-m from one preference and using it everywhere, so a rider
// who's used the web app sees the same convention on the wrist.
//
// Deliberately uses System.getDeviceSettings().distanceUnits, not the
// (arguably more semantically precise) elevationUnits field — both read
// identically for effectively every real rider, and distanceUnits is the
// long-established, extremely well-attested field for exactly this
// ft-vs-m decision in Connect IQ data fields. See FIRST_COMPILE.md: this
// was a deliberate lower-risk choice, not an oversight.

using Toybox.System;
import Toybox.Lang;

module UnitsFmt {

    // Must match resources/settings/properties.xml's unitOverride values.
    const UNIT_AUTO = 0;
    const UNIT_FT = 1;
    const UNIT_M = 2;

    // Generated from config/params.json (audit F-18). This used to be a
    // hand-written 3.28084 whose comment said "same constant as web/app.js" —
    // a duplicate acknowledged in a comment instead of removed. It was written
    // out by hand in nine places across five languages.
    const M_TO_FT = Params.M_TO_FT;

    // Resolve the effective display unit for this compute() tick. Cheap
    // enough (one Properties read, one DeviceSettings read) to call fresh
    // every time rather than cache+invalidate against a settings-changed
    // event that sideloaded installs never fire anyway (spec §10).
    function isFeet(unitOverride as Number) as Boolean {
        if (unitOverride == UNIT_FT) {
            return true;
        }
        if (unitOverride == UNIT_M) {
            return false;
        }
        var settings = System.getDeviceSettings();
        return settings.distanceUnits == System.UNIT_STATUTE;
    }

    // Bare numeric value in the chosen display unit — what FitOut.mc writes
    // (spec §5.5: developer fields carry the RIDER'S unit, not canonical
    // meters; Garmin Connect does not convert developer fields).
    function heightValue(meters as Float, feet as Boolean) as Float {
        return feet ? (meters * M_TO_FT) : meters;
    }

    function unitLabel(feet as Boolean) as String {
        return feet ? "ft" : "m";
    }

    // Digits only — "4.2" / "1.32", no unit. The big number on the glass is
    // drawn in Graphics.FONT_NUMBER_*, and that family has glyphs for digits
    // and separators ONLY: passing it "4.2 ft" renders the letters as two
    // empty tofu boxes. Found 2026-08-10 the first time the CONNECTED state
    // was ever rendered (simulator, epix2) — it had been latent since the
    // field was written, invisible because SEARCHING draws "--" and every
    // check until then had been in the SEARCHING state.
    //
    // Callers that draw in a TEXT font (footer, half/small tiers) should keep
    // using formatHeight(); only the FONT_NUMBER_* path needs the split.
    function heightDigits(meters as Float, feet as Boolean) as String {
        if (feet) {
            return (meters * M_TO_FT).format("%.1f");
        }
        return meters.format("%.2f");
    }

    // "4.2 ft" / "1.32 m" — ft 1 decimal, m 2 decimals (spec §4.3).
    function formatHeight(meters as Float, feet as Boolean) as String {
        if (feet) {
            return (meters * M_TO_FT).format("%.1f") + " ft";
        }
        return meters.format("%.2f") + " m";
    }

    // Airtime is always seconds, 2 decimals, no unit override (spec §4.3).
    function formatAirtime(seconds as Float) as String {
        return seconds.format("%.2f") + " s";
    }
}
