// ERROR HANDLING NOTE (2026-08-14): every catch in this file is a BARE
// `catch (ex)`, never `catch (ex instanceof Lang.Exception)`. Connect IQ
// raises errors that are NOT Lang.Exception; a filtered catch lets those
// escape and kills the entire data field. This project has been bitten
// twice on silicon — utf8ArrayToString throwing 'Unexpected Type Error' on
// the first notification, and the first corruption gate throwing 'System
// Error: Failed invoking <symbol>' on the first line received — both with
// every simulator test green. On an unproven watch this is the difference
// between a field that degrades and a field that dies behind a splash.
// PuckLink.mc
//
// BLE state machine (spec §5.4): IDLE -> SCANNING -> PAIRING -> DISCOVERING
// -> SUBSCRIBING -> LIVE, LIVE -> SCANNING on disconnect (Model is retained
// and marked stale, never cleared), DEAD if BLE itself is unavailable. Scan
// filter matches the NUS service UUID when scan results carry it, else the
// puck's advertised name (spec §5.1/§9.4); strongest RSSI wins among matches.
//
// NUS UUIDs and the chunked-notification/write model mirror web/app.js's
// BleTransport exactly (same service, same two characteristics, same "write
// the command, read the notify stream" shape) — see that file's header for
// why: one protocol, three clients (CLI/web/watch), never allowed to drift.
//
// This is the ONLY file in jumpfield/source that imports BLE — Protocol.mc
// and Model.mc stay hardware-free on purpose (see their headers), and
// garmin/jumpfield/tests/ must never import this file (spec's test
// constraint) so the unit tests keep running with zero simulator BLE state.
//
// A data field has no background loop of its own; poll() is this class's
// only clock, driven from JumpFieldView.compute() rather than onUpdate()
// because compute() is believed to keep running even when this field's
// screen isn't the one currently on-glass (spec §9 open item #9 — "BLE
// delegate callback delivery while the field's data page is not visible" —
// verify at M2, see FIRST_COMPILE.md).

using Toybox.BluetoothLowEnergy as Ble;
using Toybox.System;
using Toybox.StringUtil;
import Toybox.Lang;

class PuckLink extends Ble.BleDelegate {

    // NUS — Nordic UART Service (spec §5.1; identical to
    // firmware/src/platform/esp32/jh_link.cpp — formerly ble_link.h — and
    // web/app.js's NUS_SERVICE/NUS_RX/NUS_TX; the Sense sibling,
    // firmware/src/platform/nrf52/jh_link.cpp, implements the same NUS
    // surface via Bluefruit's BLEUart).
    const NUS_SERVICE = "6e400001-b5a3-f393-e0a9-e50e24dcca9e";
    const NUS_RX = "6e400002-b5a3-f393-e0a9-e50e24dcca9e";   // watch writes commands
    const NUS_TX = "6e400003-b5a3-f393-e0a9-e50e24dcca9e";   // puck notifies output

    // The one command this field ever sends (spec §13: no calibration/
    // commands from the watch) — a compile-time byte literal sidesteps
    // needing a String->ByteArray conversion API for a value that never
    // changes. Bytes are ASCII "stats\n".
    const STATS_CMD = [0x73, 0x74, 0x61, 0x74, 0x73, 0x0a]b;

    // CCCD "enable notifications" value (standard BLE, not NUS-specific):
    // little-endian uint16 = 0x0001.
    const CCCD_ENABLE = [0x01, 0x00]b;

    const BACKOFF_MIN_MS = 5000;   // spec §5.4: "backoff 5 s -> 15 s cap"
    const BACKOFF_MAX_MS = 15000;
    const BACKOFF_STEP_MS = 5000;

    // Public state values (JumpFieldView polls state() against these to pick
    // one of spec §4.2's four UI states, combined with Model.hasData()).
    // `static` so JumpFieldView can read them as PuckLink.STATE_* — plain
    // class consts are instance-scoped and the compiler rejects class-level
    // access (found on the first real build, 2026-08-04).
    static const STATE_IDLE = 0;
    static const STATE_SCANNING = 1;
    static const STATE_PAIRING = 2;
    static const STATE_DISCOVERING = 3;
    static const STATE_SUBSCRIBING = 4;
    static const STATE_LIVE = 5;
    static const STATE_DEAD = 6;

