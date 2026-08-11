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
| E2 | Monte Carlo (5000 jumps) | 100% detected, overshoot mean **1.013×**, p99 1.064×; only 0.2% >1.10×. Height-error **RMSE 4.2 cm** vs Marčiš'21 Surfr 51 cm / WOO3 70 cm. | ✅ ran |
| E3 | Self-diag ROC | Median airborne `|a|` flag is perfect without spin (**AUC 1.000**), inverts under a spin confound (**AUC 0.258**). | ✅ CONFIRMED (two methods) |
| E4 | Rotation + gyro | Bare flag false-positives 84% of spun jumps; gyro `ω²r` subtraction (quadrature + noise floor) drops FP **0.841→0.000**, keeps **11/11** real jumps. Linear subtraction over-corrects (2/11). | ✅ CONFIRMED (standalone verifier) |
| E5 | Failure map | Dangerous *detected-but-wrong-and-unflagged* band is only **a_v 0.09–0.10 g** (0.02 g wide, bias ≤11.7%), identical across 2/3/5 m. Hard-blind (silent miss) at a_v ≥ 0.34 g. | ✅ CONFIRMED (to last digit) |
| E6 | Kite gate | Reproduces Simons 2025: **2.309×** overshoot, 5.10 m true vs 11.78 m ballistic at 3.10 s; self-diag flags it (median `|a|` 0.582 g). | ✅ CONFIRMED (re-bisected vz0) |

**Verdict: wings are ballistic enough — ship the airtime method.** Realistic
wing overshoot is 1.00–1.07×; the method's own error contributes ~4 cm, an order
of magnitude below the ~50 cm real-world error the best competitor is
video-measured at. The kite exception does not transfer.

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
- The 4.2 cm RMSE is the **method's physics floor**, not a field-accuracy claim
  — it excludes real sensor/detection/calibration error. The point: the airtime
  method is not the accuracy bottleneck; placement and calibration are.
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
