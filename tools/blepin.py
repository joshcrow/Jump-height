"""Shared board-pinning census for the bench tools.

Every puck on this bench advertises a name starting "JumpHeight". Anything
built on `BleakScanner.find_device_by_filter` therefore selects *whichever
board answers first*, which is a coin flip that lands differently on
consecutive calls. That has already put a floating divider's confident 97 %
into a death-run log, attributed a DC/DC result to the wrong board, and — on
2026-08-12 — flashed the WRONG BOARD over the air, with the post-flash "the app
is back" check fooled by the same collision.

`tools/blecmd.py` solved this on 2026-08-20 by collecting every match instead
of taking the first. This module is that logic, lifted out so the other tools
use it rather than growing their own near-copies (audit F-13/F-14, 2026-08-22).

The split matters:

  census()  — talks to the radio, never short-circuits, returns everything.
  resolve() — pure. Given the matches, decides what to do and says so.

resolve() being pure is deliberate: the decision is the part that has been
wrong three times, and it is the part a unit test can actually reach without a
radio or a second board on the desk.

AMBIGUITY POLICY IS THE CALLER'S CHOICE, because the cost of guessing wrong is
not the same everywhere:

  "choose"  — warn loudly, then pick deterministically (lowest name) so a
              script at least behaves the same way twice. For READS, which are
              recoverable: you can always take the measurement again.
  "refuse"  — warn and stop. For anything that WRITES to a board — a firmware
              flash above all. A wrong read is a bad number; a wrong flash is
              a bricked board you may not be able to recover, and "take it
              again" is not available.

SPDX-License-Identifier: MIT
"""
from __future__ import annotations

import sys


class AmbiguousBoards(RuntimeError):
    """More than one board matched and the caller refuses to guess."""


class NoBoardFound(RuntimeError):
    """Nothing matched. Raised, not sys.exit()'d, so a --watch loop can treat
    it as a retryable gap (out of range, momentarily not advertising) rather
    than the end of the run."""


async def census(find_device_by_filter, name: str, addr: str | None = None,
                 seconds: float = 8.0) -> dict:
    """Every advertiser matching `name` (prefix) or `addr` (prefix).

    `find_device_by_filter` is passed in rather than imported so this is
    testable without bleak or a radio — the stub just calls the filter with
    canned devices.

    Returns {address: (device, advertised_name, rssi)}.
    """
    matches: dict = {}

    def _seen(d, adv):
        nm = (getattr(adv, "local_name", None) or d.name or "")
        if addr:
            ok = d.address.lower().startswith(addr.lower())
        else:
            ok = nm.lower().startswith(name.lower())
        if ok:
            matches[d.address] = (d, nm, getattr(adv, "rssi", None))
        # Never short-circuit: returning True here is exactly the bug. We want
        # the full census, not the first answer.
        return False

    await find_device_by_filter(_seen, timeout=seconds)
    return matches


def listing(matches: dict) -> list:
    """Matches as sorted (name, address, rssi) rows — stable for display and
    for choosing deterministically."""
    return sorted((nm or "(unnamed)", d.address, rssi)
                  for d, nm, rssi in matches.values())


def resolve(matches: dict, name: str, *, tool: str, on_ambiguous: str = "choose",
            stream=None):
    """Pure decision over a census. Returns the chosen device.

    Raises NoBoardFound if nothing matched, and AmbiguousBoards if several did
    and `on_ambiguous` is "refuse".
    """
    if stream is None:
        stream = sys.stderr
    if on_ambiguous not in ("choose", "refuse"):
        raise ValueError(f"on_ambiguous must be 'choose' or 'refuse', got {on_ambiguous!r}")

    if not matches:
        raise NoBoardFound(
            f"{tool}: no '{name}' found — is it awake and in range? "
            f"(a puck in System OFF does not advertise; `./tools/jump boards` "
            f"lists what is actually up)")

    if len(matches) == 1:
        return next(iter(matches.values()))[0]

    rows = listing(matches)
    print(f"\n⚠️  {len(matches)} boards match '{name}' — this call is AMBIGUOUS:",
          file=stream)
    for nm, adr, rssi in rows:
        print(f"      {nm:22} {adr}  rssi={rssi}", file=stream)
    print(f"    Pin the one you mean:  --name {rows[0][0]}   (or --addr <prefix>)",
          file=stream)
    print(f"    Unpinned calls have corrupted two analyses and flashed one "
          f"wrong board.\n", file=stream)

    if on_ambiguous == "refuse":
        raise AmbiguousBoards(
            f"{tool}: refusing to act on an ambiguous match. This operation "
            f"WRITES to the board, and picking wrong is not something you can "
            f"undo by running it again — pin it with --name or --addr.")

    chosen = min(matches.values(), key=lambda t: (t[1] or "", t[0].address))
    print(f"    proceeding with {chosen[1]} (lowest name, chosen "
          f"deterministically)\n", file=stream)
    return chosen[0]
