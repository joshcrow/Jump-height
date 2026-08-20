#!/usr/bin/env bash
# bench-guard — inject board ground truth when a command is about to talk to a
# puck, BEFORE the first reading rather than after the wrong conclusion.
#
# WHY THIS EXISTS (2026-08-20). spec-guard.sh catches predefined work when a
# FILE is edited. That is the right trigger for code and the wrong one for
# hardware: this morning I reasoned my way to a "hardware fault" verdict on a
# board that simply has no battery, and never edited a file with a spec, so
# nothing fired. The fact was correctly written in docs/STATUS.md two days
# earlier AND in the board registry. Two correct records, reached by neither,
# because the moment of the mistake was a Bash call, not an Edit.
#
# The trigger that matches the failure is therefore: a command that is about to
# read a board. Two conditions are worth interrupting for.
#
#   1. AMBIGUITY — a BLE tool invoked without --name/--addr. Two pucks
#      advertise; the default name is the bare prefix both match, so an
#      unpinned call is a coin flip that has now corrupted two analyses.
#      (blecmd.py also warns at runtime; this fires earlier, while the command
#      can still be changed rather than re-run.)
#
#   2. POWER CLAIMS FROM THE WRONG BOARD — anything reading battery state
#      needs to know that only the OG has a cell, and that the spare's
#      vbat/batt_pct are a floating divider: plausible numbers meaning nothing.
#
# It INFORMS, never blocks — a wrong guard that stops work is worse than a
# missed one. Silent when the command is already pinned, so being correct costs
# nothing.
#
# SPDX-License-Identifier: MIT

set -uo pipefail

command -v jq >/dev/null 2>&1 || exit 0   # no jq: stay silent, never break work

input="$(cat)"
cmd="$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)"
[ -n "$cmd" ] || exit 0

# Does this command talk to a puck over BLE?
case "$cmd" in
  *blecmd.py*|*battlog.py*|*otadfu.py*|*dualcentral.py*|*hostsoak.py*) ;;
  *) exit 0 ;;
esac

# Already pinned to one board, or merely scanning/listing? Then it is correct
# by construction and deserves no interruption.
case "$cmd" in
  *--name\ JumpHeight-*|*--addr\ *|*--scan*) exit 0 ;;
esac

# `jump boards` is the tool that ANSWERS this question — never nag it.
case "$cmd" in
  *jump\ boards*) exit 0 ;;
esac

# Testing or editing the guard itself MENTIONS the tool names without running
# them. Caught on the first live fire, 2026-08-20: a guard that cries wolf
# while being tested trains you to skim it, which is how a real warning gets
# missed later.
case "$cmd" in
  *bench-guard.sh*) exit 0 ;;
esac

read -r -d '' MSG <<'EOF'
BENCH CHECK — this command reads a puck over BLE and is NOT pinned to a board.

TWO BOARDS ADVERTISE. An unpinned call answers from whichever replies first,
and it has landed differently on consecutive calls. That is not hypothetical:
it put a floating 97% into a death-run log, and it caused a whole DC/DC result
to be attributed to the wrong board (2026-08-20).

  JumpHeight-E2C4   the OG      BATTERY: YES (pigtail soldered)
                                The ONLY board that can measure power.
  JumpHeight-45ED   the spare   BATTERY: NO  (USB only)
                                Its vbat_mv / batt_pct are a FLOATING divider:
                                plausible-looking numbers that mean nothing.
                                Seen swinging 400 mV in four seconds.

DO THIS: add  --name JumpHeight-E2C4  (or --addr) to pin the board you mean.

RULES THAT FOLLOW MECHANICALLY:
  * Drain, endurance, DC/DC and any untethered test are meaningful ONLY on the
    OG. A power figure from the spare is noise, not a measurement.
  * Never conclude "hardware fault" from a reading before establishing the
    board's CONFIGURATION. Three such verdicts have been wrong.
  * `./tools/jump boards` reports ground truth from the hardware and flags
    floating batteries — prefer it over trusting any document, including this
    message.

Registry: docs/bench-playbook.md §1
EOF

jq -n --arg m "$MSG" '
{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    additionalContext: $m
  }
}'
exit 0
