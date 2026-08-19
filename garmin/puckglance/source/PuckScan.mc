// PuckScan.mc — scan-only BLE reader for the puck's advertisement.
//
// WHAT THIS IS NOT: it is not PuckLink. jumpfield/source/PuckLink.mc is a
// six-state connect/subscribe machine (SCANNING -> PAIRING -> DISCOVERING ->
// SUBSCRIBING -> LIVE) because a data field needs the NUS stream. A glance
// needs one number, and that number is already in the air: since 2026-08-18
// the puck broadcasts manufacturer data in its scan response — company ID
// 0xFFFF, payload [batt_pct][flags], flags bit0 = charging — verified on
// hardware that day by a scan that read "battery 97%, charging=false" with
// no connection at all. So this class never calls pairDevice() and never calls
// registerProfile(): setDelegate + setScanState is the entire BLE surface.
// That is deliberate — a connection from a 64 KB glance context that also
// has to survive being killed at any moment is a liability, and it would
// fight the data field for the puck's single BLE link.
//
// (:glance) on the class: per doc/docs/Core_Topics/Backgrounding.html, which
// describes the same mechanism for :background — "Only modules, classes,
// functions and member variables decorated with the :background annotation
// will be compiled into your background service" — and Annotations.html,
// ":glance Denotes code blocks available to when running in Glance Mode."
// Everything the glance touches has to carry the annotation or it is not in
// the image at all.
//
// ERROR HANDLING: every catch here is a BARE `catch (ex)`, copying
// PuckLink.mc's header note verbatim in spirit — Connect IQ raises errors
// that are NOT Lang.Exception, and a filtered catch lets those escape. In a
// glance that means a blank row on the user's watch face list with no way to
// find out why.

using Toybox.BluetoothLowEnergy as Ble;
import Toybox.Lang;

(:glance)
class PuckScan extends Ble.BleDelegate {

    // The puck's advertised name prefix. Pucks advertise unique names
    // "JumpHeight-XXXX" (hardware-verified 2026-08-18), so this is a prefix
    // match, not an equality match — unlike jumpfield, which matches the one
    // name in its settings because it has to pick exactly one to connect to.
    // A glance has no setting and no connection, so it shows whichever
    // matching puck is loudest.
    static const NAME_PREFIX = "JumpHeight";

    // Bluetooth SIG company identifier the puck uses. 0xFFFF is the SIG's
    // reserved "for internal/test use" ID (BLE Core Spec, Assigned Numbers)
    // — correct for a hobby device that has no allocated ID, and the value
    // the firmware actually puts on air.
    static const COMPANY_ID = 0xFFFF;

    // Scan states this class exposes to the view. Deliberately fewer than
    // PuckLink's seven: there is no connection to be in the middle of.
    static const SCAN_IDLE = 0;
    static const SCAN_RUNNING = 1;
    static const SCAN_DEAD = 2;     // BLE unavailable to us at all

    // Everything is explicitly typed so this file passes `monkeyc -l 3`
    // (strict). At the default gradual level the untyped versions compiled
    // silently; strict named all of them, which is exactly the kind of
    // "compiles but nobody checked" surface this project has been bitten by
    // on silicon before.
    hidden var _state as Number;
    hidden var _name as String?;        // best match's advertised name
    hidden var _batt as Number?;        // 0..100
    hidden var _charging as Boolean;
    hidden var _rssi as Number?;        // best match's RSSI, for the tie-break
    hidden var _seen as Boolean;        // have we ever decoded a puck this scan?
    hidden var _onData as Method?;      // called when the display data changes

    // onData: a Method to invoke when _name/_batt/_charging change. The view
    // passes its own requestUpdate wrapper; keeping the callback abstract is
    // what lets this file stay WatchUi-free and therefore unit-testable
    // without a simulator (the same reason Protocol.mc/Model.mc are
    // hardware-free in jumpfield).
    function initialize(onData as Method?) {
        Ble.BleDelegate.initialize();
        _state = SCAN_IDLE;
        _name = null;
        _batt = null;
        _charging = false;
        _rssi = null;
        _seen = false;
        _onData = onData;
    }

    function state() as Number { return _state; }
    function puckName() as String? { return _name; }
    function battPct() as Number? { return _batt; }
    function isCharging() as Boolean { return _charging; }
    function hasReading() as Boolean { return _seen; }

    // Start scanning. No registerProfile() first: per the SDK docs for
    // setScanState — "Starts the BLE Scanning Operations. Once scanning is
    // started onScanResults() will be called on the registered BleDelegate
    // as Advertising data is received" — a profile is only needed for GATT
    // operations, which we never perform.
    function start() as Void {
        try {
            Ble.setDelegate(self);
            Ble.setScanState(Ble.SCAN_STATE_SCANNING);
            _state = SCAN_RUNNING;
        } catch (ex) {
            // Includes "this runtime context has no BLE central" — do not
            // retry, there is nothing to retry against. The view draws the
            // NO BLE state instead of an endless spinner.
            _state = SCAN_DEAD;
        }
    }

