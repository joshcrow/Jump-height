// PuckGlanceApp.mc — AppBase for the puck-battery glance.
//
// One app object serves two runtime contexts:
//   - Glance mode: the system calls getGlanceView() and draws the row in the
//     glance list. 64 KB budget on epix2 (compiler.json appTypes:
//     {"memoryLimit": 65536, "type": "glance"}).
//   - Full app: the user selects the row, the system calls getInitialView()
//     and the app gets the whole screen and the whole 768 KB watchApp
//     budget.
//
// THE ONE THING TO UNDERSTAND ABOUT GLANCE SCOPE, learned the hard way here.
// The compiler states the rule itself, as a warning, when this file carries
// no annotations at all:
//
//   WARNING: epix2: The entry point '$.PuckGlanceApp' was implicitly added
//   to the glance process since the app contains declarations annotated
//   with (:glance).
//
// The ENTRY CLASS goes into glance scope whole. Not the annotated members of
// it — all of it, getInitialView() included. So every symbol getInitialView()
// names must ALSO exist in glance scope, which is why PuckDetailView and
// PuckDetailDelegate carry (:glance) even though a glance never draws them.
// That is not belt-and-braces; without it the strict type checker says:
//
//   ERROR: epix2: .../PuckGlanceApp.mc:72,8:
//          Value 'PuckDetailView' not available in all function scopes.
//   ERROR: epix2: .../PuckGlanceApp.mc:72,8:
//          Value 'PuckDetailDelegate' not available in all function scopes.
//
// (all observed 2026-08-18, SDK 9.2.0, -d epix2.)
//
// ALWAYS BUILD THIS PROJECT WITH -l 3. At the default typecheck level none
// of the above is reported: a deliberate `new PuckDetailView()` planted
// inside PuckGlanceView produced BUILD SUCCESSFUL, and --build-stats showed
// the glance code section grow by 29 bytes — the size of the call, not of
// the class it referenced. A default-level BUILD SUCCESSFUL is NOT evidence
// that glance-scope symbols resolve.
//
// Cost of pulling the detail view into glance scope, measured rather than
// feared: glance code 1471 -> 1897 bytes, glance data 623 -> 817 bytes. That
// is 2.7 KB against a 64 KB budget. Removing every (:glance) in the project
// collapses the glance image to 114 B code / 223 B data, which is the proof
// that the annotation — not reachability — is what populates the scope.
//
// The per-member annotations below are therefore about intent, not size: the
// entry class is in glance scope either way, and marking the four members a
// glance actually uses is what stops the next person from assuming
// getInitialView() runs there.

import Toybox.Application;
import Toybox.Lang;
import Toybox.WatchUi;

class PuckGlanceApp extends Application.AppBase {

    (:glance)
    function initialize() {
        AppBase.initialize();
    }

    (:glance)
    function onStart(state as Dictionary?) as Void {
    }

    (:glance)
    function onStop(state as Dictionary?) as Void {
    }

    // Override called by the system to build the glance row. Not overriding
    // it would mean no row at all on API 4.0.0+: Glances.html — "In API level
    // 4.0.0 and above, apps and widgets must implement a glance view to
    // appear in the glance list."
    (:glance)
    function getGlanceView() as [WatchUi.GlanceView] or [WatchUi.GlanceView, WatchUi.GlanceViewDelegate] or Null {
        return [ new PuckGlanceView() ];
    }

    // Not annotated, because a glance never calls it — but see the header:
    // it is in glance scope regardless (whole entry class), which is why the
    // classes it names are annotated.
    function getInitialView() as [WatchUi.Views] or [WatchUi.Views, WatchUi.InputDelegates] {
        return [ new PuckDetailView(), new PuckDetailDelegate() ];
    }
}
