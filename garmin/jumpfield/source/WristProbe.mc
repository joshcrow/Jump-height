// WristProbe.mc — the watch's own wrist accelerometer, min/max per second.
//
// ============================================================================
// THIS CODE IS EXCLUDED FROM THE SHIPPED BUILD AND MUST STAY THAT WAY UNTIL
// ONE MEASUREMENT SAYS OTHERWISE.
// ============================================================================
//
// WHAT THE SDK SAYS. Toybox.Sensor is the only route to the wrist
// accelerometer. In the installed SDK 9.2.0 reference, EVERY instance
// function of that module carries the same note:
//
//     "Note: Will cause an app crash if called from a data field app"
//
//     doc/Toybox/Sensor.html — on all nine of disableSensorType,
//     enableSensorEvents, enableSensorType, getInfo, getMaxSampleRate,
//     getMaxSampleRateForSensorType, registerSensorDataListener,
//     setEnabledSensors, unregisterSensorDataListener.
//
// That phrase appears in NO other module page in the entire doc tree (9
// occurrences, all in Sensor.html — grepped 2026-09-14), so it is a
// deliberate statement about Toybox.Sensor, not boilerplate. It is also
// consistent with the SDK's own samples: four of them are data fields
// (FieldTimerEvents, MoxyField, NordicThingy52CoinCollector,
// SimpleDataField) and not one of them touches Toybox.Sensor.
//
// Activity.Info — the object compute() is already handed — carries no
// accelerometer member of any kind (doc/Toybox/Activity/Info.html). There is
// no third route.
//
// WHY THIS FILE EXISTS ANYWAY. "Will cause an app crash" is a documented
// claim, not a measurement, and this repo does not accept verdicts without
// one (CLAUDE.md §2.2). Shipping the call to the rider to find out is not an
// option: this data field is the product's only user-facing interface, and a
// crash behind the Connect IQ splash is the failure mode this codebase has
// already been bitten by twice (see JumpFieldView.mc's header). So the probe
// exists, fully written, behind the `bench` annotation — excluded from every
// normal build, and buildable in one command on the Epix dev bench where a
// crash costs an afternoon, not a season:
//
//     monkeyc -f bench.jungle -d epix2 ... -o bin/JumpField-bench.prg
//
// The result is a finding either way. If it crashes, the note is true, this
// file is deleted and the wrist channel is closed for data fields — the
// accuracy plan loses an instrument and should stop counting on it. If it
// does not crash, ids 7/8 are already correct and the fields ship.
//
// UNITS. Sensor.AccelerometerData x/y/z are "in Milli G units. For
// reference, 1000 Milli G = 1 G" (doc/Toybox/Sensor/AccelerometerData.html
// overview) — so |a| in g is sqrt(x^2+y^2+z^2)/1000, confirmed, not assumed.
//
// SAMPLE RATE. 25 Hz, which is the rate in Garmin's own registerSensorDataListener
// example. The devices can do more: SDK Devices/epix2/simulator.json and
// Devices/instinct3solar45mm/simulator.json both declare
// "sensorSampleRate": {"maxAccelRate": 100}. 25 Hz is deliberate and not a
// limit — a 40 ms grid resolves a 1-4 s flight's entry and landing perfectly
// well, and it keeps the callback at 25 integer multiply-adds per second in
// a process whose real job is BLE and drawing. getMaxSampleRate() is the
// documented way to ask the device, and it is one of the nine functions
// above, so it can never be called from here.
//
// FIELD IDS 7 and 8 are reserved for these two in FitOut.mc even while this
// file is excluded, so that a future build cannot quietly reuse a number a
// saved activity might already carry.
//
// WHAT THIS COSTS THE SHIPPED BUILD: +75 B, not zero. This class contributes
// nothing, but JumpFieldView's three (:nowrist) stubs and its _wrist slot do
// (monkeyc --build-stats 0, Instinct: 13,192 B with the baro fields alone,
// 13,267 B once the stubs landed). The bench build is 14,144 B.