    hidden var _model;             // Model.State -- where parsed lines go
    hidden var _reader;            // Protocol.LineReader -- line reassembly
    hidden var _puckName as String;

    hidden var _svcUuid;
    hidden var _txUuid;
    hidden var _rxUuid;

    hidden var _state as Number;
    hidden var _device;            // Ble.Device once connected
    hidden var _txChar;            // notify characteristic
    hidden var _rxChar;            // write characteristic

    hidden var _backoffMs as Number;
    hidden var _resumeScanAtMs as Number;   // System.getTimer() deadline; 0 = none

    // model: Model.State to feed parsed lines into. puckName: the
    // resources/settings puckName property value (name-match fallback).
    function initialize(model, puckName as String) {
        Ble.BleDelegate.initialize();
        _model = model;
        _puckName = puckName;
        _reader = new Protocol.LineReader();
        _state = STATE_IDLE;
        _backoffMs = BACKOFF_MIN_MS;
        _resumeScanAtMs = 0;
        _device = null;
        _txChar = null;
        _rxChar = null;
        _svcUuid = Ble.stringToUuid(NUS_SERVICE);
        _txUuid = Ble.stringToUuid(NUS_TX);
        _rxUuid = Ble.stringToUuid(NUS_RX);
    }

    function state() as Number {
        return _state;
    }

    // Called once from JumpFieldApp.onStart() (spec 5.4/9 -- BLE "pairing
    // does not persist across application instances" per the pairDevice
    // docs, so registering + scanning fresh on every activity start is
    // required, not just simplest). Registers our one NUS profile (limit is
    // 3 per app; we use 1) and becomes the BLE delegate. Any failure here --
    // including this device/app-type combo simply not supporting a BLE
    // central at all -- goes straight to DEAD (spec §4.2 "NO BLE") rather
    // than retrying forever against a radio that will never come up.
    function start() as Void {
        try {
            Ble.setDelegate(self);
            var profile = {
                :uuid => _svcUuid,
                :characteristics => [
                    { :uuid => _txUuid, :descriptors => [ Ble.cccdUuid() ] },
                    { :uuid => _rxUuid }
                ]
            };
            Ble.registerProfile(profile);
            // _state stays IDLE until onProfileRegister() confirms success --
            // scanning only starts from a confirmed-good profile.
        } catch (ex) {
            _state = STATE_DEAD;
        }
    }

    // Called once from JumpFieldApp.onStop().
    function stop() as Void {
        try {
            Ble.setScanState(Ble.SCAN_STATE_OFF);
        } catch (ex) {
            // nothing to do -- we're stopping anyway
        }
        _state = STATE_IDLE;
    }

    // Driven from JumpFieldView.compute() (~1 Hz) -- the only clock this
    // class has. Only acts when a backed-off rescan is due; everything else
    // is event-driven via the BleDelegate callbacks below.
    function poll() as Void {
        if (_state != STATE_SCANNING || _resumeScanAtMs == 0) {
            return;
        }
        if (System.getTimer() >= _resumeScanAtMs) {
            _resumeScanAtMs = 0;
            _beginScan();
        }
    }

    // ---------------------------------------------------- BleDelegate callbacks

    function onProfileRegister(uuid, status) as Void {
        if (status == Ble.STATUS_SUCCESS) {
            _beginScan();
        } else {
            _state = STATE_DEAD;
        }
    }

    // scanResults: Ble.Iterator of ScanResult, all advertisements seen since
    // the last call (confirmed signature -- see FIRST_COMPILE.md).
    function onScanResults(scanResults) as Void {
        if (_state != STATE_SCANNING) {
            return;
        }
        var best = null;
        var bestRssi = null;
        var result = scanResults.next();
        while (result != null) {
            if (_matchesPuck(result)) {
                var rssi = (result as Ble.ScanResult).getRssi();  // next() types as Object
                if (best == null || (rssi != null && (bestRssi == null || rssi > bestRssi))) {
                    best = result;
                    bestRssi = rssi;
                }
            }
            result = scanResults.next();
        }
        if (best != null) {
            _connectTo(best);
        }
    }

