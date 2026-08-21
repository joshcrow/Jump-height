# Which puck is mine? — identity design for a two-rider world

Written 2026-08-21, scoping the fix for
[ble-dependability.md](ble-dependability.md) layer 4b: two riders, two pucks,
two watches, each session tracked separately. Owner's framing: *"pay extra
close attention to the UX and I'm not just talking screens."*

Owner also settled a question that simplifies everything: **shore-admin while
someone rides is out of range anyway**, so the second connection is not a
feature to protect — it is a hazard to remove.

---

## 1. The principle

Glue-and-forget, applied to identity: **the rider should never think about
which puck is theirs, and must never be able to silently get it wrong.**

Those two clauses pull in opposite directions, and the resolution is the whole
design: make the common case require *nothing*, make the wrong case
*impossible to miss*, and make correction require *no laptop*.

## 2. Three constraints that kill the obvious designs

Established by reading the SDK and the code, not assumed:

1. **A data field has no input.** It is a plug-in to an activity screen; it
   cannot present a pairing dialog or take a button press. Whatever binds a
   puck to a watch must happen without the rider touching anything.
2. **Sideloaded apps receive no settings.** `properties.xml` defaults are what
   run, so `puckName` cannot be set per rider without the Connect IQ store —
   and both watches would ship identical.
3. **`Application.Storage` IS available to data fields** (SDK App_Types
   matrix, all five app types, since 2.4.0) — **but it is per-app**, so
   `puckglance` (a watch-app, which *does* have input) cannot hand a pairing
   to `jumpfield`. The natural "pair in the app, ride with the field" split is
   not available.

Net: **binding must be automatic, self-correcting, and visible** — because
there is no supported channel for the rider to state their intent.

## 3. The layers, only one of which is a screen

### 3a. Physical — the cheapest and most durable layer

**Label each puck with its own four characters, in paint pen or a label
maker, before it is glued down.** `E2C4`. `45ED`. `8673`.

- The identity is FICR-derived (`DEVICEADDR`, jh_link.cpp:502) — immutable,
  unique, and already advertised. A physical label can never drift out of
  sync with it, unlike a name stored in software.
- It survives a flat battery, a dead puck, a reflash, and a firmware version
  nobody remembers.
- It is what makes every other layer legible: a screen saying `E2C4` means
  nothing unless something on the board also says `E2C4`.

Do this at mount time. It costs a minute and it is the only layer that works
when the electronics are unavailable.

### 3b. Ritual — bind by doing what you already do

The rider's real-world act is *picking up their own board*. The design should
attach to that, not add a step.

