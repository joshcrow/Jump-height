/*
  Jump Height browser app — all the logic.

  The device speaks ONE protocol (newline-terminated text, see main.cpp) over
  two physical links: Web Bluetooth (Nordic UART Service) and Web Serial (USB).
  So the design is: a tiny transport abstraction — { sendLine, onLine,
  disconnect } — with a Ble and a Serial implementation that both do the same
  job (bytes in/out + reassemble lines on '\n'), and above it ONE line handler
  that neither knows nor cares which link delivered the line. Add a link once,
  everything else just works.

  Testability (drives the Playwright test, another agent owns it):
  when location.hash === '#mock' we install a MockTransport as the active link
  and expose a deliberately tiny, stable hook —
      window.__mock = { feed(line), sent: [] }
  feed(line) injects a line as if the device sent it; sent[] is the exact array
  the app pushes outgoing commands to. Keep that hook small and unchanging.

  Machine lines are parsed with parseKV, a faithful port of tools/jump's
  parse_kv (tag, key=value pairs, bare words as _args), so the browser and the
  CLI read the wire identically.

  Presentation notes (the UI/UX layer, safe to evolve):
  - Sunlight-first: light theme is the default; Auto/Light/Dark is an explicit
    choice persisted in localStorage and applied via data-theme on <html>.
  - The owner thinks in FEET: a global unit preference (data-unit on <html>)
    decides which number is shown big; the other is shown small beneath.
  - "Sync" is the one word for pulling a session off the device (the wire
    command is still 'dump'); the flow shows live progress and, on a verified
    save, offers to clear the device.
*/

// ------------------------------------------------------------------ protocol

// Nordic UART Service — the Phase-3 BLE contract, shared by all three agents.
const NUS_SERVICE = '6e400001-b5a3-f393-e0a9-e50e24dcca9e';
const NUS_RX      = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'; // client -> device (writes)
const NUS_TX      = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'; // device -> client (notifies)
const DEVICE_NAME = 'JumpHeight';

const BAUD = 115200;
const STORAGE_KEY = 'jh_sessions';
const THEME_KEY = 'jh_theme';
const UNIT_KEY = 'jh_unit';
const M_TO_FT = 3.28084;

// ------------------------------------------------------------------- icons
// Lucide icons (lucide.dev, ISC/MIT), inlined as path data so the page stays
// dependency-free and offline-capable. No emoji anywhere in the UI — icons
// carry state alongside words (never color or icon alone).
const ICON_PATHS = {
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  'triangle-alert': '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
  minus: '<path d="M5 12h14"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/><path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
  moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
  monitor: '<rect width="20" height="14" x="2" y="3" rx="2"/><path d="M8 21h8"/><path d="M12 17v4"/>',
  'chevron-right': '<path d="m9 18 6-6-6-6"/>',
  waves: '<path d="M2 6c.6.5 1.2 1 2.5 1C7 7 7 5 9.5 5c2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/><path d="M2 12c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/><path d="M2 18c.6.5 1.2 1 2.5 1 2.5 0 2.5-2 5-2 2.6 0 2.4 2 5 2 2.5 0 2.5-2 5-2 1.3 0 1.9.5 2.5 1"/>',
};
function icon(name, cls) {
  const s = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  s.setAttribute('viewBox', '0 0 24 24');
  s.setAttribute('fill', 'none');
  s.setAttribute('stroke', 'currentColor');
  s.setAttribute('stroke-width', '2');
  s.setAttribute('stroke-linecap', 'round');
  s.setAttribute('stroke-linejoin', 'round');
  s.setAttribute('aria-hidden', 'true');
  s.setAttribute('class', 'ico' + (cls ? ' ' + cls : ''));
  s.innerHTML = ICON_PATHS[name] || '';
  return s;
}

// Self-test status → icon + color class; the status is also conveyed by the
// row's text, so it is never icon-or-color alone.
const STATUS_ICON = { PASS: 'check', WARN: 'triangle-alert', FAIL: 'x', SKIP: 'minus' };
const STATUS_CLASS = { PASS: 'ok', WARN: 'warn', FAIL: 'bad', SKIP: 'skip' };

const BLE_UNSUPPORTED =
  'No Bluetooth in this browser. iPhone/iPad: open this page in the free ' +
  '“Bluefy” app.';
const SERIAL_UNSUPPORTED =
  'No USB in this browser — connect over Bluetooth instead.';
const BOTH_UNSUPPORTED =
  'This browser can\u2019t reach the device. Use Chrome or Edge on a computer ' +
  'or Android phone — on an iPhone or iPad, open this page in the free ' +
  '“Bluefy” app.';

/** 'JUMP n=1 airtime_s=0.62' -> {_tag:'JUMP', n:'1', airtime_s:'0.62', _args:[]}.
 *  Bare words land in _args: 'STATE recording' -> {_tag:'STATE', _args:['recording']}.
 *  Mirrors tools/jump parse_kv exactly. */
function parseKV(line) {
  const parts = line.trim().split(/\s+/).filter(Boolean);
  const out = { _tag: parts[0] || '', _args: [] };
  for (const p of parts.slice(1)) {
    const eq = p.indexOf('=');
    if (eq >= 0) out[p.slice(0, eq)] = p.slice(eq + 1);
    else out._args.push(p);
  }
  return out;
}

/** Group 'FILE <name> BEGIN' ... 'FILE <name> END' framed output by filename.
 *  Mirrors tools/jump parse_file_sections. */
function parseFileSections(lines) {
  const files = {};
  let current = null;
  for (const line of lines) {
    if (line.startsWith('FILE ') && line.endsWith(' BEGIN')) {
      current = line.split(/\s+/)[1];
      files[current] = [];
    } else if (line.startsWith('FILE ') && line.endsWith(' END')) {
      current = null;
    } else if (current !== null) {
      files[current].push(line);
    }
  }
  return files;
}

/** Reassemble a byte/text stream into whole lines. Notifications and serial
 *  reads arrive in arbitrary chunks; a line may straddle two of them. Returns a
 *  push(chunk) that calls emit(line) once per completed '\n'-terminated line. */
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

// ----------------------------------------------------------------- transports
// Each transport is { sendLine(s), onLine(cb), disconnect() } plus onClose(cb)
// so the UI can react when the link drops on its own (unplug / out of range).

