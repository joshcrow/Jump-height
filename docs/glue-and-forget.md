# Glue-and-forget: the six-month vision

The intent, verbatim:

> *We glue the device to the board and for six months forget it exists. Every
> ride I see jump height on my Garmin while riding, total time on foil vs
> activity time, other glanceable metrics. After the ride, more specific
> metrics augmenting the built-in Garmin activity. One day I have to charge it
> — no big deal. I'm confident in my data. Seamless; I stop being conscious of
> it.*

Written 2026-08-20 and revised the same night after a five-agent adversarial
attack that broke both halves of its own headline. Cut from 669 lines to this
on 2026-08-23 — the pillar-by-pillar analysis and the attack changelog are at
`git show archive/docs-2026-08-23:docs/glue-and-forget.md`. What remains is
what is still open.

---

## 1. The verdict

**The measurement instrument is nearly done. The appliance does not exist.**

Everything at the *moment* of a jump — detection, storage, the watch link, FIT
recording, storage self-healing — is built and mostly proven on silicon. What
does not exist is everything the vision needs during the other 99.9% of six
months: sleeping, waking, keeping its own counters and its own clock honest
across weeks of uptime, surviving unattended, and being charged without a bench.

## 2. Decisions that are yours — all five still open

1. **The water-day date.** It exists nowhere in the repo. The freeze is
   *defined* as "changes land ≥4 days before the session", so with no date
   there is no freeze window and every "does this fit the freeze" question is
   unanswerable. Everything in §5 sequences off this.

2. **Glue vs removable — the highest-leverage decision here.** The first draft
   inherited the hardest reading of "glue" without asking. The alternative is a
   bonded *base* with a *removable* puck, charged over USB. Your own words —
   *"one day I have to charge it, no big deal"* — read as removable. Choosing
   removable **deletes the inductive-charging era outright**, restores
   reset-button reachability, and kills most of the sealed-OTA risk. One
   decision, zero cost.

3. **The µA-meter purchase.** Off-current cannot be measured with anything this
   project owns. By the time-as-instrument method, one standby A/B point at
   10–30 µA costs **62–186 days**, and cell self-discharge (~8–10 µA-equivalent)
   is the same order as the signal — so time literally cannot separate them.
   Buy a µA-capable meter (~$100–150), or accept that the deliverable is an
   upper bound and say so.

4. **The false-positive budget.** Proposed default so inaction still yields a
   verdict: **<1 phantom jump per riding hour.** Without a number the water day
   cannot produce a pass/fail on detection trust.

5. **Charging cadence** — what "one day I have to charge it" actually means in
   practice, given riding at ~5–10 weeks per charge.

**Settled (2026-08-20):** the owner's **brother is the rider**; his Instinct 3
Solar is the product's only screen on the water. The Epix is the dev bench, so
everything "proven on the Epix" is proven on the wrong watch. Details in
`docs/watch.md`.

## 3. The two headline defects

### 3a. The float32 timebase — **CLOSED 2026-08-23**

*(Section number preserved: `firmware/include/jump_detector.h:137` cites it.)*

Uptime past ~18 hours coarsened the float32 timestamp grid beyond the 5 ms
detector clock; by six months **12.2% of real jumps were silently dropped**,
with no symptom on any surface. Fixed and verified:

- `jump_detector.h:62` `double takeoff_time_s`, `:152` `update(double t_s, …)`
- `jh_store.cpp:1001` no longer re-narrows `atof` to float
- `trace_codec.h:224` `llround` with an explicit int32 bound check
- falsifier passes — `tools/tests/test_timebase_falsifier.py:48` (+604,800 s)
- commit `37394ae` caught a hazard *in the fix*: an abort that would have
  killed a puck at 24.9 days, replaced with saturation

Still open from this section: a session-relative timebase reset (which
livelocks if fired while AIRBORNE — gate it on `state == RIDING`), and session
identity, which needs a session-counter column and is **not** the same thing as
a timebase reset.

