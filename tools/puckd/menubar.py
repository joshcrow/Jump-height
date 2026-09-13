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

ICON_IDLE = "JH"
ICON_ATTENTION = "JH!"


def format_puck_line(puck_pct: Optional[int], charging: bool) -> str:
    """"Puck 86% · charging" — `charging` False drops the suffix. `None`
    (no reading yet) renders as "Puck —"."""
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


def format_icon_title(attention: bool) -> str:
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
                     opener: Opener = default_opener):
            super().__init__("JumpHeight", title=format_icon_title(False))
            self._spool_dir = Path(spool_dir)
            self._setup_url = setup_url
            self._opener = opener

            self._status_puck = rumps.MenuItem(format_puck_line(None, False))
            self._status_ride = rumps.MenuItem(format_ride_line(None, None))
            open_folder = rumps.MenuItem("Open rides folder", callback=self._open_rides_folder)
            set_up = rumps.MenuItem("Set up…", callback=self._open_setup)

            # rumps.App appends its own "Quit" item after these four.
            self.menu = [self._status_puck, self._status_ride, open_folder, set_up]

        def set_state(self, puck_pct: Optional[int], charging: bool,
                      last_ride_dt: Optional[_dt.datetime],
                      last_jumps: Optional[int], attention: bool) -> None:
            """Rewrite the two status lines and the icon title. Called
            after every stats read and every job."""
            self._status_puck.title = format_puck_line(puck_pct, charging)
            self._status_ride.title = format_ride_line(last_ride_dt, last_jumps)
            self.title = format_icon_title(attention)

        def _open_rides_folder(self, _sender) -> None:
            self._opener(str(self._spool_dir))

        def _open_setup(self, _sender) -> None:
            self._opener(self._setup_url)

    return PuckdApp


def make_app(*args, **kwargs):
    """Build and return a running-ready PuckdApp instance. Requires rumps
    to be installed; call this only from the real daemon, never from tests."""
    return build_app_class()(*args, **kwargs)
