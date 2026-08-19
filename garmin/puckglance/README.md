# Puck Battery — Connect IQ glance

A second, tiny watch app whose only job is to answer **"is the puck charged
enough to go out?"** *before* you go out.

The gap it closes: `garmin/jumpfield/` is a Connect IQ **data field**, and a
data field only runs inside a started activity. It already draws the puck's
battery (the `batt_pct`/`chg` sub-line) — but you cannot see that until you
have started recording, by which point you are rigged and on the beach. A
**glance** runs from the watch face, with nothing started.

It never connects to the puck. Since 2026-08-18 the puck broadcasts
manufacturer data in its BLE scan response — company ID `0xFFFF`, payload
`[batt_pct][flags]`, `flags` bit0 = charging — so the battery is already in
the air. This app scans, filters advertisements whose name starts with
`JumpHeight`, decodes those two bytes, and draws them. No `pairDevice()`, no
`registerProfile()`, no GATT.

Status: **compiles clean for `epix2` at strict typecheck (`monkeyc -l 3`),
SDK 9.2.0, 2026-08-18.** Never run on the watch. See "Proven vs assumed".

---

## The research question: can a glance do BLE at all?

Yes, per the SDK's own API reference. The authoritative citation is the
BluetoothLowEnergy module page, which lists the runtime contexts the module
is available in:

> **App Types and Runtime Contexts:** Audio Content Provider · Background ·
> Data Field · **Glance** · Watch App · Widget
>
> **Requires Permission:** BluetoothLowEnergy
>
> — `<SDK>/doc/Toybox/BluetoothLowEnergy.html`

(`<SDK>` = `~/Library/Application Support/Garmin/ConnectIQ/Sdks/connectiq-sdk-mac-9.2.0-2026-06-09-92a1605b2`.)
The same page's "Supported Devices" list includes *epix™ (Gen 2) / quatix® 7
Sapphire*, which is the bench watch.

**A contradiction worth knowing about.** The older per-app-type matrix in
`<SDK>/doc/docs/Connect_IQ_Basics/App_Types.html` has a row
`Toybox.BluetoothLowEnergy` with the **Data Field** column *blank* and the
Watch Face column ticked — which is backwards on both counts (watch faces
cannot do BLE; `garmin/jumpfield/` is a data field that does, and has been
sideloaded and run). That table has "Glance" nowhere at all, because it
predates glances. Where the two disagree, the per-module
`doc/Toybox/<Module>.html` "App Types and Runtime Contexts" block is the one
that matches observed reality. `docs/garmin-datafield.md` §9 already reached
the same conclusion for the data field case.

### Documented restrictions on glance execution

From `<SDK>/doc/docs/Core_Topics/Glances.html` and
`<SDK>/doc/docs/Connect_IQ_Basics/App_Types.html`:

| Restriction | Source |
|---|---|
| "Glance views run in a limited runtime space, with reduced memory and privileges and **do not accept any input**." | App_Types.html |
| Started "in Glance mode with limited memory allocated (**32KB for most devices**)" — per-device value overrides this, see below | Glances.html |
| Use the `:glance` annotation to select which code is compiled into glance scope, exactly like `:background` | Glances.html; Annotations.html |
| Two lifecycles. **Live UI Update** (devices "that have ample resources"): app stays alive, `WatchUi.requestUpdate()` works. **Background UI Update** (low-memory devices): full `onStart` → `getGlanceView` → `onLayout`/`onShow`/`onUpdate`/`onHide` → `onStop` cycle, output cached to the filesystem, and "calls to `WatchUi.requestUpdate()` will have **no effect**" | Glances.html |
| Update rate "should be kept under 1HZ to provide a better scrolling experience" | Glances.html |
| `GlanceView`'s `dc` "will be bounded by glance area rather than a full screen dc"; **no** `addLayer`/`removeLayer`/`insertLayer`/`clearLayers`; no page control | `doc/Toybox/WatchUi/GlanceView.html` |
| "Most functionality supported in Widget is still supported when running as a Glance… however, developers should focus on making `WatchUi.GlanceView` quick to load and moving CPU intensive work to a Background service." | Glances.html |

The lifecycle split is the one that decides whether a **BLE** glance is
viable, because scan results arrive asynchronously: on a Background-UI-Update
device a scan result that lands after `onUpdate` has already run can never
reach the screen. epix2 is a Live-UI-Update device — from the SDK's own
device file, `~/Library/Application Support/Garmin/ConnectIQ/Devices/epix2/simulator.json`:

```json
"glance": { "cacheUpdate": false, "liveUpdates": true,
            "contentArea": {"x":112,"y":13,"width":274,"height":103},
            "iconArea":    {"x":40,"y":34,"width":60,"height":60} }
```

That is a **per-device** fact. Porting this app to another watch means
re-reading that watch's `simulator.json` first.

### Memory ceiling for a glance on epix2

