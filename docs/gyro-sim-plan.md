# Gyro-simulation plan — what to run at the desk before the gyro flagship

**Provenance:** authored 2026-08-05. Synthesizes six grounded + adversarially
pruned gyro-sim candidates (g1–g6) against the LSM6DS3TR-C datasheet
(DocID030071 Rev 3), wingfoil rotation kinematics, and the verified ballistic
core (`sim/wing_model.py`, `sim/sensor_model.py`, `sim/detector.py`,
`sim/selfdiag.py`, `docs/wing-ballistic-sim.md`, experiment E4). Companion to the
"gyro value brief" follow-up queued in wing-ballistic-sim §6.

## 0. Headline verdict

**The gyro flagship is only PARTLY de-riskable at the desk — and exactly ONE sim
is worth running (g4).** Every sensor-physics question — drift, angle-random-walk,
bias, full-scale/ODR sizing, trick-class separability, landing recoverability — is
either closed-form datasheet arithmetic (confirms the obvious) or rests on an
UNMEASURED input (a spin-axis tilt, a trick-tilt distribution, a contact-impact
model) that a sim can only launder back out as a "finding." Those decisions are
already made; simulating them changes nothing. The single question a sim genuinely
settles is emergent *stateful firmware* behaviour: does a **time-varying spin
burst** make the real `detector.py` fire a false landing and truncate airtime, and
does **per-sample ω²r subtraction inlined into the detector hot path** recover it?
That answer moves a concrete, expensive architecture decision — gyro as a
"recording-only trick-metric extra" vs a **detector input that must be powered and
settled by takeoff**. Everything else is gated on real S0 gyro logs, not sims.

**RESOLVED (2026-08-05, see g4 RESULT below):** g4 was run and settled this — the
gyro is a **detector hot-path input**, not a recording-only extra. The
conditional framing preserved below is the original de-risking rationale; the
decision it fed is no longer open. riding-dynamics-map §0 already treats it settled.

## 1. The gyro plan under test

The Sense board's LSM6DS3TR-C is a 6-axis part; the gyro is on-board but UNUSED
(firmware reads accel only). The plan: enable the gyro during recording + AHRS
fusion and ship a flagship trick-metrics feature (rotation degrees/rate/
direction/count over the 1–3 s air window; landing attitude). Two byproducts of
the same pipeline: self-diag spin-correction (subtract ω²r from airborne |a|,
proved in E4) and optional orientation-based height correction.

## 2. Prioritized runnable plan

| Order | Sim | Verdict | The sharpest question it answers | Decision it informs |
|---|---|---|---|---|
| 1 | **g4** spin↔height coupling | **RUN** | Does a mid-air spin *burst* make the REAL detector false-land and truncate airtime, and does hot-path per-sample ω²r subtraction recover it — across peak-dps × lever-arm, with r mis-calibrated ±20%? | Gyro = recording-only extra **vs** detector hot-path input (must be powered/settled by takeoff, inlined into `jump_detector.h`) |
| 2 | **g1** axis-tilt integration fork | **MAYBE** (strongly trimmed; gated) | Over what body-frame spin-axis TILT does single-axis trapezoid integration under-count a real 360 by >15° vs 3-axis quaternion? | Rotation-count compute path: cheap scalar integrate vs carry a quaternion — but the tilt input is ungrounded, and quaternion is cheap over ≤3 s anyway, so gate on real data first |
| 3 | **g3** ODR/FSR sizing | **SKIP** → datasheet | (not a sim) CTRL2_G register value | Ship ±2000 dps / ODR ≥208 Hz + digital LPF from the datasheet (gyro-prior-art §6: published peaks 1090–1665 dps clip ±1000); correct the power budget (see §4) |
| — | **g2** 3D coupled rotation | **SKIP** | naive-vs-AHRS = the cos(θ) identity + a definitional truth | Ship quaternion AHRS — already forced by the feature's definition |
| — | **g5** landing attitude | **SKIP** | recoverability set entirely by an ungroundable contact model | Ship cheap "flat-vs-not" settled-tilt flag; defer nose/tail/rail to labeled water data |
| — | **g6** trick classification | **SKIP** | separability manufactured by hand-authored archetypes (circular) | Log raw 3-axis gyro on S0, build a labeled library, THEN train |

