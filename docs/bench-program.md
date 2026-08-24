# The two-week bench programme — everything worth doing without water

Written 2026-08-15, after the accidental overnight run produced the first real
battery, stability and flight-physics numbers this project has ever had.

The water session is ~2 weeks out and is one shot. This is the list of work
that can be done at home, in order of value, plus the two process fixes that
make the results worth having.

---

## 1. Why the battery estimate was 4x off

**Measured:** 11.6 mA average, ~21.6 h from a 250 mAh cell.
**Assumed:** ~4 mA, ~60 h. That figure was never measured; it was a plausible
number for a device that idles between samples.

**This device never idles.** `main.cpp:1027`:

```cpp
if (now_us < next_us) return;   // pace to SAMPLE_HZ
```

`loop()` returns and Arduino immediately calls it again. At 200 Hz the real
work — one I2C burst, the detector, occasional flash — is a few hundred
microseconds out of a 5 ms budget. The other ~90 % is the CPU spinning at full
speed checking a clock.

Rough budget against the measured 11.6 mA:

| Consumer | Estimate |
|---|---|
| nRF52840 running flat out, **DC/DC never enabled** | ~6-7 mA |
| LSM6DS3TR-C, accel+gyro high-performance @208 Hz | ~0.9 mA |
| QSPI writes (a trace block every ~1 s) | small, bursty |
| BLE advertising at 1 s idle | ~0.05 mA |
| Board overhead (always-on LDO, charger, divider) | ~1-2 mA |

**The lesson is the same one this project keeps paying for:** a number nobody
measured is not a number. "~60 h" sat in the plan and justified deferring all
power work; the real figure still permits that decision, but only just.

## 2. Optimisation, ranked by value ÷ risk

**Every one of these is power/timing code, so every one runs on board #3
first — never on the OG board before the session.** Board #3 exists precisely
for this. **SUPERSEDED 2026-08-15/16:** this rule was replaced by owner
discipline the same window it was written — the OG is the only board with
a battery pigtail, so it is the only board power work can be *measured*
on at all. `power-optimisation.md`'s header states the change explicitly
("The rule is replaced by discipline: archive a known-good build, change
one thing at a time, and re-run the desk test after each"), and
`battery-measurement.md` §9 records a reviewer finding that tried to
enforce this exact rule as REFUTED, for the same reason. Both changes
below did in fact ship and get measured on the OG.

### 2.1 Let the CPU sleep between samples — the big one — SHIPPED (see power-optimisation.md §1 for the code and the measured outcome, which did not match this section's "3-5x" prize)
Replace the spin with a real wait, so the RTOS idle task runs and the core
sleeps. Potentially takes CPU duty from ~100 % to ~10 %.

- **Prize:** plausibly 3-5x endurance. 21 h becomes days.
- **Risk:** sample-timing jitter. The FreeRTOS tick is ~0.98 ms against a 5 ms
  period, so a naive `delay(1)` quantises wakeups. Airtime resolution is
  currently 5 ms and *airtime is the measurement*, so jitter must be measured,
  not assumed harmless.
- **Test:** on board #3, log inter-sample intervals for 10 minutes before and
  after; compare the distribution, not the mean.

### 2.2 Enable the DC/DC regulator — one line — SHIPPED 2026-08-22 (audit F-05; runs at every boot, `firmware/src/main.cpp:1252`)
Never enabled anywhere in the stack. Typically ~40 % off the MCU's own draw.

- **Prize:** perhaps 2-3 mA.
- **Risk:** low, but it is a power change; same board-#3 rule.

### 2.3 Duty-cycle the gyro
The gyro is most of the IMU's 0.9 mA and is only truly needed in flight —
but flight detection is what needs it. Defer until 2.1 and 2.2 are measured;
the prize is small next to them.

### 2.4 Don't bother yet
Trace-write batching, advertising tuning beyond what is done, standby tier.
All small or large-and-risky next to 2.1.

**Order of operations:** measure the baseline properly first (§3.1), then 2.2,
then 2.1, re-measuring after each. One change at a time or the attribution is
worthless.

## 3. The house programme

### 3.1 Baseline power measurement (do first — everything else compares to it)
Charge to 100 %, `clear`, unplug, leave it **still** on a shelf for 6-12 h,
then read `stats`. Still means the motion gate is idle, so this isolates the
floor: MCU + advertising + board overhead, with no recording.
**Answers:** what does it cost to simply be switched on? The overnight run
mixed 19 % recording with 81 % idle and cannot separate them.

### 3.2 Drop calibration — the zero (10 min, highest scientific value)
`./tools/jump drop`. Ten clean releases onto a cushion. In real free-fall the
true airborne acceleration is **exactly zero by definition**, so this measures
the instrument's own floor. Without it, the 0.079 g median from the walkabout
cannot be split into physics and sensor offset.

**NOTE (2026-08-23):** this had, in fact, already happened before this
document was written — `a6e477d` (2026-08-11) recorded ten drops,
`airtime_offset_s = 0.0257`, on the OG. This section's framing as an
undone first step was stale even on 2026-08-15. Ironically it is genuinely
open again now, for a different reason: the OG's live calibration reads
`CAL … source=defaults` as of tonight's flash (STATUS.md READ-THIS-FIRST
table) — the measured value is gone, not merely superseded — so the ritual
needs re-running regardless.