**65536 bytes (64 KB).** Two independent sources agree:

- `<SDK>/doc/docs/Device_Reference/epix2.html`, "App Type / Memory Limit"
  table: `Glance 65536 — Build as Watch App or Widget`. (Same table:
  Data Field 262144, Watch App 786432, Widget 786432 "Requires 4.x SDK",
  Background 65536, Launcher Icon Size 60 x 60.)
- `~/Library/Application Support/Garmin/ConnectIQ/Devices/epix2/compiler.json`:
  `{"memoryLimit": 65536, "type": "glance"}`.

Note this is **double** the "32KB for most devices" in Glances.html — the
device file wins for a specific device.

Measured against that ceiling, from `monkeyc --build-stats 1`:

```
Data:  Foreground 931 bytes   Glance 817 bytes
Code:  Foreground 1917 bytes  Glance 1897 bytes
```

~2.7 KB of 64 KB. Memory is not the constraint here.

### Is a glance a separate app from the data field?

**Separate app, separate `.prg`, separate project — not optional.** A
manifest contains exactly one `<iq:application>` (`manifest_v3.xsd` inside
`<SDK>/bin/monkeybrains.jar`: `manifestInfo` is an `xs:choice` with
`minOccurs=1 maxOccurs=1` over `application | barrel`), and that element has
a **required single** `type` attribute. `type="datafield"` and a glance-able
app type cannot coexist in one manifest, therefore not in one `.prg`.

What *can* be shared is source, via `monkey.jungle` source paths. This
project deliberately does not share `jumpfield/source/` — see
`monkey.jungle`'s header for why (that code is built around a connected NUS
stream; this app is scan-only).

### Which app type carries the glance?

`type="watch-app"`. Three findings, all measured:

1. The XML spelling is hyphenated. `type="watchApp"` (the JSON spelling) is
   rejected outright:
   `ERROR: Could not read manifest file '…': Unknown app type: watchApp`.
   The full XML set, read from `AppTypeManager$AppType` in
   `<SDK>/bin/monkeybrains.jar`: `audio-content-provider-app`, `background`,
   `datafield`, `glance`, `watch-app`, `watchface`, `widget`, `barrel`.
2. `type="widget"` **also compiles** for epix2 and produced a byte-identical
   `.prg`. So the compiler does not enforce the device's app-type list.
3. The device does. `epix2/compiler.json` `appTypes` is
   `audioContentProvider, background, datafield, glance, watchApp,
   watchFace` — **no `widget`**. epix2 runs Connect IQ 5.2.0 (same file,
   `partNumbers[].connectIQVersion`), where the widget carousel is gone and
   a glance-able mini-app is a watch app implementing `getGlanceView()`.

So `watch-app` is the choice, on the device file's authority rather than the
compiler's. If a sideload shows no glance row, flipping that one attribute to
`widget` is the first thing to try.

`minApiLevel="4.0.0"`: `GlanceView`/`getGlanceView()` exist from 3.1.0, but
Glances.html says "In API level 4.0.0 and above, apps and widgets **must**
implement a glance view to appear in the glance list" — 4.0.0 is where the
behaviour this app depends on is the documented one.

---

## The `:glance` scope trap

This cost the most time and is the thing most likely to bite the next person.
The compiler explains it itself, as a warning:

```
WARNING: epix2: The entry point '$.PuckGlanceApp' was implicitly added to
the glance process since the app contains declarations annotated with
(:glance).
```

The **entry class goes into glance scope whole** — every method, including
`getInitialView()`. So every symbol `getInitialView()` names must also exist
in glance scope, which is why `PuckDetailView` and `PuckDetailDelegate` carry
`(:glance)` despite never being drawn by a glance. Without it:

```
ERROR: epix2: …/PuckGlanceApp.mc:72,8: Value 'PuckDetailView' not available
in all function scopes.
```

**But you only see that at `-l 3`.** At the default typecheck level a
deliberate `new PuckDetailView()` planted inside `PuckGlanceView` produced
`BUILD SUCCESSFUL`, and `--build-stats` showed the glance code section grow
by 29 bytes — the size of the call, not of the class it named. A default
`BUILD SUCCESSFUL` is **not** evidence that glance-scope symbols resolve.
Always build this project with `-l 3`.

The annotation is load-bearing, not documentation: removing every `(:glance)`
collapses the glance image from 1897 B code / 817 B data to **114 B / 223 B**.

The SDK ships **no glance sample** — `grep -ril glance <SDK>/samples` returns
nothing. The BLE samples that do exist (`NordicThingy52`,
`NordicThingy52CoinCollector`) are connect-and-GATT apps, useful for the
`ScanResult` iteration shape and nothing else.

---

## How to build and sideload

