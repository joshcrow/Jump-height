// PuckLinkPolicyTest.mc
//
// F-12: PuckLink.poll() used to return immediately unless the state was
// SCANNING, so the three callback-driven states — PAIRING, DISCOVERING,
// SUBSCRIBING — had no way out if a Garmin BLE callback never arrived. The
// state machine parked forever: no scan, no reconnect, no error, just a field
// that stopped updating for the rest of the ride.
//
// The teardown around the fix (unpairDevice, rescan) cannot be unit-tested —
// the spec bars the test build from exercising BLE. The DECISION can be, and
// that is where the bug lived, so it is a static pure function taking the
// clock as an argument instead of reading it.

using Toybox.Test;
import Toybox.Lang;

(:test)
function testConnectTimeout_firesOnlyInCallbackDrivenStates(logger) {
    var deadline = 1000;
    var late = 1001;

    // The three states with no other way out.
    Test.assertEqual(
        PuckLink.connectAttemptExpired(PuckLink.STATE_PAIRING, deadline, late), true);
    Test.assertEqual(
        PuckLink.connectAttemptExpired(PuckLink.STATE_DISCOVERING, deadline, late), true);
    Test.assertEqual(
        PuckLink.connectAttemptExpired(PuckLink.STATE_SUBSCRIBING, deadline, late), true);

    // SCANNING has its own backoff and must keep it — tearing that down here
    // would fight the retry logic instead of complementing it.
    Test.assertEqual(
        PuckLink.connectAttemptExpired(PuckLink.STATE_SCANNING, deadline, late), false);
    // LIVE is the healthy steady state: a connected puck that simply has not
    // jumped in 20 s must never be torn down.
    Test.assertEqual(
        PuckLink.connectAttemptExpired(PuckLink.STATE_LIVE, deadline, late), false);
    Test.assertEqual(
        PuckLink.connectAttemptExpired(PuckLink.STATE_IDLE, deadline, late), false);
    Test.assertEqual(
        PuckLink.connectAttemptExpired(PuckLink.STATE_DEAD, deadline, late), false);
    return true;
}

(:test)
function testConnectTimeout_doesNotFireEarly(logger) {
    // A connect in progress but inside its budget is left alone. Healthy
    // connects are sub-second here (2,068/2,068 reconnects, p95 1.99 s), so
    // this must not become a way to interrupt a merely slow one.
    Test.assertEqual(
        PuckLink.connectAttemptExpired(PuckLink.STATE_PAIRING, 1000, 999), false);
    // Exactly at the deadline counts as expired.
    Test.assertEqual(
        PuckLink.connectAttemptExpired(PuckLink.STATE_PAIRING, 1000, 1000), true);
    return true;
}

(:test)
function testConnectTimeout_disarmedDeadlineNeverFires(logger) {
    // 0 means "not connecting". Without this the flag would fire on every
    // poll() in a state it happened to match, which is worse than the bug.
    Test.assertEqual(
        PuckLink.connectAttemptExpired(PuckLink.STATE_PAIRING, 0, 999999), false);
    return true;
}
