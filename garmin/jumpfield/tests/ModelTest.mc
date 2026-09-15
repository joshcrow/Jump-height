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

// ---------------------------------------------------------------- F-11
// The JUMP path used to assign session best/count straight off the wire while
// the STATS path guarded them. That mattered more, not less: JUMP is the path
// JumpFieldView.compute() pushes into FitOut.updateSession() at ~1 Hz, so a
// decrease there lands in the PERMANENT activity record.

(:test)
function testJump_rebootReseedDoesNotDriveCountOrBestDown(logger) {
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "JUMP n=12 airtime_raw_s=0.92 airtime_s=0.93 height_m=1.05 height_ft=3.4 best_m=1.05"));
    Test.assertEqual(m.jumpCount(), 12);
    Test.assertEqual(m.sessionBestM(), 1.05);

    // The puck browns out. Its session counters are RAM statics, so they
    // reseed from zero and the next real jump reports n=1. Every field here is
    // individually plausible and the wire invariant (best >= height) holds, so
    // _jumpIsCorrupt cannot reject it. Only the comparison against what the
    // watch already knows can.
    m.onLine(Protocol.parseKV(
        "JUMP n=1 airtime_raw_s=0.39 airtime_s=0.40 height_m=0.20 height_ft=0.7 best_m=0.20"));

    Test.assertEqual(m.jumpCount(), 12);        // was 1 before F-11
    Test.assertEqual(m.sessionBestM(), 1.05);   // was 0.20 before F-11

    // But the jump itself is real and must still register: the newest height
    // is the one just landed, and the flash/vibrate still arm. Freezing those
    // too would trade a wrong number for a dead display.
    Test.assertEqual(m.lastHeightM(), 0.20);
    Test.assertEqual(m.lastAirtimeS(), 0.40);
    Test.assertEqual(m.isFlashing(), true);
    Test.assertEqual(m.consumeNewJump(), true);
    return true;
}

(:test)
function testJump_stillAdoptsHigherTotalsFromALateJoin(logger) {
    // The guard refuses DECREASES only. A watch that joins mid-session sees
    // its first JUMP carrying totals well above its own zeros and must take
    // them (US6) - otherwise the fix for one failure creates another.
    var m = new Model.State();
    m.onLine(Protocol.parseKV("JUMP n=1 airtime_s=0.5 height_m=0.3 best_m=0.3"));
    m.onLine(Protocol.parseKV("JUMP n=20 airtime_s=1.4 height_m=2.0 best_m=2.0"));
    Test.assertEqual(m.jumpCount(), 20);
    Test.assertEqual(m.sessionBestM(), 2.0);
    return true;
}

(:test)
function testJump_absurdBestIsRejectedEvenWhenItExceedsTheJump(logger) {
    // _jumpIsCorrupt bounded height but not best. Its rule 4 only asks that
    // best >= height, so best_m=9000 alongside a 0.5 m jump passed every
    // check and would have pinned session best at 9000 m for the activity -
    // and, being a maximum, nothing later could bring it back down.
    var m = new Model.State();
    m.onLine(Protocol.parseKV("JUMP n=1 airtime_s=0.5 height_m=0.3 best_m=0.3"));
    m.onLine(Protocol.parseKV("JUMP n=2 airtime_s=0.6 height_m=0.5 best_m=9000.0"));

    Test.assertEqual(m.jumpCount(), 1);         // whole line rejected...
    Test.assertEqual(m.sessionBestM(), 0.3);
    Test.assertEqual(m.lastHeightM(), 0.3);     // ...not half-applied
    return true;
}

// ------------------------------------------------- 1.0.2: THIS activity
//
// The failure these tests exist for is measured, not imagined. On
// 2026-09-12 the rider's saved activity carried best_jump =
// 11.131889343261719 ft — BIT-IDENTICAL to the 09-10 ride's — because the
// puck reports its stored session best until somebody clears it and the
// watch wrote it faithfully into a different day's file
// (docs/garmin-corpus-2026-09-15.md, finding 2).
//
// The rule under test: after DataField.onTimerStart, jumps/best/airtime
// describe THIS activity. Model never sees the callback itself; it sees
// beginActivity()/endActivity(), which is the whole reason this is testable
// with no simulator hardware at all.

