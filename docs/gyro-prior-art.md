# Gyro trick-metrics — prior-art synthesis (the 2026-08 pass)

**Provenance:** three parallel research angles (academic IMU
rotation/spin counting; open-source fusion/trick libraries;
commercial + patent) run and adversarially verified 2026-08-04/05.
Every load-bearing number below was fetched or cross-checked at
research time; items an agent could not re-open at full text are
marked PAPER-SOURCED or UNVERIFIED. This document is the durable
gyro-flagship record. It **extends** `research.md` (which covers the
ballistic core) and pairs with `gyro-sim-plan.md` (which decides which
sims to run). Where the two agree the flagship is doubly de-risked;
where they diverge it is flagged.

## 0. Headline verdict

**Prior art ANSWERS the method for 4 of the 6 candidate sims and leaves
2 genuine gaps — matching, from the literature side, the sim-plan's
"only g4 is worth running."** A board-mounted 6-axis IMU counting total
rotation over a 1–3 s air window is not a research question anymore: it
is published, board-mounted, and validated to **±8.18° / 1.42% (CCC
0.998)** by Merz/Gorges 2025. The quaternion self-rotation-vs-flip
decomposition, the ±2000 dps / high-ODR sizing, and the axis-signature
trick classifier are each answered by a specific source with a real
accuracy number — **adopt the result, scope those sims to confirmation,
do not discover.** The two places we are genuinely first are exactly the
two the sim-plan keeps: **g4** (per-sample ω²r subtraction inlined into a
*stateful ballistic detector*) and **g5** (board landing *attitude* — the
one claimed prior number was retracted). One heavy caveat threads all of
it: every validated number comes from **trampoline bounce-boards or
gravity-aided fusion**, neither of which is a hand-held wing in
near-freefall — so the *methods* transfer, the *field accuracy on a wing*
is ours to measure.

## 1. Per-sim verdicts — ANSWER / INFORM / GAP

| Sim | Question | Verdict | Basis (one line) |
|---|---|---|---|
| **g1** drift/count | Count a full rotation by integrating a real MEMS gyro over 1–3 s? | **ANSWER (skip drift sim)** | Merz/Gorges 2025 measured it: ±8.18°/1.42%. ADI RAQ-139 + ST 3°/hr make drift negligible over 1–3 s; error reframes to **static-bias-offset + scale-factor**, both calibrated out (mikoff <0.1%). |
| **g2** coupled 3D | Do coupled spin+flip break naive single-axis counting; need quaternion AHRS? | **ANSWER method / GAP freefall** | US10408857B2 gives the exact quaternion decomposition (`L̇=½ω∘L`, self-rotation vs flip); it is the *definitional* answer (a "360" is about world-vertical). Freefall decomposition accuracy is validated by **nobody** (Merz = magnitude on trampoline; PMC5017605 caps at 200 dps with gravity aiding). |
| **g3** ODR/FSR | Gyro ODR + full-scale to avoid aliasing/clipping? | **ANSWER (skip → datasheet)** | Figure-skating peaks **1665 dps**, diving **1090 dps**, both inside ±2000 dps; Groh's board rig ran ±2000 dps/200 Hz and tricks fit. Register-config arithmetic, not a sim. |
| **g4** spin↔height | Does ω²r corrupt airtime/height; does gyro subtraction recover it? | **GAP — we are first** | WOO's 15–20% height error *growing with jump size* is consistent with un-subtracted rotational contamination but **no source isolates ω²r**, and none feeds per-sample subtraction into a stateful detector. This is the sim-plan's sole RUN. |
| **g5** landing attitude | Recover flat vs nose/tail/rail-first from the touchdown transient? | **GAP — we are first** | The only claimed number (Groh-2016 "2.2°") was **RETRACTED** — it isn't in the paper. U-Net gives landing *timing* (~5 ms), US10408857 gives gyro-shock *event* detection — neither recovers *attitude*. |
| **g6** trick class | Classify spin vs flip vs direction from the rotation-axis signature? | **ANSWER method / GAP dataset** | Groh 2015 skateboard: axis signature → **97.8%** (SVM & NB), stance flips x/z sign = direction. Classical features suffice; no deep learning needed. Our *wingfoil* class boundaries need labeled S0 logs. |

