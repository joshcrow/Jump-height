#!/usr/bin/env bash
# spec-guard — surface this repo's predefined work before an edit lands.
#
# WHY THIS EXISTS (2026-08-10): the gyro spin-correction work was built from a
# memory summary and a sim, and shipped as commit 3abb407 — an incomplete
# implementation of DECISIONS.md #29, which had ALREADY specified the whole
# thing, including two requirements that were missed (gyro digital LPF, and a
# pre-takeoff bias subtraction the plan called mandatory). The spec was sitting
# in the repo the entire time. Nothing asked for it, so nobody read it.
#
# This repo front-loads its thinking: DECISIONS.md is a numbered decision log
# and docs/*.md carry the study plans. A summary of that thinking is a POINTER,
# never the spec.
#
# The guard: before any Edit/Write, grep DECISIONS.md and docs/*.md for the
# basename of the file being touched, and inject whatever matches as context.
# It is deliberately silent when there are no matches, so it only speaks when
# predefined work genuinely exists — which is exactly when it would have fired
# for jump_detector.h, since DECISIONS.md #29 names that file outright.
#
# It informs, it does not block: a wrong guard that stops work is worse than a
# missed one.
#
# SPDX-License-Identifier: MIT

set -uo pipefail

command -v jq >/dev/null 2>&1 || exit 0   # no jq: stay silent, never break edits

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." 2>/dev/null && pwd)" || exit 0
[ -n "$root" ] || exit 0

input="$(cat)"
path="$(printf '%s' "$input" | jq -r '.tool_input.file_path // empty' 2>/dev/null)"
[ -n "$path" ] || exit 0

base="$(basename "$path")"
[ -n "$base" ] || exit 0

# Don't fire when editing the spec sources themselves — a doc that mentions its
# own name is noise, not a warning.
case "$path" in
  */DECISIONS.md|*/docs/*.md) exit 0 ;;
esac

# -F: the basename is a literal, not a pattern (dots in filenames).
hits="$(grep -rnF -- "$base" "$root/DECISIONS.md" "$root"/docs/*.md 2>/dev/null | head -12)"
[ -n "$hits" ] || exit 0

count="$(printf '%s\n' "$hits" | grep -c . )"

jq -n --arg b "$base" --arg h "$hits" --arg n "$count" '
{
  hookSpecificOutput: {
    hookEventName: "PreToolUse",
    additionalContext: (
      "SPEC CHECK — this repo has \($n) predefined-work reference(s) naming \($b).\n" +
      "These are the AUTHORITATIVE spec. A memory summary or a sim result is a pointer to them, not a substitute.\n" +
      "Read the relevant one in full before implementing, and check your work against every requirement it lists:\n\n" +
      $h
    )
  }
}'
exit 0
