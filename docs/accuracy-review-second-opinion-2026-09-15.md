# Second opinion — can the existing Sense measure wingfoil jump height to ±0.5 ft?

**Date:** 2026-09-15. **Object:** an independent re-examination of the question answered in `docs/accuracy-review-2026-09-15.md` (rev `9db1564`), written to stand alone. **Target:** ±0.5 ft = **±0.1524 m**. **Hardware:** Seeed XIAO nRF52840 Sense, LSM6DS3TR-C, 2 MB external flash. **Corpus:** one real riding session with a trace (`data/sessions/20260914-210637-E2C4`), recorded with the puck in a **vest pocket** (`docs/STATUS.md:28`). No board-mounted session exists.

Every number is tagged **MEASURED** (hardware, a recorded file, or source code), **SIMULATED**, or **DERIVED** (arithmetic from measured or stated-assumed inputs). Nothing here changes firmware, calibration, or the production detector.

---

## 1. Executive judgment

**The ±0.1524 m target is not reachable today for any jump class, and the obstacle is not the sensor.** The LSM6DS3TR-C is good enough: its white-noise floor integrates to about **1 mm over a 4-second flight** and quantization is 0.14 mg (DERIVED from DocID030071 Rev 3 Table 3). What stands in the way, in order: (i) **we cannot yet show that the events the device records are jumps**; (ii) **the endpoint** — the unknown difference between board height at takeoff and at landing, which on a foil is a ride-height problem, not a water-contact problem; (iii) **attitude and mount geometry**. None shrink when the sensor improves.

**By jump class (DERIVED; ANALYSIS, not measurement):**

- **Short routine jumps, T ≤ ~0.5 s.** The airtime formula's *physics* error is small here: even 30 % constant support costs 5.3 cm at 0.38 s, the corpus median. With a bounded endpoint and a calibrated unit, ±15 cm is arithmetically available. But all eleven recorded events fail the firmware's own in-flight test (finding 1), so: **plausible for this class once the events are validated; unproven until they are.**
- **0.5–1.0 s.** The airtime formula alone exhausts the budget at **0.788 s under 20 % support**, and the two longest recorded events (0.881 s, 0.931 s) are past it. Needs a reconstruction *and* a measured endpoint.
- **1.0–2.0 s.** Reconstruction only; the airtime formula is out (+1.3 to +2.9 m). Pass requires endpoint uncertainty ≤ ~0.2 m and a temperature-compensated calibration.
- **2.0–3.0 s.** Demanding: the endpoint must be *observed*, not assumed. Also outside the deployed acceptance window (`max_airtime_s = 3.0`).
- **Multi-second floats, ≥ 3.0 s.** **No defensible ±15 cm claim with this hardware alone**, and no such flight has ever been recorded by this device.

**Conditions under which it can work:** a rigid, measured board mount with a stored sensor-to-board transform; per-axis offset/scale calibration with a per-unit temperature fit; gyro bias re-zeroed close to each jump; six-axis raw capture with trustworthy sample timing; event boundaries and endpoint height established by something other than the accelerometer itself; and scoring restricted to intervals a reviewer actually watched.

**Dominant failure modes, largest first:** (1) event boundaries the state machine manufactured rather than observed; (2) endpoint displacement unknown by 0.3–0.45 m on a foil (assumption — no mast or ride-height figure exists anywhere in this repo); (3) takeoff-vs-landing pitch change moving the sensor point 10–20 cm at a 0.6 m lever; (4) accelerometer bias after a temperature swing, 10–15 mg uncompensated, costing 11 cm at 3 s; (5) silent accelerometer clipping on landing, which *lengthens* measured airtime and inflates `g·T²/8`; (6) calibrating against Surfr, whose airtime is not the flight time of the trajectory whose apex it reports.

**Is near-ballistic flight a reliable assumption?** **No — and the sharper problem is that this repo cannot yet say whether the eleven events it has recorded are single flights at all: nine of eleven have a median in-flight specific force above the 0.35 g free-fall gate itself, and two of them reload past 2.2 g in the middle of their own reported airtime.**

---

## 2. What this review did, and how it differs from the first report

**Method.** Four independent lenses — repository/implementation, physics and observability, video and reference geometry, prior art — each re-derived its numbers from primary sources, and each was then re-checked by a second skeptical reviewer whose default was to refute. Only corrections that survived that pass are used; figures the review deleted appear nowhere here.

**Reproduced.** Both of the first report's analysis scripts were re-run from a clean state. **Both reproduce byte-identically** (MEASURED):

```text
physics_results.json  18e65c29d874b3524548541c5299a0776cc274924f303e1c7c131bd11ef66eb7
repo_checks.json      cec82dfa13bd822f7d4d33e508c1ad3165f9ebc7ab279f5a9ef044287e9c518a
```

**Re-derived independently, not re-run.** Every numeric claim in the first report's sections 3, 5, 6 and `physics-notes.md` was recomputed with standard-library code importing neither the repo simulator nor the report's scripts: the support-overestimate table; `0.40·sin(35°) = 0.2294 g` and its 29.774 % overestimate; the late-support counterexample (own Euler integrator at dt = 1e-6 against their RK4 — agreement to the 5th significant figure); the three equal-mean-support paths and their 55.16 cm spread; the full bias budget and its ceiling column; the tilt-leak, boundary-timing and `(1+ε)²` time-scale results; the iid noise figures; the 20.56× dynamic-pressure ratio; the 1.83 Pa / 61.25 Pa barometer scales; the one-sided 95 % bound for 20/20. The closed forms `∫K(T/2,s)ds = T²/8, 3T²/32, T²/32` were derived analytically rather than integrated. **No arithmetic error was found in the first report's computational core.**

**Re-read at the source.** All twelve of the report's repository citations (R1–R12) were reopened at their exact anchors; all are accurate. The LSM6DS3TR-C datasheet was fetched and every quoted figure checked. The WOO whitepaper PDF, Marčiš et al. 2021, the x-io Fusion source at its pinned commit, the VQF abstract, Sensors 24:7877, the Surfr compatibility page, Kranzinger 2024 and the Oberhof ski-jump study were each fetched directly.

**New here, absent from the first report.** The per-jump `med_a_g` column the firmware already records; trace-level evidence of what happens inside the two longest reported flights; the absolute crossover at which the airtime formula exhausts the budget; the acquisition-timing arithmetic (dropped samples, stall-driven duplicates, aliased trace); gyro scale-factor error; the measured 1.033 g rest reading and what a divide does to an offset; the measured trace peak against the ±16 g rail; a foil-specific endpoint and pitch-geometry magnitude; the repo's own video methodology in `docs/data-pipeline.md`; the F-28 → F-33 chain; and the Garmin corpus's verdict on barometric altitude.

---

## 3. Supported findings

**1. Nine of eleven recorded events have a median in-flight specific force above the free-fall gate itself. [MEASURED]** `jumps.csv` carries `med_a_g`, the median |a| over the airborne window at the full 200 Hz. Values span **0.055–1.217 g**, median 0.675 g. The firmware calls this "THE MEASUREMENT THE PROJECT EXISTS TO MAKE" and predicts a **0–0.07 g** band (`main.cpp:351`, `:1946`). **Ten of eleven rows fall outside that band; nine of eleven exceed the 0.35 g gate.** `med_acorr_g == med_a_g` on all eleven, consistent with `spin_lever_m = 0` — no correction applied. (`corpus_recheck.py`, M1.)

**2. Two of the eleven "flights" contain a loaded contact in the middle. [MEASURED]** Strictly inside each reported airtime window, jump 2 reaches **2.24 g at +0.376 s** and jump 3 **2.29 g at +0.411 s**, then falls back below the gate and climbs again to the landing spike. Two contacts inside one reported airtime, not one flight. (`corpus_recheck.py`, M4.)

**3. One event's boundary was manufactured by a timer. [MEASURED]** Jump 7's takeoff at `t = 10258.462 s` follows a `NO_LANDING` closure at `t = 10258.447 s` — exactly `landing_settle_s = 0.500` after the previous low sample. Its sample count also fails to reconcile: `n_air = 74` recorded against `floor((0.395 − 0.08)×200) = 63`, and the trace carries 16 rows in that window at a uniform 20.00 ms spacing, consistent with 63–64 samples, not 74. **Ten of eleven rows reconcile; row 7 does not, and an unreconciled count is itself a finding** (CLAUDE.md rule 3).

**4. Rotation cannot explain finding 1. [DERIVED]** Asking what lever arm would make `|a| = ω²r/g` produce each row's median, using each row's own `med_w_dps`, gives **7.44–34.63 m for nine of eleven rows**. No radius on a rider or a board is in that range. Note the mount: these are **vest-pocket** data (`STATUS.md:28`), so torso articulation and pocket-relative motion are a third possible generator alongside real aerodynamic support and multi-contact episodes.

