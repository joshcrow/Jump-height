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
    # How the status line should READ, not what it says: "" plain, "ok" the
    # step succeeded, "bad" it didn't and he can try again. The window turns
    # this into a checkmark or a calm grey line; it never changes the words,
    # which are the spec's. Kept here, in the tested layer, so "which state
    # is this" is never re-derived by string-matching in the AppKit code.
    tone: str = ""


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
                              [], [(BTN_CONTINUE, "next")], tone="ok")
            tone = "bad" if self.status == STATUS_CONNECT_FAILED else ""
            return Screen("connect", TITLE_CONNECT, BODY_CONNECT, self.status,
                          [], [(BTN_CONNECT, "connect")], busy=self.busy, tone=tone)
        if self.step == "watch":
            if self.signed_in:
                return Screen("watch", TITLE_WATCH, BODY_WATCH, STATUS_SIGNED_IN,
                              [], [(BTN_CONTINUE, "next")], tone="ok")
            fields = [PLACEHOLDER_CODE] if self.needs_mfa else [PLACEHOLDER_EMAIL, PLACEHOLDER_PASSWORD]
            # A wrong password is "bad"; "Garmin sent you a code." is not a
            # failure — it is the next instruction, so it stays plain.
            tone = "bad" if (self.status and not self.busy and not self.needs_mfa) else ""
            return Screen("watch", TITLE_WATCH, BODY_WATCH, self.status, fields,
                          [(BTN_SIGN_IN, "signin"), (BTN_SKIP, "skip")], busy=self.busy, tone=tone)
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
# Layout, in points. ONE margin (PAD) down both edges, gaps that go with the
# hierarchy rather than with each control, and a height computed from the
# content — the 460x300 fixed frame this replaced left a third of the window
# empty on every screen. W is fixed because the window does not resize.
W = 440
PAD = 24
TOP = 22
BOTTOM = 20
GAP_ICON = 14          # icon -> title
GAP_TITLE = 6          # title -> body
GAP_BODY = 18          # body -> the first control
GAP_FIELD = 8          # between fields
GAP_STATUS = 14        # last control -> status line
GAP_BUTTONS = 20       # whatever is last -> the button row
ICON = 56
FIELD_H = 24
BTN_H = 28
BTN_MIN_W = 96
BTN_GAP = 10
GLYPH_W = 20           # the spinner/checkmark gutter, so status text aligns


def app_icon():
    """The app's icon: the bundle's .icns when we are one, the repo's PNG
    when we are not, and None if neither is there — the window opens either
    way, per the brief's "gracefully absent"."""
    from pathlib import Path

    from AppKit import NSImage
    from Foundation import NSBundle

    here = Path(__file__).resolve()
    candidates = []
    res = NSBundle.mainBundle().resourcePath()
    if res:
        candidates.append(Path(str(res)) / "JumpHeight.icns")
    icons = here.parents[2] / "packaging" / "icon"
    candidates += [icons / "JumpHeight.icns", icons / "JumpHeight-1024.png"]
    for path in candidates:
        try:
            if not path.is_file():
                continue
        except OSError:
            continue
        img = NSImage.alloc().initWithContentsOfFile_(str(path))
        if img is not None:
            return img
    return None


_WINDOW_CLASS = None


