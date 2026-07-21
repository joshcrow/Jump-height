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
*/

// ------------------------------------------------------------------ protocol

// Nordic UART Service — the Phase-3 BLE contract, shared by all three agents.
const NUS_SERVICE = '6e400001-b5a3-f393-e0a9-e50e24dcca9e';
const NUS_RX      = '6e400002-b5a3-f393-e0a9-e50e24dcca9e'; // client -> device (writes)
const NUS_TX      = '6e400003-b5a3-f393-e0a9-e50e24dcca9e'; // device -> client (notifies)
const DEVICE_NAME = 'JumpHeight';

const BAUD = 115200;
const STORAGE_KEY = 'jh_sessions';

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
let activeCapture = null;  // in-flight command capture (used by 'dump')

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

// ------------------------------------------------------------- line handling

/** Every incoming line goes through here, whatever the link. */
function handleLine(line) {
  if (line == null || line.trim() === '') return;
  appendConsole(line, 'rx');
  feedCapture(line);  // command capture (dump) — independent of the live UI
  dispatch(line);     // live UI updates
}

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
  $('live-empty').hidden = true;
}

function onStats(kv) {
  const sj = parseInt(kv.session_jumps, 10);
  const sb = pf(kv.session_best_m);
  if (!Number.isNaN(sj)) live.count = sj;
  if (!Number.isNaN(sb)) live.bestM = sb;
  renderLiveStats();
  if (kv.stored_jumps != null) {
    setText('live-stored', `On device: ${kv.stored_jumps} jumps, best ${fmt(pf(kv.stored_best_m), 2)} m`);
  }
}

function onState(kv) {
  const rec = kv._args[0] === 'recording';
  const st = $('live-state');
  st.hidden = false;
  st.textContent = rec ? '● recording' : 'idle';
  st.className = 'badge ' + (rec ? 'badge-rec' : 'badge-idle');
}

function renderLiveStats() {
  setText('live-best-m', live.bestM ? fmt(live.bestM, 2) + ' m' : '–');
  setText('live-best-ft', live.bestM ? fmt(live.bestM * 3.28084, 1) + ' ft' : '');
  setText('live-count', String(live.count || 0));
}

function addJumpToFeed(n, hm, hft, at) {
  const feed = $('jump-feed');
  const li = el('li', { class: 'feed-item' },
    el('span', { class: 'feed-n', text: '#' + (Number.isNaN(n) ? '?' : n) }),
    el('span', { class: 'feed-h', text: `${fmt(hm, 2)} m` }),
    el('span', { class: 'feed-ft muted', text: `${fmt(hft, 1)} ft` }),
    el('span', { class: 'feed-at muted', text: `${fmt(at, 2)} s` }),
  );
  feed.insertBefore(li, feed.firstChild); // newest on top
  while (feed.childNodes.length > 100) feed.removeChild(feed.lastChild);
}

function resetLiveSession() {
  live.count = 0; live.bestM = 0;
  ['live-height-m', 'live-height-ft', 'live-airtime'].forEach((id) => setText(id, '–'));
  setText('live-best-m', '–'); setText('live-best-ft', ''); setText('live-count', '0'); setText('live-stored', '');
  $('jump-feed').textContent = '';
  $('live-empty').hidden = false;
  $('live-state').hidden = true;
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
      + ' Try deleting old sessions below, then download again.');
    return false;
  }
}
function saveSession(s) { const a = loadSessions(); a.unshift(s); return storeSessions(a); } // newest first
function deleteSession(idx) { const a = loadSessions(); a.splice(idx, 1); storeSessions(a); renderSessions(); }

/** Turn a captured 'dump' into a stored session and render it. */
function onDumpDone(lines, err) {
  if (err) {
    showDumpStatus(err === 'timeout'
      ? "Download timed out. Over Bluetooth this can happen on a big session — try again, or plug in over USB."
      : 'Download failed: ' + err);
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
      height_ft: height_m * 3.28084,
    });
  }

  const saved = saveSession({ when: new Date().toISOString(), jumps, jumpsCsv,
                              traceCsv: traceRows.join('\n') });
  if (!saved) return;  // storeSessions already showed the failure — don't mask it
  renderSessions();
  const best = jumps.reduce((m, j) => Math.max(m, j.height_m || 0), 0);
  showDumpStatus(`Saved ${jumps.length} jumps${jumps.length ? ` — best ${best.toFixed(2)} m (${(best * 3.28084).toFixed(1)} ft)` : ''}.`);
}

