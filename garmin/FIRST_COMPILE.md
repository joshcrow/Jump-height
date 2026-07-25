# First-compile checklist

This code was written without access to the Connect IQ SDK (login-gated
download, not available in the authoring environment) or a real device/
simulator. Every API used below was checked as carefully as possible —
mostly against Garmin's live `developer.garmin.com/connect-iq/api-docs/`
pages, plus several real open-source manifest/jungle/settings files on
GitHub — but "checked against docs" is not "checked against the compiler."
This file lists every spot where a wrong guess is plausible, in rough
priority order, each with exactly what to look at and how to fix it fast.

Read this BEFORE the first `monkeyc` run, then work top to bottom as
compile errors (or simulator misbehavior) surface. Most entries below are
isolated to one function — a wrong guess should be a one-line fix, not a
redesign.

**General note on typing:** this code uses light Monkey C type annotations
(`as Number`, `as String`, etc.) opportunistically, matching current
Garmin sample style, but was never run through the type checker. If
`monkeyc` reports type-check *warnings* (not errors) about implicit `Any`
or mismatched types, that's expected and not blocking — tighten
annotations opportunistically, but a warning-only build is a successful
first compile.

---

## 1. `instinct3solar50mm` may not be a real, separate device id

**File:** `garmin/jumpfield/manifest.xml:29` (also noted in a comment at line 23)

The task that produced this tree specified two product ids,
`instinct3solar45mm` and `instinct3solar50mm`. Research during authoring
found:
- `instinct3solar45mm` is real and confirmed (used in multiple real-world
  manifests on GitHub, e.g. TrainAsONE and Barcode-Wallet's Connect IQ
  projects).
- Garmin's own compatible-devices list shows **one row** — "Instinct® 3
  Solar 45mm / 50mm" — at **one** API level (5.1), which is how Garmin
  lists device families that share one Connect IQ target (compare
  `instinct2` vs `instinct2s`, which — unlike the two Instinct 3 Solar case
  sizes — really do have different screen resolutions and so really are two
  ids).
- A Garmin forum bug report ("Connect IQ Store does not add Instinct 3
  Solar 50mm product ID to manifest") is consistent with there being no
  separate `instinct3solar50mm` id at all — the store checkbox may simply
  not correspond to anything.

Both ids are still in `manifest.xml` because that's what was asked for
and it's plausible Garmin did split them — but this is the single most
likely reason the very first `monkeyc` invocation fails.

**Fix if the compiler rejects it:** delete the
`<iq:product id="instinct3solar50mm"/>` line and rebuild with
`instinct3solar45mm` alone. Confirm either way with
`monkeyc --help` (device list) or the SDK Manager's device picker, or by
grepping the installed SDK's `bin/devices.xml` for `instinct3solar`.

## 2. BLE profile registration uses Symbol-keyed Dictionaries — wrong keys fail silently, not loudly

**File:** `garmin/jumpfield/source/PuckLink.mc:112-118`

```
var profile = {
    :uuid => _svcUuid,
    :characteristics => [
        { :uuid => _txUuid, :descriptors => [ Ble.cccdUuid() ] },
        { :uuid => _rxUuid }
    ]
};
Ble.registerProfile(profile);
```

The confirmed API doc shape is
`registerProfile(profile: {uuid: Uuid, characteristics: Array<{uuid: Uuid,
descriptors: Array<Uuid>}>})`, and Symbol keys (`:uuid`, not `"uuid"`)
match every other fixed-shape options Dictionary Garmin's BLE/FitContributor
APIs use (`:writeType`, `:mesgType`, `:units`). This is a reasonable,
well-grounded guess — but because Dictionaries are dynamically keyed, a
**wrong key name is not a compile error**. It would just mean
`registerProfile` silently gets an empty/wrong profile, `onProfileRegister`
either never fires as SUCCESS or fires but scanning never finds the puck,
and the field sits in SEARCHING forever with no error message.

**How to verify fast in the simulator:** run the field against the real
puck (or a BLE-capable simulator session) and confirm, in order: (a)
`onProfileRegister` is called with `Ble.STATUS_SUCCESS`, (b)
`onScanResults` actually delivers `ScanResult`s, (c) the state reaches
LIVE within a few seconds of the puck being in range. If it hangs at
SEARCHING, add a temporary `System.println()` in `onProfileRegister` and
`onScanResults` to see where the chain breaks, and re-check the key names
against the SDK's own `Toybox/BluetoothLowEnergy.html` doc page (installed
locally, not the live site) — or check a real open-source Connect IQ BLE
project for its literal profile dictionary.

## 3. `StringUtil.utf8ArrayToString()` called with a `ByteArray`, not an `Array<Number>`

**File:** `garmin/jumpfield/source/PuckLink.mc:352`

