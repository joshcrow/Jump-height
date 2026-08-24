# The plan

Rewritten 2026-08-14 from [STATUS.md](STATUS.md) — an evidence-first audit of
what is actually built — rather than from the previous plan's memory. That
earlier version listed already-finished work as TODO. This plan is structured
to make that impossible. (Archived: `plan-superseded-2026-08-14.md`.)

**Read [STATUS.md](STATUS.md) before adding anything here.** Run
`./tools/jump status` before believing any of it.

**On the day: [session-card.md](session-card.md)** — the one-page beach card
(checklist, the power-on ritual, what to film, what to write down, and what
the common failures actually mean).

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

> **2026-08-23: "~2 weeks out" has expired without being replaced.** Written
> 08-14, it would have meant ~08-28. **No date was ever set** — every mention
> of a water-day date in this repo asks for one. And a new dependency now
> sits in front of it that did not exist on 08-14: the field cannot be
> installed on the rider's watch except through the Connect IQ store
> (`de77de0`, `d5641d2`), whose stated review is 72 h and which has not been
> submitted. The session cannot be sooner than *submission + review*.
> Treat the constraint below as still true and the schedule as unknown.

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

## 3.4 What the final review changed (2026-08-14)

Three reviewers attacked this plan. The plan's shape survived; four things
in it did not, and three real bugs fell out. **Fixed in code already:**

- **A boot scan could reset the board and disable storage for the session.**
  `findJumpsAppendPoint`/`findTraceAppendPoint` walk the region block by block
  at boot with **no watchdog feed** — ~19k blocks on a full trace region. A
  well-used puck would reset there, latch StoreGuard, and run the whole
  session storage-less behind a `flash FAIL` row nobody reads at a beach.
  Never seen only because no region has ever been filled — which this session
  would do first. Feeds added.
- **`jump status`'s staleness gate failed GREEN.** Every path that could not
  produce both timestamps fell into the ✅ branch. A gate that fails green is
  worse than none: it asserts the thing it cannot check is fine. Now fails
  loud.
- **`jump sync` would have cried "trace hit its size cap"** on any session
  over ~44 minutes — an ESP32-era constant compared against a decoded-CSV
  size — and that *replaces* the live-vs-offline agreement verdict with a
  note. Disabled until the download carries the device's own `trace_bytes`.
- **The self-arming spin correction is now gated OFF.** It commits after ~8
  airborne samples, and it is applied to the magnitude *fed into the
  detector's gates* — so it changes which jumps are detected and what airtimes
  they get. Not reversible offline, no persistence key, on no protocol line,
  zero silicon time. The session runs the detector that was actually
  validated.

**Still to fix, and they change the session design:**

1. **The primary deliverable is not measurable as specified.** "Airborne |a|
   in the 0-0.07 g band" is confounded by rotation: the trace records `t,mag`
   only, `mag` is raw and carries ω²r, and 90 °/s of ordinary board pitch
   injects 0.13-0.25 g against a band 0.07 g wide. The gyro is read every
   sample and thrown away. **The fix is small and already sits in the file:**
   `JumpRecord` has 11 unused pad bytes — spend 8 on median airborne |a|,
   median |ω|, corrected |a| and window sample count, computed at 200 Hz over
   the detector's own airborne window. No format change, no region resize.
   Plus a 10-minute desk drop calibration to establish the instrument's own
   free-fall zero (a_v is exactly 0 in free fall by definition).
2. **The mast is the wrong ruler and the horizon is not the waterline.** A
   camera 0.8 m above the water sees the water plane 0.8 m below its own level
   line *at every distance* — using the horizon as zero adds **+53 % on a
   1.5 m jump**, a bias that does not average out. Use the board's own position
   in the takeoff/landing frames as zero, and **rider height in gear** as the
   ruler (2× the mast, roughly vertical, high contrast against sky). Shoot
   **1080p/120, not 4K/30** — frame quantisation at 30 fps is 6.7 %, the size
   of the effect under test. Better still: with a known ruler and known frame
   rate, fit the flight and recover `g_eff` directly — the same quantity the
   accelerometer measures, giving two independent measurements of the one
   number the project turns on.
3. ~~**No build identity.**~~ **DONE 2026-08-15.** `INFO` now carries
   `src=<hash>` — a hash of the firmware sources themselves
   (`tools/gen_build.py`), not a git sha. A sha was the obvious choice and is
   wrong twice over: writing HEAD into a tracked header is self-invalidating
   (the commit changes the sha it just recorded), and a dirty tree makes it
   lie outright — the compiler reads the working tree, not the commit. A
   source hash cannot be fooled by either. `./tools/jump selftest` now says
   in one line whether the board is running this tree, and every synced
   session records `build_src=` alongside its data.
