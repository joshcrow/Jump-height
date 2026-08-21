# BLE dependability — making the link bulletproof and the UX seamless

Written 2026-08-14, after a code read found a silent data-loss path in
the transmit queue and after the two-central corruption bug had spent
three days without a root cause.

**The standard this is held to:** the rider glances at their wrist
mid-session and sees the truth. Not a stale number, not a plausible
wrong number, and never a number the puck never sent. If the link is
down, the watch says so. Nothing is ever lost, because the puck is the
source of truth and the watch is a view of it.

---

## 1. The defect that started this (confirmed by code read)

`firmware/src/platform/nrf52/jh_link.cpp:298`:

```cpp
for (uint8_t i = 0; i < n_subs; ++i) s_bleuart.write(subs[i], buf, n);
s_txq_tail = (s_txq_tail + n) % TX_CAP;   // advances no matter what
```

`BLEUart::write(conn_hdl, …)` returns `len` on success and **`0` when the
notify fails** (`BLEUart.cpp:254` — `_txd.notify(...) ? len : 0`), which
happens when the SoftDevice has no free TX buffers. **We ignore that
return and advance the queue tail unconditionally**, so a rejected chunk
is discarded — silently, and per-connection.

That is exactly the reported watch symptom: bytes vanish mid-line, the
other central sees clean lines at the same moment, the puck's own state
is correct, and `LineReader` glues the surviving fragments into a `JUMP`
that still parses but carries wrong numbers.

**Why two centrals made it visible but are not the cause:** two
subscribers double the demand on one shared buffer pool at the same
pacing, so a failed notify becomes far more likely. One watch during a
busy session (50 Hz trace + jump lines) can exhaust the same buffers.
**This is a single-central product risk, not a two-client nicety.**

## 2. Failure taxonomy — the six layers, and where we actually stand

| Layer | Question | Today |
|---|---|---|
| 1. Byte integrity | do the bytes we queue arrive? | **BROKEN** — §1 |
| 2. Line integrity | if bytes are lost, can we TELL? | **NO** — no checksum, no per-line sequence |
| 3. Session continuity | what happens across a dropout? | partial — live data dropped, storage retains, reseed on reconnect |
| 4. Discovery & pairing | does it just connect? | works; no bonding; no multi-puck story |
| 5. Observability | can the user tell link state from device state? | **NO** — one "no BLE" appearance for every cause |
| 6. Policy | two centrals? authenticated `dfu`? | undecided / `dfu` is unauthenticated |

### Layer 2 is the one that turns a glitch into a lie

A dropped chunk today produces a line that still *parses*. There is no
checksum and no per-line sequence number, so a corrupt line is
indistinguishable from a good one — the numbers just come out wrong.
Fixing layer 1 makes loss rare; **only layer 2 makes it detectable**, and
undetectable-but-rare is the failure mode this project keeps getting
burned by.

## 3. The design

### Layer 1 — never discard a byte silently
- Honor the return of `s_bleuart.write()`. Advance `s_txq_tail` **only**
  when every subscribed connection accepted the chunk.
- Retry on the next pump, **bounded**. One wedged central must never
  stall output forever: after N failed attempts, disconnect that
  connection rather than block the others or drop data blindly.
- Count every forced drop and expose it in `stats`
  (`tx_drops=`). A drop we can see is a bug; a drop we cannot see is a
  wrong number on someone's wrist.
- Consider per-connection queues later; all-or-nothing plus bounded
  retry is the small correct step now.

### Layer 2 — make corruption impossible to mistake for data
- **Per-line checksum**, NMEA-style (`*XX` suffix). Tiny, human-readable,
  and it survives the existing `key=value` parser.
- **Monotonic line sequence** on every emitted line (not just `JUMP n=`),
  so a client can detect a gap even when the lost line was a whole line.
- **Watch-side gate**: reject any line failing checksum; reject a `JUMP`
  missing `airtime_s`/`best_m`; reject `n` advancing by more than 1 —
  and on a detected gap, reconcile from `stats` rather than guess.
- Rule: **a line that fails any check is dropped and counted, never
  rendered.** Showing nothing beats showing a plausible lie.

### Layer 3 — dropouts are normal; design for them
A rider's body between a board-mounted puck and a wrist watch is a wet
2.4 GHz absorber. Dropouts are the expected case, not the exception.
- The puck already stores everything; the live stream is best-effort by
  design (`sendOneChunk` drops pending when nobody is subscribed —
  correct).
- On reconnect the watch must **reconcile, not resume**: pull `stats`
  and adopt count/best, so jumps that happened while disconnected are
  reflected even though their live lines were never delivered.
- Measure and tune TX power and connection interval — but measure
  first; today TX power is never set explicitly.

### Layer 4 — pairing that needs no ritual
- Auto-reconnect to the remembered puck address; no taps at the beach.
- Multi-puck: pin by address (the bench already needs `OTADFU_ADDR` for
  this reason). **Proven necessary and SHIPPED 2026-08-18** after a live
  impersonation: two boards advertising bare "JumpHeight" — the bench logger
  connected to the freshly-flashed spare mid-experiment and logged its
  floating battery reading into the dying OG's record. Now: every puck
  advertises `JumpHeight-XXXX` (suffix from factory silicon ID), every
  client matches by prefix or NUS service (web, blecmd, otadfu, watch —
  watch already matched service-first), `blecmd`/`battlog` accept `--addr`
  pinning, and INFO reports `name=` so a human can tell which puck answered.
