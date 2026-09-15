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
//
// 1.0.2 (2026-09-15) added two things that are session state by the same
// definition and so live here rather than in a new global: the per-ACTIVITY
// view of the puck's counters (activityJumps/activityBestM/
// activityBestAirtimeS, baselined at DataField.onTimerStart) and the sticky
// error code every bare catch in the app reports into (noteErr/errCode, see
// Err.mc). Both are pure state with no hardware dependency, so both are unit
// -tested in tests/ModelTest.mc exactly like everything else here.

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
        hidden var _storageDown as Boolean;   // puck flash not mounted — it
                                              // looks healthy and records
                                              // NOTHING (measured 2026-08-19:
                                              // a reboot on a flat cell leaves
                                              // the QSPI unmounted while BLE
                                              // and sensor are fine). Reported
                                              // by the STATS adder key fs=down.
        hidden var _rejected as Number;       // lines dropped by the corruption
                                               // gate; a nonzero value means the
                                               // link is delivering damaged data

        // ---- THIS ACTIVITY, as opposed to the puck's stored session -------
        //
        // Added 2026-09-15, and the reason is a measured wrong number on the
        // rider's wrist. On 09-12 the saved activity's session field
        // best_jump was 11.131889343261719 ft — BIT-IDENTICAL to 09-10's —
        // because the puck reports its stored session best until somebody
        // clears it, and the fields above faithfully mirrored the puck
        // (docs/garmin-corpus-2026-09-15.md, finding 2). A rider who has not
        // cleared the puck since Tuesday sees Tuesday's best on Thursday's
        // ride and it is written into Thursday's FIT.
        //
        // The fix is NOT to change what the wire means. _jumpCount /
        // _sessionBestM / _bestAirtimeS above still hold exactly what the
        // puck says about its own session, unchanged, gates and monotonic
        // guards intact — every corruption test still exercises them. What
        // is added is a second, narrower view taken RELATIVE TO TIMER START.
        //
        // Count is a subtraction (the puck's counter is the only count there
        // is, and it is monotonic here by construction). Best height and
        // best airtime are NOT subtractions — a maximum cannot be undone by
        // arithmetic — so they are watch-side running maxima over the JUMP
        // lines that arrived after the timer started. That also means they
        // never inherit a stale puck best in the first place, which is the
        // 09-12 failure at its root.
        hidden var _baseJumps as Number;      // puck's raw count at timer start
        hidden var _actBestM as Float;        // max height seen since then
        hidden var _actBestAirtimeS as Float; // max airtime seen since then

        // TWO booleans, not one, and the difference is a fixed bug (adversarial
        // review, 2026-09-15). A single flag had to carry two unrelated facts —
        // "this device has never delivered onTimerStart, so degrade to the
        // pre-1.0.2 build" and "this activity has ended" — and onTimerReset
        // therefore dropped the field back to the puck's raw, un-cleared
        // session: the exact 09-12 symptom this version exists to kill,
        // restored on the glass and reachable by any compute() tick that lands
        // after the reset.
        hidden var _actEverStarted as Boolean; // onTimerStart has fired at least
                                               // ONCE in this run of the app.
                                               // NEVER cleared. False is the only
                                               // thing that falls back to the raw
                                               // puck-session values, so a device
                                               // that never delivers the callback
                                               // degrades to the old build rather
                                               // than to zeros
        hidden var _actStarted as Boolean;     // currently INSIDE an activity:
                                               // set by beginActivity, cleared by
                                               // endActivity. Governs whether a
                                               // new onTimerStart re-baselines
                                               // (it is a fresh activity) or is a
                                               // mid-ride stop/start (it is not)
        hidden var _endJumps as Number;        // activityJumps() frozen at
                                               // endActivity, so the just-ended
                                               // activity's count stays correct
                                               // even if the puck keeps counting
                                               // between the reset and the next
                                               // start
        hidden var _baselineArmed as Boolean; // timer started with no puck data
                                               // yet; the first line that arrives
                                               // supplies the baseline

        // Sticky MAX of the error classes caught anywhere in the app during
        // this RUN of it (Err.mc — the scope is per run, not per activity,
        // and nothing here clears it at an activity boundary; see that
        // file's SCOPE note). Lives here because Model.State is the one object
        // every other part already holds a reference to — PuckLink is
        // constructed with it, JumpFieldView owns it, FitOut is handed it —
        // so no new global, no module-level function called from a nested
        // class (the exact shape that threw `System Error: Failed invoking
        // <symbol>` on silicon; see this file's inline-kv note above).
        hidden var _errCode as Number;

        function initialize() {
            _lastHeightM = 0.0;
            _lastAirtimeS = 0.0;
            _sessionBestM = 0.0;
            _bestAirtimeS = 0.0;
            _jumpCount = 0;
            _rejected = 0;
            _storageDown = false;
            _lastUpdateMs = null;
            _staleSinceMs = null;
            _puckBattPct = null;
            _puckCharging = false;
            _flashUntilMs = 0;
            _newJumpPending = false;
            _baseJumps = 0;
            _actBestM = 0.0;
            _actBestAirtimeS = 0.0;
            _actEverStarted = false;
            _actStarted = false;
            _endJumps = 0;
            _baselineArmed = false;
            _errCode = 0;
        }

        // ---- error sink (Err.mc) ------------------------------------------

        // Sticky max. Called from inside bare catch blocks, so it must not be
        // able to throw: one comparison, one assignment, no allocation.
        function noteErr(code as Number) as Void {
            if (code > _errCode) { _errCode = code; }
        }

        function errCode() as Number { return _errCode; }

        // ---- per-activity session (DataField.onTimerStart/onTimerReset) ----

        // The activity timer went from stopped to started. Take the baseline
        // now if the puck is already talking; otherwise ARM, and the first
        // line to arrive supplies it (PuckLink writes `stats` once per
        // connect, so in practice that is a STATS).
        //
        // Called only for the FIRST start of an ACTIVITY — a stop/start in
        // the middle of a ride must NOT re-baseline the count to zero. The
        // caller uses activityStarted(), which is true only between
        // beginActivity and endActivity, so a resume is a no-op and the
        // first start after a reset re-baselines. onTimerReset ("the current
        // activity has ended", SDK doc/Toybox/WatchUi/DataField.html) is the
        // only thing that re-arms it.
        function beginActivity() as Void {
            _actEverStarted = true;
            _actStarted = true;
            _actBestM = 0.0;
            _actBestAirtimeS = 0.0;
            _endJumps = 0;
            if (hasData()) {
                _baseJumps = _jumpCount;
                _baselineArmed = false;
            } else {
                _baseJumps = 0;
                _baselineArmed = true;
            }
        }

        // The activity ended. It does NOT drop back to the raw puck-session
        // view — that fallback exists only for a device that has never
        // delivered onTimerStart at all (_actEverStarted). Dropping back here
        // would put the puck's un-cleared, possibly days-old best back on the
        // glass, and into the SESSION fields of any compute() tick that lands
        // between the reset and the save.
        //
        // The baseline and both maxima are RETAINED, and the count is frozen
        // at what it was, so the just-ended activity's numbers stay correct
        // and stay still — a jump landed in the gap before the next start
        // belongs to neither activity, and must not creep into the one that
        // is over.
        function endActivity() as Void {
            _endJumps = activityJumps();
            _actStarted = false;
            _baselineArmed = false;
        }

        // True only INSIDE an activity. Not "has an activity ever started" —
        // see the two flags' declarations.
        function activityStarted() as Boolean { return _actStarted; }

        // True once onTimerStart has ever fired in this run. Exposed for the
        // tests, and because "which fallback am I in" is a question a reader
        // of this class will ask.
        function activityEverStarted() as Boolean { return _actEverStarted; }

        // Jumps in THIS activity. Clamped at zero: the raw count is already
        // monotonic (the F-11 guards in _applyJump/_applyStats refuse any
        // decrease), so a puck that reboots mid-activity and comes back
        // reporting session_jumps=0 cannot drive this negative — it cannot
        // move the raw count at all. The clamp is belt-and-braces for the one
        // case the guards do not cover: a baseline taken from a HIGHER count
        // than we later hold, which no code path produces today.
        function activityJumps() as Number {
            if (!_actEverStarted) { return _jumpCount; }   // pre-1.0.2 fallback
            if (!_actStarted) { return _endJumps; }        // frozen at reset
            var n = _jumpCount - _baseJumps;
            return (n > 0) ? n : 0;
        }

        // KNOWN, UNFIXED, AND DELIBERATELY NOT PAPERED OVER: a mid-activity
        // restart of the data field (OOM, an uncaught error, a watch reboot)
        // builds a fresh State, re-baselines against the puck's current
        // count, and this activity's jump tally restarts at 0 — which is
        // then written into the saved FIT. docs/glue-and-forget.md §3b
        // names the fix (persist the baseline in Application.Storage) and
        // also names why it is not done here: doing it safely needs SESSION
        // IDENTITY, so that a baseline left behind by a dead run is not
        // silently applied to the next, unrelated activity. That is open
        // work, not something to guess at inside this change. The
        // hasData() guard in JumpFieldView.compute() still stops the
        // pre-reseed zero from reaching the file; it cannot stop the
        // post-reseed one.

        // Best height / airtime in THIS activity, in metres and seconds.
        // Falls back to the whole-puck-session value ONLY when no timer-start
        // callback has ever arrived, so the old behaviour is still reachable
        // and nothing is lost on a device that does not deliver the event —
        // and, critically, an activity that has ENDED keeps its own maxima
        // rather than reverting to the puck's stale session best.
        function activityBestM() as Float {
            return _actEverStarted ? _actBestM : _sessionBestM;
        }

        function activityBestAirtimeS() as Float {
            return _actEverStarted ? _actBestAirtimeS : _bestAirtimeS;
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
        function storageDown() as Boolean { return _storageDown; }

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
            // best_m gets the same bound as height (F-11). Rule 4 below only
            // requires best >= this jump, so without this a line reporting
            // height 0.5 and best 9000 passed every check.
            if (b < 0.0 || b > Model.MAX_HEIGHT_M) { return true; }
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
            // MONOTONIC, same as the STATS path below (F-11, audit
            // 2026-08-22). The 2026-08-21 guard was applied to STATS and to
            // best-airtime and NOT here, so the JUMP path could still drive
            // both fields DOWN — and this is the path that runs at ~1 Hz into
            // FitOut.updateSession(), i.e. into the permanent record.
            //
            // The firmware's session counters are RAM statics (main.cpp), so a
            // brownout mid-session reseeds them and the very next real jump
            // emits a perfectly well-formed "JUMP n=1 ... best_m=0.20" after a
            // 12-jump session. _jumpIsCorrupt() cannot reject that line —
            // every field is individually plausible and the wire invariant
            // holds. Only the comparison against what we already know catches
            // it, and only here.
            //
            // Within one activity the live count can only grow, so refusing a
            // decrease costs nothing real; across activities the field is
            // reconstructed anyway. A watch joining mid-session must still
            // adopt HIGHER totals, which is why this is > and not "ignore".
            if (b != null && b > _sessionBestM) { _sessionBestM = b; }
            if (n != null && n > _jumpCount) { _jumpCount = n; }
            // THIS ACTIVITY. A JUMP line that arrives now is a jump that
            // happened now, so it counts even if the baseline was still
            // armed: consume the arm at n-1 so this jump reads as the
            // activity's first, rather than being swallowed by a baseline
            // taken after the fact. (In practice STATS wins the race —
            // PuckLink writes `stats` on subscribe — but "in practice" is
            // not a guarantee and this costs two lines.)
            if (_baselineArmed) {
                _baselineArmed = false;
                _baseJumps = (_jumpCount > 0) ? _jumpCount - 1 : 0;
            }
            // Running maxima, not subtractions — see the field declarations.
            // Fed only from values that already cleared the corruption gate
            // above, so the same physical bounds and the same wire invariant
            // protect them. Gated on _actStarted, which matches the frozen
            // count in activityJumps(): a jump that lands after onTimerReset
            // belongs to no activity and must not grow the finished one's
            // best. (In the never-started case the accessors return the raw
            // puck values, so nothing reads these anyway.)
            if (_actStarted) {
                if (_lastHeightM > _actBestM) { _actBestM = _lastHeightM; }
                if (_lastAirtimeS > _actBestAirtimeS) { _actBestAirtimeS = _lastAirtimeS; }
            }
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
            // MONOTONIC WITHIN AN ACTIVITY (2026-08-21). These used to be
            // assigned unconditionally, which let the count go BACKWARDS —
            // and 0 is a value the puck really sends:
            //
            //   * a puck that brownouts and restarts mid-session (a flat cell
            //     under a hard landing) comes back with session_jumps=0,
            //     because those counters are RAM statics;
            //   * a reconnect that lands on a DIFFERENT, idle puck — the walk
            //     back up the beach with the activity still running, board
            //     leaned next to another one — reads that puck's zeros.
            //
            // JumpFieldView writes the SESSION FIT fields every compute()
            // tick, so a zero reaches the SAVED ACTIVITY within a second: a
            // ride with real jumps archived as "0 jumps, best 0.00". Silent,
            // permanent, and precisely the trust failure the whole project
            // exists to avoid.
            //
            // Within one activity the live count can only grow, so refusing a
            // decrease costs nothing real. Across activities the field is
            // reconstructed anyway. This is the same guard best-airtime has
            // had since 2026-08-18, eight lines below — the correct pattern
            // was already here, applied to one field out of three.
            var n = _toNumber(kv.get("session_jumps"));
            var b = _toFloat(kv.get("session_best_m"));
            if (n != null && n > _jumpCount) { _jumpCount = n; }
            if (b != null && b > _sessionBestM) { _sessionBestM = b; }
            // THIS ACTIVITY: the armed baseline is taken here, from the
            // reseed the puck sends on every connect. Everything the puck
            // counted BEFORE this moment belongs to a previous ride — which
            // is exactly the 09-12 failure, where a two-day-old best was
            // written into a fresh activity's session field. Note what is
            // NOT taken: session_best_m never seeds _actBestM. A maximum
            // from before the timer started is not this activity's maximum,
            // and no arithmetic can separate the two.
            //
            // Cost, stated plainly: a jump that lands between timer start
            // and the first puck connect is baselined away on the wrist. The
            // puck's own stored record still has it, and a rider who starts
            // the watch before waking the board loses nothing.
            if (_baselineArmed) {
                _baselineArmed = false;
                _baseJumps = _jumpCount;
            }
            // Best-airtime reseed (adder key, firmware >= 2026-08-18). Found by
            // parsing the M2 activity FIT: best_jump reconciled to the stored
            // best while best_airtime stayed at the live-seen max, because
            // STATS carried no airtime. Same sanity bound family as heights;
            // absent key leaves prior state (v1-style lines stay valid).
            var ba = _toFloat(kv.get("session_best_airtime_s"));
            if (ba != null && ba >= 0.0 && ba <= 10.0 && ba > _bestAirtimeS) {
                _bestAirtimeS = ba;
            }
            // Storage state. fs=down is an adder key: present ONLY when the
            // puck's flash is unmounted, so absence means healthy. Deliberately
            // NOT sticky — the firmware retries the mount every 30 s and can
            // self-heal, and a stale alarm is its own kind of lie.
            var fs = kv.get("fs");
            _storageDown = (fs != null && fs.equals("down"));
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
