# JumpHeight Sync — the Mac agent. Spec and build plan (2026-09-13)

<!-- PENDING WORK. This is the contract the build agents implement against. Delete
     or fold into STATUS.md / rider-brief.md when v1 ships. -->

## The whole product, from Nick's side

Plug the puck in to charge. Nothing else, ever.

Four notifications exist and no others:
    Ride synced · 12 jumps        (or "Ride synced · no jumps")
    Puck charged
    Puck updated
    Needs you: <one line>          body: the single action. Exactly three of these exist:
        check the puck             Press the small button on the puck twice.      (anything the puck got wrong)
        reconnect Google Drive     Open Set up in the menu bar.                    (remote gone, or a ride waited a day)
        sign in to Garmin again    Open Set up in the menu bar.                    (only if he ever signed in)

A puck with no ride on it produces nothing at all. He charges it every night.

A menu-bar icon shows state at a glance. Four glyph states and no more (the
five-state collapse from the ambient-UI report, minus "paused", which this app
does not have), each a redraw of the wing, never a badge:
    dormant    wing at 35 %        no puck attached           (environment, not the app)
    idle       wing                puck attached, up to date
    working    wing + water line   a job is running; the panel names the phase in words
    attention  wing + dot          Nick needs to do one thing; reserved ruthlessly
Click it:
    Puck 86% · charging            (or: No puck · Reading the puck… · Uploading… · Emptying the puck… · Updating the puck…)
    Last ride Tue 4:52 pm · 12 jumps
    Open rides folder
    Set up…
    Quit

There is no window after setup. Nothing to remember. No steps.

## Design rules (NNG heuristics, applied)
- Status is visible without being asked for (icon + one notification per event), never chatty.
- Words are Nick's: ride, puck, watch, charged. Never bundle, trace, verify, sync, seam, src.
- Errors are prevented by gates, not explained afterwards. When something does need him,
  ONE notification, ONE action, always phrased the same way.
- Recognition over recall: every screen has one button and states its result ("Connected as …").
- Minimal copy: if a sentence does not change what he does, it is not there.
- Nothing destructive is ever automatic UNLESS the safer copy already exists elsewhere and
  has been confirmed: the puck is emptied only after the file is confirmed on Drive.

## Setup — three screens, one button each, run once. A native window; opens by itself the first time.
(Was a local web page; replaced 2026-09-13 after the owner's first install: it could not open itself, cost a click, and ended on rclone's page.)
1  JumpHeight                          Plug the puck in to charge. Rides upload themselves.   [Continue]
2  Rides go to Google Drive            [Connect]  → browser consent → back automatically → "Connected as nick@…"  [Continue]
3  Your watch                          [email] [password] [Sign in]   ·  Skip     (+ a code field only if Garmin asks)  → "Signed in"  [Continue]
4  Done. Plug the puck in whenever.    [Close]        (the macOS notification prompt fires here; no copy of ours)
Re-runnable from the menu bar; each step individually. Token expiry → "Needs you: sign in to Garmin again" opens step 3 alone.