(:test)
function testActivity_baselineAtTimerStartZeroesAStalePuckSession(logger) as Boolean {
    var m = new Model.State();
    // The rider's puck, un-cleared since Tuesday: 9 jumps, best 3.39 m.
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=9 session_best_m=3.392 session_best_airtime_s=1.664 " +
        "stored_jumps=20 stored_best_m=3.392 trace_bytes=182031"));
    Test.assertEqual(m.jumpCount(), 9);         // raw puck view, unchanged
    Test.assertEqual(m.sessionBestM(), 3.392);

    m.beginActivity();                          // Thursday's timer starts

    Test.assertEqual(m.activityJumps(), 0);     // ...and Thursday has no jumps
    Test.assertEqual(m.activityBestM(), 0.0);   // this is the 09-12 bug, dead
    Test.assertEqual(m.activityBestAirtimeS(), 0.0);
    // The whole-puck-session values are still there for anyone who wants them.
    Test.assertEqual(m.jumpCount(), 9);
    Test.assertEqual(m.sessionBestM(), 3.392);
    return true;
}

(:test)
function testActivity_countsAndBestsOnlyJumpsAfterTimerStart(logger) as Boolean {
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=9 session_best_m=3.392 stored_jumps=20 " +
        "stored_best_m=3.392 trace_bytes=182031"));
    m.beginActivity();

    m.onLine(Protocol.parseKV(
        "JUMP n=10 airtime_raw_s=0.60 airtime_s=0.62 height_m=0.47 height_ft=1.5 best_m=3.392"));
    Test.assertEqual(m.activityJumps(), 1);
    Test.assertEqual(m.activityBestM(), 0.47);   // NOT best_m=3.392 off the wire
    Test.assertEqual(m.activityBestAirtimeS(), 0.62);

    m.onLine(Protocol.parseKV(
        "JUMP n=11 airtime_raw_s=0.80 airtime_s=0.83 height_m=0.84 height_ft=2.8 best_m=3.392"));
    Test.assertEqual(m.activityJumps(), 2);
    Test.assertEqual(m.activityBestM(), 0.84);
    Test.assertEqual(m.activityBestAirtimeS(), 0.83);

    // A smaller jump must not lower either maximum.
    m.onLine(Protocol.parseKV(
        "JUMP n=12 airtime_raw_s=0.40 airtime_s=0.41 height_m=0.20 height_ft=0.7 best_m=3.392"));
    Test.assertEqual(m.activityJumps(), 3);
    Test.assertEqual(m.activityBestM(), 0.84);
    Test.assertEqual(m.activityBestAirtimeS(), 0.83);

    // And the raw puck view is untouched by any of it.
    Test.assertEqual(m.jumpCount(), 12);
    Test.assertEqual(m.sessionBestM(), 3.392);
    return true;
}

(:test)
function testActivity_armsWhenPuckIsNotYetConnectedAtTimerStart(logger) as Boolean {
    // The ordinary case on the beach: start the watch, then wake the board.
    // Model has no data at timer start, so the baseline is ARMED and taken
    // from the first STATS PuckLink's one `stats` write brings back.
    var m = new Model.State();
    Test.assertEqual(m.hasData(), false);
    m.beginActivity();
    Test.assertEqual(m.activityJumps(), 0);

    m.onLine(Protocol.parseKV(
        "STATS session_jumps=9 session_best_m=3.392 stored_jumps=20 " +
        "stored_best_m=3.392 trace_bytes=182031"));
    Test.assertEqual(m.activityJumps(), 0);      // the 9 predate this ride
    Test.assertEqual(m.activityBestM(), 0.0);

    m.onLine(Protocol.parseKV(
        "JUMP n=10 airtime_s=0.62 height_m=0.47 best_m=3.392"));
    Test.assertEqual(m.activityJumps(), 1);
    return true;
}

