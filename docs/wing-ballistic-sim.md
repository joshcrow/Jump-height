# Wing-foil ballistic simulation — is the airtime method valid for wings?

**Provenance:** authored 2026-08-04/05. A pre-verified physics core
(`sim/wing_model.py`, `sim/sensor_model.py`, `sim/selfdiag.py`) plus a
six-experiment battery run and adversarially re-verified by an agent workflow
(12 agents, 5/5 experiment headlines independently re-derived against the core,
kite anchor reproduced <0.1%). Raw digest: `sim/experiments/RESULTS.md`;
per-run CSVs: `sim/experiments/out/*.csv`. This doc is the durable record.

## 1. The question

Height is computed from airtime, `h = g·T²/8` (`detector.py`), which is exact
only for **ballistic** flight (gravity the sole force). The *kite exception*
(docs/research.md §2, Simons 2025) proves this fails for kites: a tether holds
the rider aloft, so 3.1 s of airtime maps to 5.1 m true apex, not the 11.8 m
the formula returns — a **2.3× overshoot**. Open question, zero prior
literature: does a hand-held **wing** add enough sustained lift to break the
formula the same way? If yes, the cheap airtime approach (and the cheap
hardware that rides on it) is invalid and we need WOO-style double-integration.
If no, the whole product thesis holds.

## 2. Method

Forward model → observation model → firmware-twin estimator → self-diagnosis,
each anchored to something we can check independently.

- **`wing_model.py`** — RK4 centre-of-mass trajectory under gravity + a wing
  aerodynamic force (`F = ½·ρ·V_app²·A·C(t)`, dynamic apparent wind, technique
  schedules, **arm-force ceiling**) + body drag. Emits true apex, true airtime,
  and CoM specific force. `kite_preset` swaps the arm ceiling for a harness.
- **`sensor_model.py`** — renders the `|a|` a real IMU logs: CoM specific force
  + rotation-arm (`ω²r`) + noise + landing spike + range clip, at 200 Hz.
- **`detector.py`** — the existing firmware twin, unchanged, consumes the
  rendered `|a|`.
- **`selfdiag.py`** — the airborne-`|a|` non-ballistic flag.

**Governing law (the spine).** For constant vertical lift `a_v` (in g), flight
is reduced-gravity ballistics with `g_eff = g·(1−a_v)`, so:

```
h_reported / h_true = 1 / (1 − a_v)
```

The integrator reproduces this to <0.1% at a_v = 0.10/0.40/0.567/0.70. Mid-flight
`spec_g == a_v` exactly — the accelerometer reads sustained lift directly, which
is what makes self-diagnosis possible. **Why wings ≠ kites:** wing force is
transmitted through the **arms** (capped, ~0.3–0.5 body-weights), a kite's
through a **harness** (uncapped) — and 0.567g sustained is exactly the kite
overshoot. Arms cannot deliver that.

## 3. Results (all headlines adversarially re-verified)

| # | Experiment | Headline | Check |
|---|---|---|---|
| E1 | Bounding sweep (3072 combos) | Worst overshoot **1.169×**, only in the never-depower corner (constant technique, 18 m/s, c_max 1.3, 70 kg); realistic p90 **1.057×**, p99 1.117×. 100% detected. | ✅ CONFIRMED (indep. 1.1674) |
| E2 | Monte Carlo (5000 jumps) | 100% detected, overshoot mean **1.013×**, p99 1.064×; only 0.2% >1.10×. Height-error **RMSE 4.2 cm** vs Marčiš'21 Surfr 51 cm / WOO3 70 cm. | ✅ ran — **superseded, see E2′** |
| E2′ | Monte Carlo (**200,000 jumps**) | **5 silent misses** (2.5e-5), all `constant` technique; overshoot mean **1.0128×**, p99 1.0622×, 0.18% >1.10×, **>1.20× vanishes**. RMSE **4.6 cm**. | ✅ ran 2026-08-11 |
| E3 | Self-diag ROC | Median airborne `|a|` flag is perfect without spin (**AUC 1.000**), inverts under a spin confound (**AUC 0.258**). | ✅ CONFIRMED (two methods) |
| E4 | Rotation + gyro | Bare flag false-positives 84% of spun jumps; gyro `ω²r` subtraction (quadrature + noise floor) drops FP **0.841→0.000**, keeps **11/11** real jumps. Linear subtraction over-corrects (2/11). | ✅ CONFIRMED (standalone verifier) |
| E5 | Failure map | Dangerous *detected-but-wrong-and-unflagged* band is only **a_v 0.09–0.10 g** (0.02 g wide, bias ≤11.7%), identical across 2/3/5 m. Hard-blind (silent miss) at a_v ≥ 0.34 g. | ✅ CONFIRMED (to last digit) |
| E6 | Kite gate | Reproduces Simons 2025: **2.309×** overshoot, 5.10 m true vs 11.78 m ballistic at 3.10 s; self-diag flags it (median `|a|` 0.582 g). | ✅ CONFIRMED (re-bisected vz0) |