The confirmed signature is `utf8ArrayToString(utf8Array as
Array<Lang.Number>) as String`, but `onCharacteristicChanged` delivers a
`Lang.ByteArray`, not a `Lang.Array`. This is believed to work directly —
it's the standard idiom for decoding BLE UART-style text in Connect IQ —
but the doc's parameter type is technically different from what's passed.

**Fix if it errors or throws at runtime:** the fallback is a manual
byte-to-string loop, something like:
```
var s = "";
for (var i = 0; i < value.size(); i += 1) {
    s += value[i].toChar().toString();
}
```
(verify `Lang.Number.toChar()` exists — untested here) or try
`StringUtil.convertEncodedString(value, {:fromRepresentation =>
StringUtil.REPRESENTATION_BYTE_ARRAY, :toRepresentation =>
StringUtil.REPRESENTATION_STRING_PLAIN_TEXT})` (exact constant names for
`convertEncodedString`'s representation options were not confirmed either
— check the SDK's `Toybox/StringUtil.html` doc page).

## 4. Byte array literal syntax `[0x01, 0x00]b`

**File:** `garmin/jumpfield/source/PuckLink.mc:43, 47`

`CCCD_ENABLE` and `STATS_CMD` are written as Monkey C byte-array literals
(`[ ... ]b`). This is corroborated by an independent source describing
exactly this CCCD-enable use case, but was not confirmed against the
primary Monkey C language reference (that page did not render for this
research — see "Pages that would not render" below).

**Fix if it's a syntax error:** check `developer.garmin.com/connect-iq/
monkey-c/` (the Monkey C language guide, not the API docs) for the actual
literal syntax, or replace with `new [N]b` + indexed assignment:
```
var cccdEnable = new [2]b;
cccdEnable[0] = 0x01;
cccdEnable[1] = 0x00;
```

## 5. `DataField.Obscurity` constant names (`OBSCURE_TOP` etc.)

**File:** `garmin/jumpfield/source/JumpFieldView.mc:324-327`

Used to inset drawing away from a clipped corner on the Instinct's
semi-octagon display. Corroborated by two independent forum/search hits
naming `OBSCURE_TOP`/`OBSCURE_BOTTOM`/`OBSCURE_LEFT`/`OBSCURE_RIGHT`, but
not from a primary doc fetch (the `WatchUi/DataField/Obscurity.html` page
404'd during research).

**Blast radius if wrong:** small and isolated — only `_edgeInsets()` in
JumpFieldView.mc uses these names; the full/half/small layout-tier
selection (the more important probe) uses `dc.getWidth()`/`getHeight()`
and does not depend on them at all. If the compiler rejects the constant
names, check the SDK's local `Toybox/WatchUi/DataField.html` doc for the
real names (or grep its `api.debug.xml`), fix `_edgeInsets()`, done.

## 6. Parent-class constructor call convention

**Files:** `PuckLink.mc:82` (`Ble.BleDelegate.initialize()`),
`JumpFieldView.mc:54` (`DataField.initialize()`),
`JumpFieldApp.mc:16` (`AppBase.initialize()`)

Monkey C has no `super` keyword (confirmed); the convention used here is
calling the parent class's `initialize()` by its own class name. This is
believed correct and is a common idiom, but was not verified against a
primary doc example.

**Fix if wrong:** compare against any freshly-generated Connect IQ project
stub (Visual Studio Code's "Monkey C: New Project" command generates a
`YourAppNameApp.mc`/`YourAppNameView.mc` pair with this exact pattern
filled in) — the fix is mechanical, same call shape in three files.

## 7. `instanceof` inside a `catch` clause

**Files:** every `catch (ex instanceof Lang.Exception) { ... }` block
(PuckLink.mc, JumpFieldView.mc)

Confirmed via a real forum code sample (`catch (e instanceof
Lang.Exception) { System.println(e.getErrorMessage()); }`), so this is
higher-confidence than most entries here — listed for completeness, not
because it's especially suspect. If it errors, the fallback is a bare
`catch (ex) { ... }` (catch-all without the type clause), which should be
uncontroversially valid regardless.

## 8. `launcherIcon` omitted from manifest.xml

**File:** `garmin/jumpfield/manifest.xml`

Real sample manifests always show a `launcherIcon="@Drawables.LauncherIcon"`
attribute, but that sample was a **watchface**; data fields are believed not
to need one (they're picked from a list by name, not launched from an
icon grid) since spec §5.6 also asks for zero bitmap resources. If the
compiler demands one, either add a minimal 1-bit drawable + the attribute,
or check a real sideloadable data field's manifest for confirmation it's
genuinely optional.

## 9. Resource XML root elements

**Files:** `resources/strings/strings.xml`, `resources/settings/
properties.xml`, `resources/settings/settings.xml`

Written with each file's own tag as its root (`<strings>`, `<properties>`,
`<settings>`), matching the "one resource type per file" convention seen
in most Connect IQ project templates. One real example found during
research combined `<properties>` and `<settings>` inside one shared
`<resources>` wrapper instead. If the resource compiler complains about a
missing `<resources>` wrapper, add it — two extra lines per file.

## 10. `settingConfig type="alphaNumeric" maxLength="20"`

**File:** `garmin/jumpfield/resources/settings/settings.xml`

Used for the `puckName` setting. `alphaNumeric` as a settingConfig type is
corroborated by a search snippet; the `maxLength` attribute is a reasonable
but unconfirmed guess (matches BLE device-name length limits, which is why
20 was chosen). If invalid, drop the attribute (the type alone should still
compile) or check the Settings reference for the real bound-length
mechanism.

## 11. FitContributor "developer-data UUID" (spec §5.5) has no home in the confirmed API

**File:** `garmin/jumpfield/source/FitOut.mc` (`DEVELOPER_DATA_ID` const)

The build spec asks for "one developer-data UUID (constant in FitOut.mc)".
The CONFIRMED `DataField.createField(name, fieldId, type, options)`
signature — verified against the live API docs — takes no UUID parameter;
Connect IQ appears to tie a data field's developer fields to the app's own
manifest identity automatically. Rather than invent a fictional API call
to satisfy the letter of that spec bullet (which would be a guaranteed
compile error), `DEVELOPER_DATA_ID` is kept as a documentation-only
constant matching `manifest.xml`'s app id. If SDK exploration turns up a
real per-field UUID hook this research missed, wire it in and delete this
note.

## 12. Two behavioral assumptions worth confirming on real hardware (M2)

These aren't API-signature risks — they're design bets made where the
spec was intentionally open (§9) or silent, called out here so they get
checked deliberately rather than discovered by surprise:

- **`compute()` is assumed to keep running (~1 Hz) even when this field's
  screen is not the one currently visible**, which is why `PuckLink.poll()`,
  the vibrate trigger, and the FIT writes are all driven from `compute()`
  in JumpFieldView.mc rather than `onUpdate()`. This is spec's own open
  item §9.9 ("BLE delegate callback delivery while the field's data page
  is not the currently visible screen"). **Test:** put the field on data
  screen 2, leave screen 1 showing, toss the bench box, confirm the jump
  count/FIT session fields still update; flip back to screen 2 and confirm
  the retained numbers are there.
- **The 5s→15s scan-restart backoff (spec §5.4) is implemented as "back off
  after a failed connect/disconnect, reset to 5s floor on a healthy LIVE"**,
  not as a routine on/off scan duty-cycle while otherwise healthy (i.e.
  scanning runs continuously and uninterrupted while searching normally).
  Spec's wording ("throttle scan restarts... to spare battery") could
  support either reading. Revisit after the M2 15-minute soak test's actual
  battery draw is known — if it's worse than expected, a real duty-cycle
  (turn scanning off for N seconds even between failures) is the next thing
  to try.

## 13. Deliberate glyph substitutions (not bugs)

`JumpFieldView.mc` uses `"^"` where spec §4.1's mockup shows `"▲"`, and `"."`
where the mockup implies a middle-dot separator. This is a deliberate,
conservative choice: Garmin's system fonts on a monochrome MIP display are
not confirmed to include arbitrary Unicode glyphs (this matters far more
for `▲`, U+25B2 Geometric Shapes, than for the middle dot, which is at
least Latin-1), and a missing glyph renders as a blank box or nothing,
which is worse than a plain ASCII character. Once the simulator confirms
font support, swapping back is a one-line change per spot (`grep -n
'"\^"' source/JumpFieldView.mc`).

## 14. Float equality in unit tests

**Files:** `tests/ModelTest.mc`, `tests/ProtocolTest.mc`

Several tests do `Test.assertEqual(m.lastHeightM(), 1.316)` — comparing a
`String.toFloat()`-parsed value against a Float literal. These should
match exactly (both ultimately parse the same decimal text), but if one
ever fails by a tiny margin with no logic bug in sight, that's a
floating-point precision artifact, not a real failure — switch that one
assertion to an epsilon comparison (`(a - b).abs() < 0.0001`) rather than
chasing it further.

## Pages that would not render during research (informational)

`developer.garmin.com`'s `/connect-iq/core-topics/*` and
`/connect-iq/reference-guides/*` sections appear to be client-rendered —
every fetch of those URLs returned only the site's navigation shell, never
the actual guide content (manifest schema reference, Jungle reference,
BLE pairing walkthrough, Monkey C language reference, unit-testing guide).
Everything in this codebase that would normally cite one of those pages
instead cites the `/connect-iq/api-docs/Toybox/...` pages (which render
fine and were fetched directly), real open-source project files on GitHub,
or Garmin forum threads found via search. If something above is still
unresolved, those `core-topics`/`reference-guides` pages are the next
place to look — just visit them in an actual browser rather than an
automated fetch.
