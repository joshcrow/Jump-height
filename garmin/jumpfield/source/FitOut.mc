// FitOut.mc
//
// FitContributor developer fields (US4, spec §5.5). The original four:
// one RECORD field (jump_height, written sparsely — once per JUMP — so
// Garmin Connect can chart individual jumps) and three SESSION fields
// (jumps/best_jump/best_airtime, written continuously — cheap — so the
// summary tiles are right even if the rider saves before another jump
// lands). Since 1.0.1 also the watch's own barometer (ids 4-6), and since
// 1.0.2 the health block (ids 9-13). Twelve fields; the full table, with
// meanings, is in docs/watch.md.
//
// NOTE what changed underneath jumps/best_jump/best_airtime in 1.0.2: the
// VALUES are now this ACTIVITY's, not the puck's whole stored session. This
// file is unchanged by that — it still writes what it is given — but the
// FIT's meaning changed, which is why it is written down here too. See
// Model.State's activityJumps()/activityBestM().
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
// reused — see WristProbe.mc for why they are not shipped. IDs 9-13 (the
// health block, added 2026-09-15) start after that reservation for the same
// reason: id 7 has a meaning already, even though nothing has ever written
// it.
//
// LIMITS, from the SDK rather than from memory. createField's :count
// parameter docs (doc/Toybox/ActivityRecording/Session.html) state
// "Apps are limited to 256 total bytes per message / Data fields are limited
// to 32 bytes per message / Messages larger than the limit will result in a
// 'New Field out of memory for FIT data' error." We are a data field, so 32
// B per message is the ceiling. What we write:
//
//   RECORD  : jump_height 4 + baro_alt_m 4 + baro_pa 4 + mem_used 2
//             + link_state 1 + since_puck_s 2 + err_code 1   = 18 B
//   SESSION : jumps 2 + best_jump 4 + best_airtime 4 + baro_src 2
//             + prev_err 1                                   = 13 B
//
// HOW MUCH ROOM IS LEFT DEPENDS ON WHICH READING OF THAT SENTENCE IS RIGHT,
// and the SDK does not say (adversarial review, 2026-09-15 — an earlier
// version of this comment asserted "both fit, with room for one more float
// in each", which resolves the ambiguity in the permissive direction and
// then states the result as fact):
//
//   * PER MESSAGE TYPE — 18 of 32 and 13 of 32, i.e. 14 B and 19 B spare.
//   * AGGREGATE across the data field's developer payload — 18 + 13 = 31 of
//     32, i.e. room for NOTHING, of any type.
//
// Assume the aggregate reading until a real device says otherwise. The
// quoted sentence is also borrowed: it lives on the ActivityRecording.
// Session page, while this app calls WatchUi.DataField.createField, whose
// own documentation states no limit at all — a second reason the repo flags
// the 32 B figure PROVISIONAL and never corroborated on silicon
// (docs/watch.md). SDK 9.2.0 documents NO limit on the NUMBER of developer
// fields an app may create and no developer-data-index cap — grepped the
// whole doc tree; the byte budget above is the only stated constraint, and
// createField's fieldId parameter carries no documented range either.
//
// THE HEALTH BLOCK (ids 9-13, added 2026-09-15)
// --------------------------------------------
// On 2026-09-14 this app stopped writing developer fields 41 % of the way
// into the rider's ride and never resumed — 1,688 of 2,877 records carry no
// developer slot at all — and the file says nothing about why. The watch's
// own CIQ log never leaves the watch. So these five fields are the app's
// self-report, in the only channel that reaches us: how much memory it was
// using, what the BLE state machine thought it was doing, how long since the
// puck last said anything, and which class of error was last caught (Err.mc).
// prev_err is the same code from the PREVIOUS run, carried across in
// Application.Storage, so a run that died still reports through the next
// activity's file.
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

    // ids 7, 8 reserved — wrist_amin_g / wrist_amax_g, never shipped.

    hidden var _memUsedField;      // RECORD  — id 9  — uint16 — "B"
    hidden var _linkStateField;    // RECORD  — id 10 — uint8  — enum
    hidden var _sincePuckField;    // RECORD  — id 11 — uint16 — "s"
    hidden var _errCodeField;      // RECORD  — id 12 — uint8  — Err.mc code
    hidden var _prevErrField;      // SESSION — id 13 — uint8  — Err.mc code

    hidden var _model;             // Model.State — the sticky error sink, so
                                    // a createField refusal in HERE is itself
                                    // reportable (in the next activity)

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
    function initialize(dataField, unitLabel as String, model) {
        _model = model;
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
            _model.noteErr(Err.E_FIT_BARO_FIELDS);
        }

        // A THIRD separate try/catch, for the same reason the baro block got
        // the second one: a device that refuses a 10th, 11th, 12th or 13th
        // developer field must not take jump_height OR the baro block down
        // with it. Diagnostics are the least load-bearing thing here and go
        // last in every sense.
        //
        // mem_used is in BYTES, not KiB. The Instinct 3 Solar's datafield
        // memoryLimit is 32,768 B (Devices/instinct3solar45mm/compiler.json)
        // which fits a UINT16's 65,535 with room to spare, so the coarser
        // unit would throw away resolution for nothing. The writer saturates
        // rather than wrapping — see recordHealth().
        try {
            _memUsedField = dataField.createField(
                "mem_used", 9, FitContributor.DATA_TYPE_UINT16,
                { :mesgType => FitContributor.MESG_TYPE_RECORD, :units => "B" });

            // PuckLink.STATE_* verbatim, not a re-mapping: the raw state
            // machine is strictly more informative than the four UI states,
            // and the three connect-attempt states are exactly where a stuck
            // link parks itself (F-12). Mapping in Err.mc's spirit — one
            // table, two places, docs/watch.md carries the copy:
            //   0 IDLE  1 SCANNING  2 PAIRING  3 DISCOVERING
            //   4 SUBSCRIBING  5 LIVE  6 DEAD
            _linkStateField = dataField.createField(
                "link_state", 10, FitContributor.DATA_TYPE_UINT8,
                { :mesgType => FitContributor.MESG_TYPE_RECORD, :units => "enum" });

            _sincePuckField = dataField.createField(
                "since_puck_s", 11, FitContributor.DATA_TYPE_UINT16,
                { :mesgType => FitContributor.MESG_TYPE_RECORD, :units => "s" });

            _errCodeField = dataField.createField(
                "err_code", 12, FitContributor.DATA_TYPE_UINT8,
                { :mesgType => FitContributor.MESG_TYPE_RECORD, :units => "code" });

            // SESSION, because it describes a whole previous RUN and there is
            // nothing per-record about it.
            _prevErrField = dataField.createField(
                "prev_err", 13, FitContributor.DATA_TYPE_UINT8,
                { :mesgType => FitContributor.MESG_TYPE_SESSION, :units => "code" });
        } catch (ex) {
            _memUsedField = null;
            _linkStateField = null;
            _sincePuckField = null;
            _errCodeField = null;
            _prevErrField = null;
            _model.noteErr(Err.E_FIT_HEALTH_FIELDS);
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

    // Once per compute() tick, exactly like recordBaro() and for the same
    // reason: a RECORD field is only as dense as the record stream, and the
    // record stream is what we are trying to explain when it stops.
    //
    // SATURATING, NOT WRAPPING, on both UINT16s. A wrapped mem_used would
    // read as a small healthy number at the exact moment memory ran out, and
    // a wrapped since_puck_s would read as "just heard from it" after 18.2
    // hours of silence. Both are the "a reading that did not happen must not
    // look like a pass" rule (CLAUDE.md §2.3) applied to an integer type.
    // 65535 therefore means "at least this much", including the never-heard
    // -from case for since_puck_s, which no real activity reaches honestly.
    //
    // Nothing here is guarded individually: the whole call is inside the
    // caller's try/catch (JumpFieldView._recordHealth), which reports
    // Err.E_HEALTH. Per-field null checks are kept because a partial
    // createField failure is a real, silent state.
    function recordHealth(memUsedB as Number, linkState as Number,
                          sincePuckS as Number, errCode as Number) as Void {
        if (_memUsedField != null) {
            _memUsedField.setData(memUsedB > 65535 ? 65535 : memUsedB);
        }
        if (_linkStateField != null) {
            _linkStateField.setData(linkState);
        }
        if (_sincePuckField != null) {
            _sincePuckField.setData(sincePuckS > 65535 ? 65535 : sincePuckS);
        }
        if (_errCodeField != null) {
            _errCodeField.setData(errCode > 255 ? 255 : errCode);
        }
    }

    // SESSION scope; written every tick for the same last-write-wins reason
    // as updateSession()/updateBaroSrc(). The value is a constant for the
    // whole run, so this is one uint8 setData per second and no arithmetic.
    function updatePrevErr(code as Number) as Void {
        if (_prevErrField != null) {
            _prevErrField.setData(code > 255 ? 255 : code);
        }
    }
}