### Why only g4 survives

- **g4 is the one sim whose load-bearing output is NOT closed-form.** The false-
  landing *trigger* is algebra (rot_g = 2.5 g at spin = √(2.5·G/r)/2π = 518/401/
  317 dps at r = 0.3/0.5/0.8 m), and "offline self-diag can't un-truncate an
  airtime the detector already closed" is architectural. But whether
  `a_corr(t) = √(max(0, a(t)² − rot_g_gyro(t)²))` fed into the **real stateful
  detector** recovers airtime — without the subtraction spuriously re-crossing the
  0.35 g freefall gate mid-flight, or *erasing the real 4.8 g landing spike* when r
  is over-estimated (argument clamps to 0) — is emergent behaviour of the state
  machine, not arithmetic. **E4 never tested this:** `e4_rotation.py` feeds the
  gyro correction only to the self-diag *median* and runs `detector_detects(t0,
  m0)` on the RAW stream. That gap is the whole sim.

- **g1 is trimmed to its one non-obvious sub-question.** The drift/ARW/bias/FS
  Monte Carlo is pure datasheet arithmetic: ARW = 0.007·√3 ≈ 0.012° and
  bias-instability ≈ 0.009° over 3 s (both ≪ 15°); ±10 dps turn-on bias × 3 s =
  30° makes a pre-takeoff lead-in bias subtraction textbook-mandatory; a 360 in
  0.9–1.5 s = 240–400 dps sets FS straight off one division. All SKIP. The only
  part a sim can touch is the single-axis-vs-quaternion fork — but its decisive
  input (how far the board's spin axis tilts *in-body* during a real 360) is
  unmeasured, and the plan already commits to quaternion (cheap over ≤3 s). So even
  a "single-axis suffices" result wouldn't be trusted. MAYBE, and **gated**:
  ground the tilt from a real S0 360 capture before trusting the fork; if run, do
  it as a ~40-line regression oracle against a prescribed true attitude on the real
  Fusion lib, not a 250–400-line from-scratch attitude twin re-deriving cos(θ).

- **g2/g3/g5/g6 confirm the obvious or launder an assumption.** g2's "naive
  undercounts by cos(θ)" IS the trig identity, and a "360" is *defined* about the
  world vertical, so world-frame attitude is required the instant the board tilts —
  not an empirical fork. g3's conservative option is nearly free (±2000 dps costs
  70 vs 35 mdps/LSB, trivial against a 1.3 M-mdps integral), so there is nothing to
  optimize; the only real unknown (does a whip exceed 1000/2000 dps?) is
  instrumentation, not simulation. g5's nose/tail/rail recoverability is set
  entirely by an ungroundable fluid-impact/board-flex/leg-absorption model —
  assumptions-in, assumptions-out. g6's class separability is manufactured by the
  hand-authored archetypes (classifying by "which axis integrated most" proves
  clean axes are separable — circular). All four are answered by real S0 logs.

## 3. New sim machinery the RUN set needs

**For g4 (the only RUN) — small, all pure-stdlib, reuses the E4 harness. BUILT +
RUN 2026-08-05 (`sim/experiments/g4_spin_detector.py`); items below are as shipped:**

1. **Time-varying spin in `sensor_model.render_session`.** The scalar `cfg.spin_rps`
   → a single constant `rot_g` path stays (sensor_model.py:104–105); the new
   `spin_rps_fn(t)` callable computes `rot_g(t)` per-sample inside the airborne
   branch (sensor_model.py:111–115). **Built (~15 lines).**
2. **A burst spin-profile generator** — ω(t) that is ~0 at takeoff (so freefall
   confirms), then ramps to a peak mid-air. Parameterized by peak-dps and time-of-
   peak. **~15 lines.**
3. **A per-sample gyro-corrected stream fed INTO `detector.py`:**
   `a_corr(t) = √(max(0, a(t)² − (ω_gyro(t)²·r_assumed / G)²))`, run through the
   real `Detector` state machine (NOT the self-diag median). **~10 lines.**
