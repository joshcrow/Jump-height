"""The three notifications and no others (docs/sync-agent-plan.md, "Three
notifications exist and no others") — plus the firmware-flash event from the
job steps, which is a fourth `notify()` kind even though the top-of-spec list
only names three:

    synced     -> "Ride synced · N jumps"  /  "Ride synced · no jumps"
    charged    -> "Puck charged"
    needs_you  -> "Needs you: <line>"      body: <action>
    updated    -> "Puck updated"           (spec line 54, after a flash)

Every string this module can produce is quoted from the spec — nothing here
is invented copy. `render()` is pure (no I/O) so tests assert the exact
strings without a notification ever appearing; `notify()` fires the result
through an injectable `runner`, defaulting to a real `osascript display
notification` call.
"""

from __future__ import annotations

import subprocess
from typing import Callable, Optional, Tuple

KINDS = ("synced", "charged", "needs_you", "updated")

Runner = Callable[[str, Optional[str]], None]


def render(kind: str, **fields) -> Tuple[str, Optional[str]]:
    """Build (title, body) for one notification `kind`. Pure — no
    subprocess, no osascript — so this is what tests assert against."""
    if kind == "synced":
        jumps = fields["jumps"]
        title = "Ride synced · no jumps" if jumps == 0 else f"Ride synced · {jumps} jumps"
        return title, None

    if kind == "charged":
        return "Puck charged", None

    if kind == "needs_you":
        # Spec: "Needs you: <one line>   body: the single action" — the
        # "Needs you: " prefix is the notification title, `line` fills the
        # rest of it, `action` is the whole body verbatim.
        return f"Needs you: {fields['line']}", fields["action"]

    if kind == "updated":
        return "Puck updated", None

    raise ValueError(f"unknown notification kind: {kind!r} (expected one of {KINDS})")


def _osascript_escape(text: str) -> str:
    """Quote `text` as an AppleScript string literal."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def osascript_runner(title: str, body: Optional[str]) -> None:
    """The real runner: fire a macOS notification via osascript. Never
    called from tests — they pass their own `runner` to notify()."""
    script = "display notification {body} with title {title}".format(
        body=_osascript_escape(body or ""),
        title=_osascript_escape(title),
    )
    subprocess.run(["osascript", "-e", script], check=False)


def notify(kind: str, runner: Runner = osascript_runner, **fields) -> Tuple[str, Optional[str]]:
    """Render `kind` from `fields` and fire it through `runner(title,
    body)`. Returns the (title, body) pair that was sent, so callers and
    tests can inspect exactly what went out."""
    title, body = render(kind, **fields)
    runner(title, body)
    return title, body
