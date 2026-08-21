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
        "STATS session_jumps=2 session_best_m=0.5 stored_jumps=99 stored_best_m=9.9 trace_bytes=42"));
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
    m.onLine(Protocol.parseKV("STATS session_jumps=5 session_best_m=1.9 stored_jumps=5 stored_best_m=1.9 trace_bytes=7"));
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
    //
    // BEHAVIOUR CHANGED 2026-08-11, deliberately. This test used to assert
    // that a partial line was HALF-APPLIED — `n` updated, height retained —
    // and that is the exact mechanism that put a jump count of 64 on the
    // wrist next to a stale height while the puck reported 1. Firmware emits
    // every JUMP field from a single emitf (main.cpp), so a JUMP missing
    // fields is not "partial", it is DAMAGED, and the count it carries is no
    // more trustworthy than the height it lost.
    //
    // The original intent — never blank a good number to zero — is satisfied
    // more strongly now: the line is dropped whole, so nothing changes at all.
    var m = new Model.State();
    m.onLine(Protocol.parseKV("JUMP n=1 airtime_s=0.5 height_m=0.3 best_m=0.3"));
    m.onLine(Protocol.parseKV("JUMP n=2"));  // height_m/airtime_s/best_m absent
    Test.assertEqual(m.jumpCount(), 1);      // n= is NOT trusted either
    Test.assertEqual(m.lastHeightM(), 0.3);  // and the good number survives
    Test.assertEqual(m.rejectedCount(), 1);
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

// ---------------------------------------------------------------------------
// Corruption gate (2026-08-11). These encode the failure seen on the wrist:
// the watch displayed 64 jumps / best 0.3 ft while the puck itself reported
// session_jumps=1 session_best_m=0.164. Lost bytes — including the ones
// carrying a '\n' — let LineReader glue fragments into a line that parses
// PERFECTLY and is therefore believed. Each test below is a line that cannot
// have come intact off the wire, and the assertion is always the same: prior
// good state survives untouched, and nothing invented reaches the screen.

(:test)
function testCorrupt_truncatedJumpIsRejectedNotHalfApplied(logger) {
    var m = new Model.State();
    m.onLine(Protocol.parseKV("JUMP n=1 airtime_s=0.36 height_m=0.164 best_m=0.164"));
    Test.assertEqual(m.jumpCount(), 1);

    // Tail lost: no best_m, no airtime_s. The old code applied n and height_m
    // anyway, which is exactly how a wrong count reached the glass.
    m.onLine(Protocol.parseKV("JUMP n=64 height_m=0.9"));
    Test.assertEqual(m.jumpCount(), 1);                 // NOT 64
    Test.assertEqual(m.lastHeightM(), 0.164);           // NOT 0.9
    Test.assertEqual(m.rejectedCount(), 1);
    return true;
}

(:test)
function testCorrupt_bestBelowLastIsImpossible(logger) {
    // Firmware updates session_best BEFORE emitting the line it rides on, so
    // best_m < height_m cannot happen on the wire. This single invariant
    // would have caught the observed corruption on its first frame.
    var m = new Model.State();
    m.onLine(Protocol.parseKV("JUMP n=1 airtime_s=0.36 height_m=0.164 best_m=0.164"));
    m.onLine(Protocol.parseKV("JUMP n=2 airtime_s=0.40 height_m=0.152 best_m=0.091"));
    Test.assertEqual(m.jumpCount(), 1);
    Test.assertEqual(m.sessionBestM(), 0.164);
    Test.assertEqual(m.rejectedCount(), 1);
    return true;
}

(:test)
function testCorrupt_gluedJumpAndStatsIsRejected(logger) {
    // Two lines merged by a lost newline: a JUMP tag carrying STATS-only keys.
    // parseKV is last-key-wins, so this parses cleanly — the key SETS are the
    // only evidence that it is two lines.
    var m = new Model.State();
    m.onLine(Protocol.parseKV("JUMP n=1 airtime_s=0.36 height_m=0.164 best_m=0.164"));
    m.onLine(Protocol.parseKV(
        "JUMP n=7 airtime_s=0.5 height_m=0.3 best_m=0.4 session_jumps=64 stored_jumps=8"));
    Test.assertEqual(m.jumpCount(), 1);
    Test.assertEqual(m.rejectedCount(), 1);
    return true;
}

