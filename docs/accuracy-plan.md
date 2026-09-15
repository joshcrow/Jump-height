# Accuracy plan — from "the number is a guess" to "the number agrees" (2026-09-14)

Delivery is done: rides, Garmin files, logs and app updates flow without a
hand (`docs/sync-agent-plan.md`). The number is not done. The detector was
tuned on a simulation whose central assumption — a wing jump is ballistic —
the first full real session contradicts:

    2026-09-14 evening, Festival Park, 18 kt N, 1 h 43 m (Surfr):
      Surfr:  32 jumps, best 9.1 ft, longest airtimes 3.8 / 3.4 / 3.1 s
      puck:   11 jumps, best 3.6 ft          (freefall gate 0.35 g, cap 3.0 s)
      replay: gate 0.35 g -> 7 events, none over 1.0 s
              gate 0.50 g -> 25 events, longest 3.3 s      (measured, same trace)
    In the air the wing carries part of the rider: mid-air load sits near
    0.4-0.6 g, never near 0. A 3.3 s flight that Surfr calls 9 ft is 13 m by
    the ballistic formula. Both the gate and the height model are wrong for
    a wing, and the sim (E1-E16) could not have told us.

## What the first scorecard corrected (2026-09-14, `./tools/jump score`)

- The trace is 50.0 Hz. "26 Hz" was rows divided by elapsed time across 38
  sleep gaps: the puck logs only while moving.
- No Surfr-length flight exists in the evening trace: the lowest MEAN load over
  any 3.0 s window, anywhere in 417 min, is 0.947 g; over 1.0 s it is 0.577 g.
  A gate cannot find an interval that is not there. The two Surfr rows we have
  (6.3 ft / 3.07 s and 5.7 ft / 3.39 s) match puck candidates of 0.84 s and
  0.80 s at mean loads of 0.68 g and 0.78 g, 28 s apart as Surfr's are 29 s
  apart. Either Surfr's airtime is not the puck's airtime, or the puck's mount
  does not unload the way the rider does. Video decides; nothing else can.
- The morning bundle was not a session (see corpus).
- The earlier "gate 0.50 g finds 25 with 3.3 s airtimes" was the stock state
  machine spanning from a single low sample to a landing spike; the time
  actually inside the band was ~1 s. Retuning cannot manufacture 3 s flights.

## Ground truth is layered

| Source | Gives | Trust | Cost per session |
|---|---|---|---|
| Surfr (rider's phone app) | count, height, airtime, time-into-session per jump | the reference riders believe; its own model, bias unknown | one scrolled screenshot from the rider |
| Video (GoPro / shore) | exact airtime; real height with a known length in frame (mast) | truth, for a few jumps | rare, deliberate |
| Garmin FIT | speed, position, wall-clock, altitude (1 Hz) | features and time, not truth | automatic |
| Wind forecast / obs | context | context | automatic-able |
| Puck trace | the signal | — | automatic |

Surfr for breadth. Video for calibrating Surfr. Garmin for features.
Agreement with Surfr is the product goal until video says otherwise.

## Alignment

The puck knows only uptime; the bundle's `trace_epoch_utc` pins trace time
to wall-clock. Garmin is wall-clock. Surfr rows are minutes into a session
whose start is shown to the minute; one offset per session, solved by
matching event sequences. After that every jump has a trace window, an
approach speed and a Surfr row.

## Detection: generate liberally, then score

Replace the binary free-fall gate with a load-band model: a takeoff pop
(> ~1.5 g), a sustained low-load band (measured, not assumed — the replay
says ~0.4-0.6 g for this rider and wing), a landing spike (> ~2.5 g).
Offline the candidate generator is loose (recall); a small feature set —
duration, minimum load, pop, spike, gyro magnitude where present — gets
thresholds fitted for precision against Surfr. Firmware runs the same
features with the fitted thresholds. The small-hop floor is whatever makes
the count agree with Surfr's convention; the alignment reveals it.

## Height: physics-informed, fitted

Ballistic: h = g t^2 / 8. Under a lift fraction L the vertical acceleration
is g (1 - L), so h ~= (1 - L) g t^2 / 8 with L from the mean load during
flight. One parameter, fitted to Surfr, corrected by video when we have it.
If residuals say lift is not constant through the arc, the next model is
inertial integration with the gyro (`docs/algorithm.md`, the gyro plan).

## Discipline

- Never more free parameters than sessions / 3.
- Leave-one-session-out. Report bias and spread separately: consistently
  20 % low is a calibration; randomly +-40 % is a model problem.
- Firmware changes only when the whole corpus improves, in batches, through
  the app's firmware channel. Rider notices nothing.

## The loop

    bundle lands -> `./tools/jump score` regenerates every session's card
    -> once a week: one look, one decision, at most one firmware batch.

## Corpus (grows from here)

    data/sessions/<id>/   trace.csv jumps.csv session.json   (puck, automatic)
                          garmin.fit                          (automatic)
                          surfr.json                          (transcribed from the rider's screenshot)
                          wind.json                           (forecast/obs, when captured)
                          score.md                            (generated)

    2026-09-14 10:42  E2C4  NOT a session: a multi-boot ring buffer (t resets at row 37730); its 20 stored jumps are
                            carried over from at least three earlier boots (score.md). Surfr "12 jumps" (no heights)
    2026-09-14 21:06  E2C4  11 puck jumps, best 1.11 m; Surfr 32 jumps, best 9.1 ft, top airtimes 3.8/3.4/3.1 s,
                            rows seen: #1 6.32 ft 3.07 s 66 ft @15m21s, #2 5.74 ft 3.39 s 72 ft @15m50s
