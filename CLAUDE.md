# CLAUDE.md — read automatically, every session

This exists because of one repeated failure: **the repo already contained the
fact, and I got it wrong anyway.** On 2026-08-20 I diagnosed a hardware fault
on a board that has no battery — recorded correctly in two places, unreachable
at the moment of use.

So this file holds only what is expensive to re-derive and cheap to state.
Everything else is a reference, consulted deliberately. **A long CLAUDE.md is
an unread CLAUDE.md.**

---

## 1. Physical facts

**Only the OG has a battery.**

| Board | Advertised name | Battery | Can measure power? |
|---|---|---|---|
| **OG** (product board) | `JumpHeight-E2C4` | **YES** — pigtail soldered | **Yes — the only one** |
| Spare | `JumpHeight-45ED` | no — USB only | no. Its `vbat_mv`/`batt_pct` are a floating divider: **noise** |
| Puck | `JumpHeight-8673` | no — USB only | no |

- Every drain, endurance and DC/DC figure is meaningful **only on the OG**.
- **Three boards can advertise at once.** Unpinned BLE tools answer from
  whichever replies first — this has corrupted two analyses and flashed one
  wrong board. Always `--name JumpHeight-XXXX`.

Ground truth on demand, preferred over anything above: `./tools/jump boards`

## 2. Rules that exist because breaking them cost real time

1. **Never declare hardware dead without first establishing its
   configuration.** **Four** such verdicts have been wrong; nothing was ever
   damaged. One question: *is a cell even attached?* → `docs/xiao-hardware-truth.md`
2. **No verdict without a measurement.** Retracted figures stay retracted.
3. **A reading that did not happen is a finding.** A silent failure must never
   look like a pass — this applies to polling loops, to `grep` on a binary, to
   tests asserting the wrong thing, and to any tool that reports a count.
4. **Batch firmware into one flash.** Never iterate on silicon.
5. **Verify agent and review findings yourself.** Several confirmed findings
   have been wrong on inspection.
6. **Cite what the source actually says.** On 2026-08-23 I attributed a rule to
   this file that is not in it. In a repo whose discipline is evidence, a
   confident wrong citation is worse than no citation.

## 3. Where truth lives

| Question | File |
|---|---|
| What is true *now* | `docs/STATUS.md` — **it wins over every other document** |
| Which board is which | `docs/bench-playbook.md` |
| Why a "dead" board probably isn't | `docs/xiao-hardware-truth.md` |
| What the water session must produce | `docs/session-card.md` |
| The watch, BLE, and the store | `docs/watch.md` |
| Labels, scoring, the eval contract | `docs/data-pipeline.md` |
| Numbered, binding decisions | `DECISIONS.md` |
| Open findings (F-22…F-25) | `docs/audit-2026-08-22.md` |

## 4. Maintenance rule

When a change adds a **new way to identify a board** — an advertised name, a
serial format — update the registry **in the same commit**. Unique BLE names
shipped 08-18; the registry was touched 08-14; for two days every tool printed
an identifier that appeared in no document.

**The mirror case costs the same and is easier to cause: when you retire an
identifier, fix what pointed at it in the same commit.**

The general form: **an identifier without a lookup entry is a rediscovery
waiting to happen.**

## 5. Deleted documentation

On 2026-08-23 the doc tree was cut from 18,000 lines to ~2,000. Chronology
belongs in git; deliberation is superseded by the decisions it produced.

**Code comments still cite deleted docs** (`SENSE_FIRST_BOOT.md`, `sense.md`,
`roadmap.md`, `power-states.md` and others). That is not rot — it is a
deliberate lookup entry, per §4. They resolve at:

```
git show archive/docs-2026-08-23:<path>        # e.g. firmware/SENSE_FIRST_BOOT.md
git checkout archive/docs-2026-08-23 -- <path> # to restore one
```

Other tags: `archive/web-app` (the browser app), `archive/mule-railcheck`.
