// PuckDetailView.mc — the full-screen view behind the glance row.
//
// It only ever RUNS after the user selects the glance row, at which point
// the app has the watchApp budget (786432 bytes on epix2). It is still
// (:glance)-annotated, which looks wrong and is not: PuckGlanceApp's header
// has the compiler's own explanation — the entry class goes into glance
// scope whole, so PuckGlanceApp.getInitialView() names these two classes
// from inside glance scope and they have to exist there. Measured cost of
// that: +426 bytes of glance code, +194 bytes of glance data, against a
// 64 KB budget.
//
// Same scanner, more room to show what it found. It re-creates its own
// PuckScan rather than sharing the glance's: per Glances.html there is "no
// guarantee that a widget will always start in glance mode... before
// transitioning to its standard widget mode", so assuming a live scanner
// handed over from the glance would be assuming a lifecycle the SDK
// explicitly refuses to promise.

import Toybox.Graphics;
import Toybox.Lang;
import Toybox.WatchUi;

(:glance)
class PuckDetailView extends WatchUi.View {

    hidden var _scan as PuckScan;

    function initialize() {
        View.initialize();
        _scan = new PuckScan(method(:onScanData));
    }

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
        dc.setColor(Graphics.COLOR_BLACK, Graphics.COLOR_BLACK);
        dc.clear();
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);

        var w = dc.getWidth();
        var h = dc.getHeight();
        var cx = w / 2;

        var title = "Puck battery";
        var value = "--";
        var sub = "searching";

        if (_scan.hasReading()) {
            var name = _scan.puckName();
            if (name != null) {
                title = name;
            }
            var pct = _scan.battPct();
            value = (pct == null) ? "--" : pct.format("%d") + "%";
            sub = _scan.isCharging() ? "charging" : "on battery";
        } else if (_scan.state() == PuckScan.SCAN_DEAD) {
            sub = "BLE unavailable";
        }

        var titleH = dc.getFontHeight(Graphics.FONT_SMALL);
        var valueH = dc.getFontHeight(Graphics.FONT_NUMBER_HOT);
        var subH = dc.getFontHeight(Graphics.FONT_SMALL);
        var y = (h - (titleH + valueH + subH)) / 2;
        if (y < 0) { y = 0; }

        dc.drawText(cx, y, Graphics.FONT_SMALL, title, Graphics.TEXT_JUSTIFY_CENTER);
        dc.drawText(cx, y + titleH, Graphics.FONT_NUMBER_HOT, value, Graphics.TEXT_JUSTIFY_CENTER);
        dc.drawText(cx, y + titleH + valueH, Graphics.FONT_SMALL, sub, Graphics.TEXT_JUSTIFY_CENTER);
    }
}

// Exists so getInitialView() can return an input delegate alongside the
// view. It handles nothing yet — a back press already exits, which is the
// only behaviour this screen owes the user today.
(:glance)
class PuckDetailDelegate extends WatchUi.BehaviorDelegate {
    function initialize() {
        BehaviorDelegate.initialize();
    }
}
