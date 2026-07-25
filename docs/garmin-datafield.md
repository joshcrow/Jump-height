# Garmin wrist companion — deep scope & build spec

**Status:** scoped, not started. Build AFTER water validation (Phase 2).
**Priority:** the DATA FIELD is the product. The custom "Wing Foil activity"
app is a stretch goal with a separate go/no-go decision (see §8).
**Executor note:** this document is written to be decision-complete — an
implementing agent (e.g. Sonnet) should be able to build from it without
re-deriving choices. Where a fact must be confirmed against the SDK or real
hardware, it is listed in §9 "Verify at build time", not silently assumed.

---

## 1. The one-sentence product

After every jump, the rider's next glance at their wrist — within two
seconds, mid-run, no taps, no phone — shows how high it was.

## 2. Why this is small

The puck already broadcasts every jump as a newline-terminated text line
over standard BLE (Nordic UART Service). The Mac CLI and the web app are
clients #1 and #2 of that protocol. The watch is client #3: a BLE central
that subscribes to one characteristic, splits on `\n`, parses `key=value`
pairs, and draws three numbers. No firmware changes are required to ship
the MVP (two optional niceties in §7).

## 3. User stories (ideal, ranked)

**US1 — zero-touch link.** As a rider, I start my normal watch activity
(Windsurf/Kitesurf/whatever) with the jump field configured on a screen,
and it finds my puck by itself within ~10 s of being in range. I never tap
anything. If I walk out of range and back, it reconnects alone.

**US2 — the glance.** As a rider, when I land a jump, my next glance shows
the height HUGE — readable in sun glare, through water drops, in under a
second of attention. Last jump is unmistakably distinct from session best.

**US3 — the nudge.** As a rider, a short wrist vibration right after a
detected jump tells me there's something to glance at. (Setting, default
on; silently skipped if the platform forbids vibration from a data field.)

**US4 — the record.** As a rider, when I save the activity, my jumps are IN
it: Garmin Connect shows total jumps, best height, and a per-jump height
chart on the activity page, next to Garmin's own speed/HR/map. No extra
app, no export ritual.

**US5 — honesty about staleness.** As a rider, if the link is down, the
field says so at a glance (state glyph + dimmed numbers) rather than
showing stale data as if it were live.

**US6 — late join / mid-session restart.** As a rider, if I start the
activity late or the watch reconnects mid-session, the field shows the
session's true count/best (not zeros) within a couple of seconds.

**US7 — owner setup.** As the owner, the only settings that exist are ones
I might actually change: puck name (default `JumpHeight`), vibration
on/off, units auto/ft/m. Everything else just works.

**US8 — stranger with a DIY puck.** As an open-hardware builder who found
the repo, I install the field from the Connect IQ store, flash a puck from
the web page, and the two find each other with zero configuration.

## 4. Interaction design

### 4.1 Field layouts (the field must render in all three)

Full-screen / single-field (the recommended install):

    ┌────────────────────────────┐
    │ ● JumpHeight        3 jumps│   header: state dot + count
    │                            │
    │         4.2 ft             │   LAST JUMP — largest font that fits
    │                            │
    │   best 5.1 ft · air 1.02s  │   footer row
    └────────────────────────────┘

Half-screen (2-up):

    │       4.2 ft         │        last, large
    │  ▲5.1        n3   ●  │        best · count · state dot

Small slot (quarter): `4.2▲5.1` + state dot. Nothing else.

### 4.2 States (exhaustive)

| State        | Dot        | Numbers          | Sub-text            |
|--------------|------------|------------------|---------------------|
| SEARCHING    | hollow     | `--`             | "finding puck"      |
| CONNECTED    | solid      | live             | (none)              |
| RECONNECTING | hollow     | dimmed, retained | "reconnecting"      |
| NO BLE       | ✕          | `--`             | "BLE unavailable"   |

Rules: never blank the screen after first data; dim + mark instead. A new
JUMP inverts (highlights) the last-jump number for ~5 s, then reverts.

### 4.3 Units

Default: follow the watch's system distance/height unit (statute → ft,
metric → m). Setting can force ft or m. Airtime always seconds, 2 decimals.
Heights: ft with 1 decimal, m with 2.

### 4.4 Vibration

One short pulse per detected jump (if permitted for data fields on the
target device — §9). Setting `vibrateOnJump`, default true. Never vibrate
for reconnects or state changes.

## 5. Technical architecture

### 5.1 Platform facts this design stands on

