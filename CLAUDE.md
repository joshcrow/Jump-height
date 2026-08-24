# CLAUDE.md — read automatically, every session

This file exists because of a specific, repeated failure: **the repo already
contained the fact, and I got it wrong anyway.** On 2026-08-20 I diagnosed a
hardware fault on a board that simply has no battery — a fact written in
`docs/STATUS.md` two days earlier *and* in the board registry. Two correct
records, 1,800 lines of chronology, no way to reach them at the moment of use.

Prose in `docs/` is a **reference**, consulted deliberately. This file is
**context**, loaded unconditionally. So this file holds only what is expensive
to re-derive and cheap to state — and points at the authority for everything
else. Keep it short; a long CLAUDE.md is an unread CLAUDE.md.

---

## 1. Physical facts (the class that costs hours when wrong)

**Only the OG has a battery.**

| Board | Advertised name | Battery | Can measure power? |
|---|---|---|---|
| **OG** (product board) | `JumpHeight-E2C4` | **YES** — pigtail soldered | **Yes — the only one** |
| Spare (a.k.a. "Board #3") | `JumpHeight-45ED` | **NO** — USB only | No. Its `vbat_mv`/`batt_pct` are a floating divider: **noise, not measurements** |

Consequences that follow mechanically:
- Every drain figure, endurance number, DC/DC comparison and the three-toss
  desk test is meaningful **only on the OG**.
- The water session needs a battery-backed puck ⇒ the OG, or pigtails soldered
  to a spare first. Hardware prerequisite, not an assumption.
- **Two boards advertise at once.** Unpinned BLE tools answer from whichever
  replies first — two consecutive `stats` calls have returned two different
  boards. Always pass `--name JumpHeight-XXXX`. This has corrupted two
  analyses.

**Ground truth on demand — prefer this over trusting any of the above:**
```
./tools/jump boards        # scans, reads each board, flags floating batteries
```
Authority: [`docs/bench-playbook.md` §1](docs/bench-playbook.md).

## 2. Rules that exist because breaking them cost real time

1. **Never declare hardware dead without first establishing its
   configuration.** Three such verdicts have been wrong. The one-question
   check: *is a cell even attached?* → `docs/xiao-hardware-truth.md`.
2. **No verdict without a measurement.** A number nobody measured is not a
   number. Retracted figures stay retracted; don't quietly resurrect them.
3. **A reading that did not happen is itself a finding** — a silent failure
   must never look like a pass. This applies to polling loops, to `grep` on a
   binary file, and to tests that assert the wrong outcome.
4. **Batch firmware into one flash.** Never iterate on silicon.
5. **Verify agent/review findings yourself before acting.** Several confirmed
   findings have been wrong on inspection.

## 3. Where truth lives

| Question | File |
|---|---|
| What is true *now*, and what was retracted | `docs/STATUS.md` — **read its READ THIS FIRST table**, not the whole log |
| Which board is which | `docs/bench-playbook.md` §1 |
| Why a "dead" board probably isn't | `docs/xiao-hardware-truth.md` |
| Battery capacity, cutoffs, real endurance | `docs/battery-measurement.md` |
| What the water session must produce | `docs/session-card.md` |
| What we could measure later, and what we'd close off | `docs/future-metrics.md` |
| Numbered, binding decisions | `DECISIONS.md` |
| Audit findings & work orders (F-01…F-21), what was refuted | `docs/audit-2026-08-22.md` |
| How to run an audit phase (paste-ready prompts, cross-phase gotchas) | `docs/audit-phase-prompts.md` |

`docs/STATUS.md` wins over any other document. If they disagree, the other one
is stale — fix it.

## 4. Maintenance rule (this is the one that would have prevented 08-20)

When a change adds a **new way to identify a board** — a unique advertised
name, a serial format, a manufacturer-data field — update the registry **in
the same commit**. Unique BLE names shipped 08-18; the registry was last
touched 08-14; for two days every tool printed an identifier that appeared in
no document. The facts were all correct. Nothing could join them to the screen.

The general form: **a new identifier without a lookup entry is a rediscovery
waiting to happen.**

The mirror case costs the same and is easier to cause: **when you retire an
identifier, fix what pointed at it in the same commit.** Deleting the
`mule-railcheck` branch on 08-23 left three docs telling a reader to find
`railcheck` with `git branch -a` — a command that now returns nothing, for a
claim that is still perfectly true (it lives on tag `archive/mule-railcheck`).
A dead pointer reads as a wrong fact.
