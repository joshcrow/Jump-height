# Wing-Foil Ballistic Physics — Results Digest

**Study question:** Is the airtime height formula `h = g·T²/8` valid for wing foiling, the way it is *not* for kiteboarding? And can the sensor self-diagnose the jumps where it isn't?

**Date:** 2026-08-04 · **Pipeline:** `integrate_flight` (dt=2e-5) → `render_session` (sensor model) → firmware-twin `Detector` + self-diag flag · **All headline numbers independently re-verified against the physics core, not the experiment scripts.**

---

## Executive verdict

**Wings are ballistic enough. Ship the airtime method.**

The whole thing turns on one number: the sustained vertical acceleration a rider can hold in the air, `a_v`. Overshoot in the height estimate is exactly `1/(1 − a_v)` (closed form, confirmed numerically to <0.1%). A kite delivers `a_v ≈ 0.567 g` through the harness and overshoots **2.31×** — the airtime formula is useless there (Simons 2025, reproduced exactly in E6). A wing rider holds the wing with their **arms**, and the arm-force ceiling caps sustained lift far below that. Across a deliberately punishing 3072-combo grid (E1), realistic depowering techniques stay at **1.00–1.09×** overshoot; only a physically absurd "never depower at 18 m/s" corner reaches 1.17×. The kite exception does **not** transfer to wings.

Two things must ship alongside the airtime number:

1. **A self-diagnosis flag.** Airborne `|a|` reads wing-force/mass directly, so median airborne `|a| > 0.12 g` flags a non-ballistic jump with a **perfect ROC (AUC 1.000)** in the absence of spin (E3). The dangerous "silently detected and materially wrong, no warning" band is real but **narrow: a_v = 0.09–0.10 g, only 0.02 g wide, worst silent bias 11.7%** (E5) — and it sits *beside*, not inside, where physical wings actually operate.

2. **The gyro, for spun tricks.** A spin adds `ω²r` centripetal acceleration that masquerades as lift. It **inverts** the bare flag (AUC collapses 1.000 → 0.258, E3) and false-positives 84% of spun ballistic jumps (E4). Subtracting the gyro-known `ω²r` in quadrature, after removing the characterized noise floor, restores the flag completely: **ballistic false-positives 0.841 → 0.000, true-lift jumps retained 11/11** (E4).

---

## The six experiments

| # | Experiment | Headline | Verify |
|---|-----------|----------|--------|
| **E1** | Bounding sweep — worst plausible wing overshoot | Worst overshoot across 3072 combos is **1.169×**, and only in the never-depower "constant" corner at high wind / high c_max; realistic techniques stay near ballistic (p90 = 1.057×). 100% detected. | ✅ CONFIRMED |
| **E2** | Aero-model anchor (baseline) | *Reference anchor used throughout:* arm-capped `aero_model` at ceiling 0.40 bw gives overshoot **1.00–1.07×** — wings are ~ballistic. | ✅ anchor |
| **E3** | Self-diag ROC — can `|a|` flag non-ballistic flight? | Median airborne `|a|` is a **perfect flag without rotation (AUC 1.0000)** but **collapses to AUC 0.2582 under a spin confound** — the bare-`|a|` flag needs gyro help. | ✅ CONFIRMED |
| **E4** | Rotation confound + gyro compensation | A 0.5 rps spin at a 0.3 m lever false-positives the bare flag; gyro `ω²r` subtraction drops ballistic FP **0.841 → 0.000** while keeping **11/11** true-lift jumps flagged. | ✅ CONFIRMED |
| **E5** | Failure-boundary map (safe / flagged / blind) | The dangerous band — detected, mis-measured ≥10%, yet **unflagged** — is only **a_v = 0.09–0.10 g (0.02 g wide, bias ≤11.7%)**, identical across 2/3/5 m jumps. | ✅ CONFIRMED |
| **E6** | Kite-validation gate — reproduce Simons 2025 | Pipeline reproduces the kite anchor at **2.3091× overshoot** (5.102 m true vs 11.780 m ballistic at 3.100 s); self-diag strongly flags it (median `|a|` = 0.582 g). | ✅ CONFIRMED |

All five run experiments carry an independent verify note that re-derived the load-bearing numbers against the physics core (E1 max overshoot 1.1674 vs 1.1686; E3 AUCs reproduced by two independent methods; E4 has a standalone verifier script; E5 reproduced to the last digit; E6 re-bisected `vz0` from scratch).

---

## Key numbers

