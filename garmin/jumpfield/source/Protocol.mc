// Protocol.mc
//
// Pure wire-protocol logic: byte/text-chunk reassembly on '\n' and the
// key=value tokenizer. Mirrors web/app.js's createLineBuffer + parseKV
// exactly (same tag/args/kv shape) so this watch reads the puck's protocol
// identically to the CLI and the browser app — three clients, one parser
// design, kept in sync on purpose (see the repo's protocol comment block in
// firmware/src/main.cpp).
//
// NO Toybox.BluetoothLowEnergy, NO Toybox.WatchUi import here, by design:
// this file must run — and be unit-tested — in the simulator with zero
// hardware and zero BLE state (garmin/jumpfield/tests/ProtocolTest.mc).
// PuckLink.mc owns the BLE bytes; it decodes them to a String (BLE is a
// byte-oriented transport, this module is not) and hands the String to
// LineReader.feed(), then parseKV() on each completed line — mirroring how
// web/app.js's transports decode bytes before pushing into its line buffer.
//
// Monkey C strings are immutable, so any split necessarily allocates — the
// budget (spec §5.6) is "don't allocate MORE than that minimum": one
// substring per completed line, one per token. No per-character allocation
// (tokenize() walks a single toCharArray(), not repeated 1-char substrings).

import Toybox.Lang;

module Protocol {

    // Reassembles an arbitrary text-chunk stream into whole lines. A line may
    // straddle any number of chunk boundaries, including mid-key ("JUMP n=" in
    // one chunk, "4 height_m=1.3\n" in the next) — the buffer just keeps
    // growing until a '\n' shows up. Trailing '\r' (CRLF) is tolerated and
    // stripped, matching web/app.js's createLineBuffer exactly.
    class LineReader {

        hidden var _buf as String;

        function initialize() {
            _buf = "";
        }

        // Feed one chunk of ALREADY-DECODED text. Returns an Array of zero or
        // more complete lines (chunk boundaries don't line up 1:1 with line
        // boundaries — a stall-then-burst can complete several lines at once).
        function feed(chunk as String) as Array {
            if (chunk == null || chunk.length() == 0) {
                return [];
            }
            _buf = _buf + chunk;
            var out = [];
            var nl = _buf.find("\n");
            while (nl != null) {
                var line = _buf.substring(0, nl);
                _buf = _buf.substring(nl + 1, _buf.length());
                var lineLen = line.length();
                if (lineLen > 0 && line.substring(lineLen - 1, lineLen).equals("\r")) {
                    line = line.substring(0, lineLen - 1);
                }
                out.add(line);
                nl = _buf.find("\n");
            }
            return out;
        }
    }

    // 'JUMP n=4 airtime_s=1.036' -> {"_tag"=>"JUMP", "_args"=>[], "n"=>"4",
    // "airtime_s"=>"1.036"}. Bare words (no '=') land in "_args", in order —
    // e.g. 'STATE recording' -> {"_tag"=>"STATE", "_args"=>["recording"]}.
    // Values are always Strings (same as parseKV in web/app.js); callers
    // convert with .toFloat()/.toNumber() same as the web app's pf()/parseInt.
    // Unknown tags and keys are NOT filtered here — tolerating them is
    // Model.mc's job (spec §5.2), so this stays a dumb, faithful tokenizer:
    // chatter ('#...') and terminators (OK/ERR/READY) parse into a Dictionary
    // like anything else; they just won't match a tag Model.mc recognizes.
    function parseKV(line as String) as Dictionary {
        var tokens = _tokenize(line);
        var out = {};
        if (tokens.size() == 0) {
            out.put("_tag", "");
            out.put("_args", []);
            return out;
        }
        out.put("_tag", tokens[0]);
        var args = [];
        for (var i = 1; i < tokens.size(); i += 1) {
            var tok = tokens[i];
            var eq = tok.find("=");
            if (eq != null) {
                var key = tok.substring(0, eq);
                var value = tok.substring(eq + 1, tok.length());
                out.put(key, value);
            } else {
                args.add(tok);
            }
        }
        out.put("_args", args);
        return out;
    }

    // Split on runs of space/tab, dropping empty tokens — the Monkey C
    // equivalent of web/app.js's `line.trim().split(/\s+/).filter(Boolean)`.
    // No regex engine in Monkey C, so this walks characters once via
    // toCharArray() rather than calling substring() per character.
    // NOT `hidden`: that modifier is class-member-only — at module scope the
    // compiler rejects it (found on the first real build, 2026-08-04); the
    // underscore prefix carries the private-by-convention intent instead.
    function _tokenize(line as String) as Array {
        var tokens = [];
        if (line == null) {
            return tokens;
        }
        var chars = line.toCharArray();
        var n = chars.size();
        var start = -1;
        for (var i = 0; i < n; i += 1) {
            var c = chars[i];
            var isSpace = (c == ' ' || c == '\t');
            if (isSpace) {
                if (start >= 0) {
                    tokens.add(line.substring(start, i));
                    start = -1;
                }
            } else if (start < 0) {
                start = i;
            }
        }
        if (start >= 0) {
            tokens.add(line.substring(start, n));
        }
        return tokens;
    }
}
