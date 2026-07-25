# Jump Height — Garmin Connect IQ data field

The watch client (spec: [`docs/garmin-datafield.md`](../docs/garmin-datafield.md)).
A data field that finds the puck over BLE, shows the last jump HUGE within a
couple seconds of landing, and writes jumps into the saved activity's FIT
file — no phone, no taps, same protocol the CLI and web app already speak.

Owns its own toolchain (a completely different SDK from the firmware's
PlatformIO/ESP32 one), hence a separate top-level directory. Read
[`FIRST_COMPILE.md`](FIRST_COMPILE.md) before your first build — this code
was authored without access to the Connect IQ SDK (it's a login-gated
download) or a simulator, so every API call was researched as carefully as
possible but not compiler-checked. That file is the punch list for exactly
where a guess might need a one-line fix.

Status: source complete, never yet compiled. You are M0.

---

## M0 — toolchain setup (macOS)

### 1. Install the SDK

Either:
- Download the **Connect IQ SDK Manager** from
  <https://developer.garmin.com/connect-iq/sdk/> and run it, or
- `brew install --cask connectiq-sdk-manager`

First launch: accept the license, then use the SDK Manager to download (a)
the current Connect IQ SDK and (b) the **Instinct 3 Solar** device files.
When it asks, say yes to "use this SDK version as active." The SDK Manager
shows the installed SDK's path in its own preferences/about pane — note it,
you'll need `<sdk>/bin` on your `PATH`. It's typically somewhere under
`~/Library/Application Support/Garmin/ConnectIQ/Sdks/`, but confirm against
what the SDK Manager actually shows you rather than assuming.

```bash
export PATH="$PATH:/path/the/SDK/Manager/showed/you/bin"
```

Add that line to your shell profile once it's confirmed.

### 2. Generate a developer key (once, ever — keep it out of the repo)

Every Connect IQ app is signed with a private key. Losing it means you can
never update an already-installed/published app under the same identity —
generate it once, back it up somewhere durable, and never commit it (same
rule as the firmware's no-secrets-in-repo policy).

```bash
mkdir -p ~/.garmin-ciq   # or wherever you keep it, just not inside this repo
openssl genrsa -out ~/.garmin-ciq/developer_key.pem 4096
openssl pkcs8 -topk8 -inform PEM -outform DER \
    -in ~/.garmin-ciq/developer_key.pem -out ~/.garmin-ciq/developer_key.der -nocrypt
```

`monkeyc` wants the `.der` form (`-y` flag below).

### 3. Build

From `garmin/jumpfield/`:

```bash
cd garmin/jumpfield
mkdir -p bin
monkeyc -f monkey.jungle -d instinct3solar45mm \
    -o bin/JumpField.prg -y ~/.garmin-ciq/developer_key.der -w
```

`-d instinct3solar45mm` — see FIRST_COMPILE.md #1 if this device id is
rejected; `instinct3solar50mm` in manifest.xml is the more likely thing to
need deleting, not this one. `-w` prints compiler warnings (worth reading
on a first build with code this unvalidated).

### 4. Run in the simulator

```bash
connectiq &                                   # launches the simulator; leave it running
monkeydo bin/JumpField.prg instinct3solar45mm
```

The simulator has a data-field preview mode that shows all three field
sizes at once (full/half/small — spec §4.1) — use it for the "three things
to check first" below before anything else.

### 5. Sideload to the real watch (spec §11.1)

The Instinct 3 Solar is MTP-only (most current Garmin models are) — macOS
has no native MTP support:

1. Build a release `.prg` for the device (same command as step 3 — a
   data field sideloads as a single per-device `.prg`, not a multi-device
   `.iq` bundle; `.iq` is for the eventual Connect IQ Store upload, §11.3).
2. Install **OpenMTP** (free) or Android File Transfer.
3. Connect the watch by USB, browse it via OpenMTP, copy `JumpField.prg`
   into `GARMIN/APPS/`.
4. Eject, restart the watch.
5. On the watch: **Settings → Activities & Apps → (your sport) → Data
   Screens → add a field → Connect IQ → Jump Height.** This step is easy to
   forget and US1 silently fails without it — installed ≠ configured.

### 6. Run the unit tests

Protocol.mc and Model.mc are pure logic, tested with zero hardware and zero
BLE state (M1's whole point). Compile with the test flag, then run with it:

```bash
monkeyc -f monkey.jungle -d instinct3solar45mm \
    -o bin/JumpFieldTest.prg -y ~/.garmin-ciq/developer_key.der -t -w
connectiq &                                        # if not already running
monkeydo bin/JumpFieldTest.prg instinct3solar45mm -t
```

Expect a PASS/FAIL summary per `(:test)`-annotated function in
`tests/ProtocolTest.mc` and `tests/ModelTest.mc`. **M1's acceptance
criterion is this run green with zero BLE symbols anywhere in the tested
code** — that's structural (Protocol.mc/Model.mc have no BLE import; the
tests never touch PuckLink.mc), not just a passing assertion count.

---

## Three things to check first in the simulator

1. **All three layouts render without overlap or clipped text** — open the
   data-field preview for full/half/small and eyeball against spec §4.1's
   mockups. The layout-tier breakpoints
   (`JumpFieldView.FULL_MIN_H`/`HALF_MIN_H`) are unverified guesses; nudge
   them once you see real pixel dimensions.
2. **The four §4.2 states are visually distinct at a glance** — fake each
   one (no puck in range = SEARCHING; toggle BLE off in the simulator =
   NO BLE) and confirm the hollow/solid/✕ dot plus sub-text reads clearly
   at arm's length, not just up close in the simulator window.
3. **`onProfileRegister` reaches `STATUS_SUCCESS` and scanning finds a
   puck** — the single spot flagged as FIRST_COMPILE.md's #2 risk (wrong
   Dictionary keys fail silently, not with a compile error). If the field
   sits at "finding puck" forever against a puck you know is advertising,
   this is the first thing to instrument with `System.println()`.

---

## M2 — live link, hardware-in-loop checklist

Spec's M2 acceptance criteria, as a literal checklist against the real
puck on a desk:

- [ ] Toss the bench box (or just power it on in range) — wrist shows the
      jump within 2 seconds of landing.
- [ ] Power-cycle the puck — field returns to LIVE unaided (no button
      press, no re-opening the activity).
- [ ] After that reconnect, count and best height are correct (not reset
      to zero) — confirms the `stats` reseed on reconnect (US6) actually
      round-trips.
- [ ] 15-minute soak with the puck connected and jumping occasionally —
      zero crashes, and the field is still responsive at the end (checks
      for the "no per-callback allocations in steady state" budget from
      spec §5.6 actually holding up, not just compiling).
- [ ] Walk out of BLE range and back — field goes SEARCHING/RECONNECTING
      then LIVE again alone, matching US1's "I never tap anything."
- [ ] While a *different* data screen is showing (not the jump field),
      toss the puck, then switch back — the jump was still captured
      (FIRST_COMPILE.md #12's compute()-keeps-running-off-screen bet).