### The governing law
- **Overshoot = 1/(1 − a_v)** — closed form, matches numerical integrator to <0.1% at a_v = 0.10 / 0.40 / 0.567 / 0.70 g.
- Mid-flight `spec_g == a_v` exactly — the airborne accelerometer reads sustained lift directly. This is what makes self-diagnosis possible.

### E1 — worst-case bounding (3072 combos)
- `frac_detected = 1.000` (3072/3072)
- `max_overshoot = 1.1686` — only at technique=constant, wind=18, c_max=1.3, mass=70 kg
- `p50 = 1.0171`, `p90 = 1.0569`, `p99 = 1.1171`
- Realistic techniques at ceiling 0.40, excluding never-depower: **max 1.095×**
- Overshoot *decreases* as arm ceiling rises (0.30→1.032, 0.40→1.014, 0.55→1.011, 0.75→1.011): a higher cap grows the horizontal/drag component without proportional vertical lift.
- 357 rows *under*-shoot (<1.0) — a detector late-gate timing artifact, not under-lift.

### E3 — self-diag ROC
- `AUC_A (no spin) = 1.0000`; best op point thr 0.045 g → TPR 1.000 / FPR 0.000
- `AUC_B (spin) = 0.2582` — a 74.2% relative degradation, **below 0.5** (spin inverts the flag)
- Shipped 0.12 g flag: FPR_A = 0.000, **FPR_B = 0.856**
- Spin term adds mean 2.04 g (max 8.82 g) — dwarfs the 0.135–0.618 g lift medians.

### E4 — gyro compensation
- Analytic crossover where `ω²r = 0.12 g` at 0.3 m lever: **0.315 rps** (bare flag breaks above ~0.32 rps)
- Bare-flag ballistic FP = **0.841** (37/44 combos)
- **33/44** combos push airborne median above the 0.35 g gate → firmware detector **silently misses** exactly those 33
- Gyro (quadrature + noise-floor) FP = **0.000**, true-lift retained **11/11**, recovered spec_g ≈ 0.20 g
- Naive *linear* subtraction also zeroes FP but over-corrects: retains only **2/11** true-lift (the arm term combines in quadrature, not linearly). Quadrature *without* noise-floor removal keeps 11/11 but only drops FP to 0.659. **Only quadrature + noise-floor does both.**

### E5 — failure-boundary map
- SAFE: a_v 0.00–0.04 (bias <5%)
- MILD-BLIND: 0.05–0.08 (5–9%, unflagged but sub-material)
- **DANGER-BLIND: 0.09–0.10 (bias 10.1–11.7%, unflagged AND material) — 6 cells total, 0.02 g wide**
- FLAGGED: 0.11–0.33 (bias climbs 12.5%→90%+, but always warned)
- HARD-BLIND: ≥0.34 g — airborne `|a|` crosses the 0.35 g gate, jump vanishes (missed, never a wrong number)
- Anchor: closed-form bias at a_v=0.20 = 25.0%, observed 25.1–25.4%.

### E6 — kite gate (credibility anchor)
- `overshoot = 2.3091×`, true apex 5.102 m, ballistic prediction 11.780 m, airtime 3.100 s
- closed form 1/(1−0.567) = 2.3095× (gap is body drag)
- Realistic aero wings over 32 configs: **1.0022–1.0790×** vs the kite's 2.309×
- Self-diag on the kite: median `|a|` = 0.582 g, **flagged = True**
- Detector **silently misses** the kite (0.582 g > 0.35 g gate, 3.10 s > 3.00 s max) — the 11.780 m is what a device *would* log if it triggered.

---

## Roadmap impact

1. **Ship the airtime method (`h = g·T²/8`) as the primary height number.** For wing foiling it is accurate to ~1–7% in the realistic band (E1 p90 = 1.057×, E2 anchor 1.00–1.07×). This is the competitive claim against WOO/Surfr: on wings, the physics is on our side.

2. **Ship a self-diagnosis flag** (median airborne `|a| > 0.12 g`) that marks a jump as non-ballistic / height-inflated. Without spin it is a perfect discriminator (E3 AUC 1.000). This is the honesty layer: when a jump *isn't* ballistic, the user is told, not silently handed a wrong number.

3. **The flag requires the gyro for spun tricks.** A single accelerometer-magnitude threshold is trapped: at 0.12 g, 84% of spun ballistic jumps false-positive; raise it and you miss real lift. Wire the gyro `ω²r` subtraction (quadrature, with a per-unit-calibrated noise floor) so spins don't false-alarm (E4: FP → 0.000, sensitivity 11/11 preserved). **This is a firmware requirement, not a nice-to-have.**

