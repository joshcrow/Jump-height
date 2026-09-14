"""The menu-bar icon and its menu — exactly the five lines in the spec
(docs/sync-agent-plan.md, "A menu-bar icon shows state at a glance"):

    Puck 86% · charging
    Last ride Tue 4:52 pm · 12 jumps
    Open rides folder
    Set up…
    Quit

The two status lines and the icon title are the only things that change at
runtime; `set_state()` rewrites them. "Quit" is rumps.App's own default menu
item, so the app only ever builds the other four.

`rumps` is imported lazily, inside `build_app_class()`/`make_app()`, so the
pure line-rendering functions below can be unit-tested — with fixed inputs,
per the build instructions — on any machine, with nothing shown and no menu
bar framework required at import time.

The exact glyphs used for the icon title (idle vs. attention) and the
fallback text for a None/never-happened value (e.g. "Puck —" before the
first stats read) are this module's own choice, not spec copy — the spec
fixes the *pattern*, not every corner case.
"""

from __future__ import annotations

import datetime as _dt
import subprocess
from pathlib import Path
from typing import Callable, Optional

SPOOL_DIR = Path.home() / "Library" / "Application Support" / "JumpHeight" / "spool"
DEFAULT_SETUP_URL = "http://127.0.0.1:17888/setup"

Opener = Callable[[str], None]

# The menu-bar glyph is one of four template images (black on transparent,
# macOS recolours them) in tools/puckd/assets/ -- the state is encoded by
# REDRAWING the arrow, never by a badge or a title, and there are exactly
# four states (docs/sync-agent-plan.md, the glyph table):
#     idle       the arrow             puck attached, up to date
#     dormant    the arrow at 35 %     no puck attached
#     working    arrow over a line     a job is running (the panel says which phase)
#     attention  arrow over a dot      Nick needs to do one thing
ASSETS = Path(__file__).resolve().parent / "assets"
ICON_FILES = {
    "idle": ASSETS / "menubar.png",
    "dormant": ASSETS / "menubar-dormant.png",
    "working": ASSETS / "menubar-working.png",
    "attention": ASSETS / "menubar-attention.png",
}
ICON_PATH = ICON_FILES["idle"]
ICON_IDLE = None          # no title beside the glyph, ever
ICON_ATTENTION = None     # the dot in the glyph carries it

# The panel's first line while a job runs: the phase in words, so 28 s of
# work never looks like a stall (NNG: visibility of system status).
PHASE_WORDS = {
    "reading": "Reading the puck\u2026",
    "uploading": "Uploading\u2026",
    "emptying": "Emptying the puck\u2026",
    "updating": "Updating the puck\u2026",
}
NO_PUCK = "No puck"


def glyph_state(attention: bool, phase: Optional[str], attached: bool) -> str:
    """Which of the four glyphs to show. Attention wins; then working; then
    whether a puck is there at all."""
    if attention:
        return "attention"
    if phase:
        return "working"
    return "idle" if attached else "dormant"


def format_puck_line(puck_pct: Optional[int], charging: bool,
                     phase: Optional[str] = None, attached: bool = True) -> str:
    """"Puck 86% · charging" — `charging` False drops the suffix. `None`
    (no reading yet) renders as "Puck —". While a job runs the line is the
    phase in words; with no puck attached it is "No puck"."""
    if phase:
        return PHASE_WORDS.get(phase, PHASE_WORDS["reading"])
    if not attached:
        return NO_PUCK
    pct_text = "Puck —" if puck_pct is None else f"Puck {puck_pct}%"
    return f"{pct_text} · charging" if charging else pct_text


def format_ride_time(dt: _dt.datetime) -> str:
    """"Tue 4:52 pm" — weekday, hour with no leading zero, minute, lower-
    case am/pm."""
    weekday = dt.strftime("%a")
    hour = dt.strftime("%I").lstrip("0") or "0"
    minute = dt.strftime("%M")
    ampm = dt.strftime("%p").lower()
    return f"{weekday} {hour}:{minute} {ampm}"


def format_ride_line(last_ride_dt: Optional[_dt.datetime], last_jumps: Optional[int]) -> str:
    """"Last ride Tue 4:52 pm · 12 jumps" (or "· no jumps", matching the
    synced-notification wording). No ride yet renders as "Last ride —"."""
    if last_ride_dt is None:
        return "Last ride —"
    time_text = format_ride_time(last_ride_dt)
    if not last_jumps:
        return f"Last ride {time_text} · no jumps"
    return f"Last ride {time_text} · {last_jumps} jumps"


def format_icon_title(attention: bool) -> "Optional[str]":
    """The menu-bar glyph: distinct for "needs you" vs. everything else."""
    return ICON_ATTENTION if attention else ICON_IDLE


def default_opener(target: str) -> None:
    """`open <target>` — a path opens in Finder, a URL in the browser."""
    subprocess.run(["open", target], check=False)


def build_app_class():
    """Import rumps and build the PuckdApp class. Only called when the app
    is actually going to run as a menu-bar process — never at module import
    time, and never by the tests for the pure functions above."""
    import rumps

    class PuckdApp(rumps.App):
        def __init__(self, spool_dir: Path = SPOOL_DIR,
                     setup_url: str = DEFAULT_SETUP_URL,
                     opener: Opener = default_opener,
                     on_setup: "Optional[Callable[[], None]]" = None):
            icon = str(ICON_FILES["dormant"]) if ICON_FILES["dormant"].is_file() else None
            super().__init__("JumpHeight", title=format_icon_title(False),
                             icon=icon, template=True)
            self._glyph = "dormant"
            self._spool_dir = Path(spool_dir)
            self._setup_url = setup_url
            self._opener = opener
            self._on_setup = on_setup          # the native window, when wired

            self._status_puck = rumps.MenuItem(format_puck_line(None, False))
            self._status_ride = rumps.MenuItem(format_ride_line(None, None))
            open_folder = rumps.MenuItem("Open rides folder", callback=self._open_rides_folder)
            set_up = rumps.MenuItem("Set up…", callback=self._open_setup)

            # rumps.App appends its own "Quit" item after these four.
            self.menu = [self._status_puck, self._status_ride, open_folder, set_up]

        def set_state(self, puck_pct: Optional[int], charging: bool,
                      last_ride_dt: Optional[_dt.datetime],
                      last_jumps: Optional[int], attention: bool,
                      phase: Optional[str] = None, attached: bool = True) -> None:
            """Rewrite the two status lines and swap the glyph. Called
            after every stats read, every phase change and every job."""
            self._status_puck.title = format_puck_line(puck_pct, charging, phase, attached)
            self._status_ride.title = format_ride_line(last_ride_dt, last_jumps)
            self.title = format_icon_title(attention)
            state = glyph_state(attention, phase, attached)
            if state != self._glyph:
                path = ICON_FILES[state]
                if path.is_file():
                    self.icon = str(path)
                self._glyph = state

        def _open_rides_folder(self, _sender) -> None:
            self._opener(str(self._spool_dir))

        def _open_setup(self, _sender) -> None:
            if self._on_setup is not None:
                self._on_setup()
            else:
                self._opener(self._setup_url)

    return PuckdApp


def make_app(*args, **kwargs):
    """Build and return a running-ready PuckdApp instance. Requires rumps
    to be installed; call this only from the real daemon, never from tests."""
    return build_app_class()(*args, **kwargs)