### 3b. Session counters — **STILL OPEN**

Not inferred; already demonstrated. A real archived FIT (2026-08-18) recorded
`jumps=13` and `best_jump=1.285 m` — three desk tosses and ten fakes from
**eight hours before the activity**, on the same boot. This is a reproduced
corruption of the exact artifact the water day exists to produce.

- **Water day, zero code:** the counters are RAM statics — **reboot the puck
  immediately before starting the activity.** On the session card.
- **Later, zero firmware:** a watch-side delta in `Model.mc` with a restart
  guard (persist the baseline in `Application.Storage`, re-baseline downward
  only if the puck rebooted). Unguarded, a mid-activity field restart burns
  `count=0` into the FIT.
- Do **not** mistake the monotonic-decrease guards already shipped for this
  fix; they solve the opposite failure.

## 4. The remaining gaps

- **Power autonomy — the widest.** One always-on state; auto-sleep, motion wake
  and low-battery cutoff are all unbuilt (motion wake is genuinely
  firmware-only: INT1 is routed to P0.11, so that prerequisite is paid).
  Current-draw figures are **pending re-measurement**, not corrected — DC/DC at
  boot changed the regime and nobody has measured since.
- **Hardware survival.** A failed OTA on a sealed puck is a dead puck: the
  dark-bootloader state is recoverable only by physical reset, observed twice.
  The `dfu` trigger is **unauthenticated** — any BLE peer in range can command a
  puck into its bootloader. Decision 2 above makes most of this moot.
- **Data trust.** No long-horizon IMU sanity check; sensor death is invisible on
  the wrist. **The vision implies a requirement none of the pillars names: the
  device has to carry its own STATUS.** Every trust check this project owns
  terminates in a human running a bench command, and a glued puck in month four
  has no bench and no operator.
- **Storage lifecycle.** The jumps region (2048 records) fills somewhere around
  month 6–9 — rate uncited, because no real session has ever been recorded.

## 5. The road

**Week 0 — calendar-gated, start regardless of era.** Four of these five had
not started as of 2026-08-23:

1. Glue an adhesive coupon to scrap and drop it in a saltwater bucket **today**
   — six-month data needs a six-month clock.
2. A temperature logger wherever the board actually lives between sessions.
3. The µA-meter decision (§2.3).
4. The store submission — now the *only* route to the rider's watch, with an
   external review queue in front of it.
5. Set the water-day date (§2.1).

**Era 1 — prove the instrument (→ water day).** Zero-code only: reboot-the-puck
on the session card, reset-button reachability checked in the mount, the
false-positive budget adopted.

**Era 2 — build the appliance (post-water).** ≈2–3 weeks of coding, 4–6 weeks
to *close*, and only if the week-0 gates opened. In order: OTA safety rules
for any non-bench puck; the watch-side session delta; standby (opening with the
off-current measurement against a pre-committed kill threshold); the live foil
signal with threshold and accumulator on the watch; watch hardening.

**Era 3 — the sealed unit.** Exists **only if** decision 2 chooses glued. If
removable wins, this reduces to a qualified adhesive base plus a capsule
maintenance schedule.

## 6. In one paragraph

The puck measures right today and the watch pipeline is real; nothing about the
vision's *moments* is in doubt. The gap is the time *between* moments. The
worst of it — a timebase that silently dropped one jump in eight by month six —
is now closed. What remains: the session counters have already corrupted a real
archive, standby life is governed by cell physics this project has never had an
instrument to see, and a failed OTA on a sealed puck is a dead puck that any
BLE peer in range can trigger. Against that, the water-day fix for the counters
is a reboot, time-on-foil turns out to belong on the watch where retuning is
trivial, and one unasked question — *glued, or removable?* — can delete the
hardest remaining era outright. Five decisions are yours; everything else is
work on a known path.
