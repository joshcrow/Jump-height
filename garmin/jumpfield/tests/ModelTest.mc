// ModelTest.mc
//
// Toybox.Test unit tests for Model.mc — JUMP updates state + arms the flash
// + latches "new jump" once; STATS seeds count/best (US6) without arming
// either; unknown lines are ignored; staleness retains rather than clears.
// NO BLE, NO PuckLink import (spec's test constraint) — Protocol.parseKV is
// used only to build realistic Dictionary fixtures from literal wire lines,
// exactly as PuckLink would hand them to Model, without pulling in any BLE
// symbol (Protocol.mc has none).

using Toybox.Test;
import Toybox.Lang;

(:test)
function testJump_updatesAllFieldsFromExactSpecLine(logger) {
    var m = new Model.State();
    // Exact line from spec §5.2.
    m.onLine(Protocol.parseKV(
        "JUMP n=4 airtime_raw_s=1.021 airtime_s=1.036 height_m=1.316 height_ft=4.3 best_m=1.316"));

    Test.assertEqual(m.jumpCount(), 4);            // count comes from n=
    Test.assertEqual(m.lastHeightM(), 1.316);
    Test.assertEqual(m.lastAirtimeS(), 1.036);      // airtime_s, not airtime_raw_s
    Test.assertEqual(m.sessionBestM(), 1.316);
    Test.assertEqual(m.bestAirtimeS(), 1.036);
    Test.assertEqual(m.hasData(), true);
    return true;
}

(:test)
function testJump_armsFiveSecondFlash(logger) {
    var m = new Model.State();
    Test.assertEqual(m.isFlashing(), false);   // never flashing before any JUMP
    m.onLine(Protocol.parseKV("JUMP n=1 airtime_s=0.5 height_m=0.3 best_m=0.3"));
    Test.assertEqual(m.isFlashing(), true);    // armed immediately after
    return true;
}

(:test)
function testJump_latchesNewJumpExactlyOnce(logger) {
    var m = new Model.State();
    Test.assertEqual(m.consumeNewJump(), false);  // nothing pending yet
    m.onLine(Protocol.parseKV("JUMP n=1 airtime_s=0.5 height_m=0.3 best_m=0.3"));
    Test.assertEqual(m.consumeNewJump(), true);   // fires once...
    Test.assertEqual(m.consumeNewJump(), false);  // ...and only once
    return true;
}

(:test)
function testJump_countTracksNField(logger) {
    var m = new Model.State();
    m.onLine(Protocol.parseKV("JUMP n=1 airtime_s=0.5 height_m=0.3 best_m=0.3"));
    Test.assertEqual(m.jumpCount(), 1);
    m.onLine(Protocol.parseKV("JUMP n=2 airtime_s=0.6 height_m=0.4 best_m=0.4"));
    Test.assertEqual(m.jumpCount(), 2);
    return true;
}

(:test)
function testStats_seedsCountAndBestWithoutArmingFlashOrLatch(logger) {
    var m = new Model.State();
    // Exact line from spec §5.2 (US6: reconnect/late-join reseed).
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=4 session_best_m=1.316 stored_jumps=9 stored_best_m=1.316 trace_bytes=182031"));

    Test.assertEqual(m.jumpCount(), 4);
    Test.assertEqual(m.sessionBestM(), 1.316);
    Test.assertEqual(m.hasData(), true);
    Test.assertEqual(m.isFlashing(), false);      // STATS never flashes...
    Test.assertEqual(m.consumeNewJump(), false);  // ...or vibrates (spec §5.4)
    return true;
}

(:test)
function testStats_ignoresStoredFields(logger) {
    var m = new Model.State();
    // session_* and stored_* deliberately differ so a bug that reads the
    // wrong prefix is caught.
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=2 session_best_m=0.5 stored_jumps=99 stored_best_m=9.9"));
    Test.assertEqual(m.jumpCount(), 2);
    Test.assertEqual(m.sessionBestM(), 0.5);
    return true;
}

