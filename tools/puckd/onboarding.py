"""The first-run window, native. ONE window: a two-row checklist, then a
page that shows how it works.

    checklist   JumpHeight / Sync for the puck
                "Charge the puck from this Mac and its rides sync
                 themselves: …"
                1  Google Drive     Where the rides go                [Connect]
                2  Garmin Connect   Optional · the GPS track …        [Sign in]
                You can come back to this from the wing in the menu bar.
                                                          [Skip for now]

                The primary (Return) is on the FIRST UNDONE row. A done row
                goes green with the real account and a text "Change". Garmin
                expands INLINE under its own row: Email, Password, a Code
                field only when Garmin asks, Skip and Sign in.

    how         All set / Here's what to expect
                three cards, each a real piece of the UI he will see:
                the four menu-bar glyphs, a notification, the menu
                                       Plug the puck in to charge, whenever.
                                                                    [Done]

Both rows settled (done or skipped) advances to "how" on a first run. "Set
up…" in the menu bar reopens the checklist with the real current state and a
"How it works" button, so the page stays reachable.

OnboardingModel is pure Python and holds every string, every transition and
which button is primary, so it is testable without a screen
(tools/tests/test_puckd_onboarding.py). OnboardingWindow renders screen()
with AppKit (PyObjC, already bundled for the menu bar) and decides nothing.
The window opens by itself on the first launch (daemon.main: no Drive remote
yet) and from "Set up…" in the menu bar afterwards.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

# ---------------------------------------------------------------- copy

WINDOW_TITLE = "JumpHeight"

BRAND_TITLE = "JumpHeight"
BRAND_SUBTITLE = "Sync for the puck"
INTRO = ("Charge the puck from this Mac and its rides sync themselves: "
         "the recording goes to Google Drive, and the matching GPS track "
         "comes over from Garmin Connect. Two sign-ins, once.")

# row 1 -- Google Drive
GOOGLE_TITLE = "Google Drive"
GOOGLE_SUB_TODO = "Where the rides go"
GOOGLE_SUB_WORKING = "Finish in your browser, then come back here."
GOOGLE_SUB_DONE_AS = "Connected as {email}"
GOOGLE_SUB_DONE = "Connected to Google Drive"
GOOGLE_SUB_FAILED = "That didn’t connect. Try again."
BTN_CONNECT = "Connect"

# row 2 -- Garmin Connect
GARMIN_TITLE = "Garmin Connect"
GARMIN_SUB_TODO = "Optional · the GPS track for each ride"
GARMIN_SUB_FORM = "Your Garmin email and password. The password is never saved."
GARMIN_SUB_MFA = "Garmin sent you a code."
GARMIN_SUB_WORKING = "Signing in…"
GARMIN_SUB_DONE = "Signed in"
GARMIN_SUB_SKIPPED = "Skipped"
GARMIN_SUB_FAILED = "Couldn’t sign in. Check your email and password."
BTN_SIGN_IN = "Sign in"
BTN_SKIP = "Skip"

BTN_CHANGE = "Change"
PLACEHOLDER_EMAIL = "Email"
PLACEHOLDER_PASSWORD = "Password"
PLACEHOLDER_CODE = "Code"

FOOT_CHECKLIST = "You can come back to this from the wing in the menu bar."
FOOT_CODE_HINT = "If Garmin sends a code, a Code field appears here."
BTN_SKIP_FOR_NOW = "Skip for now"
BTN_HOW = "How it works"

# the "how it works" page
HOW_TITLE = "All set"
HOW_SUBTITLE = "Here’s what to expect"
CARD_GLYPHS_TITLE = "The wing in the menu bar"
CARD_GLYPHS_BODY = ("Faded: no puck. Solid: puck charging, all synced. "
                    "With a line: syncing now. With a dot: it needs you.")
CARD_NOTIF_TITLE = "One notification per ride"
CARD_NOTIF_BODY = "Plus “Puck charged”. You never have to open anything."
CARD_NOTIF_SAMPLE = "Ride synced · 12 jumps"
CARD_MENU_TITLE = "Click the wing for details"
CARD_MENU_BODY = "Charge, last ride, your rides folder, and this setup again."
CARD_MENU_LINES = ("Puck 86% · charging",
                   "Last ride Tue 4:52 pm · 12 jumps",
                   "—",                       # the separator line
                   "Open rides folder",
                   "Set up…")
FOOT_HOW = "Plug the puck in to charge, whenever."
BTN_DONE = "Done"

BADGE_DONE = "✓"

PAGE_CHECKLIST = "checklist"
PAGE_HOW = "how"


# --------------------------------------------------------------- model

@dataclass
class Button:
    label: str
    action: str
    style: str = "normal"       # "primary" | "normal" | "text"


@dataclass
class Field:
    placeholder: str
    secure: bool = False


@dataclass
class Row:
    """One line of the checklist. `tone` says how the subtitle should READ --
    "muted" not yet, "ok" done (green), "plain" it is narrating or it failed
    -- never what it says, which is this module's copy."""
    key: str
    badge: str
    title: str
    subtitle: str
    tone: str = "muted"
    done: bool = False
    settled: bool = False       # done, or skipped on purpose -- either way,
                                # never the thing the window points at
    busy: bool = False
    button: Optional[Button] = None
    fields: "list[Field]" = field(default_factory=list)
    actions: "list[Button]" = field(default_factory=list)

    @property
    def expanded(self) -> bool:
        return bool(self.fields or self.actions)