function renderSessions() {
  const list = $('sessions-list');
  const sessions = loadSessions();
  list.textContent = '';
  $('sessions-empty').hidden = sessions.length > 0;
  sessions.forEach((s, idx) => {
    const jumps = s.jumps || [];
    const best = jumps.reduce((m, j) => Math.max(m, j.height_m || 0), 0);
    const when = new Date(s.when);
    list.append(el('div', { class: 'card session', 'data-testid': 'session-row' },
      el('div', {},
        el('div', { class: 'session-date', text: isNaN(when) ? s.when : when.toLocaleString() }),
        el('div', { class: 'session-meta muted', text: `${jumps.length} jumps · best ${best.toFixed(2)} m (${(best * 3.28084).toFixed(1)} ft)` }),
      ),
      el('div', { class: 'btn-row wrap' },
        el('button', { class: 'btn btn-ghost', type: 'button',
          onclick: () => downloadText(`jumps-${stamp(s.when)}.csv`, s.jumpsCsv || jumpsToCsv(jumps)) }, 'jumps.csv'),
        s.traceCsv ? el('button', { class: 'btn btn-ghost', type: 'button',
          onclick: () => downloadText(`trace-${stamp(s.when)}.csv`, s.traceCsv) }, 'trace.csv') : null,
        el('button', { class: 'btn btn-danger-ghost', type: 'button',
          onclick: () => { if (confirm('Delete this saved session?')) deleteSession(idx); } }, 'Delete'),
      ),
    ));
  });
}

function jumpsToCsv(jumps) {
  const head = 'n,takeoff_s,airtime_raw_s,airtime_s,height_m';
  const rows = jumps.map((j) => [j.n, j.takeoff_s, j.airtime_raw_s, j.airtime_s, j.height_m].join(','));
  return [head, ...rows].join('\n');
}
function stamp(when) { return String(when).replace(/[:.]/g, '-').replace('T', '_').replace('Z', ''); }

/** Download text as a file via a Blob + a temporary <a download>. */
function downloadText(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/csv' }));
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

function setTransport(t, kind) {
  transport = t;
  transportKind = kind;
  t.onLine(handleLine);
  t.onClose(() => onTransportClosed(t));
  resetLiveSession();
  setStatus('connected', kind);
  // Pull current info + stats so the UI isn't blank on connect. (In demo mode
  // there's no device to answer, and we keep sent[] clean for the test.)
  if (kind !== 'Demo') { send('info'); send('stats'); }
}

function onTransportClosed(t) {
  if (t && t !== transport) return; // a stale/older transport closing — ignore
  transport = null;
  transportKind = null;
  // Abort any in-flight capture: without this a dump interrupted by the
  // disconnect leaves the Download button dead (guarded by activeCapture) and
  // a stale "Downloading…" spinner up, and after a reconnect the old capture
  // would keep swallowing lines with its timer re-arming forever.
  if (activeCapture) {
    clearTimeout(activeCapture.timer);
    activeCapture = null;
    showDumpStatus('Download interrupted — the device disconnected. Reconnect and try again.');
  }
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

// --------------------------------------------------------------------- tabs

function switchTab(name) {
  document.querySelectorAll('.tab-btn').forEach((b) => b.classList.toggle('is-active', b.dataset.tab === name));
  document.querySelectorAll('.tab-panel').forEach((p) => p.classList.toggle('is-active', p.id === 'tab-' + name));
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
  document.querySelectorAll('.tab-btn').forEach((b) => b.addEventListener('click', () => switchTab(b.dataset.tab)));
  initConnectTab();
  $('btn-disconnect').addEventListener('click', doDisconnect);
  $('btn-refresh-stats').addEventListener('click', () => { if (requireDevice()) send('stats'); });
  $('btn-download-session').addEventListener('click', () => {
    if (!requireDevice()) return;
    if (activeCapture) return; // a download is already running
    showDumpStatus(transportKind === 'BLE'
      ? 'Downloading over Bluetooth — this can take a while…'
      : 'Downloading…', true);
    startCapture('dump', onDumpDone, 60000); // generous: BLE trickles slowly
    send('dump');
  });
  $('btn-clear-device').addEventListener('click', () => {
    if (!requireDevice()) return;
    if (!confirm('This erases every jump and the trace stored on the device. It cannot be undone. Continue?')) return;
    send('clear');
    showDumpStatus('Sent “clear” — the device is wiping its stored data.');
  });
  initConsole();
  initInstallTab();
  renderSessions();

  if (location.hash === '#mock') setupMock();
}

// Module scripts are deferred, so the DOM is ready — but guard just in case.
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
else init();
