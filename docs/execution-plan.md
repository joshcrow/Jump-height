# Execution plan — from tonight to the appliance

Written 2026-08-20 late, as strategic sequencing of
[glue-and-forget.md](glue-and-forget.md) (the adversarially-verified vision
audit) against the actual resources: **three boards, two watches, the
brother tomorrow evening + weekend (no water), and agents that can run in
parallel.** This document is the work's single sequencing authority until
the water day gets a date; plan.md remains the water-day protocol itself.

## 0. The resource map (what we actually have)

| resource | identity | role this plan assigns |
|---|---|---|
| **OG** | `JumpHeight-E2C4`, battery, pigtails | **The product board.** Endurance truth, final builds only, the water build. Protected: no experiments. |
| **Spare** | `JumpHeight-45ED`, USB-only | **The bench board.** Storage/lifecycle rehearsals, BLE targets, dualcentral, first flash of every build. |
| **Third — "Puck"** | `B96D14EA…`, USB-only, unassessed since 08-12 | **The Era-2 development board.** Standby/System-OFF/wake work and deliberate OTA-abort testing — work that can strand a board. *(Never call it "the mule": that name belongs to the OG historically, and reusing it is how this project nearly treated its product board as disposable — bench-playbook.md §1.)* USB-only makes it perfect: VBUS replug always wakes it. Needs bring-up first (it predates the drive-strength fix's verification on it; "written off, almost certainly fine"). |
| **Epix Gen 2** | owner's wrist | **The dev watch.** Simulator-verified builds land here first; onTimerReset and glance tests run here. |
| **Instinct 3 Solar** | brother's wrist, available Thu eve + weekend | **The product watch.** P0 bring-up Thursday; full-dress rehearsal on the weekend. |
| Brother | Thu evening + weekend, no water | The rider. Bring-up assistant Thursday, rehearsal rider on the weekend, rider-brief recipient. |

## 1. Standing decisions this plan needs (from glue-and-forget §2)

Unblocked work proceeds regardless, but each decision opens a branch:
1. **Water-day date** → activates the freeze protocol and fixes the Era-2
   start. *Until set, this plan treats Era 2 as startable now on non-OG
   hardware* — the freeze protects the water build, not the mule.
2. **Glue vs removable** → decides whether OTA-safety work is critical path
   or mostly moot, and whether Era 3 exists.
3. **µA-meter** → decides whether Era-2 standby closes with a number or an
   upper bound.
4. ~~Brother's watch~~ **DECIDED: P0, he rides.**
5. **False-positive budget** → default <1 phantom/riding-hour adopts at the
   freeze unless overridden.

## 2. The sequence

### Phase 0 — tonight/first thing, because these are CALENDAR-gated
*Nothing below starts a clock; these do. Minutes of work, weeks of latency.*
- **Adhesive coupon into a saltwater bucket** — 6-month data has a 6-month
  clock, and every day of delay is irrecoverable (glue-and-forget §6).
- **Temperature logger / max-min thermometer** where the board actually
  lives between sessions.
- The **µA-meter decision** (§1.3) — a purchase has shipping latency.
- **Water-day date** (§1.1) — everything else keys off it.

### Phase A — tomorrow morning (Thu): close the seven-fix batch
*Owner: one shake. Me: reads and verdicts. ~30 min total.*
1. Read the OG's overnight battlog: **uptime first** (reset recurrence
   check), then the **fixed-voltage-window** comparison — time to traverse
   3980→3830 mV against the established 5.58 h / 5.49 h baselines (and the
   3961→3751 mV pair at 7.26/7.18 h, 1.1% agreement). **Not** an average
   mV/h figure: that is exactly the non-linear-curve error STATUS.md warns
   against, and an earlier draft of this plan committed it.
2. Owner **shakes the spare** → auto-clear rehearsal verdict: uptime first,
   then: trace cleared? all 3 jumps survived? storage still up? This is the
   descending-erase fix's first firing on silicon against a full region.
   **Then immediately re-run `fillstore` on the spare.** The rehearsal
   empties the trace region, and B1's whole premise is a large transfer —
   the original silent-drop bug needed 240 KB/13.4 MB to show. Running B1
   against an empty region would byte-diff nothing and report a PASS that
   tested nothing.
3. Record both verdicts in STATUS. The seven-fix batch is then fully
   validated end to end.

### Phase B — Thursday daytime: parallel streams (agents, sonnet)
*All independent; run concurrently. None touches the OG.*

