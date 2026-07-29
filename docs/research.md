# Research synthesis — the 2026-07 deep pass

**Provenance:** four parallel research agents (open-source landscape,
sports-science literature, oceanographic/naval-architecture literature,
commercial market), 2026-07-29. Every claim below was live-fetched from
the cited source at research time; items an agent could not verify at
full text are marked UNVERIFIED. This document is the durable record —
the per-agent raw digests informed it but are not committed.

## 1. Headline verdicts

1. **Our core design is now literature-backed** to a degree no
   commercial competitor publishes: the airtime method, 200 Hz, the
   additive time-domain calibration, 240 fps video ground truth, the
   2.5 g landing floor, and the 50 Hz stored trace all have direct
   peer-reviewed support (§2). Two numbers remain ours alone (0.35 g /
   0.08 s) — nothing contradicts them, nothing validates them; the
   bench and water data carry them.
2. **The market thesis sharpened, not broken** (§5): the honest claim
   is now *"nobody serves wing foil with an open, inexpensive,
   board-mounted, independently video-validated sensor"* — not "nobody
   serves wing foil." Surfr is a real, credentialed, free competitor on
   the watch; WOO's current site markets wingfoilers again.
3. **Accuracy transparency is an open goal**: the market norm is zero
   published error specs, and the single independent validation study
   found every commercial sensor overestimates (up to 20–26 % on big
   jumps). Publishing our own video-calibrated error bars at Phase 2 is
   a rare, cheap differentiator — and Trace (ski airtime app) dying
   after shipping unbackable numbers is the cautionary tale.
4. **The sea already ran our experiment** (§6): wave buoys measure
   height by double-integrating accelerometers and only survive via
   narrowband filtering plus discarding absolute position — independent
   confirmation, from instruments far better than ours, that refusing
   to integrate a broadband transient is correct.
5. **Two of our own recorded claims were stale or unverifiable** and
   are corrected in the docs (§3): the Garmin 25 Hz-cap rationale, and
   one WOO whitepaper figure.

## 2. What the science says about our design

