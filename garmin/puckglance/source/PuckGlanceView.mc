// PuckGlanceView.mc — the row the user sees in the glance list.
//
// This is the whole point of the app: a Connect IQ DATA FIELD only runs
// inside a started activity, so jumpfield can only show the puck's battery
// once you are already out on the water. A glance runs from the watch face,
// before anything is started.
//
// Drawing context: WatchUi.GlanceView's docs say the dc "will be bounded by
// glance area rather than a full screen dc", and that GlanceView does NOT
// support the layering APIs (addLayer/removeLayer/insertLayer/clearLayers).
// On epix2 that area is 274x103 at (112,13) with a 60x60 icon area at
// (40,34) — read from the SDK's own device file:
//   ~/Library/Application Support/Garmin/ConnectIQ/Devices/epix2/simulator.json
//   "glance": {"cacheUpdate": false, "liveUpdates": true,
//              "contentArea": {"x":112,"y":13,"width":274,"height":103}, ...}
// We still ask the dc for its size rather than hardcoding those numbers —
// the JSON is where they came from, not where they are read at runtime.
//
// "liveUpdates": true is the load-bearing fact for a BLE glance. Per
// doc/docs/Core_Topics/Glances.html there are two glance lifecycles: devices
// with ample resources keep the app alive and honour WatchUi.requestUpdate()
// ("Live UI Update"), while low-memory devices run a full onStart ->
// onUpdate -> onStop cycle and "calls to WatchUi.requestUpdate() will have
// no effect" ("Background UI Update"), caching the rendered pixels. Async
// BLE scan results only reach the screen on the first kind. epix2 is the
// first kind. A device where liveUpdates is false would show at best a
// stale cached battery — that is a per-device check, not an assumption.
//
// No dc.clear(): the system owns the glance row background.

import Toybox.Graphics;
import Toybox.Lang;
import Toybox.WatchUi;

(:glance)
class PuckGlanceView extends WatchUi.GlanceView {

    hidden var _scan as PuckScan;

    function initialize() {
        GlanceView.initialize();
        // method(:onScanData) is what keeps PuckScan free of any WatchUi
        // dependency — see its constructor comment.
        _scan = new PuckScan(method(:onScanData));
    }

    // Start the radio only while the row is actually on screen. onShow/onHide
    // are documented to run in the glance lifecycle for BOTH update models
    // (Glances.html lists onLayout/onShow/onUpdate/onHide explicitly for the
    // background-update path), so this is the one hook that works everywhere.
    function onShow() as Void {
        _scan.start();
    }

    function onHide() as Void {
        _scan.stop();
    }

    function onScanData() as Void {
        WatchUi.requestUpdate();
    }

    function onUpdate(dc as Graphics.Dc) as Void {
        var h = dc.getHeight();

        var label = "Puck";
        var value = "searching";
        var color = Graphics.COLOR_WHITE;

        if (_scan.hasReading()) {
            var name = _scan.puckName();
            if (name != null) {
                label = name;
            }
            var pct = _scan.battPct();
            value = (pct == null) ? "--" : pct.format("%d") + "%";
            // Charging and low battery are the two states worth a colour.
            // Everything else stays white: a glance row is read in a glance.
            if (_scan.isCharging()) {
                value = value + " chg";
                color = Graphics.COLOR_GREEN;
            } else if (pct != null && pct <= 20) {
                color = Graphics.COLOR_RED;
            }
        } else if (_scan.state() == PuckScan.SCAN_DEAD) {
            value = "no BLE";
        }

        // Two stacked lines, vertically centred in the row. FONT_TINY for the
        // label and FONT_MEDIUM for the number: the number is the thing you
        // opened the glance list to read.
        var labelH = dc.getFontHeight(Graphics.FONT_TINY);
        var valueH = dc.getFontHeight(Graphics.FONT_MEDIUM);
        var y = (h - (labelH + valueH)) / 2;
        if (y < 0) { y = 0; }

        // Left-justified from x=0 because the dc already starts at the
        // content area's left edge (x=112 on the physical screen) — drawing
        // at x=0 here is drawing at the left edge of OUR row, not the watch.
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(0, y, Graphics.FONT_TINY, label, Graphics.TEXT_JUSTIFY_LEFT);

        dc.setColor(color, Graphics.COLOR_TRANSPARENT);
        dc.drawText(0, y + labelH, Graphics.FONT_MEDIUM, value, Graphics.TEXT_JUSTIFY_LEFT);
    }
}