    function onConnectedStateChanged(device, connState) as Void {
        if (connState == Ble.CONNECTION_STATE_CONNECTED) {
            _device = device;
            _onConnected();
        } else {
            // DISCONNECTED or REJECTED. Model is deliberately left untouched
            // (spec §5.4 "retain model") except for the stale mark; a fresh
            // STATS reply on the next LIVE reseeds count/best for free.
            _device = null;
            _txChar = null;
            _rxChar = null;
            if (_state == STATE_LIVE) {
                _model.markStale();
            }
            _scheduleRescan();
        }
    }

    function onDescriptorWrite(descriptor, status) as Void {
        if (_state != STATE_SUBSCRIBING) {
            return;
        }
        if (status == Ble.STATUS_SUCCESS) {
            _state = STATE_LIVE;
            _backoffMs = BACKOFF_MIN_MS;  // a healthy connect resets the backoff
            _sendStatsRequest();
        } else {
            _scheduleRescan();
        }
    }

    function onCharacteristicChanged(characteristic, value) as Void {
        _ingest(value);
    }

    function onCharacteristicWrite(characteristic, status) as Void {
        // Only the one-shot "stats\n" write uses WRITE_TYPE_WITH_RESPONSE;
        // nothing to react to either way here -- a lost write just means
        // STATS doesn't reseed until the next reconnect (jumps still show
        // live regardless, per spec's own "device is the source of truth"
        // design for n=/best_m= on every JUMP line).
    }

    function onScanStateChange(scanState, status) as Void {
        if (scanState == Ble.SCAN_STATE_SCANNING && status != Ble.STATUS_SUCCESS) {
            _scheduleRescan();
        }
    }

    // ---------------------------------------------------------------- internals

    hidden function _beginScan() as Void {
        _state = STATE_SCANNING;
        try {
            Ble.setScanState(Ble.SCAN_STATE_SCANNING);
        } catch (ex) {
            _scheduleRescan();
        }
    }

    // Stop scanning (if running), arm the backoff deadline for poll() to
    // pick up, and grow the backoff toward the 15 s cap (spec §5.4) -- reset
    // to the 5 s floor happens only on a confirmed LIVE (onDescriptorWrite
    // success above), so a run of repeated failures backs off monotonically
    // instead of resetting itself.
    hidden function _scheduleRescan() as Void {
        _state = STATE_SCANNING;
        try {
            Ble.setScanState(Ble.SCAN_STATE_OFF);
        } catch (ex) {
            // already off / never on -- fine
        }
        _resumeScanAtMs = System.getTimer() + _backoffMs;
        if (_backoffMs < BACKOFF_MAX_MS) {
            _backoffMs += BACKOFF_STEP_MS;
            if (_backoffMs > BACKOFF_MAX_MS) {
                _backoffMs = BACKOFF_MAX_MS;
            }
        }
    }

    // Service UUID match when scan results carry it, else name match
    // against the puckName setting (spec §5.1: "Scan filter: match service
    // UUID when visible in scan results, else name").
    hidden function _matchesPuck(scanResult) as Boolean {
        var uuids = scanResult.getServiceUuids();
        if (uuids != null) {
            var u = uuids.next();
            while (u != null) {
                if (u.equals(_svcUuid)) {
                    return true;
                }
                u = uuids.next();
            }
        }
        var name = scanResult.getDeviceName();
        // Prefix, not equality: pucks advertise "JumpHeight-XXXX" (unique per
        // board, 2026-08-18) so a quiver of them stays distinguishable. The
        // service-UUID branch above already matches any name; this fallback
        // must not be stricter than it. find() == 0 is Monkey C's startsWith.
        return name != null && name.find(_puckName) == 0;
    }

