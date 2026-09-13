"""The first-run window, native. Three steps, one button each.

    connect   "JumpHeight"  Plug the puck in to charge. Rides upload
              themselves. First, connect the Google Drive folder they go to.
              [Connect Google Drive]  -> "Connected as nick@…"  [Continue]
    watch     "Your watch"  Sign in to Garmin and the GPS file for each ride
              comes along too.   [Email] [Password] [Sign in]  Skip
              -> a code field if Garmin asks   -> "Signed in"  [Continue]
    done      "Done"  Plug the puck in to charge, whenever. The wing in the
              menu bar shows its charge. A notification tells you when a
              ride is synced.   [Close]

OnboardingModel is pure Python and holds every string and every transition,
so it is testable without a screen. OnboardingWindow renders it with AppKit
(PyObjC, already bundled for the menu bar) and is only imported by the real
app. The window opens by itself on the first launch (daemon.main: no Drive
remote yet) and from "Set up…" in the menu bar afterwards.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

# ---------------------------------------------------------------- copy

TITLE_CONNECT = "JumpHeight"
BODY_CONNECT = ("Plug the puck in to charge. Rides upload themselves.\n"
                "First, connect the Google Drive folder they go to.")
BTN_CONNECT = "Connect Google Drive"
STATUS_CONNECTING = "Finish in your browser, then come back here."
STATUS_CONNECTED_AS = "Connected as {email}"
STATUS_CONNECTED = "Connected to Google Drive"
STATUS_CONNECT_FAILED = "That didn\u2019t connect. Try again."
BTN_CONTINUE = "Continue"

TITLE_WATCH = "Your watch"
BODY_WATCH = "Sign in to Garmin and the GPS file for each ride comes along too."
PLACEHOLDER_EMAIL = "Email"
PLACEHOLDER_PASSWORD = "Password"
PLACEHOLDER_CODE = "Code"
STATUS_MFA = "Garmin sent you a code."
STATUS_SIGNING_IN = "Signing in\u2026"
STATUS_SIGNED_IN = "Signed in"
BTN_SIGN_IN = "Sign in"
BTN_SKIP = "Skip"

TITLE_DONE = "Done"
BODY_DONE = ("Plug the puck in to charge, whenever.\n"
             "The wing in the menu bar shows its charge. "
             "A notification tells you when a ride is synced.")
BTN_CLOSE = "Close"

WINDOW_TITLE = "JumpHeight"


# --------------------------------------------------------------- model

@dataclass
class Screen:
    """What the window should show right now. Buttons: (label, action)."""
    step: str
    title: str
    body: str
    status: str = ""
    fields: "list[str]" = field(default_factory=list)   # placeholders, in order
    buttons: "list[tuple[str, str]]" = field(default_factory=list)
    busy: bool = False


@dataclass
class OnboardingModel:
    google_authorize: Callable[[], bool]
    google_account: Callable[[], Optional[str]]
    garmin_login: Callable[[str, str, Optional[str]], object]   # -> LoginResult-like
    on_finished: Callable[[], None] = lambda: None
    step: str = "connect"
    status: str = ""
    connected: bool = False
    signed_in: bool = False
    needs_mfa: bool = False
    busy: bool = False
    _email: str = ""
    _password: str = ""

    def start(self, already_connected: bool = False) -> None:
        self.step, self.status, self.busy = "connect", "", False
        self.connected = already_connected
        if already_connected:
            self.status = self._connected_status()

    # ---- rendering
    def screen(self) -> Screen:
        if self.step == "connect":
            if self.connected:
                return Screen("connect", TITLE_CONNECT, BODY_CONNECT, self.status,
                              [], [(BTN_CONTINUE, "next")])
            return Screen("connect", TITLE_CONNECT, BODY_CONNECT, self.status,
                          [], [(BTN_CONNECT, "connect")], busy=self.busy)
        if self.step == "watch":
            if self.signed_in:
                return Screen("watch", TITLE_WATCH, BODY_WATCH, STATUS_SIGNED_IN,
                              [], [(BTN_CONTINUE, "next")])
            fields = [PLACEHOLDER_CODE] if self.needs_mfa else [PLACEHOLDER_EMAIL, PLACEHOLDER_PASSWORD]
            return Screen("watch", TITLE_WATCH, BODY_WATCH, self.status, fields,
                          [(BTN_SIGN_IN, "signin"), (BTN_SKIP, "skip")], busy=self.busy)
        return Screen("done", TITLE_DONE, BODY_DONE, "", [], [(BTN_CLOSE, "close")])

    # ---- actions (synchronous; the window runs them off the main thread)
    def connect(self) -> None:
        self.busy, self.status = True, STATUS_CONNECTING
        ok = False
        try:
            ok = bool(self.google_authorize())
        finally:
            self.busy = False
        self.connected = ok
        self.status = self._connected_status() if ok else STATUS_CONNECT_FAILED

    def _connected_status(self) -> str:
        try:
            email = self.google_account()
        except Exception:  # noqa: BLE001
            email = None
        return STATUS_CONNECTED_AS.format(email=email) if email else STATUS_CONNECTED

    def next(self) -> None:
        if self.step == "connect" and self.connected:
            self.step, self.status = "watch", ""
        elif self.step == "watch":
            self.step, self.status = "done", ""

    def signin(self, values: "list[str]") -> None:
        if self.needs_mfa:
            code = values[0] if values else ""
            email, password = self._email, self._password
        else:
            email = values[0] if len(values) > 0 else ""
            password = values[1] if len(values) > 1 else ""
            code = None
            self._email, self._password = email, password
        self.busy, self.status = True, STATUS_SIGNING_IN
        try:
            result = self.garmin_login(email, password, code)
        except Exception:  # noqa: BLE001
            result = None
        finally:
            self.busy = False
        ok = bool(getattr(result, "ok", False))
        needs_mfa = bool(getattr(result, "needs_mfa", False))
        error = getattr(result, "error", None)
        self._password = "" if ok else self._password
        if ok:
            self.signed_in, self.needs_mfa, self.status = True, False, STATUS_SIGNED_IN
        elif needs_mfa:
            self.needs_mfa = True
            self.status = error or STATUS_MFA
        else:
            self.needs_mfa = False
            self.status = error or "Couldn\u2019t sign in. Check your email and password."

    def skip(self) -> None:
        self.needs_mfa = False
        self._password = ""
        self.step, self.status = "done", ""

    def close(self) -> None:
        self.on_finished()


# -------------------------------------------------------------- window

def build_window_class():
    """Import AppKit and build the window class. Only the real app calls
    this; tests drive OnboardingModel directly."""
    from AppKit import (NSApp, NSBackingStoreBuffered, NSButton, NSColor, NSFont,
                        NSMakeRect, NSSecureTextField, NSTextField, NSWindow,
                        NSWindowStyleMaskClosable, NSWindowStyleMaskTitled)
    from Foundation import NSObject
    from PyObjCTools import AppHelper

    W, PAD = 460, 28

    class OnboardingWindow(NSObject):
        """Owns one NSWindow and re-lays it out from model.screen()."""

        def initWithModel_(self, model):
            self = objc_super_init(self)
            if self is None:
                return None
            self.model = model
            self.window = None
            self.inputs = []
            return self

        # ---- showing
        def show(self):
            if self.window is None:
                style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                    NSMakeRect(0, 0, W, 300), style, NSBackingStoreBuffered, False)
                self.window.setTitle_(WINDOW_TITLE)
                self.window.setReleasedWhenClosed_(False)
            self.render()
            self.window.center()
            NSApp.activateIgnoringOtherApps_(True)
            self.window.makeKeyAndOrderFront_(None)

        def render(self):
            scr = self.model.screen()
            content = self.window.contentView()
            for v in list(content.subviews()):
                v.removeFromSuperview()
            self.inputs = []
            # measure from the top; AppKit's origin is bottom-left, so lay
            # out in a list first and flip at the end.
            rows = []                                       # (view, height)
            title = NSTextField.labelWithString_(scr.title)
            title.setFont_(NSFont.boldSystemFontOfSize_(22))
            rows.append((title, 30))
            body = NSTextField.wrappingLabelWithString_(scr.body)
            body.setFont_(NSFont.systemFontOfSize_(14))
            body.setTextColor_(NSColor.secondaryLabelColor())
            body.setSelectable_(False)
            rows.append((body, 20 * (scr.body.count("\n") + 2)))
            for ph in scr.fields:
                cls = NSSecureTextField if ph == PLACEHOLDER_PASSWORD else NSTextField
                f = cls.alloc().initWithFrame_(NSMakeRect(0, 0, W - 2 * PAD, 28))
                f.setPlaceholderString_(ph)
                f.setFont_(NSFont.systemFontOfSize_(14))
                rows.append((f, 30))
                self.inputs.append(f)
            if scr.status:
                st = NSTextField.wrappingLabelWithString_(scr.status)
                st.setFont_(NSFont.systemFontOfSize_(13))
                st.setSelectable_(False)
                rows.append((st, 22))
            btn_row = []
            for i, (label, action) in enumerate(scr.buttons):
                b = NSButton.buttonWithTitle_target_action_(label, self, "press:")
                b.setTag_(i)
                b.setEnabled_(not scr.busy)
                if i == 0:
                    b.setKeyEquivalent_("\r")
                btn_row.append(b)
            # place
            y = 300 - PAD
            for view, h in rows:
                y -= h
                view.setFrame_(NSMakeRect(PAD, y, W - 2 * PAD, h - 4))
                content.addSubview_(view)
                y -= 6
            y -= 8
            x = W - PAD
            for b in reversed(btn_row):
                b.sizeToFit()
                fr = b.frame()
                bw = max(fr.size.width + 24, 110)
                x -= bw
                b.setFrame_(NSMakeRect(x, y - 30, bw, 30))
                content.addSubview_(b)
                x -= 10
            self._actions = [a for _, a in scr.buttons]
            if self.inputs:
                self.window.makeFirstResponder_(self.inputs[0])

        # ---- actions
        def press_(self, sender):
            action = self._actions[sender.tag()]
            values = [str(f.stringValue()) for f in self.inputs]
            if action == "next":
                self.model.next(); self.render()
            elif action == "skip":
                self.model.skip(); self.render()
            elif action == "close":
                self.window.orderOut_(None)
                self.model.close()
            elif action == "connect":
                self._run_async(self.model.connect)
            elif action == "signin":
                self._run_async(lambda: self.model.signin(values))

        def _run_async(self, fn):
            self.model.busy = True
            self.model.status = STATUS_CONNECTING if self.model.step == "connect" else STATUS_SIGNING_IN
            self.render()

            def work():
                try:
                    fn()
                finally:
                    AppHelper.callAfter(self.render)
            threading.Thread(target=work, daemon=True).start()

    def objc_super_init(obj):
        import objc
        return objc.super(OnboardingWindow, obj).init()

    return OnboardingWindow


def make_window(model: OnboardingModel):
    return build_window_class().alloc().initWithModel_(model)


def production_model(on_finished: Callable[[], None] = lambda: None) -> OnboardingModel:
    """Wired to the real modules. Imported lazily so tests never touch them."""
    import sys
    from pathlib import Path
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import garmin  # noqa: E402
    import upload  # noqa: E402
    m = OnboardingModel(google_authorize=upload.authorize,
                        google_account=upload.account_email,
                        garmin_login=garmin.login,
                        on_finished=on_finished)
    m.start(already_connected=upload.is_authorized())
    return m
