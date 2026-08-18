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
import Toybox.Lang;

module Model {

    const FLASH_MS = 5000;  // new-jump invert-flash duration (spec §4.2)

    // ---- corruption gate -----------------------------------------------
    //
    // "The device is the source of truth" (spec §5.2) is only true of a line
    // that ARRIVED INTACT. If bytes go missing mid-stream — including the
    // bytes carrying a '\n' — LineReader glues the surviving fragments into a
    // single line, and because parseKV is last-key-wins that merge parses
    // PERFECTLY. The result is a syntactically valid JUMP holding values the
    // device never sent together, which the Model then displays as fact.
    //
    // Observed on the wrist 2026-08-11: a jump count of 64 and a best of
    // 0.3 ft at a moment the puck itself reported session_jumps=1 and
    // session_best_m=0.164. Silently wrong is the worst failure class there
    // is, so every line now has to prove itself first.
    //
    // Every check below is either a protocol invariant or a physical
    // impossibility — NOT a tuned threshold. Nothing here can reject a line
    // the firmware would actually emit.

    const MAX_HEIGHT_M  = 30.0;  // ~5x any real wing jump; rejects nonsense only
    const MAX_AIRTIME_S = 6.0;   // 6 s of airtime is a 44 m jump

    // best_m is computed from session_best, which firmware updates BEFORE
    // emitting the line it appears on (main.cpp: `if (ev.height_m >
    // session_best) session_best = ev.height_m;` immediately precedes the
    // emitf). So best_m >= height_m is guaranteed on the wire, and a line
    // where best < last is proof of corruption. Epsilon covers the %.3f
    // rounding, nothing more.
    const BEST_EPS_M = 0.0015;

    // The glue test (a JUMP carrying STATS-only keys, or the reverse) is
    // written as plain inline kv.get() chains inside State below — NOT as
    // module-level helpers taking an array of key names.
    //
    // Two reasons, both learned the hard way on 2026-08-11:
    //   1. The array-of-names version allocated two Arrays on EVERY received
    //      line. Spec §5.6 budgets NO per-callback allocations in steady
    //      state, and a data field is the one place that budget is real.
    //   2. It reached module scope from inside the nested State class
    //      (Model._hasAnyKey(...)). That resolved fine in the simulator and
    //      the device answered with `System Error: Failed invoking <symbol>`
    //      on the first line it received — the same simulator-vs-silicon
    //      divergence class as FIRST_COMPILE.md #3.
    // Inline kv.get() chains allocate nothing and cross no scope boundary.

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
        hidden var _puckBattPct;              // null until a STATS carries
                                              // batt_pct — the vbat_mv/
                                              // batt_pct/chg adder keys
                                              // (docs/sense.md §3.4) only
                                              // exist on battery-sensing
                                              // pucks; a v1 device never
                                              // sends them and this stays
                                              // null forever (View shows
                                              // nothing, spec §5.2
                                              // tolerate-unknown rule in
                                              // reverse)
        hidden var _puckCharging as Boolean;
        hidden var _rejected as Number;       // lines dropped by the corruption
                                               // gate; a nonzero value means the
                                               // link is delivering damaged data