    hidden function _connectTo(scanResult) as Void {
        _state = STATE_PAIRING;
        try {
            Ble.setScanState(Ble.SCAN_STATE_OFF);
            var d = Ble.pairDevice(scanResult);
            if (d == null) {
                _scheduleRescan();
            }
            // else: wait for onConnectedStateChanged()
        } catch (ex) {
            _scheduleRescan();
        }
    }

    // No explicit "services discovered" callback exists in BleDelegate
    // (confirmed method list -- see FIRST_COMPILE.md): the profile was
    // pre-registered in start(), so getService()/getCharacteristic() are
    // synchronous local lookups against that registration, not a live GATT
    // round-trip. DISCOVERING is nominal (no separate wait state) unless
    // the lookup unexpectedly comes back null.
    hidden function _onConnected() as Void {
        _state = STATE_DISCOVERING;
        var svc = _device.getService(_svcUuid);
        if (svc == null) {
            _scheduleRescan();
            return;
        }
        _txChar = svc.getCharacteristic(_txUuid);
        _rxChar = svc.getCharacteristic(_rxUuid);
        if (_txChar == null || _rxChar == null) {
            _scheduleRescan();
            return;
        }
        _subscribe();
    }

    hidden function _subscribe() as Void {
        _state = STATE_SUBSCRIBING;
        var cccd = _txChar.getDescriptor(Ble.cccdUuid());
        if (cccd == null) {
            _scheduleRescan();
            return;
        }
        try {
            cccd.requestWrite(CCCD_ENABLE);
            // wait for onDescriptorWrite()
        } catch (ex) {
            _scheduleRescan();
        }
    }

    hidden function _sendStatsRequest() as Void {
        // US6: one write per connect reseeds count/best from the device's
        // own session totals -- the device is the source of truth, so a
        // reconnect (or late join) is free (spec §5.2/§5.4).
        if (_rxChar == null) {
            return;
        }
        try {
            _rxChar.requestWrite(STATS_CMD, { :writeType => Ble.WRITE_TYPE_WITH_RESPONSE });
        } catch (ex) {
            // Worst case STATS doesn't reseed until the next reconnect --
            // jumps still show live either way.
        }
    }

    hidden function _ingest(value) as Void {
        var text;
        try {
            // NOT StringUtil.utf8ArrayToString(). onCharacteristicChanged
            // delivers a Lang.ByteArray; that function takes an
            // Array<Lang.Number>. The mismatch compiles clean and works in
            // the simulator, then on real hardware throws
            //
            //     Unexpected Type Error: 'Failed invoking <symbol>'
            //
            // which is NOT a Lang.Exception, so it escaped the filtered catch
            // below and killed the entire data field on the first notification
            // the puck sent. Symptom on the wrist: the Connect IQ loading
            // splash (app name + launcher icon) forever, inside the activity,
            // looking for all the world like the field could not find the puck
            // -- when in fact scan, pair, discover and subscribe had all
            // already succeeded and real data was arriving.
            //
            // Diagnosed 2026-08-11 by pulling the watch's own crash log,
            // GARMIN/Apps/TEMP/CIQ_LOG.YML, which named this exact line.
            // Predicted, with this fix, as FIRST_COMPILE.md #3.
            text = StringUtil.convertEncodedString(value, {
                :fromRepresentation => StringUtil.REPRESENTATION_BYTE_ARRAY,
                :toRepresentation => StringUtil.REPRESENTATION_STRING_PLAIN_TEXT
            });
        } catch (ex) {
            return;  // an undecodable chunk -- drop it; the next chunk still
                     // resyncs cleanly on the next '\n'
        }
        if (text == null) {
            return;
        }
        // Bare catch, deliberately: this is the boundary where bytes chosen by
        // another device become control flow here. Nothing arriving over the
        // radio may ever take the field down again -- a dropped chunk costs
        // one line, and the reader resyncs on the next '\n'.
        try {
            var lines = _reader.feed(text);
            for (var i = 0; i < lines.size(); i += 1) {
                _model.onLine(Protocol.parseKV(lines[i]));
            }
        } catch (ex) {
            return;
        }
    }
}