```sh
export PATH="/opt/homebrew/opt/openjdk/bin:$HOME/Library/Application Support/Garmin/ConnectIQ/Sdks/connectiq-sdk-mac-9.2.0-2026-06-09-92a1605b2/bin:$PATH"
cd /Users/joshcrow/Jump-height/garmin/puckglance
monkeyc -f monkey.jungle -d epix2 -o bin/PuckGlance.prg \
        -y ~/.garmin-ciq/developer_key.der -w -l 3 --build-stats 1
```

`-l 3` is not optional — see the scope trap above. The JDK must be
**prepended** to `PATH`, not appended (`garmin/README.md`'s toolchain note).

Sideload is identical to the data field's: copy `bin/PuckGlance.prg` into
`GARMIN/APPS/` over MTP (OpenMTP, or the `mtp-*` route in
`garmin/README.md`), eject, restart the watch. Then — and this is the step
with no data-field equivalent — **add it to the glance loop on the watch**:
`Settings → Glances → Add Glance → Puck Battery`. Installed ≠ in the loop.

---

## Proven vs assumed

**Proven** (a command was run and this was its output):

- `BUILD SUCCESSFUL` for `-d epix2` at `-l 3` (strict typecheck), SDK 9.2.0,
  2026-08-18, zero warnings.
- Glance scope is real and separately sized: 1897 B code / 817 B data,
  against a 64 KB device limit.
- `(:glance)` is what populates that scope (114 B / 223 B without it).
- The entry class is added to glance scope implicitly and whole (compiler
  warning quoted above).
- `type="watchApp"` is rejected by the manifest reader; `watch-app` and
  `widget` both build for epix2, byte-identically.
- `Ble.setDelegate(null)` does **not** typecheck, despite the prose docs
  saying null deregisters:
  `ERROR: Invalid 'Null' passed as parameter 1 of type
  '$.Toybox.BluetoothLowEnergy.BleDelegate'.`
- `monkeydo bin/PuckGlance.prg epix2` loaded the `.prg` into the simulator
  and ran ~30 s with no error output. That is *loaded without crashing*, and
  nothing more — see below.

**Assumed / unverified** (do not treat any of these as facts):

1. **Nothing has been seen on a screen.** The simulator run produced no
   output to read and no glance row was observed. The glance layout
   (two stacked lines in a 274×103 area) is arithmetic, not a screenshot.
2. **BLE has never run in this app.** Simulator BLE requires an nRF52 DK
   wired in and its COM port set under Settings → BLE Settings
   (`<SDK>/doc/docs/Core_Topics/Bluetooth_Low_Energy.html`); that hardware
   was not used. So `setScanState` succeeding from *glance scope
   specifically* is documented, not demonstrated.
3. **The manufacturer-data byte layout returned by
   `getManufacturerSpecificData(0xFFFF)`.** The SDK documents
   `getManufacturerSpecificDataIterator()` as yielding dictionaries with
   separate `:companyId` and `:data` keys, which implies the singular getter
   strips the two company-ID bytes — but it never says so. `PuckScan.decodeBattery()`
   therefore accepts both shapes, disambiguating on the fact that a battery
   percentage can never be `0xFF`. If the watch shows a wrong number, that
   function is where to look.
4. **That the scan response is captured at all.** The puck puts its name and
   its `0xFFFF` payload in the **scan response**, and only an *active* scan
   receives scan responses. Connect IQ does not document whether its scanner
   is active or passive, and the 2026-08-18 hardware check is recorded as "a
   scan with no connection" without saying which kind it was. Circumstantial
   support that this is fine: `ScanResult.getDeviceName()` is a documented
   API, and `docs/garmin-datafield.md` §5.1 already has the data field
   name-matching against the scan response. If the glance sits on
   "searching" forever next to a puck you know is advertising, this is
   suspect number one.
5. **That a `watch-app`-typed `.prg` actually appears in epix2's glance
   list.** Documented, not observed. See the `widget` fallback above.
6. **Coexistence with the data field.** Both apps want the BLE central. This
   app stops scanning in `onHide()`, but what happens if the glance is opened
   *during* an activity with jumpfield connected is untested — the plausible
   failure is one of them being denied the radio. Test this before trusting
   either.

---

## Files

| File | What it is |
|---|---|
| `manifest.xml` | `watch-app`, epix2, `BluetoothLowEnergy` permission |
| `monkey.jungle` | build recipe; why it does *not* include `jumpfield/source` |
| `source/PuckGlanceApp.mc` | `AppBase`; `getGlanceView()` + `getInitialView()`; the scope-trap writeup |
| `source/PuckGlanceView.mc` | `(:glance)` `GlanceView` — the row itself |
| `source/PuckScan.mc` | `(:glance)` scan-only `BleDelegate` + advert decode; no WatchUi dependency |
| `source/PuckDetailView.mc` | full-screen view behind the row (and why it is `(:glance)` anyway) |
| `resources/` | app name string; generated 60×60 launcher icon |
