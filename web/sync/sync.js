/*
  Rider sync page — all the logic. One file, no framework, no CDN, no build
  step (CONTRACT.md §3). Served from GitHub Pages at
  https://joshcrow.github.io/Jump-height/sync/ and from `python3 -m
  http.server` on localhost; both are secure contexts, which Web Bluetooth
  requires.

  Shape, and why:

  * One transport interface — { sendLine, onLine, onClose, disconnect } — with
    a Web Bluetooth implementation and a mock. Lifted from the archived
    browser app (git show archive/web-app:web/app.js), which had two real
    links behind the same interface and proved the seam.
  * One line handler. Every line the puck sends goes through onLine(): it
    feeds the in-flight command capture, the FILE-body sinks, the progress
    counters, and device.log. Nothing else parses the wire.
  * The trace body is NOT accumulated as text. `traceraw` can be ~2.7 MB of
    base64 for a full region; it is decoded a line at a time straight into a
    growing Uint8Array with the CRC-32 updated as the bytes land, so the page
    never holds a giant string (CONTRACT.md §1/§2).

  Rules this page exists to keep:

  * "Clear" is offered ONLY after the ride is verified AND delivered
    (CONTRACT.md §3 step 4). A short download followed by an erase is the
    cruellest failure this product can have, and the puck is at the rider's
    house with no bench behind it.
  * A reading that did not happen is a finding (CLAUDE.md rule 3). Every
    silent-failure path here — a base64 line that won't decode, a missing
    crc32 line, a timeout — turns into a named reason on screen and
    verified:false in the manifest, never into a quiet pass.
  * Device text never reaches innerHTML. Text nodes only.
*/

'use strict';

// ------------------------------------------------------------------ identity

// Baked into the page and copied into every manifest.json, so Josh can tell
// which build of this page produced a bundle without asking the rider
// anything (CONTRACT.md §2 `page_version`). Bump it when the page changes.
const PAGE_VERSION = '2026-09-10b';

// ------------------------------------------------------------------ protocol

// Nordic UART Service — the same profile the watch uses (docs/watch.md,
// "Protocol and BLE architecture"): TX (…0003) notifies, RX (…0002) is
// written. ASCII lines, '\n'-terminated, reassembled from MTU-sized chunks.
const NUS_SERVICE = '6e400001-b5a3-f393-e0a9-e50e24dcca9e';
const NUS_RX      = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'; // phone -> puck
const NUS_TX      = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'; // puck -> phone
const NAME_PREFIX = 'JumpHeight';

// Inactivity timeout, reset on EVERY line (CONTRACT.md §3 step 2). A long dump
// that is still flowing must never trip it; a puck that has genuinely stopped
// talking must.
const INACTIVITY_MS = 30000;
// selftest is diagnostic and tolerated: give it less rope than a transfer.
const SELFTEST_MS = 15000;

// The watch-in-an-activity case (docs/watch.md, "Two-central policy"): a
// second BLE central drags the link down to a 23-byte MTU. Don't stop, just
// say what it looks like.
const SLOW_AFTER_MS = 10000;
const SLOW_KBPS = 3;

// Audit F-22 (docs/audit-2026-08-22.md:62-79). `STATS trace_bytes` is a LIVE
// counter that counts a block's bytes before the block closes, so once the
// trace region is FULL it can run AHEAD of what a clean read-back produces —
// by at most one 50-sample batch, "800 bytes, exactly one 50-sample batch at
// 16 B/line" (the audit filled the region and measured `TRACE_BYTES
// n=14476006` live against a remount's 14,475,206).
//
// This is the SAME BAND, the same value, and the same evidence line as
// `F22_MAX_OVERREPORT_BYTES = 800` in tools/jump:345 — the page cannot import
// a Python constant, so the two are duplicated deliberately and must move
// together. The direction matters: only the device counting HIGH is F-22.
// More bytes arriving than the puck claims to hold is not this finding and
// stays a hard failure (CLAUDE.md rule 6 — a wrong citation is worse than
// none).
const F22_MAX_OVERREPORT_BYTES = 800;

// Optional push target, off unless the URL carries ?drop=https://… — Josh's
// convenience, never a default (CONTRACT.md §3 step 3).
const DROP_URL = (() => {
  try {
    const u = new URLSearchParams(location.search).get('drop');
    return u && /^https:\/\//i.test(u) ? u : null;
  } catch (_e) { return null; }
})();

// Step 4 is OFF for this loan. The puck is out with Nick, who is asked never
// to empty it: Josh does that on the bench, where a short download can still
// be re-pulled off a puck that still holds the ride. So the whole of step 4 —
// the section, its heading and the button — appears only when the URL carries
// ?allowclear=1, which nobody but Josh ever types. The gate underneath it
// (verified AND delivered, CONTRACT.md §3 step 4) is unchanged and still
// applies on top of this; doClear() re-checks this flag too, so a button
// unhidden by hand in the inspector still writes nothing to the wire.
const ALLOW_CLEAR = (() => {
  try { return new URLSearchParams(location.search).get('allowclear') === '1'; }
  catch (_e) { return false; }
})();

// '#mock' plays a Bluetooth-shaped session, '#mock-usb' a cable-shaped one:
// the transport kind changes the advice the page gives on a slow or failed
// copy and what manifest.json records, so the tests need to reach both.
const IS_MOCK = location.hash === '#mock' || location.hash === '#mock-usb';
const MOCK_KIND = location.hash === '#mock-usb' ? 'usb' : 'mock';

// iOS never honours a page-triggered <a download>, and the WebBLE wrapper
// browsers (Bluefy — the ONLY way an iPhone reaches the puck at all) ignore it
// entirely. The archived app learned this the hard way and routed iOS through
// the share sheet (git show archive/web-app:web/app.js, downloadText). Here it
// matters more: a click that quietly does nothing, counted as "delivered",
// would unlock step 4 and let him erase a ride that never left the phone.
// iPadOS masquerades as MacIntel, hence the maxTouchPoints check.
const IS_IOS = /iP(hone|ad|od)/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

const CHIPS = {
  sea:  ['flat', 'small chop', 'big chop', 'swell'],
  wind: ['light', 'medium', 'strong'],
};

// ------------------------------------------------------------------- helpers

const $ = (id) => document.getElementById(id);
const TEXT_ENCODER = new TextEncoder();

function byteLen(s) { return TEXT_ENCODER.encode(s).length; }

function numOrNull(v) {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** 'STATS a=1 b=2' -> {_tag:'STATS', a:'1', b:'2', _args:[]}. A faithful port
 *  of tools/jump's parse_kv (tools/jump:255-269), so the phone and the CLI
 *  read the wire identically. */
function parseKV(line) {
  const parts = String(line).trim().split(/\s+/).filter(Boolean);
  const out = { _tag: parts[0] || '', _args: [] };
  for (const p of parts.slice(1)) {
    const eq = p.indexOf('=');
    if (eq >= 0) out[p.slice(0, eq)] = p.slice(eq + 1);
    else out._args.push(p);
  }
  return out;
}

/** Reassemble a byte stream into whole lines: a BLE notification can split a
 *  line anywhere. From the archived app's createLineBuffer. */
function createLineBuffer(emit) {
  let buf = '';
  return (chunk) => {
    buf += chunk;
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      let line = buf.slice(0, nl);
      buf = buf.slice(nl + 1);
      if (line.endsWith('\r')) line = line.slice(0, -1);
      emit(line);
    }
  };
}

/** Terse DOM builder — no innerHTML anywhere, so a device line can never
 *  become markup. */
function el(tag, props, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else if (v === true) n.setAttribute(k, '');
    else n.setAttribute(k, String(v));
  }
  for (const c of kids) if (c != null) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
}

