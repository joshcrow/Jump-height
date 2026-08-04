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

using Toybox.FitContributor;
import Toybox.Lang;

class FitOut {

    hidden var _jumpHeightField;   // RECORD  — id 0 — float32 — sparse
    hidden var _jumpsField;        // SESSION — id 1 — uint16
    hidden var _bestJumpField;     // SESSION — id 2 — float32
    hidden var _bestAirtimeField;  // SESSION — id 3 — float32

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
}