@dataclass
class Card:
    art: str                    # "glyphs" | "notification" | "menu"
    title: str
    body: str


@dataclass
class Screen:
    page: str
    title: str
    subtitle: str
    intro: str = ""
    rows: "list[Row]" = field(default_factory=list)
    cards: "list[Card]" = field(default_factory=list)
    footer_left: str = ""
    footer_buttons: "list[Button]" = field(default_factory=list)

    def primary(self) -> Optional[Button]:
        """The one button Return presses, wherever it sits."""
        for row in self.rows:
            for b in list(row.actions) + ([row.button] if row.button else []):
                if b is not None and b.style == "primary":
                    return b
        for b in self.footer_buttons:
            if b.style == "primary":
                return b
        return None


@dataclass
class OnboardingModel:
    google_authorize: Callable[[], bool]
    google_account: Callable[[], Optional[str]]
    garmin_login: Callable[[str, str, Optional[str]], object]   # -> LoginResult-like
    on_finished: Callable[[], None] = lambda: None
    # read back when the window is reopened from "Set up…", so it always
    # shows what is true now rather than what was true at launch.
    google_is_authorized: Optional[Callable[[], bool]] = None
    garmin_is_signed_in: Optional[Callable[[], bool]] = None

    page: str = PAGE_CHECKLIST
    google: str = "todo"        # todo | working | done | failed
    garmin: str = "todo"        # todo | form | working | done | skipped
    needs_mfa: bool = False
    reopened: bool = False
    google_status: str = ""     # the account line, once it is known
    garmin_error: str = ""
    _email: str = ""
    _password: str = ""

    # ---- entry points
    def start(self, google_connected: bool = False, garmin_signed_in: bool = False,
              reopened: bool = False) -> None:
        self.page = PAGE_CHECKLIST
        self.needs_mfa = False
        self.reopened = reopened
        self.google = "done" if google_connected else "todo"
        self.google_status = self._account_line() if google_connected else ""
        self.garmin = "done" if garmin_signed_in else "todo"
        self.garmin_error = ""

    def reopen(self) -> None:
        """"Set up…" pressed again: re-read the world and show the checklist."""
        connected = self.google == "done"
        signed_in = self.garmin == "done"
        if self.google_is_authorized is not None:
            try:
                connected = bool(self.google_is_authorized())
            except Exception:  # noqa: BLE001
                pass
        if self.garmin_is_signed_in is not None:
            try:
                signed_in = bool(self.garmin_is_signed_in())
            except Exception:  # noqa: BLE001
                pass
        self.start(google_connected=connected, garmin_signed_in=signed_in, reopened=True)

    # ---- rendering
    def settled(self) -> bool:
        return self.google == "done" and self.garmin in ("done", "skipped")

    def screen(self) -> Screen:
        if self.page == PAGE_HOW:
            return Screen(PAGE_HOW, HOW_TITLE, HOW_SUBTITLE,
                          cards=[Card("glyphs", CARD_GLYPHS_TITLE, CARD_GLYPHS_BODY),
                                 Card("notification", CARD_NOTIF_TITLE, CARD_NOTIF_BODY),
                                 Card("menu", CARD_MENU_TITLE, CARD_MENU_BODY)],
                          footer_left=FOOT_HOW,
                          footer_buttons=[Button(BTN_DONE, "done", "primary")])
        rows = [self._google_row(), self._garmin_row()]
        # the primary sits on the FIRST row that still needs him, and the
        # expanded Garmin form owns it while it is open.
        # …and nothing is primary while a row is working: the target must not
        # move to the next row under his cursor while the first one runs.
        if not any(b.style == "primary" for r in rows for b in r.actions) \
                and not any(r.busy for r in rows):
            for r in rows:
                if r.button is not None and not r.settled and not r.busy:
                    r.button.style = "primary"
                    break
        left = FOOT_CODE_HINT if (self.garmin in ("form", "working") and not self.needs_mfa) else FOOT_CHECKLIST
        buttons: "list[Button]" = []
        if self.reopened:
            buttons = [Button(BTN_HOW, "how", "text")]
        elif not self.settled() and self.garmin not in ("form", "working"):
            buttons = [Button(BTN_SKIP_FOR_NOW, "skip_all", "text")]
        return Screen(PAGE_CHECKLIST, BRAND_TITLE, BRAND_SUBTITLE, INTRO,
                      rows=rows, footer_left=left, footer_buttons=buttons)

    def _google_row(self) -> Row:
        if self.google == "done":
            return Row("google", BADGE_DONE, GOOGLE_TITLE, self.google_status,
                       tone="ok", done=True, settled=True,
                       button=Button(BTN_CHANGE, "google", "text"))
        if self.google == "working":
            return Row("google", "1", GOOGLE_TITLE, GOOGLE_SUB_WORKING,
                       tone="plain", busy=True)
        if self.google == "failed":
            return Row("google", "1", GOOGLE_TITLE, GOOGLE_SUB_FAILED,
                       tone="plain", button=Button(BTN_CONNECT, "google"))
        return Row("google", "1", GOOGLE_TITLE, GOOGLE_SUB_TODO,
                   button=Button(BTN_CONNECT, "google"))

    def _garmin_row(self) -> Row:
        if self.garmin == "done":
            return Row("garmin", BADGE_DONE, GARMIN_TITLE, GARMIN_SUB_DONE,
                       tone="ok", done=True, settled=True,
                       button=Button(BTN_CHANGE, "garmin", "text"))
        if self.garmin in ("form", "working"):
            if self.needs_mfa:
                sub, fields = GARMIN_SUB_MFA, [Field(PLACEHOLDER_CODE)]
            else:
                sub = self.garmin_error or GARMIN_SUB_FORM
                fields = [Field(PLACEHOLDER_EMAIL), Field(PLACEHOLDER_PASSWORD, secure=True)]
            busy = self.garmin == "working"
            if busy:
                sub = GARMIN_SUB_WORKING
            return Row("garmin", "2", GARMIN_TITLE, sub, tone="plain", busy=busy,
                       fields=fields,
                       actions=[Button(BTN_SKIP, "garmin_skip", "text"),
                                Button(BTN_SIGN_IN, "garmin_signin", "primary")])
        if self.garmin == "skipped":
            # skipped is settled: the window stops pointing at it, and
            # nothing here ever nags him about it again.
            return Row("garmin", "2", GARMIN_TITLE, GARMIN_SUB_SKIPPED, settled=True,
                       button=Button(BTN_SIGN_IN, "garmin"))
        return Row("garmin", "2", GARMIN_TITLE, GARMIN_SUB_TODO,
                   button=Button(BTN_SIGN_IN, "garmin"))

    # ---- actions (synchronous; the window runs the slow ones off the main thread)
    def google_connect(self) -> None:
        self.google = "working"
        ok = False
        try:
            ok = bool(self.google_authorize())
        finally:
            pass
        if ok:
            self.google, self.google_status = "done", self._account_line()
        else:
            self.google, self.google_status = "failed", ""
        self._advance()

    def _account_line(self) -> str:
        try:
            email = self.google_account()
        except Exception:  # noqa: BLE001
            email = None
        return GOOGLE_SUB_DONE_AS.format(email=email) if email else GOOGLE_SUB_DONE

    def garmin_open(self) -> None:
        """Sign in / Change on the Garmin row: expand it, in place."""
        self.garmin, self.needs_mfa, self.garmin_error = "form", False, ""

    def garmin_signin(self, values: "list[str]") -> None:
        if self.needs_mfa:
            code = values[0] if values else ""
            email, password = self._email, self._password
        else:
            email = values[0] if len(values) > 0 else ""
            password = values[1] if len(values) > 1 else ""
            code = None
            self._email, self._password = email, password
        self.garmin = "working"
        try:
            result = self.garmin_login(email, password, code)
        except Exception:  # noqa: BLE001
            result = None
        ok = bool(getattr(result, "ok", False))
        needs_mfa = bool(getattr(result, "needs_mfa", False))
        error = getattr(result, "error", None)
        if ok:
            self._password = ""
            self.garmin, self.needs_mfa, self.garmin_error = "done", False, ""
        elif needs_mfa:
            self.garmin, self.needs_mfa = "form", True
            self.garmin_error = ""
        else:
            self.garmin, self.needs_mfa = "form", False
            self.garmin_error = error or GARMIN_SUB_FAILED
        self._advance()

    def garmin_skip(self) -> None:
        self.garmin, self.needs_mfa, self._password, self.garmin_error = "skipped", False, "", ""
        self._advance()

    def _advance(self) -> None:
        """Both rows settled on a first run: the window becomes the page that
        says what happens next. A reopened window never jumps under him."""
        if self.settled() and not self.reopened:
            self.page = PAGE_HOW

    def how(self) -> None:
        self.page = PAGE_HOW

    def done(self) -> None:
        self.on_finished()

    def skip_all(self) -> None:
        self.on_finished()

    # the window dispatches by name; kept here so the AppKit layer branches
    # on nothing at all.
    def act(self, action: str, values: "Optional[list[str]]" = None) -> None:
        if action == "google":
            self.google_connect()
        elif action == "garmin":
            self.garmin_open()
        elif action == "garmin_signin":
            self.garmin_signin(list(values or []))
        elif action == "garmin_skip":
            self.garmin_skip()
        elif action == "how":
            self.how()
        elif action == "done":
            self.done()
        elif action == "skip_all":
            self.skip_all()

    SLOW = ("google", "garmin_signin")   # these two touch the network


