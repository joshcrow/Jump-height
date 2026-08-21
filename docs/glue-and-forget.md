# Glue-and-forget: the six-month vision, audited

Written 2026-08-20, on the owner's direction. The vision, verbatim intent:

> *We glue the device to the board and for six months forget it exists. Every
> ride I see jump height on my Garmin while riding, total time on foil vs
> activity time, other glanceable metrics. After the ride, more specific
> metrics augmenting the built-in Garmin activity. One day I have to charge it
> — no big deal. I'm confident in my data. Seamless; I stop being conscious of
> it.*

Method: six domain audits (power, session lifecycle, BLE/watch, metrics
pipeline, hardware survival, data trust), one adversary hunting cross-domain
failures, and one deep Garmin/Connect IQ platform audit against Garmin's own
SDK documentation. Findings below were spot-verified against source before
being written down; the two headline discoveries were re-verified by hand.

---

## 1. The honest verdict

**The measurement instrument is nearly done. The appliance does not exist
yet.** Everything the vision needs at the moment of a jump — detection,
storage, the watch link, FIT recording, OTA updates on a glued device,
storage self-healing — is built and mostly proven on silicon. What does not
exist is everything the vision needs during the other 99.9% of the six
months: sleeping, waking, keeping its own counters honest across weeks of
uptime, surviving unattended, and being charged without a bench.

The project's own plan already knew part of this ("the power/standby
restructure waits for real data"). What the audit adds is that the gap is
wider than power, and two of the blockers were invisible until tonight.

## 2. The two discoveries nobody had

### 2a. Always-on uptime destroys the measurement itself (verified)

`jump_detector.h` takes time as `float t_s` — float32. Float32 resolution
degrades as the number grows:

| uptime | timestamp resolution | vs the 20 ms sample clock |
|---|---|---|
| 1 day | 7.8 ms | fine |
| 3 days | 15.6 ms | marginal |
| **7 days** | **62.5 ms** | **3× coarser than the clock** |
| 14 days | 125 ms | ±14% of a 0.9 s airtime |
| 30 days | 250 ms | unusable |

Height goes as airtime², so a 14-day-uptime puck mis-measures height by
~±28%. **Nobody has ever seen this because bench boards reboot constantly —
the always-on glued puck is precisely the condition that triggers it.** The
vision's success condition breaks the instrument.

Related, same root: the trace block time anchor is uint32 milliseconds
(wraps at 49.7 days → phantom reboots in every tool), and `millis()`
arithmetic in the motion gate and auto-clear has the same horizon.

**Fix (host-testable, zero silicon):** feed the detector integer
microseconds and subtract in integer, formatting to seconds only at emit;
reset the timebase at each session boundary so timestamps never grow beyond
hours. A per-session timebase also gives six months of jumps their session
identity — which is currently missing (see 2b).

### 2b. "Session" counters are boot-lifetime counters (verified)