4. **The capsule has never been water-tested** and `data/sessions/` is
   gitignored — the exact conditions that lost the 61-jump history. Bucket
   test (empty, then loaded for float) and a written two-copies-before-`clear`
   backup rule both go in P0.
5. **P0 order was self-invalidating** — the desk test was scheduled before the
   flash that voids it. Corrected below: flash first, then desk test, and the
   desk test is a **gate that runs after every flash**, not a one-time task.

## 4. Workstreams, prioritised

### P0 — must be true before the water (in order)

| # | Work | Why | Owner |
|---|---|---|---|
| 1 | **Flash + verify the batch** — *flashed; per-item verification still open* | BLE silent-drop fix, LED off, slow advertising, `system_off` drive, boot-scan watchdog feeds, spin self-arm gated. The batch is no longer "never on silicon": both `216f75f` (08-14 10:29) and `9277821` (08-14 10:46) predate `src=0c09863c` (flashed 08-17) and `src=66b5137b` (flashed 08-18), so the code has booted. What is still unverified is each fix's **effect** — STATUS.md's own *BLE silent-drop fix* and *chunk length latched* entries still read `built-unverified / never flashed` and now contradict the newer dated entries. Re-audit sub-item by sub-item; see STATUS.md → `## 2026-08-18` → *BLE batch: on silicon since 08-17, effect still unmeasured* | eng, needs a board |
| 2 | ~~**Download integrity**~~ **DONE 2026-08-14, verified at scale 2026-08-15** | §3.3 — and the desk test reads its own result back through this path. Fixed in `b7c3644` (STATUS.md *USB session download — WAS LOSSY, now fixed and verified*), then proven at 2x a real water session: two independent 9,872,675-byte downloads byte-identical, zero `INCOMPLETE` warnings (STATUS.md *Session-scale USB download — PROVEN at 2x a real water session*). Residual, non-blocking: **"BLE bulk export at this scale is still unmeasured"** — the desk-test/download gate itself runs over USB | eng |
| 3 | **Desk test, 3 tosses — AFTER the flash, and after EVERY flash** — *recurring gate, PASSED 2026-08-14 / 08-17 / 08-18; still gates the next flash* | The only proof a jump survives to storage on this build (§3.2). A gate, not a task. Passes so far: 08-14 (`desktest` PASS, 3 untethered tosses, best 0.238 m — STATUS.md *END-TO-END … GATE CLOSED*); 08-17 18:42 (3 jumps, best 1.00 m, `build_src=0c09863c` — `data/sessions/20260817-184208/`); 08-18 08:57 (3 jumps, best 1.28 m — `data/sessions/20260818-085718/`; the 1.285 m toss the watch reconciled at M2). **Still open for the pending OG post-death-run flash** (STATUS.md → `## 2026-08-18` → *HARDWARE DEPRECATION*). **2026-08-23: still open, and now against a much newer build.** The OG has been reflashed twice since this row was written — `src=9b35f734` (`dfecb73`, 08-22) and `src=e83f6395` (`29f03e1`, 08-23), carrying the whole F-01…F-21 audit. `29f03e1` records "one replug, one flash, `Device programmed.`, self-test PASS, 18 stored jumps and 1.55 MB of trace survived" — **self-test, not desk test.** By this row's own rule ("a gate, not a task") the 3-toss gate is owed on `e83f6395` | **you, 10 min** |
| 3b | ~~**Record median airborne \|a\| and \|ω\| per jump**~~ **DONE 2026-08-15** | §3.4 item 1 — without it the primary deliverable is unmeasurable. Columns shipped and populated on 10 real jumps; nine read `med_a` 0.039–0.154 g against the sim's 0–0.070 g ballistic band, and jump 7's 1.393 g exposed a false positive nothing else in the record distinguishes (STATUS.md *Per-jump flight physics — first real values*). **Still open underneath it:** there is **no zero calibration**, so *"0.079 g cannot yet be split into real signal vs sensor offset"* — a 10-minute drop calibration closes that. **2026-08-23, and this got worse:** the OG's previously-*measured* drop calibration is **gone**. It reads `CAL … source=defaults` on all three keys — compiled defaults, not measurements (`2041962` found it; `29f03e1` confirms it survived the latest flash and was not caused by it). The per-key provenance warning added in `4a97250` fired exactly as designed on the product board. So the drop ritual is now owed twice over: once for the zero, once to restore the height scale. Heights off this build are not calibrated heights | eng |
| 3c | **Bucket-test the capsule; write the backup rule** | §3.4 item 4 | **you, 15 min** |
| 4 | ~~**Fix the labeling procedure**~~ **DONE 2026-08-15** | §3.1 — `data-pipeline.md` rewritten around an independent ruler measurement, and enforced in code: `labels.csv` gains `height_src`, `sim/evaluate.py` excludes non-independent heights from RMSE and says why | eng |
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
NFC/solar/primary cells, ~~store submission~~, bonding.