**Reconciliation with `gyro-sim-plan.md`:** the sim-plan runs **g4**,
gates **g1** (single-axis-vs-quaternion fork) on real tilt data, and
skips g2/g3/g5/g6. Prior art independently supports every one of those
calls: g1's *drift* premise is answered (the plan trims g1 to the
non-drift fork for the same reason); g2/g3 are the trig identity + a
datasheet register; g5/g6 are answered in *method* but their
wing-specific numbers need field data a sim can only launder. The one
place to hold the line: prior art does **not** validate g4 — it makes
g4 more urgent, because the market leader's dominant failure mode
(height error growing with jump size) is exactly what g4 investigates.

## 2. ADOPT — ranked (methods + code, license, accuracy)

1. **Merz / Gorges et al. 2025** — *Comparative analysis of IMUs vs
   markerless video for rotational parameters in snowboard freestyle*,
   Measurement: Sensors, S2665917425000662. **ADOPT-THE-RESULT for g1.**
   Board-mounted 6-axis IMU, 8 riders, 88 tricks: total rotation **SDD
   ±8.18° (±1.42%), CCC 0.998, bias 1.80°±16.02° LoA**; angular-velocity
   series mean SDD <45°/s, CCC >0.9, **bias −0.19°/s ±87.48°/s LoA** (the
   wide AV LoA is itself the g4 caution). *License: paper, no code.*
   **Caveat: trampoline bounce-board, total-magnitude only — de-risks g1
   fully, g2 for magnitude only.**
2. **dlaidig/vqf** (VQF, Laidig & Seel, Information Fusion 2023) —
   **MIT**, verified on repo. Quaternion fusion with **online
   gyro-bias estimation**, 6D (mag-free) mode, rest-detection; official
   C++/Python/MATLAB. **~2.9° RMSE** vs 5.3–16.7° for Madgwick/Mahony
   (paper figure, not on repo). Best permissive answer to g1/g2
   short-window drift; clean escape from the no-license
   Madgwick/Mahony repos already flagged in `research.md §7`. *Verify
   third-party ports (Eigen3, Rust) separately.*
3. **xioTechnologies/Fusion** — **MIT, already vendored.** Its
   under-used **gyro-offset estimator** (stationary-window ZUPT,
   defaults 3°/s threshold / 3 s dwell, offset persistable to NVM)
   directly mitigates g1 bias between airs. *Caveat: a board on moving
   water is never truly still — the g1/g4 sims should stress the ZUPT
   with a non-zero wave-motion gyro floor, not a clean static board.*
4. **Mayitzin/ahrs** — **MIT, pure NumPy** (17 estimators, verified).
   One library gives BOTH the naive single-axis `Integration` baseline
   (g1) AND quaternion AHRS incl. EKF/UKF (g2) behind an identical
   `(gyr,acc,mag)` interface → g1-vs-g2 head-to-head on synthetic
   spin+flip traces is an afternoon. Ships **zero accuracy claims** — it
   de-risks the *code path*, you still own the numbers. Already the
   `sim/` parity-twin per `research.md §7`.
5. **mikoff/imu-calib** — **MIT.** In-situ scale-factor + bias +
   misalignment calibration from standstill-separated rotations, **no
   rotating table**; estimated-vs-true error **<0.1%**, validated on 5
   real MPU-9150 units. This is the g1 enabler: over a full 360°, **scale
   factor (not bias) dominates** — 1% scale = 3.6° per rev — and it
   calibrates out cheaply. Model scale error, not just bias, in g1.
6. **Groh, Kautz, Schuldhaus, Eskofier 2015** — *IMU-based Trick
   Classification in Skateboarding*, KDD LSSA (full PDF verified).
   **ADOPT the method for g6.** Board IMU (±16 g/±2000 dps/200 Hz);
   rotation-axis signature (Ollie=pitch-y, Kickflip=roll-x,
   Shove-it=yaw-z, 360-flip=x+z), **stance inverts x/z sign =
   direction**; event detection sens **94.2%**, classification **97.8%**
   (NB & SVM tie). *Paper, no code — classical features + SVM/NB, no DL
   needed.* **Caveat: ground-coupled skate tricks, not free aerial
   spins.**