# -------------------------------------------------------------- window
# Layout, in points. ONE margin (PAD) down both edges; the window is 500 wide
# and as tall as its content, measured (fittingSize), never guessed.
W = 500
PAD = 24
TOP = 20
BOTTOM = 18
GAP_BRAND = 14         # brand row -> intro
GAP_INTRO = 16         # intro -> the list
GAP_FOOT = 16          # the list/cards -> the footer
ICON = 44
ROW_PAD_X = 14
ROW_PAD_Y = 11
BADGE = 22
BADGE_GAP = 12
FIELD_H = 26
GAP_FIELD = 8
BTN_H = 26
BTN_MIN_W = 84
BTN_GAP = 8
CARD_PAD = 12
CARD_GAP = 10
GLYPH = 18


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


def glyph_images():
    """The four menu-bar glyphs — the real assets the menu bar uses
    (tools/puckd/assets/menubar-*.png) — as template images at GLYPH pt, in
    the order the card describes them: faded, solid, line, dot. The @2x file
    is preferred so the strip is crisp; whatever is missing is simply absent
    and the card still renders."""
    from pathlib import Path

    from AppKit import NSImage, NSMakeSize

    assets = Path(__file__).resolve().parent / "assets"
    out = []
    for stem in ("menubar-dormant", "menubar", "menubar-working", "menubar-attention"):
        img = None
        for name in (stem + "@2x.png", stem + ".png"):
            path = assets / name
            if path.is_file():
                img = NSImage.alloc().initWithContentsOfFile_(str(path))
                if img is not None:
                    break
        if img is not None:
            img.setTemplate_(True)
            img.setSize_(NSMakeSize(GLYPH, GLYPH))
            out.append(img)
    return out