**5. The trace peaks above the configured rail, with no clipping instrumentation. [MEASURED]** Over all 648,808 rows: peak **19.496 normalized g at t = 12928.827 s**; 14 samples ≥ 8; 1 ≥ 16. The trace stores `mag = |a|/g_baseline` (`main.cpp:1764`), so at `g_baseline = 1.033` the raw peak is **≈20.14 g — 26 % above** the per-axis rail of `32768 × 0.488 mg = 15.99 g`, and **3 samples** reach that rail equivalent. `CTRL1_XL = 0x54` → ±16 g, already the maximum. `readAccelG`/`readGyroDps` perform no rail check; no `clipped_axes` flag exists anywhere. The trace is a 4:1 decimation, so the true per-sample peak is ≥ this. A clipped landing shortens the apparent spike, delays the 2.5 g crossing, lengthens airtime, and inflates `g·T²/8`.

**6. This unit reads 1.033 g at rest, and the normalization is the wrong shape for that error. [MEASURED + DERIVED]** `SELFTEST accel PASS detail=1.033g` (`session.json`, `device.log:23`). `main.cpp:749` adopts a rest measurement as `g_baseline` and `:1764` **divides** by it — a scale correction. If the error is an offset, the surviving fraction is `o(1−f)/(1+o)`, a function of in-flight specific force `f`: **77.4 % at f = 0.2 g, 48.4 % at 0.5 g, zero at 1 g.** At f = 0.2 g, 25.6 mg survives: 3.1 cm @ 1 s, **12.5 cm @ 2 s**, 28.2 cm @ 3 s. Caveat: 1.033 g is the *sync-time* selftest; the boot-time `g_baseline` in force during the ride is recorded nowhere in the bundle.

**7. The airtime formula's absolute crossover. [DERIVED]** Under constant support `L` the error is exactly `L·g·T²/8`; setting it to 0.1524 m (`corpus_recheck.py`, M5):

| Constant support L | 0.05 | 0.10 | 0.20 | 0.30 | 0.40 |
|---|---:|---:|---:|---:|---:|
| Longest airtime still within ±15.24 cm | 1.577 s | 1.115 s | **0.788 s** | 0.644 s | 0.558 s |

All eleven corpus events have raw airtime ≤ 0.931 s (median 0.380 s); at the median even 30 % support costs 5.3 cm. **The two longest rows are past the 20 % boundary.**

**8. The Surfr rows cannot be two measurements of one trajectory. [DERIVED]** Rows 6.32 ft / 3.07 s and 5.74 ft / 3.39 s, and the best 9.1 ft / 3.8 s, imply constant support of **0.833, 0.876, 0.843**. Holding 0.833 g on an 85 kg rider+gear for 3.07 s needs **694.6 N sustained** (730.0 N for the second row) — 71–74 kgf on the handles — against the repo's own `arm_ceiling_bw = 0.40` body weights = **333.4 N** (`sim/wing_model.py:84`), i.e. 2.08–2.19×. At a crosswind apparent wind of 13.60 m/s a 5 m² wing at C = 1.0 makes 566.6 N total; the required *vertical* coefficient alone is **1.23–1.29** against `c_max = 0.8` (`:80`). Directly downwind only 27.56 N is available and the required coefficient is ~25. Three independent rows all landing at 0.83–0.88 is a definitional mismatch, not a coincidence. The repo's own `score.md §3` shows the same effect on its own data: 32 load-band candidates with a longest nominal airtime of 1.96 s against a longest actually-unloaded band of 1.06 s (maxima of two independently sorted lists, so not established as the same event; direction stands).

**9. The prior-art brief's arithmetic is wrong by ~4.2×. [DERIVED]** `docs/prior-art-2026-09-14.md:55` gives "3.3 s at 0.5 g net ... ~1.6 m". At the repo's own `G = 9.80665`, `h = a_net·T²/8` gives **6.674651… m, which rounds to 6.67 m** (6.68 m requires `g = 9.81`). The 1.6 m matches `4.905 × 1.65²/8 ≈ 1.67 m`, suggesting an apex-time/full-time mix-up.

**10. Sensor noise is not the obstacle. [DERIVED from datasheet]** `An = 130 µg/√Hz` at ±16 g in high-performance mode (Table 3 p. 22; the part is in HP mode — `CTRL6_C = 0x00`, `CTRL7_G = 0x00`). With `σ_sample = n·√(ODR/2)`, the constrained midpoint error is **0.13 / 0.37 / 0.68 / 1.04 mm at T = 1 / 2 / 3 / 4 s**, sampling-rate independent as it must be; quantization adds 0.14 mg. This is a **white-noise floor, not a bound on total random error** — Rev 3 specifies no 1/f corner, bias instability, or rate random walk, and those are precisely the terms a `T³` integration amplifies.

**11. The endpoint term does not shrink with duration, and no foil geometry is documented. [DERIVED, on stated assumptions]** If the assumed landing-minus-takeoff displacement is wrong by `dD`, apex error is `dD/2`: 5.0 cm at 0.10 m, 15.0 cm at 0.30 m, 22.5 cm at 0.45 m. A wing foiler leaves from ride height on a mast and may land back on foil, through the surface, or between — a plausible unknown of 0.3–0.45 m, **an assumption**. `grep -rn -i mast docs/ DECISIONS.md data/notes/` returns no mast length and no ride height for Nick's setup. Centring the prior (`D = −0.175 m` rather than 0) halves the residual to ±8.75 cm.

**12. Board pitch moves the sensor point by the whole budget. [DERIVED, on stated assumptions]** Sensor rise differs from board rise by `[R(T)r − R(0)r]_z`. At r = 0.6 m: 5.2 cm per 5°, **10.4 cm at 10°, 20.5 cm at 20°**; at r = 0.7 m with ±10° symmetric, 24.3 cm. A nose-up pop and a flat or nose-down landing differ by well over 10°. Additive with finding 11, removable only with measured attitude at both endpoints. The excursion is unmeasured — the trace stores no gyro, so integrated pitch cannot be recovered from existing data.

**13. The firmware has none of the acquisition machinery a reconstruction needs. [MEASURED, code]** `lsm6ds3_min.h` never touches FIFO_CTRL or TIMESTAMP — only direct polling of `0x28` and `0x22`, in **two separate I2C transactions** separated by the whole motion-gate/trace block (`main.cpp:1737` vs `:1922`). Raw int16 is converted with no rail check. `trace_codec.h` stores one u16 milli-g **magnitude** per sample, one timestamp per block, synthesized spacing. The pending 100 Hz batch (`src=c5eea285`) changes only `LOG_DECIMATE` 4 → 2 — same scalar format, no gyro axes — and cuts raw-trace capacity from ~5.2 h to ~2.7 h. **It implements none of the report's Phase A/B asks.**

**14. Acquisition timing: a clean 8 Hz decimation beat between stalls, a duplication burst after every one. [DERIVED from code + MEASURED trace]** The pacer is `SAMPLE_INTERVAL_US = 5000` (`main.cpp:89`) on an RTC1 timebase quantized to 976.5625 µs (`twim_bounded.h:57`) against a 208 Hz ODR (period 4807.7 µs) with `BDU = 1`. Simulating that grid: poll interval 4882.8 / 5000.0 / 5859.4 µs min/mean/max, **0 duplicate reads per second, 8.0 ODR samples never read per second**, duplicates beginning below **204.8 Hz** — **1.54 % of margin**, and Rev 3 specifies no ODR tolerance anywhere. This holds only between stalls: `main.cpp:1733`/`:1736` resynchronize only after >100 ms, so any shorter stall is followed by a catch-up burst at loop speed (~200 µs per read, `twim_bounded.h:52–53`), far below the ODR period — duplicate samples under BDU = 1, counted and flagged nowhere. The session's trace has **62 gaps > 22 ms** and 15 backward timestamp steps (`score.md §1`). Separately, `LOG_DECIMATE` is a plain counter with **no anti-alias filter**, so the logged trace folds 25–104 Hz into the measurement band at `log_hz = 50`. Every replay, refit and score runs on that aliased trace; the live detector, seeing 200 Hz, does not.

**15. The repo's existing false-positive discriminator does not currently work. [MEASURED, repo]** F-28's clean |a| separation (6 phantoms at 0.50–1.53 g vs 9 tosses at 0.04–0.23 g, 09-06) was superseded twice inside its own entry — to 15 real / 12 spurious at 24 mg, then to **41 real / 13 spurious at a 17 mg margin, "no engineering headroom here at all"** (`audit-2026-08-22.md:232–256`). `STATUS.md`'s 09-07 field data then makes the pooled window **negative** (a real toss at 0.352 g above the lowest phantom at 0.255 g; 44 real vs 13 spurious). **F-33** (MAJOR, 09-10, `audit:455–480`) applied it to a puck worn on a *riding* rider: it labels **10 of 10 detections spurious, including the rider's real jumps**, and says it "must not be quoted as a phantom filter for the product." F-33 appears nowhere in `STATUS.md`'s findings table. Its own weight is bounded: n = 1 session, no per-jump ground truth.