- App type: **data field** (`<iq:application type="datafield">`), because it
  runs inside any native activity — the rider keeps their preferred sport
  profile, GPS, HR, and we add jumps. Minimum SDK: Connect IQ **3.1**
  (BLE-in-datafield era); target devices: modern watches (Fenix 6/7/8,
  Epix 2, Forerunner 2xx/5xx/9xx of ~2019+). Exact device list is set in
  M0 once the owner's model is known.
- Permissions: `BluetoothLowEnergy`, `FitContributor`.
- BLE: `Toybox.BluetoothLowEnergy`. One profile registered:
  - Service `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` (NUS)
  - TX char `6E400003-…` — device notifies; watch subscribes (CCCD).
  - RX char `6E400002-…` — watch writes commands (used once per connect).
- The puck advertises the 128-bit service UUID in the primary packet and
  the name `JumpHeight` in the scan response. Scan filter: match service
  UUID when visible in scan results, else name. Multiple pucks: strongest
  RSSI whose name matches the setting.
- Framing: notifications arrive as MTU-sized chunks of a byte stream;
  reassemble on `\n` exactly like web/app.js does. Lines are ASCII.

### 5.2 Protocol contract (everything the watch parses)

The watch is READ-mostly. It must tolerate unknown lines/keys (skip).

    JUMP n=4 airtime_raw_s=1.021 airtime_s=1.036 height_m=1.316 height_ft=4.3 best_m=1.316
    STATS session_jumps=4 session_best_m=1.316 stored_jumps=9 stored_best_m=1.316 trace_bytes=182031
    READY
    # anything starting with '#' is chatter — ignore
    OK stats / ERR ... — terminators for commands the watch sends

Field usage:
- `JUMP`: display airtime_s (as "air"), height_m/height_ft (display unit
  choice happens watch-side from height_m; height_ft is a convenience),
  `n` (count), `best_m` (session best — the device is the source of truth,
  which makes reconnects free).
- `STATS`: on (re)connect the watch writes `stats\n` to RX once; the reply
  seeds count/best (US6). `session_*` fields only; `stored_*` ignored.
- `STATE recording|idle` exists but the MVP ignores it.

### 5.3 Source layout (new top-level dir; owns its own toolchain)

    garmin/
      README.md              build, simulate, sideload, store-submit guide
      jumpfield/
        manifest.xml         datafield; permissions; device list
        monkey.jungle
        resources/           strings.xml, settings.xml + properties,
                             per-family layouts only if needed (prefer
                             pure-DC drawing, no layout XML)
        source/
          JumpFieldApp.mc    AppBase; wires view + link
          JumpFieldView.mc   DataField subclass: compute()/onUpdate();
                             draws §4.1 layouts by probing obscurity/size
          PuckLink.mc        BLE state machine (below)
          Protocol.mc        line reassembly + key=value parser (pure)
          Model.mc           last/best/count/airtime + staleness + flash
          FitOut.mc          FitContributor fields (below)
          UnitsFmt.mc        ft/m formatting per settings/system
        tests/
          ProtocolTest.mc    Toybox.Test: parser + model, NO BLE needed
          ModelTest.mc

### 5.4 PuckLink state machine

    IDLE → SCANNING → PAIRING → DISCOVERING → SUBSCRIBING → LIVE
      LIVE --disconnect--> SCANNING (retain model, mark stale)
      any  --BLE off----> DEAD (show NO BLE)

- Scan continuously while not LIVE; throttle scan restarts (backoff 5 s →
  15 s cap) to spare battery.
- On LIVE: enable notifications on TX, then write `stats\n` once.
- All BLE callbacks feed bytes to Protocol; Protocol emits parsed maps to
  Model; View reads Model at its own 1 Hz.

### 5.5 FIT enrichment (US4) — developer fields

One developer-data UUID (constant in FitOut.mc). Fields:

| id | name            | type    | scope   | units |
|----|-----------------|---------|---------|-------|
| 0  | jump_height     | float32 | RECORD  | m     |
| 1  | jumps           | uint16  | SESSION | count |
| 2  | best_jump       | float32 | SESSION | m     |
| 3  | best_airtime    | float32 | SESSION | s     |

- RECORD field written once per JUMP (sparse) → per-jump chart in Connect.
- SESSION fields written continuously (cheap) → summary tiles in Connect.
- Canonical unit: meters. Garmin Connect does not convert developer
  fields; we accept metric charts (the wrist display is unit-aware, the
  archive is canonical). Documented user-facing in the store description.

