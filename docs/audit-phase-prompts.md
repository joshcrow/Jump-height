# Audit phase prompts

Ready-to-paste prompts for working `docs/audit-2026-08-22.md` (tickets
F-01…F-21) one phase at a time, in a fresh Claude session.

**How to use:** paste the *shared preamble* followed by the block for the
phase you want. One phase per session — they gate each other, and a session
that tries to do all five will blur the handoff boundaries the ledger depends
on.

---

## Shared preamble — paste this first, for every phase

```
Read docs/audit-2026-08-22.md in full, including the "Standing instructions"
block at its top, plus CLAUDE.md. Then work the phase named below.

Ground rules for all phases:

- VERIFY BEFORE YOU CODE. CLAUDE.md rule 5 applies to this document as much as
  to any agent output. Each finding survived adversarial refutation and seven
  were reproduced by hand, but confirm each against the current source
  yourself before changing anything. If a ticket no longer reproduces, say so
  and check it off as "no longer present" — do not invent a fix for it.

- A SKIP IS NOT A PASS (rule 3). Several tickets exist because a skipped or
  miscollected check reported green. Never fix one by introducing another.

- SIBLING SWEEP IS PART OF EVERY FIX, in the same commit. This codebase's
  dominant defect pattern is a correct fix applied where it hurt and nowhere
  else — five confirmed instances across five subsystems. Grep for the same
  shape elsewhere; fix or explicitly clear each hit before you commit.

- ACCEPTANCE IS WRITTEN PER TICKET. Don't check a box until its acceptance
  test actually passes and you've shown the output.

- Do not touch anything on the "Refuted — do not re-chase" list.

- Commit each ticket separately with its ID in the subject, check its box in
  the ledger with the commit hash, and push. Work on the branch containing
  docs/audit-2026-08-22.md, or a new branch cut from it — say which before you
  push.

DECISION RULE WHEN YOUR FIX SURFACES SOMEONE ELSE'S BUG

Your tickets make hidden things visible; that is their purpose. When a gate
you repair goes red on something outside your phase:

  - If it maps to an existing ticket, STOP. Do not fix it. Record it in your
    handoff as "gate now exposes F-NN" and leave it. That phase owns it.
  - If it maps to nothing in the ledger, that is a NEW finding. Add it as F-22
    onward using the same fields the existing tickets use, and say so. A fix
    outside the ledger is invisible to every later phase.
  - Never make a gate green by loosening it. Widening a tolerance, adding a
    skip, or deleting an assertion to get past a real failure recreates the
    exact defect class this work exists to remove.

THE FIVE PHASES

  Phase 1  F-01 F-02 F-03 F-04   Build + test gates. Host-only, no hardware.
  Phase 2  F-05 F-06 F-09 F-21   Firmware/power. SAME FLASH BATCH as Phase 1's
                                 F-01 flag change. Needs the OG board
                                 (JumpHeight-E2C4) — the only one with a cell.
  Phase 3  F-07 F-08 F-10 F-19   Flash store + harness. F-08 changes the trace
           F-20                  block header ⇒ a SECOND flash batch.
  Phase 4  F-11 F-12 F-13 F-14   Watch app + bench tool pinning. Hard gate
           F-15                  before the water session.
  Phase 5  F-16 F-17 F-18        Sim/firmware parity. F-18 is the
                                 highest-leverage maintainability change here.

Ordering rationale: the Phase 1 gates validate everything downstream, so they
land first. Phases 2–3 are firmware and batch into flashes. Phase 4 unblocks
the water session. Phase 5 is structural work that wants honest gates and
settled firmware underneath it.

CROSS-PHASE DEPENDENCIES

- F-02 → F-17. test_lever_arm.py is one of three files simtest currently
  cannot see. Until F-02 lands, a fix to that file is invisible to the local
  gate.
- F-01 → Phase 2. The platformio.ini change is firmware and must NOT be
  flashed alone; Phase 2 adds F-05/F-06/F-09 and flashes once (rule 4).
- F-03 → F-20. Enabling env:host compilation in CI makes the host store tests
  actually run; F-20 is a real bug in that path.
- F-10 → F-19. The region-full test needs F-10's status enum. Same phase, same
  agent, F-10 first.

FACTS PRE-CHECKED 2026-08-22 (verified against the tree — rely on these)

1. The 54 tests simtest cannot currently see ALL PASS under pytest today
   (`54 passed`). F-02 will not turn simtest red. But do not read that green
   as validation of the lever-arm logic — F-17 says test_lever_arm.py passes
   because it tests the wrong commit policy. Green there is meaningless until
   Phase 5.

2. F-03 will NOT cascade red from F-20. The only height assertion in
   test_hostdev.py is `assertGreater(float(kv["height_m"]), 0.0)` — too loose
   to catch a field-index bug, since n_air is also positive. Enabling env:host
   in CI is safe. That looseness is *why* F-20 survived; Phase 3 must tighten
   the assertion, not just fix the parser.

3. F-01's flag change carries no detectable numeric risk on the host detector:
   host_test.cpp compiled at -Ofast, -Os and -O2 against
   data/example_session.csv produces BYTE-IDENTICAL output (4 jumps, same
   values). If the C++/Python parity check fails after the change, something
   else is wrong — DO NOT widen the 0.002 tolerance to make it pass. Caveat:
   that was the x86 host build; the ARM softfloat path is unverified, which is
   what Phase 2's desk three-toss is for.

Finish with a handoff block for the next phase: tickets landed with commit
hashes, acceptance output, what the sibling sweeps found, firmware batch state
if applicable, and any gate that now exposes a later ticket.
```

---

## Phase 1 — build & test gates