**Rule: first successful connection in an activity binds, and is remembered
forever** (`Application.Storage`, keyed to the puck's BLE address).
Thereafter: prefer the bound puck; if it is present, never connect to
anything else, regardless of signal strength.

Consequence in the two-rider case: after each rider's first session, the
binding is correct and stays correct even when both are standing together at
rigging — which is precisely the moment today's strongest-signal rule fails.

### 3c. Defaults — what happens if nobody does anything

- **One puck in range:** connects, binds, never mentions it again. Identical
  to today's experience.
- **Bound puck present:** connects to it. Other pucks ignored entirely.
- **Bound puck absent, another present:** connects to the other one **and says
  so** (see 3d) — because a rider who borrowed a board still wants data, and
  refusing to work would be worse than working visibly.
- **Never bound:** strongest signal, and bind that. The only moment the
  wrong-puck risk exists is a rider's very first session.

### 3d. Noticing — the screen layer, kept small

**Show the puck's four characters in the field header, always.** Not an error
state, not a warning — ambient, like a battery icon.

- Costs one row and no settings channel.
- It closes the FIRST-session risk: rider glances down, sees `8673` where the
  board says `E2C4`, and knows before the session is wasted rather than after.
- It pays for itself outside the two-rider case too — identity confusion has
  produced **four wrong "dead board" verdicts** in this project, including one
  of mine on 2026-08-20.
- **When it changes, say it louder once**: if the field connects to a puck
  that is not the bound one, show the id inverted/boxed for the first minute.
  Not a modal, not a vibration — a thing the eye catches, that then settles.

### 3e. Correction — no laptop, no settings

Because the field has no input, correction must be a *consequence of
behaviour*, not a menu:

- **Re-binding rule: if the field connects to a non-bound puck for two
  consecutive activities, that puck becomes the new bound puck.** Deliberately
  slow. One accidental session on a friend's board does not steal your
  binding; genuinely switching boards does, without you doing anything.
- **Escape hatch that needs no UI:** the puck already answers `name` over
  BLE from the web app or CLI. Anyone with a phone can confirm which puck is
  which in the van. Not the primary path, but it exists.

### 3f. Lending, swapping, and the second board

- **Brother borrows your board for a session:** his watch is bound to his
  puck, which is absent → connects to yours, shows the id, records normally.
  His data, your board, no confusion.
- **You get a second board:** two sessions on the new one re-binds. No action.
- **A puck dies mid-season:** the watch fails to find its bound puck, takes
  the other, shows it. The rider is informed, not blocked.

### 3g. Silence — the part that is easy to get wrong

The system should be **completely silent about identity in the common case**.
No confirmations, no "connected to E2C4" toast, no vibration. The id sits in
the header and is ignored 99 % of the time, exactly like a fuel gauge.

A design that announces itself every session trains the rider to dismiss it,
which is how the one session that mattered gets dismissed too.

## 4. Firmware side: remove the hazard, don't manage it

Owner: shore admin during a ride is out of range anyway. So:

**Set `kMaxPrphConnections = 1` for the rider configuration**
(jh_link.cpp:170, currently 2).

- A puck that accepts one central **cannot be double-booked**, which deletes
  the "both watches grabbed the same puck" failure outright rather than
  detecting it.
- It also deletes the entire class the 2026-08-11 corruption came from: one
  shared TX queue serving two subscribers. Not mitigated — absent.
- Cost: the phone/laptop cannot connect while the watch is connected. In
  practice that means "end the activity before syncing", which is already the
  documented ritual.
- **Keep 2 for a bench/dev build.** The capability stays testable; the
  shipped rider config just does not expose it.

This is the rare fix that makes the system simpler and smaller. Prefer it over
any amount of arbitration logic.

## 5. What this costs

| piece | where | size |
|---|---|---|
| Puck id in the field header | `JumpFieldView.mc` | ~5 lines + a layout row |
| Bind/prefer/re-bind logic | `PuckLink.mc` + `Application.Storage` | ~40 lines |
| Name beats UUID when specific | `PuckLink._matchesPuck` | ~5 lines |
| One central in the rider build | `jh_link.cpp:170` + build flag | ~3 lines |
| Physical labels | a paint pen | one minute |

No store dependency. No settings UI. No new hardware. The expensive part is
the *thinking*, which is now done.

## 6. What I would NOT build

- **A pairing mode triggered by shaking the board.** Cute, uses the
  accelerometer, and indistinguishable from riding — a false trigger mid-
  session would re-bind the wrong puck silently. Rejected.
- **Proximity binding ("hold your watch to the board").** Requires the rider
  to know a ritual, and RSSI at 5 cm vs 1 m is reliable only until it is not.
  The automatic bind already covers it without teaching anyone anything.
- **A settings screen.** Blocked on the store, and it asks the rider to know
  a hex id before they have ever seen one.
- **Correlating watch motion with puck motion** to identify "the board I am
  riding". Genuinely elegant, genuinely over-engineered, and it would fail in
  exactly the flat-water moments when nothing is moving.

## 7. Sequencing

This is **not** water-day work. Nothing here blocks a single rider with a
single puck, which is what the water day is.

- **Now:** label the pucks physically (3a). One minute, and it makes every
  later layer legible.
- **With the watch-side session-delta work (Era 2):** the header id (3d) and
  the bind/prefer logic (3b–3f) — they touch the same files and share a test
  pass.
- **Whenever the rider build is cut:** one central (§4).
- **Never, unless a third rider appears:** anything more clever than the
  above.
