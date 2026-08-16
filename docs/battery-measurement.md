# Battery measurement — a rethink, grounded in prior art

Written 2026-08-16, after two overnight runs produced two mutually
contradictory numbers (11.6 mA and 16.3 mA) for a device whose idle draw
cannot physically exceed its recording draw. Rather than run a third night of
the same experiment, this file establishes **what our instrument can actually
measure**, what it cannot, and what to do instead.

Supersedes the measurement protocol in
[power-optimisation.md](power-optimisation.md) §0.

---

## 1. The finding: we have been using the wrong instrument

Every battery number this project has ever produced comes from `batt_pct`,
which is `vbat_mv` put through a 9-point voltage→percentage table in
`jh_power.cpp`. Battery University's article on measuring state of charge is
blunt about what that method can and cannot do for lithium chemistry:

> "80 percent of the stored energy remains in the flat voltage profile."
>
> Voltage-based estimation "only indicates full charge and low charge; **the
> important middle section cannot be estimated accurately**."

**Our 7.5 h run went from 71 % to 22 % — entirely inside that middle
section.** The measurement was taken in the one region the method is
documented not to work in.

Three further constraints from the same source, each of which we violated:

| Requirement | What we did |
|---|---|
| Cell must rest **≥4 h open-circuit** before OCV is a valid SoC proxy | Measured continuously under load, never rested |
| Voltage under load or charge is distorted by IR and polarisation | Every sample was under load |
| Coulomb counting is the better method (with periodic calibration) | We have no coulomb counter |

We also confirmed the charge-side distortion empirically: the gauge moved
28 % → 39 % in under ten minutes, which would require ~170 mA on a charger
capable of at most 100 mA. That is not charge, it is terminal voltage rising
under charge current.

**Conclusion: both 11.6 mA and 16.3 mA carry unknown, possibly large error.
Neither should be quoted again.** The disagreement between them is not a
mystery to be solved; it is the expected behaviour of a method used outside
its valid range, compounded by the separate `uptime_s`-vs-unplug-time bug.

## 2. What our data *can* still support

Not everything is lost. Two claims survive:

1. **Endurance, in wall-clock time, between two fixed voltages.** "It ran
   7.5 h going from 3961 mV to 3748 mV, idle" is a direct observation. It
   needs no capacity figure and no percentage curve. It is repeatable.
2. **A/B comparisons on an identical voltage window**, same board, same
   activity, same method. Systematic error largely cancels; what remains is
   the difference, which is the thing we actually want.

Everything else — mA, mAh, "hours of battery life" — is inference stacked on
a nameplate capacity (250 mAh) nobody has verified and a curve documented not
to work in the region we used.

**So the rule from here: measure TIME between fixed VOLTAGES, and only ever
compare like against like.**

## 3. The one thing that actually matters for the water session

Worth stating before any of the below gets scheduled, because it changes the
priority: **power is not a risk to the water session.**

The session is ~2 h. Even the most pessimistic reading of our data gives
~15 h idle endurance. That is roughly 7× margin, and the trace region fills
before the battery does (E7/soak work, `STATUS.md`). Nothing here is on the
critical path to answering "are wing jumps ballistic?".

Power optimisation is a **product-quality** goal — a sealed puck that lasts a
weekend instead of a day — not a session blocker. It should be sequenced
accordingly, and it should not consume bench time that drop calibration, the
capsule test, or the mount need.

## 4. The methods, ranked by what they cost and what they settle

### Tier 0 — free, no purchase, available today

**T0.1 Full-discharge endurance run.** Charge to full, unplug, leave it
still, and let it run until it stops. Record wall-clock. This is a *direct*
measurement of the only quantity the product cares about, and it involves no
gauge, no curve, and no capacity assumption. One number, unimpeachable, costs
one cycle.

**T0.2 Fixed-voltage checkpoints, not percentages.** Log `vbat_mv` (already
done by `tools/battlog.py`) and report time-to-reach 3900 / 3800 / 3700 mV.
Those are comparable across runs forever, even if the percentage curve is
later changed.

**T0.3 Charge timing.** Time from a fixed start voltage to `chg` going 1→0.
With fast charge now enabled (this board missed it until 2026-08-16), this
gives a clean before/after against the 50 mA baseline.

### Tier 1 — ~$10-30, useful but partial

**T1.1 Inline USB power meter.** Measures what the *charger* draws from USB.
Settles charge current directly, and would have shown the 50 mA/100 mA
question in seconds. Does not measure battery-side draw, so it cannot
evaluate firmware power changes.

### Tier 2 — ~$100, the right tool for this chip