- **Bonding: deliberately deferred.** It adds a pairing ceremony and a
  key-management failure mode to a device with no display, for a
  threat model (someone in BLE range spoofing jump heights) that does
  not justify it. Revisit only if `dfu` stays open (§6).
  **Observed in the first live multi-puck session (the M2 test):** with two
  pucks on-air the watch connected to whichever it found first — it picked
  the dying OG over the fresh spare. Correct for one-puck life; for the
  quiver, per-puck selection already works today by setting the field's
  `puckName` property to a full unique name ("JumpHeight-45ED") — a
  settings-UX story, not a firmware gap.

### Layer 4b — TWO RIDERS, TWO PUCKS (owner's question, 2026-08-21)

The natural next step once the brother rides: both of us out, each with a
puck, each tracking to our own watch. **This does not work today, and the
reason is not the naming — it is the match order.**

`PuckLink._matchesPuck()` (garmin/jumpfield/source/PuckLink.mc:283-300)
checks the service UUID FIRST and returns `true` on a match, before the name
is ever considered:

```monkeyc
if (u.equals(_svcUuid)) { return true; }        // matches ANY puck
...
return name != null && name.find(_puckName) == 0;   // unreachable when UUID visible
```

Every puck advertises the same Nordic UART service, so **the `puckName`
setting is inert whenever the UUID appears in the scan result.** Among
matches, `onScanResults` (:177-191) takes the strongest RSSI.

**The concrete failure, and it is silent:**
1. Both riders rig on the beach, watches scanning, both pucks a metre apart —
   RSSI is a coin flip.
2. Rider A's watch can select rider B's puck. Both watches can select the
   SAME puck (each puck accepts `kMaxPrphConnections = 2`, jh_link.cpp:170),
   leaving the other puck unconnected and unnoticed.
3. They ride apart. The link persists — BLE does not re-evaluate — so a watch
   keeps tracking the *other* board all session.
4. The FIT archives another rider's jumps, under this rider's name, with **no
   symptom anywhere**. Both displays look perfectly healthy.

RSSI selection is the right instinct (on the water each rider IS nearest their
own board) but the choice is made at rigging, when they are together, and it
is never revisited.

**Fixes, cheapest first:**
1. **Show WHICH puck on the glass** — the last four of the advertised name in
   the field header. Catches every variant of this, costs one row, needs no
   settings channel, and helps the single-puck case too (four "dead board"
   verdicts in this project began as identity confusion).
2. **Fix the match order**: when `puckName` is more specific than the bare
   default prefix, name must win over the UUID. Small, local, testable.
3. **Distribution is the real blocker.** Sideloads receive no settings —
   `properties.xml` defaults are what run — so both watches ship with the same
   `"JumpHeight"` default and cannot be pinned without either the Connect IQ
   store's settings UI or a per-rider build with the name compiled in. This is
   another dependency on the store submission, alongside the ones in
   glue-and-forget.md §4 Pillar 3.
4. **Consider `kMaxPrphConnections = 1` for the shipped rider config** — a
   puck that accepts one watch cannot be double-booked. It would trade away
   the shore-admin-while-riding case, which is currently forbidden anyway
   until the two-central JUMP-line test passes.

**Until fixed:** two riders with two pucks is not a supported configuration.
One puck, one watch, and the shore-admin rule from the two-central gate.

### Layer 5 — the user must be able to tell states apart
Today the watch shows one "no BLE" appearance whether the puck is
asleep, out of range, flat, or on the kitchen table. That is the same
ambiguity that made "is it even on?" unanswerable.
- **Battery + armed state in the advertisement payload** (manufacturer
  data), so the watch shows `puck 78%` *without connecting*.
- Distinguish on the watch: **connected** / **seen but not connected**
  (advertising, so it is alive and near) / **not found** (asleep, flat,
  or out of range).
- This is the cheapest UX win available and costs no link reliability.

### Layer 6 — policy calls
- **Two centrals: keep the capability, fix the cause.** The layer-1 fix
  makes it safe; the product default stays one central (the watch), and
  the second slot remains for bench work.
- **`dfu` over BLE is unauthenticated** — anyone in range can reboot the
  puck into the bootloader. Gate it (a required argument, or disable it
  outside a bench build) before this leaves the bench.

## 4. Build order (smallest, safest, highest value first)

1. **Layer 1 fix + `tx_drops` counter.** Small, firmware-only, testable
   on the bench today. Removes the silent-loss path.
2. **Watch-side corruption gate.** Pure Monkey C, no firmware coupling.
   Correct regardless of root cause.
3. **Advertisement payload (battery + state) and the three-state watch
   display.** The seamless-UX win; independent of 1 and 2.
4. **Per-line checksum + sequence.** Both sides; do it once 1-3 are in
   and the protocol change can be tested end to end.
5. **Reconnect reconciliation from `stats`.**
6. **TX power / interval tuning** — only after measuring.

## 5. How we prove it (no verdicts without measurement)

- **Induced-failure soak:** stream a full `dump` (worst-case queue
  pressure) while a second central polls, and assert **zero silent
  loss** — every byte either arrives or increments a counter. This is
  the test that would have caught §1 on day one.
- **The raw-line diagnostic on the watch**: render received line length
  and tail for one sideload. That is the experiment
  `garmin/FIRST_COMPILE.md` has been asking for, and with §1 identified
  it is now a confirmation, not a fishing trip.
- **Single-central control run** — the water session with the watch
  alone doubles as this.
- **Bench soak before the water**: N jump lines emitted, N rendered on
  the watch, counters at zero.

## 6. Open questions

- Per-connection queues vs all-or-nothing: revisit if a slow central
  ever stalls a fast one in practice.
- Does the FIT recording path need the same integrity gate as the
  display path? (A corrupt line that reaches FIT is permanent.)
- Connection-interval negotiation: the watch requests it; we pace to the
  slowest. Is our pacing leaving throughput unused mid-session?
