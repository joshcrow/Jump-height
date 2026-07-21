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

// Same glyphs the CLI's render_selftest uses, so the two UIs read alike.
const MARKS = { PASS: '✅', WARN: '⚠️', FAIL: '❌', SKIP: '—' };

const BLE_UNSUPPORTED =
  "This browser can't use Bluetooth. Use Chrome or Edge on a computer or " +
  "Android phone. On an iPhone or iPad, install the free “Bluefy” app " +
  "from the App Store and open this page inside it.";
const SERIAL_UNSUPPORTED =
  "This browser can't use USB. Use Chrome or Edge on a desktop computer (or " +
  "Android with an OTG adapter). iPhones and iPads can't do USB in the browser " +
  "— connect over Bluetooth instead.";

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
let themeMode = 'light';   // 'auto' | 'light' | 'dark'
const liveJumps = [];      // per-jump data for this session's live mini-chart
let lastStored = { jumps: 0, bestM: 0 };  // last STATS stored_* seen (for the banner)
let lastTraceBytes = NaN;  // optional STATS trace_bytes, for a real sync %
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
/** Both units, preferred first: '5.9 ft (1.79 m)'. */
function heightPair(m) {
  const ft = m * M_TO_FT;
  return unitPref === 'ft'
    ? `${fmt(ft, 1)} ft (${fmt(m, 2)} m)`
    : `${fmt(m, 2)} m (${fmt(ft, 1)} ft)`;
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
    // Chatter. The only chatter the UI acts on is a self-test hint.
    if (selftest.active && line.startsWith('# hint:')) addSelftestHint(line.slice(7).trim());
    return;
  }
  const kv = parseKV(line);
  switch (kv._tag) {
    case 'JUMP':     onJump(kv); break;
    case 'STATS':    onStats(kv); break;
    case 'STATE':    onState(kv); break;
    case 'INFO':     onInfo(kv); break;
    case 'PARAMS':   onParams(kv); break;
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

function onStats(kv) {
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
  deviceInfo.fw = kv.fw; deviceInfo.sample_hz = kv.sample_hz; deviceInfo.ble = kv.ble;
  renderDeviceInfo();
}
function onParams(kv) {
  deviceInfo.params = Object.entries(kv)
    .filter(([k]) => k !== '_tag' && k !== '_args')
    .map(([k, v]) => `${k}=${v}`).join('  ');
  renderDeviceInfo();
}
function renderDeviceInfo() {
  const c = $('device-info');
  c.hidden = false; c.textContent = '';
  const bits = [];
  if (deviceInfo.fw) bits.push('Firmware v' + deviceInfo.fw);
  if (deviceInfo.sample_hz) bits.push(deviceInfo.sample_hz + ' Hz sampling');
  if (deviceInfo.ble != null) bits.push('Bluetooth: ' + (deviceInfo.ble === '1' ? 'yes' : 'no'));
  c.append(el('div', { class: 'info-line', text: bits.join('  ·  ') || 'Device connected' }));
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
    table.append(el('div', { class: 'st-row' },
      el('span', { class: 'st-mark', text: MARKS[row.status] || '?' }),
      el('span', { class: 'st-name', text: row.name }),
      el('span', { class: 'st-detail muted', text: row.detail }),
    ));
    for (const h of row.hints) table.append(el('div', { class: 'st-hint muted', text: h }));
  }
  c.append(table);
  if (selftest.result) {
    const ok = selftest.result === 'PASS';
    c.append(el('div', { class: 'st-result ' + (ok ? 'ok' : 'bad'), text: `Result: ${selftest.result} ${ok ? '✅' : '❌'}` }));
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
        onclick: () => downloadText(`jumps-${stamp(s.when)}.csv`, s.jumpsCsv || jumpsToCsv(jumps)) }, 'jumps.csv'),
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

