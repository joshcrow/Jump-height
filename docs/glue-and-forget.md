# Glue-and-forget: the six-month vision, audited

Written 2026-08-20. **Revised the same night** after a five-agent adversarial
attack on the first version (two opus, three sonnet; web where reachable).
The attack confirmed the shape of the report and broke a number of its
specifics — including both halves of its headline. Changes are marked
inline and summarized in §9. The vision, verbatim intent:

> *We glue the device to the board and for six months forget it exists. Every
> ride I see jump height on my Garmin while riding, total time on foil vs
> activity time, other glanceable metrics. After the ride, more specific
> metrics augmenting the built-in Garmin activity. One day I have to charge it
> — no big deal. I'm confident in my data. Seamless; I stop being conscious of
> it.*

---

## 1. The honest verdict (survived attack, sharpened)

**The measurement instrument is nearly done. The appliance does not exist
yet.** Everything at the moment of a jump — detection, storage, the watch
link, FIT recording, storage self-healing — is built and mostly proven on
silicon. What does not exist is everything the vision needs during the other
99.9% of the six months: sleeping, waking, keeping its own counters and its
own *clock* honest across weeks of uptime, surviving unattended, and being
charged without a bench.

## 2. Decisions required from you (new section — the attack's sharpest point
was that the first draft forced none of these)

1. **The water-day date.** It does not exist anywhere in the repo. The freeze
   is *defined* as "changes land ≥4 days before the session," so with no date
   there is no freeze window, and every "does it fit the freeze" question —
   including two in this document's first draft — is unanswerable. Everything
   in §6 sequences off this. *(Also: 17 commits landed on 2026-08-20 alone;
   "the freeze holds" was aspiration, not description.)*

2. **Glue vs removable — the highest-leverage decision in this document.**
   The first draft inherited the hardest reading of "glue" without asking.
   The repo's own roadmap already specifies the alternative: a bonded **base**
   with a **removable puck** (GoPro-style adhesive mount, charge over USB
   with the capsule open). Your own words — "one day I have to charge it, no
   big deal" — read as removable. Choosing removable **deletes Era 3's
   inductive-charging problem entirely**, restores reset-button reachability
   (killing the sealed-case one-way-door and most of the sealed-OTA risk in
   §4), and turns adhesive qualification into a VHB-pad question. One
   decision, zero cost, deletes or keeps a whole era.

3. **The µA-meter purchase.** Era 2's keystone number — off current — cannot
   be measured with anything this project owns. By the declared
   time-as-instrument method, one standby A/B point at 10–30 µA costs
   **62–186 days**, and cell self-discharge (~8–10 µA-equivalent) is the same
   order as the signal, so time literally cannot separate them. The PPK2 was
   declined on 2026-08-16 — the right call *then*, when the question was mA.
   Era 2's question is µA, and it changes the calculus: buy a µA-capable
   meter (~$100–150), or accept that Era 2 item 3's deliverable is an upper
   bound, and say so.

4. **DECIDED (owner, 2026-08-20): the brother's Instinct is P0 — he is the
   rider on the water day; the owner is not on the wing.** This reshapes the
   watch work:
   - The Instinct stops being the port target and becomes **the product's
     only screen on the water**. Everything "proven on the Epix" is proven
     on the wrong watch: FIT dev fields, reconnect behavior, corruption
     gate, vibrate, layout tiers — all epix2-only evidence today. The
     Instinct has never even been sideloaded.
   - **Static memory is now measured and comfortable**: 12,417 B code+data
     against the 32,768 B datafield limit (monkeyc --build-stats, tonight).
     The "124 KB vs 32 KB" fear was PRG file size, which is not runtime
     memory. Runtime peak still needs one simulator session, but the
     plausible-OOM threat is largely retired.
   - The MIP screen is monochrome and 176×176: tier layouts were designed
     against it but never eyeballed even in the simulator.
   - **A rider brief becomes part of the deliverable.** The user is now
     someone who didn't build the system: he must start the activity
     (windsurf profile, field on a data screen — a one-time setup on HIS
     watch), know that dropouts are cosmetic, and never stop/discard the
     recording mid-session. One card, ten lines.
   - Units: HIS watch settings decide ft/m on screen and in the FIT.
   - **Two-central discipline on the day**: the owner on shore with a
     phone/laptop connecting to the puck while the brother rides is
     exactly the 2026-08-11 corruption config, and a phone `dump`
     mid-session blocks recording. Rule: no second central while the rider
     is on the water, unless `dualcentral.py` verification has passed
     first.