(:test)
function testActivity_armedBaselineStillCountsAJumpThatBeatsTheStats(logger) as Boolean {
    // The race: the baseline is armed and a JUMP arrives before the STATS.
    // That jump happened after the timer started, so it must count as this
    // activity's first — the baseline is taken at n-1, not at n.
    var m = new Model.State();
    m.beginActivity();
    m.onLine(Protocol.parseKV(
        "JUMP n=10 airtime_s=0.62 height_m=0.47 best_m=3.392"));
    Test.assertEqual(m.activityJumps(), 1);
    Test.assertEqual(m.activityBestM(), 0.47);

    // The STATS that follows on the same connect must not re-baseline and
    // swallow it.
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=10 session_best_m=3.392 stored_jumps=20 " +
        "stored_best_m=3.392 trace_bytes=182031"));
    Test.assertEqual(m.activityJumps(), 1);
    return true;
}

(:test)
function testActivity_rebootedPuckWithALowerCounterNeverGoesNegative(logger) as Boolean {
    // The scenario F-11 already guards at the raw level, now re-checked one
    // layer up: a puck that browns out mid-activity comes back with
    // session_jumps=0, well BELOW the baseline this activity was taken at.
    // A naive subtraction would be -12.
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=12 session_best_m=1.050 stored_jumps=30 " +
        "stored_best_m=1.316 trace_bytes=5000"));
    m.beginActivity();                            // baseline = 12
    m.onLine(Protocol.parseKV(
        "JUMP n=13 airtime_s=0.70 height_m=0.60 best_m=1.050"));
    Test.assertEqual(m.activityJumps(), 1);

    // Brownout. The rebooted puck's reseed.
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=0 session_best_m=0.000 session_best_airtime_s=0.000 " +
        "stored_jumps=41 stored_best_m=1.316 trace_bytes=0"));
    Test.assert(m.activityJumps() >= 0);
    Test.assertEqual(m.activityJumps(), 1);       // held, not dropped, not negative
    Test.assertEqual(m.activityBestM(), 0.60);    // a watch-side max; a reboot
                                                   // cannot touch it at all

    // A further jump from the rebooted puck reports n=1, which the raw
    // monotonic guard refuses (F-11) — so the activity count HOLDS rather
    // than advancing. Documented, not accidental: docs/watch.md's per-
    // activity section states this is the price of the F-11 guard.
    m.onLine(Protocol.parseKV(
        "JUMP n=1 airtime_s=0.50 height_m=0.30 best_m=0.30"));
    Test.assertEqual(m.activityJumps(), 1);
    Test.assertEqual(m.lastHeightM(), 0.30);      // but the glance still updates
    return true;
}

(:test)
function testActivity_endActivityKeepsThisActivitysNumbers(logger) as Boolean {
    // THE REGRESSION THIS TEST EXISTS FOR (adversarial review, 2026-09-15).
    // The first 1.0.2 draft cleared one flag in endActivity that also meant
    // "no activity has ever started", so onTimerReset dropped the field back
    // to the puck's raw, un-cleared session — putting the two-day-old best
    // back on the glass, and into the SESSION fields of any compute() tick
    // landing between the reset and the save. That is the 09-12 bug, in the
    // version written to kill it. Worse, the test that covered this asserted
    // the raw values as the EXPECTED result and passed.
    //
    // onTimerReset means "the current activity has ended", not "forget it".
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=9 session_best_m=3.392 session_best_airtime_s=1.664 " +
        "stored_jumps=20 stored_best_m=3.392 trace_bytes=182031"));
    m.beginActivity();
    Test.assertEqual(m.activityStarted(), true);
    Test.assertEqual(m.activityJumps(), 0);

    m.onLine(Protocol.parseKV(
        "JUMP n=10 airtime_s=0.62 height_m=0.47 best_m=3.392"));
    Test.assertEqual(m.activityJumps(), 1);

    m.endActivity();
    Test.assertEqual(m.activityStarted(), false);      // no longer inside one
    Test.assertEqual(m.activityEverStarted(), true);   // but one HAS happened
    Test.assertEqual(m.activityJumps(), 1);            // ...and these are ITS
    Test.assertEqual(m.activityBestM(), 0.47);         // numbers, not 9/3.392
    Test.assertEqual(m.activityBestAirtimeS(), 0.62);

    // The raw puck view is still exactly the puck's, untouched.
    Test.assertEqual(m.jumpCount(), 10);
    Test.assertEqual(m.sessionBestM(), 3.392);
    return true;
}

