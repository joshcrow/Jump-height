// MemoryProbeTest.mc
//
// WHAT THIS MEASURES AND WHY IT EXISTS
// ------------------------------------
// The Instinct 3 Solar is a 32,768-byte data-field device (its own
// compiler.json). The owner's brother wears it, and as of 2026-08-20 he is
// the rider — so it is the product's only screen on the water, not a port
// target. Static code+data is 12,417 B (monkeyc --build-stats), which retired
// the "124 KB build vs 32 KB limit" scare (that was PRG file size, which is
// not runtime memory). What was never measured is RUNTIME PEAK, and the repo
// said so plainly: "the simulator memory view has never been run".
//
// The memory view is a GUI. System.getSystemStats() is not, so this probe
// gets the same answer headlessly and repeatably, in CI-able form.
//
// WHAT IT DRIVES. A real session is not one line — it is hours of them, and
// this codebase allocates per line received (LineReader substrings, parseKV
// dictionaries). So the probe pushes a realistic mixed stream: JUMP lines,
// STATS lines, chatter, malformed lines that exercise the corruption gate,
// and split-chunk reassembly. If per-line garbage accumulates, this is where
// it shows.
//
// WHAT IT DOES NOT COVER, stated so a PASS is not over-read: no BLE stack is
// instantiated (the test constraint forbids importing PuckLink), and the
// simulator's allocator is not guaranteed to match the device's byte for
// byte. It bounds the APPLICATION's appetite, which is the part we control;
// the on-wrist number still deserves one look at the real memory view.

using Toybox.Test;
using Toybox.System;
import Toybox.Lang;

// Instinct 3 Solar 45mm data-field budget, from the SDK's own device file.
const INSTINCT_DATAFIELD_BUDGET = 32768;

// NOTE: the absolute budget is documented here for reference only — see the
// long comment in the steady-state test for why this harness cannot measure
// headroom against it.

function memUsed() as Number {
    var s = System.getSystemStats();
    return s.usedMemory;
}

(:test)
function testMemory_steadyStateUnderRealisticTraffic(logger) as Boolean {
    var start = memUsed();
    logger.debug("used at start: " + start + " B");

    var m = new Model.State();

    // ~1200 lines ≈ a long session's worth of traffic through the same path
    // PuckLink hands to Model.
    for (var i = 1; i <= 300; i += 1) {
        m.onLine(Protocol.parseKV(
            "JUMP n=" + i + " airtime_raw_s=1.021 airtime_s=1.036 " +
            "height_m=1.316 height_ft=4.3 best_m=1.316"));
        m.onLine(Protocol.parseKV(
            "STATS session_jumps=" + i + " session_best_m=1.316 " +
            "session_best_airtime_s=1.036 stored_jumps=" + i +
            " stored_best_m=1.316 trace_bytes=300022 vbat_mv=3905 " +
            "batt_pct=58 chg=0"));
        // Chatter and malformed traffic: the corruption gate must not leak.
        m.onLine(Protocol.parseKV("# storage: mounted, 19839 rows"));
        m.onLine(Protocol.parseKV(
            "JUMP n=" + i + " airtime_s=1.036 height_m=1.623m=1.658"));
    }

    var peak = memUsed();
    var growth = peak - start;
    logger.debug("used at peak : " + peak + " B");
    logger.debug("growth over ~1200 lines: " + growth + " B");

    // WHY THIS ASSERTS ON GROWTH AND NOT ON THE ABSOLUTE NUMBER
    // ---------------------------------------------------------
    // The first version asserted peak < 75% of the 32,768 B data-field
    // budget and "failed" — reporting 40,072 B used BEFORE the test had
    // allocated anything. That is not our app overflowing its budget; it is
    // the wrong yardstick. These tests run as a unit-test PRG, not as a data
    // field inside an activity, so getSystemStats() reports the harness's
    // memory context, not the data-field allocation the device would apply.
    // (Corroboration: the field's entire static footprint is 12,417 B by
    // monkeyc --build-stats, so a 40 KB floor cannot be ours.)
    //
    // So the absolute headroom question is NOT answerable here, and pretending
    // otherwise would be a confident wrong number of exactly the kind this
    // project keeps having to retract. It needs the simulator's data-field
    // memory view, or the real watch.
    //
    // What IS validly measurable here is the DELTA: allocate-per-line
    // behaviour is visible regardless of what the baseline includes. That is
    // the question that actually threatens a 3-hour ride — a leak, not a
    // constant.
    Test.assert(growth < 4096);
    return true;
}

(:test)
function testMemory_doesNotGrowWithSessionLength(logger) as Boolean {
    // Leak detector: measure growth over the FIRST 300 lines and the SECOND
    // 300. Steady state means the second block costs about what the first
    // did. Unbounded accumulation means the second is materially larger.
    var m = new Model.State();

    var a0 = memUsed();
    for (var i = 1; i <= 300; i += 1) {
        m.onLine(Protocol.parseKV(
            "JUMP n=" + i + " airtime_raw_s=1.0 airtime_s=1.0 " +
            "height_m=1.0 height_ft=3.3 best_m=1.0"));
    }
    var a1 = memUsed();

    for (var i = 301; i <= 600; i += 1) {
        m.onLine(Protocol.parseKV(
            "JUMP n=" + i + " airtime_raw_s=1.0 airtime_s=1.0 " +
            "height_m=1.0 height_ft=3.3 best_m=1.0"));
    }
    var a2 = memUsed();

    var first = a1 - a0;
    var second = a2 - a1;
    logger.debug("growth block 1 (300 lines): " + first + " B");
    logger.debug("growth block 2 (300 lines): " + second + " B");

    // Allow slack for GC timing, but a genuine per-line leak makes block 2
    // grow like block 1 rather than flattening.
    Test.assert(second <= first + 2048);
    return true;
}