| stream | work | verification | agent-safe? |
|---|---|---|---|
| **B1. dualcentral gate** | The harness already PASSED once (OG, 2026-08-19, byte-identical, tx_drops zero). Genuinely new work is narrower: (a) re-run pinned `--addr 45ED` on the **spare** with a refilled trace; (b) the leg that has never run anywhere — **20 fakejumps with both centrals subscribed**, counting rendered vs sent. | Byte-identical streams, tx_drops=0, **20/20 rendered, `!` absent** | Hardware: I run it. **Gates Phase C6, the two-watch test.** |
| **B2. watch-side session delta** | Model.mc: `_baseJumps` captured on first counted line, cleared by `onTimerReset()`, best/airtime as watch-local maxima, **restart guard** (baseline in `Application.Storage`, re-baseline downward only on puck reboot), Δn-mismatch marks best uncertain. + ModelTest cases incl. the restart and mismatch paths. | Simulator suite green incl. new tests; NOT sideloaded to Instinct until Phase C baseline passes | Yes — pure Monkey C + simulator |
| **B3. timebase double sweep** | (1) Falsifier first: goldens +604,800 s → confirm simtest DIVERGES today. (2) float→double for t_s/takeoff_time_/last_low_time_/JumpEvent + main.cpp:1326; llround + explicit int32-bound assert at the anchor; sweep jh_store.cpp:960. (3) Confirm reconvergence. | Host suite + the falsifier both green; flashes to the THIRD board only, after its bring-up | Yes — fully host-testable |
| **B4. docs & cards** | Session-card: reboot ritual + reset-button reachability check. New rider-brief card (10 lines, §3 below). STATUS updates. | Owner read-through | Yes |
| **B5. store submission draft** | Listing text, screenshots list, `.iq` export checklist, review-guideline pass. No account actions. | Owner review before anything is submitted | Yes |
| **B6. third-board bring-up** | Replug (owner, any time) → flash current proven build → falsifier selftest → registry row updated with its advertised name. | 6/6 selftest, name recorded | Hardware: me + one owner replug |