**16. The repo already has a quantified video methodology. [MEASURED, repo]** `data-pipeline.md:197–204`: **"Use rider height in gear as the ruler. Not the mast"**, with three stated reasons. `:206–213`: **"Zero is the board's own position, NOT the horizon"** — a camera 0.8 m above the water sees the water plane 0.8 m below its level line at every distance, inflating a 1.5 m jump by **+53 %**, which "does not average out" and "survives every sanity check." `:221–225`: `dh = g·T·dT/4` → **8.2 / 4.1 / 2.0 cm at 30 / 60 / 120 fps** for a 1 s flight (independently recomputed), and the standing rule **1080p/120, not 4K/30**. `session-card.md:103–109` adds the range geometry: Nick's usual box starts **~325 m** off the Manteo waterfront against a **~250–300 m** phone-camera reach at 1080p/120, with a kayak/SUP **100–200 m abeam** as the documented alternative. `:79–82` gives the sync protocol: **three deliberate flat board drops ~2 s apart, explicitly not a finger tap** (2–5 ms, which the 50 Hz trace can miss entirely). A 0.3–0.5 s excursion above the 0.12 g motion threshold is not at risk — it logs ~15–25 rows at `log_hz = 50`, ~30–50 at 100 Hz (code read of `main.cpp:1766–1906`, `:2029–2037`; not bench-verified).

**17. The rider-facing accuracy numbers rest on the assumption under audit. [MEASURED, repo]** `session-card.md:125–139` currently tells the rider **flat ±6.5 cm · light-wind sound ±8 · typical ocean ±9 · 18 kt sound ±10 · 25 kt sound ±14**, produced by `e15_venues.py` on top of `e14_wavy_surface.py`. Those **[SIMULATED]** figures model wave-driven endpoint asymmetry *only*, assuming the rest of `g·T²/8` is correct. In `e14`'s own summary the sea-state-attributable bias increment over flat water is **−0.04 to −0.15 cm across all six states including 1.5 m swell** — so "chop adds spread, not bias" is what the data supports — but a ~1.98 cm baseline bias is present even in the flat control and is unexplained in the files as read.

**18. The watch barometer is not a height reference. [MEASURED]** From `docs/garmin-corpus-2026-09-15.md §1`: `enhanced_altitude` has exactly **one distinct value** on 09-09 (−15.6 m) and 09-14 (−29.2 m); where it moves, the robust noise floor is **0.297 m** (MAD) against a median puck jump of **0.36 m (09-10) and 0.21 m (09-14)**. The only two jumps with a FIT record inside the flight give in-flight residuals of **+0.13 m and −0.04 m** where the arc predicts +2.60 m and +3.36 m; the ±3 s excursion test gives permutation **p = 0.36**. **Most flights are never sampled**: 5 of 18 (27.8 %) contain any record; Monte Carlo on each ride's own timestamps gives P(hit) **0.173–0.199 at 0.4 s**. `0 of 338 zips` carry `baro_pa`, `baro_alt_m` or `baro_src`.

**19. A label-schema rename would fail silently. [MEASURED, code]** `sim/evaluate.py`'s `load_labels` reads every field with `r.get(k)`, and `num()` converts a missing value to `None` without raising. A `labels.csv` written with `takeoff_lo`/`takeoff_hi` instead of `t_start_s` would not error: every `t_start_s` would be `None`, the `jump_truth` filter at `:311` would silently drop every jump, and `eval_session` would report an empty score rather than a failure. Note the existing independence gate is `INDEPENDENT_SRC = frozenset({"ruler", "sim"})` (`:80`) — a `"sim"` height also passes, by documented intent.

**20. Statistical reality of a small pilot. [DERIVED]** Twenty successes out of twenty independent trials gives only an **86.1 %** one-sided 95 % lower bound on success probability, and jumps within a session are less independent than that assumes. A 90 %-in-tolerance × 90 %-coverage gate permits only **81 %** of real jumps to receive an in-tolerance result.

---

## 4. Disagreements with the first report

