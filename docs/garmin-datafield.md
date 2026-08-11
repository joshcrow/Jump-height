# Garmin wrist companion — deep scope & build spec

**Status:** scaffold built and integrated; **first compile succeeded 2026-08-04**
(SDK 9.2.0, `BUILD SUCCESSFUL`, **24/24 simulator unit tests pass**,
five rounds to green — see [../garmin/FIRST_COMPILE.md](../garmin/FIRST_COMPILE.md)).
**M1 (protocol core, simulator-only) met**; the next gate is on-watch
render/install (M0's real-device AC + M2 live link). Field trial still
follows water validation (Phase 2).
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
  profile, GPS, HR, and we add jumps.
- **Primary target device: Garmin Instinct 3 Solar** (the rider's watch) —
  **API Level 5.1, 176×176 semi-octagon MIP** (confirmed against Garmin's
  compatible-devices list, 2026-07; the clipped-corner "semi-octagon"
  shape means keep content centered, nothing in corners).
  Consequences, binding on the design:
  - Monochrome MIP, 176×176: no dimming exists — staleness is a hollow
    glyph + "reconnecting" label, never a gray tone; the new-jump flash is
    a region INVERT (MIP renders inversion beautifully); fonts chunky,
    layouts from §4.1 verified at this resolution first.
  - Low-memory device class: budget conservative (§5.6), no resources
    beyond strings.
  - MIP Solar is the ideal field display: always-on and MORE readable in
    direct sun. The AMOLED Instinct 3 variant and bigger watches inherit
    the same monochrome-safe design for free.
  - **Fallback if §9.2 fails** (BLE not available to *data fields* on
    Instinct 3): ship the same PuckLink/Protocol/Model/FitOut core as a
    minimal full-screen **device app** ("glance mode" + its own activity
    recording per §8) — BLE is available to device apps wherever the
    module exists at all. The executor switches app type, not design.
  - Store device list beyond Instinct: add Fenix/Epix/Forerunner families
    at M5 after simulator layout passes; Instinct correctness first.
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
    STATS session_jumps=4 session_best_m=1.316 stored_jumps=9 stored_best_m=1.316 trace_bytes=182031 vbat_mv=3870 batt_pct=63 chg=0
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
- Battery adder keys `vbat_mv=`/`batt_pct=`/`chg=` are appended to
  `STATS` (and `INFO`) **only on Sense-class hardware that measures vbat**
  — absent on the ESP32/FireBeetle build by design. The field parses
  `batt_pct`/`chg` (`Model.mc`) and draws a puck-battery glyph
  (`JumpFieldView.mc`); unknown-key tolerance means their absence is a no-op.
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
- Units *(adversarial-review decision)*: write in the **rider's display
  unit**, with the FIT units string set to match ("ft" or "m") — Garmin
  Connect does not convert developer fields, and the person reading the
  activity page is a human, not an archive. The canonical metric record is
  the device's own CSVs, not the FIT file.

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

**M5 — distribution.** Two channels, in order (full detail in §11):
(a) *Sideload channel first*: local release build for the owner's model,
published as a GitHub Release asset, linked from the website with the
per-OS sideload guide; (b) *Connect IQ Store*: developer account, store
checklist (§11.3), submit, respond to review. AC: owner's watch runs a
Release-asset build installed per the guide; store listing approved; the
website's watch section shows the store badge.

**M6 — field trial.** One real water session wearing it, with the beach
phone connected at the same time (requires §7's two-central firmware).
AC: US1-US6 each verified true on the water, or filed as issues.

## 7. Firmware companions (OUR repo)

- **Two concurrent BLE centrals — ✅ DONE (firmware v0.4.2)**, no longer an
  M6 blocker. The puck now accepts two centrals subscribed at once (the app
  re-advertises after each connect while `getConnectedCount() < 2`, capping
  effective use at two — NimBLE's own default max is 3), so the rider's watch
  and the beach phone can read jumps simultaneously — tested watch + phone
  live. A single `notify()` reaches every subscribed client, so the same
  protocol lines fan out to both.
- **Battery telemetry — ✅ DONE end-to-end (2026-08-04)**: the Sense
  `jh_power` seam appends `vbat_mv=`/`batt_pct=`/`chg=` to `INFO`/`STATS`
  (Sense-only adder keys — absent on the ESP32 build by design, §5.2); the
  field parses `batt_pct`/`chg` (`Model.mc`) and draws a puck-battery glyph
  (`JumpFieldView.mc`). Bench-pending only the vbat-to-percent calibration.

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

## 9. Verify at build time — updated after SDK research (2026-07)

**RESOLVED from Garmin's documentation (sources: Toybox.BluetoothLowEnergy
API docs; compatible-devices list):**
- ✅ The BluetoothLowEnergy module explicitly permits **Data Field** apps
  (also Background/Glance/Widget/Watch App). Requires API 3.1+;
  Instinct 3 Solar is **API 5.1**. The BLE-in-datafield gate is OPEN — the
  §5.1 device-app fallback stays documented but is not expected.
- ✅ Pairing "does not persist across application instances" (per
  `pairDevice` docs) — the scan-and-connect-on-every-activity-start design
  isn't just acceptable, it's required. `getBondedDevices` (API 4.2.5+)
  exists on 5.1 as an optional reconnect accelerator.
- ✅ Profile registration limit: 3 per app; we register 1 (NUS).
- ✅ Instinct 3 Solar display: 176×176 semi-octagon (corners clipped).

**Still open (check in M0/M2/M4):**
1. Exact datafield memory limit for Instinct 3 Solar (read from the SDK's
   device files at M0; API 5.1-era limits are expected to be generous —
   budget stays conservative regardless).
2. *(resolved above)*
3. Whether `Attention.vibrate` is permitted from a data field on Instinct 3;
   if not, drop US3 silently (setting hidden; invert-flash is the nudge).
4. Whether the 128-bit NUS service UUID is visible in CIQ scan results on
   target (else match by name from scan response).
5. *(resolved above)*
6. FIT developer-field rendering in Garmin Connect for windsurf-family
   activities (charts render for most sports; confirm on a real save).
7. Store review constraints on the word "Garmin" and on BLE scan duration
   in data fields (respect current guidelines at submit time).
8. Whether the owner's watch mounts as USB mass storage or MTP-only on
   macOS (decides which sideload guide applies — §11.1).
9. BLE delegate callback delivery while the field's data page is not the
   currently visible screen (must keep receiving jumps regardless).
10. Current Connect IQ SDK license terms re: CI builds — until confirmed,
    release artifacts are built locally, never in CI (§11.2).

## 10. Design constraint from sideload reality: DEFAULTS CARRY THE APP

Sideloaded Connect IQ apps cannot receive app settings (settings flow
through Garmin's store plumbing only). Therefore every setting in §3/US7
is polish, never load-bearing: puck name defaults to `JumpHeight`, units
follow the watch, vibration defaults on-if-permitted. A sideloaded field
with zero configuration must satisfy US1-US6 completely.

## 11. Distribution & website integration

### 11.1 Sideloading — the honest per-OS guide (ships in garmin/README.md
and, post-M2, as a short page linked from the web app)

1. Download `JumpField-<device>.prg` for your watch model (Release asset;
   .prg files are built PER DEVICE — installing the wrong model's file
   fails silently. The website picker must make the model choice explicit).
2. Connect the watch by USB.
   - **Windows**: watch appears in Explorer → copy the file into
     `GARMIN/APPS` → eject → restart the watch.
   - **macOS, older watches (USB mass storage)**: same via Finder.
   - **macOS, newer watches (MTP-only — most current models)**: macOS has
     no native MTP; install OpenMTP (free) or Android File Transfer, then
     copy into `GARMIN/APPS`. This friction is exactly why the store
     channel is the real distribution (§11.3).
3. Add the field to an activity screen on the watch: Settings → Activities
   & Apps → (your sport) → Data Screens → add a field → Connect IQ →
   Jump Height. This step gets its own illustrated 5-step guide — install
   ≠ configured, and US1 silently fails without it.
4. Uninstall = delete the file (or via Garmin Express/Connect for store
   installs).

There is NO wireless sideload on this platform; "slick" pre-store
distribution means: one obvious download link per model + this guide.

### 11.2 Release artifacts (pre-store channel)

Built locally (`monkeyc` release build, signed with the project developer
key — key kept out of the repo), attached to a GitHub Release, linked from
the website. Not built in CI until §9.10 clears; never committed to the
repo (same no-binaries rule as firmware).

### 11.3 Connect IQ Store submission checklist

- Developer account on the Connect IQ portal (free).
- Release `.iq` bundle (all supported devices in one upload).
- Listing: name ("Jump Height — Wing Foil Jumps"; must not lead with
  "Garmin"), description linking the repo + web flasher ("works with an
  open-hardware puck you build for ~$20"), what-BLE-is-used-for statement,
  support URL (GitHub issues), privacy statement (no accounts, no network,
  no data leaves the watch except the activity's own FIT file).
- Assets: launcher icon (the wave glyph, per-family sizes), 3+ screenshots
  (simulator captures of §4.1's three layouts).
- Review turnaround is typically days; every update re-reviews. Expect one
  round-trip of reviewer feedback the first time.
- Post-approval: the website's watch section swaps the Release link for
  the store badge; sideload guide remains for DIY forks.

### 11.4 Website touchpoints (small, deliberate)

- **Connect tab, "New device" area** gains a third card after firmware
  install: "On a Garmin watch?" → pre-store: model picker + Release link +
  sideload guide; post-store: the store badge. One card, no new page
  structure.
- The store listing links BACK to the website as the puck's home. The two
  surfaces must agree on one sentence of positioning: *"An open-hardware
  jump tracker for wing foiling — build the puck, wear the watch."*

## 12. Stated position: "why not just use the watch's accelerometer?"

The question everyone asks (store reviewers included), answered once: the
airtime method detects **free-fall**, and a wing rider's wrist never
free-falls — the wing is lifting through the arms for the whole jump, so
the wrist stays loaded while the *board* is the thing that falls. Wing
airs are also short (0.4-1.5 s vs the 3-10 s kite jumps that wrist-only
apps detect). *(Corrected 2026-07-29: this section previously also leaned
on Connect IQ's ~25 Hz accelerometer cap — that cap is gone on newer
watches: Garmin unlocked 100 Hz raw access for Surfr, Instinct 3 Solar
included ([research.md](research.md) §3). The argument stands on physics
alone — a loaded wrist never free-falls; wakeboard wrist trackers work
precisely because those wrists do.)* The division of labor is physics,
not preference: the board wears the sensor; the wrist wears the display.

## 13. Out of scope (explicitly)

- Sending calibration or commands from the watch (phone owns that).
- Watch-side session storage/history (Garmin Connect is the archive).
- Apple Watch (different platform entirely; would be a native iOS project).
- Live trace streaming to the watch (bandwidth + battery + no use case).