/** Download text as a file via a Blob + a temporary <a download>. */
function downloadText(filename, text, mime) {
  downloadBlob(filename, new Blob([text], { type: mime || 'text/csv' }));
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

  const session = { when: new Date().toISOString(), jumps, jumpsCsv, traceCsv: traceRows.join('\n') };
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
    el('span', { class: 'ok-dot', text: '✓' }),
    el('span', { text: `Saved here — ${jumps.length} jumps, best ${heightPair(bestM)}` }),
  ));
  panel.append(sessionBody(session));

  const choice = el('div', { class: 'after-sync' });
  choice.append(el('p', { text: 'Saved here ✓ — clear the device for the next session?' }));
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
  choiceNode.append(el('p', { class: 'muted', text: 'Device cleared ✓ — ready for your next session.' }));
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

  // No share support: download the PNG, copy the text, and SAY so.
  if (blob) downloadBlob(fname, blob);
  let copied = false;
  try { if (navigator.clipboard && navigator.clipboard.writeText) { await navigator.clipboard.writeText(text); copied = true; } } catch (_e) {}
  showDumpStatus(`This browser can't open a share sheet, so I saved the share image to your downloads${copied ? ' and copied the summary to your clipboard' : ''}.`);
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
    const seen = new Set(cur.map((s) => s.when));
    let added = 0;
    for (const s of incoming) {
      if (s && s.when && !seen.has(s.when)) { cur.push(s); seen.add(s.when); added++; }
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
    $('console-caret').textContent = open ? '▾' : '▸';
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
  setStatus('connected', kind);
  acquireWakeLock(); // keep the screen awake while riding (feature-detected)
  // Pull current info + stats so the UI isn't blank on connect. (In demo mode
  // there's no device to answer, and we keep sent[] clean for the test.)
  if (kind !== 'Demo') { send('info'); send('stats'); }
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
      filters: [{ name: DEVICE_NAME }, { services: [NUS_SERVICE] }],
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
  const dark = themeMode === 'dark' || (themeMode === 'auto' && prefersDark());
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  const label = themeMode.charAt(0).toUpperCase() + themeMode.slice(1);
  setText('theme-label', label);
  const ico = $('theme-ico');
  if (ico) ico.textContent = themeMode === 'auto' ? '🌗' : themeMode === 'dark' ? '🌙' : '☀️';
  const btn = $('btn-theme');
  if (btn) btn.setAttribute('aria-label', `Theme: ${label}. Tap to change.`);
}
function cycleTheme() {
  themeMode = themeMode === 'auto' ? 'light' : themeMode === 'light' ? 'dark' : 'auto';
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
  try { themeMode = localStorage.getItem(THEME_KEY) || 'light'; } catch (_e) { themeMode = 'light'; }
  try { unitPref = localStorage.getItem(UNIT_KEY) || 'ft'; } catch (_e) { unitPref = 'ft'; }
  if (!['auto', 'light', 'dark'].includes(themeMode)) themeMode = 'light';
  if (!['ft', 'm'].includes(unitPref)) unitPref = 'ft';
  applyTheme();
  applyUnit();
  $('btn-theme').addEventListener('click', cycleTheme);
  $('btn-unit').addEventListener('click', toggleUnit);
  // Follow the OS when in Auto.
  if (window.matchMedia) {
    const mq = matchMedia('(prefers-color-scheme: dark)');
    const onChange = () => { if (themeMode === 'auto') applyTheme(); };
    if (mq.addEventListener) mq.addEventListener('change', onChange);
    else if (mq.addListener) mq.addListener(onChange);
  }
}

// --------------------------------------------------------------------- init

function initConnectTab() {
  const notes = [];
  if (!window.isSecureContext) {
    notes.push('This page must be opened over https or from localhost for Bluetooth and USB to work.');
  }
  if (!navigator.bluetooth) { $('btn-connect-ble').disabled = true; notes.push(BLE_UNSUPPORTED); }
  if (!navigator.serial) { $('btn-connect-usb').disabled = true; notes.push(SERIAL_UNSUPPORTED); }
  const help = $('connect-help');
  help.textContent = '';
  for (const n of notes) help.append(el('div', { class: 'note', text: n }));

  $('btn-connect-ble').addEventListener('click', connectBle);
  $('btn-connect-usb').addEventListener('click', connectUsb);
  $('btn-selftest').addEventListener('click', () => { if (requireDevice()) send('selftest'); });
}

/** Wire up the demo transport so the Playwright test can drive the whole app. */
function setupMock() {
  const t = new MockTransport();
  setTransport(t, 'Demo');
  // Deliberately tiny and stable: feed(line) injects; sent is the live array.
  window.__mock = { feed: (line) => t.receive(line), sent: t.sent };
}

function init() {
  initThemeUnit();
  document.querySelectorAll('.tab-btn').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));
  initConnectTab();
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