**Verdict: wings are ballistic enough — ship the airtime method.** Realistic
wing overshoot is 1.00–1.07×; the method's own error contributes ~4.6 cm, an
order of magnitude below the ~50 cm real-world error the best competitor is
video-measured at. The kite exception does not transfer.

### E2′ — what 40× the sample changed (2026-08-11)

E2 was rerun at **N = 200,000**. The bulk statistics barely moved (mean
1.0133→1.0128×, p99 1.0637→1.0622×), which is good evidence the original run was
not lucky. **The tails were another matter, and both corrections run in the
honest direction:**

1. **"100% detected" was an artifact of undersampling.** At 200k the detector
   silently misses **5 jumps (2.5×10⁻⁵)** — every one of them in the `constant`
   (never-depower) technique, which had only 155 samples at N=5000 and now has
   6,179. Per-technique: `sheeted_out` 0/119782, `mixed` 0/49994, `lofted`
   0/24045, `constant` **5/6179 (0.08%)**.

2. **The 1-in-5000 `>1.20×` event was noise.** At 200k the `>1.20×` bucket is
   empty. There is no fat overshoot tail; that single draw was a fluke.

**E5 called this shot.** E5's failure map predicted "hard-blind (silent miss) at
`a_v ≥ 0.34 g`", and E2 had simply never sampled there. It has now, and the
boundary is not approximate — it is the gate, exactly:

| mid-flight `spec_g` | jumps in 200k | missed |
|---|---|---|
| ≥ 0.34 g (E5 hard-blind) | 6 | 5 |
| ≥ 0.35 g (`freefall_enter_g`) | 5 | **5 of 5** |
| 0.34–0.35 g | 1 | 0 |

Every jump at or above the 0.35 g free-fall gate was missed; the single jump
just below it was caught. The mechanism is the same one the kite exception
describes: sustained lift holds `|a|` above the free-fall gate, takeoff never
registers, and the jump is never seen. All five sat in 20.1–21.5 m/s wind
(the top ~1% of the sampled range) at modest apex (1.00–2.36 m) — a wing sheeted
constant in 40 knots is briefly *behaving like a kite*. **The failures confirm
the theory rather than undermining it.**

