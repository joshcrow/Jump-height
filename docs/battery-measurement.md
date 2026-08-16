# Battery measurement — the plan, after its adversarial review

Written 2026-08-16; **revised the same day after a 12-agent adversarial
review** (4 attackers on independent dimensions, one skeptic per surviving
finding). 6 findings survived verification and are folded in below; 2 were
refuted, and §9 records why, because both refutations corrected the reviewers'
model of the project rather than the plan. Supersedes the measurement protocol
in [power-optimisation.md](power-optimisation.md) §0.

The original problem stands: two overnight runs produced two mutually
contradictory currents (11.6 mA, 16.3 mA) for a device whose idle draw cannot
exceed its recording draw. The instrument was the problem.

---

## 1. The instrument finding

Every battery number this project ever produced came from `batt_pct` —
`vbat_mv` through a 9-point voltage→percentage table. Battery University's
BU-903 on lithium state-of-charge: ~80 % of the stored energy sits in the
flat middle of the voltage profile, where voltage-based estimation is
**inaccurate**; it is dependable near full and near empty. (The review
weakened my original "documented not to work" to "documented to be
inaccurate" — the source says limited, not useless. The retraction below
survives either wording.) Valid open-circuit voltage also needs ≥4 h of rest;
we measured continuously under load.

Both the 71 %→22 % overnight run and the older walk figure lived entirely in
that flat middle. **Both derived currents are retracted.** The disagreement
between them is expected behaviour of the method, compounded by the separate
`uptime_s`-vs-unplug bug.

What survives from those runs:
- **3961 mV → 3748 mV in 7.51 h, idle, zero resets** — a direct observation.
- **18.55 h untethered on one charge, including 3.55 h of recording, ending
  alive at 7 %** (STATUS.md, the accidental overnight walk). The gauge is used
  only at its two trustworthy extremes — "started near full, ended near
  empty" — which is exactly the use BU-903 endorses.

## 2. What we can honestly measure, and the new rules

**Measure time between fixed voltages; compare only like against like.**

1. Never derive mA from `batt_pct` deltas.
2. Never measure state of charge while charging — terminal voltage reads high
   under charge current. (Charge *progress* via voltage-span timing, §4, is
   fine: there the lift is constant and cancels.)
3. Compare runs only over identical voltage windows, same board, same build
   except the one change, same activity, **and note the room temperature** —
   overnight temperature swing moves OCV by the same order as small effects.
4. Report time between fixed voltages; it survives future changes to the
   curve and the capacity estimate. Caveat from review: it does *not* survive
   a future per-unit `vbat_scale` calibration — record raw `vbat_mv`
   alongside any calibrated value.
5. A single run is a **bounds check** ("endurance ≥ X h"), never "the
   reference". A reference number requires the frozen build and a repeat.
6. `uptime_s` is time since **boot**. Any protocol dividing by it is wrong
   whenever the board saw USB.
7. IR sag at this device's draw is second-order (~16 mA × 0.3 Ω ≈ 5 mV) —
   fixed-voltage checkpoints are valid for A/B even when the change alters
   the load, because the sag difference is millivolts against spans of
   hundreds.

## 3. Power is not a session risk — the argument, made honestly this time

The review's sharpest catch (§9, finding 1): my original margin argument
quoted "~15 h idle endurance", which is the retracted 16.3 mA figure wearing
different units — a `batt_pct` extrapolation through the never-measured
22 %→0 % region. Violating my own rule 1, in the section that set the rules.

The honest argument needs no retracted numbers: **the device has already run
18.55 h untethered on one charge with 3.55 h of recording in it, ending
alive.** A water session is ~2 h. That is ≥9× demonstrated margin, with
recording draw included, before fast charge and before any optimisation.

Also corrected from review: I claimed "the trace region fills before the
battery does". That is duty-cycle-dependent — at continuous recording the
trace (~4.8 h capacity) fills first; at the walk's 19 % duty the battery goes
first. For a 2 h session **neither limit is reachable**, which is the only
claim the argument needed.

Power work is product quality, not a session blocker, and must not consume
bench time that drop calibration, the capsule test, and the mount need.

## 4. LIVE FINDING: fast charge is not measurably working

The review surfaced a decisive asset my original plan missed: a **complete
50 mA charge baseline already exists** — `data/soaks/
20260810-charge-and-stability-soak.csv`, the same board and cell, per-minute
`vbat_mv`, a full charge from 3612 mV to termination in 3.47 h. My attacker
claimed "no baseline exists"; its verifier found this file. Everything below
uses it.