/** USB via Web Serial. */
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
    await this.port.open({ baudRate: BAUD });
    this._writer = this.port.writable.getWriter();
    this._reader = this.port.readable.getReader();
    this._readLoop(); // fire and forget
  }

  async _readLoop() {
    try {
      for (;;) {
        const { value, done } = await this._reader.read();
        if (done) break;
        if (value) this._push(this._decoder.decode(value, { stream: true }));
      }
    } catch (_e) {
      // read error usually means the cable was pulled — fall through to close.
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

/** Bluetooth via Web Bluetooth + Nordic UART Service. */
class BleTransport {
  constructor(device) {
    this.device = device;
    this._onLine = null;
    this._onClose = null;
    this._closing = false;
    this._preferNoResponse = true; // RX usually supports write-without-response
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
    // BLE's default payload is ~20 bytes, so chunk the line to be safe. Commands
    // are short, but a client that assumes 20 will never surprise a device.
    const bytes = this._encoder.encode(s + '\n');
    for (let i = 0; i < bytes.length; i += 20) {
      await this._write(bytes.slice(i, i + 20));
    }
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

/** Test double. feed(line) plays the device; sent[] captures our commands. */
class MockTransport {
  constructor() {
    this.sent = [];
    this._onLine = null;
  }
  onLine(cb) { this._onLine = cb; }
  onClose(_cb) {}
  sendLine(s) { this.sent.push(s); }
  receive(line) { if (this._onLine) this._onLine(String(line).replace(/\r?\n$/, '')); }
  async disconnect() { this._onLine = null; }
}

// --------------------------------------------------------------------- state

let transport = null;      // active transport, or null when disconnected
let transportKind = null;  // 'USB' | 'BLE' | 'Demo'
const deviceInfo = {};     // last INFO/PARAMS seen
const live = { count: 0, bestM: 0 };
const selftest = { active: false, rows: [], result: null };
let activeCapture = null;  // in-flight command capture (used by 'dump'/sync)

let unitPref = 'ft';       // 'ft' | 'm' — the owner thinks in feet
let themeMode = 'light';   // 'light' | 'dark' (seeded from the OS until first tap)
const liveJumps = [];      // per-jump data for this session's live mini-chart
let lastStored = { jumps: 0, bestM: 0 };  // last STATS stored_* seen (for the banner)
let lastTraceBytes = NaN;  // optional STATS trace_bytes, for a real sync %
// Battery telemetry (vbat_mv/batt_pct/chg on INFO/STATS, docs/sense.md
// §3.4): only battery-sensing devices (the Sense) send these keys — on a
// v1/ESP32 device they never arrive and the battery UI never shows. The
// puck is sealed: this readout is the only battery gauge the product has.
const battery = { pct: NaN, mv: NaN, chg: NaN };
let syncState = null;      // { bytes, expected, kind } while a sync is running
let lastSynced = null;     // the session object shown in the inline result panel
let wakeLock = null;       // Screen Wake Lock sentinel while connected

// tiny helpers
const $ = (id) => document.getElementById(id);
const setText = (id, t) => { const n = $(id); if (n) n.textContent = t; };
const pf = (v) => (v == null ? NaN : parseFloat(v));
const fmt = (x, d) => (Number.isNaN(x) ? '–' : x.toFixed(d));

/** Terse DOM builder — avoids innerHTML so device text can never inject HTML. */
function el(tag, props = {}, ...kids) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (v == null || v === false) continue;
    if (k === 'class') n.className = v;
    else if (k === 'text') n.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') n.addEventListener(k.slice(2), v);
    else if (v === true) n.setAttribute(k, '');
    else n.setAttribute(k, v);
  }
  for (const c of kids) if (c != null) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
}

/** SVG element builder (charts are hand-built inline SVG, no libraries). */
function svg(tag, attrs = {}, ...kids) {
  const n = document.createElementNS('http://www.w3.org/2000/svg', tag);
  for (const k in attrs) if (attrs[k] != null) n.setAttribute(k, attrs[k]);
  for (const c of kids) if (c != null) n.append(c);
  return n;
}

// --------------------------------------------------------------- unit helpers

/** Best height in the preferred unit, e.g. '5.9 ft' or '1.79 m'. */
function heightPref(m) {
  if (!(m > 0)) return '–';
  return unitPref === 'ft' ? fmt(m * M_TO_FT, 1) + ' ft' : fmt(m, 2) + ' m';
}
/** Both units, preferred first: '5.9 ft (1.79 m)'. Same zero-guard as
 *  heightPref so the two can never disagree side by side. */
function heightPair(m) {
  if (!(m > 0)) return '–';
  const ft = m * M_TO_FT;
  return unitPref === 'ft'
    ? `${fmt(ft, 1)} ft (${fmt(m, 2)} m)`
    : `${fmt(m, 2)} m (${fmt(ft, 1)} ft)`;
}

/** Content identity for a synced session: same device bytes => same key, so
 *  re-syncing an uncleared device can be recognised instead of duplicated. */
function contentKey(jumpsCsv, traceCsv) {
  const s = jumpsCsv + ' ' + traceCsv;
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
  return h.toString(36) + '-' + s.length.toString(36);
}

// ------------------------------------------------------------- line handling

/** Every incoming line goes through here, whatever the link. */
function handleLine(line) {
  if (line == null || line.trim() === '') return;
  appendConsole(line, 'rx');
  feedCapture(line);  // command capture (sync) — independent of the live UI
  // A running sync tracks bytes for its progress readout. feedCapture may have
  // just finished the sync on the 'OK dump'/'ERR' line (clearing syncState), in
  // which case we skip — the terminal line isn't payload.
  if (syncState) {
    syncState.bytes += byteLen(line) + 1;
    showSyncProgress(syncProgressText());
  }
  dispatch(line);     // live UI updates
}

function byteLen(s) { try { return new TextEncoder().encode(s).length; } catch (_e) { return s.length; } }

/** Route a line to whatever cares about its tag. */
function dispatch(line) {
  if (line.startsWith('#')) {
    // Chatter the UI acts on: self-test hints, and the detector's near-miss
    // narration while a bench flow is running.
    if (selftest.active && line.startsWith('# hint:')) addSelftestHint(line.slice(7).trim());
    if (bench) benchOnChatter(line);
    return;
  }
  const kv = parseKV(line);
  switch (kv._tag) {
    case 'JUMP':     onJump(kv); if (bench) benchOnJump(kv); break;
    case 'STATS':    onStats(kv); break;
    case 'STATE':    onState(kv); break;
    case 'INFO':     onInfo(kv); break;
    case 'PARAMS':   onParams(kv); break;
    case 'CAL':      onCal(kv); break;
    case 'SELFTEST': onSelftest(kv); break;
    default: break; // FILE/OK/ERR/READY: nothing extra; already in the console
  }
}

/** Send a command: echo it to the console, record it, hand it to the link.
 *  sendLine may be sync (mock) or async (BLE/Serial) — tolerate both. */
function send(cmd) {
  if (!transport) return;
  appendConsole(cmd, 'tx');
  try {
    const r = transport.sendLine(cmd);
    if (r && typeof r.catch === 'function') r.catch((e) => appendConsole('send failed: ' + e.message, 'err'));
  } catch (e) {
    appendConsole('send failed: ' + e.message, 'err');
  }
}

// Command capture: collect every line after a command until its 'OK <cmd>' (or
// 'ERR ...') terminator, then hand the batch to a callback. Mirrors the CLI's
// Device.command(). Only 'dump' needs it; a single capture runs at a time.
function startCapture(firstWord, onDone, inactivityMs) {
  activeCapture = { firstWord, lines: [], onDone, ms: inactivityMs || 20000, timer: null };
  armCaptureTimer();
}
function armCaptureTimer() {
  if (!activeCapture) return;
  clearTimeout(activeCapture.timer);
  // Inactivity timeout: resets on every line, so a long-but-flowing BLE dump
  // never trips it, while a genuinely stuck device does.
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
    c.lines.push(line); c.onDone(c.lines, line);
  } else {
    activeCapture.lines.push(line);
    armCaptureTimer();
  }
}

// --------------------------------------------------------------- LIVE section

function onJump(kv) {
  const n = parseInt(kv.n, 10);
  const hm = pf(kv.height_m), hft = pf(kv.height_ft), at = pf(kv.airtime_s), best = pf(kv.best_m);
  setText('live-height-m', fmt(hm, 2));
  setText('live-height-ft', fmt(hft, 1));
  setText('live-airtime', fmt(at, 2));
  if (!Number.isNaN(n)) live.count = n;
  if (!Number.isNaN(best)) live.bestM = best;
  renderLiveStats();
  addJumpToFeed(n, hm, hft, at);
  if (!Number.isNaN(hm)) {
    liveJumps.push({ n: Number.isNaN(n) ? liveJumps.length + 1 : n, height_m: hm,
                     height_ft: Number.isNaN(hft) ? hm * M_TO_FT : hft, airtime_s: at });
    renderLiveMini();
  }
  $('live-empty').hidden = true;
}

/** Capture the optional battery keys (INFO and STATS both carry them) and
    refresh the pill. Absent keys leave prior state — a STATS from an old
    firmware must not blank a battery level INFO already reported. */
function captureBattery(kv) {
  let changed = false;
  if (kv.batt_pct != null) { battery.pct = parseInt(kv.batt_pct, 10); changed = true; }
  if (kv.vbat_mv != null)  { battery.mv = parseInt(kv.vbat_mv, 10);  changed = true; }
  if (kv.chg != null)      { battery.chg = parseInt(kv.chg, 10);     changed = true; }
  if (changed) renderBattery();
}

/** The header battery pill — the beach-glanceable "charge before this
    session?" answer. Hidden until a battery key has ever arrived. */
function renderBattery() {
  const p = $('batt-pill');
  if (!p || Number.isNaN(battery.pct)) return;
  p.hidden = false;
  // Battery keys prove a Sense-class puck → it has the `off` command too.
  const ps = $('power-section');
  if (ps) { ps.hidden = false; $('power-help').hidden = false; }
  const charging = battery.chg === 1;
  const icon = charging ? '⚡' : (battery.pct <= 20 ? '🪫' : '🔋');
  // While charging the voltage floats high and pct reads optimistic (the
  // seam's own caveat) — lead with the state, keep the number secondary.
  p.textContent = charging ? `${icon} charging` : `${icon} ${battery.pct}%`;
  p.title = Number.isNaN(battery.mv) ? '' : `battery ${(battery.mv / 1000).toFixed(2)} V`;
  p.className = 'pill ' + (charging ? 'pill-on' : (battery.pct <= 20 ? 'pill-off' : 'pill-on'));
}

function onStats(kv) {
  captureBattery(kv);
  const sj = parseInt(kv.session_jumps, 10);
  const sb = pf(kv.session_best_m);
  if (!Number.isNaN(sj)) live.count = sj;
  if (!Number.isNaN(sb)) live.bestM = sb;
  renderLiveStats();
  if (kv.stored_jumps != null) {
    const n = parseInt(kv.stored_jumps, 10) || 0;
    const bm = pf(kv.stored_best_m);
    lastStored = { jumps: n, bestM: Number.isNaN(bm) ? 0 : bm };
    renderBanner();  // the banner is the single place this fact is shown
  }
  // Optional field, added to STATS in parallel. Parse if present, tolerate absence.
  if (kv.trace_bytes != null) {
    const tb = parseInt(kv.trace_bytes, 10);
    lastTraceBytes = Number.isNaN(tb) ? NaN : tb;
  }
}

function onState(kv) {
  const rec = kv._args[0] === 'recording';
  const st = $('live-state');
  st.hidden = false;
  st.textContent = rec ? 'recording' : 'idle';  // the CSS ::before dot carries the color
  st.className = 'badge ' + (rec ? 'badge-rec' : 'badge-idle');
}

function renderLiveStats() {
  const b = live.bestM;
  setText('live-best-m', b ? fmt(b, 2) : '–');
  setText('live-best-ft', b ? fmt(b * M_TO_FT, 1) : '–');
  setText('live-count', String(live.count || 0));
}

function addJumpToFeed(n, hm, hft, at) {
  const feed = $('jump-feed');
  // The preferred unit leads here too — one preference governs every number
  // on the page, so the feed never contradicts the hero.
  const ft = unitPref === 'ft';
  const big = ft ? `${fmt(hft, 1)} ft` : `${fmt(hm, 2)} m`;
  const sml = ft ? `${fmt(hm, 2)} m` : `${fmt(hft, 1)} ft`;
  const li = el('li', { class: 'feed-item' },
    el('span', { class: 'feed-n', text: '#' + (Number.isNaN(n) ? '?' : n) }),
    el('span', { class: 'feed-h', text: big }),
    el('span', { class: 'feed-ft muted', text: sml }),
    el('span', { class: 'feed-at muted', text: `${fmt(at, 2)} s` }),
  );
  feed.insertBefore(li, feed.firstChild); // newest on top
  while (feed.childNodes.length > 100) feed.removeChild(feed.lastChild);
}

function renderLiveMini() {
  const host = $('live-mini');
  if (!host) return;
  host.textContent = '';
  if (!liveJumps.length) { host.hidden = true; return; }
  host.hidden = false;
  host.append(buildBarChart(liveJumps, { vbh: 64, mini: true }));
}

function resetLiveSession() {
  live.count = 0; live.bestM = 0;
  liveJumps.length = 0;
  ['live-height-m', 'live-height-ft', 'live-airtime'].forEach((id) => setText(id, '–'));
  setText('live-best-m', '–'); setText('live-best-ft', '–'); setText('live-count', '0');
  $('jump-feed').textContent = '';
  $('live-empty').hidden = false;
  $('live-state').hidden = true;
  renderLiveMini();
}

// -------------------------------------------------------- device info + self-test

function onInfo(kv) {
  captureBattery(kv);
  deviceInfo.fw = kv.fw; deviceInfo.sample_hz = kv.sample_hz; deviceInfo.ble = kv.ble;
  renderDeviceInfo();
}
function onParams(kv) {
  deviceInfo.params = Object.entries(kv)
    .filter(([k]) => k !== '_tag' && k !== '_args')
    .map(([k, v]) => `${k}=${v}`).join('  ');
  renderDeviceInfo();
}
function onCal(kv) {
  // Effective calibration + where it lives — the phone-only rider's only way
  // to see whether a saved calibration is actually in force.
  deviceInfo.calOffset = parseFloat(kv.airtime_offset_s || '0');
  deviceInfo.calSource = kv.source || 'defaults';
  const offMs = Math.round(parseFloat(kv.airtime_offset_s || '0') * 1000);
  const scale = parseFloat(kv.height_scale || '1');
  deviceInfo.cal = (kv.source === 'device'
    ? 'Calibration: saved on the device'
    : 'Calibration: factory defaults')
    + ` (timing ${offMs >= 0 ? '+' : ''}${offMs} ms` +
    (Math.abs(scale - 1) > 0.0005 ? `, height ×${scale.toFixed(3)})` : ')');
  renderDeviceInfo();
}
function renderDeviceInfo() {
  const c = $('device-info');
  c.hidden = false; c.textContent = '';
  const bits = [];
  if (deviceInfo.fw) bits.push('Firmware v' + deviceInfo.fw);
  if (deviceInfo.sample_hz) bits.push(deviceInfo.sample_hz + ' Hz sampling');
  if (deviceInfo.ble != null) bits.push('Bluetooth: ' + (deviceInfo.ble === '1' ? 'yes' : 'no'));
  if (!Number.isNaN(battery.pct)) {
    bits.push('Battery: ' + (battery.chg === 1 ? 'charging ⚡' : battery.pct + '%')
              + (Number.isNaN(battery.mv) ? '' : ' (' + (battery.mv / 1000).toFixed(2) + ' V)'));
  }
  c.append(el('div', { class: 'info-line', text: bits.join('  ·  ') || 'Device connected' }));
  if (deviceInfo.cal) c.append(el('div', { class: 'info-line', text: deviceInfo.cal }));
  if (deviceInfo.params) c.append(el('div', { class: 'muted small mono', text: deviceInfo.params }));
}

function onSelftest(kv) {
  const kind = kv._args[0];
  if (kind === 'BEGIN') { selftest.active = true; selftest.rows = []; selftest.result = null; }
  else if (kind === 'END') { selftest.active = false; selftest.result = kv.result || ''; }
  else selftest.rows.push({ name: kind, status: kv._args[1] || '', detail: kv.detail || '', hints: [] });
  renderSelftest();
}
function addSelftestHint(text) {
  if (selftest.rows.length) selftest.rows[selftest.rows.length - 1].hints.push(text);
  renderSelftest();
}
function renderSelftest() {
  const c = $('selftest-card');
  c.hidden = false; c.textContent = '';
  const table = el('div', { class: 'selftest' });
  for (const row of selftest.rows) {
    const mark = el('span', { class: 'st-mark ' + (STATUS_CLASS[row.status] || ''), title: row.status });
    mark.append(icon(STATUS_ICON[row.status] || 'minus'));
    table.append(el('div', { class: 'st-row' },
      mark,
      el('span', { class: 'st-name', text: row.name }),
      el('span', { class: 'st-detail muted', text: row.detail }),
    ));
    for (const h of row.hints) table.append(el('div', { class: 'st-hint muted', text: h }));
  }
  c.append(table);
  if (selftest.result === 'INTERRUPTED') {
    c.append(el('div', { class: 'st-result bad',
      text: 'Interrupted — the device disconnected. Reconnect and run it again.' }));
  } else if (selftest.result) {
    const ok = selftest.result === 'PASS';
    const line = el('div', { class: 'st-result ' + (ok ? 'ok' : 'bad') });
    line.append(icon(ok ? 'check' : 'x'), document.createTextNode(` Result: ${selftest.result}`));
    c.append(line);
  } else if (!selftest.rows.length) {
    c.append(el('div', { class: 'muted', text: 'Running…' }));
  }
}

// ------------------------------------------------------------------ charts
// One inline-SVG bar chart, no libraries. Single series in the accent colour.
// Rules (a validated design method): thin bars, 2px min gap, 4px rounded TOP
// corners only (flat at the baseline), a visible baseline, at most one gridline
// at the max, no y-axis, and a direct label on ONLY the tallest bar in text ink
// (never the series colour). Per-bar hover/tap tooltip, hit target the full
// column height and >=24px wide even for a thin bar.

const VBW = 640; // nominal viewBox width; the SVG scales to its container.

function maxHeightM(jumps) { return jumps.reduce((m, j) => Math.max(m, j.height_m || 0), 0); }

/** Path for a bar with rounded top corners (radius r) and a flat base. */
function barPath(x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h));
  const x2 = x + w, yb = y + h;
  return `M${x},${yb} L${x},${y + r} Q${x},${y} ${x + r},${y} `
       + `L${x2 - r},${y} Q${x2},${y} ${x2},${y + r} L${x2},${yb} Z`;
}

