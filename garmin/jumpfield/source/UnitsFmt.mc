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

    const M_TO_FT = 3.28084;  // same constant as web/app.js — one conversion,
                              // everywhere, so numbers never disagree across
                              // clients by a rounding-constant mismatch.

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