**Voltage-span timing under charge is a valid current comparator**: in the CC
region, terminal voltage = OCV + I·R with I·R constant for the span, so the
span width in true charge is identical across runs and its *duration* scales
inversely with current. Double the current → half the time. It cancels the
percentage curve, the capacity nameplate, and the IR lift.

First measurement, this morning (fast-charge firmware `src=87b0ecaf`
confirmed on the board, `chg=1`, HICHG driver code confirmed present and
called at 1 Hz):

| span | 50 mA baseline (08-10) | today, "100 mA" build |
|---|---|---|
| 3890 → 3970 mV | 29 min | **30 min** |

**Ratio ≈ 1.0. If 100 mA were reaching the cell, this span would take ~15
minutes.** The charge is proceeding at the 50 mA rate.

What this does and does not establish:
- The *outcome* is measured: no fast charge at the cell.
- The *cause* is not: `pincensus` reads the net with the drive released, so it
  cannot confirm the OUT register, and there is no firmware readback of the
  HICHG drive state. STATUS.md has carried fast charge as `built-unverified`
  since it shipped — this is that verification, currently failing.
- Candidate causes, in order: the board's HICHG topology doesn't respond to
  OUTPUT-LOW as documented (Seeed wiki says P0.13 LOW = 100 mA; the pull-up
  topology on the net is an admitted unknown in `jh_power.cpp`); a code-path
  fault that a one-line readback would expose; or the baseline itself ran
  under a different effective load (both runs had the board awake, so this is
  the least likely).

**Diagnostic sequence (do not flash mid-charge — it resets the run):**
1. Let today's charge complete; log the full curve for the record.
2. Add a `hichg=` field to STATS (PIN_CNF + OUT readback for P0.13) — one
   line, settles the firmware half for free on the next flash.
3. The **USB inline power meter** (§5) settles the whole question from
   outside: the input current to a linear charger ≈ charge current + board
   draw, so 50 vs 100 mA reads as ~70 vs ~120 mA at the port, live, in
   seconds. This was in my original plan as a tier — the review's finding 4
   (confirmed) is that I named it and then never scheduled it. It is now
   Phase A.

Also from review: **time-to-full is demoted to a logged curiosity.** From a
~28 % start the CC phase halves but the CV taper does not, so even a working
fast charge shows only ~1.6-1.8× on time-to-full — a weak signal buried in
start-SoC variance. Span timing in the CC region is the measurement. And the
two "fulls" differ: termination scales with the setting (~10 % of fast-charge
current), so a 100 mA full is ~3-5 mAh shy of a 50 mA full — noted so the
endurance run's starting point is stated honestly.

## 5. The revised sequence

**Phase A — now, in this order**

0. **Desk test first** (review finding 7, confirmed): `./tools/jump desktest`
   — 3 untethered tosses, 10 min, owner's hands. The board was flashed twice
   today (`src=87b0ecaf`) and no untethered desk test has run on this build;
   plan.md P0 #3 makes this the gate after *every* flash. It must precede any
   multi-hour battery cycle, because a regression discovered after two days
   of cycling voids the cycles too.
1. **Order the USB inline power meter** (~$10-15, owner) — the fast-charge
   arbiter, and the only instrument that can settle it before a PPK2 arrives.
2. Let the current charge finish; keep the log.
3. **Bounded discharge run** on this build: unplug, still, log via battlog to
   a **fixed endpoint of 3600 mV under load** (~8 % by the shipped table) —
   then recharge immediately. Never run the session cell to hardware cutoff:
   the firmware has no low-battery shutoff (the planned ~3450 mV auto-off is
   unbuilt), the pack's protection threshold is an open VERIFY item, and an
   unattended dead-cell dwell at cutoff is exactly the kind of avoidable cell
   abuse the project cannot afford on the only board with a battery pigtail.
   Report hours-to-3900/3800/3700/3600 mV. **Label: bounds check.**
4. `hichg=` readback lands with the next routine flash — not a special flash.

**Phase B — PPK2, ordered now (~$100, owner decision)**

Run conditions, per review finding 5: battery **unclipped at the JST**
(routine per bench-playbook §4), PPK2 in source mode into the battery
connector at 3.8-4.2 V — the BAT node, never the 3V3 pad, which bypasses the
board's own regulator and measures a different power tree — **USB
disconnected for every source-mode run**, activity marked via the PPK2's
digital inputs or BLE. Then, in minutes each: true idle floor, recording
draw, BLE-connection cost, sleep-change verification, and the DC/DC
experiment (`dcdc on`) — which stays on this board per the owner's recorded
call (§9), with the Seeed-schematic inductor check done first per
hardware-protection rule 2.

**Phase C — the reference number, inside the freeze window**