### 3.3 A labelled activity corpus (see §4 for how)
Walk, drive, stairs, running, a bag on a car seat, the board resting. Each
session labelled. **This is the false-positive dataset**, and the project has
none: `data/sessions/` contains no `labels.csv` at all.

### 3.4 Human jumps — the best available foil-jump proxy
A person's countermovement jump is ~0.4-0.6 s of airtime; a foil jump is
~0.8-1.2 s. Jumping off a low step gets into the real range safely.
**Why it matters:** every airtime the detector has ever seen on silicon is
either a hand toss or a false positive. Human jumps exercise the same regime
with a body's rotation signature, which is much closer to a board's than a
tumbling hand toss is.

### 3.5 Capsule + mount (15 min, highest consequence)
Bucket-test the capsule empty, then loaded so it floats. Order the mount; it
needs 24 h of cure on your brother's board. A leak or a failed base ends the
session in the first five minutes.

### 3.6 Long-run stability
The overnight run gave 18.5 h with **zero resets**. Repeat it once more at a
higher fill level to exercise the boot scan against a nearly-full trace region
— the path whose missing watchdog feed was fixed on 2026-08-14 and has still
never run against a full region.

## 4. How to label, and why it is the same problem as the video

**The puck has no wall clock.** Trace time is seconds since boot. That is
exactly why aligning kayak footage is hard — and it makes *any* labelling hard,
including a walk around the house. Solving it now on land is practice for the
thing that matters.

### The protocol (works for the house and the water)

1. **Anchor once, at the start.** Note the wall-clock time the moment you
   power it on, to the second. `trace_time + anchor = wall clock`, forever.
   Better: plug it in, run `stats`, and write down the time — the laptop knows
   the real time and the device reports its own uptime.
2. **Mark with a gesture, not a tap.** Three deliberate flat drops onto a soft
   surface, ~2 s apart. A tap is 2-5 ms — at 50 Hz decimation it may not appear
   in the trace **at all**. A flat drop is ~0.3 s of free-fall plus a spike:
   unmissable, auto-detectable, and it doubles as a zero-g reference.
3. **Note segments, not moments.** "14:32-14:51 walking", "15:05 three tosses",
   "15:20-15:40 car". Ranges survive a few seconds of clock error; instants
   do not.
4. **Say what you expect.** "no jumps here" is as valuable as "3 jumps here" —
   the false-positive rate is the number we cannot get any other way.
5. **Note anything odd**: dropped it, sat on it, left it in the sun.

### What that buys
- Every session becomes scoreable by `./tools/jump eval`, which exists and has
  never had a labelled corpus to run on.
- False positives get a rate instead of an anecdote. The walkabout produced
  one (jump 7, 1.393 g airborne) and we only know because of the new
  flight-physics column — with labels we would know the rate per hour.
- The alignment ritual gets rehearsed before it has to work from a kayak.

**Yes — noting things as you carry it around is worth real effort.** An
unlabelled 3.5 h trace is a curiosity. A labelled one is the first entry in the
corpus this project's whole evaluation pipeline was built for.

## 5. What NOT to do at home

- Do not change firmware on the OG board once the drop calibration is done —
  that is the session board. Power experiments go on #3.
- Do not `clear`/`format` any session until it is downloaded and copied twice.
- Do not chase the standby tier. It is the largest, riskiest change available
  and §2.1 alone may deliver most of the benefit for a fraction of the risk.