4. **Publish the error bars.** State the ~1–7% wing overshoot band openly. It is a feature versus competitors who quote a single number with no uncertainty — and it's defensible because it's grounded in the arm-force ceiling.

5. **Know the two silent-failure regimes and design UX around them:**
   - **DANGER-BLIND (a_v 0.09–0.10 g):** detected, ~10–12% high, *unflagged*. Narrow (0.02 g) and off to the side of physical wing behavior, but real. A slightly more conservative flag threshold could close it if desired.
   - **HARD-BLIND (a_v ≥ 0.34 g):** the jump is silently *missed* entirely (airborne `|a|` masks free-fall past the 0.35 g gate). This is kite/harness territory, annoying (a dropped jump) but never a wrong number. Note this is also why a spin can make a jump vanish (E4: 33/44).

---

## Honest caveats

- **Everything rests on the arm-ceiling and aero assumptions.** The entire "wings are ballistic" result flows from the premise that arm force caps sustained `a_v` well below a kite's 0.567 g. That premise is modeled (`aero_model`, arm_ceiling_bw), not yet measured on water. **Real on-water airborne `|a|` data checks this premise directly** — the sensor already logs the exact quantity (`spec_g == a_v`). First priority once S0 hardware is collecting real jumps: histogram airborne `|a|` and confirm it sits in the 0–0.07 g band the model predicts. If real riders routinely exceed ~0.09 g sustained, the DANGER-BLIND analysis (E5) moves to center stage.

- **The E1 1.169× headline is a corner-case tail**, not a realistic operating point: it needs technique=constant (wing held at full c_max the entire flight, never depowered), c_max=1.3 (above the 0.8 default), wind=18 m/s, and the lightest 70 kg rider simultaneously. Realistic depowering at ceiling 0.40 stays ≤1.095×.

- **The gyro noise-floor constant (0.0135 g)** used by the E4 correction is the sensor model's half-normal median. It is device-characterizable but **must be calibrated per unit** in real firmware — the 0.000 false-positive result depends on it.