5. **The false-positive budget.** Default proposed so inaction still yields
   a verdict: **<1 phantom jump per riding hour**, adopted at the freeze
   date unless you override it. Without a number, the water day cannot
   produce a pass/fail on detection trust.

## 3. The two headline defects — corrected by the attack

### 3a. The timebase defect (worse, earlier, and less novel than first stated)

The float32 resolution table was verified exact (attacked by independent
recomputation and by `nextafterf()` on the literal expression). Everything
*around* it moved:

| uptime | float32 grid | vs the **5 ms detector clock**¹ | height error (300k-jump Monte Carlo)² |
|---|---|---|---|
| **18.2 h** | 7.8 ms | **grid first exceeds the sample interval** | negligible |
| 1 day | 7.8 ms | 1.6× | RMS 1.2 cm |
| **~4 days** | 31–62 ms | 6–12× | **RMS 4.7 cm — crosses the instrument's own 4.6 cm floor; 32% of jumps exceed it** |
| 7 days | 62.5 ms | 12.5× | RMS 9 cm (2× floor) |
| 14 days | 125 ms | 25× | height RMS ~17%, p95 ~33%, worst ~98% |
| 60–97 days | 500 ms | 100× | reported heights collapse toward two values |
| **6 months** | 1.0 s | 200× | **12.2% of real jumps SILENTLY DROPPED**³ |

¹ The first draft said "3× coarser than the 20 ms clock" — wrong twice: 20 ms
is the *trace log* rate; the detector runs at 200 Hz (5 ms). The grid exceeds
the sample interval at **18.2 hours — inside a single charge today.**
² First draft said "±14% airtime → ~28% height at 14 d" — that was roughly a
p90 presented as the central case. Corrected distribution shown.
³ Past ~3 months the dominant symptom flips from *wrong* jumps to *missing*
jumps: airtime quantizes to 0.000 s and fails the 0.25 s minimum, and the
80 ms free-fall confirm window stretches 1.6–12.5×, suppressing detection —
with no symptom on any surface. Exactly the trust failure Pillar 4 has no
budget for.

**Also corrected — this is not a novel discovery.** The repo found this bug
class before, in the trace codec, and chose the remedy (double). One site
went unswept. Two more unswept sites were found during the attack:
`jh_store.cpp:960` re-narrows with `(float)atof()` — which makes the trace
anchor's millisecond resolution fictional from **2.28 hours** of uptime, not
the 49.7 days the first draft claimed — and `trace_codec.h:172` uses
`lround()`, which is 32-bit on the ARM target (overflows at 24.9 days) but
64-bit on the host, **so the existing parity harness is structurally blind
to it**. The fix needs an explicit int32-bound assertion, not trust in the
suite.

**The fix, sharpened by attack:**
- `float → double` for `t_s`, `takeoff_time_`, `last_low_time_`,
  `JumpEvent::takeoff_time_s`, and `main.cpp:1326`; `llround` at the anchor.
  Cost: a few soft-float subtracts per 5 ms sample on the M4F. Sub-ms
  resolution for ~285,000 years.
- **Ready-made falsifier already in the repo:** `sim/detector.py` mirrors the
  detector in IEEE doubles, so offsetting every golden-trace timestamp by
  +604,800 s makes `simtest` diverge *today* and re-converge exactly when
  `t_s` becomes double. One-line test change.
- **The session-boundary timebase reset from the first draft carries an
  unstated livelock**: the detector holds absolute timestamps, so a reset
  while AIRBORNE makes both landing releases unfirable. Gate any reset on
  `state == RIDING`, or reset the detector with it.