7. **g1 numeric budget** (adopt as plug-in numbers): **ST MEMS forum**
   (Borlini) — LSM6DSR "similar AVAR to ISM330DHCX" whose datasheet
   quotes **gyro bias stability 3°/hr** → ~0.0025° over 3 s; **ADI
   RAQ-139** — bias-instability dominates only after **<10 s**, so a 1–3 s
   window is bias-offset + ARW, both correctable; **DesignWorld/ADI** —
   consumer scale-factor tolerance **±1%** → ~5.4° over a 540° spin. Net:
   **integer spin *counting* is robust even uncalibrated** (±1–2% << ±180°
   half-turn tolerance); a continuous *degrees* readout needs the §2.5
   calibration.
8. **Figure-skating jump monitor** — PMC6248918 (Sensors 2018). **ADOPT
   for g3 sizing.** In-air peak ω **889–1665 dps** (doubles 1273, triples
   1465); confirms **±2000 dps** full-scale and reduces g3 to a
   *clip-margin check*, not an FSR sweep. Also: revolutions from
   integration between accel-peak takeoff/landing, airtime err
   0.031±0.025 s, height err 3.33±2.75 cm — but authors **caution drift
   was uncontrolled**, so g1 support is evidence, not proof.

## 3. READ — before writing the sims (method/positioning, no adopt)

- **US10408857B2** *Use of gyro sensors for identifying athletic
  maneuvers* (AlpineReplay/Trace, granted 2019) — **the whole-flagship
  blueprint, verified verbatim.** Quaternion gyro integration →
  self-rotation-vs-flip decomposition; pre/post-jump accel+mag averaging
  for initial attitude; **gyro-shock (not accel) landing detection**;
  template/DB trick ID. FIG.10: accel hits **4 g in a free-fall double
  cork** — the argument accel-based detection fails. **Scope g1/g2/g5/g6
  to confirmation, not discovery.** *FTO: live, broad — implement via MIT
  Fusion + our own thresholds, cite as prior art, do NOT copy the
  self-rotation/flip claim language in marketing.*
- **bareboat-necessities/ocean-imu** (MIT, C++20/ESP32) — **the only
  prior art in our exact environment:** a sensor on moving water that
  never sees clean 1 g rest. 18/21-state Kalman wave-decoupling separates
  heave/surge/sway + wave motion from true attitude. Read before g4/g5;
  the wave-decoupling may port. *Re-confirm license/ESP32 claims before
  porting.*
- **Frontiers Sports & Active Living 2021** (wheelchair, 10.3389/fspor.2021.670263)
  — off-the-shelf Madgwick degrades **~5.5°→11.7°** static→dynamic
  because it mis-corrects when specific-force ≠ gravity; RF gain-switching
  cuts dynamic error ~45%. **The g2 freefall caution:** during the
  ballistic phase the accel reference is useless — argues gyro-dominant/
  adaptive gain in the air window; xio Fusion does not "just work" here.
- **PMC5017605** (Ricci/Formica, Sensors 2016) — bounds fused-AHRS
  accuracy: static 0.25–0.44° median, dynamic ≤8.2° — **but caps at ~200
  dps (⅒ of a board spin) with gravity aiding absent in freefall**, and
  KF was *significantly* better than complementary (not "negligible" —
  prior report corrected). Treat as an **optimistic floor that does not
  cover our rate regime.** Don't burn a sim ranking filters; do note the
  air window sits in the worst regime.
- **WOO validation** PMC8706814 (Sensors) — market leader's height RMS
  **0.70–0.95 m, 15–20% at >5 m, error grows with jump size.** The g4
  failure mode and our positioning: airtime-ballistic height is
  inherently immune to ω²r. *Cannot distinguish drift from rotational
  contamination — g4 must still quantify ω²r, not assume it dominates.*