def build_window_class():
    """Import AppKit and build the window class. Only the real app calls
    this; tests drive OnboardingModel directly. Built once and cached: a
    second definition of an Objective-C class with the same name raises."""
    global _WINDOW_CLASS
    if _WINDOW_CLASS is not None:
        return _WINDOW_CLASS

    from AppKit import (NSApp, NSBackingStoreBuffered, NSBezelStyleRounded,
                        NSButton, NSColor, NSControlSizeSmall, NSFont,
                        NSFontAttributeName, NSFontWeightBold,
                        NSFontWeightSemibold, NSForegroundColorAttributeName,
                        NSImageScaleProportionallyUpOrDown, NSImageView,
                        NSMakeRect, NSMakeSize, NSProgressIndicator,
                        NSProgressIndicatorStyleSpinning, NSSecureTextField,
                        NSTextField, NSView, NSWindow,
                        NSWindowStyleMaskClosable, NSWindowStyleMaskTitled)
    import objc
    from Foundation import NSAttributedString, NSObject
    from PyObjCTools import AppHelper

    class OnboardingWindow(NSObject):
        """Owns one NSWindow and re-lays it out from model.screen()."""

        def initWithModel_(self, model):
            self = objc_super_init(self)
            if self is None:
                return None
            self.model = model
            self.window = None
            self.inputs = []
            self._actions = []
            self._typed = {}        # placeholder -> what he typed, kept across renders
            return self

        # ---- showing
        def show(self):
            first = self.window is None
            if first:
                style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                    NSMakeRect(0, 0, W, 200), style, NSBackingStoreBuffered, False)
                self.window.setTitle_(WINDOW_TITLE)
                self.window.setReleasedWhenClosed_(False)
                # The red close button is the Cancel of this window: it ends
                # setup the same way the Close button does (on_finished, which
                # quits `python -m puckd setup` and is a no-op inside the
                # daemon). Nothing it does is destructive, so Escape -> close
                # would be safe too; Escape is left unbound anyway.
                self.window.setDelegate_(self)
            self.render()
            if first:
                self.window.center()
            NSApp.activateIgnoringOtherApps_(True)
            self.window.makeKeyAndOrderFront_(None)

        # ---- layout helpers
        @objc.python_method
        def _fit(self, tf, width):
            """Height this label needs at `width`. Measured, not assumed —
            the old 20 * (newlines + 2) guess is what left the dead space."""
            tf.setPreferredMaxLayoutWidth_(width)
            h = float(tf.fittingSize().height)
            tf.setFrameSize_(NSMakeSize(width, h))
            return h

        @objc.python_method
        def _resize(self, height):
            """Grow or shrink to the content, keeping the TOP edge still so
            the window does not hop up the screen between steps."""
            win = self.window
            new = win.frameRectForContentRect_(NSMakeRect(0, 0, W, height))
            old = win.frame()
            top = old.origin.y + old.size.height
            win.setFrame_display_(
                NSMakeRect(old.origin.x, top - new.size.height,
                           new.size.width, new.size.height), True)

        @objc.python_method
        def _label(self, text, size, weight, color, wrap):
            f = (NSFont.systemFontOfSize_weight_(size, weight) if weight is not None
                 else NSFont.systemFontOfSize_(size))
            tf = (NSTextField.wrappingLabelWithString_(text) if wrap
                  else NSTextField.labelWithString_(text))
            tf.setFont_(f)
            tf.setTextColor_(color)
            tf.setSelectable_(False)
            return tf

        def render(self):
            scr = self.model.screen()
            for f in self.inputs:                     # keep what he typed
                self._typed[str(f.placeholderString() or "")] = str(f.stringValue())
            if self.model.signed_in:
                self._typed.pop(PLACEHOLDER_PASSWORD, None)
            content = self.window.contentView()
            for v in list(content.subviews()):
                v.removeFromSuperview()
            self.inputs = []
            inner = W - 2 * PAD
            rows = []                                  # (view, width, height, gap below)

            def add(view, h, gap, width=inner):
                rows.append((view, width, h, gap))

            # identity: the icon, first screen only, modest
            if scr.step == "connect":
                img = app_icon()
                if img is not None:
                    iv = NSImageView.alloc().initWithFrame_(NSMakeRect(0, 0, ICON, ICON))
                    iv.setImage_(img)
                    iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                    iv.setAccessibilityLabel_(WINDOW_TITLE)
                    add(iv, ICON, GAP_ICON, ICON)

            title = self._label(scr.title, 22, NSFontWeightSemibold,
                                NSColor.labelColor(), False)
            add(title, self._fit(title, inner), GAP_TITLE)

            body = self._label(scr.body, 13, None, NSColor.secondaryLabelColor(), True)
            add(body, self._fit(body, inner), GAP_BODY)

            for ph in scr.fields:
                cls = NSSecureTextField if ph == PLACEHOLDER_PASSWORD else NSTextField
                f = cls.alloc().initWithFrame_(NSMakeRect(0, 0, inner, FIELD_H))
                f.setPlaceholderString_(ph)
                f.setStringValue_(self._typed.get(ph, ""))
                f.setFont_(NSFont.systemFontOfSize_(13))
                f.setAccessibilityLabel_(ph)       # VoiceOver reads the placeholder as the label
                f.setTarget_(self)
                f.setAction_("fieldReturn:")       # Return in a field = the primary button
                add(f, FIELD_H, GAP_FIELD)
                self.inputs.append(f)

            # status: spinner while busy, a green check when the step is done,
            # a calm grey line when it isn't. The WORDS are the model's, always.
            if scr.status:
                gutter = GLYPH_W if (scr.busy or scr.tone == "ok") else 0
                # Full contrast when the line is the outcome of a press
                # (done, or didn't) and secondary when it is only narration
                # ("Finish in your browser…"). No red anywhere: a retry is
                # not an alarm.
                st = self._label(scr.status, 13, None,
                                 NSColor.secondaryLabelColor() if (scr.busy or not scr.tone)
                                 else NSColor.labelColor(), True)
                h = self._fit(st, inner - gutter)
                rh = max(h, 17.0)
                row = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, inner, rh))
                st.setFrame_(NSMakeRect(gutter, rh - h, inner - gutter, h))
                row.addSubview_(st)
                if scr.busy:
                    sp = NSProgressIndicator.alloc().initWithFrame_(
                        NSMakeRect(0, rh - 16, 15, 15))
                    sp.setStyle_(NSProgressIndicatorStyleSpinning)
                    sp.setControlSize_(NSControlSizeSmall)
                    sp.setDisplayedWhenStopped_(False)
                    sp.startAnimation_(None)
                    row.addSubview_(sp)
                elif scr.tone == "ok":
                    ck = self._label("✓", 13, NSFontWeightBold,
                                     NSColor.systemGreenColor(), False)
                    ck.setFrame_(NSMakeRect(0, rh - h, GLYPH_W - 4, h))
                    ck.setAccessibilityLabel_("Done")
                    row.addSubview_(ck)
                if rows:
                    view, width, rowh, _ = rows[-1]
                    rows[-1] = (view, width, rowh, GAP_STATUS)
                add(row, rh, GAP_BUTTONS)
            if rows:
                view, width, rowh, _ = rows[-1]
                rows[-1] = (view, width, rowh, GAP_BUTTONS)

            # buttons. scr.buttons[0] is the primary: bottom-right, accent
            # filled, Return presses it. Anything after it is a plain
            # text-style button to its LEFT (the old code placed them the
            # other way round, so Skip sat where Sign in belonged).
            btns = []
            for i, (lbl, _action) in enumerate(scr.buttons):
                b = NSButton.buttonWithTitle_target_action_(lbl, self, "press:")
                b.setTag_(i)
                b.setBezelStyle_(NSBezelStyleRounded)
                b.setFont_(NSFont.systemFontOfSize_(13))
                b.setEnabled_(not scr.busy)
                b.sizeToFit()
                width = float(b.frame().size.width)
                if i == 0:
                    b.setKeyEquivalent_("\r")
                    b.setBezelColor_(NSColor.controlAccentColor())
                    width = max(width + 16, BTN_MIN_W)
                else:
                    b.setBordered_(False)
                    b.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
                        lbl, {NSForegroundColorAttributeName: NSColor.controlAccentColor(),
                              NSFontAttributeName: NSFont.systemFontOfSize_(13)}))
                    b.setKeyEquivalent_("")      # Escape must never fire Skip
                    width = width + 8
                btns.append((b, width))

            height = TOP + BTN_H + BOTTOM
            for _view, _w, h, gap in rows:
                height += h + gap
            self._resize(height)

            y = height - TOP
            for view, width, h, _gap in rows:
                y -= h
                view.setFrame_(NSMakeRect(PAD, y, width, h))
                content.addSubview_(view)
                y -= _gap
            x = W - PAD
            for b, width in btns:                 # primary first = rightmost
                x -= width
                b.setFrame_(NSMakeRect(x, BOTTOM, width, BTN_H))
                content.addSubview_(b)
                x -= BTN_GAP

            self._actions = [a for _, a in scr.buttons]
            if self.inputs:
                for a, nxt in zip(self.inputs, self.inputs[1:]):
                    a.setNextKeyView_(nxt)
                self.inputs[-1].setNextKeyView_(btns[0][0])
                btns[0][0].setNextKeyView_(self.inputs[0])
                self.window.makeFirstResponder_(self.inputs[0])

        # ---- actions
        def windowWillClose_(self, notification):
            self.model.close()

        def fieldReturn_(self, sender):
            self._fire(0)

        def press_(self, sender):
            self._fire(sender.tag())

        @objc.python_method
        def _fire(self, index):
            if index >= len(self._actions):
                return
            action = self._actions[index]
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

        @objc.python_method
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

    _WINDOW_CLASS = OnboardingWindow
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
