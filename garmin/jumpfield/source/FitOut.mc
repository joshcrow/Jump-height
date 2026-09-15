// FitOut.mc
//
// FitContributor developer fields (US4, spec §5.5). One RECORD field
// (jump_height, written sparsely — once per JUMP — so Garmin Connect can
// chart individual jumps) and three SESSION fields (jumps/best_jump/
// best_airtime, written continuously — cheap — so the summary tiles are
// right even if the rider saves before another jump lands).
//
// Units: written in the RIDER'S DISPLAY UNIT (ft or m), with the FIT units
// string set to match — spec §5.5's adversarial-review decision. Garmin
// Connect does not convert developer fields, and a human reads the activity
// page, not an archive; the device's own CSVs remain the canonical-meters
// record. Callers (JumpFieldView) MUST convert via UnitsFmt before calling
// in here — this file never converts, only writes what it's given.
//
// Field IDs (0-3) and scopes are fixed by spec §5.5's table; do not renumber
// without checking whether a real saved activity already used the old ID.
// IDs 4-6 (the watch's own barometer, added 2026-09-14) obey the same rule.
// IDs 7 and 8 are RESERVED for wrist_amin_g / wrist_amax_g and must not be
// reused — see WristProbe.mc for why they are not shipped.
//
// THE WATCH'S OWN BAROMETER (ids 4-6, added 2026-09-14)
// ----------------------------------------------------
// Measured from the rider's real file, data/sessions/20260914-210637-E2C4/
// garmin.fit: the Windsurf profile wrote `enhanced_altitude` on all 2,877
// records with exactly ONE distinct value, -29.2 m. The watch has a
// barometer; the profile simply never updated the channel. So the elevation
// already in the FIT is not evidence of anything, and the puck's height is
// the only height in the file.
//
// These three fields put the watch's barometer in the FIT under our own
// developer identity, where the activity profile's elevation settings cannot
// flatten it. `baro_pa` is the load-bearing one: rawAmbientPressure is read
// straight off the internal sensor, so unlike `altitude` it does not depend
// on the profile calibrating an elevation at all. ~8.3 cm of altitude per
// Pascal at sea level, which is the resolution a 1-4 s wing jump needs.

using Toybox.FitContributor;
import Toybox.Lang;

class FitOut {

    hidden var _jumpHeightField;   // RECORD  — id 0 — float32 — sparse
    hidden var _jumpsField;        // SESSION — id 1 — uint16
    hidden var _bestJumpField;     // SESSION — id 2 — float32
    hidden var _bestAirtimeField;  // SESSION — id 3 — float32

    hidden var _baroAltField;      // RECORD  — id 4 — float32 — "m"
    hidden var _baroPaField;       // RECORD  — id 5 — float32 — "Pa"
    hidden var _baroSrcField;      // SESSION — id 6 — uint16  — provenance

    // Spec §5.5: "One developer-data UUID (constant in FitOut.mc)". The
    // CONFIRMED FitContributor.createField() signature (name, fieldId, type,
    // options={count,mesgType,units}) takes no UUID argument — Connect IQ
    // ties a data field's FIT developer fields to the app's own manifest
    // identity automatically, so there is no separate value to pass to any
    // API here. Kept as a literal match of manifest.xml's iq:application id
    // so the two are visibly the same identity, and flagged in
    // FIRST_COMPILE.md in case a real per-field UUID hook exists that this
    // author's SDK research (no local SDK available) missed.
    const DEVELOPER_DATA_ID = "873B577243574E27AB454C3FF165E7B4";