**T2.1 [Nordic Power Profiler Kit II](https://www.nordicsemi.com/Products/Development-hardware/Power-Profiler-Kit-2).**
Nordic's own instrument for nRF52 power work:

- **200 nA to 1 A**, 100 nA resolution at the low end
- **100 ksps** — fast enough to resolve individual wake/sleep transitions
- **Source mode** supplies 0.8-5 V and up to 1 A, i.e. **it replaces the
  battery** and measures the whole board directly
- Digital inputs allow code-synchronised measurement

This is the tool that turns every open question here into an afternoon:

| Question | Nights of guessing today | With a PPK2 |
|---|---|---|
| Idle floor | 1 night, ±unknown | seconds, exact |
| Did the sleep change help? | never validly measured | minutes, A/B |
| Does DC/DC help? | untested | minutes |
| What does a BLE connection cost? | inferred | directly visible |
| Recording vs idle split | confounded | resolved per-transition |

At 100 ksps it would show the actual sleep duty cycle — the specific thing
`power-optimisation.md` predicted (6-7 mA) and reality contradicted. We
currently cannot tell whether the CPU is sleeping as designed. This instrument
answers that by looking at it.

### Tier 3 — hardware change, post-water

**T3.1 A coulomb-counting fuel gauge IC** (e.g. MAX17048 class). The correct
long-term fix per the prior art, and the only way an unattended sealed product
reports honest percentage. Not before the water session.

## 5. Sequenced plan

**Phase A — now, free, no purchase (this week)**
1. The board is freshly flashed (`src=87b0ecaf`) and charging with fast charge
   enabled for the first time. **Let it reach full and record time-to-full.**
   That is T0.3 and it costs nothing but patience.
2. Then one **full-discharge endurance run** (T0.1) on this build, logging
   `vbat_mv` at fixed intervals. Report: hours to 3900/3800/3700 mV, and total
   runtime. **This becomes the project's reference number**, replacing both
   discredited mA figures.
3. Record it in `STATUS.md` as time-between-voltages, never as mA.

**Phase B — order a PPK2 now so it arrives before the water session**
4. Idle floor, recording draw, BLE-connected draw — measured, in minutes.
5. Run the **DC/DC experiment** (`dcdc on`, already built and deliberately
   gated to runtime-only) with an instrument watching. This is the last
   untested lever and is claimed to cut MCU draw ~40 %.
6. Verify the sleep change is doing what it was designed to do. If it is not,
   that is a bug to find, not a number to accept.

**Phase C — after the water, with real data**
7. Decide gyro duty-cycling, standby tier, and 100 Hz sampling on measurements
   rather than estimates.
8. Consider a fuel gauge IC for any sealed product.

## 6. Rules, so this does not happen again

1. **Never derive mA from `batt_pct` deltas.** The curve is not valid in the
   middle of the range, which is where every long run lives.
2. **Never measure while charging.** Terminal voltage is distorted upward.
3. **Never compare two runs over different voltage windows.**
4. **Report time between fixed voltages.** It survives every future change to
   the curve, the capacity estimate, and the gauge.
5. **A/B or it does not count**: same board, same build except the one change,
   same activity, same voltage window.
6. `uptime_s` is time since **boot**, not since unplug. Any protocol that
   divides by it is wrong whenever the board saw USB.

## 7. Open hardware question, raised not asserted

Seeed's own documentation for this board warns:

> "When P0.14 is set HIGH, the battery voltage reading path is disabled and
> P0.31 may reach the input voltage limit of 3.6 V, posing a risk of damaging
> the P0.31 pin."

`jh_power.cpp` holds `PIN_DIVIDER_EN` (D14 / P0.14) **HIGH** by default —
"divider off until a read wants it" — which is the state that warning names.
Our boards have run this way for weeks and report sane, stable voltages, so
there is no evidence of harm; the note may concern a different configuration,
or may be conservative. **This is a schematic check to do, not a defect
claim** — the project's rule is that any code touching a power pin gets a
datasheet check, and this one deserves a second look now that it has surfaced.

Confirmed correct in the same pass: `PIN_HICHG = 22` maps to **P0.13** in the
Seeed variant's own `g_ADigitalPinMap` (`13, // D22 is P0.13 (HICHG)`),
matching Seeed's documented charge-current pin, and LOW selects 100 mA.

---

Sources:
- [Battery University BU-903: How to Measure State of Charge](https://batteryuniversity.com/article/bu-903-how-to-measure-state-of-charge)
- [Nordic Semiconductor Power Profiler Kit II](https://www.nordicsemi.com/Products/Development-hardware/Power-Profiler-Kit-2)
- [Seeed Studio XIAO nRF52840 wiki](https://wiki.seeedstudio.com/XIAO_BLE/)