function barTooltip(j) {
  const ft = (j.height_m || 0) * M_TO_FT;
  const h = unitPref === 'ft'
    ? `${fmt(ft, 1)} ft (${fmt(j.height_m, 2)} m)`
    : `${fmt(j.height_m, 2)} m (${fmt(ft, 1)} ft)`;
  return `#${j.n} · ${h} · ${fmt(j.airtime_s, 2)} s air`;
}

function buildBarChart(jumps, opts = {}) {
  const vbh = opts.vbh || 140;
  const wrap = el('div', { class: 'chart' + (opts.mini ? ' chart-mini' : '') });
  if (opts.testid) wrap.setAttribute('data-testid', opts.testid);
  if (!jumps || !jumps.length) {
    wrap.append(el('div', { class: 'muted small', text: 'No jumps in this session.' }));
    return wrap;
  }
  const s = svg('svg', {
    viewBox: `0 0 ${VBW} ${vbh}`, preserveAspectRatio: 'none', role: 'img',
    'aria-label': 'Per-jump height chart',
  });
  s.style.height = (opts.mini ? 64 : vbh) + 'px';
  // A sparse session must not stretch into a handful of screen-wide slabs:
  // cap the rendered width so a bar never exceeds ~72px on screen, and keep
  // the chart left-aligned (bars grow rightward as jumps land).
  s.style.maxWidth = Math.min(100, jumps.length * 18 + 6) + '%';
  s.style.display = 'block';

  const padX = 6;
  const topPad = opts.showLabel ? 24 : 8;
  const basePad = 8;
  const baseY = vbh - basePad;
  const plotH = baseY - topPad;
  const n = jumps.length;
  const slot = (VBW - padX * 2) / n;
  const gap = Math.max(2, Math.min(slot * 0.35, 10));
  const bw = Math.max(1, slot - gap);
  const maxH = maxHeightM(jumps) || 1;
  let maxIdx = 0;
  for (let i = 1; i < n; i++) if ((jumps[i].height_m || 0) > (jumps[maxIdx].height_m || 0)) maxIdx = i;

  // At most one gridline, at the max value (skip on the tiny live strip).
  if (!opts.mini) s.append(svg('line', { x1: 0, y1: topPad, x2: VBW, y2: topPad, stroke: 'var(--grid)', 'stroke-width': 1 }));

  const centers = [];
  jumps.forEach((j, i) => {
    const h = Math.max(2, (Math.max(0, j.height_m || 0) / maxH) * plotH);
    const x = padX + slot * i + (slot - bw) / 2;
    const y = baseY - h;
    s.append(svg('path', { d: barPath(x, y, bw, h, 4), fill: 'var(--series)' }));
    centers.push(x + bw / 2);
  });

  // Visible baseline sits above the bars' flat feet.
  s.append(svg('line', { x1: 0, y1: baseY, x2: VBW, y2: baseY, stroke: 'var(--baseline)', 'stroke-width': 2 }));

  // Direct label on the tallest bar only, in normal text ink.
  if (opts.showLabel) {
    const lbl = heightPref(jumps[maxIdx].height_m);
    const cx = Math.max(20, Math.min(VBW - 20, centers[maxIdx]));
    s.append(svg('text', { x: cx, y: topPad - 8, 'text-anchor': 'middle', class: 'bar-label' }, document.createTextNode(lbl)));
  }

  // Tooltip + transparent hit targets last, so they sit on top.
  const tip = el('div', { class: 'chart-tip', hidden: true });
  const show = (i) => {
    tip.textContent = barTooltip(jumps[i]);
    tip.hidden = false;
    const cw = wrap.clientWidth || VBW;
    tip.style.left = Math.round(centers[i] * (cw / VBW)) + 'px';
  };
  const hide = () => { tip.hidden = true; };
  jumps.forEach((j, i) => {
    const hitW = Math.max(bw, 24);
    const hit = svg('rect', { x: centers[i] - hitW / 2, y: 0, width: hitW, height: vbh, fill: 'transparent', class: 'bar-hit' });
    hit.append(svg('title', {}, document.createTextNode(barTooltip(j)))); // native hover fallback
    hit.addEventListener('pointerenter', () => show(i));
    hit.addEventListener('pointerleave', hide);
    hit.addEventListener('click', () => show(i));
    s.append(hit);
  });
  wrap.addEventListener('pointerleave', hide);

  wrap.append(s);
  wrap.append(tip);
  return wrap;
}