(:test)
function testCorrupt_gluedStatsAndJumpIsRejected(logger) {
    // Mirror case, and the worse one: a bad STATS reseeds count AND best at
    // once (US6), poisoning the display until the next reconnect.
    var m = new Model.State();
    m.onLine(Protocol.parseKV("STATS session_jumps=3 session_best_m=1.2 stored_jumps=3 stored_best_m=1.2 trace_bytes=99"));
    Test.assertEqual(m.jumpCount(), 3);
    m.onLine(Protocol.parseKV("STATS session_jumps=64 session_best_m=0.09 height_m=0.5 best_m=0.5"));
    Test.assertEqual(m.jumpCount(), 3);
    Test.assertEqual(m.sessionBestM(), 1.2);
    Test.assertEqual(m.rejectedCount(), 1);
    return true;
}

(:test)
function testCorrupt_physicallyImpossibleValuesRejected(logger) {
    var m = new Model.State();
    m.onLine(Protocol.parseKV("JUMP n=1 airtime_s=0.36 height_m=0.164 best_m=0.164"));
    // 91 m jump.
    m.onLine(Protocol.parseKV("JUMP n=2 airtime_s=0.5 height_m=91.0 best_m=91.0"));
    // 40 s of airtime.
    m.onLine(Protocol.parseKV("JUMP n=3 airtime_s=40.0 height_m=0.2 best_m=0.3"));
    // Negative height.
    m.onLine(Protocol.parseKV("JUMP n=4 airtime_s=0.4 height_m=-1.0 best_m=0.3"));
    Test.assertEqual(m.jumpCount(), 1);
    Test.assertEqual(m.lastHeightM(), 0.164);
    Test.assertEqual(m.rejectedCount(), 3);
    return true;
}

(:test)
function testCorrupt_gateDoesNotRejectRealTraffic(logger) {
    // The gate must never eat a line the firmware actually emits. This is the
    // exact wire format from main.cpp's emitf, and the spec §5.2 sample.
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "JUMP n=4 airtime_raw_s=1.021 airtime_s=1.036 height_m=1.316 height_ft=4.3 best_m=1.316"));
    Test.assertEqual(m.jumpCount(), 4);
    Test.assertEqual(m.lastHeightM(), 1.316);
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=4 session_best_m=1.316 stored_jumps=9 stored_best_m=1.316 trace_bytes=182031 vbat_mv=3870 batt_pct=63 chg=0"));
    Test.assertEqual(m.jumpCount(), 4);
    Test.assertEqual(m.puckBattPct(), 63);
    // Equal best and height must pass (every session's first jump is this).
    m.onLine(Protocol.parseKV("JUMP n=5 airtime_s=0.4 height_m=0.2 best_m=0.2"));
    Test.assertEqual(m.jumpCount(), 5);
    Test.assertEqual(m.rejectedCount(), 0);
    return true;
}

// A STATS line truncated mid-flight loses its tail. Because parseKV is
// last-key-wins and LineReader glues fragments, the surviving head can still
// parse as a valid STATS — and STATS reseeds count AND best in one go, so a
// fragment would poison the whole session display until the next reconnect.
// stored_jumps and trace_bytes are emitted unconditionally by the firmware and
// sit AFTER the fields consumed here, so their absence proves truncation.
(:test)
function testCorrupt_truncatedStatsIsRejected(logger) {
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=4 session_best_m=1.316 stored_jumps=9 stored_best_m=1.316 trace_bytes=182031"));
    Test.assertEqual(m.jumpCount(), 4);
    // Now a truncated one: head survives, tail is gone.
    m.onLine(Protocol.parseKV("STATS session_jumps=77 session_best_m=9.1"));
    Test.assertEqual(m.jumpCount(), 4);        // must NOT reseed from a fragment
    Test.assertEqual(m.rejectedCount(), 1);
    return true;
}