`session_jumps` / `session_best` are set at boot and never reset —
`main.cpp` has no reset site. On a puck that never reboots, ride 2's watch
activity reseeds from ride 1's totals, and the saved FIT archives
"since-boot", not "this ride". Both watches (yours and your brother's)
inherit the same wrong numbers. The trace auto-clear that shipped today
defines exactly the right session boundary (≥1 h still, then motion) — the
counters just don't use it yet. Cheap fix, same boundary.

## 3. The blocker map, by pillar

### Pillar 1 — Power autonomy (the widest gap)

Current truth: one always-on state at ~7–11 mA measured; endurance 25.7–34 h
to collapse. **The glued puck goes flat in ~a day, not six months.**

| missing piece | state |
|---|---|
| Auto-sleep after idle | not built (System OFF exists, human-commanded only) |
| Motion wake | **0% built on both halves** — no nRF SENSE/DETECT config, no LSM6DS3 wake-engine registers. INT1 is wired and floating. |
| Off-current on our hardware | never measured (item 25c) — "months of standby" is arithmetic, not data |
| Low-battery cutoff | not built; the puck brownouts to the PCM every time it's forgotten |
| Sealed-case wake | today's System OFF wakes only on VBUS or reset button — **a one-way door once glued** (plan.md P0 already flags this) |

The arithmetic that makes the vision plausible: System OFF + LSM6DS3
activity-wake ≈ 10–30 µA → **~1 year of standby on this cell**. Sessions at
~6 mA (DC/DC proven 0.72×) × 2 h ≈ 12 mAh → **~4–6 weeks of riding per
charge**. "Charge monthly, no big deal" is genuinely reachable — every piece
of it is unbuilt but none of it is research; it's known-path firmware.

### Pillar 2 — Session semantics and storage over months

- **Jumps region: 2048 records, append-only, nothing ever clears it.** At
  ~25 jumps × 2–3 sessions/week, it fills around month 5–6 **and stops
  silently** — no symptom on any surface. Needs a lifecycle decision
  (ring, or auto-archive once the watch has written the FIT).
- Trace lifecycle: **done** (auto-clear shipped and rehearsal in progress).
- Timebase: 2a's fix gives sessions identity and kills both wrap bugs.
- The unexplained `reas=0` reset on the spare (2026-08-20, twice suspected,
  boot-loop ruled out by a 5-min held-port watch) stays open — unexplained
  resets are a trust tax this vision can't carry.

### Pillar 3 — Garmin platform reality (fable audit; top items)

1. **Two-central corruption fix is flashed but unverified** — and
   two-watches-one-puck (your brother) is *exactly* the config that
   corrupted. `tools/dualcentral.py` exists and has never been run. One
   bench evening settles it.
2. **PuckLink has wedge states** — no timeout out of PAIRING/DISCOVERING/
   SUBSCRIBING, no exit from DEAD. One bad moment at activity start = a
   whole ride of "finding puck". ~5-line fixes, pure watch-side.
3. **The glance has never run on a wrist** — and it is the only pre-session
   health surface and the only "you're riding but not recording" hint the
   platform allows. One 10-minute sideload closes five assumptions.
4. **Time-on-foil cannot be backfilled into a FIT.** Developer fields are
   live-write-only. The flagship metric must be computed on the puck in
   real time and streamed, or it never appears in the activity. This
   reshapes the metrics roadmap: the offline classifier is the *prototype*,
   the deliverable is firmware.
5. **Forgot-to-start has no answer** — a data field exists only inside an
   activity; the FIT can never be created afterward. Puck records
   regardless (recoverable data, unrecoverable activity). Mitigation is the
   glance hint.
6. **Instinct is a 32 KB data-field device and peak memory has never been
   measured** — compiles ≠ fits. One simulator session answers it.
7. **Sideload-only distribution vs six months of watch firmware updates.**
   The Connect IQ store listing is the structural answer (also unlocks the
   settings UI and per-puck pinning) and is untouched.
8. FIT budget reality: 32 B/message for data fields. Current use: 4 B
   record, 10 B session. Time-on-foil, carve-g, landing-g all fit;
   histograms do not (puck/web path). Budget table now belongs in
   future-metrics.md.
9. **Garmin Connect rendering (M4) has never been looked at** — the
   2026-08-18 activity is sitting on connect.garmin.com; checking it costs
   ten minutes and zero code.

### Pillar 4 — Data trust, unattended

- A chop-slam false jump is permanently indistinguishable from a real jump,
  on the wrist and in the FIT. The water day is the only source of real
  false-positive data; **no numeric false-positive budget exists** to grade
  it against. Set one before the session (e.g. "<1 phantom jump per hour of
  riding"), or the session can't produce a verdict.
- Sensor death mid-session is invisible on the wrist (firmware emits the
  evidence; the watch doesn't consume it). Same for a wedged detector.
- Nothing sanity-checks the IMU over months. Cheap self-check at each
  session boundary (gravity ≈ 1.00 g at rest) would catch drift and death.

### Pillar 5 — Hardware survival

- **Charging a glued/potted puck is an unsolved design problem** — today's
  answer is a gasketed USB port and discipline. The sealed destination
  wants inductive (WOO-style dock) — backlog, unbuilt. This is the only
  pillar where the fix is hardware, not firmware.
- Adhesive: zero data on 6-month salt/UV/thermal. Bucket test still unrun.
- Between-session environment (car trunk in summer = cell calendar-aging
  heat) — undocumented, matters for a 6-month cell.

## 4. What is already strong (deserves saying)

- Detection works and survives its own firmware churn — 6/6 on tonight's
  desk test through seven storage/BLE changes.
- OTA on a glued device is **proven** (bootloader 0.11.0 installed over the
  air; two back-to-back wireless flashes). The glued puck is updateable.
- FIT developer fields verified in a real saved activity, byte-exact parse.
- Storage self-heals, survives total battery death, and the full-region
  boot scan passed live tonight.
- The advertised-battery glance path is real on the air (97% decoded
  passively) — the wrist-side render is the only missing half.
- The battery math closes: this cell and this radio genuinely support the
  vision once standby exists. No new hardware is required to prove Era 2.

## 5. The sequenced road

**Era 1 — prove the instrument (now → water day, ~2 weeks).** Freeze holds.
Only additions: the cheap tests below, the false-positive budget number,
and the session-boundary counter fix if it fits the freeze (it is small and
de-risks the FIT for the session itself).

**Water day.** Unchanged mission + one upgrade: it is also the training-data
day for time-on-foil (label foiling/not-foiling ranges — already in the
session card).

**Era 2 — build the appliance (post-water, ~4–6 weeks of firmware).** In
dependency order:
1. Integer/session-relative timebase (kills 2a, the wraps, gives sessions
   identity) — host-testable first, ships with anything.
2. Session boundary: reset session_* + trace auto-clear (shipped) + jumps
   lifecycle decision.
3. Standby: LSM6DS3 wake engine + nRF SENSE/DETECT + auto-off + low-batt
   cutoff + **measure off-current** (the one number the whole era stands on).
4. Live time-on-foil in firmware (threshold from water-day data) + one
   STATS key + watch display + 4 B session FIT field.
5. Watch hardening: PuckLink timeouts/DEAD-retry, dualcentral verification,
   glance on wrist, low-battery warning on the field.
6. Store submission (kills sideload fragility, unlocks settings).

**Era 3 — the sealed unit (hardware).** Inductive charging or magnetic
pogo dock, potting, adhesive qualification. Only era needing new hardware.

## 6. Cheap tests that close big assumptions (do in spare minutes)

| test | closes | cost |
|---|---|---|
| Open the 2026-08-18 activity on connect.garmin.com | M4: where dev fields render | 10 min, zero code |
| Sideload puckglance to the epix | 5 glance assumptions incl. scan-in-glance | 10 min |
| Field on screen 2, fakejump, flip back | compute()-runs-off-screen (the design's core bet) | 10 min |
| `dualcentral.py` + 20 fakejumps with 2 centrals | the corruption fix, before any two-watch outing | 1 evening |
| Simulator memory view, instinct target | 32 KB fit | 1 hour |
| Pause activity, fakejump ×3, resume, save, parse FIT | auto-pause record semantics | 15 min |
| Two 3 h wear days, field on/off | watch battery cost | passive |
| Note sideload before/after next watch OTA | firmware-update survival | 1 min ×2 |

## 7. The one-paragraph version

The puck already measures right and talks to the watch right; nothing about
the vision's *moments* is in doubt. What's missing is the *time between
moments*: sleep, wake, honest counters across weeks, a jumps region that
doesn't silently die in month five, a timebase that doesn't dissolve the
measurement after a week of uptime, a verified two-watch link, and a way to
charge through a sealed case. All of it but the charging is firmware on a
known path, most of it deliberately parked behind the water day — which is
the right order. The two discoveries that change the plan: the float32
timebase (fix before Era 2 ships anything) and the session-counter semantics
(fix candidate for the freeze window, since it corrupts the FIT the water
day produces).
