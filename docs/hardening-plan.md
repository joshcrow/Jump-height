# Hardening plan — building this like a product that ships

Written 2026-08-21, owner's direction: *"build like this is a real product
going to market. The testing will come and we have that workstream figured
out. Therefore we have to plan ahead."*

This is the engineering master plan from here to the appliance. It does not
replace [glue-and-forget.md](glue-and-forget.md) (the vision audit),
[execution-plan.md](execution-plan.md) (this week's tactics) or
[unconsidered-cases.md](unconsidered-cases.md) (the risk survey) — it
sequences everything they contain into **release trains with gates**, the way
a product gets hardened rather than the way a prototype accretes.

---

## 0. Operating principles (paid for, this week, in specifics)

Every one of these was learned by breaking something. They are the review
checklist for all work below.

1. **Failure-path first.** Seven of seven defects in the storage batch made a
   failure path worse while the success path looked perfect. Every change is
   reviewed by asking *what does this do when the thing it depends on fails*
   before asking whether it works.
2. **Installed is not proven.** Simulator-green, committed, even flashed —
   none of it counts until the failure it guards against has been staged and
   watched. Every hardening item below carries an explicit on-hardware proof.
3. **Tests must be watched failing.** Mutation-test every new guard (revert
   the fix, watch the test fail, restore). Three fixtures this week encoded
   states the device cannot produce; a test that passes either way is worse
   than none.
4. **No silent failures, ever.** The device's own warning lines are read by
   every surface. Exit codes are never trusted over artifacts. A tool that
   cannot do its job says so instead of producing something output-shaped
   (the black-screenshot rule).
5. **A fix must not out-damage its bug.** The timebase assert would have
   bricked a 25-day-old puck to catch a wrong timestamp; the remount would
   have reset a session to heal storage. Saturate, degrade, report — never
   abort on a device nobody can reach.
6. **One flash per replug; batch firmware; `Device programmed` or it did not
   happen.** The serial budget is measured (~1/replug on this host).
7. **Identity before diagnosis.** Four "dead hardware" verdicts were wrong;
   all began by trusting a reading before establishing configuration. The
   registry is ground truth; the electrical test is a cross-check.
8. **History must not lie.** Explicit-path staging while agents run; every
   commit message states what was verified and how.

## 1. The release trains

Four releases, each with a hard gate. Nothing rides a train it wasn't
verified for; anything that misses a gate waits for the next train — the
water-day train does not slip for features.

### R1 — "Water-day build" (freeze candidate; gate = the session date −4 days)

The build that goes on the water. **Scope is closed as of now** except for
defects found by the remaining testing workstream (Instinct night, weekend
rehearsal).

| item | state | remaining proof |
|---|---|---|
| Seven storage/BLE fixes | 42-cycle soak ran on the **third board**; the spare's evidence is different in kind (dualcentral pass, flash-wedge recovery); OG carries the build | OG desk test done |
| Monotonic STATS reseed | staged in tonight's `.prg` | runbook §4b on-hardware proof |
| Puck-id header | staged | see `E2C4` on a wrist while connected |
| Two-central JUMP delivery | **unproven** — tonight's Epix+Instinct test | 20/20 on both, `!` absent |
| Reboot-before-activity ritual | on the session card | brother executes it in rehearsal |
| Rider brief | written | delivered and understood |
| **Calibration provenance selftest row** | **to build — R1's one *new* code item** | see §2.1 |
| Timebase rework (double sweep + saturation) | **rides along on ANY OG flash** — firmware builds from HEAD, not cherry-picks. On the spare as of 2026-08-21 (`37394ae` lineage). | its 48 h soak IS the R1 soak below |
| OG re-calibration (drop ritual) | **owner action** | reads back `source=device` |

**R1 gate:** full suite green · **48 h soak of the R1 candidate build on the
spare — which is the HEAD lineage including the timebase rework, because
firmware builds from the tree, not cherry-picks; there is no "just the
provenance row" flash** · pre-flash adversarial review of any change after
tonight · Instinct night items 1–7 **and 4b** pass · rehearsal ritual
executed by the rider, not the owner · calibration provenance row PASSES on
the OG.

**The one OG flash R1 requires, scheduled explicitly** (resolving a
contradiction an adversarial review caught — the first draft simultaneously
required this flash in the gate and forbade it in the cadence): provenance
row lands on the spare → 48 h soak clean → **single OG flash** (one replug,
one attempt, `Device programmed`) → owner re-runs the drop calibration →
`CAL … source=device` read back → gate can close. Everything else queues
for R2.

### R2 — "Appliance core" (post-water; ≈2–3 wk code, closes only if week-0 gates opened)

The release that makes glue-and-forget *true*. **Each numbered item below is
its own independently-gated sub-release (R2a–R2f), shippable alone** — a
review correctly called the first draft's single-gate bundle a big-bang train
wearing incremental clothing, contradicted the same day by our own practice
(the puck-id header shipped out-of-band by owner choice, `582ffe4`). Trains
gate *shipping*; they must not serialize *proving*. Order is dependency
order:

1. **OTA safety rules** (or mooted by the glue-vs-removable decision):
   spare-first, never-near-session, authenticated `dfu` trigger, deliberate
   mid-transfer-abort characterization on the third board.
2. **Timebase completion.** The double sweep landed (commit `37394ae`);
   what remains is the **session-relative reset** — gated on detector state
   == RIDING (the livelock the attack found), plus the **session column** in
   the jump record (reset ≠ identity). Proof: multi-day soak on the third
   board with timestamps inspected, not assumed.
3. **Session semantics on the watch** (R2c): the delta-with-restart-guard
   design (Model.mc, `Application.Storage` baseline, `onTimerReset` clear) —
   gated on `onTimerReset` confirmed on **any real device; the Epix on any
   quiet evening qualifies** (tonight's runbook lists it only as a stretch
   item, so do not couple this to the brother's scarce time).
4. **Standby engine** on the third board (USB = unstrandable): LSM6DS3 wake
   registers, SENSE/DETECT, auto-off, low-battery cutoff. **Opens with the
   off-current measurement against the pre-committed kill threshold
   (>200 µA ⇒ stop and find the leak; QSPI first suspect).** Requires the
   µA-meter decision.
5. **Watch hardening**: PuckLink state deadlines + DEAD retry; duration-aware
   staleness (read the `_staleSinceMs` that is already stored); `NO REC` on
   every layout tier.
6. **Foil-signal spec**: raw variance metric line from the puck; threshold +
   accumulator watch-side (retune = sideload, not OTA). Prototype against
   fake lines now; tune from water-day labels.

**R2 sub-gates:** each of R2a–R2f closes alone, by a staged failure (wake from real
motion after real auto-off; a killed field mid-activity recovering its
baseline; a puck surviving its own low-battery cutoff with storage intact) ·
7-day continuous soak on the third board — **sequenced strictly AFTER the
destructive OTA-abort work (R2a) has finished and the board re-verified
healthy**; same board, so the soak clock cannot start until the abort
campaign ends, and the recovery time (dark-bootloader states need a physical
reset) is schedule, not surprise ·
battery: one full charge-to-cutoff cycle on the R2 build with the drain
inside the fixed-window envelope.

### R3 — "Two-rider quiver"

Everything in [puck-identity.md](puck-identity.md): bind-and-prefer with the
two-session re-bind rule, name-beats-UUID matching, one-central rider build,
physical labels, **per-board calibration** (rides on identity; three boards
currently share one board's constant). Plus the sync-side board-identity
stamp so sessions from different boards are distinguishable in the archive.

**R3 gate:** the beach-walk scenario staged deliberately (two pucks, walk
away mid-activity) with the right binding surviving · calibration per board
measured, stored, and provenance-checked · a full weekend of two-rider use
with zero identity incidents.

### R4 — "Sealed product" (exists only if glue-vs-removable chooses glued)

Inductive charging or pogo dock, potting, adhesive qualification (the coupon soak
starts its 6-month clock **only when STATUS records it going into the
bucket — as of 2026-08-21 no such entry exists**, so it has NOT started;
this plan's first draft claimed otherwise, which is exactly the
claimed-done-but-wasn't failure STATUS.md §rules exists to stop), store-distributed watch app (draft
exists; 72 h review), Qi-through-the-wall experiment before any custom
hardware.

## 2. The hardening backlog, by subsystem

Sized: S = hours, M = a day, L = multi-day. Each carries its proof.

### 2.1 Firmware core
| item | size | proof |
|---|---|---|
| **Calibration provenance** (R1) — BUILT 2026-08-21, with one design change from this spec: the FAIL policy lives **host-side**, not as a device selftest row. Reasons: bench boards have never been drop-calibrated so "defaults" is their honest state, `jump flash` gates on all-rows-PASS (a device FAIL would break every bench flash), and a wiped device cannot distinguish never-calibrated from lost-calibration — only the registry knows the expectation. Device now reports **per key** (`off_src/scale_src/vbat_src` adder keys, fixing the OR that hid single-key fallbacks); the CLI prints an unmissable `⚠️ CALIBRATION PROVENANCE` on any fallback; the session card blocks on it. Mutation-tested. | S — done | on-silicon proof rides the R1 spare flash: wipe ONE key, warning names it, others stay quiet |
| Session-relative timebase + session column (R2b) — **the session column and the §2.2 jumps-region lifecycle decision touch the same on-flash record format; decide the lifecycle FIRST and land both as ONE schema change (or add a version byte), never two uncoordinated flashes to the same struct** | M | multi-day soak, inspected timestamps |
| Low-battery cutoff with storage-safe shutdown (R2.4) | M | staged drain-to-cutoff, storage intact after |
| Wake engine + auto-off (R2.4) | L | 100 staged wake cycles from real motion, zero misses |
| Authenticated `dfu` (R2.1) | S–M | unauthenticated trigger rejected on-air |

### 2.2 Storage
| item | size | proof |
|---|---|---|
| Jumps-region lifecycle **decision** by month-5 review (ring vs archive-on-FIT) | decision + M | staged full region behaves per decision |
| `fs=down` + tx_drops + reas + crumb unified into one **health line** every surface reads | S | each induced fault appears on watch, web, CLI |

### 2.3 BLE / link (task #15 absorbed here)
| item | size | proof |
|---|---|---|
| PuckLink deadlines + DEAD retry | S | pull the puck's power mid-pair; field recovers within 90 s. **Puck-side baseline now measured (2026-08-22): 2,068/2,068 cycles, full recovery median 1.9 s / p95 2.0 s — the puck is never the bottleneck, so any watch-side recovery slower than ~5 s is the watch's own state machine and unambiguously attributable.** |
| Duration-aware staleness | S | 20-min silent puck shows "no data — 20 min" |
| Two-central JUMP delivery (tonight) then **retire second central in rider build** (R3) | S | dualjump equivalent on two real hosts |

### 2.4 Watch app
| item | size | proof |
|---|---|---|
| Session delta + restart guard (R2.3) | M | kill the field mid-activity (staged), count survives |
| `NO REC`/health on all tiers | S | photograph each tier with storage down |
| Sideload-survival habit: post-watch-OTA verification note in rider brief | S | next real watch update |

### 2.5 Tools & web
| item | size | proof |
|---|---|---|
| `jump doctor`: one command = boards + selftest + provenance + health line, exit-coded for scripts | M | run against all three boards, one lying case staged |
| Session archive backup discipline (the laptop is the only copy of synced sessions) | decision + S | restore drill from the second copy |
| Height-algorithm version stamped into every session export (comparability across firmware changes) | S | two exports across a params change carry distinct stamps |

### 2.6 Hardware / mechanical
| item | size | proof |
|---|---|---|
| Foam-pack cell + strain-relief + photograph (next capsule opening — adversary's replacement for shock isolation) | S | photo on record |
| Reseal card zip-tied to capsule | S | it survives the weekend rehearsal |
| Coupon soak + trunk thermometer (started) | calendar | monthly readout in STATUS |

## 3. Verification infrastructure (the workstream that hardens the hardening)

1. **The nightly loop, formalized:** simtest + pytest + Instinct unit suite +
   storagesoak (rotating board) as a single scripted run with one PASS/FAIL
   artifact. Everything exists; it needs one entry point and a cron.
2. **Falsifier library:** the timebase golden-offset trick generalized —
   every physics-adjacent fix ships with a harness that demonstrably fails on
   the old code. Existing examples: +604,800 s goldens, mock flash AND-merge,
   dualcentral READY-count.
3. **Board farm roles held stable:** OG = product truth, spare = first-flash
   + BLE bench, third = destructive/standby. Written into the registry;
   violated only with a written reason.
4. **Release checklist template** distilled from R1's gate, reused every
   train.

## 4. Decisions the plan needs (unchanged, restated once)

Water-day date → defines R1's gate. Glue vs removable → R4 exists or not.
µA meter → R2.4 measures or bounds. Charging cadence + owner → the brief.
Third rider ever → R3 scope.

## 5. Cadence

- **Now → water day:** R1 only. Feature work happens on the *third board and
  simulator* exclusively; nothing new lands on the OG. Agents run the
  parallel-safe items (2.1 provenance row, 2.3 deadlines/staleness, 2.5
  doctor) as candidates that queue for R2 — built and tested now, flashed
  later.
- **Water day + 1 week:** R2 opens with the off-current measurement and the
  water-day label fits.
- **R2 close → R3:** identity work, second rider onboarded on the proven
  base.
- **R4:** only after the glued/removable decision, and only if glued.

The discipline this plan encodes: **trains, not streams.** Everything can be
built whenever there is capacity; nothing ships except at a gate, with its
proof attached.
