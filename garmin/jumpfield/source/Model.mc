// Model.mc
//
// Pure session state: what the last jump was, the session's best, how many
// jumps, and whether the data on screen is fresh. Consumes parsed lines
// (Protocol.parseKV's Dictionary shape) — never touches BLE or WatchUi, so
// it is unit-testable in the simulator exactly like Protocol.mc (see
// garmin/jumpfield/tests/ModelTest.mc). Toybox.System is fine here (just a
// millisecond clock, System.getTimer()); it's Toybox.BluetoothLowEnergy and
// Toybox.WatchUi that would break the "no hardware" testability this file
// is built around.
//
// Ownership split, deliberate: PuckLink decides IF the link is up (its own
// state machine — SCANNING/LIVE/DEAD); Model only knows whether it has EVER
// received real data (hasData()) and when it last did (for a future "stale
// for Ns" readout, not required by spec §4.2's fixed sub-text strings, but
// cheap to keep). JumpFieldView combines both signals to pick one of the
// four spec §4.2 states — Model never needs to know PuckLink's state names.

using Toybox.System;
using Toybox.Lang;

module Model {

    const FLASH_MS = 5000;  // new-jump invert-flash duration (spec §4.2)

    class State {

        hidden var _lastHeightM as Float;
        hidden var _lastAirtimeS as Float;
        hidden var _sessionBestM as Float;
        hidden var _bestAirtimeS as Float;   // longest airtime seen (independent
                                              // max from best HEIGHT — mirrors
                                              // web/app.js's sessionSummary()
                                              // "longestAir", since the wire
                                              // protocol has no best_airtime_s
                                              // field of its own; see FitOut.mc)
        hidden var _jumpCount as Number;
        hidden var _lastUpdateMs;             // null until first JUMP/STATS
        hidden var _staleSinceMs;             // set by markStale(); informational
        hidden var _flashUntilMs as Number;
        hidden var _newJumpPending as Boolean;

        function initialize() {
            _lastHeightM = 0.0;
            _lastAirtimeS = 0.0;
            _sessionBestM = 0.0;
            _bestAirtimeS = 0.0;
            _jumpCount = 0;
            _lastUpdateMs = null;
            _staleSinceMs = null;
            _flashUntilMs = 0;
            _newJumpPending = false;
        }

        // Consume one parsed line. Unknown tags (READY, STATE, INFO, PARAMS,
        // CAL, SELFTEST, '#' chatter, OK/ERR — everything but JUMP/STATS) are
        // ignored by simply not matching below, which is exactly spec §5.2's
        // "must tolerate unknown lines/keys": a sideloaded field can't be
        // updated in lockstep with firmware, so tolerance has to live here,
        // not behind a protocol-version check.
        function onLine(kv as Dictionary) as Void {
            if (kv == null) {
                return;
            }
            var tag = kv.get("_tag");
            if (tag == null) {
                return;
            }
            if (tag.equals("JUMP")) {
                _applyJump(kv);
            } else if (tag.equals("STATS")) {
                _applyStats(kv);
            }
        }

        // True once any real data has ever arrived — this (not PuckLink's
        // connection state) is what distinguishes SEARCHING ("--", never
        // connected) from RECONNECTING (retain + dim, spec §4.2) at the View.
        function hasData() as Boolean {
            return _lastUpdateMs != null;
        }

        // PuckLink calls this on LIVE -> SCANNING (spec §5.4 "retain model,
        // mark stale"). Data fields are NOT reset here on purpose — retaining
        // the last good numbers through a reconnect is the point.
        function markStale() as Void {
            _staleSinceMs = System.getTimer();
        }

        function isFlashing() as Boolean {
            return _flashUntilMs > 0 && System.getTimer() < _flashUntilMs;
        }

        // One-shot latch: true exactly once per JUMP, for whoever polls next
        // (JumpFieldView.compute(), spec's ~1 Hz cadence) to fire the vibrate
        // + the FIT RECORD write. Consuming (not just reading) it means a
        // slow poller never double-fires on the same jump.
        function consumeNewJump() as Boolean {
            var v = _newJumpPending;
            _newJumpPending = false;
            return v;
        }

        function lastHeightM() as Float { return _lastHeightM; }
        function lastAirtimeS() as Float { return _lastAirtimeS; }
        function sessionBestM() as Float { return _sessionBestM; }
        function bestAirtimeS() as Float { return _bestAirtimeS; }
        function jumpCount() as Number { return _jumpCount; }

        hidden function _applyJump(kv as Dictionary) as Void {
            var h = _toFloat(kv.get("height_m"));
            var a = _toFloat(kv.get("airtime_s"));
            var b = _toFloat(kv.get("best_m"));
            var n = _toNumber(kv.get("n"));
            // The device is the source of truth for height/airtime/best
            // (spec §5.2) — a malformed or missing field leaves the prior
            // value in place rather than blanking a good number to zero.
            if (h != null) { _lastHeightM = h; }
            if (a != null) {
                _lastAirtimeS = a;
                if (a > _bestAirtimeS) { _bestAirtimeS = a; }
            }
            if (b != null) { _sessionBestM = b; }
            if (n != null) { _jumpCount = n; }
            var now = System.getTimer();
            _lastUpdateMs = now;
            _flashUntilMs = now + Model.FLASH_MS;  // fully qualified even though
                                                    // State is nested inside
                                                    // Model -- removes any doubt
                                                    // about nested-scope lookup
            _newJumpPending = true;
        }

        hidden function _applyStats(kv as Dictionary) as Void {
            // Reconnect/late-join reseed only (US6). session_* fields only —
            // stored_* describes the device's flash archive, not this live
            // session, and is deliberately ignored (spec §5.2).
            var n = _toNumber(kv.get("session_jumps"));
            var b = _toFloat(kv.get("session_best_m"));
            if (n != null) { _jumpCount = n; }
            if (b != null) { _sessionBestM = b; }
            _lastUpdateMs = System.getTimer();
            // STATS never arms the flash or the vibrate latch: a reconnect
            // reseed is quiet by design, only a live JUMP is "news".
        }

        hidden function _toFloat(s) {
            if (s == null) { return null; }
            return s.toFloat();
        }

        hidden function _toNumber(s) {
            if (s == null) { return null; }
            return s.toNumber();
        }
    }
}
