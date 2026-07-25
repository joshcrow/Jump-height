// JumpFieldApp.mc
//
// AppBase entry point. Deliberately thin: creates the one View (which owns
// Model/PuckLink/FitOut — see JumpFieldView.mc) and forwards the activity's
// start/stop into it. BLE pairing does NOT persist across application
// instances (confirmed from the SDK's pairDevice docs, spec §9) — starting
// the scan fresh in onStart() every time is required, not just tidy.

using Toybox.Application;

class JumpFieldApp extends Application.AppBase {

    hidden var _view;

    function initialize() {
        AppBase.initialize();
    }

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
    function getInitialView() as Array {
        _view = new JumpFieldView();
        return [ _view ];
    }
}