> **CORRECTED 2026-08-23 — store submission is now the critical path, not
> "explicitly out."** Instinct 3 on fw 15.18 sweeps copied `.prg` files
> (`de77de0`, `d5641d2`), so with the brother riding (§6.2) the store is the
> only way to get the field onto the watch that will be on the water. The
> package is built (`garmin/jumpfield/bin/JumpField.iq`, 79,145 B, `95e61c1`
> then rebuilt `22ed92f`) and **not submitted**; Garmin's stated review is
> 72 h. This also moves **"Instinct 3 Solar"** out of the P2 list above.
>
> The rest of this section still holds, and the power/standby paragraph holds
> emphatically — nothing about standby has been built (`system_off()` is
> still reachable only from the `off` command, `firmware/src/main.cpp:1047-1057`).

## 5. The freeze protocol

0. **How "the exact build" is checked** (added 2026-08-15). `INFO` reports
   `src=<hash of the firmware sources>`. Run `./tools/jump selftest`: it
   prints either `✅ device is running THIS source tree (src=…)` or a warning
   naming both hashes. Before this, "the exact build" was an intention with
   no way to verify it — every build in this project's history reports
   `fw=0.4.3`, including the one that cost four days.
1. All firmware and watch changes land **≥4 days** before the session.
2. Then the **dress rehearsal runs on the exact build that goes in the water.**
3. After the freeze: no changes. Not "small" ones. This project has already
   spent four days on bugs a fresh build introduced.
4. If something breaks after the freeze, **the session moves — not the build.**

## 6. Decisions needed from you

1. **Session date** — everything sequences off it, including the mount's 24 h
   cure and the freeze. *(Still unset as of 2026-08-23: every mention of a
   water-day date in this repo asks for one; none states one. So the §5
   freeze protocol still has no window to open.)*
2. ~~**Which watch goes in the water?** Recommendation: **your Epix Gen 2** — the
   only device where the full chain is proven. The Instinct is your brother's
   and is simulator-only.~~
   - **DECIDED 2026-08-20, the opposite way** (`91709d3`, "The brother rides:
     Instinct promoted to P0"): **the brother is the rider and the Instinct
     3 Solar is the product's only screen on the water.** The owner is not on
     the wing. Everything "proven on the Epix" is therefore proven on the
     wrong watch.
   - **And "simulator-only" is no longer true either.** The full suite runs on
     the `instinct3solar45mm` target — 48/48, plus a memory probe showing no
     per-line leak (`10d2553`) — and the field was pushed to the physical
     watch on 08-22 (`4e35d26`: `Instinct 3 - 45mm, Solar`, fw 15.18, 17,996
     bytes, verified by read-back).
   - **The live question this became:** fw 15.18 **deletes** a sideloaded
     `.prg` (`de77de0`), because it keeps CIQ apps in an internal registry
     rather than as files (`d5641d2`). The Connect IQ store is now the only
     route onto the rider's wrist, and therefore on the critical path — see
     the corrected "not in the two weeks" list in §4.
3. **Is the brother briefed?** He needs to mount the puck ≥24 h ahead, wear the
   watch, and jump roughly abeam of the kayak so the mast-ruler works.
   *(2026-08-23: a written brief now exists — `docs/rider-brief.md` — but
   delivery-and-understood is still owed.)*
4. **P1 order** if time is short: watch-first, or download-integrity-first.

## 7. How this plan stays honest

- `STATUS.md` is the source of truth; this plan may only reference it.
- `./tools/jump status` machine-checks the build, the suites, the hardware,
  and whether `STATUS.md` has gone stale relative to the code.
  - **Caveat found 2026-08-23 — one of its lines is weaker than it looks.**
    `commands in binary (N)` is produced by running `strings` on the ELF and
    grepping for the `# commands:` string literal (`tools/jump:2563-2567`).
    That literal is the **help text** (`firmware/src/main.cpp:613`), not the
    dispatch table. The firmware dispatches `gyro` (`main.cpp:1145`),
    `pincensus` (`:735`) and `vbatscan` (`:1183`), none of which appear in
    it — so the line **under-reports by 3**. It confirms a binary was built
    from a tree that contains that string; it does not enumerate what the
    binary can do. Don't cite it as evidence of command coverage.
- No item enters this plan without first checking whether it is already done.
- No verdict without a measurement. That rule has now been paid for twice.