// ----------------------------------------------------------- SESSIONS section

function loadSessions() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || []; }
  catch (_e) { return []; }
}
// Both return true only when the write actually persisted — a full/blocked
// localStorage must surface as a failure, never as a silent success.
function storeSessions(arr) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(arr)); return true; }
  catch (e) {
    showDumpStatus("Couldn't save (browser storage full or blocked): " + e.message
      + ' Try deleting old sessions below, then sync again.');
    return false;
  }
}
function saveSession(s) { const a = loadSessions(); a.unshift(s); return storeSessions(a); } // newest first
function deleteSession(when) {
  const a = loadSessions().filter((s) => s.when !== when);
  storeSessions(a); renderSessions();
}

/** Compute a session's headline numbers once, for reuse across views. */
function sessionSummary(s) {
  const jumps = s.jumps || [];
  const bestM = maxHeightM(jumps);
  const longestAir = jumps.reduce((m, j) => Math.max(m, j.airtime_s || 0), 0);
  const avgM = jumps.length ? jumps.reduce((t, j) => t + (j.height_m || 0), 0) / jumps.length : 0;
  return { jumps, bestM, longestAir, avgM };
}

/** A stat cell showing the preferred unit big and the other unit small. */
function dualStat(label, m) {
  const ft = m * M_TO_FT;
  const val = m > 0
    ? el('div', { class: 'v' },
        unitPref === 'ft' ? fmt(ft, 1) + ' ft' : fmt(m, 2) + ' m',
        el('span', { class: 'sub', text: unitPref === 'ft' ? fmt(m, 2) + ' m' : fmt(ft, 1) + ' ft' }))
    : el('div', { class: 'v', text: '–' });
  return el('div', { class: 'mini-stat' }, val, el('div', { class: 'k', text: label }));
}

/** Build the stats-row + chart that both the session card and the inline
 *  just-synced panel share. */
function sessionBody(s) {
  const { jumps, bestM, longestAir, avgM } = sessionSummary(s);
  const frag = document.createDocumentFragment();
  frag.append(el('div', { class: 'stat-strip' },
    el('div', { class: 'mini-stat' }, el('div', { class: 'v', text: String(jumps.length) }), el('div', { class: 'k', text: 'Jumps' })),
    dualStat('Best', bestM),
    el('div', { class: 'mini-stat' }, el('div', { class: 'v', text: fmt(longestAir, 2) + ' s' }), el('div', { class: 'k', text: 'Longest air' })),
    dualStat('Avg height', avgM),
  ));
  frag.append(buildBarChart(jumps, { vbh: 140, showLabel: true, testid: 'session-chart' }));
  return frag;
}

function renderSessions() {
  const list = $('sessions-list');
  const sessions = loadSessions();
  list.textContent = '';
  $('sessions-empty').hidden = sessions.length > 0;
  renderAlltimeChips(sessions);
  sessions.forEach((s) => {
    const { jumps, bestM } = sessionSummary(s);
    const when = new Date(s.when);
    const card = el('div', { class: 'card session', 'data-testid': 'session-row' });
    card.append(el('div', { class: 'session-head' },
      el('div', {},
        el('div', { class: 'session-date', text: isNaN(when) ? s.when : when.toLocaleString() }),
        // "N jumps" stays a single contiguous text node (a test reads it).
        el('div', { class: 'session-meta muted', text: `${jumps.length} jumps · best ${heightPair(bestM)}` }),
      ),
      el('button', { class: 'btn btn-ghost btn-sm', type: 'button', 'data-testid': 'btn-share',
        onclick: () => shareSession(s) }, 'Share'),
    ));
    card.append(sessionBody(s));
    card.append(el('div', { class: 'session-foot' },
      el('button', { class: 'btn btn-ghost btn-sm', type: 'button',
        onclick: () => downloadText(`jumps-${stamp(s.when)}.csv`, s.jumpsCsv || jumpsToCsv(jumps)) }, 'CSV'),
      s.traceCsv ? el('button', { class: 'btn btn-ghost btn-sm', type: 'button',
        onclick: () => downloadText(`trace-${stamp(s.when)}.csv`, s.traceCsv) }, 'trace.csv') : null,
      el('button', { class: 'btn btn-danger-ghost btn-sm', type: 'button',
        onclick: () => { if (confirm('Delete this saved session?')) deleteSession(s.when); } }, 'Delete'),
    ));
    list.append(card);
  });
}

/** All-time chips across every stored session. */
function renderAlltimeChips(sessions) {
  sessions = sessions || loadSessions();
  let bestM = 0, total = 0;
  for (const s of sessions) {
    const jumps = s.jumps || [];
    total += jumps.length;
    bestM = Math.max(bestM, maxHeightM(jumps));
  }
  setText('chip-alltime-best', 'All-time best: ' + (bestM > 0 ? heightPref(bestM) : '–'));
  setText('chip-total-jumps', 'Total jumps: ' + total);
}

function jumpsToCsv(jumps) {
  const head = 'n,takeoff_s,airtime_raw_s,airtime_s,height_m';
  const rows = jumps.map((j) => [j.n, j.takeoff_s, j.airtime_raw_s, j.airtime_s, j.height_m].join(','));
  return [head, ...rows].join('\n');
}
function stamp(when) { return String(when).replace(/[:.]/g, '-').replace('T', '_').replace('Z', ''); }
function todayStamp() { return new Date().toISOString().slice(0, 10); }

// iOS never honors a page-triggered <a download> the way desktop browsers
// do, and WebBLE wrapper browsers (Bluefy) ignore it entirely — there, the
// share sheet ("Save to Files") is the real file path, clipboard the last
// resort. iPadOS masquerades as MacIntel, hence the maxTouchPoints check.
const IS_IOS = /iP(hone|ad|od)/.test(navigator.userAgent)
  || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

/** Deliver text as a file: share sheet on iOS, real download elsewhere,
 *  clipboard as the spoken last resort. Never a silent no-op. */