(:test)
function testActivity_jumpsAfterTheResetDoNotGrowTheEndedActivity(logger) as Boolean {
    // The gap between onTimerReset and the next onTimerStart: the puck may
    // still be live and counting. A jump landing there belongs to neither
    // activity, so the finished one must not creep — count frozen, maxima
    // frozen — while the raw puck view keeps tracking the wire.
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=5 session_best_m=1.000 stored_jumps=5 " +
        "stored_best_m=1.000 trace_bytes=100"));
    m.beginActivity();
    m.onLine(Protocol.parseKV(
        "JUMP n=6 airtime_s=0.50 height_m=0.40 best_m=1.000"));
    m.endActivity();
    Test.assertEqual(m.activityJumps(), 1);
    Test.assertEqual(m.activityBestM(), 0.40);

    // A big one, after the save.
    m.onLine(Protocol.parseKV(
        "JUMP n=7 airtime_s=1.10 height_m=1.50 best_m=1.500"));
    Test.assertEqual(m.activityJumps(), 1);        // frozen
    Test.assertEqual(m.activityBestM(), 0.40);     // frozen
    Test.assertEqual(m.jumpCount(), 7);            // the wire, still tracked
    Test.assertEqual(m.sessionBestM(), 1.500);

    // The NEXT activity starts clean, baselined at the puck's count now.
    m.beginActivity();
    Test.assertEqual(m.activityJumps(), 0);
    Test.assertEqual(m.activityBestM(), 0.0);
    Test.assertEqual(m.activityBestAirtimeS(), 0.0);
    m.onLine(Protocol.parseKV(
        "JUMP n=8 airtime_s=0.55 height_m=0.30 best_m=1.500"));
    Test.assertEqual(m.activityJumps(), 1);
    Test.assertEqual(m.activityBestM(), 0.30);     // NOT 1.50, NOT 0.40
    return true;
}

(:test)
function testActivity_midRideStopStartDoesNotRebaseline(logger) as Boolean {
    // onTimerStart fires again when the rider resumes a paused activity.
    // JumpFieldView gates on activityStarted(), which is still true inside
    // an activity, so the resume is a no-op and the ride's count survives.
    // (If it did not, a beach pause would silently zero the ride.)
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=2 session_best_m=0.900 stored_jumps=2 " +
        "stored_best_m=0.900 trace_bytes=50"));
    m.beginActivity();
    m.onLine(Protocol.parseKV(
        "JUMP n=3 airtime_s=0.70 height_m=0.80 best_m=0.900"));
    Test.assertEqual(m.activityJumps(), 1);

    Test.assertEqual(m.activityStarted(), true);   // the gate the View reads
    // ...so beginActivity() is NOT called again. Assert what that preserves.
    m.onLine(Protocol.parseKV(
        "JUMP n=4 airtime_s=0.60 height_m=0.50 best_m=0.900"));
    Test.assertEqual(m.activityJumps(), 2);
    Test.assertEqual(m.activityBestM(), 0.80);
    return true;
}