    // Called from the view's onHide()/the app's onStop(). Leaving a scan
    // running after the glance scrolls off screen would be a battery bug the
    // user could never attribute to us.
    function stop() as Void {
        try {
            Ble.setScanState(Ble.SCAN_STATE_OFF);
            // NOT Ble.setDelegate(null). The prose docs say null "deregisters
            // the current handler", but the declared signature is
            // setDelegate(delegate as BluetoothLowEnergy.BleDelegate) with no
            // Null in the union, and the type checker rejects it outright:
            //   ERROR: Invalid 'Null' passed as parameter 1 of type
            //   '$.Toybox.BluetoothLowEnergy.BleDelegate'.
            // (observed 2026-08-18, SDK 9.2.0, -d epix2). Stopping the scan is
            // what actually costs battery anyway; a delegate with no scan
            // running receives nothing.
        } catch (ex) {
            // Nothing to do — we are shutting down either way.
        }
        if (_state == SCAN_RUNNING) {
            _state = SCAN_IDLE;
        }
    }

    // ------------------------------------------------- BleDelegate callbacks

    // scanResults: Ble.Iterator of ScanResult — every advertisement seen
    // since the last call, not just new devices.
    function onScanResults(scanResults as Ble.Iterator) as Void {
        var changed = false;
        try {
            // Ble.Iterator.next() is declared to return Object? — the cast is
            // required, not decorative, and it is inside the try for a
            // reason: a cast that fails is an error, not a null.
            var r = scanResults.next();
            while (r != null) {
                if (_take(r as Ble.ScanResult)) {
                    changed = true;
                }
                r = scanResults.next();
            }
        } catch (ex) {
            // A malformed advert from ANY nearby device must not take the
            // glance down — we are parsing bytes from strangers.
        }
        if (changed && _onData != null) {
            _onData.invoke();
        }
    }

    function onScanStateChange(scanState as Ble.ScanState, status as Ble.Status) as Void {
        if (scanState == Ble.SCAN_STATE_SCANNING && status != Ble.STATUS_SUCCESS) {
            _state = SCAN_DEAD;
            if (_onData != null) {
                _onData.invoke();
            }
        }
    }

    // ------------------------------------------------------------- internals

    // Returns true if this result changed what the view should draw.
    hidden function _take(r as Ble.ScanResult) as Boolean {
        var name = r.getDeviceName();
        if (name == null || !_isPuckName(name)) {
            return false;
        }

        var rssi = r.getRssi();
        // Strongest-RSSI-wins, same rule as PuckLink's scan filter. Once we
        // have a reading, a weaker puck must not steal the display.
        if (_seen && _rssi != null && rssi != null && rssi < _rssi) {
            return false;
        }

        var data = null as ByteArray?;
        try {
            data = r.getManufacturerSpecificData(COMPANY_ID);
        } catch (ex) {
            data = null;
        }
        var decoded = decodeBattery(data);
        if (decoded == null) {
            // Name matched but no 0xFFFF payload: an older firmware, or the
            // scan response was not captured on this pass. Not a reading —
            // but do not clobber a good one we already have.
            return false;
        }

        _name = name;
        _batt = decoded[0] as Number;
        _charging = (decoded[1] as Number) != 0;
        _rssi = rssi;
        _seen = true;
        return true;
    }

    hidden function _isPuckName(name as String) as Boolean {
        var n = NAME_PREFIX.length();
        if (name.length() < n) {
            return false;
        }
        var head = name.substring(0, n);
        return head != null && head.equals(NAME_PREFIX);
    }

    // Decode [batt_pct][flags] out of the 0xFFFF manufacturer-specific AD
    // structure. Returns [pct, flags] or null.
    //
    // WHY THE LENGTH-4 BRANCH: the SDK documents
    // getManufacturerSpecificDataIterator() as yielding dictionaries with
    // SEPARATE :companyId and :data keys, which strongly implies
    // getManufacturerSpecificData(id) hands back the payload with the two
    // company-ID bytes already stripped — but the docs never say so
    // outright, and this has not been run against silicon. So accept both
    // shapes. The disambiguation is safe because the payload's first byte is
    // a battery percentage (0..100) and can never be 0xFF, so a leading
    // FF FF is unambiguously the little-endian company ID and not data.
    // If this ever needs fixing on the watch it is these six lines.
    // `data as ByteArray?` is spelled out rather than left implicit-Any: with
    // an untyped parameter the type checker emits "Cannot determine if
    // container access is using container type" on every b[i] read.
    static function decodeBattery(data as ByteArray?) as Array<Number>? {
        if (data == null) {
            return null;
        }
        var len = data.size();
        var off = 0;
        if (len >= 4 && data[0] == 0xFF && data[1] == 0xFF) {
            off = 2;
        }
        if (len - off < 2) {
            return null;
        }
        var pct = data[off] as Number;
        var flags = data[off + 1] as Number;
        if (pct < 0 || pct > 100) {
            return null;    // not our payload shape; refuse to draw a lie
        }
        return [pct, flags];
    }
}
