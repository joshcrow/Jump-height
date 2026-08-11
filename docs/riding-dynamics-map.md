# Riding-dynamics metric map — the on-water (non-airborne) half

**Provenance:** authored 2026-08-05, the gyro-add sweep's second synthesis.
Companion to the airborne work (`docs/wing-ballistic-sim.md` jumps,
`docs/gyro-sim-plan.md` spins — g4 already promoted the gyro from a
recording extra to a **detector hot-path input**). Where those two cover
clean ballistic/rigid-body physics, this map covers the **riding** half:
the fluid-structure dynamics while the board is on the water/foil. It is
the riding-side extension of `docs/research.md §8` "Backlog implications"
— it does **not** re-propose the six items already there (time on foil,
chop meter, crash counter, pop strength, landing quality, GPS
speed/distance). It adds the carve/turn, pumping, wave-riding, and
foil-state families the §8 pass never enumerated, triaged against physics
+ surf/foil/academic prior art. Every metric is split into a
**MEASUREMENT** kernel (physics, often desk-buildable now the gyro is
being added) and a **DETECTION** layer (thresholds, real-data-gated). The
split is the whole point.

## 0. Headline verdict

**The gyro converts one unmeasurable family — carve/turn dynamics — into
near-solved measurement, and that family is the entire pre-water build.**
Everything else on the riding side is a fluid-structure **detection**
problem whose thresholds are fiction to desk-guess (the §6 rule: "desk
guesses about what foiling feels like to the sensor would be fiction") and
that therefore waits on **one labeled, video-synced S0 water session**.
The strategic move is a clean split:

1. **Build the carve/turn MEASUREMENT bundle now** (§3): carve-g, yaw
   turn-rate, turn-count segmentation, rail/lean angle, and the
   GPS-free radius/in-turn-speed identity. These are closed-form
   rigid-body kinematics riding the **already-committed** xioTechnologies
   Fusion AHRS (`research.md §7`) and the proven ∫gyro kernel
   (`gyro-sim-plan.md` g1/g4) — the exact math the ballistic/spin core
   validated, now applied to on-water turns. They are golden-testable at
   the desk against synthesized turn kinematics, mirroring the
   `windows.py` "build-the-extractor, defer-the-threshold" discipline.
2. **Add two zero-threshold whitespace kernels now** (§3.5–3.6): ride
   smoothness (SPARC on the gyro stream) and rotational chop exposure
   (gyro-variance column on `windows.py`). Both emit a valid number on
   day one with no detection layer.
3. **Everything else is DETECTION and rides the same S0 session** (§4):
   carve-vs-wobble cutoffs, foil-breach templates, wave-ride counting,
   pump count/cadence, maneuver segmentation. They wait not because they
   are low-value but because their deliverable *is* the threshold, and the
   threshold is only knowable from labeled traces. They ride along on the
   one instrumented water session the whole backlog now blocks on — so
   the session must be **captured to serve all of them at once** (§8).

The user flagged two misses — **"g-forces in carves"** and **"green-wave
surf dynamics"**. The first is the flagship PURSUE-NOW (carve-g, §3.1);
the second is the highest-value DATA-GATED item (wave count, §4), and the
reason the S0 session must include swell/downwind, not just flat water.

## 1. How this extends §8 — the measurement/detection split

`research.md §8` annotated six metrics that were already on the roadmap.
This map's contribution is a different axis: it **enumerates the metrics
§8 never had**, and for each it draws the line §8 never drew — between the
physics you can build and unit-test at the desk (MEASUREMENT) and the
fluid-signature cutoff you cannot (DETECTION). §8's own items fit the same
frame and are the precedent: time-on-foil ships an *extractor* now
(`windows.py`), its foil/taxi/still *cutoffs* self-derive on real data.
Every item below inherits that pattern. Read §8 first; this is the layer
on top.

The one structural fact that reorganizes the whole riding side: **the
gyro is a hard prerequisite for the carve family and irrelevant to most of
the rest.** Accelerometers cannot observe a steady constant-rate turn at
all, cannot separate a leaned-turn's gravity leak from true lateral load,
and cannot recover dynamic roll under centripetal load. The gyro fixes all
three. But pump frequency, wave-face pitch, jerk, and breach vibration are
carried mostly by the accel channel `windows.py` already reads — the gyro
only sharpens them. So the gyro-unlocked carve/turn bundle is where the
new-hardware value concentrates, and it is exactly the pre-water build.

## 2. The ranked MISSED metrics

Beyond §8's six. Ranked by (value × how much of it is buildable now).
V = value, GYRO = gyro-unlocked, M/D = where the work sits.

| # | Metric | V | GYRO | M/D | Verdict | Prior art / novelty |
|---|---|---|---|---|---|---|
| 1 | **Carve g-force** (lateral/centripetal, gravity-removed) | HIGH | yes | M now, band edges later | PURSUE-NOW | FoilMotion ships per-turn G on the *wrist*; no board product. PARTIAL |
| 2 | **Yaw turn-rate** (heading angular velocity) | HIGH | yes (definitional) | pure M | PURSUE-NOW | Ski turn-rate mature (Martinez 2019, PMC7739568). PARTIAL |
| 3 | **Turn/carve count + toeside/heelside symmetry** | HIGH | yes | M now, wobble-floor later | PURSUE-NOW kernel | Ski zero-crossing count, ratio 0.997. PARTIAL |
| 4 | **Rail / lean / roll angle** | HIGH | yes (decisive) | M now, calib + label later | PURSUE-NOW | No surf/foil app ships it; wrist can't see board roll. **WHITESPACE** |
| 5 | **IMU-only turn radius + driftless in-turn speed** (R=a_lat/ω², v=a_lat/ω) | HIGH | yes | M now (GPS-free form) | PURSUE-NOW | Textbook vehicle dynamics; unclaimed in any board/foil tracker. **WHITESPACE** |
| 6 | **Ride smoothness / jerk** (SPARC on gyro) | MED–HIGH | yes (cleaner) | pure M, no threshold | PURSUE-NOW | Rehab-standard metric; zero board-sport use. **WHITESPACE** |
| 7 | **Foil breach / ventilation / touch-down** | HIGH | partial | all D | DATA-GATED | No published foil-breach IMU signature. **WHITESPACE / moat** |
| 8 | **Wave count & rides** (green-wave/swell — user-flagged) | HIGH | partial | mostly D | DATA-GATED | Surf IMU wave-count 85–95% norm; foil-swell thin. PARTIAL |
| 9 | **Pump count & cadence** | MED | no | M scaffold now, D later | DATA-GATED | Pumpfoilytics/FoilMotion/BreakFinder ship it. **EXISTS** |
| 10 | **Bottom-turn / top-turn maneuver segmentation** | MED | yes | D (rides #1/#3) | DATA-GATED | Pestana 2020 surf maneuvers, F1 0.85–0.88. PARTIAL |
| 11 | **Rotational chop exposure** (gyro-variance chop) | MED | yes (native) | M now (column) | PURSUE-NOW | Gyro extension of §8 chop meter. PARTIAL |
| 12 | **Gybe / tack / heading-reversal count** | MED | yes | M now, class-threshold later | DATA-GATED | GPS-course gybe count exists; IMU-only unclaimed. PARTIAL |
| 13 | **Maneuver success rate** (tack/jibe completed vs fell) | MED | yes | D (composition) | DATA-GATED | Hoolan + Wake ship success labels. PARTIAL |
| 14 | **Porpoising / pitch-oscillation instability** | LOW | yes | M kernel, D discrimination | DATA-GATED | Planing-craft physics; no wearable. PARTIAL |
| 15 | **Stall / sink-off-foil detection** | MED | partial | all D | DATA-GATED | Pumpfoilytics "fail" phase closest. PARTIAL |
| 16 | **Downwind bump-connect / pump-to-connect** | MED | partial | D (composition) | DATA-GATED | Technique demand documented; no product. **WHITESPACE (gated)** |

Dropped as redundant or hardware-blocked (§6): carve-vs-slide style
classification (needs GPS course + 2000-turn labels), absolute turn
radius r=v/ω (GPS-gated; the GPS-free form is #5), taxi/paddle-transition
(already the §8 time-on-foil boundary), wave ride-time/entry-exit speed
(GPS + already time-on-foil), TRAX pressure-pad stance (needs hardware we
don't have), Fliteboard powered-off-ride (needs motor telemetry).

## 3. PURSUE-NOW — clean measurement kernels buildable pre-water

All of §3.1–3.4 are **one deliverable**: the carve bundle. They share the
AHRS gravity projection and a single turn-window, so they should be built
and golden-tested together, exactly as the ballistic core was. §3.5–3.6
are two independent zero-threshold whitespace adds.

### 3.1 Carve g-force — the user-flagged flagship

- **MEASUREMENT (buildable now).** Rotate the specific-force vector into
  board frame with the gyro-fed Fusion AHRS, subtract the projected 1 g
  gravity vector, take the in-plane horizontal component:
  `a_lat = horizontal(a_specific − R(q)·g)`. The gravity removal is the
  kernel — raw |a| conflates carve load with bumps and lean. Real
  board-sport carve loads are ~1.5 body-weight ≈ 1.5 g lateral
  (PMC10141132, insole IMU carving), well inside the ±16 g Sense part.
  Golden test: prescribe (attitude, a_lat) → synthesize the specific-force
  vector → invert → assert recovery. No sim, no water needed — an
  estimator + unit test.
- **DETECTION (data-gated, inherited).** *Which* excursions are carves vs
  chop-slap vs board slap is the carve-window, and that window is
  **shared with §3.3** — so carve-g adds almost no independent detection
  risk. Session stats: peak carve-g, per-turn peak, turn G-integral.
- **Why the board wins.** FoilMotion ships per-maneuver G on the wrist,
  refined over "hundreds of sessions", no published accuracy — but wrist
  motion is arm-contaminated. A board/mast IMU reads the craft's true
  centripetal load at the source. No open board-mount version exists.
- **The gyro unlock.** Accel-only carve-g is systematically wrong under
  lean (gravity leaks onto the lateral axis when railed over — the
  motorcycle lean-angle problem); the AHRS that projects it out needs the
  gyro. Could not exist before this sweep.

### 3.2 Yaw turn-rate — the definitional unlock

- **MEASUREMENT, no detection component.** `ω_yaw = (world-z)·ω_body`,
  the gyro yaw component projected onto world vertical via AHRS.
  Instantaneous rate — no integration, no drift concern. Fully
  closed-form, desk-verifiable, nearly free once AHRS ships (one
  projection). Ski/surf prior art confirms maturity (PMC7739568,
  PMC7256581).
- **Why it anchors the family.** Accelerometers cannot observe a
  steady-state constant-rate turn *at all*. This is a genuinely new
  observable, and the substrate every downstream turn metric rides on:
  count (§3.3), radius/speed (§3.4), gybe count (§4). Ship it as the base
  signal.

### 3.3 Turn/carve count + toeside/heelside symmetry

- **MEASUREMENT (buildable now).** Turn-switch = zero-crossing of a
  low-passed ω_yaw; one turn = one extremum between crossings; direction =
  sign(ω_yaw); heading change = ∫ω_yaw over the turn; rail (toeside vs
  heelside) = sign of roll-rate. Ship a sensible ski-derived default
  filter (Martinez 2019: 4th-order zero-lag 0.5 Hz Butterworth, detection
  ratio 0.997, precision/recall 0.995). This is a deterministic pipeline —
  the segmentation kernel is desk-buildable.
- **DETECTION (one self-tuning threshold).** The *only* gated piece is the
  minimum ω_yaw magnitude/duration that separates a committed carve from a
  lazy weight-shift — a single wobble-floor that self-derives on the first
  labeled S0 carves, the same self-derivation pattern time-on-foil uses.
  Per-run carve counts split toeside/heelside are a headline session stat.
- **Reconciliation of the two lenses.** The physics-enumeration lens filed
  count as after-data; the academic lens filed it PURSUE-NOW because the
  ski precedent is near-solved and only the wobble-floor is gated. Both
  agree on the substance: **build the segmentation skeleton now, ship the
  floor as a self-tuning threshold.**

### 3.4 IMU-only turn radius + driftless in-turn speed — the sharpest find

- **MEASUREMENT (buildable now, GPS-free).** During any coordinated turn,
  the circular-motion identities `a_lat = v·ω` and `a_lat = v²/R` give
  **v = a_lat/ω_yaw** and **R = a_lat/ω_yaw²** from two instantaneous
  signals we already have (numerator = carve-g §3.1, denominator =
  yaw-rate §3.2). Purely algebraic — **no dead-reckoning, no accel
  double-integration, no GPS.** A driftless speed estimate available
  *before* Phase-4 GPS hardware, valid whenever the rider is turning.
  Golden test: prescribe v,R → synthesize a_lat,ω → invert → assert.
- **The critical distinction.** Do **not** compute radius as r = v/ω (that
  needs GPS speed we lack, or integration that drifts — this is what
  FoilMotion and every GPS watch do). The insight that survives GPS-free
  is **R = a_lat/ω²**, which sidesteps v entirely. Absolute radius via v
  is GPS-gated and shelved (§6); the ω² form ships now.
- **DETECTION caveat.** The identity assumes a *coordinated* turn;
  side-slip / skidded carves break a_lat = v·ω. So the validity gate (when
  is a real carve coordinated enough) needs traces — ship it as an
  **in-turn cross-check, not a continuous speedometer**, until real carves
  show how coordinated they are.
- **Novelty.** Textbook automotive/motorcycle IMU speed cross-check; I
  found no board-sport or foil tracker exposing turn radius or in-turn
  speed from IMU-only this way. Genuine **whitespace**, and strategically
  valuable as a pre-GPS speed source.

### 3.5 Rail / lean / roll angle

- **MEASUREMENT (buildable now).** Peak roll per turn and roll-rate
  straight off the gyro-fused Fusion AHRS the spins flagship already
  commits to — the exact g2/g5 attitude kernel from `gyro-sim-plan.md`.
  During a turn the centripetal accel corrupts any accel-only tilt
  estimate (valid only at rest), so this is a direct gyro payoff. Ski roll
  via AHRS validated to CC 0.889 (PMC8038258).
- **DETECTION (two gated pieces).** (a) The mount-frame → board-frame
  calibration — a one-time rig step the S0 drop-cal / battery bench
  (MEMORY: next up) already has to solve anyway. (b) The roll-value →
  "committed carve" mapping, self-derived on water.
- **Whitespace.** No surf/foil app publishes a rail/lean-angle metric, yet
  a board/mast 6-axis IMU is the *ideal* sensor for it (a wrist cannot see
  board roll; WOO/Surfr's board puck is jump-only). The canonical
  accel-fails/gyro-rescues case (motorcycle frequency-separation lean
  estimation). Completes the carve bundle: **count + g + lean + radius**.

### 3.6 Ride smoothness / jerk — SPARC on the gyro

- **MEASUREMENT (pure, no threshold to emit a score).** Spectral Arc
  Length (SPARC) on gyro angular-velocity magnitude, or
  Log-Dimensionless-Jerk (LDLJ) on accel — both deterministic functions of
  the signal. Smoother ride = shorter spectral arc = less high-frequency
  content. Emits a valid per-window/per-ride score on day one; the
  smoothness literature explicitly notes SPARC "can be applied directly on
  rotational velocities measured by a gyroscope" (Balasubramanian 2015;
  Melendez-Calderon 2021), so the gyro gives a cleaner gravity-free channel
  than accel.
- **DETECTION.** Only the interpretive mapping ("what SPARC value = good
  riding") is data-gated, and it does not block logging the raw score.
- **Whitespace + scope note.** No surf/skate/foil product ships a
  smoothness or jerk metric — a rare unclaimed transfer. Scope it as the
  turn/ride **jerk complement** (chattery-vs-clean carve), not a second
  chop number, to avoid overlap with the §8 chop meter. The purest
  pre-water PURSUE-NOW: whitespace, compute-only, zero threshold.

### 3.7 Rotational chop exposure — a near-free gyro column on `windows.py`

- **MEASUREMENT.** Add roll-rate and pitch-rate variance/RMS over
  foilborne windows — the rotational counterpart to `windows.py`'s
  existing translational band-energy columns. Quantifies how much the chop
  *twists* the board vs bounces it. Exists only because the gyro is being
  added; a near-zero-cost feature column worth landing while the gyro
  harness + AHRS are already being wired.
- **DETECTION.** Band cutoffs inherit the §8 chop meter's Douglas-Sea-Scale
  data-gating — defer them to the parent metric's after-data pass.

### 3.8 Pump-frequency extractor — MEASUREMENT scaffold now, metric gated

The prompt names "pumping frequency" as a pursue-now kernel, and the
**measurement** half is a cheap pre-water scaffold — but the shipped
*metric* is DATA-GATED and competitor-saturated, so it sits in §4. The
buildable-now piece: a dominant-frequency column via `windows.py`'s
existing Goertzel band-energy machinery (add a peak-frequency bin in the
~0.7–3.0 Hz pump band; the low-band split already half-computes it). That
extractor is honest DSP worth staging alongside the other columns. What it
is **not** is a shippable count/cadence — see §4 item P.

## 4. DATA-GATED — detection that rides the same S0 water session

These are not low-value; their **deliverable is the threshold**, and the
threshold is fiction to desk-guess. They wait on **one instrumented,
video-synced S0 water session** — and because that single session is what
the whole backlog now blocks on, every item here must be served by it at
once (§8). For each: the MEASUREMENT carrier already exists; only DETECTION
is gated.

| Item | Carrier (exists) | Gated detection | Rides on |
|---|---|---|---|
| **Carve/maneuver segmentation** (Pestana 2020) | ω_yaw excursion + lateral pulse | window→class model, accuracy bar (recall 95–98%, F1 0.85–0.88) | §3.1–3.3 kernels |
| **Foil breach / ventilation / touch-down** | high-band vibration burst (`windows.py`) + AHRS pitch-step | the composite template; **no published signature exists** | §3.5, breach moat |
| **Wave count & rides** (green-wave, user-flagged) | ~0.05–0.2 Hz AHRS pitch swing + forward surge | ride amplitude/shape, time-on-wave gate | new low band |
| **Pump count / cadence** (item P) | pump-band peak-freq (§3.8) | pump-vs-chop-vs-swell cutoff, cycle count | §3.8 extractor |
| **Bottom-turn / top-turn phases** | pitch × ω_yaw sign × carve-g | multi-channel maneuver templates | §3.1/§3.3, wave count |
| **Gybe / tack count** | short-window ∫ω_yaw (drift-bounded) | gybe-vs-big-carve angular threshold | §3.2 |
| **Peak carve-g banding** | crash-counter peak-hold + histogram | band edges (easy/committed/laid-out) — need real carve-g distribution | §3.1 |
| **Board trim / glide state** | AHRS pitch (buildable now) | glide = low rate-variance ∧ low pump-band ∧ small drift | §3.5, §3.7 |
| **Takeoff / drop-in / dockstart** | gyro pitch-up + hull-vibration loss | trigger FSM vs accel bump | §8 pop + time-on-foil edge |
| **Maneuver success rate** | — (composition only) | "flight maintained through turn" = foil-state ∧ no crash spike | §3.3 + §8 foil-state + crash |
| **Porpoising** | narrowband pitch-rate peak (~0.5–2 Hz) | involuntary-vs-voluntary (correlation w/ intentional roll/yaw) | §3.7 |
| **Stall / sink-off-foil** | forward decel + downward drift + re-wet vibration | smooth-trajectory template; overlaps time-on-foil down-edge | §8 |
| **Downwind bump-connect** | wave-pitch band ↔ pump band alternation | "connect" state sequence; needs downwind traces | wave count + pump |

Two notes the split forces:

- **Foil breach is the highest-value DATA-GATED item and the repo's moat.**
  `research.md §6` already established that no published vibration signature
  separates foilborne from hullborne; breach/ventilation extends that same
  gap to the loss-of-lift transient. There is nothing to build at the desk
  beyond the channel harness (which exists) — the whole value is a
  detection template that must be learned from labeled water+video. The
  Trace/AlpineReplay patent US10146980 (fire when mean **and** SD of both
  accel- and gyro-magnitude jointly exceed empirical thresholds) is the
  right template to *fit* once traces exist. The gyro's pitch-step is what
  will disambiguate a breach from an inline chop slap — so it is data worth
  deliberately labeling. Archetypal PURSUE-AFTER-DATA.

- **Pump (item P) is EXISTS, not whitespace — scope soberly.** Pumpfoilytics
  (Garmin CIQ, ML + GPS state machine), kechel/pumpfoil (open), FoilMotion,
  and BreakFinder (on-watch FFT 1.5–3.0 Hz) all ship auto pump detection.
  Our only differentiation is board-mount + open + a gyro pitch-rate phase
  channel none of them has — **not** method. The `windows.py` extractor is
  cheap; report count/cadence/glide-time, and **drop efficiency (m/stroke)
  and run/fail (8 s/25 m rule)** — both need GPS distance we lack (Tier-D).

## 5. WHITESPACE vs surf/foil prior art

Six genuinely unclaimed positions, in descending confidence. All riding
products surveyed are **wrist** (FoilMotion, Hoolan, Pumpfoilytics, Dawn
Patrol), **GPS** (Surfline, Rip Curl), or **e-foil onboard** (Fliteboard);
the one board-mounted consumer puck (WOO / Surfr board mode) is **jump-only**.

1. **IMU-only turn radius + driftless in-turn speed** (R = a_lat/ω²,
   v = a_lat/ω, §3.4). Textbook vehicle dynamics, unclaimed in any
   board-sport/foil tracker — all get speed from GPS. A pre-GPS driftless
   speed source valid in every turn. The sharpest find; buildable now.
2. **Rail / lean / roll angle from a board/mast IMU** (§3.5). No surf/foil
   app ships it; the wrist structurally cannot see board roll. Gyro-unlocked
   and buildable now.
3. **Foil-breach / ventilation IMU signature** (§4). No published
   foil-breach detector exists anywhere — the repo's known moat, grounded in
   real ventilation physics (arXiv 2503.18015) with the instrumented Moth
   foil-vibration lead (ResearchGate 351200998, flagged in `research.md §9`)
   as the nearest real dataset. All value is DETECTION; ride the S0 session.
4. **Ride smoothness / SPARC-on-gyro in board sports** (§3.6).
   Rehab/biomechanics-standard, zero surf/skate/foil application found.
   Compute-only, no threshold; buildable now.
5. **Downwind bump-connect / pump-to-connect** (§4). Technique literature
   treats bump-reading as *the* core downwind skill; no tracker quantifies
   connects. Genuine open whitespace but the most data-gated item — a
   capstone, not a near-term move.
6. **The board-mount position for the whole carve family** (meta).
   Structural whitespace: measuring carve-g, rail-angle, and yaw-rate at the
   craft — not the arm, not a GPS fix — is an unoccupied hardware position.
   This is the "why board-mount wins" rationale under §3, not a separate
   build.

Explicitly **not** whitespace: pump count/cadence (EXISTS commercially),
wave count (surf-tracker norm at 85–95%), maneuver success rate (Hoolan +
Wake ship it). Carry them for demand proof, not novelty.

## 6. What NOT to build (dropped, redundant, or hardware-gated)

- **Absolute turn radius r = v/ω** — GPS-gated; the GPS-free R = a_lat/ω²
  (§3.4) is the buildable form. Shelve the v-form as a Phase-4 GPS
  extension of the carve family, alongside §8's GPS speed/distance.
- **Carve-vs-slide/pivot style classification** — doubly blocked: attack
  angle needs GPS *course* (velocity direction, which the gyro cannot
  supply) **and** a large hand-labeled set (ski precedent: 2000 turns, 20
  skiers). The GPS-free fallback (lateral-accel tangency) is exactly the
  desk-fiction the §6 rule forbids. DROP.
- **Taxi / paddle-transition** — its load-bearing deliverable (the
  foil-up moment) *is* the §8 time-on-foil foilborne/hullborne boundary;
  fold paddle-cadence interest into that classifier, don't track separately.
- **Wave ride-time / longest-ride / entry-exit speed** — every distinctive
  output needs GPS; the one IMU-doable part (on-wave gate) is time-on-foil
  relabeled. Absorb into §8, propose nothing new.
- **TRAX pressure-pad stance** — front/back weight distribution is not
  reconstructable from a 6-axis IMU; needs traction-pad hardware we don't
  have. Keep only as the board-mount prior-art benchmark.
- **Fliteboard powered-off wave ride** — detectable only via motor-current
  telemetry (knows the motor is off); does not port to an unpowered wing.
  Keep the demand signal (board-mount telemetry is proven), drop the metric.

## 7. Honest caveats

- **Coordinated-turn assumption (§3.4).** v = a_lat/ω holds only when the
  carve is coordinated; skidded/ventilating turns break it. Ship as an
  in-turn cross-check, not a speedometer, until S0 shows how coordinated
  real wingfoil carves are.
- **The carve bundle's kernels are desk-buildable; its bands are not.**
  §3.1–3.5 emit raw signals now, but every *banding* (carve-g histogram
  edges, committed-vs-lazy lean, carve-vs-wobble floor) is a real-data
  cutoff. Building a desk-guessed histogram would be the §6 fiction. Build
  extractors now, band after data — the `windows.py` discipline exactly.
- **AHRS wiring lacks an executable oracle until the carve bundle ships a
  unit test.** The whole bundle rests on the Fusion attitude being correct;
  add a ~40-line regression oracle against a prescribed true attitude on the
  real Fusion lib (the same gap `gyro-sim-plan.md §7` flags for g2/g5), not
  a from-scratch attitude twin.
- **Mount-frame calibration is a shared prerequisite** for carve-g and
  lean (both need board-frame axes). The S0 drop-cal bench already has to
  solve mount offset; land that calibration step and both metrics inherit it.
- **Pump/wave/breach are un-simulatable by design.** Their signatures are
  fluid-structure; a desk sim would launder an assumed profile back out as a
  finding (the `gyro-sim-plan.md §5` trap). Do not sim them — capture them.

## 8. The one instrumented water session everything now waits on

Every DATA-GATED item in §4 rides the **same** labeled, video-synced S0
water session — so the session must be designed to serve all of them at
once, not re-run per metric. What it must capture, beyond what jumps/spins
already need:

- **Deliberate carves both directions** at varied intensity → carve-vs-
  wobble floor (§3.3), carve-g band edges, lean mapping, and the
  coordinated-turn validity gate for R/v (§3.4).
- **Sustained pumping** (flat-water and to-connect) → pump-vs-chop cutoff
  (§4 item P), cadence ground truth.
- **Swell / downwind riding** (the user's green-wave flag) → wave-ride
  segmentation, bottom/top-turn phases, bump-connect. Flat water alone
  cannot produce these — the session must include open-water swell.
- **Labeled foil breaches / touch-downs** → the moat template (§4). These
  happen incidentally; the value is *labeling* them from the synced video,
  so the video must be continuous and time-aligned.
- **Gybes/tacks and a few falls** → gybe threshold (§4), maneuver
  success/fail ground truth.

The synced-trace + 240 fps video rig the jump validation already specifies
(`algorithm.md`, `research.md §2`) is the same rig — riding metrics need no
new hardware, only that the session be **ridden richly** (turns, pumps,
swell, breaches, gybes) rather than jump-only. Capture once, unlock the
whole riding half.

---

*This map is the durable record of the riding-dynamics sweep. The
per-lens raw triage (physics enumeration + surf/foil/academic prior art)
informed it but is not committed, matching `research.md`'s provenance
discipline.*