using Toybox.Sensor;
using Toybox.FitContributor;
using Toybox.Math;
import Toybox.Lang;

(:bench)
class WristProbe {

    hidden var _minField;
    hidden var _maxField;
    hidden var _running = false;

    // Running min/max of the SQUARED magnitude, in (milli-g)^2, as Numbers.
    // Squared and integer on purpose: the callback is the only thing here
    // that runs at 25 Hz, so it does no sqrt, no float, and no allocation —
    // one multiply-add chain and two compares per sample. Worst case is well
    // inside a signed 32-bit Number: a 16 g axis reading is 16000 milli-g,
    // and 3 * 16000^2 = 768,000,000 against a 2,147,483,647 ceiling.
    hidden var _minSq = null;
    hidden var _maxSq = null;

    function initialize(dataField) {
        try {
            _minField = dataField.createField(
                "wrist_amin_g", 7, FitContributor.DATA_TYPE_FLOAT,
                { :mesgType => FitContributor.MESG_TYPE_RECORD, :units => "g" });
            _maxField = dataField.createField(
                "wrist_amax_g", 8, FitContributor.DATA_TYPE_FLOAT,
                { :mesgType => FitContributor.MESG_TYPE_RECORD, :units => "g" });
        } catch (ex) {
            _minField = null;
            _maxField = null;
        }
    }

    // Idempotent. Every failure path leaves _running false and the fields
    // simply never written — the degrade the task asks for, which is silence
    // in the FIT rather than a zero that reads as a measurement.
    function start() as Void {
        if (_running) {
            return;
        }
        try {
            Sensor.registerSensorDataListener(method(:onSensorData), {
                :period => 1,
                :accelerometer => { :enabled => true, :sampleRate => 25 }
            });
            _running = true;
        } catch (ex) {
            _running = false;
        }
    }

    function stop() as Void {
        if (!_running) {
            return;
        }
        _running = false;
        try {
            Sensor.unregisterSensorDataListener();
        } catch (ex) {
        }
    }

    // THE HOT PATH. Called once per :period (1 s) with ~25 samples. Keep it
    // to compares and integer arithmetic; anything else belongs in write().
    function onSensorData(data as Sensor.SensorData) as Void {
        try {
            var accel = data.accelerometerData;
            if (accel == null) {
                return;
            }
            var xs = accel.x;
            var ys = accel.y;
            var zs = accel.z;
            if (xs == null || ys == null || zs == null) {
                return;
            }
            var n = xs.size();
            for (var i = 0; i < n; i += 1) {
                var x = xs[i];
                var y = ys[i];
                var z = zs[i];
                var sq = x * x + y * y + z * z;
                if (_minSq == null || sq < _minSq) { _minSq = sq; }
                if (_maxSq == null || sq > _maxSq) { _maxSq = sq; }
            }
        } catch (ex) {
            // A malformed batch must not take the field down, and must not
            // poison the window either: whatever was accumulated stays.
        }
    }

    // Called once per compute() (1 Hz). Writes the window that just closed
    // and resets, so each RECORD carries the min and max of ITS OWN second
    // and never a running-forever extreme.
    //
    // Nothing accumulated => nothing written. An unwritten field in one
    // record is honest; 0.0 g would be a claim of free fall.
    function writeAndReset() as Void {
        // .toFloat() is load-bearing: Math.sqrt returns Lang.Decimal (Float
        // OR Double, doc/Toybox/Math.html), and Field.setData "throws
        // UnexpectedTypeException if the input type does not match the type
        // specified in createField()" (doc/Toybox/FitContributor/Field.html).
        if (_minSq != null && _minField != null) {
            _minField.setData((Math.sqrt(_minSq) / 1000.0).toFloat());
        }
        if (_maxSq != null && _maxField != null) {
            _maxField.setData((Math.sqrt(_maxSq) / 1000.0).toFloat());
        }
        _minSq = null;
        _maxSq = null;
    }
}