**D1 — §12 item 3 states 6.68 m; at the repo's own constant it is 6.67 m.** *Wrong:* arithmetic, last digit. *Evidence:* `G = 9.80665` (`physics_sensitivity.py:20`, `config/params.json`) gives 6.674651…; 6.68 m requires `g = 9.81`. Confirmed by hand and bit-identically against `repo_checks.json`'s `half_support_3p3s_height_m`. *Consequence:* trivial in substance (the doc's 1.6 m is still wrong by ~4.2×), but the report asserts its own precision.

**D2 — §2 argues about wing support from window-mean magnitudes and never prints the corpus's own per-jump `med_a_g`.** *Wrong:* omitted evidence, stronger and pointing elsewhere. *Evidence:* finding 1. The report cites the mechanism (R2) and correctly says it cannot establish the physics of *missed* jumps — but never shows that the detector's own flight diagnostic fails its predicted band on 10 of 11 **accepted** events. *Consequence:* the reader concludes the open question is "how much lift does a wing make" when the prior question is "are these events flights". This is CLAUDE.md's founding failure mode: the repo already contained the fact.

**D3 — §2/§3 never name the free-fall gate as the leading explanation for 11 vs 32.** *Wrong:* omitted selection physics. *Evidence:* a jump whose in-flight |a| stays above `freefall_enter_g = 0.35` is never detected (`jump_detector.h:165–176`); one above it for 0.5 s is closed `NO_LANDING` (`:198–207`). The eleven events are a *selected* low-support subpopulation. The report says this once, in `physics-notes.md §5.7`, never in the body where the count gap is discussed. *Consequence:* invites threshold retuning — the activity the report's own closing paragraph warns against.

**D4 — §3.1 gives percentage overestimates only, never the absolute crossover.** *Wrong:* the decision-relevant form is unstated. *Evidence:* finding 7; the target is absolute, and `L·g·T²/8 = 0.1524` inverts cleanly. *Consequence:* the owner cannot tell where the airtime formula stops being usable — **0.788 s at 20 % support**, with the corpus's two longest events already past it.

**D5 — §4's prior art omits the detection literature the repo had already found.** *Wrong:* incompleteness on the axis the report itself foregrounds as question 1 of 3. *Evidence:* `docs/prior-art-2026-09-14.md §2` already carried **Kranzinger et al. 2024** (PLOS ONE `10.1371/journal.pone.0307255` — 100 % detection for Big Air, 94 % for flights ≥ 500 ms, **44 % for < 500 ms**, via a smoothed near-zero-|a| *region* detector rather than a hard threshold), Sadi & Klukas 2011/12 (92 % / 8 % FP), the SAGE 2015 baro+IMU study (92 % / 93 %), and a halfpipe U-Net detector. None appears in the report; grep for "Kranzinger", "Sadi", "U-Net" and both DOIs returns nothing. *Consequence:* the report criticizes the 0.35 g gate without engaging the one body of work that speaks to what replaces it — and the 44 % figure bears directly on whether the short pops this device records are detectable by threshold-style methods at all.

**D6 — §9.1 proposes board-mark scaling and a new sync protocol without citing the repo's own, more specific rules.** *Wrong:* reinvention, plus an omitted quantified failure mode. *Evidence:* finding 16. `docs/data-pipeline.md` is cited nowhere in the report (grep: zero hits); "ruler" appears once, in an unrelated sense at line 375. *Consequence:* whoever labels the first clips is never told that horizon-as-zero inflates a 1.5 m jump by +53 % and survives every sanity check, nor that 4K/30 costs 8.2 cm where 1080p/120 costs 2.0 cm. One internal inconsistency the report could have resolved: `docs/accuracy-plan.md:43` still lists the mast as the video ruler, which `data-pipeline.md` explicitly rules against.

**D7 — §10's label schema renames fields rather than extending them, and the break is silent.** *Wrong:* impractical as literally specified. *Evidence:* finding 19. *Consequence:* implementing Phase A verbatim produces a plausible-looking empty score instead of an error — CLAUDE.md rule 3's exact failure mode. Fix by adding the new fields alongside the old, or by versioning the schema with a hard refusal on unknown versions.

**D8 — §5.1 leaves noise "not obviously ruled out" when the datasheet closes it.** *Wrong:* a positive result left on the table. *Evidence:* finding 10 — 1 mm at 4 s. *Consequence:* the report's illustrative 0.02 g example (disclaimed three times, so not a defect) is the only noise number a reader carries away; the real one would let the owner stop worrying about the sensor and start worrying about geometry.

**D9 — §5.2 and `physics-notes.md §3` omit gyro scale-factor error.** *Wrong:* omitted physics. *Evidence:* attitude error from sensitivity error is proportional to the *angle turned*, not elapsed time. `med_w_dps` reaches 375 dps (≈113° over jump 1's 0.30 s airborne window). At 2 % scale error over 180° turned: 3.60° → **9.24 cm** at f_h = 0.3 g, T = 2 s; at 3 %, 13.86 cm. Table 3 gives `G_SoDr = ±0.007 %/°C` but **no room-temperature gyro sensitivity tolerance**. *Consequence:* a six-position accelerometer calibration does not calibrate this; it needs a measured-angle rotation, which nothing in the plan includes.

**D10 — §7.1 says "use the part's FIFO/data-ready/timestamp facilities" with no numbers.** *Wrong:* two hard constraints omitted. *Evidence:* the timestamp is **24-bit** with `TIMER_HR` selecting 6.4 ms or 25 µs per LSB (§9.59–9.61 p. 82; §9.79 p. 90) — at the useful 25 µs setting it **wraps every 419.43 s (7.0 min)**, so any capture longer than 7 minutes must handle the wrap. The FIFO is **4 kbyte** (§5.5 p. 36) = 341 six-axis samples = **1.64 s at 208 Hz** — enough for a one-jump pre-trigger, not a substitute for continuous logging. Caveat per CLAUDE.md rule 6: Rev 3 is internally inconsistent here (§5.5.2 says "up to 4096 samples of 16 bits each", i.e. 8 kbyte); the conservative figure is used and silicon should settle it via FIFO_STATUS1/2. *Consequence:* an implementer following §7.1 literally can build a capture that silently wraps mid-session.

**D11 — §3.4's assumption table omits the repo's own endpoint model, and the report never notes that live rider-facing guidance depends on it.** *Wrong:* omitted internal inconsistency. *Evidence:* finding 17. *Consequence:* `session-card.md` continues to promise the rider ±6.5–14 cm on the strength of a simulation whose central assumption the report's own section 3 undermines.

**D12 — §8 and §12 item 8 call for pre-video false-positive work without citing F-28 or F-33.** *Wrong:* omitted evidence, the stronger of the two MAJOR. *Evidence:* finding 15. *Consequence:* the "useful work before video" section reads as greenfield when the repo has already tried the obvious |a|-only discriminator and measured it failing 10/10 on the only real riding session it was ever applied to.

**D13 — §6.6 treats a barometer as an option to schedule.** *Wrong:* impractical against the enclosure actually chosen, and the scale argument understates itself. *Evidence:* the 2026-09-15 decision is a sealed Hammond 1551WHGY tub with a gasketed lid and one capped USB opening — **no pressure port in any table of the handoff**. A sealed volume measures its own thermal expansion, not static pressure. Scales: 15.24 cm is **1.83 Pa**, while 10 m/s of airflow is **61.25 Pa — equivalent to 5.10 m of apparent altitude, 33× the whole budget**. *Timing, stated fairly:* the enclosure commits land at 16:59–17:04 and the report's inspected revision is 13:01, so this is a forward gap, not an omission. *Consequence:* §6.6 should read "an option that requires reopening the 09-15 enclosure decision".

**D14 — §5.2's 10 cm endpoint allocation is explicitly illustrative; this review supplies a foil-specific magnitude the report does not.** *This is an addition, not a correction.* The report already states a foilboard is above water before a jump (§6.1), names endpoint uncertainty as why long flights are demanding (§5.3), instructs an explicit endpoint sweep (§6.3), and works a 0.20 m example in `physics-notes.md §3`. *What is missing:* a number. On the assumptions in finding 11, `dD` of 0.3–0.45 m gives **15.0–22.5 cm from this term alone** — the whole budget before any sensor error. *An earlier draft of this review called the report's figure "wrong" and "3× undersized"; that rested on mast and ride-height figures this review itself lists as unmeasured, and it is withdrawn* (CLAUDE.md rule 2). *Consequence:* the report's budget reads as though bias leads; for a foilboard the endpoint leads at every duration.

---

## 5. Agreements verified, and remaining uncertainties

**Verified independently.**

- All twelve repository citations R1–R12 are accurate at their anchors; two adjacent-line drifts only (`Model.mc:53` not `:52`; `wing_model.py:84` is the field, `:73` the class).
- Every number in the report's sections 3, 5, 6 and `physics-notes.md` reproduces — two independent re-derivations found **no arithmetic error** in its computational core.
- The storage arithmetic (`2,097,152 − 4,096 − 65,536 = 2,027,520` bytes → 27.1 / 13.5 / 6.8 min at 104 / 208 / 416 Hz) reproduces exactly from `jh_store.cpp:107-108,746`.
- The September 14 session facts — 648,808 trace rows, `src=5c80a436`, 200/50 Hz, 11 events, best 1.106 m, the Surfr transcription — all reproduce from the on-disk bundle; so does the morning bundle's multi-boot structure (one reset, ~12.6 s final segment), and `data/refit.md:39` does score it as a count session.
- Every S4 datasheet value is verbatim in Table 3 of DocID030071 Rev 3, including both footnotes; the report correctly treats them as characterization values, not bounds.
- S1 (WOO pp. 12, 19–20, 36), S2 (Marčiš: 0.51 / 0.70 / 0.95 m RMS, 0.03–0.09 m a priori, ~0.2 m zero-level ambiguity, 20 jumps, 4K/30 capture), S5 (Fusion's default-disabled rejection and startup fallback), S6 (VQF 2.9°), S7 (ρ = 0.87, bias 2.5 cm) and S8 (Surfr's 100 Hz product requirement) all check out at the primary source.
- WOO's "exponential" integration-error framing is genuinely quadratic: its own example (20 cm @ 10 s, 20 m @ 100 s, 2 km @ 1000 s) fits `0.002·t²` at all three points.
- The report's most important observability statement is correct and worth repeating: **endpoint closure is not evidence of accuracy** — a constant bias `b` is exactly absorbed by `Δv₀ = −bT/2` while the interior moves by `bT²/8`.
- No independently validated, wing-specific ±15 cm result using this sensor exists; an independent search confirmed the negative.

**Uncertainties that remain.**

1. **Whether the eleven recorded events are jumps** — undecidable from |a|-only data.
2. **Why jump 7 records `n_air = 74` against a 63-sample window** — needs a device-side repro.
3. **The boot-time `g_baseline` for any recorded session** — absent from the bundle, and every normalized trace value depends on it. One manifest line closes this permanently.
4. **The LFCLK source and its error.** The timebase is RTC1-derived; whether LFCLK runs from the crystal or the internal RC is established nowhere, and no Nordic specification was fetched by either review. `dH ≈ 2ε·H`, so this is worth anywhere from 0.1 cm to several cm on a 2 m jump. **Any specific RC tolerance figure would be unsourced; treat it as unknown, not as a number.**
5. **The part's actual ODR and the post-stall duplicate rate** — 1.54 % of margin, no datasheet tolerance, never measured.
6. **Whether the landing spike clips per-axis** — the peak is 26 % above the rail in magnitude, but the trace carries no axes and is 4:1 decimated.
7. **Mast length, ride height, and real takeoff/landing pitch for Nick's setup** — nowhere in the repo; the dominant budget line rests on generic figures.
8. **The WOO cradle's rigidity and the mount transfer function** — cradle not received; O-ring stiffness and case mass unmeasured.
9. **The takeoff-edge gate delay on water** — the −19 ms ±9 bench calibration was measured on *drops*, which have no pop phase.
10. **Gyro sensitivity error on this unit** — no room-temperature tolerance specified, and the unit has never been rotated through a measured angle.
11. **ST AN5130 Table 11** (the source for the driver's ~67 Hz gyro LPF1 note) — attributed, not verified by either review.
12. **Whether the test suite passes at HEAD** — not run; it exceeded the time budget.

---

## 6. Board rise vs water clearance, and what each video view can validate

| Quantity | Definition | What can observe it |
|---|---|---|
| **Board rise** | `max(z_B(t)) − z_B(t_takeoff)` for a fixed board point B — rise relative to the board's own position at takeoff. Depends on an endpoint assumption `Δz`. | IMU, given a trajectory model and a chosen or measured `Δz`. |
| **Water clearance** | Height of a board point above the local instantaneous water surface beneath it. | Video only, with zero set **in the takeoff frame, not the horizon** (`data-pipeline.md:206` — horizon-as-zero adds +53 % on a 1.5 m jump). Not observable by IMU. |
| **Foil clearance** | Height of the foil's lowest point above the water — what decides whether the foil is "out". Differs from board clearance by board pitch. | Video only, and only when the foil is not hidden by spray. No repo data on foil visibility rate. |
| **What Surfr reports** | Per-jump height and airtime; no published formula (its compatibility page states a 100 Hz requirement, not a method). | Not independently verifiable. Per finding 8 it is unusable as an airtime reference, and its **count is unusable as a tuning target too** — a count from a different event definition drags the detector toward a different quantity. |

| View | Available | Can validate | Cannot validate | Uncertainty |
|---|---|---|---|---|
| Mouth GoPro | now | rider actions; rough contact timing when the board is in frame | board rise, any clearance | head/neck motion decouples camera from board entirely |
| Wing GoPro at feet | now | takeoff/landing **contact intervals**, detector recall against them, a bounded in-frame rise if a rider-height scale is added | water clearance, foil clearance | wing flexes and moves independently; camera-relative, not world-relative. One frame at 60 fps is 16.7 ms but real uncertainty spans several frames; blur and occlusion usually dominate |
| Kayak, side-on | weeks out | true water clearance **if** a stable water-plane reference is fixed at the jump's location and moment | anything, if a floating reference moving with the camera is used | kayak heave/pitch/roll uncharacterized by any document; changing range. Marčiš's "with small jumps and estimation differences up to 0.2 m it is not possible to reliably determine which of the systems is more accurate" applies most directly here |
| Shore/dock tripod | not planned | geometrically the easiest — fixed camera, fixed water plane, no camera-motion error | nothing usable beyond ~250–300 m at 1080p/120 | Nick's usual box starts **~325 m** off the Manteo waterfront (`session-card.md:104-107`) — outside reach without a zoom lens or a briefed inside line |

**What can be learned before reliable external height exists.** (1) **Whether the recorded events are jumps** — the highest-value question, needing only the wing GoPro and the documented three-flat-drops marker; cheaper than everything else on the plan. (2) **Contact-interval timing precision**, testable now against the existing trace and the `missed ≈ spurious` sync diagnostic (`data-pipeline.md:278–284`). (3) **Detector false positives and recall** on ordinary riding and falls, with F-33 as the standing warning that an |a|-only discriminator does not transfer to a worn puck. (4) **The airtime distribution**, available now from the 09-14 trace with no video. (5) **Bench reconstruction over known heights**, needing no water at all. Items 1 and 3 validate the *detector* independent of the height model; 4 and 5 bound how much video-height work is worth doing before an estimator is chosen.

---

## 7. Prior art: independently demonstrated vs vendor-described

| Source | Independent? | Truth method | Accuracy reported | Transfers to wingfoil / this hardware? |
|---|---|---|---|---|
| WOO, *The WOO Way* v1.3 (2024), pp. 12, 19–20, 36 | **Vendor** | none — a method description | none given | **Method only.** Earth-frame transform, double integration, sea-level-return correction. Twintip scope (p. 12); author says foil/wing principles "also apply" but tests nothing. Its own "exponential" error framing is quadratic. |
| Marčiš et al. 2021, *Sensors* 21(24):8353 | **Independent** (4 synced Nikon D7500 + surveyed control) | videogrammetry, a priori 0.03–0.09 m | Surfr **0.51 m** RMS, WOO3 0.70 m, WOO2 0.95 m, 20 jumps of 3.07–7.30 m | **No.** Kiteboarding, rigid commercial mounts, WOO2/WOO3-era firmware. A floor on what integration-based commercial devices achieve in a simpler sport. Silent on wing/kite lift during flight. |
| Sensors 24(24):7877 (2024) | **Independent** (8-camera mocap + dual force plates) | gold standard | double integration best: ρ = 0.87, bias **2.5 cm**, ICC 0.88, 18 subjects, IMU at 60 Hz | **No.** Standing CMJ — gravity-only flight, rigid pelvis mount. Proves integration *can* be centimetre-accurate where the ballistic assumption holds. |
| Kranzinger et al. 2024, PLOS ONE | **Independent** (25 fps GoPro, 150 ms tolerance) | video | detection only: **100 %** Big Air, **94 %** ≥ 500 ms, **44 % < 500 ms** | **Concept transfers.** A smoothed near-zero-|a| *region* detector rather than a hard threshold — directly relevant to replacing the 0.35 g gate. The 44 % figure is a warning about the short pops this device records. Rigid boot-mounted housing. |
| Sadi & Klukas 2011/12, *Sports Technology* 4(1-2) | **Independent** | not verified (paywalled) | 92 % detection, 8 % FP | Detection concept only. |
| Lee et al. 2015, SAGE J. Sports Eng. Tech. | **Independent** | not verified (403) | 92 % / 93 % sensitivity/specificity, baro+IMU vs IMU alone | Detection/classification only; the closest precedent for a barometric *aid*, and not a height result. |
| Real-time ski-jump tracking, PMC8659670 | **Independent** | geodetic + FIS video | distance MAE 0.46 m, **3D position MAE 0.12 m** | **No, instructively.** Sub-20 cm required fixed UWB antennas along the hill plus a binding-mounted tag — the price of the accuracy being asked for, which the water setting cannot pay. |
| x-io Fusion @ `3a386ee` | Independent OSS | code inspection | n/a | **Candidate, not turnkey.** Verified: `fusionAhrsDefaultSettings` sets `gyroscopeRange`, `accelerationRejection`, `magneticRejection`, `rejectionTimeout` all to `0` = disabled, and startup/recovery force acceptance of raw accelerometer as the gravity reference. Must be run configured. |
| VQF (Laidig & Seel) | Independent | mocap-referenced public datasets | 2.9° average orientation RMSE | **Partial.** An attitude filter with an offline forward/backward form; says nothing about vertical displacement. Absolute yaw is unnecessary to project onto earth vertical, so the missing magnetometer is not disqualifying. |
| Surfr compatibility page | **Vendor** | none | "100 measurements per second (100 Hz)"; rejects a 50 Hz watch | Product policy, not a sampling theorem. Neither proves 50 Hz insufficient nor 100 Hz sufficient. |

**The common thread:** every independent result that reached sub-metre accuracy used a **rigid, close-coupled mount** — board-mounted commercial units, a rigid boot housing, a binding-mounted tag. The mount selected on 2026-09-15 is a compliant O-ring cradle (`BUILD.md:37`: "a self-adhesive (3M) cradle, sensor held by two rubber O-rings", fit against the Hammond case unmeasured). **No source, old or new, quantifies how much vertical-acceleration error an O-ring interface adds relative to a rigid one.**

---

## 8. Observability of the proposed inertial reconstruction

The architecture under consideration: rotate calibrated specific force into earth coordinates, subtract g, integrate twice, close the path on an assumed endpoint displacement —

```text
a_z(t) = [R(t) f_body(t)]_z − g ;  B(t) = ∫₀ᵗ (t−s) a_z(s) ds
z(t) − z(0) = B(t) + (t/T)·[Δz − B(T)]
```

**Attitude.** In free fall the accelerometer supplies **zero** attitude information (f = 0 for every orientation), so attitude must be gyro-propagated from an initial tilt θ₀ observed *before* takeoff, from a validated window — not from arbitrary dynamic support. The vertical leak is `f_h·(θ₀ + βt)`, whose constrained path error is `|f_h θ₀|T²/8 + |f_h β|T³/(9√3)`.

**Gyro bias.** Datasheet `G_TyOff = ±3 dps`, `G_OffDr = ±0.05 dps/°C`, `Rn = 5 mdps/√Hz`. Noise is never the limit — a bias estimate from 60 s of stationary data has SD 0.65 mdps. Height cost at f_h = 0.3 g (DERIVED):

| Bias case | β (dps) | 1 s | 2 s | 3 s | 4 s |
|---|---:|---:|---:|---:|---:|
| uncalibrated (datasheet TyOff) | 3.0 | 0.99 | 7.9 | **26.7** | **63.2** cm |
| six-position bench, then a 20 °C swing | 1.0 | 0.33 | 2.6 | 8.9 | **21.1** cm |
| calibrated + temperature-compensated | 0.10 | 0.03 | 0.26 | 0.89 | 2.1 cm |
| re-zeroed immediately before the jump | 0.02 | 0.007 | 0.05 | 0.18 | 0.42 cm |

Gyro bias is therefore **not** a blocker if re-zeroed close to each jump and temperature-compensated. Two live risks: `gyro_bias.h`'s EMA (α = 0.001, ~4.8 s time constant) updates throughout RIDING and can learn a **sustained** one-direction carve as bias; and the ±2000 dps rail — `med_w` already reaches 375 dps, and a railed sample corrupts propagation irrecoverably for that jump, so it must be flagged, not clamped.

**Accelerometer bias.** A six-position fit is easy: 1–2° pose error contributes 0.15–0.61 mg and 60 s of averaging 0.012 mg — **sub-milligal by hand**. Temperature is the whole story: `LA_OffDr = ±0.5 mg/°C` gives 5 / 10 / 15 mg at ΔT of 10 / 20 / 30 °C, and a sealed polycarbonate case in Carolina sun repeatedly splashed with 22 °C water is a 20–30 °C swing. Realistic in-use residual: **10–15 mg uncompensated, 2–3 mg with a per-unit temperature fit**; 10 mg costs 1.2 cm @ 1 s, 4.9 @ 2 s, **11.0 @ 3 s**, 19.6 @ 4 s. Datasheet footnote 3 says the temperature coefficients are "based on characterization data in a limited number of samples. Not measured during final test for production" — **±0.5 mg/°C is not a per-part guarantee**, so a per-unit fit is required, not a datasheet substitution. The on-die temperature sensor (52 Hz, ±15 °C offset spec) tracks change well but needs its own one-point tare.

**Dynamic support.** Finding 7 fixes when the airtime formula dies. For a reconstruction, support is not modelled — it is measured, provided attitude is right. That is the architectural argument for integrating, and it stands.

**Acquisition timing.** Finding 14. The *timestamps* are honest (`main.cpp:1762` uses the actual tick-quantized `micros64`), so dropped samples are a decimation, not a time error; the integration does not drift from them. The real costs are the ±0.98 ms tick quantization at the edges (at a landing slope of ~250 g/s, a 0.25 g amplitude error *at the edge* — irrelevant to the interior, decisive for boundary timing), the unforced accel/gyro pairing (worst case one ODR period = 4.81 ms, up to **1.80°** of mismatch at 375 dps), and the post-stall duplicate regime.

**Initial velocity.** Not identifiable jointly with acceleration bias from a single endpoint constraint: for any constant added bias a different `v₀` satisfies the same endpoint and yields a different interior height. **Endpoint closure is not evidence of accuracy.**

**Endpoint assumption.** Finding 11. This term shrinks with neither duration nor sensor quality. **Board rotation.** Finding 12 — real motion of the sensor point, not an estimator error; it becomes an error only when a board-point endpoint is fed into sensor-point integration without `[R(T)r − R(0)r]_z`. **Sensor placement.** Today the firmware uses only |a|, which is orientation-free, so a 90° roll of the case in the cradle costs nothing. The moment a reconstruction exists it costs initial tilt: **an unmeasured 5° cradle pitch at f_h = 0.3 g over 2 s is 12.8 cm on its own.**

### Per-duration error budget — ANALYSIS, not measurement

Scenario inputs are assumptions. Mount lever r = 0.6 m. Mount compliance, aliasing and clipping are **excluded** — none measured on this unit in this enclosure. An apex is assumed per class so the timing term stays honest at long durations. All values **cm**; target 15.24.

| | accel bias | tilt θ₀ | gyro β | f_h | endpoint dD | timing dT | pitch change |
|---|---:|---:|---:|---:|---:|---:|---:|
| best | 2 mg | 0.2° | 0.05 dps | 0.2 g | 0.10 m | 5 ms | 1.5° |
| realistic | 10 mg | 0.5° | 0.30 dps | 0.3 g | 0.30 m | 20 ms | 5° |
| worst | 40 mg | 2.0° | 3.00 dps | 0.5 g | 0.60 m | 60 ms | 10° |

| T | apex | scenario | bias | tilt | endpoint | timing | pitch | **same-sign sum** | RSS | verdict |
|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---|
| 0.5 s | 0.30 m | best | 0.06 | 0.02 | 5.00 | 0.60 | 1.57 | **7.27** | 5.28 | **PASS** |
| | | realistic | 0.31 | 0.09 | 15.00 | 2.40 | 5.23 | 23.04 | 16.07 | FAIL |
| | | worst | 1.23 | 0.74 | 30.00 | 7.20 | 10.42 | 50.79 | 32.62 | FAIL |
| 1.0 s | 1.00 m | best | 0.25 | 0.10 | 5.00 | 1.00 | 1.57 | **7.96** | 5.34 | **PASS** |
| | | realistic | 1.23 | 0.42 | 15.00 | 4.00 | 5.23 | 25.92 | 16.43 | FAIL |
| | | worst | 4.90 | 3.79 | 30.00 | 12.00 | 10.42 | 65.11 | 34.74 | FAIL |
| 1.5 s | 1.50 m | best | 0.55 | 0.23 | 5.00 | 1.00 | 1.57 | **8.43** | 5.37 | **PASS** |
| | | realistic | 2.76 | 1.06 | 15.00 | 4.00 | 5.23 | 28.12 | 16.65 | FAIL |
| 2.0 s | 2.00 m | best | 0.98 | 0.43 | 5.00 | 1.00 | 1.57 | **9.08** | 5.44 | **PASS** |
| | | realistic | 4.90 | 2.07 | 15.00 | 4.00 | 5.23 | 31.31 | 17.23 | FAIL |
| | | worst | 19.61 | 21.73 | 30.00 | 12.00 | 10.42 | 101.77 | 45.54 | FAIL |
| 3.0 s | 2.00 m | best | 2.21 | 1.07 | 5.00 | 0.67 | 1.57 | **10.61** | 5.82 | **PASS** |
| | | realistic | 11.03 | 5.56 | 15.00 | 2.67 | 5.23 | 39.58 | 20.30 | FAIL |
| 4.0 s | 2.80 m | best | 3.92 | 2.07 | 5.00 | 0.70 | 1.57 | **13.41** | 6.90 | **PASS (marginal)** |
| | | realistic | 19.61 | 11.46 | 15.00 | 2.80 | 5.23 | 54.24 | 27.86 | FAIL |
| | | worst | 78.45 | 139.64 | 30.00 | 8.40 | 10.42 | 278.11 | 163.88 | FAIL |

**What the table says.** (1) The realistic column fails at every duration, and it fails on one line: **the endpoint**, 15.0 cm of a 15.24 cm budget by itself. Remove it and realistic at 1 s drops to 10.9 cm (pass); at 3 s it is still 24.6 cm (fail, on bias and tilt). (2) The best column passes everywhere to 4 s — but every input in it is an achievable *bench* number and **none has been demonstrated on water**. (3) **Duration is not the main axis; observability is.** Bias and tilt do grow as T² and T³, but a calibrated unit keeps them under 12 cm out to 4 s; the two terms that do not shrink with better sensors — endpoint and pitch geometry — are not inertial problems at all. (4) **Do not RSS these.** The RSS column bounds the optimistic end only; endpoint, pitch geometry and bias are systematic within a jump and correlated across jumps in a session.

---

## 9. Interaction with the 2026-09-15 enclosure decisions

Source: `/Users/joshcrow/Jump-height-race-openai/hardware/enclosure/HANDOFF-2026-09-15.md` (branch `enclosure-race-openai`, a separate worktree — **not on `main`**; the on-`main` lookup entries are `BUILD.md:37-38` and `docs/STATUS.md:28`). The handoff states: "**No physical fit, charging, USB transfer, firmware update, leak, RF, retention or flotation test was performed.**"

1. **Hammond 1551WHGY, 60 × 35 × 22 mm, polycarbonate with a replaceable silicone gasket.** Thermally this is the body that sets the accelerometer's temperature swing — the dominant calibration term (section 8). **Log die temperature with every research capture**: 2 bytes per block, and it cannot be recovered retrospectively.
2. **Capped CA-USBW1 USB-C extension on the lid.** Neutral for measurement, except that any research capture must come off over this path or BLE, and **no BLE throughput or sync-duration figure exists anywhere in this repo** — so the time cost of pulling a multi-hundred-KB trace is unknown.
3. **WOO adhesive cradle, sensor retained by two rubber O-rings.** A **compliant mount by construction**, against a reconstruction that assumes a fixed rigid offset. Order-of-magnitude only (assumed O-ring stiffness 1e4–1e5 N/m, assumed 55–75 g assembly): resonance at **58–215 Hz** — on or just above the 100 Hz poll Nyquist and the 104 Hz LPF1 corner. Peak displacement at resonance is negligible (0.02–0.21 mm), so this never corrupts position directly; it corrupts the **accelerometer signal**, and a rectified or aliased ring folded back by an unfiltered decimation looks exactly like a small DC bias over a 1–4 s window — the budget line that already hurts. Mitigation: a digital low-pass at ~20–30 Hz **before** decimation, plus a bench transfer-function measurement with the real case and rings when they arrive.
4. **No pressure port, by decision.** Per D13 a barometer is unavailable in this enclosure without reopening the sealing decision, and the dynamic-pressure scale (61.25 Pa at 10 m/s = 5.10 m of apparent altitude, vs 1.83 Pa for the whole budget) makes port design the hard part, not sensor resolution.
5. **Mount orientation undecided** — the handoff's next-steps item 4 says to try the closed case "flat and on its long edge" and to "**Recheck sensor-axis assumptions if mounting orientation changes**" (`:86`). Today that costs nothing (|a| only). The moment a reconstruction exists it is an initial-tilt error worth 12.8 cm at 5°. **Whichever orientation is chosen, measure and store the transform, and give the stored calibration an id that travels with every capture.**
6. **Added mass** (~70 g) on a ~100 kg rider+board system is dynamically negligible; it matters for tether loads and cradle resonance, not the physics.

---

## 10. Prioritized plan for the main coding agent

**Do not touch, until the stated gate passes:**

- **The production detector** (`jump_detector.h`, its gates, the `g·T²/8` height path) — frozen until gate **G3** (item 6) produces a measured error distribution on independently labelled events. No threshold retuning meanwhile.
- **The deployed calibration** (`airtime_offset_s = 0.0192`, `height_scale = 1.000`, device NVS) — frozen until **G3**. It was fit on bench drops and is the only calibration ever validated at all.
- **Firmware on the rider's puck.** The OG is at Nick's. No flash for research purposes until item 3 passes on the bench Puck and gate **G2** is green. The pending `c5eea285` (100 Hz logging) flash is a separate, already-decided change — do not bundle research firmware into it.
- **`docs/session-card.md`'s rider-facing ±6.5–14 cm numbers** — do not delete them; mark them conditional on the ballistic assumption (item 2).

Ordered by value per effort.

1. **Print the corpus's own flight diagnostic into the scorecard.** *Deliverable:* `sim/score.py` emits per event `med_a_g`, `med_w_dps`, `n_air`, predicted `n_air`, and max |a| strictly inside the reported window, plus a session line counting events outside the firmware's 0–0.07 g band. *Acceptance:* on `20260914-210637-E2C4` the card reports **11 events, 10 outside the band, 9 above 0.35 g, 10/11 `n_air` reconciling, jump 2 max-inside 2.24 g, jump 3 max-inside 2.29 g** — matching `corpus_recheck.json` exactly. Hours of work, and it is the finding that changes the project's direction.
2. **Mark the superseded claims, in one commit, with their lookup entries.** *Deliverable:* DECISION #28, `docs/algorithm.md:80-88`, `README.md:131-134` and `docs/prior-art-2026-09-14.md:55` marked superseded with their simulated assumptions preserved; 1.6 m corrected to **6.67 m**; `accuracy-plan.md:43`'s mast-as-ruler reconciled with `data-pipeline.md:197-204`; **F-33 added to `docs/STATUS.md`'s open-findings table**; `session-card.md`'s accuracy numbers labelled conditional. *Acceptance:* `grep -rn "near-ballistic\|4.2 cm\|4.6 cm" README.md DECISIONS.md docs/algorithm.md` returns zero unqualified hits, and `grep -n "F-33" docs/STATUS.md` returns ≥ 1.
3. **One research firmware batch: six-axis raw capture with acquisition provenance.** *Deliverable:* an opt-in mode writing `schema_version, boot_id, recording_id, firmware_hash, sample_sequence, sensor_timestamp, ax..gz raw int16, read_status, fifo_overrun, clipped_axes, die_temperature, calibration_id, register_config, mount_id`; FIFO reads with `TIMER_EN` and 25 µs timestamps; explicit 419.43 s wrap handling; a ~20–30 Hz digital low-pass **before** any decimation. One flash, per CLAUDE.md rule 4. *Acceptance, all on the bench Puck:* (a) ≥ 10 continuous minutes with **zero** unexplained sample-sequence gaps; (b) distinct-ODR-sample count per second measured and reported — expect **200.0 ± 0.5 reads/s and ≤ 1 duplicate/s** between stalls, duplicates **counted, not inferred**; (c) a deliberate 60 s BLE+flash stress produces a logged gap count matching the sequence discontinuities to the sample; (d) a controlled >16 g impact sets `clipped_axes` on ≥ 1 sample; (e) timestamp wrap exercised once and reconstructed correctly across it; (f) elapsed sensor time over a 10-minute capture agrees with an independent wall clock to **< 0.1 %** — which also measures the LFCLK term (uncertainty 4) for the first time.
4. **Bench calibration and known-height reconstruction.** *Deliverable:* per-axis offset/scale from a six-pose fit with held-out oblique poses; a per-unit temperature fit over a ≥ 20 °C sweep in the finished enclosure; a measured-angle rotation for gyro **scale**; then vertical-guide translations between measured stops at several durations, with deliberate pauses and support changes. *Acceptance:* held-out-pose residual **< 3 mg** at calibration temperature and **< 5 mg** after a 20 °C swing; gyro scale to **< 1 %** over a ≥ 180° measured rotation; reconstructed apex within **±5 cm** of a ruler over 0.5–2.0 s of guided motion at **three** orientations, with each run's endpoint/tilt/bias sensitivity reported. ***Gate G2*** — no research firmware on the rider's puck until 3 and 4 pass.
5. **Evaluation contract: extend, do not rename.** *Deliverable:* `labels.csv` gains `takeoff_lo/hi`, `landing_lo/hi`, `height_lo/hi`, `coverage_start/end/status`, `boot_id`, `mount_id`, `height_method`, `sync_uncertainty`, `annotator` **alongside** the existing `t_start_s`/`t_end_s`/`height_m`/`height_src`; `load_labels` refuses an unknown `schema_version` loudly; detections outside reviewed coverage are **unscored**, not spurious; metrics add median and p90 absolute error, fraction within 15.24 cm, precision/recall, false positives per reviewed non-jump hour, and coverage yield. *Acceptance:* a test writes a new-schema `labels.csv` and the evaluator scores it identically to the old schema on the same data, **and** fails loudly (non-zero exit, named reason) on a file with a renamed-but-unversioned column — the silent-empty path of finding 19 proven impossible by a test that fails before the fix and passes after.
6. **POV pilot with Nick's wing GoPro.** *Deliverable:* one short framing clip, then 10–20 consecutive attempts plus ordinary riding and falls; the documented **three flat board drops ~2 s apart** marker at both ends (`session-card.md:79-82`, not a finger tap); 1080p/120 per `data-pipeline.md:221-225`; contact intervals labelled **before** anyone looks at detector or Surfr output. *Acceptance:* ≥ 15 events with takeoff and landing labelled as intervals whose width is stated, ≥ 80 % of reviewed time classified covered or explicitly occluded, and a reported detector recall and false-positive rate **scored only inside reviewed coverage**. ***Gate G3.***
7. **Offline estimator comparison on exactly those windows.** *Deliverable:* on the same labelled intervals — ballistic-from-video-airtime, ballistic-from-detected-airtime, calibrated integration with bench orientation, gyro-propagated attitude, a *configured* Fusion baseline, an offline VQF baseline, and the best reconstruction with the endpoint swept over an explicit range. *Acceptance:* a table of signed bias, median and p90 absolute error per estimator per duration bin, plus the measured `dH/dD` slope for each. No estimator adopted on a bench result alone.
8. **External height reference.** *Deliverable:* a kayak or shore pass with a documented observation area, a reference of known separation measured at the jump's depth, and repeated reference views during recording. *Acceptance:* demonstrated height-reference uncertainty **≤ 5 cm** on the measured subset, validated against known separations at the near and far ends; if the geometry cannot support that, labels become interval-valued and conclusions are restricted accordingly. ***Gate G4*** — only after G4 may any accuracy claim be made, and only with coverage and false-positive rate reported beside it.
9. **Device port, last.** *Deliverable:* the selected estimator on the puck with versioned outputs, confidence and explicit rejection reasons; `height_m` gains a definition version rather than being silently repurposed. *Acceptance:* same-input parity with the offline implementation to **< 1 cm** on the frozen holdout, measured runtime and flash cost, and a held-out field comparison before any accuracy claim reaches the watch.

---

## 11. The three highest-value next experiments

### E-A. Are the recorded events jumps?

- **Purpose.** Settle finding 1. Every downstream decision — estimator choice, detector redesign, calibration target — is conditional on this, and nothing else in the plan is worth doing first.
- **Setup.** Nick rides one ordinary short block with the wing GoPro facing his feet at 1080p/120, with the **three flat board drops ~2 s apart** marker at both ends. The puck runs current firmware (a magnitude trace suffices for this question). Label takeoff/landing contact intervals from video **before** looking at `jumps.csv`; then align by the marker and compare.
- **Number produced.** For each device event, whether a reviewed video interval shows a real loss of water contact, with its `med_a_g` beside it — i.e. the fraction of device events that are flights and the fraction of video-visible flights the device found, both scored only inside reviewed coverage.
- **What changes the verdict.** If most device events are real flights with `med_a_g` above 0.35 g, wing support during genuine flight is large and the airtime formula is dead for everything but the shortest pops — go straight to reconstruction. If most are multi-contact or pocket-motion artefacts, the **detector**, not the height model, is the first thing to rebuild, and the recorded heights are not jump heights at all. Either result also says whether Surfr's 32 is the superset the gate selects out of.

### E-B. Bench reconstruction over a known, non-ballistic height

- **Purpose.** Decide whether double integration on *this* sensor can reach the target at all, before any water data exists — separating that from every water-side unknown.
- **Setup.** The assembled unit in its finished enclosure, in the WOO cradle, moved along a vertical guide between measured stops: 0.5, 1.0, 1.5 and 2.0 m of travel, each at slow and fast speeds, each with a deliberate pause near the top so long duration cannot stand in for height; repeated at three mount orientations and two temperatures ≥ 20 °C apart. Six-axis capture from item 3, calibration from item 4, endpoints measured with a ruler. Reconstruct with the section 8 formula, sweeping the assumed endpoint over ±0.3 m.
- **Number produced.** Signed bias and p90 absolute error per travel distance, duration, orientation and temperature — plus the measured slope `dH/dD` against the endpoint sweep, the term the water case cannot avoid.
- **What changes the verdict.** If p90 error is **≤ 5 cm** at every duration with the endpoint known, sensor and estimator are adequate and all remaining risk is water-side geometry — pursue video and endpoint measurement. If p90 exceeds **15 cm** on a *known* endpoint on a bench, the reconstruction cannot meet the target on water either, and the honest move is to narrow the product claim to short jumps or add an independent vertical aid (which, per section 9, reopens the enclosure decision).

### E-C. Count the samples the pipeline actually reads

- **Purpose.** Close three open uncertainties at once — the real ODR, the post-stall duplicate regime, and the LFCLK error — each of which silently corrupts an integrator, none of which has ever been measured. Cheapest experiment on the list.
- **Setup.** Bench Puck, research firmware from item 3, FIFO enabled with `TIMER_EN` and 25 µs timestamps. Ten continuous minutes: 3 quiet, 3 under deliberate BLE + flash stress, 3 of hand motion, 1 across a forced timestamp wrap. Count distinct ODR samples per second, duplicate reads per second, sequence discontinuities, and FIFO `OVER_RUN` events. Separately compare RTC1 tick count against an independent wall clock across the whole run.
- **Number produced.** Measured ODR in Hz (expected near 208; ST specifies no tolerance); duplicate reads per second in steady state and under stress; the LFCLK fractional error ε, which converts to height error via `dH ≈ 2ε·H`.
- **What changes the verdict.** If duplicates are near zero and ε is within a few hundred ppm, acquisition is solved and leaves the budget entirely. If duplicates appear under stress, or ε is percent-scale, an unflagged acquisition defect is eating a large share of the budget and **must be fixed before any reconstruction result means anything** — and the same measurement says whether the existing corpus's replay analyses were ever reading what they thought they were.

---

## 12. Evidence ledger

**Repository files read** (paths under `/Users/joshcrow/Jump-height/` unless noted).

- Docs: `CLAUDE.md`; `docs/STATUS.md` (`:28`, `:30`, `:71`, the 100 Hz section, open findings); `docs/accuracy-plan.md:43`; `docs/accuracy-review-2026-09-15.md` (the object, in full); `docs/garmin-corpus-2026-09-15.md §1`; `docs/data-pipeline.md:197–213, 221–225, 278–284`; `docs/session-card.md:79–82, 103–109, 120–139`; `docs/audit-2026-08-22.md:209–256, 455–480` (F-28, F-33); `docs/prior-art-2026-09-14.md:55`; `docs/algorithm.md:80–88`; `DECISIONS.md:60`; `README.md:131–134`; `BUILD.md:34, 37, 38`.
- Firmware: `jump_detector.h:114–129, 160–208`; `main.cpp:89–90, 202, 348–377, 711–749, 1553, 1733–1764, 1766–1906, 1922–2014, 2029–2037`; `gyro_bias.h:39–89`; `lever_arm.h:8–15`; `trace_codec.h:33–40`; `platform/nrf52/lsm6ds3_min.h:24–40, 80–88, 139–149, 166–188`; `platform/nrf52/jh_store.cpp:107–108, 746`; `platform/nrf52/twim_bounded.h:52–57`; `jh_clock.cpp`; `config/params.json`.
- Sim/tools: `sim/evaluate.py:64–121, 217–283, 296–354, 402–412`; `sim/score.py:747`; `sim/wing_model.py:80–84, 122, 130–132, 240–242`; `sim/sensor_model.py:44, 110–135`; `sim/selfdiag.py:5–6, 35`; `sim/experiments/e2_montecarlo.py:39–41`; `e11_gate_miss_cost.py:56–59`; `e14_wavy_surface.py` + `out/e14_summary.txt`; `e15_venues.py`; `tools/refit.py:183–211, 380–387`; `tools/label.py:135–136`; `garmin/jumpfield/source/Model.mc:53`.
- Private session data (read-only, not reproduced here): `data/sessions/20260914-210637-E2C4/{trace.csv,jumps.csv,session.json,surfr.json,device.log,score.md}`; `data/sessions/20260914-104207-E2C4/trace.csv`; `data/sessions/20260910-103108-E2C4/jumps.csv`; `data/refit.md:39`.
- Outside this repo: `/Users/joshcrow/Jump-height-race-openai/hardware/enclosure/HANDOFF-2026-09-15.md` (branch `enclosure-race-openai`, separate worktree; `:13`, `:86`).

**External primary sources fetched.**

- ST, LSM6DS3TR-C datasheet **DocID030071 Rev 3** — Table 3 pp. 21–23, §5.5 p. 36, Table 51 p. 60, Table 68 p. 66, Table 73 p. 67, §9.59–9.61 p. 82, §9.79 p. 90. (`st.com` refused direct download twice — HTTP/2 INTERNAL_ERROR, then a 180 s timeout; fetched from `cdn-shop.adafruit.com/product-files/4503/4503_LSM6DS3TR-C_datasheet.pdf`.)
- WOO, *The WOO Way* v1.3, 3 June 2024 — `https://a.storyblok.com/f/120582/x/56f3f4257e/thewooway13.pdf`, pp. 12, 19–20, 36.
- Marčiš et al. 2021, *Sensors* 21(24):8353 — `https://pmc.ncbi.nlm.nih.gov/articles/PMC8706814/`.
- Kranzinger et al. 2024, PLOS ONE — DOI `10.1371/journal.pone.0307255`.
- Sensors 24(24):7877 (2024) — `https://pmc.ncbi.nlm.nih.gov/articles/PMC11679435/`.
- Real-time ski-jump tracking — `https://pmc.ncbi.nlm.nih.gov/articles/PMC8659670/`.
- x-io Fusion at commit `3a386ee2fd6952d8982589a7e3eb3728dd68c391` (`FusionAhrs.c/.h`).
- VQF, Laidig & Seel — `https://arxiv.org/abs/2203.17024`.
- Surfr device compatibility — `https://support.thesurfr.app/en/articles/9859609-which-devices-are-compatible-with-the-surfr-app`.
- **Not obtained:** Sadi & Klukas 2011/12 and Lee et al. 2015 (paywalled / HTTP 403 on two attempts); the 2026 skateboarding IMU validation (`10.3390/s26082537`, 403); ST **AN5130 Table 11** (the driver's source for its ~67 Hz gyro LPF1 figure — attributed but unverified); any Nordic nRF52840 LFCLK tolerance specification. None is used as evidence for any claim above.

### Reproduction

New scripts live in `docs/accuracy-review-second-opinion-assets/`. Standard library only; neither imports the repo simulator nor the first report's scripts. From the repository root:

```bash
# Independent re-derivation of the physics and observability numbers (section 8).
python3 docs/accuracy-review-second-opinion-assets/physics_recheck.py   # -> physics_recheck.json

# Independent re-derivation of the MEASURED corpus claims (findings 1-5, 7).
python3 docs/accuracy-review-second-opinion-assets/corpus_recheck.py    # -> corpus_recheck.json
```

`corpus_recheck.py` on the September 14 evening bundle prints, and must print:

```text
jumps=11 outside_band=10 above_gate=9 n_air_reconciles=10/11
trace_rows=648808 peak=19.496 normalized g at t=12928.827 s (raw 20.14 g)
  jump 2 mid-flight max |a| = 2.24 g at +0.376 s
  jump 3 mid-flight max |a| = 2.29 g at +0.411 s
  L=0.20 -> airtime formula stays inside +-0.1524 m to T=0.788 s
```

If the private session files are absent, M1–M4 are written as `NOT RUN` rather than silently omitted; the support-boundary table (M5) needs no session data. The first report's own two scripts were also re-run and **reproduce byte-identically** (SHA-256 unchanged for both files — section 2):

```bash
python3 docs/accuracy-review-assets/physics_sensitivity.py
python3 docs/accuracy-review-assets/reproduce_repo_checks.py
shasum -a 256 docs/accuracy-review-assets/physics_results.json docs/accuracy-review-assets/repo_checks.json
```

Neither this document nor its scripts modify firmware, calibration, the production detector, or any other file in the repository.