### Phase C — Thursday evening: the Instinct session (brother + watch, ~90 min)
*Puck for all watch work: **the OG** (session-card's own pairing) unless it
is mid-endurance-run, in which case the spare — state which was used in the
notes, because two boards on the air is how identity errors start.*
*The P0. Baseline the product watch on the PROVEN build — no new watch code
tonight; B2's build waits for the weekend.*
1. Sideload the current field (built for `instinct3solar45mm` tonight,
   12.4 KB/32 KB static). Record his watch firmware version.
   **Sideload risk, budgeted:** the only proven headless MTP path is
   Epix-specific (hard-coded storage id) and its libmtp patch is described
   in garmin/README but does not exist in the repo. The Instinct's storage
   id may differ. **Hedge: have OpenMTP (GUI) open as the fallback and use
   it first if the headless path stalls** — brother-time is the scarce
   resource, not elegance.
2. One-time setup on his watch: field onto a windsurf-profile data screen.
3. Render every layout tier on the real 176×176 MIP. Photograph each.
4. Desk test: fakejumps end-to-end — count, best, corruption gate (`!N`),
   NO-REC row. Vibrate check (US3's first-ever firing).
5. Save a real activity → pull the FIT → parse: dev fields present, units
   from HIS settings.
6. **If B1 passed**: the two-watch test — Epix + Instinct subscribed
   simultaneously, 20 fakejumps, both render 20, `!` absent on both.
7. Rider brief, delivered in person while the hardware is in his hands.
8. Stretch, if time: `onTimerReset`-fires-on-hardware test (10 min, on the
   Epix) — unblocks B2's weekend sideload.

### Phase D — the weekend: full-dress rehearsal (no water needed)
**Requires B6 PASS** (the third board healthy). Fallback if B6 fails or is
inconclusive: run the rehearsal with the **spare** in the capsule (it is
USB-only, so it cannot do the untethered leg — do the mount/cure and ritual
practice, and defer the recording legs to the OG on a later evening). Do
NOT pull the OG out of its endurance run for this.
*The closest-to-water validation available, plus free false-positive data.*
1. **Bucket test first** (owner, 15 min — still unrun, P0 in plan.md):
   empty float, loaded float.
2. Third board in the capsule, mounted on the board with the **rehearsal**
   adhesive — explicitly NOT the production bond. The OG's own mount + 24 h
   cure is a separate, later, water-day prerequisite (plan.md item 6,
   session-card.md); scheduling it here would both consume the third board
   and start the wrong clock. Brother wearing the Instinct: carries, car transport, board
   tosses, walking — a full fake session with the activity recording.
   **Every minute is labeled non-riding data**, so every detected jump is a
   phantom. **Caveat, stated honestly:** carrying/driving/tossing is a
   different motion regime from chop, bracing and pumping, so a clean result
   rules out gross mishandling false-positives — it does NOT calibrate the
   on-water <1/hour budget. A DIRTY result is the informative one: phantoms
   here mean the gate is loose enough to fire on handling alone.
3. Practice the ritual exactly as the card says: reboot puck → start
   activity → three flat drops → note wall time → session → three drops →
   sync. The brother runs it, not the owner — the card is being tested as
   much as the hardware.
4. If C8 passed: sideload the B2 session-delta build to the Instinct,
   re-run the desk test, then the rehearsal counts double (delta logic
   under real conditions).
5. OG: second overnight battlog on the new build (n=2 for the drain
   regression), plus a full charge cycle.

### Phase E — Era 2 opens (now, on the mule — not gated on the water day)
*The freeze protects the water build (OG + proven watch build). The third
board is explicitly not that. Sequenced by the revised doc's order:*
1. **E0 — OTA safety** (or §1.2 makes it moot): resumable/verified
   transfer already exists; add spare-first rule + never-near-session rule
   to the playbook; prototype an authenticated `dfu` trigger. Deliberate
   mid-transfer aborts on the third board to characterize dark-bootloader
   recovery cost.
2. **E1 — timebase** = B3, flashed and verified on the mule.
3. **E2 — standby**: LSM6DS3 wake-engine registers + nRF SENSE/DETECT +
   auto-off + low-battery cutoff, developed entirely on the mule (System
   OFF with a broken wake config on a USB board = replug; on the OG =
   stranded). **Opens with the off-current question**: if the meter was
   bought, measure; if not, the deliverable is an upper bound and the doc
   says so. Kill threshold pre-committed: >200 µA → stop, find the leak
   (QSPI first suspect) before building more.
4. **E3 — foil signal spec**: one periodic puck line (raw variance metric),
   threshold+accumulator watch-side per the revised doc; prototype the
   watch half against fake lines in the simulator now, tune after water.
5. **E4 — watch hardening**: PuckLink state timeouts + DEAD retry (~5-line
   fixes), low-battery warning on the field.
6. **E5 — store submission** files when B5's draft is approved.

## 3. The rider brief (Phase B4 deliverable, delivered in Phase C7)

Ten lines, printed: (1) charge state check = glance/field shows puck %.
(2) Before riding: START the activity — windsurf profile, the screen with
the jump field. (3) The puck records regardless, but no activity = no
watch data, ever. (4) Screen says "finding puck"/drops out: cosmetic, keep
riding, it reconnects. (5) `!N` on screen: tell us after, not a problem.
(6) NO REC: tell us after, still keep riding. (7) Never stop/discard the
activity mid-session; save at the end. (8) Jump numbers on the glass are
live; the archive fills in at save. (9) Feet/meters follow YOUR watch
settings. (10) If the puck seems dead: nothing to do on the water; it gets
diagnosed at home, never at the beach.

## 4. Agent execution map (mostly sonnet)

- **Parallel-safe now**: B2, B3, B4, B5 (code/docs, no hardware, no shared
  files between streams). E3's watch half and E0's authenticated-dfu design
  can join once B-streams land.
- **Bench-serial (me, hardware in hand)**: B1, B6, all of C and D, E1/E2
  flashing.
- **Owner-only**: replugs, the shake, bucket test, coupon in bucket, temp
  logger placement, brother logistics, decisions §1.
- **Review discipline unchanged**: every agent-produced change gets
  verified against source before merge (twice tonight the reviewer was
  right and the author was me); firmware batches get a pre-flash review;
  nothing lands on the OG without passing the spare first.

## 5. What "pushing hard" does NOT change

- The OG stays protected. Speed comes from the mule and parallel agents,
  not from risking the product board.
- One flash per replug; batch firmware; `Device programmed` or it didn't
  happen.
- No second central while anyone is testing rider-mode until B1 passes.
- Every claim gets its measurement before it gets its checkmark.