- **A timebase reset is NOT session identity** (first draft conflated them):
  it gives a heuristic delimiter that fails on ordinary patterns (short
  session then long one). Identity needs a session counter column in the
  jump record — additive, independent, and cross-session ordering already
  survives via the `stored_jumps` key.
- **The blocker is conditional on a standby design choice the first draft
  suppressed**: if Era 2 standby is System OFF, every wake is a cold boot
  and `t0_us` resets for free — most of this section dissolves. If standby
  is System ON idle (the current design table's choice, to keep BLE alive),
  uptime accumulates and the double fix is mandatory. Decide together.

### 3b. Session counters (upgraded: not inferred — already demonstrated)

The first draft presented this as a code-read finding. It has already
happened in a real archive: the M2 activity's FIT (2026-08-18) recorded
`jumps=13` and `best_jump=1.285 m` — three desk tosses and ten fakes from
**eight hours before the activity**, on the same boot. The parse even
flagged `best_airtime` as an inconsistent pair. This is a reproduced
corruption of the exact artifact the water day exists to produce.

**The first draft's recommendation is withdrawn.** It suggested the firmware
fix "if it fits the freeze window." That fails three ways: (a) it
contradicts the same morning's lesson — seven defects, all from freeze-window
safety work; (b) there is no freeze window to fit (no date, §2.1); (c) it
would not even de-risk the water day — the ≥1 h-still boundary has a
realistic path to *never arming* that morning (desk test → drive (motion) →
rig <1 h → ride), landing the desk jumps in the FIT exactly as at M2.

**What replaces it:**
- **Water day, zero code:** the counters are RAM statics — *reboot the puck
  immediately before starting the activity.* One session-card line. (No
  `reboot` command exists; the routes are off+replug or the reset button —
  check button reachability in the mount before the day.)
- **Era 2, zero firmware:** watch-side delta in `Model.mc` (~25–30 lines,
  covered by the existing 40-test suite): baseline captured on first counted
  line, cleared by `onTimerReset()`, best/airtime as watch-local maxima —
  **with the restart guard the attack demanded** (persist the baseline in
  `Application.Storage`; re-baseline downward only if the puck rebooted),
  because an unguarded version re-baselines after a mid-activity field
  restart and burns count=0 into the FIT — this morning's pattern again.
  Blast radius: a bad watch build is a 10-minute re-sideload; a bad puck
  build is an OTA to a glued device.
- The puck's motion boundary and the watch's activity boundary are
  **different concepts that disagree in ordinary days** (90-min lunch;
  two activities 40 min apart). The watch is the only layer that knows
  activity boundaries; fix it there.

## 4. The blocker map, by pillar (corrected numbers marked)

### Pillar 1 — Power autonomy (still the widest gap; arithmetic corrected)

Current truth unchanged: one always-on state at ~7–11 mA; 25.7–34 h to
collapse; auto-sleep, motion wake, low-battery cutoff all unbuilt (motion
wake verified genuinely firmware-only: INT1 is routed to P0.11 — that
prerequisite is already paid). Off-current never measured.

**Corrected arithmetic** (first draft's "~1 year standby / 4–6 weeks riding"
was electronics-only and internally inconsistent):

| scenario | standby draw all-in¹ | standby life | riding life² |
|---|---|---|---|
| optimistic (2%/mo self-discharge, PCM≈0, 10 µA electronics) | ~17 µA | ~1.7 yr | ~10 weeks |
| realistic (BU-range self-discharge, ~10 µA PCM, 20 µA electronics) | ~65 µA | **~5 months** | ~6–8 weeks |
| pessimistic (hot-trunk cell, worst PCM) | >100 µA | ~2–3 months | ~5 weeks |

¹ The first draft omitted cell self-discharge (4–20%/month per Battery
University — up to the *entire* electronics budget by itself; the repo's own
2%/month figure is self-labeled "folklore-grade") and PCM quiescent current
(~10 µA order, unverified — absent from every doc in the repo).
² At 2–3 sessions/week × ~12–14 mAh/session against ~205–250 mAh usable.
The "~6 mA session" figure also rounds below the repo's own numbers
(0.72 × 9.7–10 mA ≈ 7 mA), and 0.72× was measured in an idle regime only —
no session-regime current measurement exists anywhere.

**The honest headline: "charge somewhere between monthly and bi-monthly,
dominated by cell physics, and only a measurement decides."** Still
compatible with the vision — but it is a *measurement away*, not an
arithmetic away.

**New, from the attack — Era 2 needs a kill criterion, stated up front:**
if measured off-current comes in at ~200 µA (plausible until the QSPI's
standby behavior is proven — the repo itself flags it as "the classic silent
standby killer on this exact board"), standby life is ~7 weeks and the
premise changes. Era 2 opens with the measurement against a pre-committed
threshold; it does not close with it.

### Pillar 2 — Session semantics and storage (dates corrected)

- **Jumps region fills at month 6–9, not "5–6"** (2048 ÷ 50–75/week; the
  firmware's own "~100 sessions" comment says 7.7–11.5 months). And the rate
  itself is uncited — **no real riding session has ever been recorded**;
  desk tests run 3–6 jumps. Consequence: this is a **dated month-5 review**,
  not an Era 2 blocker. (Silent-death-when-full still needs a symptom
  eventually; it just doesn't gate the appliance.)
- Trace lifecycle: shipped, but the attack corrected the framing: it is a
  **rolling ~5-hour window** (≈ the last 2–3 rides), evicting only when
  full. Fine for detection; it does cap raw-data retention for
  future-metrics work unless synced within a couple of rides.
- Timebase and session identity: see §3a.

### Pillar 3 — Garmin platform (verdicts updated by web research)

1. **Two-central verification** — unchanged, still the top item; the
   config is your brother's literal use case.
2. **PuckLink wedge states** — unchanged (~5-line fixes).
3. **Glance never run on a wrist** — unchanged; the API-table half
   re-verified clean, the hardware half is the 10-minute sideload.
4. **Time-on-foil architecture — materially improved by the attack.**
   "Cannot be backfilled" is true only for RECORD-scope fields.
   SESSION-scope fields are **last-write-wins up to save** (FitOut.mc
   already depends on this). So only the raw foil *signal* must come from
   the puck — **the threshold and accumulator belong on the watch**, where a
   retune after the water day is a 10-minute sideload instead of an OTA to a
   glued device. (Needs a periodic puck push line or a change to the
   stats-only command policy — small, but a spec change.) For one-off
   historical recovery, offline FIT-SDK tooling *can* post-hoc annotate
   files — an escape hatch, not the product path.
5. **Sideloads vs firmware updates — upgraded from hypothetical to
   documented risk**: the Fenix 7/Epix family shipped a firmware fix
   (v19.41) specifically for CIQ apps crashing after updates, and SDK
   signature changes have forced sideload rebuilds. Standing habit: after
   every watch OTA, verify the field renders before the next ride.
   The store channel remains the structural fix — and Garmin's live docs
   now state **review within 72 hours**, so it is cheaper than feared.
6. **Strava does not render CIQ developer fields** (allowlist only). If
   rides cross-post to Strava, every puck metric silently vanishes there.
   Garmin Connect is the archive of record; say so once and accept it.
7. **The 32 B/message FIT budget is now provisional, not settled** — quoted
   from the installed SDK's docs, but absent from current live docs and
   uncorroborated anywhere. One empirical write-and-inspect test settles
   it; until then don't architect against the exact number.
8. **Garmin Connect rendering (M4) still never checked** — ten minutes,
   zero code, the FIT already exists.
9. New from the attack: our firmware sets the LE General Discoverable flag,
   and real Garmin watches **drop advertisers without it**. Nothing in the
   repo documents that this flag is load-bearing. One comment line.

### Pillar 4 — Data trust (unchanged, plus one link)

Unchanged: false-positive budget (default now proposed, §2.5), sensor-death
invisible on the wrist, no long-horizon IMU sanity check. New link: the
timebase defect's six-month failure mode (silent dropped jumps, §3a) lands
in this pillar — the two compound.

### Pillar 5 — Hardware survival (one claim retracted)

- **"OTA on a glued device is proven" is retracted.** OTA is proven on a
  bench board with a reset button at hand. The documented dark-bootloader
  failure (mid-transfer disconnect → no advertisement, no connection, only
  physical reset recovers — observed twice) makes a failed OTA on a *sealed*
  puck a dead puck. Worse, found by the attack: **the `dfu` trigger is
  unauthenticated** — any BLE peer in range can command a sealed puck into
  its bootloader. Era 2 gains an item 0: OTA safety rules (spare-first,
  never within N days of a session, authenticated trigger, and ideally a
  bootloader-side timeout back to the app) — or the removable-puck decision
  (§2.2) makes most of this moot.
- Adhesive: **moved to week 0** (below) — the 6-month soak is calendar time.
- Bucket test, trunk-heat characterization: unchanged, also week 0.

## 5. What is already strong (corrected)

- Detection works and survives churn — 6/6 on the desk test through seven
  storage/BLE changes.
- OTA machinery works *on the bench* (two back-to-back wireless flashes,
  bootloader updated over the air). The sealed-case caveat is §4 Pillar 5.
- FIT developer fields verified in a real saved activity, byte-exact.
- Storage self-heals, survives total battery death, full-region boot scan
  passed live.
- The advertised-battery glance path is real on the air; the wrist render is
  the missing half.
- The cell itself held up under attack: 250 mAh nameplate is genuinely
  usable (the gauge floor *reserves* ~300 mV of real capacity), and session
  current draws are far below any derating regime.

## 6. The sequenced road (restructured by the attack)

**Week 0 — latency-gated items, start regardless of era** (the first draft
sequenced by dependency and ignored calendar):
1. Glue an adhesive coupon to scrap and drop it in a saltwater bucket
   **today** — 6-month data has a 6-month clock. (Also already P0 in
   plan.md, which the first draft's Era 3 placement contradicted.)
2. Temperature logger (or just a max-min thermometer) wherever the board
   actually lives between sessions.
3. The µA-meter decision (§2.3).
4. The store submission draft (72 h review makes this cheap).
5. Set the water-day date (§2.1) — everything else keys off it.

**Era 1 — prove the instrument (→ water day).** Freeze *starts when the date
exists*. Zero-code additions only:
- Session-card line: **reboot the puck right before starting the activity**
  (§3b), plus reset-button reachability check in the mount.
- False-positive budget adopted (§2.5).
- The cheap tests (§7) in idle moments.

**Era 2 — build the appliance (post-water).** Relabeled honestly:
**≈2–3 weeks of coding; 4–6 weeks to *close* only if the week-0 gates
opened** (meter, water-day labels, store filing). This project's measured
write→trustworthy-verdict multiplier is 3–5×; the tail is verification, not
authorship. Order:
0. OTA safety rules for any non-bench puck (or moot via §2.2).
1. The double-timebase sweep + falsifier (§3a) — ships with anything.
2. Watch-side session delta with the restart guard (§3b).
3. Standby: wake engine + SENSE/DETECT + auto-off + cutoff — **opening
   with the off-current measurement against the pre-committed kill
   threshold** (§4 P1).
4. Live foil signal from the puck; threshold + accumulator on the watch
   (§4 P3.4).
5. Watch hardening: PuckLink timeouts/DEAD-retry, dualcentral verification,
   glance on wrist, low-battery warning.
6. Store submission lands.
- *Month-5 dated review:* jumps-region lifecycle (§4 P2).

**Era 3 — the sealed unit.** **Exists only if §2.2 chooses "glued/potted."**
If removable wins, Era 3 reduces to: qualified adhesive base + capsule
maintenance schedule.

## 7. Cheap tests that close big assumptions

| test | closes | cost |
|---|---|---|
| Open the 2026-08-18 activity on connect.garmin.com | M4: where dev fields render | 10 min, zero code |
| Sideload puckglance to the epix | 5 glance assumptions incl. scan-in-glance | 10 min |
| Field on screen 2, fakejump, flip back | compute()-runs-off-screen (the design's core bet) | 10 min |
| Start activity, fakejump ×3, end, start new activity | **onTimerReset fires on real hardware** (the watch-side fix depends on it; simulator-vs-silicon has bitten twice) | 10 min |
| `dualcentral.py` + 20 fakejumps, 2 centrals | the corruption fix, before any two-watch outing | 1 evening |
| Simulator memory view, instinct target | runtime peak vs 32 KB (static now measured: 12.4 KB) | 1 hour |
| Write >32 B of dev fields in one message, inspect the FIT | the provisional FIT budget (§4 P3.7) | 30 min |
| Pause activity, fakejump ×3, resume, save, parse FIT | auto-pause record semantics | 15 min |
| Two 3 h wear days, field on/off | watch battery cost | passive |
| After every watch firmware OTA: does the field render? | the documented Epix-family update risk | 1 min, standing habit |
| `simtest` with golden timestamps +604,800 s | the timebase falsifier diverges as predicted | 15 min |

## 8. The one-paragraph version (rewritten)

The puck measures right *today* and the watch pipeline is real; nothing
about the vision's moments is in doubt. The gap is the time between
moments, and it is wider in places than the first draft said and narrower
in others: the timebase quietly degrades from **hours** of uptime (and by
six months is silently *dropping* one jump in eight), the session counters
have already corrupted a real archive, standby life is governed by cell
physics the repo has never had an instrument to see, and a failed OTA on a
sealed puck is a dead puck that any BLE peer in range can trigger. Against
that: the water-day fix for the counters is a reboot, the timebase fix is
one type and the falsifier already exists, time-on-foil turns out to belong
mostly on the watch where retuning is trivial, the jumps region lasts 6–9
months not 5, and one unasked question — *glued, or removable?* — can
delete the hardest remaining era outright. Five decisions are yours (§2);
everything else is work on a known path.

## 9. Revision changelog (what the attack changed)

- **Timebase**: table verified exact; "3× vs 20 ms clock" → 12.5× vs the
  real 5 ms clock, grid exceeds sample interval at 18.2 h; "±28% at 14 d" →
  RMS 17% / p95 33%; materiality moved to ~4 days; six-month silent-miss
  mode added (12.2% dropped, confirm-gate stretch); double-rounding (~2 ULP)
  added; "novel discovery" retracted (class known, one site unswept — two
  more unswept sites found); trace-anchor horizon 49.7 d → **2.28 h**;
  host harness shown structurally blind to the lround overflow; livelock
  hazard in the proposed reset; reset ≠ session identity; blocker shown
  conditional on the standby design choice.
- **Session counters**: upgraded from code-read to demonstrated-in-FIT;
  freeze-window firmware recommendation **withdrawn**; replaced by
  reboot-ritual (water day) + guarded watch-side delta (Era 2); boundary
  mismatch cases added.
- **Power**: self-discharge and PCM added (first draft omitted both);
  "1 year standby" → ~5 months realistic (range 2 mo–1.7 yr); "4–6 weeks
  riding" → ~5–10 weeks (first draft was internally inconsistent); kill
  criterion added; µA-meter decision forced.
- **Garmin**: Strava non-rendering added; Epix-family update-crash
  precedent added; 32 B budget downgraded to provisional; store review
  tightened to 72 h; time-on-foil moved watch-side (SESSION last-write-wins);
  discoverable-flag dependency documented.
- **Storage/OTA**: jumps fill month 5–6 → **6–9** (rate itself uncited),
  demoted to month-5 review; "OTA proven on glued device" **retracted**;
  unauthenticated `dfu` trigger added; trace framed as rolling ~5 h window.
- **Sequencing**: "freeze holds" corrected (no date → no freeze); Era 2
  relabeled (2–3 wk code, 4–6 wk close iff gates open); week-0
  latency-gated list added; adhesive moved from Era 3 to week 0; §2
  decisions section added, including glue-vs-removable.
- **Survived attack unchanged**: the ULP table, the honest verdict, the
  motion-wake-is-firmware characterization, the 250 mAh usable-capacity
  assumption, and the overall pillar structure.