- **U-Net airtime** PMC11548732 (Sensors 2024) — landing/takeoff to **~5–8
  ms**, threshold baselines over-detect to ~205%. Landing **timing**
  ceiling and an **offline oracle** to label g5 eval data — but too heavy
  for the MCU and **does not recover attitude.** Keep on-device detection
  threshold/gyro-shock based (per US10408857).
- **Groh, Fleckenstein, Eskofier 2016** (snowboard, BSN, PDF verified) —
  per-axis integrated-gyro rotation features discriminate **BS-180 vs
  FS-180 direction >90%** (g6). **Retraction logged:** the "2.2° board
  orientation error" and "96.4%/89.1%" from earlier notes are **not in the
  paper** — g5 is NOT answered here. Grind detection leans on a
  magnetometer we lack; only the gyro/air-rotation portion transfers.
- **Harding et al. 2008** (halfpipe, jst.69) — "Air Angle"
  integrate-by-summation bins **180/360/540/720** groups, P<0.001, n=216
  (g1/g6). *Downgrade: the "±1200 dps @100 Hz" spec could not be
  substantiated (likely a magnetometer µT transcription) — don't lean on
  it for g3.*
- **Trampoline/acro gymnastics** (s12662-022-00866-3; Leite 2025) —
  rotation-*axis* signature separates twist (longitudinal) vs somersault
  (transverse) across 2076 jumps/50 types (g6), and coupled
  twisting-somersaults break single-axis counting (g2). IMUs
  systematically **under-estimate** ω → reinforces the scale-factor step.