**Population exposure** (the number §5's first caveat asked for, in sim):
`spec_g_mid` mean **0.0220 g**, p50 0.0208, p90 0.0265, **p99 0.0667**, max
0.4414. That p99 lands essentially on the predicted 0–0.07 g band. Only
**0.52%** of jumps reach E5's danger-band floor (≥0.09 g) and **0.003%** reach
hard-blind. Simulated, not measured — the water check in §5 still stands.

**Reproducing it.** `sim/experiments/out/` is gitignored — experiment outputs are
local artifacts and these documents are where the numbers live, so every figure
above is stated inline rather than linked to a file you won't have. Regenerate
with:

```bash
E2_N=200000 E2_OUT=/tmp/e2_200k.csv python3 sim/experiments/e2_montecarlo.py
```

~5 h on 5 cores; the per-jump CSV is ~48 MB. Jump *i* is seeded by index, so a
big run **reproduces a small one row for row and only appends** — verified
bit-identical for the first 5000 rows against the committed-era N=5000 output,
and again at N=60 after the env-override change. A local run of this file at the
default N=5000 still reproduces the E2 row above exactly.

## 4. Roadmap impact

1. **Ship `h = g·T²/8` as the primary height number.** Accurate to ~1–7% for
   wings — the competitive claim against WOO/Surfr is that the physics is on our
   side.
2. **Ship a self-diagnosis flag** (median airborne `|a| > 0.12 g`): perfect
   discriminator without spin (E3). The honesty layer — warn, don't silently
   hand over a wrong number.
3. **The flag requires the gyro for spun tricks** (E4). A single accelerometer
   threshold is trapped; only `ω²r` subtraction fixes it. Firmware requirement,
   and it doubles as the enabler for rotation metrics (see the gyro-value
   backlog). Consistent with the roadmap's existing Tier-C gyro item.
4. **Publish the error bars.** State the ~1–7% band openly; the market ships
   none (docs/research.md §5).
5. **Design UX around two silent-failure regimes:** the narrow 0.09–0.10 g
   mis-measure band (E5), and the ≥0.34 g "jump vanishes" regime where airborne
   `|a|` masks free-fall past the 0.35 g gate (also how a fast spin drops a
   jump).

## 5. Honest caveats

- **Everything rests on the arm-force ceiling / aero assumptions.** "Wings are
  ballistic" flows from arms capping `a_v` well below a kite's 0.567 g — modelled,
  not yet measured on water. **First priority once S0 hardware logs real jumps:
  histogram airborne `|a|` (the sensor records exactly `spec_g == a_v`) and
  confirm it sits in the predicted 0–0.07 g band.** If real riders routinely
  exceed ~0.09 g sustained, the E5 danger band moves to centre stage.
- The 1.169× worst case is a corner artifact (never-depower + 18 m/s + lightest
  rider + c_max 1.3 simultaneously); realistic technique ≤1.095×.
- The **4.6 cm** RMSE (4.2 cm at N=5000; the larger sample reaches bigger jumps,
  apex to 7.60 m) is the **method's physics floor**, not a field-accuracy claim
  — it excludes real sensor/detection/calibration error. The point: the airtime
  method is not the accuracy bottleneck; placement and calibration are.
- **The detector is not miss-free.** It silently drops jumps whose mid-flight
  specific force stays at or above `freefall_enter_g` (0.35 g) — 2.5×10⁻⁵ of the
  simulated population, entirely within never-depower technique in 40-knot wind.
  A silent miss is the worst failure class there is (nothing on the watch, no
  flag, no log), so it is called out here rather than rounded to "100%".
- `const_lift` is a worst-case envelope; real wings depower after takeoff, so the
  true operating point is more favourable than the constant-lift maps.
- The gyro correction's noise-floor constant is device-characterizable but must
  be calibrated per unit; sensor clipping at extreme spins is excluded from E4's
  specificity claim.

## 6. Open follow-ups (queued)

- **Sensitivity study:** explicit wing-area + apparent-wind sweeps and a
  stress-test of the two caps (arm ceiling, sheet-out decay) to map the worst
  realistic corner; confirm foil/board size (mass + drag) are second-order for
  the height question (added mass makes a jump *more* ballistic).
- **Gyro value brief:** what a 3-axis gyro unlocks beyond the self-diag fix —
  rotation/trick metrics, landing-quality, sharper airtime timing, height
  *correction* (not just flagging) — mapped to accuracy gain, power cost
  (~0.9 mA combined on the LSM6DS3TR-C, not the ~3.6 mA the ESP32-era MPU-6050
  drew), and competitive differentiation. **Done:** see
  [gyro-sim-plan.md](gyro-sim-plan.md) and [gyro-prior-art.md](gyro-prior-art.md).