| Our choice | Verdict | Key source |
|---|---|---|
| Airtime method h = g·T²/8 | **SUPPORTED** for true free-fall (ICC 0.92–0.997); conditionally challenged only under sustained aerodynamic lift | [Balsalobre-Fernández 2015](https://pubmed.ncbi.nlm.nih.gov/25555023/) (MyJump vs force plate, ICC 0.997, bias 1.1 cm); [Simons 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12580543/) (kite exception, below) |
| Additive `airtime_offset_s` | **SUPPORTED** — the field reports IMU-vs-force-plate bias in *seconds*, not as a height scale | [Pousibet-Garrido 2026](https://pmc.ncbi.nlm.nih.gov/articles/PMC12987133/): bias −11.7 ms (LoA −72…+49 ms), 97.4 % detection, 0.2 g optimal threshold in their (single-axis) framing |
| 200 Hz sampling | **SUPPORTED** — exact-rate precedent | [Marković 2021](https://www.mdpi.com/2076-3417/11/24/12025) (200 Hz foot IMU, CMJ bias −0.18 cm, ICC 0.975); [Gorges 2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC11548732/) (201 Hz snowboard) |
| Video ground truth 120–240 fps | **SUPPORTED, precisely** — 240 fps is the published diminishing-returns point (flight-time error 1.8 ms ≈ 0.7 %) | [Pueo 2023](https://pmc.ncbi.nlm.nih.gov/articles/PMC10108745/) |
| 2.5 g landing threshold | **SUPPORTED** as a safe floor — real board-sport landings peak 2.7–5.5+ g | [Frederick 2006](https://doi.org/10.1123/jab.22.1.33) (4.74 BW skate); [Bessone 2019](https://www.mdpi.com/1424-8220/19/9/2011) (2.7±0.9 BW ski); [Simons 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12580543/) (4.2–5.5 g kite) |
| 50 Hz stored trace for offline classification | **SUPPORTED** | [Gomes 2019](https://pmc.ncbi.nlm.nih.gov/articles/PMC6679232/) surf-activity recognition: "50 Hz retained after verification of adequacy"; 1 s windows, 90.3 % wave detection vs video |
| 0.35 g enter / 0.08 s confirm | **UNTESTED** — no published analogue uses our exact |a|-magnitude definition; published confirm/smoothing windows run longer (185–463 ms), so ours is aggressive by design (wing airs are short) | [Kranzinger 2024](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0307255): even dedicated ski detectors catch only 44 % of sub-0.5 s airs — the short-air regime is genuinely hard |
| ±8 g now, ±16 g later | ±8 g challenged for *peak-severity capture only* (detection unaffected); ±16 g precedented | skateboard IMU work at [±16 g](https://pmc.ncbi.nlm.nih.gov/articles/PMC8384043/); water-ski landing study reports 26–104 g peaks (UNVERIFIED full text) |
| min/max airtime rails | precedented — Harding's snow-sport method used a 0.8–2.2 s validity band | via [Gorges 2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC11548732/) |

**The kite exception, quantified.** [Simons et al. 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12580543/)
(12 semi-pro kitesurfers, board sensor + insoles): Big Air 5.1±2.1 m at
3.1±1.2 s airtime — ballistic math on that airtime predicts ~11.8 m, a
~2.3× overprediction, which the authors attribute to the kite's
"parachute effect." Peer-reviewed proof of the exact failure mode our
roadmap flagged from the WOO whitepaper. A hand-held wing has no tether
mechanism, so wing jumps should sit near-ballistic — but whether an
aggressively-flown wing adds measurable lift mid-air is an **open
question with zero literature**; Phase 2 video calibration is the
experiment that answers it (recorded in algorithm.md).

**Independent sensor-accuracy benchmark.** [Marčiš 2021](https://www.mdpi.com/1424-8220/21/24/8353)
(4-camera videogrammetry, 0.03–0.09 m reference, 20 kite jumps > 3 m):
RMSE Surfr 0.51 m, WOO3 0.70 m, WOO2 0.95 m; all three systematically
overestimate; errors exceed 20 % of true height above 5 m. This is the
bar our video-validated Phase 2 numbers get compared against.

## 3. Corrections to our own record (made 2026-07-29)

1. **Garmin 25 Hz rationale was stale.** Connect IQ's accelerometer API
   was historically ~25 Hz, but Garmin has unlocked **100 Hz on newer
   watches specifically for Surfr — including Instinct 3 Solar**
   ([Surfr compatibility list](https://support.thesurfr.app/en/articles/10925085-garmin-compatibly-list)).
   garmin-datafield.md §12 now rests the board-vs-wrist argument where
   it always really stood: physics. A winger's wrist never free-falls
   (the wing loads the arms throughout); wakeboard wrist trackers work
   precisely because *those* wrists do free-fall.
2. **WOO's market posture.** Their current homepage markets to
   "kiteboarders and wingfoilers" — broader than the "kite-only
   retreat" phrasing our roadmap took from the 2024 whitepaper. The
   whitepaper digest stands; the market description is annotated.
3. **One WOO figure downgraded to UNVERIFIED.** The "~60 % chest-mount
   difference" number could not be re-verified (the whitepaper PDF is
   image-based); WOO's own site documents a board-8 m-vs-chest-6 m
   example (≈25 %) ([source](https://www.woosports.com/en/the-woo-way/point-of-measurement)).
   Roadmap annotated.

## 4. What our number honestly means (naval architecture already solved this)

Oceanography's wave heights (Hs, Hmax) are *statistics of a fixed
observation point*, explicitly "not intended to correspond to any
specific wave" ([CDIP](https://cdip.ucsd.edu/m/documents/wave_measurement.html)) —
the wrong frame for us. The right frame exists in
[ITTC Recommended Procedure 7.5-02-07-02.1](https://www.ittc.info/media/9705/75-02-07-021.pdf)
("Seakeeping Experiments"): **relative wave elevation** — motion
measured against the local, instantaneous water surface co-moving with
the craft. Our metric, honestly worded: **flight height above the
takeoff point**. algorithm.md now says this.

## 5. Market map and the bar to clear

- **WOO 4.0**: $279.95 board puck, IP69K, no published accuracy spec
  anywhere; Garmin integration via two CIQ apps (puck required).
- **Surfr**: free/subscription app, no hardware; live jump height on
  supported Garmin watches (tiered compatibility, Instinct 3 Solar
  "Fully Compatible" at 100 Hz); best accuracy of the three in Marčiš
  2021 (0.51 m RMSE, still overestimates); wing foil supported, no
  wing-specific tuning confirmed. **Closest direct competitor** — to
  the watch half of our plan, not the open board-sensor half.
- **Garmin native**: surf activities count waves (no jumps), Kiteboard
  profile computes no jump height, no Wingfoil activity exists. The gap
  is filled by third-party CIQ apps (Hoolan free, KiteJump Pro $10).
- **Apple ecosystem**: surf-only (Dawn Patrol, Surfline Sessions);
  Wake wakeboard app does wrist jump height + landing success — works
  because wakeboard wrists free-fall.
- **E-foil**: Fliteboard shipped a **"foil active time" odometer**
  (time-on-foil demand is proven, not speculative); no e-foil product
  touches jumps.
- **Cautionary tales**: Trace (ski) shipped flaky airtime and died;
  Syrmo (skateboard puck with auto video clipping — precedent for our
  clip-that gesture) is defunct.

**The bar to clear:** (1) one huge glanceable number first (our design
already matches); (2) Surfr-style honest tiered-compatibility wording;
(3) *publish error bars* — the market ships none; (4) consciously
reject leaderboards/social (industry treats them as mandatory; out of
scope for an open family tool); (5) never claim the 25 Hz cap again —
the board-mount argument is physics.

## 6. What the sea teaches (and what doesn't transfer)

**Transfers:**
- Buoy double-integration survives only via narrowband filtering
  (trusted band 0.033–0.6 Hz) + discarding absolute position
  ([Datawell pipeline](https://coastalmonitoring.org/ccoresources/waveparameterhandbook/),
  [BODC](https://www.bodc.ac.uk/data/documents/nodb/65329/)) — and
  still accepts 0.5–3 % error. A jump is a broadband sub-second
  transient: their machinery cannot port, and that *is* the argument
  for the airtime method, made with someone else's instruments.
- **Chop cannot false-trigger free-fall**: ocean orbital acceleration
  for typical wind chop (H≈0.3 m, T≈3 s) is ~0.07 g (Airy estimate,
  illustrative) — two orders below our 0.35 g gate. Real false
  positives are structure-borne board slap, a different regime
  oceanography doesn't model. Design against *that*, with our own trace
  data.
- **Crash-severity honesty**: ITTC states kHz-class sampling is needed
  to capture true slamming peaks; ~100–200 Hz under-resolves them. Any
  future "gnarliest crash: X g" reports peak-at-200 Hz as a **lower
  bound**. Planing-craft literature brackets the bands: 2–6 g ordinary
  hard riding, 7–10 g hard, tens-of-g rare extremes.
- **Ready-made vocabulary**: Douglas Sea Scale bands as chop-meter
  labels; JONSWAP (fetch-limited chop) vs swell distinction; "hump
  speed" (the drag peak a foiler punches through at takeoff) as the
  physical name for the pop-strength metric.
- **The moat, confirmed**: no published vibration signature separating
  foilborne from hullborne states exists in reachable literature. Our
  plan — derive it empirically from session-1 traces — is not a
  workaround; it is the only path, and whoever has the traces has the
  metric.

**Does not transfer:** buoy filter designs (narrowband assumption),
Hs/Hmax/air-gap reference frames (fixed-point statistics), kite
correction factors (different lift mechanism — the lesson is "verify
ballistic," not any number), slamming load *models* (different loading
geometry; ranges only).

## 7. Open source: adopt, read, avoid

**Adopt (licenses verified):**
- [STMems_Standard_C_drivers](https://github.com/STMicroelectronics/STMems_Standard_C_drivers)
  (BSD-3) — official register sequences for our IMU: wake-on-motion,
  activity, FIFO → S2.
- [xioTechnologies/Fusion](https://github.com/xioTechnologies/Fusion)
  (MIT, active) — AHRS for spins/carves at S5; plus
  [Mayitzin/ahrs](https://github.com/Mayitzin/ahrs) (MIT, pure Python)
  as the `sim/` parity twin — fits our C++/Python discipline.
- [garmin/connectiq-apps](https://github.com/garmin/connectiq-apps)
  (Apache-2.0, official, includes Data Field samples) → M0 reference.
- [python-fitparse](https://github.com/dtcooper/python-fitparse) /
  [fitdecode](https://github.com/polyvertex/fitdecode) (MIT) →
  round-trip validation of our FIT output.
- **Strategic**: [GoldenCheetah](https://github.com/GoldenCheetah/GoldenCheetah)
  reads FIT natively and has a user-defined-metrics engine — emitting
  FIT makes an entire mature analytics desktop our free Tier-A charting
  layer instead of us building charts forever.
- [gpxpy](https://github.com/tkrajina/gpxpy) (Apache-2.0) when GPS
  lands.

**Read for patterns (not code):** Meshtastic's
`NRF52Board.cpp` (GPL — boot voltage check → LPCOMP wake →
System OFF; their open issues also prove nRF deep-sleep-at-low-battery
is hard even for an 8000-star project); OpenLog Artemis's
`ADDING_SENSORS.md` + power-state test sketches; Edge Impulse's
official XIAO Sense support as the S5 classifier fallback.

**Licensing traps (flagged):**
- The FIT Protocol License permits an independent FIT encoder but
  **forbids vendoring Garmin's SDK source** into our public repo.
- The popular Arduino
  [MadgwickAHRS](https://github.com/arduino-libraries/MadgwickAHRS) and
  [MahonyAHRS](https://github.com/PaulStoffregen/MahonyAHRS) repos ship
  **no license file** — read-only; xio Fusion is the shippable one.
- Adafruit_nRF52_Arduino's license is mixed (incl. Nordic SoftDevice
  clauses) — fine to build on, read it before making redistribution
  claims about our binaries.

**The gap, quantified:** GitHub `topic:wingfoiling` → 0 repos;
kiteboarding topic → 7, none trackers; every airtime-tracker search
string → 0. Open-source wing-foil measurement does not exist. We are
first or nothing.

## 8. Backlog implications (annotations, not new commitments)

- **Time on foil**: demand proven (Fliteboard ships it as an odometer);
  method validated at our stored rate (Gomes 2019, 50 Hz, 1 s windows,
  90 % vs video); threshold must be self-derived (no literature — §6).
- **Chop meter**: label with Douglas Sea Scale bands; define as a
  session statistic (CDIP's "Hs is statistical" framing), never a
  per-moment number.
- **Crash counter**: report 200 Hz peak-g as a lower bound (ITTC);
  bands 2–6 / 7–10 / 10+ g from planing-craft literature.
- **Pop strength**: physically = hump-speed loading; name it that in
  analysis copy.
- **Clip-that gesture**: Syrmo did auto-video-clipping in 2014 —
  precedent exists, product died; scope soberly.
- **Landing quality**: Wake (wakeboard app) ships landing success on
  the wrist — precedent for the metric, wrong sensor position for us.

## 9. Reading list (the PDFs worth a session)

1. [Pousibet-Garrido 2026](https://pmc.ncbi.nlm.nih.gov/articles/PMC12987133/) — the airtime-offset bias analogue.
2. [Gorges 2024](https://pmc.ncbi.nlm.nih.gov/articles/PMC11548732/) — closest published analogue to our state machine, incl. chop discussion.
3. [Simons 2025](https://pmc.ncbi.nlm.nih.gov/articles/PMC12580543/) — kite non-ballistic proof + real landing g's.
4. [Marčiš 2021](https://www.mdpi.com/1424-8220/21/24/8353) — the commercial-sensor accuracy benchmark to beat.
5. [ITTC 7.5-02-07-02.1](https://www.ittc.info/media/9705/75-02-07-021.pdf) — definitions, kHz-slamming rule, spectra.
6. [Coastal Monitoring Wave Parameter Handbook](https://coastalmonitoring.org/ccoresources/waveparameterhandbook/) — the buoy pipeline primer.
7. Lead to chase with journal access: "Full Scale Measurements on a
   Hydrofoil International Moth" (ResearchGate 351200998) — possibly
   the only instrumented foilborne-vibration dataset in existence.