- **Failure-band widths are mildly seed-sensitive at the 1-cell level.** The DANGER-BLIND band is 0.02 g at the reported seed; the ~0.02 g *scale* is robust, but the exact cell membership shifts slightly because the noise offset (~0.0135 g) sits between a_v and the 0.12 g flag. E5 also ran with rotation off — a real spin shifts these boundaries (E4's domain).

- **`const_lift` is the worst-case envelope, not the wing's actual profile.** Physical wings decay after takeoff (aero force is largest at peak apparent wind, then falls as the rider accelerates downwind) and are arm-capped, so their effective a_v is lower and time-varying than the constant-lift maps assume. The real operating point is more favorable than the bounding maps.

- **Sensor saturation at extreme spins** (lever 0.8 m, ≥2.25 rps hits the 16 g clip) is excluded from E4's specificity claim — unphysical for a wing rider, but a real firmware guard should handle clipped samples gracefully.


---

## E7 — threshold sweep against REAL recorded motion (2026-08-15)

**The first experiment in this file that is not run on synthetic data.** E1-E6
all used `sim/generate.py`, which builds its signal from the same physical model
the detector assumes — so they show the detector is self-consistent, not how it
behaves on motion a person actually made. This sweeps 6,174 parameter
combinations across all 638,852 samples of the 2026-08-15 pocket-carry
recording. Every combination was verified to consume every sample
(`update()` call count == row count).

### Ground truth, and why it is not circular
The device found 10 events. Nine have a median airborne |a| of 0.039-0.154 g.
One — event 7, t=4650.087 — sits at **1.393 g**. Free fall reads ~0 g by
definition, so that is not a badly-measured jump; it is not a jump. The
criterion separates the set with a 10x gap, needs no tuning, and is independent
of the gates being swept (it looks at what the acceleration *did* inside the
window, not at the thresholds that opened it).

### Finding 1 — the shipped configuration keeps the false positive
At `freefall_enter_g=0.35, freefall_confirm_s=0.08, landing_threshold_g=2.50,
min_airtime_s=0.25` the detector returns **10 events: all 9 real ones, plus the
1.393 g event.** 3,035 of the 6,174 combinations do strictly better.

### Finding 2 — two independent, wide plateaus fix it
Holding everything else at shipped values:

| `freefall_enter_g` | events | real kept | keeps the 1.393 g event |
|---|---|---|---|
| 0.15 - 0.30 | 9 | 9/9 | **no** |
| 0.32 - 0.36 | 10 | 9/9 | yes |
| 0.40 / 0.50 / 0.60 | 12 / 24 / 47 | 9/9 | yes |

| `min_airtime_s` | events | real kept | keeps the 1.393 g event |
|---|---|---|---|
| 0.20 - 0.30 | 10-11 | 9/9 | yes |
| **0.35 - 0.45** | **9** | **9/9** | **no** |
| 0.50 | 8 | 8/9 | no — but now losing a real jump |

Either change alone removes it with no loss. `freefall_enter_g <= 0.30` is a
plateau a full factor of two wide (0.15-0.30, all identical and correct).
`min_airtime_s` in 0.35-0.45 is correct across the whole band, and the shortest
real airtime in the recording was 0.505 s, so 0.35 leaves 0.15 s of margin.

### Finding 3 — the sensitivity avalanche lives above 0.38
Event count explodes as `freefall_enter_g` rises: 11 at 0.38, 12 at 0.40, 16 at
0.45, 24 at 0.50, **47 at 0.60**. The shipped 0.35 sits on the quiet side of
that, which is reassuring — but it is only ~0.03 from where the count starts
climbing, and the water is noisier than a pocket.

### Honest limits
One recording, ten events, hand tosses and pocket motion — not water. Tuning
hard against a single false positive is exactly how a detector gets overfitted.
The defensible reading is not "set it to 0.30" but: **the shipped point sits
just inside the region where this class of false positive survives, and there
are two wide, independent plateaus that reject it at no measured cost.**

Reproduce: `python3 sim/experiments/e7_threshold_sweep.py` (~1 min, 6 workers).


---

## E8 — does E7's recommendation survive a different world? (2026-08-15)

E7's result rests on **one recording, ten events, a trouser pocket, on land.**
Tuning against that is how detectors get overfitted, and carrying false
confidence into a one-shot session is worse than changing nothing. So E8
re-runs the sweep against 12 systematically perturbed versions of the same
recording — added noise (σ = 0.01/0.02/0.05 g), gain error (±5 %), offset
(±0.02 g), sample-clock error (±0.5 %), and two combined worst cases that are
noisy, mis-scaled and biased at once.

"Correct" keeps E7's meaning: find all nine free-fall-consistent events, reject
the 1.393 g one, and add nothing spurious.

### Result

| operating point | correct in |
|---|---|
| **shipped** — enter 0.35 / min_air 0.25 / confirm 0.08 | **0 of 12 worlds** |
| **recommended** — enter 0.26 / min_air 0.30 / confirm 0.08 | **12 of 12 worlds** |

63 of the 120 tested points are correct in all 12 worlds, so this is a broad
region, not a needle. The densest part of it is `freefall_enter_g` 0.24-0.28
(11-12 of 15 sub-combinations robust, versus 6 of 15 at both 0.20 and 0.35),
which is why the recommendation sits at 0.26 rather than at the edge.

### The nuance that matters
**The shipped configuration never misses a real jump** — 9/9 in every world,
including 0.05 g of added noise. This is a *precision* problem, not a
sensitivity one. The change buys rejection of a false positive; it does not
rescue any missed jump, and nothing here suggests the detector is deaf.

Under gain 0.95 and offset −0.02 the shipped point also picks up a spurious
event beyond the known one (11 events), so its error grows as conditions
degrade, while the recommended point returns exactly 9 in every world.

### Honest limit — read this before changing anything
These 12 worlds perturb the **sensor**, not the **motion**. They say the
operating point tolerates noise, scale and clock error. They cannot say it
tolerates water: a foil jump is longer and smoother than a hand toss, a rigid
mount transmits impacts a pocket absorbs, and none of that is in this data.
E8 shows the recommendation is not an artefact of one clean recording. It does
not show it is right for the water.

Reproduce: `python3 sim/experiments/e8_robustness.py` (~1 min).

---

## E9 — the E7/E8 question, asked of every recording (2026-08-24)

Replayed all 16 unique trace files under `data/` through an 84-point
threshold grid centred on E7's plateaus. Nominal exposure 151 h, **but a
dedup caveat found during the mechanism check applies**: content-hash dedup
does not catch a stored session synced repeatedly as it GREW, so one desk
session's events appear in six traces. Per-config comparisons are unaffected
(every config replayed identical material); absolute hours and event counts
overstate unique exposure. Stated here so nobody quotes "76 events / 151 h"
as corpus truth.

On unique content, three facts, each verified event-by-event:

1. **The only event unique to the shipped config is E7's 1.393 g slap**
   (walk trace, t=4650, median-|a| 1.162 g — not a jump). The E8
   recommendation finds everything else shipped finds, and misses nothing.
2. **The shipped gate pins soft-entry takeoffs early.** One lobbed desk toss
   reads air 0.899 s under shipped vs 0.699 s under the recommendation —
   the 0.35 g gate triggers ~0.2 s up the entry slope, inflating that
   event's implied height ~65%. The drop ritual cannot see this (drops have
   crisp entries; its −19 ms bias is real but motion-shape-specific).
