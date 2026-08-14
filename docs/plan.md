# The plan

Rewritten 2026-08-14 from [STATUS.md](STATUS.md) — an evidence-first audit of
what is actually built — rather than from the previous plan's memory. That
earlier version listed already-finished work as TODO. This plan is structured
to make that impossible. (Archived: `plan-superseded-2026-08-14.md`.)

**Read [STATUS.md](STATUS.md) before adding anything here.** Run
`./tools/jump status` before believing any of it.

---

## 1. Where we are, in one paragraph

The hardware crisis is over and was never hardware: a GPIO drive-strength bug
made healthy sensors look dead for four days. Both boards now pass every
self-test row and have soaked 5/5 and 12/12. The puck detects jumps, logs to
flash, serves BLE, takes wireless firmware updates, and reports battery. The
watch renders on a real wrist over a real link. **And the product's central
question — does the airtime method measure real foil jumps — has never been
tested, because nobody has been on the water.**

## 2. The constraint that shapes everything

The water session needs the brother's board, a kayak, a windy day, and someone
filming from the water. It is **one shot, hard to repeat, ~2 weeks out.**

So the goal is not speed. It is: **make that one session impossible to waste,
and make it answer the question even if the filming goes badly.**

## 3. The three things that would waste it

Ranked by how likely they are to actually happen.

### 3.1 The ground truth is circular — fix first, costs no build time

`data-pipeline.md` derives "true height" from counted airborne frames via
`h = g·T²/8` — **the formula under test.** Scoring our `g·T²/8` against a
label built with `g·T²/8` measures timing agreement and nothing else. It
passes whether or not wings are ballistic, which is the entire open question.

**Two changes:**

1. **An independent height measure.** The rig carries its own ruler: the mast
   is a known length, in the same plane, at the same distance. Measure apex
   board-underside-to-water in mast-lengths. ±15 % is plenty — the failure it
   must catch (a kite-like 2.3× overshoot) is enormous. Requires the kayak
   roughly abeam of the jump line: a briefing item, not code.
2. **Make the primary result video-independent.** The real open question from
   the sim work is whether airborne |a| sits in the predicted 0–0.07 g band.
   That needs an intact 50 Hz trace and **no footage at all.** Promote it to
   the primary deliverable; video becomes the secondary, height-scale check.

Also unfixed: **video sync has no procedure that survives real filming.** One
marker aligns one continuous recording; from a kayak you get 20–40 short
clips, and the puck has **no wall clock** (trace time is boot-relative).
Tolerance is ±0.8 s and a bad alignment fails *silently* — it matches a subset
and prints a plausible RMSE. Needs a wall-clock↔trace anchor at both ends and
a sync marker that survives 50 Hz decimation (drop the board flat 3×, not a
finger tap).

### 3.2 Nothing has been recorded end-to-end on the current build

`STATUS.md` is blunt: no jump has been detected on silicon on **any build
since 2026-08-11**, and the only on-silicon jump history was erased by the
`format` that fixed storage. The storage path, the IMU rail drive, and
`begin()` have all changed since.

**The gate is `./tools/jump desktest`** — 3 untethered tosses, ~10 minutes,
owner's hands. It proves battery-only operation, the detector on real motion,
and that a jump *survives to storage*. Nothing else proves the last one.

### 3.3 The data comes home through a path that can silently drop blocks

`main.cpp:150` — `emitBytes` discards a whole block whenever the serial buffer
is short. That is the USB download path. It changed two days ago and has never
been exercised at session scale. A session that records perfectly and
downloads incompletely is the cruellest possible failure.

**Fix:** make the download verifiable — a byte/line count the CLI checks, and
a re-request on mismatch. Then exercise it at session scale.

## 4. Workstreams, prioritised

### P0 — must be true before the water (in order)

| # | Work | Why | Owner |
|---|---|---|---|
| 1 | **Desk test, 3 tosses** | The only proof a jump survives to storage on this build (§3.2) | **you, 10 min** |
| 2 | **Flash + verify the P1 batch** | BLE silent-drop fix, LED off, slow advertising, `system_off` drive — all `built-unverified`, never on silicon | eng, needs a board |
| 3 | **Download integrity** | §3.3 — the path the session comes home through | eng |
| 4 | **Fix the labeling procedure** | §3.1 — circular ground truth; rewrite `data-pipeline.md` | eng |
| 5 | **Sealed 3 h battery run** | "60 h" is a paper estimate. And `off` is a **one-way door in a sealed case** — §16j proves it wakes only on a VBUS edge or the reset button, neither reachable through a closed lid | both |
| 6 | **Mount: order, stick, cure** | 24 h cure, on the brother's board — a dependency on a person not in this plan | **you** |
| 7 | **Land dress rehearsal on the frozen build** | Mount, film, jump, download, label, `eval` end-to-end. Finds pipeline breaks on land instead of from a kayak | both |

### P1 — makes the session yield more (do if P0 is clear)

| Work | Why |
|---|---|
| **Close watch M2 with `fakejump`** | The field has **never displayed a correct jump on any wrist.** Ten `fakejump`s on the fixed firmware closes it — no water, wind or brother needed |
| **Watch: bare-catch every boundary** | ~10 one-word edits. This field has already died twice on silicon from non-`Lang` errors escaping a filtered catch |
| **Watch: show its own health** | Surface `rejectedCount()` and `tx_drops`; distinguish standby / out-of-range / flat. Converts "a number that might be lying" into an instrument |
| **§9.9 background-page test** | 10 min, no code: does `compute()` run when another data page is visible? If not, the watch contributes nothing all session and nobody finds out until download |
| **Lever-arm persistence + visibility** | It self-arms after the first spun jump, has **no persistence key** (dies every reboot), and appears on no protocol line. A correction that is invisible and amnesiac is worse than none |

### P2 — after the water data exists

Per-line checksum + sequence; advertisement battery/state; standby tier;
`dfu` gating; the non-ballistic self-diagnosis flag; Instinct 3 Solar;
sealed-product hardware.

### Explicitly NOT in the two weeks

**The power/standby restructure.** The largest and riskiest change available —
main loop, watchdog, I2C peripheral — for a device that runs a 2-hour session
comfortably today. Destabilising firmware in the fortnight before a one-shot
experiment is the wrong risk. It waits for real data.

Also out: two-central *support* (fix the cause, don't chase the feature),
NFC/solar/primary cells, store submission, bonding.

## 5. The freeze protocol

1. All firmware and watch changes land **≥4 days** before the session.
2. Then the **dress rehearsal runs on the exact build that goes in the water.**
3. After the freeze: no changes. Not "small" ones. This project has already
   spent four days on bugs a fresh build introduced.
4. If something breaks after the freeze, **the session moves — not the build.**

## 6. Decisions needed from you

1. **Session date** — everything sequences off it, including the mount's 24 h
   cure and the freeze.
2. **Which watch goes in the water?** Recommendation: **your Epix Gen 2** — the
   only device where the full chain is proven. The Instinct is your brother's
   and is simulator-only.
3. **Is the brother briefed?** He needs to mount the puck ≥24 h ahead, wear the
   watch, and jump roughly abeam of the kayak so the mast-ruler works.
4. **P1 order** if time is short: watch-first, or download-integrity-first.

## 7. How this plan stays honest

- `STATUS.md` is the source of truth; this plan may only reference it.
- `./tools/jump status` machine-checks the build, the suites, the hardware,
  and whether `STATUS.md` has gone stale relative to the code.
- No item enters this plan without first checking whether it is already done.
- No verdict without a measurement. That rule has now been paid for twice.