4. **An r-miscalibration knob (±20%)** on the correction's assumed lever arm —
   because the datasheet shows gyro error (ZRL ±3 dps, 5 mdps/√Hz noise) is >100×
   below a hundreds-of-dps signal, so the accuracy limiter is **lever-arm/axis
   knowledge r, not gyro quality**. Model r-uncertainty, do NOT build a gyro
   noise/bias state machine. **~5 lines.**
5. **A break-point-surface sweep driver** (peak-dps × r, with r_assumed swept
   ±20% around true r) reusing the E4 render/detector harness → a CSV surface.
   **~30 lines.**

**Explicitly NOT needed for g4:** no 3-axis gyro stream, no quaternion/AHRS, no
bias/noise/scale-factor model, no coupled-3D attitude. Those belong to the heavier
gyro sims that this plan SKIPs. Total g4 machinery ≈ 30–40 new lines on top of
`sensor_model` + a small driver, perturbing none of the verified ballistic
pipeline.

**For g1, only IF later un-gated by a real tilt number:** quaternion attitude
generator with a tilting spin axis; a single-axis-trapezoid vs 3-axis-quaternion
integrator pair; a tilt sweep (0–40°) plus a pop-transient — ideally as a ~40-line
oracle riding the real shipped Fusion lib, not a from-scratch twin.

## 4. Free datasheet outputs (no sim required)

Falling out of g3's grounding, ship today:

- **CTRL2_G = ±2000 dps FS (70 mdps/LSB), ODR ≥208 Hz, digital LPF on.**
  ±2000 clears the published in-air peaks (figure-skating 1665 dps, diving
  1090 dps, wingfoil doubles/triples 1273–1465 dps — gyro-prior-art §3/§6) that
  would clip ±1000; ODR ≥208 Hz reconstructs the rate signal to <1°. (LSB
  quantization over a 360° integral is self-evidently negligible; ±2000 costs
  70 vs 35 mdps/LSB, trivial against a >1 M-mdps integral.) ±1000 dps remains
  defensible on power if S0 confirms peaks stay <1000 dps — but pending that
  silicon measurement, size for the published clip risk.
- **A pre-takeoff stationary bias subtraction** (subtract the planing-baseline
  gyro mean) is a **mandatory** pipeline step: ±10 dps ZRL × 3 s ≈ 30° otherwise.
- **Power-budget correction:** the LSM6DS3TR-C combo accel+gyro draws **~0.9 mA
  high-performance / ~0.45 mA normal**, NOT the **~3.6 mA** the plan inherited from
  the ESP32-era MPU-6050 (wing-ballistic-sim §6). The gyro-on recording cost is
  far cheaper than budgeted — strengthens the case for enabling it.

## 5. What g4 must OUTPUT (and must not claim)

Run g4 as a **threshold/robustness MAP, not a prediction that the failure "is
real."** "Realistic wingfoil dps" is an unfalsifiable guess (no published data), so
framing g4 as "prove the false-landing happens at realistic peaks" would just
launder the assumed spin(t) into the seeded conclusion. Instead:

1. **Break-point surface:** at what peak-dps × r does the RAW detector fire a false
   landing and truncate airtime/height?
2. **Recovery:** does hot-path per-sample correction push that break-point out past
   the fastest plausible tricks (~900 dps)?
3. **r-robustness:** with r_assumed mis-set ±20% (quadrature residual ~60% of
   rot_g; negative-argument erasure when r is over-estimated), how much recovered
   margin is lost — does correction still hold airtime to a few %, or introduce NEW
   misses at high spin by eating the real landing spike?

Output the surface. The decision reads off *where* the break-point sits relative to
plausible tricks, not off a yes/no.

## 6. Decision this plan hands to firmware

**SETTLED by the g4 RESULT (below): the first branch fired.** The gyro is a
**detector hot-path input**, powered and settled by takeoff. The conditional
below records the decision logic; the outcome is no longer open.