(:test)
function testStats_reseedsBestAirtime(logger) {
    // The M2 FIT parse found best_jump reconciled while best_airtime stayed
    // at the live-seen max — STATS carried no airtime. This locks the fix:
    // the adder key reseeds best airtime on reconnect, absent key leaves
    // prior state, and an absurd value is ignored.
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=3 session_best_m=1.285 session_best_airtime_s=1.023 stored_jumps=3 stored_best_m=1.285 trace_bytes=42"));
    Test.assertEqual(m.bestAirtimeS(), 1.023);
    // absent key: prior value survives (v1-style line)
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=3 session_best_m=1.285 stored_jumps=3 stored_best_m=1.285 trace_bytes=42"));
    Test.assertEqual(m.bestAirtimeS(), 1.023);
    // absurd value: ignored
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=3 session_best_m=1.285 session_best_airtime_s=99.0 stored_jumps=3 stored_best_m=1.285 trace_bytes=42"));
    Test.assertEqual(m.bestAirtimeS(), 1.023);
    return true;
}

(:test)
function testStats_storageDownIsSurfacedAndClears(logger) {
    // Measured on hardware 2026-08-19: a puck rebooting on a flat cell comes
    // up with its flash unmounted, looks healthy, and records nothing. The
    // watch must show that -- and must clear it when the puck self-heals,
    // because the firmware now retries the mount every 30 s.
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=2 session_best_m=1.0 stored_jumps=0 trace_bytes=0 fs=down"));
    Test.assertEqual(m.storageDown(), true);
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=2 session_best_m=1.0 stored_jumps=3 trace_bytes=99"));
    Test.assertEqual(m.storageDown(), false);
    return true;
}

(:test)
function testStats_rebootedPuckCannotZeroTheLiveCount(logger) as Boolean {
    // THE SCENARIO (adversary lens, 2026-08-21): forty minutes into a session
    // a hard landing browns out a puck on a low cell. It restarts; its session
    // counters are RAM statics, so the next STATS carries session_jumps=0.
    // JumpFieldView writes SESSION FIT fields every compute() tick, so an
    // unconditional reseed puts "0 jumps, best 0.00" into the SAVED activity
    // for a ride that had real jumps. Silent and permanent.
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "JUMP n=7 airtime_raw_s=1.021 airtime_s=1.036 height_m=1.316 " +
        "height_ft=4.3 best_m=1.316"));
    Test.assert(m.jumpCount() == 7);

    // The rebooted puck's first STATS.
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=0 session_best_m=0.000 session_best_airtime_s=0.000 " +
        "stored_jumps=41 stored_best_m=1.316 trace_bytes=0 vbat_mv=3402 " +
        "batt_pct=4 chg=0"));

    Test.assert(m.jumpCount() == 7);          // not 0
    Test.assert(m.sessionBestM() > 1.3);      // not 0.0
    return true;
}

(:test)
function testStats_wrongIdlePuckCannotZeroTheLiveCount(logger) as Boolean {
    // Same guard, different door: the walk back up the beach with the activity
    // still running. The link drops on the last few metres, the scan re-runs,
    // and a DIFFERENT board leaning against the truck is now the nearest
    // advertiser. It is idle, so its counters are zero.
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "JUMP n=12 airtime_raw_s=0.900 airtime_s=0.926 height_m=1.050 " +
        "height_ft=3.4 best_m=1.050"));
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=0 session_best_m=0.000 stored_jumps=0 " +
        "stored_best_m=0.000 trace_bytes=0"));
    Test.assert(m.jumpCount() == 12);
    Test.assert(m.sessionBestM() > 1.0);
    return true;
}

(:test)
function testStats_stillReseedsUPWARDafterAColdStart(logger) as Boolean {
    // The guard must not break US6, the reason reseed exists: a watch that
    // joins mid-session (or restarts) must still adopt the puck's HIGHER
    // totals. Only decreases are refused.
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=9 session_best_m=1.400 stored_jumps=9 " +
        "stored_best_m=1.400 trace_bytes=1000"));
    Test.assert(m.jumpCount() == 9);
    Test.assert(m.sessionBestM() > 1.39);
    return true;
}
