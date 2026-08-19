# Power optimisation — plan, risks, and how each step is proved

Written 2026-08-15. Target board: **the OG** (owner's call). The usual rule is
that power changes go on the spare, but the OG is the only board with a
battery pigtail soldered — so it is the only board on which battery draw can
be measured at all. The rule is replaced by discipline: archive a known-good
build, change one thing at a time, and re-run the desk test after each.

**Measured starting point:** 11.6 mA average, ~21.6 h from a 250 mAh cell,
over an 18.55 h run that was 19 % recording and 81 % idle-but-awake.

---

## 0. The measurement protocol — SUPERSEDED 2026-08-16

> **This section's method is not valid.** It derives current from `batt_pct`
> deltas, and `batt_pct` is a voltage lookup — a method documented not to work
> in the middle of a lithium discharge curve, which is where every long run
> lives. It also divides by `uptime_s`, which is time since *boot*, not since
> unplug. Both figures it produced (11.6 mA, 16.3 mA) are unreliable and
> mutually contradictory.
>
> **Use [battery-measurement.md](battery-measurement.md) instead.** Measure
> time between fixed voltages; compare only like with like.

## 0-OLD. The original protocol (kept for the reasoning trail)

A before/after number is worthless unless the two runs are the same
experiment. The overnight run mixed recording and idle, so it cannot be
compared against anything.

**The bench drain test:**

1. Charge to ~100 %, confirm with `stats`.
2. `clear` (an empty trace region removes storage-scan variance).
3. Unplug. Leave it **still**, on a shelf, untouched.
4. After ≥4 h: plug in and immediately read `stats`.
5. `mA = (batt_pct_drop/100 × 250 mAh) ÷ (uptime_s / 3600)`

**`uptime_s` (added 2026-08-15) is what makes this exact** — no more guessing
when it was unplugged. Note the battery gauge is ±few %, so ≥4 h runs keep the
quantisation error small.

Two variants worth having:
- **Idle floor** — still on a shelf. Isolates "what does being switched on
  cost?"
- **Recording** — carried. That is the number that matters for a session, and
  it depends on duty cycle, so record the sample count too.

**We have never measured the idle floor.** It is the single most useful number
missing, because it tells us how much of the 11.6 mA is fixed overhead versus
recording work.

---

## 1. Change one: let the CPU sleep between samples

### The problem
`main.cpp:1027`:
```cpp
if (now_us < next_us) return;   // pace to SAMPLE_HZ
```
`loop()` returns; the Arduino/FreeRTOS loop task immediately calls it again.
The task never blocks, so the RTOS idle task never runs, so the core never
sleeps. At 200 Hz the real work — one I2C burst, the detector, an occasional
flash block — is a few hundred microseconds out of 5 ms. **The CPU spends
~90 % of its life re-reading a clock at 64 MHz.**

### The change
Sleep the leftover time instead of spinning, leaving a guard band so the
approach to each deadline stays precise:

```cpp
const int64_t remaining_us = next_us - now_us;
if (remaining_us > 1200) delay(1);   // ~0.98 ms RTOS tick -> idle task -> WFE
return;
```

`delay()` on this core is `vTaskDelay`, which yields to the idle task, which
sleeps the core. The 1200 µs guard means we never oversleep a deadline: one
tick is ~977 µs, so there is always >200 µs of margin left for the final
tight approach.

### Expected gain
Duty cycle falls from ~100 % to roughly 25-30 % (work + guard band). If the
MCU is ~6-7 mA of the 11.6, this should land somewhere around **6-7 mA
total — call it a 1.7-2x improvement**. A tighter guard band would win more;
start conservative and measure.

### The risk, quantified
Sample timing jitter. This matters because **airtime is the measurement**.

- Wake granularity ~0.98 ms; the pacer uses `next_us += INTERVAL` so the
  long-run *rate* stays exact — only individual samples shift.
- Airtime error propagates to height as `dh = g·T·dT/4`.
- At T = 1 s with dT = 1 ms: **dh = 2.5 mm.** Against jumps of 0.8-1.6 m.

So the physics says the jitter is negligible. That is a prediction, and it is
falsifiable — see below.

### How it is proved
1. **Jitter, measured from the data itself.** The trace stores a timestamp per
   sample at 50 Hz (4:1 decimation, so expect ~20 ms deltas). Compare the
   delta distribution — not the mean — before and after. A change in the mean
   would mean we broke the rate; a modest widening of the spread is the
   expected, harmless outcome.
2. **Desk test still passes** — 3 tosses detected and stored.
3. **Bench drain test** (§0) before and after.

### Falsifier, on record before the experiment
If the post-change delta distribution shows samples arriving **later than
~2 ms** off cadence, or the desk test's airtimes shift systematically, revert.

---

## 2. Change two: the DC/DC regulator — **HARDWARE CONFIRMED 2026-08-18**

> **The inductors are fitted.** `dcdc` run on the spare: board stayed up
> (uptime 960→998 s unbroken), selftest 6/6 including sensor/BLE/flash, a
> full revive rail-cycle clean. The "we do not know if the hardware supports
> it" premise below is RESOLVED — what remains unmeasured is the size of the
> saving, which the free matched-window A/B will give once the OG is back.
> Still never at boot until then. Detail: STATUS.md, 2026-08-18.

## 2-OLD. The original reasoning (kept for the trail)

### The prize
The nRF52840's internal DC/DC typically cuts MCU current by ~40 % versus the
LDO. Nothing in this stack enables it — confirmed by grep across the firmware,
the variant and the core.

### The problem: we do not know if the hardware supports it
The internal DC/DC needs external inductors on the DCC pins. Whether Seeed
fitted them on the XIAO nRF52840 is **not established** — the web-research
budget for this session was exhausted before it could be confirmed, and
guessing is exactly the habit that cost this project four days.

**If the inductors are absent, enabling DC/DC browns out the regulator.**

### Why it is still safe to try — if we do it right
`DCDCEN` is a register that clears on reset. So a brownout causes a reset, and
the chip comes back with DC/DC off. The danger is *not* the experiment; the
danger is enabling it **at boot**, which would turn a one-off brownout into a
boot loop.

**So: never at boot.** Add a bench command — `dcdc on` — that enables it at
runtime, on request. Then:

- If the board keeps answering, the inductors are there and we measure the
  saving.
- If it resets, we lost nothing, we learned the answer, and it comes back
  with DC/DC off by itself.

Only after it is proven live, on this board, does it earn a place in boot.

### Recovery, if it misbehaves
The bootloader does not enable DC/DC, so `stty 1200` + reflash always works.

---

## 3. Deferred, with the arithmetic recorded

**Gyro duty-cycling.** The gyro is most of the IMU's ~0.9 mA, and is only
needed in flight — but flight detection needs it. Small next to §1; revisit
after §1 and §2 are measured.

**Halving the sample rate to 100 Hz.** Roughly halves both CPU work and IMU
current, and allows a lower-power ODR. Airtime quantisation would go from 5 ms
to 10 ms, i.e. `dh = 9.8 × 1 × 0.010 / 4 = 24 mm` at a 1 s flight — still small
against 0.8-1.6 m jumps. **But it changes the measurement itself**, and the
water session should be run on the configuration the simulation was validated
against. Not before the water. Recorded here so the option is not forgotten.

**The standby/motion-wake tier.** Still the largest and riskiest change
available. §1 may deliver much of the benefit for a fraction of the risk,
which is precisely why it goes first.

---

## 4. Order of operations, and the safety net

1. **Archive the current build** (`.uf2` + sha256 + commit) so rollback is one
   flash, not a rebuild.
2. Bench drain test — **idle floor**, the number we have never had.
3. Ship §1 (sleep). Re-run: jitter check, desk test, drain test.
4. Try §2 (`dcdc on`) live, observe, measure.
5. Re-run the desk test one final time and freeze for the water session.

**One change per measurement.** Two changes at once and the attribution is
worthless — which is how "~60 h" got into a plan in the first place.