- **If g4's break-point sits inside plausible trick spins even after correction, or
  r-miscalibration reopens misses:** the gyro is promoted from "recording-only
  optional trick-metric" to a **detector hot-path input** — powered and *settled by
  takeoff* (not lazily spun up mid-flight), with `√(max(0, a² − rot_g²))` inlined
  into `jump_detector.h` ahead of the 0.35 g gate and 2.5 g landing test. That is a
  power-sequencing + firmware-architecture commitment, so g4 clearing even a modest
  decision-shift justifies its ~40 lines.
- **If correction pushes the break-point safely past the fastest tricks and holds
  under ±20% r error:** the gyro stays a recording-only extra; the accel detector
  ships unchanged and the ω²r path remains the offline self-diag it is in E4.
- **Either way:** the accuracy bottleneck g4 confirms is **lever-arm/mount
  calibration (know r and the mount offset), not gyro grade** — argues for a
  mount-position calibration step over any sensor upgrade, and confirms the
  LSM6DS3TR-C gyro is more than good enough.

## 7. Honest caveats

- **g4's spin(t) is an assumed profile.** The sim maps *robustness across* peak-dps
  and r; it does not establish that any given real trick lands in the failure band.
  The real peak-dps distribution is an S0 silicon question.
- **SKIPping g2/g5/g6 leaves the shipped AHRS/attitude wiring without an executable
  oracle.** That risk is real but is a firmware *unit test* (~40 lines on the real
  Fusion lib), not a 250–400-line plan-deciding sim.
- **g1's fork stays open until a real 360 is captured.** Committing to quaternion
  (cheap) is the safe default meanwhile; do not drop it on a sim resting on a
  guessed tilt.
- **Landing attitude (g5) and trick class (g6) are data problems.** Hand-label the
  first S0/S1 field logs from video, then check whether the gyro jolt axis+sign and
  settled gravity vector separate the classes above the ~3–5° settle floor. Only
  after real separation is observed is a sim useful — to set thresholds, not to
  invent the physics.

## g4 RESULT — run 2026-08-05 (`sim/experiments/g4_spin_detector.py`, `out/g4_spin_detector.csv`)

The one sim worth running was run. It flips the gyro from "trick-metrics extra" to
a **core-accuracy requirement for any jump with rotation.**

- **Raw accel-only detector fails on spun jumps at REALISTIC spin** — at lever 0.3 m,
  height error goes 0% → **−93% at just 300 dps peak** and stays −80…−97% to 1000 dps.
  Real wingfoil spins are 240–360 dps mean / 500–900 dps peak, so normal rotational
  tricks are silently truncated or lost. Raw break-points (miss or >15% error):
  lever 0.2/0.3/0.5/0.8 m → 350/300/250/200 dps.
- **Two emergent failure modes — why algebra couldn't answer this:**
  (1) ~300–500 dps: the spin holds |a| above the 0.35 g free-fall gate long enough to
  trip the no-landing settle, which resets and **re-pins takeoff mid-flight** → truncated
  airtime. This fires *below* the naïve 2.5 g false-landing crossover (518 dps @ 0.3 m).
  (2) ≥~518 dps: classic 2.5 g false-landing. The stateful sim was essential — the 2.5 g
  crossover alone under-predicts the onset.
- **Per-sample ω²r subtraction fed into the real detector fully recovers: 0% error
  across the whole 0–1000 dps sweep, every lever** (with perfect r).
- **Lever-arm calibration is two-sided (the real constraint; gyro noise is not):**
  under-cal (0.8 r) only partially fixes (break-point out ~50 dps, residual failures);
  over-cal (1.2 r) survives mid-flight spins but **erases the real landing spike** when
  the rider is still spinning at touchdown (900 dps late burst → jump lost entirely).
- **Decision settled:** the gyro is a **DETECTOR HOT-PATH input** — powered and settled
  BY takeoff, ω²r subtracted per sample inline in `jump_detector.h` — plus a mount/
  lever-arm calibration step. Straight airs still work accel-only (ballistic result
  stands); spun jumps need the gyro for a correct height. This is a stronger
  justification than "richer product."