- **Springboard diving** (Sports Biomech 2017, PMID 27762669) — peak
  ~1090 dps, and a **scale-factor calibration standardises ω to 0.5%**
  (informs g1's calibration need + g3 headroom).
- **Reefwing-AHRS** (Arduino, reported MIT) — MIT EKF cross-check on the
  complementary-filter xio Fusion, with an **LSM6DS3 driver (our exact
  family)**. Closest runnable on-target g2 reference. *Re-confirm LICENSE
  + EKF claims before adopting.*
- **US10451438B2** (in-motion gyro calibration, AlpineReplay) — industry
  pattern: estimate bias during cruise, freeze through the air window.
  Adopt the *concept* (plain stationary-detector), not the patented
  linear-fit trick.
- **Surfr App** + **WOO docs** + **PIQ/Rossignol** — positioning:
  Surfr owns wingfoil jump *detection* on the wrist but publishes **no
  rotation-degree metric and struggles with rotated jumps**; WOO fuses
  9-axis + NN "Tricktionary" but **always emits a raw rotation score even
  when the classifier can't name the trick — copy that fallback pattern
  for g6.** Board-mount + openness + landing-quality is our whitespace.

## 4. AVOID — dead ends / licensing traps / FTO

- **US10408857B2 / US12000702 / US9587943 / US10451438B2 claim
  language** — patented maneuver-ID + runtime bias-comp. Cite as prior
  art; implement via MIT VQF/Fusion + our own thresholds; do not copy
  claim wording. FTO, not reusable code.
- **kennyegan/Snowboard-Wearable** — "All rights reserved" despite being
  on GitHub. Same trap class as the no-license Madgwick/Mahony repos
  already flagged. Nothing to adopt or cite.
- **eupn/tracksb** — offloads fusion to a **BNO08x (on-chip fusion)** — an
  option the LSM6DS3TR-C does **not** have. Not portable; reinforces that
  we must solve g1/g2 in software. Read for hardware framing only.
- **Nkluge-correa/skateboarding-trick-classifier** (Apache-2.0) &
  **abu-rmileh PeerJ 2021** — **accel-only** (rotation axis absent) and a
  tiny clean dataset (reported 100% = optimistic ceiling). Pipeline
  template only; cannot substitute for a gyro-based g6.

## 5. Where we are genuinely FIRST (gap = our sim/data is the record)

1. **g4 — spin-corruption of a *stateful* ballistic detector.** No prior
   art tests whether per-sample `a_corr = √(max(0, a² − (ω²r/G)²))` fed
   into a real airtime state machine recovers a spin-truncated airtime —
   or spuriously re-crosses the freefall gate / erases the landing spike
   when r is mis-calibrated ±20%. WOO proves the *failure* exists
   (15–20%); nobody publishes the *fix* on a detector hot path. **This is
   the sim-plan's sole RUN; prior art makes it more urgent, not
   optional.**
2. **g5 — board landing *attitude* (flat vs nose/tail/rail-first).** The
   single claimed prior number was **retracted**; U-Net and US10408857
   recover landing *timing/event*, not *attitude*. First — but the
   recoverability is set by an ungroundable fluid-impact/board-flex model,
   so this is a **labeled-water-data** gap, not a desk-sim one (ship a
   cheap flat-vs-not settled-tilt flag now, defer nose/tail/rail).
3. **g1/g2 freefall decomposition accuracy on a *hand-held wing*.** Every
   validated number is a trampoline bounce-board (gravity-coupled at
   apex) or gravity-aided fusion capped at 200 dps. A hand-held wing has
   no kite tether (near-ballistic per `research.md §2`), no gravity aiding
   in the air, and higher rates — so our **video-validated wing rotation
   numbers will be the first of their kind.**
4. **g6 — a wingfoil spin/flip/direction class library.** The
   axis-signature *method* is answered; the *class boundaries* for a
   board-mounted wing are unlabeled. Whoever has the S0 traces owns the
   metric (mirrors the foilborne-signature moat in `research.md §6`).
5. **The whole flagship as a product:** open, inexpensive, board-mounted,
   independently video-validated wingfoil **trick metrics** — Surfr is
   wrist-only and rotation-shy, WOO is closed and kite-first. The market
   whitespace `research.md §5` identified for airtime holds a second time
   for rotation.

## 6. Net changes to the sim plan (what this pass moves)

- **g1:** delete the drift Monte-Carlo entirely — doubly answered (ST
  3°/hr + ADI <10 s crossover). Keep only the calibration budget
  (scale-factor + static offset) as arithmetic, and the single-axis-vs-
  quaternion fork **gated on a real S0 360 tilt** (unchanged from
  sim-plan).
- **g2:** ship quaternion AHRS (forced by definition + US10408857) — no
  filter-ranking sim. Log freefall decomposition accuracy from real jumps;
  it is a data gap, not a sim.
- **g3:** ship **±2000 dps / ODR ≥208 Hz** from the datasheet; the only
  open question (does a whip exceed 2000 dps?) is instrumentation. Note:
  the sim-plan's conservative **±1000 dps** pick is defensible on power
  grounds — reconcile against measured S0 peak rates before locking FSR.
- **g4:** **RUN as planned** — prior art raises its priority (WOO's
  dominant failure mode) without answering it.
- **g5:** ship flat-vs-not tilt flag; attitude recovery waits on labeled
  water data (retracted prior number means no shortcut).
- **g6:** log raw 3-axis gyro on S0, build a labeled library, then apply
  Groh's classical axis-signature features (no DL). Always emit a raw
  rotation score even when unclassified (WOO pattern).

## 7. Gyro reading list (the sessions worth spending)

1. [US10408857B2](https://patents.google.com/patent/US10408857B2/en) — the flagship blueprint; read for method, not to copy.
2. [Merz/Gorges 2025](https://www.sciencedirect.com/science/article/pii/S2665917425000662) — the ±8.18°/1.42% board-rotation validation.
3. [dlaidig/vqf](https://github.com/dlaidig/vqf) — MIT online-bias quaternion fusion (repo + Information Fusion 2023).
4. [Groh 2015](https://www5.informatik.uni-erlangen.de/Forschung/Publikationen/2015/Groh15-ITC.pdf) — the g6 axis-signature method, 97.8%.
5. [bareboat-necessities/ocean-imu](https://github.com/bareboat-necessities/ocean-imu) — wave-decoupling for the on-water g4/g5 regime.
6. [PMC6248918](https://pmc.ncbi.nlm.nih.gov/articles/PMC6248918/) — figure-skating peak-ω (1665 dps) for g3 sizing.
7. [WOO validation PMC8706814](https://pmc.ncbi.nlm.nih.gov/articles/PMC8706814/) — the g4 failure mode + positioning.