### 5.6 Memory & performance budget

Data fields get the tightest memory class. Rules: no layout XML bloat, no
bitmaps (draw the dot/arrow with DC primitives), strings lean, parser
allocation-light (reuse buffers; Monkey C strings are immutable — split
carefully). Target < 28 KB peak on the smallest supported device; check
with the simulator's memory view in M3. compute() and BLE callbacks must
stay < a few ms — no per-callback allocations in steady state.

## 6. Milestones with acceptance criteria (the build plan)

**M0 — toolchain + target lock.** Install CIQ SDK; confirm owner's watch
model + CIQ version; hello-world data field sideloaded (USB → GARMIN/APPS).
AC: static field renders on the real watch; `garmin/README.md` reproduces
the setup from scratch.

**M1 — protocol core, simulator-only.** Protocol.mc + Model.mc + unit
tests (canned JUMP/STATS/chatter lines, chunked at awkward boundaries).
AC: `connectiq` test run green; no BLE anywhere in the tested code.

**M2 — live link.** PuckLink against the real puck on a desk. AC: toss the
bench box; wrist shows the jump within 2 s. Power-cycle the puck; field
returns to LIVE unaided and count/best survive (stats reseed). 15-minute
soak with zero crashes.

**M3 — interaction polish.** All three layouts, states, flash, vibration
(if permitted), units, settings.xml. AC: §4 matched screen-for-screen;
outdoor glance test passed; memory within budget on smallest target.

**M4 — FIT fields.** Record a real (walk-around) activity with fake tosses.
AC: Garmin Connect shows jumps count, best, and the per-jump chart on the
saved activity.

**M5 — distribution.** Store listing (name: "Jump Height — Wing Foil
Jumps"; category Data Fields), screenshots from sim, description linking
the open-hardware repo + Pages flasher, privacy: no accounts, no network,
BLE only. AC: approved and installable from the store; README updated.

**M6 — field trial.** One real water session wearing it. AC: US1-US6 each
verified true on the water, or filed as issues.

## 7. Optional firmware companions (small, OUR repo, not blockers)

- **Two concurrent BLE centrals** (watch + beach phone): NimBLE config
  bump + re-test; today the second connector waits until the first leaves.
- **Battery telemetry**: add `batt_pct=` to `INFO`/`STATS` once the
  FireBeetle's battery-sense pin is wired/validated; the field would show
  a puck-battery glyph. Needs its own small hardware validation.

## 8. Stretch: the "Wing Foil activity" device app — scope & verdict

What a full Connect IQ device app adds over the data field:
- Its own activity type on the watch list ("Wing Foil" by name; FIT sport
  still records as windsurf-class — there is no wingfoil sport code).
- Full-screen page design (jump page / speed page / timer page) without
  the rider configuring field slots.
- Room for future computed metrics (time-on-foil live, once Tier A
  analytics from the roadmap exist).

What it costs: reimplementing activity mechanics natively handled today
(session start/stop/save semantics, GPS, autopause, laps), a second store
listing, and double the surface to maintain.

**Verdict:** data field first (this spec). Revisit the app only after
time-on-foil exists — that's the first metric a custom page shows that a
native activity page can't. When revisited, PuckLink/Protocol/Model/FitOut
lift unchanged; only the View/App layer is new.

## 9. Verify at build time (known unknowns — check, don't assume)

1. Owner's exact watch model + its CIQ version and datafield memory class.
2. `Toybox.BluetoothLowEnergy` availability *in data fields* on that model
   (device apps have it everywhere BLE exists; datafield support is the
   thing to confirm in the SDK device matrix).
3. Whether `Attention.vibrate` is permitted from a data field on target
   devices; if not, drop US3 silently (setting hidden).
4. Whether the 128-bit NUS service UUID is visible in CIQ scan results on
   target (else match by name from scan response).
5. Pairing persistence: whether pairDevice must rerun per activity start.
6. FIT developer-field rendering in Garmin Connect for windsurf-family
   activities (charts render for most sports; confirm on a real save).
7. Store review constraints on the word "Garmin" and on BLE scan duration
   in data fields (respect current guidelines at submit time).

## 10. Out of scope (explicitly)

- Sending calibration or commands from the watch (phone owns that).
- Watch-side session storage/history (Garmin Connect is the archive).
- Apple Watch (different platform entirely; would be a native iOS project).
- Live trace streaming to the watch (bandwidth + battery + no use case).