```
Work Phase 1: tickets F-01, F-02, F-03, F-04.

These are the build and test gates. Everything else in the audit is validated
by them, which is why they go first and alone.

Phase-specific rules:

- DO NOT FLASH. Phase 1 prepares a firmware build batch; Phase 2 flashes it
  (rule 4: never iterate on silicon). F-01's desk three-toss validation is a
  Phase 2 gate — build and verify by disassembly here, defer the hardware
  check.

- NO HARDWARE NEEDED. All four tickets are verifiable on the host: compiler
  flags, disassembly, test collection counts, CI config.

- F-01's sibling sweep is the important one. Once -Ofast is gone, every
  NaN/Inf-dependent guard in the firmware becomes live for the first time —
  find them all and confirm each is correct, not just the two the ticket
  names.

- F-02's acceptance is PARITY of collected counts, not today's number. Assert
  that simtest's collection matches pytest's on the same tree; do not hardcode
  191.

- If pytest is absent on a bench machine, simtest must FAIL, not skip.

Leave the firmware build batch prepared and describe its contents in your
handoff.
```

---

## Phase 2 — firmware & power

```
Work Phase 2: tickets F-05, F-06, F-09, F-21. Phase 1 must be complete.

Phase-specific rules:

- THIS IS THE FLASH BATCH. It includes Phase 1's F-01 optimization-flag change
  plus these four. One flash, not five (rule 4).

- HARDWARE: the OG board only (JumpHeight-E2C4). It is the only board with a
  battery, so it is the only board that can validate F-05. Pin every BLE call
  by name — `./tools/jump boards` first to confirm which boards are live.

- F-05's acceptance requires a forced watchdog reset, not just a cold boot —
  the whole point is that DCDCEN is volatile and a reset silently reverts it.

- F-06: run the falsifier the code comment itself names — post-change sample
  deltas within 2 ms of cadence, and no systematic shift in desk-test
  airtimes. If either moves, revert rather than rationalize.

- F-21 is a comment fix, but its sibling sweep matters: grep firmware/ and
  tools/ for "11.6" and "16.3", both retracted figures.

- The desk three-toss on the OG also validates Phase 1's F-01 on real ARM
  silicon for the first time. Report its numbers explicitly.
```

---

## Phase 3 — flash store & harness

```
Work Phase 3: tickets F-07, F-08, F-10, F-19, F-20.

Phase-specific rules:

- F-08 changes the trace block header format. That is a SECOND flash batch,
  separate from Phases 1–2. It must stay decode-compatible with blocks already
  written to boards in the field — version the header, don't redefine it.

- ORDER: F-10 before F-19. The region-full test needs F-10's status enum to
  assert against.

- F-10 touches a seam with THREE implementations — nrf52, host, and the mock.
  All three change in the same commit or the harness diverges from the target.

- F-20: fix the parser AND tighten the assertion. The bug survived because
  test_hostdev.py only asserts `height_m > 0.0`, which n_air also satisfies.
  Add a case where the two are distinguishable (e.g. height 1.05, n_air 44).

- F-07 and F-08 are both verifiable without hardware using
  firmware/test/store_host/ — the mock models real NOR semantics and can
  inject erase failures and power cuts. Use it; don't reason about these from
  the source alone.
```

---

## Phase 4 — watch app & bench tool pinning

```
Work Phase 4: tickets F-11, F-12, F-13, F-14, F-15. This is the hard gate
before the water session.

Phase-specific rules:

- F-14 touches tools/otadfu.py, which flashes firmware over the air. DO NOT
  test the DFU path against a board you cannot physically recover. The ticket
  is about WHICH board gets selected — a careless test is exactly how you
  discover the bug the expensive way. Test the selection logic against a stub
  or a scan-only dry run first.

- F-13/F-14/F-15 are one pattern applied in three places: blecmd.py already
  has the census-and-refuse logic. Port it; do not reinvent it per tool. The
  sibling sweep extends to hostsoak.py, storagesoak.py and dualjump.py.

- F-14 adds a new way to select a board. CLAUDE.md §4: update
  docs/bench-playbook.md §1 and .claude/hooks/bench-guard.sh's matcher IN THE
  SAME COMMIT. A new identifier without a lookup entry is a rediscovery
  waiting to happen.

- F-11 is the highest-value ticket in this phase — it corrupts the permanent
  FIT record. Its acceptance is a unit test in garmin/jumpfield/tests feeding
  a reboot-reseed sequence. The best-airtime path at Model.mc:308-310 is your
  reference implementation; copy its shape.

- When this phase is done, say explicitly whether the water session is
  unblocked.
```

---

## Phase 5 — sim/firmware parity

```
Work Phase 5: tickets F-16, F-17, F-18. F-02 (Phase 1) must have landed or
F-17's fix is invisible to the local gate.

Phase-specific rules:

- F-18's constant count (34 in two or more languages, 10 generated) is
  agent-measured, NOT hand-verified. Re-count it yourself as step one and
  correct the ledger if it's wrong.

- The parity harness only exercises the accel-only update() path
  (host_test.cpp:64, golden.py:35). Every gyro-aware divergence is invisible
  to simtest BY CONSTRUCTION. Extending parity coverage is part of F-16's fix,
  not optional — add a gyro-path golden trace.

- F-17: test_lever_arm.py currently PASSES while testing the wrong commit
  policy. Do not treat its green as a baseline. Mirror main.cpp's actual
  gating, then add a regression test proving the ~50x-corruption scenario does
  NOT move the calibration.

- F-18 is the highest-leverage change in the whole audit: it converts the
  repo's weakest axis (maintainability) into its strongest existing mechanism
  (generated + --check-gated params). Wire the extended --check into both
  simtest and CI, or it will drift again.
```

---

*Companion to `docs/audit-2026-08-22.md`. If the two disagree, the audit
ledger wins — these prompts are scaffolding, the ledger is the record.*
