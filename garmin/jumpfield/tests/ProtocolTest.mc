// ProtocolTest.mc
//
// Toybox.Test unit tests for Protocol.mc — line reassembly across awkward
// chunk boundaries, and parseKV correctness against the exact lines spec
// §5.2 documents. NO BLE, NO PuckLink import (spec's test constraint):
// these run in the simulator with zero hardware, exactly like Protocol.mc
// itself requires.
//
// (:test)-annotated functions are excluded from a normal build automatically
// (Toybox.Test: "ignored if testing is not run") — see garmin/README.md for
// the exact build+run commands (monkeyc -t, then monkeydo -t).

using Toybox.Test;
import Toybox.Lang;

// ---------------------------------------------------------- line reassembly

(:test)
function testReassembly_wholeLineOneChunk(logger) {
    var r = new Protocol.LineReader();
    var lines = r.feed("READY\n");
    Test.assertEqual(lines.size(), 1);
    Test.assertEqual(lines[0], "READY");
    return true;
}

(:test)
function testReassembly_multipleLinesOneChunk(logger) {
    var r = new Protocol.LineReader();
    var lines = r.feed("READY\nJUMP n=1 height_m=1.0\n");
    Test.assertEqual(lines.size(), 2);
    Test.assertEqual(lines[0], "READY");
    Test.assertEqual(lines[1], "JUMP n=1 height_m=1.0");
    return true;
}

(:test)
function testReassembly_splitMidKey(logger) {
    // The exact scenario called out by name in the build task: a JUMP line
    // split so a chunk boundary lands in the middle of a key=value token.
    var r = new Protocol.LineReader();
    var lines = r.feed("JUMP n=");
    Test.assertEqual(lines.size(), 0);   // nothing complete yet
    lines = r.feed("4 height_m=1.3");
    Test.assertEqual(lines.size(), 0);   // still no '\n'
    lines = r.feed("16\n");
    Test.assertEqual(lines.size(), 1);
    Test.assertEqual(lines[0], "JUMP n=4 height_m=1.316");
    return true;
}

(:test)
function testReassembly_splitOneByteAtATime(logger) {
    // Chunk boundaries anywhere, including a boundary of size 1 — the
    // pathological case for any buffer that assumes chunks carry whole
    // tokens.
    var r = new Protocol.LineReader();
    var src = "JUMP n=4\n";
    var out = [] as Array;
    for (var i = 0; i < src.length(); i += 1) {
        var piece = src.substring(i, i + 1);
        var lines = r.feed(piece);
        for (var j = 0; j < lines.size(); j += 1) {
            out.add(lines[j]);
        }
    }
    Test.assertEqual(out.size(), 1);
    Test.assertEqual(out[0], "JUMP n=4");
    return true;
}

(:test)
function testReassembly_crlfStripped(logger) {
    var r = new Protocol.LineReader();
    var lines = r.feed("READY\r\n");
    Test.assertEqual(lines.size(), 1);
    Test.assertEqual(lines[0], "READY");  // trailing '\r' stripped
    return true;
}

(:test)
function testReassembly_emptyChunkIsHarmless(logger) {
    var r = new Protocol.LineReader();
    var lines = r.feed("");
    Test.assertEqual(lines.size(), 0);
    lines = r.feed("READY\n");
    Test.assertEqual(lines.size(), 1);
    return true;
}

// ----------------------------------------------------------------- parseKV

(:test)
function testParseKV_jumpLine(logger) {
    // Exact line from spec §5.2.
    var kv = Protocol.parseKV(
        "JUMP n=4 airtime_raw_s=1.021 airtime_s=1.036 height_m=1.316 height_ft=4.3 best_m=1.316");
    Test.assertEqual(kv.get("_tag"), "JUMP");
    Test.assertEqual(kv.get("n"), "4");
    Test.assertEqual(kv.get("airtime_raw_s"), "1.021");
    Test.assertEqual(kv.get("airtime_s"), "1.036");
    Test.assertEqual(kv.get("height_m"), "1.316");
    Test.assertEqual(kv.get("height_ft"), "4.3");
    Test.assertEqual(kv.get("best_m"), "1.316");
    var args = kv.get("_args") as Array;  // get() types as Object-or-Null
    Test.assertEqual(args.size(), 0);
    return true;
}

(:test)
function testParseKV_statsLine(logger) {
    // Exact line from spec §5.2.
    var kv = Protocol.parseKV(
        "STATS session_jumps=4 session_best_m=1.316 stored_jumps=9 stored_best_m=1.316 trace_bytes=182031");
    Test.assertEqual(kv.get("_tag"), "STATS");
    Test.assertEqual(kv.get("session_jumps"), "4");
    Test.assertEqual(kv.get("session_best_m"), "1.316");
    Test.assertEqual(kv.get("stored_jumps"), "9");
    Test.assertEqual(kv.get("stored_best_m"), "1.316");
    Test.assertEqual(kv.get("trace_bytes"), "182031");
    return true;
}

(:test)
function testParseKV_readyLine(logger) {
    var kv = Protocol.parseKV("READY");
    Test.assertEqual(kv.get("_tag"), "READY");
    var args = kv.get("_args") as Array;  // get() types as Object-or-Null
    Test.assertEqual(args.size(), 0);
    return true;
}

(:test)
function testParseKV_chatterLine(logger) {
    // '#'-prefixed chatter (spec §5.2) — parses like any other line (tag
    // "#"); Model.mc ignores it by simply not recognizing the tag. There is
    // no special-case in the tokenizer, and this test pins that on purpose.
    var kv = Protocol.parseKV("# hint: almost a jump");
    Test.assertEqual(kv.get("_tag"), "#");
    var args = kv.get("_args") as Array;  // get() types as Object-or-Null
    Test.assertEqual(args.size(), 4);
    Test.assertEqual(args[0], "hint:");
    Test.assertEqual(args[1], "almost");
    Test.assertEqual(args[2], "a");
    Test.assertEqual(args[3], "jump");
    return true;
}

(:test)
function testParseKV_bareArgsGoToArgsArray(logger) {
    // 'STATE recording' -> {_tag: 'STATE', _args: ['recording']} — mirrors
    // web/app.js's parseKV doc comment exactly.
    var kv = Protocol.parseKV("STATE recording");
    Test.assertEqual(kv.get("_tag"), "STATE");
    var args = kv.get("_args") as Array;  // get() types as Object-or-Null
    Test.assertEqual(args.size(), 1);
    Test.assertEqual(args[0], "recording");
    Test.assertEqual(kv.hasKey("recording"), false);
    return true;
}

(:test)
function testParseKV_okAndErrTerminators(logger) {
    var ok = Protocol.parseKV("OK stats");
    Test.assertEqual(ok.get("_tag"), "OK");
    var okArgs = ok.get("_args") as Array;
    Test.assertEqual(okArgs.size(), 1);
    Test.assertEqual(okArgs[0], "stats");

    var err = Protocol.parseKV("ERR bad command");
    Test.assertEqual(err.get("_tag"), "ERR");
    var errArgs = err.get("_args") as Array;
    Test.assertEqual(errArgs.size(), 2);
    Test.assertEqual(errArgs[0], "bad");
    Test.assertEqual(errArgs[1], "command");
    return true;
}

(:test)
function testParseKV_emptyLine(logger) {
    var kv = Protocol.parseKV("");
    Test.assertEqual(kv.get("_tag"), "");
    var args = kv.get("_args") as Array;  // get() types as Object-or-Null
    Test.assertEqual(args.size(), 0);
    return true;
}
