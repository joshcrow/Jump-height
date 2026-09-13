"""`python -m puckd` -- three entry points, docs/sync-agent-plan.md's own
build note (P1's setup module row): "daemon.py: the loop... __main__: `python
-m puckd` runs the daemon; `python -m puckd setup` opens the setup page;
`python -m puckd once` runs one job for the bench and exits with a
plain-text report."

All three are thin: every actual decision lives in tools/puckd/daemon.py
(daemon.main(), daemon.run_setup(), daemon.run_once_report()) so this file
stays a dispatcher, not a second place the job/gates could drift from the
spec.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "tools"))  # `from puckd import daemon`

from puckd import daemon  # noqa: E402

USAGE = "usage: python -m puckd [setup|once [PORT]]"


def main(argv: "list[str] | None" = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    cmd = argv[0] if argv else "daemon"

    if cmd in ("-h", "--help"):
        print(USAGE)
        return 0

    if cmd == "setup":
        daemon.run_setup()
        return 0

    if cmd == "once":
        port = argv[1] if len(argv) > 1 else None
        cfg = daemon.build_config()
        print(daemon.run_once_report(port, cfg))
        return 0

    if cmd == "daemon":
        daemon.main()
        return 0

    print(f"{USAGE}\nunknown command: {cmd!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