async function downloadText(filename, text, mime) {
  const type = mime || 'text/csv';
  if (IS_IOS && navigator.share) {
    try {
      const file = new File([text], filename, { type });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], title: filename });
        return;
      }
    } catch (e) {
      if (e && e.name === 'AbortError') return; // user closed the sheet
    }
  }
  if (!IS_IOS) {
    downloadBlob(filename, new Blob([text], { type }));
    return;
  }
  let copied = false;
  try { await navigator.clipboard.writeText(text); copied = true; } catch (_e) {}
  showDumpStatus(copied
    ? `This browser can't save files, so ${filename} is on your clipboard — paste it into Notes or a spreadsheet. For real downloads, open this page on a computer.`
    : `This browser can't save files. Open this page on a computer to download ${filename}.`);
}
function downloadBlob(filename, blob) {
  const url = URL.createObjectURL(blob);
  const a = el('a', { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function showDumpStatus(text, busy) {
  const s = $('dump-status');
  s.hidden = false;
  s.textContent = text;
  s.classList.toggle('busy', !!busy);
}
function showSyncProgress(text) {
  const s = $('sync-progress');
  s.hidden = false;
  s.classList.add('busy');
  s.textContent = text;
}
function hideSyncProgress() {
  const s = $('sync-progress');
  s.hidden = true;
  s.classList.remove('busy');
  s.textContent = '';
}

// -------------------------------------------------------------------- sync
// "Sync" is the user-facing word; the wire command is still 'dump'.

function syncProgressText() {
  const s = syncState;
  let line;
  if (s.expected > 0) {
    const pct = Math.min(99, Math.floor((s.bytes / s.expected) * 100));
    line = `Syncing… ${pct}%`;
  } else {
    const kb = s.bytes / 1024;
    line = `Syncing… ${kb < 10 ? kb.toFixed(1) : Math.round(kb)} KB received`;
  }
  // BLE-only hint, shown ONLY during the sync (not as permanent copy).
  if (s.kind === 'BLE') line += '\nBluetooth is slow for big sessions — USB is faster.';
  return line;
}

function beginSync() {
  if (!requireDevice()) return;
  if (activeCapture || syncState) return; // a sync is already running
  clearSyncResult();
  showDumpStatus('', false); $('dump-status').hidden = true;
  syncState = { bytes: 0, expected: lastTraceBytes, kind: transportKind };
  switchTab('sessions');
  showSyncProgress(syncProgressText());
  startCapture('dump', onSyncDone, 60000); // generous: BLE trickles slowly
  send('dump');
}

/** Turn a captured 'dump' into a stored session, show it inline, and offer to
 *  clear the device — but only after the save is verified. */
function onSyncDone(lines, err) {
  syncState = null;
  hideSyncProgress();
  if (err) {
    showDumpStatus(err === 'timeout'
      ? 'Sync timed out. Over Bluetooth a big session can take a while — try again, or plug in over USB.'
      : 'Sync failed: ' + err);
    return;
  }
  const files = parseFileSections(lines);
  let jumpsRows = files['jumps.csv'] || [];
  const traceRows = files['trace.csv'] || [];
  const jumpsCsv = jumpsRows.join('\n');
  // First row is the header when any jumps exist; drop it before parsing.
  if (jumpsRows.length && jumpsRows[0].startsWith('n,')) jumpsRows = jumpsRows.slice(1);

  const jumps = [];
  for (const row of jumpsRows) {
    const c = row.split(',');
    if (c.length < 5) continue;
    const height_m = parseFloat(c[4]);
    if (Number.isNaN(height_m)) continue;
    jumps.push({
      n: parseInt(c[0], 10),
      takeoff_s: parseFloat(c[1]),
      airtime_raw_s: parseFloat(c[2]),
      airtime_s: parseFloat(c[3]),
      height_m,
      height_ft: height_m * M_TO_FT,
    });
  }

  // A device with nothing on it must not manufacture an empty session.
  if (!jumps.length) {
    showDumpStatus('Nothing to sync — no jumps on the device yet.');
    return;
  }

  // Re-syncing an uncleared device (the "Keep" path, or a double sync) returns
  // the identical bytes — recognise it instead of duplicating history.
  const traceCsv = traceRows.join('\n');
  const key = contentKey(jumpsCsv, traceCsv);
  const existing = loadSessions().find((s) => s.key === key);
  if (existing) {
    showDumpStatus('Already saved — no new jumps since the last sync.');
    showSyncResult(existing); // the device still holds them: clear/keep still applies
    return;
  }

  const session = { when: new Date().toISOString(), key, jumps, jumpsCsv, traceCsv };
  const saved = saveSession(session);
  if (!saved) return; // storeSessions already showed the failure — don't mask it
  renderSessions();
  showSyncResult(session);
}

/** The inline just-synced panel: the session's own stats + chart, then the
 *  clear-or-keep choice. Clearing is only ever offered here, after a save. */
function showSyncResult(session) {
  lastSynced = session;
  const host = $('sync-result');
  host.textContent = '';
  const { jumps, bestM } = sessionSummary(session);
  const panel = el('div', { class: 'card sync-result' });
  panel.append(el('div', { class: 'synced-head' },
    icon('check', 'ok'),
    el('span', { text: `Saved here — ${jumps.length} jumps, best ${heightPair(bestM)}` }),
  ));
  panel.append(sessionBody(session));

  const choice = el('div', { class: 'after-sync' });
  choice.append(el('p', { text: 'Saved — clear the device for the next session?' }));
  choice.append(el('div', { class: 'btn-row' },
    el('button', { class: 'btn btn-danger', type: 'button', 'data-testid': 'btn-clear-after-sync',
      onclick: () => clearDeviceAfterSync(choice) }, 'Clear device'),
    el('button', { class: 'btn btn-ghost', type: 'button', onclick: () => clearSyncResult() }, 'Keep'),
  ));
  panel.append(choice);
  host.append(panel);
}
function clearSyncResult() { lastSynced = null; const h = $('sync-result'); if (h) h.textContent = ''; }

function clearDeviceAfterSync(choiceNode) {
  send('clear');
  lastStored = { jumps: 0, bestM: 0 };
  renderBanner();
  if (transportKind !== 'Demo') send('stats'); // confirm the wipe
  choiceNode.textContent = '';
  choiceNode.append(el('p', { class: 'muted', text: 'Device cleared — ready for your next session.' }));
}

// ---------------------------------------------------------------- share

/** Draw the session onto a 1200x630 canvas — always beach-light styling,
 *  regardless of the app theme (a share card is read in the sun too). */
function drawShareCanvas(session) {
  const SURFACE = '#fcfcfb', INK = '#0b0b0b', MUTED = '#52514e', ACCENT = '#2a78d6';
  const FONT = '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif';
  const c = el('canvas'); c.width = 1200; c.height = 630;
  const g = c.getContext('2d');
  g.fillStyle = SURFACE; g.fillRect(0, 0, 1200, 630);
  g.textBaseline = 'alphabetic';

  // Wordmark.
  g.fillStyle = MUTED; g.font = `700 30px ${FONT}`;
  g.fillText('J U M P   H E I G H T', 64, 78);

  const { jumps, bestM } = sessionSummary(session);
  const ft = bestM * M_TO_FT;
  const bigVal = unitPref === 'ft' ? fmt(ft, 1) : fmt(bestM, 2);
  const bigUnit = unitPref === 'ft' ? 'ft' : 'm';
  const smallStr = unitPref === 'ft' ? `${fmt(bestM, 2)} m` : `${fmt(ft, 1)} ft`;

  // Huge best height + small other unit.
  g.fillStyle = INK; g.font = `800 180px ${FONT}`;
  g.fillText(bigVal, 60, 290);
  const bvW = g.measureText(bigVal).width;
  g.fillStyle = MUTED; g.font = `800 60px ${FONT}`;
  g.fillText(' ' + bigUnit, 60 + bvW, 290);
  g.font = `700 40px ${FONT}`;
  g.fillText(smallStr, 66, 346);

  // Date + jump count.
  g.fillStyle = INK; g.font = `600 34px ${FONT}`;
  g.fillText(`${jumps.length} jumps · ${longDate(session.when)}`, 64, 408);

  // Bar strip (same rules, no labels).
  drawBarsCanvas(g, 64, 450, 1072, 120, jumps, ACCENT);
  return c;
}

function drawBarsCanvas(g, x0, y0, w, h, jumps, accent) {
  if (!jumps || !jumps.length) return;
  const n = jumps.length;
  const padX = 4;
  const slot = (w - padX * 2) / n;
  const gap = Math.max(3, Math.min(slot * 0.35, 14));
  const bw = Math.max(2, slot - gap);
  const maxH = maxHeightM(jumps) || 1;
  const baseY = y0 + h;
  g.fillStyle = accent;
  jumps.forEach((j, i) => {
    const bh = Math.max(3, (Math.max(0, j.height_m || 0) / maxH) * h);
    const x = x0 + padX + slot * i + (slot - bw) / 2;
    const y = baseY - bh;
    roundTopRect(g, x, y, bw, bh, Math.min(4, bw / 2));
    g.fill();
  });
  // Baseline.
  g.strokeStyle = 'rgba(11,11,11,.34)'; g.lineWidth = 2;
  g.beginPath(); g.moveTo(x0, baseY); g.lineTo(x0 + w, baseY); g.stroke();
}
function roundTopRect(g, x, y, w, h, r) {
  r = Math.max(0, Math.min(r, w / 2, h));
  g.beginPath();
  g.moveTo(x, y + h);
  g.lineTo(x, y + r);
  g.quadraticCurveTo(x, y, x + r, y);
  g.lineTo(x + w - r, y);
  g.quadraticCurveTo(x + w, y, x + w, y + r);
  g.lineTo(x + w, y + h);
  g.closePath();
}

function longDate(when) {
  const d = new Date(when);
  if (isNaN(d)) return String(when);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}
function shortDate(when) {
  const d = new Date(when);
  if (isNaN(d)) return String(when);
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
// --------------------- Bench: phone-only toss test & drop calibration ------
// The device is a logger with a radio: with BLE connected, nothing about
// proving the assembly or calibrating it needs a computer or a cable. Both
// flows just watch the live JUMP stream; calibration saves straight into the
// device's own memory (`set airtime_offset_s …`). Mirrors tools/jump.
let bench = null;
const BENCH_G = 9.80665;

function median(a) {
  const s = [...a].sort((x, y) => x - y);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

function benchStart(mode) {
  const c = $('bench-card');
  c.hidden = false;
  c.textContent = '';
  if (!transport) {
    c.append(el('div', { class: 'status', text: 'Connect the device first (Bluetooth is fine).' }));
    return;
  }
  bench = { mode, good: [], expected: null, heightCm: 0, offset: null, saveRow: null,
            listEl: el('ul', { class: 'feed' }),
            statusEl: el('div', { class: 'status', role: 'status' }),
            hintEl: el('p', { class: 'muted small', hidden: true }) };
  const cancel = el('div', { class: 'btn-row' },
    el('button', { class: 'btn btn-quiet btn-sm', type: 'button', onclick: benchStop }, 'Cancel'));
  if (mode === 'toss') {
    c.append(
      el('p', { class: 'small', text:
        'Unplug any cable — Bluetooth stays connected. Wake the box with a shake, ' +
        'then toss it a foot or more straight up over a cushion, flat like a pizza ' +
        'tray, and let it land. Three clean tosses pass.' }),
      bench.listEl, bench.hintEl, bench.statusEl, cancel);
    bench.statusEl.textContent = 'Waiting for the first toss…';
  } else {
    // A guided three-step flow. The copy carries what the buttons alone
    // couldn't: what this measures, that it's once-per-build, and that the
    // result lives in the device.
    const inches = unitPref === 'ft';
    const input = el('input', { type: 'number',
      value: inches ? '39' : '100',
      min: inches ? '16' : '40', max: inches ? '98' : '250',
      inputmode: 'decimal', class: 'bench-input', 'aria-label': 'Drop height' });
    const unitSel = el('select', { class: 'bench-input bench-unit', 'aria-label': 'Height unit' },
      el('option', { value: 'cm', selected: inches ? null : true }, 'cm'),
      el('option', { value: 'in', selected: inches ? true : null }, 'inches'));
    unitSel.addEventListener('change', () => {
      const toIn = unitSel.value === 'in';
      input.min = toIn ? '16' : '40';
      input.max = toIn ? '98' : '250';
      input.value = toIn ? '39' : '100';
    });
    const startBtn = el('button', { class: 'btn btn-primary btn-sm', type: 'button' }, 'Start drops');
    const setupRow = el('div', { class: 'btn-row' }, input, unitSel, startBtn);
    startBtn.addEventListener('click', () => {
      const v = parseFloat(input.value);
      const cm = unitSel.value === 'in' ? v * 2.54 : v;
      if (!(cm >= 40 && cm <= 250)) {
        bench.statusEl.textContent = unitSel.value === 'in'
          ? 'Height must be 16–98 inches.' : 'Height must be 40–250 cm.';
        return;
      }
      bench.heightCm = cm;
      bench.expected = Math.sqrt(2 * (cm / 100) / BENCH_G);
      const shown = unitSel.value === 'in'
        ? `${v} in (${cm.toFixed(0)} cm)` : `${cm.toFixed(0)} cm`;
      setupRow.replaceWith(el('p', { class: 'small', text:
        `Step 2 of 3 — the drops. Physics says a clean drop from ${shown} ` +
        `free-falls for exactly ${bench.expected.toFixed(3)} s; each drop is ` +
        'scored against that. Unplug any cable, wake the box with a shake, ' +
        `hold it with its BOTTOM exactly at ${shown}, dead still for a ` +
        "second, then let go — don't throw. Let it land. " +
        'Three clean drops minimum; five is better.' }));
      bench.statusEl.textContent = 'Waiting for the first drop…';
    });
    c.append(
      el('p', { class: 'small', text:
        'Step 1 of 3 — measure a height. This teaches the device its own ' +
        'reaction time by comparing it against gravity, which is exact. Pick ' +
        'any height you can measure precisely — tape measure to a table ' +
        "edge works. It's once per build: the result is stored in the " +
        'device and survives reboots and reflashes.' }),
      setupRow, bench.listEl, bench.hintEl, bench.statusEl, cancel);
  }
}

function benchStop() {
  bench = null;
  const c = $('bench-card');
  c.hidden = true;
  c.textContent = '';
}

function benchOnChatter(line) {
  if (line.startsWith('# almost a jump') || line.startsWith('# free-fall seen')) {
    bench.hintEl.hidden = false;
    bench.hintEl.textContent = line.slice(2);
  }
}

function benchOnJump(kv) {
  const raw = parseFloat(kv.airtime_raw_s || '0');
  const h = parseFloat(kv.height_m || '0');
  if (bench.mode === 'toss') {
    const ok = raw >= 0.10 && raw <= 1.2;
    if (ok) bench.good.push(raw);
    bench.listEl.append(el('li', { text: ok
      ? `Toss ${bench.good.length}: ${raw.toFixed(2)} s of air → ${h.toFixed(2)} m`
      : `Ignored a ${raw.toFixed(2)} s flight — not a hand toss` }));
    if (bench.good.length >= 3) {
      bench.statusEl.textContent =
        'PASS — assembly verified: three clean tosses, no cable, no computer.';
      bench = null; // leave the verdict on screen
    } else {
      bench.statusEl.textContent = `${bench.good.length} of 3 — keep going.`;
    }
    return;
  }
  if (bench.expected == null) return; // drop height not chosen yet
  const err = raw - bench.expected;
  if (Math.abs(err) <= 0.15) {
    bench.good.push(raw);
    bench.listEl.append(el('li', { text:
      `Drop ${bench.good.length}: ${raw.toFixed(3)} s (true ${bench.expected.toFixed(3)} s, ` +
      `${err >= 0 ? '+' : ''}${(err * 1000).toFixed(0)} ms)` }));
  } else {
    bench.listEl.append(el('li', { text:
      `Ignored a ${raw.toFixed(2)} s flight — not a clean ${bench.heightCm} cm drop` }));
  }
  if (bench.good.length >= 3) benchOfferSave();
  else bench.statusEl.textContent = `${bench.good.length} of 3 minimum — keep dropping.`;
}

function benchOfferSave() {
  const b = bench;
  const errs = b.good.map((r) => r - b.expected);
  b.offset = Math.round(-median(errs) * 10000) / 10000;
  const biasMs = Math.round(median(errs) * 1000);
  const mean = errs.reduce((s, x) => s + x, 0) / errs.length;
  const sd = Math.sqrt(errs.reduce((s, x) => s + (x - mean) ** 2, 0) / errs.length);
  const meaning = biasMs === 0 ? 'spot on'
    : `announces landings ~${Math.abs(biasMs)} ms ${biasMs > 0 ? 'late' : 'early'}; the correction cancels it`;
  b.statusEl.textContent =
    `Step 3 of 3 — Timing bias measured over ${b.good.length} drops: your device ` +
    `${meaning}.` +
    (sd > 0.03 ? ` Drops are scattered (±${Math.round(sd * 1000)} ms) — stiller, cleaner drops sharpen it.` : '') +
    (deviceInfo.calSource === 'device' && deviceInfo.calOffset != null
      ? (Math.abs(deviceInfo.calOffset - b.offset) <= 0.01
        ? ' This matches the correction already saved — your calibration is confirmed.'
        : ` (Currently saved: ${Math.round(deviceInfo.calOffset * 1000)} ms correction — saving replaces it.)`)
      : '') +
    ' More drops refine the number, or save now.';
  if (!b.saveRow) {
    b.saveRow = el('div', { class: 'btn-row' },
      el('button', { class: 'btn btn-primary btn-sm', type: 'button', 'data-testid': 'btn-bench-save',
        onclick: () => {
          startCapture('set', (_lines, err) => {
            b.statusEl.textContent = !err
              ? 'Saved into the device’s memory — it survives reboots, reflashes, ' +
                'and battery swaps. You’re done: redo this only if the sensor is ' +
                'swapped or detection settings change.'
              : 'The device rejected that — check the console and try again.';
            if (!err) { bench = null; send('info'); } // refresh the CAL readout
          }, 8000);
          send(`set airtime_offset_s ${b.offset}`);
        } }, 'Save to device'));
    b.statusEl.after(b.saveRow);
  }
}

function canvasToBlob(canvas) {
  return new Promise((res) => { try { canvas.toBlob((b) => res(b), 'image/png'); } catch (_e) { res(null); } });
}

async function shareSession(session) {
  const { jumps, bestM } = sessionSummary(session);
  const text = `Best jump ${heightPair(bestM)} — ${jumps.length} jumps · ${shortDate(session.when)}`;
  const title = 'Jump Height';
  let blob = null;
  try { blob = await canvasToBlob(drawShareCanvas(session)); } catch (_e) {}
  const fname = `jump-height-${stamp(session.when)}.png`;
  const file = blob ? new File([blob], fname, { type: 'image/png' }) : null;

  try {
    if (file && navigator.canShare && navigator.canShare({ files: [file] }) && navigator.share) {
      await navigator.share({ files: [file], text, title });
      return;
    }
    if (navigator.share) { await navigator.share({ text, title }); return; }
  } catch (e) {
    if (e && e.name === 'AbortError') return; // user dismissed the share sheet
    // otherwise fall through to the download path
  }

  // No share support: on a computer, save the PNG; on iOS there is nowhere
  // to save INTO (no downloads) — copy the summary and say so honestly.
  if (blob && !IS_IOS) downloadBlob(fname, blob);
  let copied = false;
  try { if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(text); copied = true; } } catch (_e) {}
  showDumpStatus(IS_IOS
    ? `This browser can't open a share sheet${copied ? ' — summary copied to your clipboard' : ''}. Screenshot the session card, or open this page on a computer.`
    : `This browser can't open a share sheet, so I saved the share image to your downloads${copied ? ' and copied the summary to your clipboard' : ''}.`);
}

// ------------------------------------------------------------- backup / restore

function exportAll() {
  const sessions = loadSessions();
  if (!sessions.length) { showDumpStatus('No sessions to back up yet.'); return; }
  const payload = JSON.stringify({ version: 1, sessions }, null, 2);
  downloadText(`jump-height-backup-${todayStamp()}.json`, payload, 'application/json');
  showDumpStatus(`Backed up ${sessions.length} session${sessions.length === 1 ? '' : 's'} to your downloads.`);
}

function importBackup(file) {
  const r = new FileReader();
  r.onload = () => {
    let data;
    try { data = JSON.parse(r.result); }
    catch (e) { showDumpStatus("Couldn't read that backup file — it isn't valid JSON."); return; }
    const incoming = Array.isArray(data) ? data
      : (data && Array.isArray(data.sessions) ? data.sessions : null);
    if (!incoming) { showDumpStatus("That file didn't look like a Jump Height backup."); return; }
    const cur = loadSessions();
    // Dedupe by timestamp AND by content key: two browsers that each synced
    // the same on-device session have different 'when's but identical bytes.
    const seenWhen = new Set(cur.map((s) => s.when));
    const seenKey = new Set(cur.map((s) => s.key).filter(Boolean));
    let added = 0;
    for (const s of incoming) {
      if (!s || !s.when) continue;
      if (seenWhen.has(s.when) || (s.key && seenKey.has(s.key))) continue;
      cur.push(s);
      seenWhen.add(s.when);
      if (s.key) seenKey.add(s.key);
      added++;
    }
    cur.sort((a, b) => new Date(b.when) - new Date(a.when)); // newest first
    if (storeSessions(cur)) {
      renderSessions();
      showDumpStatus(added
        ? `Restored ${added} session${added === 1 ? '' : 's'} from the backup.`
        : 'Nothing new to restore — those sessions were already here.');
    }
  };
  r.readAsText(file);
}

// ------------------------------------------------------------- INSTALL section

async function initInstallTab() {
  const container = $('install-container');
  const note = $('install-note');
  let present = false;
  try {
    // The flasher can only work if the binaries are actually served here.
    const res = await fetch('firmware/firmware.bin', { method: 'HEAD', cache: 'no-store' });
    present = res.ok;
  } catch (_e) { present = false; }

  container.textContent = '';
  if (!present) {
    note.hidden = false;
    note.textContent = '';
    note.append(
      el('div', { class: 'info-line', text: "The firmware binaries aren't published here yet." }),
      el('p', { class: 'muted', text:
        "To build and flash them yourself, run  ./tools/jump flash  — it builds the " +
        "firmware locally and uploads it over USB. Once the project's CI publishes the " +
        "binaries, this button will flash them straight from the browser. (The flashing " +
        "tool itself loads from the internet, so you'll need to be online either way.)" }),
    );
    return;
  }

  // Binaries are present: mount the ESP Web Tools button with styled slots.
  const btn = document.createElement('esp-web-install-button');
  btn.setAttribute('manifest', 'manifest.json');
  btn.append(
    el('button', { class: 'btn btn-primary', slot: 'activate', type: 'button' }, 'Install / Update firmware'),
    el('span', { slot: 'unsupported', class: 'note' }, "This browser can't flash over USB — use Chrome or Edge on a desktop computer."),
    el('span', { slot: 'not-allowed', class: 'note' }, 'Flashing needs a secure (https) page.'),
  );
  container.append(btn);

  // The custom element only exists once the CDN module has loaded.
  setTimeout(() => {
    if (!customElements.get('esp-web-install-button')) {
      note.hidden = false;
      note.textContent = "Couldn't load the in-browser flasher — it comes from the internet, so check your connection and reload.";
    }
  }, 2500);
}

// ------------------------------------------------------------- console drawer

function appendConsole(text, dir) {
  const log = $('console-log');
  if (!log) return;
  const mark = dir === 'tx' ? '› ' : dir === 'err' ? '! ' : '';
  log.append(el('div', { class: 'cl cl-' + dir, text: mark + text }));
  while (log.childNodes.length > 500) log.removeChild(log.firstChild);
  log.scrollTop = log.scrollHeight;
}

function initConsole() {
  const body = $('console-body');
  const toggle = $('btn-console-toggle');
  toggle.addEventListener('click', () => {
    const open = body.hidden;
    body.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    $('console-caret').classList.toggle('is-open', open);
  });
  $('console-form').addEventListener('submit', (e) => {
    e.preventDefault();
    const v = $('console-input').value.trim();
    if (!v) return;
    if (!requireDevice()) return;
    send(v);
    $('console-input').value = '';
  });
}

// --------------------------------------------------------------- connection

function setStatus(state, kind) {
  const pill = $('conn-status');
  pill.className = 'pill ' + (state === 'connected' ? 'pill-on' : state === 'connecting' ? 'pill-wait' : 'pill-off');
  pill.textContent = state === 'connected'
    ? (kind === 'Demo' ? 'Demo mode' : kind + ' connected')
    : state === 'connecting' ? 'Connecting…' : 'Disconnected';
  $('btn-disconnect').hidden = state !== 'connected';
}

/** Show/hide the cross-tab sync banner from the last STATS + connection state. */
function renderBanner() {
  const b = $('sync-banner');
  if (!b) return;
  const show = !!transport && lastStored.jumps > 0;
  b.hidden = !show;
  const tabSync = $('btn-download-session');
  if (tabSync) tabSync.hidden = show;  // the banner owns Sync while it's up
  if (!show) return;
  setText('sync-banner-count', `${lastStored.jumps} jumps on the device`);
  setText('sync-banner-best', lastStored.bestM > 0 ? `best ${heightPref(lastStored.bestM)}` : '');
}

function setTransport(t, kind) {
  transport = t;
  transportKind = kind;
  t.onLine(handleLine);
  t.onClose(() => onTransportClosed(t));
  resetLiveSession();
  // Never carry one device's numbers into another connection: the banner and
  // the sync progress denominator must start unknown until THIS device reports.
  lastStored = { jumps: 0, bestM: 0 };
  lastTraceBytes = NaN;
  renderBanner();
  setStatus('connected', kind);
  acquireWakeLock(); // keep the screen awake while riding (feature-detected)
  // Pull current info + stats so the UI isn't blank on connect, and land the
  // user on Live — that's what connecting is FOR. (Demo/mock stays put so the
  // test suite starts from a known tab.)
  if (kind !== 'Demo') { send('info'); send('stats'); switchTab('live'); }
}

function onTransportClosed(t) {
  if (t && t !== transport) return; // a stale/older transport closing — ignore
  transport = null;
  transportKind = null;
  releaseWakeLock();
  // Abort any in-flight capture: without this a sync interrupted by the
  // disconnect leaves the button dead (guarded by activeCapture) and a stale
  // progress line up, and after a reconnect the old capture would keep
  // swallowing lines with its timer re-arming forever.
  if (activeCapture) {
    clearTimeout(activeCapture.timer);
    activeCapture = null;
    syncState = null;
    hideSyncProgress();
    showDumpStatus('Sync interrupted — the device disconnected. Reconnect and try again.');
  }
  // A bench flow cut off mid-run must say so, not sit waiting forever.
  if (bench) {
    bench.statusEl.textContent = 'Device disconnected — reconnect and start the flow again.';
    bench = null;
  }
  // A self-test cut off mid-run must not sit on "Running…" forever.
  if (selftest.active) {
    selftest.active = false;
    selftest.result = 'INTERRUPTED';
    renderSelftest();
  }
  renderBanner();
  setStatus('off');
  appendConsole('device disconnected', 'err');
}

async function doDisconnect() {
  const t = transport;
  if (t) { try { await t.disconnect(); } catch (_e) {} }
  onTransportClosed(t);
}

async function connectBle() {
  if (!navigator.bluetooth) return;
  setStatus('connecting');
  let device;
  try {
    // Match the device by name OR by advertising the NUS service (two filter
    // objects = OR). optionalServices lets us reach NUS after a name match.
    device = await navigator.bluetooth.requestDevice({
      // namePrefix, not name: pucks advertise "JumpHeight-XXXX" (unique per
      // board) since 2026-08-18. The service filter already matched any name;
      // the prefix keeps the name path working for old and new firmware both.
      filters: [{ namePrefix: DEVICE_NAME }, { services: [NUS_SERVICE] }],
      optionalServices: [NUS_SERVICE],
    });
  } catch (_e) { setStatus(transport ? 'connected' : 'off', transportKind); return; } // user cancelled
  const t = new BleTransport(device);
  try { await t.open(); }
  catch (e) { setStatus('off'); showConnectMsg('Bluetooth connection failed: ' + e.message, 'warn'); return; }
  setTransport(t, 'BLE');
}

async function connectUsb() {
  if (!navigator.serial) return;
  setStatus('connecting');
  let port;
  try { port = await navigator.serial.requestPort(); }
  catch (_e) { setStatus(transport ? 'connected' : 'off', transportKind); return; } // user cancelled
  const t = new SerialTransport(port);
  try { await t.open(); }
  catch (e) { setStatus('off'); showConnectMsg('USB connection failed: ' + e.message, 'warn'); return; }
  setTransport(t, 'USB');
}

function showConnectMsg(text, kind) {
  const m = $('connect-msg');
  m.hidden = false;
  m.textContent = text;
  m.className = 'status' + (kind ? ' ' + kind : '');
}

/** Guard actions that need a live link; nudges the user to the Connect tab. */
function requireDevice() {
  if (transport) return true;
  switchTab('connect');
  showConnectMsg('Connect a device first — Bluetooth or USB.', 'warn');
  return false;
}

// ------------------------------------------------------------- wake lock

async function acquireWakeLock() {
  try {
    if ('wakeLock' in navigator && document.visibilityState === 'visible' && !wakeLock) {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener && wakeLock.addEventListener('release', () => { wakeLock = null; });
    }
  } catch (_e) { /* denied or unsupported — silently do without */ }
}
async function releaseWakeLock() {
  try { if (wakeLock) await wakeLock.release(); } catch (_e) {}
  wakeLock = null;
}

// --------------------------------------------------------------------- tabs

function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach((b) => {
    const on = b.dataset.tab === name;
    b.classList.toggle('is-active', on);
    b.setAttribute('aria-selected', String(on));
  });
  document.querySelectorAll('.tab-panel').forEach((p) => p.classList.toggle('is-active', p.id === 'tab-' + name));
  // Opening Live quietly refreshes the numbers (replaces the old
  // "Refresh stats" button — the user should never have to ask for stats).
  if (name === 'live' && transport && transportKind !== 'Demo' && !activeCapture) {
    try { send('stats'); } catch (_e) { /* connection raced away — harmless */ }
  }
}

// -------------------------------------------------------------- theme + units

function prefersDark() {
  return !!(window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches);
}
function applyTheme() {
  const dark = themeMode === 'dark';
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  const ico = $('theme-ico');
  if (ico) {
    // The icon shows what a tap GIVES you, not what you have.
    ico.textContent = '';
    ico.append(icon(dark ? 'sun' : 'moon'));
  }
  const btn = $('btn-theme');
  if (btn) btn.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
}
function cycleTheme() {
  themeMode = themeMode === 'dark' ? 'light' : 'dark';
  try { localStorage.setItem(THEME_KEY, themeMode); } catch (_e) {}
  applyTheme();
}

function applyUnit() {
  document.documentElement.setAttribute('data-unit', unitPref);
  setText('btn-unit', unitPref === 'ft' ? 'Show meters' : 'Show feet');
}
function toggleUnit() {
  unitPref = unitPref === 'ft' ? 'm' : 'ft';
  try { localStorage.setItem(UNIT_KEY, unitPref); } catch (_e) {}
  applyUnit();
  // Everything that prints the preferred unit needs a refresh (the big hero /
  // tile numbers are pure CSS and don't).
  renderBanner();
  renderSessions();
  renderLiveMini();
  if (lastSynced) showSyncResult(lastSynced);
}

function initThemeUnit() {
  // Before the first explicit tap we follow the system; a tap makes the
  // choice explicit and remembered. Two states — dark mode is ONE tap away.
  let stored = null;
  try { stored = localStorage.getItem(THEME_KEY); } catch (_e) { /* blocked storage */ }
  if (stored === 'light' || stored === 'dark') themeMode = stored;
  else themeMode = prefersDark() ? 'dark' : 'light';
  try { unitPref = localStorage.getItem(UNIT_KEY) || 'ft'; } catch (_e) { unitPref = 'ft'; }
  if (!['ft', 'm'].includes(unitPref)) unitPref = 'ft';
  applyTheme();
  applyUnit();
  $('btn-theme').addEventListener('click', cycleTheme);
  $('btn-unit').addEventListener('click', toggleUnit);
}

// --------------------------------------------------------------------- init

function initConnectTab() {
  const notes = [];
  if (!window.isSecureContext) {
    notes.push('This page must be opened over https or from localhost for Bluetooth and USB to work.');
  }
  // One message channel, one message: two stacked near-duplicate paragraphs
  // read as a malfunction, not help.
  const noBle = !navigator.bluetooth;
  const noUsb = !navigator.serial;
  if (noBle) $('btn-connect-ble').disabled = true;
  if (noUsb) $('btn-connect-usb').disabled = true;
  if (noBle && noUsb) notes.push(BOTH_UNSUPPORTED);
  else if (noBle) notes.push(BLE_UNSUPPORTED);
  else if (noUsb) notes.push(SERIAL_UNSUPPORTED);
  const help = $('connect-help');
  help.textContent = '';
  if (notes.length) help.append(el('p', { class: 'note', text: notes.join(' ') }));

  $('btn-connect-ble').addEventListener('click', connectBle);
  $('btn-connect-usb').addEventListener('click', connectUsb);
  $('btn-selftest').addEventListener('click', () => { if (requireDevice()) send('selftest'); });
  // Two-tap confirm for power-off: a sealed puck wakes only by opening the
  // case (USB/reset), so one stray beach-tap must not kill the session.
  // The armed state self-clears after 4 s of hesitation.
  let offArmedUntil = 0;
  $('btn-power-off').addEventListener('click', () => {
    if (!requireDevice()) return;
    const btn = $('btn-power-off');
    const now = Date.now();
    if (now > offArmedUntil) {
      offArmedUntil = now + 4000;
      btn.textContent = 'Tap again to power off';
      setTimeout(() => {
        if (Date.now() > offArmedUntil) btn.textContent = 'Power off puck';
      }, 4200);
      return;
    }
    offArmedUntil = 0;
    btn.textContent = 'Power off puck';
    send('off');
    // The device farewells with "OK off" and goes silent; the BLE link then
    // drops and the normal disconnect path takes the UI back to square one.
    // No capture needed — the farewell lines land in the device console.
  });
  $('btn-bench-toss').addEventListener('click', () => benchStart('toss'));
  $('btn-bench-drop').addEventListener('click', () => benchStart('drop'));
}

/** Wire up the demo transport so the Playwright test can drive the whole app. */
function setupMock() {
  const t = new MockTransport();
  setTransport(t, 'Demo');
  // Deliberately tiny and stable: feed(line) injects; sent is the live array.
  window.__mock = { feed: (line) => t.receive(line), sent: t.sent };
}


// ------------------------------------------------------ field labelling tab
//
// One-tap timestamped labels, exported in EXACTLY the notes format
// tools/label.py parses (see its docstring):
//     HH:MM:SS jump  <note>
//     HH:MM:SS-HH:MM:SS none <note>
// Entirely client-side: localStorage, no transport, works offline once the
// page is loaded — labelling must not depend on being in BLE range or on the
// laptop being reachable, because the whole use case is a pocket, outdoors.
//
// Deliberate limits (docs/bench-program.md §4): tap precision serves `none`
// regions and jump counts; per-jump accuracy timing comes from video. The
// export is the bridge to `python3 tools/label.py <session> notes.txt`.

const LABELS_KEY = 'jh-labels-v1';

function labelsLoad() {
  try { return JSON.parse(localStorage.getItem(LABELS_KEY) || '[]'); }
  catch (_e) { return []; }
}
function labelsSave(rows) { localStorage.setItem(LABELS_KEY, JSON.stringify(rows)); }

function clockOf(ms) {
  const d = new Date(ms);
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

// A row is {t, t2, kind, note}: t/t2 epoch-ms (t2 null for instants).
function labelLine(r) {
  // Free-text notes export as comment LINES (leading '#'): label.py strips
  // everything after a '#' and silently ignores empty results, whereas an
  // unknown kind in the kind column would be misread as a `none` region.
  if (r.kind === '#') return `# ${clockOf(r.t)}  ${r.note}`;
  const when = r.t2 ? `${clockOf(r.t)}-${clockOf(r.t2)}` : clockOf(r.t);
  return `${when}  ${r.kind}${r.note ? '  ' + r.note : ''}`;
}
function labelsText() {
  const rows = labelsLoad();
  const day = rows.length ? new Date(rows[0].t).toDateString() : '';
  return `# labels captured by the web app — ${day}\n` +
         rows.map(labelLine).join('\n') + (rows.length ? '\n' : '');
}

let quietStartMs = null;  // open `none` range, if any

function labelRender() {
  const list = $('label-list');
  if (!list) return;
  const rows = labelsLoad();
  list.innerHTML = '';
  rows.forEach((r, i) => {
    const li = document.createElement('li');
    const txt = document.createElement('span');
    txt.textContent = labelLine(r);
    const del = document.createElement('button');
    del.className = 'del'; del.type = 'button'; del.textContent = '✕';
    del.setAttribute('aria-label', 'delete this label');
    del.addEventListener('click', () => {
      const rs = labelsLoad(); rs.splice(i, 1); labelsSave(rs); labelRender();
    });
    li.append(txt, del);
    list.appendChild(li);
  });
  const pill = $('label-count');
  if (pill) {
    pill.hidden = rows.length === 0 && quietStartMs === null;
    const open = quietStartMs !== null ? ' · quiet OPEN' : '';
    pill.textContent = `${rows.length}${open}`;
  }
  const qbtn = $('btn-label-quiet');
  if (qbtn) qbtn.textContent = quietStartMs === null ? 'Start quiet period' : 'End quiet period';
}

function labelAdd(kind, note, t2) {
  const rows = labelsLoad();
  rows.push({ t: Date.now(), t2: t2 || null, kind, note: note || '' });
  // Ranges are stored at the moment they CLOSE, so re-sort by start time to
  // keep the export chronological — label.py doesn't require order, humans
  // reading the file do.
  rows.sort((a, b) => a.t - b.t);
  labelsSave(rows); labelRender();
}

function initLabelTab() {
  if (!$('btn-label-jump')) return;  // markup absent (old cached page) — degrade silently
  $('btn-label-jump').addEventListener('click', () => {
    labelAdd('jump', $('label-note-text').value.trim());
    $('label-note-text').value = '';
  });
  $('btn-label-quiet').addEventListener('click', () => {
    if (quietStartMs === null) { quietStartMs = Date.now(); labelRender(); return; }
    const start = quietStartMs; quietStartMs = null;
    const rows = labelsLoad();
    rows.push({ t: start, t2: Date.now(), kind: 'none',
                note: $('label-note-text').value.trim() });
    rows.sort((a, b) => a.t - b.t);
    labelsSave(rows);
    $('label-note-text').value = '';
    labelRender();
  });
  $('btn-label-note').addEventListener('click', () => {
    const note = $('label-note-text').value.trim();
    if (!note) return;
    // A bare note is a comment line for the humans; label.py ignores it.
    labelAdd('#', note);
    $('label-note-text').value = '';
  });
  $('btn-label-export').addEventListener('click', () => {
    const blob = new Blob([labelsText()], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `notes-${new Date().toISOString().slice(0, 10)}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
  });
  $('btn-label-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText(labelsText()); } catch (_e) {}
  });
  $('btn-label-clear').addEventListener('click', () => {
    if (!confirm('Delete all captured labels?')) return;
    labelsSave([]); quietStartMs = null; labelRender();
  });
  labelRender();
}

function init() {
  initThemeUnit();
  document.querySelectorAll('.tab-btn').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));
  initConnectTab();
  initLabelTab();
  $('btn-disconnect').addEventListener('click', doDisconnect);
  // No refresh button: stats arrive on connect and with every jump, and
  // switching to Live quietly re-asks — the user never has to think about it.

  // Sync is one word, one tap — from the banner or the Sessions tab.
  $('btn-sync').addEventListener('click', beginSync);
  $('btn-download-session').addEventListener('click', beginSync);

  // Backup / restore.
  $('btn-export-all').addEventListener('click', exportAll);
  $('import-file').addEventListener('change', (e) => {
    const f = e.target.files && e.target.files[0];
    if (f) importBackup(f);
    e.target.value = ''; // let the same file be re-imported later
  });

  // Manual clear lives quietly here for the rare hands-on case.
  $('btn-clear-device').addEventListener('click', () => {
    if (!requireDevice()) return;
    if (!confirm('This erases every jump and the trace stored on the device. It cannot be undone. Continue?')) return;
    send('clear');
    lastStored = { jumps: 0, bestM: 0 };
    renderBanner();
    showDumpStatus('Sent “clear” — the device is wiping its stored data.');
  });

  initConsole();
  initInstallTab();
  renderSessions();

  // Ask the browser to keep our stored sessions around (best-effort, silent).
  try { if (navigator.storage && navigator.storage.persist) navigator.storage.persist(); } catch (_e) {}

  // Re-acquire the wake lock when the tab comes back to the foreground.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && transport && !wakeLock) acquireWakeLock();
  });

  if (location.hash === '#mock') setupMock();
}

// Module scripts are deferred, so the DOM is ready — but guard just in case.
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