        function initialize() {
            _lastHeightM = 0.0;
            _lastAirtimeS = 0.0;
            _sessionBestM = 0.0;
            _bestAirtimeS = 0.0;
            _jumpCount = 0;
            _rejected = 0;
            _lastUpdateMs = null;
            _staleSinceMs = null;
            _puckBattPct = null;
            _puckCharging = false;
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
        function puckBattPct() { return _puckBattPct; }  // Number or null
        function puckCharging() as Boolean { return _puckCharging; }
        function rejectedCount() as Number { return _rejected; }

        // Returns true if this JUMP line cannot have come intact off the wire.
        // Compatible with spec §5.2's "tolerate unknown lines/keys (skip)":
        // unknown keys still pass through untouched — what gets skipped here
        // is a line that CONTRADICTS ITSELF, which is a corrupt line, not an
        // unknown one.
        hidden function _jumpIsCorrupt(kv as Dictionary) as Boolean {
            var h = _toFloat(kv.get("height_m"));
            var a = _toFloat(kv.get("airtime_s"));
            var b = _toFloat(kv.get("best_m"));
            var n = _toNumber(kv.get("n"));

            // 1. Completeness. Firmware emits all of these on every JUMP
            //    (main.cpp's single emitf), so a missing one means bytes were
            //    lost, and the surviving fragment must not be half-applied.
            if (h == null || a == null || b == null || n == null) {
                return true;
            }
            // 2. Two lines glued by a lost newline: STATS-only keys riding on
            //    a JUMP tag. Battery adder keys are deliberately absent from
            //    this list — they legitimately ride on more than one line
            //    type (docs/sense.md §3.4).
            if (kv.get("session_jumps") != null) { return true; }
            if (kv.get("session_best_m") != null) { return true; }
            if (kv.get("stored_jumps") != null) { return true; }
            if (kv.get("stored_best_m") != null) { return true; }
            if (kv.get("trace_bytes") != null) { return true; }
            // 3. Physically impossible.
            if (h < 0.0 || h > Model.MAX_HEIGHT_M) { return true; }
            if (a < 0.0 || a > Model.MAX_AIRTIME_S) { return true; }
            if (n < 0) { return true; }
            // 4. The wire invariant: session_best is updated before the line
            //    is emitted, so best can never be below the jump it reports.
            //    This one alone would have caught the 2026-08-11 corruption.
            if (b + Model.BEST_EPS_M < h) { return true; }
            return false;
        }

        hidden function _applyJump(kv as Dictionary) as Void {
            if (_jumpIsCorrupt(kv)) {
                _rejected += 1;
                return;  // drop the whole line: one lost jump beats a wrong one
            }
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
            // Same glue test, mirrored: JUMP-only keys riding a STATS tag mean
            // two lines were merged. A corrupt STATS is worse than a corrupt
            // JUMP — it reseeds count AND best in one go (US6), so a bad one
            // poisons the whole session display until the next reconnect.
            if (kv.get("airtime_raw_s") != null || kv.get("airtime_s") != null
             || kv.get("height_m") != null || kv.get("height_ft") != null
             || kv.get("best_m") != null) {
                _rejected += 1;
                return;
            }
            var sb = _toFloat(kv.get("session_best_m"));
            if (sb != null && (sb < 0.0 || sb > Model.MAX_HEIGHT_M)) {
                _rejected += 1;
                return;
            }
            // COMPLETENESS. The JUMP gate demands every field be present; this
            // one demanded nothing, while STATS is the LONGER and more
            // chunk-exposed line — and the one that reseeds count AND best in
            // a single go. A line truncated mid-way can survive as
            // "STATS session_jumps=<spliced>" and reseed the whole session
            // display from a fragment.
            //
            // stored_jumps and trace_bytes are emitted unconditionally by BOTH
            // branches of the firmware's STATS emitf, and BOTH sit AFTER
            // session_best_m on the wire — so their presence proves the line
            // survived past the fields being consumed here. Two lookups, no
            // allocation, matching the inline style proven on silicon.
            if (kv.get("stored_jumps") == null || kv.get("trace_bytes") == null) {
                _rejected += 1;
                return;
            }

            // Reconnect/late-join reseed only (US6). session_* fields only —
            // stored_* describes the device's flash archive, not this live
            // session, and is deliberately ignored (spec §5.2).
            var n = _toNumber(kv.get("session_jumps"));
            var b = _toFloat(kv.get("session_best_m"));
            if (n != null) { _jumpCount = n; }
            if (b != null) { _sessionBestM = b; }
            // Best-airtime reseed (adder key, firmware >= 2026-08-18). Found by
            // parsing the M2 activity FIT: best_jump reconciled to the stored
            // best while best_airtime stayed at the live-seen max, because
            // STATS carried no airtime. Same sanity bound family as heights;
            // absent key leaves prior state (v1-style lines stay valid).
            var ba = _toFloat(kv.get("session_best_airtime_s"));
            if (ba != null && ba >= 0.0 && ba <= 10.0 && ba > _bestAirtimeS) {
                _bestAirtimeS = ba;
            }
            // Battery adder keys (absent on v1 pucks — leave prior state).
            var bp = _toNumber(kv.get("batt_pct"));
            if (bp != null) { _puckBattPct = bp; }
            var chg = _toNumber(kv.get("chg"));
            if (chg != null) { _puckCharging = (chg == 1); }
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
