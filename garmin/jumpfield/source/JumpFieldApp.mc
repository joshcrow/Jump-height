// JumpFieldApp.mc
//
// AppBase entry point. Deliberately thin: creates the one View (which owns
// Model/PuckLink/FitOut — see JumpFieldView.mc) and forwards the activity's
// start/stop into it. BLE pairing does NOT persist across application
// instances (confirmed from the SDK's pairDevice docs, spec §9) — so the
// scan must be started fresh for every activity.
//
// It is NOT started from this file on a cold start, though. Both hooks here
// turned out to be wrong places for it: onStart() runs before the view
// exists, and getInitialView() hung the field outright. The link is started
// from JumpFieldView.compute(); read onStart() and getInitialView() below
// for the evidence behind each.

using Toybox.Application;
import Toybox.Lang;  // bare type names (Array, Void) resolve via import, not using

class JumpFieldApp extends Application.AppBase {

    hidden var _view;

    function initialize() {
        AppBase.initialize();
    }

    // This hook CANNOT be relied on to start the link, and the header's old
    // claim that it does was wrong. PROVEN 2026-08-10 in the simulator
    // (epix2), by printing both calls:
    //
    //     LIFECYCLE onStart, _view null? true
    //     LIFECYCLE getInitialView
    //
    // The framework calls onStart() BEFORE getInitialView(), so _view is
    // still null here on a cold start and this forwards nothing. That is why
    // PuckLink.start() never ran, no profile was registered, no scan was ever
    // begun, and the field sat on "finding puck" forever with the puck
    // advertising at -48 dBm two feet away. Every state except DEAD and LIVE
    // renders as "finding puck" (JumpFieldView._uiState), so a never-started
    // radio is indistinguishable on the glass from an actively searching one
    // -- which is how it survived a compile, 24 passing tests and a sideload.
    //
    // Kept anyway for the WARM path (an activity restarted against a view
    // that already exists), where it is the natural place to rescan. The
    // cold path is owned by JumpFieldView.compute(), and the call is
    // idempotent, so at most one of them actually starts anything.
    function onStart(state) as Void {
        if (_view != null) {
            _view.onAppStart();
        }
    }

    function onStop(state) as Void {
        if (_view != null) {
            _view.onAppStop();
        }
    }

    // Data fields return a single-element view array (no input delegate --
    // a data field isn't driven by button/touch input the way a widget or
    // app is).
    function getInitialView() {  // annotation dropped: the SDK's own
        // signature is a typed tuple ([Views] or [Views, InputDelegates]);
        // `as Array` is a different type and overriding rejects it

        // Deliberately does NOT start the BLE link here, even though
        // _startPending tells us onStart() already fired against a null _view.
        //
        // 2026-08-10, on the real Epix: starting it here HUNG THE FIELD. The
        // watch sat on the Connect IQ loading splash (app name + launcher
        // icon) inside the activity and never rendered — registering a BLE
        // profile while the framework is still constructing the initial view
        // is too much work in the wrong place. JumpFieldView.compute() owns
        // the start now; see _ensureLinkStarted() there.
        _view = new JumpFieldView();
        return [ _view ];
    }
}