(:test)
function testStats_seedsAfterReconnectPreservingArrivalOrder(logger) {
    // US6 in miniature: a JUMP arrives, the link drops (markStale), then a
    // reconnect's STATS reseeds — final state must reflect the device's
    // reseed, not the pre-drop values.
    var m = new Model.State();
    m.onLine(Protocol.parseKV("JUMP n=1 airtime_s=0.5 height_m=0.3 best_m=0.3"));
    m.markStale();
    m.onLine(Protocol.parseKV("STATS session_jumps=5 session_best_m=1.9"));
    Test.assertEqual(m.jumpCount(), 5);
    Test.assertEqual(m.sessionBestM(), 1.9);
    return true;
}

(:test)
function testUnknownLines_areIgnored(logger) {
    var m = new Model.State();
    m.onLine(Protocol.parseKV("READY"));
    m.onLine(Protocol.parseKV("STATE recording"));
    m.onLine(Protocol.parseKV("# hint: almost a jump"));
    m.onLine(Protocol.parseKV("OK stats"));
    m.onLine(Protocol.parseKV("ERR bad command"));
    m.onLine(Protocol.parseKV("INFO fw=0.4.1 sample_hz=200 log_hz=50 ble=1"));

    // None of the above are JUMP/STATS -- state must still be all-defaults.
    Test.assertEqual(m.jumpCount(), 0);
    Test.assertEqual(m.lastHeightM(), 0.0);
    Test.assertEqual(m.sessionBestM(), 0.0);
    Test.assertEqual(m.hasData(), false);
    Test.assertEqual(m.isFlashing(), false);
    return true;
}

(:test)
function testMarkStale_retainsDataRatherThanClearingIt(logger) {
    var m = new Model.State();
    m.onLine(Protocol.parseKV("JUMP n=3 airtime_s=0.9 height_m=1.0 best_m=1.0"));
    m.markStale();
    // spec §5.4: "retain model, mark stale" -- the numbers must survive.
    Test.assertEqual(m.jumpCount(), 3);
    Test.assertEqual(m.lastHeightM(), 1.0);
    Test.assertEqual(m.hasData(), true);
    return true;
}

(:test)
function testMissingFieldsLeavePriorValueInPlace(logger) {
    // A malformed/partial JUMP line must not blank a good number to zero.
    var m = new Model.State();
    m.onLine(Protocol.parseKV("JUMP n=1 airtime_s=0.5 height_m=0.3 best_m=0.3"));
    m.onLine(Protocol.parseKV("JUMP n=2"));  // height_m/airtime_s/best_m absent
    Test.assertEqual(m.jumpCount(), 2);      // n= still updates
    Test.assertEqual(m.lastHeightM(), 0.3);  // but height retains the prior value
    return true;
}

(:test)
function testStats_batteryAdderKeysCapturedAndAbsentKeysRetain(logger) {
    var m = new Model.State();
    Test.assert(m.puckBattPct() == null);      // v1 puck: never any battery
    // (plain assert: assertEqual dereferences its operands and throws on null)
    // A Sense-class STATS (docs/sense.md §3.4 adder keys).
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=0 session_best_m=0.000 stored_jumps=0 stored_best_m=0.000 trace_bytes=0 vbat_mv=3920 batt_pct=68 chg=0"));
    Test.assertEqual(m.puckBattPct(), 68);
    Test.assertEqual(m.puckCharging(), false);
    // Charging flips the flag; pct still tracks.
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=0 session_best_m=0.000 stored_jumps=0 stored_best_m=0.000 trace_bytes=0 vbat_mv=4160 batt_pct=95 chg=1"));
    Test.assertEqual(m.puckBattPct(), 95);
    Test.assertEqual(m.puckCharging(), true);
    // An old-firmware STATS (no battery keys) must NOT blank known state.
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=1 session_best_m=0.4 stored_jumps=1 stored_best_m=0.4 trace_bytes=10"));
    Test.assertEqual(m.puckBattPct(), 95);
    Test.assertEqual(m.puckCharging(), true);
    return true;
}