3. **The grid is a plateau, not a needle**: 73–78 events across all 84
   configs; no cliff anywhere in the region.

## E10 — the false-positive budget's first exposure hours (2026-08-24)

The proposed <1 phantom/hour budget, measured for the first time: 11
seastate regimes (chop to H=1.2 m, slap to 8/min at 4 g, chatter, mixes) ×
60 seeded 1-hour streams × 4 operating points = 660 exposure-hours per
config. **Zero phantoms, every cell** — rate < 0.05/h at 95% for every
regime × config, twenty times inside the budget. The harness was proven
able to phantom first (hair-trigger config: 303 in 10 min), so the zeros
are measurements, not silence.

Two corollaries: DECISION #30's stated fear for lowering the gate ("common
false takeoffs on chop") does not bind anywhere down to enter 0.24 **on
this noise family** — the recommendation's raised min_airtime does the
compensating work. And the budget itself is now a measured bench bound, not
a bare proposal. Honesty: seastate.py is a bench stand-in by its own
header; the water day measures the real family.

### The open decision these two leave on the table

`config/params.json` still ships E7/E8's 0-of-12-worlds point, nine days
after the 12-of-12 recommendation. E9 adds: the corpus contains no
counterexample to the recommendation, and one concrete case where the
shipped gate corrupts an airtime. E10 adds: the change costs nothing in
false positives on every noise family we can synthesize. **Shipping it is
the owner's call, and it re-opens the drop ritual** — tonight's
airtime_offset_s was measured AT the shipped gates, and E9 fact 2 is direct
evidence the pin moves with the gate.

---

## E11 — the miss-cost of lowering the gate (2026-08-24/25)

400,000 jumps, E2's physics unchanged (dt=2e-5), each rendered signal judged
by BOTH operating points so every disagreement is a named jump. 10 h runtime.

| config | silent misses | rate (95% CI) | height RMSE | bias |
|---|---|---|---|---|
| **shipped** 0.35 / 0.25 | **3 / 400,000** | 7.5e-6 (2.6e-6…2.2e-5) | 8.8 cm | +7.5 cm |
| **e8_rec** 0.26 / 0.30 | **48 / 400,000** | 1.2e-4 (9.1e-5…1.6e-4) | 10.1 cm | +6.1 cm |

Paired: **45 jumps missed ONLY by the recommendation, 0 missed only by
shipped.** The recommendation is 16x worse in the worst failure class.

The 45 are not exotic: 44 of 45 are `constant` (never-depower) technique at
median 18.7 m/s wind, median apex **1.35 m**, and **22 of them clear 1.5 m** —
jumps a rider would certainly notice were missing. Their mid-flight spec_g
spans 0.058–0.320, i.e. exactly the 0.26–0.35 band the lower gate stops
admitting. The band's population was a guess before this run; it is now
measured.

The 3 both-missed (spec_g 0.335–0.367, all `constant`, ~20 m/s) are DECISION
#30's documented class, independently reproduced at a fresh seed: 7.5e-6 here
against #30's 2.5e-5 from 200k — same order, and #30's verdict stands.

### The decision this settles, opposite to the way it was leaning

E8/E9/E10 built a one-sided case for shipping 0.26/0.30: robust in 12/12
perturbed worlds, no corpus counterexample, zero phantoms in 660 h/config.
E11 was written to test its one unmeasured flank, and **the flank is real**.
The full trade, all four experiments together:

- **Cost of the recommendation:** 16x the silent misses — invisible, no log,
  no watch symptom, half of them on 1.5 m+ jumps.
- **Benefit of the recommendation:** rejects one 1.393 g slap in ~151 h of
  land motion (E9) — a false positive that is *visible*, reads 1.162 g median
  airborne, and is filterable in post; plus E8 robustness under perturbation.

Trading a visible, correctable false positive for 16x more invisible misses
is the wrong direction for this project, whose own DECISION #30 already ruled
that a silent miss is the worse failure. **Recommendation: HOLD the shipped
gate.** The drop calibration measured at those gates therefore stands, and no
re-drop is needed.

Open question this leaves, worth one more run: the gate was tested at two
points, 0.26 and 0.35. An intermediate gate might reject the slap without
buying the misses — E9 showed 0.28 and 0.30 behave like the recommendation on
land, but nothing has measured their miss rate. That is E12.