## What the agent does when the puck appears (the job)
  0. stats. stored_jumps=0 AND trace_bytes=0 → no ride: skip to step 8 (that reading is G2's "empty"), say nothing
  1. open the port; info, stats                     (tools/jump's Device — proven serial layer)
  2. jumps; traceraw → falls back to trace           (page's exact command order, CONTRACT.md §3.2)
  3. stats again; selftest
  4. verify exactly as the page does                 (verifyPull rules: F-22 band, trace_bytes_after window,
                                                      header-only, jump-row cross-check — reuse, do not reimplement)
  5. write the bundle to ~/Library/Application Support/JumpHeight/spool/<name>.zip
                                                     (CONTRACT.md §2 manifest, so ./tools/jump ingest is unchanged)
  6. rclone copy → gdrive:JumpHeight/inbox/ ; confirm with rclone lsjson that remote size == local size
     not confirmed → the bundle stays in the spool root and is retried every 10 min, silently; the puck is not cleared
     confirmed → the bundle moves to spool/sent/
  7. ONLY THEN: clear; confirm with stats (stored_jumps=0, trace_bytes=0); tracecheck where available
     confirmed → "Ride synced · N jumps"  (synced = safe on Drive AND off the puck, in that order)
  8. fetch <site>/firmware/latest.json; if puck src != latest.src AND puck is empty: flash
        send uf2 → wait for /Volumes/XIAO-SENSE (mount via diskutil if present-but-unmounted — measured 2026-09-11)
        → copy the .uf2 (a "Device not configured" error on the copy is the SUCCESS signature)
        → wait for the port to return → info → src must equal latest.src → "Puck updated"
  9. (the notification fired at 7; a flash gets its own "Puck updated")
 10. while attached: stats every 60 s → menu-bar %; notify "Puck charged" once when chg goes 1→0 with batt_pct ≥ 95
Garmin: on every job and every 6 h — garth: list activities since last_seen, download ORIGINAL FIT zips,
        rclone copy to gdrive:JumpHeight/fits/. Never blocks the puck job. Strava OAuth is the v2 fallback.

## Gates (the only things that may ever be adversarially reviewed as "unsafe")
  G1  never clear unless: verified AND remote size == local size on Drive
  G2  never flash unless: puck empty (stored_jumps=0, trace_bytes=0) AND src older than latest.json AND sha256 of the .uf2 matches latest.json
  G3  a job interrupted anywhere leaves the puck recoverable: nothing writes the bootloader; the spool keeps the bundle; the next plug-in retries
  G4  a reading that did not happen is a failure: no stats → no clear; no lsjson → no clear; no src after flash → "Needs you"
  G5  the port is opened by exactly one process; the web page and the agent never fight (agent releases the port when idle)

## Layout
  tools/puckd/                      Python 3.11+, one package
    serial_job.py    steps 0–5, 7      tests: tools/fake_device.py (knobs: --trace-bytes-overreport, --tracecheck-slow-delta,
                                        --tracecheck-silent, --no-traceraw, --traceraw-error; fillstore is a firmware command, not a knob)
    upload.py        rclone wrapper    tests: a fake rclone on PATH
    garmin.py        garth wrapper     tests: recorded fixtures; no live Garmin in CI
    flash.py         step 8            tests: host harness for the sequencing; silicon rehearsal by the owner
    notify.py        3 notifications   osascript / UNUserNotification via pyobjc
    menubar.py       rumps
    setup/           the four screens: static HTML + a tiny local handler; Playwright tests
    daemon.py        the loop + state machine composing the above; LaunchAgent at
                     ~/Library/LaunchAgents/com.jumpheight.puckd.plist
  packaging/         py2app, universal2 (python.org universal Python), rclone universal binary bundled,
                     .dmg; MUST be verified under Rosetta (arch -x86_64) on the M3 before it goes to Nick

## Build plan — ultracode, Sonnet for the well-specified pieces, Opus only where judgement is load-bearing
  P0  (owner)   freeze this spec; every string above is final
  P1  (Sonnet, parallel, 6 agents)   serial_job · upload · garmin · flash · notify+menubar · setup
        each: implement against the contract above, tests included, tests must fail on revert
  P2  (Sonnet)  daemon.py composing P1; end-to-end test with fake device + fake rclone
  P3  (Opus)    adversarial review of G1–G5 with mutation checks; full suite; no verification logic reimplemented
                DONE 2026-09-13: three fixes in serial_job (chatter counted as data, spool clobber, G3/G5 untested);
                the owner's rulings on its findings are the rules written above and tools/tests/test_puckd_rulings.py
  P4  (owner)   bench rehearsal on the Puck: plug in → job → Drive test folder → empty → flash → "Puck charged"
  P5  (Sonnet)  packaging + Rosetta verification + a one-paragraph install note for Josh
  P6  (Nick)    right-click → Open, four screens, done

## Known friction, not designable away
  unsigned app: first open is right-click → Open (a $99/yr Apple signature removes this)
  macOS "background items added" notice on first run
  Garmin two-factor: one code, once
  the Garmin route is unofficial (garth); it has broken and been fixed before — Strava is the fallback
