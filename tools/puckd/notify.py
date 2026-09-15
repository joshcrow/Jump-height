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
import sys
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


def native_runner(title: str, body: Optional[str]) -> bool:
    """Post through the app bundle's own notification center, so the
    notification carries the app's name and icon instead of Script Editor's.
    NSUserNotificationCenter is deprecated but present through macOS 26 and
    needs no signing or authorization prompt (measured 2026-09-15: delivered
    from the installed, unsigned bundle with bundle id com.jumpheight.puckd).
    Returns False when the framework is unavailable (a dev checkout, Linux
    CI, or Apple finally removing it), so the caller can fall back."""
    try:
        from Foundation import NSUserNotification, NSUserNotificationCenter  # type: ignore
        center = NSUserNotificationCenter.defaultUserNotificationCenter()
        if center is None:
            return False
        n = NSUserNotification.alloc().init()
        n.setTitle_(title)
        if body:
            n.setInformativeText_(body)
        center.deliverNotification_(n)
        return True
    except Exception:  # noqa: BLE001 -- any failure here means "use osascript"
        return False


def osascript_runner(title: str, body: Optional[str]) -> None:
    """The real runner. First the bundle's own notification center (the
    app's name and icon); if that is unavailable, `osascript display
    notification`, which Notification Centre attributes to Script Editor.
    Never called from tests -- they pass their own `runner` to notify()."""
    if native_runner(title, body):
        return
    script = "display notification {body} with title {title}".format(
        body=_osascript_escape(body or ""),
        title=_osascript_escape(title),
    )
    proc = subprocess.run(["osascript", "-e", script], check=False,
                          capture_output=True, encoding="utf-8", errors="replace")
    # A notification that did not appear must not look like one that did
    # (CLAUDE.md 2.3). Nothing is retried -- by the time this fires the ride
    # is already safe on Drive -- but the daemon's log says it was lost
    # instead of swallowing the exit code.
    code = getattr(proc, "returncode", 0)
    if code:
        err = (getattr(proc, "stderr", "") or "").strip().splitlines()
        detail = f": {err[-1]}" if err else ""
        print(f"notify: osascript exited {code}, {title!r} was not delivered{detail}",
              file=sys.stderr)


def notify(kind: str, runner: Runner = osascript_runner, **fields) -> Tuple[str, Optional[str]]:
    """Render `kind` from `fields` and fire it through `runner(title,
    body)`. Returns the (title, body) pair that was sent, so callers and
    tests can inspect exactly what went out."""
    title, body = render(kind, **fields)
    runner(title, body)
    return title, body