_WINDOW_CLASS = None


def build_window_class():
    """Import AppKit and build the window class. Only the real app calls
    this; tests drive OnboardingModel directly. Built once and cached: a
    second definition of an Objective-C class with the same name raises."""
    global _WINDOW_CLASS
    if _WINDOW_CLASS is not None:
        return _WINDOW_CLASS

    from AppKit import (NSApp, NSBackingStoreBuffered, NSBezelStyleRounded,
                        NSBox, NSBoxCustom, NSButton, NSColor,
                        NSControlSizeSmall, NSFont, NSFontAttributeName,
                        NSFontWeightBold, NSFontWeightMedium,
                        NSFontWeightSemibold, NSForegroundColorAttributeName,
                        NSImageScaleProportionallyUpOrDown, NSImageView,
                        NSMakeRect, NSMakeSize, NSNoTitle, NSProgressIndicator,
                        NSProgressIndicatorStyleSpinning, NSSecureTextField,
                        NSTextField, NSTextAlignmentCenter, NSView, NSWindow,
                        NSWindowStyleMaskClosable, NSWindowStyleMaskTitled)
    import objc
    from Foundation import NSAttributedString, NSObject
    from PyObjCTools import AppHelper

    def box(width, height, fill=None, border=None, radius=0.0, border_w=1.0):
        b = NSBox.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        b.setBoxType_(NSBoxCustom)
        b.setTitlePosition_(NSNoTitle)
        b.setContentViewMargins_(NSMakeSize(0, 0))
        b.setBorderWidth_(border_w if border is not None else 0.0)
        b.setCornerRadius_(radius)
        b.setFillColor_(fill if fill is not None else NSColor.clearColor())
        if border is not None:
            b.setBorderColor_(border)
        return b

    class OnboardingWindow(NSObject):
        """Owns one NSWindow and re-lays it out from model.screen()."""

        def initWithModel_(self, model):
            self = objc.super(OnboardingWindow, self).init()
            if self is None:
                return None
            self.model = model
            self.window = None
            self.inputs = []
            self._actions = []
            self._typed = {}        # placeholder -> what he typed, kept across renders
            self._shown = False
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
                # setup the same way Done does (on_finished, which quits
                # `python -m puckd setup` and is a no-op inside the daemon).
                self.window.setDelegate_(self)
            elif self._shown:
                # reopened from "Set up…": show what is true NOW.
                self.model.reopen()
            self._shown = True
            self.render()
            if first:
                self.window.center()
            NSApp.activateIgnoringOtherApps_(True)
            self.window.makeKeyAndOrderFront_(None)

        # ---- little builders
        @objc.python_method
        def _label(self, text, size, weight, color, width, wrap=True, align=None):
            f = (NSFont.systemFontOfSize_weight_(size, weight) if weight is not None
                 else NSFont.systemFontOfSize_(size))
            tf = (NSTextField.wrappingLabelWithString_(text) if wrap
                  else NSTextField.labelWithString_(text))
            tf.setFont_(f)
            tf.setTextColor_(color)
            tf.setSelectable_(False)
            if align is not None:
                tf.setAlignment_(align)
            tf.setPreferredMaxLayoutWidth_(width)
            h = float(tf.fittingSize().height)
            tf.setFrameSize_(NSMakeSize(width, h))
            return tf, h

        @objc.python_method
        def _tone_color(self, tone):
            if tone == "ok":
                return NSColor.systemGreenColor()
            if tone == "plain":
                return NSColor.secondaryLabelColor()
            return NSColor.tertiaryLabelColor()

        @objc.python_method
        def _button(self, btn, index):
            """One control for one model Button. Returns (view, width)."""
            b = NSButton.buttonWithTitle_target_action_(btn.label, self, "press:")
            b.setTag_(index)
            b.setBezelStyle_(NSBezelStyleRounded)
            b.setFont_(NSFont.systemFontOfSize_(13))
            b.sizeToFit()
            width = float(b.frame().size.width)
            if btn.style == "text":
                b.setBordered_(False)
                b.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
                    btn.label, {NSForegroundColorAttributeName: NSColor.controlAccentColor(),
                                NSFontAttributeName: NSFont.systemFontOfSize_(13)}))
                b.setKeyEquivalent_("")          # Escape must never fire Skip
                width += 6
            elif btn.style == "primary":
                b.setKeyEquivalent_("\r")
                b.setBezelColor_(NSColor.controlAccentColor())
                width = max(width + 16, BTN_MIN_W)
            else:
                width = max(width + 12, BTN_MIN_W)
            b.setFrameSize_(NSMakeSize(width, BTN_H))
            return b, width

        @objc.python_method
        def _spinner(self):
            sp = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0, 0, 16, 16))
            sp.setStyle_(NSProgressIndicatorStyleSpinning)
            sp.setControlSize_(NSControlSizeSmall)
            sp.setDisplayedWhenStopped_(False)
            sp.startAnimation_(None)
            return sp

        # ---- the checklist row
        @objc.python_method
        def _row_view(self, row, width):
            """One checklist row, laid out bottom-up in its own view."""
            btn, btn_w = (None, 0.0)
            if row.button is not None:
                btn, btn_w = self._button(row.button, len(self._actions))
                self._actions.append((row.button.action, False))
            spinner = self._spinner() if row.busy else None
            right = btn_w if btn is not None else (18.0 if spinner is not None else 0.0)
            text_x = ROW_PAD_X + BADGE + BADGE_GAP
            text_w = width - text_x - ROW_PAD_X - (right + BADGE_GAP if right else 0)

            title, th = self._label(row.title, 13.5, NSFontWeightSemibold,
                                    NSColor.labelColor(), text_w, wrap=False)
            sub, sh = self._label(row.subtitle, 12, None,
                                  self._tone_color(row.tone), text_w)

            # the inline Garmin form, under the text, aligned with it
            exp_w = width - text_x - ROW_PAD_X
            fields, acts = [], []
            exp_h = 0.0
            for f in row.fields:
                cls = NSSecureTextField if f.secure else NSTextField
                tf = cls.alloc().initWithFrame_(NSMakeRect(0, 0, exp_w, FIELD_H))
                tf.setPlaceholderString_(f.placeholder)
                tf.setStringValue_(self._typed.get(f.placeholder, ""))
                tf.setFont_(NSFont.systemFontOfSize_(13))
                tf.setAccessibilityLabel_(f.placeholder)
                tf.setTarget_(self)
                tf.setAction_("fieldReturn:")     # Return in a field = the primary
                fields.append(tf)
                exp_h += FIELD_H + GAP_FIELD
            if row.actions:
                for b in row.actions:
                    view, w = self._button(b, len(self._actions))
                    self._actions.append((b.action, True))
                    acts.append((view, w))
                exp_h += BTN_H + 2

            body_h = th + 2 + sh + (GAP_FIELD + exp_h if exp_h else 0)
            height = max(body_h, BADGE) + 2 * ROW_PAD_Y
            view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))

            y = height - ROW_PAD_Y - th
            title.setFrameOrigin_((text_x, y))
            view.addSubview_(title)
            y -= 2 + sh
            sub.setFrameOrigin_((text_x, y))
            view.addSubview_(sub)

            top_h = th + 2 + sh
            badge_y = height - ROW_PAD_Y - (top_h + BADGE) / 2.0 if top_h > BADGE else height - ROW_PAD_Y - BADGE
            green = NSColor.systemGreenColor()
            bg = box(BADGE, BADGE,
                     fill=green if row.done else NSColor.clearColor(),
                     border=green if row.done else NSColor.tertiaryLabelColor(),
                     radius=BADGE / 2.0, border_w=1.5)
            bg.setFrameOrigin_((ROW_PAD_X, badge_y))
            view.addSubview_(bg)
            mark, mh = self._label(row.badge, 12,
                                   NSFontWeightBold if row.done else NSFontWeightMedium,
                                   NSColor.whiteColor() if row.done else NSColor.secondaryLabelColor(),
                                   BADGE, wrap=False, align=NSTextAlignmentCenter)
            mark.setFrameOrigin_((ROW_PAD_X, badge_y + (BADGE - mh) / 2.0))
            view.addSubview_(mark)

            ctl_y = height - ROW_PAD_Y - (top_h + BTN_H) / 2.0
            if btn is not None:
                btn.setFrameOrigin_((width - ROW_PAD_X - btn_w, ctl_y))
                view.addSubview_(btn)
            elif spinner is not None:
                spinner.setFrameOrigin_((width - ROW_PAD_X - 16, ctl_y + 5))
                view.addSubview_(spinner)

            y -= GAP_FIELD
            for tf in fields:
                y -= FIELD_H
                tf.setFrameOrigin_((text_x, y))
                view.addSubview_(tf)
                y -= GAP_FIELD
                self.inputs.append(tf)
            if acts:
                y -= BTN_H
                x = width - ROW_PAD_X
                for view_b, w in reversed(acts):     # primary is last = rightmost
                    x -= w
                    view_b.setFrameOrigin_((x, y))
                    view.addSubview_(view_b)
                    x -= BTN_GAP
            return view, height

        # ---- the "how it works" cards
        @objc.python_method
        def _natural(self, text, size, weight):
            """How wide this line wants to be, unwrapped — so the little
            pieces of UI below are sized to their content and never clip."""
            tf, _h = self._label(text, size, weight, NSColor.labelColor(), 4000, wrap=False)
            return float(tf.fittingSize().width)

        @objc.python_method
        def _glyph_strip(self):
            imgs = glyph_images()
            w = max(len(imgs) * GLYPH + (len(imgs) - 1) * 12, GLYPH) if imgs else 0.0
            strip = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, w, GLYPH))
            x = 0.0
            for img in imgs:
                iv = NSImageView.alloc().initWithFrame_(NSMakeRect(x, 0, GLYPH, GLYPH))
                iv.setImage_(img)
                iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                iv.setContentTintColor_(NSColor.labelColor())
                strip.addSubview_(iv)
                x += GLYPH + 12
            return strip, w, GLYPH

        @objc.python_method
        def _notif_view(self):
            h = 30.0
            icon = app_icon()
            x = 8.0 + ((18 + 7) if icon is not None else 0)
            w = x + self._natural(CARD_NOTIF_SAMPLE, 11.5, NSFontWeightSemibold) + 10
            b = box(w, h, fill=NSColor.controlBackgroundColor(),
                    border=NSColor.separatorColor(), radius=8.0)
            if icon is not None:
                iv = NSImageView.alloc().initWithFrame_(NSMakeRect(8, (h - 18) / 2.0, 18, 18))
                iv.setImage_(icon)
                iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                iv.setWantsLayer_(True)
                iv.layer().setCornerRadius_(4.0)
                iv.layer().setMasksToBounds_(True)
                b.addSubview_(iv)
            lbl, lh = self._label(CARD_NOTIF_SAMPLE, 11.5, NSFontWeightSemibold,
                                  NSColor.labelColor(), w - x - 8, wrap=False)
            lbl.setFrameOrigin_((x, (h - lh) / 2.0))
            b.addSubview_(lbl)
            return b, w, h

        @objc.python_method
        def _menu_view(self):
            w = max(self._natural(t, 11, None) for t in CARD_MENU_LINES if t != "—") + 22
            lines = []
            total = 8.0
            for text in CARD_MENU_LINES:
                if text == "—":
                    lines.append((None, 7.0))
                    total += 7.0
                    continue
                lbl, lh = self._label(text, 11, None, NSColor.labelColor(), w - 20, wrap=False)
                lines.append((lbl, lh + 2))
                total += lh + 2
            b = box(w, total, fill=NSColor.controlBackgroundColor(),
                    border=NSColor.separatorColor(), radius=7.0)
            y = total - 4.0
            for lbl, h in lines:
                y -= h
                if lbl is None:
                    sep = box(w, 1.0, fill=NSColor.separatorColor())
                    sep.setFrameOrigin_((0, y + 3))
                    b.addSubview_(sep)
                    continue
                lbl.setFrameOrigin_((11, y))
                b.addSubview_(lbl)
            return b, w, total

        @objc.python_method
        def _card_art(self, card):
            if card.art == "glyphs":
                return self._glyph_strip()
            if card.art == "notification":
                return self._notif_view()
            return self._menu_view()

        @objc.python_method
        def _card_view(self, card, width, art_col, art):
            art_view, art_w, art_h = art
            text_w = width - 2 * CARD_PAD - art_col - CARD_GAP
            title, th = self._label(card.title, 13, NSFontWeightSemibold,
                                    NSColor.labelColor(), text_w)
            body, bh = self._label(card.body, 11.5, None,
                                   NSColor.secondaryLabelColor(), text_w)
            text_h = th + 2 + bh
            height = max(text_h, art_h) + 2 * CARD_PAD
            b = box(width, height, fill=NSColor.controlBackgroundColor(),
                    border=NSColor.separatorColor(), radius=10.0)
            art_view.setFrameOrigin_((CARD_PAD + (art_col - art_w) / 2.0,
                                      (height - art_h) / 2.0))
            b.addSubview_(art_view)
            ty = (height + text_h) / 2.0
            title.setFrameOrigin_((CARD_PAD + art_col + CARD_GAP, ty - th))
            body.setFrameOrigin_((CARD_PAD + art_col + CARD_GAP, ty - th - 2 - bh))
            b.addSubview_(title)
            b.addSubview_(body)
            return b, height

        # ---- the whole window
        @objc.python_method
        def _resize(self, height):
            """Grow or shrink to the content, keeping the TOP edge still so
            the window does not hop up the screen between states."""
            win = self.window
            new = win.frameRectForContentRect_(NSMakeRect(0, 0, W, height))
            old = win.frame()
            top = old.origin.y + old.size.height
            win.setFrame_display_(
                NSMakeRect(old.origin.x, top - new.size.height,
                           new.size.width, new.size.height), True)

        def render(self):
            scr = self.model.screen()
            for f in self.inputs:                     # keep what he typed
                self._typed[str(f.placeholderString() or "")] = str(f.stringValue())
            if self.model.garmin in ("done", "skipped"):
                self._typed.pop(PLACEHOLDER_PASSWORD, None)
                self._typed.pop(PLACEHOLDER_CODE, None)
            content = self.window.contentView()
            for v in list(content.subviews()):
                v.removeFromSuperview()
            self.inputs = []
            self._actions = []
            inner = W - 2 * PAD
            blocks = []                                # (view, height, gap below)

            # brand row: the icon, the name, the one-line what-this-is
            icon = app_icon()
            tx = (ICON + 14) if icon is not None else 0
            btitle, bth = self._label(scr.title, 19, NSFontWeightBold,
                                      NSColor.labelColor(), inner - tx, wrap=False)
            bsub, bsh = self._label(scr.subtitle, 12.5, None,
                                    NSColor.secondaryLabelColor(), inner - tx, wrap=False)
            brand_h = max(ICON if icon is not None else 0, bth + 2 + bsh)
            brand = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, inner, brand_h))
            if icon is not None:
                iv = NSImageView.alloc().initWithFrame_(
                    NSMakeRect(0, (brand_h - ICON) / 2.0, ICON, ICON))
                iv.setImage_(icon)
                iv.setImageScaling_(NSImageScaleProportionallyUpOrDown)
                iv.setAccessibilityLabel_(WINDOW_TITLE)
                brand.addSubview_(iv)
            stack_h = bth + 2 + bsh
            btitle.setFrameOrigin_((tx, (brand_h + stack_h) / 2.0 - bth))
            bsub.setFrameOrigin_((tx, (brand_h + stack_h) / 2.0 - bth - 2 - bsh))
            brand.addSubview_(btitle)
            brand.addSubview_(bsub)
            blocks.append((brand, brand_h, GAP_BRAND))

            if scr.intro:
                intro, ih = self._label(scr.intro, 12.5, None,
                                        NSColor.secondaryLabelColor(), inner)
                blocks.append((intro, ih, GAP_INTRO))

            if scr.rows:
                views = [self._row_view(r, inner) for r in scr.rows]
                total = sum(h for _v, h in views) + (len(views) - 1)
                group = box(inner, total, fill=NSColor.controlBackgroundColor(),
                            border=NSColor.separatorColor(), radius=10.0)
                y = total
                for i, (v, h) in enumerate(views):
                    y -= h
                    v.setFrameOrigin_((0, y))
                    group.addSubview_(v)
                    if i < len(views) - 1:
                        y -= 1
                        sep = box(inner, 1.0, fill=NSColor.separatorColor())
                        sep.setFrameOrigin_((0, y))
                        group.addSubview_(sep)
                blocks.append((group, total, GAP_FOOT))

            if scr.cards:
                # every card's art is measured first, so the three of them
                # share one column and the text starts at the same x.
                arts = [self._card_art(c) for c in scr.cards]
                art_col = max(w for _v, w, _h in arts)
                for i, (card, art) in enumerate(zip(scr.cards, arts)):
                    v, h = self._card_view(card, inner, art_col, art)
                    blocks.append((v, h, GAP_FOOT if i == len(scr.cards) - 1 else 8))

            # footer: the quiet line on the left, the buttons on the right
            fbtns = []
            for b in scr.footer_buttons:
                view, w = self._button(b, len(self._actions))
                self._actions.append((b.action, False))
                fbtns.append((view, w))
            btn_w = sum(w for _v, w in fbtns) + BTN_GAP * max(len(fbtns) - 1, 0)
            foot_text_w = inner - (btn_w + 12 if btn_w else 0)
            flbl, flh = self._label(scr.footer_left, 11.5, None,
                                    NSColor.tertiaryLabelColor(), foot_text_w)
            foot_h = max(flh, BTN_H if fbtns else 0)
            foot = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, inner, foot_h))
            flbl.setFrameOrigin_((0, (foot_h - flh) / 2.0))
            foot.addSubview_(flbl)
            x = inner
            for view, w in reversed(fbtns):
                x -= w
                view.setFrameOrigin_((x, (foot_h - BTN_H) / 2.0))
                foot.addSubview_(view)
                x -= BTN_GAP
            blocks.append((foot, foot_h, 0))

            height = TOP + BOTTOM + sum(h + g for _v, h, g in blocks)
            self._resize(height)
            y = height - TOP
            for view, h, gap in blocks:
                y -= h
                view.setFrameOrigin_((PAD, y))
                content.addSubview_(view)
                y -= gap

            # keyboard: the fields in order, then the primary (which already
            # carries Return as its key equivalent).
            if self.inputs:
                for a, nxt in zip(self.inputs, self.inputs[1:]):
                    a.setNextKeyView_(nxt)
                self.window.makeFirstResponder_(self.inputs[0])

        # ---- actions
        def windowWillClose_(self, notification):
            self.model.done()

        def fieldReturn_(self, sender):
            for i, (action, _inline) in enumerate(self._actions):
                if action == "garmin_signin":
                    self._fire(i)
                    return

        def press_(self, sender):
            self._fire(sender.tag())

        @objc.python_method
        def _fire(self, index):
            if index >= len(self._actions):
                return
            action, _inline = self._actions[index]
            values = [str(f.stringValue()) for f in self.inputs]
            if action in ("done", "skip_all"):
                self.window.orderOut_(None)
                self.model.act(action)
                return
            if action in OnboardingModel.SLOW:
                self._run_async(action, values)
                return
            self.model.act(action, values)
            self.render()

        @objc.python_method
        def _run_async(self, action, values):
            if action == "google":
                self.model.google = "working"
            else:
                self.model.garmin = "working"
            self.render()

            def work():
                try:
                    self.model.act(action, values)
                finally:
                    AppHelper.callAfter(self.render)
            threading.Thread(target=work, daemon=True).start()

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
                        on_finished=on_finished,
                        google_is_authorized=upload.is_authorized,
                        garmin_is_signed_in=garmin.is_signed_in)
    m.start(google_connected=_safe(upload.is_authorized),
            garmin_signed_in=_safe(garmin.is_signed_in))
    return m


def _safe(fn) -> bool:
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001
        return False