(:test)
function testActivity_neverStartedBehavesExactlyLikeTheOldBuild(logger) as Boolean {
    var m = new Model.State();
    m.onLine(Protocol.parseKV(
        "JUMP n=4 airtime_raw_s=1.021 airtime_s=1.036 height_m=1.316 " +
        "height_ft=4.3 best_m=1.316"));
    Test.assertEqual(m.activityStarted(), false);
    Test.assertEqual(m.activityEverStarted(), false);  // the ONLY fallback
    Test.assertEqual(m.activityJumps(), m.jumpCount());
    Test.assertEqual(m.activityBestM(), m.sessionBestM());
    Test.assertEqual(m.activityBestAirtimeS(), m.bestAirtimeS());
    return true;
}

// ------------------------------------------------- 1.0.2: err_code (Err.mc)
//
// Every bare catch in the app reports a class code into this sink, and the
// FIT carries the sticky MAXIMUM. The rule is deliberately "max", not "last"
// or "first": a max cannot be walked backwards by a later, milder failure,
// which is exactly how a one-off fatal class would otherwise disappear from
// a file written once per second for two hours.

(:test)
function testErr_startsAtZeroMeaningNothingWasCaught(logger) as Boolean {
    var m = new Model.State();
    Test.assertEqual(m.errCode(), 0);
    return true;
}

(:test)
function testErr_isStickyAndTakesTheMaximum(logger) as Boolean {
    var m = new Model.State();
    m.noteErr(Err.E_PROP_UNIT);        // 2
    Test.assertEqual(m.errCode(), Err.E_PROP_UNIT);

    m.noteErr(Err.E_DECODE);           // 15 — worse, so it wins
    Test.assertEqual(m.errCode(), Err.E_DECODE);

    m.noteErr(Err.E_VIBRATE);          // 4 — milder, must NOT displace it
    Test.assertEqual(m.errCode(), Err.E_DECODE);

    m.noteErr(Err.E_DECODE);           // idempotent
    Test.assertEqual(m.errCode(), Err.E_DECODE);
    return true;
}

(:test)
function testErr_survivesEverythingElseTheModelDoes(logger) as Boolean {
    // The code must not be cleared by a reconnect reseed, by staleness, or
    // by an activity boundary: its scope is the RUN of the app, not the
    // activity (Err.mc's SCOPE note, and docs/watch.md's field table say the
    // same in the same words — the 2026-09-15 review caught them saying
    // "activity" while the code cleared it nowhere). prev_err carries the
    // number one run further.
    var m = new Model.State();
    m.noteErr(Err.E_BLE_SUBSCRIBE);
    m.onLine(Protocol.parseKV(
        "STATS session_jumps=3 session_best_m=0.9 stored_jumps=3 " +
        "stored_best_m=0.9 trace_bytes=10"));
    m.markStale();
    m.beginActivity();
    m.endActivity();
    m.onLine(Protocol.parseKV("JUMP n=4 airtime_s=0.5 height_m=0.3 best_m=0.9"));
    Test.assertEqual(m.errCode(), Err.E_BLE_SUBSCRIBE);
    return true;
}

(:test)
function testErr_codesAreDistinctAndOrderedByConsequence(logger) as Boolean {
    // The table in Err.mc is load-bearing: a saved FIT on the rider's watch
    // is a permanent record whose only meaning is that numbering. This
    // checks the two properties the reader relies on — the groups do not
    // overlap, and every code fits the UINT8 the field is declared as.
    Test.assert(Err.E_PROP_PUCKNAME > 0);
    Test.assert(Err.E_OBSCURITY < Err.E_BLE_NAME);          // UI < BLE
    Test.assert(Err.E_BLE_REGISTER < Err.E_DECODE);         // BLE < wire
    Test.assert(Err.E_PARSE < Err.E_FIT_BARO);              // wire < FIT
    Test.assert(Err.E_FIT_INIT < Err.E_STORE_READ);         // FIT < store
    Test.assert(Err.E_HEALTH <= 255);
    return true;
}
