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