The reference endurance run happens on the **frozen build**, merged with
plan.md P0 #5's sealed-battery run (seal the puck for its first hours; one
cycle closes both items). The freeze window's dead time is exactly when a
multi-hour passive run costs nothing. As of today HEAD is on the board, so
the frozen build may already be this one.

**Phase D — post-water:** gyro duty-cycling, standby tier, 100 Hz decisions
on PPK2 data; coulomb-counting fuel gauge IC for any sealed product; a
to-death discharge if a true-zero endpoint is ever worth one cell cycle.

## 6. Owner decisions surfaced (review: previously assumed silently)

1. **USB power meter purchase** (~$10-15) — unblocks the fast-charge verdict.
2. **PPK2 purchase** (~$100) — unblocks every real power number.
3. **Desk test timing** — 10 minutes of hands, gates everything above.
4. **Session date** — still unset; Phase C's freeze-window run sequences off
   it, and "~2 weeks" in earlier docs was an assumption, not a commitment.

## 7. Open hardware questions (raised, not asserted)

- **P0.14/P0.31 divider state.** Seeed warns that P0.14 HIGH (our default)
  lets P0.31 approach its 3.6 V input limit. Review quantified the fault
  path: through the ~1 MΩ top resistor the clamp current is bounded ≈0.6 µA —
  damage at that current is implausible, which is presumably why weeks of
  running show none. Still on the schematic-check list, with one addition
  from review: the exposure is largest **while charging** (VBAT at its
  highest), which is also when we now deliberately log.
- **HICHG net topology** — the live question of §4.
- **Pack protection threshold** — unverified; one more reason for the
  3600 mV floor.

## 8. What changed from the original plan, in one look

| Original | Revised | Why |
|---|---|---|
| "~15 h idle, 7× margin" | 18.55 h measured run, ≥9× | original quoted a retracted figure (finding 1, confirmed) |
| T0.1 "run until it stops" | fixed 3600 mV floor, recharge same day | no firmware/verified-hardware guard below it (finding 2) |
| Time-to-full verifies fast charge | CC-span timing + USB meter; time-to-full demoted | taper physics caps the signal at ~1.7×; baseline exists (finding 3) |
| USB meter named, never scheduled | Phase A item 1 | finding 4, confirmed |
| "PPK2 replaces the battery" (no procedure) | unclip at JST, BAT node, USB out, inputs marked | finding 5 |
| Desk test unmentioned | Phase A step 0, gating | finding 7, confirmed |
| Reference number "this week" | bounds check now; reference on frozen build | finding 8's salvage |
| — | Fast charge measured NOT working | new data during the review itself |

## 9. Review trail — including what the review got wrong

Twelve agents: four attackers (methodology, electrical, sequencing, sources),
eight verifying skeptics. Two high-severity findings were **refuted**, and
both refutations matter more than most of the confirmations:

- *"The DC/DC experiment on the OG violates the sacrificial-board law."*
  Refuted: the bench-playbook board registry (2026-08-14) records that the
  "mule" and the "OG" are **the same physical board** — the name is retired —
  and `power-optimisation.md` records the owner's explicit call to run power
  work on it ("the rule is replaced by discipline"). The reviewer applied a
  rule whose premise (two boards) no longer exists. Kept from its wreckage:
  the pre-`dcdc` inductor schematic check.
- *"The reference number is measured on a build already scheduled for
  replacement."* Refuted: the P0 items the attacker cited as pending flashes
  (3b, the batch) shipped days ago; HEAD hashes to `src=87b0ecaf`, which is
  what the board runs. The stale plan-table premise failed, but the salvage
  stands and is Phase C: reference numbers belong to the frozen build.

Sixteen further findings (medium/low) were raised and not individually
verified; those consistent with verified physics or trivially checkable are
folded in above (temperature rule, charging-state anchor rule, `vbat_scale`
caveat, BU-903 wording, owner-decision surfacing, P0.14 charging-window
note). The rest are recorded in the review artifact, not here.

---

Sources:
- [Battery University BU-903: How to Measure State of Charge](https://batteryuniversity.com/article/bu-903-how-to-measure-state-of-charge)
- [Nordic Semiconductor Power Profiler Kit II](https://www.nordicsemi.com/Products/Development-hardware/Power-Profiler-Kit-2)
- [Seeed Studio XIAO nRF52840 wiki](https://wiki.seeedstudio.com/XIAO_BLE/)
- `data/soaks/20260810-charge-and-stability-soak.csv` — the 50 mA baseline
- Review artifact: workflow `wf_6677eb1e-e3d`, 12 agents, 2026-08-16