    // dataField: the owning DataField instance — createField() is a DataField
    // instance method (there is no Session/Activity object to call it on
    // from inside a data field). unitLabel: "ft" or "m" (UnitsFmt.unitLabel),
    // resolved once at construction — see UnitsFmt.mc's header for why a
    // mid-session unit change is an accepted, ignored edge case.
    function initialize(dataField, unitLabel as String) {
        _jumpHeightField = dataField.createField(
            "jump_height", 0, FitContributor.DATA_TYPE_FLOAT,
            { :mesgType => FitContributor.MESG_TYPE_RECORD, :units => unitLabel });

        _jumpsField = dataField.createField(
            "jumps", 1, FitContributor.DATA_TYPE_UINT16,
            { :mesgType => FitContributor.MESG_TYPE_SESSION, :units => "count" });

        _bestJumpField = dataField.createField(
            "best_jump", 2, FitContributor.DATA_TYPE_FLOAT,
            { :mesgType => FitContributor.MESG_TYPE_SESSION, :units => unitLabel });

        _bestAirtimeField = dataField.createField(
            "best_airtime", 3, FitContributor.DATA_TYPE_FLOAT,
            { :mesgType => FitContributor.MESG_TYPE_SESSION, :units => "s" });

        // Separate try/catch, deliberately. JumpFieldView drops the WHOLE
        // FitOut on any constructor throw, so a device that refuses a 5th,
        // 6th or 7th developer field would otherwise take jump_height down
        // with it — trading the product's only FIT output for a nicety. The
        // baro fields are additive evidence; the jump fields above are not.
        // Units strings are Garmin's own examples ("ft", "Pa") from
        // DataField.createField's :units parameter docs.
        try {
            _baroAltField = dataField.createField(
                "baro_alt_m", 4, FitContributor.DATA_TYPE_FLOAT,
                { :mesgType => FitContributor.MESG_TYPE_RECORD, :units => "m" });

            _baroPaField = dataField.createField(
                "baro_pa", 5, FitContributor.DATA_TYPE_FLOAT,
                { :mesgType => FitContributor.MESG_TYPE_RECORD, :units => "Pa" });

            // Which barometric source actually answered, for the whole
            // session. Without it a missing baro_pa column is ambiguous
            // between "the device has no pressure sensor", "the API returned
            // null every tick" and "createField refused" — three different
            // findings that look identical in the FIT (CLAUDE.md §2.3: a
            // reading that did not happen must not look like a pass).
            // Bitmask, see JumpFieldView.BARO_SRC_*.
            _baroSrcField = dataField.createField(
                "baro_src", 6, FitContributor.DATA_TYPE_UINT16,
                { :mesgType => FitContributor.MESG_TYPE_SESSION, :units => "bitmask" });
        } catch (ex) {
            _baroAltField = null;
            _baroPaField = null;
            _baroSrcField = null;
        }
    }

    // Once per JUMP (sparse -- spec §5.5). heightDisplay is ALREADY in the
    // rider's display unit; see the file header.
    function recordJump(heightDisplay as Float) as Void {
        _jumpHeightField.setData(heightDisplay);
    }

    // Every compute() tick (cheap -- spec §5.5), so SESSION summaries are
    // correct even for a rider who saves without another jump after the last
    // one (or reconnects mid-session and never sees a fresh JUMP at all).
    function updateSession(jumps as Number, bestDisplay as Float, bestAirtimeS as Float) as Void {
        _jumpsField.setData(jumps);
        _bestJumpField.setData(bestDisplay);
        _bestAirtimeField.setData(bestAirtimeS);
    }

    // Once per compute() tick — i.e. once per FIT RECORD, since a DataField
    // is handed Activity.Info "once per second" (SDK WatchUi.DataField
    // overview) and MESG_TYPE_RECORD "is never written faster than once per
    // second" (SDK Toybox.FitContributor overview). Per-second summaries,
    // exactly as the record rate allows; no sub-second channel exists here.
    //
    // EITHER argument may be null, and null means NOT WRITTEN — never 0.0.
    // A 0.0 Pa reading would be a fabricated sea-level-times-zero datum in a
    // column a human will later plot against the puck's trace.
    function recordBaro(altM, pa) as Void {
        if (_baroAltField != null && altM != null) {
            _baroAltField.setData(altM);
        }
        if (_baroPaField != null && pa != null) {
            _baroPaField.setData(pa);
        }
    }

    // SESSION scope, so it is written continuously and is correct on an early
    // save — same reasoning as updateSession() above.
    function updateBaroSrc(mask as Number) as Void {
        if (_baroSrcField != null) {
            _baroSrcField.setData(mask);
        }
    }
}