function human(bytes) {
  if (bytes == null || !Number.isFinite(bytes)) return '–';
  if (bytes < 1024) return bytes + ' bytes';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(bytes < 10240 ? 1 : 0) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function pad2(n) { return String(n).padStart(2, '0'); }

/** Local wall clock as 'YYYY-MM-DDTHH:MM:SS' — no zone suffix, because the
 *  zone travels separately as tz_offset_min (CONTRACT.md §2). */
function localIso(d) {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`
       + `T${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

function tzLabel(d) {
  try {
    const z = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (z) return z;
  } catch (_e) { /* fall through to the numeric offset */ }
  const off = -d.getTimezoneOffset();
  const sign = off < 0 ? '-' : '+';
  const a = Math.abs(off);
  return `UTC${sign}${pad2(Math.floor(a / 60))}:${pad2(a % 60)}`;
}

// ------------------------------------------------------------------- CRC-32

// CRC-32/ISO-HDLC, i.e. exactly Python's zlib.crc32 (CONTRACT.md §1). Table
// driven because the phone updates it over every byte of a multi-megabyte
// transfer while the UI has to stay responsive; the firmware does the same
// job bitwise, since there it is the radio, not the CPU, that is the limit.
const CRC_TABLE = (() => {
  const t = new Uint32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
    t[n] = c >>> 0;
  }
  return t;
})();

function crc32Bytes(bytes) {
  let c = 0xFFFFFFFF;
  for (let i = 0; i < bytes.length; i++) c = (c >>> 8) ^ CRC_TABLE[(c ^ bytes[i]) & 0xFF];
  return (c ^ 0xFFFFFFFF) >>> 0;
}

function crcHex(n) { return (n >>> 0).toString(16).padStart(8, '0'); }

// --------------------------------------------------------------- transports

/** Bluetooth via Web Bluetooth + Nordic UART Service. */
class BleTransport {
  constructor(device) {
    this.device = device;
    this._onLine = null;
    this._onClose = null;
    this._closing = false;
    this._preferNoResponse = true;
    this._encoder = new TextEncoder();
    this._decoder = new TextDecoder();
    this._push = createLineBuffer((l) => this._onLine && this._onLine(l));
    this._onNotify = (e) => this._push(this._decoder.decode(e.target.value));
    this._onDisc = () => { if (!this._closing && this._onClose) this._onClose(); };
  }
  onLine(cb) { this._onLine = cb; }
  onClose(cb) { this._onClose = cb; }

  async open() {
    this.device.addEventListener('gattserverdisconnected', this._onDisc);
    const server = await this.device.gatt.connect();
    const svc = await server.getPrimaryService(NUS_SERVICE);
    this._rx = await svc.getCharacteristic(NUS_RX);
    this._tx = await svc.getCharacteristic(NUS_TX);
    await this._tx.startNotifications();
    this._tx.addEventListener('characteristicvaluechanged', this._onNotify);
  }

  async sendLine(s) {
    // BLE's guaranteed payload is 20 bytes. Commands here are all short, but a
    // client that assumes 20 can never be surprised by a link that negotiated
    // the minimum MTU — which is exactly what a watch in an activity does to
    // us (docs/watch.md). Same chunking as the archived app.
    const bytes = this._encoder.encode(s + '\n');
    for (let i = 0; i < bytes.length; i += 20) await this._write(bytes.slice(i, i + 20));
  }
  async _write(chunk) {
    if (this._preferNoResponse && this._rx.writeValueWithoutResponse) {
      try { await this._rx.writeValueWithoutResponse(chunk); return; }
      catch (_e) { this._preferNoResponse = false; } // fall back permanently
    }
    await this._rx.writeValue(chunk);
  }

  async disconnect() {
    this._closing = true;
    try { this._tx.removeEventListener('characteristicvaluechanged', this._onNotify); } catch (_e) {}
    try { if (this.device.gatt.connected) this.device.gatt.disconnect(); } catch (_e) {}
  }
}

/** USB via Web Serial — the cable the puck charges on anyway. Chrome on a
 *  computer (the rider's older Intel MacBook included) has this; phones and
 *  Safari do not. Same line protocol, no 20-byte chunking (a CDC write takes
 *  the whole line), and the puck does NOT reset when the port opens — the
 *  Sense is native USB CDC (tools/jump open_device's own note), so this is the
 *  exact link `./tools/jump sync` has always used. Lifted from the archived
 *  app's SerialTransport (git show archive/web-app:web/app.js). */
class SerialTransport {
  constructor(port) {
    this.port = port;
    this._onLine = null;
    this._onClose = null;
    this._closing = false;
    this._encoder = new TextEncoder();
    this._decoder = new TextDecoder();
    this._push = createLineBuffer((l) => this._onLine && this._onLine(l));
  }
  onLine(cb) { this._onLine = cb; }
  onClose(cb) { this._onClose = cb; }

  async open() {
    // 115200 is nominal: native CDC ignores it, and it is deliberately NOT
    // 1200, which is the Arduino bootloader-entry touch (bench-playbook §4).
    await this.port.open({ baudRate: 115200 });
    this._writer = this.port.writable.getWriter();
    this._reader = this.port.readable.getReader();
    this._readLoop();
    // Whatever the puck printed while nobody was draining the port sits in
    // the CDC buffer and arrives the instant the reader starts — old STATE
    // flips, an earlier session's STATS, a boot banner. No listener is
    // attached yet (afterConnect wires onLine after open() resolves), so
    // this pause lets that backlog drain into nothing instead of into the
    // first command's reply. classify() additionally refuses to take a
    // STATS line as the wall-clock anchor outside a `stats` capture, so a
    // straggler cannot backdate the trace.
    await new Promise((r) => setTimeout(r, 400));
  }

  async _readLoop() {
    try {
      for (;;) {
        const { value, done } = await this._reader.read();
        if (done) break;
        if (value) this._push(this._decoder.decode(value, { stream: true }));
      }
    } catch (_e) {
      // A read error is the cable coming out — fall through to onClose.
    }
    if (!this._closing && this._onClose) this._onClose();
  }

  async sendLine(s) {
    await this._writer.write(this._encoder.encode(s + '\n'));
  }

  async disconnect() {
    this._closing = true;
    try { await this._reader.cancel(); } catch (_e) {}
    try { this._reader.releaseLock(); } catch (_e) {}
    try { await this._writer.close(); } catch (_e) {}
    try { await this.port.close(); } catch (_e) {}
  }
}

/** Test double (CONTRACT.md §3 test seam). feed(line) plays the puck; sent[] is
 *  the exact list of command strings this page wrote. Deliberately tiny and
 *  stable — tools/tests/test_web_sync.py drives the whole flow through it. */
class MockTransport {
  constructor() { this.sent = []; this._onLine = null; }
  onLine(cb) { this._onLine = cb; }
  onClose(_cb) {}
  sendLine(s) { this.sent.push(s); }
  receive(line) { if (this._onLine) this._onLine(String(line).replace(/\r?\n$/, '')); }
  async disconnect() { this._onLine = null; }
}

// -------------------------------------------------------------------- state

let transport = null;
let wakeLock = null;
let busy = false;             // a command sequence is running
let activeCapture = null;     // the in-flight command capture
let lastBundle = null;        // {name, blob} — kept so Send can be retried
let progressTimer = null;
const chosenChips = { sea: null, wind: null };

/** Everything captured from the puck this session. Reset on connect. */
let S = null;

function freshSession(kind) {
  return {
    transportKind: kind,
    deviceLog: [],            // every line EXCEPT FILE bodies (CONTRACT.md §2)
    infoLines: [],
    infoKV: {},
    calLine: null,
    puckName: null,
    statsBefore: null, statsBeforeKV: {},
    statsLatest: null, statsLatestKV: {},
    // The SECOND `stats`, read after the dump (doPull). The puck keeps
    // logging while it is plugged in, so trace_bytes at the end of a pull can
    // legitimately exceed trace_bytes at the start — and the before-figure
    // alone turned that growth into a hard failure that every retry
    // reproduced. Kept as the parsed line as well as the raw one, because
    // verifyPull needs the number, not the text.
    statsAfter: null, statsAfterKV: null,
    // The puck said its store is not mounted (fs=down). Sticky for the whole
    // session: only a `mount` makes those counts real again, and this page
    // never sends one. Set in classify().
    storageDown: false,
    selftestLines: [],
    syncedAt: null,           // the phone clock, captured with stats_before
    jumpsLines: [],
    traceCsvLines: [],
    jumpRows: 0,
    trace: freshTrace(),
    fileSection: null,
    pulling: false,
    pullStartMs: 0,
    pullLogStart: 0,   // index into deviceLog where THIS pull began
    pullSeconds: null,
    pullBytes: 0,
    slowShown: false,
    verified: false,
    reasons: [],
    // The trace_bytes cross-check's own working, recorded whether it passed,
    // failed, or was forgiven — CLAUDE.md rule 3: a reading that goes
    // unreported is itself a finding, and "verified" alone does not say WHICH
    // of the two ways it verified. Copied into manifest.json (CONTRACT.md §2)
    // so Josh can see, off the bundle alone, that a full puck was forgiven
    // its over-count rather than matching exactly.
    traceBytesDevice: null,   // STATS trace_bytes (before), or null
    traceBytesAfter: null,    // STATS trace_bytes (after the dump), or null
    traceBytesGot: null,      // trace bytes actually received, csv path only
    f22BandApplied: false,    // true = forgiven inside F-22's band
    f22Note: null,            // the rider-language sentence, when it was
    growthNote: null,         // the sentence for a surplus explained by the
                              // puck logging on between the two stats reads
    delivered: false,
    cleared: false,
    phase: 'connecting',
  };
}

function freshTrace() {
  return {
    format: null,             // 'jhtrace-v2-b64' | 'csv' | null
    expectedRaw: null,        // bytes= from '# traceraw'
    expectedText: null,       // wire bytes to expect, for the progress bar
    textBytes: 0,             // wire bytes of the trace body seen so far
    logHz: null,
    crcReported: null,
    crcHex: null,
    buf: null, len: 0, crc: 0xFFFFFFFF, pending: '', b64Error: null,
    bytes: null,
    rawDone: false,
  };
}

// ------------------------------------------------------------ line handling

// FILE frames this page does not carry into the bundle. Named, never
// swallowed: an unrecognised file is reported in the result panel.
let unknownBodies = new Set();

function onLine(line) {
  if (line == null) return;
  const isBegin = line.startsWith('FILE ') && line.endsWith(' BEGIN');
  const isEnd   = line.startsWith('FILE ') && line.endsWith(' END');
  const inBody  = S.fileSection !== null && !isEnd;

  if (S.pulling) S.pullBytes += byteLen(line) + 1;

  if (isBegin && S.fileSection === null) {
    S.fileSection = line.split(/\s+/)[1];
    S.deviceLog.push(line);
    // A second trace.bin frame in one session (a retried pull) must start
    // its byte sink from zero, but keep the '# traceraw bytes=' figures the
    // chatter line already delivered.
    if (S.fileSection === 'trace.bin') Object.assign(S.trace, resetRawFields());
  } else if (isEnd) {
    if (S.fileSection === 'trace.bin') finishRawSink();
    S.fileSection = null;
    S.deviceLog.push(line);
  } else if (inBody && line.startsWith('#')) {
    // The puck's own chatter, emitted INSIDE the frame. printFileFramed() in
    // firmware/src/main.cpp prints "# WARNING <name> INCOMPLETE — ..." after
    // the body and BEFORE its "FILE <name> END" line, and
    // printTraceRawFramed() does the same twice. So device.log must select by
    // KIND, not by position: CONTRACT.md §2 says it keeps "the frame lines and
    // all `#` chatter", and verifyPull's check (a) reads device.log. Swallowed
    // as body, the puck's own complaint could never fire that check — and
    // jumps.csv carries no crc and no byte count, so check (a) is its ONLY
    // protection. Measured on the #mock seam before this branch existed: a
    // jumps.csv one row short verified TRUE, the warning became the missing
    // third "jump", and step 4 offered to erase the puck.
    //
    // Discriminating on '#' is unambiguous: the base64 alphabet (CONTRACT.md §1)
    // has no '#', and jumps.csv/trace.csv rows begin with a digit or their
    // header word.
    S.deviceLog.push(line);
    classify(line);
  } else if (inBody) {
    // FILE bodies never enter device.log: the traceraw body alone can be
    // ~2.7 MB of base64 and device.log is meant to be readable (CONTRACT.md §2).
    // textBytes is the progress bar's numerator and its denominator
    // (expectedText) is the TRACE frame's size alone, so only the trace frame
    // may add to it — `jumps` runs first, and counting its body here started
    // the bar non-zero and read high for the whole transfer. S.pullBytes
    // above still counts every line, for transfer.bytes_received.
    if (S.fileSection === 'trace.bin' || S.fileSection === 'trace.csv') {
      S.trace.textBytes += byteLen(line) + 1;
    }
    if (S.fileSection === 'trace.bin') feedB64(line);
    else if (S.fileSection === 'jumps.csv') S.jumpsLines.push(line);
    else if (S.fileSection === 'trace.csv') S.traceCsvLines.push(line);
    else unknownBody(S.fileSection);
  } else {
    S.deviceLog.push(line);
    classify(line);
  }

  if (inBody) {
    // A body line is not a terminator and must never be tested as one — but it
    // IS activity, so it resets the inactivity timer. Without this, a long
    // healthy transfer would time out mid-flow.
    armCaptureTimer();
  } else {
    feedCapture(line);
  }
}

function unknownBody(name) {
  // Dropped from the bundle, but never dropped silently: the frame lines stay
  // in device.log and the name is named in the result panel.
  unknownBodies.add(name);
}

function classify(line) {
  if (line.startsWith('#')) {
    // '# traceraw bytes=N log_hz=H region_bytes=R' before the frame, and
    // '# traceraw crc32=xxxxxxxx bytes=N' after it (CONTRACT.md §1).
    if (line.startsWith('# traceraw')) {
      const kv = parseKV(line);
      const n = numOrNull(kv.bytes);
      if (n !== null && S.trace.expectedRaw === null) {
        S.trace.expectedRaw = n;
        // Wire cost of the base64 body: 4 chars per 3 raw bytes, 76 chars per
        // line (57 raw bytes), one '\n' per line. Used only for the progress
        // bar, so an off-by-a-few would cost nothing but a jumpy percentage.
        S.trace.expectedText = 4 * Math.ceil(n / 3) + Math.ceil(n / 57);
      }
      if (kv.log_hz != null) S.trace.logHz = numOrNull(kv.log_hz);
      if (kv.crc32 != null) S.trace.crcReported = String(kv.crc32).toLowerCase();
      // A second bytes= (on the crc line) that disagrees with the first is a
      // finding, not a tie-break: keep the first and let the count check speak.
    }
    // The chatter half of the fs=down pair — firmware/src/main.cpp's `stats`
    // emits this line and the fs=down key from the same `if (!fs_ok)`. Both
    // are read, because a reading that could not be taken must be impossible
    // to miss rather than caught in exactly one place (CLAUDE.md rule 3).
    if (line.startsWith('# storage NOT MOUNTED')) S.storageDown = true;
    if (line.startsWith('# name=')) {
      S.puckName = line.slice('# name='.length).trim() || S.puckName;
      renderFacts();
    }
    return;
  }
  const kv = parseKV(line);
  if (kv._tag === 'INFO') {
    S.infoKV = kv;
    renderFacts();
  } else if (kv._tag === 'CAL') {
    S.calLine = line;
  } else if (kv._tag === 'STATS') {
    S.statsLatest = line;
    S.statsLatestKV = kv;
    // fs=down is an adder key the firmware appends ONLY when the store failed
    // to mount (firmware/src/main.cpp, `stats`). It is the same condition the
    // watch renders as "NO REC" (docs/watch.md; docs/rider-brief.md item 6),
    // and it means the stored_jumps/trace_bytes beside it are unknown, not
    // zero — which is exactly what that firmware comment was written for.
    if (kv.fs === 'down') S.storageDown = true;
    // Only a STATS that answers THIS page's own `stats` may become the
    // wall-clock anchor. Over the cable a stale STATS from an earlier
    // session can sit in the CDC buffer and arrive first (SerialTransport
    // .open); over Bluetooth the puck's subscribe greeting precedes it. A
    // straggler taken as stats_before would carry the wrong uptime_s and
    // silently misdate every sample in the trace.
    const inStatsCapture = !!activeCapture && activeCapture.firstWord === 'stats';
    if (S.statsBefore === null && inStatsCapture) {
      S.statsBefore = line;
      S.statsBeforeKV = kv;
      // The ONE wall-clock anchor the trace will ever have: uptime_s and the
      // phone clock read in the same breath (CONTRACT.md §2). The puck has no
      // RTC, so nothing downstream can reconstruct this later.
      S.syncedAt = new Date();
    }
    renderFacts();
  }
}

// ------------------------------------------------------------ base64 -> bytes

function resetRawFields() {
  return { buf: new Uint8Array(1 << 16), len: 0, crc: 0xFFFFFFFF, pending: '',
           b64Error: null, bytes: null, rawDone: false, crcHex: null };
}

function rawPush(bin) {
  const t = S.trace;
  const need = t.len + bin.length;
  if (need > t.buf.length) {
    let cap = t.buf.length || 1;
    while (cap < need) cap *= 2;
    const bigger = new Uint8Array(cap);
    bigger.set(t.buf.subarray(0, t.len));
    t.buf = bigger;
  }
  let c = t.crc;
  for (let i = 0; i < bin.length; i++) {
    const b = bin.charCodeAt(i) & 0xFF;
    t.buf[t.len + i] = b;
    c = (c >>> 8) ^ CRC_TABLE[(c ^ b) & 0xFF];
  }
  t.crc = c >>> 0;
  t.len = need;
}

/** Decode one base64 line straight into the growing byte array. Never builds
 *  the whole body as a string: at 76 chars a line, a full region is millions
 *  of characters and a phone would feel every one of them. */
function feedB64(line) {
  const t = S.trace;
  if (t.b64Error) return;
  t.pending += line;
  // '=' padding appears only at the very end (CONTRACT.md §1), so once one shows
  // up the remainder is the tail and can be decoded whole.
  const n = t.pending.indexOf('=') >= 0
    ? t.pending.length
    : t.pending.length - (t.pending.length % 4);
  if (n <= 0) return;
  const chunk = t.pending.slice(0, n);
  t.pending = t.pending.slice(n);
  let bin;
  try { bin = atob(chunk); }
  catch (_e) {
    // A reading that did not happen is a finding (CLAUDE.md rule 3): remember
    // it so verification fails out loud rather than a short file passing.
    t.b64Error = 'part of the ride data was unreadable';
    return;
  }
  rawPush(bin);
}

function finishRawSink() {
  const t = S.trace;
  if (t.pending.length && !t.b64Error) {
    t.b64Error = 'the ride data stopped part-way through a chunk';
  }
  t.bytes = t.buf.subarray(0, t.len);
  t.crcHex = crcHex((t.crc ^ 0xFFFFFFFF) >>> 0);
  t.rawDone = true;
  t.format = 'jhtrace-v2-b64';
}

// -------------------------------------------------------- command capture

function startCapture(firstWord, onDone, ms) {
  activeCapture = { firstWord, lines: [], onDone, ms: ms || INACTIVITY_MS, timer: null };
  armCaptureTimer();
}

function armCaptureTimer() {
  if (!activeCapture) return;
  clearTimeout(activeCapture.timer);
  // Resets on every line (CONTRACT.md §3 step 2), so a slow-but-flowing transfer
  // never trips it while a genuinely stuck puck does.
  activeCapture.timer = setTimeout(() => {
    const c = activeCapture; activeCapture = null;
    c.onDone(c.lines, 'timeout');
  }, activeCapture.ms);
}

function feedCapture(line) {
  if (!activeCapture) return;
  if (line === 'OK ' + activeCapture.firstWord) {
    const c = activeCapture; activeCapture = null; clearTimeout(c.timer);
    c.onDone(c.lines, null);
  } else if (line.startsWith('ERR')) {
    const c = activeCapture; activeCapture = null; clearTimeout(c.timer);
    c.lines.push(line);
    c.onDone(c.lines, line);
  } else {
    activeCapture.lines.push(line);
    armCaptureTimer();
  }
}

function abortCapture(reason) {
  if (!activeCapture) return;
  const c = activeCapture; activeCapture = null; clearTimeout(c.timer);
  c.onDone(c.lines, reason);
}

/** Send a command and collect its reply. Resolves {lines, err}: err is null on
 *  'OK <cmd>', the literal 'timeout', or the puck's own ERR line. */
function runCommand(cmd, opts) {
  const o = opts || {};
  if (activeCapture) {
    // Two overlapping captures would splice one command's reply into the
    // other's — a wrong reading that looks like a right one. The whole flow is
    // sequential awaits, so reaching here is a bug, and it says so.
    return Promise.resolve({ lines: [], err: 'internal: two commands at once' });
  }
  return new Promise((resolve) => {
    startCapture(cmd.split(' ')[0], (lines, err) => resolve({ lines, err }), o.timeoutMs);
    try {
      const r = transport.sendLine(cmd);
      if (r && typeof r.catch === 'function') {
        r.catch((e) => abortCapture('could not reach the puck: ' + ((e && e.message) || e)));
      }
    } catch (e) {
      abortCapture('could not reach the puck: ' + ((e && e.message) || e));
    }
  });
}

// ----------------------------------------------------------------------- UI

function setStatus(text, kind) {
  const n = $('status');
  n.textContent = text;
  n.className = 'status' + (kind ? ' is-' + kind : '');
}

function renderFacts() {
  if (!S) return;
  $('facts').hidden = false;
  const kvI = S.infoKV || {};
  const kvS = S.statsLatestKV || {};
  $('puck-name').textContent = S.puckName || (transport && transport.device && transport.device.name) || 'connected';

  // Battery: the charging state leads, because while charging the voltage
  // floats high and the percentage reads optimistic — the archived app's own
  // caveat, and only the OG has a cell at all (CLAUDE.md §1).
  const pct = numOrNull(kvS.batt_pct != null ? kvS.batt_pct : kvI.batt_pct);
  const chg = numOrNull(kvS.chg != null ? kvS.chg : kvI.chg);
  let batt = 'not reported';
  if (pct !== null) batt = chg === 1 ? `charging (${pct}%)` : `${pct}%`;
  else if (chg === 1) batt = 'charging';
  $('battery').textContent = batt;

  // With fs=down the store never mounted, so stored_jumps=0 / trace_bytes=0
  // are readings that did not happen. Printing them as "0" is precisely the
  // 2026-08-11 mistake firmware/src/main.cpp's `stats` comment records — "A
  // reading that could not be taken must never be dressed up as a reading of
  // zero" — and on this page it would also read as a puck with nothing worth
  // keeping, one screen before an erase button.
  //
  // The "Ride data waiting" (trace_bytes) and "Puck software" (fw/src) rows
  // were cut 2026-09-09: a byte count and a build hash are diagnostics, not
  // news to a rider, and both still travel to Josh inside manifest.json
  // (trace_bytes_device, fw, src) and device.log. What must NOT be lost with
  // them is the fs=down condition, and it is not: this row says "unknown — not
  // saving" and afterConnect's status line says NO REC in his own words. Two
  // places, neither of them a number he has to interpret.
  const down = !!S.storageDown;
  const stored = numOrNull(kvS.stored_jumps);
  $('stored-jumps').textContent = down ? 'unknown — not saving'
                                       : (stored === null ? '–' : String(stored));
}

function showResult(headText, kind, lines) {
  const host = $('result');
  host.textContent = '';
  const panel = el('div');
  panel.append(el('p', { class: 'head' + (kind ? ' is-' + kind : ''), text: headText }));
  if (lines && lines.length) {
    const ul = el('ul');
    // el() turns a string into a text node and appends a node as it is, so a
    // built element (the save link on the iOS path) can ride in this list
    // without any of it going near innerHTML.
    for (const l of lines) ul.append(el('li', null, l));
    panel.append(ul);
  }
  host.append(panel);
}

/** Sentences that depend on how the puck is attached. Over the cable there is
 *  no range to close and no watch to compete with, so the Bluetooth remedies
 *  would send him chasing the wrong thing. */
function isUsb() { return !!S && S.transportKind === 'usb'; }

/** The last resort, and the one nobody thinks of on a beach: the page itself
 *  is holding state — a half-open FILE frame, a transport that will not come
 *  back — that only a reload clears. One constant, used by every "it didn't
 *  connect" sentence, so the advice cannot drift between them. */
const RELOAD_HINT = 'If it still won’t connect, reload this page.';

function retryAdvice() {
  return isUsb()
    ? 'Check the cable is pushed in properly at both ends (some cables only charge — use one that carries data), then tap "Copy the ride" again.'
    : 'Tap "Copy the ride" again — closer to the puck, and with your watch out of an activity.';
}

function setEnabled() {
  $('btn-connect').disabled = busy || !!transport;
  $('btn-connect-usb').disabled = busy || !!transport;
  // Step 2 shuts once the puck is empty. doPull's first act is
  // `lastBundle = null`, and after a clear that in-memory bundle is the only
  // copy of the ride left anywhere — so one stray tap on the big step-2 button
  // would trade the ride for a pull of an empty puck. Send stays enabled just
  // below, which is what step 3 is being kept alive for.
  // …and never opens at all until the connect-time `stats` actually answered.
  // trace_epoch_utc is computed from that one reply's uptime_s (CONTRACT.md
  // §2.3) and it is the ONLY wall-clock anchor the trace will ever have: a
  // bundle pulled without it ships trace_epoch_utc null, which tools/label.py
  // reads as a puck with no session behind it. A reading that did not happen
  // must not become a bundle (CLAUDE.md rule 3).
  $('btn-pull').disabled = busy || !transport || !!(S && S.cleared)
                        || !(S && S.statsBefore);
  // Send is offered on any COMPLETED pull, verified or not: an unverified
  // bundle is exactly the one Josh most wants to look at, and step 4 stays
  // shut regardless. A pull that stopped part-way has no bundle to build.
  // 'cleared' included on purpose: once the puck is empty the phone holds the
  // only copy of the ride, so re-sending must stay possible.
  const sendable = !!S && (S.phase === 'pulled' || S.phase === 'sent' || S.phase === 'cleared');
  $('btn-send').disabled = busy || !sendable;
  // Step 4 exists only after verified AND delivered (CONTRACT.md §3 step 4) —
  // and, for this loan, only when the URL asked for it at all (ALLOW_CLEAR).
  const offerClear = ALLOW_CLEAR && !!(S && S.verified && S.delivered && !S.cleared);
  $('btn-clear').hidden = !offerClear;
  $('btn-clear').disabled = busy || !offerClear;
  $('clear-hint').hidden = offerClear;
}

function showProgress(on) {
  $('progress').hidden = !on;
  if (!on) { $('slow-hint').hidden = true; }
}

function updateProgress() {
  if (!S || !S.pulling) return;
  const elapsed = (Date.now() - S.pullStartMs) / 1000;
  const kbps = elapsed > 0 ? (S.pullBytes / 1024) / elapsed : 0;
  const exp = S.trace.expectedText;
  let pctText;
  if (exp !== null && exp > 0) {
    const pct = Math.max(0, Math.min(99, Math.floor((S.trace.textBytes / exp) * 100)));
    $('bar-fill').style.width = pct + '%';
    pctText = `Copying… ${pct}%`;
  } else {
    pctText = `Copying… ${human(S.pullBytes)} so far`;
  }
  $('progress-text').textContent = `${pctText} · ${kbps.toFixed(1)} KB/s`;

  // The known cause is a second BLE central — his watch, mid-activity — which
  // drops the link to a 23-byte MTU (docs/watch.md, "Two-central policy").
  // Say so without stopping: the transfer will still finish, just slowly.
  // Bluetooth only: a watch cannot slow a cable, and the hint would be wrong.
  if (!isUsb() && !S.slowShown && elapsed * 1000 > SLOW_AFTER_MS && kbps < SLOW_KBPS) {
    S.slowShown = true;
    $('slow-hint').hidden = false;
  }
}

// ------------------------------------------------------------------ connect

async function acquireWakeLock() {
  try {
    if ('wakeLock' in navigator && document.visibilityState === 'visible' && !wakeLock) {
      wakeLock = await navigator.wakeLock.request('screen');
      if (wakeLock.addEventListener) wakeLock.addEventListener('release', () => { wakeLock = null; });
    }
  } catch (_e) { /* denied or unsupported — the transfer works without it */ }
}
async function releaseWakeLock() {
  try { if (wakeLock) await wakeLock.release(); } catch (_e) {}
  wakeLock = null;
}

/** The cable: Web Serial, Chrome on a computer. Chrome's picker lists the
 *  port by its USB product string, so the rider is told what to look for in
 *  the hint next to the button; no VID/PID filter, because a wrong one would
 *  hide the puck with no way to say why. */
async function doConnectUsb() {
  if (transport || busy) return;
  if (!navigator.serial) {
    setStatus('This browser can’t use the cable. Use Chrome on a computer — '
            + 'or connect over Bluetooth instead.', 'bad');
    return;
  }
  busy = true; setEnabled();
  setStatus('Pick the puck from the list Chrome shows…', 'busy');
  let port;
  try {
    port = await navigator.serial.requestPort();
  } catch (_e) {
    busy = false; setEnabled();
    // Names the button by the label it actually carries. It read "Connect with
    // the cable" until 2026-09-09; the cable is now the only path on a
    // computer, so the button is just "Connect" and a sentence pointing at the
    // old name would point at nothing on screen (CLAUDE.md §4, the mirror
    // case: retire an identifier, fix what pointed at it).
    setStatus('No puck picked. Check the cable is plugged into the puck and the '
            + 'computer, then tap "Connect" and choose the entry '
            + 'called XIAO nRF52840 Sense.');
    return;
  }
  const t = new SerialTransport(port);
  try { await t.open(); }
  catch (e) {
    busy = false; setEnabled();
    setStatus('Couldn’t open the cable connection: ' + ((e && e.message) || e)
            + '\nUnplug the puck, plug it back in, and try again. If another '
            + 'program has the puck open, close it first.\n' + RELOAD_HINT, 'bad');
    return;
  }
  await afterConnect(t, 'usb', null);
}

async function doConnect() {
  if (transport || busy) return;
  if (!navigator.bluetooth) {
    setStatus('This browser can’t use Bluetooth. On an iPhone, open this '
            + 'page in the free Bluefy app. On Android or a computer, use Chrome'
            + (navigator.serial ? ' — or plug the puck in and use the cable.' : '.'), 'bad');
    return;
  }
  busy = true; setEnabled();
  setStatus('Pick your puck from the list…', 'busy');
  let device;
  try {
    device = await navigator.bluetooth.requestDevice({
      // namePrefix OR the service UUID (two filter objects = OR), the same
      // pair the archived app used: every puck advertises JumpHeight-XXXX
      // since 2026-08-18, and the service filter covers anything older.
      filters: [{ namePrefix: NAME_PREFIX }, { services: [NUS_SERVICE] }],
      optionalServices: [NUS_SERVICE],
    });
  } catch (_e) {
    busy = false; setEnabled();
    setStatus('No puck picked. Tap Connect and choose the one whose name starts with JumpHeight.');
    return;
  }
  const t = new BleTransport(device);
  try { await t.open(); }
  catch (e) {
    busy = false; setEnabled();
    setStatus('Couldn’t connect: ' + ((e && e.message) || e)
            + '\nMake sure the puck is on, then try again.', 'bad');
    return;
  }
  await afterConnect(t, 'ble', device.name || null);
}

/** Shared by the real and the mock link: read the puck, then unlock step 2. */
async function afterConnect(t, kind, advertisedName) {
  transport = t;
  S = freshSession(kind);
  S.puckName = advertisedName;
  unknownBodies = new Set();  // per session, like everything else in S
  lastBundle = null;
  t.onLine(onLine);
  t.onClose(onLinkLost);
  await acquireWakeLock();
  // "Leave the phone alone" is the wrong instruction on a browser that refused
  // the Screen Wake Lock: the screen sleeps, the link goes quiet, and the 30 s
  // inactivity timer ends the pull with a cause the page knew about and never
  // mentioned (CLAUDE.md rule 3). Awaited, so this reads the outcome and not
  // the pending promise.
  // Not over the cable: a plugged-in computer's screen going dark does not
  // stop a USB transfer, and "tap the screen every so often" is phone advice.
  $('wake-hint').hidden = !!wakeLock || kind === 'usb';
  busy = true; setEnabled();
  setStatus('Connected. Reading the puck…', 'busy');

  const info = await runCommand('info');
  if (info.err) {
    busy = false; S.phase = 'connected'; setEnabled();
    setStatus(failWord(info.err, 'The puck connected but didn’t answer.'), 'bad');
    return;
  }
  S.infoLines = info.lines;

  const stats = await runCommand('stats');
  busy = false;
  S.phase = 'connected';
  setEnabled();
  // No STATS, no pull. `stats` is not just the "what is on it" line: its
  // uptime_s, read in the same breath as the phone clock, is the ONLY
  // wall-clock anchor the trace ever gets (trace_epoch_utc, CONTRACT.md §2.3).
  // Without it the bundle ships trace_epoch_utc null, and tools/label.py reads
  // that as a puck with no session behind it — a live puck misdiagnosed from a
  // reading that never happened (CLAUDE.md rule 3). So this covers BOTH the
  // ERR/timeout case and the quieter one: an 'OK stats' with no STATS line in
  // it, where err is null and S.statsBefore is still null. setEnabled() keeps
  // step 2 shut on the same condition.
  if (stats.err || S.statsBefore === null) {
    const lead = 'The puck didn’t answer its first question — unplug it, plug '
               + 'it back in, and reload this page.';
    setStatus(stats.err ? failWord(stats.err, lead) : lead, 'bad');
    return;
  }
  renderFacts();
  if (S.storageDown) {
    // Not "0 jumps, 0 bytes": the store never mounted, so nothing was read.
    // His own word for it is NO REC (docs/rider-brief.md item 6: "it means the
    // puck is powered and talking to your watch but not saving anything"), and
    // that brief tells him it is worth interrupting a session for and that
    // there is nothing he can do about it on the water.
    setStatus('Connected — but the puck is NOT saving anything. That is the '
            + '"NO REC" problem: powered, talking, recording nothing.\n'
            + 'Nothing you can do on the beach fixes it. Copy and send what is '
            + 'there so Josh can see it, and do NOT empty the puck.', 'bad');
    return;
  }
  const stored = numOrNull(S.statsBeforeKV.stored_jumps);
  const tb = numOrNull(S.statsBeforeKV.trace_bytes);
  const what = stored === null
    ? 'The puck is ready.'
    : `The puck is holding ${stored} jumps and ${human(tb || 0)} of ride data.`;
  setStatus('Connected. ' + what + ' Now tap "Copy the ride".', 'ok');
}

function onLinkLost() {
  transport = null;
  releaseWakeLock();
  abortCapture('the puck went out of range');
  if (S) S.pulling = false;
  clearInterval(progressTimer);
  busy = false;
  setEnabled();
  setStatus((isUsb()
    ? 'The cable connection dropped. Nothing was lost — check the cable at '
      + 'both ends, tap "Connect" and start again.'
    : 'The puck dropped out of range. Nothing was lost — move closer, '
      + 'tap Connect and start again.') + '\n' + RELOAD_HINT, 'bad');
}

// CONTRACT.md §1's fixed ERR string for "the store never mounted"
// (firmware/src/main.cpp, `traceraw`) — the one failure on this page that no
// amount of retrying can change.
const STORAGE_DOWN_RE = /\bstorage_down\b/;

// "t,mag\n". A real nrf52 puck emits this header even when it has nothing
// stored (firmware/src/platform/nrf52/jh_store.cpp:1119-1126), so a delivered
// body of 6 bytes or fewer is an empty region, not a recorded ride.
const TRACE_HEADER_BYTES = 6;

/** True when retrying — closer, watch out of an activity — could plausibly
 *  change the outcome. A store that never mounted cannot be fixed by moving
 *  the phone, and telling him otherwise is telling him to keep trying until he
 *  gives up. */
function retryCouldHelp(err) {
  return !STORAGE_DOWN_RE.test(String(err)) && !(S && S.storageDown)
      && !/\btraceraw_unsupported\b/.test(String(err));
}

/** Turn a capture error into a sentence a rider can act on.
 *
 *  The ERR strings are fixed by CONTRACT.md §1, so the ones that mean something
 *  to him are translated rather than pasted: "ERR traceraw storage_down" is
 *  protocol jargon on a page whose whole promise is that there is none
 *  (web/sync/index.html's own header comment), and the reassurance that
 *  follows every other failure — "the puck still has everything" — is exactly
 *  the claim a puck that is not saving cannot support. Unmapped ERRs keep
 *  their text: he cannot act on it, but it is the only thing he and Josh have
 *  to talk about, and device.log carries it either way. */
function failWord(err, lead) {
  const s = String(err);
  const out = [lead];
  if (err === 'timeout') {
    out.push(isUsb()
      ? 'It went quiet for 30 seconds. Check the cable at both ends and try again.'
      : 'It went quiet for 30 seconds. Move the phone right next to it '
        + 'and try again.');
  } else if (STORAGE_DOWN_RE.test(s)) {
    out.push('The puck is not saving anything — that is the "NO REC" problem: '
           + 'powered, talking, recording nothing.');
    out.push('Nothing you can do on the beach fixes it. Tell Josh, and do NOT '
           + 'empty the puck.');
  } else if (/\btraceraw_unsupported\b/.test(s)) {
    out.push('This puck’s software can’t hand the ride over this way. Nothing '
           + 'is lost — tell Josh.');
  } else {
    out.push('It said: ' + s);
  }
  // Only claim the puck kept the ride when the puck is in a state to vouch
  // for it.
  if (retryCouldHelp(err)) out.push('The puck still has everything.');
  return out.join('\n');
}

// --------------------------------------------------------------------- pull

async function doPull() {
  if (!transport || busy) return;
  busy = true;
  lastBundle = null;
  S.phase = 'pulling';
  S.pulling = true;
  S.pullStartMs = Date.now();
  // device.log is cumulative for the whole session (CONTRACT.md §2), but the
  // INCOMPLETE scan below must only look at THIS attempt: a warning left over
  // from a failed first try would otherwise condemn every retry after it.
  S.pullLogStart = S.deviceLog.length;
  S.pullBytes = 0;
  S.slowShown = false;
  S.jumpsLines = [];
  S.traceCsvLines = [];
  csvBodyCache = null;          // a retry must never inherit the last pull's body
  S.selftestLines = [];
  S.trace = freshTrace();
  // The retry poison. fileSection was reset in exactly two places —
  // freshSession() and a 'FILE … END' line — so a pull that died INSIDE a
  // frame (the 30 s inactivity timer, a dropped link) left it pointing at
  // trace.csv for the rest of the session. The retry's own 'FILE jumps.csv
  // BEGIN' then matched `inBody` instead of `isBegin` and was swallowed into
  // the trace sink: jumps.csv came out empty, check (b) failed, and every
  // retry reproduced the failure of the attempt before it. unknownBodies is
  // deliberately NOT reset here — it is per-session by design.
  S.fileSection = null;
  // Same reasoning as the trace fields: this pull's own second `stats`, or
  // nothing. A previous attempt's after-figure must not become this one's
  // ceiling.
  S.statsAfter = null;
  S.statsAfterKV = null;
  S.verified = false;
  S.reasons = [];
  S.delivered = false;
  $('bar-fill').style.width = '0%';
  $('progress-text').textContent = 'Starting…';
  showProgress(true);
  setEnabled();
  // The instruction has to name the thing he is actually holding. Over the
  // cable there is no phone in the loop at all — the puck is plugged into a
  // Mac — and "keep the phone next to the puck" sent him looking for a link
  // that does not exist. Same rule as retryAdvice() and the slow hint.
  setStatus(isUsb()
    ? 'Copying the ride across. Leave the puck plugged in.'
    : 'Copying the ride across. Keep the phone next to the puck.', 'busy');
  progressTimer = setInterval(updateProgress, 500);

  try {
    let r = await runCommand('jumps');
    if (r.err) return endPullFailed('the jump list', r.err);

    r = await runCommand('traceraw');
    if (r.err && /^ERR unknown_command\b/.test(r.err)) {
      // CONTRACT.md §1: fall back to CSV on ERR unknown_command ONLY. Every other
      // ERR (storage_down, traceraw_unsupported) is the puck reporting a real
      // condition and is shown as-is rather than papered over with a retry.
      S.trace.format = 'csv';
      S.trace.expectedText = numOrNull(S.statsBeforeKV.trace_bytes);
      setStatus('This puck has the older software, so the ride comes across '
              + 'the slow way. Nothing is lost — just give it longer.', 'busy');
      r = await runCommand('trace');
      if (r.err) return endPullFailed('the ride data', r.err);
    } else if (r.err) {
      return endPullFailed('the ride data', r.err);
    }

    r = await runCommand('stats');
    if (r.err) return endPullFailed('the final check', r.err);
    S.statsAfter = S.statsLatest;
    S.statsAfterKV = S.statsLatestKV;

    // selftest is diagnostic, not required (CONTRACT.md §3 step 2): a failure or
    // a timeout here costs a manifest field, never the ride.
    const st = await runCommand('selftest', { timeoutMs: SELFTEST_MS });
    S.selftestLines = st.lines;
    if (st.err) {
      S.selftestLines = S.selftestLines.concat(
        ['# page: selftest did not finish — ' + String(st.err)]);
    }

    endPullOk();
  } finally {
    stopPullClock();
    clearInterval(progressTimer);
    busy = false;
    setEnabled();
  }
}

function stopPullClock() {
  if (!S.pulling) return;   // already stopped by whichever branch got here first
  S.pulling = false;
  S.pullSeconds = Math.round(((Date.now() - S.pullStartMs) / 1000) * 10) / 10;
}

function endPullFailed(what, err) {
  stopPullClock();
  showProgress(false);
  S.phase = 'failed';
  setStatus('Couldn’t get ' + what + ' off the puck.\n'
          + failWord(err, 'The copy stopped part-way.'), 'bad');
  // The generic remedy is offered only where it can work. On a store that
  // never mounted, "move closer, end the activity" is an instruction to keep
  // retrying something that cannot succeed.
  showResult('Nothing was sent, and nothing was erased.', 'bad',
             retryCouldHelp(err)
               ? ['The puck still has everything. ' + retryAdvice()]
               : ['There is nothing here to send yet, and nothing was erased. '
                  + 'Tell Josh the puck is showing NO REC, and leave it alone '
                  + 'until he says otherwise.']);
}

function endPullOk() {
  stopPullClock();
  $('bar-fill').style.width = '100%';
  showProgress(false);
  S.phase = 'pulled';

  // jumps.csv's header row is not a jump.
  const rows = S.jumpsLines.filter((l) => l.trim() !== '');
  S.jumpRows = rows.length && rows[0].startsWith('n,') ? rows.length - 1 : rows.length;

  S.reasons = verifyPull();
  S.verified = S.reasons.length === 0;

  if (S.verified) {
    // An empty puck is a perfectly good outcome (he cleared it last time and
    // this ride recorded nothing), and saying "got it all" about nothing reads
    // like a success that isn't one.
    // Three outcomes, not two. "No jumps" and "nothing recorded" are
    // DIFFERENT states and conflating them cost real data on 2026-09-09,
    // when a puck holding 455 KB of ride and zero detected jumps said
    // "Nothing was recorded on the puck." That is exactly the shape of the
    // 2026-09-06 water session — 47 minutes on the water, a full trace, and
    // not one real jump in it (docs/STATUS.md) — the most valuable capture
    // this project has. A rider told nothing was recorded has every reason
    // not to bother sending it.
    const gotTrace = traceBytesGot();
    const nothingAtAll = S.jumpRows === 0 && gotTrace <= TRACE_HEADER_BYTES;
    setStatus(
      nothingAtAll
        ? 'The puck has nothing saved on it — no jumps and no ride data. You '
          + 'can still send it so Josh can see why.'
        : S.jumpRows === 0
          ? `No jumps were detected, but the whole ride is here — ${human(gotTrace)} `
            + 'of it, checked and complete. Send it in step 3: a ride with no '
            + 'jumps in it is still worth having.'
          : `Got it all — ${S.jumpRows} jumps and the whole ride, checked and `
            + 'complete. Now send it in step 3.', 'ok');
    showResult(
      nothingAtAll ? 'Nothing was recorded on the puck.'
                   : S.jumpRows === 0 ? 'The whole ride is here — no jumps detected in it.'
                                      : 'Everything on the puck is now on your phone.',
      'ok', okDetail());
  } else {
    // The cruellest failure this product can have is "recorded perfectly,
    // downloaded incompletely, then erased". Say plainly that the puck is
    // untouched, and do not offer step 4 at all.
    setStatus(S.storageDown
      ? 'The puck is not saving anything, so nothing that came across can be '
        + 'trusted.\nSend it to Josh anyway — it tells him why — and do NOT '
        + 'empty the puck.'
      : 'That didn’t come across cleanly, so it is not ready to send.\n'
        + 'The puck still has everything. ' + retryAdvice(), 'bad');
    showResult(S.storageDown ? 'The puck recorded nothing — do not empty it.'
                             : 'Not complete — the puck still has everything.',
               'bad', S.reasons);
  }
  setEnabled();
}

/** Bytes of ride data actually delivered, whichever format came across. The
 *  CSV arm is the same joinBody() the byte check uses, so this can never
 *  disagree with what was verified. */
function traceBytesGot() {
  if (S.trace.format === 'jhtrace-v2-b64') return S.trace.len || 0;
  if (S.trace.format === 'csv') return csvBody().bytes;
  return 0;
}

function okDetail() {
  const d = [`${S.jumpRows} jumps`];
  if (S.trace.format === 'jhtrace-v2-b64') d.push(`${human(S.trace.len)} of ride data, check number matches`);
  else if (S.trace.format === 'csv') d.push(`${human(csvBody().bytes)} of ride data`);
  // Said out loud, not swallowed: the check did NOT come out even, and the
  // rider (and Josh, reading the same sentence in the bundle) is told why it
  // still counts as complete.
  if (S.f22Note) d.push(S.f22Note);
  if (S.growthNote) d.push(S.growthNote);
  if (S.pullSeconds) d.push(`took ${S.pullSeconds} s`);
  for (const n of unknownBodies) d.push(`(the puck also sent "${n}", which this page does not carry)`);
  return d;
}

/** CONTRACT.md §2 `verified`: every applicable check, and every one that could
 *  not run is itself a reason. Returns the reasons in rider language; an empty
 *  list means verified. */
function verifyPull() {
  const out = [];

  // A retry re-runs this whole function, so the previous attempt's working
  // must not survive into it: a first pull forgiven inside F-22's band
  // followed by a byte-exact second one would otherwise still ship
  // f22_band_applied=true and a note about a quirk that did not apply.
  S.traceBytesDevice = null;
  S.traceBytesAfter = null;
  S.traceBytesGot = null;
  S.f22BandApplied = false;
  S.f22Note = null;
  S.growthNote = null;

  // (0) Before any arithmetic: fs=down means the store never mounted, so every
  // count the checks below compare came back unknown rather than zero
  // (firmware/src/main.cpp, `stats`). On the CSV fallback path — today's OG,
  // src=5c80a436 (CONTRACT.md §1) — an empty trace.csv matches an unread
  // trace_bytes=0 exactly, and check (b) does not run at all when
  // stored_jumps is 0 (CONTRACT.md §2 says "when stored_jumps > 0"). So nothing
  // objected: measured on the #mock seam before this check existed, the page
  // verified TRUE and offered step 4 for a puck whose store it never read.
  if (S.storageDown) {
    out.push('The puck is not saving anything (the "NO REC" problem), so what '
             + 'came across cannot be trusted. Send it to Josh and do NOT '
             + 'empty the puck.');
  }

  // (a) The puck's own complaint outranks any arithmetic we do. main.cpp emits
  // "# WARNING <file> INCOMPLETE — N bytes never reached the host"; the CLI
  // learned to look for it on 2026-08-20 (tools/jump _verify_download), and so
  // does this page.
  const incomplete = S.deviceLog.slice(S.pullLogStart).filter(
    (l) => l.includes('INCOMPLETE') && l.trimStart().startsWith('#'));
  if (incomplete.length) {
    out.push('The puck itself said part of the transfer never arrived: '
             + incomplete[0].slice(0, 160));
  }

  // (b) Jump rows against the puck's own stored_jumps. These are his actual
  // results, not raw samples, and step 4 is right behind this check.
  const devJumps = numOrNull(S.statsBeforeKV.stored_jumps);
  if (devJumps !== null && devJumps > 0 && S.jumpRows !== devJumps) {
    out.push(`Only ${S.jumpRows} of the puck's ${devJumps} jumps came across.`);
  }

  // (c) The ride data itself.
  if (S.trace.format === 'jhtrace-v2-b64') {
    if (S.trace.b64Error) out.push(S.trace.b64Error + '.');
    if (S.trace.expectedRaw === null) {
      out.push('The puck never said how much ride data to expect, so it could not be checked.');
    } else if (S.trace.len !== S.trace.expectedRaw) {
      // Exact counts, not human(): it rounds above 10 KB, so a genuine
      // shortfall could print "Only 50 KB of the puck's 50 KB" — a sentence
      // that reads like a contradiction on the one screen he has to act on.
      out.push(`Only ${S.trace.len.toLocaleString()} of the puck's `
               + `${S.trace.expectedRaw.toLocaleString()} bytes of ride data came across.`);
    }
    if (!S.trace.crcReported) {
      out.push('The puck never sent its check number for the ride data, so it could not be checked.');
    } else if (S.trace.crcHex !== S.trace.crcReported) {
      out.push('The ride data arrived damaged — its check number does not match.');
    }
  } else if (S.trace.format === 'csv') {
    const devBytes = numOrNull(S.statsBeforeKV.trace_bytes);
    // The SECOND stats, read after the dump (doPull). The first one is a
    // reading taken minutes earlier on a puck that never stopped recording,
    // so it is a floor, not a ceiling — see the surplus branch below.
    const devBytesAfter = numOrNull((S.statsAfterKV || {}).trace_bytes);
    const got = csvBody().bytes;
    S.traceBytesDevice = devBytes;
    S.traceBytesAfter = devBytesAfter;
    S.traceBytesGot = got;
    if (devBytes === null) {
      out.push('The puck never said how much ride data to expect, so it could not be checked.');
    } else if (got === devBytes) {
      // The ordinary case: the counter and the copy agree exactly.
    } else if (devBytes === 0 && got <= 6) {
      // The header-only region. A real nrf52 puck ALWAYS emits the 6-byte
      // "t,mag\n" header when it dumps trace.csv — read_chunk() sends it
      // before it looks at whether there is a single stored byte behind it
      // (firmware/src/platform/nrf52/jh_store.cpp:1119-1126) — while
      // trace_bytes only starts counting that header on the first append
      // (:1058-1063). So an empty puck reports 0 and hands over 6, and the
      // surplus arm below called that "6 bytes against the puck's 0 — that
      // does not add up": a refusal, on the one puck state that is perfectly
      // fine. It also made endPullOk's "The puck has no jumps saved on it"
      // branch unreachable on real hardware. Bounded at 6: this forgives the
      // header and nothing else.
    } else if (got < devBytes && devBytes - got <= F22_MAX_OVERREPORT_BYTES) {
      // Audit F-22. The puck's trace region is FULL — the firmware STOPS
      // writing rather than wrapping (firmware/src/main.cpp:1710, "Once full
      // it records NOTHING, forever";
      // firmware/src/platform/nrf52/jh_store.cpp:932-946 drops a block rather
      // than write past the region) — and its live trace_bytes counter then
      // reads high by up to one batch. Measured on
      // the OG 2026-09-07: the puck's own `tracecheck` answered
      // "fast=15917918 slow=15917153 DISAGREE — the slow number is the
      // correct one", a −765 B gap, and the download was COMPLETE.
      //
      // This page cannot ask `tracecheck` (that command re-walks the whole
      // region and takes minutes; the CLI has the time for it,
      // tools/jump:_query_tracecheck, a rider standing on a beach does not).
      // So the band IS the arbiter here, and it is deliberately narrow:
      // short by 1..800 B and no more. Without this, the one puck the rider
      // actually has — src=5c80a436, csv fallback only — cannot produce a
      // verified bundle once it fills, and the page tells him to re-pull a
      // ride that already came across whole.
      S.f22BandApplied = true;
      S.f22Note = `The puck is full — its counter runs ${(devBytes - got).toLocaleString()} `
                + 'bytes ahead once it fills (a known quirk); the copy is complete.';
    } else if (got > devBytes && devBytesAfter !== null && got <= devBytesAfter) {
      // The stale denominator. trace_bytes came off the connect-time `stats`,
      // latched once (classify(), S.statsBefore) — and the puck goes on
      // recording the whole time it is being handled: plugged in, jostled on
      // the bench, motion above the threshold. Minutes later the copy is
      // legitimately BIGGER than that first reading, and comparing against it
      // alone made a growing puck fail as a surplus — a hard refusal that
      // every retry reproduced, because every retry starts by taking the same
      // stale reading again.
      //
      // The pull's own second `stats` (doPull, after the dump) is the honest
      // ceiling: anything between the two readings is data the puck wrote
      // while we watched. Outside them it is still unexplained, and still a
      // refusal.
      S.growthNote = 'The puck kept recording while you plugged it in — '
                   + `${(got - devBytes).toLocaleString()} extra bytes; that is normal.`;
    } else if (got > devBytes) {
      // NOT F-22, which only ever runs the device HIGH. More arriving than
      // the puck says it holds is unexplained, and it is not this page's
      // business to invent an explanation.
      out.push(`More ride data arrived than the puck says it has — `
               + `${got.toLocaleString()} bytes against the puck's `
               + `${devBytes.toLocaleString()}. That does not add up.`);
    } else {
      // Exact counts here too, and for the same reason as the raw branch.
      out.push(`Only ${got.toLocaleString()} of the puck's `
               + `${devBytes.toLocaleString()} bytes of ride data came across.`);
    }
  } else {
    out.push('No ride data arrived at all.');
  }
  return out;
}

// ------------------------------------------------------------------- bundle

function joinBody(lines) { return lines.length ? lines.join('\n') + '\n' : ''; }

// The CSV body is joined and TextEncoder'd FOUR separate times at the end of a
// pull — traceBytesGot(), okDetail(), verifyPull()'s `got`, and buildBundle().
// At fixture scale that is invisible; measured at a real full region
// (15,917,153 B, 1,029,542 lines) it is a 116-144 ms synchronous freeze on an
// Apple M3, and it lands right after the bar reads 100 %. Nick is on an older
// Intel MacBook. One pass, cached, is all any of them needed.
//
// Keyed on the line count AND cleared explicitly in doPull's reset, because
// length alone would hand a retry that produced exactly the same number of
// lines the PREVIOUS pull's text.
let csvBodyCache = null;
function csvBody() {
  const lines = S.traceCsvLines;
  if (!csvBodyCache || csvBodyCache.n !== lines.length) {
    const text = joinBody(lines);
    csvBodyCache = { n: lines.length, text: text, bytes: byteLen(text) };
  }
  return csvBodyCache;
}

/** PUCK4: the 4 characters after 'JumpHeight-' in the advertised name
 *  (CONTRACT.md §2/§4). 'xxxx' keeps the filename's shape when the puck never
 *  told us its name, so anything reading the name positionally still works
 *  and the gap is obvious rather than silent. */
function puck4() {
  const m = /JumpHeight-([0-9A-Za-z]{4})/.exec(S.puckName || '');
  return m ? m[1] : 'xxxx';
}

function bundleName(at) {
  return `jumpheight-${puck4()}-${at.getFullYear()}${pad2(at.getMonth() + 1)}`
       + `${pad2(at.getDate())}-${pad2(at.getHours())}${pad2(at.getMinutes())}.zip`;
}

function notesText(now) {
  const lines = [`# JumpHeight rider notes — ${localIso(now)} (${tzLabel(now)})`];
  const typed = ($('note').value || '').replace(/\r\n/g, '\n').trim();
  if (typed) for (const l of typed.split('\n')) lines.push(l);
  if (chosenChips.sea) lines.push('sea: ' + chosenChips.sea);
  if (chosenChips.wind) lines.push('wind: ' + chosenChips.wind);
  return lines.join('\n') + '\n';
}

function buildManifest() {
  const at = S.syncedAt || new Date();
  const kvI = S.infoKV || {};
  const kvB = S.statsBeforeKV || {};
  const uptime = numOrNull(kvB.uptime_s);
  const isRaw = S.trace.format === 'jhtrace-v2-b64';
  return {
    bundle_version: 1,
    page_version: PAGE_VERSION,
    puck_name: S.puckName || null,
    fw: kvI.fw || null,
    src: kvI.src || null,
    synced_at_utc: at.toISOString(),
    synced_at_local: localIso(at),
    tz_offset_min: -at.getTimezoneOffset(),
    uptime_s: uptime,
    // The only wall-clock anchor the trace has (CONTRACT.md §2): ingest must use
    // this and must NOT substitute its own machine's clock.
    trace_epoch_utc: uptime === null ? null : new Date(at.getTime() - uptime * 1000).toISOString(),
    info_lines: S.infoLines,
    cal: S.calLine,
    stats_before: S.statsBefore,
    stats_after: S.statsAfter,
    selftest_lines: S.selftestLines,
    trace_format: S.trace.format,
    log_hz: S.trace.logHz !== null ? S.trace.logHz : numOrNull(kvI.log_hz),
    trace_bytes_device: numOrNull(kvB.trace_bytes),
    // What the trace_bytes cross-check actually compared, and whether F-22's
    // band forgave the difference. `trace_bytes_got` is in the SAME UNIT as
    // trace_bytes_device (csv body bytes) and is null on the raw path, where
    // trace_raw_bytes below is the decoded-binary count and not comparable.
    // f22_band_applied=true means: short by 1..800 B against a full puck's
    // live counter, called complete on that basis alone — recorded so ingest
    // and Josh can see the forgiveness rather than infer it from
    // verified=true (CLAUDE.md rule 3).
    trace_bytes_got: S.traceBytesGot,
    // The pull's own second `stats`. The puck keeps logging while it is
    // handled, so this is the honest ceiling for trace_bytes_got and the
    // before-figure is only a floor; ingest can re-derive the comparison from
    // these two rather than trusting `verified`.
    trace_bytes_after: numOrNull((S.statsAfterKV || {}).trace_bytes),
    f22_band_applied: S.f22BandApplied,
    trace_raw_bytes: isRaw ? S.trace.len : null,
    trace_crc32: isRaw ? S.trace.crcHex : null,
    stored_jumps_device: numOrNull(kvB.stored_jumps),
    jump_rows: S.jumpRows,
    verified: S.verified,
    // False in the bundle Josh normally receives: it is built in step 3 and
    // step 4 only exists afterwards. A re-send AFTER a clear records true,
    // because doClear drops the cached bundle so the re-send rebuilds — this
    // field is how ingest knows whether the puck still holds a copy, and a
    // cached `false` there would tell Josh to re-sync a puck he has already
    // emptied.
    cleared: S.cleared,
    transfer: {
      transport: S.transportKind,
      seconds: S.pullSeconds,
      bytes_received: S.pullBytes,
      mtu: null,      // Web Bluetooth does not expose the negotiated MTU
    },
    user_agent: navigator.userAgent,
  };
}

// ---------------------------------------------------------------- zip writer

/* A zip writer, because there is no build step and no CDN (CONTRACT.md §3) and
   Python's zipfile has to read the result on Josh's side. Local file headers +
   central directory + EOCD, CRC-32 over the UNCOMPRESSED bytes, and every size
   known before its header is written — which is why the deflate (when it is
   available at all) runs to completion first. 'store' is always a legal
   method, so the fallback is a bigger zip, never a broken one. */

class ByteWriter {
  constructor() { this.parts = []; this.n = 0; }
  u16(v) { const b = new Uint8Array(2); b[0] = v & 0xFF; b[1] = (v >>> 8) & 0xFF; this.parts.push(b); this.n += 2; }
  u32(v) {
    const b = new Uint8Array(4);
    b[0] = v & 0xFF; b[1] = (v >>> 8) & 0xFF; b[2] = (v >>> 16) & 0xFF; b[3] = (v >>> 24) & 0xFF;
    this.parts.push(b); this.n += 4;
  }
  bytes(b) { this.parts.push(b); this.n += b.length; }
  done() {
    const out = new Uint8Array(this.n);
    let o = 0;
    for (const p of this.parts) { out.set(p, o); o += p.length; }
    return out;
  }
}

function dosStamp(d) {
  // MS-DOS date/time: 2-second resolution, epoch 1980.
  const y = Math.max(1980, d.getFullYear());
  return {
    time: (d.getHours() << 11) | (d.getMinutes() << 5) | (d.getSeconds() >> 1),
    date: ((y - 1980) << 9) | ((d.getMonth() + 1) << 5) | d.getDate(),
  };
}

async function maybeDeflate(bytes) {
  // Optional: the csv/log/manifest text compresses roughly ten-fold, which
  // matters when the bundle travels over a phone's data connection. Any
  // browser without CompressionStream (older Bluefy builds) simply stores.
  if (typeof CompressionStream !== 'function' || bytes.length < 512) return null;
  try {
    const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('deflate-raw'));
    const z = new Uint8Array(await new Response(stream).arrayBuffer());
    return z.length < bytes.length ? z : null;
  } catch (_e) { return null; }
}

async function buildZip(files, when) {
  const { time, date } = dosStamp(when);
  const body = [];
  const central = [];
  let offset = 0;
  for (const f of files) {
    const crc = crc32Bytes(f.bytes);
    const deflated = f.text ? await maybeDeflate(f.bytes) : null;
    const method = deflated ? 8 : 0;
    const payload = deflated || f.bytes;
    const name = TEXT_ENCODER.encode(f.name);

    const lh = new ByteWriter();
    lh.u32(0x04034b50); lh.u16(20); lh.u16(0); lh.u16(method);
    lh.u16(time); lh.u16(date);
    lh.u32(crc); lh.u32(payload.length); lh.u32(f.bytes.length);
    lh.u16(name.length); lh.u16(0); lh.bytes(name);
    const lhb = lh.done();

    const ch = new ByteWriter();
    ch.u32(0x02014b50); ch.u16(20); ch.u16(20); ch.u16(0); ch.u16(method);
    ch.u16(time); ch.u16(date);
    ch.u32(crc); ch.u32(payload.length); ch.u32(f.bytes.length);
    ch.u16(name.length); ch.u16(0); ch.u16(0);
    ch.u16(0); ch.u16(0); ch.u32(0); ch.u32(offset);
    ch.bytes(name);
    central.push(ch.done());

    body.push(lhb, payload);
    offset += lhb.length + payload.length;
  }
  const cdSize = central.reduce((n, c) => n + c.length, 0);
  const eo = new ByteWriter();
  eo.u32(0x06054b50); eo.u16(0); eo.u16(0);
  eo.u16(files.length); eo.u16(files.length);
  eo.u32(cdSize); eo.u32(offset); eo.u16(0);
  return new Blob(body.concat(central, [eo.done()]), { type: 'application/zip' });
}

async function buildBundle() {
  const now = new Date();
  const at = S.syncedAt || now;
  const files = [
    { name: 'manifest.json', text: true,
      bytes: TEXT_ENCODER.encode(JSON.stringify(buildManifest(), null, 2) + '\n') },
    { name: 'jumps.csv', text: true, bytes: TEXT_ENCODER.encode(joinBody(S.jumpsLines)) },
  ];
  if (S.trace.format === 'jhtrace-v2-b64') {
    // Sliced, not the doubling buffer: the zip must carry exactly N bytes.
    files.push({ name: 'trace.bin', text: false, bytes: S.trace.bytes.slice() });
  } else if (S.trace.format === 'csv') {
    files.push({ name: 'trace.csv', text: true, bytes: TEXT_ENCODER.encode(csvBody().text) });
  }
  files.push({ name: 'notes.txt', text: true, bytes: TEXT_ENCODER.encode(notesText(now)) });
  files.push({ name: 'device.log', text: true,
               bytes: TEXT_ENCODER.encode(S.deviceLog.join('\n') + '\n') });
  const blob = await buildZip(files, now);
  return { name: bundleName(at), blob };
}

// --------------------------------------------------------------------- send

function downloadBlob(name, blob) {
  const url = URL.createObjectURL(blob);
  const a = el('a', { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  // "Delivered" here means handed to the browser's downloader: a page gets no
  // completion callback for <a download>, so this is the strongest signal the
  // platform offers, and CONTRACT.md §3 defines delivered that way on purpose.
  return true;
}

/** A link the rider presses himself, for the iOS branch below. A script-driven
 *  <a download> click is ignored on iOS — that is why that branch exists — but
 *  his own long-press is his own gesture. NOT tested on a real iPhone here, so
 *  the copy around it says "try", nothing counts it as delivered, and step 4
 *  stays shut regardless. The object URL is deliberately never revoked: he taps
 *  it minutes later, and it points at the blob already held as lastBundle. */
function saveLink(name, blob) {
  return el('a', { class: 'save-link', href: URL.createObjectURL(blob),
                   download: name }, name);
}

async function doSend() {
  if (busy || !S || !(S.phase === 'pulled' || S.phase === 'sent' || S.phase === 'cleared')) return;
  busy = true; setEnabled();
  setStatus('Packing the ride up…', 'busy');
  try {
    // Keep the built bundle so a cancelled share can be retried without
    // pulling the whole ride off the puck again (CONTRACT.md §3 step 3).
    if (!lastBundle) lastBundle = await buildBundle();
    const { name, blob } = lastBundle;
    let delivered = false;
    let how = null;   // 'share' | 'download' | 'upload' — decides the status wording
    const notes = [];

    // Built lazily: a browser old enough to lack File would otherwise throw
    // here rather than fall through to a path that still works.
    let file = null;
    try { file = new File([blob], name, { type: 'application/zip' }); } catch (_e) {}
    if (file && navigator.share && navigator.canShare && navigator.canShare({ files: [file] })) {
      try {
        await navigator.share({ files: [file], title: name });
        delivered = true; how = 'share';
        notes.push('Sent from the share sheet.');
      } catch (e) {
        if (e && e.name === 'AbortError') {
          notes.push('You closed the share sheet — tap Send again when you’re ready.');
        } else {
          // FALL BACK TO THE DOWNLOAD. Measured on the rider's own Mac,
          // 2026-09-10, first real use: navigator.share() rejected with
          // "Permission denied" and this was an if/else with no catch below
          // it, so he was left holding a finished 2.1 MB bundle and no way to
          // get it out of the page. The ride was never at risk — it stays on
          // the puck — but the only thing he could do was tell Josh.
          //
          // AbortError is deliberately NOT here: that is the rider closing
          // the sheet on purpose, and shoving a file into his Downloads
          // because he changed his mind is not a fix.
          //
          // Why share() failed is NOT established — desktop Chrome on macOS
          // reports NotAllowedError for several reasons, including a lost
          // transient activation after the zip. The fallback is correct
          // whichever it was, so it ships now and the cause is chased after.
          notes.push('The share sheet didn’t work: ' + ((e && e.message) || e));
          delivered = downloadBlob(name, blob);
          how = 'download';
          notes.push(`Saved to your Downloads as ${name} instead — send that `
                   + 'file to Josh from there. Nothing was lost.');
        }
      }
    } else if (IS_IOS) {
      // Plainly, and WITHOUT claiming delivery — the ride is still only here,
      // so step 4 stays shut. What this branch must NOT do is tell him to
      // open the page in Bluefy: Bluefy is the only iPhone browser that can
      // reach the puck at all (CONTRACT.md §3, web/index.html), so an iPhone
      // rider who has connected, pulled and got this far is already standing
      // in it — that sentence is the one instruction he has provably followed.
      // Give him something to try, and something to ask for if it does
      // nothing.
      notes.push('The share sheet didn’t open, so the ride hasn’t gone '
               + 'anywhere yet. It is still on this phone, and the puck still '
               + 'has its copy.');
      notes.push('Press and hold this link, then pick “Download Linked File”:');
      notes.push(saveLink(name, blob));
      notes.push('If nothing happens, ask Josh for the version of this link '
               + 'that sends the ride by itself, open that, and tap Send again.');
    } else {
      delivered = downloadBlob(name, blob);
      how = 'download';
      notes.push(`Saved to your Downloads as ${name} — send that file to Josh.`);
    }

    if (DROP_URL) {
      try {
        const res = await fetch(DROP_URL, {
          method: 'POST',
          headers: { 'Content-Type': 'application/zip', 'X-Filename': name },
          body: blob,
        });
        if (res.ok) { delivered = true; how = 'upload'; notes.push('Uploaded straight to Josh.'); }
        else notes.push(`The upload came back ${res.status} — the file is still on your phone.`);
      } catch (e) {
        notes.push('The upload didn’t go through: ' + ((e && e.message) || e));
      }
    }

    S.delivered = S.delivered || delivered;
    $('send-hint').hidden = false;
    if (S.delivered) {
      S.phase = 'sent';
      // A download is not a send: the file is on this computer, not with
      // Josh, and step 4 is about to be offered. Say exactly where the ride
      // is and what is still his to do (docs/rider-sync.md step 6/7).
      const where = how === 'download'
        ? 'Saved to your Downloads. Send that file to Josh (Messages, Mail or AirDrop)'
        : 'Sent';
      // There is no step 4 for the rider on this loan (ALLOW_CLEAR): the last
      // thing he is told to do is nothing. The old sentence here — "Last step:
      // empty the puck so it has room for your next ride" — was an instruction
      // to do the one thing the brief says he must never do, printed on the
      // success path where he is most likely to follow it.
      setStatus(S.verified
        ? `${where}. You’re finished — leave the puck to Josh.`
        : `${where} — but it wasn’t a clean copy, so do NOT empty the puck. `
          + 'Tell Josh and copy the ride again.', S.verified ? 'ok' : 'busy');
    } else {
      setStatus('Not sent yet. Tap Send again and pick where it should go.');
    }
    showResult(S.delivered ? `Bundle: ${name} (${human(blob.size)})`
                           : `Bundle ready: ${name} (${human(blob.size)})`,
               S.delivered ? 'ok' : null, notes);
  } catch (e) {
    setStatus('Couldn’t pack the ride up: ' + ((e && e.message) || e)
            + '\nNothing was erased — the puck still has everything.', 'bad');
  } finally {
    busy = false; setEnabled();
  }
}

// -------------------------------------------------------------------- clear

async function doClear() {
  // Belt and braces on top of the hidden button: nothing erases the puck
  // unless the ride was both verified AND delivered (CONTRACT.md §3 step 4) —
  // and, on this loan, unless the URL asked for step 4 at all.
  if (busy || !ALLOW_CLEAR || !S || !S.verified || !S.delivered || !transport) return;
  busy = true; setEnabled();
  setStatus('Emptying the puck…', 'busy');
  try {
    const r = await runCommand('clear');
    if (r.err) {
      setStatus(failWord(r.err, 'The puck didn’t confirm it was emptied.'), 'bad');
      return;
    }
    const st = await runCommand('stats');
    if (st.err) {
      setStatus(failWord(st.err, 'The puck was told to empty, but didn’t report back.'), 'bad');
      return;
    }
    const kv = S.statsLatestKV || {};
    const jumps = numOrNull(kv.stored_jumps);
    const bytes = numOrNull(kv.trace_bytes);
    if (jumps === 0 && bytes === 0) {
      S.cleared = true;
      S.phase = 'cleared';
      // Drop the cached zip so a re-send REBUILDS with cleared:true (and a
      // device.log that carries this exchange). ingest reads that field to
      // know whether the puck still holds a copy; shipping the cached
      // cleared:false after an erase would tell Josh to re-sync a puck that
      // is now empty. Same bundle name — the later file supersedes the
      // earlier one, which is the honest ordering.
      lastBundle = null;
      $('pull-hint').textContent = 'The puck is empty now — there is nothing '
        + 'left to copy. Your ride is still on this phone, so you can send it '
        + 'again if you need to.';
      setStatus('Puck is empty and ready for your next ride.', 'ok');
      showResult('All done. Thanks — that’s everything.', 'ok', []);
    } else {
      // Never call a wipe done on a reading that says otherwise.
      setStatus('The puck still reports '
              + `${jumps === null ? 'an unknown number of' : jumps} jumps and `
              + `${bytes === null ? 'an unknown amount of' : human(bytes)} ride data. `
              + 'Nothing is lost — show this to Josh.', 'bad');
    }
  } finally {
    busy = false; setEnabled();
    renderFacts();
  }
}

// -------------------------------------------------------------------- chips

function buildChips(group, host) {
  for (const label of CHIPS[group]) {
    const b = el('button', {
      class: 'chip', type: 'button', 'aria-pressed': 'false',
      onclick: () => {
        const on = chosenChips[group] === label;
        chosenChips[group] = on ? null : label;
        for (const other of host.children) {
          const sel = other.dataset.value === chosenChips[group];
          other.setAttribute('aria-pressed', String(sel));
          other.textContent = (sel ? '✓ ' : '') + other.dataset.value;
        }
        // The note travels inside the bundle, so a chip tapped after Send
        // must rebuild it rather than ship the previous text.
        lastBundle = null;
      },
    }, label);
    b.dataset.value = label;
    host.append(b);
  }
}

// ---------------------------------------------------------------- test seam

/** CONTRACT.md §3: with '#mock' the page installs a MockTransport, exposes
 *  window.__mock = { feed(line), sent: [] }, auto-connects, and exposes
 *  window.__sync = { state(), lastBundle() }. Kept deliberately small. */
function setupMock() {
  const t = new MockTransport();
  window.__mock = { feed: (line) => t.receive(line), sent: t.sent };
  afterConnect(t, MOCK_KIND, null);
}

window.__sync = {
  state: () => (S ? {
    phase: S.phase,
    connected: !!transport,
    verified: S.verified,
    delivered: S.delivered,
    cleared: S.cleared,
    trace_format: S.trace.format,
    jump_rows: S.jumpRows,
    reasons: S.reasons,
    trace_bytes_device: S.traceBytesDevice,
    trace_bytes_after: S.traceBytesAfter,
    trace_bytes_got: S.traceBytesGot,
    f22_band_applied: S.f22BandApplied,
    f22_note: S.f22Note,
    growth_note: S.growthNote,
    bundle: lastBundle ? lastBundle.name : null,
  } : { phase: 'boot', connected: false, verified: false, delivered: false,
        cleared: false, trace_format: null, jump_rows: 0, reasons: [],
        trace_bytes_device: null, trace_bytes_after: null, trace_bytes_got: null,
        f22_band_applied: false, f22_note: null, growth_note: null,
        bundle: null }),
  lastBundle: () => lastBundle,
};

// --------------------------------------------------------------------- boot

function init() {
  $('page-version').textContent = 'page version ' + PAGE_VERSION;
  $('btn-connect').addEventListener('click', doConnect);
  $('btn-connect-usb').addEventListener('click', doConnectUsb);
  // ONE way in per device — visibility only; both code paths stay wired and
  // reachable (doConnect is still bound above, and the mock/test seam does not
  // go through either button).
  //
  // The old rule was "offer every link this browser can make", and Chrome on a
  // Mac can make both: the rider saw two competing black Connect buttons, two
  // hints, and had to choose between them with nothing on the page saying
  // which. He has one configuration — a Mac, Chrome, the USB cable — so where
  // there is a serial port, the cable IS the path and Bluetooth is hidden
  // entirely. Bluetooth (and its 20–30 minute estimate) appears only where
  // there is no serial port at all: a phone. Safari has neither, and falls
  // through to the last-resort sentence at the bottom of init().
  const hasSerial = !!navigator.serial;
  $('btn-connect-usb').hidden = !hasSerial;
  $('usb-hint').hidden = !hasSerial;
  const offerBle = !hasSerial && !!navigator.bluetooth;
  $('btn-connect').hidden = !offerBle;
  $('ble-hint').hidden = !offerBle;
  $('ble-time-hint').hidden = !offerBle;
  if (offerBle) {
    // The page's default copy is written for the Mac — "your Mac", "plugged
    // in", "Plug in the puck". On the phone shape none of that is true, and
    // #pull-hint would sit directly above #ble-time-hint telling him to keep
    // the puck plugged in while it tells him to keep the phone next to it.
    $('lede').textContent = 'Copy the ride off the puck and send it to Josh. '
      + 'Three steps — then you’re done. Keep your phone next to the puck the '
      + 'whole time.';
    $('step1-title').textContent = 'Connect to the puck';
    $('pull-hint').hidden = true;
  }
  $('btn-pull').addEventListener('click', doPull);
  $('btn-send').addEventListener('click', doSend);
  $('btn-clear').addEventListener('click', doClear);
  // Step 4 is not part of the rider's page on this loan: the whole section
  // goes, so there is no heading to read, no button to find, and nothing to
  // wonder about. ?allowclear=1 puts it back for Josh — still gated on
  // verified AND delivered underneath. The step-3 closing line is the
  // complement: exactly one of the two is ever on screen.
  $('step-clear').hidden = !ALLOW_CLEAR;
  $('finish-hint').hidden = ALLOW_CLEAR;
  buildChips('sea', $('chips-sea'));
  buildChips('wind', $('chips-wind'));
  // A typed note changes the bundle, so a note edited after Send must not
  // ship the stale zip.
  $('note').addEventListener('input', () => { lastBundle = null; });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && transport && !wakeLock) acquireWakeLock();
  });
  setEnabled();
  if (IS_MOCK) setupMock();
  else if (!navigator.bluetooth && !navigator.serial) {
    // The last-resort branch: neither transport exists, so this is Safari (or
    // something older). The Mac case leads because it is the rider's ONE
    // configuration and the one remedy he can act on in ten seconds; the
    // Android and iPhone sentences stay because this page is also the phone
    // fallback and Bluefy is the only iPhone browser that reaches the puck.
    setStatus('This browser can’t reach the puck. On a Mac, open this page in '
            + 'Chrome and use the cable. On Android, use Chrome. On an iPhone, '
            + 'open it in the free Bluefy app.', 'bad');
  }
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
